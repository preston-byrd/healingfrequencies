from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import os
import asyncio
import logging
import uuid
import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr
from emergentintegrations.payments.stripe.checkout import (
    StripeCheckout, CheckoutSessionRequest
)


# Hard cap on every outbound Stripe call. Cloudflare's edge cuts the
# connection at 100s and reports "origin returned an invalid or incomplete
# response" if we don't respond in time — by capping at 25s we always have
# headroom to return a clean JSON 502 instead of a half-open socket.
STRIPE_CALL_TIMEOUT = 25


# --- Setup --------------------------------------------------------------------
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

JWT_ALGORITHM = "HS256"
JWT_SECRET = os.environ["JWT_SECRET"]
STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY", "")

# Fixed plan packages — amounts ALWAYS resolved server-side, never trusted from client.
# Admin can override the displayed/charged amount via /api/admin/plan-prices, which writes
# overrides into the plan_config collection. Defaults below are used until admin overrides.
DEFAULT_PLAN_CONFIG = {
    "monthly": {"price": 9.99, "days": 30, "label": "Pro Monthly"},
    "annual":  {"price": 60.00, "days": 365, "label": "Pro Annual"},
    "currency": "usd",
    "trial_days": 7,
}

app = FastAPI()
api = APIRouter(prefix="/api")


# --- Auth helpers -------------------------------------------------------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id, "email": email, "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def set_auth_cookie(response: Response, token: str):
    response.set_cookie(
        key="access_token", value=token, httponly=True,
        secure=True, samesite="none", max_age=7 * 24 * 3600, path="/",
    )


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user = await db.users.find_one({"id": payload["sub"]})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        user.pop("password_hash", None)
        user.pop("_id", None)
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# --- Models -------------------------------------------------------------------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    name: Optional[str] = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class SessionIn(BaseModel):
    name: str
    frequency: float
    waveform: str = "sine"
    binaural: float = 0
    duration_minutes: int = 10
    ambient: dict = Field(default_factory=dict)  # {rain: 0..1, ocean: 0..1, forest: 0..1}
    breathwork: bool = False


class Session(SessionIn):
    id: str
    user_id: str
    created_at: str


# --- Auth routes --------------------------------------------------------------
@api.post("/auth/register")
async def register(body: RegisterIn, response: Response):
    email = body.email.lower()
    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    user = {
        "id": str(uuid.uuid4()),
        "email": email,
        "name": body.name or email.split("@")[0],
        "password_hash": hash_password(body.password),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.insert_one(user)
    token = create_access_token(user["id"], email)
    set_auth_cookie(response, token)
    return {"id": user["id"], "email": email, "name": user["name"], "token": token}


@api.post("/auth/login")
async def login(body: LoginIn, response: Response):
    email = body.email.lower()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(user["id"], email)
    set_auth_cookie(response, token)
    return {"id": user["id"], "email": email, "name": user.get("name", ""), "token": token}


@api.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    return {"ok": True}


@api.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return user


# --- Sessions (favorites) -----------------------------------------------------
@api.get("/sessions")
async def list_sessions(user: dict = Depends(get_current_user)):
    items = await db.sessions.find({"user_id": user["id"]}, {"_id": 0}).sort("created_at", -1).to_list(200)
    return items


@api.post("/sessions")
async def create_session(body: SessionIn, user: dict = Depends(get_current_user)):
    # Feature gate: Basic plan caps saved sessions at 3
    if not _is_pro(user):
        count = await db.sessions.count_documents({"user_id": user["id"]})
        if count >= 3:
            raise HTTPException(
                status_code=402,
                detail="Free plan saves up to 3 sessions. Upgrade to Pro for unlimited saves.",
            )
    doc = body.model_dump()
    doc.update({
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    await db.sessions.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api.delete("/sessions/{sid}")
async def delete_session(sid: str, user: dict = Depends(get_current_user)):
    res = await db.sessions.delete_one({"id": sid, "user_id": user["id"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}


# --- Streak / Check-in --------------------------------------------------------
def _today_utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _date_minus(d_iso: str, days: int) -> str:
    from datetime import date as _date
    y, m, dd = map(int, d_iso.split("-"))
    return _date.fromordinal(_date(y, m, dd).toordinal() - days).isoformat()


class CheckinIn(BaseModel):
    minutes: float = 0


@api.get("/streak")
async def get_streak(user: dict = Depends(get_current_user)):
    doc = await db.streaks.find_one({"user_id": user["id"]}, {"_id": 0})
    if not doc:
        return {
            "current_streak": 0, "longest_streak": 0, "last_check_in": None,
            "total_sessions": 0, "total_minutes": 0,
            "checked_in_today": False,
        }
    doc["checked_in_today"] = (doc.get("last_check_in") == _today_utc_date())
    # If streak is stale (skipped a day), reflect that without writing
    last = doc.get("last_check_in")
    today = _today_utc_date()
    if last and last != today and last != _date_minus(today, 1):
        doc["current_streak"] = 0
    return doc


@api.post("/streak/checkin")
async def checkin(body: CheckinIn, user: dict = Depends(get_current_user)):
    today = _today_utc_date()
    existing = await db.streaks.find_one({"user_id": user["id"]})
    minutes = max(0.0, float(body.minutes or 0))

    if not existing:
        doc = {
            "user_id": user["id"],
            "current_streak": 1,
            "longest_streak": 1,
            "last_check_in": today,
            "total_sessions": 1,
            "total_minutes": minutes,
        }
        await db.streaks.insert_one(doc)
        doc.pop("_id", None)
        doc["checked_in_today"] = True
        return doc

    last = existing.get("last_check_in")
    current = int(existing.get("current_streak", 0))
    longest = int(existing.get("longest_streak", 0))
    total_sessions = int(existing.get("total_sessions", 0)) + 1
    total_minutes = float(existing.get("total_minutes", 0)) + minutes

    if last == today:
        # Already checked in today — just bump totals.
        pass
    elif last == _date_minus(today, 1):
        current += 1
    else:
        current = 1

    longest = max(longest, current)
    update = {
        "current_streak": current,
        "longest_streak": longest,
        "last_check_in": today,
        "total_sessions": total_sessions,
        "total_minutes": total_minutes,
    }
    await db.streaks.update_one({"user_id": user["id"]}, {"$set": update})
    update["user_id"] = user["id"]
    update["checked_in_today"] = True
    return update


# --- Subscription / Billing --------------------------------------------------
def _is_pro(user: dict) -> bool:
    """User has Pro access if they are admin OR their pro_until is in the future."""
    if user.get("role") == "admin":
        return True
    pu = user.get("pro_until")
    if not pu:
        return False
    try:
        return datetime.fromisoformat(pu) > datetime.now(timezone.utc)
    except Exception:
        return False


async def _get_plan_config() -> dict:
    doc = await db.plan_config.find_one({"_id": "current"}) or {}
    cfg = {**DEFAULT_PLAN_CONFIG, **{k: v for k, v in doc.items() if k != "_id"}}
    return cfg


def _require_admin(user: dict):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6)


class PrefsIn(BaseModel):
    """User-saved 'last used config' on the dashboard. All fields optional so
    the frontend can do partial updates as values change."""
    frequency: Optional[float] = Field(None, ge=0.1, le=20000)
    duration_minutes: Optional[int] = Field(None, ge=1, le=180)
    waveform: Optional[str] = Field(None, pattern=r"^(sine|triangle|square|sawtooth)$")
    binaural: Optional[float] = Field(None, ge=0, le=40)
    golden_stack: Optional[bool] = None
    breathwork: Optional[bool] = None
    ambient: Optional[Dict[str, float]] = None
    tone_volume: Optional[float] = Field(None, ge=0, le=1)


class ProfileUpdateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class CheckoutIn(BaseModel):
    plan: str  # "monthly" | "annual"
    origin_url: str
    # How the user wants to pay — recorded in metadata for analytics. All values
    # produce the same Stripe Checkout Session; the frontend chooses how to
    # present the resulting URL (redirect vs QR/copy).
    payment_method_preference: Optional[str] = Field(
        default="card",
        pattern=r"^(card|apple_pay|google_pay|link)$",
    )


class PlanPricesIn(BaseModel):
    monthly_price: Optional[float] = Field(None, gt=0, le=10000)
    annual_price: Optional[float] = Field(None, gt=0, le=100000)
    trial_days: Optional[int] = Field(None, ge=0, le=90)


@api.post("/me/password")
async def change_password(body: PasswordChangeIn, user: dict = Depends(get_current_user)):
    full = await db.users.find_one({"id": user["id"]})
    if not verify_password(body.current_password, full["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"password_hash": hash_password(body.new_password)}},
    )
    return {"ok": True}


@api.put("/me/profile")
async def update_profile(body: ProfileUpdateIn, user: dict = Depends(get_current_user)):
    new_name = body.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"name": new_name}},
    )
    return {"id": user["id"], "email": user["email"], "name": new_name}


@api.get("/plan/config")
async def get_plan_config_public():
    cfg = await _get_plan_config()
    # Don't leak internal fields; expose what the UI needs.
    return {
        "currency": cfg.get("currency", "usd"),
        "monthly": {"price": cfg["monthly"]["price"], "days": cfg["monthly"]["days"], "label": cfg["monthly"]["label"]},
        "annual": {"price": cfg["annual"]["price"], "days": cfg["annual"]["days"], "label": cfg["annual"]["label"]},
        "trial_days": cfg.get("trial_days", 7),
    }


@api.get("/me/subscription")
async def my_subscription(user: dict = Depends(get_current_user)):
    full = await db.users.find_one({"id": user["id"]}, {"_id": 0, "password_hash": 0}) or {}
    is_admin = full.get("role") == "admin"
    pro = _is_pro(full)
    pro_until = full.get("pro_until")
    days_left = 0
    if pro_until:
        try:
            delta = datetime.fromisoformat(pro_until) - datetime.now(timezone.utc)
            days_left = max(0, delta.days + (1 if delta.seconds > 0 else 0))
        except Exception:
            pass
    # Admin always shows as pro (lifetime access)
    sub_status = full.get("stripe_subscription_status")
    in_trial = sub_status == "trialing"
    plan = "admin" if is_admin else (full.get("plan") or ("pro" if pro else "basic"))
    return {
        "plan": plan,
        "pro": pro,
        "pro_until": pro_until,
        "days_left": days_left,
        "trial_used": bool(full.get("trial_used")),
        "is_admin": is_admin,
        "stripe_subscription_status": sub_status,
        "in_trial": in_trial,
        "trial_end": full.get("stripe_trial_end"),
        "cancel_at_period_end": bool(full.get("stripe_cancel_at_period_end")),
        "has_billing_portal": bool(full.get("stripe_customer_id")),
        "payment_failed_at": full.get("payment_failed_at"),
    }


@api.post("/me/trial")
async def start_trial(user: dict = Depends(get_current_user)):
    """DEPRECATED — the no-card trial path. The new policy (Feb 2026) requires
    a payment method to start the 7-day trial; clients should call
    POST /me/checkout instead, which embeds `trial_period_days=7` into a
    Stripe Subscription. We keep this endpoint returning a clear 410 so any
    stale frontend redirects users to the new flow instead of silently
    granting access without billing setup."""
    raise HTTPException(
        status_code=410,
        detail=(
            "The free trial now requires a payment method. "
            "Use POST /api/me/checkout with plan=monthly or plan=annual to "
            "start a Stripe Checkout session — the first 7 days are free and "
            "you can cancel anytime before billing begins."
        ),
    )


@api.get("/me/prefs")
async def get_my_prefs(user: dict = Depends(get_current_user)):
    """Return the user's last-saved dashboard config so the player can restore it on login."""
    full = await db.users.find_one({"id": user["id"]}, {"_id": 0, "prefs": 1}) or {}
    return full.get("prefs") or {}


@api.put("/me/prefs")
async def update_my_prefs(body: PrefsIn, user: dict = Depends(get_current_user)):
    """Persist the user's last-used dashboard config (frequency, ambient mix, duration, etc.).
    Merges with existing prefs — frontend can send partial updates.

    Defense-in-depth: silently ignores writes to Pro-only fields from non-Pro users
    so a stale UI state can't clobber a user's saved Pro config after a downgrade.
    """
    payload = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if not payload:
        return {"ok": True}
    # Strip Pro-only fields when the user isn't Pro.
    full = await db.users.find_one({"id": user["id"]})
    if not _is_pro(full):
        for k in ("golden_stack", "breathwork", "binaural", "ambient"):
            payload.pop(k, None)
        if not payload:
            return {"ok": True}
    # Merge with existing prefs (nested ambient dict gets replaced wholesale if sent).
    update_doc = {f"prefs.{k}": v for k, v in payload.items()}
    update_doc["prefs.updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.users.update_one({"id": user["id"]}, {"$set": update_doc})
    return {"ok": True}


# ---------- Stripe helper ----------
def _normalise_stripe_api_base():
    """Defense against the upstream library's sticky module-level mutation —
    see iter 21 RCA. Idempotent; call before every Stripe SDK call."""
    import stripe as _stripe
    if "sk_test_emergent" in STRIPE_API_KEY:
        _stripe.api_base = "https://integrations.emergentagent.com/stripe"
    else:
        _stripe.api_base = "https://api.stripe.com"
    _stripe.api_key = STRIPE_API_KEY


def _stripe_client(webhook_url: str) -> StripeCheckout:
    """Create a StripeCheckout instance and normalise the global `stripe.api_base`.
    Used by the legacy one-time payment path and the webhook handler.
    """
    _normalise_stripe_api_base()
    return StripeCheckout(api_key=STRIPE_API_KEY, webhook_url=webhook_url)


async def _stripe_call(fn, *args, **kwargs):
    """Run a synchronous Stripe SDK call in a thread with a hard timeout.
    Raises HTTPException(502) on timeout — guaranteed JSON response within
    STRIPE_CALL_TIMEOUT seconds so Cloudflare never sees an incomplete reply.
    """
    _normalise_stripe_api_base()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(fn, *args, **kwargs),
            timeout=STRIPE_CALL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error("[stripe] call %s timed out after %ds", getattr(fn, "__qualname__", fn), STRIPE_CALL_TIMEOUT)
        raise HTTPException(
            status_code=502,
            detail=f"Stripe is taking too long to respond (>{STRIPE_CALL_TIMEOUT}s). Please try again in a moment.",
        )


async def _get_or_create_stripe_customer(user: dict) -> str:
    """Return the user's Stripe customer ID, creating one on first call."""
    import stripe as _stripe
    full = await db.users.find_one({"id": user["id"]}) or {}
    cust_id = full.get("stripe_customer_id")
    if cust_id:
        return cust_id
    customer = await _stripe_call(
        _stripe.Customer.create,
        email=user["email"],
        metadata={"user_id": user["id"], "name": full.get("name", "")},
    )
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"stripe_customer_id": customer.id}},
    )
    return customer.id


def _interval_for_plan(plan: str) -> str:
    return "year" if plan == "annual" else "month"


async def _sync_subscription_to_user(user_id: str, subscription) -> dict:
    """Project a Stripe Subscription onto the user's pro_until / plan fields so
    the existing _is_pro logic keeps working unchanged. Returns the patch dict
    that was applied (for logging/tests)."""
    sub_status = subscription.get("status") if isinstance(subscription, dict) else subscription.status
    period_end = subscription.get("current_period_end") if isinstance(subscription, dict) else subscription.current_period_end
    trial_end = subscription.get("trial_end") if isinstance(subscription, dict) else subscription.trial_end
    cancel_at_period_end = subscription.get("cancel_at_period_end") if isinstance(subscription, dict) else subscription.cancel_at_period_end
    sub_id = subscription.get("id") if isinstance(subscription, dict) else subscription.id

    active_states = {"trialing", "active", "past_due"}  # past_due keeps access while Stripe retries
    pro = sub_status in active_states
    pro_until_dt = None
    if period_end:
        pro_until_dt = datetime.fromtimestamp(int(period_end), tz=timezone.utc)

    patch = {
        "stripe_subscription_id": sub_id,
        "stripe_subscription_status": sub_status,
        "stripe_cancel_at_period_end": bool(cancel_at_period_end),
        "stripe_trial_end": (datetime.fromtimestamp(int(trial_end), tz=timezone.utc).isoformat() if trial_end else None),
    }
    if pro and pro_until_dt:
        patch["pro_until"] = pro_until_dt.isoformat()
        patch["plan"] = "trial" if sub_status == "trialing" else "pro"
        if sub_status == "trialing":
            patch["trial_used"] = True
    elif sub_status in ("canceled", "incomplete_expired", "unpaid"):
        # Revoke access by clearing pro_until in the past
        patch["pro_until"] = datetime.now(timezone.utc).isoformat()
        patch["plan"] = "basic"

    await db.users.update_one({"id": user_id}, {"$set": patch})
    return patch


@api.post("/me/checkout")
async def create_checkout(body: CheckoutIn, request: Request, user: dict = Depends(get_current_user)):
    """Create a Stripe Checkout Session in SUBSCRIPTION mode with a 7-day trial.

    Behavior change (Feb 2026): we no longer create one-time payments. Every
    new Pro signup is a recurring subscription with `trial_period_days=7`,
    which means:
      * card is collected upfront at signup
      * no charge until the 7-day trial expires
      * Stripe auto-charges monthly/annually after trial
      * user can cancel anytime via the Customer Portal (/me/billing-portal)

    Returns {url, session_id} just like before so the frontend redirect path
    is unchanged. The Cloudflare-friendly 25-second hard timeout is applied
    to every outbound Stripe call.
    """
    if body.plan not in ("monthly", "annual"):
        raise HTTPException(status_code=400, detail="Invalid plan")
    if not STRIPE_API_KEY:
        raise HTTPException(status_code=500, detail="Payments not configured — STRIPE_API_KEY is missing in backend .env")

    cfg = await _get_plan_config()
    pkg = cfg[body.plan]
    amount = float(pkg["price"])
    currency = cfg.get("currency", "usd")
    interval = _interval_for_plan(body.plan)
    unit_amount_cents = int(round(amount * 100))

    # Reuse trial only if the user hasn't used theirs yet — Stripe will reject
    # `trial_period_days` on a customer who already had a trial on this price,
    # so we mirror that on our side defensively.
    full = await db.users.find_one({"id": user["id"]}) or {}
    trial_days = int(cfg.get("trial_days", 7))
    include_trial = not full.get("trial_used")

    customer_id = await _get_or_create_stripe_customer(user)

    origin = body.origin_url.rstrip("/")
    success_url = f"{origin}/?stripe_session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{origin}/?stripe_canceled=1"

    metadata = {
        "user_id": user["id"],
        "email": user["email"],
        "plan": body.plan,
        "days": str(pkg["days"]),
        "payment_method_preference": body.payment_method_preference or "card",
        "includes_trial": "true" if include_trial else "false",
    }

    import stripe as _stripe
    logger.info(
        "[checkout] user=%s plan=%s method=%s key_prefix=%s api_base=%s trial=%s customer=%s",
        user.get("email"), body.plan, body.payment_method_preference,
        (STRIPE_API_KEY[:12] + "…") if STRIPE_API_KEY else "(none)",
        _stripe.api_base, include_trial, customer_id,
    )

    subscription_data = {"metadata": metadata}
    if include_trial:
        subscription_data["trial_period_days"] = trial_days

    try:
        session = await _stripe_call(
            _stripe.checkout.Session.create,
            mode="subscription",
            customer=customer_id,
            line_items=[{
                "price_data": {
                    "currency": currency,
                    "product_data": {"name": pkg.get("label", f"Pro {body.plan.title()}")},
                    "unit_amount": unit_amount_cents,
                    "recurring": {"interval": interval},
                },
                "quantity": 1,
            }],
            payment_method_types=["card"],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata=metadata,
            subscription_data=subscription_data,
            allow_promotion_codes=True,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[checkout] Stripe session.create failed for user=%s plan=%s", user.get("email"), body.plan)
        raise HTTPException(
            status_code=502,
            detail=f"Stripe checkout failed: {str(e)}. Verify STRIPE_API_KEY is valid and recurring USD payments are enabled in your Stripe Dashboard.",
        )

    if not getattr(session, "url", None):
        logger.error("[checkout] Stripe returned no URL for user=%s plan=%s session=%r", user.get("email"), body.plan, session)
        raise HTTPException(status_code=502, detail="Stripe did not return a checkout URL")

    await db.payment_transactions.insert_one({
        "session_id": session.id,
        "user_id": user["id"],
        "email": user["email"],
        "plan": body.plan,
        "amount": amount,
        "currency": currency,
        "days": int(pkg["days"]),
        "interval": interval,
        "mode": "subscription",
        "includes_trial": include_trial,
        "status": "initiated",
        "payment_status": "pending",
        "metadata": metadata,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fulfilled": False,
    })
    return {"url": session.url, "session_id": session.id}


async def _fulfill_payment(tx: dict):
    """Idempotently apply a successful payment to the user's plan.

    For SUBSCRIPTION mode (current default): pulls the Stripe Subscription and
    projects status / current_period_end / trial_end onto the user record via
    _sync_subscription_to_user. Source of truth is always Stripe.

    For one-time PAYMENT mode (legacy txs only): just extends pro_until by the
    package's `days` count.
    """
    if tx.get("fulfilled"):
        return
    user_id = tx["user_id"]
    is_sub = tx.get("mode") == "subscription"
    if is_sub:
        # Look up the Stripe Subscription via the Checkout Session
        import stripe as _stripe
        session = await _stripe_call(_stripe.checkout.Session.retrieve, tx["session_id"])
        sub_id = session.subscription
        if sub_id:
            subscription = await _stripe_call(_stripe.Subscription.retrieve, sub_id)
            await _sync_subscription_to_user(user_id, subscription)
    else:
        days = int(tx.get("days", 30))
        full = await db.users.find_one({"id": user_id}) or {}
        now = datetime.now(timezone.utc)
        current_until = None
        pu = full.get("pro_until")
        if pu:
            try:
                current_until = datetime.fromisoformat(pu)
            except Exception:
                current_until = None
        base = current_until if (current_until and current_until > now) else now
        new_until = base + timedelta(days=days)
        await db.users.update_one(
            {"id": user_id},
            {"$set": {"plan": "pro", "pro_until": new_until.isoformat()}},
        )
    await db.payment_transactions.update_one(
        {"session_id": tx["session_id"]},
        {"$set": {"fulfilled": True, "fulfilled_at": datetime.now(timezone.utc).isoformat()}},
    )


@api.get("/payments/status/{session_id}")
async def payment_status(session_id: str, request: Request, user: dict = Depends(get_current_user)):
    tx = await db.payment_transactions.find_one({"session_id": session_id, "user_id": user["id"]})
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    import stripe as _stripe
    try:
        session = await _stripe_call(_stripe.checkout.Session.retrieve, session_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[status] retrieve failed for sid=%s", session_id)
        raise HTTPException(status_code=502, detail=f"Stripe status lookup failed: {e}")

    update = {
        "status": session.status,
        "payment_status": session.payment_status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.payment_transactions.update_one({"session_id": session_id}, {"$set": update})

    # In subscription mode the session is "complete" with payment_status="paid"
    # (or "no_payment_required" for trial-only signups). Either way fulfilment
    # should fire so the trial activates immediately.
    fulfilled = (session.status == "complete") and not tx.get("fulfilled")
    if fulfilled:
        await _fulfill_payment({**tx, **update})

    return {
        "session_id": session_id,
        "status": session.status,
        "payment_status": session.payment_status,
        "amount_total": getattr(session, "amount_total", None),
        "currency": getattr(session, "currency", None),
        "fulfilled": (session.status == "complete"),
        "plan": tx.get("plan"),
    }


@api.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    """Handles both legacy one-time payment events AND subscription lifecycle
    events (customer.subscription.{updated,deleted}, invoice.payment_{succeeded,failed}).
    For subscriptions, we re-project Stripe state onto the user record via
    _sync_subscription_to_user so the existing _is_pro logic keeps working.
    """
    if not STRIPE_API_KEY:
        return {"received": False}
    body = await request.body()
    sig = request.headers.get("Stripe-Signature", "")
    import stripe as _stripe
    _normalise_stripe_api_base()

    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    event = None
    try:
        if webhook_secret and sig:
            event = _stripe.Webhook.construct_event(body, sig, webhook_secret)
        else:
            # Without a webhook secret we trust the polling path (/payments/status)
            # for fulfilment. Still parse the body so subscription state stays fresh.
            import json
            event = json.loads(body.decode("utf-8"))
    except Exception as e:
        logger.warning("[webhook] could not parse: %s", e)
        return {"received": True}

    et = event.get("type") if isinstance(event, dict) else event["type"]
    data_obj = (event.get("data") or {}).get("object") if isinstance(event, dict) else event["data"]["object"]
    logger.info("[webhook] event=%s", et)

    try:
        if et == "checkout.session.completed":
            sid = data_obj.get("id") if isinstance(data_obj, dict) else data_obj.id
            tx = await db.payment_transactions.find_one({"session_id": sid})
            if tx and not tx.get("fulfilled"):
                await _fulfill_payment(tx)

        elif et in ("customer.subscription.updated", "customer.subscription.created", "customer.subscription.deleted"):
            sub_id = data_obj.get("id") if isinstance(data_obj, dict) else data_obj.id
            cust_id = data_obj.get("customer") if isinstance(data_obj, dict) else data_obj.customer
            full = await db.users.find_one({"stripe_customer_id": cust_id})
            if full:
                await _sync_subscription_to_user(full["id"], data_obj if isinstance(data_obj, dict) else data_obj.to_dict())
            else:
                logger.warning("[webhook] sub %s for unknown customer %s", sub_id, cust_id)

        elif et == "invoice.payment_failed":
            cust_id = data_obj.get("customer") if isinstance(data_obj, dict) else data_obj.customer
            full = await db.users.find_one({"stripe_customer_id": cust_id})
            if full:
                # Flag the failure so the dashboard banner can surface it.
                await db.users.update_one(
                    {"id": full["id"]},
                    {"$set": {"payment_failed_at": datetime.now(timezone.utc).isoformat()}},
                )

        elif et == "invoice.payment_succeeded":
            cust_id = data_obj.get("customer") if isinstance(data_obj, dict) else data_obj.customer
            sub_id = data_obj.get("subscription") if isinstance(data_obj, dict) else data_obj.subscription
            if sub_id:
                subscription = await _stripe_call(_stripe.Subscription.retrieve, sub_id)
                full = await db.users.find_one({"stripe_customer_id": cust_id})
                if full:
                    await _sync_subscription_to_user(full["id"], subscription)
                    # Clear any prior payment-failed flag
                    await db.users.update_one({"id": full["id"]}, {"$unset": {"payment_failed_at": ""}})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[webhook] handler failed for event=%s: %s", et, e)

    return {"received": True}


@api.post("/me/billing-portal")
async def billing_portal(request: Request, user: dict = Depends(get_current_user)):
    """Return a Stripe Customer Portal URL so users can manage their subscription
    (cancel, update card, see invoices). Requires that the user has previously
    completed a checkout (so we have a stripe_customer_id on file)."""
    if not STRIPE_API_KEY:
        raise HTTPException(status_code=500, detail="Payments not configured")
    full = await db.users.find_one({"id": user["id"]}) or {}
    cust_id = full.get("stripe_customer_id")
    if not cust_id:
        raise HTTPException(status_code=400, detail="No active subscription found. Start one from the Pro plan card to manage billing.")

    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    return_url = (payload.get("return_url") or "").rstrip("/")
    if not return_url:
        # Fall back to the request's own origin if the client didn't pass one
        host_url = str(request.base_url).rstrip("/")
        return_url = host_url

    import stripe as _stripe
    try:
        portal = await _stripe_call(
            _stripe.billing_portal.Session.create,
            customer=cust_id,
            return_url=return_url,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[billing-portal] create failed for user=%s", user.get("email"))
        raise HTTPException(
            status_code=502,
            detail=(
                f"Could not open Customer Portal: {e}. "
                "Make sure the Stripe Customer Portal is activated at "
                "Dashboard → Settings → Billing → Customer portal."
            ),
        )
    return {"url": portal.url}


@api.post("/me/cancel-subscription")
async def cancel_subscription(user: dict = Depends(get_current_user)):
    """In-app cancellation fallback: marks the user's active Stripe subscription
    to cancel at the end of the current period. The Customer Portal is the
    preferred UX; this endpoint exists so we can offer a one-click cancel CTA
    too (e.g., from an email link or trial-ending banner).
    """
    if not STRIPE_API_KEY:
        raise HTTPException(status_code=500, detail="Payments not configured")
    full = await db.users.find_one({"id": user["id"]}) or {}
    sub_id = full.get("stripe_subscription_id")
    if not sub_id:
        raise HTTPException(status_code=400, detail="No active subscription to cancel.")
    import stripe as _stripe
    try:
        sub = await _stripe_call(
            _stripe.Subscription.modify,
            sub_id,
            cancel_at_period_end=True,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[cancel] failed for user=%s", user.get("email"))
        raise HTTPException(status_code=502, detail=f"Could not cancel subscription: {e}")
    await _sync_subscription_to_user(user["id"], sub)
    return {"ok": True, "cancel_at_period_end": True}


@api.get("/me/transactions")
async def my_transactions(user: dict = Depends(get_current_user)):
    # Per UX requirement: surface ONLY active paid plans in Billing History.
    # Pending/initiated/expired/canceled transactions are kept in the DB (we still need
    # them for /payments/status polling + webhook reconciliation) but hidden from the user.
    items = await db.payment_transactions.find(
        {
            "user_id": user["id"],
            "$or": [
                {"payment_status": "paid"},
                {"fulfilled": True},
            ],
        },
        {"_id": 0, "metadata": 0},
    ).sort("created_at", -1).to_list(100)
    return items


@api.get("/admin/plan-prices")
async def admin_get_prices(user: dict = Depends(get_current_user)):
    _require_admin(user)
    return await _get_plan_config()


@api.put("/admin/plan-prices")
async def admin_update_prices(body: PlanPricesIn, user: dict = Depends(get_current_user)):
    _require_admin(user)
    update = {}
    if body.monthly_price is not None:
        update["monthly.price"] = float(body.monthly_price)
    if body.annual_price is not None:
        update["annual.price"] = float(body.annual_price)
    if body.trial_days is not None:
        update["trial_days"] = int(body.trial_days)
    if not update:
        raise HTTPException(status_code=400, detail="No changes provided")
    await db.plan_config.update_one({"_id": "current"}, {"$set": update}, upsert=True)
    return await _get_plan_config()


class GrantProIn(BaseModel):
    days: int = Field(365, ge=1, le=3650)


@api.get("/admin/users")
async def admin_list_users(
    q: str = "",
    user: dict = Depends(get_current_user),
):
    _require_admin(user)
    query = {}
    if q:
        query = {"email": {"$regex": q.strip(), "$options": "i"}}
    cursor = db.users.find(query, {"_id": 0, "password_hash": 0}).sort("created_at", -1).limit(200)
    items = await cursor.to_list(200)
    now = datetime.now(timezone.utc)
    for u in items:
        pu = u.get("pro_until")
        is_pro = False
        days_left = 0
        if pu:
            try:
                until = datetime.fromisoformat(pu)
                is_pro = until > now
                if is_pro:
                    days_left = max(0, (until - now).days + 1)
            except Exception:
                pass
        u["pro"] = is_pro
        u["days_left"] = days_left
        u["plan"] = u.get("plan") or ("pro" if is_pro else "basic")
    return items


@api.post("/admin/users/{user_id}/grant-pro")
async def admin_grant_pro(user_id: str, body: GrantProIn, user: dict = Depends(get_current_user)):
    _require_admin(user)
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    now = datetime.now(timezone.utc)
    current_until = None
    pu = target.get("pro_until")
    if pu:
        try:
            current_until = datetime.fromisoformat(pu)
        except Exception:
            current_until = None
    base = current_until if (current_until and current_until > now) else now
    new_until = base + timedelta(days=int(body.days))
    await db.users.update_one(
        {"id": user_id},
        {"$set": {"plan": "pro", "pro_until": new_until.isoformat()}},
    )
    return {
        "ok": True,
        "user_id": user_id,
        "email": target["email"],
        "plan": "pro",
        "pro_until": new_until.isoformat(),
        "days_added": int(body.days),
    }


@api.post("/admin/users/{user_id}/revoke-pro")
async def admin_revoke_pro(user_id: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    await db.users.update_one(
        {"id": user_id},
        {"$set": {"plan": "basic", "pro_until": None}},
    )
    return {"ok": True, "user_id": user_id, "plan": "basic"}


@api.delete("/admin/users/{user_id}")
async def admin_delete_user(user_id: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    if user_id == user["id"]:
        raise HTTPException(status_code=400, detail="You cannot delete your own admin account")
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.get("role") == "admin":
        raise HTTPException(status_code=400, detail="Cannot delete another admin")
    # Cascade delete all data scoped to this user
    await db.users.delete_one({"id": user_id})
    await db.sessions.delete_many({"user_id": user_id})
    await db.streaks.delete_many({"user_id": user_id})
    await db.payment_transactions.delete_many({"user_id": user_id})
    return {"ok": True, "user_id": user_id, "email": target["email"], "deleted": True}


# --- App setup ----------------------------------------------------------------
@api.get("/")
async def root():
    return {"message": "Healing Frequencies API"}


app.include_router(api)

origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
if origins == ["*"]:
    # When wildcard is desired, use a regex so we can still send credentials
    # (browsers reject allow_origin="*" combined with allow_credentials=True).
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=".*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@app.on_event("startup")
async def startup():
    await db.users.create_index("email", unique=True)
    await db.sessions.create_index([("user_id", 1), ("created_at", -1)])
    await db.streaks.create_index("user_id", unique=True)
    await db.payment_transactions.create_index("session_id", unique=True)
    await db.payment_transactions.create_index([("user_id", 1), ("created_at", -1)])
    # Seed plan_config if missing
    existing_cfg = await db.plan_config.find_one({"_id": "current"})
    if not existing_cfg:
        await db.plan_config.insert_one({"_id": "current", **DEFAULT_PLAN_CONFIG})
    # seed admin
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@example.com").lower()
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
    existing = await db.users.find_one({"email": admin_email})
    if not existing:
        await db.users.insert_one({
            "id": str(uuid.uuid4()),
            "email": admin_email,
            "name": "Admin",
            "password_hash": hash_password(admin_password),
            "role": "admin",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"Seeded admin: {admin_email}")
    else:
        updates = {}
        if existing.get("role") != "admin":
            updates["role"] = "admin"
        if not verify_password(admin_password, existing["password_hash"]):
            updates["password_hash"] = hash_password(admin_password)
        if updates:
            await db.users.update_one({"email": admin_email}, {"$set": updates})
            logger.info(f"Updated admin fields: {list(updates.keys())}")


@app.on_event("shutdown")
async def shutdown():
    client.close()
