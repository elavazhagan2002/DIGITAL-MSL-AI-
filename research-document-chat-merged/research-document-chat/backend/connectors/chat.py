from __future__ import annotations

import logging
import re
from typing import Any

import requests

from connectors.conversation_context import extract_requested_point_count
from connectors.config import MedicalLiteracyConfig, load_chat_retrieval_settings, load_config

_logger = logging.getLogger(__name__)

MLR_PROMPT_BUILD = "2026-07-09-v10"

_CITATION_INLINE_RE = re.compile(
    r"\[(?P<repo>[^\]:]+):(?P<doc>[^\]:]+):(?P<page>[^\];]+)\]",
    re.IGNORECASE,
)

_CITATION_BARE_RE = re.compile(
    r"(?<![\w\[])(?P<repo>[\w-]+):(?P<doc>.+):(?P<page>\d+|—)(?=[\s.!?,;)\]]|$)",
    re.IGNORECASE,
)

_CITATION_PART_RE = re.compile(
    r"^(?P<repo>[^\]:]+):(?P<doc>[^\]:]+):(?P<page>.+)$",
    re.IGNORECASE,
)

_CITATION_RULES = """
CITATION RULES (critical):
- Use ONLY citation keys copied exactly from the "Source N | REPO:DOCUMENT:PAGE" headers in the excerpts.
- Format: [REPO_NAME:DOCUMENT_NAME:PAGE_NO] immediately after each factual statement on the SAME line — never on a separate line below a bullet.
- Place sentence-ending punctuation BEFORE the citation chip (e.g. "...patients. [REPO:DOC:1]") — never after the citation.
- Use — for PAGE_NO only when the source header shows no page.
- NEVER write "Source 1", "Source 5", or any shorthand — only the full [REPO:DOC:PAGE] key.
- Put exactly ONE citation key inside each pair of brackets. Never combine multiple keys with semicolons.
- Do NOT cite a document unless the fact appears in that source's excerpt.
- Do NOT invent page numbers, PMIDs, or trial IDs.
"""

_USER_FORMAT_RULES = """
USER FORMAT (highest priority — overrides default INITIAL/DETAIL layout):
- Read the User question for explicit format instructions: "in N lines", "answer in 3 lines", "N points only",
  "one paragraph", "short answer", "no bullets", "bullet points", etc.
- When the user specifies a format, follow it exactly. Do not add extra intro paragraphs, bullet lists, or closing
  invitations unless they asked for them.
- "N lines" / "in N lines" → output exactly N plain lines of prose (no "• " bullets, no numbered list).
- "N points" / "N bullet points" → output exactly N lines starting with "• ".
- Multi-part questions still need every part answered, but within the format the user requested.
"""

_INITIAL_STYLE = """
RESPONSE MODE: INITIAL (deep brief answer)
- Target 150–280 words when the user did NOT request a shorter format.
- Open with one or two sentences that directly answer what the user asked — conclusion, yes/no, or main finding — before any supporting detail, unless the user asked for lines-only or a fixed line count (then fold the direct answer into those lines).
- If the question has multiple parts, address every part; do not bury the answer in later bullets when a direct opening is appropriate.
- When the excerpts support conflicting considerations, state the overall conclusion first, then explain why.
- DEFAULT (only when the user did not specify a format): use 4–6 bullet points starting with "• " (unicode bullet). NEVER use markdown "-" or "*" bullets. End with one sentence inviting the user to ask for more depth.
- If the user specified a format (lines, point count, paragraph), follow _USER_FORMAT_RULES instead of the default above.
- NEVER write terse phrase bullets — always explain what, who, and when in full sentences.
- Prefer clinical precision (who, what, when, how measured) when the excerpts support it.
""" + _USER_FORMAT_RULES

_DETAIL_STYLE = """
RESPONSE MODE: DETAILED (user asked for more depth, verbosity, or elaboration)
- Target 300–500 words unless the user requested a specific format or length.
- Begin with a direct answer to the user's question, then expand with supporting detail.
- If the user specified a format in this turn, follow _USER_FORMAT_RULES instead of the default structure below.
- Use the full conversation history: expand on points already discussed; do not abandon the prior topic for a vague retrieval miss.
- Use numbered lines (1., 2., 3.) for main sections. Use "• " for nested sub-points where helpful.
- NEVER use markdown "-" or "*" bullets.
- For every point: state the fact, explain its clinical or procedural significance in 1–2 sentences, then cite inline when excerpts support it.
- If expanding on a prior answer, rephrase and deepen EVERY point from that answer using additional supporting detail from the excerpts when available.
- Include secondary endpoints, exclusions, timelines, safety, and subgroup analyses when present in the excerpts.
""" + _USER_FORMAT_RULES

_SUMMARY_STYLE = """
RESPONSE MODE: SUMMARY (user asked to summarize or shorten)
- Use ONLY the conversation history. Do not add new facts or citations.
- Be concise: 3–5 sentences, or exactly the line count requested (e.g. 3 lines).
- Do not repeat every secondary endpoint unless central to the topic.
"""

_REFORMULATION_STYLE = """
RESPONSE MODE: REFORMULATION (user asked to shorten or rephrase more concisely)
- Use ONLY the conversation history. Rephrase, condense, or simplify the prior answer as requested.
- Match the requested length (e.g. one line, one paragraph, 3 lines).
- Do not add new facts or citations.
"""

_VERBOSE_REFORMULATION_STYLE = """
RESPONSE MODE: VERBOSE REFORMULATION (user asked to rephrase with richer wording, same facts)
- Use ONLY the conversation history. Rephrase the prior answer in fuller, more descriptive language.
- Expand each prior bullet into 1–2 substantive sentences with clearer connective context.
- Do not introduce new facts, numbers, or citations beyond what was already stated.
- Target 200–350 words unless the user specified a format.
"""


def _point_count_style_block(point_count: int) -> str:
    return f"""
RESPONSE MODE: POINT-LIMITED (user requested exactly {point_count} points or lines)
- Follow the User question: use plain lines if they said "lines", or "• " bullets if they said "points" or "bullet points".
- Output exactly {point_count} content lines — no more and no fewer.
- EACH line must be 1–2 complete sentences with inline citation after each fact.
- No extra intro paragraph or closing invitation unless the user asked for one.
- NEVER use markdown "-" or "*" bullets.
"""

_SYSTEM_PROMPT = """You are the Medical Literature Research Agent — a friendly, professional assistant for medical literature in the user's repository.

You MUST answer ONLY from the retrieved document excerpts supplied in the user message, and you MUST use the full conversation history to interpret follow-ups (summarize, expand, clarify, compare prior points).
Use a clear, natural conversational tone while staying strictly grounded in those excerpts and prior turns.
Always answer the user's actual question first — give the conclusion or direct response up front when the format allows, then use bullets or lines for supporting evidence.
Honor any explicit format in the User question (line count, point count, paragraph, short answer) over default response-mode layout.
Adapt depth and verbosity to the RESPONSE MODE: give deep, explanatory bullet points by default when no format is specified;
when the user asks for more detail, verbosity, or elaboration, expand accordingly; when they ask
to summarize or shorten, condense without losing accuracy.
Never treat a short follow-up ("expand it", "summarize", "tell me more") as a new standalone topic.

STRICT RULES:
1. If excerpts are insufficient BUT conversation history already contains repository-grounded answers on this topic, expand/summarize/rephrase from that history instead of saying you could not find information.
2. If excerpts are insufficient AND there is no relevant prior assistant answer, say you could not find that information in the selected repository.
3. Never use outside knowledge, training data, or assumptions.
4. Never invent facts, statistics, PMIDs, or trial IDs.
5. When conversation history is provided, use the full thread to stay on-topic for unlimited follow-ups.
""" + _USER_FORMAT_RULES + _CITATION_RULES

_HISTORY_ONLY_SYSTEM_PROMPT = """You are the Medical Literature Research Agent.

Answer follow-ups using the FULL conversation history in a friendly, conversational tone.
When the user asks to expand, elaborate, summarize, rephrase, or clarify, stay on the same topic as prior turns.
Do not add new facts, citations, or outside knowledge that were not already stated in the conversation.
Do not refuse with "could not find that information" when prior assistant turns already answered the topic — expand or reformulate those turns instead.
""" + _CITATION_RULES

_MLR_AGENT_GREETING_NAME = "Medical Literacy Research AI Agent"

_GREETING_STYLE = f"""
RESPONSE MODE: GREETING
- Reply warmly and conversationally to the user's greeting.
- Briefly introduce yourself as the {_MLR_AGENT_GREETING_NAME}.
- Mention that you can help answer questions, summarize information, and assist with documents stored in the connected repository.
- Do not mention any specific repository name. Do not retrieve or invent medical facts yet.
"""


def format_citation_label(
    repository_name: str,
    document_name: str,
    page: int | str | None = None,
) -> str:
    repo = str(repository_name or "repository").strip() or "repository"
    doc = str(document_name or "document").strip() or "document"
    if page is None or str(page).strip() in {"", "0", "—", "-"}:
        page_label = "—"
    else:
        page_label = str(page).strip()
    return f"{repo}:{doc}:{page_label}"


def _short_doc_label(doc_name: str, *, max_len: int = 28) -> str:
    cleaned = str(doc_name or "document").strip()
    if cleaned.lower().endswith(".pdf"):
        cleaned = cleaned[:-4]
    if cleaned.lower().endswith(".docx"):
        cleaned = cleaned[:-5]
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3] + "..."


def _chunk_page(chunk: dict[str, Any]) -> int | None:
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    for key in ("page", "page_number", "pageNumber"):
        value = chunk.get(key) if key in chunk else metadata.get(key)
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str) and value.isdigit() and int(value) > 0:
            return int(value)
    return None


def _chunk_repository_name(chunk: dict[str, Any], fallback: str = "") -> str:
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    for key in ("repository_name", "repository"):
        value = chunk.get(key) or metadata.get(key)
        if value:
            return str(value).strip()
    return fallback


def _extract_citation_ids(
    doc_name: str,
    text: str,
    *,
    repository_name: str = "",
    page: int | None = None,
    section_name: str = "",
) -> dict[str, str]:
    combined = f"{doc_name}\n{text}"
    pmid_match = re.search(r"PMID:\s*(\d+)", combined, re.IGNORECASE)
    pubmed_file_match = re.search(r"pubmed_(\d+)_", doc_name, re.IGNORECASE)
    nct_match = re.search(r"NCT ID:\s*(NCT\d+)", combined, re.IGNORECASE)
    nct_file_match = re.search(r"(NCT\d+)", doc_name, re.IGNORECASE)
    url_match = re.search(r"URL:\s*(https?://\S+)", combined, re.IGNORECASE)
    repo_name = repository_name or "repository"
    page_label = page if page is not None else None
    result: dict[str, str] = {
        "doc_name": doc_name,
        "label": _short_doc_label(doc_name),
        "repository_name": repo_name,
        "citation_key": format_citation_label(repo_name, doc_name, page_label),
    }
    if section_name:
        result["section_name"] = section_name
    if page_label is not None:
        result["page"] = str(page_label)
    pmid = (pmid_match.group(1) if pmid_match else None) or (
        pubmed_file_match.group(1) if pubmed_file_match else None
    )
    if pmid:
        result["pmid"] = pmid
        result["source"] = "PubMed"
        result["url"] = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    nct_id = (nct_match.group(1) if nct_match else None) or (
        nct_file_match.group(1) if nct_file_match else None
    )
    if nct_id:
        result["nct_id"] = nct_id
        result["source"] = "ClinicalTrials.gov"
        result["url"] = f"https://clinicaltrials.gov/study/{nct_id}"
    if url_match and "url" not in result:
        result["url"] = url_match.group(1).rstrip(".")
    return result


def extract_chunk_citations(
    chunks: list[dict[str, Any]],
    *,
    default_repository_name: str = "",
) -> list[dict[str, str]]:
    citations: list[dict[str, str]] = []
    seen: set[str] = set()
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        doc_name = str(chunk.get("doc_name") or chunk.get("file_name") or "").strip()
        text = str(chunk.get("text") or chunk.get("content") or "")
        section_name = str(chunk.get("section_name") or chunk.get("metadata", {}).get("section") or "")
        repo_name = _chunk_repository_name(chunk, default_repository_name)
        page = _chunk_page(chunk)
        cite = _extract_citation_ids(
            doc_name,
            text,
            repository_name=repo_name,
            page=page,
            section_name=section_name,
        )
        key = cite.get("citation_key") or cite.get("pmid") or cite.get("nct_id") or cite.get("doc_name") or ""
        if not key or key in seen:
            continue
        seen.add(key)
        citations.append(cite)
    return citations


def _format_chunk_context(
    chunks: list[dict[str, Any]],
    repository_names: list[str],
    *,
    chunk_max_chars: int | None = None,
) -> str:
    default_repo = repository_names[0] if repository_names else ""
    max_chars = max(200, int(chunk_max_chars or 0)) if chunk_max_chars else 0
    blocks: list[str] = []
    for index, chunk in enumerate(chunks, 1):
        if not isinstance(chunk, dict):
            continue
        text = str(chunk.get("text") or chunk.get("content") or "").strip()
        if max_chars and len(text) > max_chars:
            text = text[: max_chars - 3].rstrip() + "..."
        doc_name = str(chunk.get("doc_name") or chunk.get("file_name") or "document")
        repo_name = _chunk_repository_name(chunk, default_repo)
        page = _chunk_page(chunk)
        section_name = str(chunk.get("section_name") or chunk.get("metadata", {}).get("section") or "")
        citation_key = format_citation_label(repo_name, doc_name, page)
        label_parts = [f"Source {index}", citation_key]
        if section_name:
            label_parts.append(f"section: {section_name}")
        ids = _extract_citation_ids(
            doc_name,
            text,
            repository_name=repo_name,
            page=page,
            section_name=section_name,
        )
        if ids.get("pmid"):
            label_parts.append(f"PMID {ids['pmid']}")
        if ids.get("nct_id"):
            label_parts.append(ids["nct_id"])
        label = " | ".join(label_parts)
        blocks.append(f"[{label}]\n{text}")
    return "\n\n---\n\n".join(blocks)


def _citation_preview_text(text: str, *, max_len: int = 110) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    if not cleaned:
        return ""
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3].rstrip() + "..."


def build_citation_lookup(
    chunks: list[dict[str, Any]],
    *,
    default_repository_name: str = "",
) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        doc_name = str(chunk.get("doc_name") or chunk.get("file_name") or "").strip()
        if not doc_name:
            continue
        repo_name = _chunk_repository_name(chunk, default_repository_name)
        page = _chunk_page(chunk)
        cite = _extract_citation_ids(
            doc_name,
            str(chunk.get("text") or chunk.get("content") or ""),
            repository_name=repo_name,
            page=page,
            section_name=str(chunk.get("section_name") or chunk.get("metadata", {}).get("section") or ""),
        )
        cite["preview_text"] = _citation_preview_text(
            str(chunk.get("text") or chunk.get("content") or "")
        )
        key = str(cite.get("citation_key") or "").strip()
        if key:
            lookup[key] = cite
        lookup[doc_name] = cite
        lookup[doc_name.lower()] = cite
    return lookup


def citations_for_display(
    answer: str,
    citations: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    """Return citation pills only when the answer cites sources inline."""
    inline = parse_inline_citations(answer)
    if not inline:
        return []
    inline_keys = {
        str(item.get("citation_key") or "").strip()
        for item in inline
        if str(item.get("citation_key") or "").strip()
    }
    resolved: list[dict[str, str]] = []
    seen: set[str] = set()
    for cite in citations or []:
        if not isinstance(cite, dict):
            continue
        key = str(cite.get("citation_key") or "").strip()
        if not key or key not in inline_keys or key in seen:
            continue
        seen.add(key)
        resolved.append(dict(cite))
    for item in inline:
        key = str(item.get("citation_key") or "").strip()
        if key and key not in seen:
            seen.add(key)
            resolved.append(dict(item))
    return resolved


def resolve_answer_citations(
    answer: str,
    chunks: list[dict[str, Any]],
    *,
    default_repository_name: str = "",
) -> list[dict[str, str]]:
    """Keep only citations that appear inline in the answer and match retrieved chunks."""
    lookup = build_citation_lookup(chunks, default_repository_name=default_repository_name)
    resolved: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in parse_inline_citations(answer):
        key = str(item.get("citation_key") or "").strip()
        cite = lookup.get(key)
        if cite is None:
            doc = str(item.get("doc_name") or "").strip()
            cite = lookup.get(doc) or lookup.get(doc.lower())
        if cite is None and doc:
            for candidate in lookup.values():
                candidate_doc = str(candidate.get("doc_name") or "").strip()
                if candidate_doc == doc or (
                    doc.lower() in candidate_doc.lower() and candidate_doc
                ):
                    cite = candidate
                    break
        if not cite:
            continue
        resolved_key = str(cite.get("citation_key") or key)
        if resolved_key in seen:
            continue
        seen.add(resolved_key)
        resolved.append(dict(cite))
    return resolved


def build_repository_refusal_response(repository_names: list[str] | None = None) -> str:
    scope = ", ".join(repository_names) if repository_names else "the selected repository"
    return (
        f"I couldn't find information about that in **{scope}**. "
        "I can only answer from documents loaded in this repository."
    )


def is_repository_refusal_answer(answer: str) -> bool:
    """True when the assistant declined because the topic is outside repository scope."""
    text = str(answer or "").strip().lower()
    if not text:
        return False
    markers = (
        "couldn't find information",
        "could not find information",
        "could not find the",
        "couldn't find the",
        "could not find that",
        "can only answer from documents",
        "not find that information in",
        "not find the capital",
        "not in the selected repository",
        "not in the selected repository",
        "not in the provided",
        "outside the selected repository",
    )
    return any(marker in text for marker in markers)


def _greeting_opener(user_message: str = "") -> str:
    text = str(user_message or "").strip().lower().rstrip("!.? ")
    if text.startswith("hey"):
        return "Hey!"
    if text.startswith("hello"):
        return "Hello!"
    return "Hi!"


def build_greeting_response(
    repository_names: list[str] | None = None,
    *,
    user_message: str = "",
) -> str:
    del repository_names  # Greeting is repository-agnostic by design.
    opener = _greeting_opener(user_message)
    body = (
        "I can help you answer questions, summarize information, and assist with "
        "documents stored in the connected repository."
    )
    if re.search(r"\b(?:doubt|question|help)\b", str(user_message or ""), re.I):
        body = (
            "Happy to help — ask your question about any document in the connected repository, "
            "and I'll answer from that evidence."
        )
    return (
        f"{opener} I'm the **{_MLR_AGENT_GREETING_NAME}**.\n\n"
        f"{body}"
    )


def _mode_style_block(response_mode: str, *, point_count: int | None = None) -> str:
    if point_count is not None:
        return _point_count_style_block(point_count)
    if response_mode == "greeting":
        return _GREETING_STYLE
    if response_mode == "detail":
        return _DETAIL_STYLE
    if response_mode == "summary":
        return _SUMMARY_STYLE
    if response_mode == "verbose_reformulation":
        return _VERBOSE_REFORMULATION_STYLE
    if response_mode == "reformulation":
        return _REFORMULATION_STYLE
    return _INITIAL_STYLE


def _build_user_prompt(
    query: str,
    context: str,
    repository_names: list[str],
    *,
    response_mode: str = "initial",
    point_count: int | None = None,
) -> str:
    scope = ", ".join(repository_names) if repository_names else "the selected repository"
    format_rules = (
        f"The user requested exactly {point_count} points or lines — see the User question for which. "
        f"Output exactly {point_count} content lines and follow USER FORMAT rules. "
        if point_count is not None
        else (
            "Read the User question for format instructions (e.g. 'in 3 lines', '3 points only'). "
            "If present, follow them exactly — they override default INITIAL layout. "
            "If no format is specified: open with one or two direct-answer sentences, "
            'then 4–6 "• " bullets that support that answer, then one closing invitation sentence. '
        )
    )
    return (
        f"Repository scope: {scope}\n\n"
        f"{_mode_style_block(response_mode, point_count=point_count)}\n\n"
        f"User question:\n{query.strip()}\n\n"
        f"Retrieved excerpts (ONLY source you may use):\n{context}\n\n"
        "Answer in clear markdown. Add an inline citation [REPO_NAME:DOCUMENT_NAME:PAGE_NO] "
        "after every factual statement, using keys exactly as shown in the source headers. "
        f"{format_rules}"
        "If excerpts are insufficient, say you could not find that information in this repository."
    )


def _build_history_only_user_prompt(
    query: str,
    repository_names: list[str],
    *,
    response_mode: str = "summary",
    point_count: int | None = None,
) -> str:
    scope = ", ".join(repository_names) if repository_names else "the selected repository"
    point_rule = ""
    if point_count is not None:
        point_rule = (
            f"\nThe user requested EXACTLY {point_count} bullet points. "
            f'Output exactly {point_count} lines starting with "• " and no more. '
        )
    return (
        f"Repository scope: {scope}\n\n"
        f"{_mode_style_block(response_mode, point_count=point_count)}\n\n"
        f"User request:\n{query.strip()}\n\n"
        "Use the full conversation history above. Stay on the same topic as prior turns. "
        "Do not introduce new medical facts beyond what was already stated. "
        "Do not say you could not find information if prior assistant answers already covered this topic."
        f"{point_rule}"
    )


def answer_sentence_count(text: str) -> int:
    return len([part for part in re.split(r"[.!?]+", str(text or "")) if part.strip()])


def answer_bullet_count(text: str) -> int:
    cleaned = str(text or "")
    dot_bullets = len(re.findall(r"(?m)^\s*•\s+", cleaned))
    dash_bullets = len(re.findall(r"(?m)^\s*[-*]\s+", cleaned))
    numbered = len(re.findall(r"(?m)^\s*\d+\.\s+", cleaned))
    return dot_bullets + dash_bullets + numbered


def answer_non_empty_line_count(text: str) -> int:
    return len([line for line in str(text or "").splitlines() if line.strip()])


def initial_answer_is_too_terse(text: str, *, requested_points: int | None = None) -> bool:
    """True when an INITIAL-mode answer is a one-liner instead of a structured brief."""
    if requested_points is not None:
        return answer_bullet_count(text) < requested_points
    cleaned = str(text or "").strip()
    if not cleaned or is_repository_refusal_answer(cleaned):
        return False
    words = len(cleaned.split())
    bullets = answer_bullet_count(cleaned)
    lines = answer_non_empty_line_count(cleaned)
    if bullets == 0 and 2 <= lines <= 8:
        return False
    if bullets <= 1 and words < 160:
        return True
    if words < 90:
        return True
    if bullets < 3 and words < 180:
        return True
    return False


def answer_point_count_mismatch(text: str, requested_points: int) -> bool:
    return answer_bullet_count(text) != int(requested_points)


def trim_answer_to_point_count(text: str, point_count: int) -> str:
    """Keep intro/closing prose but cap bullet lines to the requested count."""
    kept: list[str] = []
    bullets_kept = 0
    for raw_line in str(text or "").replace("\r\n", "\n").splitlines():
        stripped = raw_line.strip()
        is_bullet = bool(
            re.match(r"^(?:\u2022|[-*])\s+", stripped) or re.match(r"^\d+\.\s+", stripped)
        )
        if is_bullet:
            if bullets_kept >= point_count:
                continue
            bullets_kept += 1
        kept.append(raw_line)
    return "\n".join(kept).strip()


def _retry_point_count_instruction(point_count: int) -> str:
    return (
        f"Rewrite using EXACTLY {point_count} content lines. "
        f"If I asked for lines, use plain prose lines (no bullets). "
        f"If I asked for points or bullets, use exactly {point_count} lines starting with \"• \". "
        "Keep inline citations after each fact and use only the retrieved excerpts."
    )


def is_brief_structured_answer(
    text: str,
    *,
    min_chars: int = 90,
    min_sentences: int = 2,
) -> bool:
    """True when an answer is more than a one-liner but still reasonably brief."""
    cleaned = str(text or "").strip()
    if len(cleaned) < min_chars:
        return False
    return answer_sentence_count(cleaned) >= min_sentences


def suggest_follow_up_questions(
    query: str,
    *,
    repository_names: list[str] | None = None,
    found_evidence: bool = True,
    answer: str = "",
) -> list[str]:
    scope = repository_names[0] if repository_names else "this repository"
    if not found_evidence:
        return [
            f"What documents are available in {scope}?",
            "Summarize the main topics covered in the loaded literature.",
        ]
    lowered = str(query or "").lower()
    suggestions: list[str] = []
    if "migraine" in lowered or "headache" in lowered:
        suggestions.extend(
            [
                "What adverse events were reported?",
                "What was the study design and population?",
            ]
        )
    elif "heart" in lowered or "cardio" in lowered:
        suggestions.extend(
            [
                "What were the primary outcomes?",
                "Were there any safety concerns noted?",
            ]
        )
    elif "trial" in lowered or "nct" in lowered:
        suggestions.extend(
            [
                "What are the inclusion and exclusion criteria?",
                "What interventions are being studied?",
            ]
        )
    else:
        suggestions.extend(
            [
                "Can you summarize the key findings in bullet points?",
                "What evidence limitations are noted in these documents?",
            ]
        )
    if answer and len(answer) > 400:
        suggestions.append("Elaborate each point in more detail.")
        suggestions.append("Summarize that in 3 lines.")
    elif found_evidence:
        suggestions.extend(
            [
                "Elaborate each point in more detail.",
                "Summarize that in 3 lines.",
            ]
        )
    deduped: list[str] = []
    for item in suggestions:
        if item not in deduped:
            deduped.append(item)
    return deduped[:3]


def _llm_request_body(config: MedicalLiteracyConfig, messages: list[dict[str, str]]) -> dict[str, Any]:
    body: dict[str, Any] = {"messages": messages}
    if config.llm_temperature is not None:
        body["temperature"] = config.llm_temperature
    if config.llm_provider == "openai":
        body["model"] = config.openai_model
    return body


def build_mlr_chat_messages(
    *,
    query: str,
    chunks: list[dict[str, Any]],
    conversation_history: list[dict[str, str]] | None = None,
    repository_names: list[str] | None = None,
    allow_history_only: bool = False,
    response_mode: str = "initial",
    config: MedicalLiteracyConfig | None = None,
) -> tuple[list[dict[str, str]], str, str]:
    """Build LLM messages and metadata for chat generation."""
    cfg = config or load_config()
    repo_names = [str(name).strip() for name in (repository_names or []) if str(name).strip()]
    scope_label = ", ".join(repo_names) if repo_names else "the selected repository"
    default_repo = repo_names[0] if repo_names else ""
    requested_points = extract_requested_point_count(query)
    retrieval_settings = load_chat_retrieval_settings()
    context = _format_chunk_context(
        chunks,
        repo_names,
        chunk_max_chars=retrieval_settings.chunk_max_chars,
    )
    mode = str(response_mode or "initial").strip().lower()
    if mode not in {
        "initial",
        "greeting",
        "detail",
        "summary",
        "reformulation",
        "verbose_reformulation",
        "contextual",
    }:
        mode = "initial"
    if mode == "contextual":
        mode = "initial"

    if not context.strip():
        if allow_history_only and conversation_history:
            history_mode = (
                "summary"
                if mode in {"summary", "reformulation"}
                else "verbose_reformulation"
                if mode in {"verbose_reformulation", "detail", "contextual", "initial"}
                else mode
            )
            messages: list[dict[str, str]] = [{"role": "system", "content": _HISTORY_ONLY_SYSTEM_PROMPT}]
            for turn in conversation_history:
                role = str(turn.get("role") or "").strip()
                content = str(turn.get("content") or "").strip()
                if role in {"user", "assistant"} and content:
                    messages.append({"role": role, "content": content})
            messages.append(
                {
                    "role": "user",
                    "content": _build_history_only_user_prompt(
                        query,
                        repo_names,
                        response_mode=history_mode,
                        point_count=requested_points,
                    ),
                }
            )
            return messages, default_repo, "history_only"

        return [], default_repo, "refusal"

    rag_mode = "detail" if mode == "detail" else "initial"
    messages = [
        {
            "role": "system",
            "content": _SYSTEM_PROMPT + _mode_style_block(rag_mode, point_count=requested_points),
        }
    ]
    for turn in conversation_history or []:
        role = str(turn.get("role") or "").strip()
        content = str(turn.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append(
        {
            "role": "user",
            "content": _build_user_prompt(
                query,
                context,
                repo_names,
                response_mode=rag_mode,
                point_count=requested_points,
            ),
        }
    )
    return messages, default_repo, "rag"


def call_mlr_llm(
    messages: list[dict[str, str]],
    config: MedicalLiteracyConfig | None = None,
) -> str:
    """Public wrapper for lightweight MLR LLM calls (routing, classification)."""
    return _call_llm(messages, config or load_config())


def _call_llm(messages: list[dict[str, str]], config: MedicalLiteracyConfig) -> str:
    timeout = config.llm_timeout_seconds
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            if config.llm_provider == "openai":
                if not config.openai_api_key:
                    raise RuntimeError("OPENAI_API_KEY is not configured in Medical Literacy Research/.env")
                response = requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {config.openai_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=_llm_request_body(config, messages),
                    timeout=timeout,
                )
                response.raise_for_status()
                payload = response.json()
                return str(payload["choices"][0]["message"]["content"]).strip()

            if not config.llm_configured():
                raise RuntimeError(
                    "Azure OpenAI is not configured. Set AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, "
                    "and AZURE_OPENAI_DEPLOYMENT_NAME in Medical Literacy Research/.env"
                )
            url = (
                f"{config.azure_openai_endpoint}/openai/deployments/"
                f"{config.azure_openai_deployment}/chat/completions"
                f"?api-version={config.azure_openai_api_version}"
            )
            response = requests.post(
                url,
                headers={
                    "api-key": config.azure_openai_api_key,
                    "Content-Type": "application/json",
                },
                json=_llm_request_body(config, messages),
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            return str(payload["choices"][0]["message"]["content"]).strip()
        except requests.exceptions.Timeout as exc:
            last_error = exc
            if attempt == 0:
                _logger.warning("MLR LLM request timed out; retrying once")
                continue
            raise
        except requests.exceptions.RequestException as exc:
            last_error = exc
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("LLM request failed")


def generate_mlr_chat_response(
    *,
    query: str,
    chunks: list[dict[str, Any]],
    conversation_history: list[dict[str, str]] | None = None,
    repository_names: list[str] | None = None,
    config: MedicalLiteracyConfig | None = None,
    allow_history_only: bool = False,
    response_mode: str = "initial",
) -> dict[str, Any]:
    """Build RAG prompt from retrieved chunks and generate a repository-grounded LLM answer."""
    cfg = config or load_config()
    repo_names = [str(name).strip() for name in (repository_names or []) if str(name).strip()]
    scope_label = ", ".join(repo_names) if repo_names else "the selected repository"
    mode = str(response_mode or "initial").strip().lower()
    if mode == "greeting":
        return {
            "answer": build_greeting_response(repo_names, user_message=query),
            "citations": [],
            "llm_used": False,
        }

    messages, default_repo, kind = build_mlr_chat_messages(
        query=query,
        chunks=chunks,
        conversation_history=conversation_history,
        repository_names=repo_names,
        allow_history_only=allow_history_only,
        response_mode=response_mode,
        config=cfg,
    )

    if kind == "refusal":
        return {
            "answer": (
                f"I couldn't find information about that in **{scope_label}**. "
                "I can only answer from documents loaded in this repository."
            ),
            "citations": [],
            "llm_used": False,
        }

    if kind == "history_only":
        if not cfg.llm_configured():
            prior = next(
                (
                    str(turn.get("content") or "")
                    for turn in reversed(conversation_history or [])
                    if str(turn.get("role") or "").strip() == "assistant"
                ),
                "",
            )
            return {
                "answer": prior or "I do not have a prior answer to summarize.",
                "citations": [],
                "llm_used": False,
            }
        try:
            answer = _call_llm(messages, cfg)
            requested_points = extract_requested_point_count(query)
            if requested_points is not None and answer_point_count_mismatch(answer, requested_points):
                messages.append({"role": "assistant", "content": answer})
                messages.append(
                    {"role": "user", "content": _retry_point_count_instruction(requested_points)}
                )
                answer = _call_llm(messages, cfg)
                if answer_point_count_mismatch(answer, requested_points):
                    answer = trim_answer_to_point_count(answer, requested_points)
        except Exception as exc:
            _logger.exception("MLR history-only chat LLM call failed")
            return {
                "answer": f"The LLM request failed: {exc}",
                "citations": [],
                "llm_used": False,
            }
        return {"answer": answer, "citations": [], "llm_used": True}

    if not cfg.llm_configured():
        retrieval_settings = load_chat_retrieval_settings()
        context = _format_chunk_context(
            chunks,
            repo_names,
            chunk_max_chars=retrieval_settings.chunk_max_chars,
        )
        summary_lines = [
            f"**Evidence from {scope_label} (LLM not configured):**",
            "",
            context[:12000],
        ]
        fallback_citations = resolve_answer_citations(
            context[:4000], chunks, default_repository_name=default_repo
        )
        if fallback_citations:
            summary_lines.append("\n**Sources:**")
            for cite in fallback_citations:
                label = cite.get("citation_key") or cite.get("label")
                url = cite.get("url", "")
                summary_lines.append(f"- {label}" + (f" ({url})" if url else ""))
        return {
            "answer": "\n".join(summary_lines),
            "citations": fallback_citations,
            "llm_used": False,
        }

    try:
        requested_points = extract_requested_point_count(query)
        answer = _call_llm(messages, cfg)
        rag_mode = "detail" if mode == "detail" else "initial"
        if requested_points is not None and answer_point_count_mismatch(answer, requested_points):
            messages.append({"role": "assistant", "content": answer})
            messages.append(
                {"role": "user", "content": _retry_point_count_instruction(requested_points)}
            )
            answer = _call_llm(messages, cfg)
            if answer_point_count_mismatch(answer, requested_points):
                answer = trim_answer_to_point_count(answer, requested_points)
        elif rag_mode == "initial" and initial_answer_is_too_terse(
            answer, requested_points=requested_points
        ):
            messages.append({"role": "assistant", "content": answer})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f'My question was: "{query.strip()}"\n'
                        "If I requested a specific format (N lines, N points, one paragraph, short answer), "
                        "keep that exact format — do not add bullets, intro paragraphs, or closing invitations.\n"
                        "Otherwise, expand into the full INITIAL response format required above: "
                        "one or two opening sentences that directly answer my question, then at least 4 substantive "
                        'lines starting with "• " that support that answer, inline citations after each fact, '
                        "and one closing invitation sentence. Use only the retrieved excerpts."
                    ),
                }
            )
            answer = _call_llm(messages, cfg)
    except Exception as exc:
        _logger.exception("MLR chat LLM call failed")
        return {
            "answer": f"Retrieval succeeded but the LLM request failed: {exc}",
            "citations": resolve_answer_citations(answer="", chunks=chunks, default_repository_name=default_repo),
            "llm_used": False,
        }

    citations = resolve_answer_citations(answer, chunks, default_repository_name=default_repo)
    return {
        "answer": answer,
        "citations": citations,
        "llm_used": True,
    }


def _append_parsed_citation(
    found: list[dict[str, str]],
    seen: set[str],
    *,
    repo: str,
    doc: str,
    page: str,
) -> None:
    citation_key = format_citation_label(repo, doc, page)
    if citation_key in seen:
        return
    seen.add(citation_key)
    found.append(
        {
            "citation_key": citation_key,
            "repository_name": repo.strip(),
            "doc_name": doc.strip(),
            "page": page.strip(),
            "label": _short_doc_label(doc.strip()),
        }
    )


def sentence_end_char_before_citation(text: str, citation_start: int) -> str:
    """Return the last non-whitespace character immediately before a citation marker."""
    index = int(citation_start) - 1
    while index >= 0 and str(text[index]).isspace():
        index -= 1
    return str(text[index]) if index >= 0 else ""


def inline_citation_punctuation_before_chip(
    *,
    prefix_char: str,
    trailing_after_bracket: str,
    text_before_citation: str = "",
    citation_start: int | None = None,
) -> str:
    """Return punctuation that should appear before a citation chip, not after it."""
    trailing = str(trailing_after_bracket or "").strip()
    if not trailing:
        return ""
    effective_prefix = (
        sentence_end_char_before_citation(text_before_citation, citation_start)
        if citation_start is not None
        else str(prefix_char or "")
    )
    if effective_prefix in ".!?":
        return ""
    return trailing[0]


def parse_inline_citations(answer: str) -> list[dict[str, str]]:
    """Extract inline [REPO:DOC:PAGE] citations from an assistant answer."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    text = str(answer or "")
    for bracket_match in re.finditer(r"\[([^\]]+)\]", text):
        inner = str(bracket_match.group(1) or "").strip()
        if not inner:
            continue
        parts = [part.strip() for part in inner.split(";") if part.strip()]
        for part in parts:
            part_match = _CITATION_PART_RE.match(part)
            if not part_match:
                continue
            _append_parsed_citation(
                found,
                seen,
                repo=part_match.group("repo"),
                doc=part_match.group("doc"),
                page=part_match.group("page"),
            )
    if found:
        return found
    for match in _CITATION_INLINE_RE.finditer(text):
        _append_parsed_citation(
            found,
            seen,
            repo=match.group("repo"),
            doc=match.group("doc"),
            page=match.group("page"),
        )
    if found:
        return found
    for match in _CITATION_BARE_RE.finditer(text):
        _append_parsed_citation(
            found,
            seen,
            repo=match.group("repo"),
            doc=match.group("doc"),
            page=match.group("page"),
        )
    return found
