"""Cryptography and abuse-protection helpers: password hashing and policy, PII encryption,
signed tokens, TOTP, and an in-process rate limiter."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from digital_msl.config import BACKEND_DIR, get_dmsl_settings

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------- data key / PII encryption
_KEY_FILE = BACKEND_DIR / ".dmsl_data_key"


@lru_cache()
def _data_key() -> bytes:
    configured = (get_dmsl_settings().data_key or "").strip()
    if configured:
        return configured.encode("ascii")
    if _KEY_FILE.exists():
        key = _KEY_FILE.read_text(encoding="ascii").strip().encode("ascii")
    else:
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        _KEY_FILE.write_text(key.decode("ascii"), encoding="ascii")
    logger.warning(
        "DIGITAL_MSL_DATA_KEY is not set: using the development key file %s. "
        "Set DIGITAL_MSL_DATA_KEY (and back it up) before production, or encrypted data is unrecoverable.",
        _KEY_FILE.name,
    )
    return key


def reset_key_cache() -> None:
    _data_key.cache_clear()
    _fernet.cache_clear()


@lru_cache()
def _fernet():
    from cryptography.fernet import Fernet

    return Fernet(_data_key())


def encrypt_text(value: str) -> str:
    return _fernet().encrypt((value or "").encode("utf-8")).decode("ascii") if value else ""


def decrypt_text(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def encrypt_bytes(data: bytes) -> bytes:
    return _fernet().encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    return _fernet().decrypt(token)


def _subkey(purpose: str) -> bytes:
    return hashlib.sha256(_data_key() + b"|" + purpose.encode("utf-8")).digest()


def blind_index(value: str, purpose: str) -> str:
    """Deterministic keyed hash so encrypted values stay unique / searchable without storing plaintext."""
    if not value:
        return ""
    return hmac.new(_subkey("idx:" + purpose), value.strip().encode("utf-8"), hashlib.sha256).hexdigest()


# ----------------------------------------------------------------- signed tokens (MFA step, unsubscribe)
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_token(payload: dict[str, Any], purpose: str, ttl_seconds: Optional[int] = None) -> str:
    body = dict(payload)
    body["p"] = purpose
    if ttl_seconds:
        body["exp"] = int(time.time()) + ttl_seconds
    raw = _b64(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    sig = _b64(hmac.new(_subkey("tok:" + purpose), raw.encode("ascii"), hashlib.sha256).digest())
    return f"{raw}.{sig}"


def verify_token(token: str, purpose: str) -> Optional[dict[str, Any]]:
    try:
        raw, sig = (token or "").split(".", 1)
        expected = _b64(hmac.new(_subkey("tok:" + purpose), raw.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        body = json.loads(_unb64(raw))
    except Exception:
        return None
    if body.get("p") != purpose:
        return None
    if body.get("exp") and body["exp"] < time.time():
        return None
    return body


# ----------------------------------------------------------------- passwords
_ARGON2 = None


def _hasher():
    global _ARGON2
    if _ARGON2 is None:
        from argon2 import PasswordHasher

        _ARGON2 = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)  # OWASP minimum for argon2id
    return _ARGON2


def hash_password(password: str) -> str:
    return _hasher().hash(password) if password else ""


def verify_password(password: str, hashed: Optional[str]) -> bool:
    """Strict: a missing hash or password never verifies. Accepts argon2id and legacy PBKDF2 hashes."""
    if not hashed or not password:
        return False
    if hashed.startswith("$argon2"):
        try:
            return bool(_hasher().verify(hashed, password))
        except Exception:
            return False
    parts = hashed.split("$")
    if parts[0] != "pbkdf2_sha256":
        return False
    if len(parts) == 3:
        iterations, salt, expected_hex = 100000, parts[1], parts[2]
    elif len(parts) == 4 and parts[1].isdigit():
        iterations, salt, expected_hex = int(parts[1]), parts[2], parts[3]
    else:
        return False
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return secrets.compare_digest(key.hex(), expected_hex)


def password_needs_rehash(hashed: Optional[str]) -> bool:
    if not hashed:
        return False
    if not hashed.startswith("$argon2"):
        return True
    try:
        return bool(_hasher().check_needs_rehash(hashed))
    except Exception:
        return True


@lru_cache()
def _common_passwords() -> frozenset[str]:
    path = Path(__file__).parent / "data" / "common_passwords.txt"
    try:
        return frozenset(line.strip().lower() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return frozenset()


def _hibp_pwned(password: str) -> bool:
    """k-anonymity range query: only the first 5 hash characters leave the server."""
    import requests

    digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    try:
        res = requests.get(f"https://api.pwnedpasswords.com/range/{digest[:5]}", timeout=3, headers={"Add-Padding": "true"})
        if res.status_code != 200:
            return False
        for line in res.text.splitlines():
            suffix, _, count = line.partition(":")
            if suffix == digest[5:] and int(count or 0) > 0:
                return True
    except Exception:
        logger.warning("HIBP check unavailable; continuing without it.")
    return False


def password_problems(password: str) -> list[str]:
    settings = get_dmsl_settings()
    minimum = settings.password_min_length
    problems: list[str] = []
    if len(password) < minimum:
        problems.append(f"at least {minimum} characters")
    if len(password) > 128:
        problems.append("at most 128 characters")
    if not re.search(r"[a-z]", password):
        problems.append("a lowercase letter")
    if not re.search(r"[A-Z]", password):
        problems.append("an uppercase letter")
    if not re.search(r"\d", password):
        problems.append("a digit")
    if not re.search(r"[^A-Za-z0-9]", password):
        problems.append("a symbol")
    return problems


def validate_password(password: str, *, check_breached: bool = True) -> None:
    problems = password_problems(password or "")
    if problems:
        raise ValueError("Password needs " + ", ".join(problems) + ".")
    if check_breached:
        lowered = password.lower()
        stripped = re.sub(r"[^a-z0-9]", "", lowered)
        common = _common_passwords()
        if lowered in common or stripped in common or lowered.rstrip("0123456789!@#$%^&*") in common:
            raise ValueError("This password is too common or has appeared in known breaches. Choose a different one.")
        if settings_hibp() and _hibp_pwned(password):
            raise ValueError("This password has appeared in a known data breach. Choose a different one.")


def settings_hibp() -> bool:
    return bool(get_dmsl_settings().hibp_check)


# ----------------------------------------------------------------- rate limiting
_lock = threading.Lock()
_buckets: dict[str, deque[float]] = defaultdict(deque)


def rate_limit(key: str, limit: int, window_seconds: int = 60, *, now: Optional[float] = None) -> tuple[bool, int]:
    """Sliding window. Returns (allowed, retry_after_seconds)."""
    if limit <= 0:
        return True, 0
    current = time.monotonic() if now is None else now
    with _lock:
        bucket = _buckets[key]
        while bucket and current - bucket[0] > window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            return False, max(1, int(window_seconds - (current - bucket[0])) + 1)
        bucket.append(current)
    return True, 0


def reset_rate_limits() -> None:
    with _lock:
        _buckets.clear()


# ----------------------------------------------------------------- TOTP + recovery codes
def new_totp_secret() -> str:
    import pyotp

    return pyotp.random_base32()


def totp_uri(secret: str, email: str) -> str:
    import pyotp

    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="Sciagen Digital MSL")


def verify_totp(secret: str, code: str) -> bool:
    import pyotp

    code = re.sub(r"\s", "", code or "")
    return bool(secret) and code.isdigit() and len(code) == 6 and pyotp.TOTP(secret).verify(code, valid_window=1)


def new_recovery_codes(count: int = 8) -> list[str]:
    return [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(count)]


def hash_recovery_code(code: str) -> str:
    return hmac.new(_subkey("recovery"), re.sub(r"[\s-]", "", code.lower()).encode("utf-8"), hashlib.sha256).hexdigest()


def new_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(user_id: str, channel: str, code: str) -> str:
    return hmac.new(_subkey("otp"), f"{user_id}|{channel}|{code}".encode("utf-8"), hashlib.sha256).hexdigest()


def csrf_token(session_id: str) -> str:
    """CSRF token bound to the session (also mirrored in the readable dmsl_csrf cookie: double submit)."""
    return hmac.new(_subkey("csrf"), (session_id or "").encode("utf-8"), hashlib.sha256).hexdigest()[:40]
