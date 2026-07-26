"""Backend tests for iter43 legacy `pro_expires_at` handling + one-shot
admin migration endpoint (/api/admin/promo/migrate-legacy).

Verifies:
- _is_pro() honors legacy `pro_expires_at` when `pro_until` is absent.
- /me/subscription reports pro=true, plan=pro, correct days_left from legacy field.
- Expired legacy field yields pro=false.
- Free-tier session cap lifted for legacy-pro user.
- Admin migration endpoint promotes legacy records to canonical, idempotent,
  non-admin gets 403, canonical users untouched.
- Z-suffix ISO parsing works.
"""
from __future__ import annotations

import os
import uuid
import asyncio
import requests
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import dotenv_values
from pymongo import MongoClient

from _creds import ADMIN_EMAIL, ADMIN_PASSWORD  # noqa: E402

_env_pre = dotenv_values(Path(__file__).resolve().parent.parent.parent / "frontend" / ".env")
# NOTE: We use the LOCAL backend URL (not the preview REACT_APP_BACKEND_URL) because
# this test requires direct MongoDB writes to inject the legacy `pro_expires_at`
# field — and only the local backend shares the local Mongo instance. The preview
# environment routes to a different Mongo we cannot touch from here.
BASE_URL = os.environ.get("BACKEND_TEST_URL", "http://localhost:8001").rstrip("/")

# Mongo direct-write for hand-injecting legacy field
_env = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
MONGO_URL = _env.get("MONGO_URL")
DB_NAME = _env.get("DB_NAME")


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    yield db
    client.close()


def _set_legacy(db, email, iso_value, clear_canonical=True):
    upd = {"$set": {"pro_expires_at": iso_value}}
    if clear_canonical:
        upd["$unset"] = {"pro_until": "", "pro_source": ""}
    r = db.users.update_one({"email": email}, upd)
    assert r.matched_count == 1, f"user {email} not found in Mongo"


def _get_user(db, email):
    return db.users.find_one({"email": email}, {"_id": 0})


def _register():
    s = requests.Session()
    email = f"test_legacy_{uuid.uuid4().hex[:10]}@example.com"
    r = s.post(f"{BASE_URL}/api/auth/register", json={
        "email": email, "password": "TestPass123!", "name": "LegacyTester",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    # Token may be in cookie or body
    token = data.get("token") or data.get("access_token")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    return s, email, data.get("user", data).get("id") or data.get("id")


def _login_admin():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    tok = r.json().get("token") or r.json().get("access_token")
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    return s


# -----------------------------------------------------------------------------
# Test 1: legacy pro_expires_at in future -> pro=true
# -----------------------------------------------------------------------------

def test_legacy_pro_expires_at_future_grants_pro(mongo):
    s, email, _ = _register()
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    _set_legacy(mongo, email, future)

    r = s.get(f"{BASE_URL}/api/me/subscription")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["pro"] is True, j
    assert j["plan"] == "pro", j
    # days_left should be ~30 (allow 29 or 30 depending on rounding)
    assert j["days_left"] in (29, 30), f"expected ~30 days_left, got {j['days_left']}"
    assert j["pro_until"] == future


# -----------------------------------------------------------------------------
# Test 2: legacy pro_expires_at in past -> pro=false
# -----------------------------------------------------------------------------

def test_legacy_pro_expires_at_past_denies_pro(mongo):
    s, email, _ = _register()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _set_legacy(mongo, email, past)

    r = s.get(f"{BASE_URL}/api/me/subscription")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["pro"] is False, j
    assert j["days_left"] == 0


# -----------------------------------------------------------------------------
# Test 3: Z-suffix Zulu-time ISO parsing works
# -----------------------------------------------------------------------------

def test_legacy_pro_expires_at_zulu_suffix(mongo):
    s, email, _ = _register()
    # Format with Z suffix instead of +00:00
    dt = datetime.now(timezone.utc) + timedelta(days=10)
    iso_z = dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}Z"
    _set_legacy(mongo, email, iso_z)

    r = s.get(f"{BASE_URL}/api/me/subscription")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["pro"] is True, j
    assert j["days_left"] in (9, 10)


# -----------------------------------------------------------------------------
# Test 4: legacy user can save >3 sessions (free cap lifted)
# -----------------------------------------------------------------------------

def test_legacy_user_free_session_cap_lifted(mongo):
    s, email, _ = _register()
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    _set_legacy(mongo, email, future)

    # POST 5 sessions - none should 402
    for i in range(5):
        payload = {
            "name": f"LegacyProSession{i}",
            "frequency": 528.0,
            "duration_minutes": 5,
            "waveform": "sine",
        }
        r = s.post(f"{BASE_URL}/api/sessions", json=payload)
        assert r.status_code == 200, f"session {i} rejected: {r.status_code} {r.text}"


# -----------------------------------------------------------------------------
# Test 5: Non-admin gets 403 on migration endpoint
# -----------------------------------------------------------------------------

def test_migrate_legacy_non_admin_forbidden():
    s, _, _ = _register()
    r = s.post(f"{BASE_URL}/api/admin/promo/migrate-legacy")
    assert r.status_code == 403, r.text


# -----------------------------------------------------------------------------
# Test 6: Admin runs migration -> shape + fields promoted
# -----------------------------------------------------------------------------

def test_migrate_legacy_promotes_fields(mongo):
    # Legacy user (no pro_source)
    s, legacy_email, _ = _register()
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    _set_legacy(mongo, legacy_email, future)
    # Ensure pro_source is unset
    mongo.users.update_one({"email": legacy_email}, {"$unset": {"pro_source": ""}})

    # Canonical user (pro_until already set)
    s2, canonical_email, _ = _register()
    canonical_until = (datetime.now(timezone.utc) + timedelta(days=45)).isoformat()
    mongo.users.update_one(
        {"email": canonical_email},
        {"$set": {"pro_until": canonical_until, "plan": "pro", "pro_source": "promo:WELCOME30"}},
    )
    canonical_before = _get_user(mongo, canonical_email)

    # Admin call
    admin = _login_admin()
    r = admin.post(f"{BASE_URL}/api/admin/promo/migrate-legacy")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert "migrated" in body and isinstance(body["migrated"], int)
    assert "scanned" in body and isinstance(body["scanned"], int)
    assert body["migrated"] >= 1, f"expected >=1 migrated, got {body}"

    # Verify legacy user updated
    legacy_after = _get_user(mongo, legacy_email)
    assert legacy_after["pro_until"] == future
    assert legacy_after["plan"] == "pro"
    assert legacy_after["pro_source"] == "promo:legacy"

    # Verify canonical user untouched (byte-identical for these keys)
    canonical_after = _get_user(mongo, canonical_email)
    for k in ("pro_until", "plan", "pro_source"):
        assert canonical_after.get(k) == canonical_before.get(k), \
            f"canonical user field {k} changed: {canonical_before.get(k)} -> {canonical_after.get(k)}"


# -----------------------------------------------------------------------------
# Test 7: Idempotency — second run migrates 0
# -----------------------------------------------------------------------------

def test_migrate_legacy_idempotent(mongo):
    # Seed one fresh legacy user
    s, email, _ = _register()
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    _set_legacy(mongo, email, future)
    mongo.users.update_one({"email": email}, {"$unset": {"pro_source": ""}})

    admin = _login_admin()
    r1 = admin.post(f"{BASE_URL}/api/admin/promo/migrate-legacy")
    assert r1.status_code == 200
    first = r1.json()
    assert first["migrated"] >= 1

    # Second run — the just-migrated user now has pro_until, should be excluded
    r2 = admin.post(f"{BASE_URL}/api/admin/promo/migrate-legacy")
    assert r2.status_code == 200
    second = r2.json()
    assert second["migrated"] == 0, f"expected 0 on second run, got {second}"


# -----------------------------------------------------------------------------
# Test 8: pro_source preserved if already set (only writes 'promo:legacy' when empty)
# -----------------------------------------------------------------------------

def test_migrate_legacy_preserves_existing_pro_source(mongo):
    s, email, _ = _register()
    future = (datetime.now(timezone.utc) + timedelta(days=15)).isoformat()
    mongo.users.update_one(
        {"email": email},
        {"$set": {"pro_expires_at": future, "pro_source": "promo:TRYME"},
         "$unset": {"pro_until": ""}},
    )

    admin = _login_admin()
    r = admin.post(f"{BASE_URL}/api/admin/promo/migrate-legacy")
    assert r.status_code == 200

    after = _get_user(mongo, email)
    assert after["pro_until"] == future
    assert after["pro_source"] == "promo:TRYME", f"pro_source overwritten: {after['pro_source']}"


# -----------------------------------------------------------------------------
# Regression: fresh WELCOME30 comp redemption still writes canonical pro_until
# -----------------------------------------------------------------------------

def test_welcome30_redemption_writes_canonical(mongo):
    s, email, _ = _register()
    r = s.post(f"{BASE_URL}/api/promo/redeem", json={"code": "WELCOME30"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True
    assert j["unlocked"] == "pro_comp"
    assert j["duration_days"] == 30

    after = _get_user(mongo, email)
    assert after.get("pro_until"), "canonical pro_until must be set"
    assert after.get("plan") == "pro"
    assert after.get("pro_source") == "promo:WELCOME30"

    sub = s.get(f"{BASE_URL}/api/me/subscription").json()
    assert sub["pro"] is True
    assert sub["plan"] == "pro"
    assert sub["pro_source"] == "promo:WELCOME30"
