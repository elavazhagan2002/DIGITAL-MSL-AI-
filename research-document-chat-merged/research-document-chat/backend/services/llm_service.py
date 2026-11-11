import re
from openai import OpenAI, AsyncOpenAI, AzureOpenAI, AsyncAzureOpenAI
from anthropic import Anthropic, AsyncAnthropic
from typing import Optional, AsyncGenerator, Literal
import logging
from core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

LLMProvider = Literal["openai", "azure_openai", "anthropic", "gemini"]

LLM_NOT_CONFIGURED_MESSAGE = (
    "LLM is not configured. A super admin must set up LLM configuration in Settings before using this feature."
)


class LLMService:
    """Service for interacting with OpenAI, Azure OpenAI, Anthropic, and Gemini LLMs."""

    def __init__(self):
        """Initialize LLM client from persisted system settings."""
        self.provider: Optional[LLMProvider] = None
        self.client = None
        self.async_client = None
        self.model = None
        self._capability_model = None

        # Raw credentials stored for reuse by LangChain adapter
        self._api_key: Optional[str] = None
        self._endpoint: Optional[str] = None
        self._api_version: Optional[str] = None

        self._load_from_system_config()

    @property
    def is_configured(self) -> bool:
        return self.provider is not None and self.client is not None

    def _load_from_system_config(self) -> None:
        try:
            from services.llm_config_service import get_llm_config_service
        except ImportError:
            # Standalone extraction has no persisted LLM settings service; use backend/.env.
            self._load_from_env()
            return
        try:
            cfg = get_llm_config_service().get_internal_config()
            if cfg.get("configured") and cfg.get("api_key"):
                self.apply_config(cfg)
        except Exception as exc:
            logger.warning("LLM initialization from system settings failed: %s", exc)
            self.provider = None

    def _load_from_env(self) -> None:
        provider = (settings.llm_provider or "").strip().lower()
        if provider == "openai":
            cfg = {"provider": "openai", "api_key": settings.openai_api_key, "model": settings.openai_model}
        elif provider == "azure_openai":
            cfg = {
                "provider": "azure_openai",
                "api_key": settings.azure_openai_api_key,
                "model": settings.azure_openai_deployment_name,
                "endpoint": settings.azure_openai_endpoint,
                "api_version": settings.azure_openai_api_version,
                "capability_model": settings.azure_openai_model_name,
            }
        elif provider == "anthropic":
            cfg = {"provider": "anthropic", "api_key": settings.anthropic_api_key, "model": settings.anthropic_model}
        else:
            logger.warning("LLM_PROVIDER %r is not supported; LLM disabled.", provider)
            return
        api_key = str(cfg.get("api_key") or "").strip()
        if not api_key or api_key.startswith("your_"):
            logger.warning("No API key configured for LLM_PROVIDER=%s; LLM disabled.", provider)
            return
        try:
            self.apply_config(cfg)
        except Exception as exc:
            logger.warning("LLM initialization from .env failed: %s", exc)
            self.provider = None
            self.client = None

    def apply_config(self, cfg: dict) -> None:
        """Apply a persisted or in-memory configuration dict to this service."""
        provider = (cfg.get("provider") or "").strip().lower()
        if not provider:
            raise ValueError("Provider is required.")

        api_key = (cfg.get("api_key") or "").strip() or None
        model = (cfg.get("model") or "").strip() or None
        endpoint = (cfg.get("endpoint") or "").strip() or None
        api_version = (cfg.get("apiVersion") or cfg.get("api_version") or "").strip() or None
        capability_model = (cfg.get("capabilityModel") or cfg.get("capability_model") or "").strip() or None

        if provider == "openai":
            self._init_openai(api_key=api_key, model=model)
        elif provider == "azure_openai":
            self._init_azure_openai(
                api_key=api_key,
                endpoint=endpoint,
                deployment_name=model,
                api_version=api_version,
                capability_model=capability_model,
            )
        elif provider == "anthropic":
            self._init_anthropic(api_key=api_key, model=model)
        elif provider == "gemini":
            self._init_gemini(api_key=api_key, model=model)
        else:
            raise ValueError(
                f"Unsupported provider: {provider}. Must be 'openai', 'azure_openai', 'anthropic', or 'gemini'."
            )

    def _client_options(self) -> dict:
        """Shared SDK transport settings for transient provider/network failures."""
        timeout = max(float(settings.llm_timeout_seconds or 45), 1.0)
        max_retries = max(int(settings.llm_max_retries or 0), 0)
        self._timeout_seconds = timeout
        self._max_retries = max_retries
        return {
            "timeout": timeout,
            "max_retries": max_retries,
        }

    def _init_openai(self, api_key: Optional[str] = None, model: Optional[str] = None):
        """Initialize OpenAI client"""
        if not api_key:
            raise ValueError("API key is required for OpenAI provider")

        client_options = self._client_options()
        self.client = OpenAI(api_key=api_key, **client_options)
        self.async_client = AsyncOpenAI(api_key=api_key, **client_options)
        self.model = model or "gpt-4o"
        self._capability_model = self.model
        self._use_max_completion_tokens = self._model_needs_max_completion_tokens(self._capability_model)
        self._api_key = api_key
        self._endpoint = None
        self._api_version = None
        self.provider = "openai"
        logger.info(f"Switched to OpenAI provider with model: {self.model}")

    @staticmethod
    def _model_needs_max_completion_tokens(model_name: str) -> bool:
        """Newer OpenAI models (o1, o3, gpt-4.1, gpt-5+) require max_completion_tokens instead of max_tokens."""
        if not model_name:
            return False
        m = model_name.lower()
        if re.match(r'^(o[0-9]|gpt-[4-9]\.[1-9]|gpt-[5-9])', m):
            return True
        return False

    @staticmethod
    def _model_uses_reasoning_defaults(model_name: str) -> bool:
        """Reasoning models only support default sampling params and use hidden tokens."""
        if not model_name:
            return False
        m = model_name.lower().strip()
        if re.match(r"^o[0-9]", m):
            return True
        # gpt-5.x / luna reject custom temperature (only default 1 is allowed).
        if re.match(r"^gpt-5", m) or "luna" in m:
            return True
        return False

    @staticmethod
    def _anthropic_omit_temperature(model_name: str) -> bool:
        """Newer Claude 4.5+/5 models reject explicit temperature on some API versions."""
        m = (model_name or "").lower()
        if re.match(r"claude-(sonnet|opus|haiku|fable|mythos)-5", m):
            return True
        if re.match(r"claude-(sonnet|opus|haiku)-4-[56]", m):
            return True
        return False

    @staticmethod
    def _anthropic_disable_thinking(model_name: str) -> bool:
        """Claude 5+ enables adaptive thinking by default; turn off for structured compliance calls."""
        m = (model_name or "").lower()
        if re.match(r"claude-(sonnet|opus|haiku|fable|mythos)-5", m):
            return True
        if re.match(r"claude-(sonnet|opus|haiku)-4-[56]", m):
            return True
        return False

    @staticmethod
    def _anthropic_extract_text(response) -> str:
        """Collect assistant text blocks; skip ThinkingBlock and other non-text content."""
        parts: list[str] = []
        for block in getattr(response, "content", None) or []:
            if isinstance(block, dict):
                block_type = block.get("type")
                text = block.get("text")
            else:
                block_type = getattr(block, "type", None)
                text = getattr(block, "text", None)
            if block_type == "text" and text:
                parts.append(text)
            elif block_type is None and text:
                parts.append(text)
        return "".join(parts).strip()

    @staticmethod
    def _anthropic_kwarg_variants(request_kwargs: dict) -> list[dict]:
        """Return request kwargs variants (full → no output_config → no thinking)."""
        variants: list[dict] = []
        seen: set[str] = set()

        def _add(kwargs: dict) -> None:
            key = repr(sorted(kwargs.items()))
            if key not in seen:
                seen.add(key)
                variants.append(dict(kwargs))

        _add(request_kwargs)
        if "output_config" in request_kwargs:
            _add({k: v for k, v in request_kwargs.items() if k != "output_config"})
        if "thinking" in request_kwargs:
            _add(
                {
                    k: v
                    for k, v in request_kwargs.items()
                    if k not in {"thinking", "output_config"}
                }
            )
        return variants

    @staticmethod
    def _anthropic_non_retryable(exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(
            token in msg
            for token in (
                "credit balance",
                "invalid_api_key",
                "authentication",
                "permission",
            )
        )

    def _anthropic_create_sync(self, request_kwargs: dict):
        from anthropic import BadRequestError

        last_exc: Exception | None = None
        for kwargs in self._anthropic_kwarg_variants(request_kwargs):
            try:
                return self.client.messages.create(**kwargs)
            except BadRequestError as exc:
                last_exc = exc
                if self._anthropic_non_retryable(exc):
                    raise
                logger.warning("Anthropic request fallback (sync): %s", exc)
        if last_exc:
            raise last_exc
        raise RuntimeError("Anthropic request failed with no variants")

    async def _anthropic_create_async(self, request_kwargs: dict):
        from anthropic import BadRequestError

        last_exc: Exception | None = None
        for kwargs in self._anthropic_kwarg_variants(request_kwargs):
            try:
                return await self.async_client.messages.create(**kwargs)
            except BadRequestError as exc:
                last_exc = exc
                if self._anthropic_non_retryable(exc):
                    raise
                logger.warning("Anthropic request fallback (async): %s", exc)
        if last_exc:
            raise last_exc
        raise RuntimeError("Anthropic request failed with no variants")

    def _anthropic_response_text(self, response, *, context: str = "anthropic") -> str:
        text = self._anthropic_extract_text(response)
        if text:
            return text
        block_types = [
            getattr(b, "type", None) or (b.get("type") if isinstance(b, dict) else type(b).__name__)
            for b in (getattr(response, "content", None) or [])
        ]
        logger.warning("%s response had no text blocks (types=%s)", context, block_types)
        return ""

    @classmethod
    def _anthropic_request_kwargs(
        cls,
        *,
        model: str,
        messages: list,
        temperature: Optional[float],
        max_tokens: Optional[int],
        system_message: Optional[str] = None,
    ) -> dict:
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens or 4096,
        }
        if not cls._anthropic_omit_temperature(model):
            kwargs["temperature"] = temperature if temperature is not None else 0.7
        if system_message:
            kwargs["system"] = system_message
        if cls._anthropic_disable_thinking(model):
            # Sonnet 5 defaults to adaptive thinking, which burns max_tokens and
            # returns ThinkingBlock objects that break JSON extraction callers.
            kwargs["thinking"] = {"type": "disabled"}
            kwargs["output_config"] = {"effort": "low"}
        return kwargs

    def _capability_model_for(self, request_model: Optional[str] = None) -> str:
        """Return actual model family for parameter decisions, not just deployment alias."""
        requested = (request_model or "").strip() or self.model or ""
        if self.provider == "azure_openai" and requested == self.model and self._capability_model:
            return self._capability_model
        return requested

    def _openai_token_param_for(self, max_tokens: Optional[int], model_name: str) -> dict:
        """Return the correct token-limit parameter for a given OpenAI/Azure model name."""
        if max_tokens is None:
            return {}
        if self._model_needs_max_completion_tokens(model_name):
            token_budget = int(max_tokens)
            if self._model_uses_reasoning_defaults(model_name):
                token_budget = max(token_budget, 256)
            return {"max_completion_tokens": token_budget}
        return {"max_tokens": max_tokens}

    def _openai_token_param(self, max_tokens: Optional[int]) -> dict:
        """Return the correct token-limit parameter for the current OpenAI model."""
        return self._openai_token_param_for(max_tokens, self._capability_model_for())

    def _openai_chat_kwargs(
        self,
        model: str,
        messages: list,
        temperature: Optional[float],
        max_tokens: Optional[int],
        stream: bool = False,
        seed: Optional[int] = None,
    ) -> dict:
        """Build Chat Completions kwargs compatible with standard and reasoning models."""
        capability_model = self._capability_model_for(model)
        kwargs = {
            "model": model,
            "messages": messages,
            **self._openai_token_param_for(max_tokens, capability_model),
        }
        if stream:
            kwargs["stream"] = True
        if not self._model_uses_reasoning_defaults(capability_model) and temperature is not None:
            kwargs["temperature"] = temperature
        if seed is not None:
            kwargs["seed"] = int(seed)
        return kwargs

    def _init_azure_openai(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        deployment_name: Optional[str] = None,
        api_version: Optional[str] = None,
        capability_model: Optional[str] = None,
    ):
        """Initialize Azure OpenAI client"""
        deployment_name = deployment_name or "gpt-4o"
        api_version = api_version or "2024-12-01-preview"
        capability_model = capability_model or deployment_name

        if not api_key or not endpoint:
            raise ValueError("API key and endpoint are required for Azure OpenAI provider")

        client_options = self._client_options()
        self.client = AzureOpenAI(
            api_key=api_key,
            api_version=api_version,
            azure_endpoint=endpoint,
            **client_options,
        )
        self.async_client = AsyncAzureOpenAI(
            api_key=api_key,
            api_version=api_version,
            azure_endpoint=endpoint,
            **client_options,
        )
        self.model = deployment_name
        self._capability_model = capability_model
        self._use_max_completion_tokens = self._model_needs_max_completion_tokens(self._capability_model)
        self._api_key = api_key
        self._endpoint = endpoint
        self._api_version = api_version
        self.provider = "azure_openai"
        logger.info(f"Switched to Azure OpenAI provider with deployment: {self.model}")

    def _init_anthropic(self, api_key: Optional[str] = None, model: Optional[str] = None):
        """Initialize Anthropic Claude client"""
        if not api_key:
            raise ValueError("API key is required for Anthropic provider")

        client_options = self._client_options()
        self.client = Anthropic(api_key=api_key, **client_options)
        self.async_client = AsyncAnthropic(api_key=api_key, **client_options)
        self.model = model or "claude-3-5-sonnet-20241022"
        self._capability_model = self.model
        self._use_max_completion_tokens = False
        self._api_key = api_key
        self._endpoint = None
        self._api_version = None
        self.provider = "anthropic"
        logger.info(f"Switched to Anthropic provider with model: {self.model}")

    def _init_gemini(self, api_key: Optional[str] = None, model: Optional[str] = None):
        """Initialize Gemini via OpenAI-compatible endpoint."""
        if not api_key:
            raise ValueError("API key is required for Gemini provider")

        base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
        client_options = self._client_options()
        self.client = OpenAI(api_key=api_key, base_url=base_url, **client_options)
        self.async_client = AsyncOpenAI(api_key=api_key, base_url=base_url, **client_options)
        self.model = model or "gemini-2.0-flash"
        self._capability_model = self.model
        self._use_max_completion_tokens = False
        self._api_key = api_key
        self._endpoint = base_url
        self._api_version = None
        self.provider = "gemini"
        logger.info(f"Switched to Gemini provider with model: {self.model}")

    def switch_provider(
        self,
        provider: LLMProvider,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        model: Optional[str] = None,
        api_version: Optional[str] = None,
        capability_model: Optional[str] = None,
    ):
        """
        Switch between LLM providers using explicit credentials (in-memory only).
        Prefer saving via LlmConfigService and calling apply_config for persistence.
        """
        self.apply_config(
            {
                "provider": provider,
                "api_key": api_key,
                "endpoint": endpoint,
                "model": model,
                "apiVersion": api_version,
                "capabilityModel": capability_model,
            }
        )

    def reload_from_db(self) -> None:
        """Reload provider credentials from persisted system settings."""
        self.client = None
        self.async_client = None
        self.provider = None
        self._load_from_system_config()

    def get_current_provider(self) -> dict:
        """
        Get information about the current LLM provider

        Returns:
            Dictionary with provider information (never includes API keys)
        """
        return {
            "provider": self.provider,
            "model": self.model,
            "endpoint": self._endpoint if self.provider == "azure_openai" else None,
            "api_version": self._api_version,
            "capability_model": self._capability_model,
            "configured": self.is_configured,
            "is_azure": self.provider == "azure_openai",
            "is_anthropic": self.provider == "anthropic",
            "is_openai": self.provider == "openai",
            "is_gemini": self.provider == "gemini",
        }

    def generate_stream(
        self,
        messages: list,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ):
        """
        Generate streaming response from LLM

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens to generate

        Yields:
            Chunks of generated text
        """
        if not self.client:
            raise ValueError(LLM_NOT_CONFIGURED_MESSAGE)

        try:
            if self.provider == "anthropic":
                # Anthropic Claude API format
                # Convert messages format if needed
                system_message = None
                claude_messages = []

                for msg in messages:
                    if msg["role"] == "system":
                        system_message = msg["content"]
                    else:
                        claude_messages.append(msg)

                stream_kwargs = self._anthropic_request_kwargs(
                    model=self.model,
                    messages=claude_messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    system_message=system_message,
                )

                with self.client.messages.stream(**stream_kwargs) as stream:
                    for text in stream.text_stream:
                        yield text

            else:
                # OpenAI and Azure OpenAI API format
                kwargs = self._openai_chat_kwargs(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                )

                stream = self.client.chat.completions.create(**kwargs)

                for chunk in stream:
                    # Some SDK responses may include chunks without choices/content
                    choices = getattr(chunk, "choices", None)
                    if not choices or len(choices) == 0:
                        continue
                    delta = getattr(choices[0], "delta", None)
                    if delta is None:
                        continue
                    content = getattr(delta, "content", None)
                    if content is not None:
                        yield content

        except Exception as e:
            logger.error(f"Error generating async LLM response: {e!r}")
            raise

    def generate(
        self,
        messages: list,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
    ) -> str:
        """
        Generate non-streaming response from LLM

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens to generate
            model: Optional model or Azure deployment name; defaults to configured primary model

        Returns:
            Generated text response
        """
        if not self.client:
            raise ValueError(LLM_NOT_CONFIGURED_MESSAGE)

        effective_model = (model or "").strip() or self.model

        try:
            if self.provider == "anthropic":
                # Anthropic Claude API format
                system_message = None
                claude_messages = []

                for msg in messages:
                    if msg["role"] == "system":
                        system_message = msg["content"]
                    else:
                        claude_messages.append(msg)

                request_kwargs = self._anthropic_request_kwargs(
                    model=effective_model,
                    messages=claude_messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    system_message=system_message,
                )

                response = self._anthropic_create_sync(request_kwargs)
                return self._anthropic_response_text(response, context="generate")

            else:
                # OpenAI and Azure OpenAI API format
                kwargs = self._openai_chat_kwargs(
                    model=effective_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                response = self.client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content
                return (content or "").strip()

        except Exception as e:
            logger.error(f"Error generating LLM response: {e!r}")
            raise

    async def generate_stream_async(
        self,
        messages: list,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        seed: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Generate async streaming response from LLM

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens to generate
            timeout: Optional per-request timeout in seconds (overrides client default)
            seed: Optional seed for more deterministic sampling (OpenAI/Azure)

        Yields:
            Chunks of generated text
        """
        if not self.async_client:
            raise ValueError(LLM_NOT_CONFIGURED_MESSAGE)

        request_timeout = None
        if timeout is not None:
            request_timeout = max(float(timeout), 1.0)

        try:
            if self.provider == "anthropic":
                # Anthropic Claude API format
                system_message = None
                claude_messages = []

                for msg in messages:
                    if msg["role"] == "system":
                        system_message = msg["content"]
                    else:
                        claude_messages.append(msg)

                stream_kwargs = self._anthropic_request_kwargs(
                    model=self.model,
                    messages=claude_messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    system_message=system_message,
                )
                if request_timeout is not None:
                    stream_kwargs["timeout"] = request_timeout

                async with self.async_client.messages.stream(**stream_kwargs) as stream:
                    async for text in stream.text_stream:
                        yield text

            else:
                # OpenAI and Azure OpenAI API format
                kwargs = self._openai_chat_kwargs(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                    seed=seed,
                )
                if request_timeout is not None:
                    kwargs["timeout"] = request_timeout

                stream = await self.async_client.chat.completions.create(**kwargs)

                async for chunk in stream:
                    # Some SDK responses may include chunks without choices/content
                    choices = getattr(chunk, "choices", None)
                    if not choices or len(choices) == 0:
                        continue
                    delta = getattr(choices[0], "delta", None)
                    if delta is None:
                        continue
                    content = getattr(delta, "content", None)
                    if content is not None:
                        yield content

        except Exception as e:
            logger.error(f"Error generating async LLM response: {e!r}")
            raise

    async def generate_async(
        self,
        messages: list,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
    ) -> str:
        """
        Generate async non-streaming response from LLM

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens to generate
            model: Optional model or Azure deployment name; defaults to configured primary model

        Returns:
            Generated text response
        """
        if not self.async_client:
            raise ValueError(LLM_NOT_CONFIGURED_MESSAGE)

        effective_model = (model or "").strip() or self.model

        try:
            if self.provider == "anthropic":
                # Anthropic Claude API format
                system_message = None
                claude_messages = []

                for msg in messages:
                    if msg["role"] == "system":
                        system_message = msg["content"]
                    else:
                        claude_messages.append(msg)

                request_kwargs = self._anthropic_request_kwargs(
                    model=effective_model,
                    messages=claude_messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    system_message=system_message,
                )

                response = await self._anthropic_create_async(request_kwargs)
                return self._anthropic_response_text(response, context="generate_async")

            else:
                # OpenAI and Azure OpenAI API format
                kwargs = self._openai_chat_kwargs(
                    model=effective_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                response = await self.async_client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content
                return (content or "").strip()

        except Exception as e:
            logger.error(f"Error generating async LLM response: {e!r}")
            raise

    async def generate_async_with_retry(
        self,
        messages: list,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        *,
        source: str = "LLMService",
        max_retries: Optional[int] = None,
    ) -> str:
        """Like generate_async but with shared rate-limit aware retries."""
        import asyncio
        from utils.llm_invoker import _compute_retry_delay, is_llm_transient_error

        retries = max_retries if max_retries is not None else max(self._max_retries, 5)
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                return await self.generate_async(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    model=model,
                )
            except Exception as exc:
                last_error = exc
                if not is_llm_transient_error(exc) or attempt >= retries - 1:
                    raise
                delay = _compute_retry_delay(exc, attempt)
                logger.warning(
                    "%s transient error (attempt %d/%d). Waiting %.2fs: %s",
                    source,
                    attempt + 1,
                    retries,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
        if last_error:
            raise last_error
        raise RuntimeError(f"{source}: LLM call failed without an exception")
