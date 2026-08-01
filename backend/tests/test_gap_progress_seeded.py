"""Phase 12 — Deep verification of /api/harmonic-blueprint/gap-progress with
seeded Pro user + eigenmode + multiple historical resonance_profiles.

Verifies:
- Payload structure and shape end-to-end.
- Trend classification (improving / stable / attention) under ±10% hysteresis.
- closure_pct math ((first - latest) / first * 100).
- Timeline entries chronologically ascending, ints 0-100, is_eigenmode flag.
- Summary uses first NON-eigenmode session, improvement_pct math correct.
- Empty state for a fresh Pro user (no profiles) => 200 with empty arrays.
"""
from __future__ import annotations

import os
import uuid
import asyncio
from datetime import datetime, timedelta, timezone

import bcrypt
import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE = os.environ.get("BACKEND_URL", "http://localhost:8001")
API = f"{BASE}/api"

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


async def _seed_pro_user_with_history(email: str, password: str,
                                       include_history: bool = True) -> str:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    await db.users.insert_one({
        "id": user_id,
        "email": email,
        "password_hash": _hash(password),
        "name": "Gap Test",
        "role": "user",
        "created_at": now.isoformat(),
        "pro_until": (now + timedelta(days=30)).isoformat(),
        "tokens_valid_after": now.isoformat(),
    })

    if include_history:
        # Bands: sub, low, lowmid, mid, uppermid, presence
        def _bands(sub_db, low_db, lowmid_db):
            return [
                {"key": "sub",       "lo": 20,   "hi": 60,    "db": sub_db},
                {"key": "low",       "lo": 60,   "hi": 250,   "db": low_db},
                {"key": "lowmid",    "lo": 250,  "hi": 500,   "db": lowmid_db},
                {"key": "mid",       "lo": 500,  "hi": 2000,  "db": -20},
                {"key": "uppermid",  "lo": 2000, "hi": 4000,  "db": -20},
                {"key": "presence",  "lo": 4000, "hi": 8000,  "db": -20},
            ]

        # Eigenmode baseline: sub=-30, low=-30, lowmid=-30
        # confirmed_gaps at latest doc = [sub, low, lowmid]
        # We craft:
        #   session1 (first non-baseline): sub delta 20 (severe), low delta 10, lowmid delta 5
        #   session2:                       sub delta 15,          low delta 10, lowmid delta 5
        #   session3 (latest):              sub delta 4 (improving), low delta 10 (stable), lowmid delta 12 (attention)
        # improving: closure_pct = (20-4)/20*100 = 80  -> improving
        # low: (10-10)/10*100 = 0 -> stable
        # lowmid: (5-12)/5 * 100 = -140 -> attention
        profiles = [
            {"is_eigen": True,  "t": now - timedelta(days=40), "bands": _bands(-30, -30, -30), "score": 100},
            {"is_eigen": False, "t": now - timedelta(days=30), "bands": _bands(-50, -40, -35), "score": 60},
            {"is_eigen": False, "t": now - timedelta(days=15), "bands": _bands(-45, -40, -35), "score": 68},
            {"is_eigen": False, "t": now - timedelta(days=1),  "bands": _bands(-34, -40, -18), "score": 78},
        ]
        confirmed_gaps = [
            {"key": "sub",    "label": "Sub",      "lo": 20,  "hi": 60,  "direction": "under"},
            {"key": "low",    "label": "Low",      "lo": 60,  "hi": 250, "direction": "under"},
            {"key": "lowmid", "label": "Low-mid",  "lo": 250, "hi": 500, "direction": "under"},
        ]
        for i, p in enumerate(profiles):
            doc = {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "bands": p["bands"],
                "is_eigenmode": p["is_eigen"],
                "created_at": p["t"].isoformat(),
                "resonance_score": p["score"],
                # only the latest doc carries the confirmed_gaps that the endpoint reads
                "confirmed_gaps": confirmed_gaps if i == len(profiles) - 1 else [],
            }
            await db.resonance_profiles.insert_one(doc)

    client.close()
    return user_id


async def _cleanup(email: str, user_id: str):
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    await db.users.delete_many({"email": email})
    await db.resonance_profiles.delete_many({"user_id": user_id})
    client.close()


def _login(email, password):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return r.json()["token"]


@pytest.fixture()
def pro_with_history():
    email = f"test_gap+{uuid.uuid4().hex[:6]}@example.com"
    pw = "TestGap9!"
    uid = asyncio.get_event_loop().run_until_complete(
        _seed_pro_user_with_history(email, pw, include_history=True)
    )
    token = _login(email, pw)
    yield {"email": email, "token": token, "user_id": uid}
    asyncio.get_event_loop().run_until_complete(_cleanup(email, uid))


@pytest.fixture()
def pro_empty():
    email = f"test_empty+{uuid.uuid4().hex[:6]}@example.com"
    pw = "TestGap9!"
    uid = asyncio.get_event_loop().run_until_complete(
        _seed_pro_user_with_history(email, pw, include_history=False)
    )
    token = _login(email, pw)
    yield {"email": email, "token": token, "user_id": uid}
    asyncio.get_event_loop().run_until_complete(_cleanup(email, uid))


def test_seeded_pro_full_payload(pro_with_history):
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {pro_with_history['token']}"
    r = s.get(f"{API}/harmonic-blueprint/gap-progress")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) >= {"gaps", "timeline", "eigenmode_id", "summary"}
    assert body["eigenmode_id"] is not None

    # Timeline: 4 entries (1 eigenmode + 3 sessions), chronologically ascending
    timeline = body["timeline"]
    assert len(timeline) == 4, timeline
    for i in range(1, len(timeline)):
        assert timeline[i]["at"] >= timeline[i - 1]["at"]
        assert isinstance(timeline[i]["score"], int)
        assert 0 <= timeline[i]["score"] <= 100
    assert timeline[0]["is_eigenmode"] is True
    assert all(not t["is_eigenmode"] for t in timeline[1:])

    # Summary uses first NON-eigenmode session (score 60) vs latest (78)
    summary = body["summary"]
    assert summary is not None
    assert summary["session_count"] == 3
    assert summary["first_score"] == 60
    assert summary["latest_score"] == 78
    expected_pct = round((78 - 60) / 60 * 100, 1)  # 30.0
    assert summary["improvement_pct"] == expected_pct

    # Gap trend classification
    gaps = {g["key"]: g for g in body["gaps"]}
    assert set(gaps.keys()) == {"sub", "low", "lowmid"}

    # sub: first_sev=20 (|-50-(-30)|), latest_sev=4 (|-34-(-30)|) -> closure 80 -> improving
    assert gaps["sub"]["first_severity"] == 20.0
    assert gaps["sub"]["latest_severity"] == 4.0
    assert gaps["sub"]["closure_pct"] == 80.0
    assert gaps["sub"]["trend"] == "improving"

    # low: first_sev=10, latest_sev=10 -> closure 0 -> stable
    assert gaps["low"]["first_severity"] == 10.0
    assert gaps["low"]["latest_severity"] == 10.0
    assert gaps["low"]["closure_pct"] == 0.0
    assert gaps["low"]["trend"] == "stable"

    # lowmid: first_sev=5, latest_sev=12 -> closure -140 -> attention
    assert gaps["lowmid"]["first_severity"] == 5.0
    assert gaps["lowmid"]["latest_severity"] == 12.0
    assert gaps["lowmid"]["closure_pct"] == -140.0
    assert gaps["lowmid"]["trend"] == "attention"

    # Each gap history should include 4 points w/ severity
    for g in body["gaps"]:
        assert g["sample_count"] == 4
        assert len(g["history"]) == 4
        for pt in g["history"]:
            assert "severity" in pt
            assert isinstance(pt["severity"], (int, float))
            assert "at" in pt


def test_seeded_pro_empty_state(pro_empty):
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {pro_empty['token']}"
    r = s.get(f"{API}/harmonic-blueprint/gap-progress")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["gaps"] == []
    assert body["timeline"] == []
    assert body["summary"] is None
    assert body["eigenmode_id"] is None
