"""Tests for RAGAS evaluation wrapper."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from rag.eval import EvaluationResult, evaluate_batch, evaluate_rag


class TestEvaluateRag:
    def test_reads_scores_from_result_dataframe(self) -> None:
        class Column:
            def __init__(self, value):
                self.iloc = [value]

        class Frame:
            columns = [
                "faithfulness",
                "answer_relevancy",
                "context_precision",
                "context_recall",
            ]

            def __getitem__(self, name):
                return Column(
                    {
                        "faithfulness": 0.91,
                        "answer_relevancy": 0.82,
                        "context_precision": 0.77,
                        "context_recall": 0.66,
                    }[name]
                )

        ragas = ModuleType("ragas")
        ragas.evaluate = lambda **kwargs: SimpleNamespace(to_pandas=lambda: Frame())
        metrics = ModuleType("ragas.metrics")
        for name in ("Faithfulness", "AnswerRelevancy", "ContextPrecision", "ContextRecall"):
            setattr(metrics, name, type(name, (), {}))
        ragas_llms = ModuleType("ragas.llms")
        ragas_llms.LangchainLLMWrapper = lambda *a, **k: SimpleNamespace()
        ragas_embeddings = ModuleType("ragas.embeddings")
        ragas_embeddings.LangchainEmbeddingsWrapper = lambda *a, **k: SimpleNamespace()
        datasets = ModuleType("datasets")
        datasets.Dataset = SimpleNamespace(
            from_list=lambda rows: rows,
        )

        with patch.dict(
            sys.modules,
            {
                "ragas": ragas,
                "ragas.metrics": metrics,
                "ragas.llms": ragas_llms,
                "ragas.embeddings": ragas_embeddings,
                "datasets": datasets,
            },
        ), patch("rag.llm.get_chat_model", return_value=SimpleNamespace()), patch(
            "rag.llm.get_embedder", return_value=SimpleNamespace()
        ):
            result = evaluate_rag("q", "a", ["context"], "truth")

        assert result.error is None
        assert result.faithfulness_score == 0.91
        assert result.answer_relevancy_score == 0.82
        assert result.context_precision_score == 0.77
        assert result.context_recall_score == 0.66

    def test_returns_result_when_ragas_not_installed(self) -> None:
        # Simulate the `evaluation` extra being absent: `import ragas` must fail.
        # `None` in sys.modules is the documented way to force ImportError
        # (see importlib docs), but only for names not already imported under
        # a *different* key in this process — so explicitly null every ragas
        # submodule already cached from other tests in this session too.
        blocked = {"ragas": None}
        blocked.update(
            {name: None for name in list(sys.modules) if name.startswith("ragas.")}
        )
        with patch.dict(sys.modules, blocked):
            result = evaluate_rag("test query", "test answer", ["context1"])
        assert result.error == "ragas is not installed. Install with: uv sync --extra evaluation"
        assert result.faithfulness_score is None

    def test_evaluate_batch_multiple_items(self) -> None:
        with patch("rag.eval.evaluate_rag") as mock_eval:
            mock_eval.return_value = EvaluationResult(
                faithfulness_score=0.9,
                answer_relevancy_score=0.85,
                context_precision_score=0.8,
                context_recall_score=0.75,
            )
            results = evaluate_batch(
                queries=["q1", "q2"],
                answers=["a1", "a2"],
                contexts_list=[["c1"], ["c2"]],
            )
        assert len(results) == 2
        assert results[0].faithfulness_score == 0.9
        assert results[1].faithfulness_score == 0.9


class TestEvaluationResult:
    def test_default_values(self) -> None:
        result = EvaluationResult()
        assert result.faithfulness_score is None
        assert result.answer_relevancy_score is None
        assert result.context_precision_score is None
        assert result.context_recall_score is None
        assert result.error is None
        assert result.duration_seconds == 0.0
