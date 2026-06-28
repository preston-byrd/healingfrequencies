"""Tests for PUT /api/me/profile (edit user name) — iteration 15."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://frequency-healer-31.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"
ADMIN_EMAIL = "admin@example.com"
ADMIN_PW = os.environ.get("ADMIN_TEST_PASSWORD") or __import__("tests._creds", fromlist=["ADMIN_PASSWORD"]).ADMIN_PASSWORD


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PW})
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    body = r.json()
    token = body.get("access_token") or body.get("token")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    yield s
    # cleanup: restore admin name back to "Admin"
    try:
        s.put(f"{API}/me/profile", json={"name": "Admin"})
    except Exception:
        pass


# --- positive: valid name updates and persists -------------------------------
def test_update_profile_with_valid_name(admin_session):
    r = admin_session.put(f"{API}/me/profile", json={"name": "Solaris Admin"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["name"] == "Solaris Admin"
    assert data["email"] == ADMIN_EMAIL
    assert "id" in data

    # GET to verify persistence
    me = admin_session.get(f"{API}/auth/me")
    assert me.status_code == 200
    assert me.json()["name"] == "Solaris Admin"


# --- validation: empty string ------------------------------------------------
def test_update_profile_empty_string_rejected(admin_session):
    r = admin_session.put(f"{API}/me/profile", json={"name": ""})
    # Pydantic min_length=1 -> 422; some configs may map to 400
    assert r.status_code in (400, 422), r.text


# --- validation: whitespace-only is rejected with 400 ------------------------
def test_update_profile_whitespace_rejected(admin_session):
    r = admin_session.put(f"{API}/me/profile", json={"name": "   "})
    assert r.status_code == 400, r.text
    detail = r.json().get("detail", "")
    assert "empty" in detail.lower()


# --- validation: too long (>80) ---------------------------------------------
def test_update_profile_too_long_rejected(admin_session):
    long_name = "x" * 81
    r = admin_session.put(f"{API}/me/profile", json={"name": long_name})
    assert r.status_code == 422, r.text


# --- auth: no token => 401 (or 403) -----------------------------------------
def test_update_profile_requires_auth():
    r = requests.put(f"{API}/me/profile", json={"name": "Hacker"})
    assert r.status_code in (401, 403), r.text


# --- cleanup restore name to Admin ------------------------------------------
def test_zz_restore_admin_name(admin_session):
    r = admin_session.put(f"{API}/me/profile", json={"name": "Admin"})
    assert r.status_code == 200
    assert r.json()["name"] == "Admin"
    me = admin_session.get(f"{API}/auth/me")
    assert me.status_code == 200 and me.json()["name"] == "Admin"
