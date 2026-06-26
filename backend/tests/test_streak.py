"""Backend tests for Healing Frequencies streak feature + regression for auth/sessions."""
import os
import uuid
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://frequency-healer-31.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = os.environ.get("ADMIN_TEST_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_TEST_PASSWORD", "admin123")


@pytest.fixture(scope="module")
def fresh_user():
    """Register a brand new user and return its auth header + identity."""
    email = f"TEST_streak_{uuid.uuid4().hex[:10]}@example.com"
    password = "testpass123"
    r = requests.post(f"{API}/auth/register", json={"email": email, "password": password, "name": "Streak Tester"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    return {"email": email, "password": password, "token": token,
            "headers": {"Authorization": f"Bearer {token}"}}


# --- Auth regression ----------------------------------------------------------
def test_admin_login_works():
    r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    data = r.json()
    assert "token" in data and data["email"] == ADMIN_EMAIL


def test_streak_requires_auth_get():
    r = requests.get(f"{API}/streak")
    assert r.status_code == 401


def test_streak_requires_auth_post():
    r = requests.post(f"{API}/streak/checkin", json={"minutes": 1.0})
    assert r.status_code == 401


# --- Streak feature -----------------------------------------------------------
def test_streak_initial_state_for_new_user(fresh_user):
    r = requests.get(f"{API}/streak", headers=fresh_user["headers"])
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["current_streak"] == 0
    assert data["longest_streak"] == 0
    assert data["last_check_in"] is None
    assert data["total_sessions"] == 0
    assert data["total_minutes"] == 0
    assert data["checked_in_today"] is False


def test_streak_first_checkin(fresh_user):
    r = requests.post(f"{API}/streak/checkin", json={"minutes": 2.5}, headers=fresh_user["headers"])
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["current_streak"] == 1
    assert data["longest_streak"] == 1
    assert data["last_check_in"] is not None
    # ISO date YYYY-MM-DD
    assert len(data["last_check_in"]) == 10 and data["last_check_in"][4] == "-"
    assert data["total_sessions"] == 1
    assert data["total_minutes"] == 2.5
    assert data["checked_in_today"] is True

    # GET verifies persistence
    g = requests.get(f"{API}/streak", headers=fresh_user["headers"])
    assert g.status_code == 200
    gd = g.json()
    assert gd["current_streak"] == 1
    assert gd["checked_in_today"] is True


def test_streak_same_day_checkin_does_not_increment(fresh_user):
    r = requests.post(f"{API}/streak/checkin", json={"minutes": 3.0}, headers=fresh_user["headers"])
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["current_streak"] == 1  # unchanged
    assert data["longest_streak"] == 1
    assert data["total_sessions"] == 2  # incremented
    assert data["total_minutes"] == pytest.approx(5.5)  # 2.5 + 3.0
    assert data["checked_in_today"] is True


# --- Sessions regression ------------------------------------------------------
def test_session_crud(fresh_user):
    payload = {"name": "TEST_session", "frequency": 432, "waveform": "sine",
               "binaural": 0, "duration_minutes": 5, "ambient": {"rain": 0.2}, "breathwork": False}
    r = requests.post(f"{API}/sessions", json=payload, headers=fresh_user["headers"])
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert r.json()["frequency"] == 432

    g = requests.get(f"{API}/sessions", headers=fresh_user["headers"])
    assert g.status_code == 200
    assert any(s["id"] == sid for s in g.json())

    d = requests.delete(f"{API}/sessions/{sid}", headers=fresh_user["headers"])
    assert d.status_code == 200
    assert d.json().get("ok") is True
