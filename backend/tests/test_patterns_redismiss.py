"""My Patterns — 7-session auto re-evaluation of dismissed patterns.

Verifies the behaviour requested in the "My Patterns" refresh:
  1. Dismissing a pattern stamps it with the user's current wellness_journey
     session count (stored under `dismissed_patterns_v2`).
  2. GET /api/me/patterns returns that key as dismissed while
     current_sessions - dismissed_at < 7.
  3. Once 7 or more new journey rows have been logged, the same key
     re-surfaces (no manual reset required).
  4. POST /api/me/patterns/clear un-dismisses everything immediately
     (the manual "Reset patterns" escape hatch).
  5. Legacy `dismissed_patterns` list entries are still honoured on first
     read (backwards compatibility for pre-existing users).
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
def user_ctx():
    email = f"pat+{uuid.uuid4().hex[:8]}@example.com"
    pw = "Pat9!aA"
    uid = str(uuid.uuid4())
    _col("users").insert_one({
        "id": uid, "email": email, "name": "PatTest",
        "password_hash": bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode(),
        "role": "user",
        "pro_until": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    yield email, pw, uid
    _col("wellness_journey").delete_many({"user_id": uid})
    _col("users").delete_one({"id": uid})


def _login(email, pw):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def _seed_journey_rows(uid: str, n: int, *, tod: str = "morning", hz: int = 528):
    """Insert `n` wellness_journey rows that would surface a top_frequency
    pattern. The pattern detector requires ≥ 3 rows AND a single frequency
    dominating ≥ 40 % of frequency-carrying runs, so any n≥3 with a
    single hz triggers a stable top_frequency key of `top_frequency:{hz}`.
    """
    now = datetime.now(timezone.utc)
    docs = []
    for i in range(n):
        docs.append({
            "id": str(uuid.uuid4()),
            "user_id": uid,
            "created_at": (now - timedelta(minutes=i)).isoformat(),
            "frequency": hz,
            "time_of_day": tod,
            "duration_seconds": 900,
            "mood_pre": "calm",
            "mood_post": "calm",
        })
    _col("wellness_journey").insert_many(docs)


def _invalidate_cache(uid: str):
    """Wipe the 15-min patterns cache so subsequent GETs recompute."""
    _col("users").update_one({"id": uid}, {"$unset": {"patterns_cache": ""}})


def test_dismissed_pattern_stays_hidden_until_7_new_sessions(user_ctx):
    email, pw, uid = user_ctx
    tok = _login(email, pw)

    # 1) Seed enough journey rows to surface top_frequency:528
    _seed_journey_rows(uid, 5)
    _invalidate_cache(uid)

    r = requests.get(f"{API}/me/patterns", headers=_hdr(tok))
    assert r.status_code == 200, r.text
    data = r.json()
    keys = {p["key"] for p in data.get("patterns", [])}
    assert "top_frequency:528" in keys, keys
    assert data["session_count"] == 5
    assert data["redismiss_window_sessions"] == 7
    assert "top_frequency:528" not in set(data.get("dismissed") or [])

    # 2) Dismiss it — stamped with session count = 5
    r = requests.post(
        f"{API}/me/patterns/top_frequency:528/dismiss", headers=_hdr(tok),
    )
    assert r.status_code == 200, r.text
    assert r.json()["session_count"] == 5

    # 3) Still within window (add 6 more, total = 11 → 11-5 = 6 < 7)
    _seed_journey_rows(uid, 6)
    _invalidate_cache(uid)
    r = requests.get(f"{API}/me/patterns", headers=_hdr(tok))
    data = r.json()
    assert data["session_count"] == 11
    assert "top_frequency:528" in set(data.get("dismissed") or []), data

    # 4) Cross the 7-session boundary — one more session → 12-5 = 7 ≥ 7
    _seed_journey_rows(uid, 1)
    _invalidate_cache(uid)
    r = requests.get(f"{API}/me/patterns", headers=_hdr(tok))
    data = r.json()
    assert data["session_count"] == 12
    assert "top_frequency:528" not in set(data.get("dismissed") or []), (
        "pattern should re-surface after 7 new sessions"
    )
    # And the underlying detection still surfaces the key.
    assert "top_frequency:528" in {p["key"] for p in data["patterns"]}


def test_manual_reset_clears_dismissals_immediately(user_ctx):
    email, pw, uid = user_ctx
    tok = _login(email, pw)
    _seed_journey_rows(uid, 4)
    _invalidate_cache(uid)

    requests.post(
        f"{API}/me/patterns/top_frequency:528/dismiss", headers=_hdr(tok),
    ).raise_for_status()

    # Confirm it is dismissed pre-reset.
    data = requests.get(f"{API}/me/patterns", headers=_hdr(tok)).json()
    assert "top_frequency:528" in set(data.get("dismissed") or [])

    # Manual reset via the new settings menu.
    r = requests.post(f"{API}/me/patterns/clear", headers=_hdr(tok))
    assert r.status_code == 200 and r.json().get("ok") is True

    data = requests.get(f"{API}/me/patterns", headers=_hdr(tok)).json()
    assert data.get("dismissed") == []
    # Verify v2 map is empty too so the reset really wiped both stores.
    udoc = _col("users").find_one({"id": uid}, {"dismissed_patterns_v2": 1})
    assert (udoc.get("dismissed_patterns_v2") or {}) == {}


def test_legacy_dismissed_list_is_migrated_into_7_session_window(user_ctx):
    """A user who had a v1 `dismissed_patterns: [key]` entry before this
    change should still see that key as dismissed on the next
    /me/patterns read, but the entry should be migrated into
    `dismissed_patterns_v2` (stamped with the current session count) so
    it re-surfaces after the normal 7-session window."""
    email, pw, uid = user_ctx
    tok = _login(email, pw)

    _seed_journey_rows(uid, 4)
    _invalidate_cache(uid)
    # Simulate a pre-existing legacy dismissal.
    _col("users").update_one(
        {"id": uid},
        {"$set": {"dismissed_patterns": ["top_frequency:528"]}},
    )

    data = requests.get(f"{API}/me/patterns", headers=_hdr(tok)).json()
    assert "top_frequency:528" in set(data.get("dismissed") or []), (
        "legacy dismissal must be honoured on first read"
    )

    # After the read the legacy list should have been drained and the
    # key promoted into v2 stamped at session_count=4.
    udoc = _col("users").find_one(
        {"id": uid}, {"dismissed_patterns": 1, "dismissed_patterns_v2": 1},
    )
    assert not udoc.get("dismissed_patterns"), "legacy list should be drained"
    assert (udoc.get("dismissed_patterns_v2") or {}).get("top_frequency:528") == 4

    # Cross the 7-session boundary → pattern re-surfaces automatically.
    _seed_journey_rows(uid, 7)  # total = 11 → 11-4 = 7 ≥ 7
    _invalidate_cache(uid)
    data2 = requests.get(f"{API}/me/patterns", headers=_hdr(tok)).json()
    assert "top_frequency:528" not in set(data2.get("dismissed") or []), (
        "migrated legacy dismissal should re-surface after 7 new sessions"
    )
