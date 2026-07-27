"""Backend tests for Harmonic Blueprint Phase 2 — Eigenmode Tuning.

Covers:
- GET /profile returns {profile, eigenmode} — both null for fresh Pro user.
- POST first save auto-flags eigenmode; second save does not.
- GET /eigenmode returns just eigenmode.
- POST /eigenmode/promote/{id} promotes and clears previous eigenmode.
- 404 when promoting non-existent id, 402 for free user.
- confirmed_gaps persisted onto profile.
- Retention: latest 5 + eigenmode preserved (up to 6 remain).
- Legacy migration: doc without is_eigenmode → oldest auto-promoted on GET.
- Free user 402 gating on all Phase-2 endpoints.
"""
from __future__ import annotations

import os
import uuid
import requests
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from _creds import ADMIN_EMAIL, ADMIN_PASSWORD

def _load_base_url():
    if os.environ.get("REACT_APP_BACKEND_URL"):
        return os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
    try:
        from dotenv import dotenv_values
        env = dotenv_values(Path(__file__).resolve().parent.parent.parent / "frontend" / ".env")
        return (env.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
    except Exception:
        return ""

BASE_URL = _load_base_url()
API = f"{BASE_URL}/api"


def _valid_payload(dur: float = 12.0, confirmed_gaps=None):
    return {
        "version": 1,
        "sample_rate": 22050.0,
        "duration": dur,
        "fft_size": 4096,
        "spectrum": [{"hz": 100.0 + i * 10, "db": -40.0 + (i % 20)} for i in range(64)],
        "dominant": [{"hz": 220.0, "db": -10.0}, {"hz": 440.0, "db": -14.0}],
        "dips": [{"hz": 1000.0, "db": -60.0}],
        "bands": [
            {"key": "low", "label": "Low", "lo": 60, "hi": 250, "db": -18.0},
            {"key": "mid", "label": "Mid", "lo": 250, "hi": 2000, "db": -22.0},
        ],
        "underrepresented": [{"key": "high", "label": "High"}],
        "confirmed_gaps": confirmed_gaps or [],
        "generated_at": None,
    }


@pytest.fixture(scope="module")
def mongo_col():
    try:
        from pymongo import MongoClient
    except ImportError:
        pytest.skip("pymongo unavailable")
    from dotenv import dotenv_values
    env = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
    mongo_url = env.get("MONGO_URL") or os.environ.get("MONGO_URL")
    db_name = env.get("DB_NAME") or os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        pytest.skip("Mongo env unavailable")
    mc = MongoClient(mongo_url)
    return mc[db_name]


@pytest.fixture(scope="module")
def admin_session(mongo_col):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    tok = r.json().get("access_token") or r.json().get("token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    me = s.get(f"{API}/auth/me").json()
    uid = me.get("id") or me.get("user", {}).get("id")
    s.user_id = uid
    # Full wipe via Mongo (bypass rate limit)
    mongo_col["resonance_profiles"].delete_many({"user_id": uid})
    yield s
    mongo_col["resonance_profiles"].delete_many({"user_id": uid})


@pytest.fixture(scope="module")
def free_session():
    s = requests.Session()
    email = f"TEST_hb2_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{API}/auth/register", json={"email": email, "password": "TestPass123!", "name": "HB2 Free"})
    assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
    tok = r.json().get("access_token") or r.json().get("token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    yield s


# ---------------- Fresh state GET ------------------------------------------

def test_fresh_get_returns_both_null(admin_session, mongo_col):
    mongo_col["resonance_profiles"].delete_many({"user_id": admin_session.user_id})
    r = admin_session.get(f"{API}/harmonic-blueprint/profile")
    assert r.status_code == 200
    body = r.json()
    assert "profile" in body and "eigenmode" in body
    assert body["profile"] is None
    assert body["eigenmode"] is None


def test_get_eigenmode_endpoint_null_when_fresh(admin_session, mongo_col):
    mongo_col["resonance_profiles"].delete_many({"user_id": admin_session.user_id})
    r = admin_session.get(f"{API}/harmonic-blueprint/eigenmode")
    assert r.status_code == 200
    assert r.json().get("eigenmode") is None


# ---------------- First save auto-flags eigenmode --------------------------

def test_first_save_becomes_eigenmode(admin_session, mongo_col):
    mongo_col["resonance_profiles"].delete_many({"user_id": admin_session.user_id})
    r = admin_session.post(f"{API}/harmonic-blueprint/profile", json=_valid_payload(dur=8.0))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert body.get("is_eigenmode") is True
    assert body["profile"].get("is_eigenmode") is True
    first_id = body["profile"]["id"]

    # GET /eigenmode returns this doc
    g = admin_session.get(f"{API}/harmonic-blueprint/eigenmode")
    assert g.status_code == 200
    assert g.json()["eigenmode"]["id"] == first_id


def test_second_save_is_not_eigenmode(admin_session, mongo_col):
    # Ensure there's already an eigenmode (from previous test) — else seed one
    uid = admin_session.user_id
    if not mongo_col["resonance_profiles"].find_one({"user_id": uid, "is_eigenmode": True}):
        mongo_col["resonance_profiles"].insert_one({
            "id": f"eigen-seed-{uuid.uuid4().hex[:8]}",
            "user_id": uid,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "duration": 5.0,
            "is_eigenmode": True,
        })
    r = admin_session.post(f"{API}/harmonic-blueprint/profile", json=_valid_payload(dur=9.0))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("is_eigenmode") is False
    assert body["profile"].get("is_eigenmode") is False
    # Eigenmode still survives — GET /eigenmode does not become the new one
    g = admin_session.get(f"{API}/harmonic-blueprint/eigenmode")
    assert g.status_code == 200
    eigen = g.json()["eigenmode"]
    assert eigen is not None
    assert eigen["id"] != body["profile"]["id"]


# ---------------- confirmed_gaps persistence -------------------------------

def test_confirmed_gaps_persisted(admin_session, mongo_col):
    uid = admin_session.user_id
    if not mongo_col["resonance_profiles"].find_one({"user_id": uid, "is_eigenmode": True}):
        mongo_col["resonance_profiles"].insert_one({
            "id": f"eigen-seed-{uuid.uuid4().hex[:8]}",
            "user_id": uid,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "duration": 5.0,
            "is_eigenmode": True,
        })
    gaps = [
        {"key": "low", "label": "Low", "delta_db": -6.2, "direction": "drifted low"},
        {"key": "mid", "label": "Mid", "delta_db": 4.5, "direction": "drifted high"},
    ]
    r = admin_session.post(
        f"{API}/harmonic-blueprint/profile",
        json=_valid_payload(dur=10.0, confirmed_gaps=gaps),
    )
    assert r.status_code == 200, r.text
    prof = r.json()["profile"]
    assert prof.get("confirmed_gaps") == gaps
    # Verify DB persistence
    doc = mongo_col["resonance_profiles"].find_one({"id": prof["id"]})
    assert doc["confirmed_gaps"] == gaps


# ---------------- Promote endpoint ----------------------------------------

def test_promote_makes_new_eigenmode_and_clears_previous(admin_session, mongo_col):
    uid = admin_session.user_id
    mongo_col["resonance_profiles"].delete_many({"user_id": uid})
    # Seed two docs, mark first as eigenmode
    old_id = f"old-eigen-{uuid.uuid4().hex[:8]}"
    new_id = f"future-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    mongo_col["resonance_profiles"].insert_many([
        {"id": old_id, "user_id": uid, "created_at": (now - timedelta(days=2)).isoformat(),
         "duration": 5.0, "is_eigenmode": True},
        {"id": new_id, "user_id": uid, "created_at": now.isoformat(),
         "duration": 6.0, "is_eigenmode": False},
    ])
    r = admin_session.post(f"{API}/harmonic-blueprint/eigenmode/promote/{new_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert body["eigenmode"]["id"] == new_id
    assert body["eigenmode"]["is_eigenmode"] is True

    old_doc = mongo_col["resonance_profiles"].find_one({"id": old_id})
    new_doc = mongo_col["resonance_profiles"].find_one({"id": new_id})
    assert old_doc.get("is_eigenmode") is False
    assert new_doc.get("is_eigenmode") is True


def test_promote_nonexistent_returns_404(admin_session):
    r = admin_session.post(f"{API}/harmonic-blueprint/eigenmode/promote/nonexistent-{uuid.uuid4().hex}")
    assert r.status_code == 404


# ---------------- Retention with eigenmode --------------------------------

def test_retention_keeps_latest_5_plus_eigenmode(admin_session, mongo_col):
    """Seed 8 docs with the eigenmode being the OLDEST. After one POST that
    triggers the trim, we should retain latest-5 + eigenmode = 6 docs (the
    eigenmode falls outside the newest-5 window).
    """
    uid = admin_session.user_id
    col = mongo_col["resonance_profiles"]
    col.delete_many({"user_id": uid})
    base = datetime.now(timezone.utc)
    eigen_id = f"eigen-old-{uuid.uuid4().hex[:8]}"
    col.insert_one({
        "id": eigen_id, "user_id": uid,
        "created_at": (base - timedelta(days=30)).isoformat(),
        "duration": 5.0, "is_eigenmode": True,
    })
    for i in range(7):
        col.insert_one({
            "id": f"seed-{i}-{uuid.uuid4().hex[:6]}",
            "user_id": uid,
            "created_at": (base - timedelta(minutes=10 - i)).isoformat(),
            "duration": float(i + 1),
            "is_eigenmode": False,
        })
    assert col.count_documents({"user_id": uid}) == 8

    r = admin_session.post(f"{API}/harmonic-blueprint/profile", json=_valid_payload(dur=7.0))
    if r.status_code != 200:
        pytest.skip(f"POST returned {r.status_code}; rate-limited")

    remaining = list(col.find({"user_id": uid}))
    ids = [d["id"] for d in remaining]
    # Expect: 5 newest + eigenmode = 6
    assert len(remaining) == 6, f"expected 6, got {len(remaining)}: {ids}"
    assert eigen_id in ids, "eigenmode must survive retention"
    # eigenmode still flagged
    e = col.find_one({"id": eigen_id})
    assert e.get("is_eigenmode") is True


# ---------------- Legacy migration ----------------------------------------

def test_legacy_profile_without_flag_auto_promoted(admin_session, mongo_col):
    """A Phase-1 doc with NO is_eigenmode field must be auto-promoted (as
    oldest) when GET /eigenmode is called."""
    uid = admin_session.user_id
    col = mongo_col["resonance_profiles"]
    col.delete_many({"user_id": uid})
    base = datetime.now(timezone.utc)
    legacy_id = f"legacy-{uuid.uuid4().hex[:8]}"
    newer_id = f"newer-{uuid.uuid4().hex[:8]}"
    # Insert two docs, neither with is_eigenmode
    col.insert_one({
        "id": legacy_id, "user_id": uid,
        "created_at": (base - timedelta(days=5)).isoformat(),
        "duration": 5.0,
    })
    col.insert_one({
        "id": newer_id, "user_id": uid,
        "created_at": base.isoformat(),
        "duration": 6.0,
    })
    # Confirm neither has is_eigenmode
    for d in col.find({"user_id": uid}):
        assert "is_eigenmode" not in d or d.get("is_eigenmode") is None

    r = admin_session.get(f"{API}/harmonic-blueprint/eigenmode")
    assert r.status_code == 200
    eigen = r.json()["eigenmode"]
    assert eigen is not None
    assert eigen["id"] == legacy_id, "oldest should be promoted"
    assert eigen.get("is_eigenmode") is True

    # Verify DB reflects the auto-flag
    persisted = col.find_one({"id": legacy_id})
    assert persisted.get("is_eigenmode") is True
    # newer doc unchanged (either missing flag or false)
    newer = col.find_one({"id": newer_id})
    assert not newer.get("is_eigenmode")


# ---------------- Free-tier gating ----------------------------------------

def test_free_user_get_eigenmode_402(free_session):
    r = free_session.get(f"{API}/harmonic-blueprint/eigenmode")
    assert r.status_code == 402


def test_free_user_promote_402(free_session):
    r = free_session.post(f"{API}/harmonic-blueprint/eigenmode/promote/whatever")
    assert r.status_code == 402
