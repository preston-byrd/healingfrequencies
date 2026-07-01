"""Backend tests for Sound Bath session persistence (iter39).

Covers:
 - POST /api/sessions accepts optional sound_bath dict
 - GET  /api/sessions round-trips sound_bath
 - Backward compat: session without sound_bath still works
 - Free tier still gets 402 past the 3-session Basic-tier cap even for sound_bath payloads
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Fallback to frontend env file
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.strip().split("=", 1)[1].strip('"').rstrip("/")
                    break
    except FileNotFoundError:
        pass

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "JuzlUWlMMOjHM0u#m5qv0ds!oYp8"


# ---------------- fixtures ----------------
@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD
    }, timeout=15)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    tok = r.json().get("access_token") or r.json().get("token")
    assert tok, f"no token in login response: {r.json()}"
    return tok


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def free_user():
    """Register a fresh free user for tier-limit test."""
    email = f"TEST_soundbath_{uuid.uuid4().hex[:8]}@example.com"
    password = "testpass123"
    r = requests.post(f"{BASE_URL}/api/auth/register", json={
        "email": email, "password": password, "name": "Test SB"
    }, timeout=15)
    assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
    j = r.json()
    tok = j.get("access_token") or j.get("token")
    assert tok, f"no token in register response: {j}"
    return {"email": email, "password": password, "token": tok,
            "headers": {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}}


# ---------------- tests ----------------
class TestSoundBathSessions:
    def _cleanup(self, headers, session_id):
        try:
            requests.delete(f"{BASE_URL}/api/sessions/{session_id}", headers=headers, timeout=10)
        except Exception:
            pass

    def test_create_session_with_sound_bath_roundtrips(self, admin_headers):
        payload = {
            "name": "TEST_sb_crystal",
            "frequency": 432,
            "waveform": "sine",
            "binaural": 0,
            "duration_minutes": 10,
            "ambient": {},
            "breathwork": False,
            "sound_bath": {"preset_key": "crystal_bowl_bath", "label": "Crystal Bowl Bath"},
        }
        r = requests.post(f"{BASE_URL}/api/sessions", json=payload, headers=admin_headers, timeout=15)
        assert r.status_code in (200, 201), f"POST failed: {r.status_code} {r.text}"
        created = r.json()
        assert "id" in created
        assert created.get("sound_bath") == payload["sound_bath"], \
            f"sound_bath not echoed: {created.get('sound_bath')}"

        # GET verifies persistence
        g = requests.get(f"{BASE_URL}/api/sessions", headers=admin_headers, timeout=15)
        assert g.status_code == 200
        sessions = g.json()
        assert isinstance(sessions, list)
        match = next((s for s in sessions if s.get("id") == created["id"]), None)
        assert match is not None, "created session not returned by GET"
        assert match.get("sound_bath", {}).get("preset_key") == "crystal_bowl_bath"
        assert match.get("sound_bath", {}).get("label") == "Crystal Bowl Bath"

        self._cleanup(admin_headers, created["id"])

    def test_create_session_without_sound_bath_backward_compat(self, admin_headers):
        payload = {
            "name": "TEST_no_sb",
            "frequency": 528,
            "waveform": "sine",
            "duration_minutes": 5,
            "ambient": {"rain": 0.3},
            "breathwork": False,
        }
        r = requests.post(f"{BASE_URL}/api/sessions", json=payload, headers=admin_headers, timeout=15)
        assert r.status_code in (200, 201), f"POST failed: {r.status_code} {r.text}"
        created = r.json()
        assert created["frequency"] == 528
        assert created.get("sound_bath") in (None, {}, {"preset_key": None, "label": None}), \
            f"unexpected sound_bath on plain session: {created.get('sound_bath')}"
        self._cleanup(admin_headers, created["id"])

    def test_free_user_402_past_basic_limit_with_sound_bath(self, free_user):
        h = free_user["headers"]
        # Basic tier cap = 3 sessions. Create 3, then 4th (with sound_bath) should 402.
        ids = []
        try:
            for i in range(3):
                r = requests.post(f"{BASE_URL}/api/sessions", json={
                    "name": f"TEST_free_{i}", "frequency": 396 + i,
                    "duration_minutes": 5,
                }, headers=h, timeout=15)
                assert r.status_code in (200, 201), \
                    f"session {i} unexpectedly failed for free user: {r.status_code} {r.text}"
                ids.append(r.json()["id"])

            # 4th with sound_bath should be blocked
            r = requests.post(f"{BASE_URL}/api/sessions", json={
                "name": "TEST_free_sb_4",
                "frequency": 432,
                "duration_minutes": 10,
                "sound_bath": {"preset_key": "gong_bath", "label": "Gong Bath"},
            }, headers=h, timeout=15)
            assert r.status_code == 402, \
                f"expected 402 tier-limit, got {r.status_code}: {r.text}"
        finally:
            for sid in ids:
                try:
                    requests.delete(f"{BASE_URL}/api/sessions/{sid}", headers=h, timeout=10)
                except Exception:
                    pass
