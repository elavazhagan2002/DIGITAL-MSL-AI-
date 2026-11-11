"""LLM-first chat turn planning: classify intent, then trigger backend actions."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from connectors.config import MedicalLiteracyConfig, load_config
from connectors.conversation_context import (
    classify_response_intent,
    contextualize_retrieval_query,
    is_conversational_follow_up,
    is_greeting_only,
    needs_document_retrieval,
    should_refuse_without_retrieval,
)
from connectors.document_scope import resolve_document_identifiers
from connectors.retrieval import select_primary_retrieval_query

_logger = logging.getLogger(__name__)

BackendAction = Literal[
    "greeting",
    "repository_catalog",
    "document_search",
    "acronym_definition",
    "document_scoped",
    "history_only",
    "out_of_scope",
]

_VALID_BACKEND_ACTIONS = {
    "greeting",
    "repository_catalog",
    "document_search",
    "acronym_definition",
    "document_scoped",
    "history_only",
    "out_of_scope",
}

_VALID_RESPONSE_MODES = {
    "initial",
    "greeting",
    "detail",
    "summary",
    "reformulation",
    "verbose_reformulation",
    "contextual",
    "repository_catalog",
}

_ROUTER_SYSTEM_PROMPT = """You are the routing layer for a medical-literature RAG assistant.

Given the latest user message and conversation history, return ONLY valid JSON (no markdown) with:
{
  "backend_action": "greeting|repository_catalog|document_search|acronym_definition|document_scoped|history_only|out_of_scope",
  "response_mode": "initial|detail|summary|reformulation|verbose_reformulation|contextual|greeting|repository_catalog",
  "skip_retrieval": true|false,
  "use_history_only": true|false,
  "retrieval_queries": ["..."],
  "document_identifiers": ["..."],
  "confidence": 0.0
}

Backend action rules:
- greeting: short hello with no repository question.
- repository_catalog: user explicitly asks for document count, list, inventory, or which filenames are loaded.
  Use ONLY for inventory questions (how many / list / show documents). Never use for explaining a topic,
  connector, database, trial, drug, or acronym — even if the user says "from our repo".
- acronym_definition: user asks what a trial/acronym stands for, full form, meaning, or expanded name.
- document_scoped: user names a specific document, filename, PMID, or NCT and asks about that document.
- history_only: user wants summarize/rephrase/expand/shorter answer from prior turns only.
- document_search: medical/content questions and explanations that need repository retrieval.
  Use for "what is X", "explain X", "tell me about X", including PubMed, ClinicalTrials.gov, connectors,
  trials, treatments, and sources referenced in the repository.
- out_of_scope: obvious general knowledge unrelated to repository literature.

Examples:
- "how many documents are in the repo?" → repository_catalog
- "list documents in this repository" → repository_catalog
- "what is pubmed and clinicaltrials.gov from our repo?" → document_search (explain sources, not inventory)
- "what is the BOX trial full form?" → acronym_definition
- "summarize the GIANT trial" → document_search

Retrieval query rules:
- Return exactly ONE concise hybrid-search query in retrieval_queries.
- For acronym_definition, prefer "Trial (ACRONYM)" or a phrase likely to match spelled-out trial names in titles.
- For follow-ups, incorporate the conversation topic into that single query when helpful.
- Leave retrieval_queries empty when skip_retrieval is true.

Document identifiers:
- Include NCT ids, pubmed filenames, or explicit document names when document_scoped.

Use conversation history to interpret short follow-ups like "what is the full form?" or "summarize it".
"""


@dataclass(frozen=True)
class ChatTurnPlan:
    backend_action: BackendAction
    response_mode: str
    skip_retrieval: bool
    use_history_only: bool
    retrieval_queries: list[str] = field(default_factory=list)
    document_identifiers: list[str] = field(default_factory=list)
    router_source: str = "heuristic"
    confidence: float = 0.0

    def to_pipeline_dict(self) -> dict[str, Any]:
        return {
            "backend_action": self.backend_action,
            "response_intent": self.response_mode,
            "skipped_retrieval": self.skip_retrieval,
            "use_history_only": self.use_history_only,
            "retrieval_queries": list(self.retrieval_queries),
            "document_identifiers": list(self.document_identifiers),
            "router_source": self.router_source,
            "confidence": self.confidence,
        }


def _format_history_for_router(
    conversation_history: list[dict[str, Any]] | None,
    *,
    max_turns: int = 8,
) -> str:
    if not conversation_history:
        return "(no prior turns)"
    lines: list[str] = []
    for message in conversation_history[-max_turns:]:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        content = str(message.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            if len(content) > 700:
                content = content[:697].rstrip() + "..."
            lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "(no prior turns)"


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


_ACRONYM_TOKEN_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,7})\b")
_TRIAL_ACRONYM_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,7})\s+trial\b", re.I)
_ACRONYM_STOPWORDS = frozenset(
    {
        "THE", "AND", "FOR", "NOT", "BUT", "ARE", "WAS", "HAS", "HAD", "CAN", "MAY",
        "ALL", "ANY", "WHO", "HOW", "WHY", "WHAT", "WHEN", "WHERE", "THIS", "THAT",
        "WITH", "FROM", "INTO", "ONLY", "ALSO", "PDF", "DOC", "DOCX", "HTTP", "API",
    }
)


def extract_acronym_tokens(
    user_message: str,
    conversation_history: list[dict[str, Any]] | None = None,
) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def _add(token: str) -> None:
        cleaned = str(token or "").strip().upper()
        if not cleaned or cleaned in _ACRONYM_STOPWORDS or cleaned.isdigit():
            return
        if cleaned in seen:
            return
        seen.add(cleaned)
        found.append(cleaned)

    text = str(user_message or "")
    for match in _ACRONYM_TOKEN_RE.finditer(text):
        _add(match.group(1))
    for match in _TRIAL_ACRONYM_RE.finditer(text):
        _add(match.group(1))

    if conversation_history:
        for message in reversed(conversation_history[-8:]):
            if not isinstance(message, dict):
                continue
            content = str(message.get("content") or "")
            for match in _TRIAL_ACRONYM_RE.finditer(content):
                _add(match.group(1))
            if found:
                break

    return found


def enrich_acronym_retrieval_queries(
    user_message: str,
    conversation_history: list[dict[str, Any]] | None,
    queries: list[str] | None = None,
    *,
    limit: int = 6,
) -> list[str]:
    """Add high-recall acronym expansion queries the LLM may omit."""
    merged: list[str] = []
    seen: set[str] = set()

    def _add(query: str) -> None:
        cleaned = str(query or "").strip()
        if not cleaned:
            return
        key = cleaned.lower()
        if key in seen:
            return
        seen.add(key)
        merged.append(cleaned)

    for query in queries or []:
        _add(query)

    text = str(user_message or "").strip()
    if text:
        _add(text)

    for token in extract_acronym_tokens(text, conversation_history):
        _add(f"Trial ({token})")
        _add(f"{token} trial full name stands for")
        _add(f"{token} Blood Pressure Oxygenation Targets cardiac arrest")
        _add(f"{token} acronym expanded name")

    return merged[:limit]


def _finalize_plan(
    plan: ChatTurnPlan,
    user_message: str,
    conversation_history: list[dict[str, Any]] | None,
) -> ChatTurnPlan:
    if plan.backend_action != "acronym_definition":
        return plan

    enriched = enrich_acronym_retrieval_queries(
        user_message,
        conversation_history,
        plan.retrieval_queries,
    )
    tokens = extract_acronym_tokens(user_message, conversation_history)
    primary = select_primary_retrieval_query(
        enriched,
        user_message=user_message,
        acronym_tokens=tokens,
    )
    retrieval_queries = [primary] if primary else enriched[:1]

    return ChatTurnPlan(
        backend_action=plan.backend_action,
        response_mode=plan.response_mode,
        skip_retrieval=plan.skip_retrieval,
        use_history_only=plan.use_history_only,
        retrieval_queries=retrieval_queries,
        document_identifiers=list(plan.document_identifiers),
        router_source=plan.router_source,
        confidence=plan.confidence,
    )


def _normalize_string_list(value: Any, *, limit: int = 4) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        token = str(item or "").strip()
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(token)
        if len(cleaned) >= limit:
            break
    return cleaned


def _coerce_plan_payload(payload: dict[str, Any], *, fallback: ChatTurnPlan) -> ChatTurnPlan:
    backend_action = str(payload.get("backend_action") or fallback.backend_action).strip().lower()
    if backend_action not in _VALID_BACKEND_ACTIONS:
        backend_action = fallback.backend_action

    response_mode = str(payload.get("response_mode") or fallback.response_mode).strip().lower()
    if response_mode not in _VALID_RESPONSE_MODES:
        response_mode = fallback.response_mode

    retrieval_queries = _normalize_string_list(payload.get("retrieval_queries"))
    if not retrieval_queries and not bool(payload.get("skip_retrieval")) and fallback.retrieval_queries:
        retrieval_queries = list(fallback.retrieval_queries)

    document_identifiers = _normalize_string_list(
        payload.get("document_identifiers"),
        limit=6,
    )
    if not document_identifiers:
        document_identifiers = list(fallback.document_identifiers)

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    return ChatTurnPlan(
        backend_action=backend_action,  # type: ignore[arg-type]
        response_mode=response_mode,
        skip_retrieval=bool(payload.get("skip_retrieval", fallback.skip_retrieval)),
        use_history_only=bool(payload.get("use_history_only", fallback.use_history_only)),
        retrieval_queries=retrieval_queries,
        document_identifiers=document_identifiers,
        router_source="llm",
        confidence=max(0.0, min(1.0, confidence)),
    )


def _heuristic_plan(
    user_message: str,
    conversation_history: list[dict[str, Any]] | None,
    *,
    repository_names: list[str] | None = None,
) -> ChatTurnPlan:
    del repository_names
    text = str(user_message or "").strip()
    response_mode = classify_response_intent(text, conversation_history)
    if is_greeting_only(text):
        response_mode = "greeting"

    if response_mode == "greeting":
        return ChatTurnPlan(
            backend_action="greeting",
            response_mode="greeting",
            skip_retrieval=True,
            use_history_only=False,
        )

    if should_refuse_without_retrieval(text, conversation_history):
        return ChatTurnPlan(
            backend_action="out_of_scope",
            response_mode="initial",
            skip_retrieval=True,
            use_history_only=False,
        )

    document_identifiers = resolve_document_identifiers(text, conversation_history)
    if document_identifiers:
        return ChatTurnPlan(
            backend_action="document_scoped",
            response_mode=response_mode if response_mode != "contextual" else "summary",
            skip_retrieval=False,
            use_history_only=False,
            retrieval_queries=[text],
            document_identifiers=document_identifiers,
        )

    if is_conversational_follow_up(text, conversation_history):
        return ChatTurnPlan(
            backend_action="history_only",
            response_mode=response_mode,
            skip_retrieval=True,
            use_history_only=True,
        )

    retrieval_query = contextualize_retrieval_query(text, conversation_history)
    return ChatTurnPlan(
        backend_action="document_search",
        response_mode=response_mode,
        skip_retrieval=False,
        use_history_only=False,
        retrieval_queries=[retrieval_query] if retrieval_query else [text],
    )


def _llm_plan(
    user_message: str,
    conversation_history: list[dict[str, Any]] | None,
    *,
    repository_names: list[str] | None,
    config: MedicalLiteracyConfig,
    fallback: ChatTurnPlan,
) -> ChatTurnPlan | None:
    from connectors.chat import call_mlr_llm

    if not config.llm_configured():
        return None

    repo_label = ", ".join(repository_names or []) or "selected repository"
    user_prompt = (
        f"Repository scope: {repo_label}\n\n"
        f"Conversation history:\n{_format_history_for_router(conversation_history)}\n\n"
        f"Latest user message:\n{user_message}"
    )
    try:
        raw = call_mlr_llm(
            [
                {"role": "system", "content": _ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            config=config,
        )
    except Exception:
        _logger.exception("MLR intent router LLM call failed")
        return None

    payload = _extract_json_object(raw)
    if not payload:
        return None
    return _coerce_plan_payload(payload, fallback=fallback)


def plan_chat_turn(
    *,
    user_message: str,
    conversation_history: list[dict[str, Any]] | None,
    repository_names: list[str] | None = None,
    config: MedicalLiteracyConfig | None = None,
    use_llm_router: bool | None = None,
) -> ChatTurnPlan:
    """Plan backend action and retrieval strategy for one chat turn."""
    cfg = config or load_config()
    fallback = _heuristic_plan(user_message, conversation_history, repository_names=repository_names)

    if use_llm_router is None:
        import os

        use_llm_router = os.getenv("MLR_CHAT_USE_LLM_ROUTER", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    if not use_llm_router:
        plan = _finalize_plan(fallback, user_message, conversation_history)
        return _ensure_retrieval_when_needed(plan, user_message, conversation_history)

    llm_plan = _llm_plan(
        user_message,
        conversation_history,
        repository_names=repository_names,
        config=cfg,
        fallback=fallback,
    )
    plan = _finalize_plan(llm_plan or fallback, user_message, conversation_history)
    return _ensure_retrieval_when_needed(plan, user_message, conversation_history)


def _ensure_retrieval_when_needed(
    plan: ChatTurnPlan,
    user_message: str,
    conversation_history: list[dict[str, Any]] | None,
) -> ChatTurnPlan:
    """Force hybrid retrieval when the user asks a substantive repository question."""
    if plan.backend_action in {"greeting", "out_of_scope", "repository_catalog"}:
        return plan
    if not needs_document_retrieval(user_message, conversation_history):
        return plan
    if not plan.skip_retrieval and plan.backend_action != "history_only":
        return plan

    document_identifiers = resolve_document_identifiers(user_message, conversation_history)
    retrieval_queries = [str(item).strip() for item in plan.retrieval_queries if str(item).strip()]
    if not retrieval_queries:
        contextualized = contextualize_retrieval_query(user_message, conversation_history).strip()
        retrieval_queries = [contextualized or str(user_message or "").strip()]

    if document_identifiers:
        return ChatTurnPlan(
            backend_action="document_scoped",
            response_mode=plan.response_mode,
            skip_retrieval=False,
            use_history_only=False,
            retrieval_queries=retrieval_queries,
            document_identifiers=document_identifiers,
            router_source=plan.router_source,
            confidence=plan.confidence,
        )

    return ChatTurnPlan(
        backend_action="document_search",
        response_mode=plan.response_mode,
        skip_retrieval=False,
        use_history_only=False,
        retrieval_queries=retrieval_queries,
        document_identifiers=[],
        router_source=plan.router_source,
        confidence=plan.confidence,
    )
