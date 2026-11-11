# fixAB progress (fork 1: items A and B)
- [ ] read code
- [x] A implemented (ChatNotice.tsx, ui/Modal.tsx, lib/noticeAck.ts, DigitalMSLChat edits); tests not yet written
- [x] fork1 backend 8098 (db dmsl_fork1.db seeded by scripts/seed_registry_demo.py) + vite 5174 running
- [x] B implemented (DoctorRegistryPage rewrite, ui/Drawer.tsx, ui/Popover.tsx, lib/areaNames.ts + test); e2e specs + screenshots next
- [x] notice.spec.ts written; running
- [x] A: 7/7 notice checks pass; taking screenshots
- [x] A screenshots (light+dark x4 sizes); doctors.spec.ts written, first run next (note: machine under load from other forks; retries:1 in specs)
- [x] overlays (Drawer/Modal/Popover) moved to a body portal (cause: fixed overlays were captured by an ancestor); running full fork-1 specs + layout/behaviour/chat
- [x] fixed mojibake in App.tsx/doctors.spec.ts (PowerShell re-save), error-state counts; clean full run starting (notice + doctors + layout + behaviour + chat)
