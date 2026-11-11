# Digital MSL UI reference (Sciagen)

Source of truth for the signed-in app look. Supplied by the product owner on 2026-11-09.

- `ui-reference.css`: every token (light and dark) plus the app-shell grid and all base components. Verbatim copy.
- `shell.html` and `kit.html`: the markup the CSS was written for (shell = Doctor chat; kit = buttons, inputs, role cards, badges, alerts, toast, empty/error, table, skeleton).
- The screenshots the owner attached (`ref-shell-light/dark`, `ref-kit-light/dark`, `ref-shell-mobile-light`) are not stored as files here; they exist only in the chat. Compare against them or regenerate them by opening `shell.html` and `kit.html` (add `?theme=dark` for dark) next to `ui-reference.css` and `sciagen-logo-light.png` saved as `sciagen_logo_white.png`.

The app's tokens live in `frontend/src/index.css`; they must match `ui-reference.css`.
