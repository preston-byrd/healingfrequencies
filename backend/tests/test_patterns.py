"""Phase 7 — Behavioural pattern detection tests.

Covers GET /api/me/patterns, POST /api/me/patterns/{key}/dismiss,
POST /api/me/patterns/clear, priority ordering, and the USER_PATTERNS
prompt-block presence via agent_chat.

Because time_of_day is server-derived, we insert rows DIRECTLY into
db.wellness_journey via pymongo to control TOD & created_at explicitly.
"""
from __future__ import annotations

import os
import uuid
import time
import pytest
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from pathlib import Path
from dotenv import dotenv_values
from pymongo import MongoClient

from _creds import ADMIN_EMAIL, ADMIN_PASSWORD

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL") or dotenv_values(
    Path(__file__).resolve().parent.parent.parent / "frontend" / ".env"
).get("REACT_APP_BACKEND_URL")
BASE_URL = BASE_URL.rstrip("/")
API = f"{BASE_URL}/api"

# Direct DB handle for seeding TOD-controlled rows.
_ENV = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
MONGO_URL = _ENV.get("MONGO_URL") or os.environ["MONGO_URL"]
DB_NAME = _ENV.get("DB_NAME") or os.environ["DB_NAME"]
_client = MongoClient(MONGO_URL)
_db = _client[DB_NAME]


# ---------- helpers ---------------------------------------------------------

def _register_fresh():
    s = requests.Session()
    email = f"TEST_patterns_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(
        f"{API}/auth/register",
        json={"email": email, "password": "TestPass!234", "name": "Patterns"},
        timeout=15,
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    tok = body.get("access_token") or body.get("token")
    user = body.get("user") or {}
    uid = user.get("id")
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    # Fallback — hit /auth/me to fetch id
    if not uid:
        me = s.get(f"{API}/auth/me", timeout=15).json()
        uid = me.get("id") or (me.get("user") or {}).get("id")
    assert uid, f"could not resolve user id: {body}"
    s._uid = uid  # type: ignore
    s._email = email  # type: ignore
    return s


def _insert_row(user_id, *, frequency=None, mood=None, tod="morning",
                preset_label=None, preset_key=None, ambient=None,
                extended=False, ended_early=False, soundscape=None,
                minutes_ago=0):
    now = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    doc = {
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "created_at": now.isoformat(),
        "time_of_day": tod,
        "duration_actual_seconds": 300,
        "duration_planned_seconds": 300,
        "extended": extended,
        "ended_early": ended_early,
    }
    if frequency is not None:
        doc["frequency"] = frequency
    if mood is not None:
        doc["mood"] = mood
    if preset_label:
        doc["preset_label"] = preset_label
    if preset_key:
        doc["preset_key"] = preset_key
    if ambient is not None:
        doc["ambient"] = ambient
    if soundscape:
        doc["soundscape"] = soundscape
    _db.wellness_journey.insert_one(doc)
    return doc["id"]


def _cleanup(user_id):
    _db.wellness_journey.delete_many({"user_id": user_id})
    _db.users.update_one({"id": user_id}, {"$set": {"dismissed_patterns": []}})


def _get_patterns(sess):
    r = sess.get(f"{API}/me/patterns", timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- cold-start floor ------------------------------------------------

def test_cold_start_returns_empty_below_3_rows():
    s = _register_fresh()
    try:
        body = _get_patterns(s)
        assert body == {"patterns": [], "dismissed": []}
        # 2 rows still cold-start
        for _ in range(2):
            _insert_row(s._uid, frequency=432, mood="anxious", tod="morning")
        body = _get_patterns(s)
        assert body["patterns"] == []
    finally:
        _cleanup(s._uid)


# ---------- top_frequency ---------------------------------------------------

def test_top_frequency_pattern_fires():
    s = _register_fresh()
    try:
        for _ in range(4):
            _insert_row(s._uid, frequency=432, mood="calm", tod="afternoon")
        _insert_row(s._uid, frequency=528, mood="calm", tod="afternoon")
        body = _get_patterns(s)
        patterns = body["patterns"]
        tf = [p for p in patterns if p["kind"] == "top_frequency"]
        assert tf, f"no top_frequency in {patterns}"
        p = tf[0]
        assert p["label"] == "432 Hz"
        assert p["count"] == 4
        assert p["cta"] == {"action": "arm_frequency", "frequency": 432.0}
        assert p["key"] == "top_frequency:432"
    finally:
        _cleanup(s._uid)


def test_top_frequency_not_fires_when_no_dominance():
    s = _register_fresh()
    try:
        # 3x each of 3 freqs — no single > 40%
        for f in (432, 528, 396):
            for _ in range(3):
                _insert_row(s._uid, frequency=f, tod="afternoon")
        body = _get_patterns(s)
        tf = [p for p in body["patterns"] if p["kind"] == "top_frequency"]
        assert not tf, f"top_frequency should not fire with no dominance: {tf}"
    finally:
        _cleanup(s._uid)


# ---------- preferred_time_of_day ------------------------------------------

def test_preferred_time_of_day_fires():
    s = _register_fresh()
    try:
        for _ in range(4):
            _insert_row(s._uid, frequency=432, tod="morning")
        _insert_row(s._uid, frequency=432, tod="evening")
        body = _get_patterns(s)
        tod = [p for p in body["patterns"] if p["kind"] == "preferred_time_of_day"]
        assert tod, f"no preferred_time_of_day in {body['patterns']}"
        p = tod[0]
        assert p.get("time_of_day") == "morning"
        assert p["count"] == 4
        assert p["key"] == "preferred_time_of_day:morning"
    finally:
        _cleanup(s._uid)


# ---------- mood_at_time ---------------------------------------------------

def test_mood_at_time_pattern_fires():
    s = _register_fresh()
    try:
        for _ in range(3):
            _insert_row(s._uid, frequency=432, mood="anxious", tod="morning")
        body = _get_patterns(s)
        m = [p for p in body["patterns"] if p["kind"] == "mood_at_time"]
        assert m, f"no mood_at_time in {body['patterns']}"
        p = m[0]
        assert p["mood"] == "anxious"
        assert p["time_of_day"] == "morning"
        assert p["count"] == 3
        assert "anxious" in p["message"].lower()
        assert p["key"] == "mood_at_time:anxious@morning"
    finally:
        _cleanup(s._uid)


# ---------- unused_soundscapes ---------------------------------------------

def test_unused_soundscapes_pattern_fires():
    s = _register_fresh()
    try:
        for _ in range(6):
            _insert_row(s._uid, frequency=432, tod="afternoon", ambient={"rain": 0.4})
        body = _get_patterns(s)
        u = [p for p in body["patterns"] if p["kind"] == "unused_soundscapes"]
        assert u, f"no unused_soundscapes in {body['patterns']}"
        p = u[0]
        # Should NOT recommend rain (which was used)
        assert p["cta"]["soundscape"] != "rain"
        assert p["cta"]["soundscape"] in {
            "ocean", "forest", "wind", "crickets", "bowls", "brown", "white"
        }
    finally:
        _cleanup(s._uid)


def test_unused_soundscapes_requires_5_sessions():
    s = _register_fresh()
    try:
        # 4 sessions only
        for _ in range(4):
            _insert_row(s._uid, frequency=432, tod="afternoon", ambient={"rain": 0.4})
        body = _get_patterns(s)
        u = [p for p in body["patterns"] if p["kind"] == "unused_soundscapes"]
        assert not u
    finally:
        _cleanup(s._uid)


# ---------- extension_favorite ---------------------------------------------

def test_extension_favorite_pattern_fires():
    s = _register_fresh()
    try:
        _insert_row(s._uid, preset_label="Deep Focus", preset_key="focus", tod="afternoon", extended=True)
        _insert_row(s._uid, preset_label="Deep Focus", preset_key="focus", tod="afternoon", extended=True)
        _insert_row(s._uid, preset_label="Deep Focus", preset_key="focus", tod="afternoon", extended=False)
        body = _get_patterns(s)
        ef = [p for p in body["patterns"] if p["kind"] == "extension_favorite"]
        assert ef, f"no extension_favorite in {body['patterns']}"
        p = ef[0]
        assert p["label"] == "Deep Focus"
        assert p["count"] == 2
        assert p["cta"]["action"] == "arm_preset"
        assert p["cta"]["preset_key"] == "focus"
    finally:
        _cleanup(s._uid)


def test_extension_favorite_not_fires_when_ended_early():
    s = _register_fresh()
    try:
        _insert_row(s._uid, preset_label="Deep Focus", tod="afternoon", extended=True)
        _insert_row(s._uid, preset_label="Deep Focus", tod="afternoon", extended=True)
        _insert_row(s._uid, preset_label="Deep Focus", tod="afternoon", ended_early=True)
        body = _get_patterns(s)
        ef = [p for p in body["patterns"] if p["kind"] == "extension_favorite"]
        assert not ef, "extension_favorite must not fire if any row ended_early"
    finally:
        _cleanup(s._uid)


# ---------- dismiss / clear flow -------------------------------------------

def test_dismiss_and_clear_flow():
    s = _register_fresh()
    try:
        for _ in range(3):
            _insert_row(s._uid, frequency=432, mood="anxious", tod="morning")
        body = _get_patterns(s)
        assert body["patterns"], "expected at least one pattern"
        # Pick the mood_at_time key (contains ':' and '@')
        key = "mood_at_time:anxious@morning"

        # Dismiss
        enc = quote(key, safe="")
        r = s.post(f"{API}/me/patterns/{enc}/dismiss", timeout=15)
        assert r.status_code == 200, r.text
        assert key in _get_patterns(s)["dismissed"]

        # Idempotency — second dismiss shouldn't add duplicate
        r = s.post(f"{API}/me/patterns/{enc}/dismiss", timeout=15)
        assert r.status_code == 200
        dismissed = _get_patterns(s)["dismissed"]
        assert dismissed.count(key) == 1

        # Clear
        r = s.post(f"{API}/me/patterns/clear", timeout=15)
        assert r.status_code == 200
        assert _get_patterns(s)["dismissed"] == []
    finally:
        _cleanup(s._uid)


def test_dismiss_key_with_special_chars_url_encoded():
    """Verify the {pattern_key:path} converter decodes ':' and '@'."""
    s = _register_fresh()
    try:
        for _ in range(3):
            _insert_row(s._uid, frequency=432, mood="anxious", tod="morning")
        key = "mood_at_time:anxious@morning"
        enc = quote(key, safe="")
        r = s.post(f"{API}/me/patterns/{enc}/dismiss", timeout=15)
        assert r.status_code == 200
        assert r.json()["dismissed"] == key
    finally:
        _cleanup(s._uid)


def test_patterns_endpoints_require_auth():
    r = requests.get(f"{API}/me/patterns", timeout=15)
    assert r.status_code in (401, 403)
    r = requests.post(f"{API}/me/patterns/foo/dismiss", timeout=15)
    assert r.status_code in (401, 403)
    r = requests.post(f"{API}/me/patterns/clear", timeout=15)
    assert r.status_code in (401, 403)


# ---------- USER_PATTERNS prompt injection via agent_chat ------------------

def test_agent_chat_ok_with_patterns_present():
    s = _register_fresh()
    try:
        for _ in range(3):
            _insert_row(s._uid, frequency=432, mood="anxious", tod="morning")
        r = s.post(f"{API}/me/agent/chat", json={"message": "hi"}, timeout=60)
        if r.status_code == 429:
            pytest.skip("rate-limited")
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body.get("message"), str) and body["message"].strip()
    finally:
        _cleanup(s._uid)


# ---------------------------------------------------------------------------
# Phase 7.1 — Patterns cache (15-min TTL + invalidate-on-write)
# ---------------------------------------------------------------------------

def test_patterns_cache_written_after_first_call():
    """First GET /me/patterns should populate patterns_cache on the user doc
    with a computed_at + expires_at ≈ +15 min."""
    s = _register_fresh()
    try:
        # Seed enough rows to trigger a top_frequency pattern.
        for _ in range(4):
            _insert_row(s._uid, frequency=432, mood="anxious", tod="morning")

        # Precondition — no cache before first read.
        u0 = _db.users.find_one({"id": s._uid}, {"patterns_cache": 1}) or {}
        assert not u0.get("patterns_cache")

        r = s.get(f"{API}/me/patterns", timeout=15)
        assert r.status_code == 200
        patterns_from_api = r.json()["patterns"]
        assert len(patterns_from_api) >= 1

        u1 = _db.users.find_one({"id": s._uid}, {"patterns_cache": 1}) or {}
        pc = u1.get("patterns_cache") or {}
        assert pc, "cache should be written after first call"
        assert isinstance(pc.get("patterns"), list)
        assert len(pc["patterns"]) == len(patterns_from_api)
        assert pc.get("computed_at")
        assert pc.get("expires_at")
        # TTL ≈ 15 min ± 5 s.
        delta = (pc["expires_at"] - pc["computed_at"]).total_seconds()
        assert 15 * 60 - 5 <= delta <= 15 * 60 + 5
    finally:
        _cleanup(s._uid)


def test_patterns_cache_served_on_second_call():
    """Second GET should return the SAME payload even after we've mutated
    journey rows directly (bypassing the invalidation)."""
    s = _register_fresh()
    try:
        for _ in range(4):
            _insert_row(s._uid, frequency=432, mood="anxious", tod="morning")

        r1 = s.get(f"{API}/me/patterns", timeout=15).json()

        # Directly insert a row that would produce a *different* pattern
        # set — bypassing POST /me/journey/log so the cache is NOT
        # invalidated. This proves the cache is actually being served.
        for _ in range(4):
            _insert_row(s._uid, frequency=528, mood="tired", tod="evening")

        r2 = s.get(f"{API}/me/patterns", timeout=15).json()
        assert r2["patterns"] == r1["patterns"], (
            "cache should serve identical patterns until invalidated"
        )
    finally:
        _cleanup(s._uid)


def test_patterns_cache_invalidated_by_journey_log():
    """POST /me/journey/log must clear the cache so the very next
    /me/patterns read recomputes with the fresh row included."""
    s = _register_fresh()
    try:
        for _ in range(4):
            _insert_row(s._uid, frequency=432, mood="anxious", tod="morning")

        _ = s.get(f"{API}/me/patterns", timeout=15).json()  # populate cache
        assert (_db.users.find_one({"id": s._uid}, {"patterns_cache": 1}) or {}).get("patterns_cache")

        # A fresh log via the endpoint must $unset the cache.
        r = s.post(
            f"{API}/me/journey/log",
            json={
                "frequency": 528,
                "waveform": "sine",
                "duration_planned_seconds": 300,
                "duration_actual_seconds": 240,
                "mood": "tired",
            },
            timeout=15,
        )
        assert r.status_code == 200, r.text
        pc_after = (_db.users.find_one({"id": s._uid}, {"patterns_cache": 1}) or {}).get("patterns_cache")
        assert not pc_after, "journey_log should invalidate patterns_cache"
    finally:
        _cleanup(s._uid)


def test_patterns_cache_invalidated_by_reflection():
    """POST /me/journey/{id}/reflection must also invalidate — reflections
    feed the mood-preference boost which is closely tied to pattern shape."""
    s = _register_fresh()
    try:
        for _ in range(4):
            _insert_row(s._uid, frequency=432, mood="anxious", tod="morning")
        _ = s.get(f"{API}/me/patterns", timeout=15).json()  # populate cache

        # Insert a real row via POST (invalidates once), then re-read to
        # repopulate the cache so we can assert the reflection kills it.
        r = s.post(
            f"{API}/me/journey/log",
            json={"frequency": 432, "duration_actual_seconds": 180, "mood": "anxious"},
            timeout=15,
        )
        assert r.status_code == 200
        entry_id = r.json()["entry"]["id"]

        _ = s.get(f"{API}/me/patterns", timeout=15).json()  # repopulate
        assert (_db.users.find_one({"id": s._uid}, {"patterns_cache": 1}) or {}).get("patterns_cache")

        rf = s.post(
            f"{API}/me/journey/{entry_id}/reflection",
            json={"question": "Did that feel right?", "response": "Yes very calming"},
            timeout=15,
        )
        assert rf.status_code == 200, rf.text
        pc_after = (_db.users.find_one({"id": s._uid}, {"patterns_cache": 1}) or {}).get("patterns_cache")
        assert not pc_after, "reflection write should invalidate patterns_cache"
    finally:
        _cleanup(s._uid)


def test_patterns_cache_expired_ttl_recomputes():
    """If we manually rewind expires_at to the past, the next call must
    treat it as a miss and recompute (writing a fresh entry)."""
    s = _register_fresh()
    try:
        for _ in range(4):
            _insert_row(s._uid, frequency=432, mood="anxious", tod="morning")
        _ = s.get(f"{API}/me/patterns", timeout=15).json()

        # Rewind the cache to 1 hour in the past.
        _db.users.update_one(
            {"id": s._uid},
            {"$set": {
                "patterns_cache.expires_at": datetime.now(timezone.utc) - timedelta(hours=1),
                "patterns_cache.patterns": [],  # sentinel — recompute should overwrite
            }},
        )

        r = s.get(f"{API}/me/patterns", timeout=15).json()
        assert len(r["patterns"]) >= 1, "expired cache should have been recomputed"

        pc = _db.users.find_one({"id": s._uid}, {"patterns_cache": 1})["patterns_cache"]
        # Mongo returns tz-naive datetimes — compare against a naive `now`.
        assert pc["expires_at"] > datetime.utcnow()
    finally:
        _cleanup(s._uid)
