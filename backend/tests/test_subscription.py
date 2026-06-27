"""Backend tests for subscription/billing/account-dashboard flows."""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://frequency-healer-31.preview.emergentagent.com').rstrip('/')
API = f"{BASE_URL}/api"

ADMIN_EMAIL = os.environ.get('ADMIN_TEST_EMAIL', 'admin@example.com')
ADMIN_PASSWORD = os.environ.get('ADMIN_TEST_PASSWORD', 'admin123')


# ---------- helpers ----------
def _register(email, password="testpass123", name="Test"):
    r = requests.post(f"{API}/auth/register", json={"email": email, "password": password, "name": name})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _login(email, password):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


def _fresh_email(tag="sub"):
    return f"TEST_{tag}_{uuid.uuid4().hex[:8]}@example.com"


# ---------- /api/plan/config (public) ----------
def test_plan_config_public_no_auth():
    r = requests.get(f"{API}/plan/config")
    assert r.status_code == 200
    data = r.json()
    assert data["monthly"]["days"] == 30
    assert data["annual"]["days"] == 365
    assert data["currency"] == "usd"
    assert "price" in data["monthly"] and "price" in data["annual"]
    assert "trial_days" in data


# ---------- /api/me/subscription on new user ----------
def test_new_user_subscription_defaults():
    t = _register(_fresh_email("default"))
    r = requests.get(f"{API}/me/subscription", headers=_hdr(t))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["plan"] == "basic"
    assert data["pro"] is False
    assert data["pro_until"] in (None, "")
    assert data["days_left"] == 0
    assert data["trial_used"] is False
    assert data["is_admin"] is False
    # New subscription-mode fields (Feb 2026)
    assert data.get("stripe_subscription_status") in (None, "")
    assert data.get("in_trial") is False
    assert data.get("trial_end") in (None, "")
    assert data.get("cancel_at_period_end") is False
    assert data.get("has_billing_portal") is False
    assert data.get("payment_failed_at") in (None, "")


# ---------- /api/me/trial (DEPRECATED — now returns 410) ----------
def test_start_trial_returns_410_gone():
    """Feb 2026 migration: the no-card trial path is deprecated. /me/trial
    must return HTTP 410 Gone with a message pointing clients to /me/checkout."""
    t = _register(_fresh_email("trial410"))
    r = requests.post(f"{API}/me/trial", headers=_hdr(t))
    assert r.status_code == 410, r.text
    detail = r.json().get("detail", "")
    assert "payment method" in detail.lower()
    assert "/me/checkout" in detail or "checkout" in detail.lower()


# ---------- Session save cap for free users ----------
def test_session_cap_for_free_user():
    """Free tier capped at 3 saved sessions; trial-as-bypass is no longer
    available (deprecated). Pro upgrade now requires Stripe Checkout
    (covered in test_subscription_mode.py)."""
    t = _register(_fresh_email("cap"))
    # Create 3 sessions OK
    for i in range(3):
        r = requests.post(f"{API}/sessions", headers=_hdr(t), json={
            "name": f"TEST_cap_{i}", "frequency": 432.0, "waveform": "sine",
            "binaural": 0, "duration_minutes": 5,
        })
        assert r.status_code == 200, r.text
    # 4th -> 402
    r4 = requests.post(f"{API}/sessions", headers=_hdr(t), json={
        "name": "TEST_cap_4", "frequency": 528.0, "waveform": "sine",
        "binaural": 0, "duration_minutes": 5,
    })
    assert r4.status_code == 402, r4.text
    assert "upgrade" in r4.json().get("detail", "").lower() or "pro" in r4.json().get("detail", "").lower()


# ---------- Checkout ----------
def test_checkout_invalid_plan_400():
    t = _register(_fresh_email("ckinv"))
    r = requests.post(f"{API}/me/checkout", headers=_hdr(t), json={
        "plan": "lifetime", "origin_url": "https://x.test",
    })
    assert r.status_code == 400


# ---------- Password change ----------
def test_change_password_then_login_with_new():
    email = _fresh_email("pw")
    t = _register(email, password="oldpass123")
    # wrong current
    rw = requests.post(f"{API}/me/password", headers=_hdr(t), json={
        "current_password": "wrongone", "new_password": "newpass456",
    })
    assert rw.status_code == 400
    assert "current password" in rw.json().get("detail", "").lower()

    # correct
    r = requests.post(f"{API}/me/password", headers=_hdr(t), json={
        "current_password": "oldpass123", "new_password": "newpass456",
    })
    assert r.status_code == 200

    # old login fails
    rl = requests.post(f"{API}/auth/login", json={"email": email, "password": "oldpass123"})
    assert rl.status_code == 401

    # new login works
    rl2 = requests.post(f"{API}/auth/login", json={"email": email, "password": "newpass456"})
    assert rl2.status_code == 200


# ---------- Admin plan-prices ----------
def test_admin_prices_non_admin_403():
    t = _register(_fresh_email("npd"))
    rg = requests.get(f"{API}/admin/plan-prices", headers=_hdr(t))
    assert rg.status_code == 403
    rp = requests.put(f"{API}/admin/plan-prices", headers=_hdr(t), json={"monthly_price": 12.99})
    assert rp.status_code == 403


def test_admin_prices_update_persists_and_reflected_in_public_config():
    at = _login(ADMIN_EMAIL, ADMIN_PASSWORD)

    # capture original
    orig = requests.get(f"{API}/plan/config").json()

    try:
        new = {"monthly_price": 12.99, "annual_price": 79.0, "trial_days": 14}
        ru = requests.put(f"{API}/admin/plan-prices", headers=_hdr(at), json=new)
        assert ru.status_code == 200, ru.text

        pub = requests.get(f"{API}/plan/config").json()
        assert pub["monthly"]["price"] == 12.99
        assert pub["annual"]["price"] == 79.0
        assert pub["trial_days"] == 14
    finally:
        # restore
        restore = {
            "monthly_price": orig["monthly"]["price"],
            "annual_price": orig["annual"]["price"],
            "trial_days": orig["trial_days"],
        }
        requests.put(f"{API}/admin/plan-prices", headers=_hdr(at), json=restore)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
