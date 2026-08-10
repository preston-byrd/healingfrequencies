"""Verify all outbound Resend calls use `noreply@solarisounds.com` as the
from address, and that the four documented flows (register welcome + admin
alert, support ack + admin notify, password reset) hit the SDK with the
right payload shape. We import server.py directly and monkeypatch
`_resend.Emails.send` — the container-hosted supervisor process is a
separate interpreter and can't be patched over HTTP, so we exercise the
FastAPI app in-process via httpx.AsyncClient.
"""
import os
import sys
import re
import uuid
import asyncio
import pytest
import pytest_asyncio

sys.path.insert(0, "/app/backend")


EXPECTED_FROM = "noreply@solarisounds.com"


# ---- Static / config assertions -------------------------------------------

def test_env_sender_email_is_solarisounds():
    with open("/app/backend/.env") as f:
        content = f.read()
    assert re.search(r"^RESEND_SENDER_EMAIL=noreply@solarisounds\.com\s*$",
                     content, re.MULTILINE), "RESEND_SENDER_EMAIL not set correctly in .env"


def test_module_resolves_sender_at_import_time():
    import server
    assert server._RESEND_SENDER == EXPECTED_FROM


def test_no_hardcoded_onboarding_sender_in_send_calls():
    """grep server.py: Emails.send call sites must all reference _RESEND_SENDER,
    never a hardcoded string, never 'onboarding@resend.dev'."""
    with open("/app/backend/server.py") as f:
        src = f.read()
    # Only default fallback in os.environ.get should mention onboarding@resend.dev.
    # It must NOT appear inside any `from` key of a payload.
    for m in re.finditer(r'"from"\s*:\s*([^\n,]+)', src):
        val = m.group(1).strip()
        assert val == "_RESEND_SENDER", (
            f"Unexpected from value in payload: {val!r}"
        )
    send_calls = re.findall(r"_resend\.Emails\.send\(", src)
    assert len(send_calls) <= 3, f"Too many Emails.send call sites: {len(send_calls)}"
    assert len(send_calls) >= 3, f"Expected 3 Emails.send call sites, found {len(send_calls)}"


# ---- In-process HTTP tests with mocked Resend -----------------------------

@pytest_asyncio.fixture
async def app_client(monkeypatch):
    """Spin up the FastAPI app in-process with a patched Resend SDK so we
    capture every send() call without hitting the network."""
    import server
    from motor.motor_asyncio import AsyncIOMotorClient

    # Rebind motor to the current running event loop — pytest-asyncio spins a
    # fresh loop per test, so we must reconnect or motor errors with
    # "Event loop is closed" on the first find_one.
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
    # Ensure admin recipient is set so admin-notify branches fire.
    monkeypatch.setattr(server, "_RESEND_ADMIN_RECIPIENT", "admin@example.com")

    from httpx import AsyncClient, ASGITransport
    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac, calls, server


async def _wait_for_calls(calls, expected_count, timeout=3.0):
    """Poll until background tasks queued via asyncio.create_task complete."""
    start = asyncio.get_event_loop().time()
    while len(calls) < expected_count and (asyncio.get_event_loop().time() - start) < timeout:
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_register_fires_admin_alert_and_welcome_email(app_client):
    ac, calls, server = app_client
    # Reset digest buffer for deterministic behaviour.
    server._admin_signup_buffer.clear()
    server._admin_signup_last_sent_at = 0.0

    email = f"TEST_reg_{uuid.uuid4().hex[:8]}@example.com"
    r = await ac.post("/api/auth/register", json={
        "email": email,
        "password": "StrongPass!2345",
        "name": "Alice <script>alert(1)</script>",
    })
    assert r.status_code == 200, r.text
    await _wait_for_calls(calls, 2, timeout=6.0)
    print(f"DEBUG calls={calls}")
    assert len(calls) >= 2, f"Expected 2 sends (admin+welcome), got {len(calls)}: {calls}"
    froms = [c["from"] for c in calls]
    assert all(f == EXPECTED_FROM for f in froms), froms

    # Welcome email addressed to the user (server lowercases the email).
    welcomes = [c for c in calls if email.lower() in c.get("to", [])]
    assert welcomes, "welcome email not sent to user"
    # HTML escaping of the name -> raw '<script>' should NOT appear.
    assert "<script>" not in welcomes[0]["html"]
    assert "&lt;script&gt;" in welcomes[0]["html"]

    # Cleanup user
    from pymongo import MongoClient
    MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]].users.delete_one({"email": email.lower()})


@pytest.mark.asyncio
async def test_support_contact_sends_admin_and_user_ack(app_client):
    ac, calls, server = app_client
    # Login as admin
    r = await ac.post("/api/auth/login", json={
        "email": "admin@example.com",
        "password": "JuzlUWlMMOjHM0u#m5qv0ds!oYp8",
    })
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    calls.clear()
    user_typed_email = "different-reply@example.com"
    r = await ac.post(
        "/api/support/contact",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "reason": "feature_request",
            "message": "Please add <script>alert(1)</script> support",
            "email": user_typed_email,
            "name": "Bob",
        },
    )
    assert r.status_code == 200, r.text
    await _wait_for_calls(calls, 2)
    assert len(calls) >= 2, f"expected 2 calls got {len(calls)}: {calls}"

    for c in calls:
        assert c["from"] == EXPECTED_FROM, c

    # Admin notify has admin recipient.
    admin_call = next(c for c in calls if "admin@example.com" in c.get("to", []))
    assert admin_call["subject"].startswith("[Feature Request]")

    # User ack uses body.email (different-reply), subject startswith expected.
    ack = next(c for c in calls if user_typed_email in c.get("to", []))
    assert ack["subject"].startswith("We received your message · ["), ack["subject"]
    # HTML-escaped
    assert "<script>" not in ack["html"]
    assert "&lt;script&gt;" in ack["html"]


@pytest.mark.asyncio
async def test_forgot_password_uses_solarisounds_from(app_client):
    ac, calls, server = app_client
    # Use admin as a known-existing account.
    calls.clear()
    r = await ac.post("/api/auth/forgot-password", json={"email": "admin@example.com"})
    assert r.status_code == 200
    await _wait_for_calls(calls, 1)
    assert len(calls) >= 1, "reset email should have been dispatched"
    assert calls[0]["from"] == EXPECTED_FROM
    assert "admin@example.com" in calls[0]["to"]


@pytest.mark.asyncio
async def test_flows_noop_when_resend_disabled(monkeypatch):
    """When RESEND_API_KEY is empty or module missing, endpoints still 200."""
    import server
    from motor.motor_asyncio import AsyncIOMotorClient
    fresh_client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    monkeypatch.setattr(server, "client", fresh_client)
    monkeypatch.setattr(server, "db", fresh_client[os.environ["DB_NAME"]])
    monkeypatch.setattr(server, "_RESEND_API_KEY", "")
    monkeypatch.setattr(server, "_resend", None)

    from httpx import AsyncClient, ASGITransport
    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        email = f"TEST_noop_{uuid.uuid4().hex[:8]}@example.com"
        r = await ac.post("/api/auth/register", json={
            "email": email, "password": "StrongPass!2345", "name": "NoopUser",
        })
        assert r.status_code == 200, r.text
        # forgot-password also 200 without exception
        r2 = await ac.post("/api/auth/forgot-password", json={"email": email})
        assert r2.status_code == 200

    from pymongo import MongoClient
    MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]].users.delete_one({"email": email})


@pytest.mark.asyncio
async def test_support_ack_falls_back_to_account_email(app_client):
    """When body.email is omitted, the ack should go to the account email."""
    ac, calls, server = app_client
    r = await ac.post("/api/auth/login", json={
        "email": "admin@example.com",
        "password": "JuzlUWlMMOjHM0u#m5qv0ds!oYp8",
    })
    token = r.json()["token"]

    calls.clear()
    r = await ac.post(
        "/api/support/contact",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "other", "message": "no email override provided here"},
    )
    assert r.status_code == 200, r.text
    await _wait_for_calls(calls, 2)
    ack_calls = [c for c in calls if c["subject"].startswith("We received your message")]
    assert ack_calls, "user ack not sent"
    assert "admin@example.com" in ack_calls[0]["to"]
