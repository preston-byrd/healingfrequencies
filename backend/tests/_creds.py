"""Shared test helpers — single source of truth for credentials so test files
never hardcode secrets.

Resolution order for the admin password:
    1. `ADMIN_TEST_PASSWORD` env var (CI / per-developer override)
    2. `ADMIN_PASSWORD` from `/app/backend/.env` (matches what the live admin
       account actually has, so the tests stay in sync with rotations)

If neither is set we refuse to fabricate a fallback — better to fail loudly
than to silently authenticate with a wrong/empty password and produce a 401
storm in CI.
"""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import dotenv_values


def _load_admin_creds():
    email = os.environ.get("ADMIN_TEST_EMAIL")
    password = os.environ.get("ADMIN_TEST_PASSWORD")

    if not email or not password:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            vals = dotenv_values(env_path)
            email = email or vals.get("ADMIN_EMAIL")
            password = password or vals.get("ADMIN_PASSWORD")

    if not email or not password:
        raise RuntimeError(
            "Admin test credentials not available. Set ADMIN_TEST_EMAIL + "
            "ADMIN_TEST_PASSWORD env vars, or ensure backend/.env contains "
            "ADMIN_EMAIL + ADMIN_PASSWORD."
        )
    return email, password


ADMIN_EMAIL, ADMIN_PASSWORD = _load_admin_creds()
