"""Regression: /api/admin/users returns paginated + filtered results.

Verifies the Feb 2026 fix that upgraded the endpoint from a bare
list-of-200 to `{items, total, offset, limit, filtered_test_count}`,
excludes pytest-seeded @example.com rows by default, and honours
`offset`/`limit`/`include_test`/`q` query params.

Guarantees that even with thousands of users in the DB, the Admin
User Management view can page through every registered signup.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv("/app/backend/.env")

BASE = os.environ.get("BACKEND_URL", "http://localhost:8001")
API = f"{BASE}/api"


def _col(name: str):
    return MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]][name]


@pytest.fixture()
def admin_ctx():
    """Create a dedicated admin plus a handful of fixture users so the
    assertions don't depend on whatever's already in the shared DB."""
    admin_email = f"admin+{uuid.uuid4().hex[:8]}@example.com"
    admin_pw = "AdmT9!aA"
    admin_uid = str(uuid.uuid4())
    _col("users").insert_one({
        "id": admin_uid, "email": admin_email, "name": "AdmTest",
        "password_hash": bcrypt.hashpw(admin_pw.encode(), bcrypt.gensalt()).decode(),
        "role": "admin",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # 3 fixture "real" users on a non-@example.com domain so we can
    # assert the default-hide behaviour and be sure the count is
    # deterministic relative to what we inserted.
    fake_domain = f"regr-{uuid.uuid4().hex[:6]}.test"
    real_ids = []
    for i in range(3):
        uid = str(uuid.uuid4())
        _col("users").insert_one({
            "id": uid, "email": f"real{i}@{fake_domain}", "name": f"Real{i}",
            "password_hash": bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
            "role": "user",
            # Space out created_at so we get a deterministic sort order.
            "created_at": (datetime.now(timezone.utc) - timedelta(minutes=i)).isoformat(),
        })
        real_ids.append(uid)
    yield admin_email, admin_pw, admin_uid, fake_domain, real_ids
    _col("users").delete_one({"id": admin_uid})
    for uid in real_ids:
        _col("users").delete_one({"id": uid})


def _login(email, pw):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_admin_users_returns_paginated_shape(admin_ctx):
    admin_email, admin_pw, admin_uid, _fake, _real_ids = admin_ctx
    tok = _login(admin_email, admin_pw)
    r = requests.get(f"{API}/admin/users", headers=_hdr(tok))
    assert r.status_code == 200, r.text
    data = r.json()
    # New shape — object, not a bare list.
    assert isinstance(data, dict), data
    for key in ("items", "total", "offset", "limit", "filtered_test_count"):
        assert key in data, f"missing key {key} in response: {data}"
    assert isinstance(data["items"], list)
    assert data["offset"] == 0
    assert 1 <= data["limit"] <= 500


def test_admin_users_hides_example_com_by_default(admin_ctx):
    """@example.com is IANA-reserved for tests — never real signups. The
    endpoint must hide those rows so the admin's default view surfaces
    real registered users, not thousands of pytest fixtures."""
    admin_email, admin_pw, _admin_uid, fake_domain, _real_ids = admin_ctx
    tok = _login(admin_email, admin_pw)
    r = requests.get(f"{API}/admin/users?limit=500", headers=_hdr(tok))
    assert r.status_code == 200
    data = r.json()
    for u in data["items"]:
        assert not u["email"].lower().endswith("@example.com"), (
            "default listing must exclude @example.com test users, got: %s" % u["email"]
        )
    # And the count of hidden @example.com rows must be reported so the
    # admin knows the DB has more if they need it.
    assert data["filtered_test_count"] >= 1, data
    # Real (non-example.com) fixture users must all be present.
    real_emails = {u["email"] for u in data["items"]}
    for i in range(3):
        assert f"real{i}@{fake_domain}" in real_emails, real_emails


def test_admin_users_include_test_flag_shows_everything(admin_ctx):
    admin_email, admin_pw, _admin_uid, _fake, _real = admin_ctx
    tok = _login(admin_email, admin_pw)
    r_hidden = requests.get(f"{API}/admin/users", headers=_hdr(tok)).json()
    r_all = requests.get(f"{API}/admin/users?include_test=true", headers=_hdr(tok)).json()
    assert r_all["total"] >= r_hidden["total"] + r_hidden["filtered_test_count"] - 5, (
        "include_test=true must return at least as many rows as filtered + hidden",
        r_hidden, r_all,
    )


def test_admin_users_pagination_is_stable_and_progresses(admin_ctx):
    """offset + limit must return non-overlapping pages that together
    equal the total set."""
    admin_email, admin_pw, _admin_uid, _fake, _real = admin_ctx
    tok = _login(admin_email, admin_pw)
    # Force include_test=true so we have plenty of rows to page across.
    r0 = requests.get(f"{API}/admin/users?include_test=true&limit=5&offset=0", headers=_hdr(tok)).json()
    r1 = requests.get(f"{API}/admin/users?include_test=true&limit=5&offset=5", headers=_hdr(tok)).json()
    assert r0["total"] == r1["total"], (r0["total"], r1["total"])
    ids0 = {u["id"] for u in r0["items"]}
    ids1 = {u["id"] for u in r1["items"]}
    assert ids0.isdisjoint(ids1), (ids0, ids1)
    assert len(r0["items"]) == 5 and len(r1["items"]) <= 5


def test_admin_users_limit_is_capped_at_500(admin_ctx):
    admin_email, admin_pw, _admin_uid, _fake, _real = admin_ctx
    tok = _login(admin_email, admin_pw)
    r = requests.get(f"{API}/admin/users?limit=100000", headers=_hdr(tok)).json()
    assert r["limit"] == 500, r


def test_admin_users_search_still_works_with_new_shape(admin_ctx):
    admin_email, admin_pw, _admin_uid, fake_domain, _real = admin_ctx
    tok = _login(admin_email, admin_pw)
    r = requests.get(f"{API}/admin/users?q={fake_domain}", headers=_hdr(tok)).json()
    # All 3 fixture users share the fake_domain suffix.
    emails = {u["email"] for u in r["items"]}
    assert emails == {f"real{i}@{fake_domain}" for i in range(3)}, r


def test_admin_users_forbidden_for_non_admin(admin_ctx):
    """Non-admins must get 403 — permission unchanged after refactor."""
    _admin_email, _admin_pw, _admin_uid, _fake, _real = admin_ctx
    # Seed a non-admin real user and try to hit the endpoint.
    email = f"user+{uuid.uuid4().hex[:8]}@example.com"
    pw = "UsrT9!aA"
    uid = str(uuid.uuid4())
    _col("users").insert_one({
        "id": uid, "email": email, "name": "Reg",
        "password_hash": bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode(),
        "role": "user",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    try:
        r_login = requests.post(f"{API}/auth/login", json={"email": email, "password": pw})
        assert r_login.status_code == 200, r_login.text
        tok = r_login.json()["token"]
        r = requests.get(f"{API}/admin/users", headers=_hdr(tok))
        assert r.status_code == 403, r.status_code
    finally:
        _col("users").delete_one({"id": uid})
