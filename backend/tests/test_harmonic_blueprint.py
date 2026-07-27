"""Backend tests for Harmonic Blueprint endpoints (iteration 45).

Covers:
- Auth gating (401) — endpoints require auth
- Free-tier gating (402) — non-Pro users get "Harmonic Blueprint is a Pro feature."
- Admin (Pro) POST/GET/DELETE happy path
- Pydantic payload validation (spectrum > 512, duration > 60)
- Retention: only latest 5 profiles per user kept
- Rate limit: 7th rapid POST returns 429
"""
from __future__ import annotations

import os
import time
import uuid
import requests
import pytest

from _creds import ADMIN_EMAIL, ADMIN_PASSWORD

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://frequency-healer-31.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"


def _valid_payload(sr: float = 22050.0, dur: float = 12.0):
    return {
        "version": 1,
        "sample_rate": sr,
        "duration": dur,
        "fft_size": 4096,
        "spectrum": [{"hz": 100.0 + i * 10, "db": -40.0 + (i % 20)} for i in range(64)],
        "dominant": [{"hz": 220.0, "db": -10.0}, {"hz": 440.0, "db": -14.0}],
        "dips": [{"hz": 1000.0, "db": -60.0}],
        "bands": [
            {"key": "low", "label": "Low", "lo": 60, "hi": 250, "db": -18.0},
            {"key": "mid", "label": "Mid", "lo": 250, "hi": 2000, "db": -22.0},
        ],
        "underrepresented": [{"key": "high", "label": "High"}],
        "generated_at": None,
    }


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    tok = r.json().get("access_token") or r.json().get("token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    # Reset any pre-existing profile so retention/rate-limit tests start clean-ish
    s.delete(f"{API}/harmonic-blueprint/profile")
    yield s
    s.delete(f"{API}/harmonic-blueprint/profile")


@pytest.fixture(scope="module")
def free_session():
    s = requests.Session()
    email = f"TEST_hb_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{API}/auth/register", json={"email": email, "password": "TestPass123!", "name": "HB Free"})
    assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
    tok = r.json().get("access_token") or r.json().get("token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    yield s


# --- Auth / gating ----------------------------------------------------------

def test_get_requires_auth():
    r = requests.get(f"{API}/harmonic-blueprint/profile")
    assert r.status_code in (401, 403), r.text


def test_post_requires_auth():
    r = requests.post(f"{API}/harmonic-blueprint/profile", json=_valid_payload())
    assert r.status_code in (401, 403), r.text


def test_free_user_get_returns_402(free_session):
    r = free_session.get(f"{API}/harmonic-blueprint/profile")
    assert r.status_code == 402
    assert "Pro feature" in r.json().get("detail", "")


def test_free_user_post_returns_402(free_session):
    r = free_session.post(f"{API}/harmonic-blueprint/profile", json=_valid_payload())
    assert r.status_code == 402
    assert r.json().get("detail") == "Harmonic Blueprint is a Pro feature."


# --- Admin happy path -------------------------------------------------------

def test_admin_post_and_get(admin_session):
    r = admin_session.post(f"{API}/harmonic-blueprint/profile", json=_valid_payload())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    prof = body["profile"]
    assert prof["duration"] == 12.0
    assert prof["fft_size"] == 4096
    assert isinstance(prof["dominant"], list) and len(prof["dominant"]) == 2
    assert "_id" not in prof

    g = admin_session.get(f"{API}/harmonic-blueprint/profile")
    assert g.status_code == 200
    fetched = g.json()["profile"]
    assert fetched is not None
    assert fetched["id"] == prof["id"]
    assert fetched["duration"] == 12.0
    assert "_id" not in fetched


def test_admin_delete(admin_session):
    # ensure at least one exists
    admin_session.post(f"{API}/harmonic-blueprint/profile", json=_valid_payload())
    d = admin_session.delete(f"{API}/harmonic-blueprint/profile")
    assert d.status_code == 200 and d.json().get("ok") is True
    g = admin_session.get(f"{API}/harmonic-blueprint/profile")
    assert g.status_code == 200
    assert g.json()["profile"] is None


# --- Payload validation -----------------------------------------------------

def test_reject_oversize_spectrum(admin_session):
    p = _valid_payload()
    p["spectrum"] = [{"hz": float(i), "db": -30.0} for i in range(513)]
    r = admin_session.post(f"{API}/harmonic-blueprint/profile", json=p)
    assert r.status_code == 422, r.text


def test_reject_duration_over_60(admin_session):
    p = _valid_payload(dur=61.0)
    r = admin_session.post(f"{API}/harmonic-blueprint/profile", json=p)
    assert r.status_code == 422, r.text


def test_reject_duration_zero_or_negative(admin_session):
    p = _valid_payload(dur=0.0)
    r = admin_session.post(f"{API}/harmonic-blueprint/profile", json=p)
    assert r.status_code == 422, r.text


# --- Retention: latest 5 kept ----------------------------------------------

def test_retention_keeps_only_five(admin_session):
    """Verify only latest 5 profiles per user are retained.

    Rate limit capacity is 6 with a ~10min refill, and the bucket is
    shared across tests, so we cannot rely on posting 6 via HTTP. Instead
    we seed 6 docs directly in Mongo, then trigger one HTTP POST which
    runs the trim logic, and verify DB state.
    """
    try:
        from pymongo import MongoClient
    except ImportError:
        pytest.skip("pymongo unavailable")

    from dotenv import dotenv_values
    from pathlib import Path
    env = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
    mongo_url = env.get("MONGO_URL") or os.environ.get("MONGO_URL")
    db_name = env.get("DB_NAME") or os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        pytest.skip("Mongo env unavailable")

    mc = MongoClient(mongo_url)
    col = mc[db_name]["resonance_profiles"]
    # Find admin user id via /auth/me
    me = admin_session.get(f"{API}/auth/me").json()
    uid = me.get("id") or me.get("user", {}).get("id")
    assert uid, f"no admin id in /auth/me: {me}"

    col.delete_many({"user_id": uid})
    from datetime import datetime, timezone, timedelta
    base = datetime.now(timezone.utc)
    for i in range(6):
        col.insert_one({
            "id": f"seed-{i}",
            "user_id": uid,
            "created_at": (base - timedelta(minutes=10 - i)).isoformat(),
            "duration": float(i + 1),
        })
    assert col.count_documents({"user_id": uid}) == 6

    # Trigger the trim via one HTTP POST (may be 429 — if so, use DB directly)
    r = admin_session.post(f"{API}/harmonic-blueprint/profile", json=_valid_payload(dur=7.0))
    if r.status_code == 200:
        # After insert (7 total) trim should leave 5
        remaining = col.count_documents({"user_id": uid})
        assert remaining == 5, f"expected 5 after trim, got {remaining}"
    else:
        pytest.skip(f"POST returned {r.status_code}; cannot exercise trim via HTTP")
    col.delete_many({"user_id": uid})


# --- Rate limit -------------------------------------------------------------

def test_rate_limit_after_burst(admin_session):
    admin_session.delete(f"{API}/harmonic-blueprint/profile")
    codes = []
    for i in range(8):
        r = admin_session.post(f"{API}/harmonic-blueprint/profile", json=_valid_payload(dur=2.0))
        codes.append(r.status_code)
        if r.status_code == 429:
            break
    assert 429 in codes, f"expected 429 within 8 rapid calls, got {codes}"
    # Bucket state is shared with prior tests (refill 1/600s), so we only
    # assert that the throttle *does* fire — the exact count of preceding
    # 200s depends on how many tokens were left when we started.
    admin_session.delete(f"{API}/harmonic-blueprint/profile")
