"""HF-047 Pro-access expiration reminder tests.

Covers:
- 72-96h window detection for trial / paid / promo users
- Skips cancel_at_period_end=True subscribers
- Skips users who renewed in the last 24h
- Skips nudge_unsubscribed users
- Skips admin accounts
- Idempotent — one email per (user_id, expires_at) pair
- pro_last_renewed_at is stamped when Stripe extends pro_until forward
- Admin force-tick endpoint returns counters
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv('/app/backend/.env')

BASE_URL = os.environ.get(
    'REACT_APP_BACKEND_URL',
    'https://frequency-healer-31.preview.emergentagent.com',
).rstrip('/')
API = f"{BASE_URL}/api"
MONGO_URL = os.environ['MONGO_URL']
DB_NAME = os.environ['DB_NAME']

ADMIN_EMAIL = os.environ.get("ADMIN_TEST_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_TEST_PASSWORD") or __import__(
    "tests._creds", fromlist=["ADMIN_PASSWORD"]
).ADMIN_PASSWORD


def _mongo():
    return AsyncIOMotorClient(MONGO_URL)[DB_NAME]


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    tok = r.json()["token"]
    s.headers.update({"Authorization": f"Bearer {tok}"})
    return s


async def _seed_user(**overrides) -> dict:
    """Seed a raw user document directly into Mongo (bypasses phone
    verification). Returns the persisted doc."""
    db = _mongo()
    uid = str(uuid.uuid4())
    doc = {
        "id": uid,
        "email": f"exp_{uid[:8]}@example.com",
        "name": "Expiring User",
        "password_hash": "$2b$04$notarealhashjustforseedingxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "role": "user",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    doc.update(overrides)
    await db.users.insert_one(doc)
    return doc


async def _cleanup_user(uid: str):
    db = _mongo()
    await db.users.delete_one({"id": uid})
    await db.expiration_notices.delete_many({"user_id": uid})


def _iso_in(**kwargs) -> str:
    return (datetime.now(timezone.utc) + timedelta(**kwargs)).isoformat()


# --- Direct tick tests (exercise the helper via imported module) ---
import sys
sys.path.insert(0, '/app/backend')
import server as srv  # noqa: E402


def _run(coro):
    """Helper that runs an async coroutine in a fresh loop."""
    return asyncio.get_event_loop().run_until_complete(coro)


class TestExpirationNoticeTick:

    def test_trial_ending_in_72h_gets_notice(self, event_loop):
        async def go():
            u = await _seed_user(
                pro_until=_iso_in(hours=80),
                stripe_subscription_status="trialing",
                stripe_trial_end=_iso_in(hours=80),
                stripe_cancel_at_period_end=False,
            )
            stats = await srv._expiration_notice_tick(datetime.now(timezone.utc))
            # Row is persisted regardless of Resend availability.
            db = _mongo()
            row = await db.expiration_notices.find_one({"user_id": u["id"]})
            assert row is not None, f"stats={stats}"
            assert row["tier"] == "trial"
            await _cleanup_user(u["id"])
        event_loop.run_until_complete(go())

    def test_paid_pro_ending_in_72h_gets_notice(self, event_loop):
        async def go():
            u = await _seed_user(
                pro_until=_iso_in(hours=90),
                stripe_subscription_status="active",
                stripe_cancel_at_period_end=False,
                plan="pro",
            )
            await srv._expiration_notice_tick(datetime.now(timezone.utc))
            db = _mongo()
            row = await db.expiration_notices.find_one({"user_id": u["id"]})
            assert row is not None
            assert row["tier"] == "paid"
            await _cleanup_user(u["id"])
        event_loop.run_until_complete(go())

    def test_promo_comp_ending_in_72h_gets_notice(self, event_loop):
        async def go():
            u = await _seed_user(
                pro_until=_iso_in(hours=78),
                pro_source="promo:HOLIDAY2026",
                plan="pro",
            )
            await srv._expiration_notice_tick(datetime.now(timezone.utc))
            db = _mongo()
            row = await db.expiration_notices.find_one({"user_id": u["id"]})
            assert row is not None
            assert row["tier"] == "promo"
            await _cleanup_user(u["id"])
        event_loop.run_until_complete(go())

    def test_pro_ending_in_10_days_is_ignored(self, event_loop):
        """Outside the 72-96h window, no notice."""
        async def go():
            u = await _seed_user(
                pro_until=_iso_in(days=10),
                stripe_subscription_status="active",
            )
            await srv._expiration_notice_tick(datetime.now(timezone.utc))
            db = _mongo()
            row = await db.expiration_notices.find_one({"user_id": u["id"]})
            assert row is None
            await _cleanup_user(u["id"])
        event_loop.run_until_complete(go())

    def test_pro_ending_tomorrow_is_ignored(self, event_loop):
        """Anything < 72h out is also outside the window (too late to nudge)."""
        async def go():
            u = await _seed_user(
                pro_until=_iso_in(hours=20),
                stripe_subscription_status="active",
            )
            await srv._expiration_notice_tick(datetime.now(timezone.utc))
            db = _mongo()
            row = await db.expiration_notices.find_one({"user_id": u["id"]})
            assert row is None
            await _cleanup_user(u["id"])
        event_loop.run_until_complete(go())

    def test_cancel_at_period_end_true_is_skipped(self, event_loop):
        """Users who already turned off auto-renewal aren't re-nagged."""
        async def go():
            u = await _seed_user(
                pro_until=_iso_in(hours=80),
                stripe_subscription_status="active",
                stripe_cancel_at_period_end=True,
            )
            stats = await srv._expiration_notice_tick(datetime.now(timezone.utc))
            assert stats["skipped_cancelled_at_period_end"] >= 1
            db = _mongo()
            row = await db.expiration_notices.find_one({"user_id": u["id"]})
            assert row is None
            await _cleanup_user(u["id"])
        event_loop.run_until_complete(go())

    def test_recently_renewed_is_skipped(self, event_loop):
        """A user whose Stripe sub was extended within the last 24h is
        marked as 'just renewed' and skipped even if pro_until falls in
        the 72h window (edge case: mid-cycle renewal with weird timing)."""
        async def go():
            u = await _seed_user(
                pro_until=_iso_in(hours=80),
                stripe_subscription_status="active",
                stripe_cancel_at_period_end=False,
                pro_last_renewed_at=_iso_in(hours=-6),  # 6h ago
            )
            stats = await srv._expiration_notice_tick(datetime.now(timezone.utc))
            assert stats["skipped_just_renewed"] >= 1
            db = _mongo()
            assert await db.expiration_notices.find_one({"user_id": u["id"]}) is None
            await _cleanup_user(u["id"])
        event_loop.run_until_complete(go())

    def test_unsubscribed_user_is_skipped(self, event_loop):
        async def go():
            u = await _seed_user(
                pro_until=_iso_in(hours=80),
                stripe_subscription_status="active",
                nudge_unsubscribed=True,
            )
            await srv._expiration_notice_tick(datetime.now(timezone.utc))
            db = _mongo()
            assert await db.expiration_notices.find_one({"user_id": u["id"]}) is None
            await _cleanup_user(u["id"])
        event_loop.run_until_complete(go())

    def test_admin_account_is_skipped(self, event_loop):
        """Admin has lifetime access — no reminder even if pro_until falls
        in the window (which shouldn't happen in practice)."""
        async def go():
            u = await _seed_user(
                role="admin",
                pro_until=_iso_in(hours=80),
                stripe_subscription_status="active",
            )
            await srv._expiration_notice_tick(datetime.now(timezone.utc))
            db = _mongo()
            assert await db.expiration_notices.find_one({"user_id": u["id"]}) is None
            await _cleanup_user(u["id"])
        event_loop.run_until_complete(go())

    def test_second_tick_is_idempotent(self, event_loop):
        """Second tick for the SAME (user_id, expires_at) does not create
        a second row."""
        async def go():
            expires = _iso_in(hours=80)
            u = await _seed_user(
                pro_until=expires,
                stripe_subscription_status="active",
                stripe_cancel_at_period_end=False,
            )
            await srv._expiration_notice_tick(datetime.now(timezone.utc))
            stats2 = await srv._expiration_notice_tick(datetime.now(timezone.utc))
            assert stats2["skipped_already_sent"] >= 1
            db = _mongo()
            count = await db.expiration_notices.count_documents({"user_id": u["id"]})
            assert count == 1
            await _cleanup_user(u["id"])
        event_loop.run_until_complete(go())


class TestSyncSubscriptionStampsRenewedAt:
    """`_sync_subscription_to_user` should stamp `pro_last_renewed_at`
    whenever the Stripe subscription extends pro_until forward."""

    def test_stamps_pro_last_renewed_at_on_extend(self, event_loop):
        async def go():
            u = await _seed_user(
                pro_until=_iso_in(days=-3),  # currently past
            )
            future_ts = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
            fake_sub = {
                "id": "sub_test_" + uuid.uuid4().hex[:6],
                "status": "active",
                "current_period_end": future_ts,
                "trial_end": None,
                "cancel_at_period_end": False,
            }
            await srv._sync_subscription_to_user(u["id"], fake_sub)
            db = _mongo()
            fresh = await db.users.find_one({"id": u["id"]})
            assert fresh.get("pro_last_renewed_at")
            await _cleanup_user(u["id"])
        event_loop.run_until_complete(go())


class TestAdminForceTickEndpoint:
    def test_requires_admin(self):
        r = requests.post(f"{API}/admin/expiration-notices/tick")
        assert r.status_code in (401, 403), r.text

    def test_admin_can_run(self, admin_session):
        r = admin_session.post(f"{API}/admin/expiration-notices/tick")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        for key in ("scanned", "sent", "skipped_cancelled_at_period_end",
                    "skipped_just_renewed", "skipped_unsub",
                    "skipped_already_sent", "errors"):
            assert key in body["stats"], body
