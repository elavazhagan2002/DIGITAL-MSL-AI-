"""Follow-up question generation and session feedback formatting for chat."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from services.llm_service import LLMService

logger = logging.getLogger(__name__)

_MAX_FOLLOW_UPS = 3


def heuristic_follow_up_questions(
    user_message: Optional[str],
    assistant_response: Optional[str],
) -> List[str]:
    """Lightweight fallback when LLM suggestion generation is unavailable."""
    user = (user_message or "").strip()
    answer = (assistant_response or "").strip()
    if not user and not answer:
        return []

    questions: List[str] = []
    if user:
        questions.append(f"Can you explain more about { _short_topic(user) }?")
        questions.append(f"What are the GxP considerations for { _short_topic(user) }?")
        questions.append("Can you summarize the key points in a shorter format?")
    elif answer:
        questions.append("Can you expand on the most important point?")
        questions.append("What related documents or SOP topics should I review next?")
        questions.append("Can you provide a practical example?")

    seen: set[str] = set()
    unique: List[str] = []
    for question in questions:
        normalized = question.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(question.strip())
        if len(unique) >= _MAX_FOLLOW_UPS:
            break
    return unique


def _short_topic(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip().rstrip("?.!")
    if len(cleaned) <= 72:
        return cleaned.lower()
    return cleaned[:69].rsplit(" ", 1)[0].lower() + "..."


async def generate_follow_up_questions(
    llm_service: Optional["LLMService"],
    user_message: Optional[str],
    assistant_response: Optional[str],
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> List[str]:
    """Return up to three contextual follow-up questions for the latest turn."""
    fallback = heuristic_follow_up_questions(user_message, assistant_response)
    if llm_service is None or not getattr(llm_service, "async_client", None):
        return fallback

    user = (user_message or "").strip()
    answer = (assistant_response or "").strip()
    if not answer:
        return fallback

    context_lines: List[str] = []
    for msg in (conversation_history or [])[-4:]:
        role = str(msg.get("role") or "user").capitalize()
        content = str(msg.get("content") or "").strip()
        if content:
            context_lines.append(f"{role}: {content[:400]}")

    prompt = (
        "You help users continue a regulated QMS / GxP document chat.\n"
        "Suggest exactly 3 concise follow-up questions the user might ask next.\n"
        "Questions must stay on the same topic, be specific, and be under 120 characters each.\n"
        "Return ONLY a JSON array of 3 strings. No markdown.\n\n"
    )
    if context_lines:
        prompt += "Recent conversation:\n" + "\n".join(context_lines) + "\n\n"
    prompt += f"Latest user message:\n{user[:600]}\n\nLatest assistant answer:\n{answer[:1200]}"

    try:
        from core.config import get_settings

        intent_model = (get_settings().intent_recognition_model or "").strip() or None
        raw = await llm_service.generate_async(
            messages=[
                {"role": "system", "content": "Return valid JSON arrays only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.35,
            max_tokens=220,
            model=intent_model,
        )
        parsed = _parse_question_array(raw)
        if parsed:
            return parsed
    except Exception as exc:
        logger.warning("Follow-up question generation failed: %s", exc)
    return fallback


def _parse_question_array(raw: str) -> List[str]:
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", text)
        if not match:
            return []
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, list):
        return []
    questions: List[str] = []
    for item in parsed:
        question = str(item or "").strip()
        if question:
            questions.append(question)
        if len(questions) >= _MAX_FOLLOW_UPS:
            break
    return questions


def format_session_feedback_for_prompt(feedback_lines: List[str]) -> str:
    """Turn stored feedback notes into system-prompt guidance."""
    cleaned = [line.strip() for line in feedback_lines if isinstance(line, str) and line.strip()]
    if not cleaned:
        return ""
    return (
        "Use the following user feedback from this session to improve upcoming answers "
        "(tone, depth, structure, and focus):\n"
        + "\n".join(f"- {line}" for line in cleaned[-8:])
    )
