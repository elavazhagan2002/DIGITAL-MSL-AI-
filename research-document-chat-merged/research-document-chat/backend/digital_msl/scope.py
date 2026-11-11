"""Therapeutic-area scope policy (retina built in; other areas add keywords).

Used in three places:
* literature search  - reject out-of-scope queries, drop non-retina results;
* chat intake        - out-of-scope questions are refused without opening an MSL case;
* safety screening   - individual-patient / prescribing requests are escalated.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from digital_msl.config import get_dmsl_settings

# Brand names and research codes for the supported molecules.
MOLECULE_ALIASES: dict[str, tuple[str, ...]] = {
    "aflibercept": ("aflibercept", "eylea", "vegf trap-eye", "vegf-trap eye"),
    "ranibizumab": ("ranibizumab", "lucentis", "byooviz", "cimerli", "susvimo"),
    "faricimab": ("faricimab", "vabysmo", "ro6867461"),
    "bevacizumab": ("bevacizumab", "avastin"),
    "brolucizumab": ("brolucizumab", "beovu"),
    "pegcetacoplan": ("pegcetacoplan", "syfovre"),
    "avacincaptad pegol": ("avacincaptad", "izervay"),
}

RETINA_TERMS: tuple[str, ...] = (
    "retina", "retinal", "retinopathy", "macula", "macular", "maculopathy", "fovea", "foveal",
    "choroid", "choroidal", "choroidal neovascularization", "vitreous", "vitreoretinal", "vitrectomy",
    "intravitreal", "subretinal", "photoreceptor", "rpe", "retinal pigment epithelium",
    "age-related macular degeneration", "amd", "namd", "wet amd", "geographic atrophy",
    "diabetic macular edema", "diabetic macular oedema", "dme", "dmo", "diabetic retinopathy",
    "retinal vein occlusion", "rvo", "brvo", "crvo", "cnv", "polypoidal", "pcv",
    "retinopathy of prematurity", "rop", "retinal detachment", "uveitis", "uveitic",
    "anti-vegf", "vegf", "oct", "optical coherence tomography", "fundus", "best-corrected visual acuity",
    "bcva", "visual acuity", "central subfield thickness", "cst", "ophthalmology", "ophthalmic",
    "ocular", "eye", "eyes",
)

# Requests for individual medical decisions are outside the bot's remit (FRD 3.2).
_INDIVIDUAL_ADVICE_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bmy patient\b",
        r"\bshould i (prescribe|give|inject|treat|switch|stop)\b",
        r"\bwhat (dose|dosage) should\b",
        r"\b(diagnose|diagnosis for) (my|this) (patient|case)\b",
        r"\bfor (him|her) (personally|specifically)\b",
        r"\boff[- ]label\b",
    )
)


def _contains_term(text: str, term: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None


def _contains_area_term(text: str, term: str) -> bool:
    """Whole-word match that also accepts a plural ("inhibitor" matches "inhibitors")."""
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?:s|es)?(?![a-z0-9])", text) is not None


@dataclass
class ScopeDecision:
    in_scope: bool
    retina_terms: list[str] = field(default_factory=list)
    molecules: list[str] = field(default_factory=list)
    unsupported_molecules: list[str] = field(default_factory=list)
    safety_flags: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "in_scope": self.in_scope,
            "retina_terms": self.retina_terms,
            "molecules": self.molecules,
            "unsupported_molecules": self.unsupported_molecules,
            "safety_flags": self.safety_flags,
            "reason": self.reason,
        }


def _alias_map() -> dict[str, tuple[str, ...]]:
    aliases = {name: tuple(values) for name, values in MOLECULE_ALIASES.items()}
    try:
        from digital_msl import store

        for item in store.get_policy().get("molecules") or []:
            name = str(item.get("name") or "").strip().lower()
            if not name:
                continue
            extra = [a.strip().lower() for a in str(item.get("aliases") or "").split(",") if a.strip()]
            merged = list(aliases.get(name, ()))
            for alias in extra + [name]:
                if alias not in merged:
                    merged.append(alias)
            aliases[name] = tuple(merged)
    except Exception:
        pass
    return aliases


def _allowed_molecules() -> set[str]:
    try:
        from digital_msl import store

        names = {str(item.get("name") or "").strip().lower() for item in store.get_policy().get("molecules") or []}
        names.discard("")
        if names:
            return names
    except Exception:
        pass
    return set(get_dmsl_settings().molecule_list)


def _molecules_in(text: str) -> list[str]:
    found: list[str] = []
    for molecule, aliases in _alias_map().items():
        if any(_contains_term(text, alias) for alias in aliases):
            found.append(molecule)
    return found


def classify_query(text: str, extra_terms: Iterable[str] = ()) -> ScopeDecision:
    """Decide whether a question or search query is within scope.

    The retina vocabulary is always in scope. ``extra_terms`` are the scope keywords of the
    therapeutic areas the asker is assigned to; a match on any of them also puts the question
    in scope (other areas than retina), even when it names a molecule outside the retina list.
    """
    lowered = str(text or "").lower()
    allowed = _allowed_molecules()
    retina_hits = [t for t in RETINA_TERMS if _contains_term(lowered, t)]
    mentioned = _molecules_in(lowered)
    supported = [m for m in mentioned if m in allowed]
    unsupported = [m for m in mentioned if m not in allowed]
    safety = [p.pattern for p in _INDIVIDUAL_ADVICE_PATTERNS if p.search(lowered)]
    area_hits = [t for t in dict.fromkeys(extra_terms) if t and _contains_area_term(lowered, t.lower())]
    if area_hits:
        return ScopeDecision(True, retina_hits + area_hits, supported, [], safety, "")

    if unsupported and not supported:
        reason = (
            "The question refers to "
            + ", ".join(unsupported)
            + ", which is outside the configured molecules ("
            + ", ".join(sorted(allowed))
            + ")."
        )
        return ScopeDecision(False, retina_hits, supported, unsupported, safety, reason)
    if not retina_hits and not supported:
        return ScopeDecision(
            False, retina_hits, supported, unsupported, safety,
            "The question is not about retina / eye-care or the configured molecules.",
        )
    return ScopeDecision(True, retina_hits, supported, unsupported, safety, "")


def is_retina_relevant_record(*texts: str) -> bool:
    """True when a connector record (title, summary...) is about the retina / eye."""
    combined = " ".join(str(t or "") for t in texts).lower()
    return any(_contains_term(combined, t) for t in RETINA_TERMS)
