"""Split compound answer-agent claims into atomic validated units (one numeric result each)."""
from __future__ import annotations

import re

from digital_msl.claim_quality import result_measures, text_has_study_result_value
from digital_msl.contracts import Claim, ClaimEvidence

_CLAUSE_SPLIT = re.compile(
    r";\s+|\.\s+(?=[A-Z0-9(])|\s+whereas\s+|\s*,\s+while\s+",
    re.I,
)
_ARM_RESULT = re.compile(
    r"\d+(?:[.,]\d+)?\s*letters?\s+in\s+[^,.;]+",
    re.I,
)


def _split_arm_results(text: str) -> list[str]:
    matches = list(_ARM_RESULT.finditer(text))
    if len(matches) < 2:
        return []
    prefix = text[: matches[0].start()].strip().rstrip(",")
    prefix = re.sub(r"\s+(was|were)\s*$", "", prefix, flags=re.I).strip()
    sentences: list[str] = []
    for match in matches:
        arm = match.group(0).strip(" ,")
        if prefix:
            body = f"{prefix} was {arm}" if not prefix.lower().endswith("was") else f"{prefix} {arm}"
        else:
            body = arm
        sentences.append(body if body.endswith(".") else f"{body}.")
    return sentences


def _measure_count(text: str) -> int:
    return sum(len(values) for values in result_measures(text).values())


def _clone_evidence(evidence: list[ClaimEvidence]) -> list[ClaimEvidence]:
    return [ev.model_copy(deep=True) for ev in evidence]


def atomize_compound_claims(claims: list[Claim]) -> list[Claim]:
    """One claim per distinct numeric result when the model merged several into one string."""
    expanded: list[Claim] = []
    for claim in claims:
        text = (claim.claim_text or "").strip()
        if not text or _measure_count(text) <= 1:
            expanded.append(claim)
            continue

        parts = [part.strip(" .") for part in _CLAUSE_SPLIT.split(text) if part.strip()]
        atomic = [part for part in parts if text_has_study_result_value(part)]
        if len(atomic) <= 1:
            atomic = _split_arm_results(text)
        if len(atomic) <= 1:
            expanded.append(claim)
            continue

        for part in atomic:
            sentence = part if part.endswith((".", "!", "?")) else f"{part}."
            expanded.append(
                Claim(
                    claim_id="C0",
                    claim_text=sentence,
                    evidence=_clone_evidence(claim.evidence),
                )
            )

    for index, claim in enumerate(expanded, start=1):
        claim.claim_id = f"C{index}"
    return expanded
