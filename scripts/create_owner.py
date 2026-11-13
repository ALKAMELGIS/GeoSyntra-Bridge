#!/usr/bin/env python3
"""Create (or promote) the owner account of a commercial installation:

    python scripts/create_owner.py owner@example.com "Full Name"        (asks for the password)

Owners see the owner console (subscribers, credits, plans, prices, settings, payments, audit log).
Alternatively list the owner e-mails in GSB_OWNER_EMAILS — they become owners when they sign up / sign in.
"""
import getpass
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from geosyntra_bridge.accounts.security import hash_password, password_problem  # noqa: E402
from geosyntra_bridge.accounts.store import Store  # noqa: E402
from geosyntra_bridge.config import settings  # noqa: E402

if len(sys.argv) < 2:
    sys.exit(__doc__)
email, name = sys.argv[1].strip().lower(), (sys.argv[2] if len(sys.argv) > 2 else "")
st = Store(os.path.join(settings.data_dir, "accounts.db"))
u = st.user_by_email(email)
pw = os.environ.get("GSB_OWNER_PASSWORD") or getpass.getpass("Password (8+ characters): ")
if password_problem(pw):
    sys.exit(password_problem(pw))
if u:
    st.update_user(u["id"], role="owner", status="active", password_hash=hash_password(pw), email_verified=1)
    print(f"{email} is now an owner (password updated)")
else:
    u = st.create_user(email, name, hash_password(pw), role="owner", verified=True)
    print(f"owner account created: {email}")
