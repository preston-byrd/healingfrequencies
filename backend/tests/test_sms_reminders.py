"""Regression tests for HF-032 SMS session reminders in the scheduler.

Runs `_sms_reminder_tick()` in-process against Mongo, seeding fixtures
that flex every gate:
- Qualifies: Pro + opted-in + reminders category + no recent session +
  no recent reminder + not STOPped
- Skips: not Pro, not opted-in, category off, STOPped, recent session,
  recent reminder (7-day cooldown)

We hit the ACTUAL `_sms_reminder_tick` (no mocking of the query layer)
so we exercise the exact Mongo filter that ships to production.
"""

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pymongo import MongoClient

API_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/") + "/api"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server  # noqa: E402

# Synchronous Mongo client purely for test setup/teardown so we don't
# fight Motor's async event-loop binding across tests. Uses the same
# MONGO_URL + DB_NAME the server does.
_sync_db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def _rand_phone() -> str:
    n = uuid.uuid4().int % 10000000
    return f"+1555{n:07d}"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _seed_user(
    *,
    opted_in: bool = True,
    reminders_on: bool = True,
    stopped: bool = False,
    pro: bool = True,
    last_session_hours_ago: int | None = 96,
    last_reminder_days_ago: int | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    uid = str(uuid.uuid4())
    doc = {
        "id": uid,
        "email": f"smsreminder_{uuid.uuid4().hex[:8]}@example.com",
        "name": "Reminder User",
        "phone_number": _rand_phone(),
        "phone_verified": True,
        "phone_verified_at": _iso(now),
        "created_at": _iso(now - timedelta(days=30)),
        "last_login_at": _iso(now - timedelta(days=30)),
        "sms_prefs": {
            "marketing_opted_in": opted_in,
            "marketing_opted_in_at": _iso(now - timedelta(days=1)) if opted_in else None,
            "stopped_at": _iso(now - timedelta(hours=1)) if stopped else None,
            "categories": {
                "transactional": True,
                "reminders": reminders_on,
                "recommendations": False,
                "announcements": False,
            },
        },
    }
    if pro:
        doc["pro_until"] = _iso(now + timedelta(days=30))
    _sync_db.users.insert_one(doc)
    if last_session_hours_ago is not None:
        _sync_db.sessions.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": uid,
            "frequency": 528.0,
            "duration_minutes": 10,
            "created_at": _iso(now - timedelta(hours=last_session_hours_ago)),
        })
    if last_reminder_days_ago is not None:
        _sync_db.sms_messages.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": uid,
            "phone_last4": doc["phone_number"][-4:],
            "category": "reminders",
            "status": "sent-test-mode",
            "sent_at": _iso(now - timedelta(days=last_reminder_days_ago)),
        })
    return uid


@pytest.fixture(autouse=True)
def cleanup():
    yield
    _sync_db.users.delete_many({"email": {"$regex": "^smsreminder_"}})
    _sync_db.sms_messages.delete_many({"phone_last4": {"$exists": True}, "category": "reminders", "status": "sent-test-mode"})


def _tick() -> dict:
    """Run one pass of the SMS reminder tick against the LIVE server via
    the admin endpoint with force=true (bypasses the CST send-window
    gate). Avoids all motor-loop-mismatch headaches — the server owns its
    own loop and we just call the HTTP surface."""
    with httpx.Client() as c:
        login = c.post(f"{API_URL}/auth/login", json={
            "email": "admin@example.com",
            "password": os.environ.get("ADMIN_PASSWORD", "JuzlUWlMMOjHM0u#m5qv0ds!oYp8"),
        })
        token = login.json()["token"]
        r = c.post(
            f"{API_URL}/admin/email-engagement/tick?force=true",
            headers={"Authorization": f"Bearer {token}"},
        )
    r.raise_for_status()
    body = r.json()
    stats = body.get("stats", body)
    return {
        "scanned": stats.get("sms_scanned", 0),
        "sent": stats.get("sms_sent", 0),
        "deferred": stats.get("sms_deferred", 0),
        "skipped_not_pro": stats.get("sms_skipped_not_pro", 0),
        "skipped_no_consent": stats.get("sms_skipped_no_consent", 0),
        "skipped_recent_session": stats.get("sms_skipped_recent_session", 0),
        "skipped_recent_reminder": stats.get("sms_skipped_recent_reminder", 0),
        "skipped_stopped": stats.get("sms_skipped_stopped", 0),
        "errors": stats.get("sms_errors", 0),
    }


# ---------- Positive path --------------------------------------------------

def test_qualifying_user_gets_reminder():
    uid = _seed_user()
    stats = _tick()
    assert stats["sent"] >= 1
    # Cooldown row exists for future ticks.
    count = _sync_db.sms_messages.count_documents({"user_id": uid, "category": "reminders"})
    assert count >= 1


# ---------- Skip: not Pro (query-level filter) -----------------------------

def test_free_user_not_scanned():
    uid = _seed_user(pro=False)
    _tick()
    count = _sync_db.sms_messages.count_documents({"user_id": uid, "category": "reminders"})
    assert count == 0


# ---------- Skip: no marketing consent -------------------------------------

def test_opt_out_not_scanned():
    uid = _seed_user(opted_in=False)
    _tick()
    count = _sync_db.sms_messages.count_documents({"user_id": uid, "category": "reminders"})
    assert count == 0


# ---------- Skip: reminders category off -----------------------------------

def test_reminders_category_off_not_scanned():
    uid = _seed_user(reminders_on=False)
    _tick()
    count = _sync_db.sms_messages.count_documents({"user_id": uid, "category": "reminders"})
    assert count == 0


# ---------- Skip: STOP is sticky -------------------------------------------

def test_stopped_user_not_scanned():
    uid = _seed_user(stopped=True)
    _tick()
    count = _sync_db.sms_messages.count_documents({"user_id": uid, "category": "reminders"})
    assert count == 0


# ---------- Skip: recent session -------------------------------------------

def test_recent_session_skipped():
    uid = _seed_user(last_session_hours_ago=6)
    stats = _tick()
    assert stats["skipped_recent_session"] >= 1
    count = _sync_db.sms_messages.count_documents({"user_id": uid, "category": "reminders"})
    assert count == 0


# ---------- Skip: recent reminder cooldown ---------------------------------

def test_recent_reminder_skipped():
    uid = _seed_user(last_reminder_days_ago=2)
    stats = _tick()
    assert stats["skipped_recent_reminder"] >= 1
    # We seeded one reminder but no new one was added.
    count = _sync_db.sms_messages.count_documents({"user_id": uid, "category": "reminders"})
    assert count == 1


def test_reminder_older_than_7d_not_a_cooldown():
    uid = _seed_user(last_reminder_days_ago=10)
    stats = _tick()
    assert stats["sent"] >= 1
    # Original + newly-sent = 2 rows.
    count = _sync_db.sms_messages.count_documents({"user_id": uid, "category": "reminders"})
    assert count == 2


# ---------- Second tick same window ----------------------------------------

def test_second_tick_same_window_no_duplicate():
    uid = _seed_user()
    _tick()
    stats2 = _tick()
    assert stats2["skipped_recent_reminder"] >= 1
    assert stats2["sent"] == 0


# ---------- End-to-end via admin tick endpoint -----------------------------

def test_admin_tick_endpoint_returns_sms_counters():
    _seed_user()
    with httpx.Client() as c:
        login = c.post(f"{API_URL}/auth/login", json={
            "email": "admin@example.com",
            "password": os.environ.get("ADMIN_PASSWORD", "JuzlUWlMMOjHM0u#m5qv0ds!oYp8"),
        })
        token = login.json()["token"]
        r = c.post(
            f"{API_URL}/admin/email-engagement/tick?force=true",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    stats = body.get("stats", body)
    assert "sms_scanned" in stats
    assert "sms_sent" in stats
