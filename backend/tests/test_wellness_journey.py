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


# =====================================================================
# Phase 6 — Post-session reflection + preference boost
# =====================================================================

def _seed_journey_row(sess, mood="anxious", frequency=432, duration=120):
    r = sess.post(
        f"{API}/me/journey/log",
        json={
            "frequency": frequency,
            "waveform": "sine",
            "duration_planned_seconds": duration,
            "duration_actual_seconds": duration,
            "mood": mood,
        },
        timeout=15,
    )
    assert r.status_code == 200, r.text
    entry = r.json().get("entry") or {}
    return entry.get("id")


# --- Feature: reflection POST — positive sentiment ---
def test_reflection_positive_sentiment(admin_session):
    entry_id = _seed_journey_row(admin_session, mood="anxious", frequency=432)
    assert entry_id
    r = admin_session.post(
        f"{API}/me/journey/{entry_id}/reflection",
        json={
            "question": "Did you notice any shift during the session?",
            "response": "Yes I felt so much calmer and grounded, really loved it.",
        },
        timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    refl = body.get("reflection") or {}
    assert refl.get("sentiment") == "positive"
    assert refl.get("question")
    assert refl.get("response")
    assert refl.get("created_at")

    # GET journey and confirm reflection is attached to the row
    lg = admin_session.get(f"{API}/me/journey", timeout=15)
    assert lg.status_code == 200
    entries = lg.json().get("entries") or []
    match = next((e for e in entries if e.get("id") == entry_id), None)
    assert match is not None, "seeded entry not returned by GET /me/journey"
    assert (match.get("reflection") or {}).get("sentiment") == "positive"


# --- Feature: reflection POST — negation / neutral sentiment ---
def test_reflection_negative_sentiment(admin_session):
    entry_id = _seed_journey_row(admin_session, mood="tired", frequency=852)
    r = admin_session.post(
        f"{API}/me/journey/{entry_id}/reflection",
        json={
            "question": "Did you notice any shift during the session?",
            "response": "No, I did not feel any shift and it was uncomfortable.",
        },
        timeout=15,
    )
    assert r.status_code == 200, r.text
    assert r.json()["reflection"]["sentiment"] == "negative"


def test_reflection_neutral_sentiment_not_spuriously_positive(admin_session):
    entry_id = _seed_journey_row(admin_session, mood="okay", frequency=396)
    r = admin_session.post(
        f"{API}/me/journey/{entry_id}/reflection",
        json={"question": "How's your body feeling right now?", "response": "It was fine."},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    sentiment = r.json()["reflection"]["sentiment"]
    # The critical thing: 'fine' alone should NOT be positive
    assert sentiment in {"neutral", "negative"}, f"'It was fine.' classified as {sentiment}"


# --- Feature: 404 on missing / non-owned entry ---
def test_reflection_missing_entry_returns_404(admin_session):
    fake = f"nonexistent-{uuid.uuid4().hex}"
    r = admin_session.post(
        f"{API}/me/journey/{fake}/reflection",
        json={"question": "Q?", "response": "hello"},
        timeout=15,
    )
    assert r.status_code == 404


def test_reflection_other_users_entry_returns_404(admin_session, fresh_user_session):
    # Seed on fresh user; try to attach reflection as admin
    other_id = _seed_journey_row(fresh_user_session, mood="calm", frequency=528)
    r = admin_session.post(
        f"{API}/me/journey/{other_id}/reflection",
        json={"question": "Q?", "response": "sneaky"},
        timeout=15,
    )
    assert r.status_code == 404


def test_reflection_requires_auth(admin_session):
    entry_id = _seed_journey_row(admin_session)
    r = requests.post(
        f"{API}/me/journey/{entry_id}/reflection",
        json={"question": "Q?", "response": "hi"},
        timeout=15,
    )
    assert r.status_code in (401, 403)


# --- Feature: preference boost end-to-end ---
def test_preference_boost_prioritises_positive_frequency():
    """Register a NEW user, seed 2 anxious/432Hz rows with positive
    reflections, then verify agent_chat surfaces 432 Hz in top-3 suggestions."""
    s = requests.Session()
    email = f"TEST_prefboost_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{API}/auth/register", json={"email": email, "password": "TestPass!234", "name": "Pref Boost"}, timeout=15)
    assert r.status_code in (200, 201), r.text
    tok = r.json().get("access_token") or r.json().get("token")
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})

    for _ in range(2):
        entry_id = _seed_journey_row(s, mood="anxious", frequency=432, duration=120)
        rr = s.post(
            f"{API}/me/journey/{entry_id}/reflection",
            json={
                "question": "Did that feel like the right frequency for today?",
                "response": "Yes it was beautiful, felt so calm and grounded.",
            },
            timeout=15,
        )
        assert rr.status_code == 200, rr.text
        assert rr.json()["reflection"]["sentiment"] == "positive"

    # Now ask the agent
    chat = s.post(
        f"{API}/me/agent/chat",
        json={"message": "I feel anxious tonight, what should I try?"},
        timeout=60,
    )
    if chat.status_code == 429:
        pytest.skip("agent chat rate-limited")
    assert chat.status_code == 200, chat.text
    body = chat.json()
    suggestions = body.get("suggestions") or []
    assert isinstance(suggestions, list) and len(suggestions) > 0

    top3 = suggestions[:3]
    freqs = [s.get("frequency") for s in top3 if isinstance(s, dict)]
    assert 432 in freqs, f"432 Hz not in top-3 suggestions: {freqs} — preference hint failed"


# --- Feature: sentiment classifier unit tests via the reflection endpoint ---
@pytest.mark.parametrize("text,expected", [
    ("calm and grounded", "positive"),
    ("not calm", "negative"),
    ("nothing shifted", "negative"),
    ("It was okay I guess", "neutral"),
])
def test_classify_sentiment_via_endpoint(admin_session, text, expected):
    entry_id = _seed_journey_row(admin_session, mood="test", frequency=396)
    r = admin_session.post(
        f"{API}/me/journey/{entry_id}/reflection",
        json={"question": "Q?", "response": text},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    got = r.json()["reflection"]["sentiment"]
    assert got == expected, f"'{text}' → got {got}, expected {expected}"


def test_classify_sentiment_empty_string_rejected(admin_session):
    """Empty response is rejected by Pydantic (min_length=1), which is the
    documented contract; the classifier itself would return 'neutral'."""
    entry_id = _seed_journey_row(admin_session)
    r = admin_session.post(
        f"{API}/me/journey/{entry_id}/reflection",
        json={"question": "Q?", "response": ""},
        timeout=15,
    )
    assert r.status_code == 422


# --- Feature: response length cap enforced (500 chars) ---
def test_reflection_response_length_cap(admin_session):
    entry_id = _seed_journey_row(admin_session)
    r = admin_session.post(
        f"{API}/me/journey/{entry_id}/reflection",
        json={"question": "Q?", "response": "a" * 501},
        timeout=15,
    )
    assert r.status_code == 422


# --- Feature: _mood_bucket coverage via _summarise_journey_entry side effects ---
# We can't import the module inside the container from a networked test, but
# we can validate the bucketing indirectly: seed a row with an anxious mood,
# reflect positively, and confirm the preference hint kicks in even when the
# NEW query mood is phrased differently ('feeling really anxious').
def test_mood_bucket_matches_synonyms():
    s = requests.Session()
    email = f"TEST_bucket_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{API}/auth/register", json={"email": email, "password": "TestPass!234", "name": "Bucket"}, timeout=15)
    assert r.status_code in (200, 201), r.text
    tok = r.json().get("access_token") or r.json().get("token")
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    for _ in range(2):
        eid = _seed_journey_row(s, mood="anxious", frequency=432)
        rr = s.post(
            f"{API}/me/journey/{eid}/reflection",
            json={"question": "Q?", "response": "so calm and loved it, grounded."},
            timeout=15,
        )
        assert rr.status_code == 200
    # Query with a synonym phrasing
    chat = s.post(
        f"{API}/me/agent/chat",
        json={"message": "feeling really anxious right now, help"},
        timeout=60,
    )
    if chat.status_code == 429:
        pytest.skip("rate-limited")
    assert chat.status_code == 200
    freqs = [x.get("frequency") for x in (chat.json().get("suggestions") or [])[:3] if isinstance(x, dict)]
    assert 432 in freqs, f"anxious-synonym did not surface 432 Hz: {freqs}"
