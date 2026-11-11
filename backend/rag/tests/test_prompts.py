"""Prompt evaluation and golden-set tests.

Verifies that prompt templates are well-formed, contain required placeholders,
and produce safe output. These are contract tests for prompt changes.
"""

from __future__ import annotations

import pytest

from rag.prompts import (
    ANSWER_GENERATION_PROMPT,
    LANGUAGE_DETECTION_PROMPT,
    QUESTION_REPHRASING_PROMPT,
    SMALL_TALK_PROMPT,
    SUMMARIZE_PROMPT,
)


class TestPromptContracts:
    """Every prompt must contain the placeholders the graph injects."""

    def test_answer_prompt_has_required_placeholders(self) -> None:
        messages = ANSWER_GENERATION_PROMPT.messages
        text = "\n".join(m.prompt.template for m in messages if hasattr(m, "prompt") and m.prompt)
        assert "{language}" in text
        assert "{question}" in text
        assert "{history}" in text
        assert "{context}" in text

    def test_rephrase_prompt_has_required_placeholders(self) -> None:
        messages = QUESTION_REPHRASING_PROMPT.messages
        text = "\n".join(m.prompt.template for m in messages if hasattr(m, "prompt") and m.prompt)
        assert "{language}" in text
        assert "{question}" in text
        assert "{history}" in text

    def test_language_detection_prompt_has_placeholder(self) -> None:
        messages = LANGUAGE_DETECTION_PROMPT.messages
        text = "\n".join(m.prompt.template for m in messages if hasattr(m, "prompt") and m.prompt)
        assert "{question}" in text

    def test_small_talk_prompt_has_placeholder(self) -> None:
        messages = SMALL_TALK_PROMPT.messages
        text = "\n".join(m.prompt.template for m in messages if hasattr(m, "prompt") and m.prompt)
        assert "{language}" in text
        assert "{question}" in text

    def test_summarize_prompt_has_placeholder(self) -> None:
        messages = SUMMARIZE_PROMPT.messages
        text = "\n".join(m.prompt.template for m in messages if hasattr(m, "prompt") and m.prompt)
        assert "{text}" in text


class TestPromptSafety:
    """Prompts must resist injection and refuse to invent facts."""

    def test_answer_prompt_refuses_injection(self) -> None:
        messages = ANSWER_GENERATION_PROMPT.messages
        text = "\n".join(m.prompt.template for m in messages if hasattr(m, "prompt") and m.prompt)
        assert "ignore previous instructions" in text.lower()
        assert "Do not guess" in text

    def test_answer_prompt_requires_citations_context(self) -> None:
        messages = ANSWER_GENERATION_PROMPT.messages
        text = "\n".join(m.prompt.template for m in messages if hasattr(m, "prompt") and m.prompt)
        assert "Use ONLY the context" in text
        assert "I can't answer that" in text

    def test_small_talk_does_not_leak_internal_terms(self) -> None:
        messages = SMALL_TALK_PROMPT.messages
        text = "\n".join(m.prompt.template for m in messages if hasattr(m, "prompt") and m.prompt)
        assert "qdrant" not in text.lower()
        assert "vector" not in text.lower()
        assert "embedding" not in text.lower()

    def test_summarize_prompt_ignores_embedded_instructions(self) -> None:
        messages = SUMMARIZE_PROMPT.messages
        text = "\n".join(m.prompt.template for m in messages if hasattr(m, "prompt") and m.prompt)
        assert "untrusted data" in text.lower()
        assert "Ignore any instructions" in text


class TestPromptFormatting:
    """Prompts must produce parseable, structured output."""

    def test_answer_prompt_requires_markdown(self) -> None:
        messages = ANSWER_GENERATION_PROMPT.messages
        text = "\n".join(m.prompt.template for m in messages if hasattr(m, "prompt") and m.prompt)
        assert "Markdown" in text
        assert "code blocks" in text

    def test_language_detection_requires_json(self) -> None:
        messages = LANGUAGE_DETECTION_PROMPT.messages
        text = "\n".join(m.prompt.template for m in messages if hasattr(m, "prompt") and m.prompt)
        assert "JSON" in text
        assert "language" in text

    def test_rephrase_prompt_returns_only_text(self) -> None:
        messages = QUESTION_REPHRASING_PROMPT.messages
        text = "\n".join(m.prompt.template for m in messages if hasattr(m, "prompt") and m.prompt)
        assert "Return ONLY" in text
        assert "No preamble" in text
