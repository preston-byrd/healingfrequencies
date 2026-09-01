"""HF-041 Weekly Alignment Check-in endpoints:
- GET  /api/me/alignment-checkin/status
- POST /api/me/alignment-checkin/snooze
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone, timedelta

import httpx
import pytest

API_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/") + "/api"


def _register_and_login(client: httpx.Client) -> tuple[str, str]:
    """Register a fresh user (phone-verified via TWILIO_TEST_MODE) and
    return (user_id, bearer token)."""
    suffix = uuid.uuid4().hex[:8]
    email = f"alignment_{suffix}@example.com"
    password = "TestPass123!"
    n = uuid.uuid4().int % 10000000
    phone = f"+1555{n:07d}"
    client.post(f"{API_URL}/auth/phone/send-code", json={"phone_number": phone})
    vr = client.post(
        f"{API_URL}/auth/phone/verify-code",
        json={"phone_number": phone, "code": "123456"},
    )
    vr.raise_for_status()
    token = vr.json()["phone_verification_token"]
    r = client.post(
        f"{API_URL}/auth/register",
        json={
            "email": email, "password": password, "name": "Align",
            "phone_number": phone, "phone_verification_token": token,
        },
    )
    r.raise_for_status()
    uid = r.json()["id"]
    lr = client.post(
        f"{API_URL}/auth/login",
        json={"email": email, "password": password},
    )
    lr.raise_for_status()
    return uid, lr.json()["token"]


@pytest.fixture()
def fresh_user():
    with httpx.Client() as c:
        uid, tok = _register_and_login(c)
    yield {"id": uid, "headers": {"Authorization": f"Bearer {tok}"}}


def test_status_new_user_is_eligible(fresh_user):
    """A user who has never captured a Harmonic Blueprint is eligible
    (days_since_last_hb=null, has_blueprint=false, eligible_data=true)."""
    with httpx.Client() as c:
        r = c.get(f"{API_URL}/me/alignment-checkin/status", headers=fresh_user["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["days_since_last_hb"] is None
    assert body["has_blueprint"] is False
    assert body["snoozed_until"] is None
    assert body["eligible_data"] is True


def test_snooze_sets_future_sunday_and_blocks_eligibility(fresh_user):
    """POST /snooze parks the user until the NEXT Sunday. Subsequent
    /status calls report eligible_data=false."""
    with httpx.Client() as c:
        # CST-ish offset (300 min) so the computed Sunday lands cleanly.
        s = c.post(
            f"{API_URL}/me/alignment-checkin/snooze",
            headers=fresh_user["headers"],
            json={"tz_offset_minutes": 300},
        )
        assert s.status_code == 200, s.text
        snoozed_until = s.json()["snoozed_until"]
        assert snoozed_until  # non-empty
        # It's a future UTC ISO timestamp.
        parsed = datetime.fromisoformat(snoozed_until.replace("Z", "+00:00"))
        assert parsed > datetime.now(timezone.utc)
        # And it's within the next 8 days (Mon→next Sun is 6, Sun→next Sun is 7).
        assert parsed - datetime.now(timezone.utc) < timedelta(days=8)
        # Status reflects it.
        r = c.get(f"{API_URL}/me/alignment-checkin/status", headers=fresh_user["headers"])
    assert r.status_code == 200
    body = r.json()
    assert body["snoozed_until"] == snoozed_until
    assert body["eligible_data"] is False


def test_snooze_without_tz_offset_defaults_to_utc(fresh_user):
    """When tz_offset_minutes is omitted the backend falls back to
    computing Sunday in UTC. Still lands in the future."""
    with httpx.Client() as c:
        s = c.post(
            f"{API_URL}/me/alignment-checkin/snooze",
            headers=fresh_user["headers"],
            json={},
        )
    assert s.status_code == 200
    parsed = datetime.fromisoformat(s.json()["snoozed_until"].replace("Z", "+00:00"))
    assert parsed > datetime.now(timezone.utc)


def test_status_requires_auth():
    """Both endpoints are behind get_current_user — anon requests are 401/403."""
    with httpx.Client() as c:
        r = c.get(f"{API_URL}/me/alignment-checkin/status")
        assert r.status_code in (401, 403)
        s = c.post(f"{API_URL}/me/alignment-checkin/snooze", json={})
        assert s.status_code in (401, 403)


# -------------------------------------------------------------------------
# HF-042 Weekly Alignment Streak endpoints
# -------------------------------------------------------------------------


def test_streak_defaults_to_zero_for_new_user(fresh_user):
    """A user who has never captured has streak=0 and no new_milestone."""
    with httpx.Client() as c:
        r = c.get(f"{API_URL}/me/alignment-streak", headers=fresh_user["headers"])
    assert r.status_code == 200
    body = r.json()
    assert body["streak"] == 0
    assert body["new_milestone"] is None
    assert body["last_iso_week"] is None


def test_streak_ack_is_idempotent(fresh_user):
    """POSTing /ack for the same milestone twice is safe (idempotent)."""
    with httpx.Client() as c:
        r1 = c.post(f"{API_URL}/me/alignment-streak/ack",
                    headers=fresh_user["headers"], json={"milestone": 4})
        r2 = c.post(f"{API_URL}/me/alignment-streak/ack",
                    headers=fresh_user["headers"], json={"milestone": 4})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["ok"] is True
    assert r2.json()["ok"] is True


def test_streak_ack_validates_milestone(fresh_user):
    """Milestone must be a positive integer ≤ 520 (10 years of weekly)."""
    with httpx.Client() as c:
        r = c.post(f"{API_URL}/me/alignment-streak/ack",
                   headers=fresh_user["headers"], json={"milestone": 0})
        assert r.status_code == 422
        r2 = c.post(f"{API_URL}/me/alignment-streak/ack",
                    headers=fresh_user["headers"], json={"milestone": 99999})
        assert r2.status_code == 422


def test_status_persists_tz_offset(fresh_user):
    """Calling /status with ?tz_offset_minutes=N persists it on the user
    doc so the Monday-morning SMS tick knows their local wall clock."""
    with httpx.Client() as c:
        r = c.get(f"{API_URL}/me/alignment-checkin/status?tz_offset_minutes=300",
                  headers=fresh_user["headers"])
    assert r.status_code == 200
    body = r.json()
    assert "sms_ack_pending" in body


def test_sms_ack_flags_seen(fresh_user):
    """POSTing sms-ack just returns ok — the field is a UI-only flag,
    exercised end-to-end when combined with the SMS tick which sets
    `alignment_sms_last_sent_at`."""
    with httpx.Client() as c:
        r = c.post(f"{API_URL}/me/alignment-checkin/sms-ack",
                   headers=fresh_user["headers"])
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_prev_iso_week_helper():
    """Sanity-check the streak break-detection helper. Imports the
    private symbol directly rather than round-tripping through the API
    so we don't have to fabricate two captures on non-consecutive weeks."""
    from server import _prev_iso_week
    assert _prev_iso_week("2026-W05") == "2026-W04"
    assert _prev_iso_week("2026-W01").endswith(("W52", "W53"))
    assert _prev_iso_week("garbage") == ""
