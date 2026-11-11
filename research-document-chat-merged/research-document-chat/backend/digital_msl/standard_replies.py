"""Fixed, versioned replies. [MLR TO APPROVE] before go-live."""
from __future__ import annotations

VERSION = "sr-2026-11-v1"

NOT_FOUND = (
    "I could not find this in the approved sources, so I have passed your question to a Physical MSL. "
    "You will receive a reply here."
)
PATIENT_SPECIFIC = "Please do not use this service for patient-specific diagnosis or treatment decisions."
ROUTED_TO_MSL = "Your question will be reviewed by a Physical MSL."
