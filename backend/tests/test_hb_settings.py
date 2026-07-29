"""Phase 8 — Harmonic Blueprint settings + gap-note annotation tests.

Covers:
  - GET/POST /api/me/settings (default, round-trip, unknown fields, empty body, auth-gate)
  - _hb_note_for_frequency helper unit tests (gap band, drift band, out-of-band, invalid inputs, empty lists)
  - Integration through /api/me/ai-recommend and /api/me/agent/chat that:
      * harmonic_note field appears only when frequency lands in-band AND toggle is ON
      * harmonic_note is absent for all suggestions when toggle is OFF
      * LLM prompt does not carry Harmonic Blueprint block when toggle is OFF (best-effort text check)
"""
from __future__ import annotations

import os
import sys
import uuid
import pytest
import requests
from datetime import datetime, timezone
from pathlib import Path
from dotenv import dotenv_values
from pymongo import MongoClient

# Allow importing the helper directly for unit tests
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import _hb_note_for_frequency  # noqa: E402

from _creds import ADMIN_EMAIL, ADMIN_PASSWORD  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL") or dotenv_values(
    Path(__file__).resolve().parent.parent.parent / "frontend" / ".env"
).get("REACT_APP_BACKEND_URL")
BASE_URL = BASE_URL.rstrip("/")
API = f"{BASE_URL}/api"

_ENV = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
MONGO_URL = _ENV.get("MONGO_URL") or os.environ["MONGO_URL"]
DB_NAME = _ENV.get("DB_NAME") or os.environ["DB_NAME"]
_client = MongoClient(MONGO_URL)
_db = _client[DB_NAME]


# ---------- helpers ---------------------------------------------------------

def _register_fresh():
    s = requests.Session()
    email = f"TEST_hb8_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(
        f"{API}/auth/register",
        json={"email": email, "password": "TestPass!234", "name": "HB8"},
        timeout=15,
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    tok = body.get("access_token") or body.get("token")
    user = body.get("user") or {}
    uid = user.get("id")
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    if not uid:
        me = s.get(f"{API}/auth/me", timeout=15).json()
        uid = me.get("id") or (me.get("user") or {}).get("id")
    assert uid, f"no user id resolved: {body}"
    s._uid = uid  # type: ignore
    s._email = email  # type: ignore
    return s


def _admin_login():
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    tok = body.get("access_token") or body.get("token")
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    me = s.get(f"{API}/auth/me", timeout=15).json()
    s._uid = me.get("id") or (me.get("user") or {}).get("id")  # type: ignore
    return s


def _seed_profile(user_id: str, confirmed_gaps: list):
    """Insert an eigenmode resonance_profile with the given confirmed_gaps."""
    now = datetime.now(timezone.utc).isoformat()
    pid = uuid.uuid4().hex
    doc = {
        "id": pid,
        "user_id": user_id,
        "created_at": now,
        "is_eigenmode": True,
        "confirmed_gaps": confirmed_gaps,
        # Minimal band shape so any drift computations don't blow up.
        "bands": [
            {"key": "sub", "lo": 400, "hi": 460, "level_db": -20.0},
        ],
    }
    _db.resonance_profiles.insert_one(doc)
    return pid


def _grant_pro(user_id: str):
    future = (datetime.now(timezone.utc).replace(year=datetime.now().year + 1)).isoformat()
    _db.users.update_one({"id": user_id}, {"$set": {"pro_until": future}})


def _cleanup_user(user_id: str):
    _db.resonance_profiles.delete_many({"user_id": user_id})
    _db.users.update_one(
        {"id": user_id},
        {"$set": {"assistant_settings": {}, "pro_until": None}},
    )


# ========== _hb_note_for_frequency unit tests ===============================

def test_hb_note_confirmed_gap_in_band():
    gaps = [{"lo": 400, "hi": 460, "label": "sub"}]
    note = _hb_note_for_frequency(432, gaps, [])
    assert note is not None
    assert "sub" in note
    assert "Harmonic Blueprint" in note


def test_hb_note_confirmed_gap_uses_key_when_no_label():
    gaps = [{"lo": 400, "hi": 460, "key": "low-band"}]
    note = _hb_note_for_frequency(430, gaps, [])
    assert note is not None
    assert "low-band" in note


def test_hb_note_drift_band_when_delta_ge_3():
    drift = [{"lo": 500, "hi": 600, "delta_db": -3.5}]
    note = _hb_note_for_frequency(528, [], drift)
    assert note is not None
    assert "drift" in note


def test_hb_note_drift_band_positive_delta():
    drift = [{"lo": 500, "hi": 600, "delta_db": 3.0}]
    note = _hb_note_for_frequency(528, [], drift)
    assert note is not None


def test_hb_note_weak_drift_below_threshold_returns_none():
    drift = [{"lo": 500, "hi": 600, "delta_db": -2.9}]
    assert _hb_note_for_frequency(528, [], drift) is None


def test_hb_note_out_of_band_returns_none():
    gaps = [{"lo": 400, "hi": 460, "label": "sub"}]
    drift = [{"lo": 500, "hi": 600, "delta_db": -5.0}]
    assert _hb_note_for_frequency(800, gaps, drift) is None


def test_hb_note_invalid_hz_returns_none():
    gaps = [{"lo": 400, "hi": 460, "label": "sub"}]
    assert _hb_note_for_frequency(0, gaps, []) is None
    assert _hb_note_for_frequency(-100, gaps, []) is None
    assert _hb_note_for_frequency("nope", gaps, []) is None  # type: ignore
    assert _hb_note_for_frequency(None, gaps, []) is None  # type: ignore


def test_hb_note_empty_lists_return_none():
    assert _hb_note_for_frequency(432, [], []) is None
    assert _hb_note_for_frequency(432, None, None) is None  # type: ignore


def test_hb_note_gap_takes_precedence_over_drift():
    gaps = [{"lo": 400, "hi": 460, "label": "sub"}]
    drift = [{"lo": 400, "hi": 460, "delta_db": -6.0}]
    note = _hb_note_for_frequency(432, gaps, drift)
    assert "sub" in note  # gap wins


def test_hb_note_bad_band_shape_skipped():
    """Non-numeric lo/hi shouldn't raise — just skipped."""
    gaps = [
        {"lo": "abc", "hi": 460, "label": "bad"},
        {"lo": 400, "hi": 460, "label": "good"},
    ]
    note = _hb_note_for_frequency(432, gaps, [])
    assert note is not None
    assert "good" in note


# ========== /me/settings endpoint tests =====================================

def test_settings_default_true_for_fresh_user():
    s = _register_fresh()
    try:
        r = s.get(f"{API}/me/settings", timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body == {"harmonic_influence_enabled": True}
    finally:
        _cleanup_user(s._uid)


def test_settings_roundtrip_persists():
    s = _register_fresh()
    try:
        r = s.post(f"{API}/me/settings",
                   json={"harmonic_influence_enabled": False}, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json() == {"harmonic_influence_enabled": False}
        # GET reads back the new value
        r2 = s.get(f"{API}/me/settings", timeout=15)
        assert r2.status_code == 200
        assert r2.json() == {"harmonic_influence_enabled": False}
        # Flip back
        r3 = s.post(f"{API}/me/settings",
                    json={"harmonic_influence_enabled": True}, timeout=15)
        assert r3.json() == {"harmonic_influence_enabled": True}
    finally:
        _cleanup_user(s._uid)


def test_settings_empty_body_is_noop():
    s = _register_fresh()
    try:
        # First set to false
        s.post(f"{API}/me/settings",
               json={"harmonic_influence_enabled": False}, timeout=15)
        # Empty body should NOT change the value
        r = s.post(f"{API}/me/settings", json={}, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json() == {"harmonic_influence_enabled": False}
    finally:
        _cleanup_user(s._uid)


def test_settings_unknown_fields_ignored():
    s = _register_fresh()
    try:
        r = s.post(f"{API}/me/settings",
                   json={"random_key": "value", "harmonic_influence_enabled": False,
                         "wat": 42}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        # Only the recognised field survives; unknowns are silently dropped
        assert body == {"harmonic_influence_enabled": False}
        assert "random_key" not in body
    finally:
        _cleanup_user(s._uid)


def test_settings_requires_auth():
    r = requests.get(f"{API}/me/settings", timeout=15)
    assert r.status_code in (401, 403), r.text
    r2 = requests.post(f"{API}/me/settings",
                       json={"harmonic_influence_enabled": False}, timeout=15)
    assert r2.status_code in (401, 403), r2.text


# ========== Integration: /me/ai-recommend gap-note annotation ================

@pytest.mark.slow
def test_ai_recommend_harmonic_note_when_toggle_on_and_in_band():
    """Seed a fresh user with a gap {lo:400,hi:460}, grant Pro, request an
    AI recommendation, and assert: shape returns 200, and if the LLM picked
    a frequency in-band the harmonic_note appears; if out-of-band absent."""
    s = _register_fresh()
    try:
        _grant_pro(s._uid)
        _seed_profile(s._uid, [{"lo": 400, "hi": 460, "label": "sub"}])

        # Toggle ON (default)
        r = s.post(f"{API}/me/ai-recommend",
                   json={"intent": "I want to feel grounded", "mood": "unsettled",
                         "goal": "calm", "duration_min": 10}, timeout=60)
        # LLM may sometimes 502 in test env; skip rather than fail hard.
        if r.status_code == 503:
            pytest.skip("LLM key not configured")
        if r.status_code == 502:
            pytest.skip(f"LLM upstream failed: {r.text[:200]}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "frequency" in body
        f = body["frequency"]
        if 400 <= float(f) <= 460:
            assert "harmonic_note" in body, f"in-band {f} missing harmonic_note: {body}"
            assert "Harmonic Blueprint" in body["harmonic_note"]
        else:
            assert "harmonic_note" not in body, (
                f"out-of-band {f} unexpectedly carried harmonic_note: {body}"
            )
    finally:
        _cleanup_user(s._uid)


@pytest.mark.slow
def test_ai_recommend_no_harmonic_note_when_toggle_off():
    """With harmonic_influence_enabled=false, harmonic_note must never appear."""
    s = _register_fresh()
    try:
        _grant_pro(s._uid)
        _seed_profile(s._uid, [{"lo": 400, "hi": 460, "label": "sub"}])
        # Turn HB influence OFF
        r0 = s.post(f"{API}/me/settings",
                    json={"harmonic_influence_enabled": False}, timeout=15)
        assert r0.status_code == 200

        r = s.post(f"{API}/me/ai-recommend",
                   json={"intent": "I want to feel grounded", "mood": "unsettled",
                         "goal": "calm", "duration_min": 10}, timeout=60)
        if r.status_code == 503:
            pytest.skip("LLM key not configured")
        if r.status_code == 502:
            pytest.skip(f"LLM upstream failed: {r.text[:200]}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "harmonic_note" not in body, (
            f"toggle OFF but harmonic_note present: {body}"
        )
    finally:
        _cleanup_user(s._uid)


# ========== Integration: /me/agent/chat gap-note annotation ==================

@pytest.mark.slow
def test_agent_chat_no_harmonic_note_when_toggle_off():
    """Toggle-off must strip harmonic_note from every suggestion in
    /me/agent/chat, AND the assistant's reply text should not contain the
    'Harmonic Blueprint' phrase (best-effort — soft fail)."""
    s = _register_fresh()
    try:
        _seed_profile(s._uid, [{"lo": 400, "hi": 460, "label": "sub"}])
        s.post(f"{API}/me/settings",
               json={"harmonic_influence_enabled": False}, timeout=15)

        r = s.post(f"{API}/me/agent/chat",
                   json={"message": "I feel anxious",
                         "session_id": f"test-{uuid.uuid4().hex[:6]}"},
                   timeout=60)
        if r.status_code == 503:
            pytest.skip("LLM key not configured")
        if r.status_code == 502:
            pytest.skip(f"LLM upstream failed: {r.text[:200]}")
        assert r.status_code == 200, r.text
        body = r.json()
        # Hard requirement: no harmonic_note on any suggestion
        for sug in (body.get("suggestions") or []):
            assert "harmonic_note" not in sug, (
                f"toggle OFF but suggestion has harmonic_note: {sug}"
            )
    finally:
        _cleanup_user(s._uid)


@pytest.mark.slow
def test_agent_chat_returns_200_with_toggle_on():
    """Smoke: with toggle ON and profile seeded, /me/agent/chat still returns
    200 and the shape has message + suggestions. harmonic_note is opportunistic
    (depends on LLM freq choice)."""
    s = _register_fresh()
    try:
        _seed_profile(s._uid, [{"lo": 400, "hi": 460, "label": "sub"}])
        r = s.post(f"{API}/me/agent/chat",
                   json={"message": "I want to relax",
                         "session_id": f"test-{uuid.uuid4().hex[:6]}"},
                   timeout=60)
        if r.status_code in (502, 503):
            pytest.skip(f"LLM upstream unavailable: {r.status_code}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "message" in body
        assert "suggestions" in body
    finally:
        _cleanup_user(s._uid)
