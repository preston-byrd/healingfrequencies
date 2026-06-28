"""Iter-27 regression suite.

Verifies the refactored `admin_sound_lineage` endpoint still returns identical
shape + surrounding admin/auth/audit/Stripe-webhook/Resend/CORS surfaces.
"""
from __future__ import annotations

import os
import secrets
import time
import uuid
import requests
import pytest

from _creds import ADMIN_EMAIL, ADMIN_PASSWORD

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://frequency-healer-31.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"


# ---------- fixtures ----------
@pytest.fixture(scope="module")
def admin_token() -> str:
    r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=20)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok, f"no token in login resp: {r.json()}"
    return tok


@pytest.fixture
def admin_headers(admin_token) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


# ---------- auth ----------
class TestAuth:
    def test_login_new_password_ok(self):
        r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=20)
        assert r.status_code == 200
        body = r.json()
        tok = body.get("token") or body.get("access_token")
        assert tok and isinstance(tok, str) and len(tok) > 20

    def test_login_old_password_rejected(self):
        r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": "admin123"}, timeout=20)
        assert r.status_code == 401

    def test_register_short_password_422(self):
        r = requests.post(
            f"{API}/auth/register",
            json={"email": f"TEST_{uuid.uuid4().hex[:8]}@example.com", "password": "abc", "name": "Short"},
            timeout=20,
        )
        assert r.status_code == 422, f"got {r.status_code} {r.text}"

    def test_register_strong_ok_resend_softfail(self):
        email = f"TEST_{uuid.uuid4().hex[:10]}@example.com"
        r = requests.post(
            f"{API}/auth/register",
            json={"email": email, "password": "StrongPass!234", "name": "T"},
            timeout=30,
        )
        assert r.status_code == 200, f"got {r.status_code} {r.text}"
        body = r.json()
        tok = body.get("token") or body.get("access_token")
        assert tok, f"no token: {body}"

    def test_logout_revokes_token(self):
        email = f"TEST_{uuid.uuid4().hex[:10]}@example.com"
        rr = requests.post(
            f"{API}/auth/register",
            json={"email": email, "password": "StrongPass!234", "name": "T"},
            timeout=20,
        )
        assert rr.status_code == 200
        tok = rr.json().get("token") or rr.json().get("access_token")
        h = {"Authorization": f"Bearer {tok}"}
        # me works
        assert requests.get(f"{API}/auth/me", headers=h, timeout=10).status_code == 200
        # logout
        lo = requests.post(f"{API}/auth/logout", headers=h, timeout=10)
        assert lo.status_code in (200, 204)
        # me fails
        after = requests.get(f"{API}/auth/me", headers=h, timeout=10)
        assert after.status_code == 401


class TestLoginThrottle:
    def test_throttle_after_8_bad(self):
        """8 bad attempts from same IP should yield a 429 by the 9th."""
        bad_email = f"TEST_throttle_{uuid.uuid4().hex[:6]}@example.com"
        statuses = []
        for _ in range(12):
            r = requests.post(f"{API}/auth/login", json={"email": bad_email, "password": "wrongwrongwrong"}, timeout=10)
            statuses.append(r.status_code)
            if r.status_code == 429:
                break
        assert 429 in statuses, f"never throttled; statuses={statuses}"


# ---------- admin security + audit ----------
class TestAdminSecurity:
    def test_security_requires_admin(self):
        r = requests.get(f"{API}/admin/security", timeout=10)
        assert r.status_code in (401, 403)

    def test_security_with_admin(self, admin_headers):
        r = requests.get(f"{API}/admin/security", headers=admin_headers, timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        for k in ("metrics", "recent_events", "new_users_24h"):
            assert k in body, f"missing {k} in {list(body.keys())}"
        metrics = body["metrics"]
        for m in (
            "failed_logins",
            "successful_logins",
            "registrations",
            "ai_throttle_hits",
            "webhook_signature_rejections",
            "session_revocations",
            "throttle_hits",
        ):
            assert m in metrics, f"missing metric {m}"
            for win in ("last_hour", "last_24h", "last_7d"):
                assert win in metrics[m], f"{m} missing window {win}"
                assert isinstance(metrics[m][win], (int, float)), f"{m}.{win} not numeric"

    def test_audit_log_event_filter(self, admin_headers):
        r = requests.get(f"{API}/admin/audit-log", headers=admin_headers, params={"event": "auth.", "limit": 5}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        items = body.get("items") if isinstance(body, dict) else body
        assert isinstance(items, list)
        for row in items:
            assert row.get("event", "").startswith("auth."), f"bad event: {row.get('event')}"


# ---------- sound lineage (the refactor target) ----------
class TestSoundLineageRefactor:
    REQUIRED_TOTALS = {"signups", "checkouts_started", "billing_fulfilled", "admin_grants", "peak_dau"}
    REQUIRED_TOP = {"window_days", "start", "end", "series", "annotations", "totals"}
    SERIES_KEYS = {"date", "daily_active", "signups", "checkouts_started", "billing_fulfilled", "admin_grants"}

    def _assert_shape(self, body: dict, expected_days: int):
        assert self.REQUIRED_TOP.issubset(body.keys()), f"missing: {self.REQUIRED_TOP - set(body)}"
        assert body["window_days"] == expected_days
        assert isinstance(body["series"], list) and len(body["series"]) == expected_days
        for row in body["series"]:
            assert self.SERIES_KEYS.issubset(row.keys()), f"series row missing keys: {self.SERIES_KEYS - set(row)}"
            assert isinstance(row["daily_active"], int)
        assert isinstance(body["annotations"], list)
        assert self.REQUIRED_TOTALS.issubset(body["totals"].keys()), f"totals missing: {self.REQUIRED_TOTALS - set(body['totals'])}"

    def test_days_30(self, admin_headers):
        r = requests.get(f"{API}/admin/sound-lineage", headers=admin_headers, params={"days": 30}, timeout=30)
        assert r.status_code == 200, r.text
        self._assert_shape(r.json(), 30)

    def test_days_7(self, admin_headers):
        r = requests.get(f"{API}/admin/sound-lineage", headers=admin_headers, params={"days": 7}, timeout=30)
        assert r.status_code == 200, r.text
        self._assert_shape(r.json(), 7)

    def test_days_999_clamps_to_365(self, admin_headers):
        r = requests.get(f"{API}/admin/sound-lineage", headers=admin_headers, params={"days": 999}, timeout=60)
        assert r.status_code == 200, r.text
        self._assert_shape(r.json(), 365)

    def test_days_1_clamps_to_7(self, admin_headers):
        r = requests.get(f"{API}/admin/sound-lineage", headers=admin_headers, params={"days": 1}, timeout=30)
        assert r.status_code == 200, r.text
        self._assert_shape(r.json(), 7)

    def test_lineage_requires_admin(self):
        r = requests.get(f"{API}/admin/sound-lineage", params={"days": 7}, timeout=15)
        assert r.status_code in (401, 403)


# ---------- AI recommend ----------
class TestAIRecommend:
    SHAPE = {"frequency", "name", "description", "waveform", "binaural", "isochronic", "golden_stack", "ambient", "duration_min"}

    def test_strict_shape(self, admin_headers):
        r = requests.post(f"{API}/me/ai-recommend", headers=admin_headers, json={"intent": "deep calm before sleep"}, timeout=60)
        assert r.status_code == 200, r.text
        body = r.json()
        assert self.SHAPE.issubset(body.keys()), f"missing: {self.SHAPE - set(body)}"
        assert isinstance(body["frequency"], (int, float))
        assert isinstance(body["binaural"], (int, float))
        assert isinstance(body["isochronic"], (int, float))
        assert isinstance(body["duration_min"], (int, float))

    def test_throttle_429(self, admin_headers):
        """6 reqs/user/~2 min — should 429 by ~7th."""
        codes = []
        for _ in range(10):
            r = requests.post(f"{API}/me/ai-recommend", headers=admin_headers, json={"intent": f"unique intent {_}"}, timeout=30)
            codes.append(r.status_code)
            if r.status_code == 429:
                break
        assert 429 in codes, f"never throttled: {codes}"


# ---------- Stripe webhook ----------
class TestStripeWebhook:
    def test_missing_signature(self):
        r = requests.post(f"{API}/webhook/stripe", data=b"{}", timeout=10)
        assert r.status_code == 400
        assert "signature" in r.text.lower() or "configured" in r.text.lower()

    def test_invalid_signature_or_not_configured(self):
        r = requests.post(
            f"{API}/webhook/stripe",
            data=b'{"id":"evt_test"}',
            headers={"Stripe-Signature": "t=1,v1=deadbeef"},
            timeout=10,
        )
        assert r.status_code == 400
        msg = r.text.lower()
        assert ("invalid" in msg) or ("not configured" in msg) or ("signature" in msg)


# ---------- CORS ----------
class TestCORS:
    def test_allowed_origin(self):
        r = requests.options(
            f"{API}/auth/login",
            headers={
                "Origin": "https://solarisound.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
            timeout=10,
        )
        # Either 200 or 204 — but allow-origin header must be set & echo origin
        aco = r.headers.get("access-control-allow-origin", "")
        assert "solarisound.com" in aco or aco == "*", f"expected origin allowed, got '{aco}' status={r.status_code}"

    def test_disallowed_origin(self):
        r = requests.options(
            f"{API}/auth/login",
            headers={
                "Origin": "https://evil.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
            timeout=10,
        )
        aco = r.headers.get("access-control-allow-origin", "")
        assert "evil.com" not in aco, f"evil.com should not be reflected: '{aco}'"
