from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import os
import logging
import uuid
import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr
from emergentintegrations.payments.stripe.checkout import (
    StripeCheckout, CheckoutSessionRequest
)


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
    """User has Pro access if their pro_until is in the future."""
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


class CheckoutIn(BaseModel):
    plan: str  # "monthly" | "annual"
    origin_url: str


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
    pro = _is_pro(full)
    pro_until = full.get("pro_until")
    days_left = 0
    if pro_until:
        try:
            delta = datetime.fromisoformat(pro_until) - datetime.now(timezone.utc)
            days_left = max(0, delta.days + (1 if delta.seconds > 0 else 0))
        except Exception:
            pass
    return {
        "plan": full.get("plan") or ("pro" if pro else "basic"),
        "pro": pro,
        "pro_until": pro_until,
        "days_left": days_left,
        "trial_used": bool(full.get("trial_used")),
        "is_admin": full.get("role") == "admin",
    }


@api.post("/me/trial")
async def start_trial(user: dict = Depends(get_current_user)):
    full = await db.users.find_one({"id": user["id"]})
    if full.get("trial_used"):
        raise HTTPException(status_code=400, detail="Free trial already used")
    if _is_pro(full):
        raise HTTPException(status_code=400, detail="You already have an active Pro plan")
    cfg = await _get_plan_config()
    until = datetime.now(timezone.utc) + timedelta(days=int(cfg.get("trial_days", 7)))
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"plan": "trial", "pro_until": until.isoformat(), "trial_used": True}},
    )
    return {"ok": True, "pro_until": until.isoformat()}


@api.post("/me/checkout")
async def create_checkout(body: CheckoutIn, request: Request, user: dict = Depends(get_current_user)):
    if body.plan not in ("monthly", "annual"):
        raise HTTPException(status_code=400, detail="Invalid plan")
    if not STRIPE_API_KEY:
        raise HTTPException(status_code=500, detail="Payments not configured")

    cfg = await _get_plan_config()
    pkg = cfg[body.plan]
    amount = float(pkg["price"])  # SERVER-SIDE truth — never trust client
    currency = cfg.get("currency", "usd")

    host_url = str(request.base_url).rstrip("/")
    webhook_url = f"{host_url}/api/webhook/stripe"
    sc = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url=webhook_url)

    origin = body.origin_url.rstrip("/")
    success_url = f"{origin}/?stripe_session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{origin}/?stripe_canceled=1"

    metadata = {
        "user_id": user["id"],
        "email": user["email"],
        "plan": body.plan,
        "days": str(pkg["days"]),
    }
    req = CheckoutSessionRequest(
        amount=amount,
        currency=currency,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=metadata,
    )
    session = await sc.create_checkout_session(req)

    # Create pending transaction BEFORE redirect
    await db.payment_transactions.insert_one({
        "session_id": session.session_id,
        "user_id": user["id"],
        "email": user["email"],
        "plan": body.plan,
        "amount": amount,
        "currency": currency,
        "days": int(pkg["days"]),
        "status": "initiated",
        "payment_status": "pending",
        "metadata": metadata,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fulfilled": False,
    })
    return {"url": session.url, "session_id": session.session_id}


async def _fulfill_payment(tx: dict):
    """Idempotently apply a successful payment to the user's plan."""
    if tx.get("fulfilled"):
        return
    user_id = tx["user_id"]
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
        {"$set": {"fulfilled": True, "fulfilled_at": now.isoformat()}},
    )


@api.get("/payments/status/{session_id}")
async def payment_status(session_id: str, request: Request, user: dict = Depends(get_current_user)):
    tx = await db.payment_transactions.find_one({"session_id": session_id, "user_id": user["id"]})
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    host_url = str(request.base_url).rstrip("/")
    webhook_url = f"{host_url}/api/webhook/stripe"
    sc = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url=webhook_url)
    status = await sc.get_checkout_status(session_id)

    update = {
        "status": status.status,
        "payment_status": status.payment_status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.payment_transactions.update_one({"session_id": session_id}, {"$set": update})

    if status.payment_status == "paid" and not tx.get("fulfilled"):
        await _fulfill_payment({**tx, **update})

    return {
        "session_id": session_id,
        "status": status.status,
        "payment_status": status.payment_status,
        "amount_total": status.amount_total,
        "currency": status.currency,
        "fulfilled": (status.payment_status == "paid"),
    }


@api.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    if not STRIPE_API_KEY:
        return {"received": False}
    body = await request.body()
    sig = request.headers.get("Stripe-Signature", "")
    host_url = str(request.base_url).rstrip("/")
    webhook_url = f"{host_url}/api/webhook/stripe"
    sc = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url=webhook_url)
    try:
        resp = await sc.handle_webhook(body, sig)
    except Exception as e:
        logger.warning(f"Stripe webhook error: {e}")
        return {"received": True}
    if resp.payment_status == "paid" and resp.session_id:
        tx = await db.payment_transactions.find_one({"session_id": resp.session_id})
        if tx and not tx.get("fulfilled"):
            await _fulfill_payment(tx)
    return {"received": True}


@api.get("/me/transactions")
async def my_transactions(user: dict = Depends(get_current_user)):
    items = await db.payment_transactions.find(
        {"user_id": user["id"]},
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
    elif not verify_password(admin_password, existing["password_hash"]):
        await db.users.update_one(
            {"email": admin_email},
            {"$set": {"password_hash": hash_password(admin_password)}},
        )


@app.on_event("shutdown")
async def shutdown():
    client.close()
