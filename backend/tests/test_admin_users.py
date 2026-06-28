"""Backend tests for admin user management (grant/revoke Pro)."""
import os
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://frequency-healer-31.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"

ADMIN_EMAIL = os.environ.get('ADMIN_TEST_EMAIL', 'admin@example.com')
ADMIN_PASSWORD = os.environ.get('ADMIN_TEST_PASSWORD') or __import__("tests._creds", fromlist=["ADMIN_PASSWORD"]).ADMIN_PASSWORD


def _login(email: str, password: str) -> str:
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["token"]


def _register(email: str, password: str, name: str = "Tester") -> dict:
    r = requests.post(f"{API}/auth/register",
                      json={"email": email, "password": password, "name": name}, timeout=20)
    assert r.status_code == 200, f"register failed: {r.status_code} {r.text}"
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------- fixtures ----------
@pytest.fixture(scope="module")
def admin_token() -> str:
    return _login(ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture()
def fresh_user():
    tag = uuid.uuid4().hex[:8]
    email = f"TEST_{tag}@example.com"
    info = _register(email, "abcdef12", name=f"User {tag}")
    return {**info, "email": email, "password": "abcdef12"}


# ---------- tests ----------

# GET /api/admin/users
class TestAdminListUsers:
    def test_list_requires_auth(self):
        r = requests.get(f"{API}/admin/users", timeout=20)
        assert r.status_code == 401

    def test_list_requires_admin(self, fresh_user):
        token = _login(fresh_user["email"], fresh_user["password"])
        r = requests.get(f"{API}/admin/users", headers=_auth(token), timeout=20)
        assert r.status_code == 403

    def test_admin_list_returns_users(self, admin_token):
        r = requests.get(f"{API}/admin/users", headers=_auth(admin_token), timeout=20)
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list)
        assert len(items) > 0
        sample = items[0]
        for k in ("id", "email", "name", "plan", "pro", "days_left"):
            assert k in sample, f"missing key {k}"
        # role is optional (only set for admin users)
        # pro_until may or may not be present
        assert "_id" not in sample
        assert "password_hash" not in sample
        # pro_until may be None but key should be present sometimes — verify type when set
        for u in items:
            assert isinstance(u["pro"], bool)
            assert isinstance(u["days_left"], int)
            assert u["plan"] in ("pro", "basic", "trial")

    def test_search_filters_by_email(self, admin_token, fresh_user):
        # search for our just-created TEST_ user
        substr = fresh_user["email"].split("@")[0]  # TEST_<tag>
        r = requests.get(f"{API}/admin/users", params={"q": substr},
                         headers=_auth(admin_token), timeout=20)
        assert r.status_code == 200
        items = r.json()
        assert len(items) >= 1
        for u in items:
            assert substr.lower() in u["email"].lower()

    def test_search_case_insensitive(self, admin_token, fresh_user):
        substr = fresh_user["email"].split("@")[0].upper()
        r = requests.get(f"{API}/admin/users", params={"q": substr},
                         headers=_auth(admin_token), timeout=20)
        assert r.status_code == 200
        emails = [u["email"] for u in r.json()]
        # Server lowercases emails on register, so match lowercase
        assert any(fresh_user["email"].lower() == e for e in emails)


# POST /api/admin/users/{id}/grant-pro
class TestGrantPro:
    def test_grant_pro_as_non_admin_forbidden(self, fresh_user):
        token = _login(fresh_user["email"], fresh_user["password"])
        r = requests.post(f"{API}/admin/users/{fresh_user['id']}/grant-pro",
                          headers=_auth(token), json={"days": 30}, timeout=20)
        assert r.status_code == 403

    def test_grant_pro_nonexistent_user(self, admin_token):
        bogus = str(uuid.uuid4())
        r = requests.post(f"{API}/admin/users/{bogus}/grant-pro",
                          headers=_auth(admin_token), json={"days": 30}, timeout=20)
        assert r.status_code == 404

    def test_grant_pro_then_list_shows_pro(self, admin_token, fresh_user):
        r = requests.post(f"{API}/admin/users/{fresh_user['id']}/grant-pro",
                          headers=_auth(admin_token), json={"days": 30}, timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["plan"] == "pro"
        # verify via list
        r2 = requests.get(f"{API}/admin/users",
                          params={"q": fresh_user["email"]},
                          headers=_auth(admin_token), timeout=20)
        assert r2.status_code == 200
        match = [u for u in r2.json() if u["id"] == fresh_user["id"]]
        assert len(match) == 1
        u = match[0]
        assert u["plan"] == "pro"
        assert u["pro"] is True
        # days_left should be around 30 (29-31 tolerance)
        assert 29 <= u["days_left"] <= 31, f"days_left={u['days_left']}"
        assert u.get("pro_until") is not None

    def test_grant_pro_extends_from_existing_pro_until(self, admin_token, fresh_user):
        # 1st grant: 30 days
        r1 = requests.post(f"{API}/admin/users/{fresh_user['id']}/grant-pro",
                           headers=_auth(admin_token), json={"days": 30}, timeout=20)
        assert r1.status_code == 200
        pu1 = datetime.fromisoformat(r1.json()["pro_until"])
        # 2nd grant: another 30 days -> should be ~60 days from now
        r2 = requests.post(f"{API}/admin/users/{fresh_user['id']}/grant-pro",
                           headers=_auth(admin_token), json={"days": 30}, timeout=20)
        assert r2.status_code == 200
        pu2 = datetime.fromisoformat(r2.json()["pro_until"])
        # pu2 - pu1 should be ~30 days (proving extension, not reset)
        delta = pu2 - pu1
        assert timedelta(days=29, hours=23) <= delta <= timedelta(days=30, hours=1), \
            f"expected ~30d extension, got {delta}"
        # And the resulting days_left should be ~60
        r3 = requests.get(f"{API}/admin/users",
                          params={"q": fresh_user["email"]},
                          headers=_auth(admin_token), timeout=20)
        u = [x for x in r3.json() if x["id"] == fresh_user["id"]][0]
        assert 59 <= u["days_left"] <= 61, f"days_left={u['days_left']}"


# POST /api/admin/users/{id}/revoke-pro
class TestRevokePro:
    def test_revoke_as_non_admin_forbidden(self, fresh_user):
        token = _login(fresh_user["email"], fresh_user["password"])
        r = requests.post(f"{API}/admin/users/{fresh_user['id']}/revoke-pro",
                          headers=_auth(token), timeout=20)
        assert r.status_code == 403

    def test_revoke_after_grant_resets_to_basic(self, admin_token, fresh_user):
        # grant first
        g = requests.post(f"{API}/admin/users/{fresh_user['id']}/grant-pro",
                          headers=_auth(admin_token), json={"days": 30}, timeout=20)
        assert g.status_code == 200
        # revoke
        r = requests.post(f"{API}/admin/users/{fresh_user['id']}/revoke-pro",
                          headers=_auth(admin_token), timeout=20)
        assert r.status_code == 200
        assert r.json()["plan"] == "basic"
        # verify via list
        r2 = requests.get(f"{API}/admin/users",
                          params={"q": fresh_user["email"]},
                          headers=_auth(admin_token), timeout=20)
        u = [x for x in r2.json() if x["id"] == fresh_user["id"]][0]
        assert u["plan"] == "basic"
        assert u["pro"] is False
        assert u["days_left"] == 0
        assert u.get("pro_until") in (None, "")


# Regression: existing admin/me endpoints still work
class TestRegression:
    def test_admin_plan_prices_still_works(self, admin_token):
        r = requests.get(f"{API}/admin/plan-prices", headers=_auth(admin_token), timeout=20)
        assert r.status_code == 200
        d = r.json()
        assert "monthly" in d and "annual" in d and "trial_days" in d

    def test_me_subscription_still_works(self, admin_token):
        r = requests.get(f"{API}/me/subscription", headers=_auth(admin_token), timeout=20)
        assert r.status_code == 200
        d = r.json()
        for k in ("plan", "pro", "pro_until", "days_left", "trial_used", "is_admin"):
            assert k in d
        assert d["is_admin"] is True
