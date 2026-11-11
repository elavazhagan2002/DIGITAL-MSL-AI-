"""Write docs/screenshots/fixAB/INDEX.md from the PNG files in that folder (items A and B)."""
from __future__ import annotations

from pathlib import Path

root = Path(__file__).resolve().parent.parent / "docs" / "screenshots" / "fixAB"
files = sorted(p.name for p in root.rglob("*.png"))
groups = {
    "A. Doctor chat: notice": [f for f in files if f.startswith("chat-")],
    "B. Registered Doctors: Physical MSL": [f for f in files if f.startswith("doctors-physical-msl")],
    "B. Registered Doctors: Administrator": [f for f in files if f.startswith("doctors-administrator")],
    "Before the redesign (evidence)": [f for f in files if f.startswith("doctors-") and "before" in f],
}
lines = ["# Screenshots: items A and B", "", f"{len(files)} files. Names: `<screen>-<mode>-<width>x<height>.png`.", ""]
for title, names in groups.items():
    lines += [f"## {title} ({len(names)})", ""] + [f"- {n}" for n in names] + [""]
(root / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
print(f"{len(files)} screenshots indexed")
