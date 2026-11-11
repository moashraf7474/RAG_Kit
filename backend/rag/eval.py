"""RAGAS evaluation wrapper.

Provides a thin, optional wrapper around the `ragas` library so the rest of
the codebase never imports it directly. When the `evaluation` extra is not
installed, all metrics return None and the caller can decide how to degrade.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


def _patch_ragas_vertexai_import() -> None:
    """Work around a packaging bug in `ragas.llms.base`.

    It unconditionally imports `langchain_community.chat_models.vertexai`,
    which no longer exists in current `langchain-community` releases (Google
    Vertex support moved to a separate package). Nothing in this codebase
    configures a Vertex provider, so the import is dead weight — stub the
    module before ragas loads it rather than pin `langchain-community` down
    for the whole project just to satisfy this optional extra.
    """
    import sys
    import types

    module_name = "langchain_community.chat_models.vertexai"
    if module_name in sys.modules:
        return
    try:
        import langchain_community.chat_models.vertexai  # noqa: F401
        return  # actually available; nothing to patch
    except ImportError:
        pass

    stub = types.ModuleType(module_name)

    class ChatVertexAI:  # pragma: no cover - never instantiated
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "Vertex AI is not configured in this project; "
                "langchain-community no longer ships ChatVertexAI."
            )

    stub.ChatVertexAI = ChatVertexAI
    sys.modules[module_name] = stub


@dataclass
class EvaluationResult:
    """Single RAGAS evaluation result."""

    faithfulness_score: float | None = None
    answer_relevancy_score: float | None = None
    context_precision_score: float | None = None
    context_recall_score: float | None = None
    duration_seconds: float = 0.0
    error: str | None = None


def evaluate_rag(
    query: str,
    answer: str,
    contexts: list[str],
    ground_truth: str | None = None,
) -> EvaluationResult:
    """Run RAGAS evaluation for a single query/answer/context tuple.

    Returns an EvaluationResult with scores or an error field if evaluation
    failed or ragas is not installed.
    """
    start = time.perf_counter()
    result = EvaluationResult()

    try:
        _patch_ragas_vertexai_import()
        from ragas import evaluate
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import (
            Faithfulness,
            AnswerRelevancy,
            ContextPrecision,
            ContextRecall,
        )
        from datasets import Dataset
    except ImportError:
        result.error = "ragas is not installed. Install with: uv sync --extra evaluation"
        result.duration_seconds = time.perf_counter() - start
        return result

    try:
        # Reuse the same chat model and embedder the rest of the app is
        # already configured with (including a local/Ollama setup), rather
        # than RAGAS's OpenAI-only default, which requires its own key.
        from rag.llm import get_chat_model, get_embedder

        judge_llm = LangchainLLMWrapper(get_chat_model())
        judge_embeddings = LangchainEmbeddingsWrapper(get_embedder())

        dataset = Dataset.from_list([
            {
                "question": query,
                "answer": answer,
                "contexts": contexts,
                "ground_truth": ground_truth or "",
            }
        ])

        eval_result = evaluate(
            dataset=dataset,
            metrics=[
                Faithfulness(),
                AnswerRelevancy(),
                ContextPrecision(),
                ContextRecall(),
            ],
            llm=judge_llm,
            embeddings=judge_embeddings,
        )

        # RAGAS returns a Result wrapper. Convert it to a DataFrame before
        # inspecting columns; `column_names` belongs to Dataset, not Result.
        frame = eval_result.to_pandas()

        def _score(column: str) -> float | None:
            if column not in frame.columns:
                return None
            value = frame[column].iloc[0]
            if value is None:
                return None
            value = float(value)
            return None if math.isnan(value) else value

        result.faithfulness_score = _score("faithfulness")
        result.answer_relevancy_score = _score("answer_relevancy")
        result.context_precision_score = _score("context_precision")
        result.context_recall_score = _score("context_recall")
    except Exception:
        logger.exception("RAGAS evaluation failed.")
        result.error = "evaluation_failed"

    result.duration_seconds = time.perf_counter() - start
    return result


def evaluate_batch(
    queries: list[str],
    answers: list[str],
    contexts_list: list[list[str]],
    ground_truths: list[str] | None = None,
) -> list[EvaluationResult]:
    """Evaluate a batch of RAG triples."""
    results = []
    ground_truths = ground_truths or [""] * len(queries)
    for query, answer, contexts, gt in zip(queries, answers, contexts_list, ground_truths):
        results.append(evaluate_rag(query, answer, contexts, gt))
    return results
