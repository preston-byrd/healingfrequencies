"""Phase 10 — Notification system tests.

Covers preferences, category/master gating, daily cap, quiet-hours model,
feature announcement CRUD + audience filtering, public tick idempotency,
push subscription upsert + unsubscribe, and admin analytics gating.
"""

from __future__ import annotations

import os
import uuid
import pytest
import requests

BASE = os.environ.get("BACKEND_URL", "http://localhost:8001")
API = f"{BASE}/api"

ADMIN_EMAIL = os.environ.get("ADMIN_TEST_EMAIL", os.environ.get("ADMIN_EMAIL", "admin@example.com"))
ADMIN_PASSWORD = os.environ.get(
    "ADMIN_TEST_PASSWORD", os.environ.get("ADMIN_PASSWORD", "JuzlUWlMMOjHM0u#m5qv0ds!oYp8")
)


def _register(session, email=None, password="ComplexPass9!"):
    email = email or f"notif+{uuid.uuid4().hex[:8]}@example.com"
    r = session.post(f"{API}/auth/register", json={
        "email": email, "password": password, "name": "Notif Tester",
    })
    r.raise_for_status()
    return r.json()["token"], email


def _login(session, email, password):
    r = session.post(f"{API}/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return r.json()["token"]


@pytest.fixture()
def s():
    with requests.Session() as sess:
        yield sess


def test_vapid_key_public(s):
    r = s.get(f"{API}/notifications/vapid-public-key")
    assert r.status_code == 200
    assert r.json().get("public_key")


def test_prefs_default_and_partial_update(s):
    token, _ = _register(s)
    h = {"Authorization": f"Bearer {token}"}
    prefs = s.get(f"{API}/me/notifications/prefs", headers=h).json()
    assert prefs["enabled"] is True and prefs["push_enabled"] is False
    for cat in ("feature_announcement", "checkin", "recommendation", "session_reminder", "harmonic_blueprint"):
        assert prefs["categories"][cat] is True
    assert prefs["quiet_hours"]["enabled"] is True
    assert prefs["max_per_day"] == 4

    p2 = s.put(f"{API}/me/notifications/prefs", headers=h,
                json={"categories": {"checkin": False}}).json()
    assert p2["categories"]["checkin"] is False
    assert p2["categories"]["feature_announcement"] is True

    p3 = s.put(f"{API}/me/notifications/prefs", headers=h,
                json={"quiet_hours": {"start_hour": 21}}).json()
    assert p3["quiet_hours"]["start_hour"] == 21
    assert p3["quiet_hours"]["end_hour"] == 7


def test_checkin_nudge_gated_by_category(s):
    token, _ = _register(s)
    h = {"Authorization": f"Bearer {token}"}
    s.put(f"{API}/me/notifications/prefs", headers=h,
           json={"categories": {"checkin": False}, "quiet_hours": {"enabled": False}})
    r = s.post(f"{API}/me/notifications/checkin-nudge", headers=h,
                json={"trigger": "post_session"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "delivered": False}
    s.put(f"{API}/me/notifications/prefs", headers=h, json={"categories": {"checkin": True}})
    r2 = s.post(f"{API}/me/notifications/checkin-nudge", headers=h,
                 json={"trigger": "post_session"})
    assert r2.json() == {"ok": True, "delivered": True}


def test_checkin_nudge_gated_by_master(s):
    token, _ = _register(s)
    h = {"Authorization": f"Bearer {token}"}
    s.put(f"{API}/me/notifications/prefs", headers=h,
           json={"enabled": False, "quiet_hours": {"enabled": False}})
    r = s.post(f"{API}/me/notifications/checkin-nudge", headers=h,
                json={"trigger": "post_session"})
    assert r.json()["delivered"] is False


def test_checkin_nudge_invalid_trigger(s):
    token, _ = _register(s)
    h = {"Authorization": f"Bearer {token}"}
    r = s.post(f"{API}/me/notifications/checkin-nudge", headers=h,
                json={"trigger": "clinical_diagnosis"})
    assert r.status_code == 422


def test_daily_cap_gates_nudges(s):
    token, _ = _register(s)
    h = {"Authorization": f"Bearer {token}"}
    s.put(f"{API}/me/notifications/prefs", headers=h,
           json={"max_per_day": 1, "quiet_hours": {"enabled": False}})
    r1 = s.post(f"{API}/me/notifications/checkin-nudge", headers=h,
                 json={"trigger": "post_session"})
    assert r1.json()["delivered"] is True
    r2 = s.post(f"{API}/me/notifications/checkin-nudge", headers=h,
                 json={"trigger": "post_session"})
    assert r2.json()["delivered"] is False


def test_tick_delivers_feature_announcements_idempotently(s):
    token, _ = _register(s)
    h = {"Authorization": f"Bearer {token}"}
    admin_token = _login(s, ADMIN_EMAIL, ADMIN_PASSWORD)
    ah = {"Authorization": f"Bearer {admin_token}"}
    title = f"Test feature {uuid.uuid4().hex[:6]}"
    ann = s.post(f"{API}/admin/feature-announcements", headers=ah, json={
        "title": title, "body": "gentle body", "destination": "/", "audience": "all",
    }).json()
    aid = ann["id"]
    try:
        r = s.post(f"{API}/me/notifications/tick", headers=h).json()
        assert r["delivered_announcements"] >= 1
        r2 = s.post(f"{API}/me/notifications/tick", headers=h).json()
        assert r2["delivered_announcements"] == 0
        titles = [n["title"] for n in s.get(f"{API}/me/notifications", headers=h).json()["items"]]
        assert title in titles
    finally:
        s.delete(f"{API}/admin/feature-announcements/{aid}", headers=ah)


def test_audience_pro_only_gates_free_user(s):
    token, _ = _register(s)
    h = {"Authorization": f"Bearer {token}"}
    admin_token = _login(s, ADMIN_EMAIL, ADMIN_PASSWORD)
    ah = {"Authorization": f"Bearer {admin_token}"}
    ann = s.post(f"{API}/admin/feature-announcements", headers=ah, json={
        "title": f"Pro only {uuid.uuid4().hex[:6]}", "body": "for pro users",
        "destination": "/", "audience": "pro",
    }).json()
    aid = ann["id"]
    try:
        s.post(f"{API}/me/notifications/tick", headers=h)
        listing = s.get(f"{API}/me/notifications", headers=h).json()["items"]
        assert not any(n.get("meta", {}).get("announcement_id") == aid for n in listing)
    finally:
        s.delete(f"{API}/admin/feature-announcements/{aid}", headers=ah)


def test_mark_opened_and_dismissed(s):
    token, _ = _register(s)
    h = {"Authorization": f"Bearer {token}"}
    s.put(f"{API}/me/notifications/prefs", headers=h, json={"quiet_hours": {"enabled": False}})
    s.post(f"{API}/me/notifications/checkin-nudge", headers=h, json={"trigger": "pre_session"})
    items = s.get(f"{API}/me/notifications", headers=h).json()["items"]
    assert items
    nid = items[0]["id"]
    assert s.post(f"{API}/me/notifications/{nid}/opened", headers=h).status_code == 200
    assert s.post(f"{API}/me/notifications/{nid}/dismissed", headers=h).status_code == 200
    after = s.get(f"{API}/me/notifications", headers=h).json()["items"]
    assert all(n["id"] != nid for n in after)


def test_push_subscribe_upsert_and_unsubscribe(s):
    token, _ = _register(s)
    h = {"Authorization": f"Bearer {token}"}
    endpoint = f"https://fcm.googleapis.com/fcm/send/{uuid.uuid4().hex}"
    sub = {"endpoint": endpoint, "keys": {"p256dh": "test", "auth": "test"}}
    r = s.post(f"{API}/me/notifications/push/subscribe", headers=h,
                json={"subscription": sub, "user_agent": "pytest"})
    assert r.status_code == 200
    r2 = s.post(f"{API}/me/notifications/push/subscribe", headers=h,
                 json={"subscription": sub, "user_agent": "pytest"})
    assert r2.status_code == 200
    prefs = s.get(f"{API}/me/notifications/prefs", headers=h).json()
    assert prefs["push_enabled"] is True
    r3 = s.delete(f"{API}/me/notifications/push/subscribe",
                   headers=h, params={"endpoint": endpoint})
    assert r3.status_code == 200
    r4 = s.post(f"{API}/me/notifications/push/subscribe", headers=h,
                 json={"subscription": {}})
    assert r4.status_code == 400


def test_admin_endpoints_require_admin(s):
    token, _ = _register(s)
    h = {"Authorization": f"Bearer {token}"}
    assert s.get(f"{API}/admin/feature-announcements", headers=h).status_code == 403
    assert s.get(f"{API}/admin/notifications/analytics", headers=h).status_code == 403


def test_admin_announcement_crud_and_analytics(s):
    token = _login(s, ADMIN_EMAIL, ADMIN_PASSWORD)
    h = {"Authorization": f"Bearer {token}"}
    title = f"Ephemeral {uuid.uuid4().hex[:6]}"
    a = s.post(f"{API}/admin/feature-announcements", headers=h, json={
        "title": title, "body": "a gentle body", "destination": "/", "audience": "all",
    }).json()
    aid = a["id"]
    r2 = s.put(f"{API}/admin/feature-announcements/{aid}", headers=h, json={
        "title": title + " v2", "body": "updated body", "destination": "/",
        "audience": "all", "active": True,
    }).json()
    assert r2["title"].endswith("v2")
    lst = s.get(f"{API}/admin/feature-announcements", headers=h).json()["items"]
    assert any(a["id"] == aid for a in lst)
    ra = s.get(f"{API}/admin/notifications/analytics", headers=h)
    assert ra.status_code == 200 and "rows" in ra.json()
    rd = s.delete(f"{API}/admin/feature-announcements/{aid}", headers=h)
    assert rd.status_code == 200


def test_input_validation_short_title_rejected(s):
    token = _login(s, ADMIN_EMAIL, ADMIN_PASSWORD)
    h = {"Authorization": f"Bearer {token}"}
    r = s.post(f"{API}/admin/feature-announcements", headers=h, json={
        "title": "x", "body": "y", "destination": "/", "audience": "all",
    })
    assert r.status_code == 422


def test_unauth_endpoints_return_401(s):
    for path, method in [
        ("/me/notifications/prefs", "get"),
        ("/me/notifications", "get"),
        ("/me/notifications/unread-count", "get"),
        ("/me/notifications/tick", "post"),
        ("/me/notifications/checkin-nudge", "post"),
        ("/me/notifications/push/subscribe", "post"),
    ]:
        fn = getattr(s, method)
        kw = {"json": {}} if method == "post" else {}
        r = fn(f"{API}{path}", **kw)
        assert r.status_code == 401, f"{path} should require auth (got {r.status_code})"
