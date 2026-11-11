"""
Automated Unit Tests for Citation Extraction and Validation.
Verifies that generated citations match retrieved chunks and reject fabricated citations.
"""
from utils.citation_validator import CitationValidator


def test_citation_extraction():
    text = (
        'The primary endpoint was met with 45% overall response rate '
        '<span class="citation"><span class="citation-pill">'
        '[ONCO_REPO:Pembrolizumab_Study.pdf:4] — "45% overall response rate"</span></span> '
        'as reported in the trial.'
    )
    citations = CitationValidator.extract_citations(text)
    assert len(citations) == 1
    assert citations[0]["repository"] == "ONCO_REPO"
    assert citations[0]["document"] == "Pembrolizumab_Study.pdf"
    assert citations[0]["page"] == "4"
    assert citations[0]["quote"] == "45% overall response rate"


def test_citation_validation_against_real_chunks():
    text = (
        'The overall survival was 24 months '
        '<span class="citation"><span class="citation-pill">'
        '[REPO_A:Trial_Results.docx:12] — "overall survival was 24 months"</span></span>.'
    )

    chunks = [
        {
            "chunk_index": 0,
            "file_id": "doc_123",
            "file_name": "Trial_Results.docx",
            "repository_name": "REPO_A",
            "page_number": 12,
            "content": "In cohort 1, the overall survival was 24 months."
        }
    ]

    res = CitationValidator.validate_citations_against_chunks(text, chunks, target_repository_id="REPO_A")
    assert res["is_fully_traceable"] is True
    assert len(res["verified_citations"]) == 1
    assert len(res["unverified_citations"]) == 0


def test_citation_validation_detects_hallucinated_document():
    text = (
        'The response rate was 99% '
        '<span class="citation"><span class="citation-pill">'
        '[REPO_A:Fabricated_Study.pdf:1] — "response rate was 99%"</span></span>.'
    )

    chunks = [
        {
            "chunk_index": 0,
            "file_id": "doc_123",
            "file_name": "Real_Study.pdf",
            "repository_name": "REPO_A",
            "page_number": 1,
            "content": "Some real evidence."
        }
    ]

    res = CitationValidator.validate_citations_against_chunks(text, chunks, target_repository_id="REPO_A")
    assert res["is_fully_traceable"] is False
    assert len(res["verified_citations"]) == 0
    assert len(res["unverified_citations"]) == 1


def test_citation_validation_rejects_unretrieved_page():
    text = (
        'The result was significant '
        '<span class="citation"><span class="citation-pill">'
        '[REPO_A:Trial_Results.docx:99] — "result was significant"</span></span>.'
    )
    chunks = [{
        "chunk_index": 0,
        "file_id": "doc_123",
        "file_name": "Trial_Results.docx",
        "repository_name": "REPO_A",
        "repository_id": "REPO_A",
        "page_number": 12,
        "page_end": 12,
        "content": "Evidence from page 12 only.",
    }]
    res = CitationValidator.validate_citations_against_chunks(text, chunks, target_repository_id="REPO_A")
    assert res["is_fully_traceable"] is False
    assert len(res["unverified_citations"]) == 1
