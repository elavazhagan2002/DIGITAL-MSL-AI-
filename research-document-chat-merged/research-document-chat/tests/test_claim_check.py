"""The claim checker: supported / unsupported / mismatched numbers / citation problems."""
from __future__ import annotations

from digital_msl import claim_check

PASSAGE = "The planted Zorvantel dosing interval is 97 weeks according to the APPROVED test sheet. Enrollment was 120 participants."


def response(answer, passages=(PASSAGE,), message_type="answer"):
    return {
        "message_type": message_type,
        "answer": answer,
        "claims": [{"evidence": [{"document_name": "doc.pdf", "document_content": p}] for p in passages}] if passages else [],
    }


def test_verbatim_quote_with_citation_passes():
    answer = "From the approved sources:\n\n“The planted Zorvantel dosing interval is 97 weeks according to the APPROVED test sheet.” [1]\n\nReference\n[1] doc — version 2.0."
    report = claim_check.check_response(response(answer))
    assert report.passed and report.verbatim_share == 1.0 and len(report.claims) == 1


def test_changed_number_is_a_mismatch():
    report = claim_check.check_response(response("The Zorvantel dosing interval is 91 weeks. [1]"))
    assert not report.passed and len(report.mismatched) == 1 and "number:91 weeks" in report.unsupported[0].missing


def test_invented_name_and_unsupported_text_fail():
    report = claim_check.check_response(response("Zorvantel reduced mortality in Smith et al. trial participants. [1]"))
    assert not report.passed and report.unsupported


def test_paraphrase_with_matching_facts_passes_but_is_not_verbatim():
    report = claim_check.check_response(response("Zorvantel dosing interval is 97 weeks per the APPROVED test sheet. [1]"))
    assert report.passed and report.verbatim_share == 0.0


def test_answer_without_citation_marker_is_a_citation_problem():
    report = claim_check.check_response(response("The planted Zorvantel dosing interval is 97 weeks according to the APPROVED test sheet."))
    assert report.citation_problems and not report.passed


def test_answer_with_no_sources_is_never_acceptable():
    report = claim_check.check_response(response("Zorvantel works. [1]", passages=()))
    assert not report.has_sources and "answer without any source" in report.citation_problems and not report.passed


def test_non_answers_are_not_scored():
    report = claim_check.check_response(response("I could not find this in the approved sources.", passages=(), message_type="escalation"))
    assert report.claims == [] and not report.citation_problems


def test_split_claims_drops_reference_block_and_markers():
    claims = claim_check.split_claims("From the approved sources:\n\n“One fact is here.” [1]\n\nReference\n[1] Title — version 1.")
    assert claims == ["One fact is here."]
