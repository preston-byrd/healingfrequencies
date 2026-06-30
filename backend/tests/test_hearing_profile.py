"""Backend tests for the Equalizer Calibration / hearing profile feature (iter38).

Endpoints under test:
  - GET    /api/me/hearing-profile  (auth)
  - POST   /api/me/hearing-profile  (auth)
  - DELETE /api/me/hearing-profile  (auth)

Also touches a sanity ping of /me/agent/chat + /me/agent/checkin for regression.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://frequency-healer-31.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "JuzlUWlMMOjHM0u#m5qv0ds!oYp8"

CANONICAL_BANDS = [60, 125, 250, 500, 1000, 2000, 4000, 8000, 12000]


# ---------- shared fixtures ---------------------------------------------------
@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def admin_token(api):
    r = api.post(f"{BASE_URL}/api/auth/login", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
    })
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code} {r.text}")
    data = r.json()
    tok = data.get("token") or data.get("access_token")
    assert tok, f"Login response missing token: {data}"
    return tok


@pytest.fixture
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def fresh_profile(api, auth_headers):
    """DELETE the profile so each test starts from a clean state."""
    api.delete(f"{BASE_URL}/api/me/hearing-profile", headers=auth_headers)
    yield


# ---------- GET ---------------------------------------------------------------
class TestGetHearingProfile:
    def test_get_after_delete_returns_null(self, api, auth_headers, fresh_profile):
        r = api.get(f"{BASE_URL}/api/me/hearing-profile", headers=auth_headers)
        assert r.status_code == 200
        assert r.json() is None

    def test_get_without_auth_returns_401(self, api, fresh_profile):
        r = requests.get(f"{BASE_URL}/api/me/hearing-profile")
        assert r.status_code in (401, 403), f"got {r.status_code}"


# ---------- POST: real calibration -------------------------------------------
class TestPostCalibration:
    def test_full_calibration_with_mixed_heard(self, api, auth_headers, fresh_profile):
        # Mark 60, 8000, 12000 as not heard; rest heard.
        unheard = {60, 8000, 12000}
        payload_bands = [{"freq": f, "heard": f not in unheard} for f in CANONICAL_BANDS]
        r = api.post(f"{BASE_URL}/api/me/hearing-profile",
                     json={"bands": payload_bands}, headers=auth_headers)
        assert r.status_code == 200, r.text
        prof = r.json()
        assert prof["skipped"] is False
        assert prof["test_level_db"] == -30 or prof["test_level_db"] == -30.0
        assert isinstance(prof["calibrated_at"], str) and "T" in prof["calibrated_at"]
        bands = prof["bands"]
        assert len(bands) == 9
        for band in bands:
            assert set(band.keys()) >= {"freq", "heard", "gain_db"}
            if band["freq"] in unheard:
                assert band["heard"] is False
                assert band["gain_db"] == 6.0 or band["gain_db"] == 6
            else:
                assert band["heard"] is True
                assert band["gain_db"] == 0.0 or band["gain_db"] == 0
        # Persistence — GET should mirror what POST returned
        g = api.get(f"{BASE_URL}/api/me/hearing-profile", headers=auth_headers)
        assert g.status_code == 200
        gp = g.json()
        assert gp is not None
        assert len(gp["bands"]) == 9
        assert gp["skipped"] is False

    def test_out_of_range_band_ignored_fallback_to_canonical(self, api, auth_headers, fresh_profile):
        # One valid band (1000 Hz, not heard) and one invalid (99999 Hz, not heard)
        payload = {"bands": [
            {"freq": 1000, "heard": False},
            {"freq": 99999, "heard": False},
        ]}
        r = api.post(f"{BASE_URL}/api/me/hearing-profile", json=payload, headers=auth_headers)
        assert r.status_code == 200, r.text
        prof = r.json()
        bands = {b["freq"]: b for b in prof["bands"]}
        assert set(bands.keys()) == set(CANONICAL_BANDS)
        assert bands[1000]["heard"] is False
        assert bands[1000]["gain_db"] == 6.0 or bands[1000]["gain_db"] == 6
        for f in CANONICAL_BANDS:
            if f == 1000:
                continue
            assert bands[f]["heard"] is True
            assert bands[f]["gain_db"] in (0, 0.0)
        # 99999 must not appear
        assert 99999 not in bands


# ---------- POST: skipped path -----------------------------------------------
class TestPostSkipped:
    def test_skipped_stub(self, api, auth_headers, fresh_profile):
        r = api.post(f"{BASE_URL}/api/me/hearing-profile", json={"skipped": True}, headers=auth_headers)
        assert r.status_code == 200, r.text
        prof = r.json()
        assert prof["bands"] == []
        assert prof["skipped"] is True
        assert isinstance(prof["calibrated_at"], str)
        # Verify GET returns the same stub (so client won't re-prompt)
        g = api.get(f"{BASE_URL}/api/me/hearing-profile", headers=auth_headers).json()
        assert g is not None
        assert g["skipped"] is True
        assert g["bands"] == []


# ---------- POST: invalid -----------------------------------------------------
class TestPostInvalid:
    def test_empty_body_returns_400(self, api, auth_headers, fresh_profile):
        r = api.post(f"{BASE_URL}/api/me/hearing-profile", json={}, headers=auth_headers)
        assert r.status_code == 400, r.text

    def test_no_auth_returns_401(self, api, fresh_profile):
        r = requests.post(f"{BASE_URL}/api/me/hearing-profile",
                          json={"bands": [{"freq": 1000, "heard": True}]})
        assert r.status_code in (401, 403)


# ---------- DELETE ------------------------------------------------------------
class TestDelete:
    def test_delete_then_get_null(self, api, auth_headers):
        # First make sure something is present.
        api.post(f"{BASE_URL}/api/me/hearing-profile", json={"skipped": True}, headers=auth_headers)
        r = api.delete(f"{BASE_URL}/api/me/hearing-profile", headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}
        g = api.get(f"{BASE_URL}/api/me/hearing-profile", headers=auth_headers)
        assert g.status_code == 200
        assert g.json() is None


# ---------- Regression sanity for agent endpoints ----------------------------
class TestAgentRegression:
    def test_agent_chat_ping(self, api, auth_headers):
        r = api.post(f"{BASE_URL}/api/me/agent/chat",
                     json={"message": "ping"}, headers=auth_headers)
        # Either 200 (with reply) or rate-limited 429 — both acceptable as alive signals
        assert r.status_code in (200, 429), f"{r.status_code}: {r.text[:200]}"

    def test_agent_checkin_ping(self, api, auth_headers):
        payload = {
            "message": "ping from regression test",
            "suggestion": {"kind": "preset", "preset": "Calm"},
        }
        r = api.post(f"{BASE_URL}/api/me/agent/checkin", json=payload, headers=auth_headers)
        assert r.status_code in (200, 429), f"{r.status_code}: {r.text[:200]}"
