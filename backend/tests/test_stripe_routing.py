"""Regression tests for the new _stripe_client(webhook_url) helper in server.py.

The helper exists to defend against a sticky-state bug: emergentintegrations'
StripeCheckout wrapper sets `stripe.api_base` to Emergent's proxy when the
API key contains 'sk_test_emergent' but NEVER resets it. If a process ever
served an sk_test_emergent request, ALL subsequent sk_live_ requests would
keep routing through the Emergent proxy, producing checkout URLs that load
BLANK in the user's real Stripe account.

These tests verify:
1) For sk_test_emergent keys, helper sets api_base to the Emergent proxy.
2) For sk_live_/any-other keys, helper resets api_base to https://api.stripe.com
   even if a previous sk_test_emergent call had set it to the proxy.
3) Live HTTP regression: POST /api/me/checkout still returns 200 with a real
   https://checkout.stripe.com URL (current env uses sk_test_emergent).
4) GET /api/payments/status/{sid} returns the expected shape.
5) POST /api/webhook/stripe is reachable and acknowledges with {received: ...}.
6) Backend logs the new [checkout] diagnostic line including api_base.
"""
import os
import re
import sys
import time
import uuid
import importlib
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://frequency-healer-31.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"
STRIPE_URL_RE = re.compile(r"^https://checkout\.stripe\.com/.+")

# Ensure we can import the backend server module to access _stripe_client
sys.path.insert(0, "/app/backend")


# ---------- helpers ----------
def _register(email, password="testpass123", name="Test"):
    r = requests.post(f"{API}/auth/register", json={"email": email, "password": password, "name": name})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


def _fresh_email(tag="stripe"):
    return f"TEST_{tag}_{uuid.uuid4().hex[:8]}@example.com"


# =============================================================================
# Unit tests for _stripe_client — these directly verify the sticky-state fix
# =============================================================================
class TestStripeClientHelperRouting:
    """Direct unit tests on the helper proving stripe.api_base normalisation."""

    def test_helper_sets_emergent_proxy_for_sk_test_emergent(self, monkeypatch):
        # Current env should already be sk_test_emergent — confirm and exercise
        import stripe as _stripe
        import server as srv

        assert "sk_test_emergent" in srv.STRIPE_API_KEY, "Preview env must use sk_test_emergent for this test"

        # Pre-pollute api_base with the WRONG value
        monkeypatch.setattr(_stripe, "api_base", "https://api.stripe.com", raising=False)
        srv._stripe_client("https://example.test/api/webhook/stripe")
        assert _stripe.api_base == "https://integrations.emergentagent.com/stripe", (
            f"Helper failed to set Emergent proxy for sk_test_emergent key; got {_stripe.api_base}"
        )

    def test_helper_recovers_from_sticky_emergent_proxy_when_key_is_live(self, monkeypatch):
        """Critical sticky-state recovery test.

        Simulate: a previous sk_test_emergent call left stripe.api_base pointing
        to Emergent's proxy. Now the operator rotates STRIPE_API_KEY to a real
        sk_live_ key. Without our helper, api_base would remain stuck on the
        Emergent proxy. With our helper, it MUST reset to https://api.stripe.com.
        """
        import stripe as _stripe
        import server as srv

        # 1) Simulate the sticky state from a prior sk_test_emergent call
        monkeypatch.setattr(_stripe, "api_base", "https://integrations.emergentagent.com/stripe", raising=False)
        assert _stripe.api_base == "https://integrations.emergentagent.com/stripe"

        # 2) Operator rotates to a real live key (we use a clearly fake/dummy
        #    string so we never accidentally hit the real Stripe API; the
        #    helper only INSPECTS the key string when deciding api_base,
        #    so no network call is made).
        monkeypatch.setattr(srv, "STRIPE_API_KEY", "sk_live_dummy_fake_not_real_0000000000", raising=True)

        # 3) Helper must reset api_base back to the real Stripe endpoint
        srv._stripe_client("https://example.test/api/webhook/stripe")
        assert _stripe.api_base == "https://api.stripe.com", (
            f"Helper failed to reset api_base from sticky Emergent proxy to api.stripe.com; got {_stripe.api_base}"
        )

    def test_helper_resets_even_if_pre_corrupted_to_arbitrary_value(self, monkeypatch):
        import stripe as _stripe
        import server as srv

        monkeypatch.setattr(_stripe, "api_base", "https://garbage.example.com", raising=False)
        # Live-ish key path
        monkeypatch.setattr(srv, "STRIPE_API_KEY", "sk_live_xxxx", raising=True)
        srv._stripe_client("https://example.test/api/webhook/stripe")
        assert _stripe.api_base == "https://api.stripe.com"

    def test_helper_handles_test_key_other_than_emergent(self, monkeypatch):
        """Non-emergent test keys (e.g. plain sk_test_) should ALSO route to real Stripe."""
        import stripe as _stripe
        import server as srv

        monkeypatch.setattr(_stripe, "api_base", "https://integrations.emergentagent.com/stripe", raising=False)
        monkeypatch.setattr(srv, "STRIPE_API_KEY", "sk_test_abc123_realstripe", raising=True)
        srv._stripe_client("https://example.test/api/webhook/stripe")
        assert _stripe.api_base == "https://api.stripe.com"


# =============================================================================
# Live HTTP regression — preview env runs sk_test_emergent
# =============================================================================
class TestCheckoutLiveRegression:
    """Confirms the helper change didn't break the existing /api/me/checkout happy path."""

    @pytest.fixture(scope="class")
    def token(self):
        return _register(_fresh_email("ckhelper"))

    def test_checkout_returns_real_stripe_url(self, token):
        body = {"plan": "monthly", "origin_url": "https://example.test"}
        r = requests.post(f"{API}/me/checkout", headers=_hdr(token), json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "url" in data and "session_id" in data, data
        assert STRIPE_URL_RE.match(data["url"]), f"Bad URL: {data['url']}"

    def test_checkout_with_each_method_still_works(self, token):
        for method in ("card", "apple_pay", "google_pay", "link"):
            body = {"plan": "monthly", "origin_url": "https://example.test", "payment_method_preference": method}
            r = requests.post(f"{API}/me/checkout", headers=_hdr(token), json=body)
            assert r.status_code == 200, f"{method}: {r.text}"
            assert STRIPE_URL_RE.match(r.json()["url"]), method

    def test_payment_status_shape(self, token):
        # Create then poll status
        body = {"plan": "annual", "origin_url": "https://example.test"}
        r = requests.post(f"{API}/me/checkout", headers=_hdr(token), json=body)
        assert r.status_code == 200, r.text
        sid = r.json()["session_id"]

        s = requests.get(f"{API}/payments/status/{sid}", headers=_hdr(token))
        assert s.status_code == 200, s.text
        d = s.json()
        for k in ("session_id", "status", "payment_status", "amount_total", "currency", "fulfilled", "plan"):
            assert k in d, f"missing {k} in {d}"
        assert d["session_id"] == sid
        assert d["plan"] == "annual"
        assert d["fulfilled"] is False
        assert d["payment_status"] in ("pending", "unpaid", None) or isinstance(d["payment_status"], str)

    def test_webhook_endpoint_is_reachable(self):
        # No signature => library will raise inside try/except and return {received: True}
        r = requests.post(f"{API}/webhook/stripe", data=b"{}", headers={"Content-Type": "application/json"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "received" in body, body
        # Without STRIPE_API_KEY missing this should be True; with valid key but bad sig also True.
        assert body["received"] in (True, False)


# =============================================================================
# Backend log diagnostic verification
# =============================================================================
class TestCheckoutDiagnosticLog:
    def test_checkout_emits_api_base_log_line(self):
        token = _register(_fresh_email("cklog"))

        # Issue a checkout call so the [checkout] log line is produced
        body = {"plan": "monthly", "origin_url": "https://example.test"}
        r = requests.post(f"{API}/me/checkout", headers=_hdr(token), json=body)
        assert r.status_code == 200, r.text

        # Give supervisor a moment to flush logs
        time.sleep(1.0)

        log_paths = [
            "/var/log/supervisor/backend.err.log",
            "/var/log/supervisor/backend.out.log",
        ]
        found_line = None
        for p in log_paths:
            try:
                with open(p, "r", errors="ignore") as fh:
                    # Read tail (last ~200KB) to keep it cheap
                    fh.seek(0, 2)
                    size = fh.tell()
                    fh.seek(max(0, size - 200_000))
                    tail = fh.read()
            except FileNotFoundError:
                continue
            for line in reversed(tail.splitlines()):
                if "[checkout]" in line and "api_base=" in line:
                    found_line = line
                    break
            if found_line:
                break

        assert found_line, "Did not find any [checkout] ... api_base= line in backend logs"
        # For sk_test_emergent key, api_base must be the Emergent proxy
        assert "api_base=https://integrations.emergentagent.com/stripe" in found_line, (
            f"Unexpected api_base in log line: {found_line}"
        )
        assert "key_prefix=sk_test_emer" in found_line or "key_prefix=sk_test_em" in found_line, (
            f"Expected key_prefix in log line: {found_line}"
        )
