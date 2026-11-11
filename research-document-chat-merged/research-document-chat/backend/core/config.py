from typing import Optional
from pydantic_settings import BaseSettings
from functools import lru_cache
from pydantic import AliasChoices, computed_field, Field

# Used when AUTH_CORS_ORIGINS is unset or empty (empty .env entry otherwise yields [] and breaks browser CORS).
_DEFAULT_AUTH_CORS_ORIGINS: tuple[str, ...] = (
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
)


class Settings(BaseSettings):
    # Application settings
    app_name: str = "GxP Docs AI"
    app_version: str = "1.0.0"
    host: str = "localhost"
    port: int = 8087

    # Optional override for user-facing URLs (e.g. "/api" when behind a reverse proxy).
    # When set, backend_url returns this value instead of computing from host/port.
    backend_public_url: str = ""

    # Computed field for backend_url
    @computed_field
    @property
    def backend_url(self) -> str:
        if self.backend_public_url:
            return self.backend_public_url.rstrip("/")
        if self.host == "0.0.0.0" or self.host == "localhost":
            return f"http://localhost:{self.port}"
        return f"https://{self.host}:{self.port}"

    # Embedding settings
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # Chunking settings (Word-friendly): keep within embedding model's token limit
    chunk_size: int = 150
    chunk_overlap: int = 2

    # Reranker settings
    enable_reranking: bool = False  # Set False to skip cross-encoder reranking (faster, less precise)
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L6-v2"

    # Retrieval filtering: min score (0–1) to keep a chunk; None = no filtering. Set e.g. 0.3 to reduce noise.
    retrieval_min_score: Optional[float] = None

    # Hybrid search settings (alpha can be overridden per call for e.g. keyword-heavy compliance)
    hybrid_search_alpha: float = 0.5  # 0.0 = pure keyword, 1.0 = pure semantic; try 0.3–0.4 for regulatory
    retrieval_limit: int = 100  # Cap for candidates retrieved before reranking (funnel size)

    # Centralized DMS service (dms_service). GxP Docs AI delegates document/RAG operations here.
    rag_builder_enabled: bool = True
    rag_builder_base_url: str = "http://localhost:8088"
    rag_builder_api_prefix: str = "/api/v1"
    rag_builder_timeout_seconds: int = 600
    rag_builder_api_key: str = ""
    rag_builder_user: str = Field(
        default="",
        validation_alias=AliasChoices("RAG_BUILDER_USER", "RAG_BUILDER_USERNAME"),
    )
    rag_builder_password: str = ""

    # OpenAI settings (set via environment variables)
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Azure OpenAI settings
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_deployment_name: str = "gpt-4o"
    azure_openai_api_version: str = "2024-12-01-preview"
    # Optional actual model behind an Azure deployment. Useful when deployment
    # names are aliases and the SDK needs model-family-specific parameters.
    azure_openai_model_name: str = ""

    # Anthropic Claude settings
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-sonnet-20241022"

    # LLM Provider settings
    llm_provider: str = "azure_openai"  # Options: "openai", "azure_openai", "anthropic"
    llm_default_profile_id: str = ""
    llm_api_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_API_BASE_URL", "AZURE_OPENAI_ENDPOINT"),
    )
    llm_model: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_MODEL", "AZURE_OPENAI_MODEL_NAME", "OPENAI_MODEL"),
    )
    llm_deployment: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_DEPLOYMENT", "AZURE_OPENAI_DEPLOYMENT_NAME"),
    )
    llm_api_version: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_API_VERSION", "AZURE_OPENAI_API_VERSION"),
    )
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_API_KEY", "AZURE_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    llm_timeout_seconds: int = 180
    llm_max_retries: int = 2
    compliance_openai_model: str = Field(
        default="gpt-4o-mini",
        validation_alias=AliasChoices("COMPLIANCE_OPENAI_MODEL"),
    )
    compliance_grammar_min_issues: int = Field(
        default=0,
        validation_alias=AliasChoices("COMPLIANCE_GRAMMAR_MIN_ISSUES"),
    )
    compliance_grammar_max_issues: int = Field(
        default=200,
        validation_alias=AliasChoices("COMPLIANCE_GRAMMAR_MAX_ISSUES"),
    )
    compliance_grammar_use_llm: bool = Field(
        default=False,
        validation_alias=AliasChoices("COMPLIANCE_GRAMMAR_USE_LLM"),
    )
    compliance_guidelines_min_rows: int = Field(
        default=100,
        validation_alias=AliasChoices("COMPLIANCE_GUIDELINES_MIN_ROWS"),
    )
    compliance_guidelines_max_rows: int = Field(
        default=400,
        validation_alias=AliasChoices("COMPLIANCE_GUIDELINES_MAX_ROWS"),
    )
    # Fraction of in-scope guideline requirements to LLM-assess (per guideline file).
    # 1.0 = use full Knowledge Scope inventory for pairing + scoring.
    compliance_guidelines_assess_coverage: float = Field(
        default=1.0,
        validation_alias=AliasChoices("COMPLIANCE_GUIDELINES_ASSESS_COVERAGE"),
    )
    # Target fraction of uploaded SOP section mass touched by evidence retrieval.
    # 1.0 = cover content present in the uploaded document (paragraph-driven table).
    compliance_guidelines_doc_coverage: float = Field(
        default=1.0,
        validation_alias=AliasChoices("COMPLIANCE_GUIDELINES_DOC_COVERAGE"),
    )
    # Absolute per-file LLM assessment ceiling (cost guardrail).
    compliance_guidelines_max_llm_requirements: int = Field(
        default=250,
        validation_alias=AliasChoices(
            "COMPLIANCE_GUIDELINES_MAX_LLM_REQUIREMENTS",
            "GUIDELINE_MAX_LLM_REQUIREMENTS",
        ),
    )
    llm_profiles: list[dict] = Field(default_factory=list)

    # Document Harmonization settings
    output_dir: str = "reports"
    similarity_candidate_threshold: float = 75.0
    similarity_duplicate_threshold: float = 85.0
    similarity_merge_threshold: float = 75.0
    classification_model_name: str = "BAAI/bge-base-en-v1.5"
    # When false (default), skip SentenceTransformer preload and use keyword classification only.
    classification_semantic_model_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "CLASSIFICATION_SEMANTIC_MODEL_ENABLED",
            "DOCUMENT_HARMONIZATION_SEMANTIC_MODEL_ENABLED",
        ),
    )
    classification_max_chunks_per_document: int = 120
    classification_excerpt_chars: int = 1200
    classification_batch_size: int = 8
    classification_parallel_batches: int = 2
    classification_keyword_confidence: float = 0.82

    # Optional deployment/model name for lightweight intent / topic screening only.
    # When unset, the main provider model (e.g. AZURE_OPENAI_DEPLOYMENT_NAME) is used.
    intent_recognition_model: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("INTENT_RECOGNITION_MODEL"),
    )

    # PostgreSQL settings
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "qms_db"
    postgres_user: str = "postgres"
    postgres_password: str = ""

    # File upload settings
    upload_dir: str = "uploads"
    max_file_size_mb: int = 200
    allowed_extensions: list = [".docx", ".doc", ".pdf", ".txt"]

    # Veeva Vault document integration settings. Supports the same env names
    # used by the AMS Veeva SDK so existing .env files can be reused.
    veeva_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("VEEVA_BASE_URL", "VAULT_BASE_URL"),
    )
    veeva_vault_dns: str = Field(
        default="",
        validation_alias=AliasChoices("VAULT_DNS", "VEEVA_QUALITY_DOMAIN"),
    )
    veeva_api_version: str = Field(
        default="",
        validation_alias=AliasChoices("VAULT_API_VERSION", "API_VERSION"),
    )
    veeva_username: str = Field(
        default="",
        validation_alias=AliasChoices("VAULT_USER", "VEEVA_USERNAME"),
    )
    veeva_password: str = Field(
        default="",
        validation_alias=AliasChoices("VAULT_PWD", "VEEVA_PASSWORD"),
    )
    veeva_client_id: str = Field(
        default="",
        validation_alias=AliasChoices("VAULT_CLIENT_ID", "VEEVA_CLIENT_ID"),
    )
    veeva_default_lifecycle: str = Field(
        default="Draft to Effective Lifecycle",
        validation_alias=AliasChoices("VEEVA_DEFAULT_LIFECYCLE", "VAULT_DEFAULT_LIFECYCLE"),
    )
    veeva_timeout_seconds: int = 30
    veeva_verify_ssl: bool = True

    # Document templates & drafts
    templates_dir: str = "templates"
    drafts_dir: str = "drafts"

    # Logging settings
    log_level: str = "INFO"

    # JWT Authentication settings (RS256)
    jwt_private_key_path: str = "keys/private_key.pem"
    jwt_public_key_path: str = "keys/public_key.pem"
    jwt_algorithm: str = "RS256"
    jwt_issuer: str = "qms-backend"
    jwt_audience: str = "qms-frontend"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 7
    jwt_key_id: str = "key-2024-01"

    # Password settings
    password_min_length: int = 12
    password_max_failed_attempts: int = 5
    password_lockout_minutes: int = 15

    # Session settings
    session_expire_days: int = 30
    max_sessions_per_user: int = 5

    # Email verification settings
    email_verification_expire_hours: int = 24
    password_reset_expire_hours: int = 1

    # CORS settings for auth: env AUTH_CORS_ORIGINS is comma-separated (no JSON)
    auth_cors_origins_env: str = Field(
        default=",".join(_DEFAULT_AUTH_CORS_ORIGINS),
        validation_alias="AUTH_CORS_ORIGINS",
    )

    @computed_field
    @property
    def auth_cors_origins(self) -> list[str]:
        s = (self.auth_cors_origins_env or "").strip()
        if not s:
            return list(_DEFAULT_AUTH_CORS_ORIGINS)
        return [origin.strip() for origin in s.split(",") if origin.strip()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"

    def get_llm_profile(self, profile_id: str) -> dict | None:
        for profile in self.llm_profiles or []:
            if str(profile.get("id", "")).strip() == str(profile_id or "").strip():
                return profile
        return None


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()
