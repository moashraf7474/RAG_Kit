"""Embedding quality and configuration tests.

Verifies:
- The active embedder is the configured one (OpenRouter must NOT be used for embeddings).
- Embeddings have the expected dimension.
- Similar texts map to nearby vectors; unrelated texts stay apart.
- Embeddings are deterministic.
- Retrieval returns relevant chunks first for known-good queries.
"""

from __future__ import annotations

import hashlib
import math
import os

import pytest

from rag.conf import get_config
from rag.llm import get_embedder


def _load_env() -> None:
    """Load .env.backend if present so tests reflect real deployment config."""
    env_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", ".env.backend"
    )
    if os.path.exists(env_path):
        from dotenv import load_dotenv

        load_dotenv(env_path)
        get_config.cache_clear()


class TestEmbedderConfiguration:
    """OpenRouter must never be used for embeddings."""

    def setup_method(self) -> None:
        _load_env()

    def test_embedder_is_not_openrouter(self) -> None:
        settings = get_config().embedder
        assert settings.provider.lower() != "openrouter", (
            "Embeddings must not use OpenRouter; use local FastEmbed or a direct "
            "OpenAI-compatible endpoint."
        )

    def test_embedder_has_valid_dimensions(self) -> None:
        settings = get_config().embedder
        assert settings.dimensions in (384, 768, 1024, 1536, 3072), (
            f"Unusual embedding dimension: {settings.dimensions}"
        )

    def test_embedder_provider_local_uses_fastembed(self) -> None:
        settings = get_config().embedder
        if settings.provider.lower() == "local":
            embedder = get_embedder()
            assert type(embedder).__name__ == "FastEmbedEmbeddings", (
                "Local provider must use FastEmbedEmbeddings"
            )

    def test_local_embedder_has_no_remote_api_key(self) -> None:
        settings = get_config().embedder
        if settings.provider.lower() == "local":
            assert not settings.api_key.get_secret_value(), (
                "Local FastEmbed should not require an API key"
            )
            assert not settings.base_url, (
                "Local FastEmbed should not use a remote base_url"
            )


class TestEmbeddingQuality:
    """Semantic quality checks for the active embedding model."""

    def setup_method(self) -> None:
        _load_env()
        self.embedder = get_embedder()

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0

    def test_embedding_dimensions_match_config(self) -> None:
        vec = self.embedder.embed_query("test")
        assert len(vec) == get_config().embedder.dimensions

    def test_similar_texts_have_high_similarity(self) -> None:
        a = "machine learning and artificial intelligence"
        b = "AI and ML are transforming industries"
        sim = self._cosine_similarity(
            self.embedder.embed_query(a), self.embedder.embed_query(b)
        )
        assert sim > 0.6, f"Similar texts should be close in vector space, got {sim}"

    def test_unrelated_texts_have_lower_similarity(self) -> None:
        a = "machine learning and artificial intelligence"
        b = "the stock market fell today"
        sim = self._cosine_similarity(
            self.embedder.embed_query(a), self.embedder.embed_query(b)
        )
        assert sim < 0.8, f"Unrelated texts should be farther apart, got {sim}"

    def test_embeddings_are_deterministic(self) -> None:
        text = "deterministic embedding check"
        vec1 = self.embedder.embed_query(text)
        vec2 = self.embedder.embed_query(text)
        assert vec1 == vec2, "Same text must produce identical embeddings"

    def test_different_texts_produce_different_embeddings(self) -> None:
        vec1 = self.embedder.embed_query("hello world")
        vec2 = self.embedder.embed_query("goodbye world")
        assert vec1 != vec2, "Different texts must produce different embeddings"

    def test_embedding_is_not_all_zeros(self) -> None:
        vec = self.embedder.embed_query("non-zero embedding test")
        assert any(abs(v) > 1e-6 for v in vec), "Embedding should not be all zeros"

    def test_multilingual_content_embeds(self) -> None:
        texts = [
            "Hello world",
            "Bonjour le monde",
            "Hola mundo",
            "مرحبا بالعالم",
        ]
        for text in texts:
            vec = self.embedder.embed_query(text)
            assert len(vec) == get_config().embedder.dimensions
            assert any(abs(v) > 1e-6 for v in vec)


class TestEmbeddingRetrievalQuality:
    """End-to-end retrieval quality using the actual embedder."""

    def setup_method(self) -> None:
        _load_env()
        self.embedder = get_embedder()

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0

    def test_retrieval_ranks_relevant_chunk_first(self) -> None:
        """A query should match its source chunk higher than random noise."""
        query = "What is the capital of France?"
        relevant = "Paris is the capital and most populous city of France."
        noise = "The stock market closed higher today amid tech gains."

        q_vec = self.embedder.embed_query(query)
        rel_vec = self.embedder.embed_query(relevant)
        noise_vec = self.embedder.embed_query(noise)

        rel_score = self._cosine_similarity(q_vec, rel_vec)
        noise_score = self._cosine_similarity(q_vec, noise_vec)

        assert rel_score > noise_score, (
            f"Relevant chunk ({rel_score:.3f}) should score higher than noise "
            f"({noise_score:.3f})"
        )

    def test_book_content_embeds_coherently(self) -> None:
        """Simulated book paragraphs should cluster by topic."""
        tech_para = (
            "Python is a high-level programming language. "
            "It supports multiple paradigms including procedural, object-oriented, "
            "and functional programming."
        )
        finance_para = (
            "The Federal Reserve raised interest rates by 25 basis points, "
            "citing persistent inflation pressures in the economy."
        )

        vec_tech = self.embedder.embed_query(tech_para)
        vec_fin = self.embedder.embed_query(finance_para)

        sim = self._cosine_similarity(vec_tech, vec_fin)
        assert sim < 0.8, (
            f"Unrelated book paragraphs should not be too similar, got {sim:.3f}"
        )

    def test_book_query_matches_relevant_paragraph(self) -> None:
        """A query about programming should match a programming paragraph."""
        query = "What programming language is high-level and supports multiple paradigms?"
        paragraph = (
            "Python is a high-level programming language. "
            "It supports multiple paradigms including procedural, object-oriented, "
            "and functional programming."
        )
        noise = (
            "The weather in London is typically rainy and overcast "
            "during the winter months."
        )

        q_vec = self.embedder.embed_query(query)
        p_vec = self.embedder.embed_query(paragraph)
        n_vec = self.embedder.embed_query(noise)

        p_score = self._cosine_similarity(q_vec, p_vec)
        n_score = self._cosine_similarity(q_vec, n_vec)

        assert p_score > n_score, (
            f"Book query should match relevant paragraph ({p_score:.3f}) "
            f"higher than noise ({n_score:.3f})"
        )
        assert p_score > 0.5, (
            f"Relevant book paragraph should have strong similarity, got {p_score:.3f}"
        )


class TestEmbeddingIdempotency:
    """Re-ingesting the same document must produce identical embeddings."""

    def setup_method(self) -> None:
        _load_env()
        self.embedder = get_embedder()

    def test_same_text_same_hash(self) -> None:
        text = "Re-ingestion idempotency check " * 50
        vec1 = self.embedder.embed_query(text)
        vec2 = self.embedder.embed_query(text)
        assert hashlib.sha256(str(vec1).encode()).hexdigest() == hashlib.sha256(
            str(vec2).encode()
        ).hexdigest()
