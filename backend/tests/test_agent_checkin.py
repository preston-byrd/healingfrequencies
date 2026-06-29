"""Tests for /api/me/agent/checkin endpoint and PRIOR_INSIGHTS prompt enrichment (iteration 33).

Covers:
- POST /me/agent/checkin happy path (200 + persistence in `agent_checkins`)
- 400 on unknown suggestion kind
- 401 unauthenticated
- PRIOR_INSIGHTS injection — subsequent /me/agent/chat reply references prior 396 Hz pick
- 50-row history trim per user
"""
import asyncio
import os
import time
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL", "https://frequency-healer-31.preview.emergentagent.com"
).rstrip("/")
API = f"{BASE_URL}/api"

_BACKEND_ENV = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
MONGO_URL = _BACKEND_ENV.get("MONGO_URL") or os.environ.get("MONGO_URL")
DB_NAME = _BACKEND_ENV.get("DB_NAME") or os.environ.get("DB_NAME")
ADMIN_EMAIL = _BACKEND_ENV.get("ADMIN_EMAIL")
ADMIN_PASSWORD = _BACKEND_ENV.get("ADMIN_PASSWORD")


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(
        f"{API}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
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
    """Wipe all agent_checkins for the admin before/after each test that needs a known state."""
    async def _wipe():
        client = AsyncIOMotorClient(MONGO_URL)
        try:
            db = client[DB_NAME]
            await db.agent_checkins.delete_many({"user_id": admin_user_id})
        finally:
            client.close()

    asyncio.run(_wipe())
    yield
    asyncio.run(_wipe())


# --- Checkin endpoint validation ---------------------------------------------
class TestAgentCheckinEndpoint:
    def test_unauthenticated_returns_401(self):
        r = requests.post(
            f"{API}/me/agent/checkin",
            json={
                "message": "feeling tense",
                "suggestion": {"kind": "preset", "label": "396 Hz", "frequency": 396, "waveform": "sine"},
            },
            timeout=15,
        )
        assert r.status_code in (401, 403), f"Expected 401/403, got {r.status_code}: {r.text[:200]}"

    def test_unknown_kind_returns_400(self, auth_headers):
        r = requests.post(
            f"{API}/me/agent/checkin",
            headers=auth_headers,
            json={
                "message": "feeling tense",
                "suggestion": {"kind": "bogus", "label": "X"},
            },
            timeout=15,
        )
        assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text[:300]}"
        assert "kind" in (r.json().get("detail") or "").lower()

    def test_checkin_persists_to_mongo(self, auth_headers, admin_user_id, clean_checkins):
        payload = {
            "message": "feeling anxious about a meeting",
            "suggestion": {
                "kind": "preset",
                "label": "396 Hz · Release Fear",
                "frequency": 396,
                "waveform": "sine",
            },
            "session_id": "iter33-persist",
        }
        r = requests.post(f"{API}/me/agent/checkin", headers=auth_headers, json=payload, timeout=15)
        assert r.status_code == 200, f"Status {r.status_code}: {r.text[:300]}"
        body = r.json()
        assert body.get("ok") is True
        doc_id = body.get("id")
        assert doc_id and isinstance(doc_id, str)

        # Verify via direct mongo query
        async def _read():
            client = AsyncIOMotorClient(MONGO_URL)
            try:
                doc = await client[DB_NAME].agent_checkins.find_one({"id": doc_id})
                return doc
            finally:
                client.close()

        doc = asyncio.run(_read())
        assert doc is not None, "Document not persisted in MongoDB"
        assert doc.get("user_id") == admin_user_id
        assert doc.get("message") == payload["message"]
        assert doc.get("session_id") == "iter33-persist"
        assert doc.get("created_at")
        s = doc.get("suggestion") or {}
        assert s.get("kind") == "preset"
        assert float(s.get("frequency")) == 396.0
        assert s.get("waveform") == "sine"


# --- PRIOR_INSIGHTS prompt enrichment ----------------------------------------
class TestPriorInsightsInjection:
    def test_chat_references_prior_396_pick(self, auth_headers, clean_checkins):
        """After persisting a (anxious → 396 Hz) check-in, the next chat reply
        should reference the prior insight. Wording varies — assert at least
        one signal token appears in the message."""
        # 1) Seed a prior check-in: anxious → 396 Hz preset.
        seed = {
            "message": "feeling anxious about a meeting at work tomorrow",
            "suggestion": {
                "kind": "preset",
                "label": "396 Hz · Release Fear",
                "frequency": 396,
                "waveform": "sine",
            },
            "session_id": "iter33-prior-seed",
        }
        r = requests.post(f"{API}/me/agent/checkin", headers=auth_headers, json=seed, timeout=15)
        assert r.status_code == 200, f"seed failed: {r.text[:300]}"

        # 2) Trigger /agent/chat with a similar mood — should pull PRIOR_INSIGHTS.
        # Burst-rate-limit defensively: sleep a little so we don't collide with the
        # 8-token bucket leftover from other tests.
        time.sleep(2)
        msg = {
            "message": "I'm feeling anxious again about another meeting — what helps?",
            "history": [],
            "session_id": "iter33-prior-chat",
        }
        r = requests.post(f"{API}/me/agent/chat", headers=auth_headers, json=msg, timeout=60)
        assert r.status_code == 200, f"chat failed: {r.status_code} {r.text[:300]}"
        reply_msg = (r.json().get("message") or "").lower()
        assert reply_msg, "Empty message"

        tokens = ["396", "last time", "before", "remember", "helped", "previously", "earlier"]
        hit = next((t for t in tokens if t in reply_msg), None)
        assert hit is not None, (
            f"Prior-insights reference not found in reply.\nReply: {reply_msg!r}"
        )


# --- 50-row history trim -----------------------------------------------------
class TestCheckinTrim:
    def test_per_user_capped_at_50(self, auth_headers, admin_user_id, clean_checkins):
        """Insert 52 check-ins; collection should have ≤50 for that user."""
        for i in range(52):
            r = requests.post(
                f"{API}/me/agent/checkin",
                headers=auth_headers,
                json={
                    "message": f"trim test {i}",
                    "suggestion": {
                        "kind": "preset",
                        "label": f"432 Hz #{i}",
                        "frequency": 432,
                        "waveform": "sine",
                    },
                    "session_id": "iter33-trim",
                },
                timeout=20,
            )
            assert r.status_code == 200, f"insert {i} failed: {r.status_code} {r.text[:200]}"

        async def _count():
            client = AsyncIOMotorClient(MONGO_URL)
            try:
                return await client[DB_NAME].agent_checkins.count_documents({"user_id": admin_user_id})
            finally:
                client.close()

        cnt = asyncio.run(_count())
        assert cnt <= 50, f"Expected ≤50 rows, found {cnt}"
        # And specifically: the trim should leave exactly 50 after inserting 52.
        assert cnt == 50, f"Expected exactly 50 rows after inserting 52, found {cnt}"
