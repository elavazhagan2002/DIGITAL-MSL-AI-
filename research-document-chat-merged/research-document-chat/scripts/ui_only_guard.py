"""UI-only guard: fails when any file outside the UI layer changed since the baseline snapshot.

The project is not a git repository, so the guard compares SHA-256 hashes against a manifest.

    python scripts/ui_only_guard.py --snapshot   # record the baseline (docs/ui-baseline/forbidden-manifest.json)
    python scripts/ui_only_guard.py --check      # exit 1 and list changed / added / removed forbidden files

Forbidden = backend code and data models, API routes, API client / service modules, auth and session logic,
the fetch wrapper, env files, backend tests, requirements and the API config / types.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs" / "ui-baseline" / "forbidden-manifest.json"

# (directory or file relative to ROOT, recurse?)
FORBIDDEN = [
    ("backend", True),
    ("tests", True),
    ("scripts", True),
    ("frontend/src/services", True),
    ("frontend/src/types", True),
    ("frontend/src/config/api.ts", False),
    ("frontend/src/portal/AuthContext.tsx", False),
    ("frontend/src/lib/queryCache.ts", False),  # request cache: carries request/retry behaviour
    ("frontend/src/hooks/useAppData.ts", False),  # data hook: endpoints and query keys
    ("frontend/.env", False),
    ("frontend/.env.local", False),
    ("frontend/.env.example", False),
    ("frontend/.env.production", False),
    (".env.example", False),
]

EXCLUDE_DIR_NAMES = {"venv", ".venv", "__pycache__", "logs", "uploads", "node_modules", ".pytest_cache", "traces", "golden_cache"}
EXCLUDE_SUFFIXES = {".db", ".db-journal", ".sqlite", ".pyc", ".log"}
EXCLUDE_NAMES = {".dmsl_data_key", "forbidden-manifest.json", "ui_only_guard.py", "diff_network_contract.py"}  # the UI tooling itself is not forbidden


def _excluded(path: Path) -> bool:
    if path.name in EXCLUDE_NAMES or path.suffix in EXCLUDE_SUFFIXES:
        return True
    return any(part in EXCLUDE_DIR_NAMES for part in path.relative_to(ROOT).parts)


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect() -> dict[str, str]:
    files: dict[str, str] = {}
    for rel, recurse in FORBIDDEN:
        target = ROOT / rel
        if target.is_file():
            candidates = [target]
        elif target.is_dir() and recurse:
            candidates = [p for p in target.rglob("*") if p.is_file()]
        else:
            candidates = []
        for path in candidates:
            if _excluded(path):
                continue
            files[path.relative_to(ROOT).as_posix()] = _digest(path)
    return dict(sorted(files.items()))


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    current = collect()
    if args.snapshot:
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps({"files": current}, indent=1), encoding="utf-8")
        print(f"Snapshot written: {len(current)} forbidden files -> {MANIFEST.relative_to(ROOT)}")
        return 0
    if not MANIFEST.exists():
        print("No baseline manifest. Run with --snapshot first.", file=sys.stderr)
        return 2
    baseline = json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]
    changed = sorted(f for f in current if f in baseline and current[f] != baseline[f])
    added = sorted(set(current) - set(baseline))
    removed = sorted(set(baseline) - set(current))
    if not (changed or added or removed):
        print(f"ui-only-guard OK: {len(current)} forbidden files unchanged.")
        return 0
    print("ui-only-guard FAILED: forbidden files differ from the baseline.", file=sys.stderr)
    for label, items in (("changed", changed), ("added", added), ("removed", removed)):
        for item in items:
            print(f"  {label}: {item}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
