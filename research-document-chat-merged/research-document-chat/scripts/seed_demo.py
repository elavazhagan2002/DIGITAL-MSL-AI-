"""Seed the demo accounts (doctor / msl / admin @demo.example). Demo mode only; refuses in production.

Usage:  DIGITAL_MSL_DEMO_MODE=true python scripts/seed_demo.py
Safe to re-run: it re-links the demo Physical MSL to every enabled therapeutic area.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from digital_msl import store  # noqa: E402
from digital_msl.config import DemoModeInProduction, check_demo_guard, get_dmsl_settings  # noqa: E402


def main() -> int:
    try:
        check_demo_guard()
    except DemoModeInProduction as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    if not get_dmsl_settings().demo_enabled:
        print("Demo mode is off (set DIGITAL_MSL_DEMO_MODE=true). Nothing seeded.", file=sys.stderr)
        return 2
    store.init_store()
    emails = store.seed_demo_accounts()
    print("Seeded demo accounts:", ", ".join(emails))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
