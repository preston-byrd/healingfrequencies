"""Backend tests for the new /api/me/prefs endpoints + /payments/status plan field."""
import os
import uuid
import asyncio
from datetime import datetime, timezone, timedelta
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://frequency-healer-31.preview.emergentagent.com').rstrip('/')
API = f"{BASE_URL}/api"


def _register(email=None, password="testpass123"):
    email = email or f"TEST_prefs_{uuid.uuid4().hex[:8]}@example.com"
    r = requests.post(f"{API}/auth/register", json={"email": email, "password": password, "name": "Pref"})
    assert r.status_code == 200, r.text
    body = r.json()
    return email, body["token"], body.get("id")


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


def _grant_pro(user_id):
    """Direct mongo upgrade — /me/trial returns 410 since Feb 2026 migration,
    so prefs tests grant Pro by setting pro_until directly."""
    from motor.motor_asyncio import AsyncIOMotorClient
    mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
    db_name = os.environ.get('DB_NAME', 'test_database')
    until = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

    async def _go():
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        await db.users.update_one(
            {"id": user_id},
            {"$set": {"plan": "trial", "pro_until": until, "trial_used": True}},
        )
    asyncio.new_event_loop().run_until_complete(_go())


# --- /api/me/prefs ---
class TestPrefs:
    def test_fresh_user_empty_prefs(self):
        _, t, _uid = _register()
        r = requests.get(f"{API}/me/prefs", headers=_hdr(t))
        assert r.status_code == 200
        assert r.json() == {}

    def test_auth_required_get(self):
        r = requests.get(f"{API}/me/prefs")
        assert r.status_code == 401

    def test_auth_required_put(self):
        r = requests.put(f"{API}/me/prefs", json={"frequency": 528})
        assert r.status_code == 401

    def test_put_full_payload_then_get_roundtrip(self):
        _, t, _uid = _register()
        # Grant Pro directly (via mongo) so Pro-only fields are accepted (defense-in-depth strips them otherwise).
        _grant_pro(_uid)
        payload = {
            "frequency": 528,
            "duration_minutes": 15,
            "waveform": "triangle",
            "binaural": 5,
            "golden_stack": True,
            "breathwork": True,
            "tone_volume": 0.4,
            "ambient": {"rain": 0.5, "ocean": 0.3},
        }
        r = requests.put(f"{API}/me/prefs", headers=_hdr(t), json=payload)
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}

        rg = requests.get(f"{API}/me/prefs", headers=_hdr(t))
        assert rg.status_code == 200
        data = rg.json()
        for k, v in payload.items():
            assert data[k] == v, f"{k} mismatch: {data.get(k)} != {v}"
        assert "updated_at" in data

    def test_partial_put_merges(self):
        _, t, _uid = _register()
        # Grant Pro directly (via mongo) so ambient (Pro-only) is accepted.
        _grant_pro(_uid)
        # initial
        full = {
            "frequency": 432, "duration_minutes": 20, "waveform": "sine",
            "binaural": 0, "ambient": {"rain": 0.7}, "tone_volume": 0.3,
        }
        r1 = requests.put(f"{API}/me/prefs", headers=_hdr(t), json=full)
        assert r1.status_code == 200
        # partial: only frequency
        r2 = requests.put(f"{API}/me/prefs", headers=_hdr(t), json={"frequency": 639})
        assert r2.status_code == 200
        data = requests.get(f"{API}/me/prefs", headers=_hdr(t)).json()
        assert data["frequency"] == 639
        assert data["duration_minutes"] == 20
        assert data["waveform"] == "sine"
        assert data["ambient"] == {"rain": 0.7}
        assert data["tone_volume"] == 0.3

    def test_non_pro_pro_fields_stripped_then_kept_after_trial(self):
        """Defense-in-depth: PUT Pro-only fields as non-Pro -> not saved.
        After /me/trial, same PUT succeeds and fields appear in GET."""
        _, t, _uid = _register()
        pro_payload = {
            "frequency": 528,
            "duration_minutes": 15,
            "tone_volume": 0.5,
            "golden_stack": True,
            "binaural": 6,
            "breathwork": True,
            "ambient": {"rain": 0.6},
        }
        r = requests.put(f"{API}/me/prefs", headers=_hdr(t), json=pro_payload)
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}  # idempotent regardless

        data = requests.get(f"{API}/me/prefs", headers=_hdr(t)).json()
        # Non-Pro fields persisted
        assert data.get("frequency") == 528
        assert data.get("duration_minutes") == 15
        assert data.get("tone_volume") == 0.5
        # Pro-only fields STRIPPED
        assert "golden_stack" not in data, f"golden_stack leaked: {data}"
        assert "binaural" not in data, f"binaural leaked: {data}"
        assert "breathwork" not in data, f"breathwork leaked: {data}"
        assert "ambient" not in data, f"ambient leaked: {data}"

        # Now upgrade via direct mongo grant (since /me/trial returns 410)
        _grant_pro(_uid)

        # Same Pro payload should now persist all fields
        r2 = requests.put(f"{API}/me/prefs", headers=_hdr(t), json=pro_payload)
        assert r2.status_code == 200
        data2 = requests.get(f"{API}/me/prefs", headers=_hdr(t)).json()
        assert data2.get("golden_stack") is True
        assert data2.get("binaural") == 6
        assert data2.get("breathwork") is True
        assert data2.get("ambient") == {"rain": 0.6}
        assert data2.get("tone_volume") == 0.5

    @pytest.mark.parametrize("payload", [
        {"frequency": 0.05},
        {"frequency": 25000},
        {"duration_minutes": 0},
        {"duration_minutes": 181},
        {"waveform": "noise"},
        {"binaural": -1},
        {"binaural": 41},
        {"tone_volume": -0.1},
        {"tone_volume": 1.1},
    ])
    def test_validation_422(self, payload):
        _, t, _uid = _register()
        r = requests.put(f"{API}/me/prefs", headers=_hdr(t), json=payload)
        assert r.status_code == 422, f"{payload} -> {r.status_code} {r.text}"


# --- /api/payments/status/{sid} now returns 'plan' ---
class TestPaymentsStatusPlan:
    def test_status_returns_plan_field(self):
        _, t, _uid = _register()
        # Create monthly checkout
        rc = requests.post(
            f"{API}/me/checkout", headers=_hdr(t),
            json={"plan": "monthly", "origin_url": BASE_URL},
        )
        assert rc.status_code == 200, rc.text
        sid = rc.json()["session_id"]

        rs = requests.get(f"{API}/payments/status/{sid}", headers=_hdr(t))
        assert rs.status_code == 200, rs.text
        data = rs.json()
        assert "plan" in data
        assert data["plan"] == "monthly"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
