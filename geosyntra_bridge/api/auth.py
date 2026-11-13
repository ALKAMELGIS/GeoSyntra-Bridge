"""
Authentication hook. The module reuses the host platform's identity:

  GSB_AUTH_MODE=none    single-user / localhost (default)
  GSB_AUTH_MODE=header  the platform's reverse proxy authenticates and passes the
                        user in GSB_USER_HEADER (default X-Forwarded-User)
  GSB_AUTH_MODE=token   Bearer tokens from GSB_API_TOKENS ("user:token,user2:token2")
  GSB_AUTH_MODE=accounts  commercial: own user accounts (password, Google, GitHub), credits and Stripe billing

Administrators (schema import, authorised rule overwrite) are listed in GSB_ADMIN_USERS.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request

from ..config import Settings


@dataclass
class Principal:
    user: str
    admin: bool
    uid: Optional[int] = None          # accounts mode: the account id (jobs are owned by "u<id>")
    email: str = ""
    role: str = ""


def make_auth(s: Settings, accounts=None):
    tokens = {}
    for t in s.api_tokens:
        user, _, tok = t.partition(":")
        if tok:
            tokens[tok] = user
        else:
            tokens[user] = "api"
    admins = set(s.admin_users)

    def dep(request: Request) -> Principal:
        if s.auth_mode == "none":
            return Principal("local", True)
        if s.auth_mode == "header":
            u = request.headers.get(s.user_header, "").strip()
            if not u:
                raise HTTPException(401, "not authenticated")
            return Principal(u, u in admins)
        if s.auth_mode == "token":
            h = request.headers.get("Authorization", "")
            tok = h[7:].strip() if h.lower().startswith("bearer ") else request.query_params.get("token", "")
            for known, user in tokens.items():
                if tok and hmac.compare_digest(tok, known):
                    return Principal(user, user in admins)
            raise HTTPException(401, "invalid or missing token")
        if s.auth_mode == "accounts" and accounts is not None:
            u = accounts.current(request)
            if not u:
                raise HTTPException(401, "sign in required")
            return Principal(f"u{u['id']}", u["role"] in ("owner", "admin"), u["id"], u["email"], u["role"])
        raise HTTPException(500, f"unknown GSB_AUTH_MODE '{s.auth_mode}'")
    return dep
