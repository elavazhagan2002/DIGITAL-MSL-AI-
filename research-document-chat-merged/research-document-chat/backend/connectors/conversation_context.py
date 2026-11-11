"""Conversation trimming and follow-up intent for the Medical Literature Research Agent."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

DEFAULT_MAX_MESSAGES = 40
DEFAULT_MAX_TOKENS = 12000

ResponseIntent = Literal[
    "initial",
    "greeting",
    "summary",
    "detail",
    "reformulation",
    "verbose_reformulation",
    "contextual",
]

_GREETING_ONLY_RE = re.compile(
    r"^(?:"
    r"hi|hello|hey|howdy|greetings|"
    r"good\s+(?:morning|afternoon|evening)"
    r")"
    r"(?:\s+there|\s+again)?[!?.]*$",
    re.I,
)

_GREETING_PREFIX_RE = re.compile(
    r"^(?:"
    r"hi|hello|hey|howdy|greetings|"
    r"good\s+(?:morning|afternoon|evening)"
    r")\b",
    re.I,
)

_CONVERSATIONAL_OPENER_RE = re.compile(
    r"\b("
    r"i\s+have\s+(?:a\s+)?(?:doubt|question|query)|"
    r"i\s+need\s+(?:some\s+)?help|"
    r"can\s+you\s+help(?:\s+me)?|"
    r"help\s+me|"
    r"just\s+(?:saying\s+)?hi|"
    r"nice\s+to\s+meet\s+you"
    r")\b",
    re.I,
)

_SUBSTANTIVE_QUESTION_RE = re.compile(
    r"\?|\b("
    r"what|how|why|when|where|which|who|"
    r"tell\s+me\s+about|explain|describe|"
    r"what\s+is|what\s+are|what\s+about|"
    r"can\s+you\s+(?:tell|explain|describe|summarize|summarise)"
    r")\b",
    re.I,
)

_SUMMARY_REQUEST_RE = re.compile(
    r"\b("
    r"summari[sz]e(?:\s+(?:it|this|that))?(?:\s+document)?|summari[sz]ation|"
    r"tl;?dr|recap|"
    r"in\s+\d+\s+lines?|"
    r"short\s+paragraph|one\s+short\s+paragraph|"
    r"give\s+me\s+a\s+summary|brief\s+summary"
    r")\b",
    re.I,
)

_DETAIL_REQUEST_RE = re.compile(
    r"\b("
    r"more\s+details?|tell\s+me\s+more|explain\s+(?:further|more|in\s+detail)|"
    r"elaborate|expand(?:\s+(?:on|it|this|that|them|the\s+(?:answer|points?|summary)))?|"
    r"go\s+deeper|in\s+depth|"
    r"full\s+details?|comprehensive|everything\s+about|"
    r"what\s+else|additional\s+information|"
    r"more\s+verbose|be\s+more\s+verbose|verbos(?:e|ely)|deeper\s+explanation|"
    r"more\s+depth|in\s+more\s+detail|with\s+more\s+detail|"
    r"elaborate\s+each|expand\s+each|explain\s+each|flesh\s+out|"
    r"richer\s+detail|deeper\s+brief|more\s+explanatory|"
    r"could\s+you\s+expand|can\s+you\s+expand|please\s+expand|"
    r"expand\s+(?:further|again)|give\s+me\s+more"
    r")\b",
    re.I,
)

_VERBOSE_REFORMULATION_RE = re.compile(
    r"\b("
    r"rephrase.*(?:more\s+verbos|verbos|detail|depth)|"
    r"(?:more\s+verbos|verbos|detail|depth).*(?:rephrase|reword|rewrite)|"
    r"reword.*(?:more\s+verbos|verbos|detail)|"
    r"rewrite.*(?:more\s+verbos|verbos|detail)"
    r")\b",
    re.I,
)

_MEDICAL_HINT_RE = re.compile(
    r"\b("
    r"trial|study|patient|migraine|atogepant|pubmed|nct\d+|clinical|endpoint|"
    r"disease|drug|therapy|heart|cancer|diabetes|symptom|treatment|pcsk5|"
    r"cardiac|myocardial|angio|pmcid|document|evidence|literature|article|"
    r"inclusion|exclusion|adverse|efficacy|safety|diagnosis|prognosis"
    r")\b",
    re.I,
)

_GENERAL_KNOWLEDGE_RE = re.compile(
    r"\b("
    r"capital\s+of|population\s+of|currency\s+of|who\s+is\s+the\s+president|"
    r"weather\s+in|how\s+old\s+is|when\s+was\s+.+\s+born|tallest\s+mountain|"
    r"prime\s+minister\s+of|largest\s+country"
    r")\b",
    re.I,
)

_REFORMULATION_REQUEST_RE = re.compile(
    r"\b("
    r"same\s+(information|info|answer|content|thing|response|details?|text)|"
    r"in\s+(a\s+)?single\s+line|one\s+line|one\s+sentence|"
    r"shorter|brief(?:ly)?|condense|simplify|"
    r"explain\s+again|repeat|above|previous|earlier|"
    r"what\s+you\s+(just\s+)?said|that\s+again|make\s+it\s+shorter|"
    r"can\s+you\s+shorten|put\s+that\s+in|"
    r"rephrase(?:\s+it)?(?:\s+simply)?|reword(?:\s+it)?|rewrite(?:\s+it)?"
    r")\b",
    re.I,
)

_POINT_COUNT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(\d{1,2})\s+(?:bullet\s+)?points?\s+only\b", re.I),
    re.compile(r"\b(?:in|into|as)\s+(\d{1,2})\s+(?:bullet\s+)?points?\b", re.I),
    re.compile(r"\b(?:exactly|only)\s+(\d{1,2})\s+(?:bullet\s+)?points?\b", re.I),
    re.compile(
        r"\bsummari[sz]e\b.*?\b(?:in|into|as)\s+(\d{1,2})\s+(?:bullet\s+)?points?\b",
        re.I,
    ),
    re.compile(
        r"\b(?:give|write|provide|need)\s+(?:me\s+)?(?:exactly\s+)?(\d{1,2})\s+(?:bullet\s+)?points?\b",
        re.I,
    ),
    re.compile(r"\bin\s+(\d{1,2})\s+lines?\s+only\b", re.I),
    re.compile(r"\b(\d{1,2})\s+(?:bullet\s+)?points?\b", re.I),
)

_INLINE_CITATION_RE = re.compile(r"\[[^\]]+:[^\]]+:[^\]]+\]")


def extract_requested_point_count(text: str) -> int | None:
    """Return N when the user asks for exactly N bullet points/lines."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return None
    for pattern in _POINT_COUNT_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            count = int(match.group(1))
            if 1 <= count <= 12:
                return count
    return None


def strip_inline_citations(text: str) -> str:
    cleaned = _INLINE_CITATION_RE.sub("", str(text or ""))
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def has_assistant_reply(conversation_history: list[dict[str, Any]] | None) -> bool:
    if not conversation_history:
        return False
    return any(
        str(message.get("role") or "").strip().lower() == "assistant"
        and str(message.get("content") or "").strip()
        for message in conversation_history
        if isinstance(message, dict)
    )


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def _truncate_content(content: str, max_tokens: int) -> str:
    max_chars = max(80, max_tokens * 4)
    text = str(content or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def trim_conversation_history(
    conversation_history: list[dict[str, Any]] | None,
    *,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[dict[str, str]]:
    if not conversation_history:
        return []

    cleaned: list[dict[str, str]] = []
    for message in conversation_history:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        content = strip_inline_citations(str(message.get("content") or "").strip())
        if role in {"user", "assistant"} and content:
            cleaned.append({"role": role, "content": content})

    if not cleaned:
        return []

    tail = cleaned[-max(1, max_messages) :]
    while len(tail) > 1 and sum(estimate_tokens(item["content"]) for item in tail) > max_tokens:
        tail = tail[1:]

    if not tail:
        return []

    if sum(estimate_tokens(item["content"]) for item in tail) > max_tokens:
        budget = max_tokens
        kept: list[dict[str, str]] = []
        for item in reversed(tail):
            tokens = estimate_tokens(item["content"])
            if tokens <= budget:
                kept.insert(0, item)
                budget -= tokens
                continue
            if budget > 0:
                kept.insert(0, {
                    "role": item["role"],
                    "content": _truncate_content(item["content"], budget),
                })
            break
        if kept:
            return kept
        last = tail[-1]
        return [{"role": last["role"], "content": _truncate_content(last["content"], max_tokens)}]

    return tail


def last_user_message(conversation_history: list[dict[str, Any]] | None) -> str:
    if not conversation_history:
        return ""
    for message in reversed(conversation_history):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "").strip().lower() == "user":
            return str(message.get("content") or "").strip()
    return ""


def last_assistant_message(conversation_history: list[dict[str, Any]] | None) -> str:
    if not conversation_history:
        return ""
    for message in reversed(conversation_history):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "").strip().lower() == "assistant":
            content = str(message.get("content") or "").strip()
            if content:
                return content
    return ""


def is_history_meta_follow_up(user_message: str) -> bool:
    """True for short ask-to-rewrite turns (summarize / expand / rephrase) with no new topic."""
    text = _normalize_user_text(user_message)
    if not text:
        return True
    if is_greeting_only(text):
        return True
    lowered = text.lower().rstrip("!.? ")
    if lowered in {
        "summarize it",
        "summarise it",
        "summary",
        "summarize",
        "summarise",
        "expand it",
        "expand",
        "continue",
        "go on",
        "why?",
        "how?",
        "more",
        "details",
        "elaborate",
    }:
        return True
    # Longer topical questions are substantive even if they contain "summarize"/"detail".
    if len(text) > 140 and _MEDICAL_HINT_RE.search(text):
        return False
    if len(text) > 120 and _SUBSTANTIVE_QUESTION_RE.search(text) and not (
        _DETAIL_REQUEST_RE.search(text)
        or _SUMMARY_REQUEST_RE.search(text)
        or _REFORMULATION_REQUEST_RE.search(text)
        or _VERBOSE_REFORMULATION_RE.search(text)
    ):
        return False
    if (
        _DETAIL_REQUEST_RE.search(text)
        or _SUMMARY_REQUEST_RE.search(text)
        or _REFORMULATION_REQUEST_RE.search(text)
        or _VERBOSE_REFORMULATION_RE.search(text)
    ):
        return len(text) <= 140
    return False


def last_substantive_user_message(conversation_history: list[dict[str, Any]] | None) -> str:
    """Most recent user topic question, skipping summarize/expand/rephrase meta turns."""
    if not conversation_history:
        return ""
    for message in reversed(conversation_history):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        content = str(message.get("content") or "").strip()
        if content and not is_history_meta_follow_up(content):
            return content
    return last_user_message(conversation_history)


def _normalize_user_text(user_message: str) -> str:
    return unicodedata.normalize("NFKC", str(user_message or "")).strip()


def is_greeting_only(user_message: str) -> bool:
    """True for short greetings without a substantive repository question."""
    text = _normalize_user_text(user_message)
    if not text or len(text) > 100:
        return False
    # Block only substantive medical/repository questions, not vague openers.
    if _MEDICAL_HINT_RE.search(text) or re.search(r"\bnct\d+", text, re.I):
        return False
    if _SUBSTANTIVE_QUESTION_RE.search(text) and not _CONVERSATIONAL_OPENER_RE.search(text):
        return False
    lowered = text.lower().rstrip("!.? ")
    if lowered in {"hi", "hello", "hey", "howdy", "greetings", "hiya", "yo"}:
        return True
    if _GREETING_ONLY_RE.match(text):
        return True
    if _GREETING_PREFIX_RE.match(text):
        remainder = _GREETING_PREFIX_RE.sub("", text, count=1).strip(" ,.!?")
        if not remainder:
            return True
        if _CONVERSATIONAL_OPENER_RE.search(text):
            return True
    if _CONVERSATIONAL_OPENER_RE.search(text) and len(text) <= 80:
        return True
    return False


def classify_response_intent(
    user_message: str,
    conversation_history: list[dict[str, Any]] | None,
) -> ResponseIntent:
    """Classify how the assistant should respond to this turn."""
    text = str(user_message or "").strip()
    if is_greeting_only(text):
        return "greeting"
    if not text:
        return "contextual"
    if _DETAIL_REQUEST_RE.search(text):
        return "detail"
    if _VERBOSE_REFORMULATION_RE.search(text):
        return "verbose_reformulation"
    if _SUMMARY_REQUEST_RE.search(text):
        return "summary"
    if _REFORMULATION_REQUEST_RE.search(text):
        return "reformulation"
    lowered = text.lower()
    if lowered in {"summarize it", "summarise it", "summary", "summarize", "summarise"}:
        return "summary"
    if not has_assistant_reply(conversation_history):
        return "initial"
    if len(text) <= 80 and lowered in {"why?", "how?", "continue", "go on"}:
        return "contextual"
    return "contextual"


def is_conversational_follow_up(
    user_message: str,
    conversation_history: list[dict[str, Any]] | None,
) -> bool:
    """True when retrieval should be skipped for meta follow-ups (summarize it, expand)."""
    if not has_assistant_reply(conversation_history):
        return False
    text = str(user_message or "").strip()
    if needs_document_retrieval(user_message, conversation_history) and not is_history_meta_follow_up(text):
        return False
    if is_history_meta_follow_up(text):
        return True
    intent = classify_response_intent(text, conversation_history)
    return intent in {"greeting", "reformulation", "verbose_reformulation"}


def contextualize_retrieval_query(
    query: str,
    conversation_history: list[dict[str, Any]] | None,
) -> str:
    """Expand follow-up questions for better hybrid retrieval."""
    text = str(query or "").strip()
    intent = classify_response_intent(text, conversation_history)
    prior_topic = last_substantive_user_message(conversation_history)
    prior_user = last_user_message(conversation_history)
    prior_assistant = last_assistant_message(conversation_history)

    if intent == "detail" and (prior_topic or prior_assistant):
        parts = []
        if prior_topic:
            parts.append(f"Original topic question: {prior_topic}")
        if prior_assistant:
            # Keep enough topic anchors without blowing retrieval query size.
            assistant_snip = strip_inline_citations(prior_assistant)
            if len(assistant_snip) > 900:
                assistant_snip = assistant_snip[:897].rstrip() + "..."
            parts.append(f"Prior answer to expand:\n{assistant_snip}")
        parts.append(f"User follow-up: {text}")
        parts.append(
            "Retrieve comprehensive supporting evidence for a detailed, verbose answer. "
            "Expand each prior point with fuller explanatory sentences grounded in the excerpts."
        )
        return "\n\n".join(parts)

    if not text or is_conversational_follow_up(text, conversation_history):
        return text

    if not prior_topic and not prior_user:
        return text

    anchor = prior_topic or prior_user
    if len(text) <= 120 and anchor.lower() not in text.lower():
        return f"{text}\n\nPrior user question for context: {anchor}"
    return text


def needs_document_retrieval(
    user_message: str,
    conversation_history: list[dict[str, Any]] | None,
) -> bool:
    from connectors.document_scope import is_document_scoped_request

    if is_document_scoped_request(user_message, conversation_history):
        return True
    intent = classify_response_intent(user_message, conversation_history)
    return intent in {"initial", "detail", "contextual"}


def should_allow_history_fallback(
    user_message: str,
    conversation_history: list[dict[str, Any]] | None,
) -> bool:
    """When retrieval finds nothing, still answer from prior turns for follow-ups."""
    if not has_assistant_reply(conversation_history):
        return False
    intent = classify_response_intent(user_message, conversation_history)
    if intent in {"summary", "reformulation", "verbose_reformulation", "detail"}:
        return True
    if intent == "contextual" and is_history_meta_follow_up(user_message):
        return True
    # Short referential follow-ups ("expand it", "why?", "what about that")
    text = str(user_message or "").strip()
    return bool(text) and len(text) <= 120 and (
        re.search(r"\b(it|this|that|them|those|above|previous|earlier)\b", text, re.I)
        or is_history_meta_follow_up(text)
    )


def should_refuse_without_retrieval(
    user_message: str,
    conversation_history: list[dict[str, Any]] | None,
) -> bool:
    """True for obvious general-knowledge questions unrelated to repository literature."""
    if has_assistant_reply(conversation_history):
        return False
    text = str(user_message or "").strip()
    if not text or is_greeting_only(text):
        return False
    if _MEDICAL_HINT_RE.search(text):
        return False
    return bool(_GENERAL_KNOWLEDGE_RE.search(text))
