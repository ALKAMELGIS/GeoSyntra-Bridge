"""
External services, configured only through environment variables (secrets never go into the database):

  Google sign-in   GSB_GOOGLE_CLIENT_ID, GSB_GOOGLE_CLIENT_SECRET
  GitHub sign-in   GSB_GITHUB_CLIENT_ID, GSB_GITHUB_CLIENT_SECRET
  Stripe           GSB_STRIPE_SECRET_KEY (sk_live_… / sk_test_…), GSB_STRIPE_WEBHOOK_SECRET (whsec_…)
  E-mail           GSB_SMTP_HOST, GSB_SMTP_PORT (587), GSB_SMTP_USER, GSB_SMTP_PASSWORD, GSB_SMTP_FROM
  Public address   GSB_PUBLIC_URL (https://bridge.example.com) — used for OAuth redirects, Stripe return URLs, e-mails

Provider endpoints can be redirected (GSB_OAUTH_<PROVIDER>_<AUTH|TOKEN|USER|EMAILS>_URL, GSB_STRIPE_API_BASE) for a
corporate proxy or for tests.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import smtplib
import time
import urllib.parse
from email.message import EmailMessage
from typing import Any, Dict, Optional, Tuple

import requests


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


# ============================================================================ OAuth (Google, GitHub)
PROVIDERS: Dict[str, Dict[str, str]] = {
    "google": {"label": "Google", "auth": "https://accounts.google.com/o/oauth2/v2/auth",
               "token": "https://oauth2.googleapis.com/token",
               "user": "https://openidconnect.googleapis.com/v1/userinfo", "scope": "openid email profile"},
    "github": {"label": "GitHub", "auth": "https://github.com/login/oauth/authorize",
               "token": "https://github.com/login/oauth/access_token",
               "user": "https://api.github.com/user", "emails": "https://api.github.com/user/emails",
               "scope": "read:user user:email"},
}


class OAuthError(Exception):
    pass


def provider_conf(name: str) -> Optional[Dict[str, str]]:
    base = PROVIDERS.get(name)
    if not base:
        return None
    cid, sec = env(f"GSB_{name.upper()}_CLIENT_ID"), env(f"GSB_{name.upper()}_CLIENT_SECRET")
    if not cid or not sec:
        return None
    out = dict(base, client_id=cid, client_secret=sec)
    for k in ("auth", "token", "user", "emails"):
        o = env(f"GSB_OAUTH_{name.upper()}_{k.upper()}_URL")
        if o:
            out[k] = o
    return out


def configured_providers() -> Dict[str, str]:
    return {n: p["label"] for n, p in PROVIDERS.items() if provider_conf(n)}


def authorize_url(name: str, redirect_uri: str, state: str) -> str:
    c = provider_conf(name)
    q = {"client_id": c["client_id"], "redirect_uri": redirect_uri, "state": state, "scope": c["scope"],
         "response_type": "code"}
    if name == "google":
        q.update({"access_type": "online", "prompt": "select_account"})
    else:
        q["allow_signup"] = "true"
    return c["auth"] + "?" + urllib.parse.urlencode(q)


def fetch_profile(name: str, code: str, redirect_uri: str) -> Dict[str, Any]:
    """Exchange the code -> {subject, email, email_verified, name, avatar}."""
    c = provider_conf(name)
    if not c:
        raise OAuthError(f"{name} sign-in is not configured")
    try:
        r = requests.post(c["token"], data={"client_id": c["client_id"], "client_secret": c["client_secret"], "code": code,
                                            "redirect_uri": redirect_uri, "grant_type": "authorization_code"},
                          headers={"Accept": "application/json"}, timeout=20)
        tok = r.json().get("access_token") if r.ok else None
        if not tok:
            raise OAuthError(f"{c['label']} refused the sign-in ({r.status_code})")
        h = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
        u = requests.get(c["user"], headers=h, timeout=20).json()
        if name == "google":
            return {"subject": str(u.get("sub")), "email": u.get("email"), "email_verified": bool(u.get("email_verified")),
                    "name": u.get("name") or "", "avatar": u.get("picture") or ""}
        emails = requests.get(c["emails"], headers=h, timeout=20).json()
        prim = next((e for e in emails if e.get("primary") and e.get("verified")), None) or \
            next((e for e in emails if e.get("verified")), None)
        return {"subject": str(u.get("id")), "email": prim["email"] if prim else None, "email_verified": bool(prim),
                "name": u.get("name") or u.get("login") or "", "avatar": u.get("avatar_url") or ""}
    except OAuthError:
        raise
    except Exception as e:  # noqa: BLE001
        raise OAuthError(f"{c['label']} sign-in failed: {e}")


# ============================================================================ Stripe (REST, no SDK needed)
class StripeError(Exception):
    pass


def stripe_configured() -> bool:
    return bool(env("GSB_STRIPE_SECRET_KEY"))


def stripe_mode() -> str:
    k = env("GSB_STRIPE_SECRET_KEY")
    return "live" if k.startswith(("sk_live", "rk_live")) else "test" if k else "not configured"


def _flatten(d: Any, prefix: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(_flatten(v, f"{prefix}[{k}]" if prefix else k))
    elif isinstance(d, list):
        for i, v in enumerate(d):
            out.update(_flatten(v, f"{prefix}[{i}]"))
    elif d is not None:
        out[prefix] = "true" if d is True else "false" if d is False else d
    return out


def stripe(method: str, path: str, data: Optional[Dict[str, Any]] = None, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
    key = env("GSB_STRIPE_SECRET_KEY")
    if not key:
        raise StripeError("Stripe is not configured (GSB_STRIPE_SECRET_KEY)")
    base = env("GSB_STRIPE_API_BASE", "https://api.stripe.com").rstrip("/")
    h = {"Authorization": f"Bearer {key}", "Stripe-Version": "2024-06-20"}
    if idempotency_key:
        h["Idempotency-Key"] = idempotency_key
    try:
        r = requests.request(method, f"{base}/v1/{path.lstrip('/')}", data=_flatten(data or {}), headers=h, timeout=30)
    except Exception as e:  # noqa: BLE001
        raise StripeError(f"Stripe not reachable: {e}")
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not r.ok:
        raise StripeError((j.get("error") or {}).get("message") or f"Stripe HTTP {r.status_code}")
    return j


def verify_webhook(payload: bytes, header: str, tolerance: int = 300) -> Dict[str, Any]:
    """Stripe-Signature: t=<ts>,v1=<hex hmac-sha256 of 't.payload'>."""
    secret = env("GSB_STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise StripeError("GSB_STRIPE_WEBHOOK_SECRET is not set")
    parts: Dict[str, list] = {}
    for kv in (header or "").split(","):
        k, _, v = kv.strip().partition("=")
        parts.setdefault(k, []).append(v)
    try:
        ts = int(parts["t"][0])
    except (KeyError, ValueError):
        raise StripeError("missing signature timestamp")
    if abs(time.time() - ts) > tolerance:
        raise StripeError("signature timestamp outside the tolerance")
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in parts.get("v1", [])):
        raise StripeError("invalid signature")
    return json.loads(payload.decode("utf-8"))


def sign_webhook(payload: bytes, secret: str, ts: Optional[int] = None) -> str:
    """Build a Stripe-Signature header (tests and local replays)."""
    ts = ts or int(time.time())
    return f"t={ts},v1={hmac.new(secret.encode(), f'{ts}.'.encode() + payload, hashlib.sha256).hexdigest()}"


# ============================================================================ e-mail
def smtp_configured() -> bool:
    return bool(env("GSB_SMTP_HOST"))


def send_mail(to: str, subject: str, body: str, outbox: str) -> Tuple[bool, str]:
    """Send through SMTP when configured; always keep a copy in <data>/mail_outbox for the owner."""
    msg = EmailMessage()
    msg["From"] = env("GSB_SMTP_FROM") or env("GSB_SMTP_USER") or "no-reply@localhost"
    msg["To"], msg["Subject"] = to, subject
    msg.set_content(body)
    os.makedirs(outbox, exist_ok=True)
    with open(os.path.join(outbox, f"{int(time.time() * 1000)}_{to.replace('@', '_at_')}.eml"), "wb") as f:
        f.write(bytes(msg))
    if not smtp_configured():
        return False, "SMTP not configured — message kept in the outbox"
    try:
        port = int(env("GSB_SMTP_PORT", "587"))
        cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
        with cls(env("GSB_SMTP_HOST"), port, timeout=20) as s:
            if port != 465:
                s.starttls()
            if env("GSB_SMTP_USER"):
                s.login(env("GSB_SMTP_USER"), env("GSB_SMTP_PASSWORD"))
            s.send_message(msg)
        return True, "sent"
    except Exception as e:  # noqa: BLE001
        return False, f"SMTP error: {e}"
