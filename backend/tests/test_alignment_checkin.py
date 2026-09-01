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
