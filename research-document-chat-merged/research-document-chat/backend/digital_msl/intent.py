"""Intent router: decides what kind of message this is before any retrieval or LLM call.

Deterministic rules only. No model writes any of the replies here: every reply is fixed, versioned text
filled with the doctor's name, therapeutic areas and local time of day. All wording is
[MLR TO APPROVE] before go-live.

Order (safety is checked by the compliance gate before this runs):
  injection -> greeting / small talk -> capability -> unclear -> clinical in area
  -> clinical outside the doctor's areas -> non-medical off-topic
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from digital_msl.scope import MOLECULE_ALIASES, RETINA_TERMS, classify_query

VERSION = "intent-2026-11-v1"

INJECTION = re.compile(
    r"(ignore|disregard|forget|override)\s+(all\s+|any\s+|your\s+|the\s+)?(previous|prior|above|earlier|system|these)?\s*"
    r"(instructions?|rules?|prompts?|guidelines?)|"
    r"(reveal|show|print|repeat|display|tell me)\s+(me\s+)?(your|the)\s+(system\s+)?(prompt|instructions)|"
    r"system prompt|developer mode|jailbreak|you are now (?:a|an|in)\b|pretend (?:to be|you are)|act as (?:a|an)\b(?!.*\b(?:msl|doctor)\b)",
    re.I,
)

_GREETINGS = {
    "hi", "hello", "hey", "hiya", "howdy", "greetings", "namaste", "namaskar", "gm", "good morning", "good afternoon",
    "good evening", "hi there", "hello there", "hey there", "hola", "vanakkam",
}
_NIGHT = {"gn", "good night", "night"}
_HOW_ARE_YOU = {
    "how are you", "how are you doing", "how are you today", "how r u", "how do you do", "hows it going",
    "how is it going", "whats up", "what's up", "sup", "how are things", "how have you been",
}
_THANKS = {"thanks", "thank you", "ty", "thx", "thank u", "thanks a lot", "many thanks", "thank you very much", "tysm"}
_ACK = {"ok", "okay", "k", "alright", "got it", "fine", "cool", "sure", "understood", "noted"}
_BYE = {"bye", "goodbye", "see you", "cya", "bye bye", "see you later", "take care"}
_CAPABILITY = {
    "who are you", "what can you do", "help", "what do you do", "what is this", "what are you", "who is this",
    "how can you help", "how does this work", "what can i ask", "what can i ask you", "can you help me",
}

_STOP = frozenset(
    "a an and are as at be but by can could did do does for from has have how i in is it its me my of on or our "
    "please so tell that the their there this to us was we were what when where which who why will with would you "
    "your about any some dr doctor sir madam hi hello hey gm thanks thank ok okay".split()
)

# Words that make a short message a real question (specific, not just "eye").
_GENERIC_TERMS = frozenset(
    {"eye", "eyes", "ocular", "ophthalmic", "ophthalmology", "retina", "retinal", "macula", "macular", "vitreous", "vision", "visual"}
)
_GENERAL_MEDICAL = re.compile(
    r"\b(patients?|treatment|therapy|therapies|drug|drugs|dose|dosing|dosage|mg|trial|trials|study|studies|efficacy|safety|"
    r"clinical|disease|diagnos\w+|symptoms?|surgery|surgical|cancer|tumou?rs?|carcinoma|lymphoma|diabetes|diabetic|"
    r"hypertension|cardiac|heart|blood|infection|antibiotic|vaccine|asthma|copd|arthritis|psoriasis|epilepsy|stroke|"
    r"kidney|liver|lung|skin|cornea|glaucoma|cataract|uveitis|pharmac\w+|medic\w+|inject\w+|prescri\w+|side effects?|"
    r"survival|biomarker|guideline|label|indication|contraindication|statins?|cholesterol|lipids?|insulin|glucose|thyroid|hormones?|vitamins?|supplements?|antibiotics?|steroids?|opioids?|chemotherapy|radiotherapy|pregnan\w+|p(?:a)?ediatric\w*|dermat\w+|neuro\w+|cardio\w+|oncolog\w+|gastro\w+|endocrin\w+|immun\w+|rheumat\w+|nephro\w+|pulmon\w+|psychiatr\w+|depress\w+|anxiety|pain|fever|covid|hiv|pressure)\b",
    re.I,
)

_OFF_TOPIC = re.compile(
    r"\b(weather|forecast|jokes?|riddles?|stories|story|poems?|songs?|sing|movies?|cricket|football|soccer|news|recipes?|"
    r"stocks?|bitcoin|crypto|horoscope|lottery|capital of|translate|flight|restaurant)\b",
    re.I,
)

_EMOJI_AND_PUNCT = re.compile(r"[^\w\s']", re.UNICODE)


def normalize(text: str) -> str:
    cleaned = _EMOJI_AND_PUNCT.sub(" ", str(text or "").lower().replace("’", "'"))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if re.fullmatch(r"h+i+", cleaned):
        return "hi"
    if re.fullmatch(r"he+y+", cleaned):
        return "hey"
    if re.fullmatch(r"he+l+o+", cleaned):
        return "hello"
    return cleaned


def _meaningful_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9][a-z0-9\-']*", text) if w not in _STOP and len(w) > 1]


def _specific_term(text: str) -> bool:
    """A molecule, abbreviation or disease term specific enough to be a question on its own."""
    lowered = text.lower()
    for aliases in MOLECULE_ALIASES.values():
        if any(re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", lowered) for a in aliases):
            return True
    for term in RETINA_TERMS:
        if term in _GENERIC_TERMS:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", lowered):
            return True
    return False


@dataclass
class Intent:
    kind: str  # greeting|smalltalk|capability|unclear|clinical|out_of_area|off_topic|injection
    sub: str = ""  # greeting | how_are_you | thanks | ack | bye | night
    remainder: str = ""  # message text after a leading greeting, for clinical routing
    actions: list[str] = field(default_factory=list)

    @property
    def is_fixed_reply(self) -> bool:
        return self.kind != "clinical"


def _strip_leading_greeting(norm: str) -> tuple[bool, str]:
    for phrase in sorted(_GREETINGS | _NIGHT, key=len, reverse=True):
        if norm == phrase:
            return True, ""
        if norm.startswith(phrase + " "):
            return True, norm[len(phrase) :].strip()
    return False, norm


def classify_intent(text: str, *, area_terms: Iterable[str] = (), all_area_terms: Iterable[str] = ()) -> Intent:
    """Classify one message. ``area_terms`` are the doctor's assigned areas' keywords; ``all_area_terms`` every area's."""
    raw = str(text or "")
    if INJECTION.search(raw):
        return Intent("injection")
    norm = normalize(raw)
    if not norm:
        return Intent("unclear")

    greeted, rest = _strip_leading_greeting(norm)
    if greeted and not rest:
        return Intent("greeting", "night" if norm in _NIGHT or norm.startswith("good night") else "greeting")
    if norm in _HOW_ARE_YOU or (greeted and rest in _HOW_ARE_YOU):
        return Intent("smalltalk", "how_are_you")
    if norm in _THANKS:
        return Intent("smalltalk", "thanks")
    if norm in _ACK:
        return Intent("smalltalk", "ack")
    if norm in _BYE:
        return Intent("smalltalk", "bye")
    if norm in _CAPABILITY or (greeted and rest in _CAPABILITY):
        return Intent("capability")

    body = rest if greeted else norm  # "hi, what does the label say about X" is a clinical question
    if _OFF_TOPIC.search(body) and not _GENERAL_MEDICAL.search(body) and not _specific_term(body):
        return Intent("off_topic")
    words = _meaningful_words(body)
    if len(words) < 3 and not _specific_term(body) and not _GENERAL_MEDICAL.search(body) and not any(
        re.search(rf"(?<![a-z0-9]){re.escape(t.lower())}(?![a-z0-9])", body) for t in area_terms if t and t.lower() not in _GENERIC_TERMS
    ):
        return Intent("unclear")

    scope = classify_query(body, list(area_terms))
    if scope.in_scope:
        return Intent("clinical", remainder=body)
    other = [t for t in all_area_terms if t and re.search(rf"(?<![a-z0-9]){re.escape(t.lower())}(?:s|es)?(?![a-z0-9])", body)]
    if other or _GENERAL_MEDICAL.search(body):
        return Intent("out_of_area", actions=["send_to_msl"])
    return Intent("off_topic")


# ---------------------------------------------------------------- fixed replies
def _salutation(display_name: str) -> str:
    base = (display_name or "").strip().split(",")[0].strip()
    if not base:
        return ""
    name = re.sub(r"^dr\.?\s+", "", base, flags=re.I).strip()
    return f"Dr. {name}" if name else ""


def time_greeting(utc_offset_minutes: Optional[int], *, now: Optional[datetime] = None) -> str:
    """Time of day from the doctor's own clock (the browser's getTimezoneOffset), never the server's."""
    if utc_offset_minutes is None:
        return "Hello"
    utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    hour = (utc - timedelta(minutes=int(utc_offset_minutes))).hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def _areas_text(area_names: list[str]) -> str:
    names = [n for n in area_names if n]
    return "; ".join(names) if names else ""


def _examples(area_names: list[str]) -> tuple[str, str]:
    area = area_names[0] if area_names else "your therapeutic area"
    return (
        f"What do the approved sources say about visual acuity outcomes in {area}?",
        f"What does the approved label say about dosing intervals in {area}?",
    )


def build_reply(
    intent: Intent,
    *,
    display_name: str = "",
    area_names: Optional[list[str]] = None,
    utc_offset_minutes: Optional[int] = None,
    now: Optional[datetime] = None,
) -> str:
    areas = area_names or []
    about = _areas_text(areas)
    about_phrase = f"about {about}" if about else "in your therapeutic areas"
    sal = _salutation(display_name)
    who = f", {sal}" if sal else ""
    if intent.kind == "greeting":
        if intent.sub == "night":
            return f"Good night{who}. Come back any time with a scientific question {about_phrase}."
        opener = f"{time_greeting(utc_offset_minutes, now=now)}{who}."
        if not about:
            return f"{opener} I can help with scientific questions once you have chosen a therapeutic area. What would you like to know?"
        return f"{opener} I can help with scientific questions {about_phrase}. What would you like to know?"
    if intent.kind == "smalltalk":
        if intent.sub == "how_are_you":
            return f"I'm ready to help{who}. What would you like to know {about_phrase}?"
        if intent.sub == "thanks":
            return f"You're welcome. Ask me anything else {about_phrase}, or I can pass a question to a Physical MSL."
        if intent.sub == "bye":
            return f"Goodbye{who}. Come back any time with a scientific question {about_phrase}."
        return f"Understood. Ask me a scientific question {about_phrase} whenever you are ready."
    if intent.kind == "capability":
        areas_sentence = f" Your therapeutic areas: {about}." if about else " You have not chosen a therapeutic area yet."
        return (
            "I answer scientific questions with quotes from approved documents, each with its source. "
            "When I cannot find a sourced answer, I pass your question to a Physical MSL, who replies here." + areas_sentence
        )
    if intent.kind == "unclear":
        first, second = _examples(areas)
        return f'Could you tell me a little more? For example: "{first}" or "{second}".'
    if intent.kind == "out_of_area":
        have = f" ({about})" if about else ""
        return (
            f"That looks like a question outside your therapeutic areas{have}. "
            "I can send it to a Physical MSL, who will reply here."
        )
    if intent.kind == "off_topic":
        return f"I can only help with scientific questions {about_phrase}. Please ask a medical or scientific question."
    if intent.kind == "injection":
        return "I can only help with scientific questions answered from approved sources."
    return ""
