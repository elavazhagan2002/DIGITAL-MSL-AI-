"""Tests for final answer summarization and email letter formatting."""
import asyncio

from digital_msl.contracts import AgentResponse, Claim, ClaimEvidence, InboundMessage
from digital_msl.summary import (
    ComposedAnswer,
    citation_section,
    compact_references,
    compose_validated_answer,
    document_title,
    format_email_letter,
    format_web_answer,
    numbers_preserved,
    summary_paragraph_count,
    topic_phrase,
)
from digital_msl.channels import render_medical_information_letter


def _switching_claims() -> list[Claim]:
    evidence = ClaimEvidence(
        chunk_id="c1",
        document_id="doc-1",
        document_name="pubmed_42920147_PMC13693012_Real-world injection burden_ treatment switching_ and visual.pdf",
        document_content="Treatment switching from bevacizumab to aflibercept occurred in 167 of 563 eyes (29.7%).",
        page_no=4,
        paragraph="Section: Results | Paragraph 46 | Lines 47-48",
        section="Age (years), mean ± SD",
        original_text="Treatment switching from bevacizumab to aflibercept occurred in 167 of 563 eyes (29.7%).",
    )
    evidence2 = ClaimEvidence(
        chunk_id="c1",
        document_id="doc-1",
        document_name="pubmed_42920147_PMC13693012_Real-world injection burden_ treatment switching_ and visual.pdf",
        document_content="The mean time to switching was 4.5 ± 0.7 months after treatment initiation.",
        page_no=4,
        paragraph="Section: Results | Paragraph 46 | Lines 47-48",
        section="Age (years), mean ± SD",
        original_text="The mean time to switching was 4.5 ± 0.7 months after treatment initiation.",
    )
    return [
        Claim(
            claim_id="C1",
            claim_text="29.7% of eyes switched treatment from bevacizumab to aflibercept.",
            evidence=[evidence],
        ),
        Claim(
            claim_id="C2",
            claim_text="The mean time to switching was 4.5 ± 0.7 months after treatment initiation.",
            evidence=[evidence2],
        ),
    ]


def test_deterministic_summary_is_concise_without_passage_copy():
    query = (
        "In the 563-eye diabetic macular edema cohort, what percentage of eyes switched treatment, "
        "and what was the mean time to switching to aflibercept?"
    )
    response = AgentResponse(
        message_id="m1",
        correlation_id="c1",
        conversation_id="conv1",
        channel="web",
        status="answered",
        decision="respond",
        answer="draft",
        overall_score=0.98,
        threshold=0.9,
        claims=_switching_claims(),
    )
    composed = asyncio.run(compose_validated_answer(query, response, llm=None))
    assert "29.7%" in composed.body
    assert "4.5" in composed.body
    assert summary_paragraph_count(composed.body) >= 4
    assert "retrieved evidence" not in composed.body.lower()
    assert "Supporting excerpt" not in composed.body
    assert len(composed.reference_lines) == 1
    assert "page 4" in composed.reference_lines[0]
    assert "Results" in composed.reference_lines[0]
    assert "Age (years)" not in composed.reference_lines[0]


def test_numeric_guard_rejects_fabricated_values():
    claims = _switching_claims()
    assert numbers_preserved("29.7% switched in 4.5 ± 0.7 months.", claims)
    assert not numbers_preserved("99% switched in 12 months.", claims)


def test_document_title_strips_pubmed_prefix():
    name = "pubmed_42920147_PMC13693012_Real-world injection burden_ treatment switching_ and visual.pdf"
    assert document_title(name).startswith("Real-world injection burden")


def test_citation_section_prefers_paragraph_over_table_label():
    ev = _switching_claims()[0].evidence[0]
    assert citation_section(ev) == "Results"


def test_email_letter_matches_clean_template():
    message = InboundMessage(
        channel="email",
        repository_id="repo-1",
        message_text="What percentage switched in DME?",
        external_sender_id="santhosh.s@rudhrainfosolutions.com",
        sender_name="Santhosh Kumar Santhanam",
        subject="Diabetic macular edema",
    )
    response = AgentResponse(
        message_id="m1",
        correlation_id="c1",
        conversation_id="conv1",
        channel="email",
        status="answered",
        decision="respond",
        answer="",
        overall_score=0.98,
        threshold=0.9,
        claims=_switching_claims(),
        presentation_body=(
            "In a real-world cohort of 563 eyes with DME, 167 eyes (29.7%) switched from bevacizumab to aflibercept. "
            "The mean time to switching was 4.5 ± 0.7 months after treatment initiation."
        ),
        presentation_references=[
            "Real-world injection burden, treatment switching, and visual outcomes — page 4, Results section."
        ],
    )
    letter = render_medical_information_letter(message, response)
    assert "Dear Dr. Santhanam," in letter
    assert letter.count("29.7%") == 1
    assert "Supporting Scientific Evidence" not in letter
    assert "Key finding:" not in letter
    assert "Reference" in letter
    assert "Please let us know if you require any further scientific information." in letter
    assert "Digital MSL" in letter


def test_web_answer_separates_body_and_reference():
    composed = ComposedAnswer(
        body="Aflibercept improved visual acuity by 10.7 letters at week 52.",
        reference_lines=["VIVID VISTA DME — page 4, Results section."],
        topic_phrase="visual acuity in dme",
    )
    text = format_web_answer(composed)
    assert text.startswith("Aflibercept improved")
    assert "Reference" in text
    assert text.count("10.7") == 1


def test_simple_question_gets_short_body():
    claims = [
        Claim(
            claim_id="C1",
            claim_text="Ocular adverse events were similar between groups.",
            evidence=[
                ClaimEvidence(
                    chunk_id="c1",
                    document_name="trial.pdf",
                    document_content="Ocular adverse events were similar between groups.",
                    page_no=2,
                    paragraph="Section: Safety | Paragraph 3",
                    original_text="Ocular adverse events were similar between groups.",
                )
            ],
        )
    ]
    refs = compact_references(claims)
    assert len(refs) == 1
    assert "page 2" in refs[0]
