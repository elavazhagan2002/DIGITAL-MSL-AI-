# UI baseline progress

- [x] Step 1: scripts/ui_only_guard.py written; baseline snapshot taken and --check passes; npm scripts ui-only-guard + ui-lint added.
- NOTE: the reference kit digital_msl_ui_reference/ is NOT on this machine (searched Downloads, Desktop, Documents, user profile, Claude temp). Working source of truth: brief token list + frontend/src/index.css + tailwind.config.js.
- NOTE: not a git repository, so the guard uses a SHA-256 manifest. The guard's forbidden set also includes scripts/.
- [x] Step 2: frontend/scripts/ui-lint.mjs + ui-lint-allow.json; baseline in lint-baseline.txt: 19 violations {important:7, fn:10, hex:2}, palette 0. Probe test confirmed the rules fire.
- [x] Step 3: screen-inventory.md written (PRESENT / NOT PRESENT).
