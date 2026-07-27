"""Backend tests for Harmonic Blueprint Phase 4 — Account summary, history,
gap editing, drift computation, LLM context injection, legacy migration.
"""
from __future__ import annotations

import os
import uuid
import asyncio
import requests
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from _creds import ADMIN_EMAIL, ADMIN_PASSWORD

BANNED = ["diagnos", "symptom", "condition", "illness", "disease",
          "treatment", "therap", "patient", "medical"]


def _load_base_url():
    if os.environ.get("REACT_APP_BACKEND_URL"):
        return os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
    from dotenv import dotenv_values
    env = dotenv_values(Path(__file__).resolve().parent.parent.parent / "frontend" / ".env")
    return (env.get("REACT_APP_BACKEND_URL") or "").rstrip("/")


BASE_URL = _load_base_url()
API = f"{BASE_URL}/api"


# --- Fixtures ---------------------------------------------------------------

@pytest.fixture(scope="module")
def mongo_col():
    from pymongo import MongoClient
    from dotenv import dotenv_values
    env = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
    mongo_url = env.get("MONGO_URL") or os.environ.get("MONGO_URL")
    db_name = env.get("DB_NAME") or os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        pytest.skip("Mongo env unavailable")
    return MongoClient(mongo_url)[db_name]


@pytest.fixture(scope="module")
def admin_session(mongo_col):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
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


@pytest.fixture
def free_session():
    s = requests.Session()
    email = f"TEST_hb4_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{API}/auth/register", json={
        "email": email, "password": "TestPass123!", "name": "HB4 Free"
    })
    assert r.status_code in (200, 201), r.text
    tok = r.json().get("access_token") or r.json().get("token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    me = s.get(f"{API}/auth/me").json()
    s.user_id = me.get("id") or me.get("user", {}).get("id")
    return s


def _make_bands(sub=-20.0, low=-18.0, lowmid=-16.0, mid=-14.0, uppermid=-12.0, presence=-10.0):
    return [
        {"key": "sub", "label": "Sub", "lo": 20, "hi": 60, "db": sub},
        {"key": "low", "label": "Low", "lo": 60, "hi": 250, "db": low},
        {"key": "lowmid", "label": "Low-mid", "lo": 250, "hi": 500, "db": lowmid},
        {"key": "mid", "label": "Mid", "lo": 500, "hi": 2000, "db": mid},
        {"key": "uppermid", "label": "Upper-mid", "lo": 2000, "hi": 4000, "db": uppermid},
        {"key": "presence", "label": "Presence", "lo": 4000, "hi": 8000, "db": presence},
    ]


def _seed_profile(mongo_col, uid, *, is_eigenmode=False, bands=None, confirmed_gaps=None,
                  created_at=None, dominant=None):
    doc = {
        "id": f"prof-{uuid.uuid4().hex[:8]}",
        "user_id": uid,
        "created_at": (created_at or datetime.now(timezone.utc)).isoformat(),
        "duration": 12.0,
        "bands": bands or _make_bands(),
        "dominant": dominant or [{"hz": 220.0, "db": -10.0}],
        "confirmed_gaps": confirmed_gaps or [],
        "is_eigenmode": is_eigenmode,
    }
    mongo_col["resonance_profiles"].insert_one(doc)
    return doc


# --- 1. Summary endpoint ---------------------------------------------------

def test_summary_admin_pro_with_seeded_profile(admin_session, mongo_col):
    uid = admin_session.user_id
    mongo_col["resonance_profiles"].delete_many({"user_id": uid})
    mongo_col["harmonic_journeys"].delete_many({"user_id": uid})
    eigen = _seed_profile(
        mongo_col, uid, is_eigenmode=True,
        confirmed_gaps=[{"key": "mid", "label": "Mid", "lo": 500, "hi": 2000,
                         "direction": "quieter", "delta_db": -5.0}],
    )
    # journey
    mongo_col["harmonic_journeys"].insert_one({
        "id": f"j-{uuid.uuid4().hex[:6]}",
        "user_id": uid,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "name": "Your Eigenmode Journey",
        "tracks": [{"id": "t1", "name": "Track One", "rationale": "..."}],
        "tier": "pro",
        "total_duration_seconds": 900,
    })
    r = admin_session.get(f"{API}/harmonic-blueprint/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_pro"] is True
    assert body["eigenmode"] is not None
    assert body["eigenmode"]["id"] == eigen["id"]
    assert body["latest_profile"] is not None
    assert body["latest_profile"]["id"] == eigen["id"]
    # No drift because latest == eigen
    assert body["current_drift"] == []
    assert len(body["confirmed_gaps"]) == 1
    assert body["latest_journey"] is not None


def test_summary_free_user_empty(free_session):
    r = free_session.get(f"{API}/harmonic-blueprint/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["is_pro"] is False
    assert body["eigenmode"] is None
    assert body["latest_profile"] is None
    assert body["current_drift"] == []
    assert body["confirmed_gaps"] == []
    assert body["latest_journey"] is None


# --- 2. History endpoint ----------------------------------------------------

def test_history_admin_pro(admin_session, mongo_col):
    uid = admin_session.user_id
    mongo_col["resonance_profiles"].delete_many({"user_id": uid})
    base = datetime.now(timezone.utc)
    _seed_profile(mongo_col, uid, is_eigenmode=True, bands=_make_bands(),
                  created_at=base - timedelta(days=10))
    _seed_profile(mongo_col, uid, is_eigenmode=False,
                  bands=_make_bands(mid=-20.0),  # 6dB drift on mid
                  confirmed_gaps=[{"key": "mid"}],
                  created_at=base - timedelta(days=5))
    _seed_profile(mongo_col, uid, is_eigenmode=False,
                  bands=_make_bands(presence=-18.0),  # 8dB drift on presence
                  created_at=base)
    r = admin_session.get(f"{API}/harmonic-blueprint/history")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "eigenmode_id" in body
    assert body["eigenmode_id"] is not None
    hist = body["history"]
    assert len(hist) == 3
    # Sorted oldest → newest
    assert hist[0]["is_eigenmode"] is True
    assert hist[-1]["is_eigenmode"] is False
    # Verify shape
    for entry in hist:
        assert set(["id", "created_at", "is_eigenmode", "duration",
                    "band_deltas", "drift_score", "confirmed_gap_count"]).issubset(entry.keys())
        assert isinstance(entry["band_deltas"], dict)
        assert len(entry["band_deltas"]) == 6  # 6 bands
    # Middle entry (mid drift -6) has confirmed_gap_count=1
    assert hist[1]["confirmed_gap_count"] == 1
    # Eigen entry has drift_score == 0
    assert hist[0]["drift_score"] == 0.0


def test_history_free_returns_402(free_session):
    r = free_session.get(f"{API}/harmonic-blueprint/history")
    assert r.status_code == 402


# --- 3. PATCH gaps ---------------------------------------------------------

def test_patch_gaps_replaces_atomically(admin_session, mongo_col):
    uid = admin_session.user_id
    mongo_col["resonance_profiles"].delete_many({"user_id": uid})
    prof = _seed_profile(mongo_col, uid, is_eigenmode=True, confirmed_gaps=[
        {"key": "mid", "label": "Mid"},
        {"key": "presence", "label": "Presence"},
    ])
    new_gaps = [{"key": "sub", "label": "Sub", "lo": 20, "hi": 60,
                 "direction": "quieter", "delta_db": -4.5}]
    r = admin_session.patch(
        f"{API}/harmonic-blueprint/profile/{prof['id']}/gaps",
        json={"confirmed_gaps": new_gaps},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["confirmed_gaps"] == new_gaps
    # Verify persisted via subsequent GET
    r2 = admin_session.get(f"{API}/harmonic-blueprint/profile")
    assert r2.status_code == 200
    prof_data = r2.json().get("profile") or {}
    assert prof_data.get("confirmed_gaps") == new_gaps


def test_patch_gaps_404_nonexistent(admin_session):
    r = admin_session.patch(
        f"{API}/harmonic-blueprint/profile/nonexistent-id-xxx/gaps",
        json={"confirmed_gaps": []},
    )
    assert r.status_code == 404


def test_patch_gaps_402_for_free(free_session, mongo_col):
    # Seed a profile owned by the free user so 402 fires before 404
    _seed_profile(mongo_col, free_session.user_id, is_eigenmode=True)
    r = free_session.patch(
        f"{API}/harmonic-blueprint/profile/anything/gaps",
        json={"confirmed_gaps": []},
    )
    assert r.status_code == 402


# --- 4. Drift computation (unit level) --------------------------------------

def test_compute_drift_ranks_and_caps():
    from server import _compute_drift, _BAND_LABELS
    eigen = {"bands": _make_bands()}
    latest = {"bands": _make_bands(
        sub=-30.0,       # -10 dB (quieter)
        low=-18.0,        # 0 dB - filtered out
        lowmid=-11.0,     # +5 dB (louder)
        mid=-6.0,         # +8 dB (louder)
        uppermid=-16.0,   # -4 dB (quieter, exactly at threshold)
        presence=-4.0,    # +6 dB (louder)
    )}
    findings = _compute_drift(latest, eigen, min_delta_db=4.0)
    # Should include 5 of them (all except 'low' which has 0 delta)
    assert len(findings) <= 5
    # Sorted by magnitude desc
    magnitudes = [f["magnitude"] for f in findings]
    assert magnitudes == sorted(magnitudes, reverse=True)
    # First finding is 'sub' with magnitude 10
    assert findings[0]["key"] == "sub"
    assert findings[0]["magnitude"] == 10.0
    assert findings[0]["direction"] == "quieter"
    assert findings[0]["delta_db"] == -10.0
    assert findings[0]["label"] == _BAND_LABELS["sub"]
    # Louder direction
    louder = [f for f in findings if f["direction"] == "louder"]
    assert len(louder) >= 2
    for f in louder:
        assert f["delta_db"] > 0
    # Under threshold band 'low' excluded
    assert "low" not in [f["key"] for f in findings]


def test_compute_drift_empty_inputs():
    from server import _compute_drift
    assert _compute_drift(None, None) == []
    assert _compute_drift({}, {"bands": _make_bands()}) == []


# --- 5. LLM context helper -------------------------------------------------

def test_harmonic_context_for_llm_admin(admin_session, mongo_col):
    from server import _harmonic_context_for_llm
    uid = admin_session.user_id
    mongo_col["resonance_profiles"].delete_many({"user_id": uid})
    _seed_profile(mongo_col, uid, is_eigenmode=True,
                  confirmed_gaps=[{"key": "mid", "label": "Mid", "lo": 500,
                                   "hi": 2000, "direction": "quieter"}])
    txt = asyncio.get_event_loop().run_until_complete(
        _harmonic_context_for_llm(uid)
    ) if False else asyncio.new_event_loop().run_until_complete(
        _harmonic_context_for_llm(uid)
    )
    assert "HARMONIC_BLUEPRINT" in txt
    assert "eigenmode bands" in txt
    assert "confirmed resonance points" in txt.lower() or "confirmed resonance points" in txt
    # Non-diagnostic language
    lower = txt.lower()
    for banned in BANNED:
        assert banned not in lower, f"banned '{banned}' in llm context: {txt}"


def test_harmonic_context_for_llm_empty_for_no_eigenmode():
    from server import _harmonic_context_for_llm
    # Use a fake user id that has no profiles
    fake_uid = f"nobody-{uuid.uuid4().hex}"
    txt = asyncio.new_event_loop().run_until_complete(
        _harmonic_context_for_llm(fake_uid)
    )
    assert txt == ""


# --- 6. Legacy migration ---------------------------------------------------

def test_legacy_migration_promotes_oldest(admin_session, mongo_col):
    """User has profiles but NONE flagged is_eigenmode: oldest gets promoted
    on first summary call (via _ensure_eigenmode)."""
    uid = admin_session.user_id
    mongo_col["resonance_profiles"].delete_many({"user_id": uid})
    base = datetime.now(timezone.utc)
    # Insert two profiles with NO is_eigenmode flag
    oldest = {
        "id": f"legacy-old-{uuid.uuid4().hex[:6]}",
        "user_id": uid,
        "created_at": (base - timedelta(days=30)).isoformat(),
        "duration": 10.0, "bands": _make_bands(),
        "confirmed_gaps": [],
    }
    newer = {
        "id": f"legacy-new-{uuid.uuid4().hex[:6]}",
        "user_id": uid,
        "created_at": (base - timedelta(days=1)).isoformat(),
        "duration": 10.0, "bands": _make_bands(),
        "confirmed_gaps": [],
    }
    mongo_col["resonance_profiles"].insert_many([oldest, newer])

    r = admin_session.get(f"{API}/harmonic-blueprint/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["eigenmode"] is not None
    assert body["eigenmode"]["id"] == oldest["id"]
    # Verify DB persisted the flag
    doc = mongo_col["resonance_profiles"].find_one({"id": oldest["id"]})
    assert doc.get("is_eigenmode") is True


# --- 7. LLM injection into agent_chat / ai-recommend ----------------------
def test_ai_recommend_still_works_with_hb_context(admin_session, mongo_col):
    """Smoke test: /me/ai-recommend still returns 200 (or Pro-related error)
    when the user has an eigenmode saved. Regression check that HB context
    injection doesn't crash the endpoint."""
    uid = admin_session.user_id
    mongo_col["resonance_profiles"].delete_many({"user_id": uid})
    _seed_profile(mongo_col, uid, is_eigenmode=True,
                  confirmed_gaps=[{"key": "presence", "label": "Presence"}])
    r = admin_session.post(f"{API}/me/ai-recommend", json={
        "feelings": "I feel tense and want to relax",
    })
    # Any non-5xx is acceptable (200 success, or 429 rate limit)
    assert r.status_code < 500, f"HB context injection appears to have broken endpoint: {r.status_code} {r.text}"
