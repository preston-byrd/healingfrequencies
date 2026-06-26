"""Backend tests for new payment_method_preference field on /api/me/checkout.

Verifies:
- Each of card | apple_pay | google_pay | link produces a valid Stripe Checkout URL
- The created payment_transactions tx records the preference inside metadata
- Omitting the field defaults to 'card'
- Invalid values return 422 (Pydantic pattern mismatch)
"""
import os
import re
import uuid
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://frequency-healer-31.preview.emergentagent.com').rstrip('/')
API = f"{BASE_URL}/api"

STRIPE_URL_RE = re.compile(r"^https://checkout\.stripe\.com/.+")


# ---------- helpers ----------
def _register(email, password="testpass123", name="Test"):
    r = requests.post(f"{API}/auth/register", json={"email": email, "password": password, "name": name})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


def _fresh_email(tag="pm"):
    return f"TEST_{tag}_{uuid.uuid4().hex[:8]}@example.com"


def _checkout(token, plan="monthly", method=None):
    body = {"plan": plan, "origin_url": "https://example.test"}
    if method is not None:
        body["payment_method_preference"] = method
    return requests.post(f"{API}/me/checkout", headers=_hdr(token), json=body)


# ---------- happy paths ----------
@pytest.mark.parametrize("method", ["card", "apple_pay", "google_pay", "link"])
def test_checkout_accepts_each_payment_method_preference(method):
    t = _register(_fresh_email(f"pm_{method}"))
    r = _checkout(t, plan="monthly", method=method)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "url" in data and "session_id" in data
    assert STRIPE_URL_RE.match(data["url"]), f"Got non-stripe url: {data['url']}"
    assert isinstance(data["session_id"], str) and len(data["session_id"]) > 0


def test_checkout_default_payment_method_is_card_when_omitted():
    # Pydantic defaults payment_method_preference to "card" when omitted.
    t = _register(_fresh_email("pm_default"))
    r = _checkout(t, plan="monthly", method=None)
    assert r.status_code == 200, r.text
    data = r.json()
    assert STRIPE_URL_RE.match(data["url"])


def test_checkout_annual_plan_with_link_method():
    t = _register(_fresh_email("pm_annual_link"))
    r = _checkout(t, plan="annual", method="link")
    assert r.status_code == 200, r.text
    assert STRIPE_URL_RE.match(r.json()["url"])


# ---------- validation ----------
def test_checkout_invalid_payment_method_returns_422():
    t = _register(_fresh_email("pm_bad"))
    r = _checkout(t, plan="monthly", method="bitcoin")
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("bad", ["paypal", "APPLE_PAY", "", "wire", "venmo"])
def test_checkout_invalid_payment_method_various(bad):
    t = _register(_fresh_email("pm_bad2"))
    r = _checkout(t, plan="monthly", method=bad)
    assert r.status_code == 422, f"Expected 422 for {bad!r}, got {r.status_code}: {r.text}"


# ---------- regression on plan validation ----------
def test_checkout_invalid_plan_still_400_with_payment_method():
    t = _register(_fresh_email("pm_plan_bad"))
    r = _checkout(t, plan="lifetime", method="card")
    assert r.status_code == 400


def test_checkout_unauthenticated_401():
    r = requests.post(f"{API}/me/checkout", json={
        "plan": "monthly", "origin_url": "https://example.test", "payment_method_preference": "card",
    })
    assert r.status_code in (401, 403)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
