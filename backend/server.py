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


# --- Kubernetes health probes -----------------------------------------------
# Emergent's deployment platform hits GET /health (root path, no /api prefix)
# from inside the pod for liveness / readiness. Without this, probes 404 and
# Kubernetes rolls the pod back. Also expose /api/health for parity so any
# external ingress-side check works too.
@app.get("/health")
@app.head("/health")
async def _health_root():
    return {"ok": True}


@api.get("/health")
async def _health_api():
    return {"ok": True}


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


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class SessionIn(BaseModel):
    name: str
    frequency: float
    waveform: str = "sine"
    binaural: float = 0
    duration_minutes: int = 10
    ambient: dict = Field(default_factory=dict)  # {rain: 0..1, ocean: 0..1, forest: 0..1}
    breathwork: bool = False
    # Optional Sound-Bath bookmark. When present, the session represents an
    # algorithmic Sound Bath preset the user wanted to revisit — the client
    # replays `sound_bath.preset_key` on load. Shape: {preset_key, label}.
    sound_bath: Optional[dict] = None


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


# --- Password reset ---------------------------------------------------------
# SECURITY: JWT-signed reset tokens (30-min TTL) whose `jti` is persisted in
# `password_reset_tokens` so we can enforce single-use. The forgot-password
# endpoint ALWAYS returns the same generic response to prevent user email
# enumeration. Rate-limited per-IP (5 req / 15 min ≈ refill 1 / 180s) and
# per-email (3 req / 15 min ≈ refill 1 / 300s) to reduce abuse.
_RESET_TOKEN_TTL_MIN = 30


def _reset_link_base(request: Request) -> str:
    """Base URL used to construct the reset link in the outgoing email.
    SECURITY: prefer the server-configured FRONTEND_URL (canonical), then
    fall back to a strict allow-list of hosts derived from CORS_ORIGINS,
    and only then to the request's Origin header. This prevents an
    attacker from tricking us into embedding a phishing domain in the
    password-reset email by spoofing the Origin header.
    """
    canonical = os.environ.get("FRONTEND_URL", "").strip().rstrip("/")
    if canonical:
        return canonical
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if not origin:
        return ""
    allow_wildcard = _raw_origins == "*" if "_raw_origins" in globals() else False
    if allow_wildcard:
        # Preview/dev mode reflects any origin — still safe here because the
        # reset URL is only ever emailed to the account owner.
        return origin
    allowed = {o.strip().rstrip("/") for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()}
    return origin if origin in allowed else ""


def _reset_email_html(reset_url: str) -> str:
    return f"""
    <table style="font-family: -apple-system, system-ui, sans-serif; max-width: 480px; margin: 0; padding: 24px; background: #08120F; color: #E8E3D9; border-radius: 12px;">
      <tr><td style="font-size: 11px; letter-spacing: 2px; color: #72C2AC; text-transform: uppercase;">Solarisound · Password reset</td></tr>
      <tr><td style="padding-top: 12px; font-size: 22px; font-weight: 500; color: #E8E3D9;">Reset your password</td></tr>
      <tr><td style="padding-top: 14px; font-size: 14px; line-height: 1.55; color: #C6CDCA;">
        We received a request to reset the password for your Solarisound account.
        Click the button below within the next 30 minutes to choose a new password.
      </td></tr>
      <tr><td style="padding-top: 24px;">
        <a href="{reset_url}" style="display: inline-block; padding: 12px 22px; border-radius: 999px; background: #5C9E8C; color: #08120F; text-decoration: none; font-weight: 500; letter-spacing: .02em;">Reset password</a>
      </td></tr>
      <tr><td style="padding-top: 20px; font-size: 12px; color: #8A9A92; word-break: break-all;">
        Or paste this link in your browser:<br/>
        <span style="color: #72C2AC;">{reset_url}</span>
      </td></tr>
      <tr><td style="padding-top: 20px; font-size: 12px; color: #5A6B65; line-height: 1.55;">
        If you didn't request this, you can safely ignore this email — your password
        won't change unless you visit the link above and choose a new one.
      </td></tr>
      <tr><td style="padding-top: 20px; font-size: 11px; color: #5A6B65;">— Solarisound</td></tr>
    </table>
    """


async def _dispatch_password_reset_email(user: dict, request: Request) -> None:
    """Create a signed reset token, persist its jti, and send the email.
    Silently swallows all errors so the caller always returns generic success."""
    try:
        jti = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        exp = now + timedelta(minutes=_RESET_TOKEN_TTL_MIN)
        payload = {
            "sub": user["id"],
            "email": user["email"],
            "type": "password_reset",
            "jti": jti,
            "iat": now.timestamp(),
            "exp": exp,
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        await db.password_reset_tokens.insert_one({
            "id": jti,
            "user_id": user["id"],
            "email": user["email"],
            "created_at": now.isoformat(),
            "expires_at": exp.isoformat(),
            "used_at": None,
            "ip": _client_ip(request),
        })
        base = _reset_link_base(request)
        reset_url = f"{base}/?reset_token={token}" if base else f"/?reset_token={token}"
        subject = "Reset your Solarisound password"
        html = _reset_email_html(reset_url)
        # Send via Resend (sync SDK wrapped for the event loop). If the SDK
        # or API key is missing, _send_email_sync no-ops — we log below.
        message_id = await asyncio.to_thread(
            _send_email_sync, user["email"], subject, html
        )
        if not message_id:
            logger.warning(
                "[auth.forgot_password] email dispatch skipped or failed for %s",
                user["email"],
            )
    except Exception as e:
        # Never propagate — the outer endpoint returns generic success either way.
        logger.exception("[auth.forgot_password] dispatch error: %s", type(e).__name__)


@api.post("/auth/forgot-password")
async def forgot_password(body: ForgotPasswordIn, request: Request):
    ip = _client_ip(request)
    email = body.email.lower().strip()
    # Per-IP + per-email throttle. Both must pass. We check the IP bucket
    # first so a single attacker can't burn through many emails from one host.
    _rate_limit_or_429(
        f"forgot:ip:{ip}", capacity=5, refill_per_sec=1 / 180,
        label="password reset request",
    )
    _rate_limit_or_429(
        f"forgot:email:{email}", capacity=3, refill_per_sec=1 / 300,
        label="password reset request",
    )
    generic = {
        "ok": True,
        "message": "If an account exists for that email, a reset link is on its way.",
    }
    user = await db.users.find_one({"email": email})
    await _audit(
        "auth.forgot_password_requested", request,
        user_id=(user or {}).get("id"),
        user_email=email,
        metadata={"account_exists": bool(user)},
    )
    if not user:
        return generic
    # Fire-and-forget dispatch so timing doesn't leak account existence.
    asyncio.create_task(_dispatch_password_reset_email(user, request))
    return generic


@api.post("/auth/reset-password")
async def reset_password(body: ResetPasswordIn, request: Request, response: Response):
    ip = _client_ip(request)
    # Throttle reset attempts per IP too — stops brute-forcing tokens.
    _rate_limit_or_429(
        f"reset:ip:{ip}", capacity=10, refill_per_sec=1 / 60,
        label="reset attempt",
    )
    try:
        payload = jwt.decode(body.token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="This reset link has expired. Please request a new one.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="This reset link is invalid.")
    if payload.get("type") != "password_reset":
        raise HTTPException(status_code=400, detail="This reset link is invalid.")
    jti = payload.get("jti")
    user_id = payload.get("sub")
    if not jti or not user_id:
        raise HTTPException(status_code=400, detail="This reset link is invalid.")
    record = await db.password_reset_tokens.find_one({"id": jti})
    if not record:
        raise HTTPException(status_code=400, detail="This reset link is invalid.")
    if record.get("used_at"):
        raise HTTPException(status_code=400, detail="This reset link has already been used.")
    if record.get("user_id") != user_id:
        raise HTTPException(status_code=400, detail="This reset link is invalid.")
    user = await db.users.find_one({"id": user_id})
    if not user:
        raise HTTPException(status_code=400, detail="This reset link is invalid.")
    # SECURITY: mark the token used atomically BEFORE mutating the password so
    # a concurrent replay is rejected. Guard on used_at being null.
    marked = await db.password_reset_tokens.update_one(
        {"id": jti, "used_at": None},
        {"$set": {"used_at": datetime.now(timezone.utc).isoformat()}},
    )
    if marked.modified_count != 1:
        raise HTTPException(status_code=400, detail="This reset link has already been used.")
    now_iso = datetime.now(timezone.utc).isoformat()
    await db.users.update_one(
        {"id": user_id},
        {"$set": {"password_hash": hash_password(body.new_password),
                  "tokens_valid_after": now_iso}},
    )
    _bump_metric("session_revocations")
    await _audit(
        "auth.password_reset_completed", request,
        user_id=user_id, user_email=user["email"],
        metadata={"jti": jti},
    )
    return {"ok": True, "message": "Your password has been reset. You can now sign in."}


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
async def create_session(body: SessionIn, request: Request, user: dict = Depends(get_current_user)):
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
    await _audit(
        "session.saved", request,
        user_id=user["id"], user_email=user.get("email"),
        metadata={"session_id": doc["id"], "name": doc.get("name")},
    )
    return doc


@api.delete("/sessions/{sid}")
async def delete_session(sid: str, request: Request, user: dict = Depends(get_current_user)):
    res = await db.sessions.delete_one({"id": sid, "user_id": user["id"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    await _audit(
        "session.deleted", request,
        user_id=user["id"], user_email=user.get("email"),
        metadata={"session_id": sid},
    )
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
    """User has Pro access if they are admin OR their entitlement window is
    still in the future. Reads the canonical `pro_until` field first, and
    falls back to the legacy `pro_expires_at` field that was written by an
    early version of `/promo/redeem` (pre-iter41). This lets any user who
    redeemed a comp promo before the schema was normalised still see their
    Pro access without needing a manual DB touch."""
    if user.get("role") == "admin":
        return True
    now = datetime.now(timezone.utc)
    for field in ("pro_until", "pro_expires_at"):
        raw = user.get(field)
        if not raw:
            continue
        try:
            if datetime.fromisoformat(raw.replace("Z", "+00:00")) > now:
                return True
        except Exception:
            continue
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
    # Optional promo code applied at checkout. If it's a discount code the
    # server creates a Stripe coupon on-the-fly and attaches it to the
    # session; if it's a referral code it's recorded on the user + logged;
    # comp codes never hit this endpoint (they short-circuit to /promo/redeem).
    promo_code: Optional[str] = Field(default=None, max_length=48)


# ------------------------------------------------------------------ #
# Promo Codes                                                        #
# ------------------------------------------------------------------ #
class PromoCreateIn(BaseModel):
    code: str = Field(..., min_length=3, max_length=48)
    type: str = Field(..., pattern=r"^(comp|discount|referral)$")
    active: bool = True
    expires_at: Optional[str] = None    # ISO datetime string
    max_uses: Optional[int] = Field(default=None, ge=1)
    # comp
    duration_days: Optional[int] = Field(default=None, ge=1, le=3650)
    # discount
    percent_off: Optional[int] = Field(default=None, ge=1, le=100)
    applies_to: Optional[str] = Field(default=None, pattern=r"^(monthly|annual|both)$")
    # referral
    rep_name: Optional[str] = Field(default=None, max_length=120)
    rep_email: Optional[str] = Field(default=None, max_length=200)


class PromoUpdateIn(BaseModel):
    active: Optional[bool] = None
    expires_at: Optional[str] = None
    max_uses: Optional[int] = Field(default=None, ge=1)


class PromoValidateIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=48)


class PromoRedeemIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=48)


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
async def update_profile(body: ProfileUpdateIn, request: Request, user: dict = Depends(get_current_user)):
    new_name = body.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"name": new_name}},
    )
    await _audit(
        "profile.updated", request,
        user_id=user["id"], user_email=user.get("email"),
        metadata={"name": new_name},
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
    # Prefer `pro_until` (canonical) but fall back to legacy `pro_expires_at`
    # so users who redeemed pre-iter41 still see the correct countdown.
    pro_until = full.get("pro_until") or full.get("pro_expires_at")
    days_left = 0
    if pro_until:
        try:
            delta = datetime.fromisoformat(pro_until.replace("Z", "+00:00")) - datetime.now(timezone.utc)
            days_left = max(0, delta.days + (1 if delta.seconds > 0 else 0))
        except Exception:
            pass
    # Admin always shows as pro (lifetime access)
    sub_status = full.get("stripe_subscription_status")
    in_trial = sub_status == "trialing"
    plan = "admin" if is_admin else (full.get("plan") or ("pro" if pro else "basic"))
    # Surface the entitlement source so the client can differentiate paid Pro
    # from Stripe trial vs promo comp. `pro_source` is set by /promo/redeem
    # (e.g. "promo:WELCOME30") and left blank for Stripe-driven Pro.
    pro_source = full.get("pro_source")
    is_promo_pro = bool(pro_source and pro_source.startswith("promo:"))
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
        "pro_source": pro_source,
        "is_promo_pro": is_promo_pro,
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


# --- Hearing profile (equalizer calibration) ---------------------------------
# Audiogram-style threshold curve captured during a 30s onboarding test. The
# frontend audio engine reads it back and inserts a chain of peaking biquad
# filters between the master gain and ctx.destination so the user's Solfeggio
# tones / binaural beats are equalised for their ears + their hardware.
#
# Storage: stored on the user document under `hearing_profile`. Single object,
# never an array; replaced wholesale on each calibration submission.
#
# Schema:
#   {
#     bands: [{freq: int, heard: bool, gain_db: float}, ...],
#     test_level_db: float,     # the test gain used during calibration
#     calibrated_at: iso8601 str,
#     skipped: bool             # true if the user chose to skip onboarding
#   }
_CAL_BANDS = (60, 125, 250, 500, 1000, 2000, 4000, 8000, 12000)
_CAL_TEST_LEVEL_DB = -30.0  # how soft each test tone played
_CAL_BOOST_DB = 6.0          # gain applied to bands the user couldn't hear
_CAL_MAX_DB = 9.0            # clamp for safety / clipping prevention


class HearingProfileIn(BaseModel):
    # bands is a list of {freq:int, heard:bool} dicts. We validate per-row in
    # the endpoint so a single malformed row doesn't 422 the whole submission.
    bands: Optional[list] = Field(default=None)
    skipped: Optional[bool] = Field(default=None)


def _compute_band_gain_db(heard: bool) -> float:
    """Map heard/unheard at the standard test level into a peaking-filter gain.
    Heard bands stay flat (0 dB) — we never dampen the frequencies the user
    can hear so playback never gets quieter than baseline. Unheard bands get
    a fixed +6 dB boost, clamped to ±9 dB. Future versions can refine with
    multiple test levels per band for a finer audiogram."""
    g = 0.0 if heard else _CAL_BOOST_DB
    return max(-_CAL_MAX_DB, min(_CAL_MAX_DB, g))


@api.get("/me/hearing-profile")
async def get_hearing_profile(user: dict = Depends(get_current_user)):
    """Return the user's calibration profile or null. The frontend uses this
    to decide whether to run the first-time calibration flow on dashboard
    mount and to apply the EQ chain to the audio engine."""
    full = await db.users.find_one({"id": user["id"]}, {"_id": 0, "hearing_profile": 1}) or {}
    return full.get("hearing_profile") or None


@api.post("/me/hearing-profile")
async def save_hearing_profile(body: HearingProfileIn, request: Request, user: dict = Depends(get_current_user)):
    """Persist the user's calibration. Two valid shapes:
      1) {bands: [...]}    — save a real calibration. We compute gain_db
                              server-side so a malicious client can't ask for
                              +60 dB boosts.
      2) {skipped: true}   — user dismissed onboarding. We store a stub so
                              the frontend doesn't re-prompt every session.
    """
    if body.skipped:
        # Minimal skip stub — no bands, just a flag + timestamp.
        stub = {
            "bands": [],
            "test_level_db": _CAL_TEST_LEVEL_DB,
            "calibrated_at": datetime.now(timezone.utc).isoformat(),
            "skipped": True,
        }
        await db.users.update_one({"id": user["id"]}, {"$set": {"hearing_profile": stub}})
        await _audit(
            "hearing.skipped", request,
            user_id=user["id"], user_email=user.get("email"),
        )
        return stub

    if not body.bands:
        raise HTTPException(status_code=400, detail="bands required")
    # Restrict to the canonical _CAL_BANDS list so the EQ chain is always the
    # same shape, regardless of what the client submitted.
    by_freq: dict = {}
    for b in body.bands:
        try:
            f = int(b.get("freq") if isinstance(b, dict) else getattr(b, "freq"))
            heard = bool(b.get("heard") if isinstance(b, dict) else getattr(b, "heard"))
            if 20 <= f <= 20000:
                by_freq[f] = heard
        except Exception:
            continue
    record_bands: list = []
    for f in _CAL_BANDS:
        heard = bool(by_freq.get(f, True))  # default to "heard" if missing
        record_bands.append({"freq": f, "heard": heard, "gain_db": _compute_band_gain_db(heard)})
    profile = {
        "bands": record_bands,
        "test_level_db": _CAL_TEST_LEVEL_DB,
        "calibrated_at": datetime.now(timezone.utc).isoformat(),
        "skipped": False,
    }
    await db.users.update_one({"id": user["id"]}, {"$set": {"hearing_profile": profile}})
    boosted = [b["freq"] for b in record_bands if b["gain_db"] > 0]
    await _audit(
        "hearing.calibrated", request,
        user_id=user["id"], user_email=user.get("email"),
        metadata={"boosted_bands": boosted, "band_count": len(record_bands)},
    )
    return profile


@api.delete("/me/hearing-profile")
async def delete_hearing_profile(request: Request, user: dict = Depends(get_current_user)):
    """Reset the user's calibration. Used by the 'Recalibrate' button in
    Account → Hearing profile."""
    await db.users.update_one({"id": user["id"]}, {"$unset": {"hearing_profile": ""}})
    await _audit(
        "hearing.reset", request,
        user_id=user["id"], user_email=user.get("email"),
    )
    return {"ok": True}


# --- Harmonic Blueprint (Pro) -----------------------------------------------
# Users record or upload a short vocal sample; the browser runs an FFT locally
# and posts ONLY the derived resonance profile here — the raw audio never
# leaves the device. Payload shape is enforced loosely (bands + spectrum +
# dominant/dips) so future frontend iterations can extend it without a schema
# migration.

class HarmonicBandIn(BaseModel):
    key: str
    label: str
    lo: float
    hi: float
    db: float


class HarmonicPeakIn(BaseModel):
    hz: float
    db: float


class HarmonicProfileIn(BaseModel):
    version: int = 1
    sample_rate: float
    duration: float = Field(gt=0, le=60)
    fft_size: int = Field(gt=0, le=32768)
    spectrum: list = Field(default_factory=list, max_length=512)
    dominant: list[HarmonicPeakIn] = Field(default_factory=list, max_length=16)
    dips: list[HarmonicPeakIn] = Field(default_factory=list, max_length=16)
    bands: list[HarmonicBandIn] = Field(default_factory=list, max_length=16)
    underrepresented: list[dict] = Field(default_factory=list, max_length=16)
    generated_at: Optional[str] = None
    # Phase 2 — Eigenmode Tuning. When the client renders the Review-Findings
    # step, it POSTs the gaps the user affirmed as personally resonant back to
    # us. Free-form dicts so future finding shapes don't require a migration.
    confirmed_gaps: list[dict] = Field(default_factory=list, max_length=16)


async def _ensure_eigenmode(user_id: str) -> Optional[dict]:
    """Return the user's eigenmode profile — a legacy-aware helper. Profiles
    saved before Phase 2 have no `is_eigenmode` field; if the user has any
    profiles but none flagged eigenmode, promote the OLDEST one so their very
    first capture is preserved as their natural baseline (Phase 2 semantics)."""
    eigen = await db.resonance_profiles.find_one(
        {"user_id": user_id, "is_eigenmode": True}, {"_id": 0},
    )
    if eigen:
        return eigen
    oldest = await db.resonance_profiles.find_one(
        {"user_id": user_id}, {"_id": 0},
        sort=[("created_at", 1)],
    )
    if not oldest:
        return None
    await db.resonance_profiles.update_one(
        {"id": oldest["id"]},
        {"$set": {"is_eigenmode": True}},
    )
    oldest["is_eigenmode"] = True
    return oldest


@api.get("/harmonic-blueprint/profile")
async def get_harmonic_profile(user: dict = Depends(get_current_user)):
    """Return the user's most recent resonance profile alongside their
    eigenmode baseline (which may be the same document if they've only ever
    captured once). `{profile: null, eigenmode: null}` when they haven't
    recorded anything yet. Pro-only feature — free users get a 402 so the
    UI can route them to the paywall."""
    if not _is_pro(user):
        raise HTTPException(status_code=402, detail="Harmonic Blueprint is a Pro feature.")
    latest = await db.resonance_profiles.find_one(
        {"user_id": user["id"]},
        {"_id": 0},
        sort=[("created_at", -1)],
    )
    eigen = await _ensure_eigenmode(user["id"])
    return {"profile": latest, "eigenmode": eigen}


@api.get("/harmonic-blueprint/eigenmode")
async def get_eigenmode_profile(user: dict = Depends(get_current_user)):
    """Return just the user's eigenmode (natural baseline) profile, or null
    when they haven't captured one yet."""
    if not _is_pro(user):
        raise HTTPException(status_code=402, detail="Harmonic Blueprint is a Pro feature.")
    eigen = await _ensure_eigenmode(user["id"])
    return {"eigenmode": eigen}


@api.post("/harmonic-blueprint/profile")
async def save_harmonic_profile(
    body: HarmonicProfileIn,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Persist the derived resonance profile. If the user has no eigenmode
    baseline yet, this save becomes their eigenmode automatically (Phase 2
    semantics: the very first capture is treated as their natural harmonic
    signature). Free-tier users get 402 so the client can offer an upgrade."""
    if not _is_pro(user):
        raise HTTPException(status_code=402, detail="Harmonic Blueprint is a Pro feature.")
    ip = _client_ip(request)
    _rate_limit_or_429(
        f"harmonic:{user['id']}:{ip}", capacity=6, refill_per_sec=1 / 600,
        label="blueprint save",
    )
    existing_eigen = await _ensure_eigenmode(user["id"])
    is_first_ever = existing_eigen is None
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "created_at": now,
        "version": body.version,
        "sample_rate": body.sample_rate,
        "duration": body.duration,
        "fft_size": body.fft_size,
        "spectrum": body.spectrum,
        "dominant": [p.model_dump() for p in body.dominant],
        "dips": [p.model_dump() for p in body.dips],
        "bands": [b.model_dump() for b in body.bands],
        "underrepresented": body.underrepresented,
        "confirmed_gaps": body.confirmed_gaps,
        "generated_at": body.generated_at or now,
        "is_eigenmode": is_first_ever,
    }
    await db.resonance_profiles.insert_one({**doc})
    # Retention: keep latest 5 profiles per user, PLUS the eigenmode even if
    # older (Phase 2: the baseline must survive forever, or until the user
    # explicitly resets it).
    all_docs = await db.resonance_profiles.find(
        {"user_id": user["id"]},
        {"id": 1, "created_at": 1, "is_eigenmode": 1, "_id": 0},
        sort=[("created_at", -1)],
    ).to_list(1000)
    keep = {r["id"] for r in all_docs[:5]}
    for r in all_docs:
        if r.get("is_eigenmode"):
            keep.add(r["id"])
    if len(all_docs) > len(keep):
        await db.resonance_profiles.delete_many({
            "user_id": user["id"],
            "id": {"$nin": list(keep)},
        })
    await _audit(
        "harmonic.profile_saved", request,
        user_id=user["id"], user_email=user.get("email"),
        metadata={
            "duration": body.duration,
            "dominant_count": len(body.dominant),
            "is_eigenmode": is_first_ever,
            "confirmed_gaps_count": len(body.confirmed_gaps),
        },
    )
    doc.pop("_id", None)
    return {"ok": True, "profile": doc, "is_eigenmode": is_first_ever}


@api.post("/harmonic-blueprint/eigenmode/promote/{profile_id}")
async def promote_eigenmode(
    profile_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Promote an existing profile to be the user's new eigenmode baseline.
    Used by the 'Set as new baseline' action on the results panel."""
    if not _is_pro(user):
        raise HTTPException(status_code=402, detail="Harmonic Blueprint is a Pro feature.")
    target = await db.resonance_profiles.find_one(
        {"id": profile_id, "user_id": user["id"]}, {"_id": 0},
    )
    if not target:
        raise HTTPException(status_code=404, detail="Profile not found.")
    # Atomic-ish swap: clear all, set target. Two writes so a crash between
    # them leaves the user with zero eigenmodes — `_ensure_eigenmode` will
    # self-heal on next fetch by promoting the oldest surviving doc.
    await db.resonance_profiles.update_many(
        {"user_id": user["id"]},
        {"$set": {"is_eigenmode": False}},
    )
    await db.resonance_profiles.update_one(
        {"id": profile_id},
        {"$set": {"is_eigenmode": True}},
    )
    await _audit(
        "harmonic.eigenmode_promoted", request,
        user_id=user["id"], user_email=user.get("email"),
        metadata={"profile_id": profile_id},
    )
    target["is_eigenmode"] = True
    return {"ok": True, "eigenmode": target}


@api.delete("/harmonic-blueprint/profile")
async def delete_harmonic_profile(request: Request, user: dict = Depends(get_current_user)):
    """Full reset — used by the 'Record again' entry point when a user wants
    a fresh baseline."""
    if not _is_pro(user):
        raise HTTPException(status_code=402, detail="Harmonic Blueprint is a Pro feature.")
    await db.resonance_profiles.delete_many({"user_id": user["id"]})
    await _audit(
        "harmonic.profile_reset", request,
        user_id=user["id"], user_email=user.get("email"),
    )
    return {"ok": True}


# --- Phase 3: Eigenmode Journey generator -------------------------------------
# A curated playlist assembled from Solarisound's existing catalog to guide the
# user back toward their eigenmode baseline. Selection is deterministic and
# rule-based: each confirmed_gap picks catalog entries whose `targets_bands`
# overlap. Free users get a 2-track preview; Pro users get the full run.
#
# NOTE ON DATA SOURCE: this catalog mirrors the frontend content already
# available to the audio engines (solfeggio frequencies, Sound Baths, Flow
# journeys). Storing it server-side lets the client render each track's copy
# verbatim from a single source of truth.

HARMONIC_JOURNEY_CATALOG = [
    # Solfeggio single-tone tracks — thin layer over audioEngine.play(freq).
    {"id": "solf-174", "type": "solfeggio", "name": "174 Hz Foundation",
     "freq": 174, "duration_seconds": 300, "targets_bands": ["sub"],
     "tagline": "Pain relief · grounding root"},
    {"id": "solf-285", "type": "solfeggio", "name": "285 Hz Healing",
     "freq": 285, "duration_seconds": 300, "targets_bands": ["low"],
     "tagline": "Tissue restore · warm depth"},
    {"id": "solf-396", "type": "solfeggio", "name": "396 Hz Liberation",
     "freq": 396, "duration_seconds": 300, "targets_bands": ["lowmid"],
     "tagline": "Release fear · reopen chest"},
    {"id": "solf-417", "type": "solfeggio", "name": "417 Hz Renewal",
     "freq": 417, "duration_seconds": 300, "targets_bands": ["lowmid"],
     "tagline": "Undo change · reset resonance"},
    {"id": "solf-432", "type": "solfeggio", "name": "432 Hz Earth",
     "freq": 432, "duration_seconds": 300, "targets_bands": ["lowmid", "mid"],
     "tagline": "Natural tuning · settle the field"},
    {"id": "solf-528", "type": "solfeggio", "name": "528 Hz Miracle",
     "freq": 528, "duration_seconds": 300, "targets_bands": ["mid"],
     "tagline": "DNA repair · love · expressive core"},
    {"id": "solf-639", "type": "solfeggio", "name": "639 Hz Connection",
     "freq": 639, "duration_seconds": 300, "targets_bands": ["mid"],
     "tagline": "Relationship · heart-mid bridge"},
    {"id": "solf-741", "type": "solfeggio", "name": "741 Hz Awakening",
     "freq": 741, "duration_seconds": 300, "targets_bands": ["uppermid"],
     "tagline": "Expression · unlock the throat"},
    {"id": "solf-852", "type": "solfeggio", "name": "852 Hz Intuition",
     "freq": 852, "duration_seconds": 300, "targets_bands": ["uppermid"],
     "tagline": "Spiritual order · inner sight"},
    {"id": "solf-963", "type": "solfeggio", "name": "963 Hz Unity",
     "freq": 963, "duration_seconds": 300, "targets_bands": ["presence"],
     "tagline": "Pure being · crown brightness"},
    # Sound Baths — richer immersive textures (`ref` matches soundBathEngine.js).
    {"id": "bath-grounding", "type": "soundbath", "name": "Grounding Bath",
     "ref": "grounding", "freq": 174, "duration_seconds": 600,
     "targets_bands": ["sub", "low"],
     "tagline": "Deep-earth drone bath for anchoring"},
    {"id": "bath-solfeggio", "type": "soundbath", "name": "Solfeggio Wash",
     "ref": "solfeggio", "freq": 528, "duration_seconds": 600,
     "targets_bands": ["mid", "lowmid"],
     "tagline": "Layered solfeggio harmonics for the heart-mid range"},
    {"id": "bath-aurora", "type": "soundbath", "name": "Aurora Bath",
     "ref": "aurora", "freq": 741, "duration_seconds": 600,
     "targets_bands": ["uppermid", "presence"],
     "tagline": "Shimmering high-band aurora sweeps"},
    # Flow Mode journeys — 3-stage guided crossfades (`ref` = JOURNEYS key).
    {"id": "flow-deep_restore", "type": "flow", "name": "Flow · Deep Restore",
     "ref": "deep_restore", "freq": 432, "duration_seconds": 900,
     "targets_bands": ["lowmid", "mid"],
     "tagline": "Liberation → Renewal → Earth"},
    {"id": "flow-morning_rise", "type": "flow", "name": "Flow · Morning Rise",
     "ref": "morning_rise", "freq": 528, "duration_seconds": 900,
     "targets_bands": ["mid", "presence"],
     "tagline": "Gamma → Miracle → Unity"},
    {"id": "flow-night_drift", "type": "flow", "name": "Flow · Night Drift",
     "ref": "night_drift", "freq": 174, "duration_seconds": 900,
     "targets_bands": ["sub", "low"],
     "tagline": "Foundation → Delta → Theta"},
]

# Human-readable band labels used in the personalised rationale copy. Kept in
# sync with the frontend BAND_MEANINGS dictionary.
_BAND_LABELS = {
    "sub": "grounding root",
    "low": "warm depth",
    "lowmid": "chest resonance",
    "mid": "expressive core",
    "uppermid": "articulation range",
    "presence": "brightness and openness",
}


def _rationale_for(track: dict, gap: dict) -> str:
    """Build a plain-language rationale linking a track to a specific gap.
    Copy stays supportive + non-diagnostic to match the Phase 2 language rules."""
    label = _BAND_LABELS.get(gap.get("key", ""), gap.get("label", "range"))
    direction = gap.get("direction", "quieter")
    if direction == "quieter":
        return (
            f"{track['name']} has been included to support your "
            f"{label} which has drifted from your natural tuning."
        )
    return (
        f"{track['name']} has been included to help settle your "
        f"{label}, currently more amplified than your baseline."
    )


def _demo_gaps() -> list[dict]:
    """Fallback gap set for users with no confirmed_gaps yet (or free tier).
    Sub + presence quieter is the most common casual-recording drift and
    lets the demo journey feel meaningful even without a real capture."""
    return [
        {"key": "sub",      "label": "Grounding & root",       "direction": "quieter",
         "delta_db": -6.0, "lo": 60,   "hi": 160},
        {"key": "presence", "label": "Brightness & openness",  "direction": "quieter",
         "delta_db": -5.0, "lo": 2400, "hi": 4000},
    ]


def _generate_journey_tracks(gaps: list[dict]) -> list[dict]:
    """Match confirmed gaps to catalog entries. For each gap we pick up to two
    tracks (a solfeggio single-tone + a richer bath/flow) whose targets_bands
    include the gap's key. Preserve gap order (already ranked by magnitude in
    Phase 2) and deduplicate by track id."""
    picked: list[dict] = []
    seen: set[str] = set()
    for gap in gaps:
        key = gap.get("key")
        if not key:
            continue
        matches = [t for t in HARMONIC_JOURNEY_CATALOG if key in t["targets_bands"]]
        matches.sort(key=lambda t: (t["type"] != "solfeggio", t["duration_seconds"]))
        added = 0
        for t in matches:
            if t["id"] in seen:
                continue
            seen.add(t["id"])
            entry = {**t, "rationale": _rationale_for(t, gap),
                     "gap_key": key, "gap_label": gap.get("label", "")}
            picked.append(entry)
            added += 1
            if added >= 2:
                break
    if not picked:
        picked.append({
            **next(t for t in HARMONIC_JOURNEY_CATALOG if t["id"] == "solf-432"),
            "rationale": "432 Hz Earth · a universal centering tone included as a natural anchor.",
            "gap_key": None, "gap_label": "",
        })
    return picked


@api.post("/harmonic-blueprint/journey/generate")
async def generate_harmonic_journey(request: Request, user: dict = Depends(get_current_user)):
    """Generate the user's personalised Eigenmode Journey playlist. Pro users
    receive the full curated playlist; free-tier users receive a 2-track
    preview + upgrade metadata. This endpoint is intentionally NOT Pro-gated
    so the funnel can be demonstrated to free users."""
    is_pro = _is_pro(user)
    ip = _client_ip(request)
    _rate_limit_or_429(
        f"journey:{user['id']}:{ip}", capacity=8, refill_per_sec=1 / 300,
        label="journey generation",
    )
    latest = await db.resonance_profiles.find_one(
        {"user_id": user["id"]}, {"_id": 0},
        sort=[("created_at", -1)],
    )
    gaps: list[dict] = []
    if latest and latest.get("confirmed_gaps"):
        gaps = latest["confirmed_gaps"]
    elif latest and latest.get("underrepresented"):
        gaps = [
            {"key": b.get("key"), "label": b.get("label", ""),
             "direction": "quieter", "lo": b.get("lo"), "hi": b.get("hi")}
            for b in latest["underrepresented"] if b.get("key")
        ]
    if not gaps:
        gaps = _demo_gaps()
    tracks = _generate_journey_tracks(gaps)
    full_track_count = len(tracks)
    if not is_pro:
        tracks = tracks[:2]
    journey = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "name": "Your Eigenmode Journey",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile_id": (latest or {}).get("id"),
        "tier": "pro" if is_pro else "free",
        "gaps_used": gaps,
        "tracks": tracks,
        "full_track_count": full_track_count,
        "total_duration_seconds": sum(t["duration_seconds"] for t in tracks),
        "upgrade_prompt": None if is_pro else "Unlock your full Eigenmode Journey with Pro.",
    }
    await db.harmonic_journeys.insert_one({**journey})
    all_docs = await db.harmonic_journeys.find(
        {"user_id": user["id"]}, {"id": 1, "created_at": 1, "_id": 0},
        sort=[("created_at", -1)],
    ).to_list(100)
    keep = {r["id"] for r in all_docs[:3]}
    if len(all_docs) > 3:
        await db.harmonic_journeys.delete_many({
            "user_id": user["id"], "id": {"$nin": list(keep)},
        })
    await _audit(
        "harmonic.journey_generated", request,
        user_id=user["id"], user_email=user.get("email"),
        metadata={"tier": journey["tier"], "track_count": len(tracks)},
    )
    journey.pop("_id", None)
    return journey


@api.get("/harmonic-blueprint/journey")
async def get_latest_harmonic_journey(user: dict = Depends(get_current_user)):
    """Return the most recent journey for this user (or null). Not Pro-gated
    so free users can pull their preview back on subsequent visits."""
    doc = await db.harmonic_journeys.find_one(
        {"user_id": user["id"]}, {"_id": 0},
        sort=[("created_at", -1)],
    )
    return {"journey": doc}


# --- Phase 4: Account-view summary, drift history, gap CRUD -----------------
# Progressive personalisation: the Account page shows everything the user has
# accumulated (eigenmode, latest capture, current drift, confirmed points,
# most recent journey). The same helpers feed the LLM prompts so every
# subsequent Wellness Assistant / AI Prescription session gets richer.

def _band_map(bands: list[dict]) -> dict:
    return {b.get("key"): b for b in (bands or []) if b and b.get("key")}


def _compute_drift(latest: dict, eigen: dict, min_delta_db: float = 4.0) -> list[dict]:
    """Server-side twin of the frontend `compareToEigenmode`. Returns ranked
    findings ≥ min_delta_db drift from eigenmode, top 5."""
    if not latest or not eigen:
        return []
    cur = _band_map(latest.get("bands", []))
    eig = _band_map(eigen.get("bands", []))
    findings: list[dict] = []
    for key, meta in _BAND_LABELS.items():
        c = cur.get(key)
        e = eig.get(key)
        if not c or not e:
            continue
        delta = float(c.get("db", -60)) - float(e.get("db", -60))
        magnitude = abs(delta)
        if magnitude < min_delta_db:
            continue
        direction = "quieter" if delta < 0 else "louder"
        findings.append({
            "key": key,
            "label": meta,
            "direction": direction,
            "delta_db": round(delta, 2),
            "magnitude": round(magnitude, 2),
            "lo": e.get("lo"),
            "hi": e.get("hi"),
        })
    findings.sort(key=lambda f: -f["magnitude"])
    return findings[:5]


async def _user_settings(user_id: str) -> dict:
    """Return the user's assistant settings, filling in defaults. Kept small
    so we can extend it (mute chip, dark mode preference, etc.) without a
    migration. All settings default to the pre-existing behaviour so an
    empty document is fully backward-compatible.
    """
    doc = await db.users.find_one(
        {"id": user_id},
        {"assistant_settings": 1},
    ) or {}
    s = (doc.get("assistant_settings") or {})
    return {
        "harmonic_influence_enabled": bool(s.get("harmonic_influence_enabled", True)),
    }


def _hb_note_for_frequency(hz: float, confirmed_gaps: list, drift: list) -> Optional[str]:
    """Deterministic, non-LLM annotation. Returns a soft one-liner when the
    given `hz` falls inside a confirmed resonance gap band OR a strong
    current-drift band (≥ 3 dB deviation). Returns None otherwise.

    Kept intentionally brief and non-clinical — the goal is background
    intelligence, not a lecture.
    """
    if not isinstance(hz, (int, float)) or hz <= 0:
        return None
    hz_f = float(hz)
    for g in (confirmed_gaps or [])[:8]:
        try:
            lo = float(g.get("lo"))
            hi = float(g.get("hi"))
        except (TypeError, ValueError):
            continue
        if lo <= hz_f <= hi:
            label = g.get("label") or g.get("key") or "your resonance points"
            return (
                "Also aligns with your Harmonic Blueprint — targets the "
                f"{label} range you've affirmed as personally relevant."
            )
    for f in (drift or [])[:6]:
        try:
            lo = float(f.get("lo"))
            hi = float(f.get("hi"))
            delta = float(f.get("delta_db", 0))
        except (TypeError, ValueError):
            continue
        if abs(delta) >= 3.0 and lo <= hz_f <= hi:
            return (
                "Also aligns with your Harmonic Blueprint — targets a range "
                "where your natural tuning currently shows some drift."
            )
    return None


async def _hb_notes_context(user_id: str) -> tuple[list, list]:
    """Fetch (confirmed_gaps, drift) once so a whole batch of suggestions
    can be annotated without repeated Mongo hits. Returns ([], []) when
    the user has no HB profile."""
    try:
        eigen = await _ensure_eigenmode(user_id)
    except Exception:
        return [], []
    if not eigen:
        return [], []
    latest = await db.resonance_profiles.find_one(
        {"user_id": user_id}, {"_id": 0},
        sort=[("created_at", -1)],
    )
    confirmed = (latest or {}).get("confirmed_gaps") or []
    drift: list = []
    if latest and latest.get("id") != eigen.get("id"):
        try:
            drift = _compute_drift(latest, eigen) or []
        except Exception:
            drift = []
    return confirmed, drift


async def _harmonic_context_for_llm(user_id: str) -> str:
    """Compact LLM-friendly snapshot of the user's Harmonic Blueprint state.
    Injected into Wellness Assistant + AI Prescription prompts so every
    recommendation gets progressively more personalised."""
    try:
        eigen = await _ensure_eigenmode(user_id)
    except Exception:
        return ""
    if not eigen:
        return ""
    latest = await db.resonance_profiles.find_one(
        {"user_id": user_id}, {"_id": 0},
        sort=[("created_at", -1)],
    )
    parts = ["HARMONIC_BLUEPRINT (user's saved harmonic signature — use as an "
             "extra personalisation input alongside their current mood/goals):"]
    ebands = _band_map(eigen.get("bands", []))
    if ebands:
        parts.append(
            "- eigenmode bands (dB): " + ", ".join(
                f"{k}={round(float(ebands[k].get('db', -60)), 1)}"
                for k in ("sub", "low", "lowmid", "mid", "uppermid", "presence")
                if k in ebands
            )
        )
    edom = eigen.get("dominant") or []
    if edom:
        parts.append(
            "- baseline dominant frequencies: "
            + ", ".join(f"{round(p['hz'])}Hz" for p in edom[:4])
        )
    confirmed = (latest or {}).get("confirmed_gaps") or []
    if confirmed:
        parts.append(
            "- confirmed resonance points the user affirmed as personally "
            "relevant (favour tracks/frequencies that address these):"
        )
        for g in confirmed[:5]:
            parts.append(
                f"  · {g.get('label', g.get('key'))} "
                f"({g.get('lo')}-{g.get('hi')}Hz) — {g.get('direction')}"
            )
    if latest and latest.get("id") != eigen.get("id"):
        drift = _compute_drift(latest, eigen)
        if drift:
            parts.append("- current drift from baseline:")
            for f in drift[:3]:
                parts.append(f"  · {f['label']}: {f['delta_db']:+.1f} dB")
    parts.append(
        "When appropriate, subtly weight your suggestions toward frequencies "
        "and presets that support the affirmed resonance points and drift. "
        "Never mention the raw numeric details unless the user asks."
    )
    return "\n".join(parts)


@api.get("/harmonic-blueprint/summary")
async def harmonic_blueprint_summary(user: dict = Depends(get_current_user)):
    """One-shot payload for the Account → Harmonic Blueprint section."""
    eigen = await _ensure_eigenmode(user["id"])
    latest = await db.resonance_profiles.find_one(
        {"user_id": user["id"]}, {"_id": 0},
        sort=[("created_at", -1)],
    )
    journey = await db.harmonic_journeys.find_one(
        {"user_id": user["id"]}, {"_id": 0},
        sort=[("created_at", -1)],
    )
    drift = _compute_drift(latest, eigen) if (latest and eigen) else []
    return {
        "eigenmode": eigen,
        "latest_profile": latest,
        "current_drift": drift,
        "confirmed_gaps": (latest or {}).get("confirmed_gaps") or [],
        "latest_journey": journey,
        "is_pro": _is_pro(user),
    }


@api.get("/harmonic-blueprint/history")
async def harmonic_blueprint_history(user: dict = Depends(get_current_user)):
    """Time-series drift view — every retained profile with per-band delta
    from the eigenmode baseline. Powers the drift-over-time chart."""
    if not _is_pro(user):
        raise HTTPException(status_code=402, detail="Harmonic Blueprint is a Pro feature.")
    eigen = await _ensure_eigenmode(user["id"])
    if not eigen:
        return {"history": [], "eigenmode_id": None}
    docs = await db.resonance_profiles.find(
        {"user_id": user["id"]}, {"_id": 0},
        sort=[("created_at", 1)],
    ).to_list(50)
    ebands = _band_map(eigen.get("bands", []))
    entries = []
    for d in docs:
        cur = _band_map(d.get("bands", []))
        band_deltas = {
            k: round(float(cur.get(k, {}).get("db", ebands.get(k, {}).get("db", -60)))
                     - float(ebands.get(k, {}).get("db", -60)), 2)
            for k in ("sub", "low", "lowmid", "mid", "uppermid", "presence")
            if k in ebands
        }
        entries.append({
            "id": d["id"],
            "created_at": d.get("created_at"),
            "is_eigenmode": bool(d.get("is_eigenmode")),
            "duration": d.get("duration"),
            "band_deltas": band_deltas,
            "drift_score": round(sum(abs(v) for v in band_deltas.values()), 2),
            "confirmed_gap_count": len(d.get("confirmed_gaps") or []),
        })
    return {"history": entries, "eigenmode_id": eigen.get("id")}


class GapEditIn(BaseModel):
    confirmed_gaps: list[dict] = Field(default_factory=list, max_length=16)


@api.patch("/harmonic-blueprint/profile/{profile_id}/gaps")
async def update_profile_gaps(
    profile_id: str,
    body: GapEditIn,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Replace the confirmed_gaps array on a profile. Used by the Account
    section when a user removes / edits individual resonance points without
    re-recording. Pro-only."""
    if not _is_pro(user):
        raise HTTPException(status_code=402, detail="Harmonic Blueprint is a Pro feature.")
    target = await db.resonance_profiles.find_one(
        {"id": profile_id, "user_id": user["id"]}, {"_id": 0},
    )
    if not target:
        raise HTTPException(status_code=404, detail="Profile not found.")
    await db.resonance_profiles.update_one(
        {"id": profile_id},
        {"$set": {"confirmed_gaps": body.confirmed_gaps}},
    )
    await _audit(
        "harmonic.gaps_updated", request,
        user_id=user["id"], user_email=user.get("email"),
        metadata={"profile_id": profile_id, "count": len(body.confirmed_gaps)},
    )
    return {"ok": True, "confirmed_gaps": body.confirmed_gaps}




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
    name = str(raw.get("name", "Wellness Prescription"))[:80]
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
    # Phase 4: personalise with the user's saved Harmonic Blueprint signature.
    # Silently no-ops when the user hasn't captured one yet — no extra tokens.
    # Also gate on the user's assistant setting so opt-outs get a fully
    # HB-free recommendation.
    settings = await _user_settings(user["id"])
    hb_enabled = settings.get("harmonic_influence_enabled", True)
    if hb_enabled:
        hb_ctx = await _harmonic_context_for_llm(user["id"])
        if hb_ctx:
            parts.append(hb_ctx)
    # Phase 6: preference boost from post-session reflections. When the user
    # has consistently rated a frequency positively for this mood in the past,
    # surface a soft hint the LLM can lean on. Silently no-ops on cold-start.
    try:
        pref_hint = await _mood_preference_hint(user["id"], body.mood)
        if pref_hint:
            parts.append(pref_hint)
    except Exception as exc:
        logger.warning("[ai_recommend] mood_preference_hint failed: %s", exc)
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
        # Phase 8: attach a soft "aligns with your Harmonic Blueprint" note
        # when the returned frequency falls inside a confirmed resonance
        # gap OR a strong current-drift band. Skipped entirely when the
        # user has toggled HB influence off — settings resolved above.
        if hb_enabled:
            try:
                gaps, drift = await _hb_notes_context(user["id"])
                note = _hb_note_for_frequency(reco.get("frequency"), gaps, drift)
                if note:
                    reco["harmonic_note"] = note
            except Exception as exc:
                logger.warning("[ai_recommend] hb_note annotation failed: %s", exc)
        return reco
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("AI recommend failed: %s", exc)
        raise HTTPException(status_code=502, detail="AI recommendation temporarily unavailable. Please try again in a moment.")


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
      "kind": "ai_prescription", // launches the full Wellness Prescription with this intent
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
- Wellness Prescription is Pro — include it when their need is complex/specific.
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


class JourneyLogIn(BaseModel):
    """One completed listening session. Written from the client the moment a
    ≥60-second run ends (same trigger as the streak check-in), so the
    Wellness Assistant can build a longitudinal memory of what worked when.

    All fields are optional except the durations — we tolerate partial
    captures rather than refuse the row.
    """
    frequency: Optional[float] = Field(default=None, ge=0, le=20000)
    waveform: Optional[str] = Field(default=None, max_length=16)
    binaural: Optional[float] = Field(default=None, ge=0, le=200)
    ambient: Optional[dict] = None                # {rain: 0..1, ocean: 0..1, ...}
    soundscape: Optional[str] = Field(default=None, max_length=32)     # active curated mix key, if any
    preset_key: Optional[str] = Field(default=None, max_length=48)     # sound-bath / journey preset, if any
    preset_label: Optional[str] = Field(default=None, max_length=80)
    duration_planned_seconds: Optional[int] = Field(default=None, ge=0, le=86400)
    duration_actual_seconds: int = Field(ge=0, le=86400)
    mood: Optional[str] = Field(default=None, max_length=300)
    extended: bool = False
    ended_early: bool = False
    agent_initiated: bool = False


class JourneyReflectionIn(BaseModel):
    """One post-session emotional reflection. Attached to an existing
    wellness_journey entry via `POST /api/me/journey/{entry_id}/reflection`.
    Server derives `sentiment` from a lightweight keyword classifier so we
    can bias future frequency suggestions without an LLM round-trip.
    """
    question: str = Field(min_length=1, max_length=200)
    response: str = Field(min_length=1, max_length=500)


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

    # Phase 4: inject the user's Harmonic Blueprint signature (eigenmode +
    # confirmed resonance points + current drift). Silently no-ops when the
    # user hasn't captured one yet, so free / new users aren't affected.
    # Phase 8: gated by the user's assistant setting so opt-outs get a
    # fully HB-free experience.
    hb_settings = await _user_settings(user["id"])
    hb_enabled = hb_settings.get("harmonic_influence_enabled", True)
    if hb_enabled:
        try:
            hb_ctx = await _harmonic_context_for_llm(user["id"])
            if hb_ctx:
                parts.append(hb_ctx)
        except Exception as exc:
            logger.warning("[agent_chat] harmonic_context lookup failed: %s", exc)

    # Longitudinal wellness memory — recent completed sessions (last 8 of 30).
    # Lets the assistant say "Last time you felt anxious, 432 Hz Earth helped
    # you settle — want to start there?" Silently no-ops for new users.
    try:
        wj_ctx = await _wellness_journey_for_llm(user["id"], limit=8)
        if wj_ctx:
            parts.append(wj_ctx)
    except Exception as exc:
        logger.warning("[agent_chat] wellness_journey lookup failed: %s", exc)

    # Phase 6: mood-preference boost from post-session reflections. When the
    # current message echoes a mood the user has previously reflected on
    # positively for a specific frequency, we surface a soft hint so the LLM
    # can naturally lean toward the same suggestion.
    try:
        pref_hint = await _mood_preference_hint(user["id"], body.message)
        if pref_hint:
            parts.append(pref_hint)
    except Exception as exc:
        logger.warning("[agent_chat] mood_preference_hint failed: %s", exc)

    # Phase 7: behavioural patterns detected across the user's journey.
    # Compact block, top-3 non-dismissed patterns, with soft one-callback
    # guidance so the LLM doesn't lecture.
    try:
        pat_block = await _user_patterns_prompt_block(user["id"])
        if pat_block:
            parts.append(pat_block)
    except Exception as exc:
        logger.warning("[agent_chat] user_patterns block failed: %s", exc)

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

    # Audit only the FIRST turn of each companion session — subsequent turns
    # in the same conversation stay quiet so the audit log doesn't drown in
    # per-message rows. `history` is empty on turn 1 by construction.
    if not (body.history or []):
        await _audit(
            "agent.session_started", request,
            user_id=user["id"], user_email=user.get("email"),
            metadata={"session_id": body.session_id, "message_preview": body.message.strip()[:80]},
        )

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
        # Phase 8: annotate each suggestion carrying a `frequency` with a
        # soft HB alignment note when applicable. Skipped when the user
        # has toggled HB influence off.
        if hb_enabled and reply.get("suggestions"):
            try:
                gaps, drift = await _hb_notes_context(user["id"])
                if gaps or drift:
                    for s in reply["suggestions"]:
                        f = s.get("frequency")
                        if isinstance(f, (int, float)) and f > 0:
                            note = _hb_note_for_frequency(f, gaps, drift)
                            if note:
                                s["harmonic_note"] = note
            except Exception as exc:
                logger.warning("[agent_chat] hb_note annotation failed: %s", exc)
        return reply
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("agent_chat failed: %s", exc)
        raise HTTPException(status_code=502, detail="Wellness Assistant is temporarily unavailable. Please try again in a moment.")


@api.post("/me/agent/checkin")
async def agent_checkin(body: AgentCheckinIn, request: Request, user: dict = Depends(get_current_user)):
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
    await _audit(
        "agent.suggestion_taken", request,
        user_id=user["id"], user_email=user.get("email"),
        metadata={
            "kind": record_sug.get("kind"),
            "label": record_sug.get("label"),
            "mood_preview": body.message.strip()[:80],
        },
    )
    return {"ok": True, "id": doc["id"]}


# --- Wellness Journey (longitudinal session memory) ---------------------------
JOURNEY_MAX_PER_USER = 30  # last N sessions retained per user


def _time_of_day_label(dt: datetime) -> str:
    """Coarse bucket used both server-side (for the LLM prompt) and displayed
    verbatim in the "My Journey" timeline row. UTC-based — good enough given
    we don't ship user timezone anywhere else on the server yet."""
    h = dt.hour
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    if 17 <= h < 22:
        return "evening"
    return "night"


def _summarise_journey_entry(row: dict) -> str:
    """Compact one-line summary for embedding in the agent_chat LLM prompt.
    Kept short (≤ ~200 chars) so 8 rows fit comfortably in the token budget."""
    when = str(row.get("created_at") or "")[:19]  # YYYY-MM-DDTHH:MM:SS
    tod = row.get("time_of_day") or ""
    mood = str(row.get("mood") or "").strip()[:80]
    bits: list = []
    if row.get("preset_label"):
        bits.append(str(row["preset_label"])[:40])
    elif row.get("frequency"):
        try:
            bits.append(f"{float(row['frequency']):.0f} Hz")
        except Exception:
            pass
    if row.get("soundscape"):
        bits.append(f"+{row['soundscape']}")
    dur_min = max(1, int(round((row.get("duration_actual_seconds") or 0) / 60)))
    tags: list = []
    if row.get("ended_early"):
        tags.append("ended early")
    if row.get("extended"):
        tags.append("extended")
    if row.get("agent_initiated"):
        tags.append("assistant-led")
    # Reflection tail — appended when the user answered the post-session
    # follow-up question. Sentiment is derived on write via _classify_sentiment.
    refl = row.get("reflection") or None
    refl_tail = ""
    if isinstance(refl, dict) and refl.get("response"):
        r_txt = str(refl["response"]).strip()[:80]
        sent = str(refl.get("sentiment") or "neutral")
        refl_tail = f'reflected "{r_txt}" ({sent})'
    payload = " · ".join(x for x in [
        f"{when} ({tod})" if tod else when,
        f'mood "{mood}"' if mood else "",
        " ".join(bits) if bits else "session",
        f"{dur_min} min",
        ", ".join(tags),
        refl_tail,
    ] if x)
    return "- " + payload


# --- Sentiment (keyword-based, deterministic) ---------------------------------
# Deliberately tiny. If we later need nuance (sarcasm, mixed sentiment) we can
# swap in an LLM classifier here — the rest of the code only cares about the
# 3-way "positive|neutral|negative" return.
_POSITIVE_TOKENS = {
    "yes", "yeah", "yep", "yup", "definitely", "absolutely", "totally",
    "calm", "calmer", "better", "settled", "grounded", "centered", "centred",
    "great", "amazing", "wonderful", "beautiful", "lovely", "loved", "love",
    "deep", "deeper", "relaxed", "relaxing", "relief", "released", "release",
    "resonant", "resonated", "resonance",
    "right", "helped", "helping", "helpful", "peaceful", "peace", "quiet",
    "focused", "clear", "clearer", "open", "opened", "spacious", "soft",
    "renewed", "restored", "warm", "warmer", "melted", "held", "safe",
    "good", "positive", "nice", "gentle", "sweet", "meditative", "healing",
}
_NEGATIVE_TOKENS = {
    "no", "nope", "not really", "didn't", "did not", "didnt",
    "nothing", "none", "worse", "harder", "off", "wrong", "meh",
    "agitated", "restless", "anxious", "distracted", "tense", "irritated",
    "angry", "annoyed", "boring", "bored", "flat", "empty", "numb",
    "couldn't", "couldnt", "unable", "can't", "cant",
    "uncomfortable", "disturbed", "unsettled", "harsh",
    "bad", "negative", "hate", "hated", "awful", "terrible",
}
# Compact negation cue — if any of these appear, we downgrade a positive
# token that follows them within ~4 tokens ("did NOT feel calm").
_NEGATORS = {"not", "no", "never", "hardly", "barely", "didn't", "didnt",
             "wasn't", "wasnt", "isn't", "isnt", "don't", "dont"}


def _classify_sentiment(text: str) -> str:
    """Return one of 'positive' | 'neutral' | 'negative'. Deterministic.

    Rules:
      1. Tokenise on non-word chars, lowercase, strip.
      2. Count positive / negative tokens; a negator within 4 tokens flips
         the polarity of the token that follows it.
      3. Ties or empty text → 'neutral'.
    """
    import re as _re
    if not text:
        return "neutral"
    tokens = [t for t in _re.split(r"[^A-Za-z']+", text.lower()) if t]
    pos = 0
    neg = 0
    for i, tok in enumerate(tokens):
        # Check for a negator up to 3 tokens back.
        negated = any(tokens[j] in _NEGATORS for j in range(max(0, i - 3), i))
        if tok in _POSITIVE_TOKENS:
            if negated:
                neg += 1
            else:
                pos += 1
        elif tok in _NEGATIVE_TOKENS:
            if negated:
                pos += 1
            else:
                neg += 1
    if pos == 0 and neg == 0:
        return "neutral"
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


# --- Mood-preference boost for AI prescription --------------------------------
def _mood_bucket(text: Optional[str]) -> Optional[str]:
    """Reduce a free-form mood string to a coarse bucket so we can match
    "I'm feeling really anxious right now" against a prior "anxious" reflection.
    Returns the first matching bucket or None for unbucketable input."""
    if not text:
        return None
    lc = text.lower()
    buckets = {
        "anxious":  ["anxious", "anxiety", "panic", "on edge", "nervous", "worried"],
        "stressed": ["stressed", "stress", "overwhelmed", "burned out", "burnout"],
        "tired":    ["tired", "exhausted", "drained", "sleepy", "wiped"],
        "sad":      ["sad", "down", "low", "blue", "grief", "grieving", "heavy"],
        "restless": ["restless", "agitated", "wound up", "cant sit", "can't sit"],
        "angry":    ["angry", "furious", "irritated", "frustrated", "mad"],
        "focused":  ["focus", "focused", "study", "studying", "work", "flow"],
        "grounding":["grounding", "grounded", "root", "settle"],
        "sleep":    ["sleep", "insomnia", "cant sleep", "can't sleep", "falling asleep"],
        "calm":     ["calm", "peaceful", "quiet", "still"],
    }
    for bucket, cues in buckets.items():
        for cue in cues:
            if cue in lc:
                return bucket
    return None


async def _mood_preference_hint(user_id: str, current_mood: Optional[str]) -> str:
    """Build a USER_PREFERENCE_HINT string for the AI prescription prompt when
    the current mood echoes one the user has previously reflected positively
    on. Returns "" when there's no confident preference.

    A preference is defined as: ≥ 1 journey row with (mood_bucket == current
    bucket) AND (reflection.sentiment == 'positive') AND a frequency or
    preset_label attached to the row. We surface the top-3 most recent hits.
    """
    bucket = _mood_bucket(current_mood)
    if not bucket:
        return ""
    try:
        cursor = db.wellness_journey.find(
            {"user_id": user_id, "reflection.sentiment": "positive"},
            {"_id": 0},
        ).sort("created_at", -1).limit(30)
        rows = await cursor.to_list(length=30)
    except Exception as exc:
        logger.warning("[mood_preference] lookup failed: %s", exc)
        return ""
    hits: list = []
    for row in rows:
        if _mood_bucket(row.get("mood")) != bucket:
            continue
        label = row.get("preset_label") or (
            f"{float(row['frequency']):.0f} Hz" if row.get("frequency") else None
        )
        if not label:
            continue
        hits.append(label)
        if len(hits) >= 3:
            break
    if not hits:
        return ""
    # Dedupe while preserving order.
    seen = set()
    uniq = [h for h in hits if not (h in seen or seen.add(h))]
    return (
        "USER_PREFERENCE_HINT: "
        f'when this user reports moods like "{bucket}", they have '
        f"responded positively to {', '.join(uniq)}. Favour these if they fit "
        "the request, but you are not required to pick them."
    )


async def _wellness_journey_for_llm(user_id: str, limit: int = 8) -> str:
    """Build the WELLNESS_JOURNEY prompt block. Silently no-ops when the user
    hasn't accumulated any entries yet."""
    try:
        cursor = db.wellness_journey.find(
            {"user_id": user_id}, {"_id": 0}
        ).sort("created_at", -1).limit(limit)
        rows = await cursor.to_list(length=limit)
    except Exception as exc:
        logger.warning("[wellness_journey] fetch failed for %s: %s", user_id, exc)
        return ""
    if not rows:
        return ""
    lines = ["WELLNESS_JOURNEY (most recent listening sessions, newest first):"]
    lines.extend(_summarise_journey_entry(r) for r in rows)
    lines.append(
        "If the user's current state echoes one of these prior sessions, you MAY "
        "gently reference it (e.g. \"Last time you felt anxious, 432 Hz Earth "
        "helped you settle — want to start there?\"). Do NOT force a callback "
        "if it doesn't fit, and never invent details that aren't in this list."
    )
    return "\n".join(lines)


@api.post("/me/journey/log")
async def journey_log(body: JourneyLogIn, user: dict = Depends(get_current_user)):
    """Record one completed listening session. Called from the client the
    moment a ≥ 60-second run ends. We keep only the last JOURNEY_MAX_PER_USER
    entries per user (older rows pruned on write)."""
    # Hard floor — reject junk pings so an empty run doesn't create noise
    # in the timeline nor in the LLM prompt.
    if body.duration_actual_seconds < 60:
        return {"ok": False, "reason": "too_short"}
    now = datetime.now(timezone.utc)
    doc = body.model_dump()
    doc.update({
        "id": uuid.uuid4().hex,
        "user_id": user["id"],
        "created_at": now.isoformat(),
        "time_of_day": _time_of_day_label(now),
    })
    # Normalise the ambient mix to only include active channels ( > 0.01 )
    # so timeline rendering stays clean without post-processing.
    amb = doc.get("ambient") or {}
    if isinstance(amb, dict):
        doc["ambient"] = {
            k: round(float(v), 3)
            for k, v in amb.items()
            if isinstance(v, (int, float)) and float(v) > 0.01
        }
    await db.wellness_journey.insert_one(doc)
    # Prune to last JOURNEY_MAX_PER_USER
    try:
        cursor = db.wellness_journey.find(
            {"user_id": user["id"]}, {"id": 1, "created_at": 1}
        ).sort("created_at", -1).skip(JOURNEY_MAX_PER_USER)
        stale = [r["id"] async for r in cursor]
        if stale:
            await db.wellness_journey.delete_many({"id": {"$in": stale}})
    except Exception as exc:  # noqa: BLE001 — defensive housekeeping
        logger.warning("[journey_log] prune failed: %s", exc)
    doc.pop("_id", None)
    # Invalidate the patterns cache — a new row can materially change
    # detected patterns (unlocking a top_frequency, shifting a mood_at_time,
    # etc.). Cheap $unset on the user doc.
    await _invalidate_patterns_cache(user["id"])
    return {"ok": True, "entry": doc}


@api.get("/me/journey")
async def journey_list(user: dict = Depends(get_current_user)):
    """Return the user's last 30 completed sessions, newest first. Rendered
    by the "My Journey" timeline in the Account dashboard."""
    cursor = db.wellness_journey.find(
        {"user_id": user["id"]}, {"_id": 0}
    ).sort("created_at", -1).limit(JOURNEY_MAX_PER_USER)
    rows = await cursor.to_list(length=JOURNEY_MAX_PER_USER)
    return {"entries": rows}


@api.post("/me/journey/{entry_id}/reflection")
async def journey_reflection(
    entry_id: str,
    body: JourneyReflectionIn,
    user: dict = Depends(get_current_user),
):
    """Attach the user's post-session emotional reflection to an existing
    journey row. The server derives `sentiment` from a deterministic keyword
    classifier so we can bias future frequency suggestions without an extra
    LLM call. Idempotent per entry — a re-submission replaces the previous
    reflection wholesale (users may re-answer if they choose)."""
    row = await db.wellness_journey.find_one(
        {"id": entry_id, "user_id": user["id"]}, {"_id": 0}
    )
    if not row:
        raise HTTPException(status_code=404, detail="Journey entry not found")
    reflection = {
        "question": body.question.strip(),
        "response": body.response.strip(),
        "sentiment": _classify_sentiment(body.response),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.wellness_journey.update_one(
        {"id": entry_id, "user_id": user["id"]},
        {"$set": {"reflection": reflection}},
    )
    # A new reflection can affect USER_PREFERENCE_HINT + patterns downstream;
    # kill the cache so the next /me/patterns read recomputes fresh.
    await _invalidate_patterns_cache(user["id"])
    return {"ok": True, "reflection": reflection}


# --- Behavioural pattern detection (Phase 7) ----------------------------------
# Read-only scan of a user's wellness_journey rows. Returns a list of
# pattern objects; each pattern requires ≥ 3 supporting rows before it
# ever surfaces, so cold-start users see no proactive prompts.

# Client-side ambient channel catalog. Kept here (rather than imported from
# frontend) so pattern detection isn't coupled to frontend deploys. If the
# frontend ever renames a channel, we just need to keep this list in sync.
_AMBIENT_CATALOG = (
    "rain", "ocean", "forest", "wind", "crickets", "bowls", "brown", "white",
)
_TOD_LABELS = ("morning", "afternoon", "evening", "night")
_TOD_PHRASING = {
    "morning": "in the morning",
    "afternoon": "in the afternoon",
    "evening": "in the evening",
    "night": "late at night",
}
# Copy templates for the assistant greeting chip. Kept short and warm.
_PATTERN_TEMPLATES = {
    "top_frequency": "You've been gravitating toward {label} this week — shall we continue that journey?",
    "preferred_time_of_day": "You usually tune in {when} — {label} has been your steady favourite.",
    "extension_favorite": "You often extend {label} — want to start there and let it stretch?",
    "unused_soundscapes": "You haven't tried {label} yet — could be a nice one to explore.",
    "mood_at_time": "Around this time you often feel {mood}, and {label} has helped you settle before. Start there?",
}


def _pattern_key(kind: str, value: str) -> str:
    """Stable dedupe key used both for the frontend chip and the user's
    `dismissed_patterns` list."""
    return f"{kind}:{value}"


def _current_tod_utc() -> str:
    """Coarse time-of-day bucket at CALL TIME. Used to weight `mood_at_time`
    patterns so we prefer to surface the mood that matches "right now"."""
    return _time_of_day_label(datetime.now(timezone.utc))


async def _detect_wellness_patterns(user_id: str) -> list[dict]:
    """Pure-Python pattern extraction over the last 30 wellness_journey rows.
    No LLM calls, no per-request cost beyond the single Mongo read the
    endpoint already needs.

    Every returned pattern has:
      { key, kind, label, count, message, cta? }
    where `cta` (when present) is `{action, frequency?|preset_key?|soundscape?}`
    the client can act on with one tap.
    """
    try:
        cursor = db.wellness_journey.find(
            {"user_id": user_id}, {"_id": 0}
        ).sort("created_at", -1).limit(JOURNEY_MAX_PER_USER)
        rows = await cursor.to_list(length=JOURNEY_MAX_PER_USER)
    except Exception as exc:
        logger.warning("[patterns] fetch failed for %s: %s", user_id, exc)
        return []
    total = len(rows)
    if total < 3:
        return []  # cold-start floor — never surface anything

    patterns: list[dict] = []

    # ── Pattern 1: top_frequency ──────────────────────────────────────────
    # A single frequency played ≥ 3 times AND dominating ≥ 40 % of runs
    # that carried a frequency. Presets don't count here — those show up
    # as extension_favorite instead.
    freq_counts: dict[int, int] = {}
    for r in rows:
        f = r.get("frequency")
        if isinstance(f, (int, float)) and f > 0:
            k = int(round(float(f)))
            freq_counts[k] = freq_counts.get(k, 0) + 1
    if freq_counts:
        top_hz, top_n = max(freq_counts.items(), key=lambda x: x[1])
        freq_total = sum(freq_counts.values())
        if top_n >= 3 and (top_n / freq_total) >= 0.40:
            patterns.append({
                "key": _pattern_key("top_frequency", str(top_hz)),
                "kind": "top_frequency",
                "label": f"{top_hz} Hz",
                "count": top_n,
                "message": _PATTERN_TEMPLATES["top_frequency"].format(label=f"{top_hz} Hz"),
                "cta": {"action": "arm_frequency", "frequency": float(top_hz)},
            })

    # ── Pattern 2: preferred_time_of_day ─────────────────────────────────
    # A time-of-day bucket that owns ≥ 50 % of a user's ≥ 3 sessions.
    tod_counts: dict[str, int] = {}
    tod_labels: dict[str, dict[str, int]] = {}   # tod → {primary_label → count}
    for r in rows:
        tod = r.get("time_of_day")
        if tod not in _TOD_LABELS:
            continue
        tod_counts[tod] = tod_counts.get(tod, 0) + 1
        # What did the user actually play at that time? preset_label wins,
        # else "<hz> Hz". We surface the most common one alongside the TOD.
        lbl = r.get("preset_label") or (
            f"{int(round(float(r['frequency'])))} Hz" if isinstance(r.get("frequency"), (int, float)) else None
        )
        if lbl:
            bucket = tod_labels.setdefault(tod, {})
            bucket[lbl] = bucket.get(lbl, 0) + 1
    if tod_counts:
        top_tod, top_tod_n = max(tod_counts.items(), key=lambda x: x[1])
        if top_tod_n >= 3 and (top_tod_n / total) >= 0.50:
            fav_at_tod = None
            if top_tod in tod_labels and tod_labels[top_tod]:
                fav_at_tod = max(tod_labels[top_tod].items(), key=lambda x: x[1])[0]
            label = fav_at_tod or "your usual mix"
            patterns.append({
                "key": _pattern_key("preferred_time_of_day", top_tod),
                "kind": "preferred_time_of_day",
                "label": label,
                "time_of_day": top_tod,
                "count": top_tod_n,
                "message": _PATTERN_TEMPLATES["preferred_time_of_day"].format(
                    when=_TOD_PHRASING[top_tod], label=label,
                ),
            })

    # ── Pattern 3: extension_favorite ────────────────────────────────────
    # An item (preset_label OR "<hz> Hz") the user has extended ≥ 2 times
    # AND has ≥ 3 total sessions of AND has never ended early. Until the
    # Extend +5 chip ships (Task 3), all rows carry extended:false so this
    # returns nothing — code path is future-proof.
    ext_by_item: dict[str, dict] = {}  # label → {total, ext, early}
    for r in rows:
        lbl = r.get("preset_label") or (
            f"{int(round(float(r['frequency'])))} Hz" if isinstance(r.get("frequency"), (int, float)) else None
        )
        if not lbl:
            continue
        st = ext_by_item.setdefault(lbl, {"total": 0, "ext": 0, "early": 0, "row": r})
        st["total"] += 1
        if r.get("extended"):
            st["ext"] += 1
        if r.get("ended_early"):
            st["early"] += 1
    for lbl, st in ext_by_item.items():
        if st["total"] >= 3 and st["ext"] >= 2 and st["early"] == 0:
            row = st["row"]
            cta: Optional[dict] = None
            if row.get("preset_key"):
                cta = {"action": "arm_preset", "preset_key": row["preset_key"], "preset_label": lbl}
            elif row.get("frequency"):
                cta = {"action": "arm_frequency", "frequency": float(row["frequency"])}
            patterns.append({
                "key": _pattern_key("extension_favorite", lbl),
                "kind": "extension_favorite",
                "label": lbl,
                "count": st["ext"],
                "message": _PATTERN_TEMPLATES["extension_favorite"].format(label=lbl),
                **({"cta": cta} if cta else {}),
            })

    # ── Pattern 4: unused_soundscapes ────────────────────────────────────
    # Only surfaces once the user has ≥ 5 total sessions AND there's at
    # least one catalog soundscape they've never touched (either as an
    # `ambient` channel > 0 OR as the `soundscape` field). We pick one to
    # nudge, favouring earlier-listed channels for a stable rotation.
    if total >= 5:
        used: set = set()
        for r in rows:
            if r.get("soundscape"):
                used.add(str(r["soundscape"]))
            amb = r.get("ambient")
            if isinstance(amb, dict):
                for k, v in amb.items():
                    if isinstance(v, (int, float)) and float(v) > 0.01:
                        used.add(str(k))
        for cand in _AMBIENT_CATALOG:
            if cand not in used:
                patterns.append({
                    "key": _pattern_key("unused_soundscapes", cand),
                    "kind": "unused_soundscapes",
                    "label": cand.capitalize(),
                    "count": 0,
                    "message": _PATTERN_TEMPLATES["unused_soundscapes"].format(label=cand.capitalize()),
                    "cta": {"action": "arm_soundscape", "soundscape": cand},
                })
                break  # only nudge one at a time; the next will surface after this is dismissed

    # ── Pattern 5: mood_at_time ──────────────────────────────────────────
    # A (mood_bucket, tod) pair that recurs ≥ 3 times. We prefer to
    # surface the one matching the CURRENT tod, so the chip lands as
    # "around this time" rather than a random hour. Also carries a
    # suggested label (the most common preset / freq the user picked in
    # rows matching that bucket).
    mood_at_time: dict[tuple, int] = {}
    mood_at_time_labels: dict[tuple, dict[str, int]] = {}
    for r in rows:
        bucket = _mood_bucket(r.get("mood"))
        tod = r.get("time_of_day")
        if not bucket or tod not in _TOD_LABELS:
            continue
        key = (bucket, tod)
        mood_at_time[key] = mood_at_time.get(key, 0) + 1
        lbl = r.get("preset_label") or (
            f"{int(round(float(r['frequency'])))} Hz" if isinstance(r.get("frequency"), (int, float)) else None
        )
        if lbl:
            b = mood_at_time_labels.setdefault(key, {})
            b[lbl] = b.get(lbl, 0) + 1
    if mood_at_time:
        current_tod = _current_tod_utc()
        # Prefer pairs matching current_tod, else fall back to the strongest.
        candidates = sorted(
            mood_at_time.items(),
            key=lambda kv: (kv[0][1] == current_tod, kv[1]),
            reverse=True,
        )
        for (bucket, tod), n in candidates:
            if n < 3:
                continue
            lbl_map = mood_at_time_labels.get((bucket, tod), {})
            fav = max(lbl_map.items(), key=lambda x: x[1])[0] if lbl_map else "your usual mix"
            # Try to attach a CTA when the favourite is a raw frequency label
            # (e.g. "432 Hz"). Preset labels are left CTA-less for now
            # because the client-side preset arm dispatch isn't wired yet.
            cta: Optional[dict] = None
            m = re.fullmatch(r"(\d+)\s*Hz", fav)
            if m:
                cta = {"action": "arm_frequency", "frequency": float(m.group(1))}
            patterns.append({
                "key": _pattern_key("mood_at_time", f"{bucket}@{tod}"),
                "kind": "mood_at_time",
                "label": fav,
                "mood": bucket,
                "time_of_day": tod,
                "count": n,
                "message": _PATTERN_TEMPLATES["mood_at_time"].format(mood=bucket, label=fav),
                **({"cta": cta} if cta else {}),
            })
            break  # one is enough

    return patterns


def _pattern_priority(p: dict) -> int:
    """Higher = shown first as the greeting chip. Contextual patterns
    (mood_at_time) win over generic favourites, which win over nudges."""
    order = {
        "mood_at_time": 5,
        "extension_favorite": 4,
        "top_frequency": 3,
        "preferred_time_of_day": 2,
        "unused_soundscapes": 1,
    }
    return order.get(p.get("kind", ""), 0)


async def _user_patterns_prompt_block(user_id: str) -> str:
    """Assemble a compact `USER_PATTERNS` block for the agent_chat LLM
    prompt. Only non-dismissed, top-3-by-priority patterns are included so
    we don't drown the model in signals it already gets from journey rows."""
    try:
        user = await db.users.find_one({"id": user_id}, {"dismissed_patterns": 1})
        dismissed = set((user or {}).get("dismissed_patterns") or [])
        patterns = await _cached_detect_wellness_patterns(user_id)
    except Exception as exc:
        logger.warning("[patterns] prompt build failed: %s", exc)
        return ""
    live = [p for p in patterns if p.get("key") not in dismissed]
    if not live:
        return ""
    live.sort(key=_pattern_priority, reverse=True)
    live = live[:3]
    lines = ["USER_PATTERNS (recurring behaviours worth noticing):"]
    for p in live:
        lines.append(f"- ({p['kind']}) {p['message']}")
    lines.append(
        "You MAY reference one of these once, conversationally, if it fits "
        "the user's current message. Do not enumerate all of them, do not "
        "lecture — a single warm callback is plenty."
    )
    return "\n".join(lines)


# --- Pattern cache (Phase 7.1) ------------------------------------------------
# The pattern detector is cheap (bounded at 30 rows) but /me/patterns is on
# the Dashboard mount critical path, and the greeting chip has to feel
# instant on slow mobile networks. We cache the computed patterns on the
# user doc with a 15-minute TTL, invalidated on every journey_log /
# journey_reflection write. Dismissals are read separately so they don't
# touch the cache.
PATTERNS_CACHE_TTL_SECONDS = 15 * 60


async def _cached_detect_wellness_patterns(user_id: str) -> list[dict]:
    """Return the user's detected patterns, using a 15-min cache on the
    user doc when fresh. Falls through to a full recompute + cache-write
    on miss. Any exception during the cache read or write path is
    swallowed and we return the freshly-computed patterns — cache is a
    perf optimisation, never a correctness dependency."""
    now = datetime.now(timezone.utc)
    try:
        doc = await db.users.find_one({"id": user_id}, {"patterns_cache": 1}) or {}
        cached = doc.get("patterns_cache") or None
        if cached and isinstance(cached, dict):
            expires_at = cached.get("expires_at")
            if isinstance(expires_at, datetime):
                # BSON datetimes come back tz-naive; treat them as UTC so
                # the comparison against a tz-aware `now` doesn't raise.
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at > now:
                    patterns = cached.get("patterns")
                    if isinstance(patterns, list):
                        return patterns
    except Exception as exc:  # noqa: BLE001 — cache read never blocks
        logger.warning("[patterns_cache] read failed for %s: %s", user_id, exc)

    patterns = await _detect_wellness_patterns(user_id)
    try:
        await db.users.update_one(
            {"id": user_id},
            {"$set": {"patterns_cache": {
                "patterns": patterns,
                "computed_at": now,
                "expires_at": now + timedelta(seconds=PATTERNS_CACHE_TTL_SECONDS),
            }}},
        )
    except Exception as exc:  # noqa: BLE001 — cache write never blocks
        logger.warning("[patterns_cache] write failed for %s: %s", user_id, exc)
    return patterns


async def _invalidate_patterns_cache(user_id: str) -> None:
    """Drop the patterns cache for a user. Called from any endpoint that
    materially changes journey rows (log write, reflection attach) so the
    next /me/patterns read recomputes fresh."""
    try:
        await db.users.update_one(
            {"id": user_id}, {"$unset": {"patterns_cache": ""}}
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[patterns_cache] invalidate failed for %s: %s", user_id, exc)


@api.get("/me/patterns")
async def patterns_list(user: dict = Depends(get_current_user)):
    """Return the user's currently-detected patterns, plus their dismissal
    list. The client sorts / picks which chip to show. Uses the 15-min
    patterns_cache to keep the greeting chip snappy on mobile."""
    patterns = await _cached_detect_wellness_patterns(user["id"])
    doc = await db.users.find_one({"id": user["id"]}, {"dismissed_patterns": 1})
    dismissed = list((doc or {}).get("dismissed_patterns") or [])
    return {"patterns": patterns, "dismissed": dismissed}


@api.post("/me/patterns/{pattern_key:path}/dismiss")
async def pattern_dismiss(pattern_key: str, user: dict = Depends(get_current_user)):
    """Mark a pattern key as dismissed for this user. Idempotent — repeats
    are a no-op. `path` converter is used because keys contain ':' and '@'.
    """
    if not pattern_key or len(pattern_key) > 120:
        raise HTTPException(status_code=400, detail="Invalid pattern key")
    await db.users.update_one(
        {"id": user["id"]},
        {"$addToSet": {"dismissed_patterns": pattern_key}},
    )
    return {"ok": True, "dismissed": pattern_key}


@api.post("/me/patterns/clear")
async def patterns_clear(user: dict = Depends(get_current_user)):
    """Clear all dismissals so all currently-active patterns can re-surface."""
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"dismissed_patterns": []}},
    )
    return {"ok": True}


# --- Assistant settings (Phase 8) ---------------------------------------------
class AssistantSettingsIn(BaseModel):
    """Editable Wellness Assistant preferences. Kept minimal — extend here
    as new toggles arrive. `None` on a field means "no change" so the
    frontend can PATCH-style update a single toggle without echoing state
    it doesn't own."""
    harmonic_influence_enabled: Optional[bool] = None


@api.get("/me/settings")
async def get_settings(user: dict = Depends(get_current_user)):
    """Return the user's Wellness Assistant settings, filled with defaults."""
    return await _user_settings(user["id"])


@api.post("/me/settings")
async def update_settings(
    body: AssistantSettingsIn,
    user: dict = Depends(get_current_user),
):
    """Update one or more settings fields. Only fields the caller sent
    are written — omitted fields leave prior values intact."""
    payload = body.model_dump(exclude_none=True)
    if payload:
        update = {f"assistant_settings.{k}": v for k, v in payload.items()}
        await db.users.update_one({"id": user["id"]}, {"$set": update})
    return await _user_settings(user["id"])


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
        # Generic message for the client; full detail is in server logs.
        # Reference id lets support look up the exact trace without
        # leaking internal exception shapes.
        raise HTTPException(
            status_code=502,
            detail=f"Checkout failed. Please try again in a moment (ref {rid}).",
        )


async def _create_checkout_impl(body: CheckoutIn, request: Request, user: dict, rid: str):
    if body.plan not in ("monthly", "annual"):
        raise HTTPException(status_code=400, detail="Invalid plan")
    if not STRIPE_API_KEY:
        # Log the operator diagnostic; give the client a generic message.
        logger.error("[checkout] STRIPE_API_KEY missing in backend .env")
        raise HTTPException(status_code=503, detail="Payments are temporarily unavailable.")

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

    # Optional promo-code application. Discount codes: create a Stripe coupon
    # on-the-fly and attach via `discounts`. Referral codes: tag the user and
    # log the redemption. Comp codes should never reach this endpoint — they
    # short-circuit via /promo/redeem.
    promo_discounts = None
    promo_doc = None
    if body.promo_code:
        promo_doc = await db.promo_codes.find_one({"code": body.promo_code.strip().upper()})
        ok, reason = _promo_active_now(promo_doc)
        if not ok:
            raise HTTPException(status_code=400, detail=reason)
        if promo_doc["type"] == "comp":
            raise HTTPException(status_code=400, detail="Complimentary codes are redeemed directly, not at checkout.")
        if promo_doc["type"] == "discount":
            # Enforce plan restriction (monthly-only / annual-only / both).
            applies = promo_doc.get("applies_to", "both")
            if applies != "both" and applies != body.plan:
                raise HTTPException(status_code=400, detail=f"This code only applies to the {applies} plan.")
            metadata["promo_code"] = promo_doc["code"]
            try:
                coupon = await _stripe_call(
                    _stripe.Coupon.create,
                    percent_off=int(promo_doc["percent_off"]),
                    duration="once",
                    name=f"Solarisound promo {promo_doc['code']}",
                )
                promo_discounts = [{"coupon": coupon.id}]
            except Exception as e:
                logger.warning("[checkout] Stripe coupon.create failed for promo=%s: %s", promo_doc.get("code"), e)
                raise HTTPException(status_code=502, detail="Could not apply that discount code right now — please try again.")
        elif promo_doc["type"] == "referral":
            metadata["promo_code"] = promo_doc["code"]
            metadata["referral_rep"] = promo_doc.get("rep_name") or ""

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

        # Build kwargs so we only pass `discounts` when we have a real coupon.
        # Stripe rejects an empty `discounts=[]` list.
        session_kwargs = dict(
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
        )
        if promo_discounts:
            session_kwargs["discounts"] = promo_discounts
        else:
            # Only allow Stripe-managed promotion codes when we're NOT already
            # applying our own coupon (Stripe rejects both flags together).
            session_kwargs["allow_promotion_codes"] = True

        session = await _stripe_call(
            _stripe.checkout.Session.create,
            **session_kwargs,
        )
        # Log our-side redemption tally so the admin's usage counter updates.
        # We do this optimistically at checkout-creation time (matches our
        # existing metrics behaviour on `/me/checkout`). Webhook-driven double-
        # count is prevented by keying on the session_id in metadata.
        if promo_doc:
            log_entry = {
                "user_id": user["id"],
                "user_email": user.get("email"),
                "user_name": user.get("name") or user.get("email"),
                "plan": body.plan,
                "redeemed_at": datetime.now(timezone.utc).isoformat(),
                "stripe_session_id": getattr(session, "id", None),
            }
            await db.promo_codes.update_one(
                {"code": promo_doc["code"]},
                {"$inc": {"redemptions": 1}, "$push": {"redemption_log": log_entry}},
            )
            if promo_doc["type"] == "referral":
                await db.users.update_one(
                    {"id": user["id"]},
                    {"$set": {"referral_code": promo_doc["code"], "referral_rep": promo_doc.get("rep_name")}},
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[checkout] Stripe call failed for user=%s plan=%s", user.get("email"), body.plan)
        # Do NOT echo the Stripe error text or config hints to the client —
        # both leak information (API key status, enabled currencies, etc.).
        # Operator guidance stays in the log line above.
        raise HTTPException(
            status_code=502,
            detail="Checkout is temporarily unavailable. Please try again in a moment.",
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
async def admin_security(
    days: Optional[int] = None,
    user: dict = Depends(get_current_user),
):
    """Live security counters for the admin dashboard tile.
    Each counter exposes `last_hour` / `last_24h` / `last_7d` totals plus the
    most recent audit events. Counters are in-memory (reset on restart);
    audit-log events are persisted in MongoDB.

    Query params:
        days — optional (7 | 30 | 90). When present, the `recent_events`
               feed is scoped to events with `ts >= now - days` and the
               limit is expanded (up to 300 items) so the admin can page
               through a longer history. Omit or pass an invalid value to
               get the default 12-item most-recent snapshot.
    """
    _require_admin(user)
    metrics = {name: _metric_summary(name) for name in _METRIC_BUCKETS.keys()}
    # Recent-events feed. Default snapshot = 12 items (backwards compatible).
    # When `days` is supplied and one of the accepted values, filter by ts
    # and lift the cap so the feed becomes browsable history.
    recent_query: dict = {}
    recent_limit = 12
    if days in (7, 30, 90):
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        recent_query = {"ts": {"$gte": cutoff.isoformat()}}
        recent_limit = 300
    recent_cursor = db.audit_log.find(recent_query, {"_id": 0}).sort("ts", -1).limit(recent_limit)
    recent = await recent_cursor.to_list(recent_limit)
    # Pending registration count for the "new user notification" badge:
    # how many users registered in the last 24h that the admin hasn't acknowledged.
    one_day_ago = datetime.now(timezone.utc) - timedelta(days=1)
    new_users_24h = await db.users.count_documents({
        "created_at": {"$gte": one_day_ago.isoformat()},
    })
    return {
        "metrics": metrics,
        "recent_events": recent,
        "recent_window_days": days if days in (7, 30, 90) else None,
        "recent_returned": len(recent),
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


# =========================================================================
# Promo Codes
# =========================================================================
# Three code types (comp / discount / referral) share one Mongo collection.
# The `type` field decides which sub-flow runs on redemption. See the class
# `PromoCreateIn` docstring for field-by-field semantics.
# =========================================================================

def _promo_public(doc: dict) -> dict:
    """Strip Mongo internals + return an API-safe dict for the admin list."""
    if not doc:
        return {}
    doc = {k: v for k, v in doc.items() if k != "_id"}
    return doc


def _promo_active_now(doc: dict) -> tuple[bool, str]:
    """Return (is_valid, reason) — reason is a user-facing error string if
    the code cannot be redeemed right now."""
    if not doc:
        return False, "Code not found."
    if not doc.get("active", True):
        return False, "This code has been deactivated."
    exp = doc.get("expires_at")
    if exp:
        try:
            if datetime.fromisoformat(exp.replace("Z", "+00:00")) < datetime.now(timezone.utc):
                return False, "This code has expired."
        except ValueError:
            pass
    max_uses = doc.get("max_uses")
    if max_uses is not None and doc.get("redemptions", 0) >= max_uses:
        return False, "This code has reached its redemption limit."
    return True, ""


def _promo_summary(doc: dict) -> str:
    """Human-friendly one-liner describing what the code unlocks."""
    t = doc.get("type")
    if t == "comp":
        d = doc.get("duration_days", 0)
        return f"Complimentary Pro access for {d} day{'s' if d != 1 else ''}"
    if t == "discount":
        p = doc.get("percent_off", 0)
        target = doc.get("applies_to", "both")
        scope = {"monthly": "monthly", "annual": "annual", "both": "monthly or annual"}.get(target, "Pro")
        return f"{p}% off the {scope} plan"
    if t == "referral":
        return f"Referral tracking — {doc.get('rep_name') or 'partner'}"
    return "Promo code"


@api.post("/admin/promo")
async def admin_create_promo(body: PromoCreateIn, user: dict = Depends(get_current_user)):
    _require_admin(user)
    code = body.code.strip().upper()
    existing = await db.promo_codes.find_one({"code": code})
    if existing:
        raise HTTPException(status_code=409, detail="A promo code with this name already exists.")
    # Type-specific validation
    if body.type == "comp" and not body.duration_days:
        raise HTTPException(status_code=400, detail="duration_days is required for Complimentary Access codes.")
    if body.type == "discount" and (not body.percent_off or not body.applies_to):
        raise HTTPException(status_code=400, detail="percent_off and applies_to are required for Discount codes.")
    if body.type == "referral" and not body.rep_name:
        raise HTTPException(status_code=400, detail="rep_name is required for Referral codes.")

    doc = {
        "code": code,
        "type": body.type,
        "active": bool(body.active),
        "expires_at": body.expires_at,
        "max_uses": body.max_uses,
        "duration_days": body.duration_days,
        "percent_off": body.percent_off,
        "applies_to": body.applies_to,
        "rep_name": body.rep_name,
        "rep_email": body.rep_email,
        "redemptions": 0,
        "redemption_log": [],  # list of {user_id, user_email, user_name, plan, redeemed_at}
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": user.get("email"),
    }
    await db.promo_codes.insert_one(doc)
    await _audit("promo_created", None, user_email=user.get("email"), metadata={"code": code, "type": body.type})
    return _promo_public(doc)


@api.get("/admin/promo")
async def admin_list_promo(user: dict = Depends(get_current_user)):
    _require_admin(user)
    cursor = db.promo_codes.find({}, {"_id": 0}).sort("created_at", -1).limit(500)
    return await cursor.to_list(500)


@api.patch("/admin/promo/{code}")
async def admin_update_promo(code: str, body: PromoUpdateIn, user: dict = Depends(get_current_user)):
    _require_admin(user)
    updates = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to update.")
    res = await db.promo_codes.update_one({"code": code.upper()}, {"$set": updates})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Code not found.")
    await _audit("promo_updated", None, user_email=user.get("email"), metadata={"code": code.upper(), "updates": updates})
    doc = await db.promo_codes.find_one({"code": code.upper()}, {"_id": 0})
    return doc


@api.delete("/admin/promo/{code}")
async def admin_delete_promo(code: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    res = await db.promo_codes.delete_one({"code": code.upper()})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Code not found.")
    await _audit("promo_deleted", None, user_email=user.get("email"), metadata={"code": code.upper()})
    return {"ok": True}


@api.post("/promo/validate")
async def promo_validate(body: PromoValidateIn, user: dict = Depends(get_current_user)):
    """User-facing: check whether a promo code is redeemable right now and
    return a friendly summary of what it unlocks. Does NOT mutate anything —
    the actual redemption happens via `/promo/redeem` (comp/referral) or
    `/me/checkout` with `promo_code` (discount)."""
    code = body.code.strip().upper()
    doc = await db.promo_codes.find_one({"code": code})
    ok, reason = _promo_active_now(doc)
    if not ok:
        return {"valid": False, "reason": reason}
    return {
        "valid": True,
        "type": doc["type"],
        "summary": _promo_summary(doc),
        "duration_days": doc.get("duration_days"),
        "percent_off": doc.get("percent_off"),
        "applies_to": doc.get("applies_to"),
    }


@api.post("/promo/redeem")
async def promo_redeem(body: PromoRedeemIn, user: dict = Depends(get_current_user)):
    """User-facing: redeem a Complimentary or Referral code. Discount codes
    are NOT redeemed here — they're applied at checkout via `/me/checkout`.

    Comp codes grant Pro instantly for `duration_days`. Referral codes
    attach the rep to the user's record + append to the redemption log
    without changing subscription state.
    """
    code = body.code.strip().upper()
    doc = await db.promo_codes.find_one({"code": code})
    ok, reason = _promo_active_now(doc)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
    if doc["type"] == "discount":
        raise HTTPException(status_code=400, detail="Discount codes are applied at checkout, not redeemed here.")

    # Guard against double-redemption per user (comp only — referral can be
    # re-tagged if the user restarted their signup flow). We use an ATOMIC
    # conditional update so two concurrent taps can't both pass the check
    # and stack the entitlement. The update only succeeds when the
    # redemption_log does NOT already contain this user_id.
    log_entry = {
        "user_id": user["id"],
        "user_email": user.get("email"),
        "user_name": user.get("name") or user.get("email"),
        "plan": "comp_pro" if doc["type"] == "comp" else "referral_signup",
        "redeemed_at": datetime.now(timezone.utc).isoformat(),
    }
    if doc["type"] == "comp":
        result = await db.promo_codes.update_one(
            {"code": code, "redemption_log.user_id": {"$ne": user["id"]}},
            {"$inc": {"redemptions": 1}, "$push": {"redemption_log": log_entry}},
        )
        if result.modified_count == 0:
            # Either the code no longer exists (unlikely — we just read it)
            # or the user is already in redemption_log. Either way, refuse.
            raise HTTPException(status_code=400, detail="You've already redeemed this code.")
    else:
        # Referral code — atomic append is fine (idempotent-ish; log entry
        # duplication is OK because we don't grant entitlement here).
        await db.promo_codes.update_one(
            {"code": code},
            {"$inc": {"redemptions": 1}, "$push": {"redemption_log": log_entry}},
        )

    if doc["type"] == "comp":
        # Grant Pro for duration_days. Writes to the canonical `pro_until`
        # field that `_is_pro()` reads — this is the SAME entitlement path as
        # Stripe subscription grants and admin manual grants, so every
        # Pro-only feature (Brainwave & Specials, Soundscapes, Sleep Mode,
        # Smart Fade, Haptics, Wellness Prescriptions, Sound Bath, Meditation
        # Sounds, Flow Custom Builder, saved-session load, etc.) unlocks
        # automatically once this write completes.
        days = int(doc.get("duration_days") or 30)
        now = datetime.now(timezone.utc)
        # If the user already has a pro_until in the future (e.g. paid Pro
        # active OR a prior comp code still running), extend from that point
        # instead of overwriting so codes can stack additively. Otherwise
        # start from `now`.
        current_until = None
        try:
            cu = user.get("pro_until") or (await db.users.find_one({"id": user["id"]}, {"pro_until": 1}) or {}).get("pro_until")
            if cu:
                current_until = datetime.fromisoformat(cu)
        except Exception:
            current_until = None
        base = current_until if (current_until and current_until > now) else now
        new_until = (base + timedelta(days=days)).isoformat()
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {
                "pro_until": new_until,
                "pro_source": f"promo:{code}",
                "plan": "pro",
            }},
        )
        await _audit("promo_comp_redeemed", None, user_id=user["id"], user_email=user.get("email"), metadata={"code": code, "days": days, "pro_until": new_until})
        return {"ok": True, "unlocked": "pro_comp", "duration_days": days, "pro_until": new_until}

    # Referral
    await db.users.update_one({"id": user["id"]}, {"$set": {"referral_code": code, "referral_rep": doc.get("rep_name")}})
    await _audit("promo_referral_tagged", None, user_id=user["id"], user_email=user.get("email"), metadata={"code": code})
    return {"ok": True, "unlocked": "referral", "rep_name": doc.get("rep_name")}


@api.post("/admin/promo/migrate-legacy")
async def admin_migrate_legacy_promo(user: dict = Depends(get_current_user)):
    """One-shot cleanup: promote any user with legacy `pro_expires_at` (from
    the pre-iter41 comp-redeem path) to the canonical `pro_until` field so
    downstream code that only reads `pro_until` picks them up too. Idempotent
    — running it twice is a no-op. Reports how many records were touched.
    """
    _require_admin(user)
    cursor = db.users.find(
        {"pro_expires_at": {"$exists": True, "$ne": None}, "pro_until": {"$in": [None, ""]}},
        {"_id": 0, "id": 1, "email": 1, "pro_expires_at": 1, "pro_source": 1},
    )
    stale = await cursor.to_list(500)
    migrated = 0
    for u in stale:
        exp = u.get("pro_expires_at")
        if not exp:
            continue
        updates = {"pro_until": exp, "plan": "pro"}
        if not u.get("pro_source"):
            updates["pro_source"] = "promo:legacy"
        await db.users.update_one({"id": u["id"]}, {"$set": updates})
        migrated += 1
    await _audit(
        "promo_legacy_migrated", None,
        user_email=user.get("email"),
        metadata={"count": migrated, "scanned": len(stale)},
    )
    return {"ok": True, "migrated": migrated, "scanned": len(stale)}



app.include_router(api)

# SECURITY: never combine wildcard origin with credentials. When CORS_ORIGINS
# is a comma-separated allowlist we set `allow_origins` directly. When it's
# the explicit wildcard "*" (used for Emergent-managed deployments where the
# production host is dynamic) we switch to `allow_origin_regex=".*"` — this
# makes starlette echo the request Origin verbatim so `allow_credentials`
# stays compatible (the browser refuses credentials when the server responds
# with the literal `Access-Control-Allow-Origin: *`).
_raw_origins = os.environ.get("CORS_ORIGINS", "").strip()
_use_wildcard = _raw_origins == "*"
origins = [] if _use_wildcard else [o.strip() for o in _raw_origins.split(",") if o.strip() and o.strip() != "*"]
if not origins and not _use_wildcard:
    # Fail-soft default: use a SAFE allowlist (the production domain + the
    # preview host) rather than crashing the server. Logged loudly so the
    # operator knows to set CORS_ORIGINS explicitly.
    origins = [
        "https://solarisound.com",
        "https://www.solarisound.com",
        "http://localhost:3000",
    ]
    logging.getLogger(__name__).warning(
        "CORS_ORIGINS not set — falling back to safe default allowlist: %s",
        origins,
    )
_cors_kwargs = {
    "allow_credentials": True,
    "allow_methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    "allow_headers": ["Authorization", "Content-Type", "X-Requested-With"],
    "max_age": 600,
}
if _use_wildcard:
    _cors_kwargs["allow_origin_regex"] = ".*"
    logging.getLogger(__name__).info(
        "CORS_ORIGINS='*' — reflecting request Origin via allow_origin_regex",
    )
else:
    _cors_kwargs["allow_origins"] = origins
app.add_middleware(CORSMiddleware, **_cors_kwargs)

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
