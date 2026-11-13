"""
Accounts database (SQLite, one file in the data folder): users, sign-in identities (Google / GitHub), sessions,
credit ledger, plans, payments, settings and the owner's audit log.

Credits are only ever changed through `ledger_add`, which writes the ledger row and the new balance in one
transaction, so a user's balance always equals the sum of their ledger.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT DEFAULT '',
  company TEXT DEFAULT '',
  password_hash TEXT,
  role TEXT NOT NULL DEFAULT 'user',          -- owner | admin | user
  status TEXT NOT NULL DEFAULT 'active',      -- active | suspended | deleted
  email_verified INTEGER NOT NULL DEFAULT 0,
  avatar_url TEXT DEFAULT '',
  credits INTEGER NOT NULL DEFAULT 0,
  stripe_customer_id TEXT,
  plan_id TEXT,
  subscription_id TEXT,
  subscription_status TEXT,
  current_period_end TEXT,
  cancel_at_period_end INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  last_login_at TEXT,
  signup_method TEXT DEFAULT 'password'
);
CREATE TABLE IF NOT EXISTS identities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider TEXT NOT NULL, subject TEXT NOT NULL, email TEXT, created_at TEXT NOT NULL,
  UNIQUE(provider, subject)
);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash TEXT NOT NULL UNIQUE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL, last_seen TEXT, ip TEXT, user_agent TEXT
);
CREATE TABLE IF NOT EXISTS tokens (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, user_id INTEGER, data TEXT,
  expires_at TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  delta INTEGER NOT NULL, balance_after INTEGER NOT NULL,
  kind TEXT NOT NULL,                         -- trial | subscription | purchase | grant | revoke | charge | refund
  ref TEXT, note TEXT, by_user INTEGER, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_user ON ledger(user_id, id);
CREATE INDEX IF NOT EXISTS ledger_ref ON ledger(ref);
CREATE TABLE IF NOT EXISTS plans (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL, kind TEXT NOT NULL,     -- subscription | pack | enterprise
  price_amount INTEGER DEFAULT 0,             -- minor units (fils / cents), display + revenue estimate
  currency TEXT DEFAULT 'aed', interval TEXT DEFAULT 'month',
  credits INTEGER NOT NULL DEFAULT 0, stripe_price_id TEXT,
  active INTEGER NOT NULL DEFAULT 1, sort INTEGER DEFAULT 0, highlight INTEGER DEFAULT 0,
  description TEXT DEFAULT '', features TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER, stripe_id TEXT UNIQUE, kind TEXT, plan_id TEXT,
  amount INTEGER, currency TEXT, description TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  plan_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected | cancelled
  reference TEXT NOT NULL, note TEXT DEFAULT '', amount INTEGER DEFAULT 0, currency TEXT DEFAULT 'aed',
  created_at TEXT NOT NULL, decided_at TEXT, decided_by INTEGER, decision_note TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS requests_status ON requests(status, id);
CREATE TABLE IF NOT EXISTS stripe_events (id TEXT PRIMARY KEY, type TEXT, received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor_id INTEGER, actor_email TEXT,
  action TEXT NOT NULL, target TEXT, detail TEXT, ip TEXT
);
"""

DEFAULT_SETTINGS: Dict[str, Any] = {
    "trial_credits": 50,                    # free credits on sign-up
    "generate_base_credits": 10,            # one geodatabase (validation, previews and re-generation are free)
    "generate_credits_per_1000_features": 5,
    "signup_open": True,
    "require_email_verification": False,
    "company_name": "GeoSyntra",
    "support_email": "",
    "payment_instructions": "",             # bank / invoice details shown on a purchase request (no online payment)
}

DEFAULT_PLANS = [
    {"id": "starter", "name": "Starter", "kind": "subscription", "price_amount": 17900, "currency": "aed",
     "interval": "month", "credits": 300, "sort": 1, "highlight": 0,
     "description": "For a site engineer delivering a few as-built packages a month.",
     "features": ["300 credits / month (~20 geodatabases)", "All seven ADM templates", "DMT QC replica & submission package",
                  "DEM elevation (Copernicus / SRTM / your DTM)", "Email support"]},
    {"id": "professional", "name": "Professional", "kind": "subscription", "price_amount": 54900, "currency": "aed",
     "interval": "month", "credits": 1200, "sort": 2, "highlight": 1,
     "description": "For consultants and contractors handing over projects every week.",
     "features": ["1,200 credits / month (~90 geodatabases)", "Everything in Starter", "Saved mapping profiles & learned rules",
                  "Reference GIS spatial join", "Priority support"]},
    {"id": "enterprise", "name": "Enterprise", "kind": "enterprise", "price_amount": 0, "currency": "aed",
     "interval": "month", "credits": 0, "sort": 3, "highlight": 0,
     "description": "Municipal departments and large programmes.",
     "features": ["Volume credits", "On-premise or private cloud", "Template updates on publication", "SSO & dedicated support"]},
    {"id": "pack_100", "name": "100 credits", "kind": "pack", "price_amount": 9900, "currency": "aed", "interval": "",
     "credits": 100, "sort": 10, "highlight": 0, "description": "One-off top-up, never expires.", "features": []},
    {"id": "pack_500", "name": "500 credits", "kind": "pack", "price_amount": 39900, "currency": "aed", "interval": "",
     "credits": 500, "sort": 11, "highlight": 0, "description": "One-off top-up, never expires.", "features": []},
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso_in(**kw) -> str:
    return (datetime.now(timezone.utc) + timedelta(**kw)).isoformat(timespec="seconds")


def token_hash(tok: str) -> str:
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()


class InsufficientCredits(Exception):
    def __init__(self, needed: int, balance: int):
        super().__init__(f"{needed} credits needed, {balance} available")
        self.needed, self.balance = needed, balance


class Store:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(SCHEMA)
        self._seed()

    # ------------------------------------------------------------------ helpers
    def q(self, sql: str, args: Iterable[Any] = ()) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.db.execute(sql, tuple(args)).fetchall()]

    def one(self, sql: str, args: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql: str, args: Iterable[Any] = ()) -> int:
        with self._lock:
            cur = self.db.execute(sql, tuple(args))
            return cur.lastrowid

    def _seed(self):
        for p in DEFAULT_PLANS:
            if not self.one("SELECT id FROM plans WHERE id=?", (p["id"],)):
                self.upsert_plan(p)

    # ------------------------------------------------------------------ settings
    def settings(self) -> Dict[str, Any]:
        out = dict(DEFAULT_SETTINGS)
        for r in self.q("SELECT key, value FROM settings"):
            try:
                out[r["key"]] = json.loads(r["value"])
            except ValueError:
                pass
        return out

    def set_settings(self, values: Dict[str, Any]):
        for k, v in values.items():
            if k in DEFAULT_SETTINGS:
                self.x("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (k, json.dumps(v)))

    # ------------------------------------------------------------------ users
    def user(self, uid: int) -> Optional[Dict[str, Any]]:
        return self.one("SELECT * FROM users WHERE id=?", (uid,))

    def user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        return self.one("SELECT * FROM users WHERE email=? COLLATE NOCASE", (email.strip(),))

    def create_user(self, email: str, name: str = "", password_hash: Optional[str] = None, role: str = "user",
                    verified: bool = False, method: str = "password", avatar: str = "", company: str = "") -> Dict[str, Any]:
        uid = self.x("INSERT INTO users(email, name, company, password_hash, role, email_verified, created_at, signup_method, avatar_url) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", (email.strip().lower(), name.strip(), company.strip(), password_hash, role,
                                                    int(verified), now(), method, avatar))
        trial = int(self.settings().get("trial_credits") or 0)
        if trial > 0:
            self.ledger_add(uid, trial, "trial", note="free trial credits")
        return self.user(uid)

    def update_user(self, uid: int, **kw):
        allowed = {"name", "company", "password_hash", "role", "status", "email_verified", "avatar_url", "stripe_customer_id",
                   "plan_id", "subscription_id", "subscription_status", "current_period_end", "cancel_at_period_end",
                   "last_login_at", "email"}
        kw = {k: v for k, v in kw.items() if k in allowed}
        if not kw:
            return
        sets = ", ".join(f"{k}=?" for k in kw)
        self.x(f"UPDATE users SET {sets} WHERE id=?", (*kw.values(), uid))

    def link_identity(self, uid: int, provider: str, subject: str, email: str):
        self.x("INSERT OR IGNORE INTO identities(user_id, provider, subject, email, created_at) VALUES(?,?,?,?,?)",
               (uid, provider, subject, email, now()))

    def identity_user(self, provider: str, subject: str) -> Optional[Dict[str, Any]]:
        return self.one("SELECT u.* FROM identities i JOIN users u ON u.id=i.user_id WHERE i.provider=? AND i.subject=?",
                        (provider, subject))

    # ------------------------------------------------------------------ sessions / one-time tokens
    def new_session(self, uid: int, ip: str = "", ua: str = "", days: int = 30) -> str:
        tok = secrets.token_urlsafe(32)
        self.x("INSERT INTO sessions(token_hash, user_id, created_at, expires_at, last_seen, ip, user_agent) VALUES(?,?,?,?,?,?,?)",
               (token_hash(tok), uid, now(), iso_in(days=days), now(), ip[:64], ua[:200]))
        self.update_user(uid, last_login_at=now())
        return tok

    def session_user(self, tok: str) -> Optional[Dict[str, Any]]:
        if not tok:
            return None
        r = self.one("SELECT s.id AS sid, s.expires_at, s.last_seen, u.* FROM sessions s JOIN users u ON u.id=s.user_id "
                     "WHERE s.token_hash=?", (token_hash(tok),))
        if not r or r["expires_at"] < now():
            return None
        if (r.get("last_seen") or "") < iso_in(minutes=-5):
            self.x("UPDATE sessions SET last_seen=? WHERE id=?", (now(), r["sid"]))
        return r

    def end_session(self, tok: str):
        self.x("DELETE FROM sessions WHERE token_hash=?", (token_hash(tok),))

    def end_all_sessions(self, uid: int):
        self.x("DELETE FROM sessions WHERE user_id=?", (uid,))

    def new_token(self, kind: str, uid: Optional[int], data: Any = None, minutes: int = 60) -> str:
        tok = secrets.token_urlsafe(32)
        self.x("INSERT INTO tokens(kind, token_hash, user_id, data, expires_at) VALUES(?,?,?,?,?)",
               (kind, token_hash(tok), uid, json.dumps(data), iso_in(minutes=minutes)))
        return tok

    def use_token(self, kind: str, tok: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            r = self.one("SELECT * FROM tokens WHERE kind=? AND token_hash=?", (kind, token_hash(tok or "")))
            if not r or r["used"] or r["expires_at"] < now():
                return None
            self.x("UPDATE tokens SET used=1 WHERE id=?", (r["id"],))
            r["data"] = json.loads(r["data"] or "null")
            return r

    # ------------------------------------------------------------------ credits
    def ledger_add(self, uid: int, delta: int, kind: str, ref: Optional[str] = None, note: str = "",
                   by_user: Optional[int] = None, allow_negative: bool = False) -> int:
        """Atomically change a balance. Raises InsufficientCredits for a debit larger than the balance."""
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                bal = self.db.execute("SELECT credits FROM users WHERE id=?", (uid,)).fetchone()
                if bal is None:
                    raise KeyError("user not found")
                new = bal[0] + int(delta)
                if new < 0 and not allow_negative:
                    raise InsufficientCredits(-int(delta), bal[0])
                self.db.execute("UPDATE users SET credits=? WHERE id=?", (new, uid))
                self.db.execute("INSERT INTO ledger(user_id, delta, balance_after, kind, ref, note, by_user, created_at) "
                                "VALUES(?,?,?,?,?,?,?,?)", (uid, int(delta), new, kind, ref, note, by_user, now()))
                self.db.execute("COMMIT")
                return new
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def ledger(self, uid: int, limit: int = 100) -> List[Dict[str, Any]]:
        return self.q("SELECT * FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit))

    def ref_total(self, ref: str, kinds: Iterable[str]) -> int:
        ks = list(kinds)
        r = self.one(f"SELECT COALESCE(SUM(delta),0) AS s FROM ledger WHERE ref=? AND kind IN ({','.join('?' * len(ks))})",
                     (ref, *ks))
        return int(r["s"]) if r else 0

    def has_ref(self, ref: str, kind: str) -> bool:
        return self.one("SELECT 1 AS x FROM ledger WHERE ref=? AND kind=?", (ref, kind)) is not None

    # ------------------------------------------------------------------ plans / payments
    def plans(self, active_only: bool = True) -> List[Dict[str, Any]]:
        rows = self.q("SELECT * FROM plans" + (" WHERE active=1" if active_only else "") + " ORDER BY sort, id")
        for r in rows:
            r["features"] = json.loads(r.get("features") or "[]")
            r["active"], r["highlight"] = bool(r["active"]), bool(r["highlight"])
        return rows

    def plan(self, pid: str) -> Optional[Dict[str, Any]]:
        return next((p for p in self.plans(False) if p["id"] == pid), None)

    def plan_by_price(self, price_id: str) -> Optional[Dict[str, Any]]:
        return next((p for p in self.plans(False) if p.get("stripe_price_id") and p["stripe_price_id"] == price_id), None)

    def upsert_plan(self, p: Dict[str, Any]):
        cols = ["id", "name", "kind", "price_amount", "currency", "interval", "credits", "stripe_price_id", "active", "sort",
                "highlight", "description", "features"]
        cur = self.plan(p["id"]) or {}
        v = {**{"active": 1, "sort": 0, "highlight": 0, "currency": "aed", "interval": "month", "price_amount": 0,
                "credits": 0, "description": "", "features": [], "stripe_price_id": None, "kind": "subscription"},
             **cur, **p}
        v["features"] = json.dumps(v.get("features") or [])
        v["active"], v["highlight"] = int(bool(v["active"])), int(bool(v["highlight"]))
        self.x(f"INSERT INTO plans({','.join(cols)}) VALUES({','.join('?' * len(cols))}) ON CONFLICT(id) DO UPDATE SET "
               + ", ".join(f"{c}=excluded.{c}" for c in cols[1:]), [v[c] for c in cols])

    def add_payment(self, uid: Optional[int], stripe_id: str, kind: str, plan_id: Optional[str], amount: int,
                    currency: str, description: str) -> bool:
        with self._lock:
            if self.one("SELECT 1 AS x FROM payments WHERE stripe_id=?", (stripe_id,)):
                return False
            self.x("INSERT INTO payments(user_id, stripe_id, kind, plan_id, amount, currency, description, created_at) "
                   "VALUES(?,?,?,?,?,?,?,?)", (uid, stripe_id, kind, plan_id, amount, currency, description, now()))
            return True

    # ------------------------------------------------------------------ purchase requests (no online payment)
    def add_request(self, uid: int, plan: Dict[str, Any], note: str = "") -> Dict[str, Any]:
        pend = self.one("SELECT * FROM requests WHERE user_id=? AND plan_id=? AND status='pending'", (uid, plan["id"]))
        if pend:
            return pend
        rid = self.x("INSERT INTO requests(user_id, plan_id, status, reference, note, amount, currency, created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (uid, plan["id"], "pending", "", note[:300], int(plan.get("price_amount") or 0), plan.get("currency") or "aed", now()))
        ref = f"GSB-{rid:05d}"
        self.x("UPDATE requests SET reference=? WHERE id=?", (ref, rid))
        return self.one("SELECT * FROM requests WHERE id=?", (rid,))

    def requests(self, uid: Optional[int] = None, status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        sql, args = "SELECT r.*, u.email, u.name FROM requests r JOIN users u ON u.id=r.user_id WHERE 1=1", []
        if uid is not None:
            sql += " AND r.user_id=?"; args.append(uid)
        if status:
            sql += " AND r.status=?"; args.append(status)
        return self.q(sql + " ORDER BY r.id DESC LIMIT ?", args + [limit])

    def decide_request(self, rid: int, approve: bool, by: Optional[int], note: str = "") -> Dict[str, Any]:
        with self._lock:
            r = self.one("SELECT * FROM requests WHERE id=?", (rid,))
            if not r:
                raise KeyError("request not found")
            if r["status"] != "pending":
                raise ValueError(f"already {r['status']}")
            self.x("UPDATE requests SET status=?, decided_at=?, decided_by=?, decision_note=? WHERE id=?",
                   ("approved" if approve else "rejected", now(), by, note[:300], rid))
            if approve:
                plan = self.plan(r["plan_id"]) or {}
                if plan.get("credits"):
                    self.ledger_add(r["user_id"], int(plan["credits"]),
                                    "subscription" if plan.get("kind") == "subscription" else "purchase",
                                    ref=f"request:{r['reference']}", note=f"{plan.get('name', r['plan_id'])} — {r['reference']}", by_user=by)
                if plan.get("kind") == "subscription":
                    u = self.user(r["user_id"]) or {}
                    start = u.get("current_period_end") if (u.get("plan_id") == plan["id"] and u.get("current_period_end")
                                                            and u["current_period_end"] > now()) else None
                    end = (datetime.fromisoformat(start) + timedelta(days=30)).isoformat(timespec="seconds") if start else iso_in(days=30)
                    self.update_user(r["user_id"], plan_id=plan["id"], subscription_status="active",
                                     current_period_end=end, cancel_at_period_end=0)
                self.x("INSERT INTO payments(user_id, stripe_id, kind, plan_id, amount, currency, description, created_at) VALUES (?,?,?,?,?,?,?,?)",
                       (r["user_id"], f"manual:{r['reference']}", "manual", r["plan_id"], r["amount"], r["currency"],
                        f"{plan.get('name', r['plan_id'])} (invoice {r['reference']})", now()))
            return self.one("SELECT * FROM requests WHERE id=?", (rid,))

    def renew_manual(self, uid: int) -> Optional[Dict[str, Any]]:
        """A subscription paid by invoice whose month is over: past_due + a renewal request (once) for the owner to approve."""
        u = self.user(uid)
        if not u or u.get("subscription_id") or u.get("subscription_status") not in ("active", "past_due") or not u.get("plan_id"):
            return None                                   # Stripe subscriptions renew through Stripe
        if not u.get("current_period_end") or u["current_period_end"] > now():
            return None
        plan = self.plan(u["plan_id"])
        if not plan:
            return None
        if u["subscription_status"] != "past_due":
            self.update_user(uid, subscription_status="past_due")
        return self.add_request(uid, plan, "renewal")

    def cancel_request(self, rid: int, uid: int) -> bool:
        return bool(self.db.execute("UPDATE requests SET status='cancelled', decided_at=? WHERE id=? AND user_id=? AND status='pending'",
                                    (now(), rid, uid)).rowcount)

    def event_seen(self, eid: str, etype: str) -> bool:
        """Record a Stripe event id; True when it was already processed (webhooks are retried)."""
        with self._lock:
            if self.one("SELECT 1 AS x FROM stripe_events WHERE id=?", (eid,)):
                return True
            self.x("INSERT INTO stripe_events(id, type, received_at) VALUES(?,?,?)", (eid, etype, now()))
            return False

    # ------------------------------------------------------------------ audit
    def audit(self, actor: Optional[Dict[str, Any]], action: str, target: str = "", detail: Any = None, ip: str = ""):
        self.x("INSERT INTO audit(at, actor_id, actor_email, action, target, detail, ip) VALUES(?,?,?,?,?,?,?)",
               (now(), (actor or {}).get("id"), (actor or {}).get("email"), action, target,
                json.dumps(detail, ensure_ascii=False) if detail is not None else None, ip[:64]))


def public_user(u: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("id", "email", "name", "company", "role", "status", "email_verified", "avatar_url", "credits", "plan_id",
            "subscription_status", "current_period_end", "cancel_at_period_end", "created_at", "last_login_at", "signup_method")
    out = {k: u.get(k) for k in keys}
    out["email_verified"] = bool(out["email_verified"])
    out["cancel_at_period_end"] = bool(out["cancel_at_period_end"])
    out["has_password"] = bool(u.get("password_hash"))
    return out


_ = time  # noqa
