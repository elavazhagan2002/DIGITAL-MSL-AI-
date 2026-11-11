"""
Lightweight prompt-injection and scope heuristics for chat agents.

These checks are intentionally conservative pattern matches; they complement
(rather than replace) system-prompt guardrails and must not log raw user text
at high verbosity in production.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

# Attempts to override system/developer policy (English-centric; extend as needed).
_INJECTION_PATTERNS = [
    re.compile(
        r"\bignore\s+("
        r"all\s+(your\s+|prior\s+|previous\s+|earlier\s+)?"
        r"|your\s+"
        r"|any\s+"
        r"|the\s+"
        r")?"
        r"(instructions|rules|guidelines|policies|prompts?)\b",
        re.I,
    ),
    re.compile(
        r"\b(disregard|forget)\s+(all|any|the|your)\s+"
        r"(prior|previous|earlier)?\s*(instructions|rules|guidelines|policies)",
        re.I,
    ),
    re.compile(
        r"\byou\s+are\s+now\s+",
        re.I,
    ),
    re.compile(
        r"\bpretend\s+(you\s+are|to\s+be)\b",
        re.I,
    ),
    re.compile(
        r"\b(jailbreak|dan\s+mode|developer\s+mode)\b",
        re.I,
    ),
    re.compile(
        r"\b(reveal|print|leak|show)\s+(me\s+)?(your|the)\s+"
        r"(system|hidden|initial)\s+",
        re.I,
    ),
    re.compile(
        r"\b(bypass|disable|turn\s+off)\s+(your|the)\s+"
        r"(restrictions|filters|safety|guardrails?|rules)",
        re.I,
    ),
]


def looks_like_prompt_injection(text: Optional[str]) -> bool:
    """Return True if the message resembles a prompt-injection attempt."""
    if not text or not isinstance(text, str):
        return False
    t = text.strip()
    if len(t) < 8:
        return False
    return any(p.search(t) for p in _INJECTION_PATTERNS)


def thread_contains_injection_attempt(
    conversation_history: Optional[List[Dict]],
    max_messages: int = 12,
) -> bool:
    """True if any recent user message looks like a prompt-injection attempt."""
    if not conversation_history:
        return False
    tail = conversation_history[-max_messages:]
    for msg in tail:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str) and looks_like_prompt_injection(content):
            return True
    return False


_REFUSAL_MESSAGE = (
    "I can only assist with quality-management topics, your workspace documents, "
    "and this product's supported workflows. I cannot change or override my "
    "operating rules. Please ask a question related to your SOPs, QMS, or "
    "document tasks."
)

_OFF_TOPIC_REFUSAL_MESSAGE = (
    "I'm limited to quality management, regulatory topics, and your workspace "
    "documents. I can't help with weather, movies, sports, shopping, or other "
    "general consumer topics — including workarounds, \"how to look it up\" "
    "guides, or lists of apps or websites. Ask about your SOPs, documents, or "
    "supported QMS tasks instead."
)

# Consumer / chit-chat the product must not answer (high-precision phrases).
_OFF_TOPIC_CONSUMER_PATTERNS = [
    re.compile(r"\bwhat\s*['']?s\s+the\s+weather\b", re.I),
    re.compile(r"\bweather\s+(in|for|at|near)\b", re.I),
    re.compile(r"\bweather\s+today\b", re.I),
    # Forecast only when phrased like a consumer weather check (not "sales forecast")
    re.compile(
        r"\bforecast\b.*\b("
        r"quickly|today|tonight|now|temperature|rain|snow|humidity|outside|"
        r"atlanta|local|current|hourly|weekly)\b",
        re.I,
    ),
    re.compile(
        r"\b(quickly|how\s+to|where\s+to|current|check)\b.*\bforecast\b",
        re.I,
    ),
    re.compile(r"\b(accuweather|weather\.gov|weather\s+channel)\b", re.I),
    re.compile(
        r"\b(show\s+you\s+how|show\s+me\s+how)\s+to\s+check\b.*\bforecast\b",
        re.I,
    ),
    re.compile(r"\bhow\s+to\s+check\s+the\s+current\s+forecast\b", re.I),
    re.compile(r"\binterpret\s+a\s+weather\s+report\b", re.I),
    # Movies / entertainment
    re.compile(r"\bmovie(s)?\s+to\s+watch\b", re.I),
    re.compile(r"\bsuggest\s+(a\s+)?(good\s+)?(movie|film)\b", re.I),
    re.compile(r"\bgood\s+movie\b", re.I),
    re.compile(r"\b(can\s+you\s+suggest|please\s+suggest)\s+.*\bmovie\b", re.I),
    re.compile(r"\b(movie|film)\s+recommend\b", re.I),
]


def looks_like_off_topic_consumer_request(text: Optional[str]) -> bool:
    """True for common off-domain consumer intents (weather, movies, etc.)."""
    if not text or not isinstance(text, str):
        return False
    t = text.strip()
    if len(t) < 4:
        return False
    return any(p.search(t) for p in _OFF_TOPIC_CONSUMER_PATTERNS)


def thread_contains_off_topic_consumer_request(
    conversation_history: Optional[List[Dict]],
    max_messages: int = 14,
) -> bool:
    """True if any recent user turn looks like consumer off-topic chat."""
    if not conversation_history:
        return False
    for msg in conversation_history[-max_messages:]:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str) and looks_like_off_topic_consumer_request(content):
            return True
    return False


def refusal_message() -> str:
    """Short, product-consistent refusal without calling an LLM."""
    return _REFUSAL_MESSAGE


def off_topic_refusal_message() -> str:
    """Refusal for disallowed consumer / general-internet topics (no LLM)."""
    return _OFF_TOPIC_REFUSAL_MESSAGE


# --- Strict product allowlist (general / central gate before LLM) ----------------

_OUT_OF_SCOPE_ALLOWLIST_REFUSAL = (
    "I only support document validation, document impact analysis, document guidance, "
    "document drafting or writing in this application, searching or explaining your "
    "workspace documents, QMS topics, regulated quality practices, pharmaceutical "
    "and biotech manufacturing, process equipment, applicable regulations, SOPs, and URS. "
    "I can’t help with that request as stated—please ask something within those areas."
)

_COURTESY_REPLY = (
    "Hello — I support document validation, impact analysis, guidance, drafting, "
    "document questions, QMS, pharmaceutical manufacturing, equipment, regulations, "
    "SOPs, and URS in this app. What would you like to work on?"
)

_COURTESY_ONLY = re.compile(
    r"^(hi|hello|hey|good\s+(morning|afternoon|evening))[\s!.]*$"
    r"|^(thanks?|thank\s+you|thx)[\s!.]*$"
    r"|^(ok\.?|okay|yes|no|bye|goodbye)[\s!.]*$",
    re.I,
)

# Signals the message is plausibly about the allowed product surface (not exhaustive).
_IN_SCOPE_TOPIC_RE = re.compile(
    r"\b("
    r"sop|standard\s+operating\s+procedure|urs|user\s+requirements?\s+specification|"
    r"user\s+requirements?\b|frs|functional\s+requirements?\s+specification|"
    r"qms|quality\s+(management(\s+system)?|system|unit|control|assurance|manual|"
    r"record|risk|department)|"
    r"\b(?:gmp|gxp|glp|gcp|gdp)\b|\bfda\b|ich\b|iso\s*\d*|regulat|compliance|"
    r"audit|inspecti?|validation|qualification|\biq\b|\boq\b|\bpq\b|commissioning|"
    r"protocol|deviation|\bcapa\b|\boos\b|\boot\b|\booc\b|\booac\b|"
    r"out\s+of\s+specification|out\s+of\s+trend|out\s+of\s+calibration|quality\s+event|"
    r"change\s+control|impact\s+analysis|affected\s+documents|"
    r"documents?\b|documentation|procedure|work\s+instruction|\bwi\b|template|"
    r"specifications?\b|requirements?\b|batch\s+record|data\s+integrity|alcoa|csv\b|"
    r"electronic\s+record|part\s*11|annex\s*11|traceabilit|risk\s+assessment|"
    r"calibration|stability|release|supplier|vendor|hazard|"
    r"repository|workspace|library|knowledge\s+base|vectoriz|"
    r"how\s+many\s+documents?|citation|retriev|\[source|appendix|revision|approval|"
    r"training|competenc|gamp|design\s+control|lifecycle|cleaning|steriliz|"
    r"controlled\s+document|master\s+record|deviation|investigation|"
    r"regulatory\s+filing|submission|dossier|"
    # Pharmaceutical / biotech manufacturing, facilities, and equipment (on-topic for QMS chat)
    r"pharma(?:ceutical)?s?|biotech|biopharma|medicinal\s+product|life\s*sciences?|"
    r"drug\s+substance|drug\s+product|\bapi\b|active\s+pharmaceutical|"
    r"manufactur|production|process\s+equipment|shop\s+floor|facility\b|"
    r"equipments?|fermenter|fermentation|bioreactor|autoclave|lyophilizer|"
    r"centrifuge|chromatograph|vessel|skid|filling|fill[\s-]*finish|"
    r"aseptic|sterile\s+processing|cleanroom|isolator|granulation|"
    r"tablet|capsule|blister|packaging\s+line|process\s+validation|cleaning\s+validation"
    r")\b",
    re.I,
)

_DRAFTING_INTENT_RE = re.compile(
    r"\b(draft|drafting|create|generat|write|author|prepare)\b[\s\S]{0,140}?\b("
    r"sop|urs|protocol|document|report|specification|template"
    r")\b|\b(sop|urs|protocol)\b[\s\S]{0,100}?\b(draft|write|create|generat)\b",
    re.I,
)

# If the thread was on-topic but the new user line clearly shifts to unrelated domains,
# do not treat it as a short follow-up.
_UNRELATED_TOPIC_SHIFT = re.compile(
    r"\b(mars|trivia|movie|netflix|weather|python|javascript|typescript|"
    r"recipe|cooking|sports|football|basketball|crypto|bitcoin|stock\s+market|"
    r"celebrit|gossip|travel\s+deal|hotel\s+booking)\b",
    re.I,
)


def text_plausibly_in_product_allowlist(text: Optional[str]) -> bool:
    """Heuristic: message appears on-topic for QMS / documents / SOP / URS."""
    if not text or not isinstance(text, str):
        return False
    sample = text.strip()[:15000]
    if not sample:
        return False
    if _IN_SCOPE_TOPIC_RE.search(sample):
        return True
    if _DRAFTING_INTENT_RE.search(sample):
        return True
    return False


def thread_plausibly_in_product_allowlist(
    conversation_history: Optional[List[Dict]],
    max_messages: int = 14,
) -> bool:
    """True if any recent turn looks on-topic (supports short follow-ups)."""
    if not conversation_history:
        return False
    for msg in conversation_history[-max_messages:]:
        content = msg.get("content")
        if isinstance(content, str) and text_plausibly_in_product_allowlist(content):
            return True
    return False


def resolve_general_product_scope_gate(
    user_message: Optional[str],
    conversation_history: Optional[List[Dict]],
    has_uploaded_content: bool,
    session_has_vectorized_files: bool,
    has_selected_repository: bool = False,
) -> Optional[str]:
    """
    When non-None, the caller should stream this string as the full reply and **not**
    invoke the LLM. None means the message may proceed (still subject to other guards).
    """
    # Selected-repo / upload context means the user is asking about their documents —
    # allow RAG even when the wording is clinical/scientific rather than QMS jargon.
    if has_uploaded_content or session_has_vectorized_files or has_selected_repository:
        return None
    if not user_message or not isinstance(user_message, str):
        return None
    t = user_message.strip()
    if not t:
        return None
    if _COURTESY_ONLY.match(t):
        return _COURTESY_REPLY
    if len(t) < 4:
        return None
    if text_plausibly_in_product_allowlist(t):
        return None
    if (
        len(t) <= 120
        and thread_plausibly_in_product_allowlist(conversation_history)
        and not _UNRELATED_TOPIC_SHIFT.search(t)
    ):
        return None
    return _OUT_OF_SCOPE_ALLOWLIST_REFUSAL


def out_of_scope_allowlist_refusal_message() -> str:
    """Fixed refusal when the message is outside the product allowlist."""
    return _OUT_OF_SCOPE_ALLOWLIST_REFUSAL


def scope_gate_reply_for_agent(
    user_message: Optional[str],
    conversation_history: Optional[List[Dict]],
    uploaded_files: Optional[List[Dict]],
    session_context: Optional[Dict],
) -> Optional[str]:
    """
    Shared pre-LLM gate for chat agents: same rules as ``resolve_general_product_scope_gate``,
    with upload/session file context derived from agent arguments.
    """
    has_uploaded = bool(
        uploaded_files
        and len(uploaded_files) > 0
        and any(f.get("content") for f in uploaded_files)
    )
    fid: list = []
    if session_context and session_context.get("uploaded_file_ids"):
        fid = list(session_context.get("uploaded_file_ids") or [])
    has_selected_repository = bool(
        session_context and str(session_context.get("repository_id") or "").strip()
    )
    return resolve_general_product_scope_gate(
        user_message,
        conversation_history,
        has_uploaded,
        len(fid) > 0,
        has_selected_repository=has_selected_repository,
    )
