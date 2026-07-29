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


def test_concurrent_redemption_only_one_succeeds(fresh_user):
    """Post-audit: two SIMULTANEOUS redeem calls should yield exactly one
    200 and one 400 — never two 200s. The atomic `$ne` guard in
    `promo_redeem` prevents the old check-then-act race from stacking
    extra Pro days on the user."""
    import concurrent.futures as _cf
    s, _, _ = fresh_user

    def _fire():
        return s.post(f"{BASE_URL}/api/promo/redeem", json={"code": "WELCOME30"})

    # Fire 5 in parallel to give the race the best possible chance of
    # slipping through.
    with _cf.ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(lambda _: _fire(), range(5)))

    codes = [r.status_code for r in results]
    successes = [c for c in codes if c == 200]
    rejects = [c for c in codes if c == 400]
    assert len(successes) == 1, f"expected exactly 1 success across concurrent taps, got {codes}"
    # Every other call must have been rejected as double-redeem.
    assert len(rejects) == 4, f"expected 4 rejections, got {codes}"

    # Sanity: user's Pro-until should reflect ONE 30-day grant, not two.
    r_sub = s.get(f"{BASE_URL}/api/me/subscription")
    assert r_sub.status_code == 200
    assert 28 <= r_sub.json()["days_left"] <= 31


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


# ---------------------------------------------------------------------------
# Cross-user max_uses cap race (Feb 2026 code-review fix)
# ---------------------------------------------------------------------------

def test_concurrent_cross_user_redemption_respects_cap(admin_session):
    """Two DIFFERENT users redeeming the same capped comp code
    concurrently must not both slip through when redemptions == cap - 1.

    Fix: the atomic promo update filter now includes
        `redemptions: {"$lt": max_uses}`
    so exactly one of the two update_ones modifies.

    Strategy:
    1. Admin creates a comp code with max_uses=1 (the tightest possible cap).
    2. Register 2 fresh users.
    3. Fire both /promo/redeem calls in parallel threads.
    4. Assert exactly 1 × 200 + 1 × 400 with detail mentioning the cap.
    """
    import concurrent.futures as _cf

    # Create a fresh capped comp code (endpoint: POST /api/admin/promo)
    code = f"CAPRACE{uuid.uuid4().hex[:6].upper()}"
    r_create = admin_session.post(
        f"{BASE_URL}/api/admin/promo",
        json={
            "code": code,
            "type": "comp",
            "duration_days": 30,
            "max_uses": 1,
            "active": True,
        },
    )
    assert r_create.status_code in (200, 201), r_create.text

    # Register two fresh users, one session each
    s1 = requests.Session()
    s2 = requests.Session()
    _register(s1)
    _register(s2)

    def _fire(sess):
        return sess.post(f"{BASE_URL}/api/promo/redeem", json={"code": code})

    with _cf.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(_fire, s1)
        f2 = ex.submit(_fire, s2)
        r1, r2 = f1.result(), f2.result()

    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [200, 400], (
        f"expected exactly one 200 + one 400 (cap enforced atomically), "
        f"got {codes}. Bodies: {r1.text} | {r2.text}"
    )
    # The 400 body should mention the cap, not double-redeem (both users
    # are distinct — the atomic filter's cap clause is what blocked us).
    loser_body = r1.text if r1.status_code == 400 else r2.text
    assert "limit" in loser_body.lower() or "cap" in loser_body.lower(), (
        f"loser detail should mention cap, got: {loser_body}"
    )

    # Cleanup: delete the code
    admin_session.delete(f"{BASE_URL}/api/admin/promo/{code}")
