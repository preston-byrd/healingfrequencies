"""Regressions for two defects surfaced by the Feb 2026 code review:

  1. HIGH — `_generate_recommendation_notification` used `_pattern_key(p)`
     (wrong arity), silently killing the whole pattern-based daily
     suggestion branch for every user. Fixed by using `p.get("key")`.
     This test exercises `POST /api/me/notifications/tick` and asserts
     that a user with a matching detected pattern actually receives a
     `meta.source == "pattern"` notification.

  2. MEDIUM — `_ensure_monthly_report` cached the current-month report
     forever after first generation, so a mid-month capture never
     updated `total_sessions` for the still-in-progress month. Fixed by
     always recomputing/upserting the current month; completed months
     remain immutable.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import pytest
import requests
from pymongo import MongoClient

BASE = os.environ.get("BACKEND_URL", "http://localhost:8001")
API = f"{BASE}/api"


def _col(name: str):
    return MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))[
        os.environ.get("DB_NAME", "test_database")
    ][name]


@pytest.fixture()
def pro_user():
    email = f"cr+{uuid.uuid4().hex[:8]}@example.com"
    pw = "CrR9!aA"
    uid = str(uuid.uuid4())
    _col("users").insert_one({
        "id": uid, "email": email, "name": "CRReview",
        "password_hash": bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode(),
        "role": "user",
        "pro_until": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "stripe_subscription_status": "active",
        "created_at": datetime.now(timezone.utc).isoformat(),
        # Disable quiet-hours so the daily-recommendation gate doesn't
        # swallow the notification purely because tests happen to run
        # in the default 22:00–07:00 UTC quiet window.
        "notification_prefs": {
            "enabled": True,
            "quiet_hours": {"enabled": False, "start_hour": 0, "end_hour": 0},
            "max_per_day": 20,
        },
    })
    yield email, pw, uid
    _col("resonance_profiles").delete_many({"user_id": uid})
    _col("wellness_journey").delete_many({"user_id": uid})
    _col("hb_monthly_reports").delete_many({"user_id": uid})
    _col("notifications").delete_many({"user_id": uid})
    _col("users").delete_one({"id": uid})


def _login(email, pw):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


# ── 1) HIGH — pattern-based recommendation notification actually fires ──

def test_recommendation_notif_uses_pattern_key(pro_user):
    """A user with ≥ 3 wellness_journey rows carrying a dominant frequency
    should receive a pattern-based daily suggestion notification.
    Regression against the `_pattern_key(p)` arity bug that silently
    killed this whole branch."""
    email, pw, uid = pro_user
    tok = _login(email, pw)

    # Seed 4 rows with frequency=528 Hz → `top_frequency:528` pattern
    # with an `arm_frequency` CTA the notification path can act on.
    now = datetime.now(timezone.utc)
    _col("wellness_journey").insert_many([{
        "id": str(uuid.uuid4()), "user_id": uid,
        "created_at": (now - timedelta(minutes=i)).isoformat(),
        "frequency": 528, "time_of_day": "morning",
        "duration_seconds": 900, "mood_pre": "calm", "mood_post": "calm",
    } for i in range(4)])
    # Kill any patterns_cache so the tick recomputes fresh.
    _col("users").update_one({"id": uid}, {"$unset": {"patterns_cache": ""}})

    r = requests.post(f"{API}/me/notifications/tick", headers=_hdr(tok))
    assert r.status_code == 200, r.text

    # The recommendation may go through the `notifications` collection.
    # Look for any notif whose `meta.source == "pattern"` OR check the
    # tick response payload for one.
    tick = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    created = tick.get("created") or tick.get("notifications") or []
    pattern_notifs = [n for n in created
                      if (n.get("meta") or {}).get("source") == "pattern"]
    if not pattern_notifs:
        # Fall back to the DB — some ticks persist and return counts.
        docs = list(_col("notifications").find(
            {"user_id": uid, "meta.source": "pattern"}, {"_id": 0},
        ))
        pattern_notifs = docs
    assert pattern_notifs, (
        "Expected a pattern-based recommendation notification but none "
        "was produced. The `_pattern_key(p)` arity bug is regressing — "
        "the whole pattern branch is being swallowed by its try/except."
    )
    n = pattern_notifs[0]
    meta = n.get("meta") or {}
    assert meta.get("source") == "pattern"
    assert meta.get("pattern_key"), meta
    assert isinstance(meta.get("pattern_key"), str), meta


def test_recommendation_notif_respects_dismissed_pattern(pro_user):
    """A dismissed pattern (within the 7-session window) must NOT surface
    as a pattern-source recommendation notification. Regression against
    the same arity bug: with `_pattern_key(p)` raising, `dismissed`
    check never executed and the guard was effectively useless."""
    email, pw, uid = pro_user
    tok = _login(email, pw)

    now = datetime.now(timezone.utc)
    _col("wellness_journey").insert_many([{
        "id": str(uuid.uuid4()), "user_id": uid,
        "created_at": (now - timedelta(minutes=i)).isoformat(),
        "frequency": 528, "time_of_day": "morning",
        "duration_seconds": 900, "mood_pre": "calm", "mood_post": "calm",
    } for i in range(4)])
    _col("users").update_one({"id": uid}, {"$unset": {"patterns_cache": ""}})

    # Dismiss the top_frequency pattern first.
    requests.post(
        f"{API}/me/patterns/top_frequency:528/dismiss", headers=_hdr(tok),
    ).raise_for_status()

    # Wipe any prior notifications so the assertion is unambiguous.
    _col("notifications").delete_many({"user_id": uid})

    requests.post(f"{API}/me/notifications/tick", headers=_hdr(tok)).raise_for_status()

    pattern_notifs = list(_col("notifications").find(
        {"user_id": uid, "meta.source": "pattern",
         "meta.pattern_key": "top_frequency:528"},
        {"_id": 0},
    ))
    assert not pattern_notifs, (
        "A dismissed pattern within its 7-session window should not "
        "surface as a recommendation, but one was produced: "
        f"{pattern_notifs}"
    )


# ── 2) MEDIUM — current-month monthly report recomputes on new capture ──

def _bands(offsets):
    base = {"sub": -30, "low": -25, "lowmid": -20, "mid": -18, "uppermid": -22, "presence": -28}
    keys = [("sub", 20, 60), ("low", 60, 250), ("lowmid", 250, 500),
            ("mid", 500, 2000), ("uppermid", 2000, 4000), ("presence", 4000, 8000)]
    return [{"key": k, "label": k.capitalize(), "lo": lo, "hi": hi,
             "db": base[k] + offsets.get(k, 0)} for k, lo, hi in keys]


def _insert_profile(uid: str, when: datetime, score: int, offsets=None):
    _col("resonance_profiles").insert_one({
        "id": str(uuid.uuid4()), "user_id": uid,
        "created_at": when.isoformat(),
        "resonance_score": score,
        "spectrum_data": {"bands": _bands(offsets or {})},
    })


def test_current_month_report_recomputes_on_new_capture(pro_user):
    """After a mid-month new capture the current-month report must
    reflect the updated `total_sessions` — no stale snapshot."""
    email, pw, uid = pro_user
    tok = _login(email, pw)

    # Anchor all seeded profiles to *this* calendar month so the
    # current-month branch of `_ensure_monthly_report` is exercised
    # (completed months are still cached forever — see the assertion
    # further down that verifies uniqueness).
    now = datetime.now(timezone.utc)
    start_of_month = now.replace(day=1, hour=6, minute=0, second=0, microsecond=0)
    # Seed 2 current-month captures so the report qualifies.
    _insert_profile(uid, start_of_month + timedelta(days=1), 62)
    _insert_profile(uid, start_of_month + timedelta(days=2), 65)

    # Explicitly request the current month so we don't get a completed
    # (last-month) report served instead.
    current_month = now.strftime("%Y-%m")
    r1 = requests.get(f"{API}/hb/monthly-report/{current_month}", headers=_hdr(tok))
    assert r1.status_code == 200, r1.text
    rep1 = (r1.json() or {}).get("report") or {}
    assert rep1.get("total_sessions") == 2, rep1
    assert rep1.get("month") == current_month, rep1

    # Add a 3rd current-month capture; the current-month report must
    # recompute rather than serve the stale 2-session snapshot.
    _insert_profile(uid, start_of_month + timedelta(days=3), 68)

    r2 = requests.get(f"{API}/hb/monthly-report/{current_month}", headers=_hdr(tok))
    assert r2.status_code == 200, r2.text
    rep2 = (r2.json() or {}).get("report") or {}
    assert rep2.get("total_sessions") == 3, (
        "Current-month report must refresh on new capture, got: %r" % rep2
    )
    # And the stored doc should have been upserted in place (still one
    # row for this month → uniqueness index respected).
    stored = list(_col("hb_monthly_reports").find(
        {"user_id": uid, "month": current_month}, {"_id": 0},
    ))
    assert len(stored) == 1, stored
    assert stored[0].get("total_sessions") == 3
