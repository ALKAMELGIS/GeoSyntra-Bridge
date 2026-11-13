"""Password hashing (scrypt, standard library), login throttling and input checks."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

N, R, P = 2 ** 14, 8, 1
EMAIL_RX = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=N, r=R, p=P, dklen=32)
    return f"scrypt${N}${R}${P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(pw: str, stored: Optional[str]) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        # constant-ish time even when the account has no password (OAuth-only)
        hashlib.scrypt(b"x", salt=b"0" * 16, n=N, r=R, p=P, dklen=32)
        return False
    _, n, r, p, salt, dk = stored.split("$")
    got = hashlib.scrypt(pw.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    return hmac.compare_digest(got, base64.b64decode(dk))


def password_problem(pw: str) -> Optional[str]:
    if len(pw or "") < 8:
        return "the password must have at least 8 characters"
    if len(pw) > 200:
        return "the password is too long"
    if pw.isdigit() or pw.lower() in ("password", "12345678", "qwertyui"):
        return "choose a less common password"
    return None


def email_problem(email: str) -> Optional[str]:
    return None if EMAIL_RX.match((email or "").strip()) else "enter a valid email address"


class Throttle:
    """At most `limit` failures per key within `window` seconds."""

    def __init__(self, limit: int = 8, window: int = 900):
        self.limit, self.window = limit, window
        self._f: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, key: str) -> Deque[float]:
        d = self._f[key]
        t = time.time() - self.window
        while d and d[0] < t:
            d.popleft()
        return d

    def blocked(self, key: str) -> Tuple[bool, int]:
        with self._lock:
            d = self._trim(key)
            if len(d) >= self.limit:
                return True, int(d[0] + self.window - time.time()) + 1
            return False, 0

    def fail(self, key: str):
        with self._lock:
            self._trim(key).append(time.time())

    def clear(self, key: str):
        with self._lock:
            self._f.pop(key, None)

    def clear_prefix(self, prefix: str):
        with self._lock:
            for k in [k for k in self._f if k.startswith(prefix)]:
                self._f.pop(k, None)
