"""Phase 12c/12d — Session Impact Rating + Monthly Report tests.

Covers:
- GET /api/hb/pending-impact-ratings (24h threshold + hb_recommended filter)
- POST /api/hb/impact-rating (persistence + idempotency)
- GET /api/hb/effective-frequencies (aggregation + sample_count>=2 gate + 402)
- GET /api/hb/monthly-report (lazy generation, 2-session gate, 402)
- GET /api/hb/monthly-report/{month} (specific month + 400 bad format + 404)
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests
import bcrypt

BASE = os.environ.get("BACKEND_URL", "http://localhost:8001")
API = f"{BASE}/api"


def _mongo_col(name: str):
    from pymongo import MongoClient
    return MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))[
        os.environ.get("DB_NAME", "test_database")
    ][name]


def _bands(offsets):
    base = {"sub": -30, "low": -25, "lowmid": -20, "mid": -18, "uppermid": -22, "presence": -28}
    keys = [("sub", 20, 60), ("low", 60, 250), ("lowmid", 250, 500),
            ("mid", 500, 2000), ("uppermid", 2000, 4000), ("presence", 4000, 8000)]
    return [{"key": k, "label": k.capitalize(), "lo": lo, "hi": hi,
             "db": base[k] + offsets.get(k, 0)} for k, lo, hi in keys]


@pytest.fixture()
def pro_user():
    email = f"imp+{uuid.uuid4().hex[:8]}@example.com"
    pw = "Impact9!"
    uid = str(uuid.uuid4())
    _mongo_col("users").insert_one({
        "id": uid, "email": email, "name": "ImpactTest",
        "password_hash": bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode(),
        "role": "user",
        "pro_until": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "stripe_subscription_status": "active",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    yield email, pw, uid
    _mongo_col("resonance_profiles").delete_many({"user_id": uid})
    _mongo_col("wellness_journey").delete_many({"user_id": uid})
    _mongo_col("hb_monthly_reports").delete_many({"user_id": uid})
    _mongo_col("users").delete_one({"id": uid})


def _login(email, pw):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw})
    r.raise_for_status()
    return r.json()["token"]


# ---------------- Impact rating ----------------

def test_pending_impact_ratings_filters_correctly(pro_user):
    email, pw, uid = pro_user
    now = datetime.now(timezone.utc)
    rows = [
        # ≥24h old + hb_recommended + unrated → INCLUDED
        {"id": "e-old-unrated", "user_id": uid, "frequency": 432.0,
         "created_at": (now - timedelta(hours=26)).isoformat(),
         "hb_recommended": True, "preset_label": "432 Hz Earth",
         "duration_actual_seconds": 900},
        # <24h old → EXCLUDED
        {"id": "e-fresh", "user_id": uid, "frequency": 528.0,
         "created_at": (now - timedelta(hours=2)).isoformat(),
         "hb_recommended": True, "preset_label": "528 Hz",
         "duration_actual_seconds": 500},
        # Not hb_recommended → EXCLUDED
        {"id": "e-not-hb", "user_id": uid, "frequency": 963.0,
         "created_at": (now - timedelta(days=2)).isoformat(),
         "hb_recommended": False, "preset_label": "963 Hz",
         "duration_actual_seconds": 500},
        # Already rated → EXCLUDED
        {"id": "e-rated", "user_id": uid, "frequency": 741.0,
         "created_at": (now - timedelta(days=2)).isoformat(),
         "hb_recommended": True, "preset_label": "741 Hz",
         "impact_rating": "clear_shift", "duration_actual_seconds": 500},
    ]
    _mongo_col("wellness_journey").insert_many(rows)
    tok = _login(email, pw)
    body = requests.get(f"{API}/hb/pending-impact-ratings",
                       headers={"Authorization": f"Bearer {tok}"}).json()
    ids = [p["id"] for p in body["pending"]]
    assert ids == ["e-old-unrated"], f"unexpected pending list: {ids}"
    # Label falls back through preset_label → soundscape → freq
    assert body["pending"][0]["label"] == "432 Hz Earth"


def test_impact_rating_persists_and_is_idempotent(pro_user):
    email, pw, uid = pro_user
    now = datetime.now(timezone.utc)
    _mongo_col("wellness_journey").insert_one({
        "id": "rate-1", "user_id": uid, "frequency": 432.0,
        "created_at": (now - timedelta(days=2)).isoformat(),
        "hb_recommended": True, "preset_label": "432 Hz",
        "duration_actual_seconds": 900,
    })
    tok = _login(email, pw)
    headers = {"Authorization": f"Bearer {tok}"}
    # First submission
    r = requests.post(f"{API}/hb/impact-rating", headers=headers,
                     json={"entry_id": "rate-1", "rating": "subtle_difference"})
    assert r.status_code == 200 and r.json()["ok"] is True
    # Second submission overrides — verify by fetching the row directly.
    r2 = requests.post(f"{API}/hb/impact-rating", headers=headers,
                     json={"entry_id": "rate-1", "rating": "clear_shift"})
    assert r2.status_code == 200
    row = _mongo_col("wellness_journey").find_one({"id": "rate-1"})
    assert row["impact_rating"] == "clear_shift"
    # Bad rating value rejected by pydantic pattern
    r3 = requests.post(f"{API}/hb/impact-rating", headers=headers,
                     json={"entry_id": "rate-1", "rating": "whatever"})
    assert r3.status_code == 422
    # 404 for someone else's entry
    r4 = requests.post(f"{API}/hb/impact-rating", headers=headers,
                     json={"entry_id": "does-not-exist", "rating": "clear_shift"})
    assert r4.status_code == 404


def test_effective_frequencies_requires_pro():
    email = f"free+{uuid.uuid4().hex[:6]}@example.com"
    r = requests.post(f"{API}/auth/register", json={"email": email, "password": "FreeTest9!"})
    r.raise_for_status()
    tok = r.json()["token"]
    r = requests.get(f"{API}/hb/effective-frequencies",
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 402


def test_effective_frequencies_aggregates_and_ranks(pro_user):
    email, pw, uid = pro_user
    now = datetime.now(timezone.utc)
    # 432 Hz: 2 clear + 1 subtle → mean 2.33 → 78%
    # 528 Hz: 2 subtle → mean 1 → 33%
    # 396 Hz: 1 rated only → EXCLUDED (needs >=2 samples)
    rows = []
    for _ in range(2):
        rows.append({"id": uuid.uuid4().hex, "user_id": uid, "frequency": 432.0,
                     "hb_recommended": True, "impact_rating": "clear_shift",
                     "created_at": now.isoformat(), "preset_label": "432 Hz Earth",
                     "duration_actual_seconds": 900})
    rows.append({"id": uuid.uuid4().hex, "user_id": uid, "frequency": 432.0,
                 "hb_recommended": True, "impact_rating": "subtle_difference",
                 "created_at": now.isoformat(), "preset_label": "432 Hz Earth",
                 "duration_actual_seconds": 500})
    for _ in range(2):
        rows.append({"id": uuid.uuid4().hex, "user_id": uid, "frequency": 528.0,
                     "hb_recommended": True, "impact_rating": "subtle_difference",
                     "created_at": now.isoformat(), "preset_label": "528 Hz",
                     "duration_actual_seconds": 500})
    rows.append({"id": uuid.uuid4().hex, "user_id": uid, "frequency": 396.0,
                 "hb_recommended": True, "impact_rating": "clear_shift",
                 "created_at": now.isoformat(), "preset_label": "396 Hz",
                 "duration_actual_seconds": 500})
    _mongo_col("wellness_journey").insert_many(rows)
    tok = _login(email, pw)
    body = requests.get(f"{API}/hb/effective-frequencies",
                       headers={"Authorization": f"Bearer {tok}"}).json()
    freqs = body["frequencies"]
    assert [f["frequency"] for f in freqs] == [432, 528]
    assert freqs[0]["score"] == 78 and freqs[0]["sample_count"] == 3
    assert freqs[1]["score"] == 33 and freqs[1]["sample_count"] == 2


# ---------------- Monthly report ----------------

def test_monthly_report_requires_pro():
    email = f"free+{uuid.uuid4().hex[:6]}@example.com"
    r = requests.post(f"{API}/auth/register", json={"email": email, "password": "FreeTest9!"})
    r.raise_for_status()
    tok = r.json()["token"]
    r = requests.get(f"{API}/hb/monthly-report",
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 402


def test_monthly_report_empty_when_too_few_sessions(pro_user):
    email, pw, uid = pro_user
    # Only 1 capture this month → NOT generated
    now = datetime.now(timezone.utc)
    _mongo_col("resonance_profiles").insert_one({
        "id": f"c-{uuid.uuid4().hex[:6]}", "user_id": uid,
        "created_at": now.isoformat(), "is_eigenmode": False,
        "resonance_score": 70, "bands": _bands({})})
    tok = _login(email, pw)
    body = requests.get(f"{API}/hb/monthly-report",
                       headers={"Authorization": f"Bearer {tok}"}).json()
    assert body["report"] is None
    assert body["available_months"] == []


def test_monthly_report_lazy_generation(pro_user):
    email, pw, uid = pro_user
    now = datetime.now(timezone.utc)
    docs = [
        {"id": f"eigen-{uuid.uuid4().hex[:6]}", "user_id": uid,
         "created_at": (now - timedelta(days=60)).isoformat(),
         "is_eigenmode": True, "resonance_score": 100,
         "bands": _bands({}), "confirmed_gaps": []},
        # Previous month reference
        {"id": f"p-{uuid.uuid4().hex[:6]}", "user_id": uid,
         "created_at": (now - timedelta(days=32)).isoformat(),
         "is_eigenmode": False, "resonance_score": 60,
         "bands": _bands({"low": -6, "mid": -5}), "confirmed_gaps": []},
        # This month captures (both >= 2 required)
        {"id": f"c1-{uuid.uuid4().hex[:6]}", "user_id": uid,
         "created_at": (now - timedelta(days=15)).isoformat(),
         "is_eigenmode": False, "resonance_score": 70,
         "bands": _bands({"low": -3, "mid": -5}), "confirmed_gaps": []},
        {"id": f"c2-{uuid.uuid4().hex[:6]}", "user_id": uid,
         "created_at": (now - timedelta(days=2)).isoformat(),
         "is_eigenmode": False, "resonance_score": 82,
         "bands": _bands({"low": -1, "mid": -4}), "confirmed_gaps": []},
    ]
    _mongo_col("resonance_profiles").insert_many(docs)
    # HB-recommended journey for listening minutes
    _mongo_col("wellness_journey").insert_one({
        "id": uuid.uuid4().hex, "user_id": uid, "frequency": 432.0,
        "hb_recommended": True,
        "created_at": (now - timedelta(days=8)).isoformat(),
        "duration_actual_seconds": 1500,
    })
    tok = _login(email, pw)
    body = requests.get(f"{API}/hb/monthly-report",
                       headers={"Authorization": f"Bearer {tok}"}).json()
    rep = body["report"]
    assert rep is not None
    for k in ("month", "title", "total_sessions", "resonance_score_current",
              "resonance_score_previous", "resonance_score_delta",
              "most_improved_ranges", "most_persistent_gaps",
              "recommended_frequencies", "listening_seconds", "listening_minutes"):
        assert k in rep
    assert rep["title"].startswith("Your ") and "Resonance Journey" in rep["title"]
    assert rep["total_sessions"] >= 2
    assert rep["resonance_score_current"] == 82
    assert rep["resonance_score_previous"] == 60
    assert rep["resonance_score_delta"] == 22
    assert rep["listening_minutes"] == 25
    assert len(body["available_months"]) >= 1


def test_monthly_report_specific_month_bad_format(pro_user):
    email, pw, _ = pro_user
    tok = _login(email, pw)
    r = requests.get(f"{API}/hb/monthly-report/not-a-month",
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 400


def test_monthly_report_specific_month_404_when_no_data(pro_user):
    email, pw, _ = pro_user
    tok = _login(email, pw)
    r = requests.get(f"{API}/hb/monthly-report/2020-01",
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 404
