"""Backend tests for promo redemption flow (iter41 bug fix).

Covers: /api/promo/validate, /api/promo/redeem for comp WELCOME30, negatives
(unknown / inactive / max-uses / double redemption), /api/me/subscription
reflecting pro=true + pro_source, additive stacking, and the free-tier
3-session cap being lifted after redemption.
"""
import os
import time
import uuid
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://frequency-healer-31.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "JuzlUWlMMOjHM0u#m5qv0ds!oYp8"


def _register(session: requests.Session, prefix="TEST_promo"):
    email = f"{prefix}_{uuid.uuid4().hex[:10]}@example.com"
    r = session.post(f"{BASE_URL}/api/auth/register", json={
        "email": email, "password": "TestPass123!", "name": "PromoTester",
    })
    assert r.status_code == 200, r.text
    return email, r.json()


def _login(session: requests.Session, email: str, password: str):
    r = session.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def fresh_user():
    s = requests.Session()
    email, data = _register(s)
    return s, email, data


@pytest.fixture
def admin_session():
    s = requests.Session()
    _login(s, ADMIN_EMAIL, ADMIN_PASSWORD)
    return s


# -----------------------------------------------------------------------------
# /promo/validate
# -----------------------------------------------------------------------------

def test_validate_welcome30(fresh_user):
    s, _, _ = fresh_user
    r = s.post(f"{BASE_URL}/api/promo/validate", json={"code": "WELCOME30"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["valid"] is True
    assert j["type"] == "comp"
    assert j["duration_days"] == 30
    assert "Complimentary" in j["summary"]


def test_validate_lowercase_ok(fresh_user):
    s, _, _ = fresh_user
    r = s.post(f"{BASE_URL}/api/promo/validate", json={"code": "welcome30"})
    assert r.status_code == 200
    assert r.json()["valid"] is True


def test_validate_unknown(fresh_user):
    s, _, _ = fresh_user
    r = s.post(f"{BASE_URL}/api/promo/validate", json={"code": "BOGUSXYZ"})
    assert r.status_code == 200
    j = r.json()
    assert j["valid"] is False
    assert j.get("reason")


# -----------------------------------------------------------------------------
# /promo/redeem happy path
# -----------------------------------------------------------------------------

def test_redeem_welcome30_grants_pro(fresh_user):
    s, email, _ = fresh_user
    # Before: baseline free
    r0 = s.get(f"{BASE_URL}/api/me/subscription")
    assert r0.status_code == 200
    j0 = r0.json()
    assert j0["pro"] is False
    assert j0.get("is_promo_pro") in (False, None)

    # Redeem
    r = s.post(f"{BASE_URL}/api/promo/redeem", json={"code": "WELCOME30"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["unlocked"] == "pro_comp"
    assert body["duration_days"] == 30
    assert "pro_until" in body

    # After: /me/subscription reflects pro
    r2 = s.get(f"{BASE_URL}/api/me/subscription")
    assert r2.status_code == 200
    j = r2.json()
    assert j["pro"] is True, j
    assert j["plan"] == "pro"
    assert j["pro_source"] == "promo:WELCOME30"
    assert j["is_promo_pro"] is True
    # days_left should be ~30 (allow 28..31)
    assert 28 <= j["days_left"] <= 31, j


def test_double_redemption_blocked(fresh_user):
    s, _, _ = fresh_user
    r1 = s.post(f"{BASE_URL}/api/promo/redeem", json={"code": "WELCOME30"})
    assert r1.status_code == 200
    r2 = s.post(f"{BASE_URL}/api/promo/redeem", json={"code": "WELCOME30"})
    assert r2.status_code == 400
    assert "already redeemed" in r2.json().get("detail", "").lower()


def test_free_session_cap_lifted_after_redemption(fresh_user):
    s, _, _ = fresh_user
    # First save 3 as free — 4th should 402
    for i in range(3):
        r = s.post(f"{BASE_URL}/api/sessions", json={
            "name": f"TEST_sess_{i}", "frequency": 432.0, "waveform": "sine",
            "binaural": 0, "duration_minutes": 5, "ambient": {},
        })
        assert r.status_code == 200, r.text
    r4 = s.post(f"{BASE_URL}/api/sessions", json={
        "name": "TEST_sess_4", "frequency": 432.0, "waveform": "sine",
        "binaural": 0, "duration_minutes": 5, "ambient": {},
    })
    assert r4.status_code == 402, r4.text

    # Redeem and try again — should succeed
    r = s.post(f"{BASE_URL}/api/promo/redeem", json={"code": "WELCOME30"})
    assert r.status_code == 200
    for i in range(4, 7):
        r = s.post(f"{BASE_URL}/api/sessions", json={
            "name": f"TEST_sess_{i}", "frequency": 432.0, "waveform": "sine",
            "binaural": 0, "duration_minutes": 5, "ambient": {},
        })
        assert r.status_code == 200, f"After redeem, 4th+ save must succeed, got {r.status_code}: {r.text}"


# -----------------------------------------------------------------------------
# Negative paths (via admin-created ephemeral codes)
# -----------------------------------------------------------------------------

def test_redeem_unknown_code(fresh_user):
    s, _, _ = fresh_user
    r = s.post(f"{BASE_URL}/api/promo/redeem", json={"code": "DOESNOTEXIST_XYZ"})
    assert r.status_code == 400
    assert r.json().get("detail")


def test_redeem_inactive_code(fresh_user, admin_session):
    code = f"TEST_INACTIVE_{uuid.uuid4().hex[:6].upper()}"
    r = admin_session.post(f"{BASE_URL}/api/admin/promo", json={
        "code": code, "type": "comp", "active": False,
        "duration_days": 7, "max_uses": 5,
    })
    assert r.status_code == 200, r.text
    try:
        s, _, _ = fresh_user
        r2 = s.post(f"{BASE_URL}/api/promo/redeem", json={"code": code})
        assert r2.status_code == 400
        assert "deactivated" in r2.json().get("detail", "").lower()
        # validate should also say invalid
        r3 = s.post(f"{BASE_URL}/api/promo/validate", json={"code": code})
        j = r3.json()
        assert j["valid"] is False
        assert "deactivated" in j["reason"].lower()
    finally:
        admin_session.delete(f"{BASE_URL}/api/admin/promo/{code}")


def test_redeem_max_uses_hit(admin_session):
    code = f"TEST_MAXUSES_{uuid.uuid4().hex[:6].upper()}"
    r = admin_session.post(f"{BASE_URL}/api/admin/promo", json={
        "code": code, "type": "comp", "active": True,
        "duration_days": 3, "max_uses": 1,
    })
    assert r.status_code == 200, r.text
    try:
        # User A redeems successfully
        sa = requests.Session(); _register(sa)
        r1 = sa.post(f"{BASE_URL}/api/promo/redeem", json={"code": code})
        assert r1.status_code == 200

        # User B hits the limit
        sb = requests.Session(); _register(sb)
        r2 = sb.post(f"{BASE_URL}/api/promo/redeem", json={"code": code})
        assert r2.status_code == 400
        assert "limit" in r2.json().get("detail", "").lower()

        # validate should also flag it
        r3 = sb.post(f"{BASE_URL}/api/promo/validate", json={"code": code})
        assert r3.json()["valid"] is False
    finally:
        admin_session.delete(f"{BASE_URL}/api/admin/promo/{code}")


def test_discount_code_not_redeemable_here(fresh_user):
    s, _, _ = fresh_user
    r = s.post(f"{BASE_URL}/api/promo/redeem", json={"code": "SAVE20"})
    assert r.status_code == 400
    # Detail should indicate checkout-only
    assert "checkout" in r.json().get("detail", "").lower()


# -----------------------------------------------------------------------------
# Additive stacking
# -----------------------------------------------------------------------------

def test_comp_stacks_additively(fresh_user, admin_session):
    """Redeem WELCOME30 (30d) then a fresh 10d comp — pro_until should be ~40d out."""
    stack_code = f"TEST_STACK_{uuid.uuid4().hex[:6].upper()}"
    r = admin_session.post(f"{BASE_URL}/api/admin/promo", json={
        "code": stack_code, "type": "comp", "active": True,
        "duration_days": 10, "max_uses": 5,
    })
    assert r.status_code == 200
    try:
        s, _, _ = fresh_user
        r1 = s.post(f"{BASE_URL}/api/promo/redeem", json={"code": "WELCOME30"})
        assert r1.status_code == 200
        j1 = s.get(f"{BASE_URL}/api/me/subscription").json()
        first_days = j1["days_left"]
        assert 28 <= first_days <= 31

        r2 = s.post(f"{BASE_URL}/api/promo/redeem", json={"code": stack_code})
        assert r2.status_code == 200
        j2 = s.get(f"{BASE_URL}/api/me/subscription").json()
        # Days should have extended by ~10, not been overwritten
        assert j2["days_left"] >= first_days + 8, (first_days, j2)
        assert j2["pro"] is True
        # pro_source updates to latest
        assert j2["pro_source"] == f"promo:{stack_code}"
    finally:
        admin_session.delete(f"{BASE_URL}/api/admin/promo/{stack_code}")


# -----------------------------------------------------------------------------
# Regression: existing free user (no redemption) stays free; admin stays pro
# -----------------------------------------------------------------------------

def test_regression_free_user_stays_free(fresh_user):
    s, _, _ = fresh_user
    j = s.get(f"{BASE_URL}/api/me/subscription").json()
    assert j["pro"] is False
    assert j.get("is_promo_pro") in (False, None)
    assert not j.get("pro_source")


def test_regression_admin_pro_no_promo_source(admin_session):
    j = admin_session.get(f"{BASE_URL}/api/me/subscription").json()
    assert j["pro"] is True
    # Admin should NOT have a promo pro_source
    assert not j.get("is_promo_pro")
    # pro_source may be None/absent for admin
    assert not (j.get("pro_source") or "").startswith("promo:")


# -----------------------------------------------------------------------------
# Admin: redemption count increments
# -----------------------------------------------------------------------------

def test_admin_redemption_count_increments(admin_session):
    r0 = admin_session.get(f"{BASE_URL}/api/admin/promo")
    assert r0.status_code == 200
    lst = r0.json()
    welcome = next((c for c in lst if c["code"] == "WELCOME30"), None)
    assert welcome, "WELCOME30 must exist"
    before = welcome.get("redemptions", 0)

    s = requests.Session(); _register(s)
    r = s.post(f"{BASE_URL}/api/promo/redeem", json={"code": "WELCOME30"})
    assert r.status_code == 200

    r1 = admin_session.get(f"{BASE_URL}/api/admin/promo")
    welcome1 = next(c for c in r1.json() if c["code"] == "WELCOME30")
    assert welcome1["redemptions"] == before + 1
