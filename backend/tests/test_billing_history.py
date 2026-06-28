"""Tests for GET /api/me/transactions filter (only paid/fulfilled shown)."""
import os
import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient
import asyncio

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://frequency-healer-31.preview.emergentagent.com').rstrip('/')
ADMIN_EMAIL = os.environ.get("ADMIN_TEST_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_TEST_PASSWORD") or __import__("tests._creds", fromlist=["ADMIN_PASSWORD"]).ADMIN_PASSWORD

# Load backend env for mongo access
from dotenv import load_dotenv
load_dotenv('/app/backend/.env')
MONGO_URL = os.environ['MONGO_URL']
DB_NAME = os.environ['DB_NAME']


@pytest.fixture(scope="module")
def admin_client():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    tok = r.json()["token"]
    s.headers.update({"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    return s, r.json()["id"]


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _mongo():
    return AsyncIOMotorClient(MONGO_URL)[DB_NAME]


class TestBillingHistoryFilter:
    """Verify /api/me/transactions excludes pending and includes paid/fulfilled rows."""

    def test_pending_checkout_is_hidden(self, admin_client, event_loop):
        s, user_id = admin_client
        # Create a pending tx via checkout
        r = s.post(f"{BASE_URL}/api/me/checkout",
                   json={"plan": "monthly", "origin_url": "http://test"})
        assert r.status_code == 200, f"checkout failed: {r.text}"
        session_id = r.json()["session_id"]
        assert session_id

        # Verify the pending row IS in payment_transactions (still needed for polling)
        async def find_pending():
            db = _mongo()
            tx = await db.payment_transactions.find_one({"session_id": session_id})
            return tx
        tx = event_loop.run_until_complete(find_pending())
        assert tx is not None, "Pending tx must persist for status polling"
        assert tx["payment_status"] == "pending"
        assert tx["fulfilled"] is False

        # /me/transactions must NOT include this pending tx
        r2 = s.get(f"{BASE_URL}/api/me/transactions")
        assert r2.status_code == 200
        txs = r2.json()
        for t in txs:
            assert t["session_id"] != session_id, \
                f"Pending tx {session_id} should be hidden from billing history"
            # Defensive: anything returned must be paid or fulfilled
            assert (t.get("payment_status") == "paid") or (t.get("fulfilled") is True), \
                f"Non-paid/non-fulfilled tx leaked: {t}"

        # Store session_id for next test
        TestBillingHistoryFilter._test_session_id = session_id
        TestBillingHistoryFilter._test_user_id = user_id

    def test_marking_paid_makes_it_appear(self, admin_client, event_loop):
        s, user_id = admin_client
        session_id = TestBillingHistoryFilter._test_session_id

        # Simulate webhook success by direct DB mark
        async def mark_paid():
            db = _mongo()
            await db.payment_transactions.update_one(
                {"session_id": session_id},
                {"$set": {"payment_status": "paid", "fulfilled": True}},
            )
        event_loop.run_until_complete(mark_paid())

        r = s.get(f"{BASE_URL}/api/me/transactions")
        assert r.status_code == 200
        txs = r.json()
        found = next((t for t in txs if t["session_id"] == session_id), None)
        assert found is not None, \
            f"Paid+fulfilled tx must appear in /me/transactions, got {len(txs)} txs"
        assert found["payment_status"] == "paid"
        assert found["fulfilled"] is True
        # _id excluded
        assert "_id" not in found
        # metadata excluded by projection
        assert "metadata" not in found

    def test_cleanup_test_tx(self, admin_client, event_loop):
        """Cleanup: remove the test transaction we created."""
        session_id = getattr(TestBillingHistoryFilter, "_test_session_id", None)
        if not session_id:
            pytest.skip("nothing to clean")

        async def cleanup():
            db = _mongo()
            await db.payment_transactions.delete_one({"session_id": session_id})
        event_loop.run_until_complete(cleanup())


class TestBillingHistoryRegression:
    """Make sure /me/transactions still works for unauth + basic shape."""

    def test_unauthenticated_rejected(self):
        r = requests.get(f"{BASE_URL}/api/me/transactions")
        assert r.status_code == 401

    def test_response_is_list(self, admin_client):
        s, _ = admin_client
        r = s.get(f"{BASE_URL}/api/me/transactions")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
