"""Password reset feature tests (forgot-password + reset-password).
Covers privacy-safe generic responses, rate limiting, token validation,
single-use enforcement, expiry semantics, and full E2E reset -> login flow.
"""
import os
import time
import uuid
from datetime import datetime, timezone, timedelta

import jwt
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/") or \
    open("/app/frontend/.env").read().split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip().rstrip("/")
API = f"{BASE_URL}/api"

# Load JWT secret from backend env
def _load_env(path, key):
    for ln in open(path):
        if ln.startswith(key + "="):
            v = ln.split("=", 1)[1].strip()
            return v.strip('"').strip("'")
    return None

JWT_SECRET = _load_env("/app/backend/.env", "JWT_SECRET")
JWT_ALGORITHM = "HS256"
MONGO_URL = _load_env("/app/backend/.env", "MONGO_URL")
DB_NAME = _load_env("/app/backend/.env", "DB_NAME")

mongo = MongoClient(MONGO_URL)
db = mongo[DB_NAME]


@pytest.fixture(scope="module")
def test_user():
    email = f"TEST_pwreset_{uuid.uuid4().hex[:8]}@example.com"
    password = "OriginalPass123!"
    r = requests.post(f"{API}/auth/register",
                      json={"email": email, "password": password, "name": "PW Reset Test"})
    assert r.status_code in (200, 201), r.text
    yield {"email": email, "password": password, "id": r.json()["id"]}
    # Cleanup
    try:
        db.users.delete_one({"email": email})
        db.password_reset_tokens.delete_many({"email": email})
    except Exception:
        pass


class TestForgotPassword:
    def test_nonexistent_email_generic_response(self):
        r = requests.post(f"{API}/auth/forgot-password",
                          json={"email": f"nonexistent_{uuid.uuid4().hex[:8]}@example.com"})
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True
        assert "If an account exists" in data.get("message", "")

    def test_existing_email_same_generic_response(self, test_user):
        r = requests.post(f"{API}/auth/forgot-password", json={"email": test_user["email"]})
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True
        assert "If an account exists" in data.get("message", "")

    def test_invalid_email_format(self):
        r = requests.post(f"{API}/auth/forgot-password", json={"email": "not-an-email"})
        assert r.status_code == 422


class TestResetPassword:
    def test_invalid_token(self):
        r = requests.post(f"{API}/auth/reset-password",
                          json={"token": "not.a.jwt", "new_password": "NewPass1234"})
        assert r.status_code == 400
        assert "invalid" in r.json().get("detail", "").lower()

    def test_tampered_token(self):
        # Signed with wrong secret
        tok = jwt.encode({"sub": "x", "email": "x@y.z", "type": "password_reset",
                          "jti": "abc", "iat": datetime.now(timezone.utc).timestamp(),
                          "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
                         "wrong-secret", algorithm=JWT_ALGORITHM)
        r = requests.post(f"{API}/auth/reset-password",
                          json={"token": tok, "new_password": "NewPass1234"})
        assert r.status_code == 400

    def test_short_password_rejected(self):
        r = requests.post(f"{API}/auth/reset-password",
                          json={"token": "x", "new_password": "short"})
        assert r.status_code == 422

    def test_expired_token(self, test_user):
        now = datetime.now(timezone.utc)
        jti = str(uuid.uuid4())
        # Insert record
        db.password_reset_tokens.insert_one({
            "id": jti, "user_id": test_user["id"], "email": test_user["email"],
            "created_at": (now - timedelta(hours=1)).isoformat(),
            "expires_at": (now - timedelta(minutes=30)).isoformat(),
            "used_at": None, "ip": "127.0.0.1",
        })
        tok = jwt.encode({
            "sub": test_user["id"], "email": test_user["email"], "type": "password_reset",
            "jti": jti, "iat": (now - timedelta(hours=1)).timestamp(),
            "exp": now - timedelta(minutes=30),
        }, JWT_SECRET, algorithm=JWT_ALGORITHM)
        r = requests.post(f"{API}/auth/reset-password",
                          json={"token": tok, "new_password": "NewPass1234"})
        assert r.status_code == 400
        assert "expired" in r.json().get("detail", "").lower()

    def test_wrong_type_token(self, test_user):
        now = datetime.now(timezone.utc)
        tok = jwt.encode({
            "sub": test_user["id"], "email": test_user["email"], "type": "access",
            "jti": str(uuid.uuid4()), "iat": now.timestamp(),
            "exp": now + timedelta(minutes=10),
        }, JWT_SECRET, algorithm=JWT_ALGORITHM)
        r = requests.post(f"{API}/auth/reset-password",
                          json={"token": tok, "new_password": "NewPass1234"})
        assert r.status_code == 400

    def test_e2e_reset_flow_and_reuse(self, test_user):
        # Forge a valid token + persist jti
        now = datetime.now(timezone.utc)
        jti = str(uuid.uuid4())
        db.password_reset_tokens.insert_one({
            "id": jti, "user_id": test_user["id"], "email": test_user["email"],
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=30)).isoformat(),
            "used_at": None, "ip": "127.0.0.1",
        })
        tok = jwt.encode({
            "sub": test_user["id"], "email": test_user["email"], "type": "password_reset",
            "jti": jti, "iat": now.timestamp(),
            "exp": now + timedelta(minutes=30),
        }, JWT_SECRET, algorithm=JWT_ALGORITHM)

        new_pw = "BrandNewPass456!"
        r = requests.post(f"{API}/auth/reset-password",
                          json={"token": tok, "new_password": new_pw})
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True

        # Reuse should fail
        r2 = requests.post(f"{API}/auth/reset-password",
                           json={"token": tok, "new_password": "AnotherPw999"})
        assert r2.status_code == 400
        assert "already been used" in r2.json().get("detail", "").lower()

        # Login with new password succeeds
        rl = requests.post(f"{API}/auth/login",
                           json={"email": test_user["email"], "password": new_pw})
        assert rl.status_code == 200, rl.text
        assert "token" in rl.json()

        # Old password fails
        ro = requests.post(f"{API}/auth/login",
                           json={"email": test_user["email"], "password": test_user["password"]})
        assert ro.status_code == 401

        # Update fixture so subsequent tests know new pw
        test_user["password"] = new_pw


class TestRateLimit:
    def test_forgot_password_email_rate_limit(self):
        """3 requests per email per 15 min should trigger 429 on 4th."""
        email = f"TEST_rl_{uuid.uuid4().hex[:6]}@example.com"
        codes = []
        for _ in range(8):
            r = requests.post(f"{API}/auth/forgot-password", json={"email": email})
            codes.append(r.status_code)
            if r.status_code == 429:
                break
        assert 429 in codes, f"Expected 429 within 8 requests, got {codes}"


class TestRegressionAuth:
    def test_register_login_me_flow(self):
        email = f"TEST_reg_{uuid.uuid4().hex[:6]}@example.com"
        pw = "SomePass1234"
        r = requests.post(f"{API}/auth/register",
                          json={"email": email, "password": pw, "name": "Reg Test"})
        assert r.status_code in (200, 201), r.text
        tok = r.json()["token"]

        rl = requests.post(f"{API}/auth/login", json={"email": email, "password": pw})
        assert rl.status_code == 200
        tok2 = rl.json()["token"]

        me = requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {tok2}"})
        assert me.status_code == 200
        assert me.json()["email"].lower() == email.lower()

        # Password change
        pw_new = "AnotherPass9999"
        pc = requests.post(f"{API}/me/password",
                           headers={"Authorization": f"Bearer {tok2}"},
                           json={"current_password": pw, "new_password": pw_new})
        assert pc.status_code == 200, pc.text

        # Old token invalidated
        me_old = requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {tok2}"})
        assert me_old.status_code == 401

        # New password works
        rl2 = requests.post(f"{API}/auth/login", json={"email": email, "password": pw_new})
        assert rl2.status_code == 200

        # Cleanup
        try:
            db.users.delete_one({"email": email})
        except Exception:
            pass
