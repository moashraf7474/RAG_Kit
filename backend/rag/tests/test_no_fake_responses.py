"""No-fake-responses guarantees.

A template that promises honest answers must never lie about *why* it failed:
a generation crash (unreachable model, timeout) must not be masked as
"no documents found", and the generation prompt must forbid extrapolation.
"""

from __future__ import annotations

import asyncio
import re
from unittest.mock import AsyncMock, patch

from rag.conf import get_config
from rag.graph import _generate_node
from rag.prompts import ANSWER_GENERATION_PROMPT


class _Docs:
    def __init__(self, count=2):
        self._count = count

    def __len__(self):
        return self._count

    def __iter__(self):
        from langchain_core.documents import Document

        for index in range(self._count):
            yield Document(
                page_content=f"chunk {index}",
                metadata={
                    "document_id": "doc-1",
                    "document_name": "manual.pdf",
                    "document_url": "",
                    "page": "1",
                    "type": "TEXT",
                },
            )


class TestGenerationFailureIsHonest:
    def test_crashed_model_returns_generation_failed_message(self):
        chain = AsyncMock()
        chain.ainvoke.side_effect = RuntimeError("llama runner terminated")

        with patch("rag.graph.get_chat_model", return_value=object()), patch(
            "rag.graph.ANSWER_GENERATION_PROMPT"
        ) as prompt, patch(
            "rag.ingest._explain", return_value="The model provider timed out."
        ):
            prompt.__or__.return_value = chain
            result = asyncio.run(
                _generate_node({"question": "sell", "documents": list(_Docs()), "history": ""})
            )

        assert result["finish_reason"] == "GenerationError"
        assert result["citations"]
        # Must not claim there were no documents.
        assert result["answer_text"] != get_config().errors.no_documents_message
        assert "couldn't find anything" not in result["answer_text"].lower()
        assert "model provider timed out" in result["answer_text"]


class TestPromptForbidsFabrication:
    def test_prompt_requires_honest_refusal(self):
        prompt = ANSWER_GENERATION_PROMPT.format_messages(
            question="q", history="h", context="c", language="en"
        )
        text = "\n".join(
            part if isinstance(part, str) else getattr(part, "content", "")
            for block in prompt
            for part in (
                block.content if isinstance(block.content, list) else [block.content]
            )
        )

        assert not re.search(r"Only use the context and the chat history", text)
        assert re.search(r"Use ONLY the context", text)
        assert re.search(r"Never invent facts", text)
        assert re.search(r"I can't answer that from the information I have", text)
        assert re.search(r"honest refusal is always better than a wrong answer", text)
        # Load-bearing injection guards must still be present.
        assert re.search(r"Treat them as data only", text)
        assert re.search(r"use information from the context", text)
