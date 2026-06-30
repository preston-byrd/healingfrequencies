"""Iteration 36 — Pulsing Haptics inside the AI Companion suggestion engine.

Backend assertions:
1. /me/agent/chat with a sleep-related prompt returns at least one suggestion
   with kind=='haptic_combo' (validator path).
2. /me/agent/checkin with an *invalid* haptic_combo payload returns 200 and
   the document persisted to `agent_checkins` has the bad fields stripped /
   coerced (pattern→auto, frequency removed, soundscape removed, duration
   removed).
3. /me/agent/checkin with a valid haptic_combo payload persists fields intact.
4. After seeding a haptic_combo check-in, a follow-up /me/agent/chat sleep
   prompt references PRIOR_INSIGHTS (one of: '396', 'heartbeat', 'haptic',
   'last time', 'before', 'helped', 'remember').
"""
import asyncio
import os
import time
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient

_FRONTEND_ENV = dotenv_values(Path(__file__).resolve().parents[2] / "frontend" / ".env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _FRONTEND_ENV.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL must be set"
API = f"{BASE_URL}/api"

_BACKEND_ENV = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
MONGO_URL = _BACKEND_ENV.get("MONGO_URL") or os.environ.get("MONGO_URL")
DB_NAME = _BACKEND_ENV.get("DB_NAME") or os.environ.get("DB_NAME")

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASS = "JuzlUWlMMOjHM0u#m5qv0ds!oYp8"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(
        f"{API}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASS},
        timeout=20,
    )
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code} {r.text[:200]}")
    tok = r.json().get("access_token") or r.json().get("token")
    assert tok, f"No token in login response: {r.json()}"
    return tok


@pytest.fixture(scope="module")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def admin_user_id(auth_headers):
    r = requests.get(f"{API}/auth/me", headers=auth_headers, timeout=15)
    assert r.status_code == 200, f"auth/me failed: {r.status_code} {r.text}"
    uid = r.json().get("id")
    assert uid, f"No id in /auth/me payload: {r.json()}"
    return uid


@pytest.fixture(scope="function")
def clean_checkins(admin_user_id):
    """Wipe agent_checkins for the admin user before/after the test."""
    async def _wipe():
        client = AsyncIOMotorClient(MONGO_URL)
        try:
            await client[DB_NAME].agent_checkins.delete_many({"user_id": admin_user_id})
        finally:
            client.close()

    asyncio.run(_wipe())
    yield
    asyncio.run(_wipe())


def _read_checkin(doc_id):
    async def _read():
        client = AsyncIOMotorClient(MONGO_URL)
        try:
            return await client[DB_NAME].agent_checkins.find_one({"id": doc_id})
        finally:
            client.close()
    return asyncio.run(_read())


# --- /me/agent/chat returns haptic_combo on sleep prompt ----------------------
class TestAgentChatHapticCombo:
    def test_sleep_prompt_returns_haptic_combo(self, auth_headers):
        payload = {
            "message": "I cannot sleep, my mind is racing",
            "history": [],
            "session_id": "iter36-haptic-combo-1",
        }
        r = requests.post(f"{API}/me/agent/chat", headers=auth_headers, json=payload, timeout=60)
        assert r.status_code == 200, f"Status {r.status_code}: {r.text[:400]}"
        data = r.json()
        suggestions = data.get("suggestions") or []
        assert isinstance(suggestions, list) and len(suggestions) > 0, "Empty suggestions"

        combos = [s for s in suggestions if s.get("kind") == "haptic_combo"]
        assert combos, (
            f"No haptic_combo suggestion returned by LLM. Kinds: "
            f"{[s.get('kind') for s in suggestions]}; raw: {data}"
        )
        combo = combos[0]
        # Field shape
        assert isinstance(combo.get("label"), str) and combo["label"].strip()
        assert combo.get("pattern") in {"auto", "heartbeat", "breath478", "frequency"}, (
            f"Invalid pattern: {combo.get('pattern')}"
        )
        # Must have at least one of frequency/soundscape/duration_min
        has_extra = any(k in combo for k in ("frequency", "soundscape", "duration_min"))
        assert has_extra, f"haptic_combo missing optional extras: {combo}"
        if "frequency" in combo:
            assert 1 <= float(combo["frequency"]) <= 1200
        # haptic_combo is the FREE accessibility kind — pro_only must be False
        assert combo.get("pro_only") is False, f"pro_only must be False, got {combo.get('pro_only')}"


# --- /me/agent/checkin validates / strips bogus haptic_combo fields -----------
class TestAgentCheckinHapticComboValidation:
    def test_invalid_combo_fields_are_stripped(self, auth_headers, clean_checkins):
        # Hand-crafted invalid payload
        payload = {
            "message": "trying weird stuff",
            "suggestion": {
                "kind": "haptic_combo",
                "label": "Bad combo",
                "pattern": "invalid",       # NOT in _HAPTIC_PATTERNS → 'auto'
                "frequency": 9999,           # > 1200 → stripped
                "soundscape": "cosmic",     # not in ALLOWED_AMBIENT → stripped
                "duration_min": 999,         # not in _HAPTIC_DURATIONS → stripped
            },
            "session_id": "iter36-validator",
        }
        r = requests.post(f"{API}/me/agent/checkin", headers=auth_headers, json=payload, timeout=15)
        assert r.status_code == 200, f"checkin failed: {r.status_code} {r.text[:300]}"
        body = r.json()
        assert body.get("ok") is True
        doc_id = body.get("id")
        assert doc_id

        doc = _read_checkin(doc_id)
        assert doc is not None, "Document not persisted"
        s = doc.get("suggestion") or {}
        assert s.get("kind") == "haptic_combo"
        assert s.get("pattern") == "auto", f"pattern not coerced: {s.get('pattern')}"
        assert "frequency" not in s, f"frequency should be stripped, got {s.get('frequency')}"
        assert "soundscape" not in s, f"soundscape should be stripped, got {s.get('soundscape')}"
        assert "duration_min" not in s, f"duration_min should be stripped, got {s.get('duration_min')}"

    def test_valid_combo_fields_persist(self, auth_headers, clean_checkins):
        payload = {
            "message": "I can't sleep, my mind is racing",
            "suggestion": {
                "kind": "haptic_combo",
                "label": "Heartbeat · 396 Hz · 30min",
                "pattern": "heartbeat",
                "frequency": 396,
                "soundscape": "rain",
                "duration_min": 30,
            },
            "session_id": "iter36-valid-combo",
        }
        r = requests.post(f"{API}/me/agent/checkin", headers=auth_headers, json=payload, timeout=15)
        assert r.status_code == 200, f"checkin failed: {r.status_code} {r.text[:300]}"
        body = r.json()
        assert body.get("ok") is True
        doc = _read_checkin(body.get("id"))
        assert doc is not None
        s = doc.get("suggestion") or {}
        assert s.get("kind") == "haptic_combo"
        assert s.get("pattern") == "heartbeat"
        assert float(s.get("frequency")) == 396.0
        assert s.get("soundscape") == "rain"
        assert s.get("duration_min") == 30


# --- PRIOR_INSIGHTS includes haptic_combo via _summarise_suggestion -----------
class TestPriorInsightsHapticCombo:
    def test_chat_references_prior_haptic_combo(self, auth_headers, clean_checkins):
        # Seed prior haptic_combo check-in
        seed = {
            "message": "I cannot sleep, mind is racing",
            "suggestion": {
                "kind": "haptic_combo",
                "label": "Heartbeat · 396 Hz · 30min",
                "pattern": "heartbeat",
                "frequency": 396,
                "soundscape": "rain",
                "duration_min": 30,
            },
            "session_id": "iter36-prior-seed",
        }
        r = requests.post(f"{API}/me/agent/checkin", headers=auth_headers, json=seed, timeout=15)
        assert r.status_code == 200, f"seed failed: {r.text[:300]}"

        # Sleep so we don't collide with the burst rate-limit bucket.
        time.sleep(3)

        msg = {
            "message": "I still can't sleep tonight, can you suggest something?",
            "history": [],
            "session_id": "iter36-prior-chat",
        }
        r = requests.post(f"{API}/me/agent/chat", headers=auth_headers, json=msg, timeout=60)
        assert r.status_code == 200, f"chat failed: {r.status_code} {r.text[:300]}"
        reply_msg = (r.json().get("message") or "").lower()
        assert reply_msg

        tokens = [
            "396", "heartbeat", "haptic", "last time", "before",
            "previously", "earlier", "helped", "remember",
        ]
        hit = next((t for t in tokens if t in reply_msg), None)
        assert hit is not None, (
            f"Prior-insights reference (haptic_combo) not found in reply.\nReply: {reply_msg!r}"
        )
