"""Evidence and claim checker for sent answers (kept in the repo, used by the live suite and by tests).

For one reply: split the answer text into claims (sentences), find the support in the cited passages and
report claims with no support, claims whose numbers / names differ from the source, and citation problems.

A claim passes only if its text is a verbatim span of a cited passage, OR every number (with unit), date,
acronym and capitalised name in it appears in a cited passage. Replies with no sources are never answers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from digital_msl import grounding

_REFERENCE_HEAD = re.compile(r"^\s*(reference|references|sources?)\s*$", re.I)
_LEADIN = re.compile(r"^from the approved sources:?$", re.I)
_NAME = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]{2,}\b")
_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9\-]{2,}\b")
_DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")


@dataclass
class ClaimResult:
    text: str
    supported: bool
    verbatim: bool = False
    missing: list[str] = field(default_factory=list)


@dataclass
class CheckReport:
    claims: list[ClaimResult] = field(default_factory=list)
    citation_problems: list[str] = field(default_factory=list)
    has_sources: bool = False
    message_type: str = ""

    @property
    def supported(self) -> int:
        return sum(1 for c in self.claims if c.supported)

    @property
    def unsupported(self) -> list[ClaimResult]:
        return [c for c in self.claims if not c.supported]

    @property
    def mismatched(self) -> list[ClaimResult]:
        return [c for c in self.claims if not c.supported and any(m.startswith("number:") for m in c.missing)]

    @property
    def verbatim_share(self) -> float:
        return (sum(1 for c in self.claims if c.verbatim) / len(self.claims)) if self.claims else 0.0

    @property
    def passed(self) -> bool:
        return bool(self.claims) and not self.unsupported and not self.citation_problems and self.has_sources

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_type": self.message_type,
            "has_sources": self.has_sources,
            "claims": [{"text": c.text, "supported": c.supported, "verbatim": c.verbatim, "missing": c.missing} for c in self.claims],
            "citation_problems": self.citation_problems,
            "supported": self.supported,
            "unsupported": len(self.unsupported),
            "mismatched_numbers": len(self.mismatched),
            "verbatim_share": round(self.verbatim_share, 2),
            "passed": self.passed,
        }


def split_claims(answer: str) -> list[str]:
    """Sentences of the answer body, without the reference block, lead-in or citation markers."""
    body: list[str] = []
    for line in str(answer or "").splitlines():
        if _REFERENCE_HEAD.match(line):
            break
        body.append(line)
    text = re.sub(r"\[\d+\]", "", "\n".join(body))
    text = text.replace("“", '"').replace("”", '"')
    claims: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        block = re.sub(r"\s+", " ", block).strip().strip('"').strip()
        if not block or _LEADIN.match(block):
            continue
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"])", block):
            sentence = sentence.strip().strip('"').strip()
            if len(sentence) >= 12:
                claims.append(sentence)
    return claims


def _items(claim: str) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for token in grounding._numbers_with_units(claim):
        items.append(("number", token))
    for date in _DATE.findall(claim):
        items.append(("date", date))
    for acronym in set(_ACRONYM.findall(claim)):
        items.append(("name", acronym))
    for name in set(_NAME.findall(claim)):
        items.append(("name", name))
    return items


def check_claim(claim: str, passages: list[str]) -> ClaimResult:
    if any(grounding.occurs_exactly(claim, p) for p in passages):
        return ClaimResult(claim, True, verbatim=True)
    corpus = grounding._norm(" ".join(passages))
    missing: list[str] = []
    for kind, item in _items(claim):
        needle = grounding._norm(item)
        if kind == "number":
            number = needle.split(" ")[0]
            ok = needle in corpus or (" " not in needle and re.search(rf"(?<![\w.]){re.escape(number)}(?![\w.])", corpus))
        else:
            ok = needle in corpus
        if not ok:
            missing.append(f"{kind}:{item}")
    # A claim made only of words that never appear together in a passage is not support either.
    terms = [t for t in re.findall(r"[a-z]{4,}", grounding._norm(claim))]
    overlap = (sum(1 for t in terms if t in corpus) / len(terms)) if terms else 0.0
    if overlap < 0.6:
        missing.append(f"text:{overlap:.0%} of its words appear in the cited passages")
    return ClaimResult(claim, not missing, verbatim=False, missing=missing)


def check_response(response: dict[str, Any]) -> CheckReport:
    """``response`` is an AgentResponse dict as returned to the web portal."""
    report = CheckReport(message_type=str(response.get("message_type") or ""))
    claims = response.get("claims") or []
    passages: list[str] = []
    cited_names: list[str] = []
    for claim in claims:
        for ev in claim.get("evidence") or []:
            if ev.get("document_content"):
                passages.append(str(ev["document_content"]))
            cited_names.append(str(ev.get("document_name") or ""))
    report.has_sources = bool(passages)
    if report.message_type == "answer" and not report.has_sources:
        report.citation_problems.append("answer without any source")
    if report.message_type != "answer":
        return report
    answer = str(response.get("answer") or "")
    for text in split_claims(answer):
        report.claims.append(check_claim(text, passages))
    if not re.search(r"\[\d+\]|^\s*(reference|sources?)\b", answer, re.I | re.M):
        report.citation_problems.append("no citation marker or reference block in the answer text")
    if any(not name.strip() for name in cited_names):
        report.citation_problems.append("a citation has no document name")
    return report
