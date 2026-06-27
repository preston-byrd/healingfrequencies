"""Iter-25 defensive hardening tests:
 1. GET /api/health/stripe — three states: good key, missing key, bad key.
 2. POST /api/me/checkout outer guard:
    a) generic Exception inside _create_checkout_impl -> 502 with rid+ErrorClass
    b) HTTPException raised inside _create_checkout_impl -> re-raised as-is (not wrapped)
 3. Regression: happy path still returns {url, session_id}; bad-key still
    surfaces via inner try/except as 502 with 'Stripe checkout failed:'.
"""
import os
import re
import sys
import uuid
import asyncio
import pytest
import requests
import httpx

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or os.environ.get("FRONTEND_URL")
            or "https://frequency-healer-31.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

sys.path.insert(0, "/app/backend")
import server as server_mod  # noqa: E402
from fastapi import HTTPException  # noqa: E402


def _fresh_email(tag="i25"):
    return f"TEST_{tag}_{uuid.uuid4().hex[:8]}@example.com"


def _register(email, password="Testpass123!", name="Tester"):
    r = requests.post(f"{API}/auth/register",
                      json={"email": email, "password": password, "name": name})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


def _run_async(coro_factory):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        new_client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        new_db = new_client[os.environ["DB_NAME"]]
        server_mod.client = new_client
        server_mod.db = new_db
        return loop.run_until_complete(coro_factory())
    finally:
        loop.close()


# ---------------- /api/health/stripe -----------------------------------------
def test_health_stripe_ok_with_emergent_key():
    """Live preview uses sk_test_emergent — endpoint must return 200 + ok=true."""
    r = requests.get(f"{API}/health/stripe", timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("ok") is True, f"expected ok=true, got {data}"
    assert data.get("api_base") == "https://integrations.emergentagent.com/stripe"
    kp = data.get("key_prefix", "")
    assert kp.startswith("sk_test_emer"), f"unexpected key_prefix {kp!r}"
    assert data.get("timeout_seconds") == 25


def test_health_stripe_missing_key_returns_config_error(monkeypatch):
    """If STRIPE_API_KEY is empty we must return graceful JSON, no exception/500."""
    monkeypatch.setattr(server_mod, "STRIPE_API_KEY", "")

    async def _go():
        transport = httpx.ASGITransport(app=server_mod.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/health/stripe")
            return r.status_code, r.json()
    status, body = _run_async(_go)
    assert status == 200, f"expected 200, got {status}: {body}"
    assert body == {"ok": False, "error": "STRIPE_API_KEY not set", "stage": "config"}


def test_health_stripe_bad_key_returns_graceful_failure(monkeypatch):
    """Bad key must surface as ok=false with stage=stripe_call, no 500/520."""
    monkeypatch.setattr(server_mod, "STRIPE_API_KEY", "sk_test_invalid_xyz")

    async def _go():
        transport = httpx.ASGITransport(app=server_mod.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/health/stripe")
            return r.status_code, r.json()
    status, body = _run_async(_go)
    assert status == 200, f"expected 200 envelope, got {status}: {body}"
    assert body.get("ok") is False
    assert body.get("stage") == "stripe_call"
    assert body.get("api_base") == "https://api.stripe.com"
    assert body.get("key_prefix", "").startswith("sk_test_inva")
    assert body.get("error"), "must include error text"


# ---------------- /me/checkout outer guard -----------------------------------
def test_checkout_outer_guard_wraps_unexpected_exception(monkeypatch):
    """If _create_checkout_impl raises a non-HTTPException, the outer guard
    must convert it to a 502 with detail starting with 'Checkout failed
    unexpectedly (rid=' followed by 8-char rid + error class name."""
    async def boom(*a, **kw):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(server_mod, "_get_plan_config", boom)

    async def _go():
        transport = httpx.ASGITransport(app=server_mod.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/auth/register",
                             json={"email": _fresh_email("ogrt"),
                                   "password": "Testpass123!", "name": "T"})
            assert r.status_code == 200, r.text
            t = r.json()["token"]
            r2 = await c.post("/api/me/checkout",
                              headers={"Authorization": f"Bearer {t}"},
                              json={"plan": "monthly", "origin_url": "https://x.test"})
            return r2.status_code, r2.json()
    status, body = _run_async(_go)
    assert status == 502, f"expected 502, got {status}: {body}"
    detail = body.get("detail", "")
    # Must start with the rid envelope
    m = re.match(r"^Checkout failed unexpectedly \(rid=([0-9a-f]{8})\): RuntimeError: ", detail)
    assert m, f"unexpected detail format: {detail!r}"
    assert "synthetic" in detail


def test_checkout_outer_guard_preserves_httpexception(monkeypatch):
    """HTTPException raised inside _create_checkout_impl must be re-raised as-is.
    The outer guard MUST NOT wrap it in a 502."""
    async def maintenance(*a, **kw):
        raise HTTPException(status_code=503, detail="maintenance")
    monkeypatch.setattr(server_mod, "_get_plan_config", maintenance)

    async def _go():
        transport = httpx.ASGITransport(app=server_mod.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/auth/register",
                             json={"email": _fresh_email("oghe"),
                                   "password": "Testpass123!", "name": "T"})
            assert r.status_code == 200, r.text
            t = r.json()["token"]
            r2 = await c.post("/api/me/checkout",
                              headers={"Authorization": f"Bearer {t}"},
                              json={"plan": "monthly", "origin_url": "https://x.test"})
            return r2.status_code, r2.json()
    status, body = _run_async(_go)
    assert status == 503, f"expected 503 (preserved), got {status}: {body}"
    assert body.get("detail") == "maintenance"


# ---------------- Regression: contract still intact --------------------------
def test_checkout_happy_path_response_contract_unchanged():
    """Iter-24 contract — body must have both 'url' (Stripe URL) and 'session_id'."""
    t = _register(_fresh_email("contract"))
    r = requests.post(f"{API}/me/checkout", headers=_hdr(t), json={
        "plan": "monthly", "origin_url": "https://x.test",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(["url", "session_id"]).issubset(data.keys()), data
    assert data["url"].startswith("https://checkout.stripe.com")
    assert data["session_id"].startswith("cs_test_") or data["session_id"].startswith("cs_live_")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
