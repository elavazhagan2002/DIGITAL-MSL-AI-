"""LLM access for the Digital MSL agents.

The agents depend only on the small ``LLMClient`` protocol; by default it is backed by
the host application's ``services.llm_service.LLMService`` (Azure OpenAI / OpenAI /
Anthropic, configured in backend/.env).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    @property
    def is_configured(self) -> bool: ...

    async def generate_async(self, messages: list, temperature: float = 0.0, max_tokens: Optional[int] = None, model: Optional[str] = None) -> str: ...


def default_llm() -> Optional[LLMClient]:
    try:
        from services.llm_service import LLMService

        service = LLMService()
        return service if service.is_configured else None
    except Exception as exc:
        logger.warning("LLM service unavailable: %s", exc)
        return None


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse the first JSON object in an LLM reply (tolerates code fences / prose)."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", str(text or "").strip(), flags=re.MULTILINE).strip()
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        value = json.loads(cleaned[start : end + 1])
        if isinstance(value, dict):
            return value
    raise ValueError("LLM reply did not contain a JSON object")
