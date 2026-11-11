"""Small-talk gate tests.

The gate decides whether a message skips retrieval entirely. These tests keep
the regex config honest so a pure greeting can never leak into the vector DB
and a real knowledge query can never be swallowed by the gate.
"""

from __future__ import annotations

from unittest.mock import patch

from rag.graph import is_small_talk, _route_from_start, _conversational_node
from rag.conf import get_config


def test_pure_greetings_are_small_talk():
    for message in ("hey", "hi", "Hello", "hello there!", "good morning", "HI!!!"):
        assert is_small_talk(message), message


def test_thanks_and_bye_are_small_talk():
    for message in ("thanks", "Thank you!", "bye", "see you", "ty"):
        assert is_small_talk(message), message


def test_capability_questions_are_small_talk():
    for message in ("what can you do", "who are you", "What can you help me with?"):
        assert is_small_talk(message), message


def test_where_help_questions_are_small_talk():
    for message in ("where can you help", "where you can help me", "what is this"):
        assert is_small_talk(message), message


def test_real_queries_are_not_small_talk():
    for message in (
        "hey how can we sell",
        "how much does AI Build service cost",
        "recipe for butter chicken",
        "what is RAG?",
        "explain the pricing",
    ):
        assert not is_small_talk(message), message


def test_blank_is_not_small_talk():
    assert not is_small_talk("   ")


def test_route_from_start_small_talk():
    state = {"question": "hey"}
    assert _route_from_start(state) == "conversational"


def test_route_from_start_knowledge_query():
    state = {"question": "how we can sell"}
    assert _route_from_start(state) == "determine_language"


def test_disabled_gate_sends_everything_to_retrieval():
    with patch.object(get_config().small_talk, "enabled", False):
        assert not is_small_talk("hey")
        assert _route_from_start({"question": "hey"}) == "determine_language"


def test_template_mode_returns_static_reply_without_llm():
    import asyncio
    from unittest.mock import AsyncMock

    with patch("rag.graph.get_chat_model", side_effect=AssertionError("no LLM call")) as llm:
        result = asyncio.run(_conversational_node({"question": "hey"}))

    llm.assert_not_called()
    assert result["citations"] == []
    assert result["finish_reason"] == "stop"
    assert result["answer_text"] == get_config().small_talk.response


def test_llm_mode_uses_model_and_falls_back_to_template():
    import asyncio
    from unittest.mock import AsyncMock

    fake_chain = AsyncMock()
    fake_chain.ainvoke.return_value = type(
        "FakeResponse", (), {"content": "Hello! How can I help you today?"}
    )()

    with patch("rag.graph.get_chat_model", return_value=object()) as get_model, patch(
        "rag.graph.SMALL_TALK_PROMPT"
    ) as prompt:
        prompt.__or__.return_value = fake_chain
        with patch.object(get_config().small_talk, "reply_mode", "llm"):
            result = asyncio.run(_conversational_node({"question": "hey", "language": "en"}))

    get_model.assert_called_once()
    fake_chain.ainvoke.assert_awaited_once_with(
        {"question": "hey", "language": "en"}, config=None
    )
    assert result["answer_text"].endswith("today?")
    assert result["citations"] == []
    assert result["finish_reason"] == "stop"


def test_llm_mode_error_falls_back_to_template():
    import asyncio
    from unittest.mock import AsyncMock

    fake_chain = AsyncMock()
    fake_chain.ainvoke.side_effect = RuntimeError("model down")

    with patch("rag.graph.get_chat_model", return_value=object()), patch(
        "rag.graph.SMALL_TALK_PROMPT"
    ) as prompt:
        prompt.__or__.return_value = fake_chain
        with patch.object(get_config().small_talk, "reply_mode", "llm"):
            result = asyncio.run(_conversational_node({"question": "hey"}))

    assert result["answer_text"] == get_config().small_talk.response
    assert result["citations"] == []
