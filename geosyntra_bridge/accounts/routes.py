"""
Accounts API: sign-up / sign-in (password, Google, GitHub), sessions, account & billing, Stripe webhook and the
owner console.  Enabled with GSB_AUTH_MODE=accounts; owners are the e-mails in GSB_OWNER_EMAILS (or created with
scripts/create_owner.py).
"""
from __future__ import annotations

import json
import os
import threading
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from ..config import Settings
from . import billing
from .integrations import (OAuthError, PROVIDERS, StripeError, authorize_url, configured_providers, env, fetch_profile,
                           send_mail, smtp_configured, stripe_configured, stripe_mode, verify_webhook)
from .security import Throttle, email_problem, hash_password, password_problem, verify_password
from .store import InsufficientCredits, Store, now, public_user

COOKIE = "gsb_session"
STATE_COOKIE = "gsb_oauth_state"


def owner_emails() -> set:
    return {e.strip().lower() for e in env("GSB_OWNER_EMAILS").split(",") if e.strip()}


class Accounts:
    def __init__(self, s: Settings):
        self.s = s
        self.store = Store(os.path.join(s.data_dir, "accounts.db"))
        self.throttle = Throttle(limit=8, window=900)
        self.outbox = os.path.join(s.data_dir, "mail_outbox")

    # ------------------------------------------------------------------ request helpers
    def base_url(self, request: Request) -> str:
        pub = env("GSB_PUBLIC_URL").rstrip("/")
        if pub:
            return pub + self.s.base_path
        return str(request.base_url).rstrip("/") + self.s.base_path

    def cookie_path(self) -> str:
        return self.s.base_path or "/"

    def secure(self, request: Request) -> bool:
        return env("GSB_COOKIE_SECURE") == "1" or request.url.scheme == "https" or \
            request.headers.get("x-forwarded-proto", "") == "https"

    def set_session(self, resp: Response, request: Request, uid: int):
        tok = self.store.new_session(uid, ip=_ip(request), ua=request.headers.get("user-agent", ""))
        resp.set_cookie(COOKIE, tok, max_age=30 * 86400, httponly=True, samesite="lax", secure=self.secure(request),
                        path=self.cookie_path())

    def current(self, request: Request) -> Optional[Dict[str, Any]]:
        tok = request.cookies.get(COOKIE) or ""
        h = request.headers.get("Authorization", "")
        if not tok and h.lower().startswith("bearer "):
            tok = h[7:].strip()
        u = self.store.session_user(tok)
        if u and u["status"] != "active":
            return None
        return u

    def role_for(self, email: str) -> str:
        return "owner" if email.strip().lower() in owner_emails() else "user"


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")


def build_router(s: Settings, acc: Accounts, jobs, principal_dep: Callable) -> APIRouter:
    r = APIRouter(prefix=f"{s.base_path}/api")
    st = acc.store
    accounts_mode = s.auth_mode == "accounts"

    def me_or_401(request: Request) -> Dict[str, Any]:
        u = acc.current(request)
        if not u:
            raise HTTPException(401, "sign in required")
        return u

    def staff(request: Request) -> Dict[str, Any]:
        if not accounts_mode:
            return {"id": None, "email": "local", "role": "owner"}
        u = me_or_401(request)
        if u["role"] not in ("owner", "admin"):
            raise HTTPException(403, "owner console: owners and administrators only")
        return u

    def owner_only(u: Dict[str, Any]):
        if u["role"] != "owner":
            raise HTTPException(403, "only an owner can change this")

    # ================================================================== public auth
    @r.get("/auth/config")
    def auth_config():
        cfg = st.settings()
        return {"mode": s.auth_mode, "providers": configured_providers(), "signup_open": bool(cfg["signup_open"]),
                "trial_credits": cfg["trial_credits"], "stripe": stripe_configured(),
                "company_name": cfg["company_name"], "support_email": cfg["support_email"],
                "pricing": {k: cfg[k] for k in ("generate_base_credits", "generate_credits_per_1000_features")}}

    @r.get("/auth/me")
    def auth_me(request: Request):
        if not accounts_mode:
            return {"mode": s.auth_mode, "user": {"id": None, "email": "local", "name": "Local user", "role": "owner",
                                                  "credits": None, "status": "active"}}
        u = acc.current(request)
        return {"mode": s.auth_mode, "user": public_user(u) if u else None}

    @r.post("/auth/signup")
    def signup(request: Request, body: Dict[str, Any] = Body(...)):
        _need_accounts()
        cfg = st.settings()
        email = str(body.get("email", "")).strip().lower()
        if not cfg["signup_open"] and email not in owner_emails():
            raise HTTPException(403, "sign-up is closed — contact us for an account")
        for prob in (email_problem(email), password_problem(str(body.get("password", "")))):
            if prob:
                raise HTTPException(400, prob)
        if st.user_by_email(email):
            raise HTTPException(409, "an account with this e-mail already exists — sign in instead")
        u = st.create_user(email, str(body.get("name", ""))[:100], hash_password(body["password"]), role=acc.role_for(email),
                           company=str(body.get("company", ""))[:120])
        st.audit(u, "signup", f"user:{u['id']}", {"method": "password"}, _ip(request))
        _send_verify(request, u)
        resp = JSONResponse({"user": public_user(st.user(u["id"]))})
        acc.set_session(resp, request, u["id"])
        return resp

    @r.post("/auth/login")
    def login(request: Request, body: Dict[str, Any] = Body(...)):
        _need_accounts()
        email = str(body.get("email", "")).strip().lower()
        key = f"{email}|{_ip(request)}"
        blocked, wait = acc.throttle.blocked(key)
        if blocked:
            raise HTTPException(429, f"too many attempts — try again in {wait // 60 + 1} minute(s)")
        u = st.user_by_email(email)
        if not u or not verify_password(str(body.get("password", "")), u.get("password_hash")):
            acc.throttle.fail(key)
            if u and not u.get("password_hash"):
                raise HTTPException(401, "this account signs in with Google or GitHub — use that button, or reset a password")
            raise HTTPException(401, "wrong e-mail or password")
        if u["status"] != "active":
            raise HTTPException(403, "this account is suspended — contact support")
        acc.throttle.clear(key)
        if u["role"] == "user" and email in owner_emails():
            st.update_user(u["id"], role="owner")
        resp = JSONResponse({"user": public_user(st.user(u["id"]))})
        acc.set_session(resp, request, u["id"])
        st.audit(u, "login", f"user:{u['id']}", {"method": "password"}, _ip(request))
        return resp

    @r.post("/auth/logout")
    def logout(request: Request):
        tok = request.cookies.get(COOKIE)
        if tok:
            st.end_session(tok)
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path=acc.cookie_path())
        return resp

    # ---- Google / GitHub
    @r.get("/auth/oauth/{provider}/start")
    def oauth_start(provider: str, request: Request):
        _need_accounts()
        if provider not in configured_providers():
            raise HTTPException(404, f"{provider} sign-in is not configured")
        state = secrets.token_urlsafe(24)
        url = authorize_url(provider, f"{acc.base_url(request)}/api/auth/oauth/{provider}/callback", state)
        resp = RedirectResponse(url, status_code=302)
        resp.set_cookie(STATE_COOKIE, f"{provider}:{state}", max_age=600, httponly=True, samesite="lax",
                        secure=acc.secure(request), path=acc.cookie_path())
        return resp

    @r.get("/auth/oauth/{provider}/callback")
    def oauth_callback(provider: str, request: Request, code: str = "", state: str = "", error: str = ""):
        _need_accounts()
        base = acc.base_url(request)

        def back(msg: str):
            resp = RedirectResponse(f"{base}/#/login?error={_q(msg)}", status_code=302)
            resp.delete_cookie(STATE_COOKIE, path=acc.cookie_path())
            return resp

        expect = request.cookies.get(STATE_COOKIE, "")
        if error:
            return back(f"{PROVIDERS.get(provider, {}).get('label', provider)}: {error}")
        if not code or not state or not secrets.compare_digest(expect, f"{provider}:{state}"):
            return back("the sign-in expired or was started in another browser — try again")
        try:
            prof = fetch_profile(provider, code, f"{base}/api/auth/oauth/{provider}/callback")
        except OAuthError as e:
            return back(str(e))
        if not prof.get("email") or not prof.get("email_verified"):
            return back(f"your {PROVIDERS[provider]['label']} account has no verified e-mail address")
        u = st.identity_user(provider, prof["subject"])
        if not u:
            u = st.user_by_email(prof["email"])
            if u:
                st.link_identity(u["id"], provider, prof["subject"], prof["email"])
                if not u["email_verified"]:
                    st.update_user(u["id"], email_verified=1)
            else:
                if not st.settings()["signup_open"] and prof["email"].lower() not in owner_emails():
                    return back("sign-up is closed — contact us for an account")
                u = st.create_user(prof["email"], prof.get("name", ""), None, role=acc.role_for(prof["email"]), verified=True,
                                   method=provider, avatar=prof.get("avatar", ""))
                st.link_identity(u["id"], provider, prof["subject"], prof["email"])
                st.audit(u, "signup", f"user:{u['id']}", {"method": provider}, _ip(request))
        u = st.user(u["id"])
        if u["status"] != "active":
            return back("this account is suspended — contact support")
        if prof.get("avatar") and not u.get("avatar_url"):
            st.update_user(u["id"], avatar_url=prof["avatar"])
        resp = RedirectResponse(f"{base}/#/app", status_code=302)
        resp.delete_cookie(STATE_COOKIE, path=acc.cookie_path())
        acc.set_session(resp, request, u["id"])
        st.audit(u, "login", f"user:{u['id']}", {"method": provider}, _ip(request))
        return resp

    # ---- password reset / e-mail verification
    @r.post("/auth/password/forgot")
    def forgot(request: Request, body: Dict[str, Any] = Body(...)):
        _need_accounts()
        u = st.user_by_email(str(body.get("email", "")))
        if u and u["status"] == "active":
            tok = st.new_token("reset", u["id"], minutes=60)
            send_mail(u["email"], "Reset your GeoSyntra Bridge password",
                      f"Hello {u.get('name') or ''},\n\nOpen this link within one hour to choose a new password:\n"
                      f"{acc.base_url(request)}/#/reset?token={tok}\n\nIf you did not ask for it, ignore this e-mail.",
                      acc.outbox)
            st.audit(u, "password_reset_requested", f"user:{u['id']}", None, _ip(request))
        return {"ok": True, "message": "If the address has an account, a reset link is on its way."}

    @r.post("/auth/password/reset")
    def reset(request: Request, body: Dict[str, Any] = Body(...)):
        _need_accounts()
        prob = password_problem(str(body.get("password", "")))
        if prob:
            raise HTTPException(400, prob)
        t = st.use_token("reset", str(body.get("token", "")))
        if not t:
            raise HTTPException(400, "the reset link is invalid or expired — ask for a new one")
        st.update_user(t["user_id"], password_hash=hash_password(body["password"]), email_verified=1)
        st.end_all_sessions(t["user_id"])
        u = st.user(t["user_id"])
        acc.throttle.clear_prefix(f"{u['email']}|")       # the owner of the address proved it
        resp = JSONResponse({"user": public_user(u)})
        acc.set_session(resp, request, u["id"])
        st.audit(u, "password_reset", f"user:{u['id']}", None, _ip(request))
        return resp

    @r.get("/auth/verify")
    def verify(request: Request, token: str = ""):
        t = st.use_token("verify", token)
        if t:
            st.update_user(t["user_id"], email_verified=1)
        return RedirectResponse(f"{acc.base_url(request)}/#/account?verified={'1' if t else '0'}", status_code=302)

    def _send_verify(request: Request, u: Dict[str, Any]):
        tok = st.new_token("verify", u["id"], minutes=60 * 48)
        send_mail(u["email"], "Confirm your e-mail — GeoSyntra Bridge",
                  f"Welcome to GeoSyntra Bridge.\n\nConfirm your e-mail address:\n{acc.base_url(request)}/api/auth/verify?token={tok}\n\n"
                  f"Your account has {st.settings()['trial_credits']} free credits to try the converter.", acc.outbox)

    # ================================================================== account & billing (signed in)
    @r.patch("/account")
    def update_account(request: Request, body: Dict[str, Any] = Body(...)):
        u = me_or_401(request)
        st.update_user(u["id"], **{k: str(v)[:120] for k, v in body.items() if k in ("name", "company")})
        return {"user": public_user(st.user(u["id"]))}

    @r.post("/account/password")
    def change_password(request: Request, body: Dict[str, Any] = Body(...)):
        u = me_or_401(request)
        if u.get("password_hash") and not verify_password(str(body.get("current", "")), u["password_hash"]):
            raise HTTPException(400, "the current password is not correct")
        prob = password_problem(str(body.get("password", "")))
        if prob:
            raise HTTPException(400, prob)
        st.update_user(u["id"], password_hash=hash_password(body["password"]))
        st.audit(u, "password_changed", f"user:{u['id']}", None, _ip(request))
        return {"ok": True}

    @r.post("/account/resend-verification")
    def resend_verification(request: Request):
        u = me_or_401(request)
        if not u["email_verified"]:
            _send_verify(request, u)
        return {"ok": True, "smtp": smtp_configured()}

    @r.get("/billing/plans")
    def plans():
        return [{k: v for k, v in p.items() if k != "stripe_price_id"} | {"purchasable": bool(p.get("stripe_price_id"))}
                for p in st.plans()]

    @r.get("/billing/status")
    def billing_status(request: Request):
        u = me_or_401(request)
        before = st.one("SELECT id FROM requests WHERE user_id=? AND status='pending' AND note='renewal'", (u["id"],))
        ren = st.renew_manual(u["id"])
        if ren:
            if not before:
                request_mails(ren, request, "renewal")
            u = st.user(u["id"])
        return billing.billing_status(st, u)

    @r.get("/billing/quote/{job_id}")
    def billing_quote(job_id: str, request: Request):
        if not accounts_mode:
            return {"enforced": False}
        u = me_or_401(request)
        jobs.check_owner(job_id, f"u{u['id']}", u["role"] in ("owner", "admin"))
        if billing.exempt(u):
            return {"enforced": False, "unlimited": True}
        feats = _features(job_id)
        paid = billing.already_paid(st, job_id)
        cost = 0 if paid else billing.quote(st, feats)
        return {"enforced": True, "features": feats, "cost": cost, "already_paid": paid, "balance": u["credits"],
                "enough": u["credits"] >= cost}

    @r.post("/billing/checkout")
    def billing_checkout(request: Request, body: Dict[str, Any] = Body(...)):
        u = me_or_401(request)
        try:
            url = billing.checkout(st, u, str(body.get("plan_id", "")), acc.base_url(request))
        except StripeError as e:
            raise HTTPException(400, str(e))
        st.audit(u, "checkout_started", str(body.get("plan_id")), None, _ip(request))
        return {"url": url}

    # -------- without online payment: a request (invoice / bank transfer), approved by the owner
    def notify(to: str, subject: str, body: str) -> None:
        """E-mail in the background (SMTP can be slow); a copy always stays in the outbox."""
        if to:
            threading.Thread(target=send_mail, args=(to, subject, body, acc.outbox), daemon=True).start()

    def notify_owners(subject: str, body: str) -> None:
        for e in sorted(owner_emails() | ({st.settings().get("support_email")} - {None, ""})):
            notify(e, subject, body)

    def request_mails(r: Dict[str, Any], request: Request, event: str) -> None:
        u = st.user(r["user_id"]) or {}
        plan = st.plan(r["plan_id"]) or {}
        what = f"{plan.get('name') or r['plan_id']} · {(r.get('currency') or 'aed').upper()} {int(r.get('amount') or 0) / 100:,.2f}"
        base, pay = acc.base_url(request), st.settings().get("payment_instructions") or ""
        if event in ("new", "renewal"):
            notify_owners(f"{r['reference']} — {'renewal' if event == 'renewal' else 'new request'} from {u.get('email')}",
                          f"{u.get('name') or ''} <{u.get('email')}>\n{what}\n\nApprove once paid: {base}/#/owner")
            notify(u.get("email", ""), f"{r['reference']} — {'renewal due' if event == 'renewal' else 'payment details'}",
                   f"Hello {u.get('name') or ''},\n\n{what}\nReference: {r['reference']}\n\n" + (pay + "\n\n" if pay else "")
                   + "Credits are added as soon as the payment is confirmed.")
        elif event == "approved":
            notify(u.get("email", ""), f"{r['reference']} — payment confirmed",
                   f"Hello {u.get('name') or ''},\n\n{what}\nThe credits were added to your account.\n"
                   f"Receipt: {base}/#/account")
        elif event == "rejected":
            notify(u.get("email", ""), f"{r['reference']} — not confirmed",
                   f"Hello {u.get('name') or ''},\n\nWe could not confirm the payment for {what}."
                   + (f"\n\n{r.get('decision_note')}" if r.get("decision_note") else "") + "\n\nReply to this e-mail if you need help.")

    @r.post("/billing/request")
    def billing_request(request: Request, body: Dict[str, Any] = Body(...)):
        u = me_or_401(request)
        plan = st.plan(str(body.get("plan_id", "")))
        if not plan or not plan.get("active") or plan.get("kind") == "enterprise":
            raise HTTPException(400, "unknown plan")
        before = st.one("SELECT id FROM requests WHERE user_id=? AND plan_id=? AND status='pending'", (u["id"], plan["id"]))
        r = st.add_request(u["id"], plan, str(body.get("note", "")))
        if not before:
            request_mails(r, request, "new")
        st.audit(u, "purchase_requested", plan["id"], {"reference": r["reference"]}, _ip(request))
        return r

    @r.get("/billing/requests")
    def billing_requests(request: Request):
        return st.requests(uid=me_or_401(request)["id"], limit=50)

    @r.post("/billing/requests/{rid}/cancel")
    def billing_request_cancel(rid: int, request: Request):
        u = me_or_401(request)
        if not st.cancel_request(rid, u["id"]):
            raise HTTPException(400, "nothing to cancel")
        return {"ok": True}

    @r.get("/billing/requests/{rid}/receipt.pdf")
    def billing_receipt(rid: int, request: Request):
        """PDF receipt of an approved request — the customer's own, or any for the owner."""
        from fastapi.responses import Response
        from .receipt import receipt_pdf
        u = me_or_401(request)
        r = st.one("SELECT * FROM requests WHERE id=?", (rid,))
        if not r or (r["user_id"] != u["id"] and u["role"] not in ("owner", "admin")):
            raise HTTPException(404, "not found")
        if r["status"] != "approved":
            raise HTTPException(400, "a receipt exists once the payment is confirmed")
        pdf = receipt_pdf(r, st.user(r["user_id"]) or {}, st.plan(r["plan_id"]) or {}, st.settings())
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{r["reference"]}.pdf"'})

    @r.get("/owner/requests")
    def owner_requests(request: Request, status: str = ""):
        staff(request)
        return st.requests(status=status or None)

    @r.post("/owner/requests/{rid}")
    def owner_decide(rid: int, request: Request, body: Dict[str, Any] = Body(...)):
        actor = staff(request)
        try:
            r = st.decide_request(rid, bool(body.get("approve")), actor.get("id"), str(body.get("note", "")))
        except KeyError:
            raise HTTPException(404, "request not found")
        except ValueError as e:
            raise HTTPException(400, str(e))
        request_mails(r, request, r["status"])
        st.audit(actor, "request_" + r["status"], f"request:{r['reference']}", {"user": r["user_id"], "plan": r["plan_id"]}, _ip(request))
        return r

    @r.post("/billing/portal")
    def billing_portal(request: Request):
        u = me_or_401(request)
        try:
            return {"url": billing.portal(st, u, acc.base_url(request))}
        except StripeError as e:
            raise HTTPException(400, str(e))

    @r.post("/billing/webhook")
    async def stripe_webhook(request: Request):
        payload = await request.body()
        try:
            ev = verify_webhook(payload, request.headers.get("stripe-signature", ""))
        except StripeError as e:
            raise HTTPException(400, str(e))
        result = billing.handle_event(st, ev)
        st.audit(None, "stripe_event", ev.get("type", ""), {"id": ev.get("id"), "result": result})
        return {"received": True, "result": result}

    # ================================================================== owner console
    @r.get("/owner/overview")
    def owner_overview(request: Request):
        staff(request)
        d30 = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
        d7 = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
        one = lambda sql, a=(): (st.one(sql, a) or {}).get("n") or 0  # noqa: E731
        subs = st.q("SELECT plan_id, COUNT(*) AS n FROM users WHERE subscription_status IN ('active','trialing','past_due') "
                    "AND plan_id IS NOT NULL GROUP BY plan_id")
        plans_ = {p["id"]: p for p in st.plans(False)}
        mrr = sum(int(plans_.get(x["plan_id"], {}).get("price_amount") or 0) * x["n"] for x in subs)
        series = st.q("SELECT substr(created_at,1,10) AS day, COUNT(*) AS n FROM users WHERE created_at>=? GROUP BY day ORDER BY day", (d30,))
        usage = st.q("SELECT substr(created_at,1,10) AS day, -SUM(delta) AS credits, COUNT(*) AS jobs FROM ledger "
                     "WHERE kind='charge' AND created_at>=? GROUP BY day ORDER BY day", (d30,))
        rev = st.q("SELECT currency, SUM(amount) AS amount FROM payments WHERE created_at>=? GROUP BY currency", (d30,))
        return {
            "users": one("SELECT COUNT(*) AS n FROM users WHERE status!='deleted'"),
            "active_7d": one("SELECT COUNT(DISTINCT user_id) AS n FROM sessions WHERE last_seen>=?", (d7,)),
            "new_30d": one("SELECT COUNT(*) AS n FROM users WHERE created_at>=?", (d30,)),
            "suspended": one("SELECT COUNT(*) AS n FROM users WHERE status='suspended'"),
            "subscribers": sum(x["n"] for x in subs), "subscribers_by_plan": {x["plan_id"]: x["n"] for x in subs},
            "mrr": mrr, "currency": next(iter({p["currency"] for p in plans_.values()}), "aed"),
            "revenue_30d": rev, "credits_used_30d": sum(x["credits"] or 0 for x in usage),
            "geodatabases_30d": sum(x["jobs"] or 0 for x in usage),
            "credits_outstanding": one("SELECT COALESCE(SUM(credits),0) AS n FROM users WHERE status='active'"),
            "signups_series": series, "usage_series": usage,
            "integrations": _integrations(request),
            "pending_requests": one("SELECT COUNT(*) AS n FROM requests WHERE status='pending'"),
        }

    @r.get("/owner/users")
    def owner_users(request: Request, q: str = "", status: str = "", plan: str = "", limit: int = 200, offset: int = 0):
        staff(request)
        where, args = ["status!='deleted'"], []
        if q:
            where.append("(email LIKE ? OR name LIKE ? OR company LIKE ?)")
            args += [f"%{q}%"] * 3
        if status:
            where.append("status=?")
            args.append(status)
        if plan == "none":
            where.append("(plan_id IS NULL OR subscription_status NOT IN ('active','trialing','past_due'))")
        elif plan:
            where.append("plan_id=?")
            args.append(plan)
        w = " AND ".join(where)
        rows = st.q(f"SELECT u.*, (SELECT COUNT(*) FROM ledger l WHERE l.user_id=u.id AND l.kind='charge') AS geodatabases, "
                    f"(SELECT COALESCE(-SUM(delta),0) FROM ledger l WHERE l.user_id=u.id AND l.kind IN ('charge','refund')) AS credits_used "
                    f"FROM users u WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?", (*args, min(limit, 1000), offset))
        total = (st.one(f"SELECT COUNT(*) AS n FROM users WHERE {w}", args) or {}).get("n", 0)
        return {"total": total, "users": [public_user(x) | {"geodatabases": x["geodatabases"], "credits_used": x["credits_used"]}
                                          for x in rows]}

    @r.get("/owner/users/{uid}")
    def owner_user(uid: int, request: Request):
        staff(request)
        u = st.user(uid)
        if not u:
            raise HTTPException(404, "user not found")
        return {"user": public_user(u), "stripe_customer_id": u.get("stripe_customer_id"),
                "subscription_id": u.get("subscription_id"),
                "identities": st.q("SELECT provider, email, created_at FROM identities WHERE user_id=?", (uid,)),
                "sessions": st.q("SELECT created_at, last_seen, ip, user_agent, expires_at FROM sessions WHERE user_id=? "
                                 "ORDER BY id DESC LIMIT 20", (uid,)),
                "ledger": st.ledger(uid, 200),
                "payments": st.q("SELECT * FROM payments WHERE user_id=? ORDER BY id DESC", (uid,)),
                "jobs": [j for j in jobs.list_jobs(f"u{uid}", False, limit=100)] if accounts_mode else [],
                "audit": st.q("SELECT * FROM audit WHERE target=? ORDER BY id DESC LIMIT 50", (f"user:{uid}",))}

    @r.post("/owner/users/{uid}/credits")
    def owner_credits(uid: int, request: Request, body: Dict[str, Any] = Body(...)):
        actor = staff(request)
        try:
            delta = int(body.get("delta", 0))
        except (TypeError, ValueError):
            raise HTTPException(400, "delta must be a whole number")
        if not delta or abs(delta) > 1_000_000:
            raise HTTPException(400, "enter a non-zero amount")
        note = str(body.get("note", ""))[:200]
        try:
            bal = st.ledger_add(uid, delta, "grant" if delta > 0 else "revoke", note=note or "owner adjustment",
                                by_user=actor.get("id"))
        except InsufficientCredits as e:
            raise HTTPException(400, f"the user has only {e.balance} credits")
        except KeyError:
            raise HTTPException(404, "user not found")
        st.audit(actor, "credits_adjusted", f"user:{uid}", {"delta": delta, "note": note}, _ip(request))
        return {"balance": bal}

    @r.patch("/owner/users/{uid}")
    def owner_update_user(uid: int, request: Request, body: Dict[str, Any] = Body(...)):
        actor = staff(request)
        u = st.user(uid)
        if not u:
            raise HTTPException(404, "user not found")
        changes: Dict[str, Any] = {}
        if "role" in body:
            owner_only(actor)
            if body["role"] not in ("owner", "admin", "user"):
                raise HTTPException(400, "role must be owner, admin or user")
            if actor.get("id") == uid and body["role"] != "owner":
                raise HTTPException(400, "you cannot remove your own owner role")
            changes["role"] = body["role"]
        if "status" in body:
            if body["status"] not in ("active", "suspended"):
                raise HTTPException(400, "status must be active or suspended")
            if actor.get("id") == uid:
                raise HTTPException(400, "you cannot suspend yourself")
            if u["role"] == "owner" and actor.get("role") != "owner":
                raise HTTPException(403, "only an owner can suspend an owner")
            changes["status"] = body["status"]
        for k in ("name", "company"):
            if k in body:
                changes[k] = str(body[k])[:120]
        if "email_verified" in body:
            changes["email_verified"] = int(bool(body["email_verified"]))
        st.update_user(uid, **changes)
        if changes.get("status") == "suspended":
            st.end_all_sessions(uid)
        st.audit(actor, "user_updated", f"user:{uid}", changes, _ip(request))
        return {"user": public_user(st.user(uid))}

    @r.post("/owner/users/{uid}/reset-link")
    def owner_reset_link(uid: int, request: Request):
        actor = staff(request)
        if not st.user(uid):
            raise HTTPException(404, "user not found")
        tok = st.new_token("reset", uid, minutes=60 * 24)
        st.audit(actor, "reset_link_created", f"user:{uid}", None, _ip(request))
        return {"url": f"{acc.base_url(request)}/#/reset?token={tok}", "valid_hours": 24}

    @r.post("/owner/users/{uid}/logout")
    def owner_logout_user(uid: int, request: Request):
        actor = staff(request)
        st.end_all_sessions(uid)
        st.audit(actor, "sessions_ended", f"user:{uid}", None, _ip(request))
        return {"ok": True}

    @r.delete("/owner/users/{uid}")
    def owner_delete_user(uid: int, request: Request):
        actor = staff(request)
        owner_only(actor)
        u = st.user(uid)
        if not u:
            raise HTTPException(404, "user not found")
        if actor.get("id") == uid:
            raise HTTPException(400, "you cannot delete your own account here")
        st.end_all_sessions(uid)
        st.x("DELETE FROM identities WHERE user_id=?", (uid,))
        st.update_user(uid, status="deleted", email=f"deleted-{uid}-{secrets.token_hex(3)}@deleted.invalid", name="",
                       company="", password_hash=None)
        st.audit(actor, "user_deleted", f"user:{uid}", {"email": u["email"]}, _ip(request))
        return {"ok": True}

    @r.post("/owner/users")
    def owner_invite(request: Request, body: Dict[str, Any] = Body(...)):
        """Create an account for a customer (e.g. an enterprise contract) — returns a link to set the password."""
        actor = staff(request)
        email = str(body.get("email", "")).strip().lower()
        if email_problem(email):
            raise HTTPException(400, email_problem(email))
        if st.user_by_email(email):
            raise HTTPException(409, "an account with this e-mail already exists")
        u = st.create_user(email, str(body.get("name", "")), None, role="user", company=str(body.get("company", "")),
                           method="invited")
        extra = int(body.get("credits") or 0)
        if extra:
            st.ledger_add(u["id"], extra, "grant", note="initial credits", by_user=actor.get("id"))
        tok = st.new_token("reset", u["id"], minutes=60 * 24 * 7)
        url = f"{acc.base_url(request)}/#/reset?token={tok}"
        send_mail(email, "Your GeoSyntra Bridge account", f"An account was created for you.\nSet your password here (valid 7 days):\n{url}",
                  acc.outbox)
        st.audit(actor, "user_invited", f"user:{u['id']}", {"email": email, "credits": extra}, _ip(request))
        return {"user": public_user(st.user(u["id"])), "url": url}

    @r.get("/owner/plans")
    def owner_plans(request: Request):
        staff(request)
        return st.plans(False)

    @r.put("/owner/plans/{pid}")
    def owner_put_plan(pid: str, request: Request, body: Dict[str, Any] = Body(...)):
        actor = staff(request)
        owner_only(actor)
        if not pid.replace("_", "").replace("-", "").isalnum() or len(pid) > 40:
            raise HTTPException(400, "plan id: letters, digits, '_' or '-'")
        if body.get("kind", (st.plan(pid) or {}).get("kind", "subscription")) not in ("subscription", "pack", "enterprise"):
            raise HTTPException(400, "kind must be subscription, pack or enterprise")
        price = body.get("stripe_price_id")
        if price and not str(price).startswith("price_"):
            raise HTTPException(400, "a Stripe price id starts with price_")
        p = {k: body[k] for k in ("name", "kind", "price_amount", "currency", "interval", "credits", "stripe_price_id",
                                  "active", "sort", "highlight", "description", "features") if k in body}
        p["id"] = pid
        if not (st.plan(pid) or p.get("name")):
            raise HTTPException(400, "a new plan needs a name")
        st.upsert_plan(p)
        st.audit(actor, "plan_saved", f"plan:{pid}", p, _ip(request))
        return st.plan(pid)

    @r.get("/owner/settings")
    def owner_settings(request: Request):
        staff(request)
        return {"settings": st.settings(), "integrations": _integrations(request)}

    @r.put("/owner/settings")
    def owner_put_settings(request: Request, body: Dict[str, Any] = Body(...)):
        actor = staff(request)
        owner_only(actor)
        clean: Dict[str, Any] = {}
        for k in ("trial_credits", "generate_base_credits", "generate_credits_per_1000_features"):
            if k in body:
                v = int(body[k])
                if v < 0 or v > 100000:
                    raise HTTPException(400, f"{k} out of range")
                clean[k] = v
        for k in ("signup_open", "require_email_verification"):
            if k in body:
                clean[k] = bool(body[k])
        for k in ("company_name", "support_email"):
            if k in body:
                clean[k] = str(body[k])[:120]
        if "payment_instructions" in body:
            clean["payment_instructions"] = str(body["payment_instructions"])[:1000]
        st.set_settings(clean)
        st.audit(actor, "settings_saved", "settings", clean, _ip(request))
        return st.settings()

    @r.get("/owner/payments")
    def owner_payments(request: Request, limit: int = 200):
        staff(request)
        return st.q("SELECT p.*, u.email FROM payments p LEFT JOIN users u ON u.id=p.user_id ORDER BY p.id DESC LIMIT ?",
                    (min(limit, 2000),))

    @r.get("/owner/audit")
    def owner_audit(request: Request, limit: int = 300, action: str = ""):
        staff(request)
        if action:
            return st.q("SELECT * FROM audit WHERE action=? ORDER BY id DESC LIMIT ?", (action, min(limit, 2000)))
        return st.q("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (min(limit, 2000),))

    # ------------------------------------------------------------------ helpers
    def _need_accounts():
        if not accounts_mode:
            raise HTTPException(400, "accounts are disabled on this server (GSB_AUTH_MODE is not 'accounts')")

    def _features(job_id: str) -> int:
        try:
            return int(jobs.results(job_id)["summary"]["features_imported"])
        except Exception:  # noqa: BLE001
            return 0

    def _integrations(request: Request) -> Dict[str, Any]:
        base = acc.base_url(request)
        return {"public_url": base, "public_url_set": bool(env("GSB_PUBLIC_URL")),
                "stripe": stripe_mode(), "stripe_webhook_secret": bool(env("GSB_STRIPE_WEBHOOK_SECRET")),
                "stripe_webhook_url": f"{base}/api/billing/webhook",
                "stripe_events": ["checkout.session.completed", "invoice.paid", "invoice.payment_failed",
                                  "customer.subscription.created", "customer.subscription.updated",
                                  "customer.subscription.deleted"],
                "google": "google" in configured_providers(), "github": "github" in configured_providers(),
                "oauth_redirects": {p: f"{base}/api/auth/oauth/{p}/callback" for p in PROVIDERS},
                "smtp": smtp_configured(), "owners_from_env": sorted(owner_emails()),
                "auth_mode": s.auth_mode}

    # expose for app.py
    r.features_of = _features  # type: ignore[attr-defined]
    return r


def _q(s: str) -> str:
    import urllib.parse
    return urllib.parse.quote(s)


_ = json  # noqa
