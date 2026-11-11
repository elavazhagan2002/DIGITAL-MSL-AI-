"""CI / deploy guard: exit non-zero when demo mode is enabled for a production environment.

Usage:  python scripts/check_demo_guard.py
Reads DIGITAL_MSL_DEMO_MODE, DIGITAL_MSL_SEED_DEMO_USERS (deprecated alias) and DIGITAL_MSL_ENVIRONMENT
from the environment / backend/.env, exactly like the application does.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from digital_msl.config import DemoModeInProduction, check_demo_guard, get_dmsl_settings  # noqa: E402


def main() -> int:
    settings = get_dmsl_settings()
    try:
        check_demo_guard(settings)
    except DemoModeInProduction as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"OK: demo_mode={settings.demo_enabled} environment={settings.environment}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
