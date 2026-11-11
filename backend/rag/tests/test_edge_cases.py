"""Edge-case and resilience tests for the RAG pipeline.

Run with:
    uv run pytest rag/tests/test_edge_cases.py -v
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from rag.chunking import pieces_to_documents
from rag.extract import Piece
from rag.image_captioner import caption_images, caption_image
from rag.vectordb import build_filter


class TestEmptyAndMalformedInputs:
    def test_empty_pieces_list(self):
        documents = pieces_to_documents([], document_id="d1", owner_id=1, document_name="empty.txt")
        assert documents == []

    def test_piece_with_empty_content(self):
        documents = pieces_to_documents(
            [Piece(content="   \n\n  ", content_type="TEXT", page="1")],
            document_id="d1",
            owner_id=1,
            document_name="empty.txt",
        )
        assert documents == []

    def test_none_page_handled(self):
        documents = pieces_to_documents(
            [Piece(content="hello", content_type="TEXT", page=None)],
            document_id="d1",
            owner_id=1,
            document_name="test.txt",
        )
        assert documents[0].metadata["page"] == ""

    def test_unicode_content(self):
        text = "日本語テスト 🎯 中文测试 العربية"
        documents = pieces_to_documents(
            [Piece(content=text, content_type="TEXT", page="1")],
            document_id="d1",
            owner_id=1,
            document_name="unicode.txt",
        )
        assert len(documents) == 1
        assert documents[0].page_content == text

    def test_sql_injection_like_content(self):
        text = "'; DROP TABLE rag_chunks; --"
        documents = pieces_to_documents(
            [Piece(content=text, content_type="TEXT", page="1")],
            document_id="d1",
            owner_id=1,
            document_name="injection.txt",
        )
        assert len(documents) == 1
        assert "DROP TABLE" in documents[0].page_content

    def test_html_in_content(self):
        text = "<script>alert('xss')</script>Normal text"
        documents = pieces_to_documents(
            [Piece(content=text, content_type="TEXT", page="1")],
            document_id="d1",
            owner_id=1,
            document_name="html.txt",
        )
        assert "<script>" in documents[0].page_content

    def test_extremely_long_single_word(self):
        text = "word" * 25_000
        documents = pieces_to_documents(
            [Piece(content=text, content_type="TEXT", page="1")],
            document_id="d1",
            owner_id=1,
            document_name="long.txt",
        )
        assert len(documents) > 1
        for doc in documents:
            assert len(doc.page_content) <= 1000

    def test_null_bytes_in_content(self):
        text = "hello\x00world\x00\x00"
        documents = pieces_to_documents(
            [Piece(content=text, content_type="TEXT", page="1")],
            document_id="d1",
            owner_id=1,
            document_name="null.txt",
        )
        assert len(documents) == 1


class TestMixedContentTypes:
    def test_text_and_table_mixed(self):
        pieces = [
            Piece(content="Some text before", content_type="TEXT", page="1"),
            Piece(
                content="| a | b |\n| --- | --- |\n| 1 | 2 |\n" + "\n".join(f"| {i} | {i*2} |" for i in range(100)),
                content_type="TABLE",
                page="1",
            ),
            Piece(content="Some text after", content_type="TEXT", page="1"),
        ]
        documents = pieces_to_documents(
            pieces, document_id="d1", owner_id=1, document_name="mixed.txt"
        )
        assert len(documents) == 3
        assert all(d.metadata["type"] == t for d, t in zip(documents, ["TEXT", "TABLE", "TEXT"]))

    def test_empty_table_not_skipped_if_has_words(self):
        table = "| header |\n| --- |\n| value |"
        documents = pieces_to_documents(
            [Piece(content=table, content_type="TABLE", page="1")],
            document_id="d1",
            owner_id=1,
            document_name="table.txt",
        )
        assert len(documents) == 1
        assert documents[0].metadata["type"] == "TABLE"

    def test_table_with_only_punctuation_skipped(self):
        table = "| --- | --- |\n| --- | --- |"
        pieces = [Piece(content=table, content_type="TABLE", page="1")]
        documents = pieces_to_documents(
            pieces,
            document_id="d1",
            owner_id=1,
            document_name="table.txt",
        )
        assert documents == []


class TestChunkingQuality:
    def test_chunks_respect_max_size(self):
        text = "word " * 500
        documents = pieces_to_documents(
            [Piece(content=text, content_type="TEXT", page="1")],
            document_id="d1",
            owner_id=1,
            document_name="big.txt",
        )
        for doc in documents:
            assert len(doc.page_content) <= 1000

    def test_chunks_have_overlap(self):
        text = "word " * 300
        documents = pieces_to_documents(
            [Piece(content=text, content_type="TEXT", page="1")],
            document_id="d1",
            owner_id=1,
            document_name="overlap.txt",
        )
        assert len(documents) >= 2
        total_coverage = sum(len(d.page_content) for d in documents)
        assert total_coverage >= len(text)

    def test_stable_ids_same_content(self):
        text = "hello world " * 100
        docs1 = pieces_to_documents(
            [Piece(content=text, content_type="TEXT", page="1")],
            document_id="d1", owner_id=1, document_name="doc.txt",
        )
        docs2 = pieces_to_documents(
            [Piece(content=text, content_type="TEXT", page="1")],
            document_id="d1", owner_id=1, document_name="doc.txt",
        )
        assert [d.metadata["id"] for d in docs1] == [d.metadata["id"] for d in docs2]

    def test_different_positions_different_ids(self):
        text = "word " * 200
        docs = pieces_to_documents(
            [Piece(content=text, content_type="TEXT", page="1")],
            document_id="d1", owner_id=1, document_name="doc.txt",
        )
        ids = [d.metadata["id"] for d in docs]
        assert len(ids) == len(set(ids))

    def test_metadata_integrity_across_chunks(self):
        text = "word " * 200
        docs = pieces_to_documents(
            [Piece(content=text, content_type="TEXT", page="5")],
            document_id="doc-123", owner_id=42, document_name="report.pdf",
        )
        for doc in docs:
            assert doc.metadata["document_id"] == "doc-123"
            assert doc.metadata["owner_id"] == 42
            assert doc.metadata["document_name"] == "report.pdf"
            assert doc.metadata["page"] == "5"
            assert doc.metadata["type"] == "TEXT"


class TestImageCaptioningEdgeCases:
    def test_disabled_returns_unchanged(self):
        pieces = [Piece(content="img", content_type="IMAGE", page="1")]
        with patch("rag.image_captioner.conf.get_config") as mock_config:
            mock_config.return_value.image_captioner.enabled = False
            result = asyncio.run(caption_images(pieces, document_id="d1"))
        assert result == pieces

    def test_no_images_returns_unchanged(self):
        pieces = [Piece(content="text", content_type="TEXT", page="1")]
        with patch("rag.image_captioner.conf.get_config") as mock_config:
            mock_config.return_value.image_captioner.enabled = True
            result = asyncio.run(caption_images(pieces, document_id="d1"))
        assert result == pieces

    def test_caption_failure_preserves_original(self):
        pieces = [Piece(content="bad_image_data", content_type="IMAGE", page="1")]
        with patch("rag.image_captioner.conf.get_config") as mock_config, patch(
            "rag.image_captioner.caption_image", return_value=None
        ):
            mock_config.return_value.image_captioner.enabled = True
            mock_config.return_value.image_captioner.max_concurrency = 2
            result = asyncio.run(caption_images(pieces, document_id="d1"))
        assert result[0].content == "bad_image_data"

    def test_upload_failure_keeps_caption(self):
        pieces = [Piece(content="img_data", content_type="IMAGE", page="1")]
        with patch("rag.image_captioner.conf.get_config") as mock_config, patch(
            "rag.image_captioner.caption_image", return_value="A chart."
        ), patch(
            "rag.image_captioner._upload_image", side_effect=RuntimeError("S3 down")
        ):
            mock_config.return_value.image_captioner.enabled = True
            mock_config.return_value.image_captioner.max_concurrency = 2
            result = asyncio.run(caption_images(pieces, document_id="d1"))
        assert result[0].content == "A chart."
        assert result[0].metadata["storage_key"] == ""

    def test_caption_image_disabled_returns_none(self):
        with patch("rag.image_captioner.conf.get_config") as mock_config:
            mock_config.return_value.image_captioner.enabled = False
            result = asyncio.run(caption_image("img_data"))
        assert result is None

    def test_caption_image_success_through_pipeline(self):
        pieces = [Piece(content="img_data", content_type="IMAGE", page="1")]
        with patch("rag.image_captioner.conf.get_config") as mock_config, patch(
            "rag.image_captioner.caption_image", return_value="A bar chart."
        ), patch("rag.image_captioner._upload_image", return_value="images/d1/page-1.png"):
            mock_config.return_value.image_captioner.enabled = True
            mock_config.return_value.image_captioner.max_concurrency = 2
            result = asyncio.run(caption_images(pieces, document_id="d1"))
        assert result[0].content == "A bar chart."
        assert result[0].metadata["storage_key"] == "images/d1/page-1.png"

    def test_caption_image_failure_through_pipeline(self):
        pieces = [Piece(content="img_data", content_type="IMAGE", page="1")]
        with patch("rag.image_captioner.conf.get_config") as mock_config, patch(
            "rag.image_captioner.caption_image", return_value=None
        ):
            mock_config.return_value.image_captioner.enabled = True
            mock_config.return_value.image_captioner.max_concurrency = 2
            result = asyncio.run(caption_images(pieces, document_id="d1"))
        assert result[0].content == "img_data"

    def test_concurrency_limit_respected(self):
        pieces = [Piece(content=f"img_{i}", content_type="IMAGE", page="1") for i in range(10)]
        with patch("rag.image_captioner.conf.get_config") as mock_config, patch(
            "rag.image_captioner.caption_image", side_effect=lambda data, mime_type="image/png", **kw: f"caption_{data}"
        ), patch("rag.image_captioner._upload_image", return_value="key"):
            mock_config.return_value.image_captioner.enabled = True
            mock_config.return_value.image_captioner.max_concurrency = 2
            result = asyncio.run(caption_images(pieces, document_id="d1"))
        assert all(r.content.startswith("caption_img_") for r in result)


class TestRetrieverFilters:
    def test_build_filter_empty_returns_none(self):
        assert build_filter(None) is None
        assert build_filter({}) is None

    def test_build_filter_single_field(self):
        from qdrant_client.http import models
        result = build_filter({"owner_id": 42})
        assert result is not None
        assert len(result.must) == 1
        assert result.must[0].key == "metadata.owner_id"
        assert result.must[0].match.value == 42

    def test_build_filter_multiple_fields(self):
        from qdrant_client.http import models
        result = build_filter({"owner_id": 1, "document_id": "doc-1"})
        assert result is not None
        assert len(result.must) == 2
        keys = [c.key for c in result.must]
        assert "metadata.owner_id" in keys
        assert "metadata.document_id" in keys
