"""Phase 11 — Resonance / Drift Score tests.

Covers the backend scoring logic (cosine-similarity mapping to 0-100), the
preview + history endpoints, the automatic persistence of `resonance_score`
on profile save, and the lazy backfill on `GET /profile` for legacy rows.
"""

from __future__ import annotations

import os
import time
import uuid
import pytest
import requests
from datetime import datetime, timezone

BASE = os.environ.get("BACKEND_URL", "http://localhost:8001")
API = f"{BASE}/api"

ADMIN_EMAIL = os.environ.get("ADMIN_TEST_EMAIL", os.environ.get("ADMIN_EMAIL", "admin@example.com"))
ADMIN_PASSWORD = os.environ.get(
    "ADMIN_TEST_PASSWORD", os.environ.get("ADMIN_PASSWORD", "JuzlUWlMMOjHM0u#m5qv0ds!oYp8")
)


def _login(session, email, password):
    r = session.post(f"{API}/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return r.json()["token"]


# Cached admin token so the whole test module makes AT MOST one login call —
# the brute-force throttle on /auth/login is meant for external attackers,
# not for local pytest runs where every fixture would otherwise trip it.
_admin_token_cache: dict = {}


def _admin_token(session):
    if _admin_token_cache.get("t"):
        return _admin_token_cache["t"]
    tok = _login(session, ADMIN_EMAIL, ADMIN_PASSWORD)
    _admin_token_cache["t"] = tok
    return tok


@pytest.fixture()
def admin_session():
    with requests.Session() as s:
        token = _admin_token(s)
        s.headers["Authorization"] = f"Bearer {token}"
        yield s


def _mock_spectrum(seed: int = 0) -> list:
    """A deterministic small spectrum: 5 peaks with pseudo-random magnitudes."""
    import math
    return [
        {"freq": f, "mag": 0.3 + 0.2 * math.sin(seed + i)}
        for i, f in enumerate([120, 240, 528, 963, 1200])
    ]


def _minimal_profile_body(spectrum: list, ver: int = 1) -> dict:
    return {
        "version": ver,
        "sample_rate": 48000,
        "duration": 15.0,
        "fft_size": 4096,
        "spectrum": spectrum,
        "dominant": [],
        "dips": [],
        "bands": [],
        "underrepresented": [],
        "confirmed_gaps": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def test_preview_endpoint_returns_100_when_no_baseline(admin_session):
    # If the admin already has a baseline, this test can't run cleanly; skip.
    prof = admin_session.get(f"{API}/harmonic-blueprint/profile").json()
    if prof.get("eigenmode"):
        pytest.skip("admin already has a baseline")
    r = admin_session.post(
        f"{API}/harmonic-blueprint/resonance-score/preview",
        json={"spectrum": _mock_spectrum(0)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["score"] == 100
    assert body["has_baseline"] is False


def test_preview_identical_spectrum_scores_100(admin_session):
    prof = admin_session.get(f"{API}/harmonic-blueprint/profile").json()
    eigen = prof.get("eigenmode") or {}
    if not eigen.get("spectrum"):
        pytest.skip("admin eigenmode has no spectrum data")
    r = admin_session.post(
        f"{API}/harmonic-blueprint/resonance-score/preview",
        json={"spectrum": eigen["spectrum"]},
    )
    assert r.status_code == 200
    assert r.json()["score"] == 100


def test_history_endpoint_returns_time_series(admin_session):
    r = admin_session.get(f"{API}/harmonic-blueprint/resonance-score/history")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    # Structure check on any items present
    for it in body["items"]:
        assert isinstance(it["score"], int)
        assert 0 <= it["score"] <= 100
        assert it["at"]
        assert isinstance(it["is_eigenmode"], bool)


def test_get_profile_lazily_backfills_score(admin_session):
    # First read should populate `resonance_score` on the latest + eigen if
    # they were missing. Second read confirms it's present.
    r1 = admin_session.get(f"{API}/harmonic-blueprint/profile").json()
    r2 = admin_session.get(f"{API}/harmonic-blueprint/profile").json()
    if r2.get("profile"):
        assert r2["profile"].get("resonance_score") is not None
    if r2.get("eigenmode"):
        assert r2["eigenmode"].get("resonance_score") is not None


def test_free_user_gets_402(admin_session):
    """A fresh Free user must receive HTTP 402 (paywall) on the new endpoints."""
    email = f"free+{uuid.uuid4().hex[:8]}@example.com"
    with requests.Session() as free:
        r = free.post(f"{API}/auth/register",
                       json={"email": email, "password": "FreeTest9!"})
        r.raise_for_status()
        free.headers["Authorization"] = f"Bearer {r.json()['token']}"
        assert free.post(f"{API}/harmonic-blueprint/resonance-score/preview",
                          json={"spectrum": []}).status_code == 402
        assert free.get(f"{API}/harmonic-blueprint/resonance-score/history").status_code == 402


def test_score_persists_on_new_profile_save(admin_session):
    """Save a mock profile via the existing endpoint and confirm the response
    reflects a Resonance Score. Whether it's 100 (no baseline yet) or 0..100
    (baseline exists) depends on admin's state, so we just assert the field
    is present and is a valid int in range."""
    body = _minimal_profile_body(_mock_spectrum(int(time.time()) % 1000))
    r = admin_session.post(f"{API}/harmonic-blueprint/profile", json=body)
    # Rate-limit: this endpoint has a 6/window bucket. If exhausted, skip.
    if r.status_code == 429:
        pytest.skip("harmonic-blueprint save rate-limited")
    assert r.status_code == 200, r.text
    prof = admin_session.get(f"{API}/harmonic-blueprint/profile").json().get("profile") or {}
    assert "resonance_score" in prof
    assert isinstance(prof["resonance_score"], int)
    assert 0 <= prof["resonance_score"] <= 100
