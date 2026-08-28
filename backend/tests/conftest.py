"""Shared pytest fixtures + helpers for the backend test suite.

The most important helper here is `_auto_inject_phone_verification`, an
autouse fixture that monkeypatches httpx so any POST to /api/auth/register
that omits `phone_number` gets a freshly-verified phone + token appended
transparently. Without this, all pre-HF-030 tests that call register with
just `{email, password, name}` would fail Pydantic validation.

The helper only kicks in when TWILIO_TEST_MODE=1 is set on the running
server (see server.py). In production this fixture is inert.
"""

import os
import uuid
from urllib.parse import urlparse

import pytest
import httpx
from _pytest.monkeypatch import MonkeyPatch


API_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/") + "/api"


def _fresh_verified_phone() -> tuple[str, str]:
    """Send-code + verify-code against the running server and return
    `(phone_number_e164, phone_verification_token)`. Requires the server
    to have TWILIO_TEST_MODE=1 enabled so 123456 is accepted."""
    n = uuid.uuid4().int % 10000000
    phone = f"+1555{n:07d}"
    with httpx.Client(timeout=10.0) as c:
        c.post(f"{API_URL}/auth/phone/send-code", json={"phone_number": phone})
        vr = c.post(f"{API_URL}/auth/phone/verify-code",
                    json={"phone_number": phone, "code": "123456"})
        vr.raise_for_status()
        return phone, vr.json()["phone_verification_token"]


def _needs_phone_injection(url: str, body) -> bool:
    try:
        path = urlparse(str(url)).path
    except Exception:
        path = str(url)
    return path.endswith("/auth/register") and isinstance(body, dict) and "phone_number" not in body


@pytest.fixture(autouse=True, scope="session")
def _auto_inject_phone_verification():
    """Session-scoped monkeypatch: wraps httpx.Client.post + requests.post
    so any register call missing a phone gets one auto-injected. Session
    scope so module-scoped fixtures (like `fresh_user`) that register a
    test account also benefit — a function-scoped autouse fixture would
    activate AFTER module fixtures run, missing them entirely.
    """
    mp = MonkeyPatch()
    original_httpx_post = httpx.Client.post
    try:
        import requests as _requests
        original_requests_post = _requests.post
    except ImportError:
        _requests = None
        original_requests_post = None

    def patched_httpx_post(self, url, *args, **kwargs):
        body = kwargs.get("json")
        if _needs_phone_injection(url, body):
            try:
                phone, token = _fresh_verified_phone()
                kwargs["json"] = {**body, "phone_number": phone, "phone_verification_token": token}
            except Exception:
                pass
        return original_httpx_post(self, url, *args, **kwargs)

    def patched_requests_post(url, *args, **kwargs):
        body = kwargs.get("json")
        if _needs_phone_injection(url, body):
            try:
                phone, token = _fresh_verified_phone()
                kwargs["json"] = {**body, "phone_number": phone, "phone_verification_token": token}
            except Exception:
                pass
        return original_requests_post(url, *args, **kwargs)

    mp.setattr(httpx.Client, "post", patched_httpx_post)
    if _requests is not None:
        mp.setattr(_requests, "post", patched_requests_post)
    yield
    mp.undo()
