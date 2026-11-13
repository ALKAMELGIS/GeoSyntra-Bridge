"""
Credits and Stripe.

  * every new account receives the trial credits (owner setting);
  * uploading, mapping, validating and previewing are free — a geodatabase costs
        generate_base_credits + generate_credits_per_1000_features × ceil(features / 1000)
    charged once per job when generation starts; regenerating the same job is free; a failed generation is refunded;
  * subscriptions (Stripe Checkout, mode=subscription) add the plan's credits on every paid invoice — unused credits
    carry over; credit packs (mode=payment) add their credits once;
  * customers manage cards, invoices and cancellation in the Stripe customer portal.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

from .integrations import StripeError, stripe, stripe_configured
from .store import InsufficientCredits, Store, now


def job_ref(job_id: str) -> str:
    return f"job:{job_id}"


def quote(store: Store, features: int) -> int:
    s = store.settings()
    return int(s["generate_base_credits"]) + int(s["generate_credits_per_1000_features"]) * math.ceil(max(0, features) / 1000)


def already_paid(store: Store, job_id: str) -> bool:
    """A job whose charge is still standing (charged and not refunded)."""
    return store.ref_total(job_ref(job_id), ("charge", "refund")) < 0


def exempt(user: Dict[str, Any] | None) -> bool:
    """The owner (and admins) run the service: their geodatabases are never charged."""
    return bool(user) and user.get("role") in ("owner", "admin")


def charge_generation(store: Store, uid: int, job_id: str, features: int) -> Dict[str, Any]:
    if exempt(store.user(uid)):
        return {"charged": 0, "reason": "owner account — not charged"}
    if already_paid(store, job_id):
        return {"charged": 0, "reason": "already paid — regeneration is free"}
    cost = quote(store, features)
    bal = store.ledger_add(uid, -cost, "charge", ref=job_ref(job_id), note=f"geodatabase, {features} features")
    return {"charged": cost, "balance": bal}


def refund_generation(store: Store, job_id: str, reason: str) -> int:
    total = store.ref_total(job_ref(job_id), ("charge", "refund"))
    if total >= 0:
        return 0
    row = store.one("SELECT user_id FROM ledger WHERE ref=? AND kind='charge' ORDER BY id DESC", (job_ref(job_id),))
    if not row:
        return 0
    store.ledger_add(row["user_id"], -total, "refund", ref=job_ref(job_id), note=reason)
    return -total


# --------------------------------------------------------------------------------------- Stripe checkout / portal
def ensure_customer(store: Store, user: Dict[str, Any]) -> str:
    if user.get("stripe_customer_id"):
        return user["stripe_customer_id"]
    c = stripe("POST", "customers", {"email": user["email"], "name": user.get("name") or None,
                                     "metadata": {"user_id": user["id"]}}, idempotency_key=f"cust-{user['id']}")
    store.update_user(user["id"], stripe_customer_id=c["id"])
    return c["id"]


def checkout(store: Store, user: Dict[str, Any], plan_id: str, base_url: str) -> str:
    plan = store.plan(plan_id)
    if not plan or not plan["active"]:
        raise StripeError("unknown plan")
    if plan["kind"] == "enterprise":
        raise StripeError("Enterprise is arranged with our team — contact us")
    if not plan.get("stripe_price_id"):
        raise StripeError(f"the plan '{plan['name']}' has no Stripe price yet (owner console → Plans)")
    if plan["kind"] == "subscription" and user.get("subscription_id") and user.get("subscription_status") in ("active", "trialing", "past_due"):
        raise StripeError("you already have a subscription — change plan or cancel in Manage billing")
    cust = ensure_customer(store, user)
    meta = {"user_id": user["id"], "plan_id": plan["id"]}
    data: Dict[str, Any] = {
        "mode": "subscription" if plan["kind"] == "subscription" else "payment",
        "customer": cust, "client_reference_id": str(user["id"]),
        "line_items": [{"price": plan["stripe_price_id"], "quantity": 1}],
        "success_url": f"{base_url}/#/account?checkout=success", "cancel_url": f"{base_url}/#/account?checkout=cancelled",
        "allow_promotion_codes": True, "metadata": meta,
    }
    if plan["kind"] == "subscription":
        data["subscription_data"] = {"metadata": meta}
    else:
        data["payment_intent_data"] = {"metadata": meta}
        data["invoice_creation"] = {"enabled": True}
    return stripe("POST", "checkout/sessions", data)["url"]


def portal(store: Store, user: Dict[str, Any], base_url: str) -> str:
    cust = ensure_customer(store, user)
    return stripe("POST", "billing_portal/sessions", {"customer": cust, "return_url": f"{base_url}/#/account"})["url"]


# --------------------------------------------------------------------------------------- webhook
def _user_for(store: Store, obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    meta = obj.get("metadata") or {}
    for uid in (meta.get("user_id"), obj.get("client_reference_id")):
        if uid and str(uid).isdigit():
            u = store.user(int(uid))
            if u:
                return u
    cust = obj.get("customer")
    if cust:
        return store.one("SELECT * FROM users WHERE stripe_customer_id=?", (cust,))
    return None


def _iso(ts: Any) -> Optional[str]:
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None


def handle_event(store: Store, ev: Dict[str, Any]) -> str:
    etype, obj = ev.get("type", ""), (ev.get("data") or {}).get("object") or {}
    if ev.get("id") and store.event_seen(ev["id"], etype):
        return "duplicate — already processed"
    if etype == "checkout.session.completed":
        u = _user_for(store, obj)
        if not u:
            return "no matching user"
        if obj.get("customer") and not u.get("stripe_customer_id"):
            store.update_user(u["id"], stripe_customer_id=obj["customer"])
        plan = store.plan((obj.get("metadata") or {}).get("plan_id") or "")
        if obj.get("mode") == "payment" and obj.get("payment_status") == "paid" and plan:
            ref = f"stripe:{obj['id']}"
            if not store.has_ref(ref, "purchase"):
                store.ledger_add(u["id"], int(plan["credits"]), "purchase", ref=ref, note=plan["name"])
            store.add_payment(u["id"], obj["id"], "pack", plan["id"], int(obj.get("amount_total") or 0),
                              obj.get("currency") or plan["currency"], plan["name"])
            return f"pack {plan['id']}: +{plan['credits']} credits"
        if obj.get("mode") == "subscription":
            store.update_user(u["id"], subscription_id=obj.get("subscription"), plan_id=plan["id"] if plan else u.get("plan_id"),
                              subscription_status="active")
            return "subscription linked"
        return "ignored"
    if etype in ("invoice.paid", "invoice.payment_succeeded"):
        u = _user_for(store, obj)
        if not u:
            return "no matching user"
        lines = ((obj.get("lines") or {}).get("data") or [])
        price = next(((ln.get("price") or {}).get("id") for ln in lines if ln.get("price")), None)
        plan = store.plan_by_price(price) if price else None
        if not plan or plan["kind"] != "subscription":
            return "not a subscription invoice"
        ref = f"stripe:{obj['id']}"
        if not store.has_ref(ref, "subscription"):
            store.ledger_add(u["id"], int(plan["credits"]), "subscription", ref=ref, note=f"{plan['name']} — monthly credits")
        period_end = next((_iso((ln.get("period") or {}).get("end")) for ln in lines if ln.get("period")), None)
        store.update_user(u["id"], plan_id=plan["id"], subscription_status="active",
                          subscription_id=obj.get("subscription") or u.get("subscription_id"),
                          current_period_end=period_end or u.get("current_period_end"))
        store.add_payment(u["id"], obj["id"], "subscription", plan["id"], int(obj.get("amount_paid") or 0),
                          obj.get("currency") or plan["currency"], f"{plan['name']} subscription")
        return f"subscription {plan['id']}: +{plan['credits']} credits"
    if etype == "invoice.payment_failed":
        u = _user_for(store, obj)
        if u:
            store.update_user(u["id"], subscription_status="past_due")
        return "payment failed recorded"
    if etype in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        u = _user_for(store, obj)
        if not u:
            return "no matching user"
        items = ((obj.get("items") or {}).get("data") or [])
        price = next(((it.get("price") or {}).get("id") for it in items if it.get("price")), None)
        plan = store.plan_by_price(price) if price else None
        status = "canceled" if etype.endswith("deleted") else obj.get("status")
        store.update_user(u["id"], subscription_id=obj.get("id"), subscription_status=status,
                          plan_id=None if status == "canceled" else (plan["id"] if plan else u.get("plan_id")),
                          current_period_end=_iso(obj.get("current_period_end") or (items[0].get("current_period_end") if items else None)),
                          cancel_at_period_end=int(bool(obj.get("cancel_at_period_end"))))
        return f"subscription {status}"
    return "ignored"


def billing_status(store: Store, user: Dict[str, Any]) -> Dict[str, Any]:
    plan = store.plan(user.get("plan_id") or "") if user.get("plan_id") else None
    return {"credits": user["credits"], "unlimited": exempt(user), "plan": plan, "subscription_status": user.get("subscription_status"),
            "current_period_end": user.get("current_period_end"),
            "cancel_at_period_end": bool(user.get("cancel_at_period_end")),
            "stripe": stripe_configured(), "has_customer": bool(user.get("stripe_customer_id")),
            "ledger": store.ledger(user["id"], 50),
            "payments": store.q("SELECT * FROM payments WHERE user_id=? ORDER BY id DESC LIMIT 50", (user["id"],)),
            "pricing": {k: store.settings()[k] for k in ("generate_base_credits", "generate_credits_per_1000_features")},
            "payment_instructions": store.settings().get("payment_instructions") or "",
            "support_email": store.settings().get("support_email") or "",
            "as_of": now()}


__all__ = ["InsufficientCredits", "quote", "charge_generation", "refund_generation", "checkout", "portal", "handle_event",
           "billing_status", "already_paid"]
