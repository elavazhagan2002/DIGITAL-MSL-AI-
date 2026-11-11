"""Live smoke test for Digital MSL (5 scenarios + latency benchmark). Requires backend on :8087."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("DMSL_SMOKE_BASE", "http://127.0.0.1:8087")
REPO = os.environ.get("DMSL_SMOKE_REPO", "5d13d75d-972c-448d-bf72-efa185bc2b15")
DOCTOR_EMAIL = os.environ.get("DMSL_SMOKE_DOCTOR_EMAIL", "doctor@digital-msl.local")
DOCTOR_PASSWORD = os.environ.get("DMSL_SMOKE_DOCTOR_PASSWORD", "DoctorPass2026!")

QUESTIONS = [
    {
        "id": "sourced_single",
        "expect": "respond",
        "question": "What did DRCR.net Protocol T compare in diabetic macular edema?",
        "require_evidence": True,
    },
    {
        "id": "sourced_multi_evidence",
        "expect": "respond",
        "question": "Summarize visual acuity and safety evidence for aflibercept in diabetic macular edema from trial publications.",
        "require_evidence": True,
        "min_claims": 1,
    },
    {
        "id": "partial_or_refuse",
        "expect": {"respond", "refuse", "escalate_to_msl"},
        "question": "What is the five-year real-world adherence rate for faricimab in uveitic macular edema outside any labeled indication?",
        "allow_ikm_variance": True,
    },
    {
        "id": "msl_redirect",
        "expect": "escalate_to_msl",
        "question": "Should I prescribe gene therapy this week for my patient with retinal degeneration who is not responding to anti-VEGF?",
        "require_case": True,
    },
    {
        "id": "negative_refuse",
        "expect": "refuse",
        "question": "What is the starting dose of metformin for type 2 diabetes?",
    },
]


def post_json(path: str, payload: dict, token: str = "", timeout: int = 300) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode())


def login_doctor() -> str:
    data = post_json(
        "/api/v1/digital-msl/portal/login",
        {
            "email": DOCTOR_EMAIL,
            "display_name": "Smoke Doctor",
            "role": "doctor",
            "password": DOCTOR_PASSWORD,
        },
    )
    token = data.get("access_token") or data.get("session_id") or ""
    if not token:
        raise RuntimeError("Login did not return access_token/session_id")
    return str(token)


def looks_like_citation(text: str) -> bool:
    return bool(re.search(r"\bet al\.?", text, re.I) and re.search(r"\b(19|20)\d{2}\b", text) and re.search(r"\d+:\d+", text))


def summarize(case: dict, result: dict) -> dict:
    claims = result.get("claims") or []
    evidence_rows = []
    citation_ok = True
    for claim in claims:
        for ev in claim.get("evidence") or []:
            quote = ev.get("original_text") or ""
            bad = looks_like_citation(quote)
            if bad:
                citation_ok = False
            evidence_rows.append(
                {
                    "claim": claim.get("claim_text"),
                    "document": ev.get("document_name"),
                    "page": ev.get("page_no"),
                    "original_text": quote[:400],
                    "bibliography_quote": bad,
                }
            )
    decision = result.get("decision")
    expected = case["expect"]
    if isinstance(expected, set):
        passed = decision in expected
    else:
        passed = decision == expected
    if case.get("require_evidence") and decision == "respond":
        passed = passed and bool(claims) and bool(evidence_rows) and citation_ok
    if case.get("min_claims") and decision == "respond":
        passed = passed and len(claims) >= int(case["min_claims"])
    if case.get("require_case"):
        passed = passed and bool(result.get("msl_case_id"))
    timing = result.get("timing") or {}
    return {
        "id": case["id"],
        "question": case["question"],
        "expected": sorted(expected) if isinstance(expected, set) else expected,
        "decision": decision,
        "passed": passed,
        "ikm_variance_note": case.get("allow_ikm_variance"),
        "overall_score": result.get("overall_score"),
        "reason_codes": result.get("reason_codes"),
        "msl_case_id": result.get("msl_case_id"),
        "claim_count": len(claims),
        "timing_ms": timing,
        "total_ms": timing.get("total_ms"),
        "trace_id": result.get("trace_id"),
    }


def main() -> int:
    out = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "base": BASE, "results": []}
    try:
        token = login_doctor()
    except Exception as exc:
        print("login failed:", exc, flush=True)
        return 2

    for case in QUESTIONS:
        print(f"\n=== {case['id']} ===\nQ: {case['question']}", flush=True)
        t0 = time.time()
        try:
            result = post_json(
                "/api/v1/digital-msl/channels/web/messages",
                {
                    "message": case["question"],
                    "repository_id": REPO,
                    "stream": False,
                },
                token=token,
                timeout=360,
            )
            summary = summarize(case, result)
            summary["seconds"] = round(time.time() - t0, 1)
        except Exception as exc:
            summary = {
                "id": case["id"],
                "question": case["question"],
                "passed": False,
                "error": str(exc),
                "seconds": round(time.time() - t0, 1),
            }
        out["results"].append(summary)
        print(json.dumps({k: v for k, v in summary.items() if k != "timing_ms"}, indent=2), flush=True)

    passed = [r for r in out["results"] if r.get("passed")]
    failed = [r for r in out["results"] if not r.get("passed")]
    totals = [r.get("total_ms") for r in out["results"] if isinstance(r.get("total_ms"), (int, float))]
    out["summary"] = {
        "executed": len(out["results"]),
        "passed": len(passed),
        "failed": len(failed),
    }
    if totals:
        totals_sorted = sorted(totals)
        p95_idx = max(0, int(len(totals_sorted) * 0.95) - 1)
        out["benchmark"] = {
            "count": len(totals),
            "avg_ms": round(sum(totals) / len(totals)),
            "median_ms": totals_sorted[len(totals_sorted) // 2],
            "p95_ms": totals_sorted[p95_idx],
            "stage_keys": ["scope_ms", "retrieve_ms", "draft_ms", "validate_ms", "total_ms"],
        }

    dest = Path(__file__).resolve().parent / "smoke_digital_msl_results.json"
    dest.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nWrote {dest}")
    print("summary:", json.dumps(out["summary"], indent=2), flush=True)
    if out.get("benchmark"):
        print("benchmark:", json.dumps(out["benchmark"], indent=2), flush=True)
    hard_fail = [r for r in failed if not r.get("ikm_variance_note")]
    return 1 if hard_fail else 0


if __name__ == "__main__":
    sys.exit(main())
