"""Tests for the /api/support/contact endpoint (Support Bubble feature)."""
import os
import time
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://frequency-healer-31.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "JuzlUWlMMOjHM0u#m5qv0ds!oYp8"

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def db():
    # Read backend .env for DB_NAME
    dbname = DB_NAME
    try:
        with open("/app/backend/.env") as f:
            for line in f:
                if line.startswith("DB_NAME="):
                    dbname = line.strip().split("=", 1)[1].strip('"').strip("'")
                if line.startswith("MONGO_URL="):
                    mu = line.strip().split("=", 1)[1].strip('"').strip("'")
        client = MongoClient(mu)
    except Exception:
        client = MongoClient(MONGO_URL)
    return client[dbname]


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test_no_auth_returns_401():
    r = requests.post(f"{BASE_URL}/api/support/contact", json={"reason": "share_feedback", "message": "hello there friend"}, timeout=10)
    assert r.status_code in (401, 403), f"expected 401/403 got {r.status_code}"


def test_unknown_reason_400(token):
    r = requests.post(f"{BASE_URL}/api/support/contact", headers=_headers(token),
                      json={"reason": "hacker", "message": "hello there friend"}, timeout=10)
    assert r.status_code == 400
    assert r.json().get("detail") == "Unknown reason"


def test_short_message_422(token):
    r = requests.post(f"{BASE_URL}/api/support/contact", headers=_headers(token),
                      json={"reason": "share_feedback", "message": "short"}, timeout=10)
    assert r.status_code == 422


def test_valid_send_returns_200_and_persists(token, db):
    marker = f"TEST_support_msg_{int(time.time())}"
    r = requests.post(f"{BASE_URL}/api/support/contact", headers=_headers(token),
                      json={"reason": "share_feedback", "message": f"{marker} this is a real message"}, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert "delivered" in body
    assert body["message"] == "Thank you for reaching out. We will get back to you shortly."

    # Verify DB persistence
    doc = db.support_messages.find_one({"message": {"$regex": marker}})
    assert doc is not None, "support_messages row not found"
    for field in ["id", "user_id", "reason_key", "reason_label", "message", "reply_to_email", "ip", "user_agent", "created_at", "delivered", "provider"]:
        assert field in doc, f"missing field {field}"
    assert doc["reason_key"] == "share_feedback"
    assert doc["reason_label"] == "Share Feedback"


def test_html_injection_stored_raw(token, db):
    marker = f"TEST_xss_{int(time.time())}"
    payload_msg = f"{marker} <script>alert(1)</script> hello"
    r = requests.post(f"{BASE_URL}/api/support/contact", headers=_headers(token),
                      json={"reason": "other", "message": payload_msg}, timeout=15)
    assert r.status_code == 200
    doc = db.support_messages.find_one({"message": {"$regex": marker}})
    assert doc is not None
    # Stored raw (as-is)
    assert "<script>alert(1)</script>" in doc["message"]


def test_rate_limit_429_within_4_rapid_sends(token):
    # Fresh user bucket may have been partially consumed by previous tests. Send 4 quick calls;
    # at least one within the batch should be 429 (capacity=3).
    codes = []
    for i in range(4):
        r = requests.post(f"{BASE_URL}/api/support/contact", headers=_headers(token),
                          json={"reason": "other", "message": f"TEST_rl_{i} rapid rate limit probe message"}, timeout=10)
        codes.append(r.status_code)
    assert 429 in codes, f"expected at least one 429 in {codes}"


@pytest.fixture(scope="module", autouse=True)
def _cleanup(db):
    yield
    try:
        db.support_messages.delete_many({"message": {"$regex": "^TEST_"}})
    except Exception:
        pass
