"""Backend tests for Harmonic Blueprint Phase 3 — Eigenmode Journey.

Covers:
- POST /harmonic-blueprint/journey/generate for Pro (admin) with confirmed_gaps
- Rationale copy contains 'has been included' and gap band language
- Non-diagnostic language audit (no banned substrings)
- Gap-driven selection (sub → sub-targeted tracks; mid → mid-targeted)
- Free-tier gating (tier=free, 2 tracks, upgrade_prompt, full_track_count > 2)
- Fallback for user with no profile (uses _demo_gaps)
- Rate limiting (8/40min, 9th returns 429)
- GET /harmonic-blueprint/journey returns latest journey doc
- Retention: only latest 3 journeys kept
"""
from __future__ import annotations

import os
import uuid
import requests
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from _creds import ADMIN_EMAIL, ADMIN_PASSWORD

BANNED = ["diagnos", "symptom", "condition", "illness", "disease",
          "treatment", "therap", "patient", "medical"]

BAND_LABELS = ["grounding root", "warm depth", "chest resonance",
               "expressive core", "articulation range",
               "brightness and openness"]


def _load_base_url():
    if os.environ.get("REACT_APP_BACKEND_URL"):
        return os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
    try:
        from dotenv import dotenv_values
        env = dotenv_values(Path(__file__).resolve().parent.parent.parent / "frontend" / ".env")
        return (env.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
    except Exception:
        return ""


BASE_URL = _load_base_url()
API = f"{BASE_URL}/api"


def _valid_payload(dur: float = 12.0, confirmed_gaps=None, underrepresented=None):
    return {
        "version": 1,
        "sample_rate": 22050.0,
        "duration": dur,
        "fft_size": 4096,
        "spectrum": [{"hz": 100.0 + i * 10, "db": -40.0 + (i % 20)} for i in range(64)],
        "dominant": [{"hz": 220.0, "db": -10.0}],
        "dips": [{"hz": 1000.0, "db": -60.0}],
        "bands": [
            {"key": "low", "label": "Low", "lo": 60, "hi": 250, "db": -18.0},
        ],
        "underrepresented": underrepresented or [],
        "confirmed_gaps": confirmed_gaps or [],
        "generated_at": None,
    }


@pytest.fixture(scope="module")
def mongo_col():
    try:
        from pymongo import MongoClient
    except ImportError:
        pytest.skip("pymongo unavailable")
    from dotenv import dotenv_values
    env = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
    mongo_url = env.get("MONGO_URL") or os.environ.get("MONGO_URL")
    db_name = env.get("DB_NAME") or os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        pytest.skip("Mongo env unavailable")
    mc = MongoClient(mongo_url)
    return mc[db_name]


@pytest.fixture(scope="module")
def admin_session(mongo_col):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    tok = r.json().get("access_token") or r.json().get("token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    me = s.get(f"{API}/auth/me").json()
    uid = me.get("id") or me.get("user", {}).get("id")
    s.user_id = uid
    mongo_col["resonance_profiles"].delete_many({"user_id": uid})
    mongo_col["harmonic_journeys"].delete_many({"user_id": uid})
    yield s
    mongo_col["resonance_profiles"].delete_many({"user_id": uid})
    mongo_col["harmonic_journeys"].delete_many({"user_id": uid})


def _seed_profile(mongo_col, uid, confirmed_gaps):
    """Insert a profile doc directly (bypasses rate limits + eigenmode logic)."""
    doc = {
        "id": f"prof-{uuid.uuid4().hex[:8]}",
        "user_id": uid,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "duration": 10.0,
        "confirmed_gaps": confirmed_gaps,
        "is_eigenmode": True,
    }
    mongo_col["resonance_profiles"].insert_one(doc)
    return doc


# --- Pro user, saved profile with confirmed_gaps ---
def test_pro_generate_full_journey_with_rationale(admin_session, mongo_col):
    uid = admin_session.user_id
    mongo_col["resonance_profiles"].delete_many({"user_id": uid})
    mongo_col["harmonic_journeys"].delete_many({"user_id": uid})
    _seed_profile(mongo_col, uid, [
        {"key": "mid", "label": "Mid", "direction": "quieter", "delta_db": -6.0},
        {"key": "presence", "label": "Presence", "direction": "quieter", "delta_db": -5.0},
    ])

    r = admin_session.post(f"{API}/harmonic-blueprint/journey/generate")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tier"] == "pro"
    assert body["name"] == "Your Eigenmode Journey"
    assert body["upgrade_prompt"] is None
    assert isinstance(body["tracks"], list)
    assert len(body["tracks"]) >= 2
    assert body["full_track_count"] == len(body["tracks"])

    # Rationale copy checks
    for t in body["tracks"]:
        assert "rationale" in t
        assert "has been included" in t["rationale"]
        for banned in BANNED:
            assert banned not in t["rationale"].lower(), \
                f"banned '{banned}' in rationale: {t['rationale']}"
    # At least one rationale references gap band language
    combined = " ".join(t["rationale"] for t in body["tracks"])
    assert any(lbl in combined for lbl in BAND_LABELS), \
        f"no band label found in rationales: {combined}"


# --- Selection: sub gap → sub-targeting track present ---
def test_selection_targets_sub_band(admin_session, mongo_col):
    uid = admin_session.user_id
    mongo_col["resonance_profiles"].delete_many({"user_id": uid})
    _seed_profile(mongo_col, uid, [{"key": "sub", "label": "Sub", "direction": "quieter"}])
    r = admin_session.post(f"{API}/harmonic-blueprint/journey/generate")
    assert r.status_code == 200, r.text
    tracks = r.json()["tracks"]
    assert any("sub" in t.get("targets_bands", []) for t in tracks), \
        f"no sub-targeting track in: {[t['id'] for t in tracks]}"


def test_selection_targets_mid_band(admin_session, mongo_col):
    uid = admin_session.user_id
    mongo_col["resonance_profiles"].delete_many({"user_id": uid})
    _seed_profile(mongo_col, uid, [{"key": "mid", "label": "Mid", "direction": "quieter"}])
    r = admin_session.post(f"{API}/harmonic-blueprint/journey/generate")
    assert r.status_code == 200, r.text
    tracks = r.json()["tracks"]
    assert any("mid" in t.get("targets_bands", []) for t in tracks), \
        f"no mid-targeting track in: {[t['id'] for t in tracks]}"


# --- Free-tier gating ---
def test_free_tier_returns_preview(mongo_col):
    s = requests.Session()
    email = f"TEST_hb3_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{API}/auth/register", json={"email": email, "password": "TestPass123!", "name": "HB3 Free"})
    assert r.status_code in (200, 201), r.text
    tok = r.json().get("access_token") or r.json().get("token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    r = s.post(f"{API}/harmonic-blueprint/journey/generate")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tier"] == "free"
    assert len(body["tracks"]) == 2
    assert body["upgrade_prompt"] == "Unlock your full Eigenmode Journey with Pro."
    assert body["full_track_count"] > 2


# --- Pro fallback: user with no profile ---
def test_pro_no_profile_uses_demo_gaps(admin_session, mongo_col):
    uid = admin_session.user_id
    mongo_col["resonance_profiles"].delete_many({"user_id": uid})
    mongo_col["harmonic_journeys"].delete_many({"user_id": uid})
    r = admin_session.post(f"{API}/harmonic-blueprint/journey/generate")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tier"] == "pro"
    assert len(body["tracks"]) > 0
    # Demo gaps are sub + presence
    keys = {t.get("gap_key") for t in body["tracks"]}
    assert keys & {"sub", "presence"}, f"demo gap keys not present: {keys}"


# --- GET latest journey ---
def test_get_latest_journey(admin_session, mongo_col):
    uid = admin_session.user_id
    # Ensure at least one exists
    mongo_col["resonance_profiles"].delete_many({"user_id": uid})
    mongo_col["harmonic_journeys"].delete_many({"user_id": uid})
    gen = admin_session.post(f"{API}/harmonic-blueprint/journey/generate")
    assert gen.status_code == 200
    gen_id = gen.json()["id"]
    r = admin_session.get(f"{API}/harmonic-blueprint/journey")
    assert r.status_code == 200
    body = r.json()
    assert body["journey"] is not None
    assert body["journey"]["id"] == gen_id


def test_get_latest_journey_null_when_empty(admin_session, mongo_col):
    uid = admin_session.user_id
    mongo_col["harmonic_journeys"].delete_many({"user_id": uid})
    r = admin_session.get(f"{API}/harmonic-blueprint/journey")
    assert r.status_code == 200
    assert r.json().get("journey") is None


def test_get_journey_available_to_free_user():
    s = requests.Session()
    email = f"TEST_hb3g_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{API}/auth/register", json={"email": email, "password": "TestPass123!", "name": "HB3 GetFree"})
    assert r.status_code in (200, 201)
    tok = r.json().get("access_token") or r.json().get("token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    r = s.get(f"{API}/harmonic-blueprint/journey")
    assert r.status_code == 200


# --- Retention: only 3 latest kept ---
def test_retention_keeps_latest_3(admin_session, mongo_col):
    uid = admin_session.user_id
    mongo_col["harmonic_journeys"].delete_many({"user_id": uid})
    # Seed 4 old docs directly, then trigger one real generate → 5 → trim to 3
    base = datetime.now(timezone.utc)
    for i in range(4):
        mongo_col["harmonic_journeys"].insert_one({
            "id": f"seed-j-{i}-{uuid.uuid4().hex[:6]}",
            "user_id": uid,
            "created_at": (base - timedelta(days=10 - i)).isoformat(),
            "tracks": [],
            "tier": "pro",
        })
    r = admin_session.post(f"{API}/harmonic-blueprint/journey/generate")
    if r.status_code != 200:
        pytest.skip(f"generate rate-limited: {r.status_code}")
    remaining = list(mongo_col["harmonic_journeys"].find({"user_id": uid}))
    assert len(remaining) == 3, f"expected 3, got {len(remaining)}: {[d['id'] for d in remaining]}"


# --- Rate limit (9th consecutive call returns 429) ---
def test_rate_limit_after_capacity(mongo_col):
    """Use a fresh free user to isolate the rate-limit bucket per user+ip."""
    s = requests.Session()
    email = f"TEST_hb3rl_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{API}/auth/register", json={"email": email, "password": "TestPass123!", "name": "HB3 RL"})
    assert r.status_code in (200, 201)
    tok = r.json().get("access_token") or r.json().get("token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    codes = []
    for _ in range(9):
        rr = s.post(f"{API}/harmonic-blueprint/journey/generate")
        codes.append(rr.status_code)
    # First 8 should be 200, 9th should be 429
    assert codes[:8] == [200] * 8, f"expected 8x200, got {codes}"
    assert codes[8] == 429, f"expected 429 on 9th, got {codes[8]} (all: {codes})"
