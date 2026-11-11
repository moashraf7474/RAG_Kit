"""Retrieval pipeline tests.

These cover the ported logic that is easy to break silently: summary expansion,
deduplication, pruning, and the tenant filter.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from langchain_core.documents import Document

from rag.conf import get_config
from rag.graph import build_context, document_to_citation, format_history
from rag.retrieval import _early_prune, _expand_summaries, _remove_duplicates


def make_document(doc_id: str, *, content_type="TEXT", related=None, score=0.9, content="text"):
    return Document(
        page_content=content,
        metadata={
            "id": doc_id,
            "type": content_type,
            "related": related or [],
            "score": score,
            "document_id": "doc-1",
            "document_name": "manual.pdf",
        },
    )


class TestRemoveDuplicates:
    def test_keeps_first_occurrence_only(self):
        documents = [make_document("a"), make_document("b"), make_document("a")]
        result = _remove_duplicates(documents)
        assert [d.metadata["id"] for d in result] == ["a", "b"]

    def test_empty_input(self):
        assert _remove_duplicates([]) == []


class TestExpandSummaries:
    def test_summary_is_replaced_by_related_chunks(self):
        summary = make_document("s1", content_type="SUMMARY", related=["c1", "c2"])
        underlying = [make_document("c1"), make_document("c2")]

        with patch("rag.retrieval.get_documents_by_ids", return_value=underlying) as fetch:
            result = _expand_summaries([summary])

        fetch.assert_called_once()
        assert set(fetch.call_args[0][0]) == {"c1", "c2"}
        # The summary itself must never survive as a citation.
        assert all(d.metadata["type"] != "SUMMARY" for d in result)
        assert {d.metadata["id"] for d in result} == {"c1", "c2"}

    def test_does_not_refetch_already_present_chunks(self):
        summary = make_document("s1", content_type="SUMMARY", related=["c1"])
        present = make_document("c1")
        with patch("rag.retrieval.get_documents_by_ids") as fetch:
            result = _expand_summaries([summary, present])
        fetch.assert_not_called()
        assert [d.metadata["id"] for d in result] == ["c1"]

    def test_passthrough_without_summaries(self):
        documents = [make_document("a"), make_document("b")]
        assert _expand_summaries(documents) == documents

    def test_expansion_failure_is_survivable(self):
        """A vector DB hiccup during expansion must not fail the query."""
        summary = make_document("s1", content_type="SUMMARY", related=["c1"])
        with patch("rag.retrieval.get_documents_by_ids", side_effect=RuntimeError("qdrant down")):
            result = _expand_summaries([summary, make_document("a")])
        assert [d.metadata["id"] for d in result] == ["a"]


class TestEarlyPrune:
    def test_prunes_to_total_k_by_score(self):
        documents = [make_document(str(i), score=i / 100) for i in range(30)]
        result = _early_prune(documents)
        expected = get_config().retriever.total_k_documents
        assert len(result) == expected
        # Highest scores survive.
        assert result[0].metadata["score"] > result[-1].metadata["score"]

    def test_no_prune_when_under_cap(self):
        documents = [make_document("a"), make_document("b")]
        assert _early_prune(documents) == documents


class TestRerankGate:
    """The reranker's relevance gate trims the retrieved candidate set.

    Retrieval is lenient on purpose: weak-but-real matches (e.g. a "Client
    Acquisition" section answering "how we can sell") score ~3e-5 in FlashRank's
    sigmoid range and must not be hidden from the model. The gate only (1) drops
    near-zero garbage below the absolute bar and (2) cuts the distant tail via a
    ratio of the anchor. Whether the final answer is honest is decided at
    generation (see `test_no_fake_responses.py`), not here.
    """

    def _ranked_entries(self, scores: list[float]) -> list[dict]:
        return [{"id": index, "score": score} for index, score in enumerate(scores)]

    def _fake_ranker(self, scores: list[float]) -> object:
        class FakeRanker:
            def rerank(self, request):
                return self._scores

        fake = FakeRanker()
        fake._scores = self._ranked_entries(scores)
        return fake

    def _patch(self, scores):
        # rerank.py binds get_config at import time, so patch it there.
        return patch("rag.rerank._get_ranker", return_value=self._fake_ranker(scores))

    def _document(self, doc_id: str) -> Document:
        return Document(
            page_content=f"doc {doc_id}",
            metadata={
                "id": doc_id,
                "type": "TEXT",
                "document_id": "doc-1",
                "document_name": "manual.pdf",
            },
        )

    def test_near_zero_garbage_is_discarded(self):
        """Scores that are effectively zero still surface nothing."""
        from rag.rerank import _rerank_sync

        documents = [self._document("a"), self._document("b")]
        with (
            self._patch([1e-9, 1e-10]),
            patch("rag.rerank.get_config", autospec=True) as cfg,
        ):
            cfg.return_value.reranker.k_documents = 5
            cfg.return_value.reranker.min_relevance_score = 1e-6
            cfg.return_value.reranker.min_relevance_ratio = 0.05
            result = _rerank_sync(documents, "recipe for butter chicken")

        assert result == []

    def test_relative_tail_is_cut(self):
        """A weak-but-real anchor keeps a close tail but drops distant noise.

        Retrieval must not hide low-scoring real matches, so the absolute bar
        stays far below them; only the ratio prunes the noisy tail.
        """
        from rag.rerank import _rerank_sync

        documents = [
            self._document("real-match"),
            self._document("close-tail"),
            self._document("distant-noise"),
        ]
        with (
            self._patch([3.0e-5, 2.5e-5, 1e-9]),
            patch("rag.rerank.get_config", autospec=True) as cfg,
        ):
            cfg.return_value.reranker.k_documents = 5
            cfg.return_value.reranker.min_relevance_score = 1e-6
            cfg.return_value.reranker.min_relevance_ratio = 0.1
            result = _rerank_sync(documents, "how we can sell")

        assert [d.metadata["id"] for d in result] == ["real-match", "close-tail"]

    def test_weak_real_matches_survive_the_absolute_bar(self):
        """Real matches just above the floor are passed through for generation
        to answer honestly instead of being hidden from it."""
        from rag.rerank import _rerank_sync

        documents = [self._document("a"), self._document("b")]
        with (
            self._patch([2e-5, 1.9e-5]),
            patch("rag.rerank.get_config", autospec=True) as cfg,
        ):
            cfg.return_value.reranker.k_documents = 5
            cfg.return_value.reranker.min_relevance_score = 1e-6
            cfg.return_value.reranker.min_relevance_ratio = 0.05
            result = _rerank_sync(documents, "best anime to watch")

        assert [d.metadata["id"] for d in result] == ["a", "b"]


class TestGraphHelpers:
    def test_format_history_respects_limit(self):
        messages = [
            {"role": "user", "content": f"q{i}"} if i % 2 == 0 else {"role": "assistant", "content": f"a{i}"}
            for i in range(10)
        ]
        formatted = format_history(messages)
        # Default limit is 4 messages.
        assert len(formatted.splitlines()) == 4

    def test_format_history_empty(self):
        assert format_history([]) == ""

    def test_citation_shape(self):
        citation = document_to_citation(make_document("a"))
        assert set(citation) == {
            "page_content",
            "type",
            "document_id",
            "document_name",
            "document_url",
            "page",
            "score",
        }

    def test_build_context_numbers_sources(self):
        context = build_context([make_document("a", content="alpha"), make_document("b", content="beta")])
        assert "[1]" in context and "[2]" in context
        assert "alpha" in context and "beta" in context


class TestRetrieveScoping:
    """Tenant isolation must be enforced in the query, not after it."""

    async def test_every_search_carries_owner_filter(self):
        captured: list[dict] = []

        async def fake_search(query, *, k, score_threshold, filter_kwargs=None):
            captured.append(filter_kwargs)
            return []

        with (
            patch("rag.retrieval.collection_available", return_value=True),
            patch("rag.retrieval.asearch", side_effect=fake_search),
        ):
            from rag.retrieval import retrieve

            await retrieve("question", owner_id=42)

        assert captured, "no searches were issued"
        assert all(f["owner_id"] == 42 for f in captured)
        # One search per content type.
        assert {f["type"] for f in captured} == {"TEXT", "TABLE", "SUMMARY", "IMAGE"}

    async def test_document_scope_is_applied(self):
        captured: list[dict] = []

        async def fake_search(query, *, k, score_threshold, filter_kwargs=None):
            captured.append(filter_kwargs)
            return []

        with (
            patch("rag.retrieval.collection_available", return_value=True),
            patch("rag.retrieval.asearch", side_effect=fake_search),
        ):
            from rag.retrieval import retrieve

            await retrieve("question", owner_id=7, document_id="doc-9")

        assert all(f["document_id"] == "doc-9" for f in captured)

    async def test_empty_collection_raises(self):
        with patch("rag.retrieval.collection_available", return_value=False):
            from rag.retrieval import NoOrEmptyCollectionError, retrieve

            with pytest.raises(NoOrEmptyCollectionError):
                await retrieve("question", owner_id=1)

    async def test_one_failing_quark_does_not_sink_the_query(self):
        async def flaky(query, *, k, score_threshold, filter_kwargs=None):
            if filter_kwargs["type"] == "TABLE":
                raise RuntimeError("index corrupt")
            return [make_document(f"{filter_kwargs['type']}-1")]

        async def noop_rerank(documents, query):
            # This test asserts quark resilience, not reranker behaviour.
            return documents

        with (
            patch("rag.retrieval.collection_available", return_value=True),
            patch("rag.retrieval.asearch", side_effect=flaky),
            patch("rag.retrieval._rerank", side_effect=noop_rerank),
        ):
            from rag.retrieval import retrieve

            result = await retrieve("question", owner_id=1)

        types = {d.metadata["id"] for d in result}
        assert "TEXT-1" in types
        assert "TABLE-1" not in types
