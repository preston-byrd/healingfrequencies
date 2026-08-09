"""Tests for the Admin Support Inbox endpoints under /api/admin/support."""
import os
import uuid
from datetime import datetime, timezone
import pytest
import requests
from pymongo import MongoClient


def _db():
    mu = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    dbname = os.environ.get("DB_NAME", "test_database")
    try:
        with open("/app/backend/.env") as f:
            for line in f:
                if line.startswith("DB_NAME="):
                    dbname = line.strip().split("=", 1)[1].strip('"').strip("'")
                if line.startswith("MONGO_URL="):
                    mu = line.strip().split("=", 1)[1].strip('"').strip("'")
    except Exception:
        pass
    return MongoClient(mu)[dbname]


def _seed_ticket_direct(user_id, user_email, marker, i, reason="report_issue", label="Report an Issue"):
    """Insert directly into Mongo — bypasses /support/contact rate limits."""
    db = _db()
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "user_email": user_email,
        "user_name": "Test User",
        "reason_key": reason,
        "reason_label": label,
        "message": f"TEST_seed_{marker} ticket {i} needs help please",
        "reply_to_email": user_email,
        "reply_to_name": "Test User",
        "ip": "127.0.0.1",
        "user_agent": "pytest",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "delivered": False,
        "provider": "none",
        "status": "open",
        "admin_replies": [],
        "resolved_at": None,
        "resolved_by": None,
    }
    db.support_messages.insert_one(doc)
    return doc["id"]

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://frequency-healer-31.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "JuzlUWlMMOjHM0u#m5qv0ds!oYp8"


def _h(t):
    return {"Authorization": f"Bearer {t}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def user_ctx():
    """Register a fresh non-admin user."""
    email = f"TEST_user_{uuid.uuid4().hex[:8]}@example.com"
    password = "TestPassword123!"
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={"email": email, "password": password, "name": "Test User"}, timeout=15)
    assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
    tok = r.json().get("token")
    if not tok:
        r2 = requests.post(f"{BASE_URL}/api/auth/login",
                           json={"email": email, "password": password}, timeout=15)
        tok = r2.json()["token"]
    return {"email": email, "token": tok}


@pytest.fixture(scope="module")
def seeded_tickets(user_ctx, admin_token):
    """Seed a few support_messages so we have known content."""
    unique_marker = uuid.uuid4().hex[:8]
    # Get user_id from /auth/me
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=_h(user_ctx["token"]), timeout=15).json()
    uid = me.get("id") or me.get("user", {}).get("id")
    assert uid, f"could not get user id: {me}"
    ids = []
    for i in range(3):
        ids.append(_seed_ticket_direct(uid, user_ctx["email"], unique_marker, i))
    return {"ids": ids, "marker": unique_marker}


# --- Auth gate ---------------------------------------------------------------

class TestAuthGate:
    def test_unauth_returns_401(self):
        r = requests.get(f"{BASE_URL}/api/admin/support", timeout=15)
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"

    def test_non_admin_returns_403(self, user_ctx):
        r = requests.get(f"{BASE_URL}/api/admin/support",
                         headers=_h(user_ctx["token"]), timeout=15)
        assert r.status_code == 403


# --- Listing / filters / search / pagination --------------------------------

class TestListing:
    def test_list_all_shape(self, admin_token, seeded_tickets):
        r = requests.get(f"{BASE_URL}/api/admin/support?status=all",
                         headers=_h(admin_token), timeout=15)
        assert r.status_code == 200
        data = r.json()
        for k in ("items", "total", "offset", "limit", "counts"):
            assert k in data, f"missing key {k}"
        for k in ("open", "resolved", "all"):
            assert k in data["counts"]
        assert data["counts"]["all"] == data["counts"]["open"] + data["counts"]["resolved"]
        assert data["total"] >= len(seeded_tickets["ids"])

    def test_list_open_only(self, admin_token, seeded_tickets):
        r = requests.get(f"{BASE_URL}/api/admin/support?status=open",
                         headers=_h(admin_token), timeout=15)
        assert r.status_code == 200
        for it in r.json()["items"]:
            assert it.get("status", "open") == "open"

    def test_list_resolved_only(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/support?status=resolved",
                         headers=_h(admin_token), timeout=15)
        assert r.status_code == 200
        for it in r.json()["items"]:
            assert it["status"] == "resolved"

    def test_search_q_filter(self, admin_token, seeded_tickets):
        marker = seeded_tickets["marker"]
        r = requests.get(f"{BASE_URL}/api/admin/support",
                         params={"status": "all", "q": marker, "limit": 25},
                         headers=_h(admin_token), timeout=15)
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) >= 1, f"search for {marker} returned nothing"
        for it in items:
            body = (it.get("message") or "") + (it.get("user_email") or "") + (it.get("reason_label") or "")
            assert marker.lower() in body.lower()

    def test_pagination_limit_caps_at_100(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/support",
                         params={"status": "all", "limit": 500},
                         headers=_h(admin_token), timeout=15)
        assert r.status_code == 200
        assert r.json()["limit"] == 100

    def test_pagination_skip(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/support",
                         params={"status": "all", "skip": 1, "limit": 5},
                         headers=_h(admin_token), timeout=15)
        assert r.status_code == 200
        assert r.json()["offset"] == 1
        assert r.json()["limit"] == 5


# --- Resolve / Reopen -------------------------------------------------------

class TestResolveReopen:
    def test_resolve_and_idempotent(self, admin_token, seeded_tickets):
        tid = seeded_tickets["ids"][0]
        r = requests.post(f"{BASE_URL}/api/admin/support/{tid}/resolve",
                          headers=_h(admin_token), timeout=15)
        assert r.status_code == 200
        doc = r.json()["message"]
        assert doc["status"] == "resolved"
        assert doc["resolved_at"]
        assert doc["resolved_by"]
        # Idempotent
        r2 = requests.post(f"{BASE_URL}/api/admin/support/{tid}/resolve",
                           headers=_h(admin_token), timeout=15)
        assert r2.status_code == 200
        assert r2.json()["message"]["status"] == "resolved"

    def test_reopen(self, admin_token, seeded_tickets):
        tid = seeded_tickets["ids"][0]
        r = requests.post(f"{BASE_URL}/api/admin/support/{tid}/reopen",
                          headers=_h(admin_token), timeout=15)
        assert r.status_code == 200
        doc = r.json()["message"]
        assert doc["status"] == "open"
        assert doc["resolved_at"] is None
        assert doc["resolved_by"] is None

    def test_resolve_unknown_404(self, admin_token):
        r = requests.post(f"{BASE_URL}/api/admin/support/does-not-exist/resolve",
                          headers=_h(admin_token), timeout=15)
        assert r.status_code == 404

    def test_reopen_unknown_404(self, admin_token):
        r = requests.post(f"{BASE_URL}/api/admin/support/does-not-exist/reopen",
                          headers=_h(admin_token), timeout=15)
        assert r.status_code == 404


# --- Reply ------------------------------------------------------------------

class TestReply:
    def test_reply_appends_and_marks_resolved(self, admin_token, seeded_tickets):
        tid = seeded_tickets["ids"][1]
        r = requests.post(f"{BASE_URL}/api/admin/support/{tid}/reply",
                          json={"message": "Thanks for reaching out — here is our answer.",
                                "mark_resolved": True},
                          headers=_h(admin_token), timeout=30)
        assert r.status_code == 200, r.text
        doc = r.json()["message"]
        assert doc["status"] == "resolved"
        assert len(doc["admin_replies"]) >= 1
        last = doc["admin_replies"][-1]
        assert "Thanks for reaching out" in last["message"]
        assert last["admin_email"] == ADMIN_EMAIL
        assert "at" in last
        # delivery may be False in preview — do not assert

    def test_reply_no_mark_resolved_stays_open(self, admin_token, seeded_tickets):
        tid = seeded_tickets["ids"][2]
        r = requests.post(f"{BASE_URL}/api/admin/support/{tid}/reply",
                          json={"message": "Following up separately, staying open.",
                                "mark_resolved": False},
                          headers=_h(admin_token), timeout=30)
        assert r.status_code == 200, r.text
        assert r.json()["message"]["status"] == "open"

    def test_reply_too_short_422(self, admin_token, seeded_tickets):
        tid = seeded_tickets["ids"][2]
        r = requests.post(f"{BASE_URL}/api/admin/support/{tid}/reply",
                          json={"message": "hi", "mark_resolved": False},
                          headers=_h(admin_token), timeout=15)
        assert r.status_code == 422

    def test_reply_unknown_id_404(self, admin_token):
        r = requests.post(f"{BASE_URL}/api/admin/support/nope-nope/reply",
                          json={"message": "This is a reply body.", "mark_resolved": False},
                          headers=_h(admin_token), timeout=15)
        assert r.status_code == 404


# --- Delete -----------------------------------------------------------------

class TestDelete:
    def test_delete_and_confirm_removed(self, admin_token, user_ctx):
        # Seed one ticket directly via Mongo
        marker = uuid.uuid4().hex[:8]
        me = requests.get(f"{BASE_URL}/api/auth/me", headers=_h(user_ctx["token"]), timeout=15).json()
        uid = me.get("id") or me.get("user", {}).get("id")
        tid = _seed_ticket_direct(uid, user_ctx["email"], marker, 0)

        d = requests.delete(f"{BASE_URL}/api/admin/support/{tid}",
                            headers=_h(admin_token), timeout=15)
        assert d.status_code == 200
        # Confirm gone
        list_r = requests.get(f"{BASE_URL}/api/admin/support",
                              params={"status": "all", "q": marker},
                              headers=_h(admin_token), timeout=15)
        assert not any(x["id"] == tid for x in list_r.json()["items"])

    def test_delete_unknown_404(self, admin_token):
        r = requests.delete(f"{BASE_URL}/api/admin/support/does-not-exist",
                            headers=_h(admin_token), timeout=15)
        assert r.status_code == 404
