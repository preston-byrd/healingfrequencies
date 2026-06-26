"""Backend tests for the defensive checkout error handling refactor.

Covers the new behavior in /api/me/checkout:
  - Happy path with sk_test_emergent still returns 200 with `url` + `session_id`
  - Invalid plan -> 400
  - Missing auth -> 401
  - Bad Stripe key -> 502 with detail starting with "Stripe checkout failed:"
  - Missing STRIPE_API_KEY -> 500 with "Payments not configured" message
"""
import os
import sys
import uuid
import importlib
import pytest
import requests

# REACT_APP_BACKEND_URL fallback to FRONTEND_URL (env-driven, no hardcoded prod URL).
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or os.environ.get("FRONTEND_URL")
            or "https://frequency-healer-31.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"


def _fresh_email(tag="ckerr"):
    return f"TEST_{tag}_{uuid.uuid4().hex[:8]}@example.com"


def _register(email, password="Testpass123!", name="Tester"):
    r = requests.post(f"{API}/auth/register",
                      json={"email": email, "password": password, "name": name})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


# ---------- Happy path (HTTP, real running server) ----------
def test_checkout_monthly_happy_returns_stripe_url():
    """Preview server uses sk_test_emergent — should return a real Stripe URL."""
    t = _register(_fresh_email("mhappy"))
    r = requests.post(f"{API}/me/checkout", headers=_hdr(t), json={
        "plan": "monthly", "origin_url": "https://x.test",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data.get("url"), str) and data["url"], "url must be non-empty"
    assert data["url"].startswith("https://checkout.stripe.com"), (
        f"expected checkout.stripe.com URL, got {data['url']!r}")
    assert isinstance(data.get("session_id"), str) and data["session_id"]


def test_checkout_annual_happy_returns_stripe_url():
    t = _register(_fresh_email("ahappy"))
    r = requests.post(f"{API}/me/checkout", headers=_hdr(t), json={
        "plan": "annual", "origin_url": "https://x.test",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["url"].startswith("https://checkout.stripe.com")
    assert data["session_id"]


# ---------- Auth / validation paths (HTTP) ----------
def test_checkout_requires_auth_returns_401():
    r = requests.post(f"{API}/me/checkout", json={
        "plan": "monthly", "origin_url": "https://x.test",
    })
    assert r.status_code == 401, r.text


def test_checkout_invalid_plan_returns_400():
    t = _register(_fresh_email("invp"))
    r = requests.post(f"{API}/me/checkout", headers=_hdr(t), json={
        "plan": "lifetime", "origin_url": "https://x.test",
    })
    assert r.status_code == 400
    assert "invalid plan" in r.json().get("detail", "").lower()


# ---------- In-process tests for error paths (httpx ASGITransport) ------------
# These cannot be done over HTTP against the live preview server without
# changing STRIPE_API_KEY in /app/backend/.env (which the review forbids).
# Instead we mount the FastAPI app via httpx.AsyncClient + ASGITransport so
# Motor and FastAPI share the same asyncio loop, then monkeypatch
# server.STRIPE_API_KEY to simulate bad / missing keys.

import asyncio
import httpx

sys.path.insert(0, "/app/backend")
import server as server_mod  # noqa: E402


def _run_async(coro_factory):
    """Each call creates a fresh event loop AND rebinds motor's AsyncIOMotorClient
    to that loop, because Motor binds at instantiation time and a previously-used
    loop will be closed across tests."""
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        # Rebind the global motor client + db that server.py uses.
        new_client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        new_db = new_client[os.environ["DB_NAME"]]
        server_mod.client = new_client
        server_mod.db = new_db
        return loop.run_until_complete(coro_factory())
    finally:
        loop.close()


async def _inproc_register(client, email):
    r = await client.post("/api/auth/register",
                          json={"email": email, "password": "Testpass123!", "name": "T"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_checkout_bad_stripe_key_returns_502(monkeypatch):
    monkeypatch.setattr(server_mod, "STRIPE_API_KEY", "sk_live_invalid_key_for_test")

    async def _go():
        transport = httpx.ASGITransport(app=server_mod.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            t = await _inproc_register(c, _fresh_email("badkey"))
            r = await c.post("/api/me/checkout",
                             headers={"Authorization": f"Bearer {t}"},
                             json={"plan": "monthly", "origin_url": "https://x.test"})
            return r.status_code, r.json()
    status, body = _run_async(_go)
    assert status == 502, f"expected 502, got {status}: {body}"
    detail = body.get("detail", "")
    assert detail.startswith("Stripe checkout failed:"), (
        f"detail should start with 'Stripe checkout failed:', got {detail!r}")


def test_checkout_missing_stripe_key_returns_500(monkeypatch):
    monkeypatch.setattr(server_mod, "STRIPE_API_KEY", "")

    async def _go():
        transport = httpx.ASGITransport(app=server_mod.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            t = await _inproc_register(c, _fresh_email("nokey"))
            r = await c.post("/api/me/checkout",
                             headers={"Authorization": f"Bearer {t}"},
                             json={"plan": "monthly", "origin_url": "https://x.test"})
            return r.status_code, r.json()
    status, body = _run_async(_go)
    assert status == 500, f"expected 500, got {status}: {body}"
    detail = body.get("detail", "")
    assert "Payments not configured" in detail
    assert "STRIPE_API_KEY" in detail


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
