from digital_msl.claim_atomization import atomize_compound_claims
from digital_msl.contracts import Claim, ClaimEvidence


def test_atomize_splits_multi_drug_letter_gains():
    claim = Claim(
        claim_id="C1",
        claim_text=(
            "Mean BCVA change from baseline to week 52 was 10.5 letters in EYLEA2, "
            "11.4 letters in RANI, and 11.3 letters in BEVA."
        ),
        evidence=[
            ClaimEvidence(
                chunk_id="c1",
                document_id="d1",
                document_name="DME.pdf",
                document_content="Results text",
                page_no=4,
                paragraph="p1",
                original_text="10.5 letters EYLEA2 11.4 RANI 11.3 BEVA",
            )
        ],
    )
    out = atomize_compound_claims([claim])
    assert len(out) == 3
    assert all("letters" in c.claim_text.lower() for c in out)
    assert out[0].claim_id == "C1" and out[2].claim_id == "C3"
