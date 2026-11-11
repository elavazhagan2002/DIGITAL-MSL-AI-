"""Doctor registration rules: identity fields, validation, consent documents and registry verification.

The fields collected here are the ones the National Medical Register (NMR, run by the National
Medical Commission) searches on: name as registered, registration number, the issuing council and
the year of registration. Verification goes through a ``RegistrationVerifier`` so an NMR lookup can
replace manual review later without changing the registration flow.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from digital_msl import security
from digital_msl.config import get_dmsl_settings

PENDING_CONTACT = "PENDING_CONTACT_VERIFICATION"
PENDING_REGISTRATION = "PENDING_REGISTRATION_VERIFICATION"
VERIFIED = "VERIFIED"
REJECTED = "REJECTED"
NEEDS_INFO = "NEEDS_INFO"
VERIFICATION_STATUSES = (PENDING_CONTACT, PENDING_REGISTRATION, VERIFIED, REJECTED, NEEDS_INFO)
ADMIN_DECISIONS = (VERIFIED, REJECTED, NEEDS_INFO, PENDING_REGISTRATION)

LEGACY_STATUS_MAP = {
    "pending": PENDING_REGISTRATION,
    "verified": VERIFIED,
    "rejected": REJECTED,
    "needs_info": NEEDS_INFO,
}


def normalize_status(value: Optional[str]) -> str:
    raw = (value or "").strip()
    if raw in VERIFICATION_STATUSES:
        return raw
    return LEGACY_STATUS_MAP.get(raw.lower(), PENDING_REGISTRATION)


# Seed data for the ``dmsl_councils`` table; the API and validation read the table, not this tuple.
COUNCIL_SEED = (
    ("nmc", "National Medical Commission (NMC)"),
    ("andhra-pradesh", "Andhra Pradesh Medical Council"),
    ("arunachal-pradesh", "Arunachal Pradesh Medical Council"),
    ("assam", "Assam Medical Council"),
    ("bihar", "Bihar Medical Council"),
    ("chhattisgarh", "Chhattisgarh Medical Council"),
    ("delhi", "Delhi Medical Council"),
    ("goa", "Goa Medical Council"),
    ("gujarat", "Gujarat Medical Council"),
    ("haryana", "Haryana Medical Council"),
    ("himachal-pradesh", "Himachal Pradesh Medical Council"),
    ("jammu-kashmir", "Jammu & Kashmir Medical Council"),
    ("jharkhand", "Jharkhand Medical Council"),
    ("karnataka", "Karnataka Medical Council"),
    ("kerala", "Travancore-Cochin Medical Council (Kerala)"),
    ("madhya-pradesh", "Madhya Pradesh Medical Council"),
    ("maharashtra", "Maharashtra Medical Council"),
    ("manipur", "Manipur Medical Council"),
    ("mizoram", "Mizoram Medical Council"),
    ("nagaland", "Nagaland Medical Council"),
    ("odisha", "Odisha Council of Medical Registration"),
    ("punjab", "Punjab Medical Council"),
    ("rajasthan", "Rajasthan Medical Council"),
    ("sikkim", "Sikkim Medical Council"),
    ("tamil-nadu", "Tamil Nadu Medical Council"),
    ("telangana", "Telangana State Medical Council"),
    ("tripura", "Tripura Medical Council"),
    ("uttar-pradesh", "Uttar Pradesh Medical Council"),
    ("uttarakhand", "Uttarakhand Medical Council"),
    ("west-bengal", "West Bengal Medical Council"),
    ("other", "Other state / union territory council"),
)

SPECIALITIES = (
    "Ophthalmology",
    "Retina / Vitreoretinal Surgery",
    "Glaucoma",
    "Cornea and External Disease",
    "Cataract and Refractive Surgery",
    "Neuro-ophthalmology",
    "Paediatric Ophthalmology",
    "Oculoplastics",
    "Uveitis",
    "Optometry",
    "Internal Medicine",
    "Endocrinology / Diabetology",
    "General Practice",
    "Other",
)

QUALIFICATIONS = (
    "MBBS",
    "MD Ophthalmology",
    "MS Ophthalmology",
    "DNB Ophthalmology",
    "DO / DOMS",
    "Fellowship (Retina / Glaucoma / Cornea / other)",
    "MD",
    "MS",
    "DM",
    "MCh",
    "FRCS",
    "Other postgraduate",
)

PRACTICE_TYPES = (
    "Hospital (private)",
    "Hospital (government)",
    "Medical college / academic",
    "Clinic / private practice",
    "Research institute",
)

CONSENT_VERSION = "2026-11-v2"

# Wording is a working draft: Legal must approve before go-live.
CONSENT_ITEMS: tuple[dict[str, Any], ...] = (
    {
        "type": "terms_privacy",
        "required": True,
        "title": "Terms of use and Privacy Notice",
        "text": (
            "I have read and accept the Terms of Use and the Privacy Notice. I agree that Sciagen Digital MSL may "
            "collect and process the professional details I provide (name, registration number, council, "
            "qualification, speciality, institution, contact details and an optional registration certificate) to "
            "verify that I am a registered medical practitioner, to provide scientific information, and to keep an "
            "audit trail of my interactions, for as long as my account is active plus the retention period in the "
            "Privacy Notice. I can withdraw consent, request access or erasure, or raise a grievance through the "
            "contact in the Privacy Notice. [LEGAL TO APPROVE]"
        ),
    },
    {
        "type": "hcp_declaration",
        "required": True,
        "title": "Healthcare professional declaration",
        "text": (
            "I declare that I am a registered medical practitioner, that the registration details I provided are "
            "true and correct, and that I will use Digital MSL for scientific exchange only. I understand that my "
            "registration may be checked against the National Medical Register and that false information can lead "
            "to removal of my access. [LEGAL TO APPROVE]"
        ),
    },
    {
        "type": "whatsapp_messaging",
        "required": True,
        "title": "I agree to receive messages from Digital MSL on WhatsApp",
        "text": (
            "I agree to receive scientific replies, case updates and service messages from Digital MSL and my "
            "Physical MSL on WhatsApp at the mobile number I provided. I can withdraw this consent at any time in "
            "my profile or by replying STOP. [LEGAL TO APPROVE]"
        ),
    },
    {
        "type": "email_messaging",
        "required": True,
        "title": "I agree to receive messages from Digital MSL by email",
        "text": (
            "I agree to receive scientific replies, case updates and service messages from Digital MSL and my "
            "Physical MSL by email at the address I provided. I can withdraw this consent at any time in my profile "
            "or with the unsubscribe link in any email. [LEGAL TO APPROVE]"
        ),
    },
)

CONSENT_TYPES = tuple(item["type"] for item in CONSENT_ITEMS)
CHANNEL_CONSENT = {"whatsapp": "whatsapp_messaging", "email": "email_messaging"}

_MOBILE = re.compile(r"^\+[1-9]\d{9,14}$")
_REG_NO = re.compile(r"^[A-Z0-9/\-]{3,30}$")
_PINCODE = re.compile(r"^[A-Za-z0-9 \-]{3,10}$")


def normalize_mobile(value: str) -> str:
    return re.sub(r"[\s\-().]", "", str(value or ""))


def validate_password(password: str) -> None:
    security.validate_password(password or "")


def password_problems(password: str) -> list[str]:
    return security.password_problems(password or "")


def _clean(value: Any, label: str, *, min_len: int = 2, max_len: int = 120, required: bool = True) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        if required:
            raise ValueError(f"{label} is required.")
        return ""
    if len(text) < min_len or len(text) > max_len:
        raise ValueError(f"{label} must be {min_len}-{max_len} characters.")
    if re.search(r"[<>]", text):
        raise ValueError(f"{label} contains invalid characters.")
    return text


def validate_display_name(value: str) -> str:
    return _clean(value, "Full name", min_len=2, max_len=100)


def validate_professional_details(data: dict[str, Any], councils: dict[str, str]) -> dict[str, Any]:
    """Validate and normalise the identity block. ``councils`` maps council name -> council id (from the DB)."""
    mobile = normalize_mobile(data.get("mobile", ""))
    if not _MOBILE.match(mobile):
        raise ValueError("Mobile number must be in international format, for example +919876543210.")
    reg_no = re.sub(r"\s+", "", str(data.get("registration_number") or "")).upper()
    if not reg_no:
        raise ValueError("Registration number is required.")
    if not _REG_NO.match(reg_no):
        raise ValueError("Registration number may contain letters, digits, '/' and '-' only (3-30 characters).")
    council_name = _clean(data.get("registration_council"), "Registering council", max_len=160)
    council_id = councils.get(council_name)
    if not council_id:
        raise ValueError("Select the council that issued your registration.")
    try:
        year = int(data.get("registration_year"))
    except (TypeError, ValueError):
        raise ValueError("Year of registration is required.") from None
    this_year = datetime.now(timezone.utc).year
    if year < 1950 or year > this_year:
        raise ValueError(f"Year of registration must be between 1950 and {this_year}.")
    pincode = _clean(data.get("pincode"), "PIN code", min_len=3, max_len=10)
    if not _PINCODE.match(pincode):
        raise ValueError("PIN code is not valid.")
    highest = _clean(data.get("highest_qualification") or data.get("qualification"), "Highest qualification", max_len=120)
    nmr_id = _clean(data.get("nmr_id"), "NMC Unique ID (NMR ID)", min_len=3, max_len=40, required=get_dmsl_settings().nmr_id_required)
    return {
        "mobile": mobile,
        "registration_number": reg_no,
        "registration_council": council_name,
        "council_id": council_id,
        "registration_year": year,
        "basic_qualification": _clean(data.get("basic_qualification"), "Basic qualification", max_len=120),
        "highest_qualification": highest,
        "speciality": _clean(data.get("speciality") or "Ophthalmology", "Primary speciality", max_len=80),
        "institution": _clean(data.get("institution"), "Hospital or clinic", max_len=160),
        "practice_type": _clean(data.get("practice_type") or "Clinic / private practice", "Practice type", max_len=80),
        "city": _clean(data.get("city"), "City", max_len=80),
        "state": _clean(data.get("state"), "State", max_len=80),
        "pincode": pincode,
        "nmr_id": nmr_id,
    }


def validate_consents(consents: dict[str, Any], version: str) -> dict[str, bool]:
    if (version or "") != CONSENT_VERSION:
        raise ValueError("The consent form has changed. Reload the page and review it again.")
    granted = {t: bool(consents.get(t)) for t in CONSENT_TYPES}
    if not granted["terms_privacy"]:
        raise ValueError("You must accept the Terms of use and Privacy Notice to register.")
    if not granted["hcp_declaration"]:
        raise ValueError("The healthcare professional declaration must be accepted.")
    if not granted["whatsapp_messaging"]:
        raise ValueError("Consent to receive messages on WhatsApp is required to register.")
    if not granted["email_messaging"]:
        raise ValueError("Consent to receive messages by email is required to register.")
    return granted


# ----------------------------------------------------------------- verification
@dataclass
class VerificationOutcome:
    status: str
    source: str
    detail: str = ""


def nmr_query(profile: dict[str, Any]) -> dict[str, Any]:
    """The fields an NMR search needs; reused by the future automated lookup."""
    return {
        "name": profile.get("display_name", ""),
        "registration_number": profile.get("registration_number", ""),
        "council": profile.get("registration_council", ""),
        "year_of_registration": profile.get("registration_year"),
        "nmr_id": profile.get("nmr_id", ""),
    }


class RegistrationVerifier(Protocol):
    name: str

    def verify(self, profile: dict[str, Any]) -> VerificationOutcome:
        """Return the verification outcome for a registration whose contact details are confirmed."""


class ManualVerifier:
    """Default: an Administrator checks the details against the public NMC register and records the decision."""

    name = "manual"

    def verify(self, profile: dict[str, Any]) -> VerificationOutcome:
        return VerificationOutcome(PENDING_REGISTRATION, self.name, "Awaiting manual verification against the National Medical Register.")


class NmrVerifier:
    """Placeholder for the NMC integration. Wire it up once API access and terms are agreed with NMC."""

    name = "nmr"

    def verify(self, profile: dict[str, Any]) -> VerificationOutcome:
        return VerificationOutcome(PENDING_REGISTRATION, self.name, "NMR verification is not configured; manual review required.")


def get_verifier() -> RegistrationVerifier:
    provider = (get_dmsl_settings().nmr_provider or "manual").lower()
    return NmrVerifier() if provider == "nmr" else ManualVerifier()


def lookup_registry(profile: dict[str, Any]) -> VerificationOutcome:
    return get_verifier().verify(profile)


def is_verified(user: Optional[dict[str, Any]]) -> bool:
    if not user or user.get("role") != "doctor":
        return True
    if not get_dmsl_settings().require_verified_doctor:
        return True
    return normalize_status(user.get("verification_status")) == VERIFIED
