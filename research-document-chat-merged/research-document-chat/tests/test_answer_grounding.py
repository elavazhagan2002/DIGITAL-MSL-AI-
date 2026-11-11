"""Answer grounding: citations follow the passage that states the fact, and only asked claims are returned."""
import json

import pytest

from digital_msl import store
from digital_msl.config import get_dmsl_settings
from digital_msl.evidence import normalize_result, resolve_evidence_quote
from digital_msl.contracts import EvidenceChunk
from tests.test_digital_msl import RAW_RESULT, _ask


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGITAL_MSL_DATABASE_URL", f"sqlite:///{tmp_path / 'dmsl.db'}")
    monkeypatch.setenv("DIGITAL_MSL_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setenv("DIGITAL_MSL_MOLECULES", "aflibercept,ranibizumab,faricimab")
    monkeypatch.setenv("DIGITAL_MSL_SMTP_HOST", "")
    monkeypatch.setenv("DIGITAL_MSL_IMAP_HOST", "")
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()
    from digital_msl import evidence as evidence_mod

    evidence_mod._DOC_NAME_CACHE.clear()
    yield tmp_path
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()


class ScriptedLLM:
    is_configured = True

    def __init__(self, answer: str, claims: list[dict], verdict: str = "supported"):
        self.answer = answer
        self.claims = claims
        self.verdict = verdict

    async def generate_async(self, messages, temperature=0.7, max_tokens=None, model=None):
        system = messages[0]["content"]
        if "evidence auditor" in system:
            return json.dumps(
                {
                    "results": [
                        {"claim_id": f"C{i + 1}", "verdict": self.verdict, "rationale": "test"}
                        for i in range(len(self.claims))
                    ]
                }
            )
        return json.dumps({"answer": self.answer, "claims": self.claims})


def _chunk(chunk_id: str, text: str, page: int, *, section: str = "Results", line_start: int = 10, line_end: int = 20):
    raw = {
        **RAW_RESULT,
        "chunk_id": chunk_id,
        "text": text,
        "page": page,
        "citation_anchor": {
            "document_id": "doc-1",
            "page_start": page,
            "page_end": page,
            "start_line": line_start,
            "end_line": line_end,
            "parent_section": section,
            "section_name": "Table 1. Mean change",
            "source_blocks": [{"index": 0, "reading_order": 4, "line_start": line_start, "line_end": line_end}],
        },
    }
    return raw


class ListDms:
    def __init__(self, results):
        self.results = results

    def retrieve(self, *, query, top_k=10, repository_id=None, **kwargs):
        return {"results": self.results, "blocked": False}

    def list_repository_documents(self, repository_id, *, limit=500):
        return {"documents": [{"document_id": "doc-1", "original_file_name": "Switching_DME.pdf"}]}


def test_table_heading_is_not_used_as_section():
    chunk = normalize_result(
        _chunk(
            "c-table",
            "Item 1 measure: Mean Change From Baseline in BCVA at Week 52.",
            3,
            section="Item 1 measure: Mean Change From Baseline in BCVA",
        ),
        doc_names={"doc-1": "Protocol.pdf"},
        repository_id="repo-1",
    )
    assert chunk.section == ""
    assert "Item 1" not in chunk.paragraph
    assert "Table 1" not in chunk.paragraph


def test_quote_on_wrong_source_uses_the_page_that_contains_it(isolated_store):
    methods = "Patients with diabetic macular edema were enrolled and followed for one year in clinic."
    result = "Treatment switching occurred in 167 of 563 eyes (29.7%). The mean time to switching was 4.5 months."
    llm = ScriptedLLM(
        "29.7% of eyes switched.",
        [
            {
                "claim_text": "29.7% of eyes switched treatment.",
                "evidence": [{"source_id": "S1", "original_text": "Treatment switching occurred in 167 of 563 eyes (29.7%)."}],
            }
        ],
    )
    response = _ask(
        "In the diabetic macular edema cohort, what percentage of eyes switched treatment?",
        llm,
        dms=ListDms([_chunk("methods", methods, 2, section="Methods"), _chunk("result", result, 9, section="Results", line_start=40, line_end=48)]),
    )
    assert response.decision == "respond", response.reason_codes
    evidence = response.claims[0].evidence[0]
    assert evidence.page_no == 9
    assert evidence.document_name == "Switching_DME.pdf"
    assert "29.7%" in evidence.original_text
    assert evidence.original_text in result
    assert "Results" in evidence.paragraph
    assert evidence.line_start >= 40
    assert evidence.line_end <= 48
    assert "page 9" in response.answer


def test_second_sentence_lines_move_within_the_cited_page():
    passage = (
        "Introduction describes the cohort of patients with diabetic macular edema who received therapy in clinic. "
        "Treatment switching occurred in 167 of 563 eyes (29.7%)."
    )
    chunk = EvidenceChunk(
        chunk_id="c1",
        document_id="doc-1",
        document_name="Switching_DME.pdf",
        document_content=passage,
        page_no=9,
        section="Results",
        paragraph="Section: Results | Paragraph 5 | Lines 10-30",
        line_start=10,
        line_end=30,
    )
    aligned = resolve_evidence_quote(
        "29.7% of eyes switched treatment.",
        "Treatment switching occurred in 167 of 563 eyes (29.7%).",
        chunk,
        [chunk],
    )
    assert aligned.page_no == 9
    assert aligned.original_text.startswith("Treatment switching")
    assert aligned.line_start > 10
    assert aligned.line_end <= 30
    assert "29.7%" in aligned.original_text


def test_irrelevant_extra_claim_is_dropped(isolated_store):
    passage = (
        "In the VIVID and VISTA trials, intravitreal aflibercept 2 mg every 8 weeks improved "
        "best-corrected visual acuity by 10.7 letters at week 52 in patients with diabetic macular edema. "
        "Patients were followed at European clinical sites."
    )
    llm = ScriptedLLM(
        "Aflibercept improved visual acuity by 10.7 letters. Patients were followed in Europe.",
        [
            {
                "claim_text": "Aflibercept improved best-corrected visual acuity by 10.7 letters at week 52 in diabetic macular edema.",
                "evidence": [{"source_id": "S1", "original_text": "intravitreal aflibercept 2 mg every 8 weeks improved best-corrected visual acuity by 10.7 letters at week 52 in patients with diabetic macular edema."}],
            },
            {
                "claim_text": "Patients were followed at European clinical sites.",
                "evidence": [{"source_id": "S1", "original_text": "Patients were followed at European clinical sites."}],
            },
        ],
    )
    response = _ask(
        "What visual acuity gain did aflibercept show in diabetic macular edema?",
        llm,
        dms=ListDms([_chunk("body", passage, 4, line_start=120, line_end=128)]),
    )
    assert response.decision == "respond", response.reason_codes
    assert len(response.claims) == 1
    assert "10.7" in response.claims[0].claim_text
    assert "European" not in response.answer


def test_conflicting_result_values_are_not_answered(isolated_store):
    first = "Intravitreal aflibercept improved best-corrected visual acuity by 10.7 letters at week 52 in diabetic macular edema."
    second = "Intravitreal aflibercept improved best-corrected visual acuity by 8.4 letters at week 52 in diabetic macular edema."
    llm = ScriptedLLM(
        "Visual acuity improved by 10.7 letters and by 8.4 letters.",
        [
            {
                "claim_text": "Aflibercept improved visual acuity by 10.7 letters at week 52 in diabetic macular edema.",
                "evidence": [{"source_id": "S1", "original_text": first}],
            },
            {
                "claim_text": "Aflibercept improved visual acuity by 8.4 letters at week 52 in diabetic macular edema.",
                "evidence": [{"source_id": "S2", "original_text": second}],
            },
        ],
    )
    response = _ask(
        "What visual acuity gain did aflibercept show in diabetic macular edema?",
        llm,
        dms=ListDms([
            _chunk("a", first, 4, line_start=10, line_end=14),
            _chunk("b", second, 5, line_start=20, line_end=24),
        ]),
    )
    assert response.decision == "escalate_to_msl"
    assert "CONFLICTING_EVIDENCE" in response.reason_codes


def test_multipart_question_states_when_one_part_is_missing(isolated_store):
    result = "Treatment switching occurred in 167 of 563 eyes (29.7%)."
    llm = ScriptedLLM(
        "29.7% of eyes switched.",
        [
            {
                "claim_text": "29.7% of eyes switched treatment.",
                "evidence": [{"source_id": "S1", "original_text": result}],
            }
        ],
    )
    response = _ask(
        "In the diabetic macular edema cohort, what percentage of eyes switched treatment, and what was the mean time to switching?",
        llm,
        dms=ListDms([_chunk("result", result, 9)]),
    )
    assert response.decision == "escalate_to_msl"
    assert "INCOMPLETE_ANSWER" in response.reason_codes


def test_multipart_question_answers_each_supported_part(isolated_store):
    result = "Treatment switching occurred in 167 of 563 eyes (29.7%). The mean time to switching was 4.5 months."
    llm = ScriptedLLM(
        "29.7% of eyes switched, and the mean time was 4.5 months.",
        [
            {
                "claim_text": "29.7% of eyes switched treatment.",
                "evidence": [{"source_id": "S1", "original_text": "Treatment switching occurred in 167 of 563 eyes (29.7%)."}],
            },
            {
                "claim_text": "The mean time to switching was 4.5 months.",
                "evidence": [{"source_id": "S1", "original_text": "The mean time to switching was 4.5 months."}],
            },
        ],
    )
    response = _ask(
        "In the diabetic macular edema cohort, what percentage of eyes switched treatment, and what was the mean time to switching?",
        llm,
        dms=ListDms([_chunk("result", result, 9, line_start=40, line_end=52)]),
    )
    assert response.decision == "respond", response.reason_codes
    assert len(response.claims) == 2
    joined = " ".join(c.claim_text for c in response.claims)
    assert "29.7%" in joined and "4.5" in joined


def test_unsupported_number_still_escalates(isolated_store):
    passage = "Intravitreal aflibercept improved best-corrected visual acuity by 10.7 letters at week 52 in diabetic macular edema."
    llm = ScriptedLLM(
        "Aflibercept improved visual acuity by 15.2 letters.",
        [
            {
                "claim_text": "Aflibercept improved visual acuity by 15.2 letters at week 52 in diabetic macular edema.",
                "evidence": [{"source_id": "S1", "original_text": passage}],
            }
        ],
        verdict="unsupported",
    )
    response = _ask(
        "What visual acuity gain did aflibercept show in diabetic macular edema?",
        llm,
        dms=ListDms([_chunk("body", passage, 4)]),
    )
    assert response.decision == "escalate_to_msl"
    assert "NUMERIC_MISMATCH" in response.reason_codes
