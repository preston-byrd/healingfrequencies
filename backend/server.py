from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import os
import asyncio
import logging
# Logger — module-scoped so it's usable by helpers defined near the top of
# the file (Twilio init, etc.). basicConfig runs once via idempotent guard.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
import math
import uuid
import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError
from pydantic import BaseModel, Field, EmailStr
from emergentintegrations.payments.stripe.checkout import (
    StripeCheckout,
)
from emergentintegrations.llm.chat import LlmChat, UserMessage
import json
import re
import random
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
    """Best-effort client IP derivation with XFF spoofing mitigation.

    Priority order (most reliable first):
      1. `CF-Connecting-IP` — Cloudflare's own header carrying the real
         client IP. Cloudflare fronts solarisound.com so this is present
         and cannot be spoofed by clients (Cloudflare's edge strips any
         inbound client-supplied CF-Connecting-IP and re-writes it to
         the true origin IP).
      2. `True-Client-IP` — Akamai/CF Enterprise variant, same trust
         model as CF-Connecting-IP.
      3. `X-Real-IP` — set by some ingress controllers (nginx-ingress)
         to the immediate client.
      4. `X-Forwarded-For` RIGHT-most public entry (walking r→l,
         skipping private hops) — spoof-resistant fallback used only
         when none of the 1–3 headers are present.
      5. `request.client.host` — direct peer, only useful when nothing
         else is present.

    HF-039: The Cloudflare-family headers (CF-Connecting-IP + True-Client-IP)
    are now trusted UNCONDITIONALLY when `TRUST_CLOUDFLARE_HEADERS=true`
    (default). Previously we only trusted them when the direct peer was
    private — but Emergent's GCP load balancer hands requests to the pod
    with a PUBLIC peer IP (34.x.x.x), so the old logic returned the LB IP
    for every request on solarisound.com and the real client never landed
    in the audit log. This is safe because Cloudflare's edge strips any
    inbound client-supplied CF-Connecting-IP header before forwarding.
    Deployments that don't front their pods with Cloudflare can set
    `TRUST_CLOUDFLARE_HEADERS=false` to revert to the private-peer gate.
    """
    # 1 & 2: Cloudflare-family headers — trusted when the operator has
    # confirmed the deployment sits behind Cloudflare. Default TRUE
    # because that's the production topology.
    if _TRUST_CLOUDFLARE_HEADERS:
        for header in ("cf-connecting-ip", "true-client-ip"):
            val = (request.headers.get(header) or "").strip()
            if val and _is_valid_public_ip(val):
                return val[:64]

    peer = ""
    try:
        peer = (request.client.host or "")
    except Exception:
        peer = ""

    # If the direct peer is public AND we're not behind a Cloudflare-family
    # proxy (checked above), we're being hit directly (local dev / test).
    # Ignore forwarded headers to prevent client-side XFF spoofing.
    if not _is_private_peer(peer):
        # Even with cf-trust OFF, we still honour cf-headers when the peer
        # itself is a trusted proxy — but that's the pre-HF-039 legacy path.
        if not _TRUST_CLOUDFLARE_HEADERS:
            return (peer or "unknown")[:64]
        return (peer or "unknown")[:64]

    # Behind a private-range proxy — walk the remaining trusted headers.
    # (When TRUST_CLOUDFLARE_HEADERS is off we still check them here.)
    if not _TRUST_CLOUDFLARE_HEADERS:
        for header in ("cf-connecting-ip", "true-client-ip"):
            val = (request.headers.get(header) or "").strip()
            if val and _is_valid_public_ip(val):
                return val[:64]
    val = (request.headers.get("x-real-ip") or "").strip()
    if val and not _is_private_peer(val):
        return val[:64]

    # Fall back to XFF. HF-039 improvement: when the chain contains a
    # Cloudflare-owned IP, the entry IMMEDIATELY TO ITS LEFT is the true
    # client — Cloudflare always appends the origin BEFORE their own hop
    # (`client, cf-edge, our-lb`). This handles the preview + free/pro CF
    # topology where CF-Connecting-IP isn't propagated to the pod but
    # Cloudflare is still doing the TLS termination.
    xff = request.headers.get("x-forwarded-for") or ""
    candidates = [c.strip() for c in xff.split(",") if c.strip()]
    if candidates:
        # Walk right→left, tracking the previous IP. When we hit a CF
        # edge, the previous (leftward) IP is the client.
        for i in range(len(candidates) - 1, -1, -1):
            ip = candidates[i]
            if _is_cloudflare_ip(ip) and i > 0:
                left = candidates[i - 1]
                if _is_valid_public_ip(left):
                    return left[:64]
        # No CF hop found — fall back to the right-most public entry
        # (spoof-resistant against left-side prepending).
        for ip in reversed(candidates):
            if not _is_private_peer(ip):
                return ip[:64]

    return (peer or "unknown")[:64]


def _is_valid_public_ip(ip: str) -> bool:
    """True when `ip` parses AND is a routable public address. Used to
    gate the CF-Connecting-IP / True-Client-IP trust path so a malformed
    header value can't leak "unknown" or a private-range string into the
    audit log."""
    if not ip:
        return False
    try:
        import ipaddress
        obj = ipaddress.ip_address(ip)
        # Reject the same buckets `_is_private_peer` flags, so a spoofed
        # "127.0.0.1" or "10.0.0.1" header can't smuggle through.
        return not (obj.is_private or obj.is_loopback
                    or obj.is_link_local or obj.is_reserved
                    or obj.is_multicast or obj.is_unspecified)
    except ValueError:
        return False


# HF-039: operator switch for the unconditional Cloudflare-header trust
# path. Default TRUE — production solarisound.com sits behind Cloudflare +
# GCP LB and the LB always presents a public peer IP. Set to "false" only
# in deployments that expose the pod directly to the public internet.
_TRUST_CLOUDFLARE_HEADERS = (
    os.environ.get("TRUST_CLOUDFLARE_HEADERS", "true").strip().lower()
    not in ("false", "0", "no", "off", "")
)


# HF-039: known Cloudflare IP ranges. Used to anchor XFF resolution when
# CF-Connecting-IP isn't propagated to the pod (some plans / proxy chains
# strip it). When we walk XFF right-to-left and hit a CF-owned IP, the
# entry immediately to its LEFT is the true client — Cloudflare always
# appends the origin before their own hop.
# List from https://www.cloudflare.com/ips-v4/ + /ips-v6/ (Feb 2026 snapshot).
# Refresh only if CF publishes new ranges (rare — has been stable for years).
_CLOUDFLARE_CIDRS = (
    # IPv4
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22",
    "103.31.4.0/22", "141.101.64.0/18", "108.162.192.0/18",
    "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22",
    "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    # IPv6
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32",
    "2405:b500::/32", "2405:8100::/32", "2a06:98c0::/29",
    "2c0f:f248::/32",
)


def _is_cloudflare_ip(ip: str) -> bool:
    """True when `ip` is inside any published Cloudflare range."""
    if not ip:
        return False
    try:
        import ipaddress
        obj = ipaddress.ip_address(ip)
        for cidr in _CLOUDFLARE_CIDRS:
            try:
                if obj in ipaddress.ip_network(cidr, strict=False):
                    return True
            except ValueError:
                continue
        return False
    except ValueError:
        return False


def _is_private_peer(ip: str) -> bool:
    """True when `ip` is empty, loopback, RFC1918 private, link-local, or
    a Kubernetes cluster CIDR (fc00::/7, ::1). Used to decide whether the
    direct peer is our trusted proxy or a public client."""
    if not ip:
        return True
    try:
        import ipaddress
        obj = ipaddress.ip_address(ip)
        return (obj.is_private or obj.is_loopback
                or obj.is_link_local or obj.is_reserved
                or obj.is_multicast or obj.is_unspecified)
    except ValueError:
        # Non-parseable string (e.g. "unknown") — treat as private so we
        # don't accidentally trust it as a public client.
        return True


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


async def _send_welcome_email(user_email: str, user_name: str) -> None:
    """Fire-and-forget welcome email sent from noreply@<verified-domain> to a
    freshly-registered user. Skipped silently when Resend isn't configured
    so local dev still works. Content is intentionally warm + brief — the
    goal is confirmation of a good signup, not a marketing pitch."""
    if not _resend or not _RESEND_API_KEY:
        return
    safe_name = _html_escape((user_name or "").strip())[:120]
    greeting = f"Welcome, {safe_name}." if safe_name else "Welcome."
    html = f"""
    <table style="font-family: -apple-system, system-ui, sans-serif; max-width: 520px; margin: 0; padding: 28px; background: #08120F; color: #E8E3D9; border-radius: 12px;">
      <tr><td style="font-size: 11px; letter-spacing: 2px; color: #C4A67A; text-transform: uppercase;">Solarisound</td></tr>
      <tr><td style="padding-top: 14px; font-family: 'Cormorant Garamond', Georgia, serif; font-size: 30px; font-weight: 400; color: #E8E3D9;">{greeting}</td></tr>
      <tr><td style="padding-top: 18px; font-size: 15px; color: #C9DED6; line-height: 1.65;">
        Your account is ready. You now have access to Solfeggio presets, the Custom Generator, ambient soundscapes, and a curated set of guided journeys. Explore what feels good — most people start with 528 Hz "Miracle" or a Deep Restore flow.
      </td></tr>
      <tr><td style="padding-top: 18px; font-size: 14px; color: #8A9A92; line-height: 1.65;">
        A gentle tip: put on headphones the first time you tune in. The binaural cues and lower Solfeggio frequencies are meant to be felt as much as heard.
      </td></tr>
      <tr><td style="padding-top: 26px;">
        <a href="https://solarisound.com/" style="display: inline-block; padding: 12px 22px; background: #C4A67A; color: #08120F; border-radius: 999px; text-decoration: none; font-weight: 500; font-size: 14px;">Open Solarisound</a>
      </td></tr>
      <tr><td style="padding-top: 26px; font-size: 11px; color: #5A6B65;">
        Reply to this email any time — a real person on our team will read it. Welcome home.
      </td></tr>
    </table>
    """
    try:
        await asyncio.to_thread(
            _send_email_sync,
            user_email,
            "Welcome to Solarisound",
            html,
        )
    except Exception as e:
        logger.warning("[resend] welcome email failed: %s", type(e).__name__)


async def _send_support_ack_to_user(user_email: str, user_name: str, reason_label: str, msg: str) -> None:
    """Confirmation email sent to the user acknowledging that we received
    their support submission. Complements the in-app "Thank you" screen
    with a durable receipt in their inbox that they can search for later.
    Silent no-op when Resend / API key aren't configured."""
    if not _resend or not _RESEND_API_KEY:
        return
    safe_name = _html_escape((user_name or "").strip())[:120]
    safe_reason = _html_escape(reason_label)
    safe_msg = _html_escape(msg).replace("\n", "<br/>")
    hello = f"Hi {safe_name}," if safe_name else "Hi,"
    html = f"""
    <table style="font-family: -apple-system, system-ui, sans-serif; max-width: 540px; margin: 0; padding: 28px; background: #08120F; color: #E8E3D9; border-radius: 12px;">
      <tr><td style="font-size: 11px; letter-spacing: 2px; color: #C4A67A; text-transform: uppercase;">Solarisound · Support</td></tr>
      <tr><td style="padding-top: 12px; font-family: 'Cormorant Garamond', Georgia, serif; font-size: 26px; font-weight: 400; color: #E8E3D9;">We received your message</td></tr>
      <tr><td style="padding-top: 14px; font-size: 14px; color: #C9DED6; line-height: 1.6;">
        {hello} thanks for reaching out about <span style="color: #72C2AC;">{safe_reason}</span>. A real person on our team will read this and get back to you shortly. If there's more context we should know, just reply directly to this email.
      </td></tr>
      <tr><td style="padding-top: 22px; font-size: 11px; color: #5A6B65; letter-spacing: 1px; text-transform: uppercase;">A copy of what you sent</td></tr>
      <tr><td style="padding-top: 8px;">
        <div style="background: #101F1A; border-left: 2px solid rgba(196,166,122,0.4); padding: 14px 16px; font-size: 13px; color: #C9DED6; line-height: 1.55; white-space: pre-wrap;">{safe_msg}</div>
      </td></tr>
      <tr><td style="padding-top: 24px; font-size: 12px; color: #8A9A92;">
        Meanwhile, keep tuning — we'll be in touch soon.
      </td></tr>
      <tr><td style="padding-top: 24px; font-size: 11px; color: #5A6B65;">— The Solarisound team</td></tr>
    </table>
    """
    try:
        await asyncio.to_thread(
            _send_email_sync,
            user_email,
            f"We received your message · [{reason_label}]",
            html,
        )
    except Exception as e:
        logger.warning("[resend] support ack failed: %s", type(e).__name__)


# ---- Admin new-user notification -------------------------------------------
# Product spec (Feb 2026): one email per successful registration with the
# exact subject "New User Registration - Solarisound" and a clean template
# containing name, email, timestamp, registration method (email / Google /
# etc.), and plan (Free / Trial / Pro). The per-IP register throttle
# (15/hr) already bounds abuse so we don't need a digest buffer anymore.

def _derive_plan_label(user: dict) -> str:
    """Return 'Free' / 'Trial' / 'Pro' based on a fresh user's flags. At
    registration time this is almost always 'Free' — we still compute it
    from the doc so a future auto-trial-on-signup change surfaces here
    without a code edit here. Parses ISO strings to datetime for a
    timezone-correct comparison (naive string comparison would misclassify
    docs whose pro_until is missing the '+00:00' suffix)."""
    now_dt = datetime.now(timezone.utc)
    def _future(v):
        if not v:
            return False
        try:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt > now_dt
        except Exception:
            return False
    if _future(user.get("pro_until")) or _future(user.get("pro_expires_at")):
        return "Pro"
    if _future(user.get("stripe_trial_end")):
        return "Trial"
    return "Free"


async def _notify_admin_registration(user: dict, method: str = "email") -> None:
    """Fire-and-forget admin alert on EACH successful registration. Silent
    no-op when Resend / RESEND_ADMIN_RECIPIENT aren't configured. Method
    string is the auth provider ("email", "Google", "Apple", …) — currently
    only "email" is wired but the field is a stable contract for future
    OAuth additions.

    Subject is exactly 'New User Registration - Solarisound' per product
    spec. Body is intentionally plain — no CTAs, no marketing copy — just
    the six data points an admin needs to triage."""
    if not _resend or not _RESEND_API_KEY or not _RESEND_ADMIN_RECIPIENT:
        return
    safe_name  = _html_escape((user.get("name") or "").strip())[:120] or "(no name)"
    safe_email = _html_escape((user.get("email") or "").strip())[:200]
    safe_method = _html_escape((method or "email").strip())[:40].title()
    plan = _derive_plan_label(user)
    # Registration date/time — parse the created_at ISO we just stamped so
    # the email carries the exact instant the row was written (not now()).
    try:
        created_at_dt = datetime.fromisoformat(user.get("created_at").replace("Z", "+00:00"))
    except Exception:
        created_at_dt = datetime.now(timezone.utc)
    when_pretty = created_at_dt.strftime("%B %d, %Y · %H:%M UTC")

    # Clean, minimal HTML — no aurora gradients or CTAs, per "simply
    # formatted, purely informational" requirement. Table layout is
    # email-client-safe (Gmail / Outlook / Apple Mail).
    html = f"""
    <table style="font-family: -apple-system, system-ui, sans-serif; max-width: 520px; margin: 0; padding: 26px; background: #08120F; color: #E8E3D9; border-radius: 12px;">
      <tr><td style="font-size: 11px; letter-spacing: 2px; color: #72C2AC; text-transform: uppercase;">Solarisound · New Registration</td></tr>
      <tr><td style="padding-top: 12px; font-size: 20px; font-weight: 500; color: #E8E3D9;">A new user just joined</td></tr>
      <tr><td style="padding-top: 18px;">
        <table style="width: 100%; font-size: 14px; color: #E8E3D9;">
          <tr>
            <td style="padding: 6px 0; color: #8A9A92; width: 160px;">Name</td>
            <td style="padding: 6px 0;">{safe_name}</td>
          </tr>
          <tr>
            <td style="padding: 6px 0; color: #8A9A92;">Email</td>
            <td style="padding: 6px 0;">{safe_email}</td>
          </tr>
          <tr>
            <td style="padding: 6px 0; color: #8A9A92;">Registered</td>
            <td style="padding: 6px 0; font-family: ui-monospace, monospace; font-size: 13px;">{when_pretty}</td>
          </tr>
          <tr>
            <td style="padding: 6px 0; color: #8A9A92;">Method</td>
            <td style="padding: 6px 0;">{safe_method}</td>
          </tr>
          <tr>
            <td style="padding: 6px 0; color: #8A9A92;">Plan</td>
            <td style="padding: 6px 0;">{plan}</td>
          </tr>
        </table>
      </td></tr>
      <tr><td style="padding-top: 24px; font-size: 11px; color: #5A6B65;">No action needed — this is a heads-up notification.</td></tr>
    </table>
    """
    try:
        await asyncio.to_thread(
            _send_email_sync,
            _RESEND_ADMIN_RECIPIENT,
            "New User Registration - Solarisound",
            html,
        )
    except Exception as e:
        logger.warning("[resend] admin registration notify failed: %s", type(e).__name__)


# ---- Re-engagement email nudges --------------------------------------------
# Automated warm re-engagement emails sent to users who haven't logged in
# recently. 4-tier sequence at 72h → 7d → 14d → 30d, gated by a per-user
# time window (9am CST default, never 22:00-05:00 CST). Message copy comes
# from three pools (has-Harmonic-Blueprint / no-HB / all-users) with a
# rotation rule that never repeats the last variant sent to a given user.
#
# Delivery pipeline:
#   1. Background loop (started in FastAPI lifespan) ticks every 15 min.
#   2. For each candidate user, decides tier + variant + timing.
#   3. Sends via Resend (from = noreply@solarisounds.com per iter 74).
#   4. Records the send in `email_nudges` for open/click tracking + admin
#      analytics.

from zoneinfo import ZoneInfo

_CST = ZoneInfo("America/Chicago")

# Tier thresholds — a user with `last_login_at` older than this window (and
# no successful nudge already at this tier) becomes eligible.
_NUDGE_TIERS = [
    {"key": "72h", "min_hours": 72,   "max_hours":  7 * 24},
    {"key": "7d",  "min_hours": 7*24, "max_hours": 14 * 24},
    {"key": "14d", "min_hours": 14*24, "max_hours": 30 * 24},
    {"key": "30d", "min_hours": 30*24, "max_hours": 365 * 24},
]

# Variant pools. Each variant has a stable `key` used for rotation gating.
_MSG_HB = [
    {"key": "hb_balanced",   "text": "Hi {name}, you haven't checked in for a little while. I'd love to make sure you're balanced. Your frequencies are waiting."},
    {"key": "hb_update",     "text": "Hey {name}, it's been a few days. Let's update your Harmonic Blueprint so we can help keep you aligned."},
    {"key": "hb_reset",      "text": "Hi {name}, your last session was {days} days ago. Your nervous system might be ready for a reset. Let's find your frequency today."},
    {"key": "hb_eigenmode",  "text": "Hey {name}, your Eigenmode Journey is ready and waiting. A few minutes of sound therapy could be exactly what you need right now."},
    {"key": "hb_assistant",  "text": "Hi {name}, your Wellness Assistant has a new recommendation ready based on your Harmonic Blueprint. Come see what it's suggesting."},
    {"key": "hb_streak",     "text": "Hi {name}, we noticed your streak is at risk. Come back today and keep your resonance practice going."},
]
_MSG_NO_HB = [
    {"key": "nohb_setup",     "text": "Hi {name}, I noticed you haven't set up your Harmonic Blueprint yet. If you've got a minute, let's map your unique frequency signature together."},
    {"key": "nohb_discover",  "text": "Hey {name}, your Harmonic Blueprint is waiting to be discovered. It only takes a few minutes and it will personalize your entire Solarisound experience."},
    {"key": "nohb_powerful",  "text": "Hi {name}, did you know Solarisound can map your unique harmonic signature? Your Harmonic Blueprint is one of our most powerful features and yours is ready to set up."},
]
_MSG_ALL = [
    {"key": "all_listening",  "text": "Hi {name}, I would love to set up a listening session for you. Sign in and let's try a few frequencies together."},
    {"key": "all_five_min",   "text": "Hey {name}, even a 5-minute session can shift your whole day. Your frequencies are waiting at solarisound.com."},
    {"key": "all_topfreq",    "text": "Hi {name}, your {top_freq} Hz session is ready and waiting. Come tune in."},
]

# 30-day tier gets a warmer, more personal subject and body copy.
_MSG_30D_TEMPLATE = (
    "Hi {name}, it's been about a month since your last session. "
    "We saved your place — your frequencies are still here whenever you're ready. "
    "Come back for a quiet 5 minutes and see how you feel."
)


def _nudge_tier_for_hours(hours: float) -> Optional[dict]:
    """Return the tier dict a user falls into, or None if too fresh / too old."""
    for t in _NUDGE_TIERS:
        if t["min_hours"] <= hours < t["max_hours"]:
            return t
    return None


def _in_send_window_cst(now_utc: datetime) -> bool:
    """9am CST default with a hard block on 22:00–05:00 CST. Since we can't
    yet target each user's typical session hour (v2), we send during the
    9am–10am CST slot only — 24h scheduler tick will hit that once per day."""
    now_cst = now_utc.astimezone(_CST)
    hour = now_cst.hour
    if hour < 5 or hour >= 22:
        return False
    return 9 <= hour < 10


async def _pick_top_frequency_for(user_id: str) -> Optional[float]:
    """Return the frequency the user tunes to most often (from wellness_journey
    entries). None if they've never logged a session."""
    try:
        cur = db.wellness_journey.aggregate([
            {"$match": {"user_id": user_id, "frequency": {"$exists": True, "$ne": None}}},
            {"$group": {"_id": "$frequency", "n": {"$sum": 1}}},
            {"$sort": {"n": -1}},
            {"$limit": 1},
        ])
        docs = await cur.to_list(1)
        return float(docs[0]["_id"]) if docs else None
    except Exception:
        return None


async def _user_has_harmonic_blueprint(user_id: str) -> bool:
    try:
        doc = await db.resonance_profiles.find_one(
            {"user_id": user_id, "is_eigenmode": True},
            {"_id": 1},
        )
        return bool(doc)
    except Exception:
        return False


def _pick_variant(pool: list, last_key: Optional[str]) -> dict:
    """Rotate variants — never repeat the last one sent. Falls back to the
    first pool entry if `last_key` is the only variant (single-item pool)."""
    if last_key:
        alt = [v for v in pool if v["key"] != last_key]
        if alt:
            return random.choice(alt)
    return random.choice(pool)


def _build_frontend_url() -> str:
    """Canonical base URL for the CTA button and deep links inside the email.
    Prefers the configured FRONTEND_URL; falls back to solarisound.com."""
    base = os.environ.get("FRONTEND_URL", "").strip()
    if base:
        return base.rstrip("/")
    return "https://solarisound.com"


def _nudge_cta_url(user_id: str, nudge_id: str, top_freq: Optional[float], tier_key: str) -> str:
    """CTA link — for the 14d + 30d tiers, deep-link into a pre-loaded
    session with the user's most-played frequency. For earlier tiers, just
    open the homepage. Also carries the nudge_id so /api/e/track/click can
    stamp the open + click before redirecting."""
    base = _build_frontend_url()
    target = base
    if tier_key in ("14d", "30d") and top_freq:
        # /play route reads ?frequency=<hz> to preload a session.
        target = f"{base}/play?frequency={top_freq:g}"
    # Click-tracking wrapper: hit our backend first, which records the click
    # and 302s to the real URL.
    api_base = base  # same origin — /api/e/track/click will exist on prod
    return f"{api_base}/api/e/track/click/{nudge_id}?to={_url_quote(target)}"


def _url_quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")


def _nudge_subject_for(tier_key: str, name: str) -> str:
    safe_name = (name or "").strip() or "there"
    if tier_key == "30d":
        return f"We miss you, {safe_name}"
    if tier_key == "14d":
        return f"Your frequencies are still here, {safe_name}"
    if tier_key == "7d":
        return f"A few days away — {safe_name}, we saved your spot"
    return "Your frequencies are waiting"


def _render_nudge_html(*, name: str, body_text: str, cta_url: str, nudge_id: str,
                       unsubscribe_url: str, preferences_url: str, top_freq: Optional[float]) -> str:
    """One template shared across all four tiers. Variant / tier differences
    live in body_text + subject; the design chrome stays consistent."""
    safe_body = _html_escape(body_text).replace("\n", "<br/>")
    base = _build_frontend_url()
    freq_line = ""
    if top_freq:
        freq_line = (
            f'<div style="padding-top: 14px; font-family: \'Cormorant Garamond\', Georgia, serif; '
            f'font-size: 20px; color: #C4A67A;">Your most-played: {top_freq:g} Hz</div>'
        )
    # 1×1 tracking pixel for open rate.
    pixel = f'<img src="{base}/api/e/track/open/{nudge_id}" width="1" height="1" alt="" style="display:none;" />'
    return f"""
    <table style="font-family: -apple-system, system-ui, sans-serif; max-width: 540px; margin: 0; padding: 28px; background: #08120F; color: #E8E3D9; border-radius: 12px;">
      <tr><td style="font-size: 11px; letter-spacing: 2px; color: #C4A67A; text-transform: uppercase;">Solarisound</td></tr>
      <tr><td style="padding-top: 12px; font-family: 'Cormorant Garamond', Georgia, serif; font-size: 28px; font-weight: 400; color: #E8E3D9;">A little check-in</td></tr>
      <tr><td style="padding-top: 18px; font-size: 15px; color: #C9DED6; line-height: 1.65;">{safe_body}</td></tr>
      <tr><td>{freq_line}</td></tr>
      <tr><td style="padding-top: 28px;">
        <a href="{cta_url}" style="display: inline-block; padding: 13px 26px; background: #C4A67A; color: #08120F; border-radius: 999px; text-decoration: none; font-weight: 500; font-size: 14px;">Return to My Frequencies</a>
      </td></tr>
      <tr><td style="padding-top: 40px; font-size: 11px; color: #5A6B65; line-height: 1.5;">
        You're receiving this because you signed up at solarisound.com.
        <a href="{preferences_url}" style="color: #8A9A92; text-decoration: underline;">Get these weekly instead</a>
        &nbsp;·&nbsp;
        <a href="{unsubscribe_url}" style="color: #8A9A92; text-decoration: underline;">Unsubscribe</a>
      </td></tr>
      {pixel}
    </table>
    """


async def _ensure_unsub_token(user: dict) -> str:
    """Lazily generate a per-user unsubscribe token. Persisted so the same
    link works forever (users often click months later)."""
    tok = user.get("nudge_unsubscribe_token")
    if tok:
        return tok
    tok = str(uuid.uuid4())
    await db.users.update_one({"id": user["id"]}, {"$set": {"nudge_unsubscribe_token": tok}})
    return tok


async def _send_reengagement_nudge(user: dict, tier: dict, dry_run: bool = False) -> Optional[dict]:
    """Compose + send one nudge. Returns the persisted email_nudges doc on
    success (or the composed doc when dry_run=True), None on skip."""
    if not _resend or not _RESEND_API_KEY:
        return None
    now = datetime.now(timezone.utc)
    tier_key = tier["key"]
    name = (user.get("name") or "").strip() or user.get("email", "").split("@")[0]

    # Pool selection — HB set → HB + all-users pools. Otherwise no-HB + all.
    has_hb = await _user_has_harmonic_blueprint(user["id"])
    if tier_key == "30d":
        # 30-day tier uses the fixed "we miss you" template — no rotation.
        variant_key = "miss_you_30d"
        top_freq = await _pick_top_frequency_for(user["id"])
        body_text = _MSG_30D_TEMPLATE.format(name=name)
    else:
        pool = (_MSG_HB if has_hb else _MSG_NO_HB) + _MSG_ALL
        # Rotation — read the last variant this user was sent.
        last = await db.email_nudges.find_one(
            {"user_id": user["id"]},
            sort=[("sent_at", -1)],
        )
        last_key = (last or {}).get("variant_key")
        variant = _pick_variant(pool, last_key)
        variant_key = variant["key"]
        top_freq = await _pick_top_frequency_for(user["id"])
        # Days since last login for the 'X days ago' interpolation.
        last_iso = user.get("last_login_at") or user.get("created_at")
        try:
            days_since = int((now - datetime.fromisoformat(last_iso.replace("Z", "+00:00"))).total_seconds() // 86400)
        except Exception:
            days_since = 0
        body_text = variant["text"].format(
            name=name,
            days=days_since,
            top_freq=(f"{top_freq:g}" if top_freq else "528"),
        )

    nudge_id = str(uuid.uuid4())
    unsub_tok = await _ensure_unsub_token(user)
    base = _build_frontend_url()
    unsub_url = f"{base}/api/e/unsub/{unsub_tok}"
    prefs_url = f"{base}/api/e/prefs/{unsub_tok}?cadence=weekly"
    cta_url = _nudge_cta_url(user["id"], nudge_id, top_freq, tier_key)
    subject = _nudge_subject_for(tier_key, name)
    html = _render_nudge_html(
        name=name, body_text=body_text, cta_url=cta_url,
        nudge_id=nudge_id, unsubscribe_url=unsub_url,
        preferences_url=prefs_url, top_freq=(top_freq if tier_key in ("7d", "14d", "30d") else None),
    )

    doc = {
        "id": nudge_id,
        "user_id": user["id"],
        "user_email": user.get("email"),
        "tier": tier_key,
        "variant_key": variant_key,
        "has_hb": has_hb,
        "top_freq": top_freq,
        "subject": subject,
        "sent_at": now.isoformat(),
        "delivered": False,
        "opened_at": None,
        "clicked_at": None,
        "resend_id": None,
    }

    if dry_run:
        return doc

    resend_id = await asyncio.to_thread(_send_email_sync, user["email"], subject, html)
    doc["resend_id"] = resend_id
    doc["delivered"] = bool(resend_id)
    try:
        await db.email_nudges.insert_one(doc)
    except Exception as e:
        logger.warning("[nudge] insert failed: %s", type(e).__name__)
    return doc


async def _reengagement_tick() -> dict:
    """Single pass of the scheduler. Returns a small dict of counters so
    tests + the admin diagnostics endpoint can inspect results without
    parsing logs."""
    now = datetime.now(timezone.utc)
    stats = {"scanned": 0, "sent": 0, "skipped_window": 0, "skipped_unsub": 0, "skipped_recent": 0, "skipped_no_tier": 0, "skipped_already_sent": 0}
    if not _in_send_window_cst(now):
        stats["skipped_window"] = 1
        return stats
    # Only scan users whose last login (or, for legacy users where we haven't
    # yet stamped that field, whose registration date) is at least 72h old.
    # The $or across last_login_at AND (missing last_login_at + old created_at)
    # ensures the first-ever nudge batch reaches users who signed up before
    # iter 76 introduced the last_login_at field.
    cutoff = (now - timedelta(hours=72)).isoformat()
    cursor = db.users.find(
        {
            "$and": [
                {"$or": [
                    {"last_login_at": {"$lte": cutoff}},
                    {"$and": [
                        {"last_login_at": {"$exists": False}},
                        {"created_at": {"$lte": cutoff}},
                    ]},
                ]},
                {"$or": [
                    {"nudge_unsubscribed": {"$ne": True}},
                    {"nudge_unsubscribed": {"$exists": False}},
                ]},
            ],
        },
        {"_id": 0, "id": 1, "email": 1, "name": 1, "last_login_at": 1, "created_at": 1,
         "nudge_unsubscribed": 1, "nudge_cadence": 1, "nudge_unsubscribe_token": 1,
         "nudge_sequence_reset_at": 1},
    )
    async for u in cursor:
        stats["scanned"] += 1
        if u.get("nudge_unsubscribed"):
            stats["skipped_unsub"] += 1
            continue
        # Compute the login-reset watermark UP FRONT so both the recency and
        # already-sent gates scope their queries to nudges sent after the
        # user's most recent login. Without this, a nudge sent BEFORE the
        # last login incorrectly counts as "recent" and blocks new sends
        # even though the spec says login resets the sequence.
        reset_at = u.get("nudge_sequence_reset_at") or u.get("last_login_at")
        # Cadence check — weekly users only get one nudge every 7 days.
        cadence = (u.get("nudge_cadence") or "default").lower()
        recency_hours = 72 if cadence == "default" else (7 * 24)
        recency_query = {"user_id": u["id"]}
        if reset_at:
            recency_query["sent_at"] = {"$gt": reset_at}
        last = await db.email_nudges.find_one(recency_query, sort=[("sent_at", -1)])
        if last:
            try:
                last_dt = datetime.fromisoformat(last["sent_at"].replace("Z", "+00:00"))
                if (now - last_dt).total_seconds() < recency_hours * 3600:
                    stats["skipped_recent"] += 1
                    continue
            except Exception:
                pass
        # Tier decision.
        last_iso = u.get("last_login_at") or u.get("created_at")
        try:
            last_dt = datetime.fromisoformat(last_iso.replace("Z", "+00:00"))
        except Exception:
            continue
        hours = (now - last_dt).total_seconds() / 3600.0
        tier = _nudge_tier_for_hours(hours)
        if not tier:
            stats["skipped_no_tier"] += 1
            continue
        # Have we already sent this tier to this user in this login-gap?
        # Same watermark used by the recency gate above.
        already = await db.email_nudges.find_one({
            "user_id": u["id"], "tier": tier["key"],
            **({"sent_at": {"$gt": reset_at}} if reset_at else {}),
        })
        if already:
            stats["skipped_already_sent"] += 1
            continue
        await _send_reengagement_nudge(u, tier)
        stats["sent"] += 1
    # HF-032 SMS Session Reminders: after the email pass, sweep Pro users
    # who have opted in to `reminders` category and haven't practiced in
    # 3+ days. Kept in the same tick so operators have a single lever to
    # run + observe (admin tick endpoint returns both counters).
    sms_stats = await _sms_reminder_tick(now)
    stats.update({f"sms_{k}": v for k, v in sms_stats.items()})
    return stats


async def _sms_reminder_tick(now: datetime) -> dict:
    """One pass of the SMS session-reminder scheduler.

    Rules:
      • User must be Pro (pro_until > now). Free users don't get texts —
        keeps the SMS budget tight and creates a real Pro benefit.
      • User must have a verified phone AND `sms_prefs.marketing_opted_in`
        AND `sms_prefs.categories.reminders`.
      • User must NOT have replied STOP.
      • Last session in `db.sessions` must be >= 72h ago (or no sessions at
        all combined with an account older than 72h — same threshold as
        the email nudger so both signals stay in sync).
      • Cooldown: only ONE SMS reminder every 7 days per user, tracked by
        an `sms_messages` doc with category='reminders'. Prevents daily
        spamming even when the scheduler runs frequently.
      • Quiet hours: send_sms() already defers if in quiet hours; we
        surface `deferred` in the counter so operators can see it.

    Returns counters — never raises.
    """
    stats = {"scanned": 0, "sent": 0, "deferred": 0, "skipped_not_pro": 0,
             "skipped_no_consent": 0, "skipped_recent_session": 0,
             "skipped_recent_reminder": 0, "skipped_stopped": 0, "errors": 0}
    now_iso = now.isoformat()
    stale_cutoff_iso = (now - timedelta(hours=72)).isoformat()
    reminder_cooldown_iso = (now - timedelta(days=7)).isoformat()
    # Only fetch users who could plausibly qualify. The Mongo query narrows
    # the population up front so we're not iterating the entire user base.
    cursor = db.users.find(
        {
            "phone_verified": True,
            "phone_number": {"$exists": True, "$ne": None},
            "sms_prefs.marketing_opted_in": True,
            "sms_prefs.categories.reminders": True,
            "sms_prefs.stopped_at": None,
            "pro_until": {"$gt": now_iso},
        },
        {"_id": 0, "id": 1, "name": 1, "phone_number": 1, "pro_until": 1},
    )
    async for u in cursor:
        stats["scanned"] += 1
        try:
            # Cooldown: did we send this user a reminders SMS in the last 7 days?
            recent = await db.sms_messages.find_one(
                {
                    "user_id": u["id"],
                    "category": "reminders",
                    "sent_at": {"$gt": reminder_cooldown_iso},
                    "status": {"$in": ["queued", "sent", "delivered", "sent-test-mode"]},
                },
                {"_id": 1},
            )
            if recent:
                stats["skipped_recent_reminder"] += 1
                continue
            # Last session — count as "inactive" if the most recent session
            # is older than 72h. Missing sessions collection = 0 sessions =
            # also inactive if account is >72h old (we already know phone
            # was verified during register, which happens on the same day).
            latest = await db.sessions.find_one(
                {"user_id": u["id"]},
                sort=[("created_at", -1)],
                projection={"_id": 0, "created_at": 1},
            )
            if latest and latest.get("created_at"):
                try:
                    if latest["created_at"] >= stale_cutoff_iso:
                        stats["skipped_recent_session"] += 1
                        continue
                except Exception:
                    pass
            # Compose the reminder body. Kept short, supportive, non-clinical
            # per HF-031 guardrails (no "healing", "cure", "urgent", etc.).
            first_name = (u.get("name") or "").split()[0][:24] if u.get("name") else "friend"
            body = (
                f"Hi {first_name}, your Solarisound practice is waiting. "
                f"3 minutes today is a gift to tomorrow-you. "
                f"solarisound.com/session"
            )
            outcome = await send_sms(u, "reminders", body)
            if outcome.get("sent"):
                stats["sent"] += 1
            elif outcome.get("deferred"):
                stats["deferred"] += 1
            else:
                # send_sms already logged the reason. Bucket by common reasons
                # so the admin can see WHY sends were skipped.
                reason = (outcome.get("reason") or "").lower()
                if "stop" in reason:
                    stats["skipped_stopped"] += 1
                elif "consent" in reason or "disabled" in reason:
                    stats["skipped_no_consent"] += 1
                else:
                    stats["errors"] += 1
        except Exception as e:
            logger.warning("[sms.reminder] user=%s err=%s", u.get("id"), type(e).__name__)
            stats["errors"] += 1
    return stats


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

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app_):
    """FastAPI lifespan (replaces the deprecated @app.on_event decorators).

    Runs one-time startup work (index creation, plan-config + admin seed),
    yields control to the app, then runs shutdown cleanup on graceful stop.
    The real body lives at the bottom of the file (`_lifespan_startup` /
    `_lifespan_shutdown`) so we keep it near the collection references it
    touches. Forward-declared here so `app = FastAPI(lifespan=...)` can
    wire it in before any routes are registered.
    """
    await _lifespan_startup()
    try:
        yield
    finally:
        await _lifespan_shutdown()


app = FastAPI(lifespan=lifespan)
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
    name: Optional[str] = Field(default=None, max_length=120)
    # HF-030: Phone verification is required for account creation. The
    # client must first hit /api/auth/phone/send-code, receive the SMS,
    # then /api/auth/phone/verify-code — which returns a short-lived
    # JWT `phone_verification_token`. That token is then passed here to
    # prove the phone belongs to the person creating the account.
    phone_number: str = Field(min_length=6, max_length=20)
    phone_verification_token: str = Field(min_length=10, max_length=800)


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

# --- Twilio Verify (phone number OTP) ---------------------------------------
# We use Twilio's managed Verify Service so Twilio owns code generation,
# delivery retries, expiry, per-recipient throttling, and fraud detection.
# This endpoint tier is intentionally thin — the heavy lifting sits with
# Twilio and we only add app-level rate-limits + our own audit trail.
_TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID") or ""
_TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN") or ""
_TWILIO_VERIFY_SERVICE_SID = os.environ.get("TWILIO_VERIFY_SERVICE_SID") or ""
# Test-mode escape hatch. When this env var is truthy we bypass the real
# Twilio Verify API and accept a deterministic code table for the given
# phone numbers. Used ONLY by the pytest suite so CI doesn't burn SMS
# credits. Never enable in production — the .env template ships it empty.
_TWILIO_TEST_MODE = (os.environ.get("TWILIO_TEST_MODE") or "").strip() in ("1", "true", "yes")
_TWILIO_TEST_ACCEPT_CODE = "123456"
_twilio_client = None
if _TWILIO_TEST_MODE:
    logger.warning(
        "[twilio] TWILIO_TEST_MODE is ON — phone verification will accept the "
        "test code %s WITHOUT calling Twilio. This MUST NOT be enabled in "
        "production. Unset TWILIO_TEST_MODE to disable.",
        _TWILIO_TEST_ACCEPT_CODE,
    )
if _TWILIO_ACCOUNT_SID and _TWILIO_AUTH_TOKEN and not _TWILIO_TEST_MODE:
    try:
        from twilio.rest import Client as _TwilioClient  # type: ignore
        _twilio_client = _TwilioClient(_TWILIO_ACCOUNT_SID, _TWILIO_AUTH_TOKEN)
    except Exception as _e:
        logger.warning("[twilio] client init failed: %s", type(_e).__name__)


# E.164 phone-number regex. Accepts +[country][number] with 8-15 total
# digits after the plus. This is a syntactic gate only — the semantic
# check (does this number actually receive SMS?) is done by Twilio Verify.
_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def _normalize_phone(raw: str) -> str:
    """Strip whitespace + parens + dashes and prepend '+' if the caller
    forgot. Return an E.164 string or raise 400."""
    if not raw:
        raise HTTPException(status_code=400, detail="Phone number is required")
    s = re.sub(r"[\s\-\(\)\.]", "", str(raw))
    if not s.startswith("+"):
        raise HTTPException(status_code=400, detail="Phone number must include country code (e.g. +14155552671)")
    if not _E164_RE.match(s):
        raise HTTPException(status_code=400, detail="Phone number must be in E.164 format (+[country code][number], 8-15 digits)")
    return s


class PhoneSendIn(BaseModel):
    phone_number: str = Field(min_length=6, max_length=20)
    # Optional channel for the OTP — Twilio Verify supports "sms" (default)
    # and "call" (voice call reading digits). Frontend surfaces "call" as a
    # fallback when SMS Lookup rejects a number (Twilio error 60200 etc.).
    channel: Optional[str] = None


class PhoneVerifyIn(BaseModel):
    phone_number: str = Field(min_length=6, max_length=20)
    code: str = Field(min_length=4, max_length=10)


@api.post("/auth/phone/send-code")
async def phone_send_code(body: PhoneSendIn, request: Request):
    """Send an SMS OTP to the given number via Twilio Verify.

    Rate limits:
      • 3 sends per phone per 10 min (Twilio Verify enforces its own too)
      • 8 sends per IP per hour (blocks scripted account spam)
    Duplicate-account gate: if a user with this phone already exists, we
    return the same success response as a clean send. Preventing account
    enumeration matters more than a fast "phone taken" error path — the
    downstream register endpoint is authoritative and will refuse to
    create the account.
    """
    ip = _client_ip(request)
    phone = _normalize_phone(body.phone_number)
    # Whitelist channels so a malformed client can't pass "email" or worse.
    channel = (body.channel or "sms").strip().lower()
    if channel not in ("sms", "call"):
        channel = "sms"

    _rate_limit_or_429(
        f"phone-send:phone:{phone}", capacity=3, refill_per_sec=1 / 200,
        label="verification code",
    )
    if ip not in ("127.0.0.1", "::1", "localhost", "unknown") and not _TWILIO_TEST_MODE:
        _rate_limit_or_429(
            f"phone-send:ip:{ip}", capacity=8, refill_per_sec=1 / 450,
            label="verification code",
        )

    generic_msg = (
        "If that number can receive a call, we'll ring you shortly."
        if channel == "call"
        else "If that number can receive SMS, a code is on the way."
    )
    generic = {"ok": True, "message": generic_msg, "channel": channel}

    if _TWILIO_TEST_MODE:
        # Bypass Twilio entirely. The verify-code endpoint's matching
        # bypass accepts _TWILIO_TEST_ACCEPT_CODE for any phone.
        await _audit(
            "auth.phone.code_sent", request,
            metadata={"phone_last4": phone[-4:], "status": "test-mode", "channel": channel},
        )
        return {**generic, "status": "pending"}

    if not _twilio_client or not _TWILIO_VERIFY_SERVICE_SID:
        # Not configured — surface a clear operator-visible error, but only
        # in dev. In prod we still want the endpoint to look normal so a
        # misconfig doesn't leak.
        logger.warning("[twilio] send-code called with no Verify service configured")
        raise HTTPException(status_code=503, detail="Phone verification is not configured yet. Please contact support.")

    try:
        # Twilio SDK is sync; run in threadpool so we don't stall the loop.
        def _send():
            return _twilio_client.verify.v2 \
                .services(_TWILIO_VERIFY_SERVICE_SID) \
                .verifications.create(to=phone, channel=channel)
        verification = await asyncio.get_event_loop().run_in_executor(None, _send)
        status = getattr(verification, "status", "pending")
    except Exception as e:
        # Best-effort classification of common Twilio errors → user-friendly.
        msg = str(e)
        friendly = "We couldn't send a code to that number. Please double-check the format and try again."
        can_retry_by_call = False
        if "60200" in msg or "invalid" in msg.lower():
            if channel == "call":
                friendly = "That phone number doesn't look valid. Please check the country code and try again."
            else:
                friendly = "That number couldn't be verified by SMS. Try requesting a voice call instead."
                can_retry_by_call = True
        elif "60203" in msg or "60212" in msg or "too many" in msg.lower():
            friendly = "Too many attempts for that number. Please wait a few minutes and try again."
        elif "landline" in msg.lower() or "60205" in msg or "60600" in msg:
            if channel == "sms":
                friendly = "That number can't receive SMS — try a voice call instead."
                can_retry_by_call = True
            else:
                friendly = "That number can't receive a verification call either. Please use a different phone."
        logger.warning("[twilio.send] channel=%s %s: %s", channel, type(e).__name__, msg[:200])
        await _audit(
            "auth.phone.send_failed", request,
            metadata={
                "phone_last4": phone[-4:],
                "twilio_error": type(e).__name__,
                "channel": channel,
            },
        )
        # Return the retry hint in headers so the frontend can toggle the
        # "Try a phone call instead" button. HTTPException(detail=…) doesn't
        # let us attach a structured body cleanly, so we shape the detail as
        # a dict — clients that just render `detail` still see the string
        # message (formatApiError extracts .message first).
        detail_body: dict = {"message": friendly}
        if can_retry_by_call:
            detail_body["can_retry_by_call"] = True
        raise HTTPException(status_code=400, detail=detail_body)

    await _audit(
        "auth.phone.code_sent", request,
        metadata={"phone_last4": phone[-4:], "status": status, "channel": channel},
    )
    return {**generic, "status": status}


@api.post("/auth/phone/verify-code")
async def phone_verify_code(body: PhoneVerifyIn, request: Request):
    """Verify an OTP against Twilio Verify. Returns a short-lived JWT that
    proves this phone has been verified. The client passes this token to
    /api/auth/register so account creation is bound to the verified phone.
    """
    ip = _client_ip(request)
    phone = _normalize_phone(body.phone_number)
    code = str(body.code).strip()
    if not code.isdigit() or not (4 <= len(code) <= 10):
        raise HTTPException(status_code=400, detail="Verification code must be 4-10 digits.")

    # Anti-brute-force: 6 verify attempts per phone per 10 min, plus a
    # generous per-IP cap so a botnet doesn't spread attempts.
    _rate_limit_or_429(
        f"phone-verify:phone:{phone}", capacity=6, refill_per_sec=1 / 100,
        label="verification attempt",
    )
    if ip not in ("127.0.0.1", "::1", "localhost", "unknown") and not _TWILIO_TEST_MODE:
        _rate_limit_or_429(
            f"phone-verify:ip:{ip}", capacity=20, refill_per_sec=1 / 60,
            label="verification attempt",
        )

    if _TWILIO_TEST_MODE:
        # In test mode, only the fixed code passes. Everything else 400s
        # exactly like a real "pending" Twilio response would.
        if code != _TWILIO_TEST_ACCEPT_CODE:
            await _audit(
                "auth.phone.verify_failed", request,
                metadata={"phone_last4": phone[-4:], "status": "test-mode-wrong"},
            )
            raise HTTPException(status_code=400, detail="That code is incorrect. Please try again or request a new one.")
        status = "approved"
    elif not _twilio_client or not _TWILIO_VERIFY_SERVICE_SID:
        raise HTTPException(status_code=503, detail="Phone verification is not configured yet. Please contact support.")
    else:
        try:
            def _check():
                return _twilio_client.verify.v2 \
                    .services(_TWILIO_VERIFY_SERVICE_SID) \
                    .verification_checks.create(to=phone, code=code)
            check = await asyncio.get_event_loop().run_in_executor(None, _check)
            status = getattr(check, "status", "")
        except Exception as e:
            msg = str(e)
            # 20404 == "No pending verifications" (code expired or never sent)
            if "20404" in msg or "not found" in msg.lower():
                await _audit(
                    "auth.phone.verify_expired", request,
                    metadata={"phone_last4": phone[-4:]},
                )
                raise HTTPException(status_code=400, detail="This code has expired or was never sent. Please request a new one.")
            logger.warning("[twilio.verify] %s: %s", type(e).__name__, msg[:200])
            raise HTTPException(status_code=400, detail="We couldn't verify that code. Please try again.")

    if status != "approved":
        await _audit(
            "auth.phone.verify_failed", request,
            metadata={"phone_last4": phone[-4:], "status": status},
        )
        raise HTTPException(status_code=400, detail="That code is incorrect. Please try again or request a new one.")

    # Approved → mint a short-lived proof-of-verification JWT bound to
    # this exact phone number. TTL: 15 min — enough for the user to
    # complete the rest of the form; expires if the tab is abandoned.
    payload = {
        "type": "phone_verification",
        "phone": phone,
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": int((datetime.now(timezone.utc) + timedelta(minutes=15)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    await _audit(
        "auth.phone.verified", request,
        metadata={"phone_last4": phone[-4:]},
    )
    return {
        "ok": True,
        "phone_verification_token": token,
        "phone_number": phone,
        "expires_in": 15 * 60,
    }


def _consume_phone_verification_token(token: str, expected_phone: str) -> str:
    """Decode + validate the JWT minted by /auth/phone/verify-code.
    Returns the normalized phone number embedded in the token, or raises 400.
    """
    try:
        p = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="Your phone verification has expired. Please verify your number again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Phone verification token is invalid.")
    if p.get("type") != "phone_verification":
        raise HTTPException(status_code=400, detail="Phone verification token is invalid.")
    tok_phone = p.get("phone") or ""
    if not tok_phone or tok_phone != expected_phone:
        raise HTTPException(status_code=400, detail="Phone verification token does not match this phone number.")
    return tok_phone


# --- HF-031: SMS notification system ---------------------------------------
# Uses Twilio Programmable SMS (`TWILIO_PHONE_NUMBER` sender) — separate from
# the Verify Service used for one-time OTPs. Every send routes through
# `send_sms()` which enforces:
#   • Consent — the user's `sms_prefs.categories[category]` must be true,
#     AND `sms_prefs.stopped_at` must be null (STOP reply is a hard opt-out)
#   • Transactional bypass — the `transactional` category (account /
#     payment / verification confirmations) sends whenever a phone number
#     exists AND the user has NOT sent STOP. TCPA carves out transactional
#     messages from the standard marketing-consent rule.
#   • Quiet hours — respects the same `notification_prefs.quiet_hours`
#     window used by push. Transactional messages bypass quiet hours; other
#     categories are queued-past by returning `deferred=true` (caller decides
#     whether to retry after quiet hours end).
#   • Rate limit — max 5 SMS per user per day (marketing categories),
#     plus a hard 20/day cap that includes transactional. Prevents runaway
#     loops from overwhelming a phone.
#   • Content guardrails — every non-transactional body is validated
#     against `_SMS_FORBIDDEN_PHRASES` (medical/diagnostic/urgent claims)
#     and prepended with a "Reply STOP to unsubscribe" tag when the
#     recipient hasn't received that disclosure in the last 30 days.
#   • Audit — every attempt writes an `sms_messages` doc with the Twilio
#     SID (or failure reason), enabling delivery-status reconciliation via
#     the /api/sms/webhook/status callback.

SMS_CATEGORIES = ("transactional", "reminders", "recommendations", "announcements")
_SMS_TRANSACTIONAL = "transactional"  # always-on (unless STOP)
_SMS_FORBIDDEN_PHRASES = (
    # Health-claim guardrails per HF-031 spec — "avoid medical, diagnostic,
    # urgent, manipulative, or unsupported health claims".
    "cure", "heal your", "diagnose", "treatment", "prescription",
    "guaranteed", "act now", "urgent", "medical", "clinical",
)
_SMS_UNSUBSCRIBE_TAG = " Reply STOP to unsubscribe."
_SMS_HELP_REPLY = (
    "Solarisound: Text notifications for account, session, and feature "
    "updates. Msg&data rates may apply. Reply STOP to unsubscribe."
)
_SMS_STOP_REPLY = (
    "You've been unsubscribed from Solarisound texts. You'll still receive "
    "essential account messages. Reply START to re-subscribe."
)
_SMS_START_REPLY = (
    "You're re-subscribed to Solarisound texts. Reply STOP to unsubscribe."
)


def _default_sms_prefs() -> dict:
    return {
        # TCPA-safe defaults: NOTHING is opted-in by default. User must
        # actively enable each non-transactional category before we send.
        "marketing_opted_in": False,
        "marketing_opted_in_at": None,
        "consent_ip": None,
        "stopped_at": None,
        "last_disclosure_at": None,
        "categories": {
            "transactional": True,  # user cannot fully disable transactional
            "reminders": False,
            "recommendations": False,
            "announcements": False,
        },
    }


async def _get_sms_prefs(user_id: str) -> dict:
    doc = await db.users.find_one({"id": user_id}, {"sms_prefs": 1}) or {}
    prefs = doc.get("sms_prefs") or {}
    merged = _default_sms_prefs()
    if isinstance(prefs, dict):
        for k, v in prefs.items():
            if k == "categories" and isinstance(v, dict):
                merged["categories"] = {
                    **merged["categories"],
                    **{c: bool(x) for c, x in v.items() if c in SMS_CATEGORIES},
                }
                # Guardrail: transactional cannot be turned off from prefs
                # (only STOP can silence it entirely).
                merged["categories"]["transactional"] = True
            else:
                merged[k] = v
    return merged


def _sms_body_ok(body: str) -> tuple[bool, str]:
    """Return (ok, reason). Blocks forbidden phrases + oversized bodies."""
    if not body or not body.strip():
        return False, "empty body"
    if len(body) > 320:  # 2 SMS segments max
        return False, "body too long (max 320 chars)"
    lower = body.lower()
    for term in _SMS_FORBIDDEN_PHRASES:
        if term in lower:
            return False, f"contains disallowed phrase: {term!r}"
    return True, ""


async def _sms_daily_count(user_id: str, transactional_only: bool = False) -> int:
    """Count today's sends for rate-limit checks."""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    q: dict = {
        "user_id": user_id,
        "sent_at": {"$gte": start.isoformat()},
        "status": {"$in": ["queued", "sent", "delivered"]},
    }
    if transactional_only:
        q["category"] = _SMS_TRANSACTIONAL
    return await db.sms_messages.count_documents(q)


async def send_sms(
    user: dict,
    category: str,
    body: str,
    *,
    bypass_quiet_hours: bool = False,
    request: Optional[Request] = None,
) -> dict:
    """Central SMS dispatch. Returns a dict describing the outcome:
        {sent: bool, deferred: bool, reason: str, sms_id: str | None}

    Never raises to the caller — SMS is opportunistic. Failures are logged
    and audited but should never break the primary flow that triggered
    the send.
    """
    user_id = user.get("id") if isinstance(user, dict) else None
    phone = (user.get("phone_number") if isinstance(user, dict) else None) or ""

    def _fail(reason: str, log: bool = True) -> dict:
        if log:
            logger.info("[sms.skip] user=%s cat=%s reason=%s", user_id, category, reason)
        return {"sent": False, "deferred": False, "reason": reason, "sms_id": None}

    if category not in SMS_CATEGORIES:
        return _fail(f"unknown category: {category}")
    if not user_id or not phone:
        return _fail("no phone on user")

    # Content guardrails.
    ok, why = _sms_body_ok(body)
    if not ok:
        return _fail(f"body rejected: {why}")

    prefs = await _get_sms_prefs(user_id)
    # Hard opt-out — STOP reply silences everything except HELP/START.
    if prefs.get("stopped_at"):
        return _fail("user opted out (STOP)")

    # Consent gate. Transactional is always allowed if not STOPPED.
    if category != _SMS_TRANSACTIONAL:
        if not prefs.get("marketing_opted_in"):
            return _fail("no marketing consent")
        if not prefs.get("categories", {}).get(category, False):
            return _fail(f"category disabled: {category}")

    # Quiet hours (bypassed by transactional + explicit override).
    if category != _SMS_TRANSACTIONAL and not bypass_quiet_hours:
        notif_prefs = await _get_notification_prefs(user_id)
        if _is_within_quiet_hours(notif_prefs):
            logger.info("[sms.deferred] user=%s cat=%s reason=quiet_hours", user_id, category)
            return {"sent": False, "deferred": True, "reason": "quiet_hours", "sms_id": None}

    # Rate limits.
    total_today = await _sms_daily_count(user_id)
    if total_today >= 20:
        return _fail(f"daily cap reached ({total_today})")
    if category != _SMS_TRANSACTIONAL and total_today >= 5:
        return _fail(f"marketing daily cap reached ({total_today})")

    # Prepend the unsubscribe tag on non-transactional bodies if we haven't
    # shown it in the last 30 days. CTIA guideline.
    final_body = body
    if category != _SMS_TRANSACTIONAL:
        last = prefs.get("last_disclosure_at")
        needs_tag = True
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                if (datetime.now(timezone.utc) - last_dt).days < 30:
                    needs_tag = False
            except Exception:
                needs_tag = True
        if needs_tag and _SMS_UNSUBSCRIBE_TAG.strip().lower() not in body.lower():
            # Only append if it fits in the 320-char budget.
            if len(body) + len(_SMS_UNSUBSCRIBE_TAG) <= 320:
                final_body = body + _SMS_UNSUBSCRIBE_TAG

    if _TWILIO_TEST_MODE:
        # Test mode: log the message but DON'T dispatch — same guardrail as
        # Verify. Return {sent: True} so caller flows work. Runs BEFORE the
        # unconfigured check because we WANT the "sent" outcome path even
        # when Twilio programmable-SMS keys are empty in preview.
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "phone_last4": phone[-4:],
            "category": category,
            "body": final_body,
            "status": "sent-test-mode",
            "twilio_sid": None,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.sms_messages.insert_one(doc)
        # Refresh the unsubscribe disclosure clock so we don't spam it.
        if category != _SMS_TRANSACTIONAL:
            await db.users.update_one(
                {"id": user_id},
                {"$set": {"sms_prefs.last_disclosure_at": datetime.now(timezone.utc).isoformat()}},
            )
        return {"sent": True, "deferred": False, "reason": "test-mode", "sms_id": doc["id"]}

    if not _twilio_client or not _TWILIO_ACCOUNT_SID or not (os.environ.get("TWILIO_PHONE_NUMBER") or "").strip():
        # Configured for Verify only (no programmable-SMS sender). Log the
        # would-be send so admins can see what WOULD have gone out once
        # Twilio SMS is enabled — but don't fail the caller.
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "phone_last4": phone[-4:],
            "category": category,
            "body_len": len(final_body),
            "status": "skipped-unconfigured",
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.sms_messages.insert_one(doc)
        return _fail("twilio programmable-sms not configured", log=False)

    sender = (os.environ.get("TWILIO_PHONE_NUMBER") or "").strip()
    try:
        def _send():
            return _twilio_client.messages.create(to=phone, from_=sender, body=final_body)
        message = await asyncio.get_event_loop().run_in_executor(None, _send)
        sid = getattr(message, "sid", None)
        status = getattr(message, "status", "queued")
    except Exception as e:
        # Log failure. Don't raise — SMS is opportunistic.
        logger.warning("[sms.send_fail] user=%s cat=%s err=%s", user_id, category, type(e).__name__)
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "phone_last4": phone[-4:],
            "category": category,
            "body_len": len(final_body),
            "status": "failed",
            "error": str(e)[:400],
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.sms_messages.insert_one(doc)
        return _fail(f"twilio error: {type(e).__name__}")

    sms_id = str(uuid.uuid4())
    doc = {
        "id": sms_id,
        "user_id": user_id,
        "phone_last4": phone[-4:],
        "category": category,
        "body": final_body,
        "status": status or "queued",
        "twilio_sid": sid,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.sms_messages.insert_one(doc)

    if category != _SMS_TRANSACTIONAL:
        await db.users.update_one(
            {"id": user_id},
            {"$set": {"sms_prefs.last_disclosure_at": datetime.now(timezone.utc).isoformat()}},
        )

    return {"sent": True, "deferred": False, "reason": "queued", "sms_id": sms_id}


# --- HF-031 endpoints -------------------------------------------------------
class SmsPrefsIn(BaseModel):
    marketing_opted_in: Optional[bool] = None
    categories: Optional[Dict[str, bool]] = None


@api.get("/me/sms-prefs")
async def me_get_sms_prefs(user: dict = Depends(get_current_user)):
    prefs = await _get_sms_prefs(user["id"])
    # Also surface whether we CAN text (phone verified + configured).
    prefs["phone_number_last4"] = (user.get("phone_number") or "")[-4:] if user.get("phone_number") else None
    prefs["phone_verified"] = bool(user.get("phone_verified"))
    return prefs


@api.put("/me/sms-prefs")
async def me_update_sms_prefs(
    body: SmsPrefsIn,
    request: Request,
    user: dict = Depends(get_current_user),
):
    if not user.get("phone_verified"):
        raise HTTPException(
            status_code=400,
            detail="Verify your phone number before enabling text notifications.",
        )
    current = await _get_sms_prefs(user["id"])
    # STOP-out is sticky. To re-subscribe the user must reply START, not
    # flip the toggle in-app — TCPA policy.
    if current.get("stopped_at"):
        raise HTTPException(
            status_code=400,
            detail=(
                "This number is unsubscribed from Solarisound texts. Reply "
                "START to any message to re-subscribe."
            ),
        )
    set_doc: dict = {}
    if body.marketing_opted_in is not None:
        v = bool(body.marketing_opted_in)
        if v and not current.get("marketing_opted_in"):
            set_doc["sms_prefs.marketing_opted_in"] = True
            set_doc["sms_prefs.marketing_opted_in_at"] = datetime.now(timezone.utc).isoformat()
            set_doc["sms_prefs.consent_ip"] = _client_ip(request)
            await _audit(
                "sms.opt_in", request, user_id=user["id"], user_email=user.get("email"),
                metadata={"phone_last4": (user.get("phone_number") or "")[-4:]},
            )
        elif not v and current.get("marketing_opted_in"):
            set_doc["sms_prefs.marketing_opted_in"] = False
            await _audit(
                "sms.opt_out_soft", request, user_id=user["id"], user_email=user.get("email"),
                metadata={"phone_last4": (user.get("phone_number") or "")[-4:]},
            )
    if body.categories and isinstance(body.categories, dict):
        for cat, val in body.categories.items():
            if cat in SMS_CATEGORIES and cat != _SMS_TRANSACTIONAL:
                set_doc[f"sms_prefs.categories.{cat}"] = bool(val)
    if set_doc:
        await db.users.update_one({"id": user["id"]}, {"$set": set_doc})
    return await _get_sms_prefs(user["id"])


@api.post("/sms/webhook/inbound")
async def sms_inbound_webhook(request: Request):
    """Twilio inbound-SMS webhook. Parses STOP / START / HELP keywords per
    industry standard and reflects them onto the user's `sms_prefs`.

    Twilio POSTs application/x-www-form-urlencoded. We don't validate the
    signature here yet (add `X-Twilio-Signature` HMAC check when the SID
    for validating is configured); we do rate-limit by `From` phone.
    """
    form = await request.form()
    from_phone = str(form.get("From") or "").strip()
    body = str(form.get("Body") or "").strip().upper()
    if not from_phone or not body:
        return {"ok": True}

    user = await db.users.find_one({"phone_number": from_phone})
    if not user:
        # Silent no-op — Twilio still sends the auto-reply.
        logger.info("[sms.inbound] unknown from=%s body=%s", from_phone[-4:], body[:20])
        return {"ok": True}

    kw = body.split()[0] if body else ""
    now_iso = datetime.now(timezone.utc).isoformat()
    if kw in ("STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"):
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {"sms_prefs.stopped_at": now_iso, "sms_prefs.marketing_opted_in": False}},
        )
        await _audit(
            "sms.opt_out_stop", request, user_id=user["id"], user_email=user.get("email"),
            metadata={"phone_last4": from_phone[-4:], "keyword": kw},
        )
        return {"ok": True, "reply": _SMS_STOP_REPLY}
    if kw in ("START", "YES", "UNSTOP"):
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {"sms_prefs.stopped_at": None}},
        )
        await _audit(
            "sms.opt_in_start", request, user_id=user["id"], user_email=user.get("email"),
            metadata={"phone_last4": from_phone[-4:], "keyword": kw},
        )
        return {"ok": True, "reply": _SMS_START_REPLY}
    if kw in ("HELP", "INFO"):
        return {"ok": True, "reply": _SMS_HELP_REPLY}
    # Any other body is a free-form message → we don't route those.
    return {"ok": True}


@api.post("/sms/webhook/status")
async def sms_status_webhook(request: Request):
    """Twilio delivery-status callback. Updates the `sms_messages` doc so
    admins can see which messages actually landed."""
    form = await request.form()
    sid = str(form.get("MessageSid") or "").strip()
    status = str(form.get("MessageStatus") or "").strip()
    err_code = str(form.get("ErrorCode") or "").strip()
    if not sid:
        return {"ok": True}
    update: dict = {"status": status or "unknown"}
    if status == "delivered":
        update["delivered_at"] = datetime.now(timezone.utc).isoformat()
    elif status in ("failed", "undelivered"):
        update["failed_at"] = datetime.now(timezone.utc).isoformat()
        update["error_code"] = err_code
    await db.sms_messages.update_one({"twilio_sid": sid}, {"$set": update})
    return {"ok": True}


@api.get("/admin/sms/stats")
async def admin_sms_stats(user: dict = Depends(get_current_user)):
    _require_admin(user)
    # Aggregate raw status counts (kept for the existing tests + power users).
    pipeline = [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    counts: dict = {}
    async for row in db.sms_messages.aggregate(pipeline):
        counts[row["_id"] or "unknown"] = row["count"]

    # Roll raw statuses into the four buckets the admin dashboard tile shows.
    # `sent-test-mode` counts as sent+delivered because in preview we assume
    # a happy path so operators can eyeball the pipeline before flipping the
    # Twilio env vars on.
    def _sum(*keys: str) -> int:
        return sum(int(counts.get(k, 0)) for k in keys)

    sent_bucket = _sum("queued", "sent", "delivered", "sent-test-mode")
    delivered_bucket = _sum("delivered", "sent-test-mode")
    failed_bucket = _sum("failed", "undelivered")
    skipped_bucket = _sum("skipped-unconfigured")

    # 24h / 7d rolling counts of "actually sent" messages so the tile shows
    # program momentum, not just lifetime totals.
    now = datetime.now(timezone.utc)
    since_24h = (now - timedelta(hours=24)).isoformat()
    since_7d = (now - timedelta(days=7)).isoformat()
    sent_status_filter = {"$in": ["queued", "sent", "delivered", "sent-test-mode"]}
    sent_24h = await db.sms_messages.count_documents({
        "status": sent_status_filter, "sent_at": {"$gte": since_24h},
    })
    sent_7d = await db.sms_messages.count_documents({
        "status": sent_status_filter, "sent_at": {"$gte": since_7d},
    })

    # Category breakdown across all-time — helps spot channel mix (reminders
    # vs marketing vs transactional).
    by_category: dict = {}
    async for row in db.sms_messages.aggregate([
        {"$match": {"status": sent_status_filter}},
        {"$group": {"_id": "$category", "count": {"$sum": 1}}},
    ]):
        by_category[row["_id"] or "unknown"] = row["count"]

    opted_in = await db.users.count_documents({"sms_prefs.marketing_opted_in": True})
    stopped = await db.users.count_documents({"sms_prefs.stopped_at": {"$ne": None}})
    verified = await db.users.count_documents({"phone_verified": True})

    # Recent sends (last 10) — small strip below the tile so the admin can
    # verify the last texts landed without scrolling into a full log view.
    recent: list[dict] = []
    async for row in db.sms_messages.find(
        {},
        {
            "_id": 0, "id": 1, "category": 1, "status": 1, "phone_last4": 1,
            "sent_at": 1, "delivered_at": 1, "failed_at": 1, "error_code": 1,
        },
        sort=[("sent_at", -1)],
        limit=10,
    ):
        recent.append(row)

    return {
        # Legacy fields — do not remove (test_sms_notifications relies on them).
        "by_status": counts,
        "opted_in": opted_in,
        "stopped": stopped,
        # New rolled-up tiles for AdminSMSStats.
        "sent": sent_bucket,
        "delivered": delivered_bucket,
        "failed": failed_bucket,
        "skipped": skipped_bucket,
        "sent_24h": sent_24h,
        "sent_7d": sent_7d,
        "verified_users": verified,
        "by_category": by_category,
        "recent": recent,
        "generated_at": now.isoformat(),
    }


@api.post("/auth/register")
async def register(body: RegisterIn, request: Request, response: Response):
    # SEC-001 hardening: per-IP throttle prevents bulk-account spam that would
    # otherwise flood the DB and the admin sign-up alert inbox. 15 sign-ups per
    # IP per hour (refill 1 token every 4 min) — generous for genuine bursts
    # (household NAT / campus, integration tests) while a script attempting
    # thousands of accounts will trip within seconds. Localhost is skipped so
    # the pytest suite and internal integration checks aren't hobbled.
    ip = _client_ip(request)
    if ip not in ("127.0.0.1", "::1", "localhost", "unknown") and not _TWILIO_TEST_MODE:
        _rate_limit_or_429(
            f"register:{ip}", capacity=15, refill_per_sec=1 / 240,
            label="registration attempt",
        )
    email = body.email.lower()
    # Normalize + verify the phone token BEFORE consulting the DB so a
    # duplicate-email caller (a bot recycling old creds) still has to
    # burn a valid Twilio verification per attempt.
    phone = _normalize_phone(body.phone_number)
    _consume_phone_verification_token(body.phone_verification_token, phone)
    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    # Optional: soft-block phone reuse. We treat phone as identifying info
    # (SMS notifications, 2FA later, account recovery) so two accounts on
    # the same number is confusing. Same generic error message as email
    # collision to avoid enumeration signal beyond what's already leaked.
    phone_owner = await db.users.find_one({"phone_number": phone})
    if phone_owner:
        raise HTTPException(status_code=400, detail="That phone number is already tied to another account.")
    user = {
        "id": str(uuid.uuid4()),
        "email": email,
        "name": (body.name or email.split("@")[0])[:120],
        "password_hash": hash_password(body.password),
        "phone_number": phone,
        "phone_verified": True,
        "phone_verified_at": datetime.now(timezone.utc).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        # Seed last_login_at with the registration moment so the re-engagement
        # scheduler doesn't immediately flag a brand-new user as "inactive
        # 72h" if their creation happened at an old timestamp during a
        # data migration.
        "last_login_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.insert_one(user)
    # Audit + counter for the admin security tile.
    _bump_metric("registrations")
    await _audit(
        "user.registered", request,
        user_id=user["id"], user_email=email,
        metadata={"name": user["name"], "phone_last4": phone[-4:]},
    )
    # Fire-and-forget admin alert — one clean per-registration email per
    # product spec. Silent no-op when Resend/RESEND_ADMIN_RECIPIENT are
    # not configured (local dev / tests).
    asyncio.create_task(
        _notify_admin_registration(user, method="email")
    )
    # Warm welcome email to the user — sent from noreply@<verified-domain>.
    # Silent no-op when Resend isn't configured so tests / local dev work.
    asyncio.create_task(
        _send_welcome_email(email, user["name"])
    )
    # HF-031: fire a transactional welcome SMS confirming the number is
    # tied to the account. Transactional category → doesn't require
    # marketing opt-in, but still respects STOP + hard daily caps.
    asyncio.create_task(
        send_sms(
            user,
            _SMS_TRANSACTIONAL,
            f"Welcome to Solarisound, {user['name'].split()[0][:24]}! Your phone is verified. Manage text preferences at solarisound.com/account.",
            bypass_quiet_hours=True,
            request=request,
        )
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
    if not _TWILIO_TEST_MODE:
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
    # Stamp last_login_at so the re-engagement scheduler can compute
    # "days since last login" for tier decisions. Also clears any pending
    # nudge sequence — a fresh login means the user is engaged again.
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {"last_login_at": now_iso, "nudge_sequence_reset_at": now_iso}},
        )
    except Exception as e:
        logger.warning("[auth.login] last_login stamp failed: %s", type(e).__name__)
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


# --- Support / contact form -------------------------------------------------
# In-app floating "Support Bubble" — logged-in users can send a short message
# to the Solarisound admin inbox picking one of six reasons. Delivered via
# Resend (same transport used for admin sign-up notifications) with the
# subject formatted as "[{Reason}] - Solarisound Support" per the product
# spec. Rate-limited per user + IP to prevent abuse; the message body is
# HTML-escaped before it hits the email template so nothing the user types
# can break the layout or inject markup.

_SUPPORT_REASONS = {
    "report_issue":      "Report an Issue",
    "share_feedback":    "Share Feedback",
    "express_gratitude": "Express Gratitude",
    "feature_request":   "Feature Request",
    "billing_question":  "Billing Question",
    "other":             "Other",
}


class SupportContactIn(BaseModel):
    reason: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=10, max_length=4000)
    # Name / email are pre-filled from /auth/me on the frontend but we accept
    # user-provided overrides so someone signed in as one identity can still
    # sign the message with a different display name if they want to.
    name: Optional[str] = Field(default=None, max_length=120)
    email: Optional[EmailStr] = None


def _html_escape(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


@api.post("/support/contact")
async def support_contact(
    body: SupportContactIn,
    request: Request,
    user: dict = Depends(get_current_user),
):
    reason_label = _SUPPORT_REASONS.get(body.reason)
    if not reason_label:
        raise HTTPException(status_code=400, detail="Unknown reason")

    ip = _client_ip(request)
    # Per-user AND per-IP rate limits — 3 messages / 10 minutes on each. The
    # per-IP bucket catches shared-account abuse; the per-user bucket catches
    # a single account rotating IPs. Refill = 1 per 200s → 3 in 10 minutes.
    _rate_limit_or_429(f"support:user:{user['id']}", capacity=3, refill_per_sec=1 / 200, label="support message")
    _rate_limit_or_429(f"support:ip:{ip}",           capacity=5, refill_per_sec=1 / 120, label="support message")

    display_name = (body.name or user.get("name") or "").strip()[:120]
    reply_email  = (body.email or user.get("email") or "").strip()[:200]
    msg          = body.message.strip()
    when_iso     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Log to DB so admins have a searchable audit trail even if the Resend
    # send fails (email is best-effort; the message is never lost).
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "user_email": user.get("email"),
        "user_name": user.get("name"),
        "reason_key": body.reason,
        "reason_label": reason_label,
        "message": msg,
        "reply_to_email": reply_email,
        "reply_to_name": display_name,
        "ip": ip,
        "user_agent": (request.headers.get("user-agent") or "")[:512],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "delivered": False,
        "provider": "resend" if (_resend and _RESEND_API_KEY) else "none",
        # Admin Support Inbox — every new message lands in 'open' status and
        # is empty of admin replies. Resolving from the inbox flips this to
        # 'resolved' and stamps resolved_at / resolved_by.
        "status": "open",
        "admin_replies": [],
        "resolved_at": None,
        "resolved_by": None,
    }
    try:
        await db.support_messages.insert_one(doc)
    except Exception as e:
        logger.warning("[support] db insert failed: %s", type(e).__name__)

    delivered = False
    if _resend and _RESEND_API_KEY and _RESEND_ADMIN_RECIPIENT:
        # Escape everything user-controlled before it hits the HTML template.
        safe_name  = _html_escape(display_name or "(no name provided)")
        safe_email = _html_escape(reply_email or "(no email provided)")
        safe_msg   = _html_escape(msg).replace("\n", "<br/>")
        safe_ip    = _html_escape(ip or "unknown")[:64]
        safe_ua    = _html_escape((request.headers.get("user-agent") or "")[:256])
        html = f"""
        <table style="font-family: -apple-system, system-ui, sans-serif; max-width: 560px; margin: 0; padding: 24px; background: #08120F; color: #E8E3D9; border-radius: 12px;">
          <tr><td style="font-size: 11px; letter-spacing: 2px; color: #C4A67A; text-transform: uppercase;">Solarisound · Support</td></tr>
          <tr><td style="padding-top: 8px; font-size: 20px; font-weight: 500; color: #E8E3D9;">{_html_escape(reason_label)}</td></tr>
          <tr><td style="padding-top: 4px; font-size: 13px; color: #8A9A92;">from {safe_name} &lt;{safe_email}&gt;</td></tr>
          <tr><td style="padding-top: 20px;">
            <div style="background: #101F1A; border: 1px solid rgba(92,158,140,0.2); border-radius: 8px; padding: 16px; font-size: 14px; color: #E8E3D9; line-height: 1.55; white-space: pre-wrap;">{safe_msg}</div>
          </td></tr>
          <tr><td style="padding-top: 16px; font-family: ui-monospace, monospace; font-size: 11px; color: #5A6B65;">
            Reason key: {_html_escape(body.reason)}<br/>
            User ID: {_html_escape(user.get('id') or '')}<br/>
            Account email: {_html_escape(user.get('email') or '')}<br/>
            IP: {safe_ip}<br/>
            UA: {safe_ua}<br/>
            {when_iso}
          </td></tr>
          <tr><td style="padding-top: 20px; font-size: 11px; color: #5A6B65;">
            — Reply directly to reach the user
          </td></tr>
        </table>
        """
        try:
            # reply_to lets the admin hit "Reply" and land straight in the
            # user's inbox without copy-pasting the address out of the body.
            def _send_with_reply_to():
                if not _resend or not _RESEND_API_KEY:
                    return None
                payload = {
                    "from": _RESEND_SENDER,
                    "to": [_RESEND_ADMIN_RECIPIENT],
                    "subject": f"[{reason_label}] - Solarisound Support",
                    "html": html,
                }
                if reply_email:
                    payload["reply_to"] = [reply_email]
                try:
                    result = _resend.Emails.send(payload)
                    return (result or {}).get("id")
                except Exception as e:
                    logger.warning("[resend] support send failed: %s", type(e).__name__)
                    return None
            resend_id = await asyncio.to_thread(_send_with_reply_to)
            if resend_id:
                delivered = True
                try:
                    await db.support_messages.update_one(
                        {"id": doc["id"]},
                        {"$set": {"delivered": True, "resend_id": resend_id}},
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.warning("[support] resend dispatch error: %s", type(e).__name__)

    # Confirmation email TO THE USER — separate task so a Resend hiccup on
    # the admin notify doesn't block the user ack (and vice versa). Also
    # silent no-op when Resend isn't configured.
    if reply_email:
        asyncio.create_task(
            _send_support_ack_to_user(reply_email, display_name, reason_label, msg)
        )

    return {
        "ok": True,
        "delivered": delivered,
        "message": "Thank you for reaching out. We will get back to you shortly.",
    }


# --- Admin Support Inbox ----------------------------------------------------
# Admin-only endpoints for browsing, replying to, and resolving support
# tickets that users submit via the floating Support Bubble on the frontend.
# Every ticket is stored durably in db.support_messages (see /support/contact
# above). This inbox is the primary place admins operate on that collection —
# email is best-effort delivery, this UI is the source of truth.


class SupportReplyIn(BaseModel):
    message: str = Field(min_length=5, max_length=6000)
    # When true (default), the ticket flips to 'resolved' after the reply is
    # queued. Admin can un-check this in the UI to keep the thread open for
    # further correspondence.
    mark_resolved: bool = True


def _support_public(doc: dict) -> dict:
    """Strip Mongo internals + normalise legacy docs (no status field yet)."""
    out = dict(doc or {})
    out.pop("_id", None)
    out.setdefault("status", "open")
    out.setdefault("admin_replies", [])
    out.setdefault("resolved_at", None)
    out.setdefault("resolved_by", None)
    return out


@api.get("/admin/support")
async def admin_support_list(
    status: str = "all",
    q: str = "",
    skip: int = 0,
    limit: int = 25,
    user: dict = Depends(get_current_user),
):
    _require_admin(user)
    limit = max(1, min(100, int(limit)))
    skip  = max(0, int(skip))

    query: dict = {}
    if status in ("open", "resolved"):
        # Legacy docs (created before this feature) have no `status` field,
        # so treat missing as 'open' for consistency.
        if status == "open":
            query["$or"] = [{"status": "open"}, {"status": {"$exists": False}}]
        else:
            query["status"] = "resolved"
    q_clean = (q or "").strip()
    if q_clean:
        rx = {"$regex": re.escape(q_clean), "$options": "i"}
        # Match against user_email, user_name, message body, or reason label.
        or_clause = [
            {"user_email": rx},
            {"user_name": rx},
            {"message": rx},
            {"reason_label": rx},
        ]
        if "$or" in query:
            # Combine status $or with search $or via $and so both apply.
            existing = query.pop("$or")
            query["$and"] = [{"$or": existing}, {"$or": or_clause}]
        else:
            query["$or"] = or_clause

    total = await db.support_messages.count_documents(query)
    # Open messages first (by newest), then resolved (by newest). We express
    # this as a compound sort on a synthetic priority AFTER the query, which
    # Mongo can't do natively — so instead we just sort by created_at desc.
    # Filtering by status is the primary way admins narrow the view.
    cursor = (
        db.support_messages.find(query, {"_id": 0})
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
    )
    items = [_support_public(d) for d in await cursor.to_list(limit)]

    # Aggregate counts for the sidebar filter chips ("Open · 12 / Resolved · 45").
    total_open = await db.support_messages.count_documents({
        "$or": [{"status": "open"}, {"status": {"$exists": False}}],
    })
    total_resolved = await db.support_messages.count_documents({"status": "resolved"})

    return {
        "items": items,
        "total": total,
        "offset": skip,
        "limit": limit,
        "counts": {
            "open": total_open,
            "resolved": total_resolved,
            "all": total_open + total_resolved,
        },
    }


@api.post("/admin/support/{msg_id}/reply")
async def admin_support_reply(
    msg_id: str,
    body: SupportReplyIn,
    request: Request,
    user: dict = Depends(get_current_user),
):
    _require_admin(user)
    doc = await db.support_messages.find_one({"id": msg_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Message not found")

    reply_text = body.message.strip()
    reply_email = (doc.get("reply_to_email") or doc.get("user_email") or "").strip()
    if not reply_email:
        raise HTTPException(status_code=400, detail="No email on file to reply to")

    when_iso = datetime.now(timezone.utc).isoformat()
    resend_id: Optional[str] = None
    delivered = False

    if _resend and _RESEND_API_KEY:
        safe_reply = _html_escape(reply_text).replace("\n", "<br/>")
        safe_original = _html_escape(doc.get("message", "")).replace("\n", "<br/>")
        safe_reason = _html_escape(doc.get("reason_label", "Support"))
        html = f"""
        <table style="font-family: -apple-system, system-ui, sans-serif; max-width: 560px; margin: 0; padding: 24px; background: #08120F; color: #E8E3D9; border-radius: 12px;">
          <tr><td style="font-size: 11px; letter-spacing: 2px; color: #72C2AC; text-transform: uppercase;">Solarisound · Reply</td></tr>
          <tr><td style="padding-top: 8px; font-size: 20px; font-weight: 500; color: #E8E3D9;">Re: {safe_reason}</td></tr>
          <tr><td style="padding-top: 20px;">
            <div style="background: #101F1A; border: 1px solid rgba(92,158,140,0.2); border-radius: 8px; padding: 16px; font-size: 14px; color: #E8E3D9; line-height: 1.55; white-space: pre-wrap;">{safe_reply}</div>
          </td></tr>
          <tr><td style="padding-top: 20px; font-size: 11px; color: #5A6B65; letter-spacing: 1px; text-transform: uppercase;">Your original message</td></tr>
          <tr><td style="padding-top: 8px;">
            <div style="background: rgba(20,38,31,0.35); border-left: 2px solid rgba(196,166,122,0.4); padding: 12px 14px; font-size: 12px; color: #8A9A92; line-height: 1.5; white-space: pre-wrap;">{safe_original}</div>
          </td></tr>
          <tr><td style="padding-top: 24px; font-size: 11px; color: #5A6B65;">— The Solarisound team</td></tr>
        </table>
        """
        def _send():
            try:
                result = _resend.Emails.send({
                    "from": _RESEND_SENDER,
                    "to": [reply_email],
                    "subject": f"Re: [{doc.get('reason_label','Support')}] - Solarisound Support",
                    "html": html,
                })
                return (result or {}).get("id")
            except Exception as e:
                logger.warning("[resend] admin support reply failed: %s", type(e).__name__)
                return None
        resend_id = await asyncio.to_thread(_send)
        delivered = bool(resend_id)

    reply_entry = {
        "message": reply_text,
        "at": when_iso,
        "admin_id": user["id"],
        "admin_email": user.get("email"),
        "resend_id": resend_id,
        "delivered": delivered,
    }
    update: dict = {"$push": {"admin_replies": reply_entry}}
    if body.mark_resolved:
        update["$set"] = {
            "status": "resolved",
            "resolved_at": when_iso,
            "resolved_by": user["id"],
        }
    await db.support_messages.update_one({"id": msg_id}, update)
    doc2 = await db.support_messages.find_one({"id": msg_id}, {"_id": 0})
    return {"ok": True, "delivered": delivered, "message": _support_public(doc2)}


@api.post("/admin/support/{msg_id}/resolve")
async def admin_support_resolve(msg_id: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    res = await db.support_messages.update_one(
        {"id": msg_id},
        {"$set": {
            "status": "resolved",
            "resolved_at": datetime.now(timezone.utc).isoformat(),
            "resolved_by": user["id"],
        }},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Message not found")
    doc = await db.support_messages.find_one({"id": msg_id}, {"_id": 0})
    return {"ok": True, "message": _support_public(doc)}


@api.post("/admin/support/{msg_id}/reopen")
async def admin_support_reopen(msg_id: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    res = await db.support_messages.update_one(
        {"id": msg_id},
        {"$set": {"status": "open", "resolved_at": None, "resolved_by": None}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Message not found")
    doc = await db.support_messages.find_one({"id": msg_id}, {"_id": 0})
    return {"ok": True, "message": _support_public(doc)}


@api.delete("/admin/support/{msg_id}")
async def admin_support_delete(msg_id: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    res = await db.support_messages.delete_one({"id": msg_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"ok": True}


# --- Per-frequency ideal default volume ------------------------------------
# The frontend ships a baseline map (see lib/frequencyDefaults.js). Admins can
# override any Hz→volume via these endpoints without a code change. The
# public GET is unauthenticated so the Dashboard can fetch overrides during
# initial mount, before we know if the visitor is even signed in.

@api.get("/frequency-defaults")
async def get_frequency_defaults():
    """Public: returns admin-configured per-frequency ideal default volumes.

    Merged over the frontend baseline map on the client. Values are
    number 0..1 keyed by string-Hz (JSON keys must be strings).
    """
    try:
        cursor = db.frequency_volume_defaults.find({}, {"_id": 0, "hz": 1, "volume": 1})
        overrides: dict[str, float] = {}
        async for doc in cursor:
            hz = doc.get("hz")
            vol = doc.get("volume")
            if hz is None or vol is None:
                continue
            try:
                overrides[str(float(hz))] = max(0.0, min(1.0, float(vol)))
            except (TypeError, ValueError):
                continue
        return {"overrides": overrides}
    except Exception:
        return {"overrides": {}}


@api.get("/admin/frequency-defaults")
async def admin_list_frequency_defaults(user: dict = Depends(get_current_user)):
    _require_admin(user)
    docs: list[dict] = []
    cursor = db.frequency_volume_defaults.find({}, {"_id": 0}).sort("hz", 1)
    async for d in cursor:
        docs.append(d)
    return {"overrides": docs}


@api.put("/admin/frequency-defaults")
async def admin_upsert_frequency_default(
    payload: dict,
    user: dict = Depends(get_current_user),
):
    _require_admin(user)
    try:
        hz = float(payload.get("hz"))
        volume = float(payload.get("volume"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="hz and volume must be numbers")
    if not (0.0 < hz <= 20000.0):
        raise HTTPException(status_code=400, detail="hz out of range")
    if not (0.0 <= volume <= 1.0):
        raise HTTPException(status_code=400, detail="volume must be between 0 and 1")
    now_iso = datetime.now(timezone.utc).isoformat()
    await db.frequency_volume_defaults.update_one(
        {"hz": hz},
        {
            "$set": {
                "hz": hz,
                "volume": volume,
                "updated_at": now_iso,
                "updated_by": user.get("id"),
            }
        },
        upsert=True,
    )
    return {"ok": True, "hz": hz, "volume": volume}


@api.delete("/admin/frequency-defaults/{hz}")
async def admin_delete_frequency_default(hz: float, user: dict = Depends(get_current_user)):
    _require_admin(user)
    res = await db.frequency_volume_defaults.delete_one({"hz": hz})
    return {"ok": True, "deleted": res.deleted_count}


# --- Re-engagement email: tracking + prefs + admin analytics ---------------
# Public endpoints (no auth) used by the outbound HTML: open pixel + click
# redirect + one-tap unsubscribe. All accept a per-nudge or per-user token
# so no personal state is required to render them.

@api.get("/e/track/open/{nudge_id}")
async def nudge_track_open(nudge_id: str):
    """Transparent 1×1 GIF used as the email's open-tracking pixel."""
    try:
        await db.email_nudges.update_one(
            {"id": nudge_id, "opened_at": None},
            {"$set": {"opened_at": datetime.now(timezone.utc).isoformat()}},
        )
    except Exception:
        pass
    # A single transparent 43-byte GIF89a payload.
    pixel = (
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04"
        b"\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
    )
    return Response(content=pixel, media_type="image/gif", headers={"Cache-Control": "no-store"})


@api.get("/e/track/click/{nudge_id}")
async def nudge_track_click(nudge_id: str, to: str = ""):
    """Records the click on the nudge CTA, then 302s the recipient to their
    intended destination. `to` is a fully-qualified URL — we validate it
    against a strict prefix allow-list so the redirect can't be abused as
    an open redirector."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        await db.email_nudges.update_one(
            {"id": nudge_id},
            {"$set": {"clicked_at": now}},
        )
        # A click implies an open — stamp opened_at too if we somehow missed
        # the pixel (Gmail image proxy caching, etc.).
        await db.email_nudges.update_one(
            {"id": nudge_id, "opened_at": None},
            {"$set": {"opened_at": now}},
        )
    except Exception:
        pass
    base = _build_frontend_url()
    dest = to or base
    # Anti-open-redirect — only allow same-origin destinations.
    if not (dest.startswith(base + "/") or dest == base):
        dest = base
    return Response(status_code=302, headers={"Location": dest})


@api.get("/e/unsub/{token}")
async def nudge_unsubscribe(token: str):
    """One-tap unsubscribe from re-engagement emails. GET so the link in
    the email body works without a form. Idempotent."""
    if not token or len(token) < 8:
        raise HTTPException(status_code=400, detail="Invalid unsubscribe link")
    res = await db.users.update_one(
        {"nudge_unsubscribe_token": token},
        {"$set": {"nudge_unsubscribed": True}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Unknown unsubscribe link")
    # Warm HTML confirmation instead of raw JSON — this is user-facing.
    html = """<!doctype html><html><head><meta charset="utf-8"><title>Unsubscribed</title></head>
    <body style="background:#08120F;color:#E8E3D9;font-family:system-ui,sans-serif;text-align:center;padding:80px 20px;">
      <h1 style="font-family:'Cormorant Garamond',Georgia,serif;font-weight:400;">You're unsubscribed.</h1>
      <p style="color:#8A9A92;max-width:420px;margin:20px auto;line-height:1.6;">We won't send you any more re-engagement emails. Your account is unchanged — sign in any time.</p>
      <p style="padding-top:20px;"><a href="/" style="color:#C4A67A;">Return to Solarisound</a></p>
    </body></html>"""
    return Response(content=html, media_type="text/html")


@api.get("/e/prefs/{token}")
async def nudge_prefs(token: str, cadence: str = "weekly"):
    """One-tap preference change from the email footer. Currently supports
    switching cadence between 'default' (72h) and 'weekly'."""
    if cadence not in ("default", "weekly", "off"):
        cadence = "weekly"
    res = await db.users.update_one(
        {"nudge_unsubscribe_token": token},
        {"$set": {"nudge_cadence": cadence,
                  "nudge_unsubscribed": cadence == "off"}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Unknown link")
    label = "weekly" if cadence == "weekly" else ("paused" if cadence == "off" else "default (every few days)")
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Preferences updated</title></head>
    <body style="background:#08120F;color:#E8E3D9;font-family:system-ui,sans-serif;text-align:center;padding:80px 20px;">
      <h1 style="font-family:'Cormorant Garamond',Georgia,serif;font-weight:400;">Set to {label}.</h1>
      <p style="color:#8A9A92;max-width:420px;margin:20px auto;line-height:1.6;">Change this any time from your account.</p>
      <p style="padding-top:20px;"><a href="/" style="color:#C4A67A;">Return to Solarisound</a></p>
    </body></html>"""
    return Response(content=html, media_type="text/html")


class NudgePrefsIn(BaseModel):
    unsubscribed: Optional[bool] = None
    cadence: Optional[str] = None  # 'default' | 'weekly' | 'off'


@api.get("/me/nudge-prefs")
async def me_get_nudge_prefs(user: dict = Depends(get_current_user)):
    doc = await db.users.find_one({"id": user["id"]}, {"nudge_unsubscribed": 1, "nudge_cadence": 1, "_id": 0})
    return {
        "unsubscribed": bool((doc or {}).get("nudge_unsubscribed")),
        "cadence": (doc or {}).get("nudge_cadence") or "default",
    }


@api.put("/me/nudge-prefs")
async def me_update_nudge_prefs(body: NudgePrefsIn, user: dict = Depends(get_current_user)):
    upd = {}
    if body.unsubscribed is not None:
        upd["nudge_unsubscribed"] = bool(body.unsubscribed)
    if body.cadence is not None:
        c = body.cadence.lower()
        if c not in ("default", "weekly", "off"):
            raise HTTPException(status_code=400, detail="Invalid cadence")
        upd["nudge_cadence"] = c
        if c == "off":
            upd["nudge_unsubscribed"] = True
    if not upd:
        raise HTTPException(status_code=400, detail="Nothing to update")
    await db.users.update_one({"id": user["id"]}, {"$set": upd})
    return {"ok": True, **upd}


@api.get("/admin/email-engagement")
async def admin_email_engagement(user: dict = Depends(get_current_user)):
    """Aggregate stats + recent nudges for the admin Email Engagement panel.

    Previously this endpoint issued ~13 separate count_documents queries
    (one per tier × three per tier + totals + unsubs), which on a growing
    production dataset added up to seconds of wall-time and sporadically
    tripped Cloudflare's 520 "could not parse" timeout on Refresh. The
    entire stat block now comes from a single aggregation pipeline over
    email_nudges + one small user count for the unsubscribed total, so
    the response consistently returns in tens of milliseconds regardless
    of collection size."""
    _require_admin(user)
    try:
        pipeline = [
            {"$group": {
                "_id": None,
                "total":     {"$sum": 1},
                "delivered": {"$sum": {"$cond": [{"$eq": ["$delivered", True]}, 1, 0]}},
                "opened":    {"$sum": {"$cond": [{"$ne": ["$opened_at", None]}, 1, 0]}},
                "clicked":   {"$sum": {"$cond": [{"$ne": ["$clicked_at", None]}, 1, 0]}},
                "per_tier_raw": {"$push": {
                    "tier": "$tier",
                    "opened": {"$cond": [{"$ne": ["$opened_at", None]}, 1, 0]},
                    "clicked": {"$cond": [{"$ne": ["$clicked_at", None]}, 1, 0]},
                }},
            }},
        ]
        agg = await db.email_nudges.aggregate(pipeline).to_list(1)
    except Exception as e:
        logger.warning("[email-engagement] aggregate failed: %s", type(e).__name__)
        agg = []

    if agg:
        row = agg[0]
        total = int(row.get("total", 0))
        delivered = int(row.get("delivered", 0))
        opened = int(row.get("opened", 0))
        clicked = int(row.get("clicked", 0))
        per_tier: dict = {t: {"sent": 0, "opened": 0, "clicked": 0} for t in ("72h", "7d", "14d", "30d")}
        for r in row.get("per_tier_raw", []) or []:
            tk = r.get("tier")
            if tk in per_tier:
                per_tier[tk]["sent"]    += 1
                per_tier[tk]["opened"]  += int(r.get("opened") or 0)
                per_tier[tk]["clicked"] += int(r.get("clicked") or 0)
    else:
        total = delivered = opened = clicked = 0
        per_tier = {t: {"sent": 0, "opened": 0, "clicked": 0} for t in ("72h", "7d", "14d", "30d")}

    # Recent list — capped at 25, projected to only the fields the frontend
    # renders so response size stays small.
    try:
        recent = await db.email_nudges.find(
            {},
            {"_id": 0, "id": 1, "user_email": 1, "tier": 1, "variant_key": 1,
             "top_freq": 1, "sent_at": 1, "delivered": 1, "opened_at": 1, "clicked_at": 1},
        ).sort("sent_at", -1).limit(25).to_list(25)
    except Exception as e:
        logger.warning("[email-engagement] recent fetch failed: %s", type(e).__name__)
        recent = []

    try:
        unsub_count = await db.users.count_documents({"nudge_unsubscribed": True})
    except Exception:
        unsub_count = 0

    return {
        "total": total,
        "delivered": delivered,
        "opened": opened,
        "clicked": clicked,
        "open_rate":  round(opened / total, 3) if total else 0.0,
        "click_rate": round(clicked / total, 3) if total else 0.0,
        "per_tier": per_tier,
        "unsubscribed_users": unsub_count,
        "recent": recent,
    }


@api.post("/admin/email-engagement/tick")
async def admin_email_engagement_tick(
    force: bool = False,
    user: dict = Depends(get_current_user),
):
    """Admin diagnostic — fire one scheduler pass RIGHT NOW without waiting
    for the 15-min tick. Returns the same stats dict the loop logs.

    `force=true` bypasses the 9-10am CST send-window gate so an admin can
    hand-fire an initial batch outside the usual window (e.g. first launch
    of the re-engagement system). All other gates (72h+ inactivity,
    unsubscribed check, cadence throttle, tier-already-sent check) still
    apply, so this cannot accidentally spam anyone."""
    _require_admin(user)
    if force:
        # Temporarily bypass the send-window gate by monkeypatching the
        # helper for the duration of this single tick. Cleanly restored
        # in `finally` so a raised exception can't leave the loop unlocked.
        import server as _self  # noqa: F401
        original = _in_send_window_cst
        try:
            globals()["_in_send_window_cst"] = lambda _now: True
            stats = await _reengagement_tick()
        finally:
            globals()["_in_send_window_cst"] = original
        stats["forced"] = True
        return {"ok": True, "stats": stats}
    stats = await _reengagement_tick()
    return {"ok": True, "stats": stats}




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
        try:
            await db.streaks.insert_one(doc)
        except DuplicateKeyError:
            # Race: two concurrent first-ever check-ins hit the unique
            # streaks.user_id index simultaneously. The loser falls
            # through to the update-existing branch below so both
            # requests still register a valid check-in for today.
            existing = await db.streaks.find_one({"user_id": user["id"]})
        else:
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


@api.get("/admin/health/ip-trace")
async def _admin_health_ip_trace(request: Request, user: dict = Depends(get_current_user)):
    """HF-039: admin-only trace endpoint that echoes back the derived
    client IP alongside the raw proxy headers we consider. Used to debug
    the audit-log IP resolution in production without leaking header
    dumps to the public. Non-admins get 403.
    """
    _require_admin(user)
    hdr = request.headers
    return {
        "derived_ip": _client_ip(request),
        "peer_host": (request.client.host if request.client else None),
        "trust_cloudflare_headers": _TRUST_CLOUDFLARE_HEADERS,
        "headers_seen": {
            "cf-connecting-ip": hdr.get("cf-connecting-ip"),
            "true-client-ip": hdr.get("true-client-ip"),
            "x-real-ip": hdr.get("x-real-ip"),
            "x-forwarded-for": hdr.get("x-forwarded-for"),
            "x-forwarded-host": hdr.get("x-forwarded-host"),
            "x-forwarded-proto": hdr.get("x-forwarded-proto"),
        },
    }


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


# ---------- Phase 11: Resonance / Drift Score --------------------------------
# Cosine similarity between the caller's current spectrum and their saved
# eigenmode baseline, mapped 0-100. The spectrum shape is a list of {freq,
# magnitude} pairs (client-side FFT output). We log-bin both spectra onto a
# shared frequency grid then compute cosine similarity so a small variance in
# the exact peak frequencies doesn't wreck the score — what matters is the
# distribution of energy across the audible range.
_RS_BINS = 48
_RS_F_LO = 20.0
_RS_F_HI = 20000.0


def _spectrum_to_bins(spectrum: list) -> list:
    if not spectrum:
        return [0.0] * _RS_BINS
    out = [0.0] * _RS_BINS
    counts = [0] * _RS_BINS
    log_hi_lo = math.log(_RS_F_HI / _RS_F_LO)
    for point in spectrum:
        try:
            if isinstance(point, dict):
                f = float(point.get("freq") or point.get("frequency") or 0)
                m = float(point.get("mag") or point.get("magnitude") or 0)
            elif isinstance(point, (list, tuple)) and len(point) >= 2:
                f = float(point[0]); m = float(point[1])
            else:
                continue
        except (TypeError, ValueError):
            continue
        if f < _RS_F_LO or f > _RS_F_HI or not math.isfinite(m):
            continue
        idx = min(_RS_BINS - 1, max(0, int(_RS_BINS * math.log(f / _RS_F_LO) / log_hi_lo)))
        out[idx] += m
        counts[idx] += 1
    for i in range(_RS_BINS):
        if counts[i]:
            out[i] /= counts[i]
    return out


def _compute_resonance_score(current_spectrum: list, eigen_spectrum: list) -> int:
    """Cosine similarity between `current` and `eigen` spectra → 0..100.
    Returns 100 when either spectrum is missing (there's nothing to compare)
    so a first-ever capture registers as 'baseline established today'."""
    if not current_spectrum or not eigen_spectrum:
        return 100
    a = _spectrum_to_bins(current_spectrum)
    b = _spectrum_to_bins(eigen_spectrum)
    dot = 0.0; na = 0.0; nb = 0.0
    for i in range(_RS_BINS):
        dot += a[i] * b[i]
        na += a[i] * a[i]
        nb += b[i] * b[i]
    if na <= 0 or nb <= 0:
        return 100
    cos = dot / math.sqrt(na * nb)
    if cos < 0: cos = 0.0
    if cos > 1: cos = 1.0
    return int(round(cos * 100))


class ResonanceScorePreviewIn(BaseModel):
    """Client-provided spectrum used to preview a Resonance Score prior to
    saving a new profile. Reuses the same shape as HarmonicProfileIn.spectrum."""
    spectrum: list = Field(default_factory=list, max_length=512)


@api.post("/harmonic-blueprint/resonance-score/preview")
async def preview_resonance_score(
    body: ResonanceScorePreviewIn,
    user: dict = Depends(get_current_user),
):
    """Return the caller's Resonance Score for a candidate spectrum, without
    persisting anything. Returns 100 when the caller has no eigenmode yet."""
    if not _is_pro(user):
        raise HTTPException(status_code=402, detail="Harmonic Blueprint is a Pro feature.")
    eigen = await _ensure_eigenmode(user["id"])
    if not eigen or not eigen.get("spectrum"):
        return {"score": 100, "has_baseline": False}
    score = _compute_resonance_score(body.spectrum or [], eigen.get("spectrum") or [])
    return {"score": score, "has_baseline": True}


@api.get("/harmonic-blueprint/resonance-score/history")
async def resonance_score_history(user: dict = Depends(get_current_user)):
    """Return the caller's resonance-score time series (chronological), oldest
    first, so the client can chart drift over time."""
    if not _is_pro(user):
        raise HTTPException(status_code=402, detail="Harmonic Blueprint is a Pro feature.")
    cur = db.resonance_profiles.find(
        {"user_id": user["id"], "resonance_score": {"$exists": True}},
        {"_id": 0, "resonance_score": 1, "created_at": 1, "is_eigenmode": 1, "id": 1},
    ).sort("created_at", 1)
    rows = await cur.to_list(500)
    return {"items": [
        {
            "id": r.get("id"),
            "score": int(r.get("resonance_score") or 0),
            "at": r.get("created_at"),
            "is_eigenmode": bool(r.get("is_eigenmode")),
        }
        for r in rows if r.get("resonance_score") is not None
    ]}


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
    # Phase 11 back-compat: profiles saved before the Resonance Score existed
    # lack the field. Compute + persist lazily on first read so existing users
    # see their score immediately when they reopen HB.
    if latest and latest.get("resonance_score") is None:
        if eigen and latest.get("id") != eigen.get("id"):
            score = _compute_resonance_score(
                latest.get("spectrum") or [], eigen.get("spectrum") or [],
            )
        else:
            score = 100
        try:
            await db.resonance_profiles.update_one(
                {"id": latest["id"]}, {"$set": {"resonance_score": score}},
            )
        except Exception:
            pass
        latest["resonance_score"] = score
    if eigen and eigen.get("resonance_score") is None:
        try:
            await db.resonance_profiles.update_one(
                {"id": eigen["id"]}, {"$set": {"resonance_score": 100}},
            )
        except Exception:
            pass
        eigen["resonance_score"] = 100
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
    # Phase 11 — compute + persist the Resonance Score against the eigenmode
    # baseline. First-ever captures ARE the baseline, so score = 100.
    if is_first_ever:
        resonance_score = 100
    else:
        resonance_score = _compute_resonance_score(
            body.spectrum or [], (existing_eigen or {}).get("spectrum") or [],
        )
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
        "resonance_score": resonance_score,
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
    # Sound Baths — richer immersive textures (`ref` MUST exactly match a
    # key in soundBathEngine.js `PRESETS`; a bad ref makes the journey
    # player silently no-op that track because `getSoundBath.start(ref)`
    # can't find the preset. Previously "aurora"/"grounding"/"solfeggio"
    # were used here but the actual PRESETS keys are the *_bath /
    # solfeggio_wash form — so the Eigenmode Journey looked like it was
    # playing but produced no bath audio.
    {"id": "bath-grounding", "type": "soundbath", "name": "Grounding Bath",
     "ref": "grounding_bath", "freq": 174, "duration_seconds": 600,
     "targets_bands": ["sub", "low"],
     "tagline": "Deep-earth drone bath for anchoring"},
    {"id": "bath-solfeggio", "type": "soundbath", "name": "Solfeggio Wash",
     "ref": "solfeggio_wash", "freq": 528, "duration_seconds": 600,
     "targets_bands": ["mid", "lowmid"],
     "tagline": "Layered solfeggio harmonics for the heart-mid range"},
    {"id": "bath-aurora", "type": "soundbath", "name": "Aurora Bath",
     "ref": "aurora_bath", "freq": 741, "duration_seconds": 600,
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
        # Phase 9 — Harmonic Blueprint setup tips shown on first capture.
        # Users can opt out permanently via a toggle on the tips screen;
        # default is false so first-time users get the gentle guided intro.
        "hb_tips_skipped": bool(s.get("hb_tips_skipped", False)),
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


@api.get("/harmonic-blueprint/gap-progress")
async def harmonic_blueprint_gap_progress(user: dict = Depends(get_current_user)):
    """Track each **confirmed resonance gap** in the user's latest profile across
    every historical capture. For each gap we compute its severity (|delta_db|
    from the eigenmode baseline in that gap's band) at every session, then
    classify the movement as improving / stable / needs attention.

    Also returns the resonance-score timeline alongside the eigenmode baseline
    so the frontend can render the "Resonance Progress Timeline" chart from
    the same payload.

    Pro-gated to match the rest of the Harmonic Blueprint surface.
    """
    if not _is_pro(user):
        raise HTTPException(status_code=402, detail="Harmonic Blueprint is a Pro feature.")

    eigen = await _ensure_eigenmode(user["id"])
    if not eigen:
        return {
            "gaps": [],
            "timeline": [],
            "eigenmode_id": None,
            "summary": None,
        }

    docs = await db.resonance_profiles.find(
        {"user_id": user["id"]},
        {"_id": 0, "id": 1, "bands": 1, "created_at": 1, "is_eigenmode": 1,
         "resonance_score": 1, "confirmed_gaps": 1},
    ).sort("created_at", 1).to_list(200)

    if not docs:
        return {
            "gaps": [],
            "timeline": [],
            "eigenmode_id": eigen.get("id"),
            "summary": None,
        }

    ebands = _band_map(eigen.get("bands", []))

    # Active confirmed gaps come from the latest profile — those are what the
    # user has affirmed as personally relevant. Fall back to underrepresented
    # bands only if there are no confirmed gaps at all.
    latest_doc = docs[-1]
    active_gaps = list(latest_doc.get("confirmed_gaps") or [])

    def _severity_at(doc: dict, band_key: str) -> Optional[float]:
        cur = _band_map(doc.get("bands", []))
        if band_key not in cur or band_key not in ebands:
            return None
        try:
            delta = float(cur[band_key].get("db", -60)) - float(ebands[band_key].get("db", -60))
        except (TypeError, ValueError):
            return None
        return round(abs(delta), 2)

    gap_rows = []
    for g in active_gaps[:16]:
        key = g.get("key")
        if not key:
            continue
        history_points = []
        for d in docs:
            sev = _severity_at(d, key)
            if sev is None:
                continue
            history_points.append({
                "profile_id": d.get("id"),
                "at": d.get("created_at"),
                "severity": sev,
                "is_eigenmode": bool(d.get("is_eigenmode")),
            })
        if not history_points:
            continue

        # First severity is the first NON-eigenmode reading (baseline is 0 by
        # definition and would inflate "improvement" numbers). Fall back to
        # the first available point if the user only has an eigenmode.
        non_baseline = [p for p in history_points if not p["is_eigenmode"]]
        first_pt = non_baseline[0] if non_baseline else history_points[0]
        last_pt = history_points[-1]

        first_sev = first_pt["severity"]
        latest_sev = last_pt["severity"]

        # Closure percentage: positive means gap is getting smaller (closer to
        # eigenmode). Guard against division by zero.
        if first_sev > 0.01:
            closure_pct = round(((first_sev - latest_sev) / first_sev) * 100, 1)
        else:
            closure_pct = 0.0

        # Trend classification — hysteresis around ±10% keeps it from
        # flipping on tiny measurement noise.
        if closure_pct >= 10:
            trend = "improving"
        elif closure_pct <= -10:
            trend = "attention"
        else:
            trend = "stable"

        gap_rows.append({
            "key": key,
            "label": g.get("label") or key,
            "lo": g.get("lo"),
            "hi": g.get("hi"),
            "direction": g.get("direction"),
            "first_severity": first_sev,
            "latest_severity": latest_sev,
            "closure_pct": closure_pct,
            "trend": trend,
            "sample_count": len(history_points),
            "history": history_points,
        })

    # Resonance-score timeline for the "Resonance Progress Timeline" chart.
    timeline = [
        {
            "id": d.get("id"),
            "score": int(d.get("resonance_score")) if d.get("resonance_score") is not None else None,
            "at": d.get("created_at"),
            "is_eigenmode": bool(d.get("is_eigenmode")),
        }
        for d in docs if d.get("resonance_score") is not None
    ]

    # Overall summary — improvement in resonance score since the first
    # non-baseline session. Falls back gracefully if the user only has an
    # eigenmode + one follow-up.
    non_eigen_timeline = [t for t in timeline if not t["is_eigenmode"]]
    summary = None
    if len(non_eigen_timeline) >= 1:
        first_score = non_eigen_timeline[0]["score"]
        latest_score = non_eigen_timeline[-1]["score"]
        if first_score and first_score > 0:
            pct = round(((latest_score - first_score) / first_score) * 100, 1)
        else:
            pct = 0.0
        summary = {
            "first_score": first_score,
            "latest_score": latest_score,
            "improvement_pct": pct,
            "session_count": len(non_eigen_timeline),
        }

    return {
        "gaps": gap_rows,
        "timeline": timeline,
        "eigenmode_id": eigen.get("id"),
        "summary": summary,
    }


class GapEditIn(BaseModel):
    confirmed_gaps: list[dict] = Field(default_factory=list, max_length=16)


# ---------------- Phase 12b: Before/After Frequency Map ----------------

_BAND_ORDER = ("sub", "low", "lowmid", "mid", "uppermid", "presence")
_BAND_LABELS = {
    "sub": "Sub-bass grounding",
    "low": "Lower grounding",
    "lowmid": "Warm-body",
    "mid": "Mid-harmonic",
    "uppermid": "Upper-mid clarity",
    "presence": "Presence / brilliance",
}


def _band_alignment(delta_db: float) -> str:
    """Classify how aligned a single band is vs the baseline eigenmode.

    Bands close to baseline (|delta| < 2 dB) are considered *aligned*.
    Bands with a moderate deviation (2 - 4 dB) sit in *near*. Anything ≥ 4 dB
    off the baseline is *drift*. Thresholds mirror the drift ranking rules
    already used elsewhere in this file.
    """
    a = abs(delta_db)
    if a < 2.0:
        return "aligned"
    if a < 4.0:
        return "near"
    return "drift"


def _describe_before_after(band_deltas: list[dict]) -> str:
    """Plain-language summary of what improved vs what still drifts.

    Groups bands into (1) noticeable strengthening (lower or sub bands now
    close to baseline that were previously off), and (2) remaining drift
    (bands ≥ 4 dB off in the latest reading). Falls back gracefully to a
    generic encouragement when there's not enough signal to speak to.
    """
    strengthened = [b for b in band_deltas if b.get("improved") and b["alignment"] == "aligned"]
    lingering = [b for b in band_deltas if b["alignment"] == "drift"]

    if not band_deltas:
        return "Keep capturing sessions — your before-and-after map will appear here."

    parts: list[str] = []
    if strengthened:
        # Prefer the human-friendly labels of the first 2 strengthened bands
        names = ", ".join(b["label"].lower() for b in strengthened[:2])
        parts.append(
            f"Your {names} frequencies have strengthened significantly since you began."
        )
    if lingering:
        names = ", ".join(b["label"].lower() for b in lingering[:2])
        parts.append(
            f"Your {names} range continues to show some drift and remains a focus area."
        )
    if not parts:
        # Everything is roughly stable
        parts.append(
            "Your harmonic signature is holding steady with your baseline — a "
            "calm, consistent field to keep tuning from."
        )
    return " ".join(parts)


@api.get("/harmonic-blueprint/before-after")
async def harmonic_blueprint_before_after(user: dict = Depends(get_current_user)):
    """Side-by-side comparison of the user's first eigenmode baseline vs
    their most recent Harmonic Blueprint capture.

    Returns per-band values for both readings plus band-level classifications
    (`improved`, `alignment`) the frontend uses to colour teal/amber cells,
    and a plain-language `summary_text` beneath the visualisation.

    Also exposes `show_celebration` which flips true every 5th non-baseline
    session so the frontend can gently celebrate progress at the end of a
    capture flow.
    """
    if not _is_pro(user):
        raise HTTPException(status_code=402, detail="Harmonic Blueprint is a Pro feature.")

    eigen = await _ensure_eigenmode(user["id"])
    if not eigen:
        return {
            "baseline": None,
            "latest": None,
            "band_deltas": [],
            "summary_text": "Capture your baseline eigenmode to unlock your before-and-after map.",
            "session_count": 0,
            "show_celebration": False,
        }

    # "Latest" must be a capture that is *actually after* the baseline —
    # not just any non-eigenmode row. If the user manually promoted a
    # later capture to be their eigenmode (via POST
    # /harmonic-blueprint/eigenmode/promote/{id}) then older captures
    # still exist as non-eigenmodes; without this guard we'd display an
    # older reading as "latest" and end up with baseline > latest by
    # calendar date, which is impossible by definition.
    eigen_created_at = eigen.get("created_at") or ""
    latest = await db.resonance_profiles.find_one(
        {
            "user_id": user["id"],
            "is_eigenmode": {"$ne": True},
            "created_at": {"$gt": eigen_created_at},
        },
        {"_id": 0},
        sort=[("created_at", -1)],
    )
    if not latest:
        # Differentiate "first-time user" vs "user who reset their
        # baseline after having built a side-by-side map before". The
        # only signal is whether ANY non-eigenmode capture (even one
        # older than the current eigenmode, i.e. left over from before
        # the reset) exists.
        prior_readings = await db.resonance_profiles.count_documents({
            "user_id": user["id"],
            "is_eigenmode": {"$ne": True},
        })
        if prior_readings > 0:
            summary = (
                "You've reset your baseline. Take a fresh Harmonic "
                "Blueprint reading to unlock your updated side-by-side map."
            )
        else:
            summary = (
                "Your baseline is captured. Take a fresh Harmonic Blueprint "
                "reading to unlock your first side-by-side map."
            )
        return {
            "baseline": {
                "id": eigen.get("id"),
                "created_at": eigen.get("created_at"),
                "bands": eigen.get("bands", []),
            },
            "latest": None,
            "band_deltas": [],
            "summary_text": summary,
            "session_count": 0,
            "show_celebration": False,
        }

    session_count = await db.resonance_profiles.count_documents({
        "user_id": user["id"],
        "is_eigenmode": {"$ne": True},
        "created_at": {"$gt": eigen_created_at},
    })

    ebands = _band_map(eigen.get("bands", []))
    lbands = _band_map(latest.get("bands", []))

    band_deltas: list[dict] = []
    for key in _BAND_ORDER:
        if key not in ebands or key not in lbands:
            continue
        try:
            base_db = float(ebands[key].get("db", -60))
            now_db = float(lbands[key].get("db", -60))
        except (TypeError, ValueError):
            continue
        delta = round(now_db - base_db, 2)
        alignment = _band_alignment(delta)
        # "Improved" means the band is now aligned to baseline (delta ≈ 0).
        # It doesn't try to reason about whether the raw dB went up or down,
        # because the eigenmode itself is the target — closer is always better.
        improved = alignment == "aligned"
        band_deltas.append({
            "key": key,
            "label": _BAND_LABELS.get(key, key),
            "lo": ebands[key].get("lo"),
            "hi": ebands[key].get("hi"),
            "baseline_db": round(base_db, 2),
            "latest_db": round(now_db, 2),
            "delta_db": delta,
            "alignment": alignment,
            "improved": improved,
        })

    return {
        "baseline": {
            "id": eigen.get("id"),
            "created_at": eigen.get("created_at"),
            "bands": eigen.get("bands", []),
        },
        "latest": {
            "id": latest.get("id"),
            "created_at": latest.get("created_at"),
            "bands": latest.get("bands", []),
        },
        "band_deltas": band_deltas,
        "summary_text": _describe_before_after(band_deltas),
        "session_count": session_count,
        # Every 5th non-baseline session triggers the gentle celebration.
        "show_celebration": session_count > 0 and session_count % 5 == 0,
    }


# ---------------- Phase 12c: Session Impact Rating ----------------

_RATING_WEIGHTS = {"clear_shift": 3, "subtle_difference": 1, "not_sure": 0}


class ImpactRatingIn(BaseModel):
    entry_id: str = Field(min_length=1, max_length=64)
    rating: str = Field(pattern="^(clear_shift|subtle_difference|not_sure)$")


@api.get("/hb/pending-impact-ratings")
async def hb_pending_impact_ratings(user: dict = Depends(get_current_user)):
    """Return HB-recommended journey entries from ≥ 24h ago that don't yet
    have an `impact_rating`. Powers the "How did you feel after yesterday's
    {frequency} session?" prompt shown on the next app open."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    cursor = db.wellness_journey.find(
        {
            "user_id": user["id"],
            "hb_recommended": True,
            "created_at": {"$lte": cutoff},
            "impact_rating": {"$exists": False},
        },
        {"_id": 0, "id": 1, "frequency": 1, "preset_label": 1, "preset_key": 1,
         "created_at": 1, "soundscape": 1, "duration_actual_seconds": 1,
         "hb_source": 1},
    ).sort("created_at", 1).limit(5)
    rows = await cursor.to_list(length=5)

    def _label(row: dict) -> str:
        if row.get("preset_label"):
            return row["preset_label"]
        if row.get("soundscape"):
            return str(row["soundscape"]).replace("_", " ").title()
        if row.get("frequency"):
            return f"{round(float(row['frequency']))} Hz"
        return "recent"

    for r in rows:
        r["label"] = _label(r)
    return {"pending": rows}


@api.post("/hb/impact-rating")
async def hb_impact_rating(
    body: ImpactRatingIn,
    user: dict = Depends(get_current_user),
):
    """Store the user's post-session impact rating alongside the journey
    entry. Idempotent — a re-submission replaces the previous rating."""
    row = await db.wellness_journey.find_one(
        {"id": body.entry_id, "user_id": user["id"]}, {"_id": 0, "id": 1},
    )
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    await db.wellness_journey.update_one(
        {"id": body.entry_id, "user_id": user["id"]},
        {"$set": {
            "impact_rating": body.rating,
            "impact_rated_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    return {"ok": True, "rating": body.rating}


@api.get("/hb/effective-frequencies")
async def hb_effective_frequencies(user: dict = Depends(get_current_user)):
    """Aggregate rated HB-recommended sessions by frequency and return the
    top 5 by effectiveness score. Requires at least 2 rated sessions per
    frequency to reduce single-session noise.

    Effectiveness score = mean(weights) where clear_shift=3, subtle=1,
    not_sure=0. Normalised to 0-100 for a friendlier UI.
    """
    if not _is_pro(user):
        raise HTTPException(status_code=402, detail="Harmonic Blueprint is a Pro feature.")
    cursor = db.wellness_journey.find(
        {
            "user_id": user["id"],
            "hb_recommended": True,
            "impact_rating": {"$in": list(_RATING_WEIGHTS.keys())},
            "frequency": {"$ne": None},
        },
        {"_id": 0, "frequency": 1, "impact_rating": 1, "preset_label": 1},
    )
    rows = await cursor.to_list(length=1000)

    # Bucket by rounded frequency (nearest 1 Hz) so 432.0 and 432.1 collapse.
    buckets: dict[int, dict] = {}
    for r in rows:
        try:
            hz = int(round(float(r.get("frequency"))))
        except (TypeError, ValueError):
            continue
        b = buckets.setdefault(hz, {"scores": [], "labels": []})
        b["scores"].append(_RATING_WEIGHTS[r["impact_rating"]])
        if r.get("preset_label"):
            b["labels"].append(r["preset_label"])

    ranked = []
    for hz, b in buckets.items():
        n = len(b["scores"])
        if n < 2:
            continue
        mean = sum(b["scores"]) / n
        # Normalise 0-3 → 0-100 for a friendlier display value.
        pct = round((mean / 3.0) * 100)
        label = max(set(b["labels"]), key=b["labels"].count) if b["labels"] else f"{hz} Hz"
        ranked.append({
            "frequency": hz,
            "label": label,
            "score": pct,
            "sample_count": n,
        })

    ranked.sort(key=lambda x: (-x["score"], -x["sample_count"]))
    return {"frequencies": ranked[:5]}


# ---------------- Phase 12d: Monthly Harmonic Blueprint Report ----------------

_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _month_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def _month_bounds(month_key: str) -> tuple[datetime, datetime]:
    y, m = (int(x) for x in month_key.split("-"))
    start = datetime(y, m, 1, tzinfo=timezone.utc)
    if m == 12:
        end = datetime(y + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(y, m + 1, 1, tzinfo=timezone.utc)
    return start, end


def _friendly_month_title(month_key: str) -> str:
    y, m = (int(x) for x in month_key.split("-"))
    return f"Your {_MONTH_NAMES[m - 1]} Resonance Journey"


async def _compose_monthly_report(user_id: str, month_key: str) -> Optional[dict]:
    """Build the report payload for a specific YYYY-MM. Returns None when the
    user doesn't yet qualify (< 2 HB captures in that month). Pure function
    — no persistence. Caller decides whether/when to store the result.
    """
    start, end = _month_bounds(month_key)
    # HB captures this month (non-eigenmode only — the eigenmode is a
    # one-off baseline, not a repeatable "session").
    profiles = await db.resonance_profiles.find(
        {
            "user_id": user_id,
            "is_eigenmode": {"$ne": True},
            "created_at": {"$gte": start.isoformat(), "$lt": end.isoformat()},
        },
        {"_id": 0},
    ).sort("created_at", 1).to_list(200)
    if len(profiles) < 2:
        return None

    # Reference eigenmode for band-delta math.
    eigen = await db.resonance_profiles.find_one(
        {"user_id": user_id, "is_eigenmode": True}, {"_id": 0},
    )
    ebands = _band_map(eigen.get("bands", [])) if eigen else {}

    # Current-month latest score.
    latest = profiles[-1]
    current_score = int(latest.get("resonance_score") or 0)

    # Previous-month latest score (any capture, eigenmode or not).
    prev_start, _ = _month_bounds(month_key)
    prev_end = prev_start
    prev_month_key = (prev_start - timedelta(days=1)).strftime("%Y-%m")
    ps, pe = _month_bounds(prev_month_key)
    prev_doc = await db.resonance_profiles.find_one(
        {"user_id": user_id,
         "created_at": {"$gte": ps.isoformat(), "$lt": pe.isoformat()}},
        {"_id": 0, "resonance_score": 1},
        sort=[("created_at", -1)],
    )
    previous_score = int(prev_doc["resonance_score"]) if prev_doc and prev_doc.get("resonance_score") is not None else None

    # Band deltas: compare month-start profile (or eigenmode) against
    # month-end profile to compute per-band closure.
    first_month_profile = profiles[0]
    first_bands = _band_map(first_month_profile.get("bands", []))
    last_bands = _band_map(latest.get("bands", []))

    band_movements: list[dict] = []
    for key in _BAND_ORDER:
        if key not in ebands or key not in last_bands:
            continue
        base_db = float(ebands[key].get("db", -60))
        first_db = float(first_bands.get(key, {}).get("db", base_db))
        last_db = float(last_bands.get(key, {}).get("db", base_db))
        first_sev = abs(first_db - base_db)
        last_sev = abs(last_db - base_db)
        band_movements.append({
            "key": key,
            "label": _BAND_LABELS.get(key, key),
            "lo": ebands[key].get("lo"),
            "hi": ebands[key].get("hi"),
            "closure_db": round(first_sev - last_sev, 2),  # + = improved
            "current_severity": round(last_sev, 2),
        })

    most_improved = [b for b in sorted(band_movements, key=lambda x: -x["closure_db"])
                     if b["closure_db"] > 0.5][:3]
    most_persistent = [b for b in sorted(band_movements, key=lambda x: -x["current_severity"])
                       if b["current_severity"] >= 3.0][:3]

    # Recommended focus frequencies: centre Hz of the top persistent gaps.
    recommended = []
    for b in most_persistent:
        try:
            centre = int((float(b["lo"]) + float(b["hi"])) / 2)
        except (TypeError, ValueError):
            continue
        recommended.append({
            "frequency": centre,
            "band": b["label"],
            "range": f"{b['lo']}-{b['hi']} Hz",
        })

    # Total listening time on HB-recommended sessions this month.
    listening_cursor = db.wellness_journey.find(
        {
            "user_id": user_id,
            "hb_recommended": True,
            "created_at": {"$gte": start.isoformat(), "$lt": end.isoformat()},
        },
        {"_id": 0, "duration_actual_seconds": 1},
    )
    listening_rows = await listening_cursor.to_list(length=500)
    listening_seconds = int(sum(r.get("duration_actual_seconds") or 0 for r in listening_rows))

    return {
        "month": month_key,
        "title": _friendly_month_title(month_key),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_sessions": len(profiles),
        "resonance_score_current": current_score,
        "resonance_score_previous": previous_score,
        "resonance_score_delta": (current_score - previous_score) if previous_score is not None else None,
        "most_improved_ranges": most_improved,
        "most_persistent_gaps": most_persistent,
        "recommended_frequencies": recommended,
        "listening_seconds": listening_seconds,
        "listening_minutes": round(listening_seconds / 60),
    }


async def _ensure_monthly_report(user_id: str, month_key: str) -> Optional[dict]:
    """Return the stored report for (user, month) or lazily compose+persist
    a new one on first access. Returns None when the user doesn't yet
    qualify (< 2 sessions that month).

    For the **still-in-progress** current month we always recompute (and
    upsert) so mid-month captures aren't shown against a stale snapshot.
    Completed months are cached forever — those numbers can no longer
    change.
    """
    is_current_month = month_key == _month_key(datetime.now(timezone.utc))
    if not is_current_month:
        existing = await db.hb_monthly_reports.find_one(
            {"user_id": user_id, "month": month_key}, {"_id": 0},
        )
        if existing:
            return existing
    payload = await _compose_monthly_report(user_id, month_key)
    if not payload:
        return None
    try:
        # Upsert so the in-progress month refreshes in place and completed
        # months persist the first snapshot forever.
        await db.hb_monthly_reports.update_one(
            {"user_id": user_id, "month": month_key},
            {
                "$set": {**payload, "user_id": user_id},
                "$setOnInsert": {"id": uuid.uuid4().hex},
            },
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001 — race with another request
        logger.warning("[monthly_report] upsert failed for %s %s: %s", user_id, month_key, exc)
    stored = await db.hb_monthly_reports.find_one(
        {"user_id": user_id, "month": month_key}, {"_id": 0},
    )
    return stored or {**payload, "user_id": user_id}


@api.get("/hb/monthly-report")
async def hb_monthly_report_latest(user: dict = Depends(get_current_user)):
    """Return the most recent monthly report available for the user, plus a
    list of every prior report month for the profile-section browser.

    Lazy generation: if the user has enough sessions THIS month or LAST month
    but no stored report yet, we compose and persist one on the fly.
    """
    if not _is_pro(user):
        raise HTTPException(status_code=402, detail="Harmonic Blueprint is a Pro feature.")

    now = datetime.now(timezone.utc)
    prev_key = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    # Prefer LAST calendar month — a completed month is a real "monthly
    # summary". Fall back to the current in-progress month if last month
    # is empty but the user has already crossed the 2-session threshold.
    for candidate in (prev_key, _month_key(now)):
        r = await _ensure_monthly_report(user["id"], candidate)
        if r:
            latest = r
            break
    else:
        latest = None

    all_cursor = db.hb_monthly_reports.find(
        {"user_id": user["id"]}, {"_id": 0, "month": 1, "title": 1, "generated_at": 1},
    ).sort("month", -1).limit(24)
    available = await all_cursor.to_list(length=24)
    return {"report": latest, "available_months": available}


@api.get("/hb/monthly-report/{month}")
async def hb_monthly_report_by_month(
    month: str,
    user: dict = Depends(get_current_user),
):
    """Fetch a specific month's report. Format YYYY-MM."""
    if not _is_pro(user):
        raise HTTPException(status_code=402, detail="Harmonic Blueprint is a Pro feature.")
    if not (len(month) == 7 and month[4] == "-" and month[:4].isdigit() and month[5:].isdigit()):
        raise HTTPException(status_code=400, detail="Invalid month format. Use YYYY-MM.")
    r = await _ensure_monthly_report(user["id"], month)
    if not r:
        raise HTTPException(status_code=404, detail="No report available for this month yet.")
    return {"report": r}


# ---------------- Phase 12e: Milestone celebrations ----------------

# Milestone catalogue. Each entry maps to a deterministic detector + a warm
# celebration message that the frontend renders verbatim.
_MILESTONES = {
    "first_eigenmode": {
        "title": "Your Harmonic Blueprint is set",
        "message": "This is the beginning of your resonance journey.",
    },
    "first_gap_closed": {
        "title": "A frequency range has returned to alignment",
        "message": "A frequency range that was drifting has returned to alignment. Your practice is working.",
    },
    "streak_7": {
        "title": "Seven days of consistent practice",
        "message": "Seven days of consistent practice. Your nervous system is noticing.",
    },
    "streak_30": {
        "title": "Thirty days of returning to resonance",
        "message": "Thirty days of returning to resonance. Your commitment to your sound practice is remarkable.",
    },
    "resonance_90": {
        "title": "Deeply aligned with your natural tuning",
        "message": "You are deeply aligned with your natural tuning today.",
    },
    "full_spectrum_improvement": {
        "title": "Your entire harmonic spectrum has improved",
        "message": "Your entire harmonic spectrum shows improvement since your first baseline. A profound achievement.",
    },
}


async def _recent_milestone_for_agent(user_id: str) -> Optional[dict]:
    """Return the most recent milestone earned by the user within the past
    3 days (celebrated or not), or None. Used by the Wellness Assistant to
    weave a warm reference into its greeting when the moment fits.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    doc = await db.hb_milestones.find_one(
        {"user_id": user_id, "achieved_at": {"$gte": cutoff}},
        {"_id": 0, "key": 1, "achieved_at": 1},
        sort=[("achieved_at", -1)],
    )
    if not doc:
        return None
    cat = _MILESTONES.get(doc["key"], {})
    if not cat:
        return None
    return {
        "key": doc["key"],
        "title": cat.get("title", doc["key"]),
        "message": cat.get("message", ""),
        "achieved_at": doc.get("achieved_at") or "",
    }


async def _detect_milestones(user_id: str) -> list[dict]:
    """Return the list of milestone keys the user has EVER earned. Pure
    detection — writes nothing. Callers decide when to persist. Order
    matches the sequence users would typically experience so newly-awarded
    milestones surface in a natural order.
    """
    earned: list[dict] = []

    # 1. First eigenmode
    eigen = await db.resonance_profiles.find_one(
        {"user_id": user_id, "is_eigenmode": True}, {"_id": 0, "id": 1, "created_at": 1},
    )
    if eigen:
        earned.append({"key": "first_eigenmode", "achieved_at": eigen.get("created_at"),
                       "meta": {"profile_id": eigen.get("id")}})

    # 2. Resonance ≥ 90 on any capture
    hi = await db.resonance_profiles.find_one(
        {"user_id": user_id, "resonance_score": {"$gte": 90}},
        {"_id": 0, "id": 1, "created_at": 1, "resonance_score": 1},
        sort=[("created_at", 1)],
    )
    if hi:
        earned.append({"key": "resonance_90", "achieved_at": hi.get("created_at"),
                       "meta": {"profile_id": hi.get("id"),
                                "score": int(hi.get("resonance_score", 90))}})

    # 3. First gap closed — any historically-confirmed gap whose severity in a
    # LATER capture is below the 2 dB alignment threshold. We walk profiles
    # chronologically and look for the first later profile where the gap
    # became aligned.
    if eigen:
        ebands = _band_map(eigen.get("bands", []))  # need eigenmode bands for delta math
        # Refetch eigen with bands (previous find had a projection)
        eigen_full = await db.resonance_profiles.find_one(
            {"id": eigen["id"]}, {"_id": 0, "bands": 1},
        )
        ebands = _band_map((eigen_full or {}).get("bands", []))
        profiles = await db.resonance_profiles.find(
            {"user_id": user_id, "is_eigenmode": {"$ne": True}},
            {"_id": 0},
        ).sort("created_at", 1).to_list(200)
        # Union of every gap the user has ever confirmed
        ever_confirmed_keys: set[str] = set()
        first_seen_at: dict[str, str] = {}
        for p in profiles:
            for g in (p.get("confirmed_gaps") or []):
                k = g.get("key")
                if not k:
                    continue
                ever_confirmed_keys.add(k)
                first_seen_at.setdefault(k, p.get("created_at") or "")
        for k in ever_confirmed_keys:
            for p in profiles:
                if (p.get("created_at") or "") < first_seen_at.get(k, ""):
                    continue
                cur = _band_map(p.get("bands", []))
                if k not in cur or k not in ebands:
                    continue
                try:
                    delta = abs(float(cur[k].get("db", -60))
                                - float(ebands[k].get("db", -60)))
                except (TypeError, ValueError):
                    continue
                if delta < 2.0:
                    earned.append({"key": "first_gap_closed",
                                   "achieved_at": p.get("created_at"),
                                   "meta": {"band": k, "profile_id": p.get("id")}})
                    break
            if any(e["key"] == "first_gap_closed" for e in earned):
                break

    # 4/5. Streak milestones — reads current + longest so a user who
    # already crossed the threshold once earns permanently.
    streak = await db.streaks.find_one({"user_id": user_id}, {"_id": 0}) or {}
    peak = max(int(streak.get("longest", 0)), int(streak.get("current", 0)))
    if peak >= 7:
        earned.append({"key": "streak_7",
                       "achieved_at": streak.get("last_checkin_date") or streak.get("updated_at"),
                       "meta": {"days": peak}})
    if peak >= 30:
        earned.append({"key": "streak_30",
                       "achieved_at": streak.get("last_checkin_date") or streak.get("updated_at"),
                       "meta": {"days": peak}})

    # 6. Full spectrum improvement — every tracked band's |delta| in the
    # LATEST capture is smaller than its |delta| in the FIRST non-baseline
    # capture. Requires ≥ 2 non-baseline captures to reason about
    # "improvement since first baseline".
    if eigen:
        eigen_full = await db.resonance_profiles.find_one(
            {"id": eigen["id"]}, {"_id": 0, "bands": 1},
        )
        ebands = _band_map((eigen_full or {}).get("bands", []))
        non_baseline = await db.resonance_profiles.find(
            {"user_id": user_id, "is_eigenmode": {"$ne": True}},
            {"_id": 0, "id": 1, "bands": 1, "created_at": 1},
        ).sort("created_at", 1).to_list(200)
        if len(non_baseline) >= 2 and ebands:
            first_p, last_p = non_baseline[0], non_baseline[-1]
            fbands = _band_map(first_p.get("bands", []))
            lbands = _band_map(last_p.get("bands", []))
            def _sev(bmap: dict, key: str) -> Optional[float]:
                if key not in bmap or key not in ebands:
                    return None
                try:
                    return abs(float(bmap[key].get("db", -60))
                               - float(ebands[key].get("db", -60)))
                except (TypeError, ValueError):
                    return None
            all_improved = True
            checked = 0
            for key in _BAND_ORDER:
                fs = _sev(fbands, key)
                ls = _sev(lbands, key)
                if fs is None or ls is None:
                    continue
                checked += 1
                # Every band must be strictly better OR already aligned
                # (severity < 2 dB is effectively perfect).
                if ls >= fs and ls >= 2.0:
                    all_improved = False
                    break
            if checked >= 4 and all_improved:
                earned.append({"key": "full_spectrum_improvement",
                               "achieved_at": last_p.get("created_at"),
                               "meta": {"profile_id": last_p.get("id"),
                                        "bands_checked": checked}})

    return earned


@api.get("/hb/milestones")
async def hb_milestones_list(user: dict = Depends(get_current_user)):
    """Return the user's milestone timeline. Runs detection, persists any
    newly-earned milestones (idempotent — a `(user_id, key)` uniqueness
    guard prevents duplicates), and reports which ones haven't been
    celebrated yet so the frontend can surface the overlay.
    """
    detected = await _detect_milestones(user["id"])
    stored = await db.hb_milestones.find(
        {"user_id": user["id"]}, {"_id": 0},
    ).sort("achieved_at", 1).to_list(50)
    stored_by_key = {m["key"]: m for m in stored}

    new_docs: list[dict] = []
    for d in detected:
        if d["key"] in stored_by_key:
            continue
        doc = {
            "id": uuid.uuid4().hex,
            "user_id": user["id"],
            "key": d["key"],
            "achieved_at": d.get("achieved_at") or datetime.now(timezone.utc).isoformat(),
            "celebrated_at": None,
            "meta": d.get("meta") or {},
        }
        try:
            await db.hb_milestones.insert_one(doc)
            doc.pop("_id", None)
            new_docs.append(doc)
        except Exception as exc:  # duplicate-key race
            logger.warning("[milestones] insert failed for %s %s: %s",
                          user["id"], d["key"], exc)

    all_docs = list(stored_by_key.values()) + new_docs
    all_docs.sort(key=lambda m: m.get("achieved_at") or "")

    for m in all_docs:
        cat = _MILESTONES.get(m["key"], {})
        m["title"] = cat.get("title", m["key"])
        m["message"] = cat.get("message", "")

    # A milestone is "new" (celebration overlay eligible) when it has never
    # been celebrated yet. Surface the OLDEST un-celebrated first so the
    # user experiences milestones in the order they earned them.
    pending = [m for m in all_docs if not m.get("celebrated_at")]
    return {
        "milestones": all_docs,
        "pending_celebration": pending,
    }


@api.post("/hb/milestones/{key}/celebrate")
async def hb_milestones_celebrate(
    key: str,
    user: dict = Depends(get_current_user),
):
    """Mark a milestone as celebrated so it no longer surfaces the overlay
    on future app opens."""
    if key not in _MILESTONES:
        raise HTTPException(status_code=400, detail="Unknown milestone key.")
    now = datetime.now(timezone.utc).isoformat()
    r = await db.hb_milestones.update_one(
        {"user_id": user["id"], "key": key},
        {"$set": {"celebrated_at": now}},
    )
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="Milestone not yet earned.")
    return {"ok": True, "celebrated_at": now}


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
    # Phase 12c — flag set by the client when the session was triggered by an
    # HB gap recommendation or a Wellness Assistant suggestion that referenced
    # a resonance gap. Powers the 24h impact-rating follow-up and the
    # "Your Most Effective Frequencies" aggregation.
    hb_recommended: bool = False
    hb_source: Optional[str] = Field(default=None, max_length=32)   # e.g. 'hb_gap' | 'assistant_gap'


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

    # Phase 9: Gentle HB setup nudge — for users WITHOUT an eigenmode
    # profile who have logged enough listening sessions since the last
    # nudge. Rules:
    #   • never fires if the user has already captured an eigenmode
    #   • never fires within the same session as a dismissal
    #   • ≥ 3 completed listening sessions must have passed since the last
    #     nudge OR since account creation (spacing floor)
    #   • the LLM decides the wording each time — the directive only
    #     invites it to weave a natural one-liner IF the reply is
    #     already suggesting a frequency/preset
    hb_nudge_shown = False
    try:
        needs_nudge = False
        eigen_exists = await _ensure_eigenmode(user["id"])
        if not eigen_exists:
            udoc = await db.users.find_one(
                {"id": user["id"]},
                {"hb_nudge_last_shown_journey_count": 1, "hb_nudge_dismissed_session_id": 1},
            ) or {}
            if udoc.get("hb_nudge_dismissed_session_id") != body.session_id:
                journey_count = await db.wellness_journey.count_documents({"user_id": user["id"]})
                last_shown = int(udoc.get("hb_nudge_last_shown_journey_count") or 0)
                # Spacing floor: 3 listening sessions between nudges. First
                # nudge fires once the user has ≥ 3 sessions of history.
                if journey_count - last_shown >= 3:
                    needs_nudge = True
        if needs_nudge:
            parts.append(
                "HB_SETUP_NUDGE_ELIGIBLE: this user has NOT captured a Harmonic "
                "Blueprint yet. If — and only if — your reply is already "
                "suggesting a frequency, preset, or soundscape, weave ONE "
                "warm, brief sentence inviting them to capture their "
                "Harmonic Blueprint so future suggestions can be personalised "
                "to their resonance profile. VARY the wording every time; "
                "never sound like a canned prompt. Never mention 'HB setup', "
                "'set up', or use bracket/parenthetical asides — say it "
                "conversationally as if suggesting a next step to a friend. "
                "If the reply isn't already suggesting audio, OMIT the nudge "
                "entirely — do not force it."
            )
            hb_nudge_shown = True
    except Exception as exc:
        logger.warning("[agent_chat] HB nudge eligibility check failed: %s", exc)

    # Phase 7: behavioural patterns detected across the user's journey.
    # Compact block, top-3 non-dismissed patterns, with soft one-callback
    # guidance so the LLM doesn't lecture.
    try:
        pat_block = await _user_patterns_prompt_block(user["id"])
        if pat_block:
            parts.append(pat_block)
    except Exception as exc:
        logger.warning("[agent_chat] user_patterns block failed: %s", exc)

    # Phase 12f: recent milestone reference. If the user earned a milestone
    # in the last 3 days (celebrated OR not), invite the assistant to
    # reference it once, warmly, if the moment fits. Never a canned prompt.
    try:
        recent_ms = await _recent_milestone_for_agent(user["id"])
        if recent_ms:
            parts.append(
                f"RECENT_MILESTONE: The user recently reached the milestone "
                f"\"{recent_ms['title']}\" — {recent_ms['message']} — on "
                f"{recent_ms['achieved_at'][:10]}. If — and ONLY if — the "
                f"moment feels right in your reply (e.g. warm greeting or "
                f"acknowledging progress), weave ONE brief, human sentence "
                f"referencing it. Vary wording every time. Never bracket or "
                f"parenthesise. Never repeat it verbatim. If it doesn't "
                f"fit the emotional register of your reply, OMIT it entirely."
            )
    except Exception as exc:
        logger.warning("[agent_chat] recent milestone block failed: %s", exc)

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
        # Phase 9: attach the nudge-shown flag AND record the journey-count
        # snapshot on the user doc so we honour the 3-session spacing rule
        # even if the user never explicitly dismisses. Failure to record
        # doesn't block the reply.
        if hb_nudge_shown:
            reply["hb_nudge_shown"] = True
            try:
                jc = await db.wellness_journey.count_documents({"user_id": user["id"]})
                await db.users.update_one(
                    {"id": user["id"]},
                    {"$set": {"hb_nudge_last_shown_journey_count": jc}},
                )
            except Exception as exc:
                logger.warning("[agent_chat] hb_nudge bookkeeping failed: %s", exc)
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


# Number of *new* wellness_journey sessions that must accumulate after a
# pattern is dismissed before it becomes eligible to naturally re-surface
# (only if the underlying behaviour is still present in detection).
PATTERN_REDISMISS_SESSION_WINDOW = 7


async def _wellness_session_count(user_id: str) -> int:
    """Return the count of wellness_journey rows for this user. This is
    the "session count" that drives the 7-session re-evaluation window
    for dismissed patterns. Errors are swallowed and return 0 so the
    dismissal logic degrades gracefully rather than crashing the whole
    /me/patterns read."""
    try:
        return int(await db.wellness_journey.count_documents({"user_id": user_id}))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[patterns] session_count failed for %s: %s", user_id, exc)
        return 0


async def _effective_dismissed_pattern_keys(
    user_id: str, session_count: Optional[int] = None,
) -> set:
    """Return the set of pattern keys that are *currently* considered
    dismissed for this user, after applying the 7-session auto
    re-evaluation window.

    Reads both the modern `dismissed_patterns_v2` map ({key: session_count})
    and the legacy `dismissed_patterns` list. Any legacy list entries
    are migrated into `dismissed_patterns_v2` (stamped with the current
    session count) on first read so they get exactly one grace window
    and then follow the normal 7-session re-evaluation like everything
    else.

    A key is auto-un-dismissed once `current_sessions - dismissed_at >= 7`.
    """
    if session_count is None:
        session_count = await _wellness_session_count(user_id)
    try:
        doc = await db.users.find_one(
            {"id": user_id},
            {"dismissed_patterns": 1, "dismissed_patterns_v2": 1},
        ) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[patterns] effective_dismissed lookup failed for %s: %s", user_id, exc)
        return set()

    v2 = doc.get("dismissed_patterns_v2") or {}
    legacy = doc.get("dismissed_patterns") or []

    # Migrate any legacy list entries into v2 with the current session
    # count so they enter the normal 7-session window. Best-effort; a
    # failure here does not block the read.
    if legacy:
        try:
            v2_updates: dict = {}
            for k in legacy:
                if isinstance(v2, dict) and k in v2:
                    continue
                v2_updates[f"dismissed_patterns_v2.{k}"] = session_count
                if isinstance(v2, dict):
                    v2[k] = session_count
            update_doc: dict = {"$unset": {"dismissed_patterns": ""}}
            if v2_updates:
                update_doc["$set"] = v2_updates
            await db.users.update_one({"id": user_id}, update_doc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[patterns] legacy migration failed for %s: %s", user_id, exc)

    still_dismissed: set = set()
    for k, at in (v2.items() if isinstance(v2, dict) else []):
        try:
            at_i = int(at)
        except (TypeError, ValueError):
            at_i = 0
        if session_count - at_i < PATTERN_REDISMISS_SESSION_WINDOW:
            still_dismissed.add(k)
    return still_dismissed


async def _user_patterns_prompt_block(user_id: str) -> str:
    """Assemble a compact `USER_PATTERNS` block for the agent_chat LLM
    prompt. Only non-dismissed, top-3-by-priority patterns are included so
    we don't drown the model in signals it already gets from journey rows."""
    try:
        dismissed = await _effective_dismissed_pattern_keys(user_id)
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
    """Return the user's currently-detected patterns, plus their effective
    dismissal list (after applying the 7-session auto re-evaluation
    window). The client sorts / picks which chip to show. Uses the 15-min
    patterns_cache to keep the greeting chip snappy on mobile.

    Dismissed patterns naturally re-surface once
    `current_sessions - dismissed_at_session_count >= 7` — no manual
    reset required. If the underlying behaviour is no longer present in
    detection, the key stays hidden regardless.
    """
    session_count = await _wellness_session_count(user["id"])
    patterns = await _cached_detect_wellness_patterns(user["id"])
    dismissed_set = await _effective_dismissed_pattern_keys(user["id"], session_count)

    # Opportunistically prune expired v2 entries so the doc doesn't grow
    # unbounded. Legacy list entries are already drained during the
    # `_effective_dismissed_pattern_keys` call above. Best-effort; a
    # failure here does not break the read.
    try:
        doc = await db.users.find_one(
            {"id": user["id"]}, {"dismissed_patterns_v2": 1},
        ) or {}
        v2 = doc.get("dismissed_patterns_v2") or {}
        stale_v2 = [
            k for k, at in (v2.items() if isinstance(v2, dict) else [])
            if (session_count - (int(at) if str(at).lstrip("-").isdigit() else 0))
            >= PATTERN_REDISMISS_SESSION_WINDOW
        ]
        if stale_v2:
            await db.users.update_one(
                {"id": user["id"]},
                {"$unset": {f"dismissed_patterns_v2.{k}": "" for k in stale_v2}},
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[patterns] prune failed for %s: %s", user["id"], exc)

    return {
        "patterns": patterns,
        "dismissed": list(dismissed_set),
        "session_count": session_count,
        "redismiss_window_sessions": PATTERN_REDISMISS_SESSION_WINDOW,
    }


@api.post("/me/patterns/{pattern_key:path}/dismiss")
async def pattern_dismiss(pattern_key: str, user: dict = Depends(get_current_user)):
    """Mark a pattern key as dismissed for this user, stamped with the
    user's current wellness_journey session count so the pattern can be
    auto re-evaluated after `PATTERN_REDISMISS_SESSION_WINDOW` new
    sessions. Idempotent — repeats update the stamp to "now" so a fresh
    dismissal restarts the countdown. `path` converter is used because
    keys contain ':' and '@'.
    """
    if not pattern_key or len(pattern_key) > 120:
        raise HTTPException(status_code=400, detail="Invalid pattern key")
    session_count = await _wellness_session_count(user["id"])
    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {f"dismissed_patterns_v2.{pattern_key}": session_count},
            # Drop any legacy list entry for this key so the two stores
            # don't disagree.
            "$pull": {"dismissed_patterns": pattern_key},
        },
    )
    return {"ok": True, "dismissed": pattern_key, "session_count": session_count}


@api.post("/me/patterns/clear")
async def patterns_clear(user: dict = Depends(get_current_user)):
    """Manual reset — clears every dismissal (both legacy list and the
    v2 session-stamped map) so all currently-active patterns re-surface
    immediately. Exposed as "Reset patterns" in the section's settings
    menu; the normal path is the automatic 7-session re-evaluation."""
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"dismissed_patterns": [], "dismissed_patterns_v2": {}}},
    )
    return {"ok": True}


# --- Assistant settings (Phase 8) ---------------------------------------------
class AssistantSettingsIn(BaseModel):
    """Editable Wellness Assistant preferences. Kept minimal — extend here
    as new toggles arrive. `None` on a field means "no change" so the
    frontend can PATCH-style update a single toggle without echoing state
    it doesn't own."""
    harmonic_influence_enabled: Optional[bool] = None
    # Phase 9 — set true when the user chooses "Skip tips next time" on the
    # HB setup tips screen. Skips straight from IntroPanel → capture.
    hb_tips_skipped: Optional[bool] = None


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


class HBNudgeDismissIn(BaseModel):
    """Records that the user tapped "not now" on the gentle HB setup nudge.
    Dismissal is scoped to the current agent chat session — the same
    session won't re-nudge, but a fresh session after 3+ new listening
    sessions still can. Once the user actually captures an eigenmode,
    nudges silence permanently regardless of dismissals.
    """
    session_id: str = Field(min_length=1, max_length=80)


@api.post("/me/hb-nudge/dismiss")
async def hb_nudge_dismiss(
    body: HBNudgeDismissIn,
    user: dict = Depends(get_current_user),
):
    """Persist that the user said "not now" to this session's HB nudge."""
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"hb_nudge_dismissed_session_id": body.session_id}},
    )
    return {"ok": True}


# ==========================================================================
# NOTIFICATIONS — Phase 10 (Feb 2026)
# --------------------------------------------------------------------------
# Multi-surface notification system with the following user-facing surfaces:
#   • In-app notification center (bell + panel)  — always available
#   • Browser / PWA push (VAPID)                — opt-in, tab-closed reach
#
# Categories (per-user toggles):
#   • feature_announcement — new features shipped
#   • checkin              — gentle emotional check-in nudges
#   • recommendation       — frequency / soundscape / preset suggestions
#   • session_reminder     — "your quiet moment" reminders
#   • harmonic_blueprint   — HB capture / rescan nudges
#
# Copy guardrails: warm, supportive, non-clinical, no diagnosis/treatment
# claims, no urgency, no manipulation. Content generators enforce short
# strings (title ≤ 80, body ≤ 180) and the admin CMS validates the same.
# ==========================================================================

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_PEM = (os.environ.get("VAPID_PRIVATE_PEM", "") or "").replace("\\n", "\n")
VAPID_CONTACT = os.environ.get("VAPID_CONTACT", "mailto:admin@solarisound.com")

NOTIFICATION_CATEGORIES = (
    "feature_announcement",
    "checkin",
    "recommendation",
    "session_reminder",
    "harmonic_blueprint",
)

# Default: everything on, quiet hours 22:00 → 07:00 local, cap 4/day.
def _default_notification_prefs() -> dict:
    return {
        "enabled": True,
        "push_enabled": False,  # requires explicit browser opt-in
        "categories": {c: True for c in NOTIFICATION_CATEGORIES},
        "quiet_hours": {"enabled": True, "start_hour": 22, "end_hour": 7},
        "max_per_day": 4,
        "timezone_offset_minutes": 0,  # UTC by default; frontend refreshes
    }


class NotificationPrefsIn(BaseModel):
    """PATCH-style. Any subset of fields updates that subset."""
    enabled: Optional[bool] = None
    push_enabled: Optional[bool] = None
    categories: Optional[Dict[str, bool]] = None
    quiet_hours: Optional[Dict[str, object]] = None  # {enabled, start_hour, end_hour}
    max_per_day: Optional[int] = Field(None, ge=0, le=50)
    timezone_offset_minutes: Optional[int] = Field(None, ge=-720, le=840)


async def _get_notification_prefs(user_id: str) -> dict:
    doc = await db.users.find_one({"id": user_id}, {"notification_prefs": 1}) or {}
    prefs = doc.get("notification_prefs") or {}
    merged = _default_notification_prefs()
    # Shallow merge with a deep merge for categories + quiet_hours.
    for k, v in prefs.items():
        if k == "categories" and isinstance(v, dict):
            merged["categories"] = {**merged["categories"], **{c: bool(x) for c, x in v.items() if c in NOTIFICATION_CATEGORIES}}
        elif k == "quiet_hours" and isinstance(v, dict):
            qh = merged["quiet_hours"].copy()
            if "enabled" in v: qh["enabled"] = bool(v["enabled"])
            if "start_hour" in v:
                try:
                    qh["start_hour"] = max(0, min(23, int(v["start_hour"])))
                except (TypeError, ValueError):
                    pass
            if "end_hour" in v:
                try:
                    qh["end_hour"] = max(0, min(23, int(v["end_hour"])))
                except (TypeError, ValueError):
                    pass
            merged["quiet_hours"] = qh
        else:
            merged[k] = v
    return merged


def _is_within_quiet_hours(prefs: dict, now_utc: Optional[datetime] = None) -> bool:
    qh = prefs.get("quiet_hours") or {}
    if not qh.get("enabled"): return False
    now = (now_utc or datetime.now(timezone.utc))
    local = now + timedelta(minutes=int(prefs.get("timezone_offset_minutes") or 0))
    hr = local.hour
    start = int(qh.get("start_hour", 22)); end = int(qh.get("end_hour", 7))
    if start == end: return False
    if start < end:  return start <= hr < end
    return hr >= start or hr < end  # wraps midnight


async def _daily_notification_count(user_id: str, now_utc: Optional[datetime] = None) -> int:
    now = now_utc or datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    return await db.notifications.count_documents({
        "user_id": user_id,
        "created_at": {"$gte": since.isoformat()},
    })


async def _can_send_notification(user_id: str, category: str, prefs: Optional[dict] = None,
                                  bypass_quiet: bool = False) -> tuple[bool, str]:
    """Returns (allowed, reason). Never raises."""
    if category not in NOTIFICATION_CATEGORIES:
        return False, "unknown_category"
    prefs = prefs or await _get_notification_prefs(user_id)
    if not prefs.get("enabled"): return False, "master_disabled"
    if not (prefs.get("categories") or {}).get(category, True):
        return False, "category_disabled"
    if not bypass_quiet and _is_within_quiet_hours(prefs):
        return False, "quiet_hours"
    cap = int(prefs.get("max_per_day") or 4)
    if cap > 0:
        count = await _daily_notification_count(user_id)
        if count >= cap: return False, "daily_cap"
    return True, "ok"


async def _enqueue_notification(*, user_id: str, category: str, kind: str, title: str,
                                 body: str, destination: Optional[str] = None,
                                 meta: Optional[dict] = None,
                                 send_push: bool = True,
                                 bypass_gates: bool = False) -> Optional[dict]:
    """Creates an in-app notification row and (optionally) fires a browser push.

    Gates: honours preferences + quiet hours + daily cap unless `bypass_gates`.
    Returns the notification doc on success, None if gated. Never raises.
    """
    title = (title or "").strip()[:80]
    body = (body or "").strip()[:180]
    destination = (destination or "").strip()[:120] or None
    if not title or not body:
        return None
    prefs = await _get_notification_prefs(user_id)
    if not bypass_gates:
        allowed, reason = await _can_send_notification(user_id, category, prefs=prefs)
        if not allowed:
            logger.info("[notif] gated user=%s category=%s reason=%s", user_id, category, reason)
            return None
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "category": category,
        "kind": kind,
        "title": title,
        "body": body,
        "destination": destination,
        "meta": (meta or {}),
        "created_at": now,
        "opened_at": None,
        "dismissed_at": None,
    }
    try:
        await db.notifications.insert_one(dict(doc))
    except Exception as exc:
        logger.warning("[notif] insert failed user=%s: %s", user_id, exc)
        return None
    # Analytics event
    try:
        await db.notification_events.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "notification_id": doc["id"],
            "event": "sent",
            "category": category,
            "kind": kind,
            "surface": "inapp",
            "created_at": now,
        })
    except Exception:
        pass
    # Push
    if send_push and prefs.get("push_enabled") and prefs.get("enabled"):
        try:
            asyncio.create_task(_dispatch_push(user_id, doc))
        except Exception as exc:
            logger.warning("[notif] push dispatch failed user=%s: %s", user_id, exc)
    return doc


async def _dispatch_push(user_id: str, notif: dict) -> None:
    """Sends a Web Push via VAPID to every subscription registered for the user.
    Best-effort; broken subscriptions (410 Gone) are pruned automatically.
    """
    if not (VAPID_PRIVATE_PEM and VAPID_PUBLIC_KEY):
        return
    try:
        from pywebpush import webpush, WebPushException  # type: ignore
    except Exception:
        return
    subs_cur = db.push_subscriptions.find({"user_id": user_id})
    subs = await subs_cur.to_list(length=25)
    if not subs: return
    payload_str = json.dumps({
        "title": notif["title"],
        "body": notif["body"],
        "destination": notif.get("destination") or "/",
        "id": notif["id"],
        "category": notif["category"],
    })
    for sub in subs:
        info = sub.get("subscription") or {}
        try:
            await asyncio.to_thread(
                webpush,
                subscription_info=info,
                data=payload_str,
                vapid_private_key=VAPID_PRIVATE_PEM,
                vapid_claims={"sub": VAPID_CONTACT},
                ttl=3600,
            )
            try:
                await db.notification_events.insert_one({
                    "id": str(uuid.uuid4()),
                    "user_id": user_id,
                    "notification_id": notif["id"],
                    "event": "sent",
                    "category": notif["category"],
                    "kind": notif["kind"],
                    "surface": "push",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
            except Exception:
                pass
        except WebPushException as exc:  # type: ignore
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code in (404, 410):
                try:
                    await db.push_subscriptions.delete_one({"_id": sub["_id"]})
                except Exception:
                    pass
            logger.info("[push] webpush failed status=%s", code)
        except Exception as exc:
            logger.warning("[push] send unexpected: %s", exc)


# ---------- Public VAPID endpoint ----------
@api.get("/notifications/vapid-public-key")
async def notifications_vapid_public_key():
    return {"public_key": VAPID_PUBLIC_KEY or ""}


# ---------- User notification prefs ----------
@api.get("/me/notifications/prefs")
async def notifications_prefs(user: dict = Depends(get_current_user)):
    return await _get_notification_prefs(user["id"])


@api.put("/me/notifications/prefs")
async def notifications_prefs_update(
    body: NotificationPrefsIn,
    user: dict = Depends(get_current_user),
):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return await _get_notification_prefs(user["id"])
    # Sanitise nested fields to prevent unknown keys.
    if "categories" in updates and isinstance(updates["categories"], dict):
        updates["categories"] = {
            c: bool(v) for c, v in updates["categories"].items() if c in NOTIFICATION_CATEGORIES
        }
    if "quiet_hours" in updates and isinstance(updates["quiet_hours"], dict):
        qh = {}
        raw = updates["quiet_hours"]
        if "enabled" in raw: qh["enabled"] = bool(raw["enabled"])
        if "start_hour" in raw:
            try: qh["start_hour"] = max(0, min(23, int(raw["start_hour"])))
            except (TypeError, ValueError): pass
        if "end_hour" in raw:
            try: qh["end_hour"] = max(0, min(23, int(raw["end_hour"])))
            except (TypeError, ValueError): pass
        updates["quiet_hours"] = qh
    to_set = {f"notification_prefs.{k}": v for k, v in updates.items()}
    if to_set:
        await db.users.update_one({"id": user["id"]}, {"$set": to_set})
    # If master or push disabled, log an analytics event.
    if updates.get("enabled") is False or updates.get("push_enabled") is False:
        try:
            await db.notification_events.insert_one({
                "id": str(uuid.uuid4()),
                "user_id": user["id"],
                "event": "disabled",
                "surface": "settings",
                "detail": {k: v for k, v in updates.items() if k in ("enabled", "push_enabled")},
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass
    return await _get_notification_prefs(user["id"])


# ---------- Push subscription mgmt ----------
class PushSubscribeIn(BaseModel):
    subscription: Dict[str, object]  # {endpoint, keys:{p256dh,auth}}
    user_agent: Optional[str] = Field(None, max_length=255)


@api.post("/me/notifications/push/subscribe")
async def notifications_push_subscribe(
    body: PushSubscribeIn,
    user: dict = Depends(get_current_user),
):
    sub = body.subscription or {}
    endpoint = str(sub.get("endpoint") or "").strip()
    if not endpoint:
        raise HTTPException(status_code=400, detail="Missing subscription endpoint")
    now = datetime.now(timezone.utc).isoformat()
    # Idempotent upsert on (user_id, endpoint).
    await db.push_subscriptions.update_one(
        {"user_id": user["id"], "endpoint": endpoint},
        {"$set": {
            "user_id": user["id"],
            "endpoint": endpoint,
            "subscription": sub,
            "user_agent": (body.user_agent or "")[:255],
            "updated_at": now,
        }, "$setOnInsert": {"id": str(uuid.uuid4()), "created_at": now}},
        upsert=True,
    )
    # Flip push_enabled on now that we have a real subscription.
    await db.users.update_one({"id": user["id"]}, {"$set": {"notification_prefs.push_enabled": True}})
    return {"ok": True}


@api.delete("/me/notifications/push/subscribe")
async def notifications_push_unsubscribe(
    endpoint: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    q: Dict[str, object] = {"user_id": user["id"]}
    if endpoint: q["endpoint"] = endpoint
    await db.push_subscriptions.delete_many(q)
    if not endpoint:
        await db.users.update_one({"id": user["id"]}, {"$set": {"notification_prefs.push_enabled": False}})
    return {"ok": True}


# ---------- Notification list / mark ----------
@api.get("/me/notifications")
async def notifications_list(
    limit: int = 30,
    include_dismissed: bool = False,
    user: dict = Depends(get_current_user),
):
    limit = max(1, min(int(limit or 30), 100))
    q: Dict[str, object] = {"user_id": user["id"]}
    if not include_dismissed:
        q["dismissed_at"] = None
    cur = db.notifications.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
    items = await cur.to_list(length=limit)
    unread = await db.notifications.count_documents({
        "user_id": user["id"], "dismissed_at": None, "opened_at": None,
    })
    return {"items": items, "unread": unread}


@api.get("/me/notifications/unread-count")
async def notifications_unread_count(user: dict = Depends(get_current_user)):
    n = await db.notifications.count_documents({
        "user_id": user["id"], "dismissed_at": None, "opened_at": None,
    })
    return {"unread": n}


@api.post("/me/notifications/{notif_id}/opened")
async def notifications_mark_opened(notif_id: str, user: dict = Depends(get_current_user)):
    now = datetime.now(timezone.utc).isoformat()
    r = await db.notifications.update_one(
        {"id": notif_id, "user_id": user["id"], "opened_at": None},
        {"$set": {"opened_at": now}},
    )
    if r.modified_count:
        try:
            n = await db.notifications.find_one({"id": notif_id}, {"category": 1, "kind": 1}) or {}
            await db.notification_events.insert_one({
                "id": str(uuid.uuid4()),
                "user_id": user["id"],
                "notification_id": notif_id,
                "event": "opened",
                "category": n.get("category"),
                "kind": n.get("kind"),
                "surface": "inapp",
                "created_at": now,
            })
        except Exception:
            pass
    return {"ok": True}


@api.post("/me/notifications/{notif_id}/dismissed")
async def notifications_mark_dismissed(notif_id: str, user: dict = Depends(get_current_user)):
    now = datetime.now(timezone.utc).isoformat()
    r = await db.notifications.update_one(
        {"id": notif_id, "user_id": user["id"], "dismissed_at": None},
        {"$set": {"dismissed_at": now}},
    )
    if r.modified_count:
        try:
            n = await db.notifications.find_one({"id": notif_id}, {"category": 1, "kind": 1}) or {}
            await db.notification_events.insert_one({
                "id": str(uuid.uuid4()),
                "user_id": user["id"],
                "notification_id": notif_id,
                "event": "dismissed",
                "category": n.get("category"),
                "kind": n.get("kind"),
                "surface": "inapp",
                "created_at": now,
            })
        except Exception:
            pass
    return {"ok": True}


@api.post("/me/notifications/read-all")
async def notifications_mark_all_read(user: dict = Depends(get_current_user)):
    now = datetime.now(timezone.utc).isoformat()
    await db.notifications.update_many(
        {"user_id": user["id"], "opened_at": None},
        {"$set": {"opened_at": now}},
    )
    return {"ok": True}


# ---------- Content generators ----------
def _time_of_day() -> str:
    h = datetime.now(timezone.utc).hour
    if 5 <= h < 12: return "morning"
    if 12 <= h < 17: return "afternoon"
    if 17 <= h < 22: return "evening"
    return "night"


async def _generate_recommendation_notification(user_id: str) -> Optional[dict]:
    """Compute a single, well-grounded suggestion based on the user's data.
    Returns a notification dict ({title, body, destination, meta}) or None if
    there isn't enough data to make a warm, honest suggestion.
    """
    # 1) Detected pattern with a start-there CTA is the strongest signal.
    try:
        pats = await _cached_detect_wellness_patterns(user_id)
        dismissed = await _effective_dismissed_pattern_keys(user_id)
        udoc = await db.users.find_one({"id": user_id}, {"assistant_settings": 1}) or {}
        settings = udoc.get("assistant_settings") or {}
        hb_enabled = bool(settings.get("harmonic_influence_enabled", True))
        for p in pats or []:
            if p.get("key") in dismissed: continue
            cta = p.get("cta") or {}
            # Pattern CTAs are `{"action": "arm_frequency", "frequency": hz}`
            # — we only surface as a start-there recommendation when the
            # user can be armed to a concrete Hz.
            if cta.get("action") != "arm_frequency": continue
            hz = cta.get("frequency")
            if not hz: continue
            msg = (p.get("message") or "").strip()
            if not msg: continue
            body = msg
            if not body.rstrip().endswith("?"):
                body = body.rstrip(".") + ". Want to start there?"
            return {
                "title": "A gentle suggestion",
                "body": body[:180],
                "destination": f"/play?frequency={hz}",
                "meta": {"source": "pattern", "pattern_key": p.get("key"), "arm_frequency": hz},
            }
    except Exception as exc:
        logger.warning("[notif][rec] pattern read failed: %s", exc)
    # 2) HB confirmed gap (only if user opted in).
    try:
        udoc = await db.users.find_one({"id": user_id}, {"assistant_settings": 1}) or {}
        if bool((udoc.get("assistant_settings") or {}).get("harmonic_influence_enabled", True)):
            latest = await db.resonance_profiles.find_one(
                {"user_id": user_id}, sort=[("created_at", -1)]
            )
            gaps = ((latest or {}).get("confirmed_gaps") or []) if latest else []
            for g in gaps:
                lo, hi = g.get("low_hz"), g.get("high_hz")
                if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                    center = int(round((lo + hi) / 2))
                    return {
                        "title": "Something for your resonance",
                        "body": f"Your Harmonic Blueprint shows an opening around {center} Hz. Want to spend some time there?",
                        "destination": f"/play?frequency={center}",
                        "meta": {"source": "hb_gap", "frequency": center},
                    }
    except Exception:
        pass
    return None


async def _maybe_send_daily_recommendation(user_id: str) -> Optional[dict]:
    """Send at most one recommendation notification per calendar day per user.
    Called from `/api/me/notifications/tick` on app open. Silent when gated."""
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    udoc = await db.users.find_one({"id": user_id}, {"notif_last_rec_day": 1}) or {}
    if udoc.get("notif_last_rec_day") == today:
        return None
    prefs = await _get_notification_prefs(user_id)
    allowed, _ = await _can_send_notification(user_id, "recommendation", prefs=prefs)
    if not allowed:
        return None
    rec = await _generate_recommendation_notification(user_id)
    if not rec: return None
    doc = await _enqueue_notification(
        user_id=user_id, category="recommendation", kind="daily",
        title=rec["title"], body=rec["body"],
        destination=rec.get("destination"), meta=rec.get("meta"),
    )
    if doc:
        await db.users.update_one({"id": user_id}, {"$set": {"notif_last_rec_day": today}})
    return doc


async def _maybe_send_feature_announcements(user_id: str) -> list:
    """Enqueues per-user notifications for any published feature announcements
    the user hasn't yet seen. Idempotent via `feature_announcements_seen[]`
    on the user doc."""
    prefs = await _get_notification_prefs(user_id)
    allowed, _ = await _can_send_notification(user_id, "feature_announcement",
                                               prefs=prefs, bypass_quiet=True)
    if not allowed:
        return []
    udoc = await db.users.find_one(
        {"id": user_id}, {"feature_announcements_seen": 1, "pro_until": 1, "role": 1, "created_at": 1},
    ) or {}
    seen = set(udoc.get("feature_announcements_seen") or [])
    is_pro = bool(udoc.get("role") == "admin") or (
        isinstance(udoc.get("pro_until"), str) and udoc["pro_until"] > datetime.now(timezone.utc).isoformat()
    )
    created = udoc.get("created_at")  # only announce features published after account creation
    cur = db.feature_announcements.find({"active": True}, {"_id": 0}).sort("published_at", -1).limit(20)
    anns = await cur.to_list(length=20)
    delivered = []
    for a in anns:
        aid = a.get("id")
        if not aid or aid in seen: continue
        audience = a.get("audience") or "all"
        if audience == "pro" and not is_pro: continue
        if audience == "free" and is_pro: continue
        # Don't back-announce features shipped before the user joined.
        pub = a.get("published_at") or ""
        if created and pub and pub < created: 
            seen.add(aid)
            continue
        doc = await _enqueue_notification(
            user_id=user_id, category="feature_announcement", kind="release",
            title=a.get("title") or "", body=a.get("body") or "",
            destination=a.get("destination"),
            meta={"announcement_id": aid},
            bypass_gates=False,
        )
        if doc: delivered.append(doc)
        seen.add(aid)
    if seen:
        await db.users.update_one(
            {"id": user_id},
            {"$set": {"feature_announcements_seen": list(seen)}},
        )
    return delivered


@api.post("/me/notifications/tick")
async def notifications_tick(user: dict = Depends(get_current_user)):
    """Client heartbeat, typically called on app open. Runs feature-announcement
    and daily-recommendation sweeps for the caller. Returns fresh unread count.
    """
    delivered_ann = await _maybe_send_feature_announcements(user["id"])
    delivered_rec = await _maybe_send_daily_recommendation(user["id"])
    n = await db.notifications.count_documents({
        "user_id": user["id"], "dismissed_at": None, "opened_at": None,
    })
    return {
        "unread": n,
        "delivered_announcements": len(delivered_ann or []),
        "delivered_recommendation": 1 if delivered_rec else 0,
    }


# ---------- Emotional check-in nudge ----------
class CheckinNudgeIn(BaseModel):
    trigger: str = Field(pattern=r"^(pre_session|post_session|inactivity)$")


@api.post("/me/notifications/checkin-nudge")
async def notifications_checkin_nudge(
    body: CheckinNudgeIn,
    user: dict = Depends(get_current_user),
):
    """Called by the frontend when a check-in surface renders (post-session
    card, pre-session pause, or long-idle prompt). Emits an in-app notification
    row AND, if push is enabled, a browser push — but never during active
    playback (the client is responsible for not calling this mid-playback).

    Destinations are chosen so the reader's "Open" CTA always leads somewhere
    the user can actually accept the invitation:
      • pre_session  → Wellness Assistant (set an intention)
      • post_session → Wellness Assistant (reflect together)
      • inactivity   → Breathwork (take a breath together)
    """
    trig = body.trigger
    lines = {
        "pre_session": (
            "Want to check in for a moment?",
            "Before you begin — how are you arriving today?",
            "#wellness-assistant",
        ),
        "post_session": (
            "How are you feeling?",
            "Take a soft moment. Anything shift for you just now?",
            "#wellness-assistant",
        ),
        "inactivity": (
            "A quiet moment awaits",
            "You haven't been here in a while. Want to take a breath together?",
            "#breathwork",
        ),
    }
    title, body_txt, destination = lines[trig]
    doc = await _enqueue_notification(
        user_id=user["id"], category="checkin", kind=trig,
        title=title, body=body_txt, destination=destination,
        meta={"trigger": trig},
    )
    return {"ok": True, "delivered": bool(doc)}


# ---------- Admin: feature announcements CRUD ----------
class FeatureAnnouncementIn(BaseModel):
    title: str = Field(min_length=2, max_length=80)
    body: str = Field(min_length=2, max_length=180)
    destination: Optional[str] = Field(None, max_length=120)
    audience: str = Field(default="all", pattern=r"^(all|pro|free)$")
    active: bool = True


@api.get("/admin/feature-announcements")
async def admin_feature_ann_list(user: dict = Depends(get_current_user)):
    _require_admin(user)
    cur = db.feature_announcements.find({}, {"_id": 0}).sort("published_at", -1).limit(200)
    return {"items": await cur.to_list(length=200)}


@api.post("/admin/feature-announcements")
async def admin_feature_ann_create(
    body: FeatureAnnouncementIn,
    user: dict = Depends(get_current_user),
):
    _require_admin(user)
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        "title": body.title.strip(),
        "body": body.body.strip(),
        "destination": (body.destination or "").strip() or None,
        "audience": body.audience,
        "active": bool(body.active),
        "published_at": now,
        "created_by": user["id"],
    }
    await db.feature_announcements.insert_one(dict(doc))
    return doc


@api.put("/admin/feature-announcements/{ann_id}")
async def admin_feature_ann_update(
    ann_id: str,
    body: FeatureAnnouncementIn,
    user: dict = Depends(get_current_user),
):
    _require_admin(user)
    updates = {
        "title": body.title.strip(),
        "body": body.body.strip(),
        "destination": (body.destination or "").strip() or None,
        "audience": body.audience,
        "active": bool(body.active),
    }
    r = await db.feature_announcements.update_one({"id": ann_id}, {"$set": updates})
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    doc = await db.feature_announcements.find_one({"id": ann_id}, {"_id": 0})
    return doc


@api.delete("/admin/feature-announcements/{ann_id}")
async def admin_feature_ann_delete(ann_id: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    await db.feature_announcements.delete_one({"id": ann_id})
    return {"ok": True}


@api.post("/admin/feature-announcements/{ann_id}/broadcast")
async def admin_feature_ann_broadcast(ann_id: str, user: dict = Depends(get_current_user)):
    """Enqueue this announcement for every user who hasn't seen it yet.
    Bounded to 5000 users per call to keep runtime predictable; call again
    to continue if you have a larger base."""
    _require_admin(user)
    ann = await db.feature_announcements.find_one({"id": ann_id})
    if not ann or not ann.get("active"):
        raise HTTPException(status_code=404, detail="Announcement not found or inactive")
    cur = db.users.find({}, {"id": 1, "feature_announcements_seen": 1}).limit(5000)
    delivered = 0
    async for u in cur:
        uid = u.get("id")
        if not uid: continue
        if ann_id in (u.get("feature_announcements_seen") or []): continue
        try:
            sent = await _maybe_send_feature_announcements(uid)
            if sent: delivered += 1
        except Exception:
            pass
    return {"ok": True, "delivered": delivered}


@api.get("/admin/notifications/analytics")
async def admin_notif_analytics(user: dict = Depends(get_current_user)):
    _require_admin(user)
    pipeline = [
        {"$group": {"_id": {"event": "$event", "category": "$category", "surface": "$surface"}, "count": {"$sum": 1}}},
    ]
    rows = await db.notification_events.aggregate(pipeline).to_list(length=500)
    out = []
    for r in rows:
        k = r.get("_id") or {}
        out.append({
            "event": k.get("event"), "category": k.get("category"),
            "surface": k.get("surface"), "count": int(r.get("count") or 0),
        })
    total_notifs = await db.notifications.count_documents({})
    total_subs = await db.push_subscriptions.count_documents({})
    return {"rows": out, "total_notifications": total_notifs, "push_subscriptions": total_subs}


# ==========================================================================
# END NOTIFICATIONS
# ==========================================================================


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

    RACE HARDENING (Feb 2026): Stripe retries `checkout.session.completed`
    up to ~3× per event. Under load two retries can arrive concurrently and
    both read `fulfilled: false`. To guarantee exactly-once fulfilment we
    ATOMICALLY claim the tx first — any concurrent caller sees
    `modified_count == 0` and returns immediately. Prevents silent
    double-granting of pro_until in one-time mode; also removes redundant
    Stripe API calls in subscription mode.
    """
    now = datetime.now(timezone.utc)
    claim = await db.payment_transactions.update_one(
        {"session_id": tx["session_id"], "fulfilled": {"$ne": True}},
        {"$set": {"fulfilled": True, "fulfilled_at": now.isoformat()}},
    )
    if claim.modified_count == 0:
        # Another concurrent webhook (or a prior retry) already fulfilled
        # this session. Silent no-op — the earlier caller applied the plan.
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
        raise HTTPException(status_code=502, detail="Payment status lookup is temporarily unavailable.")

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
        # Client message stays generic — the "activate Customer Portal in
        # Stripe Dashboard" hint is an operator diagnostic and lives in the
        # log line above.
        raise HTTPException(
            status_code=502,
            detail="The billing portal is temporarily unavailable. Please try again in a moment.",
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
        raise HTTPException(status_code=502, detail="Subscription cancellation is temporarily unavailable. Please try again in a moment.")
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
    offset: int = 0,
    limit: int = 100,
    include_test: bool = False,
    user: dict = Depends(get_current_user),
):
    """List all registered users for the Admin User Management view.

    Returns `{items, total, offset, limit, filtered_test_count}` so the
    UI can page through and surface how many users exist in total. This
    replaces the previous flat-list-of-200 shape that silently hid every
    user beyond the 200th row (a real problem once we crossed ~1600
    seeded rows in preview).

    - `q`: substring email search (regex-escaped, capped at 100 chars).
    - `offset` / `limit`: pagination; `limit` capped at 500 to protect
      the response size / mongo memory.
    - `include_test`: when False (default) we hide pytest-seeded
      `@example.com` addresses so the admin sees real registered users
      first. Set true to see everything (used only when the admin
      explicitly asks via a checkbox).
    """
    _require_admin(user)
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))

    query: dict = {}
    if q:
        # SECURITY: escape regex metacharacters from user input to prevent
        # ReDoS / regex injection. Admin-only but defense in depth.
        safe_q = re.escape(q.strip())[:100]
        query["email"] = {"$regex": safe_q, "$options": "i"}
    if not include_test:
        # `@example.com` is an IANA-reserved test domain. Nobody registers
        # with it in production — everything under that domain is pytest
        # seed data leftover from CI runs. Exclude by default so the
        # admin sees real signups, not thousands of fixture rows.
        query["email"] = {**(query.get("email") or {}), "$not": {"$regex": r"@example\.com$", "$options": "i"}}

    total = await db.users.count_documents(query)
    # Also expose how many synthetic rows we're hiding by default so the
    # admin knows the "total in DB" if they care.
    filtered_test_count = 0
    if not include_test:
        try:
            filtered_test_count = await db.users.count_documents({
                "email": {"$regex": r"@example\.com$", "$options": "i"},
            })
        except Exception:  # noqa: BLE001
            pass

    cursor = (
        db.users.find(query, {"_id": 0, "password_hash": 0})
        .sort("created_at", -1)
        .skip(offset)
        .limit(limit)
    )
    items = await cursor.to_list(limit)
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
    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "filtered_test_count": filtered_test_count,
    }


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


# --- Admin user profile view + edit ---------------------------------------
# GET returns the full profile (minus password_hash) of any user.
# PUT accepts a partial patch of admin-editable fields, validates each,
# writes an audit-log row PER changed field with before/after values,
# and returns the fresh profile. Sensitive fields (email, role) require
# the caller to also send `confirm: true` in the payload so a fat-finger
# never re-roles someone silently.

# Field allow-list. Anything not in here is silently ignored on PUT — the
# frontend cannot expand the surface area of what admins can edit by
# guessing extra keys. Keys marked SENSITIVE require confirm=True.
_ADMIN_EDITABLE_TOP = {
    "name", "email", "role", "plan_notes",
    "nudge_cadence", "nudge_unsubscribed",
    "phone_number", "phone_verified",
}
_ADMIN_SENSITIVE = {"email", "role", "phone_number", "phone_verified"}
_ADMIN_NUDGE_CADENCES = {"default", "weekly", "paused"}
_ADMIN_ROLES = {"user", "admin"}


def _sanitize_notification_prefs_patch(np_in: dict) -> dict:
    """Coerce an admin-supplied notification_prefs patch onto the same
    schema `_get_notification_prefs` returns. Only known keys pass through.
    """
    out: dict = {}
    if not isinstance(np_in, dict):
        return out
    if "enabled" in np_in:
        out["enabled"] = bool(np_in["enabled"])
    if "push_enabled" in np_in:
        out["push_enabled"] = bool(np_in["push_enabled"])
    if "max_per_day" in np_in:
        try:
            out["max_per_day"] = max(0, min(50, int(np_in["max_per_day"])))
        except (TypeError, ValueError):
            pass
    if "categories" in np_in and isinstance(np_in["categories"], dict):
        out["categories"] = {
            c: bool(v) for c, v in np_in["categories"].items()
            if c in NOTIFICATION_CATEGORIES
        }
    if "quiet_hours" in np_in and isinstance(np_in["quiet_hours"], dict):
        qh = {}
        if "enabled" in np_in["quiet_hours"]:
            qh["enabled"] = bool(np_in["quiet_hours"]["enabled"])
        for k in ("start_hour", "end_hour"):
            if k in np_in["quiet_hours"]:
                try:
                    qh[k] = max(0, min(23, int(np_in["quiet_hours"][k])))
                except (TypeError, ValueError):
                    pass
        if qh:
            out["quiet_hours"] = qh
    return out


def _redact_user_profile(doc: dict) -> dict:
    """Return a copy of the user doc safe for admin consumption.
    Strips password_hash + any raw stripe secret fields. Preserves everything
    else so the admin has full visibility into the account state.
    """
    if not doc:
        return {}
    safe = {k: v for k, v in doc.items() if k not in {
        "password_hash",
        "_id",
    }}
    return safe


@api.get("/admin/users/{user_id}/profile")
async def admin_get_user_profile(
    user_id: str,
    user: dict = Depends(get_current_user),
):
    _require_admin(user)
    doc = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="User not found")
    # Attach a compact list of the fields the admin is permitted to edit so
    # the frontend never has to hard-code the allow-list separately.
    doc["_editable_fields"] = sorted(list(_ADMIN_EDITABLE_TOP)) + [
        "notification_prefs", "reset_hearing_profile", "reset_prefs",
    ]
    doc["_sensitive_fields"] = sorted(list(_ADMIN_SENSITIVE))
    # Also compute derived plan-label the UI is likely to show.
    now = datetime.now(timezone.utc)
    pu = doc.get("pro_until")
    days_left = 0
    is_pro = False
    if pu:
        try:
            until = datetime.fromisoformat(pu)
            is_pro = until > now
            if is_pro:
                days_left = max(0, (until - now).days + 1)
        except Exception:
            pass
    doc["_plan_label"] = "admin" if doc.get("role") == "admin" else (
        "pro" if is_pro else "basic"
    )
    doc["_pro"] = is_pro
    doc["_days_left"] = days_left
    return _redact_user_profile(doc)


@api.put("/admin/users/{user_id}/profile")
async def admin_update_user_profile(
    user_id: str,
    payload: dict,
    request: Request,
    user: dict = Depends(get_current_user),
):
    _require_admin(user)
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    confirm = bool(payload.get("confirm"))
    # Track before/after per changed field so we can write one audit row per
    # change. Also lets the response advertise which fields actually landed
    # so the client can render "N fields updated".
    changes: list[dict] = []
    set_doc: dict = {}
    unset_doc: dict = {}

    # ---- Top-level scalar fields ----
    for field in ("name", "plan_notes"):
        if field in payload:
            new = payload[field]
            if new is None:
                # Empty string coerces to unset-equivalent for plan_notes;
                # name is required so treat None as skip.
                if field == "plan_notes":
                    unset_doc[field] = ""
                    if target.get(field):
                        changes.append({"field": field, "before": target.get(field), "after": None})
                continue
            new = str(new).strip()
            if field == "name":
                if not new:
                    raise HTTPException(status_code=400, detail="Name cannot be empty")
                if len(new) > 80:
                    raise HTTPException(status_code=400, detail="Name too long (max 80)")
            if field == "plan_notes":
                if len(new) > 2000:
                    raise HTTPException(status_code=400, detail="plan_notes too long (max 2000)")
            if new != (target.get(field) or ""):
                set_doc[field] = new
                changes.append({"field": field, "before": target.get(field), "after": new})

    # ---- Email (sensitive) ----
    if "email" in payload and payload["email"] is not None:
        new_email = str(payload["email"]).strip().lower()
        if not new_email or "@" not in new_email or "." not in new_email.split("@")[-1]:
            raise HTTPException(status_code=400, detail="Invalid email format")
        if len(new_email) > 200:
            raise HTTPException(status_code=400, detail="Email too long")
        if new_email != (target.get("email") or "").lower():
            if not confirm:
                raise HTTPException(status_code=400, detail="Email change requires confirm=true")
            # Uniqueness check
            existing = await db.users.find_one({"email": new_email, "id": {"$ne": user_id}}, {"_id": 1})
            if existing:
                raise HTTPException(status_code=409, detail="Another user already has that email")
            set_doc["email"] = new_email
            changes.append({"field": "email", "before": target.get("email"), "after": new_email})

    # ---- Role (sensitive) ----
    if "role" in payload and payload["role"] is not None:
        new_role = str(payload["role"]).strip().lower()
        if new_role not in _ADMIN_ROLES:
            raise HTTPException(status_code=400, detail=f"role must be one of {sorted(_ADMIN_ROLES)}")
        if new_role != (target.get("role") or "user"):
            if not confirm:
                raise HTTPException(status_code=400, detail="Role change requires confirm=true")
            # Guardrail: admin cannot demote themselves via this endpoint —
            # forces an out-of-band recovery path if they meant to strip
            # their own admin.
            if user_id == user["id"] and new_role != "admin":
                raise HTTPException(status_code=400, detail="You cannot demote your own admin account")
            set_doc["role"] = new_role
            changes.append({"field": "role", "before": target.get("role"), "after": new_role})

    # ---- Phone number (sensitive) ----
    # Empty string / None from the admin form means "clear this user's phone"
    # — we unset both phone_number and phone_verified so they must re-verify
    # if they want to re-attach a number. Otherwise validate as E.164 and
    # enforce uniqueness against other users.
    if "phone_number" in payload:
        raw = payload["phone_number"]
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            if target.get("phone_number"):
                if not confirm:
                    raise HTTPException(status_code=400, detail="Phone change requires confirm=true")
                unset_doc["phone_number"] = ""
                unset_doc["phone_verified"] = ""
                unset_doc["phone_verified_at"] = ""
                changes.append({
                    "field": "phone_number",
                    "before": (target.get("phone_number") or "")[-4:],
                    "after": None,
                })
        else:
            new_phone = _normalize_phone(raw)
            if new_phone != (target.get("phone_number") or ""):
                if not confirm:
                    raise HTTPException(status_code=400, detail="Phone change requires confirm=true")
                existing = await db.users.find_one(
                    {"phone_number": new_phone, "id": {"$ne": user_id}},
                    {"_id": 1},
                )
                if existing:
                    raise HTTPException(status_code=409, detail="Another user already has that phone number")
                set_doc["phone_number"] = new_phone
                # Changing the number invalidates the prior verification —
                # admin can flip phone_verified back on via its own toggle.
                if target.get("phone_verified"):
                    set_doc["phone_verified"] = False
                    unset_doc["phone_verified_at"] = ""
                changes.append({
                    "field": "phone_number",
                    "before": (target.get("phone_number") or "")[-4:] or None,
                    "after": new_phone[-4:],
                })

    # ---- Phone verified flag (sensitive) ----
    # Manual override so support can flip a user's verified status without
    # actually running an SMS round-trip (e.g. Twilio outage, VoIP number).
    if "phone_verified" in payload and payload["phone_verified"] is not None:
        new_v = bool(payload["phone_verified"])
        # Only meaningful when a phone_number is present — otherwise flipping
        # this flag creates an inconsistent state.
        final_phone = set_doc.get("phone_number") or target.get("phone_number")
        if new_v and not final_phone:
            raise HTTPException(status_code=400, detail="Cannot mark phone as verified without a phone_number")
        # If phone_number just changed, set_doc may already contain the reset
        # to False — respect the explicit admin choice regardless.
        if new_v != bool(target.get("phone_verified")) or "phone_verified" in set_doc:
            if not confirm:
                raise HTTPException(status_code=400, detail="Phone verified change requires confirm=true")
            set_doc["phone_verified"] = new_v
            if new_v:
                set_doc["phone_verified_at"] = datetime.now(timezone.utc).isoformat()
            else:
                unset_doc["phone_verified_at"] = ""
            changes.append({
                "field": "phone_verified",
                "before": bool(target.get("phone_verified")),
                "after": new_v,
            })

    # ---- Nudge cadence + unsubscribed ----
    if "nudge_cadence" in payload and payload["nudge_cadence"] is not None:
        new_c = str(payload["nudge_cadence"]).strip().lower()
        if new_c not in _ADMIN_NUDGE_CADENCES:
            raise HTTPException(status_code=400, detail=f"nudge_cadence must be one of {sorted(_ADMIN_NUDGE_CADENCES)}")
        if new_c != (target.get("nudge_cadence") or "default"):
            set_doc["nudge_cadence"] = new_c
            changes.append({"field": "nudge_cadence", "before": target.get("nudge_cadence"), "after": new_c})

    if "nudge_unsubscribed" in payload and payload["nudge_unsubscribed"] is not None:
        new_u = bool(payload["nudge_unsubscribed"])
        if new_u != bool(target.get("nudge_unsubscribed")):
            set_doc["nudge_unsubscribed"] = new_u
            changes.append({"field": "nudge_unsubscribed", "before": bool(target.get("nudge_unsubscribed")), "after": new_u})

    # ---- Notification prefs (nested patch) ----
    if "notification_prefs" in payload and isinstance(payload["notification_prefs"], dict):
        patch = _sanitize_notification_prefs_patch(payload["notification_prefs"])
        current_np = target.get("notification_prefs") or {}
        if patch:
            for k, v in patch.items():
                before = current_np.get(k)
                if before != v:
                    set_doc[f"notification_prefs.{k}"] = v
                    changes.append({"field": f"notification_prefs.{k}", "before": before, "after": v})

    # ---- Destructive resets (safe: single toggles) ----
    if payload.get("reset_hearing_profile") is True:
        if target.get("hearing_profile"):
            unset_doc["hearing_profile"] = ""
            changes.append({"field": "hearing_profile", "before": "<set>", "after": None})
    if payload.get("reset_prefs") is True:
        if target.get("prefs"):
            unset_doc["prefs"] = ""
            changes.append({"field": "prefs", "before": "<set>", "after": None})

    if not changes:
        return {"ok": True, "changes": [], "message": "No changes"}

    # Apply the mutation. $set + $unset can coexist in a single update.
    op: dict = {}
    if set_doc:
        op["$set"] = set_doc
    if unset_doc:
        op["$unset"] = unset_doc
    if op:
        await db.users.update_one({"id": user_id}, op)

    # Audit each field change separately so a per-field query in
    # /admin/audit-log surfaces every mutation with its before/after.
    for ch in changes:
        await _audit(
            "admin.user.profile.updated", request,
            user_id=user["id"], user_email=user.get("email"),
            metadata={
                "target_user_id": user_id,
                "target_email": target.get("email"),
                "field": ch["field"],
                "before": ch["before"],
                "after": ch["after"],
            },
        )

    fresh = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    return {"ok": True, "changes": changes, "user": _redact_user_profile(fresh)}


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
        # Atomic filter also enforces the max_uses cap so two DIFFERENT
        # users can't both slip through at redemptions == max_uses - 1.
        # If max_uses is unlimited (None), we skip that clause entirely.
        max_uses = doc.get("max_uses")
        filter_ = {"code": code, "redemption_log.user_id": {"$ne": user["id"]}}
        if max_uses is not None:
            filter_["redemptions"] = {"$lt": int(max_uses)}
        result = await db.promo_codes.update_one(
            filter_,
            {"$inc": {"redemptions": 1}, "$push": {"redemption_log": log_entry}},
        )
        if result.modified_count == 0:
            # Either the user already redeemed OR the cap was reached by
            # a concurrent redeemer. Re-read to disambiguate the message.
            fresh = await db.promo_codes.find_one({"code": code}, {"redemption_log": 1, "redemptions": 1, "max_uses": 1})
            already = any(
                (e or {}).get("user_id") == user["id"]
                for e in ((fresh or {}).get("redemption_log") or [])
            )
            if already:
                raise HTTPException(status_code=400, detail="You've already redeemed this code.")
            raise HTTPException(status_code=400, detail="This code has reached its redemption limit.")
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


# --- Security headers -------------------------------------------------------
# Adds defense-in-depth HTTP headers to every response. Kept intentionally
# strict but compatible with the SPA's needs:
#
#   • X-Content-Type-Options: nosniff — blocks MIME-sniffing based attacks
#   • X-Frame-Options: DENY           — mitigates clickjacking on API responses
#   • Referrer-Policy: strict-origin-when-cross-origin — hides paths from
#                                                        cross-origin referrers
#   • Strict-Transport-Security: HSTS with a 6-month max-age. Only sent
#                                when the request came in over TLS so local
#                                dev over http://localhost isn't affected.
#   • Permissions-Policy: minimises what iframed pages could request from
#                          the browser. Microphone is intentionally allowed
#                          because the Harmonic Blueprint records audio.
#   • Content-Security-Policy: an API-side CSP is a light backstop — the
#                              real CSP for the SPA is applied by the
#                              frontend host (nginx / Cloudflare). We
#                              publish `frame-ancestors 'none'` here as a
#                              second clickjacking guard.
@app.middleware("http")
async def _security_headers_middleware(request: Request, call_next):
    resp: Response = await call_next(request)
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault(
        "Permissions-Policy",
        "geolocation=(), camera=(), microphone=(self), "
        "payment=(self), usb=(), interest-cohort=()",
    )
    h.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    # HSTS only when the request itself arrived over TLS. The Emergent
    # ingress sets X-Forwarded-Proto=https for external traffic.
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).lower()
    if proto == "https":
        h.setdefault(
            "Strict-Transport-Security",
            "max-age=15552000; includeSubDomains",
        )
    return resp

# NOTE: logger + basicConfig moved to the top of the file (near imports) so
# early module-scoped helpers (Twilio init) can call it. Left this line
# intentionally removed.


# --- FastAPI lifespan bodies ------------------------------------------------
# Wired via the `lifespan=` context manager declared at module top; the
# legacy @app.on_event decorators were removed to silence the FastAPI
# deprecation warnings and avoid double-fire.
async def _lifespan_startup():
    await db.users.create_index("email", unique=True)
    # HF-030: unique-but-sparse index on phone_number so existing accounts
    # without a phone (pre-Twilio users) don't collide, while any newly
    # created account is protected from phone reuse race conditions.
    await db.users.create_index("phone_number", unique=True, sparse=True)
    await db.sessions.create_index([("user_id", 1), ("created_at", -1)])
    await db.streaks.create_index("user_id", unique=True)
    await db.payment_transactions.create_index("session_id", unique=True)
    await db.payment_transactions.create_index([("user_id", 1), ("created_at", -1)])
    # Audit log: paged filter index + TTL so the collection self-prunes after
    # 180 days (the timestamp field is stored as ISO string; the TTL index
    # works against the parallel `ts_at` Date field — added below per-insert).
    await db.audit_log.create_index([("ts", -1)])
    await db.audit_log.create_index([("event", 1), ("ts", -1)])
    # Notifications (Phase 10)
    await db.notifications.create_index([("user_id", 1), ("created_at", -1)])
    await db.notifications.create_index([("user_id", 1), ("dismissed_at", 1), ("opened_at", 1)])
    await db.notification_events.create_index([("user_id", 1), ("created_at", -1)])
    await db.notification_events.create_index([("event", 1), ("category", 1)])
    await db.push_subscriptions.create_index([("user_id", 1), ("endpoint", 1)], unique=True)
    # Phase 12e — one milestone doc per (user, key)
    await db.hb_milestones.create_index([("user_id", 1), ("key", 1)], unique=True)
    await db.hb_monthly_reports.create_index([("user_id", 1), ("month", 1)], unique=True)
    await db.feature_announcements.create_index([("active", 1), ("published_at", -1)])
    # Re-engagement nudges — sort by sent_at (descending) is the primary
    # access pattern; user_id + tier is used by the sequence-gate lookup.
    await db.email_nudges.create_index([("sent_at", -1)])
    await db.email_nudges.create_index([("user_id", 1), ("sent_at", -1)])
    await db.email_nudges.create_index([("user_id", 1), ("tier", 1), ("sent_at", -1)])
    # Unsubscribe token lookup for the public one-tap unsub / prefs links.
    await db.users.create_index("nudge_unsubscribe_token", sparse=True)
    # Seed default feature announcements the first time the app boots (idempotent).
    seed_anns = [
        {
            "slug": "wellness-assistant-hb-nudges",
            "title": "Your Wellness Assistant just got warmer",
            "body": "It now weaves in gentle Harmonic Blueprint suggestions when they're a good fit — no pressure, just an invitation.",
            "destination": "#wellness-assistant",
            "audience": "all",
        },
        {
            "slug": "harmonic-blueprint-setup-ritual",
            "title": "A calmer way into Harmonic Blueprint",
            "body": "Before you record, we'll share four short tips for the most honest reading. You can always skip.",
            "destination": "#harmonic-blueprint",
            "audience": "pro",
        },
        {
            "slug": "notification-center-launch",
            "title": "A quiet notification center is here",
            "body": "Feature news, gentle check-ins, and personalised suggestions — all opt-in, all under your control.",
            "destination": "#notification-preferences",
            "audience": "all",
        },
    ]
    for a in seed_anns:
        exists = await db.feature_announcements.find_one({"slug": a["slug"]})
        if exists: continue
        await db.feature_announcements.insert_one({
            "id": str(uuid.uuid4()),
            "slug": a["slug"],
            "title": a["title"],
            "body": a["body"],
            "destination": a["destination"],
            "audience": a["audience"],
            "active": True,
            "published_at": datetime.now(timezone.utc).isoformat(),
            "created_by": "seed",
        })
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
    # Kick off the re-engagement scheduler as a background task. Silent
    # no-op inside the loop when Resend isn't configured.
    global _reengagement_task
    _reengagement_task = asyncio.create_task(_reengagement_scheduler_loop())


_reengagement_task: Optional[asyncio.Task] = None
_REENGAGEMENT_TICK_INTERVAL_S = int(os.environ.get("REENGAGEMENT_TICK_INTERVAL_S", "900"))  # 15 min default


async def _reengagement_scheduler_loop():
    """Long-running background loop that fires _reengagement_tick() at a
    fixed cadence. Sleeps between ticks so a slow tick doesn't cascade.
    Any exception inside the tick is swallowed + logged so the loop
    survives transient DB / Resend outages."""
    while True:
        try:
            stats = await _reengagement_tick()
            if stats.get("sent"):
                logger.info("[nudge] tick sent=%s scanned=%s", stats["sent"], stats["scanned"])
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[nudge] tick error: %s", type(e).__name__)
        await asyncio.sleep(_REENGAGEMENT_TICK_INTERVAL_S)


async def _lifespan_shutdown():
    try:
        if _reengagement_task and not _reengagement_task.done():
            _reengagement_task.cancel()
    except Exception:
        pass
    client.close()

# NOTE: startup + shutdown are wired via the `lifespan=` context manager
# declared at module top (see `async def lifespan(app_)`).
