"""
Central async LLM invocation with rate-limit aware retries.

All drafting agents should route LangChain model.ainvoke() calls through
``ainvoke_with_retry`` so OpenAI/Azure 429 TPM limits are handled by waiting
for the provider-specified delay instead of failing immediately.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_LLM_GLOBAL_CONCURRENCY = max(
    1,
    int(os.getenv("LLM_GLOBAL_CONCURRENCY", os.getenv("SECTION_AGENT_LLM_CONCURRENCY", "6")) or "6"),
)
_LLM_MAX_RETRIES = max(
    1,
    int(os.getenv("LLM_MAX_RETRIES", os.getenv("SECTION_AGENT_LLM_MAX_RETRIES", "5")) or "5"),
)
_LLM_BASE_DELAY_SECONDS = max(
    0.5,
    float(os.getenv("LLM_BASE_DELAY_SECONDS", os.getenv("SECTION_AGENT_LLM_BASE_DELAY_SECONDS", "1.0")) or "1.0"),
)
_LLM_MIN_INTERVAL_SECONDS = max(
    0.0,
    float(os.getenv("LLM_MIN_INTERVAL_SECONDS", "0.25") or "0.25"),
)

_global_llm_semaphore = asyncio.Semaphore(_LLM_GLOBAL_CONCURRENCY)
_last_llm_call_at = 0.0
_llm_interval_lock = asyncio.Lock()

_RETRY_AFTER_RE = re.compile(
    r"try again in\s+(\d+(?:\.\d+)?)\s*s",
    re.IGNORECASE,
)


def parse_rate_limit_retry_seconds(exc: Exception) -> float | None:
    """Extract provider-suggested wait time from a 429 / rate-limit error."""
    text = str(exc)
    match = _RETRY_AFTER_RE.search(text)
    if match:
        try:
            return max(float(match.group(1)), 0.5)
        except (TypeError, ValueError):
            pass
    return None


def is_llm_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    class_name = exc.__class__.__name__.lower()
    return (
        "ratelimit" in class_name
        or "rate_limit" in text
        or "too many requests" in text
        or "error code: 429" in text
        or "status code: 429" in text
        or "rate limit" in text
    )


def is_llm_transient_error(exc: Exception) -> bool:
    text = str(exc).lower()
    class_name = exc.__class__.__name__.lower()
    transient_markers = (
        "apiconnectionerror",
        "api connection",
        "connection error",
        "connecterror",
        "peer closed connection",
        "remoteprotocolerror",
        "incomplete message body",
        "connection closed",
        "connection aborted",
        "connection refused",
        "name or service not known",
        "temporary failure in name resolution",
        "nodename nor servname",
        "getaddrinfo failed",
        "network is unreachable",
        "timeout",
        "timed out",
        "readtimeout",
        "connecttimeout",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
        "status code: 500",
        "status code: 502",
        "status code: 503",
        "status code: 504",
        "error code: 500",
        "error code: 502",
        "error code: 503",
        "error code: 504",
        "remoteprotocol",
        "peer closed",
        "incomplete message body",
        "connection closed",
    )
    return is_llm_rate_limit_error(exc) or any(
        marker in class_name or marker in text for marker in transient_markers
    )


def _compute_retry_delay(exc: Exception, attempt: int) -> float:
    provider_wait = parse_rate_limit_retry_seconds(exc)
    if provider_wait is not None:
        return min(120.0, provider_wait + random.uniform(0.1, 0.75))
    if is_llm_rate_limit_error(exc):
        return min(120.0, _LLM_BASE_DELAY_SECONDS * (2 ** attempt) + random.uniform(0.5, 2.0))
    text = str(exc).lower()
    # DNS / connection blips need a longer cool-down than generic timeouts
    if any(
        m in text
        for m in (
            "name or service not known",
            "connection error",
            "connecterror",
            "connection refused",
            "network is unreachable",
        )
    ):
        return min(60.0, 2.0 * (2 ** attempt) + random.uniform(0.5, 2.0))
    return min(45.0, _LLM_BASE_DELAY_SECONDS * (2 ** attempt) + random.uniform(0.25, 1.5))


async def _enforce_min_interval() -> None:
    global _last_llm_call_at
    if _LLM_MIN_INTERVAL_SECONDS <= 0:
        return
    async with _llm_interval_lock:
        now = time.perf_counter()
        elapsed = now - _last_llm_call_at
        if elapsed < _LLM_MIN_INTERVAL_SECONDS:
            await asyncio.sleep(_LLM_MIN_INTERVAL_SECONDS - elapsed)
        _last_llm_call_at = time.perf_counter()


async def ainvoke_with_retry(
    model,
    messages,
    *,
    source: str = "llm",
    max_retries: int | None = None,
    on_success: Callable[[Any], None] | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> Any:
    """
    Invoke a LangChain chat model with global concurrency control and retries.

    Args:
        model: LangChain chat model instance.
        messages: Messages list/dict accepted by model.ainvoke().
        source: Label for logs and token tracking.
        max_retries: Override default retry count.
        on_success: Optional callback invoked with the model response on success
            (e.g. drafting token tracker).
        semaphore: Optional semaphore; defaults to global LLM semaphore.
    """
    sem = semaphore or _global_llm_semaphore
    retries = max_retries if max_retries is not None else _LLM_MAX_RETRIES
    last_error: Exception | None = None

    for attempt in range(retries):
        try:
            await _enforce_min_interval()
            async with sem:
                response = await model.ainvoke(messages)
            if on_success:
                on_success(response)
            return response
        except Exception as exc:
            last_error = exc
            if not is_llm_transient_error(exc) or attempt >= retries - 1:
                raise
            delay = _compute_retry_delay(exc, attempt)
            logger.warning(
                "%s transient LLM error (attempt %d/%d). Waiting %.2fs: %s",
                source,
                attempt + 1,
                retries,
                delay,
                exc,
            )
            await asyncio.sleep(delay)

    if last_error:
        raise last_error
    raise RuntimeError(f"{source}: LLM invocation failed without an exception")
