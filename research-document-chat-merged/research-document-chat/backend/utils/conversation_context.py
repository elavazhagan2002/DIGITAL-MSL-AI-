"""Shared conversation window trimming for chat agents."""
from __future__ import annotations

import re
from typing import Dict, List, Optional

DEFAULT_MAX_MESSAGES = 10
DEFAULT_MAX_TOKENS = 3000

_FOLLOW_UP_REFORMULATION_RE = re.compile(
    r"\b("
    r"same\s+(information|info|answer|content|thing|response|details?|text)|"
    r"in\s+(a\s+)?single\s+line|one\s+line|one\s+sentence|"
    r"summari[sz]e|shorter|brief(?:ly)?|condense|rephrase|"
    r"simplify|explain\s+again|repeat|above|previous|earlier|"
    r"what\s+you\s+(just\s+)?said|that\s+again|make\s+it\s+shorter|"
    r"can\s+you\s+shorten|put\s+that\s+in"
    r")\b",
    re.I,
)


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 characters per token for English prose)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def trim_conversation_history(
    conversation_history: Optional[List[Dict]],
    *,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> List[Dict]:
    """
    Keep the most recent turns, capped by message count and approximate token budget.

    Always preserves chronological order. Drops oldest messages first when over budget.
    """
    if not conversation_history:
        return []

    cleaned: List[Dict] = []
    for msg in conversation_history:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip().lower()
        content = msg.get("content")
        if role not in {"user", "assistant", "system"}:
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        cleaned.append({"role": role, "content": content.strip()})

    if not cleaned:
        return []

    tail = cleaned[-max(1, max_messages) :]
    while tail and sum(estimate_tokens(m["content"]) for m in tail) > max_tokens:
        tail = tail[1:]
    return tail


def resolve_conversation_history(
    db_history: Optional[List[Dict]],
    client_history: Optional[List[Dict]],
) -> List[Dict]:
    """
    Prefer persisted session history; use client payload when it carries more turns.
    """
    db = trim_conversation_history(db_history)
    client = trim_conversation_history(client_history)
    if len(db) >= len(client):
        return db
    if client:
        return client
    return db


def is_conversational_follow_up(
    user_message: Optional[str],
    conversation_history: Optional[List[Dict]],
) -> bool:
    """
    True when the latest user line is a short reformulation/clarification of an on-topic thread.

    Used to skip redundant RAG and topic-screening LLM calls on follow-ups like
    "provide the same information in a single line".
    """
    if not conversation_history:
        return False
    text = (user_message or "").strip()
    if not text or len(text) > 280:
        return False

    from utils.input_guard import thread_plausibly_in_product_allowlist

    if not thread_plausibly_in_product_allowlist(conversation_history):
        return False

    if _FOLLOW_UP_REFORMULATION_RE.search(text):
        return True

    # Very short follow-ups in an active thread (e.g. "yes", "go on", "continue")
    if len(text) <= 80:
        return True

    return False
