from digital_msl.claim_quality import (
    claim_answers_query,
    claim_states_quantitative_result,
    claims_conflict,
    evidence_is_outcome_label_only,
    query_expects_quantitative_answer,
    query_parts,
    text_has_study_result_value,
)


def test_quant_query_detection():
    assert query_expects_quantitative_answer("What visual acuity gain did aflibercept show in DME?")
    assert not query_expects_quantitative_answer("What is aflibercept?")


def test_endpoint_label_vs_result():
    label = "Item 1 measure: Mean Change From Baseline in BCVA at Week 52"
    result = "intravitreal aflibercept improved BCVA by 10.7 letters at week 52"
    assert evidence_is_outcome_label_only(label)
    assert not evidence_is_outcome_label_only(result)
    assert text_has_study_result_value(result)
    assert claim_states_quantitative_result(result)
    assert text_has_study_result_value("Treatment switching occurred in 167 of 563 eyes (29.7%).")
    assert text_has_study_result_value("The mean time to switching was 4.5 ± 0.7 months.")
    assert claim_states_quantitative_result("29.7% of eyes switched treatment.")


def test_relevance_conflict_and_multipart_split():
    query = "What visual acuity gain did aflibercept show in diabetic macular edema?"
    assert claim_answers_query(query, "Aflibercept improved visual acuity by 10.7 letters.")
    assert not claim_answers_query(query, "Patients were followed at European clinical sites.")
    assert claims_conflict(
        "Aflibercept improved visual acuity by 10.7 letters.",
        "Aflibercept improved visual acuity by 8.4 letters.",
    )
    assert not claims_conflict(
        "29.7% of eyes switched treatment.",
        "The mean time to switching was 4.5 months.",
    )
    parts = query_parts(
        "In the diabetic macular edema cohort, what percentage of eyes switched treatment, and what was the mean time to switching?"
    )
    assert len(parts) == 2
