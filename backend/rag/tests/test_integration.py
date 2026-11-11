"""End-to-end pipeline test against real Qdrant and MinIO.

Skipped automatically when the services are not reachable, so the unit suite
still runs standalone. Embeddings are deterministic fakes: this proves the
wiring (extract -> chunk -> embed -> upsert -> filtered retrieval), not the
quality of any particular model, and needs no API key.

Run with the compose stack up:
    docker compose up -d db redis qdrant minio
    DATABASE_HOST=localhost DATABASE_PORT=5433 \
    VECTOR_DB_URL=http://localhost:6333 S3_ENDPOINT=http://localhost:9000 \
    uv run pytest rag/tests/test_integration.py -v
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from unittest.mock import patch

import pytest


def _qdrant_reachable() -> bool:
    try:
        import requests

        url = os.environ.get("VECTOR_DB_URL", "http://localhost:6333")
        return requests.get(f"{url}/readyz", timeout=2).ok
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _qdrant_reachable(), reason="Qdrant is not reachable; start the compose stack."
)


from langchain_core.embeddings import Embeddings  # noqa: E402
from qdrant_client.http import models  # noqa: E402


class FakeEmbeddings(Embeddings):
    """Deterministic embeddings: same text always maps to the same vector.

    Must subclass `Embeddings` — langchain-qdrant type-checks this at
    collection-creation time. Dimension matches the configured embedder (the
    real one is bge-small at 384): the collection is sized from config, so a
    fake with a different width fails Qdrant's size validation.
    """

    dimensions = 384

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        # Tile the digest out to the configured dimension.
        raw = (digest * (self.dimensions // len(digest) + 1))[: self.dimensions]
        values = [(byte - 128) / 128.0 for byte in raw]
        norm = sum(value * value for value in values) ** 0.5 or 1.0
        return [value / norm for value in values]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)


@pytest.fixture
def isolated_collection(monkeypatch):
    """Give each test its own collection so runs cannot interfere."""
    name = f"test_{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("VECTOR_DB_COLLECTION_NAME", name)
    monkeypatch.setenv("VECTOR_DB_RETRIEVAL_MODE", "DENSE")
    monkeypatch.setenv("RERANKER_ENABLED", "False")
    monkeypatch.setenv("SPARSE_EMBEDDER_ENABLED", "False")
    monkeypatch.setenv("EMBEDDER_DIMENSIONS", "384")

    from rag import conf, llm, vectordb

    for cached in (
        conf.get_config,
        llm.get_embedder,
        llm.get_sparse_embedder,
        vectordb.get_client,
        vectordb.get_vectorstore,
    ):
        cached.cache_clear()

    with patch.object(llm, "get_embedder", return_value=FakeEmbeddings()), patch(
        "rag.vectordb.get_embedder", return_value=FakeEmbeddings()
    ):
        yield name

    try:
        vectordb.get_client().delete_collection(name)
    except Exception:
        pass
    for cached in (
        conf.get_config,
        llm.get_embedder,
        llm.get_sparse_embedder,
        vectordb.get_client,
        vectordb.get_vectorstore,
    ):
        cached.cache_clear()


class TestVectorRoundTrip:
    def test_upload_then_retrieve_with_owner_isolation(self, isolated_collection):
        """The core guarantee: one user's query can never reach another's chunks."""
        import asyncio

        from langchain_core.documents import Document

        from rag import vectordb
        from rag.retrieval import retrieve

        def make(text: str, owner: int, doc: str) -> Document:
            return Document(
                page_content=text,
                metadata={
                    "id": hashlib.sha256(text.encode()).hexdigest(),
                    "document_id": doc,
                    "owner_id": owner,
                    "document_name": f"{doc}.txt",
                    "document_url": "",
                    "type": "TEXT",
                    "page": "1",
                    "related": [],
                },
            )

        alice_text = "The deployment runbook requires rotating the signing key quarterly."
        bob_text = "Bob's private notes about the acquisition timeline."

        vectordb.upload([make(alice_text, 1, "alice-doc"), make(bob_text, 2, "bob-doc")])

        # Alice searches for Bob's exact wording and must still get nothing of his.
        results = asyncio.run(retrieve(bob_text, owner_id=1))
        assert all(d.metadata["owner_id"] == 1 for d in results), (
            "cross-tenant leak: another owner's chunk was returned"
        )

        # Alice can find her own content.
        own = asyncio.run(retrieve(alice_text, owner_id=1))
        assert any(d.metadata["document_id"] == "alice-doc" for d in own)

    def test_delete_removes_only_that_document(self, isolated_collection):
        import asyncio

        from langchain_core.documents import Document

        from rag import vectordb
        from rag.retrieval import retrieve

        def make(text: str, doc: str) -> Document:
            return Document(
                page_content=text,
                metadata={
                    "id": hashlib.sha256(text.encode()).hexdigest(),
                    "document_id": doc,
                    "owner_id": 1,
                    "document_name": doc,
                    "document_url": "",
                    "type": "TEXT",
                    "page": "1",
                    "related": [],
                },
            )

        vectordb.upload([make("keep this content around", "keep"), make("purge this one", "purge")])
        vectordb.delete_by(document_id="purge")

        remaining = asyncio.run(retrieve("content", owner_id=1))
        assert all(d.metadata["document_id"] != "purge" for d in remaining)

    def test_reupload_is_idempotent(self, isolated_collection):
        """Re-ingesting identical content must not duplicate points."""
        from langchain_core.documents import Document

        from rag import vectordb

        text = "Idempotency check: the same chunk uploaded twice."
        document = Document(
            page_content=text,
            metadata={
                "id": hashlib.sha256(text.encode()).hexdigest(),
                "document_id": "doc",
                "owner_id": 1,
                "document_name": "doc",
                "document_url": "",
                "type": "TEXT",
                "page": "1",
                "related": [],
            },
        )
        vectordb.upload([document])
        vectordb.upload([document])

        points, _ = vectordb.get_client().scroll(
            collection_name=isolated_collection,
            scroll_filter=models.Filter(
                must_not=[
                    models.FieldCondition(
                        key="metadata.id",
                        match=models.MatchValue(value="__rag_system_embedder__"),
                    )
                ]
            ),
            limit=10,
            with_payload=False,
            with_vectors=False,
        )
        assert len(points) == 1, f"expected 1 user point after re-upload, found {len(points)}"


class TestExtraction:
    def test_csv_becomes_a_markdown_table(self, tmp_path):
        from rag.extract import extract_file

        path = tmp_path / "data.csv"
        path.write_text("name,role\nAda,engineer\nGrace,admiral\n")

        pieces, _diagnostics = extract_file(path, "data.csv")
        assert pieces
        assert pieces[0].content_type == "TABLE"
        assert "| name | role |" in pieces[0].content
        assert "Ada" in pieces[0].content

    def test_plain_text_extraction(self, tmp_path):
        from rag.extract import extract_file

        path = tmp_path / "notes.txt"
        path.write_text("Retrieval augmented generation grounds answers in sources.")

        pieces, _diagnostics = extract_file(path, "notes.txt")
        assert len(pieces) == 1
        assert "Retrieval augmented" in pieces[0].content

    def test_chunking_assigns_stable_ids_and_metadata(self):
        from rag.chunking import pieces_to_documents
        from rag.extract import Piece

        pieces = [Piece(content="alpha beta gamma " * 200, content_type="TEXT", page="3")]
        documents = pieces_to_documents(
            pieces,
            document_id="doc-1",
            owner_id=7,
            document_name="big.txt",
            document_url="https://example.com/big.txt",
        )

        assert len(documents) > 1, "long text should split into several chunks"
        assert all(d.metadata["owner_id"] == 7 for d in documents)
        assert all(d.metadata["document_id"] == "doc-1" for d in documents)
        # Ids are content hashes, so they are stable and unique per chunk.
        ids = [d.metadata["id"] for d in documents]
        assert len(ids) == len(set(ids))

    def test_tables_are_never_split(self):
        from rag.chunking import pieces_to_documents
        from rag.extract import Piece

        table = "| a | b |\n| --- | --- |\n" + "\n".join(f"| {i} | {i * 2} |" for i in range(400))
        documents = pieces_to_documents(
            [Piece(content=table, content_type="TABLE")],
            document_id="d",
            owner_id=1,
            document_name="t.csv",
            document_url="",
        )
        assert len(documents) == 1, "a table must stay intact even when long"

    def test_markdown_headers_split_into_sections(self):
        from rag.chunking import pieces_to_documents
        from rag.extract import Piece

        content = "\n".join([
            "# Anas Ahmad",
            "Software Engineer.",
            "",
            "## Technical Skills",
            "Python, JavaScript.",
            "",
            "## Experience",
            "Worked at Company A.",
            "",
            "### Credminds",
            "Built things.",
            "",
            "## Projects",
            "AI coding agent.",
        ])
        documents = pieces_to_documents(
            [Piece(content=content, content_type="TEXT", page="1")],
            document_id="doc-1",
            owner_id=1,
            document_name="resume.md",
        )

        paths = [d.metadata.get("section_path", "") for d in documents]
        assert "Anas Ahmad" in paths
        assert "Anas Ahmad > Technical Skills" in paths
        assert "Anas Ahmad > Experience" in paths
        assert "Anas Ahmad > Experience > Credminds" in paths
        assert "Anas Ahmad > Projects" in paths

    def test_long_markdown_section_is_recursively_split(self):
        from rag.chunking import pieces_to_documents
        from rag.extract import Piece

        long_line = "word " * 200
        content = "\n".join([
            "# Projects",
            long_line,
            long_line,
        ])
        documents = pieces_to_documents(
            [Piece(content=content, content_type="TEXT", page="1")],
            document_id="doc-1",
            owner_id=1,
            document_name="resume.md",
        )

        assert len(documents) > 1, "oversized markdown section should be split"
        for doc in documents:
            assert doc.metadata.get("section_path") == "Projects"

    def test_plain_text_falls_back_to_recursive_splitter(self):
        from rag.chunking import pieces_to_documents
        from rag.extract import Piece

        content = "alpha beta gamma " * 200
        documents = pieces_to_documents(
            [Piece(content=content, content_type="TEXT", page="1")],
            document_id="doc-1",
            owner_id=1,
            document_name="notes.txt",
        )

        assert len(documents) > 1, "plain text should still split"
        assert all(d.metadata.get("section_path", "") == "" for d in documents)

    def test_section_path_is_preserved_across_positions(self):
        from rag.chunking import pieces_to_documents
        from rag.extract import Piece

        long_line = "word " * 200
        content = "\n".join([
            "# Experience",
            long_line,
            long_line,
        ])
        documents = pieces_to_documents(
            [Piece(content=content, content_type="TEXT", page="1")],
            document_id="doc-1",
            owner_id=1,
            document_name="resume.md",
        )

        assert len(documents) > 1
        for doc in documents:
            assert doc.metadata["section_path"] == "Experience"

    def test_image_captioning_disabled_preserves_original_piece(self):
        from rag.extract import Piece
        from rag.image_captioner import caption_images

        pieces = [
            Piece(
                content="ZmFrZSBpbWFnZSBkYXRh",
                content_type="IMAGE",
                page="1",
                metadata={"mime_type": "image/png"},
            )
        ]
        with patch("rag.image_captioner.conf.get_config") as mock_config, patch(
            "rag.image_captioner.caption_image", side_effect=AssertionError("captioning disabled")
        ) as mock_caption:
            mock_config.return_value.image_captioner.enabled = False
            result = asyncio.run(caption_images(pieces, document_id="doc-1"))

        assert result[0].content == "ZmFrZSBpbWFnZSBkYXRh"
        assert result[0].content_type == "IMAGE"
        mock_caption.assert_not_called()

    def test_image_captioning_replaces_content_with_caption(self):
        from rag.extract import Piece
        from rag.image_captioner import caption_images

        pieces = [
            Piece(
                content="ZmFrZSBpbWFnZSBkYXRh",
                content_type="IMAGE",
                page="1",
                metadata={"mime_type": "image/png"},
            )
        ]
        with patch("rag.image_captioner.conf.get_config") as mock_config, patch(
            "rag.image_captioner.caption_image", return_value="A red bar chart showing Q3 revenue."
        ) as mock_caption, patch(
            "rag.image_captioner._upload_image", return_value="images/doc-1/page-1.png"
        ) as mock_upload:
            mock_config.return_value.image_captioner.enabled = True
            mock_config.return_value.image_captioner.max_concurrency = 2
            result = asyncio.run(caption_images(pieces, document_id="doc-1"))

        assert result[0].content == "A red bar chart showing Q3 revenue."
        assert result[0].content_type == "IMAGE"
        assert result[0].metadata["storage_key"] == "images/doc-1/page-1.png"
        assert result[0].metadata["base64_image"] == "ZmFrZSBpbWFnZSBkYXRh"
        mock_caption.assert_called_once_with("ZmFrZSBpbWFnZSBkYXRh", "image/png")

    def test_image_captioning_failure_keeps_original_piece(self):
        from rag.extract import Piece
        from rag.image_captioner import caption_images

        pieces = [
            Piece(
                content="ZmFrZSBpbWFnZSBkYXRh",
                content_type="IMAGE",
                page="1",
                metadata={"mime_type": "image/png"},
            )
        ]
        with patch("rag.image_captioner.conf.get_config") as mock_config, patch(
            "rag.image_captioner.caption_image", return_value=None
        ) as mock_caption:
            mock_config.return_value.image_captioner.enabled = True
            mock_config.return_value.image_captioner.max_concurrency = 2
            result = asyncio.run(caption_images(pieces, document_id="doc-1"))

        assert result[0].content == "ZmFrZSBpbWFnZSBkYXRh"
        assert result[0].content_type == "IMAGE"
        mock_caption.assert_called_once()
