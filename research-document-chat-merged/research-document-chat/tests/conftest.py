"""Shared test setup: deterministic security settings and a clean rate limiter per test."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for entry in (str(ROOT), str(ROOT / "backend")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

# Set before digital_msl.config is first imported / cached.
os.environ.setdefault("DIGITAL_MSL_REQUIRE_ADMIN_MFA", "false")
# backend/.env may switch demo mode on for local use; the suite must not depend on it.
os.environ.setdefault("DIGITAL_MSL_DEMO_MODE", "false")
os.environ.setdefault("DIGITAL_MSL_SEED_DEMO_USERS", "false")
os.environ.setdefault("DIGITAL_MSL_ENVIRONMENT", "development")
os.environ.setdefault("DIGITAL_MSL_DEV_ECHO_OTP", "true")
os.environ.setdefault("DIGITAL_MSL_LEGACY_DEMO_USERS", "true")  # the suite still uses the @digital-msl.local accounts

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_limiters():
    from digital_msl import compliance, security

    security.reset_rate_limits()
    compliance.reset_rate_limits()
    yield
    security.reset_rate_limits()
    compliance.reset_rate_limits()
os.environ.setdefault("DIGITAL_MSL_APPROVED_REPOSITORY_IDS", "*")
os.environ.setdefault("DIGITAL_MSL_REQUIRE_APPROVAL_METADATA", "false")  # backend/.env turns this on for the live IKM  # the suite uses fake repositories; the real default is empty (answer nothing)
