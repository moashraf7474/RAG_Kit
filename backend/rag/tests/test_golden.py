"""Opt-in answer-quality regression checks.

The contract helpers run offline in normal CI. Live cases require a configured
organization and model provider, so they are explicitly enabled with
``RUN_GOLDEN=1`` and ``GOLDEN_ORGANIZATION_ID``.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
import yaml

from rag.graph import answer

GOLDEN = yaml.safe_load((Path(__file__).parent / "golden/set.yaml").read_text())


def assert_golden_answer(case: dict, answer_text: str) -> None:
    """Apply content assertions shared by live golden tests and unit tests."""
    normalized = answer_text.lower()
    for phrase in case.get("must_contain", []):
        assert phrase.lower() in normalized, (
            f"expected {phrase!r} in answer: {answer_text[:300]}"
        )
    for phrase in case.get("must_not_contain", []):
        assert phrase.lower() not in normalized, (
            f"forbidden {phrase!r} found in answer: {answer_text[:300]}"
        )


def test_golden_assertions_are_deterministic():
    case = GOLDEN[0]
    assert_golden_answer(case, "RAG explains retrieval augmented generation.")
    with pytest.raises(AssertionError):
        assert_golden_answer(case, "I couldn't find anything in the knowledge base.")


@pytest.mark.golden
@pytest.mark.skipif(
    os.environ.get("RUN_GOLDEN") != "1",
    reason="Live golden checks require RUN_GOLDEN=1",
)
@pytest.mark.parametrize("case", GOLDEN, ids=lambda case: case["id"])
def test_golden_answers(case: dict):
    organization_id = os.environ.get("GOLDEN_ORGANIZATION_ID")
    if not organization_id:
        pytest.fail("GOLDEN_ORGANIZATION_ID is required when RUN_GOLDEN=1")

    result = asyncio.run(
        answer(
            case["question"],
            organization_id=organization_id,
        )
    )
    assert result["confidence"] != "unknown", "golden answer had no scored citations"
    assert_golden_answer(case, result["answer"])
