"""Phase 12b — Before/After Frequency Map tests.

Covers `/api/harmonic-blueprint/before-after`:
- Auth gate (401)
- Pro gate (402 for free users)
- Empty state (200 with baseline=None when no captures)
- No-latest state (200 with baseline set, latest=None when only eigenmode)
- Full comparison shape when eigenmode + non-baseline session exist
- Band-level classification correctness (aligned/near/drift + improved flag)
- Celebration flag triggers on 5th non-baseline session
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests

BASE = os.environ.get("BACKEND_URL", "http://localhost:8001")
API = f"{BASE}/api"

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get(
    "ADMIN_TEST_PASSWORD", os.environ.get("ADMIN_PASSWORD", "JuzlUWlMMOjHM0u#m5qv0ds!oYp8")
)


def _mongo_col(name: str):
    from pymongo import MongoClient
    client = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    return client[os.environ.get("DB_NAME", "test_database")][name]


def _bands(offsets):
    base = {"sub": -30, "low": -25, "lowmid": -20, "mid": -18, "uppermid": -22, "presence": -28}
    keys = [
        ("sub", 20, 60), ("low", 60, 250), ("lowmid", 250, 500),
        ("mid", 500, 2000), ("uppermid", 2000, 4000), ("presence", 4000, 8000),
    ]
    return [
        {"key": k, "label": k.capitalize(), "lo": lo, "hi": hi,
         "db": base[k] + offsets.get(k, 0)}
        for k, lo, hi in keys
    ]


@pytest.fixture()
def pro_user():
    """Seed a fresh Pro user directly in Mongo and return (email, password, uid).

    Direct DB insert bypasses Stripe entirely and gives each test a clean slate.
    """
    email = f"ba+{uuid.uuid4().hex[:8]}@example.com"
    password = "BaTest9!"
    uid = str(uuid.uuid4())
    import bcrypt
    ph = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    users = _mongo_col("users")
    users.insert_one({
        "id": uid, "email": email, "name": "BaTest",
        "password_hash": ph, "role": "user",
        "pro_until": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "stripe_subscription_status": "active",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    yield email, password, uid
    # Cleanup so retries don't accumulate profiles.
    _mongo_col("resonance_profiles").delete_many({"user_id": uid})
    users.delete_one({"id": uid})


def _login(email, password):
    r = requests.post(f"{API}/auth/login",
                      json={"email": email, "password": password})
    r.raise_for_status()
    return r.json()["token"]


def test_before_after_requires_auth():
    r = requests.get(f"{API}/harmonic-blueprint/before-after")
    assert r.status_code == 401


def test_before_after_free_user_402():
    email = f"free+{uuid.uuid4().hex[:6]}@example.com"
    r = requests.post(f"{API}/auth/register",
                      json={"email": email, "password": "FreeTest9!"})
    r.raise_for_status()
    tok = r.json()["token"]
    r = requests.get(f"{API}/harmonic-blueprint/before-after",
                     headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 402


def test_before_after_no_baseline(pro_user):
    email, pw, _ = pro_user
    tok = _login(email, pw)
    r = requests.get(f"{API}/harmonic-blueprint/before-after",
                     headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    body = r.json()
    # A brand-new Pro user has no eigenmode yet — endpoint should degrade
    # gracefully with a helpful summary instead of 500ing.
    assert body["baseline"] is None
    assert body["latest"] is None
    assert body["band_deltas"] == []
    assert body["show_celebration"] is False
    assert "baseline" in body["summary_text"].lower() or "eigenmode" in body["summary_text"].lower()


def test_before_after_eigenmode_only(pro_user):
    email, pw, uid = pro_user
    _mongo_col("resonance_profiles").insert_one({
        "id": f"eigen-{uuid.uuid4().hex[:8]}", "user_id": uid,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "is_eigenmode": True, "resonance_score": 100,
        "bands": _bands({}), "spectrum": [], "confirmed_gaps": [],
    })
    tok = _login(email, pw)
    body = requests.get(f"{API}/harmonic-blueprint/before-after",
                       headers={"Authorization": f"Bearer {tok}"}).json()
    assert body["baseline"] is not None
    assert body["latest"] is None
    assert body["session_count"] == 0
    assert body["show_celebration"] is False


def test_before_after_full_shape_and_classification(pro_user):
    email, pw, uid = pro_user
    now = datetime.now(timezone.utc)
    _mongo_col("resonance_profiles").insert_many([
        {"id": f"eigen-{uuid.uuid4().hex[:8]}", "user_id": uid,
         "created_at": (now - timedelta(days=30)).isoformat(),
         "is_eigenmode": True, "resonance_score": 100,
         "bands": _bands({}), "spectrum": [], "confirmed_gaps": []},
        # Latest capture: low aligned (-1), mid drifted (-5), presence near (-3)
        {"id": f"s1-{uuid.uuid4().hex[:6]}", "user_id": uid,
         "created_at": now.isoformat(),
         "is_eigenmode": False, "resonance_score": 70,
         "bands": _bands({"low": -1, "mid": -5, "presence": -3}),
         "spectrum": [], "confirmed_gaps": []},
    ])
    tok = _login(email, pw)
    body = requests.get(f"{API}/harmonic-blueprint/before-after",
                       headers={"Authorization": f"Bearer {tok}"}).json()

    # Payload shape
    for key in ("baseline", "latest", "band_deltas", "summary_text",
                "session_count", "show_celebration"):
        assert key in body

    assert body["baseline"]["id"].startswith("eigen-")
    assert body["latest"]["id"].startswith("s1-")
    assert body["session_count"] == 1
    assert body["show_celebration"] is False

    by_key = {b["key"]: b for b in body["band_deltas"]}
    # Sub, lowmid, uppermid untouched → aligned + improved
    for k in ("sub", "lowmid", "uppermid"):
        assert by_key[k]["alignment"] == "aligned"
        assert by_key[k]["improved"] is True
    # Low -1 dB drift → still aligned (< 2 dB)
    assert by_key["low"]["alignment"] == "aligned"
    # Mid -5 dB drift → drift bucket (≥ 4 dB)
    assert by_key["mid"]["alignment"] == "drift"
    assert by_key["mid"]["improved"] is False
    # Presence -3 dB drift → near bucket (2-4 dB)
    assert by_key["presence"]["alignment"] == "near"
    assert by_key["presence"]["improved"] is False

    # Summary is a non-trivial string and mentions "drift" or a focus area
    # for the mid band which we explicitly staged as drifting.
    assert "focus area" in body["summary_text"] or "drift" in body["summary_text"]


def test_before_after_celebration_flag_on_fifth_session(pro_user):
    email, pw, uid = pro_user
    now = datetime.now(timezone.utc)
    docs = [
        {"id": f"eigen-{uuid.uuid4().hex[:8]}", "user_id": uid,
         "created_at": (now - timedelta(days=50)).isoformat(),
         "is_eigenmode": True, "resonance_score": 100,
         "bands": _bands({}), "spectrum": [], "confirmed_gaps": []},
    ]
    # 5 non-baseline sessions ⇒ show_celebration must be True
    for i in range(5):
        docs.append({
            "id": f"s{i}-{uuid.uuid4().hex[:6]}", "user_id": uid,
            "created_at": (now - timedelta(days=40 - i * 8)).isoformat(),
            "is_eigenmode": False, "resonance_score": 60 + i * 5,
            "bands": _bands({"mid": -3 + i * 0.5}),
            "spectrum": [], "confirmed_gaps": [],
        })
    _mongo_col("resonance_profiles").insert_many(docs)
    tok = _login(email, pw)
    body = requests.get(f"{API}/harmonic-blueprint/before-after",
                       headers={"Authorization": f"Bearer {tok}"}).json()
    assert body["session_count"] == 5
    assert body["show_celebration"] is True
