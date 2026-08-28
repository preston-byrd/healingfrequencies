"""Regression tests for the per-frequency ideal default volume feature.

Verifies:
- Public GET /api/frequency-defaults returns empty overrides initially.
- Admin PUT upserts an override that shows up in public GET.
- Admin DELETE removes it.
- Volume out-of-range and hz out-of-range raise 400.
- Non-admin users cannot PUT or DELETE.
"""

import os

import pytest
import httpx

API_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/") + "/api"

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "JuzlUWlMMOjHM0u#m5qv0ds!oYp8"


@pytest.fixture(scope="module")
def admin_token():
    with httpx.Client() as c:
        r = c.post(f"{API_URL}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        r.raise_for_status()
        body = r.json()
        return body.get("access_token") or body.get("token")


@pytest.fixture(scope="module")
def non_admin_token():
    email = "freqvol_regular@example.com"
    password = "Test1234!Regular"
    with httpx.Client() as c:
        # Register may fail if user exists; login is authoritative either way.
        c.post(f"{API_URL}/auth/register", json={"email": email, "password": password, "name": "Freq Vol"})
        r = c.post(f"{API_URL}/auth/login", json={"email": email, "password": password})
        r.raise_for_status()
        body = r.json()
        return body.get("access_token") or body.get("token")


@pytest.fixture(autouse=True)
def cleanup_test_hz(admin_token):
    # Guarantee the 987.65 slot is empty before AND after each test so we
    # never leave state that would leak across the module.
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        c.delete(f"{API_URL}/admin/frequency-defaults/987.65", headers=hdr)
    yield
    with httpx.Client() as c:
        c.delete(f"{API_URL}/admin/frequency-defaults/987.65", headers=hdr)


def test_public_get_shape():
    with httpx.Client() as c:
        r = c.get(f"{API_URL}/frequency-defaults")
    assert r.status_code == 200
    body = r.json()
    assert "overrides" in body and isinstance(body["overrides"], dict)


def test_admin_put_then_public_get_reflects_override(admin_token):
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        put = c.put(f"{API_URL}/admin/frequency-defaults", headers=hdr, json={"hz": 987.65, "volume": 0.37})
        assert put.status_code == 200
        pub = c.get(f"{API_URL}/frequency-defaults")
        assert pub.status_code == 200
        overrides = pub.json()["overrides"]
        assert "987.65" in overrides
        assert abs(overrides["987.65"] - 0.37) < 1e-6


def test_admin_delete_removes_override(admin_token):
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        c.put(f"{API_URL}/admin/frequency-defaults", headers=hdr, json={"hz": 987.65, "volume": 0.5})
        d = c.delete(f"{API_URL}/admin/frequency-defaults/987.65", headers=hdr)
        assert d.status_code == 200
        assert d.json()["deleted"] == 1
        pub = c.get(f"{API_URL}/frequency-defaults")
        assert "987.65" not in pub.json()["overrides"]


def test_admin_put_rejects_out_of_range_volume(admin_token):
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        r = c.put(f"{API_URL}/admin/frequency-defaults", headers=hdr, json={"hz": 528, "volume": 1.5})
        assert r.status_code == 400
        r = c.put(f"{API_URL}/admin/frequency-defaults", headers=hdr, json={"hz": 528, "volume": -0.1})
        assert r.status_code == 400


def test_admin_put_rejects_out_of_range_hz(admin_token):
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        r = c.put(f"{API_URL}/admin/frequency-defaults", headers=hdr, json={"hz": 0, "volume": 0.3})
        assert r.status_code == 400
        r = c.put(f"{API_URL}/admin/frequency-defaults", headers=hdr, json={"hz": 30000, "volume": 0.3})
        assert r.status_code == 400


def test_non_admin_cannot_put(non_admin_token):
    hdr = {"Authorization": f"Bearer {non_admin_token}"}
    with httpx.Client() as c:
        r = c.put(f"{API_URL}/admin/frequency-defaults", headers=hdr, json={"hz": 528, "volume": 0.3})
        assert r.status_code in (401, 403)


def test_non_admin_cannot_delete(non_admin_token):
    hdr = {"Authorization": f"Bearer {non_admin_token}"}
    with httpx.Client() as c:
        r = c.delete(f"{API_URL}/admin/frequency-defaults/528", headers=hdr)
        assert r.status_code in (401, 403)


def test_admin_list_endpoint_shape(admin_token):
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        c.put(f"{API_URL}/admin/frequency-defaults", headers=hdr, json={"hz": 987.65, "volume": 0.42})
        r = c.get(f"{API_URL}/admin/frequency-defaults", headers=hdr)
    assert r.status_code == 200
    docs = r.json()["overrides"]
    match = next((d for d in docs if abs(d["hz"] - 987.65) < 1e-6), None)
    assert match is not None
    assert abs(match["volume"] - 0.42) < 1e-6
    assert "updated_at" in match and "updated_by" in match
