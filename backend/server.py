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
from typing import Dict, Optional

from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr
from emergentintegrations.payments.stripe.checkout import (
    StripeCheckout,
)
from emergentintegrations.llm.chat import LlmChat, UserMessage
import json
import re
try:
    import resend as _resend  # transactional email — optional, gracefully no-op if key missing
except Exception:  # pragma: no cover
    _resend = None


# Hard cap on every outbound Stripe call. Cloudflare's edge cuts the
# connection at 100s and reports "origin returned an invalid or incomplete
# response" if we don't respond in time — by capping at 25s we always have
# headroom to return a clean JSON 502 instead of a half-open socket.
STRIPE_CALL_TIMEOUT = 25


# --- In-memory rate limiter --------------------------------------------------
# Lightweight per-key token bucket. Good enough for single-instance deployments
# to stop accidental hammering + obvious abuse without bringing in Redis. For
# multi-replica deployments this should move to a shared store.
from collections import defaultdict
import time as _time
import threading as _threading

_rl_buckets: dict = defaultdict(lambda: {"tokens": 0.0, "ts": 0.0})
_rl_lock = _threading.Lock()


def _rate_limit(key: str, capacity: float, refill_per_sec: float) -> bool:
    """Return True if the request is allowed, False if it should be rejected.
    Burst up to `capacity`; refill at `refill_per_sec`. Per-key state."""
    now = _time.monotonic()
    with _rl_lock:
        b = _rl_buckets[key]
        elapsed = now - b["ts"] if b["ts"] else 0
        b["tokens"] = min(capacity, b["tokens"] + elapsed * refill_per_sec)
        if b["ts"] == 0:
            b["tokens"] = capacity  # first hit — full bucket
        b["ts"] = now
        if b["tokens"] >= 1.0:
            b["tokens"] -= 1.0
            return True
        return False


def _rate_limit_or_429(key: str, capacity: float, refill_per_sec: float, label: str = "request"):
    if not _rate_limit(key, capacity, refill_per_sec):
        # Bump the AI-throttle counter when an AI endpoint is the caller (we
        # key those buckets with the "ai:" prefix); other 429s land in a
        # generic counter. Pure observability — doesn't change behaviour.
        bucket = "ai_throttle_hits" if key.startswith("ai:") else "throttle_hits"
        _bump_metric(bucket)
        raise HTTPException(status_code=429, detail=f"Too many {label}s — please slow down")


# --- Security metrics + audit log -------------------------------------------
# Hourly rolling counters in memory. Powers GET /api/admin/security. For a
# single-instance preview/prod this is fine; multi-replica would need Redis.
from collections import deque as _deque
_METRICS_LOCK = _threading.Lock()
_METRIC_BUCKETS: dict = {  # event_name -> deque[(ts_epoch_seconds, count)]
    "failed_logins": _deque(maxlen=240),     # ~10 days @ 1/hour
    "successful_logins": _deque(maxlen=240),
    "registrations": _deque(maxlen=240),
    "ai_throttle_hits": _deque(maxlen=240),
    "throttle_hits": _deque(maxlen=240),
    "webhook_signature_rejections": _deque(maxlen=240),
    "session_revocations": _deque(maxlen=240),
}


def _current_hour_ts() -> int:
    now = int(_time.time())
    return now - (now % 3600)


def _bump_metric(name: str, n: int = 1):
    if name not in _METRIC_BUCKETS:
        return
    hour = _current_hour_ts()
    with _METRICS_LOCK:
        b = _METRIC_BUCKETS[name]
        if b and b[-1][0] == hour:
            ts, count = b[-1]
            b[-1] = (ts, count + n)
        else:
            b.append((hour, n))


def _metric_summary(name: str) -> dict:
    """Return last_hour, last_24h, last_7d totals for a counter."""
    now = _current_hour_ts()
    with _METRICS_LOCK:
        buckets = list(_METRIC_BUCKETS.get(name, ()))
    last_hour = sum(c for ts, c in buckets if ts == now)
    last_24h = sum(c for ts, c in buckets if ts >= now - 23 * 3600)
    last_7d = sum(c for ts, c in buckets if ts >= now - 7 * 24 * 3600)
    return {"last_hour": last_hour, "last_24h": last_24h, "last_7d": last_7d}


def _client_ip(request: Request) -> str:
    # Trust the first IP from X-Forwarded-For (set by the Emergent ingress);
    # fall back to direct client. Truncate to keep audit rows compact.
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if xff:
        return xff[:64]
    try:
        return (request.client.host or "unknown")[:64]
    except Exception:
        return "unknown"


async def _audit(
    event: str,
    request: Optional[Request],
    *,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> None:
    """Append a row to the `audit_log` collection. Best-effort — never raises
    out of the caller (the caller's flow must not depend on logging).
    Persisted to MongoDB so events survive restarts (unlike the rolling
    in-memory counters above).
    """
    try:
        doc = {
            "id": str(uuid.uuid4()),
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "ip": _client_ip(request) if request else None,
            "user_id": user_id,
            "user_email": user_email,
            "metadata": metadata or {},
        }
        await db.audit_log.insert_one(doc)
    except Exception as e:
        logger.warning("[audit] insert failed for event=%s: %s", event, type(e).__name__)


# --- Resend transactional email --------------------------------------------
# Used for admin notifications (new user registered, etc.). Sync SDK; wrapped
# in asyncio.to_thread so it doesn't block the FastAPI event loop. All callers
# are best-effort — if the SDK is missing or the API key is unset we silently
# skip rather than failing the user's request.
_RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
_RESEND_SENDER = os.environ.get("RESEND_SENDER_EMAIL", "onboarding@resend.dev").strip()
_RESEND_ADMIN_RECIPIENT = os.environ.get("RESEND_ADMIN_RECIPIENT", "").strip()
if _resend is not None and _RESEND_API_KEY:
    _resend.api_key = _RESEND_API_KEY


def _send_email_sync(to: str, subject: str, html: str) -> Optional[str]:
    if not _resend or not _RESEND_API_KEY:
        return None
    try:
        result = _resend.Emails.send({
            "from": _RESEND_SENDER,
            "to": [to],
            "subject": subject,
            "html": html,
        })
        return result.get("id") if isinstance(result, dict) else None
    except Exception as e:
        logger.warning("[resend] send failed to=%s: %s", to, type(e).__name__)
        return None


async def _notify_admin_new_user(user_email: str, user_name: str, ip: str) -> None:
    """Fire-and-forget admin alert when someone signs up. Skipped entirely
    when RESEND_API_KEY / RESEND_ADMIN_RECIPIENT are not configured."""
    if not _resend or not _RESEND_API_KEY or not _RESEND_ADMIN_RECIPIENT:
        return
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    safe_name = (user_name or "").strip()[:120].replace("<", "&lt;").replace(">", "&gt;")
    safe_email = user_email.strip()[:200].replace("<", "&lt;").replace(">", "&gt;")
    safe_ip = (ip or "unknown")[:64]
    html = f"""
    <table style="font-family: -apple-system, system-ui, sans-serif; max-width: 480px; margin: 0; padding: 24px; background: #08120F; color: #E8E3D9; border-radius: 12px;">
      <tr><td style="font-size: 11px; letter-spacing: 2px; color: #72C2AC; text-transform: uppercase;">Solarisound · new sign-up</td></tr>
      <tr><td style="padding-top: 12px; font-size: 22px; font-weight: 500; color: #E8E3D9;">{safe_name or safe_email}</td></tr>
      <tr><td style="padding-top: 8px; font-size: 13px; color: #8A9A92;">{safe_email}</td></tr>
      <tr><td style="padding-top: 16px; font-family: ui-monospace, monospace; font-size: 11px; color: #5A6B65;">
        IP {safe_ip}<br/>
        {when}
      </td></tr>
      <tr><td style="padding-top: 20px; font-size: 11px; color: #5A6B65;">
        — From your Healing Frequencies admin alerts
      </td></tr>
    </table>
    """
    try:
        await asyncio.to_thread(
            _send_email_sync,
            _RESEND_ADMIN_RECIPIENT,
            f"New sign-up: {safe_email}",
            html,
        )
    except Exception as e:
        logger.warning("[resend] admin notify failed: %s", type(e).__name__)


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
    # SECURITY: reduced from 7d → 1d. `iat` is checked against the user's
    # `tokens_valid_after` watermark on every request so logout / password
    # change can invalidate every outstanding token across all devices.
    # iat carries microsecond precision (float) to avoid same-second races
    # between rapid login → logout → re-login sequences.
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id, "email": email, "type": "access",
        "iat": now.timestamp(),
        "exp": now + timedelta(days=1),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def set_auth_cookie(response: Response, token: str):
    response.set_cookie(
        key="access_token", value=token, httponly=True,
        secure=True, samesite="none", max_age=24 * 3600, path="/",
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
        # SECURITY: enforce server-side revocation watermark. Tokens issued
        # before `tokens_valid_after` are rejected — set on logout, password
        # change, or admin "force-signout".
        tva = user.get("tokens_valid_after")
        if tva:
            try:
                tva_ts = datetime.fromisoformat(tva).timestamp()
                # Strict less-than: tokens issued AT OR AFTER the watermark
                # are valid; tokens issued strictly before are revoked.
                if float(payload.get("iat", 0)) < tva_ts:
                    raise HTTPException(status_code=401, detail="Session revoked")
            except HTTPException:
                raise
            except Exception:
                pass  # malformed watermark — fail open (still bounded by exp)
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
    password: str = Field(min_length=8, max_length=128)
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
async def register(body: RegisterIn, request: Request, response: Response):
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
    # Audit + counter for the admin security tile.
    _bump_metric("registrations")
    await _audit(
        "user.registered", request,
        user_id=user["id"], user_email=email,
        metadata={"name": user["name"]},
    )
    # Fire-and-forget admin email alert (Resend). Wrapped in create_task so a
    # slow/failed email never blocks the user's registration response.
    asyncio.create_task(
        _notify_admin_new_user(email, user["name"], _client_ip(request))
    )
    token = create_access_token(user["id"], email)
    set_auth_cookie(response, token)
    return {"id": user["id"], "email": email, "name": user["name"], "token": token}


@api.post("/auth/login")
async def login(body: LoginIn, request: Request, response: Response):
    # SECURITY: brute-force throttle — 8 login attempts per IP per 5 minutes
    # (refill 1 token every ~37s). Tight enough to slow credential stuffing
    # without locking out real users who fat-finger their password.
    ip = _client_ip(request)
    _rate_limit_or_429(f"login:{ip}", capacity=8, refill_per_sec=1 / 37, label="login attempt")
    email = body.email.lower()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(body.password, user["password_hash"]):
        _bump_metric("failed_logins")
        await _audit(
            "auth.login_failed", request,
            user_email=email,
            metadata={"reason": "bad_credentials"},
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")
    _bump_metric("successful_logins")
    await _audit(
        "auth.login_succeeded", request,
        user_id=user["id"], user_email=email,
    )
    token = create_access_token(user["id"], email)
    set_auth_cookie(response, token)
    return {"id": user["id"], "email": email, "name": user.get("name", ""), "token": token}


@api.post("/auth/logout")
async def logout(response: Response, request: Request):
    # SECURITY: clear the cookie AND bump the per-user revocation watermark
    # so any token (cookie OR bearer in localStorage) is rejected on next use.
    # Best-effort — if the user isn't authenticated we still clear the cookie.
    try:
        u = await get_current_user(request)
        await db.users.update_one(
            {"id": u["id"]},
            {"$set": {"tokens_valid_after": datetime.now(timezone.utc).isoformat()}},
        )
        _bump_metric("session_revocations")
        await _audit(
            "session.revoked", request,
            user_id=u["id"], user_email=u.get("email"),
            metadata={"trigger": "logout"},
        )
    except Exception:
        pass
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
    new_password: str = Field(min_length=8, max_length=128)


class PrefsIn(BaseModel):
    """User-saved 'last used config' on the dashboard. All fields optional so
    the frontend can do partial updates as values change."""
    frequency: Optional[float] = Field(None, ge=0.1, le=20000)
    duration_minutes: Optional[int] = Field(None, ge=1, le=180)
    waveform: Optional[str] = Field(None, pattern=r"^(sine|triangle|square|sawtooth)$")
    binaural: Optional[float] = Field(None, ge=0, le=40)
    isochronic: Optional[float] = Field(None, ge=0, le=40)
    golden_stack: Optional[bool] = None
    breathwork: Optional[bool] = None
    ambient: Optional[Dict[str, float]] = None
    tone_volume: Optional[float] = Field(None, ge=0, le=1)
    visual_mode: Optional[str] = Field(None, pattern=r"^(rings|chladni|ripples)$")
    sleep_duration_min: Optional[int] = Field(None, ge=30, le=480)


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
async def change_password(body: PasswordChangeIn, request: Request, response: Response, user: dict = Depends(get_current_user)):
    full = await db.users.find_one({"id": user["id"]})
    if not verify_password(body.current_password, full["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    # SECURITY: bump revocation watermark so every existing token (other
    # devices, leaked copies) is invalidated. Issue a fresh token for the
    # caller so they don't get kicked out of their own session.
    now_iso = datetime.now(timezone.utc).isoformat()
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"password_hash": hash_password(body.new_password),
                  "tokens_valid_after": now_iso}},
    )
    _bump_metric("session_revocations")
    await _audit(
        "auth.password_changed", request,
        user_id=user["id"], user_email=user.get("email"),
    )
    fresh = create_access_token(user["id"], user["email"])
    set_auth_cookie(response, fresh)
    return {"ok": True, "token": fresh}


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
        for k in ("golden_stack", "breathwork", "binaural", "isochronic", "ambient", "visual_mode", "sleep_duration_min"):
            payload.pop(k, None)
        if not payload:
            return {"ok": True}
    # Merge with existing prefs (nested ambient dict gets replaced wholesale if sent).
    update_doc = {f"prefs.{k}": v for k, v in payload.items()}
    update_doc["prefs.updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.users.update_one({"id": user["id"]}, {"$set": update_doc})
    return {"ok": True}


# --- AI Frequency Recommendation (Pro) ---------------------------------------
EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")

AI_RECO_SYSTEM = """You are a sound-healing curator for the "Healing Frequencies" app.
The user describes how they feel or what they want to achieve. You respond with ONE
personalized audio prescription as a JSON object — no prose, no markdown, just JSON.

Hard requirements for the JSON object:
  frequency: number in Hz, between 1 and 1200. Prefer culturally-significant healing
             frequencies when they match the intent (Solfeggio: 174, 285, 396, 417,
             432, 528, 639, 741, 852, 963; Brainwaves: Delta 2, Theta 6, Schumann 7.83,
             Alpha 10, Gamma 40; Specials: 111, 222, 369, 444, 1111). Otherwise pick
             any value 1-1200 that fits the intent.
  name: short evocative title, 3-6 words, e.g. "Quiet Mind · Slow Tide"
  description: ONE sentence, max 22 words, explaining why this prescription suits
               the user's intent. No clinical claims.
  waveform: one of "sine" | "triangle" | "square" | "sawtooth" — sine for calm,
            triangle for warmth, square only for sharp focus, sawtooth rarely.
  binaural: integer Hz offset 0..40 — 0 for pure tone; use brainwave targets
            (Delta 1-4, Theta 4-8, Alpha 8-13, Beta 13-30, Gamma 30-40) when
            entrainment fits the intent. Set to 0 if isochronic > 0.
  isochronic: integer Hz pulse rate 0..40 — 0 for off; use brainwave targets
              when sharp on/off pulsing fits (focus, alertness). Mutually
              exclusive with binaural — set one OR the other, not both.
  golden_stack: boolean — true for transcendent / heart-opening intents.
  ambient: object whose keys are a subset of ["rain","ocean","forest","wind",
           "crickets","bowls","brown","white"] with values 0..1. Use 0-3 layers,
           mixed gently. Empty {} is fine for pure-tone work.
  duration_min: integer 5..60, the recommended session length in minutes.

Return ONLY the JSON object. No code fences, no prose."""


class AIRecommendIn(BaseModel):
    intent: str = Field(min_length=2, max_length=500)
    mood: Optional[str] = Field(default=None, max_length=80)
    goal: Optional[str] = Field(default=None, max_length=80)
    duration_min: Optional[int] = Field(default=None, ge=5, le=60)


def _extract_json(text: str) -> dict:
    """LLMs sometimes wrap JSON in code fences or add prose. Find the first
    {...} block via brace-matching and json.loads it."""
    if not text:
        raise ValueError("empty LLM response")
    # Strip code fences first
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    # Try direct parse
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    # Brace-match scan
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("no JSON object found in LLM response")
    depth = 0
    for i in range(start, len(cleaned)):
        c = cleaned[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start:i + 1])
    raise ValueError("unterminated JSON in LLM response")


ALLOWED_AMBIENT = {"rain", "ocean", "forest", "wind", "crickets", "bowls", "brown", "white"}
ALLOWED_WAVEFORMS = {"sine", "triangle", "square", "sawtooth"}


def _validate_reco(raw: dict) -> dict:
    """Coerce / clamp every field so a hallucinated value can't break the player."""
    freq = float(raw.get("frequency", 432))
    freq = max(1.0, min(1200.0, freq))
    name = str(raw.get("name", "AI Prescription"))[:80]
    desc = str(raw.get("description", ""))[:240]
    waveform = str(raw.get("waveform", "sine")).lower()
    if waveform not in ALLOWED_WAVEFORMS:
        waveform = "sine"
    binaural = int(max(0, min(40, raw.get("binaural", 0) or 0)))
    isochronic = int(max(0, min(40, raw.get("isochronic", 0) or 0)))
    # Mutual exclusivity (system prompt says so, but enforce server-side too).
    if isochronic > 0 and binaural > 0:
        binaural = 0
    golden = bool(raw.get("golden_stack", False))
    duration = int(max(5, min(60, raw.get("duration_min", 15) or 15)))
    ambient_raw = raw.get("ambient") or {}
    ambient = {}
    if isinstance(ambient_raw, dict):
        for k, v in ambient_raw.items():
            if k in ALLOWED_AMBIENT:
                try:
                    fv = float(v)
                    if fv > 0:
                        ambient[k] = max(0.0, min(1.0, fv))
                except Exception:
                    continue
        # Cap to 3 simultaneous layers (keeps mix airy)
        if len(ambient) > 3:
            ambient = dict(sorted(ambient.items(), key=lambda kv: -kv[1])[:3])
    return {
        "frequency": freq,
        "name": name,
        "description": desc,
        "waveform": waveform,
        "binaural": binaural,
        "isochronic": isochronic,
        "golden_stack": golden,
        "ambient": ambient,
        "duration_min": duration,
    }


@api.post("/me/ai-recommend")
async def ai_recommend(body: AIRecommendIn, user: dict = Depends(get_current_user)):
    """Generate a personalized frequency prescription via Claude Sonnet 4.5.
    Pro-only. Returns a strict-shape JSON the frontend can apply directly to
    the audio engine.
    """
    full = await db.users.find_one({"id": user["id"]})
    if not _is_pro(full):
        raise HTTPException(status_code=403, detail="AI prescriptions are a Pro feature")
    if not EMERGENT_LLM_KEY:
        raise HTTPException(status_code=503, detail="LLM key not configured")
    # SECURITY: rate-limit per user. Burst of 6 with refill 1/20s = ~3/min sustained.
    # Stops a compromised account from burning the EMERGENT_LLM_KEY budget.
    _rate_limit_or_429(f"ai:{user['id']}", capacity=6, refill_per_sec=1 / 20, label="AI request")

    # Build the user message
    parts = [f"Intent: {body.intent.strip()}"]
    if body.mood:
        parts.append(f"Current mood: {body.mood.strip()}")
    if body.goal:
        parts.append(f"Goal: {body.goal.strip()}")
    if body.duration_min:
        parts.append(f"Preferred duration: {body.duration_min} minutes")
    user_text = "\n".join(parts)

    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"ai-reco-{user['id']}-{uuid.uuid4().hex[:8]}",
        system_message=AI_RECO_SYSTEM,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")

    try:
        # Stream-collect into a single string (the playbook recommends streaming;
        # for one-shot JSON we still consume the stream then parse).
        collected = []
        async for ev in chat.stream_message(UserMessage(text=user_text)):
            cls = ev.__class__.__name__
            if cls == "TextDelta":
                collected.append(getattr(ev, "content", "") or "")
            elif cls == "StreamDone":
                break
        text = "".join(collected).strip()
        if not text:
            # Some library versions return a single concatenated event; try
            # the explicit non-streaming path as a fallback.
            try:
                text = await chat.send_message(UserMessage(text=user_text))  # type: ignore
                if hasattr(text, "content"):
                    text = text.content
                text = str(text or "").strip()
            except Exception:
                pass
        if not text:
            raise HTTPException(status_code=502, detail="AI returned an empty response")
        raw = _extract_json(text)
        reco = _validate_reco(raw)
        return reco
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("AI recommend failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"AI recommendation failed: {exc}")


# --- Conversational AI Agent (check-in companion) ---------------------------
AGENT_SYSTEM = """You are the Healing Frequencies companion — a warm, brief
sound-curation guide. The user just logged in. Greet them by name when given,
ask how they feel, and recommend sounds. Use plain, supportive language.

NEVER make medical or therapeutic claims. NEVER diagnose. Speak as a thoughtful
friend who knows the catalog. Keep messages short (1–3 sentences max).

Your reply MUST be valid JSON only — no prose outside the JSON, no code fences:

{
  "message": "string (your reply, 1-3 sentences)",
  "suggestions": [ /* 0..4 items the user can tap. Empty array if mid-conversation
                    and you just want to ask a follow-up. */
    {
      "kind": "preset",       // tone preset
      "label": "528 Hz · Heart Coherence",
      "frequency": 528,        // required for preset (1-1200)
      "waveform": "sine"       // optional, defaults to sine
    },
    {
      "kind": "soundscape",   // single ambient layer
      "label": "Slow rain",
      "soundscape": "rain",    // one of: rain, ocean, forest, wind, crickets, bowls, brown, white
      "volume": 0.55           // 0..1
    },
    {
      "kind": "sleep",        // Sleep Mode (Pro)
      "label": "Sleep Mode · 1h",
      "duration_min": 60       // one of: 30, 60, 120, 240, 480
    },
    {
      "kind": "ai_prescription", // launches the full AI Prescription with this intent
      "label": "Custom prescription for slowing down",
      "intent": "anxious, need to slow my nervous system down"
    },
    {
      "kind": "haptic_combo",   // one-tap card that bundles a haptic pattern
                                 // with an optional carrier sound + duration.
                                 // Pair with sleep/anxiety/focus prompts when
                                 // the user might benefit from FEELING the pacing.
      "label": "Heartbeat haptic + 396 Hz · 30min",
      "pattern": "heartbeat",    // one of: auto, heartbeat, breath478, frequency
      "frequency": 396,          // optional Hz carrier (1-1200)
      "soundscape": "rain",      // optional ambient layer (see soundscape kind above)
      "duration_min": 30         // optional session length: 5, 10, 15, 20, 30, 45, 60, 90
    }
  ]
}

Rules:
- 2-4 suggestions on the FIRST recommendation. 0 suggestions if asking a follow-up.
- If the user declines or asks for different options, offer a NEW set.
- If user says "no", "not those", "something else": ask once if they'd like another set, then provide it.
- Match or gently shift their state — calm for anxious, focus for restless, warmth for low-energy.
- Sleep Mode is Pro — only include it if the user explicitly mentions sleep/night/rest.
- AI Prescription is Pro — include it when their need is complex/specific.
- Haptic combos pair well with sleep ("can't sleep" → heartbeat + 396 Hz or breath478 + 432 Hz),
  anxiety ("racing thoughts" → breath478 + 528 Hz), and focus ("scattered" → frequency at 10 Hz alpha).
  Use sparingly — at most ONE haptic_combo per suggestion set, and never the only option.
- Solfeggio frequencies: 174 (grounding), 285 (regen), 396 (release fear), 417 (change), 432 (calm), 528 (heart), 639 (connection), 741 (clarity), 852 (intuition), 963 (unity).
- Brainwaves: 2 (Delta/sleep), 6 (Theta/meditation), 7.83 (Schumann), 10 (Alpha/relaxed), 40 (Gamma/focus).
"""


class AgentChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=600)
    history: Optional[list] = Field(default=None)  # [{role: 'user'|'assistant', text: str}]
    session_id: Optional[str] = Field(default=None, max_length=80)


class AgentCheckinIn(BaseModel):
    """One persisted (mood → chosen suggestion) pair. Logged when the user taps
    a suggestion in the AI Companion sheet. Used to enrich the LLM prompt on
    subsequent visits ("Last week you said you were anxious and 396 Hz helped —
    want to start there again?"). All fields are user-supplied / agent-supplied;
    we never persist anything that wasn't already in the user's chat window."""
    message: str = Field(min_length=1, max_length=600)
    suggestion: dict
    session_id: Optional[str] = Field(default=None, max_length=80)


def _summarise_suggestion(s: dict) -> str:
    """Compact one-line summary of a suggestion for embedding in the LLM
    prompt. Keeps the prior-insights block short and token-friendly."""
    kind = str(s.get("kind") or "")
    label = str(s.get("label") or "")[:60]
    if kind == "preset":
        hz = s.get("frequency")
        return f"preset {hz} Hz ({label})" if hz else f"preset ({label})"
    if kind == "soundscape":
        return f"soundscape {s.get('soundscape') or ''} ({label})".strip()
    if kind == "sleep":
        return f"sleep mode {s.get('duration_min') or '?'}min ({label})"
    if kind == "ai_prescription":
        return f"AI prescription ({label})"
    if kind == "haptic_combo":
        bits: list = []
        if s.get("pattern"):
            bits.append(str(s.get("pattern")))
        if s.get("frequency"):
            bits.append(f"{s.get('frequency')} Hz")
        if s.get("soundscape"):
            bits.append(str(s.get("soundscape")))
        if s.get("duration_min"):
            bits.append(f"{s.get('duration_min')}min")
        body = " + ".join(bits) if bits else label
        return f"haptic combo {body} ({label})" if bits else f"haptic combo ({label})"
    return label or kind


_AGENT_KINDS = {"preset", "soundscape", "sleep", "ai_prescription", "haptic_combo"}
_HAPTIC_PATTERNS = {"auto", "heartbeat", "breath478", "frequency"}
_HAPTIC_DURATIONS = (5, 10, 15, 20, 30, 45, 60, 90)


def _validate_agent_reply(raw: dict, is_pro: bool) -> dict:
    """Coerce the LLM's reply into the strict shape the frontend renders.
    Drops/filters hallucinated fields; clamps numeric values; tags Pro gating."""
    msg = str(raw.get("message") or "").strip()[:600]
    if not msg:
        msg = "I'm here. How are you feeling?"
    raw_suggestions = raw.get("suggestions") or []
    out: list = []
    if isinstance(raw_suggestions, list):
        for item in raw_suggestions[:4]:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").lower()
            if kind not in _AGENT_KINDS:
                continue
            label = str(item.get("label") or "").strip()[:80] or "Suggestion"
            entry: dict = {"kind": kind, "label": label}
            if kind == "preset":
                try:
                    freq = float(item.get("frequency") or 0)
                except Exception:
                    continue
                if not (1 <= freq <= 1200):
                    continue
                entry["frequency"] = freq
                wf = str(item.get("waveform") or "sine").lower()
                entry["waveform"] = wf if wf in ALLOWED_WAVEFORMS else "sine"
                entry["pro_only"] = False
            elif kind == "soundscape":
                sc = str(item.get("soundscape") or "").lower()
                if sc not in ALLOWED_AMBIENT:
                    continue
                vol = item.get("volume", 0.5)
                try:
                    vol = float(vol)
                except Exception:
                    vol = 0.5
                entry["soundscape"] = sc
                entry["volume"] = max(0.0, min(1.0, vol))
                entry["pro_only"] = False
            elif kind == "sleep":
                try:
                    dm = int(item.get("duration_min") or 30)
                except Exception:
                    dm = 30
                if dm not in (30, 60, 120, 240, 480):
                    dm = 30
                entry["duration_min"] = dm
                entry["pro_only"] = not is_pro
            elif kind == "ai_prescription":
                intent = str(item.get("intent") or "").strip()[:300]
                if not intent:
                    continue
                entry["intent"] = intent
                entry["pro_only"] = not is_pro
            elif kind == "haptic_combo":
                # Bundled one-tap card: vibration pattern + optional carrier
                # frequency / soundscape / session length. The combo is FREE
                # (no Pro gating) because Pulsing Haptics itself is a free
                # accessibility feature; if the LLM supplies an inner
                # frequency/soundscape they must validate as the free kinds do.
                pat = str(item.get("pattern") or "auto").lower()
                if pat not in _HAPTIC_PATTERNS:
                    pat = "auto"
                entry["pattern"] = pat
                # Optional carrier frequency.
                fhz = item.get("frequency")
                if fhz is not None:
                    try:
                        f = float(fhz)
                        if 1 <= f <= 1200:
                            entry["frequency"] = f
                    except Exception:
                        pass
                # Optional soundscape layer.
                sc = item.get("soundscape")
                if sc:
                    sc_s = str(sc).lower()
                    if sc_s in ALLOWED_AMBIENT:
                        entry["soundscape"] = sc_s
                        # Optional volume for the soundscape layer (0..1).
                        try:
                            v = float(item.get("volume", 0.5))
                            entry["volume"] = max(0.0, min(1.0, v))
                        except Exception:
                            entry["volume"] = 0.5
                # Optional session length (minutes).
                dm = item.get("duration_min")
                if dm is not None:
                    try:
                        d = int(dm)
                        if d in _HAPTIC_DURATIONS:
                            entry["duration_min"] = d
                    except Exception:
                        pass
                entry["pro_only"] = False
            out.append(entry)
    return {"message": msg, "suggestions": out}


@api.post("/me/agent/chat")
async def agent_chat(body: AgentChatIn, request: Request, user: dict = Depends(get_current_user)):
    """Conversational check-in agent. Multi-turn — pass `history` from the
    client on each call (we don't persist server-side). Returns a strict
    `{message, suggestions}` shape the frontend can render as a chat bubble
    plus a row of tappable suggestion cards.
    """
    if not EMERGENT_LLM_KEY:
        raise HTTPException(status_code=503, detail="LLM key not configured")
    # Cheaper per-user throttle than the AI Prescription endpoint — this is
    # quick conversational turns. Burst 8 with refill 1/8s = ~7/min sustained.
    _rate_limit_or_429(f"agent:{user['id']}", capacity=8, refill_per_sec=1 / 8, label="chat message")
    full = await db.users.find_one({"id": user["id"]})
    is_pro = _is_pro(full)
    # Build a single concatenated prompt: prior history + the new message.
    # This stays inside one stream_message call (no chat memory needed server-side).
    parts: list = []
    name = (full.get("name") or "").strip()
    if name:
        parts.append(f"USER_NAME: {name}")
    parts.append(f"USER_IS_PRO: {bool(is_pro)}")

    # Prior insights — last 3 successful (mood → suggestion) check-ins from
    # MongoDB. Lets the LLM acknowledge what worked before. Cheap query;
    # capped at 3 rows + 60-char labels to keep the prompt token-budget small.
    try:
        prior_cursor = db.agent_checkins.find(
            {"user_id": user["id"]}
        ).sort("created_at", -1).limit(3)
        prior_rows = await prior_cursor.to_list(length=3)
        if prior_rows:
            parts.append("PRIOR_INSIGHTS (most recent first — earlier moments where the user picked a suggestion):")
            for row in prior_rows:
                mood = str(row.get("message") or "").strip()[:120]
                picked = _summarise_suggestion(row.get("suggestion") or {})
                if mood and picked:
                    parts.append(f"- felt \"{mood}\" → chose {picked}")
            parts.append(
                "If their current state echoes one of these prior moments, you MAY gently reference it "
                "(e.g. \"Last time you mentioned X, the 396 Hz preset seemed to help — want to start there?\"). "
                "Do NOT force a callback if it doesn't fit."
            )
    except Exception as exc:  # noqa: BLE001 — defensive: never let history lookup break chat
        logger.warning("[agent_chat] prior_insights lookup failed: %s", exc)

    history = body.history or []
    for turn in history[-10:]:  # cap context window
        if not isinstance(turn, dict):
            continue
        role = "USER" if turn.get("role") == "user" else "AGENT"
        text = str(turn.get("text") or "").strip()[:600]
        if text:
            parts.append(f"{role}: {text}")
    parts.append(f"USER: {body.message.strip()}")
    parts.append("Reply now with the JSON object only.")
    user_text = "\n".join(parts)

    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=body.session_id or f"agent-{user['id']}-{uuid.uuid4().hex[:6]}",
        system_message=AGENT_SYSTEM,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")

    try:
        collected: list = []
        async for ev in chat.stream_message(UserMessage(text=user_text)):
            cls = ev.__class__.__name__
            if cls == "TextDelta":
                collected.append(getattr(ev, "content", "") or "")
            elif cls == "StreamDone":
                break
        text = "".join(collected).strip()
        if not text:
            raise HTTPException(status_code=502, detail="AI returned empty response")
        raw = _extract_json(text)
        reply = _validate_agent_reply(raw, is_pro)
        return reply
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("agent_chat failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Agent chat failed: {exc}")


@api.post("/me/agent/checkin")
async def agent_checkin(body: AgentCheckinIn, user: dict = Depends(get_current_user)):
    """Persist a (mood → chosen suggestion) pair from the AI Companion sheet.
    Called by the frontend when the user actually taps a suggestion, so we
    only log moments the user committed to (not idle browsing). Read back
    on the next visit by /me/agent/chat as PRIOR_INSIGHTS.
    """
    # Light validation — kind must be one we recognise; everything else is
    # bounded-length strings/floats we re-serialize verbatim.
    sug = body.suggestion or {}
    kind = str(sug.get("kind") or "").lower()
    if kind not in _AGENT_KINDS:
        raise HTTPException(status_code=400, detail="Unknown suggestion kind")
    # Re-shape into a stable subset (mirrors _validate_agent_reply output).
    record_sug: dict = {"kind": kind, "label": str(sug.get("label") or "")[:80]}
    if kind == "preset":
        try:
            hz = float(sug.get("frequency"))
            record_sug["frequency"] = max(1.0, min(20000.0, hz))
        except Exception:
            pass
        record_sug["waveform"] = str(sug.get("waveform") or "sine")[:12]
    elif kind == "soundscape":
        record_sug["soundscape"] = str(sug.get("soundscape") or "")[:16]
        try:
            record_sug["volume"] = max(0.0, min(1.0, float(sug.get("volume") or 0.5)))
        except Exception:
            record_sug["volume"] = 0.5
    elif kind == "sleep":
        try:
            dm = int(sug.get("duration_min") or 30)
        except Exception:
            dm = 30
        record_sug["duration_min"] = dm if dm in (30, 60, 120, 240, 480) else 30
    elif kind == "ai_prescription":
        record_sug["intent"] = str(sug.get("intent") or "")[:300]
    elif kind == "haptic_combo":
        pat = str(sug.get("pattern") or "auto").lower()
        record_sug["pattern"] = pat if pat in _HAPTIC_PATTERNS else "auto"
        fhz = sug.get("frequency")
        if fhz is not None:
            try:
                f = float(fhz)
                if 1 <= f <= 1200:
                    record_sug["frequency"] = f
            except Exception:
                pass
        sc = sug.get("soundscape")
        if sc:
            sc_s = str(sc).lower()
            if sc_s in ALLOWED_AMBIENT:
                record_sug["soundscape"] = sc_s
                try:
                    v = float(sug.get("volume", 0.5))
                    record_sug["volume"] = max(0.0, min(1.0, v))
                except Exception:
                    record_sug["volume"] = 0.5
        dm = sug.get("duration_min")
        if dm is not None:
            try:
                d = int(dm)
                if d in _HAPTIC_DURATIONS:
                    record_sug["duration_min"] = d
            except Exception:
                pass

    doc = {
        "id": uuid.uuid4().hex,
        "user_id": user["id"],
        "message": body.message.strip()[:600],
        "suggestion": record_sug,
        "session_id": body.session_id or None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.agent_checkins.insert_one(doc)
    # Keep the per-user history bounded — only the most recent 50 rows are
    # ever read. Trim older rows so the collection doesn't grow unbounded.
    try:
        cursor = db.agent_checkins.find(
            {"user_id": user["id"]}, {"id": 1, "created_at": 1}
        ).sort("created_at", -1).skip(50)
        stale = [r["id"] async for r in cursor]
        if stale:
            await db.agent_checkins.delete_many({"id": {"$in": stale}})
    except Exception as exc:  # noqa: BLE001 — defensive housekeeping; never fail the request
        logger.warning("[agent_checkin] history trim failed: %s", exc)
    return {"ok": True, "id": doc["id"]}


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
    is unchanged. Cloudflare-friendly: every Stripe call is bounded by
    STRIPE_CALL_TIMEOUT (25s) and the ENTIRE endpoint body is wrapped in an
    outer try/except so even unexpected errors (mongo drops, pydantic edge
    cases, threading issues) return a clean JSON 502 instead of an empty/half-
    open socket that Cloudflare would render as a 520 page.
    """
    rid = uuid.uuid4().hex[:8]
    try:
        return await _create_checkout_impl(body, request, user, rid)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[checkout rid=%s] unexpected error for user=%s plan=%s", rid, user.get("email"), getattr(body, "plan", "?"))
        raise HTTPException(
            status_code=502,
            detail=f"Checkout failed unexpectedly (rid={rid}): {type(e).__name__}: {str(e)[:200]}",
        )


async def _create_checkout_impl(body: CheckoutIn, request: Request, user: dict, rid: str):
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
    subscription_data = {"metadata": metadata}
    if include_trial:
        subscription_data["trial_period_days"] = trial_days

    # Unified try/except so ANY Stripe failure (Customer.create, Session.create,
    # network/timeout, bad key, restricted account) produces the same 502
    # envelope with a friendly message. Without this, AuthenticationError raised
    # from Customer.create bubbles up as a raw 500 — see iter-23 test_checkout_bad_stripe_key_returns_502.
    try:
        customer_id = await _get_or_create_stripe_customer(user)

        logger.info(
            "[checkout] user=%s plan=%s method=%s api_base=%s trial=%s customer=%s",
            user.get("id"), body.plan, body.payment_method_preference,
            _stripe.api_base, include_trial, customer_id,
        )

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
        logger.exception("[checkout] Stripe call failed for user=%s plan=%s", user.get("email"), body.plan)
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
    # Audit: "user initiated upgrade" — feeds the Sound Lineage timeline as a
    # checkout intent (vs. billing.fulfilled which is the conversion point).
    await _audit(
        "billing.checkout_started", request,
        user_id=user["id"], user_email=user.get("email"),
        metadata={
            "session_id": session.id,
            "plan": body.plan,
            "amount": amount,
            "currency": currency,
            "includes_trial": include_trial,
            "payment_method_preference": body.payment_method_preference,
        },
    )
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
    # Audit: canonical "user just became Pro / activated trial" event for the
    # Sound Lineage timeline. Skipped if request context is unavailable (this
    # is also called from the webhook handler — no Request object there).
    user_doc = await db.users.find_one({"id": user_id}, {"_id": 0, "email": 1})
    await _audit(
        "billing.fulfilled", None,
        user_id=user_id,
        user_email=user_doc.get("email") if user_doc else None,
        metadata={
            "session_id": tx["session_id"],
            "plan": tx.get("plan"),
            "mode": tx.get("mode") or ("subscription" if is_sub else "payment"),
            "amount": tx.get("amount"),
            "currency": tx.get("currency"),
        },
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


@api.get("/health/stripe")
async def health_stripe(user: dict = Depends(get_current_user)):
    """Diagnostic endpoint — checks (a) STRIPE_API_KEY is set, (b) we can reach
    the configured Stripe api_base with a minimal account-balance read, all
    inside the 25s timeout budget. ADMIN-ONLY: previously this endpoint was
    public and leaked the Stripe key prefix; now it requires an admin session.
    """
    _require_admin(user)
    if not STRIPE_API_KEY:
        return {"ok": False, "error": "STRIPE_API_KEY not set", "stage": "config"}
    import stripe as _stripe
    _normalise_stripe_api_base()
    try:
        # Cheapest read in the Stripe API — confirms connectivity + auth.
        await _stripe_call(_stripe.Balance.retrieve)
        return {
            "ok": True,
            "api_base": _stripe.api_base,
            "timeout_seconds": STRIPE_CALL_TIMEOUT,
        }
    except HTTPException as he:
        return {
            "ok": False,
            "error": he.detail,
            "stage": "stripe_call",
            "api_base": _stripe.api_base,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
            "stage": "stripe_call",
            "api_base": _stripe.api_base,
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

    # SECURITY: webhook signature verification is MANDATORY. Without it any
    # attacker who knows a target's stripe_customer_id can forge a subscription
    # event and grant themselves Pro for free. We refuse to fulfil unsigned
    # events even in dev — set STRIPE_WEBHOOK_SECRET (whsec_…) from your Stripe
    # Dashboard → Developers → Webhooks → Signing secret.
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if not webhook_secret:
        logger.error("[webhook] STRIPE_WEBHOOK_SECRET not configured — rejecting event")
        _bump_metric("webhook_signature_rejections")
        # 400 (not 500) so Stripe retries are spaced rather than alarming PagerDuty.
        raise HTTPException(status_code=400, detail="Webhook secret not configured")
    if not sig:
        logger.warning("[webhook] missing Stripe-Signature header")
        _bump_metric("webhook_signature_rejections")
        raise HTTPException(status_code=400, detail="Missing signature")
    try:
        event = _stripe.Webhook.construct_event(body, sig, webhook_secret)
    except Exception as e:
        # Generic 400 — don't echo crypto error details, just log them.
        logger.warning("[webhook] signature verification failed: %s", type(e).__name__)
        _bump_metric("webhook_signature_rejections")
        raise HTTPException(status_code=400, detail="Invalid signature")

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
        # SECURITY: escape regex metacharacters from user input to prevent
        # ReDoS / regex injection. Admin-only but defense in depth.
        safe_q = re.escape(q.strip())
        if len(safe_q) > 100:
            safe_q = safe_q[:100]
        query = {"email": {"$regex": safe_q, "$options": "i"}}
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
async def admin_grant_pro(user_id: str, body: GrantProIn, request: Request, user: dict = Depends(get_current_user)):
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
    await _audit(
        "admin.grant_pro", request,
        user_id=user["id"], user_email=user.get("email"),
        metadata={"target_user_id": user_id, "target_email": target["email"], "days_added": int(body.days)},
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
async def admin_revoke_pro(user_id: str, request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    await db.users.update_one(
        {"id": user_id},
        {"$set": {"plan": "basic", "pro_until": None}},
    )
    await _audit(
        "admin.revoke_pro", request,
        user_id=user["id"], user_email=user.get("email"),
        metadata={"target_user_id": user_id, "target_email": target["email"]},
    )
    return {"ok": True, "user_id": user_id, "plan": "basic"}


@api.delete("/admin/users/{user_id}")
async def admin_delete_user(user_id: str, request: Request, user: dict = Depends(get_current_user)):
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
    await _audit(
        "admin.delete_user", request,
        user_id=user["id"], user_email=user.get("email"),
        metadata={"target_user_id": user_id, "target_email": target["email"]},
    )
    return {"ok": True, "user_id": user_id, "email": target["email"], "deleted": True}


# --- Admin observability ----------------------------------------------------
@api.get("/admin/security")
async def admin_security(user: dict = Depends(get_current_user)):
    """Live security counters for the admin dashboard tile.
    Each counter exposes `last_hour` / `last_24h` / `last_7d` totals plus the
    most recent audit events. Counters are in-memory (reset on restart);
    audit-log events are persisted in MongoDB.
    """
    _require_admin(user)
    metrics = {name: _metric_summary(name) for name in _METRIC_BUCKETS.keys()}
    # Most recent 12 events for context (lets the tile show a recent activity feed).
    recent_cursor = db.audit_log.find({}, {"_id": 0}).sort("ts", -1).limit(12)
    recent = await recent_cursor.to_list(12)
    # Pending registration count for the "new user notification" badge:
    # how many users registered in the last 24h that the admin hasn't acknowledged.
    one_day_ago = datetime.now(timezone.utc) - timedelta(days=1)
    new_users_24h = await db.users.count_documents({
        "created_at": {"$gte": one_day_ago.isoformat()},
    })
    return {
        "metrics": metrics,
        "recent_events": recent,
        "new_users_24h": new_users_24h,
    }


@api.get("/admin/audit-log")
async def admin_audit_log(
    event: Optional[str] = None,
    user_email: Optional[str] = None,
    limit: int = 100,
    skip: int = 0,
    user: dict = Depends(get_current_user),
):
    """Paged audit-log viewer. Filter by `event` prefix (e.g. "auth.")
    or by `user_email` exact match. Newest first."""
    _require_admin(user)
    limit = max(1, min(500, limit))
    skip = max(0, skip)
    query: dict = {}
    if event:
        # Prefix match — "auth." matches login_failed, login_succeeded, etc.
        safe = re.escape(event.strip())[:80]
        query["event"] = {"$regex": f"^{safe}"}
    if user_email:
        query["user_email"] = user_email.strip().lower()[:100]
    total = await db.audit_log.count_documents(query)
    cursor = db.audit_log.find(query, {"_id": 0}).sort("ts", -1).skip(skip).limit(limit)
    items = await cursor.to_list(limit)
    return {"total": total, "items": items, "skip": skip, "limit": limit}


# --- Sound Lineage — product growth timeline -------------------------------
def _day_key(iso: str) -> str:
    """Normalize an ISO timestamp to a `YYYY-MM-DD` UTC day-bucket key."""
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d")
    except Exception:
        return iso[:10]


def _init_lineage_buckets(start: datetime, days: int) -> Dict[str, dict]:
    """Pre-seed empty per-day buckets so the chart x-axis has no gaps."""
    return {
        (start + timedelta(days=i)).strftime("%Y-%m-%d"): {
            "date": (start + timedelta(days=i)).strftime("%Y-%m-%d"),
            "daily_active": 0,
            "signups": 0,
            "checkouts_started": 0,
            "billing_fulfilled": 0,
            "admin_grants": 0,
        }
        for i in range(days)
    }


# Maps event name → bucket counter field. Keeps the hot loop's branch logic
# table-driven (no chained elif), which both lowers cyclomatic complexity and
# makes adding new lineage events a one-line change.
_LINEAGE_COUNTERS = {
    "user.registered": "signups",
    "billing.checkout_started": "checkouts_started",
    "billing.fulfilled": "billing_fulfilled",
    "admin.grant_pro": "admin_grants",
}


def _annotate_event(row: dict) -> Optional[dict]:
    """Return a dashboard-friendly annotation dict for events worth surfacing
    on the timeline (Pro conversions + admin grants), or None for events that
    are counted but not annotated (signups, checkouts)."""
    ev = row.get("event", "")
    md = row.get("metadata") or {}
    if ev == "billing.fulfilled":
        return {
            "ts": row["ts"], "event": ev,
            "user_email": row.get("user_email"),
            "label": f"Pro: {row.get('user_email') or '—'}",
            "plan": md.get("plan"),
        }
    if ev == "admin.grant_pro":
        return {
            "ts": row["ts"], "event": ev,
            "user_email": md.get("target_email"),
            "label": f"Grant: {md.get('target_email') or '—'} (+{md.get('days_added', 0)}d)",
            "days_added": md.get("days_added"),
        }
    return None


def _accumulate_lineage(rows: list, buckets: dict) -> tuple:
    """Single pass over audit rows: bump bucket counters, track DAU sets, and
    collect annotations. Returns (annotations, dau_sets_by_day)."""
    seen_per_day: Dict[str, set] = {d: set() for d in buckets.keys()}
    annotations: list = []
    for r in rows:
        d = _day_key(r["ts"])
        if d not in buckets:
            continue
        uid = r.get("user_id")
        if uid:
            seen_per_day[d].add(uid)
        counter = _LINEAGE_COUNTERS.get(r.get("event", ""))
        if counter:
            buckets[d][counter] += 1
        ann = _annotate_event(r)
        if ann is not None:
            annotations.append(ann)
    return annotations, seen_per_day


def _lineage_totals(buckets: dict) -> dict:
    return {
        "signups": sum(b["signups"] for b in buckets.values()),
        "checkouts_started": sum(b["checkouts_started"] for b in buckets.values()),
        "billing_fulfilled": sum(b["billing_fulfilled"] for b in buckets.values()),
        "admin_grants": sum(b["admin_grants"] for b in buckets.values()),
        "peak_dau": max((b["daily_active"] for b in buckets.values()), default=0),
    }


@api.get("/admin/sound-lineage")
async def admin_sound_lineage(
    days: int = 30,
    user: dict = Depends(get_current_user),
):
    """Sound Lineage timeline data for the admin dashboard chart.

    Returns per-day buckets (DAU + signups + checkouts + Pro conversions +
    admin grants) plus the most recent 50 annotated events and window totals.
    Heavy lifting is delegated to small focused helpers (`_init_lineage_buckets`,
    `_accumulate_lineage`, `_annotate_event`, `_lineage_totals`) so this
    endpoint stays a thin orchestrator.
    """
    _require_admin(user)
    days = max(7, min(365, days))
    now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = now - timedelta(days=days - 1)
    rows_cursor = db.audit_log.find(
        {"ts": {"$gte": start.isoformat()}},
        {"_id": 0, "ts": 1, "event": 1, "user_id": 1, "user_email": 1, "metadata": 1},
    ).sort("ts", 1)
    rows = await rows_cursor.to_list(20000)

    buckets = _init_lineage_buckets(start, days)
    annotations, seen_per_day = _accumulate_lineage(rows, buckets)
    for d, s in seen_per_day.items():
        buckets[d]["daily_active"] = len(s)

    return {
        "window_days": days,
        "start": start.strftime("%Y-%m-%d"),
        "end": now.strftime("%Y-%m-%d"),
        "series": list(buckets.values()),
        "annotations": sorted(annotations, key=lambda a: a["ts"], reverse=True)[:50],
        "totals": _lineage_totals(buckets),
    }


# --- App setup ----------------------------------------------------------------
@api.get("/")
async def root():
    return {"message": "Healing Frequencies API"}


app.include_router(api)

# SECURITY: never combine wildcard origin with credentials. CORS_ORIGINS MUST
# be an explicit comma-separated allowlist of full origins (scheme + host +
# optional port). A misconfigured wildcard would let any website read a
# logged-in user's cookies via the browser.
_raw_origins = os.environ.get("CORS_ORIGINS", "").strip()
origins = [o.strip() for o in _raw_origins.split(",") if o.strip() and o.strip() != "*"]
if not origins:
    # Fail-soft default: use a SAFE allowlist (the production domain + the
    # preview host) rather than crashing the server. Logged loudly so the
    # operator knows to set CORS_ORIGINS explicitly. We do NOT fall back to
    # wildcard '*' here because that defeats the audit fix.
    origins = [
        "https://solarisound.com",
        "https://www.solarisound.com",
        "http://localhost:3000",
    ]
    logging.getLogger(__name__).warning(
        "CORS_ORIGINS not set — falling back to safe default allowlist: %s",
        origins,
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    max_age=600,
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
    # Audit log: paged filter index + TTL so the collection self-prunes after
    # 180 days (the timestamp field is stored as ISO string; the TTL index
    # works against the parallel `ts_at` Date field — added below per-insert).
    await db.audit_log.create_index([("ts", -1)])
    await db.audit_log.create_index([("event", 1), ("ts", -1)])
    # Seed plan_config if missing
    existing_cfg = await db.plan_config.find_one({"_id": "current"})
    if not existing_cfg:
        await db.plan_config.insert_one({"_id": "current", **DEFAULT_PLAN_CONFIG})
    # seed admin — strong password required; never auto-reset an existing
    # admin's password unless ADMIN_BOOTSTRAP_RESET="true" is explicitly set.
    # This closes the "self-healing default password" footgun where rotating
    # the admin password via the API would be silently undone on next restart.
    admin_email = os.environ.get("ADMIN_EMAIL", "").lower().strip()
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    if not admin_email or not admin_password:
        logger.warning("[seed] ADMIN_EMAIL / ADMIN_PASSWORD not set — skipping admin seed")
        return
    if len(admin_password) < 12:
        logger.error("[seed] ADMIN_PASSWORD too short (<12 chars) — refusing to seed admin")
        return
    bootstrap_reset = os.environ.get("ADMIN_BOOTSTRAP_RESET", "false").lower() == "true"
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
        logger.info("[seed] admin created")
    else:
        updates = {}
        if existing.get("role") != "admin":
            updates["role"] = "admin"
        # Only re-hash password on explicit opt-in. Default behaviour is to
        # leave whatever the admin has (rotated via /api/me/password).
        if bootstrap_reset and not verify_password(admin_password, existing["password_hash"]):
            updates["password_hash"] = hash_password(admin_password)
            updates["tokens_valid_after"] = datetime.now(timezone.utc).isoformat()
        if updates:
            await db.users.update_one({"email": admin_email}, {"$set": updates})
            logger.info("[seed] admin fields updated: %s", list(updates.keys()))


@app.on_event("shutdown")
async def shutdown():
    client.close()
