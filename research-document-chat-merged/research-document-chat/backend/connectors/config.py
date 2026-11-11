from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MLR_ENV_DIR = _REPO_ROOT / "Medical Literacy Research"
_MLR_ENV_FILE = _MLR_ENV_DIR / ".env"
# Also support gxp-doc-ai layout: backend/vendor/src/... → backend/.env
_BACKEND_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def _load_dotenv_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_config() -> MedicalLiteracyConfig:
    _load_dotenv_file(_MLR_ENV_FILE)
    _load_dotenv_file(_BACKEND_ENV_FILE)
    return MedicalLiteracyConfig.from_env()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class MlrChatRetrievalSettings:
    """Tunable retrieval knobs for Medical Literacy Research general chat."""

    initial_top_k: int
    detail_top_k: int
    max_expanded_queries: int
    use_rerank: bool
    expand_query: bool
    parallel_retrieval: bool
    single_retrieve_call: bool
    chunk_max_chars: int

    @classmethod
    def from_env(cls) -> MlrChatRetrievalSettings:
        _load_dotenv_file(_MLR_ENV_FILE)
        return cls(
            initial_top_k=max(1, int(os.getenv("MLR_CHAT_INITIAL_TOP_K", "6"))),
            detail_top_k=max(1, int(os.getenv("MLR_CHAT_DETAIL_TOP_K", "8"))),
            max_expanded_queries=max(1, int(os.getenv("MLR_CHAT_MAX_EXPANDED_QUERIES", "1"))),
            use_rerank=_env_bool("MLR_CHAT_USE_RERANK", True),
            expand_query=_env_bool("MLR_CHAT_EXPAND_QUERY", True),
            parallel_retrieval=_env_bool("MLR_CHAT_PARALLEL_RETRIEVAL", True),
            single_retrieve_call=_env_bool("MLR_CHAT_SINGLE_RETRIEVE_CALL", True),
            chunk_max_chars=max(200, int(os.getenv("MLR_CHAT_CHUNK_MAX_CHARS", "1500"))),
        )


def load_chat_retrieval_settings() -> MlrChatRetrievalSettings:
    return MlrChatRetrievalSettings.from_env()


@dataclass(frozen=True)
class MedicalLiteracyConfig:
    pubmed_api_key: str
    pubmed_email: str
    pubmed_url: str
    clinicaltrials_url: str
    pubmed_max_results: int
    clinicaltrials_max_results: int
    llm_provider: str
    llm_timeout_seconds: int
    azure_openai_api_key: str
    azure_openai_endpoint: str
    azure_openai_deployment: str
    azure_openai_api_version: str
    openai_api_key: str
    openai_model: str
    llm_temperature: float | None

    @classmethod
    def from_env(cls) -> MedicalLiteracyConfig:
        provider = os.getenv("MLR_LLM_PROVIDER", "azure_openai").strip().lower()
        temp_raw = os.getenv("MLR_LLM_TEMPERATURE", "").strip()
        if temp_raw:
            llm_temperature: float | None = float(temp_raw)
        elif provider == "openai":
            llm_temperature = 0.2
        else:
            # Some Azure deployments only accept the default temperature (1).
            llm_temperature = None
        return cls(
            pubmed_api_key=os.getenv("PUBMED_API_KEY", "").strip(),
            pubmed_email=os.getenv("PUBMED_EMAIL", "").strip(),
            pubmed_url=os.getenv(
                "PUBMED_URL",
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
            ).rstrip("/"),
            clinicaltrials_url=os.getenv(
                "CLINICALTRIALS_URL",
                "https://clinicaltrials.gov/api/v2",
            ).rstrip("/"),
            pubmed_max_results=int(os.getenv("MLR_PUBMED_MAX_RESULTS", "25")),
            clinicaltrials_max_results=int(os.getenv("MLR_CLINICALTRIALS_MAX_RESULTS", "25")),
            llm_provider=os.getenv("MLR_LLM_PROVIDER", "azure_openai").strip().lower(),
            llm_timeout_seconds=int(os.getenv("MLR_LLM_TIMEOUT_SECONDS", "180")),
            azure_openai_api_key=os.getenv("AZURE_OPENAI_API_KEY", "").strip(),
            azure_openai_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", "").strip().rstrip("/"),
            azure_openai_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "").strip(),
            azure_openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview").strip(),
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip(),
            llm_temperature=llm_temperature,
        )

    def llm_configured(self) -> bool:
        if self.llm_provider == "openai":
            return bool(self.openai_api_key)
        return bool(
            self.azure_openai_api_key
            and self.azure_openai_endpoint
            and self.azure_openai_deployment
        )

    def resolve_pubmed_sources(self, repo_sources: dict | None) -> dict[str, str]:
        sources = dict(repo_sources or {})
        return {
            "api_key": str(sources.get("pubmed_api_key") or self.pubmed_api_key).strip(),
            "email": str(sources.get("pubmed_email") or self.pubmed_email).strip(),
            "base_url": str(sources.get("pubmed_url") or self.pubmed_url).rstrip("/"),
        }

    def resolve_clinicaltrials_url(self, repo_sources: dict | None) -> str:
        sources = dict(repo_sources or {})
        return str(sources.get("clinicaltrials_url") or self.clinicaltrials_url).rstrip("/")
