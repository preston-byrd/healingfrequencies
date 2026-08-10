"""Backend tests for the re-engagement email nudge system (Solarisound).

Covers:
  * HTTP endpoints under /api/e/*, /api/me/nudge-prefs, /api/admin/email-engagement
  * Internal helpers _in_send_window_cst, _nudge_tier_for_hours, _pick_variant
  * Full scheduler flow via _reengagement_tick() with mocked send + window
  * Idempotency, login-reset watermark, unsubscribed & cadence gating
  * 30d tier fixed subject/body, deep-link CTA for 14d/30d
  * Anti-open-redirect + HTML-escape safety

Uses pymongo (sync) for DB seeding/verification to avoid motor event-loop
lifecycle issues, and a persistent asyncio loop for running _reengagement_tick.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import requests
from pymongo import MongoClient
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _creds import ADMIN_EMAIL, ADMIN_PASSWORD  # noqa: E402

# ---------------------------------------------------------------------------
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
if not BASE_URL:
    fe = dotenv_values(Path(__file__).resolve().parents[2] / "frontend" / ".env")
    BASE_URL = (fe.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL must be set"

_be_env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
MONGO_URL = _be_env.get("MONGO_URL") or os.environ.get("MONGO_URL")
DB_NAME = _be_env.get("DB_NAME") or os.environ.get("DB_NAME")


# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def pymongo_db():
    c = MongoClient(MONGO_URL)
    yield c[DB_NAME]
    c.close()


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def server_mod(event_loop):
    """Import the server module and re-bind its motor client to our
    session-wide event loop so async calls stay on one loop."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    mod = importlib.import_module("server")
    # Rebind motor client on the persistent loop.
    from motor.motor_asyncio import AsyncIOMotorClient
    asyncio.set_event_loop(event_loop)
    mod.client = AsyncIOMotorClient(MONGO_URL, io_loop=event_loop)
    mod.db = mod.client[DB_NAME]
    return mod


def _run(loop, coro):
    return loop.run_until_complete(coro)


@pytest.fixture(scope="session")
def api():
    s = requests.Session()
    s.headers["Content-Type"] = "application/json"
    return s


@pytest.fixture(scope="session")
def admin_session(api):
    r = api.post(f"{BASE_URL}/api/auth/login",
                 json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code} {r.text[:200]}")
    token = r.json().get("token")
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json",
                      "Authorization": f"Bearer {token}"})
    return s


@pytest.fixture(scope="module")
def user_session(api):
    email = f"TEST_nudge_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "TestPass123!"
    r = api.post(f"{BASE_URL}/api/auth/register",
                 json={"email": email, "password": pwd, "name": "TEST Nudge User"}, timeout=15)
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json",
                      "Authorization": f"Bearer {token}"})
    s.user_id = r.json()["id"]  # type: ignore[attr-defined]
    s.user_email = email        # type: ignore[attr-defined]
    return s


# ===========================================================================
# 1. Pure helpers
# ===========================================================================

class TestSendWindow:
    def test_window_hours(self, server_mod):
        cst = server_mod._CST
        cases = [
            (2, False), (5, False), (8, False),
            (9, True),
            (10, False), (11, False), (15, False),
            (21, False), (22, False), (23, False),
        ]
        for hour, expected in cases:
            dt_utc = datetime(2025, 6, 1, hour, 0, tzinfo=cst).astimezone(timezone.utc)
            assert server_mod._in_send_window_cst(dt_utc) is expected, f"hour={hour}"

    def test_window_9_30(self, server_mod):
        dt = datetime(2025, 6, 1, 9, 30, tzinfo=server_mod._CST).astimezone(timezone.utc)
        assert server_mod._in_send_window_cst(dt) is True

    def test_window_9_59(self, server_mod):
        dt = datetime(2025, 6, 1, 9, 59, tzinfo=server_mod._CST).astimezone(timezone.utc)
        assert server_mod._in_send_window_cst(dt) is True

    def test_window_10(self, server_mod):
        dt = datetime(2025, 6, 1, 10, 0, tzinfo=server_mod._CST).astimezone(timezone.utc)
        assert server_mod._in_send_window_cst(dt) is False


class TestNudgeTier:
    def test_tiers(self, server_mod):
        f = server_mod._nudge_tier_for_hours
        assert f(0) is None
        assert f(71.9) is None
        assert f(72)["key"] == "72h"
        assert f(6 * 24 + 23)["key"] == "72h"
        assert f(7 * 24)["key"] == "7d"
        assert f(13 * 24 + 23)["key"] == "7d"
        assert f(14 * 24)["key"] == "14d"
        assert f(29 * 24 + 23)["key"] == "14d"
        assert f(30 * 24)["key"] == "30d"
        assert f(364 * 24)["key"] == "30d"
        assert f(365 * 24) is None


class TestPickVariant:
    def test_never_repeats(self, server_mod):
        pool = [{"key": "a"}, {"key": "b"}, {"key": "c"}]
        for _ in range(40):
            assert server_mod._pick_variant(pool, "a")["key"] != "a"

    def test_single_item_fallback(self, server_mod):
        pool = [{"key": "only"}]
        assert server_mod._pick_variant(pool, "only")["key"] == "only"

    def test_no_last(self, server_mod):
        pool = [{"key": "a"}, {"key": "b"}]
        assert server_mod._pick_variant(pool, None)["key"] in ("a", "b")


# ===========================================================================
# 2. Auth stamps (HTTP + pymongo verify)
# ===========================================================================

class TestAuthStamps:
    def test_register_stamps_last_login(self, api, pymongo_db):
        email = f"TEST_stamp_{uuid.uuid4().hex[:8]}@example.com"
        r = api.post(f"{BASE_URL}/api/auth/register",
                     json={"email": email, "password": "TestPass123!", "name": "TEST"}, timeout=15)
        assert r.status_code == 200, r.text
        uid = r.json()["id"]
        try:
            u = pymongo_db.users.find_one({"id": uid})
            assert u and u.get("last_login_at")
            lla = datetime.fromisoformat(u["last_login_at"].replace("Z", "+00:00"))
            cra = datetime.fromisoformat(u["created_at"].replace("Z", "+00:00"))
            assert abs((lla - cra).total_seconds()) < 5
        finally:
            pymongo_db.users.delete_one({"id": uid})

    def test_login_updates_last_login_and_reset(self, api, pymongo_db):
        email = f"TEST_login_{uuid.uuid4().hex[:8]}@example.com"
        pwd = "TestPass123!"
        r = api.post(f"{BASE_URL}/api/auth/register",
                     json={"email": email, "password": pwd, "name": "TEST"}, timeout=15)
        uid = r.json()["id"]
        try:
            past = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
            pymongo_db.users.update_one({"id": uid},
                                        {"$set": {"last_login_at": past, "nudge_sequence_reset_at": past}})
            r2 = api.post(f"{BASE_URL}/api/auth/login",
                          json={"email": email, "password": pwd}, timeout=15)
            assert r2.status_code == 200
            u = pymongo_db.users.find_one({"id": uid})
            lla = datetime.fromisoformat(u["last_login_at"].replace("Z", "+00:00"))
            nra = datetime.fromisoformat(u["nudge_sequence_reset_at"].replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            assert (now - lla).total_seconds() < 30
            assert (now - nra).total_seconds() < 30
        finally:
            pymongo_db.users.delete_one({"id": uid})


# ===========================================================================
# 3. Tracking endpoints
# ===========================================================================

def _seed_nudge_sync(pymongo_db, tier="7d", user_id=None):
    nid = str(uuid.uuid4())
    doc = {
        "id": nid, "user_id": user_id or f"TEST_u_{nid[:8]}",
        "user_email": "TEST_track@example.com", "tier": tier,
        "variant_key": "hb_balanced", "has_hb": True, "top_freq": 528.0,
        "subject": "Test", "sent_at": datetime.now(timezone.utc).isoformat(),
        "delivered": True, "opened_at": None, "clicked_at": None, "resend_id": None,
    }
    pymongo_db.email_nudges.insert_one(doc)
    return nid


class TestNudgeTracking:
    def test_open_pixel(self, pymongo_db):
        nid = _seed_nudge_sync(pymongo_db)
        try:
            r = requests.get(f"{BASE_URL}/api/e/track/open/{nid}", timeout=10)
            assert r.status_code == 200
            assert r.headers.get("content-type", "").startswith("image/gif")
            assert len(r.content) == 43
            assert r.content[:6] == b"GIF89a"
            doc = pymongo_db.email_nudges.find_one({"id": nid})
            assert doc["opened_at"] is not None
            first_ts = doc["opened_at"]
            time.sleep(0.6)
            requests.get(f"{BASE_URL}/api/e/track/open/{nid}", timeout=10)
            doc2 = pymongo_db.email_nudges.find_one({"id": nid})
            assert doc2["opened_at"] == first_ts
        finally:
            pymongo_db.email_nudges.delete_one({"id": nid})

    def test_click_302_same_origin(self, pymongo_db):
        nid = _seed_nudge_sync(pymongo_db)
        try:
            target = "https://solarisound.com/play?frequency=528"
            r = requests.get(f"{BASE_URL}/api/e/track/click/{nid}",
                             params={"to": target}, allow_redirects=False, timeout=10)
            assert r.status_code == 302
            assert r.headers.get("location") == target
            doc = pymongo_db.email_nudges.find_one({"id": nid})
            assert doc["clicked_at"] is not None
            assert doc["opened_at"] is not None
        finally:
            pymongo_db.email_nudges.delete_one({"id": nid})

    def test_click_anti_open_redirect(self, pymongo_db):
        nid = _seed_nudge_sync(pymongo_db)
        try:
            r = requests.get(f"{BASE_URL}/api/e/track/click/{nid}",
                             params={"to": "https://evil.com/steal"},
                             allow_redirects=False, timeout=10)
            assert r.status_code == 302
            loc = r.headers.get("location", "")
            assert "evil.com" not in loc
            assert loc.startswith("https://")
        finally:
            pymongo_db.email_nudges.delete_one({"id": nid})


# ===========================================================================
# 4. Unsub / prefs (email HTML endpoints + /api/me/nudge-prefs)
# ===========================================================================

def _seed_user_sync(pymongo_db, token=None, **overrides):
    token = token or str(uuid.uuid4())
    uid = str(uuid.uuid4())
    doc = {
        "id": uid, "email": f"TEST_u_{uid[:8]}@example.com",
        "name": "TEST", "password_hash": "x",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": datetime.now(timezone.utc).isoformat(),
        "nudge_unsubscribe_token": token,
    }
    doc.update(overrides)
    pymongo_db.users.insert_one(doc)
    return uid, token


class TestUnsubAndPrefsEmail:
    def test_unsub_valid(self, pymongo_db):
        uid, tok = _seed_user_sync(pymongo_db)
        try:
            r = requests.get(f"{BASE_URL}/api/e/unsub/{tok}", timeout=10)
            assert r.status_code == 200
            assert "text/html" in r.headers.get("content-type", "")
            u = pymongo_db.users.find_one({"id": uid})
            assert u["nudge_unsubscribed"] is True
        finally:
            pymongo_db.users.delete_one({"id": uid})

    def test_unsub_short_400(self):
        r = requests.get(f"{BASE_URL}/api/e/unsub/abc", timeout=10)
        assert r.status_code == 400

    def test_unsub_unknown_404(self):
        r = requests.get(f"{BASE_URL}/api/e/unsub/{uuid.uuid4()}", timeout=10)
        assert r.status_code == 404

    def test_prefs_weekly(self, pymongo_db):
        uid, tok = _seed_user_sync(pymongo_db)
        try:
            r = requests.get(f"{BASE_URL}/api/e/prefs/{tok}?cadence=weekly", timeout=10)
            assert r.status_code == 200
            u = pymongo_db.users.find_one({"id": uid})
            assert u["nudge_cadence"] == "weekly"
            assert not u.get("nudge_unsubscribed")
        finally:
            pymongo_db.users.delete_one({"id": uid})

    def test_prefs_off_also_unsubs(self, pymongo_db):
        uid, tok = _seed_user_sync(pymongo_db)
        try:
            r = requests.get(f"{BASE_URL}/api/e/prefs/{tok}?cadence=off", timeout=10)
            assert r.status_code == 200
            u = pymongo_db.users.find_one({"id": uid})
            assert u["nudge_cadence"] == "off"
            assert u["nudge_unsubscribed"] is True
        finally:
            pymongo_db.users.delete_one({"id": uid})


class TestMePrefs:
    def test_flow(self, user_session):
        r = user_session.get(f"{BASE_URL}/api/me/nudge-prefs", timeout=10)
        assert r.status_code == 200
        j = r.json()
        assert j["cadence"] == "default"
        assert j["unsubscribed"] is False
        r2 = user_session.put(f"{BASE_URL}/api/me/nudge-prefs",
                              json={"cadence": "weekly"}, timeout=10)
        assert r2.status_code == 200
        assert user_session.get(f"{BASE_URL}/api/me/nudge-prefs", timeout=10).json()["cadence"] == "weekly"

    def test_off_flips_unsub(self, user_session):
        r = user_session.put(f"{BASE_URL}/api/me/nudge-prefs",
                             json={"cadence": "off"}, timeout=10)
        assert r.status_code == 200
        j = user_session.get(f"{BASE_URL}/api/me/nudge-prefs", timeout=10).json()
        assert j["cadence"] == "off"
        assert j["unsubscribed"] is True

    def test_invalid_400(self, user_session):
        r = user_session.put(f"{BASE_URL}/api/me/nudge-prefs",
                             json={"cadence": "hourly"}, timeout=10)
        assert r.status_code == 400


# ===========================================================================
# 5. Admin engagement panel
# ===========================================================================

class TestAdminEngagement:
    def test_non_admin_forbidden(self, user_session):
        r = user_session.get(f"{BASE_URL}/api/admin/email-engagement", timeout=10)
        assert r.status_code == 403

    def test_stats_shape(self, pymongo_db, admin_session):
        seeds = []
        for tier, opened, clicked in [
            ("72h", True, False), ("7d", True, True),
            ("14d", False, False), ("30d", False, False),
        ]:
            nid = str(uuid.uuid4())
            pymongo_db.email_nudges.insert_one({
                "id": nid, "user_id": f"TEST_eng_{nid[:6]}",
                "user_email": "TEST_eng@example.com", "tier": tier,
                "variant_key": "x", "has_hb": False, "top_freq": None, "subject": "s",
                "sent_at": datetime.now(timezone.utc).isoformat(), "delivered": True,
                "opened_at": datetime.now(timezone.utc).isoformat() if opened else None,
                "clicked_at": datetime.now(timezone.utc).isoformat() if clicked else None,
                "resend_id": None,
            })
            seeds.append(nid)
        try:
            r = admin_session.get(f"{BASE_URL}/api/admin/email-engagement", timeout=10)
            assert r.status_code == 200, r.text
            j = r.json()
            for k in ("total", "delivered", "opened", "clicked",
                      "open_rate", "click_rate", "per_tier",
                      "unsubscribed_users", "recent"):
                assert k in j
            for tk in ("72h", "7d", "14d", "30d"):
                assert tk in j["per_tier"]
                assert set(j["per_tier"][tk].keys()) >= {"sent", "opened", "clicked"}
            assert 0.0 <= j["open_rate"] <= 1.0
            assert 0.0 <= j["click_rate"] <= 1.0
            assert isinstance(j["recent"], list)
            # Verify our seeded nudges count in totals.
            assert j["total"] >= 4
        finally:
            for nid in seeds:
                pymongo_db.email_nudges.delete_one({"id": nid})

    def test_tick_endpoint(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/admin/email-engagement/tick", timeout=30)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["ok"] is True
        assert "stats" in j and isinstance(j["stats"], dict)


# ===========================================================================
# 6. Full scheduler flow (in-process, mocked send)
# ===========================================================================

def _seed_stale_user(pymongo_db, hours_ago, name="TEST Sched", freq=None):
    uid = str(uuid.uuid4())
    past = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    pymongo_db.users.insert_one({
        "id": uid, "email": f"TEST_sched_{uid[:8]}@example.com",
        "name": name, "password_hash": "x",
        "created_at": past, "last_login_at": past,
        "nudge_sequence_reset_at": past,
    })
    if freq is not None:
        pymongo_db.wellness_journey.insert_one({
            "user_id": uid, "frequency": freq,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    return uid


def _cleanup_user(pymongo_db, uid):
    pymongo_db.users.delete_one({"id": uid})
    pymongo_db.email_nudges.delete_many({"user_id": uid})
    pymongo_db.wellness_journey.delete_many({"user_id": uid})


def _mocks(server_mod, capture=None):
    def fake_send(to, subject, html):
        if capture is not None:
            capture.append({"to": to, "subject": subject, "html": html})
        return "fake_" + uuid.uuid4().hex[:8]
    return [
        patch.object(server_mod, "_in_send_window_cst", return_value=True),
        patch.object(server_mod, "_resend", MagicMock()),
        patch.object(server_mod, "_RESEND_API_KEY", "test_key"),
        patch.object(server_mod, "_send_email_sync", side_effect=fake_send),
    ]


class TestSchedulerFlow:
    def test_tiers_and_freshness(self, event_loop, server_mod, pymongo_db):
        u100 = _seed_stale_user(pymongo_db, 100)
        u200 = _seed_stale_user(pymongo_db, 200)
        u50 = _seed_stale_user(pymongo_db, 50)
        try:
            mocks = _mocks(server_mod)
            for m in mocks: m.start()
            try:
                _run(event_loop, server_mod._reengagement_tick())
            finally:
                for m in mocks: m.stop()
            assert pymongo_db.email_nudges.count_documents({"user_id": u50}) == 0
            docs100 = list(pymongo_db.email_nudges.find({"user_id": u100}))
            assert len(docs100) == 1 and docs100[0]["tier"] == "72h"
            docs200 = list(pymongo_db.email_nudges.find({"user_id": u200}))
            assert len(docs200) == 1 and docs200[0]["tier"] == "7d"
        finally:
            for uid in (u100, u200, u50):
                _cleanup_user(pymongo_db, uid)

    def test_idempotency(self, event_loop, server_mod, pymongo_db):
        u = _seed_stale_user(pymongo_db, 100)
        try:
            mocks = _mocks(server_mod)
            for m in mocks: m.start()
            try:
                _run(event_loop, server_mod._reengagement_tick())
                _run(event_loop, server_mod._reengagement_tick())
            finally:
                for m in mocks: m.stop()
            assert pymongo_db.email_nudges.count_documents({"user_id": u}) == 1
        finally:
            _cleanup_user(pymongo_db, u)

    def test_login_reset_watermark(self, event_loop, server_mod, pymongo_db):
        u = _seed_stale_user(pymongo_db, 100)
        try:
            mocks = _mocks(server_mod)
            for m in mocks: m.start()
            try:
                _run(event_loop, server_mod._reengagement_tick())
                assert pymongo_db.email_nudges.count_documents({"user_id": u}) == 1
                # Simulate login: bump both watermarks to now.
                now_iso = datetime.now(timezone.utc).isoformat()
                pymongo_db.users.update_one({"id": u}, {
                    "$set": {"last_login_at": now_iso, "nudge_sequence_reset_at": now_iso}
                })
                # Rewind last_login only.
                past = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
                pymongo_db.users.update_one({"id": u}, {"$set": {"last_login_at": past}})
                _run(event_loop, server_mod._reengagement_tick())
            finally:
                for m in mocks: m.stop()
            assert pymongo_db.email_nudges.count_documents({"user_id": u}) == 2
        finally:
            _cleanup_user(pymongo_db, u)

    def test_unsubscribed_skipped(self, event_loop, server_mod, pymongo_db):
        u = _seed_stale_user(pymongo_db, 100)
        pymongo_db.users.update_one({"id": u}, {"$set": {"nudge_unsubscribed": True}})
        try:
            mocks = _mocks(server_mod)
            for m in mocks: m.start()
            try:
                _run(event_loop, server_mod._reengagement_tick())
            finally:
                for m in mocks: m.stop()
            assert pymongo_db.email_nudges.count_documents({"user_id": u}) == 0
        finally:
            _cleanup_user(pymongo_db, u)

    def test_cadence_weekly_gates(self, event_loop, server_mod, pymongo_db):
        u = _seed_stale_user(pymongo_db, 100)
        pymongo_db.users.update_one({"id": u}, {"$set": {"nudge_cadence": "weekly"}})
        pymongo_db.email_nudges.insert_one({
            "id": str(uuid.uuid4()), "user_id": u,
            "user_email": "x", "tier": "72h", "variant_key": "x",
            "has_hb": False, "top_freq": None, "subject": "s",
            "sent_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
            "delivered": True, "opened_at": None, "clicked_at": None, "resend_id": None,
        })
        try:
            mocks = _mocks(server_mod)
            for m in mocks: m.start()
            try:
                _run(event_loop, server_mod._reengagement_tick())
            finally:
                for m in mocks: m.stop()
            assert pymongo_db.email_nudges.count_documents({"user_id": u}) == 1
        finally:
            _cleanup_user(pymongo_db, u)

    def test_default_cadence_gates_72h(self, event_loop, server_mod, pymongo_db):
        u = _seed_stale_user(pymongo_db, 100)
        pymongo_db.email_nudges.insert_one({
            "id": str(uuid.uuid4()), "user_id": u,
            "user_email": "x", "tier": "72h", "variant_key": "x",
            "has_hb": False, "top_freq": None, "subject": "s",
            "sent_at": (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),
            "delivered": True, "opened_at": None, "clicked_at": None, "resend_id": None,
        })
        try:
            mocks = _mocks(server_mod)
            for m in mocks: m.start()
            try:
                _run(event_loop, server_mod._reengagement_tick())
            finally:
                for m in mocks: m.stop()
            assert pymongo_db.email_nudges.count_documents({"user_id": u}) == 1
        finally:
            _cleanup_user(pymongo_db, u)


# ===========================================================================
# 7. Copy / CTA / safety
# ===========================================================================

class TestCopyAndSafety:
    def _tick(self, event_loop, server_mod, capture):
        mocks = _mocks(server_mod, capture=capture)
        for m in mocks: m.start()
        try:
            _run(event_loop, server_mod._reengagement_tick())
        finally:
            for m in mocks: m.stop()

    def test_30d_subject_and_body(self, event_loop, server_mod, pymongo_db):
        u = _seed_stale_user(pymongo_db, 31 * 24, name="Alice")
        cap = []
        try:
            self._tick(event_loop, server_mod, cap)
            # Find the send for our user (by matching subject/name).
            hit = next((x for x in cap if "Alice" in (x["subject"] or "")), None)
            assert hit is not None, f"no send captured for Alice; captures={len(cap)}"
            assert hit["subject"] == "We miss you, Alice"
            assert "about a month" in hit["html"]
            assert "saved your place" in hit["html"]
        finally:
            _cleanup_user(pymongo_db, u)

    def test_14d_cta_has_frequency(self, event_loop, server_mod, pymongo_db):
        u = _seed_stale_user(pymongo_db, 15 * 24, name="Bob", freq=528.0)
        cap = []
        try:
            self._tick(event_loop, server_mod, cap)
            hit = next((x for x in cap if "Bob" in (x["subject"] or "")), None)
            assert hit is not None
            html = hit["html"]
            # URL-encoded (?frequency= → %3Ffrequency%3D) inside track/click ?to=
            assert ("frequency%3D528" in html) or ("frequency=528" in html)
        finally:
            _cleanup_user(pymongo_db, u)

    def test_72h_cta_no_frequency(self, event_loop, server_mod, pymongo_db):
        u = _seed_stale_user(pymongo_db, 80, name="Carol", freq=432.0)
        cap = []
        try:
            self._tick(event_loop, server_mod, cap)
            hit = next((x for x in cap if "Carol" in (x["subject"] or "") or "waiting" in (x["subject"] or "").lower()), None)
            # 72h subject is generic ("Your frequencies are waiting") — find by user email.
            if hit is None:
                hit = next((x for x in cap if f"TEST_sched_{u[:8]}" in (x["to"] or "")), None)
            assert hit is not None
            html = hit["html"]
            assert "frequency=" not in html and "frequency%3D" not in html
        finally:
            _cleanup_user(pymongo_db, u)

    def test_html_injection_escaped(self, event_loop, server_mod, pymongo_db):
        u = _seed_stale_user(pymongo_db, 100, name="<script>alert(1)</script>")
        cap = []
        try:
            self._tick(event_loop, server_mod, cap)
            hit = next((x for x in cap if f"TEST_sched_{u[:8]}" in (x["to"] or "")), None)
            assert hit is not None
            html = hit["html"]
            assert "<script>alert(1)</script>" not in html
            assert "&lt;script&gt;" in html
        finally:
            _cleanup_user(pymongo_db, u)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
