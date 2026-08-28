"""Regression tests for the admin user-profile view/edit endpoints.

Covers:
- GET /api/admin/users/{user_id}/profile returns editable + sensitive lists
- PUT accepts name / plan_notes / nudge_cadence / notification_prefs
- Sensitive fields (email, role) require confirm=true
- Role change blocked when admin targets self (self-demote guard)
- Validation errors (invalid email, out-of-range max_per_day, bad cadence)
- Audit log records one row per changed field with before/after
- Non-admin callers get 403
- Reset flags: reset_hearing_profile / reset_prefs
"""

import os
import uuid

import pytest
import httpx

API_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/") + "/api"

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "JuzlUWlMMOjHM0u#m5qv0ds!oYp8"


def _login(email: str, password: str) -> str:
    with httpx.Client() as c:
        r = c.post(f"{API_URL}/auth/login", json={"email": email, "password": password})
        r.raise_for_status()
        return r.json()["token"]


@pytest.fixture(scope="module")
def admin_token():
    return _login(ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture()
def target_user():
    """Create a fresh user for each test so cross-test mutations don't leak."""
    suffix = uuid.uuid4().hex[:8]
    email = f"admin_profile_target_{suffix}@example.com"
    password = "TestPass123!"
    # HF-030: register now requires a verified phone. Use TWILIO_TEST_MODE=1
    # test-code path to mint a real verification token.
    n = uuid.uuid4().int % 10000000
    phone = f"+1555{n:07d}"
    with httpx.Client() as c:
        c.post(f"{API_URL}/auth/phone/send-code", json={"phone_number": phone})
        vr = c.post(f"{API_URL}/auth/phone/verify-code",
                    json={"phone_number": phone, "code": "123456"})
        vr.raise_for_status()
        token = vr.json()["phone_verification_token"]
        r = c.post(f"{API_URL}/auth/register", json={
            "email": email, "password": password, "name": "Target User",
            "phone_number": phone, "phone_verification_token": token,
        })
        r.raise_for_status()
        uid = r.json()["id"]
    yield {"id": uid, "email": email, "password": password}


@pytest.fixture()
def non_admin_token(target_user):
    return _login(target_user["email"], target_user["password"])


def test_admin_get_profile_shape(admin_token, target_user):
    with httpx.Client() as c:
        r = c.get(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == target_user["email"]
    assert body["name"] == "Target User"
    assert "password_hash" not in body
    assert "_editable_fields" in body and isinstance(body["_editable_fields"], list)
    assert "email" in body["_editable_fields"]
    assert "role" in body["_editable_fields"]
    assert "_sensitive_fields" in body
    assert set(body["_sensitive_fields"]) == {"email", "role", "phone_number", "phone_verified"}
    # HF-030 phone fields must be in the editable allow-list too.
    assert "phone_number" in body["_editable_fields"]
    assert "phone_verified" in body["_editable_fields"]


def test_admin_put_name_and_plan_notes(admin_token, target_user):
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        r = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"name": "New Display Name", "plan_notes": "Comp Pro through Q2"},
        )
    assert r.status_code == 200, r.text
    changes = r.json()["changes"]
    fields = {c["field"] for c in changes}
    assert "name" in fields and "plan_notes" in fields


def test_admin_put_sensitive_email_requires_confirm(admin_token, target_user):
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        # Without confirm — 400
        r = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"email": f"reassigned_{uuid.uuid4().hex[:6]}@example.com"},
        )
        assert r.status_code == 400
        # With confirm — 200
        new_email = f"reassigned_{uuid.uuid4().hex[:6]}@example.com"
        r = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"email": new_email, "confirm": True},
        )
        assert r.status_code == 200, r.text
        assert any(c["field"] == "email" and c["after"] == new_email for c in r.json()["changes"])


def test_admin_put_email_rejects_invalid(admin_token, target_user):
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        r = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"email": "not-an-email", "confirm": True},
        )
    assert r.status_code == 400


def test_admin_put_email_rejects_duplicate(admin_token, target_user):
    # Admin's own email already exists in DB → moving target user onto it must fail.
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        r = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"email": ADMIN_EMAIL, "confirm": True},
        )
    assert r.status_code == 409


def test_admin_role_change_requires_confirm(admin_token, target_user):
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        r = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"role": "admin"},
        )
        assert r.status_code == 400
        r = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"role": "admin", "confirm": True},
        )
        assert r.status_code == 200


def test_admin_cannot_self_demote(admin_token):
    """Admin targeting their own row cannot flip role to 'user'."""
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        me = c.get(f"{API_URL}/me/subscription", headers=hdr).json()
        # The /me endpoint doesn't return the raw user id, so look up via list.
        users = c.get(f"{API_URL}/admin/users?limit=500&include_test=true", headers=hdr).json()["items"]
        admin_row = next(u for u in users if u["email"] == ADMIN_EMAIL)
        r = c.put(
            f"{API_URL}/admin/users/{admin_row['id']}/profile",
            headers=hdr,
            json={"role": "user", "confirm": True},
        )
    assert r.status_code == 400
    assert "demote" in r.json()["detail"].lower()


def test_admin_put_notification_prefs(admin_token, target_user):
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        r = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"notification_prefs": {"max_per_day": 2, "enabled": False}},
        )
    assert r.status_code == 200, r.text
    fields = {c["field"] for c in r.json()["changes"]}
    assert "notification_prefs.max_per_day" in fields
    assert "notification_prefs.enabled" in fields


def test_admin_put_nudge_cadence_validation(admin_token, target_user):
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        r = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"nudge_cadence": "nonsense"},
        )
    assert r.status_code == 400


def test_admin_audit_log_records_every_field(admin_token, target_user):
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"name": "Audit Test Name", "plan_notes": "auditing this"},
        )
        # Audit log GET
        r = c.get(
            f"{API_URL}/admin/audit-log",
            headers=hdr,
            params={"event": "admin.user.profile.updated", "limit": 20},
        )
    assert r.status_code == 200
    rows = r.json()["items"]
    matching = [
        row for row in rows
        if row.get("metadata", {}).get("target_user_id") == target_user["id"]
    ]
    fields = {row["metadata"]["field"] for row in matching}
    assert "name" in fields
    assert "plan_notes" in fields
    for row in matching:
        assert "before" in row["metadata"]
        assert "after" in row["metadata"]


def test_admin_reset_prefs_flag(admin_token, target_user):
    hdr = {"Authorization": f"Bearer {admin_token}"}
    # Seed prefs via /me/prefs while impersonating? Simpler: log in as target, set prefs, then log back as admin.
    tgt_token = _login(target_user["email"], target_user["password"])
    with httpx.Client() as c:
        r = c.put(
            f"{API_URL}/me/prefs",
            headers={"Authorization": f"Bearer {tgt_token}"},
            json={"frequency": 528.0, "duration_minutes": 15},
        )
        assert r.status_code == 200
        # Admin resets
        r = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"reset_prefs": True},
        )
    assert r.status_code == 200, r.text
    assert any(c["field"] == "prefs" for c in r.json()["changes"])


def test_non_admin_blocked(admin_token, target_user, non_admin_token):
    hdr = {"Authorization": f"Bearer {non_admin_token}"}
    with httpx.Client() as c:
        r = c.get(f"{API_URL}/admin/users/{target_user['id']}/profile", headers=hdr)
        assert r.status_code in (401, 403)
        r = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"name": "Hack"},
        )
        assert r.status_code in (401, 403)


def test_admin_get_nonexistent_returns_404(admin_token):
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        r = c.get(f"{API_URL}/admin/users/nonexistent-id-xyz/profile", headers=hdr)
    assert r.status_code == 404


def test_no_op_put_returns_empty_changes(admin_token, target_user):
    """PUT that sends fields identical to current values must not audit."""
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        # First read the current name.
        cur = c.get(f"{API_URL}/admin/users/{target_user['id']}/profile", headers=hdr).json()
        r = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"name": cur["name"]},
        )
    assert r.status_code == 200
    assert r.json()["changes"] == []


# -----------------------------------------------------------------------------
# HF-030: phone_number + phone_verified — admin can edit both, both sensitive.
# -----------------------------------------------------------------------------

def _rand_phone() -> str:
    """Generate a fake but E.164-shaped US phone for uniqueness safety."""
    n = uuid.uuid4().int % 10000000
    return f"+1666{n:07d}"


def test_admin_put_phone_requires_confirm(admin_token, target_user):
    hdr = {"Authorization": f"Bearer {admin_token}"}
    new_phone = _rand_phone()
    with httpx.Client() as c:
        r = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"phone_number": new_phone},
        )
        assert r.status_code == 400
        assert "confirm" in r.text.lower()
        r2 = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"phone_number": new_phone, "confirm": True},
        )
    assert r2.status_code == 200, r2.text
    fields = {c["field"] for c in r2.json()["changes"]}
    assert "phone_number" in fields


def test_admin_put_phone_invalid_format(admin_token, target_user):
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        r = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"phone_number": "555-1234", "confirm": True},
        )
    assert r.status_code == 400
    assert "country code" in r.text.lower() or "e.164" in r.text.lower()


def test_admin_put_phone_uniqueness_collision(admin_token, target_user):
    hdr = {"Authorization": f"Bearer {admin_token}"}
    # Create a second user with a distinct phone.
    suffix = uuid.uuid4().hex[:8]
    other_email = f"admin_phone_other_{suffix}@example.com"
    other_phone = _rand_phone()
    with httpx.Client() as c:
        c.post(f"{API_URL}/auth/phone/send-code", json={"phone_number": other_phone})
        vr = c.post(f"{API_URL}/auth/phone/verify-code",
                    json={"phone_number": other_phone, "code": "123456"})
        token = vr.json()["phone_verification_token"]
        c.post(f"{API_URL}/auth/register", json={
            "email": other_email, "password": "TestPass123!", "name": "Other",
            "phone_number": other_phone, "phone_verification_token": token,
        }).raise_for_status()
        # Now try to give target_user the same phone.
        r = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"phone_number": other_phone, "confirm": True},
        )
    assert r.status_code == 409, r.text


def test_admin_put_phone_verified_toggle(admin_token, target_user):
    """Admin can flip phone_verified independently, but only if a phone exists."""
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        # target_user was registered with a verified phone by the fixture.
        # Flip verified off:
        r = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"phone_verified": False, "confirm": True},
        )
        assert r.status_code == 200, r.text
        fresh = c.get(f"{API_URL}/admin/users/{target_user['id']}/profile", headers=hdr).json()
        assert fresh.get("phone_verified") in (False, None)
        # Flip back on:
        r2 = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"phone_verified": True, "confirm": True},
        )
    assert r2.status_code == 200


def test_admin_put_phone_verified_requires_phone(admin_token, target_user):
    """Marking verified=true without a phone_number on file is nonsensical."""
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        # Clear phone first.
        c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"phone_number": "", "confirm": True},
        ).raise_for_status()
        r = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"phone_verified": True, "confirm": True},
        )
    assert r.status_code == 400
    assert "phone_number" in r.text


def test_admin_change_phone_invalidates_verified(admin_token, target_user):
    """Changing phone_number should force phone_verified back to False."""
    hdr = {"Authorization": f"Bearer {admin_token}"}
    new_phone = _rand_phone()
    with httpx.Client() as c:
        # target starts verified (fixture); PUT a new number.
        r = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"phone_number": new_phone, "confirm": True},
        )
        assert r.status_code == 200, r.text
        fresh = c.get(f"{API_URL}/admin/users/{target_user['id']}/profile", headers=hdr).json()
    assert fresh["phone_number"] == new_phone
    assert fresh.get("phone_verified") in (False, None)


def test_admin_clear_phone_unsets_verified(admin_token, target_user):
    """Sending empty phone_number clears both fields."""
    hdr = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client() as c:
        r = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"phone_number": "", "confirm": True},
        )
        assert r.status_code == 200
        fresh = c.get(f"{API_URL}/admin/users/{target_user['id']}/profile", headers=hdr).json()
    assert not fresh.get("phone_number")
    assert not fresh.get("phone_verified")


def test_admin_phone_audit_log_masks_full_number(admin_token, target_user):
    """Audit log must record only last-4 of the phone, never the full E.164."""
    hdr = {"Authorization": f"Bearer {admin_token}"}
    new_phone = _rand_phone()
    with httpx.Client() as c:
        r = c.put(
            f"{API_URL}/admin/users/{target_user['id']}/profile",
            headers=hdr,
            json={"phone_number": new_phone, "confirm": True},
        )
    assert r.status_code == 200
    change = next((ch for ch in r.json()["changes"] if ch["field"] == "phone_number"), None)
    assert change is not None
    # `after` should be the last-4 digits, not the full number.
    assert change["after"] == new_phone[-4:]
    assert new_phone not in str(change)
