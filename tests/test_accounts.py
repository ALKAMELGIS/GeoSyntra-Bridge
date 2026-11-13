"""Commercial mode: accounts (password, GitHub OAuth), sessions, credits charged per geodatabase, Stripe checkout and
webhooks, owner console. External services are replaced by a local fake (Stripe API + OAuth provider)."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from geosyntra_bridge.accounts.integrations import sign_webhook  # noqa: E402
from geosyntra_bridge.api.app import create_app  # noqa: E402
from geosyntra_bridge.config import Settings  # noqa: E402
from geosyntra_bridge.elevation.dem import write_geotiff  # noqa: E402

WHSEC = "whsec_test_secret"
CALLS = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = parse_qs(self.rfile.read(n).decode())
        CALLS.append((self.path, body, dict(self.headers)))
        if self.path == "/v1/customers":
            return self._send({"id": "cus_test1"})
        if self.path == "/v1/checkout/sessions":
            return self._send({"id": "cs_test1", "url": "https://checkout.stripe.test/cs_test1"})
        if self.path == "/v1/billing_portal/sessions":
            return self._send({"url": "https://billing.stripe.test/p1"})
        if self.path == "/oauth/token":
            return self._send({"access_token": "gho_test"} if body.get("code") == ["good"] else {"error": "bad"}, 200)
        self._send({"error": {"message": "unknown"}}, 404)

    def do_GET(self):
        if self.path == "/oauth/user":
            return self._send({"id": 4242, "login": "devuser", "name": "Dev User", "avatar_url": "https://a/x.png"})
        if self.path == "/oauth/emails":
            return self._send([{"email": "dev@github.example", "primary": True, "verified": True}])
        self._send({}, 404)


def wait(c, jid, statuses, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = c.get(f"/api/jobs/{jid}").json()
        if st["status"] in statuses:
            return st
        if st["status"] == "failed":
            raise AssertionError(st["message"])
        time.sleep(0.2)
    raise AssertionError("timeout")


class Accounts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.srv.server_port}"
        cls.env = {"GSB_OWNER_EMAILS": "owner@geosyntra.test", "GSB_STRIPE_SECRET_KEY": "sk_test_x",
                   "GSB_STRIPE_WEBHOOK_SECRET": WHSEC, "GSB_STRIPE_API_BASE": base,
                   "GSB_GITHUB_CLIENT_ID": "gh_id", "GSB_GITHUB_CLIENT_SECRET": "gh_secret",
                   "GSB_OAUTH_GITHUB_TOKEN_URL": f"{base}/oauth/token", "GSB_OAUTH_GITHUB_USER_URL": f"{base}/oauth/user",
                   "GSB_OAUTH_GITHUB_EMAILS_URL": f"{base}/oauth/emails", "NO_PROXY": "127.0.0.1,localhost",
                   "no_proxy": "127.0.0.1,localhost"}
        cls.old = {k: os.environ.get(k) for k in cls.env}
        os.environ.update(cls.env)
        cls.tmp = tempfile.mkdtemp(prefix="gsb_acc_")
        cls.app = create_app(Settings(auth_mode="accounts", data_dir=os.path.join(cls.tmp, "d"),
                                      rules_dir=os.path.join(cls.tmp, "r")))

    @classmethod
    def tearDownClass(cls):
        cls.app.state.jobs.shutdown()
        cls.srv.shutdown()
        for k, v in cls.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def client(self):
        return TestClient(self.app)

    def signup(self, email, pw="Bridge-2026!", **kw):
        c = self.client()
        r = c.post("/api/auth/signup", json={"email": email, "password": pw, "name": kw.get("name", "Eng")})
        self.assertEqual(r.status_code, 200, r.text)
        return c, r.json()["user"]

    def job(self, c):
        with open(os.path.join(ROOT, "sample_data", "sample_hard_landscaping.dxf"), "rb") as f:
            jid = c.post("/api/jobs", data={"schema_id": "adm_hl_hardlandscaping"}, files={"file": ("h.dxf", f, "x")}).json()["job_id"]
        wait(c, jid, ("ready",))
        c.post(f"/api/jobs/{jid}/mapping/accept-suggestions")
        c.patch(f"/api/jobs/{jid}/mapping", json={"fields": {"*": {k: {"source": "constant", "value": v} for k, v in
                                                                  {"DATA_SOURCE": "AS BUILT", "REGION": "ADM"}.items()}}})
        ext = c.get(f"/api/jobs/{jid}/discovery").json()["extent"]
        tif = os.path.join(self.tmp, f"dtm_{jid}.tif")
        write_geotiff(tif, np.full((int(ext[3] - ext[1] + 100), int(ext[2] - ext[0] + 100)), 3.0, dtype=np.float32),
                      (ext[0] - 50, 1.0, 0, ext[3] + 50, 0, -1.0), 32640)
        with open(tif, "rb") as f:
            self.assertEqual(c.post(f"/api/jobs/{jid}/elevation/upload", files={"file": ("d.tif", f, "x")},
                                    data={"vertical": "EPSG:5773"}).status_code, 200)
        c.post(f"/api/jobs/{jid}/validate")
        wait(c, jid, ("validated",))
        return jid

    # ---------------------------------------------------------------------------------------------------------
    def test_1_auth_and_sessions(self):
        anon = self.client()
        self.assertEqual(anon.get("/api/schemas").status_code, 401)
        cfg = anon.get("/api/auth/config").json()
        self.assertEqual((cfg["mode"], cfg["trial_credits"], cfg["providers"]), ("accounts", 50, {"github": "GitHub"}))
        self.assertIsNone(anon.get("/api/auth/me").json()["user"])
        self.assertEqual(anon.post("/api/auth/signup", json={"email": "x@y.com", "password": "short"}).status_code, 400)
        c, u = self.signup("Alice@Example.com")
        self.assertEqual((u["email"], u["credits"], u["role"]), ("alice@example.com", 50, "user"))
        self.assertEqual(c.get("/api/schemas").status_code, 200)
        self.assertEqual(self.client().post("/api/auth/signup", json={"email": "alice@example.com", "password": "Another-pass1"}).status_code, 409)
        bad = self.client()
        for _ in range(8):
            self.assertEqual(bad.post("/api/auth/login", json={"email": "alice@example.com", "password": "wrong-pass"}).status_code, 401)
        self.assertEqual(bad.post("/api/auth/login", json={"email": "alice@example.com", "password": "Bridge-2026!"}).status_code, 429)
        c.post("/api/auth/logout")
        self.assertEqual(c.get("/api/schemas").status_code, 401)
        self.assertEqual(c.get("/api/owner/overview").status_code, 401)
        # password reset through a one-time link
        c2 = self.client()
        c2.post("/api/auth/password/forgot", json={"email": "alice@example.com"})
        mail = sorted(os.listdir(os.path.join(self.tmp, "d", "mail_outbox")))[-1]
        body = open(os.path.join(self.tmp, "d", "mail_outbox", mail), encoding="utf-8").read()
        tok = body.split("token=")[1].split()[0]
        self.assertEqual(c2.post("/api/auth/password/reset", json={"token": tok, "password": "New-Bridge-26"}).status_code, 200)
        self.assertEqual(c2.post("/api/auth/password/reset", json={"token": tok, "password": "New-Bridge-26"}).status_code, 400)
        self.assertEqual(self.client().post("/api/auth/login", json={"email": "alice@example.com", "password": "New-Bridge-26"}).status_code, 200)

    def test_2_github_sign_in(self):
        c = self.client()
        r = c.get("/api/auth/oauth/github/start", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        bad = c.get(f"/api/auth/oauth/github/callback?code=good&state=forged", follow_redirects=False)
        self.assertIn("#/login?error=", bad.headers["location"])
        r = c.get("/api/auth/oauth/github/start", follow_redirects=False)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        r = c.get(f"/api/auth/oauth/github/callback?code=good&state={state}", follow_redirects=False)
        self.assertTrue(r.headers["location"].endswith("/#/app"), r.headers["location"])
        me = c.get("/api/auth/me").json()["user"]
        self.assertEqual((me["email"], me["signup_method"], me["credits"], me["email_verified"]), ("dev@github.example", "github", 50, True))

    def test_3_credits_and_owner(self):
        owner, ou = self.signup("owner@geosyntra.test")
        self.assertEqual(ou["role"], "owner")
        c, u = self.signup("bob@contractor.ae")
        self.assertEqual(c.get("/api/owner/overview").status_code, 403)
        jid = self.job(c)
        q = c.get(f"/api/billing/quote/{jid}").json()
        self.assertEqual(q["cost"], 15)                                # 10 + 5 x ceil(features / 1000)
        r = c.post(f"/api/jobs/{jid}/generate")
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(r.json()["charge"]["charged"], 15)
        wait(c, jid, ("completed",))
        self.assertEqual(c.get("/api/auth/me").json()["user"]["credits"], 35)
        self.assertEqual(c.post(f"/api/jobs/{jid}/generate").json()["charge"]["charged"], 0)      # regeneration is free
        wait(c, jid, ("completed",))
        self.assertEqual(c.get("/api/auth/me").json()["user"]["credits"], 35)
        # other users cannot see the job; the owner can
        other, _ = self.signup("eve@else.ae")
        self.assertEqual(other.get(f"/api/jobs/{jid}").status_code, 404)
        self.assertEqual(owner.get(f"/api/jobs/{jid}").status_code, 200)
        # owner console
        ov = owner.get("/api/owner/overview").json()
        self.assertGreaterEqual(ov["users"], 3)
        self.assertEqual(ov["geodatabases_30d"], 1)
        users = owner.get("/api/owner/users", params={"q": "bob"}).json()
        self.assertEqual(users["users"][0]["credits_used"], 15)
        self.assertEqual(owner.post(f"/api/owner/users/{u['id']}/credits", json={"delta": -35, "note": "test"}).json()["balance"], 0)
        jid2 = self.job(c)
        r = c.post(f"/api/jobs/{jid2}/generate")
        self.assertEqual(r.status_code, 402, r.text)                   # not enough credits, nothing generated
        self.assertNotEqual(c.get(f"/api/jobs/{jid2}").json()["status"], "queued")
        owner.post(f"/api/owner/users/{u['id']}/credits", json={"delta": 100})
        self.assertEqual(c.get("/api/auth/me").json()["user"]["credits"], 100)
        # suspend -> signed out everywhere; reactivate
        self.assertEqual(owner.patch(f"/api/owner/users/{u['id']}", json={"status": "suspended"}).status_code, 200)
        self.assertEqual(c.get("/api/schemas").status_code, 401)
        self.assertEqual(self.client().post("/api/auth/login", json={"email": "bob@contractor.ae", "password": "Bridge-2026!"}).status_code, 403)
        owner.patch(f"/api/owner/users/{u['id']}", json={"status": "active"})
        # an administrator cannot change roles or settings, an owner can
        owner.patch(f"/api/owner/users/{u['id']}", json={"role": "admin"})
        c = self.client()
        c.post("/api/auth/login", json={"email": "bob@contractor.ae", "password": "Bridge-2026!"})
        self.assertEqual(c.get("/api/owner/overview").status_code, 200)
        self.assertEqual(c.put("/api/owner/settings", json={"trial_credits": 999}).status_code, 403)
        self.assertEqual(owner.put("/api/owner/settings", json={"trial_credits": 20}).json()["trial_credits"], 20)
        _, nu = self.signup("new@user.ae")
        self.assertEqual(nu["credits"], 20)
        audit = owner.get("/api/owner/audit").json()
        self.assertTrue({"credits_adjusted", "user_updated", "settings_saved"} <= {a["action"] for a in audit})
        # invited (enterprise) account
        inv = owner.post("/api/owner/users", json={"email": "city@municipality.ae", "credits": 500}).json()
        self.assertIn("#/reset?token=", inv["url"])
        self.assertEqual(inv["user"]["credits"], 520)

    def test_4_stripe(self):
        c, u = self.signup("carol@consult.ae")
        plans = {p["id"]: p for p in c.get("/api/billing/plans").json()}
        self.assertFalse(plans["pack_100"]["purchasable"])
        self.assertEqual(c.post("/api/billing/checkout", json={"plan_id": "pack_100"}).status_code, 400)   # no price yet
        own = self.client()
        own.post("/api/auth/login", json={"email": "owner@geosyntra.test", "password": "Bridge-2026!"})
        if own.get("/api/auth/me").json()["user"] is None:
            own, _ = self.signup("owner@geosyntra.test")
        self.assertEqual(own.put("/api/owner/plans/pack_100", json={"stripe_price_id": "price_pack100"}).status_code, 200)
        self.assertEqual(own.put("/api/owner/plans/professional", json={"stripe_price_id": "price_pro"}).status_code, 200)
        self.assertEqual(own.put("/api/owner/plans/professional", json={"stripe_price_id": "bad"}).status_code, 400)
        r = c.post("/api/billing/checkout", json={"plan_id": "pack_100"})
        self.assertEqual(r.json()["url"], "https://checkout.stripe.test/cs_test1")
        sess = next(b for p, b, h in CALLS if p == "/v1/checkout/sessions")
        self.assertEqual((sess["mode"], sess["line_items[0][price]"], sess["metadata[user_id]"]), (["payment"], ["price_pack100"], [str(u["id"])]))

        def hook(ev, secret=WHSEC):
            body = json.dumps(ev).encode()
            return self.client().post("/api/billing/webhook", content=body,
                                      headers={"Stripe-Signature": sign_webhook(body, secret), "Content-Type": "application/json"})
        ev = {"id": "evt_1", "type": "checkout.session.completed", "data": {"object": {
            "id": "cs_test1", "mode": "payment", "payment_status": "paid", "customer": "cus_test1", "amount_total": 9900,
            "currency": "aed", "client_reference_id": str(u["id"]), "metadata": {"user_id": str(u["id"]), "plan_id": "pack_100"}}}}
        self.assertEqual(hook(ev, "whsec_wrong").status_code, 400)
        self.assertEqual(hook(ev).json()["result"], "pack pack_100: +100 credits")
        self.assertEqual(hook(ev).json()["result"], "duplicate — already processed")
        self.assertEqual(c.get("/api/auth/me").json()["user"]["credits"], u["credits"] + 100)
        # subscription: monthly invoice adds the plan credits, cancellation is recorded
        inv = {"id": "evt_2", "type": "invoice.paid", "data": {"object": {
            "id": "in_1", "customer": "cus_test1", "subscription": "sub_1", "amount_paid": 54900, "currency": "aed",
            "lines": {"data": [{"price": {"id": "price_pro"}, "period": {"end": 1893456000}}]}}}}
        self.assertIn("+1200", hook(inv).json()["result"])
        b = c.get("/api/billing/status").json()
        self.assertEqual((b["credits"], b["plan"]["id"], b["subscription_status"]), (u["credits"] + 1300, "professional", "active"))
        self.assertEqual(c.post("/api/billing/checkout", json={"plan_id": "professional"}).status_code, 400)   # already subscribed
        self.assertEqual(c.post("/api/billing/portal").json()["url"], "https://billing.stripe.test/p1")
        hook({"id": "evt_3", "type": "customer.subscription.deleted", "data": {"object": {
            "id": "sub_1", "customer": "cus_test1", "status": "canceled", "items": {"data": [{"price": {"id": "price_pro"}}]}}}})
        b = c.get("/api/billing/status").json()
        self.assertEqual((b["subscription_status"], b["plan"], b["credits"]), ("canceled", None, u["credits"] + 1300))   # credits kept
        ov = own.get("/api/owner/overview").json()
        self.assertTrue(any(r["amount"] == 9900 + 54900 for r in ov["revenue_30d"]))
        self.assertEqual(len(own.get("/api/owner/payments").json()), 2)


    def test_6_requests_without_stripe(self):
        """No online payment: the customer requests a plan, the owner approves it -> credits + plan; reject adds nothing."""
        c, u = self.signup("buyer@example.test")
        start = c.get("/api/auth/me").json()["user"]["credits"]
        r = c.post("/api/billing/request", json={"plan_id": "starter"})
        self.assertEqual(r.status_code, 200, r.text)
        req = r.json()
        self.assertTrue(req["reference"].startswith("GSB-"))
        self.assertEqual(c.post("/api/billing/request", json={"plan_id": "starter"}).json()["id"], req["id"])   # no duplicates
        self.assertEqual(c.post("/api/billing/request", json={"plan_id": "enterprise"}).status_code, 400)
        self.assertEqual(c.get(f"/api/owner/requests").status_code, 403)                                          # customers cannot decide
        o = self.client()
        if o.post("/api/auth/login", json={"email": "owner@geosyntra.test", "password": "Bridge-2026!"}).status_code != 200:
            o, _ = self.signup("owner@geosyntra.test")
        self.assertEqual(o.put("/api/owner/settings", json={"payment_instructions": "IBAN AE00 TEST"}).status_code, 200)
        self.assertEqual(c.get("/api/billing/status").json()["payment_instructions"], "IBAN AE00 TEST")
        pend = o.get("/api/owner/requests?status=pending").json()
        self.assertTrue(any(x["id"] == req["id"] for x in pend))
        self.assertGreaterEqual(o.get("/api/owner/overview").json()["pending_requests"], 1)
        d = o.post(f"/api/owner/requests/{req['id']}", json={"approve": True})
        self.assertEqual(d.json()["status"], "approved")
        me = c.get("/api/auth/me").json()["user"]
        plan = next(p for p in c.get("/api/billing/plans").json() if p["id"] == "starter")
        self.assertEqual(me["credits"], start + plan["credits"])
        self.assertEqual(c.get("/api/billing/status").json()["plan"]["id"], "starter")
        self.assertEqual(o.post(f"/api/owner/requests/{req['id']}", json={"approve": True}).status_code, 400)  # only once
        # a pack, rejected: nothing added; a pack cancelled by the customer
        p2 = c.post("/api/billing/request", json={"plan_id": "pack_100"}).json()
        o.post(f"/api/owner/requests/{p2['id']}", json={"approve": False})
        self.assertEqual(c.get("/api/auth/me").json()["user"]["credits"], start + plan["credits"])
        p3 = c.post("/api/billing/request", json={"plan_id": "pack_100"}).json()
        self.assertEqual(c.post(f"/api/billing/requests/{p3['id']}/cancel").status_code, 200)
        self.assertEqual([x["status"] for x in c.get("/api/billing/requests").json()][:3], ["cancelled", "rejected", "approved"])
        # e-mails (background, kept in the outbox): owner told of the request, customer of the details and the decision
        box = os.path.join(self.tmp, "d", "mail_outbox")
        for _ in range(50):
            names = os.listdir(box)
            if sum(req["reference"] in open(os.path.join(box, n), encoding="utf-8").read() for n in names) >= 3:
                break
            time.sleep(0.1)
        txt = [open(os.path.join(box, n), encoding="utf-8").read() for n in os.listdir(box)]
        mine = [t for t in txt if req["reference"] in t]
        self.assertTrue(any("new request" in t for t in mine))
        self.assertTrue(any("payment confirmed" in t and "buyer_at_example" not in t for t in mine))
        self.assertTrue(any("IBAN AE00 TEST" in t for t in txt) or any("payment details" in t for t in mine))
        # receipt: own approved request (and the owner), not a rejected one, not another customer
        rc = c.get(f"/api/billing/requests/{req['id']}/receipt.pdf")
        self.assertEqual(rc.status_code, 200); self.assertTrue(rc.content.startswith(b"%PDF"))
        self.assertEqual(o.get(f"/api/billing/requests/{req['id']}/receipt.pdf").status_code, 200)
        self.assertEqual(c.get(f"/api/billing/requests/{p2['id']}/receipt.pdf").status_code, 400)
        x, _ = self.signup("other@example.test")
        self.assertEqual(x.get(f"/api/billing/requests/{req['id']}/receipt.pdf").status_code, 404)
        # month over -> past_due + one renewal request; approval extends the period
        st = self.app.state.accounts.store
        st.update_user(u["id"], current_period_end="2020-01-01T00:00:00+00:00")
        self.assertEqual(c.get("/api/billing/status").json()["subscription_status"], "past_due")
        c.get("/api/billing/status")
        ren = [r for r in c.get("/api/billing/requests").json() if r["status"] == "pending"]
        self.assertEqual(len(ren), 1); self.assertEqual(ren[0]["note"], "renewal")
        o.post(f"/api/owner/requests/{ren[0]['id']}", json={"approve": True})
        b = c.get("/api/billing/status").json()
        self.assertEqual(b["subscription_status"], "active")

    def test_5_refund_on_failed_generation(self):
        from geosyntra_bridge.accounts import billing
        from geosyntra_bridge.accounts.store import Store
        st = Store(os.path.join(self.tmp, "unit.db"))
        u = st.create_user("unit@test.ae")
        start = st.user(u["id"])["credits"]
        self.assertEqual(billing.charge_generation(st, u["id"], "job1", 2500)["charged"], 25)
        self.assertEqual(billing.charge_generation(st, u["id"], "job1", 2500)["charged"], 0)     # already paid
        self.assertEqual(billing.refund_generation(st, "job1", "failed"), 25)
        self.assertEqual(billing.refund_generation(st, "job1", "failed"), 0)                     # refunded once
        self.assertEqual(st.user(u["id"])["credits"], start)
        self.assertEqual(billing.charge_generation(st, u["id"], "job1", 2500)["charged"], 25)    # a new attempt pays again
        self.assertEqual(sum(r["delta"] for r in st.ledger(u["id"])), st.user(u["id"])["credits"])


if __name__ == "__main__":
    unittest.main()


class OwnerNotCharged(unittest.TestCase):
    def test_owner_exempt(self):
        import tempfile
        from geosyntra_bridge.accounts.store import Store
        from geosyntra_bridge.accounts import billing
        st = Store(os.path.join(tempfile.mkdtemp(), "a.db"))
        u = st.create_user("o@x.test", "Owner", role="owner")
        uid = u["id"] if isinstance(u, dict) else u
        r = billing.charge_generation(st, uid, "job-1", 5000)
        self.assertEqual(r["charged"], 0)
        self.assertTrue(billing.billing_status(st, st.user(uid))["unlimited"])
