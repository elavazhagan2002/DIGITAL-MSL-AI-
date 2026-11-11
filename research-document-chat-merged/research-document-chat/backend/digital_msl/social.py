"""Short social greetings — no repository retrieval."""
from __future__ import annotations

import re
from datetime import datetime

_SOCIAL_EXACT = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "howdy",
        "hiya",
        "yo",
        "greetings",
        "good morning",
        "good afternoon",
        "good evening",
        "how are you",
        "how are you?",
        "how are you doing",
        "how are you doing?",
        "what's up",
        "whats up",
        "sup",
        "thanks",
        "thank you",
        "ok",
        "okay",
    }
)

_SOCIAL_PATTERNS = (
    re.compile(r"^h+i+[!?.]*$", re.I),
    re.compile(r"^he+y+[!?.]*$", re.I),
    re.compile(r"^he+l+o+[!?.]*$", re.I),
    re.compile(r"^(hi|hello|hey|howdy|hiya|yo|greetings)\b[!?.]*$", re.I),
    re.compile(r"^good (morning|afternoon|evening)\b[!?.]*$", re.I),
    re.compile(r"^how are you\b", re.I),
    re.compile(r"^(thanks|thank you)\b[!?.]*$", re.I),
)


def _normalize_social_text(text: str) -> str:
    """Lowercase, trim punctuation, collapse stretched spellings (hii → hi)."""
    raw = (text or "").strip()
    if not raw:
        return ""
    lowered = re.sub(r"\s+", " ", raw.lower()).strip("!.? ")
    lowered = re.sub(r"[!?.]+$", "", lowered).strip()
    if len(lowered) <= 20:
        if re.fullmatch(r"h+i+", lowered):
            return "hi"
        if re.fullmatch(r"he+y+", lowered):
            return "hey"
        if re.fullmatch(r"he+l+o+", lowered):
            return "hello"
        if re.fullmatch(r"h+i+a+", lowered):
            return "hiya"
    return lowered


def is_social_greeting(text: str) -> bool:
    """True for short conversational openers without a medical question."""
    lowered = _normalize_social_text(text)
    if not lowered:
        return False
    if lowered in _SOCIAL_EXACT:
        return True
    if len(lowered.split()) > 8:
        return False
    return any(p.match(lowered) for p in _SOCIAL_PATTERNS)


def _salutation(display_name: str) -> str:
    trimmed = (display_name or "").strip()
    if not trimmed:
        return ""
    base = trimmed.split(",")[0].strip()
    if not base:
        return ""
    if re.match(r"^dr\.?\s", base, re.I):
        return base
    name = re.sub(r"^dr\.?\s+", "", base, flags=re.I).strip()
    return f"Dr. {name}" if name else ""


def _time_greeting(when: datetime | None = None) -> str:
    hour = (when or datetime.now()).hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def social_greeting_reply(display_name: str = "") -> str:
    sal = _salutation(display_name)
    if sal:
        return (
            f"{_time_greeting()}, {sal}! "
            "I'm Digital MSL, your scientific information assistant. "
            "How can I help you today?"
        )
    return (
        "Hello! I'm Digital MSL, your scientific information assistant. "
        "How can I help you today?"
    )
