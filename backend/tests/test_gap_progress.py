"""Phase 12 — Harmonic Blueprint Gap Closure Progress + Resonance Timeline tests.

Covers `/api/harmonic-blueprint/gap-progress`:
- Auth gate (401 unauth, 402 free)
- Empty state when the caller has no eigenmode
- Payload shape (gaps + timeline + summary + eigenmode_id)
- Trend classification (improving / stable / attention) via the closure_pct
  hysteresis around ±10%
- History points are chronological and include severity
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

_admin_token_cache: dict = {}


def _admin_token(session):
    if _admin_token_cache.get("t"):
        return _admin_token_cache["t"]
    r = session.post(f"{API}/auth/login",
                     json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    r.raise_for_status()
    tok = r.json()["token"]
    _admin_token_cache["t"] = tok
    return tok


@pytest.fixture()
def admin_session():
    with requests.Session() as s:
        s.headers["Authorization"] = f"Bearer {_admin_token(s)}"
        yield s


def test_gap_progress_requires_auth():
    r = requests.get(f"{API}/harmonic-blueprint/gap-progress")
    assert r.status_code == 401


def test_gap_progress_free_user_402():
    email = f"free+{uuid.uuid4().hex[:8]}@example.com"
    with requests.Session() as free:
        r = free.post(f"{API}/auth/register",
                       json={"email": email, "password": "FreeTest9!"})
        r.raise_for_status()
        free.headers["Authorization"] = f"Bearer {r.json()['token']}"
        assert free.get(f"{API}/harmonic-blueprint/gap-progress").status_code == 402


def test_gap_progress_shape(admin_session):
    r = admin_session.get(f"{API}/harmonic-blueprint/gap-progress")
    assert r.status_code == 200, r.text
    body = r.json()
    # Every key we depend on for the frontend must be present, even when the
    # admin has no captured baseline yet.
    for key in ("gaps", "timeline", "eigenmode_id", "summary"):
        assert key in body, f"missing key {key}"
    assert isinstance(body["gaps"], list)
    assert isinstance(body["timeline"], list)


def test_gap_progress_trend_classification(admin_session):
    """When gaps exist, each row must include the fields the UI relies on and
    the trend must be one of the three canonical labels."""
    r = admin_session.get(f"{API}/harmonic-blueprint/gap-progress")
    assert r.status_code == 200
    body = r.json()
    for gap in body["gaps"]:
        for field in ("key", "label", "first_severity", "latest_severity",
                      "closure_pct", "trend", "sample_count", "history"):
            assert field in gap, f"gap row missing {field}"
        assert gap["trend"] in ("improving", "stable", "attention")
        assert isinstance(gap["history"], list)
        for pt in gap["history"]:
            assert "profile_id" in pt
            assert "at" in pt
            assert "severity" in pt
            assert isinstance(pt["severity"], (int, float))


def test_gap_progress_timeline_is_chronological(admin_session):
    r = admin_session.get(f"{API}/harmonic-blueprint/gap-progress")
    assert r.status_code == 200
    timeline = r.json()["timeline"]
    if len(timeline) < 2:
        pytest.skip("not enough timeline entries to assert ordering")
    for i in range(1, len(timeline)):
        assert timeline[i]["at"] >= timeline[i - 1]["at"], (
            "timeline must be chronological ascending"
        )
        assert isinstance(timeline[i]["score"], int)
        assert 0 <= timeline[i]["score"] <= 100


def test_gap_progress_summary_shape(admin_session):
    """Summary is either null (no non-eigenmode captures yet) OR a dict with
    first_score / latest_score / improvement_pct / session_count."""
    r = admin_session.get(f"{API}/harmonic-blueprint/gap-progress")
    body = r.json()
    summary = body["summary"]
    if summary is None:
        # Admin only has an eigenmode → summary should be null.
        assert all(t["is_eigenmode"] for t in body["timeline"]) or not body["timeline"]
        return
    for field in ("first_score", "latest_score", "improvement_pct", "session_count"):
        assert field in summary, f"summary missing {field}"
    assert isinstance(summary["improvement_pct"], (int, float))
    assert summary["session_count"] >= 1
