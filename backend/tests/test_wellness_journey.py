"""Wellness Journey (longitudinal session memory) — iter 49 tests.

Covers: POST /api/me/journey/log validation, pruning to 30, auth,
GET /api/me/journey ordering, and that /api/me/agent/chat still returns
200 with {message, suggestions} when the user has journey rows (proxy
for WELLNESS_JOURNEY prompt injection working without exploding)."""
from __future__ import annotations

import os
import uuid
import pytest
import requests
from dotenv import dotenv_values
from pathlib import Path

from _creds import ADMIN_EMAIL, ADMIN_PASSWORD

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL") or dotenv_values(
    Path(__file__).resolve().parent.parent.parent / "frontend" / ".env"
).get("REACT_APP_BACKEND_URL")
BASE_URL = BASE_URL.rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    tok = r.json().get("access_token") or r.json().get("token")
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    return s


@pytest.fixture(scope="module")
def fresh_user_session():
    """Register a fresh user so we can test pruning on a clean journey list."""
    s = requests.Session()
    email = f"TEST_journey_{uuid.uuid4().hex[:8]}@example.com"
    pw = "TestPass!234"
    r = s.post(f"{API}/auth/register", json={"email": email, "password": pw, "name": "Journey Tester"}, timeout=15)
    assert r.status_code in (200, 201), f"register: {r.status_code} {r.text}"
    tok = r.json().get("access_token") or r.json().get("token")
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    s._email = email  # type: ignore[attr-defined]
    return s


# --- Feature: POST /me/journey/log full payload ---
def test_journey_log_full_payload(admin_session):
    payload = {
        "frequency": 432,
        "waveform": "sine",
        "ambient": {"rain": 0.4, "ocean": 0.2, "forest": 0},
        "duration_planned_seconds": 600,
        "duration_actual_seconds": 540,
        "mood": "anxious tonight",
        "agent_initiated": True,
    }
    r = admin_session.post(f"{API}/me/journey/log", json=payload, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    entry = body.get("entry") or {}
    assert entry.get("frequency") == 432
    assert entry.get("time_of_day") in {"morning", "afternoon", "evening", "night"}
    amb = entry.get("ambient") or {}
    # Zero channel should be pruned
    assert "forest" not in amb
    assert amb.get("rain") == 0.4
    assert amb.get("ocean") == 0.2
    assert entry.get("agent_initiated") is True
    assert "_id" not in entry


# --- Feature: too-short rejection ---
def test_journey_log_too_short_rejected(admin_session):
    # Snapshot count before
    r0 = admin_session.get(f"{API}/me/journey", timeout=15)
    assert r0.status_code == 200
    before = len(r0.json().get("entries") or [])

    r = admin_session.post(
        f"{API}/me/journey/log",
        json={"duration_actual_seconds": 30},
        timeout=15,
    )
    assert r.status_code == 200
    assert r.json() == {"ok": False, "reason": "too_short"}

    r1 = admin_session.get(f"{API}/me/journey", timeout=15)
    after = len(r1.json().get("entries") or [])
    assert after == before, "too-short call should not persist a row"


# --- Feature: freeform (minimal) payload accepted ---
def test_journey_log_minimal_payload(admin_session):
    r = admin_session.post(
        f"{API}/me/journey/log",
        json={"duration_actual_seconds": 90},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    entry = body.get("entry") or {}
    assert entry.get("duration_actual_seconds") == 90
    assert entry.get("frequency") is None
    assert entry.get("time_of_day") in {"morning", "afternoon", "evening", "night"}


# --- Feature: auth required ---
def test_journey_log_requires_auth():
    r = requests.post(
        f"{API}/me/journey/log",
        json={"duration_actual_seconds": 120},
        timeout=15,
    )
    assert r.status_code in (401, 403), f"expected 401/403 got {r.status_code}"


def test_journey_list_requires_auth():
    r = requests.get(f"{API}/me/journey", timeout=15)
    assert r.status_code in (401, 403)


# --- Feature: prune to 30 & sorted newest-first ---
def test_journey_prune_to_30(fresh_user_session):
    # Insert 32 rows on a fresh user so we can assert the exact 30 boundary.
    for i in range(32):
        r = fresh_user_session.post(
            f"{API}/me/journey/log",
            json={
                "duration_actual_seconds": 60 + i,
                "frequency": 200 + i,
                "mood": f"TEST_seed_{i}",
            },
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True

    r = fresh_user_session.get(f"{API}/me/journey", timeout=15)
    assert r.status_code == 200
    entries = r.json().get("entries") or []
    assert len(entries) == 30, f"expected 30 after pruning, got {len(entries)}"

    # Newest first: created_at descending
    ts = [e.get("created_at") for e in entries]
    assert ts == sorted(ts, reverse=True), "entries must be newest-first"

    # The oldest 2 seeded (i=0,1) should be gone; the newest (i=31) present
    moods = {e.get("mood") for e in entries}
    assert "TEST_seed_31" in moods
    assert "TEST_seed_0" not in moods
    assert "TEST_seed_1" not in moods


# --- Feature: /me/agent/chat still returns valid shape when journey rows exist ---
def test_agent_chat_with_journey_rows(admin_session):
    # Admin already has journey rows from earlier tests.
    r = admin_session.post(
        f"{API}/me/agent/chat",
        json={"message": "feeling anxious tonight, what should I try?"},
        timeout=60,
    )
    # 429 rate limit is acceptable in a busy test env
    if r.status_code == 429:
        pytest.skip("agent chat rate-limited in this run")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "message" in body
    assert "suggestions" in body
    assert isinstance(body["suggestions"], list)
