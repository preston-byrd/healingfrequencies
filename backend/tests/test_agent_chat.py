"""Tests for /api/me/agent/chat endpoint (iteration 32)."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://frequency-healer-31.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASS = "JuzlUWlMMOjHM0u#m5qv0ds!oYp8"

ALLOWED_KINDS = {"preset", "soundscape", "sleep", "ai_prescription"}


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=20)
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code} {r.text[:200]}")
    tok = r.json().get("access_token") or r.json().get("token")
    assert tok, f"No token in login response: {r.json()}"
    return tok


@pytest.fixture(scope="module")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


# --- Agent chat endpoint shape & multi-turn -----------------------------------
class TestAgentChatBasic:
    def test_unauthenticated_returns_401(self):
        r = requests.post(f"{API}/me/agent/chat", json={"message": "hi", "history": []}, timeout=20)
        assert r.status_code in (401, 403), f"Expected 401/403, got {r.status_code}"

    def test_chat_returns_message_and_suggestions(self, auth_headers):
        # Use a short, common request to elicit a structured response.
        payload = {"message": "I feel anxious and tense, anything that could help calm me?", "history": [], "session_id": "test-session-iter32-1"}
        r = requests.post(f"{API}/me/agent/chat", headers=auth_headers, json=payload, timeout=60)
        assert r.status_code == 200, f"Status {r.status_code}: {r.text[:400]}"
        data = r.json()
        assert isinstance(data, dict)
        assert "message" in data and isinstance(data["message"], str) and data["message"].strip()
        assert "suggestions" in data and isinstance(data["suggestions"], list)
        # If suggestions exist, validate each shape
        for s in data["suggestions"]:
            assert isinstance(s, dict)
            assert s.get("kind") in ALLOWED_KINDS, f"bad kind: {s.get('kind')}"
            assert isinstance(s.get("label"), str) and s["label"].strip()
            if s["kind"] == "preset":
                assert isinstance(s.get("frequency"), (int, float))
                assert 1.0 <= float(s["frequency"]) <= 20000.0, f"freq clamp: {s['frequency']}"
            if s["kind"] == "soundscape":
                assert isinstance(s.get("soundscape"), str) and s["soundscape"]
            if s["kind"] == "sleep":
                assert isinstance(s.get("duration_min"), int)
                assert s["duration_min"] > 0
            if s["kind"] == "ai_prescription":
                assert isinstance(s.get("intent"), str) and s["intent"].strip()

    def test_multi_turn_with_history(self, auth_headers):
        h = [
            {"role": "assistant", "text": "Hello, how are you feeling right now?"},
            {"role": "user", "text": "Stressed from work"},
            {"role": "assistant", "text": "I hear you. Let's try something grounding."},
        ]
        payload = {"message": "Show me a different option please", "history": h, "session_id": "test-session-iter32-2"}
        r = requests.post(f"{API}/me/agent/chat", headers=auth_headers, json=payload, timeout=60)
        assert r.status_code == 200, f"Status {r.status_code}: {r.text[:400]}"
        data = r.json()
        assert isinstance(data.get("message"), str) and data["message"].strip()
        assert isinstance(data.get("suggestions"), list)


# --- Rate limiting ------------------------------------------------------------
class TestAgentChatRateLimit:
    def test_rate_limit_429_after_burst(self, auth_headers):
        # Burst capacity is 8 per server config; refill ~1/8s. Fire ~15 quickly.
        statuses = []
        for i in range(15):
            r = requests.post(
                f"{API}/me/agent/chat",
                headers=auth_headers,
                json={"message": f"burst test {i}", "history": [], "session_id": "test-session-burst"},
                timeout=60,
            )
            statuses.append(r.status_code)
            if r.status_code == 429:
                break
        assert 429 in statuses, f"Expected a 429 in burst; got {statuses}"
