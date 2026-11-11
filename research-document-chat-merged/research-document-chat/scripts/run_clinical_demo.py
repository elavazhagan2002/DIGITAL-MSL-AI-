"""Run the 10 demo questions through the real pipeline and print what the doctor would see.

Needs a TEST repository with approved sample documents (see tests/golden/retina_demo_questions.json) and
the repository id on the approved list. Read-only against IKM (retrieval only). Never use real patient data.

    DIGITAL_MSL_DEMO_MODE=true DIGITAL_MSL_APPROVED_REPOSITORY_IDS=<id> python scripts/run_clinical_demo.py <id>

Writes docs/clinical_demo_results.md. Skips (exit 2) when no repository id is given.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from digital_msl import store  # noqa: E402
from digital_msl.agents.query_agent import QueryAgent  # noqa: E402
from digital_msl.config import get_dmsl_settings  # noqa: E402
from digital_msl.contracts import InboundMessage  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("Skipped: pass the TEST repository id as the first argument.", file=sys.stderr)
        return 2
    repository_id = sys.argv[1].strip()
    approved = get_dmsl_settings().approved_repositories()
    if "*" not in approved and repository_id not in approved:
        print("Skipped: the repository id is not on DIGITAL_MSL_APPROVED_REPOSITORY_IDS.", file=sys.stderr)
        return 2
    store.init_store()
    doctor = store.get_portal_user_by_email("doctor@demo.example")
    if not doctor:
        print("Skipped: demo accounts are missing (run scripts/seed_demo.py with demo mode on).", file=sys.stderr)
        return 2
    store.assign_doctor_repositories(doctor["user_id"], [repository_id])
    spec = json.loads((ROOT / "tests/golden/retina_demo_questions.json").read_text(encoding="utf-8"))
    agent = QueryAgent()
    lines = ["# Clinical demo run", "", f"Repository: `{repository_id}`", ""]
    for item in spec["questions"]:
        message = InboundMessage(channel="web", repository_id=repository_id, user_id=doctor["user_id"], message_text=item["text"])
        response = asyncio.run(agent.handle(message))
        ok = response.message_type == item["expect"]["message_type"]
        lines += [
            f"## {item['id']} ({item['kind']}): {'MATCH' if ok else 'DIFFERS'}",
            f"- Question: {item['text']}",
            f"- Intent: {response.intent}; type: {response.message_type}; retrieval: {response.retrieval_ran}; LLM: {response.llm_called}",
            f"- Case: {response.msl_case_id or 'none'}; reason codes: {', '.join(response.reason_codes) or 'none'}",
            "- Reply:",
            "",
            *(f"  > {line}" for line in (response.answer or "").splitlines()),
            "",
        ]
    out = ROOT / "docs" / "clinical_demo_results.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
