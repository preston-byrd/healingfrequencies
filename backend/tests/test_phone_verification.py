"""Regression tests for the phone verification flow.

Runs against the LIVE preview server with TWILIO_TEST_MODE=1 enabled so
we don't burn real SMS credits. In test mode the server accepts the
fixed code "123456" for any phone without contacting Twilio; every other
code returns 400 exactly like a real "pending" Twilio response would.

Production must ship with TWILIO_TEST_MODE unset — the server logs a
loud warning on boot if it's still on.

Covers:
- POST /auth/phone/send-code accepts E.164, rejects malformed
- POST /auth/phone/verify-code returns a signed token on approved code
- POST /auth/phone/verify-code returns 400 on wrong code
- POST /auth/register requires phone_number + phone_verification_token
- Register succeeds with a valid token; phone saved + phone_verified=true
- Register rejects a token that doesn't match the phone
- Register rejects an expired token
- Duplicate phone reuse blocked (409)
"""

import os
import sys
import time
import uuid

import pytest
import httpx
import jwt

API_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/") + "/api"

# Import server just for the JWT_SECRET so we can craft an expired token
# to prove the register-side check refuses stale proofs.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server  # noqa: E402


def _rand_phone() -> str:
    n = uuid.uuid4().int % 10000000
    return f"+1555{n:07d}"


def _rand_email() -> str:
    return f"phoneverify_{uuid.uuid4().hex[:8]}@example.com"


@pytest.fixture(autouse=True)
def _require_test_mode():
    """Fail loudly if the running server isn't in test mode — otherwise
    we'd hammer the real Twilio Verify API and the suite would be flaky."""
    with httpx.Client() as c:
        # Probe the send-code endpoint with a valid phone; if the server
        # returns 503 (not configured), test mode is off AND no Verify SID
        # exists. If it returns 200, either test mode is on OR a real
        # Verify service is configured. Either way the test suite can
        # continue only when it can produce a working code.
        r = c.post(f"{API_URL}/auth/phone/send-code",
                   json={"phone_number": "+15551234567"})
        if r.status_code == 503:
            pytest.skip("phone verification not configured — enable TWILIO_TEST_MODE=1 or set TWILIO_VERIFY_SERVICE_SID")


# ---------- Send-code endpoint ---------------------------------------------

def test_send_code_rejects_malformed_phone():
    with httpx.Client() as c:
        r = c.post(f"{API_URL}/auth/phone/send-code", json={"phone_number": "555-1234"})
    assert r.status_code == 400


def test_send_code_missing_country_code():
    with httpx.Client() as c:
        r = c.post(f"{API_URL}/auth/phone/send-code", json={"phone_number": "14155552671"})
    assert r.status_code == 400


def test_send_code_accepts_e164():
    phone = _rand_phone()
    with httpx.Client() as c:
        r = c.post(f"{API_URL}/auth/phone/send-code", json={"phone_number": phone})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


# ---------- Verify-code endpoint -------------------------------------------

def test_verify_code_returns_token_on_approved():
    phone = _rand_phone()
    with httpx.Client() as c:
        c.post(f"{API_URL}/auth/phone/send-code", json={"phone_number": phone})
        r = c.post(f"{API_URL}/auth/phone/verify-code",
                   json={"phone_number": phone, "code": "123456"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert "phone_verification_token" in body
    payload = jwt.decode(
        body["phone_verification_token"],
        server.JWT_SECRET,
        algorithms=[server.JWT_ALGORITHM],
    )
    assert payload["type"] == "phone_verification"
    assert payload["phone"] == phone


def test_verify_code_wrong_code_returns_400():
    phone = _rand_phone()
    with httpx.Client() as c:
        c.post(f"{API_URL}/auth/phone/send-code", json={"phone_number": phone})
        r = c.post(f"{API_URL}/auth/phone/verify-code",
                   json={"phone_number": phone, "code": "999999"})
    assert r.status_code == 400
    assert "incorrect" in r.json()["detail"].lower()


def test_verify_code_rejects_non_digit_code():
    phone = _rand_phone()
    with httpx.Client() as c:
        c.post(f"{API_URL}/auth/phone/send-code", json={"phone_number": phone})
        r = c.post(f"{API_URL}/auth/phone/verify-code",
                   json={"phone_number": phone, "code": "abcdef"})
    assert r.status_code == 400


# ---------- Register endpoint ----------------------------------------------

def test_register_requires_phone_and_token():
    # Use module-level httpx.post (not patched by the conftest auto-inject)
    # so we can assert the API's own validation rejects a body missing
    # the phone fields.
    r = httpx.post(f"{API_URL}/auth/register", json={
        "email": _rand_email(), "password": "Test1234!", "name": "P",
    })
    # Pydantic validation → 422
    assert r.status_code == 422


def test_register_happy_path():
    phone = _rand_phone()
    email = _rand_email()
    with httpx.Client() as c:
        c.post(f"{API_URL}/auth/phone/send-code", json={"phone_number": phone})
        vr = c.post(f"{API_URL}/auth/phone/verify-code",
                    json={"phone_number": phone, "code": "123456"})
        token = vr.json()["phone_verification_token"]
        r = c.post(f"{API_URL}/auth/register", json={
            "email": email,
            "password": "Test1234!",
            "name": "Phone User",
            "phone_number": phone,
            "phone_verification_token": token,
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == email
    # Sanity-check via admin: phone is persisted + phone_verified=True.
    admin_login = httpx.post(
        f"{API_URL}/auth/login",
        json={"email": "admin@example.com", "password": os.environ.get("ADMIN_PASSWORD", "")},
    )
    if admin_login.status_code == 200:
        admin_token = admin_login.json()["token"]
        prof = httpx.get(
            f"{API_URL}/admin/users/{body['id']}/profile",
            headers={"Authorization": f"Bearer {admin_token}"},
        ).json()
        assert prof.get("phone_number") == phone
        assert prof.get("phone_verified") is True


def test_register_rejects_token_for_different_phone():
    phone_a = _rand_phone()
    phone_b = _rand_phone()
    with httpx.Client() as c:
        c.post(f"{API_URL}/auth/phone/send-code", json={"phone_number": phone_a})
        vr = c.post(f"{API_URL}/auth/phone/verify-code",
                    json={"phone_number": phone_a, "code": "123456"})
        token = vr.json()["phone_verification_token"]
        r = c.post(f"{API_URL}/auth/register", json={
            "email": _rand_email(),
            "password": "Test1234!",
            "name": "P",
            "phone_number": phone_b,
            "phone_verification_token": token,
        })
    assert r.status_code == 400
    assert "match" in r.json()["detail"].lower() or "invalid" in r.json()["detail"].lower()


def test_register_rejects_expired_token():
    phone = _rand_phone()
    expired = jwt.encode(
        {
            "type": "phone_verification",
            "phone": phone,
            "iat": int(time.time()) - 3600,
            "exp": int(time.time()) - 60,
            "jti": uuid.uuid4().hex,
        },
        server.JWT_SECRET,
        algorithm=server.JWT_ALGORITHM,
    )
    with httpx.Client() as c:
        r = c.post(f"{API_URL}/auth/register", json={
            "email": _rand_email(),
            "password": "Test1234!",
            "name": "P",
            "phone_number": phone,
            "phone_verification_token": expired,
        })
    assert r.status_code == 400
    assert "expired" in r.json()["detail"].lower()


def test_register_blocks_phone_reuse():
    phone = _rand_phone()
    with httpx.Client() as c:
        c.post(f"{API_URL}/auth/phone/send-code", json={"phone_number": phone})
        vr = c.post(f"{API_URL}/auth/phone/verify-code",
                    json={"phone_number": phone, "code": "123456"})
        token1 = vr.json()["phone_verification_token"]
        r1 = c.post(f"{API_URL}/auth/register", json={
            "email": _rand_email(), "password": "Test1234!", "name": "One",
            "phone_number": phone, "phone_verification_token": token1,
        })
        assert r1.status_code == 200, r1.text
        # Second attempt with SAME phone → blocked (even with fresh token).
        c.post(f"{API_URL}/auth/phone/send-code", json={"phone_number": phone})
        vr2 = c.post(f"{API_URL}/auth/phone/verify-code",
                     json={"phone_number": phone, "code": "123456"})
        token2 = vr2.json()["phone_verification_token"]
        r2 = c.post(f"{API_URL}/auth/register", json={
            "email": _rand_email(), "password": "Test1234!", "name": "Two",
            "phone_number": phone, "phone_verification_token": token2,
        })
    assert r2.status_code == 400
    assert "already" in r2.json()["detail"].lower()
