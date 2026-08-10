"""Iteration 74 — Admin new-user registration notification.

Verifies:
  * ONE call to _notify_admin_registration per successful /api/auth/register
  * Subject exactly "New User Registration - Solarisound"
  * HTML body contains name, email, human-readable timestamp, method,
    plan label (Free/Trial/Pro derived from user doc)
  * Duplicate register (400) → notify NOT called
  * Validation failure (422)   → notify NOT called
  * Rate-limited (429)          → notify NOT called
  * Silent no-op when RESEND_ADMIN_RECIPIENT is empty
  * _derive_plan_label branches: Pro / Trial / Free
  * HTML injection safety (script tag escaped)
  * Removal of legacy digest helpers verified by grep of server.py source
  * Welcome email regression — both admin notify + welcome fire

Runs in-process via httpx.AsyncClient (matches
test_resend_from_address.py pattern) and mocks the Resend SDK.
"""
import os
import re
import sys
import uuid
import asyncio
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

sys.path.insert(0, "/app/backend")

ADMIN_TO = "admin-recipient@example.com"


@pytest_asyncio.fixture
async def app_client(monkeypatch):
    import server
    from motor.motor_asyncio import AsyncIOMotorClient

    fresh_client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    fresh_db = fresh_client[os.environ["DB_NAME"]]
    monkeypatch.setattr(server, "client", fresh_client)
    monkeypatch.setattr(server, "db", fresh_db)

    calls: list = []

    class _FakeEmails:
        @staticmethod
        def send(payload):
            calls.append(dict(payload))
            return {"id": f"mock-{uuid.uuid4()}"}

    class _FakeResend:
        Emails = _FakeEmails
        api_key = "mock"

    monkeypatch.setattr(server, "_resend", _FakeResend)
    monkeypatch.setattr(server, "_RESEND_API_KEY", "mock-key")
    monkeypatch.setattr(server, "_RESEND_ADMIN_RECIPIENT", ADMIN_TO)

    from httpx import AsyncClient, ASGITransport
    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac, calls, server


async def _wait_for_calls(calls, expected_count, timeout=5.0):
    start = asyncio.get_event_loop().time()
    while len(calls) < expected_count and (asyncio.get_event_loop().time() - start) < timeout:
        await asyncio.sleep(0.05)


def _cleanup_user(email: str):
    from pymongo import MongoClient
    MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]].users.delete_one({"email": email.lower()})


# ---- Legacy code removal ---------------------------------------------------

def test_legacy_digest_helpers_removed_from_server_py():
    with open("/app/backend/server.py") as f:
        src = f.read()
    for symbol in [
        "_queue_admin_signup_alert",
        "_flush_admin_signup_digest",
        "_notify_admin_new_user",
        "_admin_signup_buffer",
    ]:
        assert symbol not in src, f"Legacy digest helper {symbol!r} still present in server.py"


# ---- Direct helper unit tests ---------------------------------------------

def test_derive_plan_label_free():
    import server
    assert server._derive_plan_label({}) == "Free"
    # Expired flags → still Free
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert server._derive_plan_label({"pro_until": past, "stripe_trial_end": past}) == "Free"


def test_derive_plan_label_trial():
    import server
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    assert server._derive_plan_label({"stripe_trial_end": future}) == "Trial"


def test_derive_plan_label_pro():
    import server
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    assert server._derive_plan_label({"pro_until": future}) == "Pro"
    # pro takes precedence over trial
    assert server._derive_plan_label({"pro_until": future, "stripe_trial_end": future}) == "Pro"


@pytest.mark.asyncio
async def test_notify_admin_noop_when_recipient_empty(monkeypatch):
    import server
    monkeypatch.setattr(server, "_RESEND_ADMIN_RECIPIENT", "")

    sent = []

    def _fake_send(to, subject, html):
        sent.append((to, subject, html))
        return "id"

    monkeypatch.setattr(server, "_send_email_sync", _fake_send)
    # Also patch resend so key check would pass (we want to isolate recipient guard)
    monkeypatch.setattr(server, "_RESEND_API_KEY", "mock")
    class _R: api_key = "x"
    monkeypatch.setattr(server, "_resend", _R)

    await server._notify_admin_registration({
        "email": "x@example.com", "name": "X",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, method="email")
    assert sent == [], "Should not send when RESEND_ADMIN_RECIPIENT blank"


@pytest.mark.asyncio
async def test_notify_admin_noop_when_api_key_empty(monkeypatch):
    import server
    monkeypatch.setattr(server, "_RESEND_API_KEY", "")
    monkeypatch.setattr(server, "_RESEND_ADMIN_RECIPIENT", ADMIN_TO)

    sent = []

    def _fake_send(to, subject, html):
        sent.append((to, subject, html))

    monkeypatch.setattr(server, "_send_email_sync", _fake_send)

    await server._notify_admin_registration({
        "email": "x@example.com", "name": "X",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, method="email")
    assert sent == []


# ---- Full HTTP register flow tests ----------------------------------------

@pytest.mark.asyncio
async def test_register_fires_exactly_one_admin_notify(app_client):
    ac, calls, server = app_client
    email = f"TEST_notify_{uuid.uuid4().hex[:8]}@example.com"
    try:
        r = await ac.post("/api/auth/register", json={
            "email": email,
            "password": "StrongPass!2345",
            "name": "Alice Wonder",
        })
        assert r.status_code == 200, r.text
        await _wait_for_calls(calls, 2, timeout=6.0)

        admin_calls = [c for c in calls if c["subject"] == "New User Registration - Solarisound"]
        assert len(admin_calls) == 1, f"expected 1 admin notify, got {len(admin_calls)}: {[c['subject'] for c in calls]}"
        ac_call = admin_calls[0]
        assert ADMIN_TO in ac_call["to"]

        html = ac_call["html"]
        assert "Alice Wonder" in html
        assert email.lower() in html
        assert "Email" in html  # method title-cased
        assert "Free" in html   # plan label
        # Timestamp rendered human-readable — 4-digit year & UTC suffix.
        assert re.search(r"\b20\d{2}\b", html)
        assert "UTC" in html
    finally:
        _cleanup_user(email)


@pytest.mark.asyncio
async def test_register_admin_notify_html_escapes_script(app_client):
    ac, calls, server = app_client
    email = f"TEST_xss_{uuid.uuid4().hex[:8]}@example.com"
    try:
        r = await ac.post("/api/auth/register", json={
            "email": email,
            "password": "StrongPass!2345",
            "name": "<script>alert(1)</script>",
        })
        assert r.status_code == 200
        await _wait_for_calls(calls, 2, timeout=6.0)
        admin_calls = [c for c in calls if c["subject"] == "New User Registration - Solarisound"]
        assert admin_calls
        html = admin_calls[0]["html"]
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html
    finally:
        _cleanup_user(email)


@pytest.mark.asyncio
async def test_register_fires_both_admin_and_welcome(app_client):
    ac, calls, server = app_client
    email = f"TEST_both_{uuid.uuid4().hex[:8]}@example.com"
    try:
        r = await ac.post("/api/auth/register", json={
            "email": email,
            "password": "StrongPass!2345",
            "name": "Both User",
        })
        assert r.status_code == 200
        await _wait_for_calls(calls, 2, timeout=6.0)
        subjects = [c["subject"] for c in calls]
        assert "New User Registration - Solarisound" in subjects
        assert any("Welcome" in s for s in subjects), f"welcome missing: {subjects}"
    finally:
        _cleanup_user(email)


@pytest.mark.asyncio
async def test_duplicate_register_does_not_notify_admin(app_client):
    ac, calls, server = app_client
    email = f"TEST_dup_{uuid.uuid4().hex[:8]}@example.com"
    try:
        r1 = await ac.post("/api/auth/register", json={
            "email": email, "password": "StrongPass!2345", "name": "Dup1",
        })
        assert r1.status_code == 200
        await _wait_for_calls(calls, 2, timeout=6.0)

        # Now clear and try duplicate — patch out _notify_admin_registration
        # to detect calls with an AsyncMock.
        mock_notify = AsyncMock()
        with patch.object(server, "_notify_admin_registration", mock_notify):
            r2 = await ac.post("/api/auth/register", json={
                "email": email, "password": "StrongPass!2345", "name": "Dup2",
            })
            assert r2.status_code == 400
            assert "already" in r2.json().get("detail", "").lower()
            await asyncio.sleep(0.3)
            assert mock_notify.call_count == 0, "admin notify fired on duplicate"
    finally:
        _cleanup_user(email)


@pytest.mark.asyncio
async def test_invalid_password_does_not_notify_admin(app_client):
    ac, calls, server = app_client
    mock_notify = AsyncMock()
    with patch.object(server, "_notify_admin_registration", mock_notify):
        r = await ac.post("/api/auth/register", json={
            "email": f"TEST_weak_{uuid.uuid4().hex[:6]}@example.com",
            "password": "short",  # too weak
            "name": "Weak",
        })
        assert r.status_code in (400, 422), r.text
        await asyncio.sleep(0.3)
        assert mock_notify.call_count == 0


@pytest.mark.asyncio
async def test_invalid_email_does_not_notify_admin(app_client):
    ac, calls, server = app_client
    mock_notify = AsyncMock()
    with patch.object(server, "_notify_admin_registration", mock_notify):
        r = await ac.post("/api/auth/register", json={
            "email": "not-an-email",
            "password": "StrongPass!2345",
            "name": "Bad",
        })
        assert r.status_code in (400, 422)
        await asyncio.sleep(0.3)
        assert mock_notify.call_count == 0


@pytest.mark.asyncio
async def test_rate_limited_register_does_not_notify_admin(monkeypatch):
    """Force a non-localhost IP so the register throttle kicks in on burst."""
    import server
    from motor.motor_asyncio import AsyncIOMotorClient

    fresh_client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    monkeypatch.setattr(server, "client", fresh_client)
    monkeypatch.setattr(server, "db", fresh_client[os.environ["DB_NAME"]])

    # Fake resend so admin notify would fire if it ever got called.
    calls: list = []
    class _FE:
        @staticmethod
        def send(p):
            calls.append(p); return {"id": "x"}
    class _R:
        Emails = _FE
        api_key = "mock"
    monkeypatch.setattr(server, "_resend", _R)
    monkeypatch.setattr(server, "_RESEND_API_KEY", "mock")
    monkeypatch.setattr(server, "_RESEND_ADMIN_RECIPIENT", ADMIN_TO)

    # Force non-loopback IP so throttle applies.
    monkeypatch.setattr(server, "_client_ip", lambda req: "203.0.113.99")

    from httpx import AsyncClient, ASGITransport
    transport = ASGITransport(app=server.app)
    created_emails = []
    mock_notify = AsyncMock()
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            # Burn through the capacity (15) then confirm 429.
            got_429 = False
            for i in range(30):
                em = f"TEST_rl_{uuid.uuid4().hex[:8]}@example.com"
                r = await ac.post("/api/auth/register", json={
                    "email": em, "password": "StrongPass!2345", "name": f"RL{i}",
                })
                if r.status_code == 200:
                    created_emails.append(em)
                elif r.status_code == 429:
                    got_429 = True
                    break
            assert got_429, "Expected a 429 after burst"

            # Now with notify mocked, verify a further 429 doesn't invoke it.
            with patch.object(server, "_notify_admin_registration", mock_notify):
                r = await ac.post("/api/auth/register", json={
                    "email": f"TEST_rl_extra_{uuid.uuid4().hex[:8]}@example.com",
                    "password": "StrongPass!2345", "name": "Extra",
                })
                assert r.status_code == 429
                await asyncio.sleep(0.2)
                assert mock_notify.call_count == 0
    finally:
        # Cleanup
        from pymongo import MongoClient
        col = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]].users
        for em in created_emails:
            col.delete_one({"email": em.lower()})
