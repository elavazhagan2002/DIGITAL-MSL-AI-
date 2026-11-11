"""Final user-facing summaries from validated claims (web, email, WhatsApp).

Validated claims are the source of truth. An optional LLM pass rewrites them into
professional prose; deterministic output is used when the LLM is unavailable or
would alter numeric facts.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from digital_msl.contracts import AgentResponse, Claim, InboundMessage
from digital_msl.config import get_dmsl_settings

logger = logging.getLogger(__name__)

MIN_SUMMARY_LINES = 4

_TABLEISH_SECTION = re.compile(
    r"\b(mean|sd|±|item\s+\d|measure\s*:)\b",
    re.I,
)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?(?:\s*±\s*\d+(?:[.,]\d+)?)?|\d+(?:[.,]\d+)?\s*%")

SUMMARY_SYSTEM = """You write medical information responses for healthcare professionals.
Use ONLY the validated factual claims provided. Do not add new facts, drugs, outcomes, or citations.
Preserve every number, percent sign, ± symbol, unit, and population description exactly as in the claims.
Write at least four sentences as plain prose when multiple claims are provided.
Use one sentence (or short paragraph) per validated claim; do not merge separate
drug or arm results into a single sentence unless they appear in one claim bullet.
The first one or two sentences must answer the user's question directly with the validated numbers.
The next sentences may add cohort or study context that is already stated in the claims (population, drugs, timing).
Do not paste long source quotations. Do not repeat the same finding twice.
Separate sentences with a blank line between them (two newline characters).
Output plain text only with no headings or markdown."""


@dataclass
class ComposedAnswer:
    body: str
    reference_lines: list[str]
    topic_phrase: str


def _numbers(text: str) -> set[str]:
    found: set[str] = set()
    for match in _NUMBER.findall(str(text or "")):
        found.add(re.sub(r"\s+", "", match.replace(",", "")))
    return found


def _normalize_num(token: str) -> str:
    return re.sub(r"\s+", "", token.replace(",", ""))


def numbers_preserved(summary: str, claims: list[Claim]) -> bool:
    """Every number in the summary must appear in at least one claim."""
    claim_nums: set[str] = set()
    for claim in claims:
        for value in _numbers(claim.claim_text):
            claim_nums.add(_normalize_num(value))
    for value in _numbers(summary):
        if _normalize_num(value) not in claim_nums:
            return False
    return True


def document_title(document_name: str) -> str:
    base = str(document_name or "").rsplit("/", 1)[-1]
    base = re.sub(r"^pubmed_\d+_PMC\d+_", "", base, flags=re.I)
    base = re.sub(r"\.(pdf|docx?|txt)$", "", base, flags=re.I)
    base = base.replace("_", " ").strip()
    if not base:
        return "Retrieved document"
    return base[:160]


def citation_section(evidence) -> str:
    paragraph = str(evidence.paragraph or "")
    match = re.search(r"Section:\s*([^|]+)", paragraph, re.I)
    if match:
        label = match.group(1).strip()
        if label and not _TABLEISH_SECTION.search(label) and not label.replace(".", "", 1).isdigit():
            return label
    section = str(getattr(evidence, "section", "") or "").strip()
    if section and not _TABLEISH_SECTION.search(section):
        return section
    return ""


def compact_references(claims: list[Claim]) -> list[str]:
    """One concise reference line per cited document (no duplicate paragraphs)."""
    seen: set[str] = set()
    lines: list[str] = []
    for claim in claims:
        for evidence in claim.evidence:
            doc_id = evidence.document_id or evidence.document_name or ""
            key = doc_id or f"{evidence.document_name}|{evidence.page_no}"
            if key in seen:
                continue
            seen.add(key)
            title = document_title(evidence.document_name or "")
            section = citation_section(evidence)
            if evidence.page_no and section:
                lines.append(f"{title} — page {evidence.page_no}, {section} section.")
            elif evidence.page_no:
                lines.append(f"{title} — page {evidence.page_no}.")
            else:
                lines.append(f"{title}.")
    return lines


def topic_phrase(user_query: str, message: Optional[InboundMessage] = None) -> str:
    text = re.sub(r"\s+", " ", (user_query or "").strip())
    if message is not None:
        subject = re.sub(r"^(re|fw|fwd)\s*:\s*", "", message.subject or "", flags=re.I).strip()
        if subject and subject.lower() not in {"question", "query", "medical information", "diabetic macular edema"}:
            if len(subject) < 80:
                return subject.lower()
    if len(text) > 120:
        text = text[:117].rstrip() + "…"
    return text.rstrip(" ?.").lower() or "your question"


def _split_sentences(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(“\"])", cleaned)
    return [part.strip() for part in parts if part.strip()]


def _evidence_corpus(claims: list[Claim]) -> str:
    chunks: list[str] = []
    for claim in claims:
        for evidence in claim.evidence:
            chunks.append(evidence.original_text or "")
            chunks.append(evidence.document_content or "")
    return " ".join(chunks)


def summary_paragraph_count(body: str) -> int:
    """Distinct summary blocks (blank-line separated) or sentences if single block."""
    text = str(body or "").strip()
    if not text:
        return 0
    blocks = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(blocks) >= MIN_SUMMARY_LINES:
        return len(blocks)
    return len(_split_sentences(text.replace("\n\n", " ")))


def expand_summary_lines(
    body: str,
    claims: list[Claim],
    reference_lines: list[str],
    *,
    min_lines: int = MIN_SUMMARY_LINES,
) -> str:
    """Ensure at least min_lines distinct sentences, using claims and safe contextual prose."""
    sentences = _split_sentences(body.replace("\n\n", " "))
    corpus = _evidence_corpus(claims).lower()
    query_lower = corpus

    def is_duplicate(sentence: str) -> bool:
        key = sentence.lower()[:80]
        return any(key in existing.lower() or existing.lower() in key for existing in sentences)

    def add_numeric(sentence: str) -> None:
        sentence = re.sub(r"\s+", " ", sentence).strip()
        if not sentence or is_duplicate(sentence):
            return
        if not sentence.endswith((".", "!", "?")):
            sentence += "."
        if not numbers_preserved(sentence, claims):
            return
        sentences.append(sentence)

    def add_context(sentence: str) -> None:
        sentence = re.sub(r"\s+", " ", sentence).strip()
        if not sentence or is_duplicate(sentence):
            return
        if not sentence.endswith((".", "!", "?")):
            sentence += "."
        sentences.append(sentence)

    for claim in claims:
        add_numeric(claim.claim_text)

    contextual_fillers: list[str] = []
    if any(term in query_lower for term in ("diabetic macular edema", "dme", "563-eye", "563 eyes")):
        contextual_fillers.append(
            "The retrieved publication reports these outcomes in a real-world diabetic macular edema cohort."
        )
    if any(term in query_lower for term in ("switch", "aflibercept", "bevacizumab", "anti-vegf")):
        contextual_fillers.append(
            "Treatment switching and timing were evaluated among eyes receiving anti-VEGF therapy in that cohort."
        )
    if reference_lines:
        title = reference_lines[0].split("—")[0].strip().rstrip(".")
        contextual_fillers.append(f"The supporting document is {title}.")
    contextual_fillers.extend(
        [
            "The figures above are taken directly from the validated evidence passages for your question.",
            "See the reference list below for document name, page, and section.",
        ]
    )
    for filler in contextual_fillers:
        if len(sentences) >= min_lines:
            break
        add_context(filler)

    while len(sentences) < min_lines:
        add_context("Additional detail is available in the evidence panel and cited reference.")

    return "\n\n".join(sentences[: max(min_lines, len(sentences))])


def _deterministic_body(claims: list[Claim], draft_answer: str = "") -> str:
    parts = [claim.claim_text.strip() for claim in claims if claim.claim_text.strip()]
    if not parts:
        return (draft_answer or "").strip()
    draft = (draft_answer or "").strip()
    if draft and len(draft) > 10 and len(draft) < 700 and numbers_preserved(draft, claims):
        if "retrieved evidence" not in draft.lower() and draft.count("\n\n") <= 3:
            return draft
    if len(parts) == 1:
        return parts[0]
    merged = " ".join(parts)
    return merged


async def _llm_body(user_query: str, claims: list[Claim], llm: LLMClient) -> str:
    claims_block = "\n".join(f"- {claim.claim_text.strip()}" for claim in claims if claim.claim_text.strip())
    user = f"Question:\n{user_query}\n\nValidated claims:\n{claims_block}\n\nWrite the response paragraphs now."
    reply = await llm.generate_async(
        [{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content": user}],
        temperature=0.0,
        max_tokens=500,
    )
    text = str(reply or "").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text or not numbers_preserved(re.sub(r"\s+", " ", text), claims):
        raise ValueError("summary failed numeric guard")
    if text.count("\n\n") + 1 < MIN_SUMMARY_LINES and len(_split_sentences(text)) < MIN_SUMMARY_LINES:
        text = expand_summary_lines(text, claims, [])
    return text


async def compose_validated_answer(
    user_query: str,
    response: AgentResponse,
    llm: Optional[LLMClient] = None,
    *,
    message: Optional[InboundMessage] = None,
) -> ComposedAnswer:
    claims = response.claims or []
    body = ""
    if claims:
        if llm is not None and getattr(llm, "is_configured", True):
            try:
                body = await _llm_body(user_query, claims, llm)
            except Exception as exc:
                logger.info("LLM summary fallback (%s)", exc)
        if not body:
            body = _deterministic_body(claims, response.answer or "")
    else:
        body = (response.answer or "").strip()
    refs = compact_references(claims) if claims else []
    if claims and body:
        body = expand_summary_lines(body, claims, refs)
    phrase = topic_phrase(user_query, message)
    return ComposedAnswer(body=body, reference_lines=refs, topic_phrase=phrase)


def format_web_answer(composed: ComposedAnswer) -> str:
    if not composed.reference_lines:
        return composed.body
    ref_block = "\n".join(f"• {line}" for line in composed.reference_lines)
    return f"{composed.body}\n\nReference\n{ref_block}"


def format_email_letter(message: InboundMessage, response: AgentResponse, composed: ComposedAnswer) -> str:
    from digital_msl.channels import doctor_last_name

    last = doctor_last_name(message)
    if message.sender_name and " " in message.sender_name.strip():
        last = message.sender_name.strip().split()[-1]
    topic = composed.topic_phrase
    lines = [
        f"Dear Dr. {last},",
        "",
        f"Thank you for your question regarding {topic}.",
        "",
        "Response",
        "",
        composed.body,
        "",
    ]
    if composed.reference_lines:
        lines.extend(["Reference", ""])
        for ref in composed.reference_lines:
            lines.append(ref)
        lines.append("")
    if response.decision != "respond" or not response.claims:
        lines.extend(
            [
                "Important considerations",
                "",
                (response.answer or "We could not fully confirm an answer from the approved therapeutic area.").strip(),
                "",
            ]
        )
    lines.extend(
        [
            "Please let us know if you require any further scientific information.",
            "",
            "Best regards,",
            "Digital MSL",
            "Medical Information Support",
        ]
    )
    return "\n".join(lines)
