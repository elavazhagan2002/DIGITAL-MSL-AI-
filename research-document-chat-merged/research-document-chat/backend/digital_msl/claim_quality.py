"""Heuristics: does a claim actually answer the question with result values in the sources?"""
from __future__ import annotations

import re

_QUANT_QUERY = re.compile(
    r"\b("
    r"gain|change|improvement|difference|efficacy|how much|how many|"
    r"number of letters|letters at|percent|reduction|increase|decrease|"
    r"mean change|from baseline|showed|was the|what visual acuity"
    r")\b",
    re.I,
)

_RESULT_WITH_UNIT = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:letters|letter|μm|um|µm|mm|mg|mcg|months?|days?)\b|\b\d+(?:[.,]\d+)?\s*%",
    re.I,
)

_RESULT_PHRASE = re.compile(
    r"\b(?:improved|gained|change(?:d)?|increase[d]?|decrease[d]?|reduction|by)\s+(?:by\s+)?[-+]?\d+(?:[.,]\d+)?",
    re.I,
)

_OUTCOME_LABEL = re.compile(
    r"\b("
    r"item\s+\d+\s+(type|measure)|measure:|primary outcome|secondary outcome|"
    r"outcome measure|endpoint"
    r")\b",
    re.I,
)

_DRAFT_DISCLAIMER = re.compile(
    r"\b("
    r"not detailed in the provided|does not contain|do not contain|not found in|"
    r"library does not contain|not in the provided sources|no specific numerical|"
    r"cannot find|could not find"
    r")\b",
    re.I,
)


def query_expects_quantitative_answer(query: str) -> bool:
    return bool(_QUANT_QUERY.search(str(query or "")))


def text_has_study_result_value(text: str) -> bool:
    blob = str(text or "")
    if not blob.strip():
        return False
    if _RESULT_WITH_UNIT.search(blob):
        return True
    if _RESULT_PHRASE.search(blob):
        return True
    for match in re.finditer(r"\b\d+(?:[.,]\d+)?\b", blob):
        number = match.group(0)
        start, end = match.start(), match.end()
        window = blob[max(0, start - 20) : min(len(blob), end + 24)].lower()
        if re.search(rf"\bweek\s+{re.escape(number)}\b", window):
            continue
        if re.search(rf"{re.escape(number)}\s*[-–]?\s*week", window):
            continue
        if re.search(r"\b(letters|letter|μm|um|mm|%|mg|months?|days?)\b", blob[end : end + 30], re.I):
            return True
        if blob[end : end + 1] == "%":
            return True
        if re.search(r"\b(?:BCVA|visual acuity)\b", window, re.I):
            return True
    return False


def claim_states_quantitative_result(claim: str) -> bool:
    return text_has_study_result_value(claim)


def evidence_is_outcome_label_only(text: str) -> bool:
    """True when passage names an endpoint/measure but does not state a numeric result."""
    blob = re.sub(r"\s+", " ", str(text or "")).strip()
    if not blob:
        return False
    if not _OUTCOME_LABEL.search(blob):
        return False
    return not text_has_study_result_value(blob)


def draft_disclaims_retrieved_sources(draft: str) -> bool:
    return bool(_DRAFT_DISCLAIMER.search(str(draft or "")))


_PART_SPLIT = re.compile(r"\?\s*|\s*;\s+|\s*,?\s+and\s+(?:what|how|which|when)\b", re.I)
_STOP = frozenset(
    "a an and are as at be been being but by can could did do does for from had has have "
    "in into is it its may might more most no not of on or our than that the their them then "
    "there these they this those to was were which while who will with within would also both "
    "each other such between after before during over under up down out about against among "
    "per via vs versus what show shown did does the".split()
)
_RESULT_MEASURE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(letters?|%|percent|months?|days?|mg|mcg|mm|μm|um|µm)\b",
    re.I,
)


def _stems(text: str) -> set[str]:
    stems: set[str] = set()
    for token in re.findall(r"[a-z0-9]+", str(text or "").lower()):
        if token in _STOP or len(token) <= 2 or token.isdigit():
            continue
        for suffix in ("ations", "ation", "ings", "ing", "edly", "ed", "es", "ly", "s"):
            if len(token) > len(suffix) + 3 and token.endswith(suffix):
                token = token[: -len(suffix)]
                break
        stems.add(token)
    return stems


def claim_answers_query(query: str, claim: str) -> bool:
    """True when the claim is about the question, not an extra background fact."""
    claim_terms = _stems(claim)
    query_terms = _stems(query)
    if not claim_terms or not query_terms:
        return True
    shared = claim_terms & query_terms
    if len(shared) >= 2:
        return True
    smaller = min(len(claim_terms), len(query_terms))
    return (len(shared) / smaller) >= 0.34


def query_parts(query: str) -> list[str]:
    parts = [part.strip(" .") for part in _PART_SPLIT.split(str(query or ""))]
    return [part for part in parts if len(part.split()) >= 3]


def result_measures(text: str) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for value, unit in _RESULT_MEASURE.findall(str(text or "")):
        key = unit.lower().rstrip("s")
        if key == "percent":
            key = "%"
        found.setdefault(key, set()).add(value.replace(",", ""))
    return found


def claims_conflict(left: str, right: str) -> bool:
    """Same measure reported with different values."""
    if not claim_answers_query(left, right):
        return False
    left_measures, right_measures = result_measures(left), result_measures(right)
    shared_units = set(left_measures) & set(right_measures)
    for unit in shared_units:
        if left_measures[unit].isdisjoint(right_measures[unit]):
            return True
    return False
