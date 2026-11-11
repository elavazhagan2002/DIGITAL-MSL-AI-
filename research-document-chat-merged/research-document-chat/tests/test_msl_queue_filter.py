"""MSL queue filtering by therapeutic area assignment."""
from __future__ import annotations

import pytest

from digital_msl import store
from digital_msl.config import get_dmsl_settings
from digital_msl.contracts import InboundMessage


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGITAL_MSL_DATABASE_URL", f"sqlite:///{tmp_path / 'msl_queue.db'}")
    monkeypatch.setenv("DIGITAL_MSL_TRACE_DIR", str(tmp_path / "traces"))
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()
    store.init_store()
    yield
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()


def test_msl_without_ta_sees_no_unassigned_pool_cases():
    store.seed_portal_users()
    store.upsert_therapeutic_area(repository_id="ta-a", name="Retina A", owner_msl_id="usr_other")
    store.create_case(
        InboundMessage(
            channel="web",
            repository_id="ta-a",
            message_text="Question?",
            user_id="usr_doctor",
        ),
        trace_id="trace_x",
        reason_codes=["NO_SUPPORTED_CLAIMS"],
    )
    cases = store.list_cases(viewer_msl_id="usr_msl")
    assert cases == []


def test_msl_sees_cases_in_assigned_ta():
    store.seed_portal_users()
    store.upsert_therapeutic_area(repository_id="ta-mine", name="Mine", owner_msl_id="usr_msl")
    store.create_case(
        InboundMessage(
            channel="web",
            repository_id="ta-mine",
            message_text="DME question?",
            user_id="usr_doctor",
        ),
        trace_id="trace_y",
        reason_codes=["NO_SUPPORTED_CLAIMS"],
    )
    cases = store.list_cases(viewer_msl_id="usr_msl")
    assert len(cases) == 1
    assert cases[0]["repository_id"] == "ta-mine"
