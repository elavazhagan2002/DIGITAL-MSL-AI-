"""Settings for the Digital MSL component (env prefix ``DIGITAL_MSL_``)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class DigitalMSLSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DIGITAL_MSL_",
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Therapeutic scope
    molecules: str = "aflibercept,ranibizumab,faricimab"

    # Validation engine
    confidence_threshold: float = 0.90
    claim_min_score: float = 0.70
    use_llm_judge: bool = True
    retrieval_top_k: int = 6
    max_claims: int = 12

    # Persistence
    database_url: str = os.environ.get("DATABASE_URL", "sqlite:///./research_chat.db")
    trace_dir: str = "logs/validation"

    # Escalation routing
    default_msl_id: str = "msl_default"

    # Repository used for email / WhatsApp questions that do not name one (comma-separated for multiple)
    default_repository_id: str = ""

    def default_repository_ids(self) -> list[str]:
        return [part.strip() for part in (self.default_repository_id or "").split(",") if part.strip()]

    # Outbound channel providers (optional; outbox is used when unset)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"
    imap_poll_seconds: int = 45
    whatsapp_api_url: str = ""
    whatsapp_token: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""
    # Comma-separated E.164 numbers notified when a WhatsApp question escalates (your "MSL group" roster)
    whatsapp_msl_notify_phones: str = ""
    whatsapp_demo_group_name: str = "Digital MSL Scientific Support"
    # WhatsApp Business Account ID (WABA) — required to subscribe app for inbound webhooks
    whatsapp_waba_id: str = ""
    # When true, skip Meta send API; outbox shows sent (for demos while WABA is restricted).
    whatsapp_mock: bool = False
    whatsapp_business_display: str = "+1 555-658-2876"

    # Security / compliance
    session_ttl_hours: int = 12
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    # Doctors must be verified (NMR / manual review) before they can use any channel.
    require_verified_doctor: bool = True
    # Email / WhatsApp senders that are not registered doctors. Set false in production so only
    # verified, registered doctors get answers.
    allow_unregistered_senders: bool = True
    # Shared secret for the unauthenticated email / WhatsApp bridge endpoints (header X-DMSL-Inbound-Secret).
    inbound_secret: str = ""
    # Per-doctor ceiling on questions per minute across all channels.
    rate_limit_per_minute: int = 12
    # Demo mode seeds well-known demo accounts. The app refuses to start when demo mode is on and
    # ENVIRONMENT is 'production'. DIGITAL_MSL_SEED_DEMO_USERS is a deprecated alias of DEMO_MODE.
    demo_mode: bool = False
    environment: str = "development"
    seed_demo_users: bool = False
    # Tests only: also seed the older @digital-msl.local accounts the test-suite relies on.
    legacy_demo_users: bool = False
    # Passwords, sessions, MFA
    password_min_length: int = 12
    hibp_check: bool = False  # k-anonymity lookup against Have I Been Pwned (needs outbound HTTPS)
    require_admin_mfa: bool = True
    session_idle_minutes: int = 30
    cookie_secure: bool = False  # forced on automatically for https requests
    auth_rate_limit_per_minute: int = 10  # per IP and route: login, register, verify, resend, unsubscribe, mfa
    # PII encryption key (Fernet, urlsafe base64 32 bytes). Empty => a dev key file is generated; set it in production.
    data_key: str = ""
    # Registration contact verification
    dev_echo_otp: bool = False  # NEVER true in production: returns OTPs in the register response
    whatsapp_otp_template: str = ""  # approved WhatsApp template name for OTP messages (production)
    nmr_id_required: bool = False  # flip on when the NMR integration is live
    clamav_host: str = ""
    clamav_port: int = 3310
    # Compliance
    pv_contact: str = "[PHARMACOVIGILANCE CONTACT]"
    public_base_url: str = "http://localhost:8087"  # used in unsubscribe links
    # Doctor registry provider: "manual" (MSL / admin review) or "nmr" (reserved for the NMR integration).
    nmr_provider: str = "manual"
    nmr_api_url: str = ""
    nmr_api_key: str = ""

    @property
    def demo_enabled(self) -> bool:
        return bool(self.demo_mode or self.seed_demo_users)

    @property
    def is_production(self) -> bool:
        return (self.environment or "").strip().lower() == "production"

    # Grounded answers. Repositories QA has declared approved-only. EMPTY by default: answer nothing,
    # escalate everything, until this is configured. "*" means every repository (tests / dev only).
    approved_repository_ids: str = ""
    # Once IKM returns approval metadata, a hit without approval_status=APPROVED is dropped. Until then
    # only hits that carry a bad status, a past expiry or superseded_by are dropped.
    require_approval_metadata: bool = False
    # Confidence = retrieval score >= T1 AND groundedness pass AND no source conflict.
    retrieval_score_min: float = 0.30

    def approved_repositories(self) -> set[str]:
        return {part.strip() for part in (self.approved_repository_ids or "").split(",") if part.strip()}

    @property
    def molecule_list(self) -> list[str]:
        return [m.strip().lower() for m in self.molecules.split(",") if m.strip()]

    @property
    def trace_path(self) -> Path:
        path = Path(self.trace_dir)
        return path if path.is_absolute() else BACKEND_DIR / path


class DemoModeInProduction(RuntimeError):
    """Demo mode must never run in production."""


def check_demo_guard(settings: "DigitalMSLSettings | None" = None) -> None:
    """Raise when demo mode (or the deprecated SEED_DEMO_USERS alias) is on in a production environment."""
    current = settings or get_dmsl_settings()
    if current.demo_enabled and current.is_production:
        raise DemoModeInProduction(
            "Refusing to start: DIGITAL_MSL_DEMO_MODE (or DIGITAL_MSL_SEED_DEMO_USERS) is enabled while "
            "DIGITAL_MSL_ENVIRONMENT=production. Turn demo mode off."
        )


@lru_cache()
def get_dmsl_settings() -> DigitalMSLSettings:
    return DigitalMSLSettings()
