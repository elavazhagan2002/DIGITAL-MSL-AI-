"""Social greeting fast path."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from digital_msl import store
from digital_msl.agents.query_agent import QueryAgent
from digital_msl.config import get_dmsl_settings
from digital_msl.contracts import InboundMessage, new_id
from digital_msl.social import is_social_greeting, social_greeting_reply


def test_is_social_greeting_patterns():
    assert is_social_greeting("hi")
    assert is_social_greeting("Hii")
    assert is_social_greeting("Hii!")
    assert is_social_greeting("Hello!")
    assert is_social_greeting("how are you?")
    assert not is_social_greeting("What is the starting dose of metformin?")


def test_social_greeting_reply_includes_help():
    text = social_greeting_reply("Dr. Priya Raman")
    assert "How can I help you today?" in text
    assert "Dr." in text


@pytest.mark.asyncio
async def test_query_agent_social_greeting_skips_retrieval(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGITAL_MSL_DATABASE_URL", f"sqlite:///{tmp_path / 'sg.db'}")
    monkeypatch.setenv("DIGITAL_MSL_TRACE_DIR", str(tmp_path / "traces"))
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()
    store.init_store()

    called = {"retrieve": False}

    def fake_retrieve(*args, **kwargs):
        called["retrieve"] = True
        raise AssertionError("retrieve should not run")

    monkeypatch.setattr("digital_msl.agents.query_agent.retrieve_evidence", fake_retrieve)

    msg = InboundMessage(
        channel="web",
        repository_id="ta-mine",
        message_text="hello",
        user_id="usr_doctor",
        conversation_id=new_id("conv"),
        session_id=new_id("sess"),
    )
    store.seed_portal_users()
    store.upsert_therapeutic_area(repository_id="ta-mine", name="Mine", owner_msl_id="usr_msl")
    store.assign_doctor_repositories("usr_doctor", ["ta-mine"])
    agent = QueryAgent(use_default_llm=False)
    result = await agent.handle(msg)
    assert result.decision == "respond"
    assert called["retrieve"] is False
    assert "help" in (result.answer or "").lower()
