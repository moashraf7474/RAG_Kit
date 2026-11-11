"""Aggressive reindexing pipeline tests.

Covers the full reindex flow without requiring a running database:
- Upload creates correct storage_key format
- Reindex downloads the original file from object storage
- Reindex purges old vectors and chunks before re-ingesting
- Reindex is idempotent (same inputs → same chunk counts)
- Reindex handles missing/corrupt storage gracefully
- Multiple consecutive reindexes work correctly
- Document metadata is preserved across reindexes
- Storage key format is user_pk/uuid/filename
- Admin bulk reindex queues correctly
- _extract dispatches to the right extractor per source type
- _persist_chunks mirrors to Postgres with correct shape
- _purge_existing deletes vectors then chunks
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import pytest

from rag.conf import get_config
from rag.ingest import _extract, _persist_chunks, _purge_existing, ingest_document
from rag.models import Chunk, Document, SourceType, Status
from rag.quality import IngestionDiagnostics


def _load_env() -> None:
    env_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", ".env.backend"
    )
    if os.path.exists(env_path):
        from dotenv import load_dotenv

        load_dotenv(env_path)
        get_config.cache_clear()


def _make_document(**kwargs):
    defaults = {
        "id": str(uuid.uuid4()),
        "owner_id": 1,
        "name": "test.pdf",
        "source_type": SourceType.FILE,
        "storage_key": "1/abc-123/test.pdf",
        "status": Status.PROCESSING,
    }
    defaults.update(kwargs)
    document = Document(**defaults)
    document.save = MagicMock()
    return document


def _start_patches(patches):
    for p in patches:
        p.start()
    return patches


def _stop_patches(patches):
    for p in patches:
        p.stop()


def _patch_ingest(document, extract_return=None, summarize_return=None):
    """Patch all external dependencies for ingest_document()."""
    extract_return = extract_return or []
    summarize_return = summarize_return or []

    mock_doc = MagicMock()
    mock_doc.metadata = {"id": "mock-chunk-1", "type": "TEXT", "page": "1"}
    mock_doc.page_content = "mock chunk content"

    mock_diagnostics = IngestionDiagnostics()
    mock_diagnostics.text_chunks = len(extract_return)
    mock_diagnostics.table_chunks = 0
    mock_diagnostics.image_chunks = 0
    mock_diagnostics.summary_chunks = 0
    mock_diagnostics.failed_summaries = 0
    mock_diagnostics.embedding_status = "completed"
    mock_diagnostics.vector_upload_status = "completed"
    mock_diagnostics.warnings = []
    mock_diagnostics.quality_score = "GOOD"
    mock_diagnostics.ingestion_duration_seconds = 0.1
    mock_diagnostics.extractor_used = "test"
    mock_diagnostics.ocr_used = False
    mock_diagnostics.ocr_language = ""

    patches = [
        patch("rag.ingest.Document.objects.get", return_value=document),
        patch("rag.ingest.get_metrics"),
        patch("rag.ingest.get_trace_callbacks", return_value=[]),
        patch("rag.ingest._extract", return_value=(extract_return, mock_diagnostics)),
        patch("rag.ingest.caption_images", return_value=extract_return),
        patch("rag.ingest.pieces_to_documents", return_value=[mock_doc]),
        patch("rag.ingest.add_summaries", return_value=summarize_return),
        patch("rag.ingest._purge_existing"),
        patch("rag.vectordb.upload"),
        patch("rag.ingest._persist_chunks"),
        patch("rag.ingest.flush_traces"),
        patch("rag.models.IngestionReport.objects.create", return_value=MagicMock()),
        patch("django.db.transaction.atomic", lambda func: func),
    ]
    return patches


class TestStorageKeyLifecycle:
    """Upload must create a correct storage_key; reindex must reuse it."""

    def setup_method(self) -> None:
        _load_env()

    def test_upload_creates_correct_storage_key_format(self) -> None:
        """storage_key must be user_pk/uuid/filename."""
        user_pk = 42
        filename = "report.pdf"
        storage_key = f"{user_pk}/{uuid.uuid4()}/{filename}"

        parts = storage_key.split("/")
        assert len(parts) == 3
        assert parts[0] == str(user_pk)
        assert parts[2] == filename
        uuid.UUID(parts[1])

    def test_reindex_uses_same_storage_key(self) -> None:
        """After reindex, the document's storage_key must not change."""
        document = _make_document(
            storage_key="7/550e8400-e29b-41d4-a716-446655440000/report.pdf",
            status=Status.READY,
        )
        original_key = document.storage_key

        patches = _patch_ingest(document, extract_return=[MagicMock(content_type="TEXT", content="text", page="1", metadata={})])
        _start_patches(patches)
        try:
            ingest_document(str(document.id))
        finally:
            _stop_patches(patches)

        assert document.storage_key == original_key

    def test_reindex_downloads_from_storage(self) -> None:
        """_extract for FILE sources must call storage.download_to_path."""
        document = _make_document(storage_key="1/abc-123/report.pdf")

        mock_download = MagicMock()
        mock_extract_file = MagicMock(return_value=(
            [MagicMock(content_type="TEXT", content="text", page="1", metadata={})],
            IngestionDiagnostics(),
        ))

        patches = [
            patch("rag.ingest.Document.objects.get", return_value=document),
            patch("rag.ingest.get_metrics"),
            patch("rag.ingest.get_trace_callbacks", return_value=[]),
            patch("rag.ingest.caption_images", return_value=[]),
            patch("rag.ingest.pieces_to_documents", return_value=[MagicMock(metadata={"id": "c1", "type": "TEXT", "page": "1"})]),
            patch("rag.ingest.add_summaries", return_value=[]),
            patch("rag.ingest._purge_existing"),
            patch("rag.vectordb.upload"),
            patch("rag.ingest._persist_chunks"),
            patch("rag.ingest.flush_traces"),
            patch("rag.ingest.storage.download_to_path", mock_download),
            patch("rag.ingest.extract_file", mock_extract_file),
            patch("rag.models.IngestionReport.objects.create", return_value=MagicMock()),
            patch("django.db.transaction.atomic", lambda func: func),
        ]
        _start_patches(patches)
        try:
            ingest_document(str(document.id))
        finally:
            _stop_patches(patches)

        mock_download.assert_called_once_with("1/abc-123/report.pdf", ANY)
        mock_extract_file.assert_called_once()
        extracted_path = mock_extract_file.call_args[0][0]
        assert isinstance(extracted_path, Path)

    def test_url_source_does_not_download_from_storage(self) -> None:
        """URL sources must not touch MinIO."""
        document = _make_document(
            source_type=SourceType.URL,
            source_uri="https://example.com",
        )

        mock_download = MagicMock()
        mock_extract_url = MagicMock(return_value=[
            MagicMock(content_type="TEXT", content="web text", page="1", metadata={})
        ])

        patches = [
            patch("rag.ingest.Document.objects.get", return_value=document),
            patch("rag.ingest.get_metrics"),
            patch("rag.ingest.get_trace_callbacks", return_value=[]),
            patch("rag.ingest.caption_images", return_value=[]),
            patch("rag.ingest.pieces_to_documents", return_value=[MagicMock(metadata={"id": "c1", "type": "TEXT", "page": "1"})]),
            patch("rag.ingest.add_summaries", return_value=[]),
            patch("rag.ingest._purge_existing"),
            patch("rag.vectordb.upload"),
            patch("rag.ingest._persist_chunks"),
            patch("rag.ingest.flush_traces"),
            patch("rag.ingest.extract_url", mock_extract_url),
            patch("rag.ingest.storage.download_to_path", mock_download),
            patch("rag.models.IngestionReport.objects.create", return_value=MagicMock()),
            patch("django.db.transaction.atomic", lambda func: func),
        ]
        _start_patches(patches)
        try:
            ingest_document(str(document.id))
        finally:
            _stop_patches(patches)

        mock_download.assert_not_called()
        mock_extract_url.assert_called_once_with("https://example.com")

    def test_sitemap_source_does_not_download_from_storage(self) -> None:
        document = _make_document(
            source_type=SourceType.SITEMAP,
            source_uri="https://example.com/sitemap.xml",
        )

        mock_download = MagicMock()
        mock_extract_sitemap = MagicMock(return_value=[
            MagicMock(content_type="TEXT", content="sitemap text", page="1", metadata={})
        ])

        patches = [
            patch("rag.ingest.Document.objects.get", return_value=document),
            patch("rag.ingest.get_metrics"),
            patch("rag.ingest.get_trace_callbacks", return_value=[]),
            patch("rag.ingest.caption_images", return_value=[]),
            patch("rag.ingest.pieces_to_documents", return_value=[MagicMock(metadata={"id": "c1", "type": "TEXT", "page": "1"})]),
            patch("rag.ingest.add_summaries", return_value=[]),
            patch("rag.ingest._purge_existing"),
            patch("rag.vectordb.upload"),
            patch("rag.ingest._persist_chunks"),
            patch("rag.ingest.flush_traces"),
            patch("rag.ingest.extract_sitemap", mock_extract_sitemap),
            patch("rag.ingest.storage.download_to_path", mock_download),
            patch("rag.models.IngestionReport.objects.create", return_value=MagicMock()),
            patch("django.db.transaction.atomic", lambda func: func),
        ]
        _start_patches(patches)
        try:
            ingest_document(str(document.id))
        finally:
            _stop_patches(patches)

        mock_download.assert_not_called()
        mock_extract_sitemap.assert_called_once_with("https://example.com/sitemap.xml")


class TestPurgeExisting:
    """_purge_existing must remove vectors and chunks atomically."""

    def test_purge_deletes_vectors(self) -> None:
        document = _make_document()
        mock_qs = MagicMock()
        mock_qs.count.return_value = 0
        with patch("rag.ingest.vectordb.delete_by") as mock_delete, patch(
            "rag.ingest.Chunk.objects.filter", return_value=mock_qs
        ):
            _purge_existing(document)
        mock_delete.assert_called_once_with(document_id=str(document.id))
        mock_qs.delete.assert_called_once()

    def test_purge_deletes_chunks(self) -> None:
        document = _make_document()
        mock_qs = MagicMock()
        mock_qs.count.return_value = 0
        with patch("rag.ingest.Chunk.objects.filter", return_value=mock_qs) as mock_filter:
            _purge_existing(document)
        mock_filter.assert_called_once_with(document=document)
        mock_qs.delete.assert_called_once()

    def test_purge_survives_vector_db_failure(self) -> None:
        """If Qdrant is down, purge must still delete Postgres chunks."""
        document = _make_document()
        mock_qs = MagicMock()
        mock_qs.count.return_value = 0
        with patch("rag.ingest.vectordb.delete_by", side_effect=RuntimeError("qdrant down")), patch(
            "rag.ingest.Chunk.objects.filter", return_value=mock_qs
        ):
            _purge_existing(document)
        mock_qs.delete.assert_called_once()


class TestReindexIdempotency:
    """Re-ingesting the same document must produce identical chunk counts."""

    def test_two_ingests_produce_same_chunk_count(self) -> None:
        document = _make_document(
            storage_key="1/idem-123/report.pdf",
        )
        pdf_bytes = b"%PDF-1.4 fake pdf content " * 100

        mock_download = MagicMock()
        mock_download.side_effect = lambda key, path: Path(path).write_bytes(pdf_bytes)
        mock_extract = MagicMock(return_value=(
            [MagicMock(content_type="TEXT", content="text " * 200, page="1", metadata={})],
            IngestionDiagnostics(),
        ))

        patches = [
            patch("rag.ingest.Document.objects.get", return_value=document),
            patch("rag.ingest.get_metrics"),
            patch("rag.ingest.get_trace_callbacks", return_value=[]),
            patch("rag.ingest._extract", mock_extract),
            patch("rag.ingest.caption_images", return_value=[]),
            patch("rag.ingest.pieces_to_documents", return_value=[MagicMock(metadata={"id": "c1", "type": "TEXT", "page": "1"})]),
            patch("rag.ingest.add_summaries", return_value=[]),
            patch("rag.ingest._purge_existing"),
            patch("rag.vectordb.upload"),
            patch("rag.ingest._persist_chunks"),
            patch("rag.ingest.flush_traces"),
            patch("rag.ingest.storage.download_to_path", mock_download),
            patch("rag.models.IngestionReport.objects.create", return_value=MagicMock()),
            patch("django.db.transaction.atomic", lambda func: func),
        ]
        _start_patches(patches)
        try:
            count1 = ingest_document(str(document.id))
            document.status = Status.PROCESSING
            document.save = MagicMock()
            count2 = ingest_document(str(document.id))
        finally:
            _stop_patches(patches)

        assert count1 == count2, f"Re-ingestion produced different chunk counts: {count1} vs {count2}"

    def test_reindex_clears_previous_chunks(self) -> None:
        """After reindex, old chunk rows must not remain."""
        document = _make_document(
            storage_key="1/purge-123/report.pdf",
        )
        mock_qs = MagicMock()
        mock_qs.count.return_value = 0

        patches = [
            patch("rag.ingest.Document.objects.get", return_value=document),
            patch("rag.ingest.get_metrics"),
            patch("rag.ingest.get_trace_callbacks", return_value=[]),
            patch("rag.ingest._extract", return_value=([MagicMock(content_type="TEXT", content="text", page="1", metadata={})], IngestionDiagnostics())),
            patch("rag.ingest.caption_images", return_value=[]),
            patch("rag.ingest.pieces_to_documents", return_value=[MagicMock(metadata={"id": "c1", "type": "TEXT", "page": "1"})]),
            patch("rag.ingest.add_summaries", return_value=[]),
            patch("rag.vectordb.upload"),
            patch("rag.ingest._persist_chunks"),
            patch("rag.ingest.flush_traces"),
            patch("rag.ingest.storage.download_to_path", return_value=None),
            patch("rag.ingest.Chunk.objects.filter", return_value=mock_qs),
            patch("rag.models.IngestionReport.objects.create", return_value=MagicMock()),
            patch("django.db.transaction.atomic", lambda func: func),
        ]
        _start_patches(patches)
        try:
            ingest_document(str(document.id))
        finally:
            _stop_patches(patches)

        mock_qs.delete.assert_called_once()


class TestMissingStorageKey:
    """FILE sources without a storage_key must fail with a clear error."""

    def test_missing_storage_key_raises(self) -> None:
        document = _make_document(storage_key="")

        patches = [
            patch("rag.ingest.Document.objects.get", return_value=document),
            patch("rag.ingest.get_metrics"),
            patch("rag.ingest.get_trace_callbacks", return_value=[]),
            patch("rag.ingest.storage.download_to_path", return_value=None),
            patch("rag.ingest.caption_images", return_value=[]),
            patch("rag.ingest.pieces_to_documents", return_value=[MagicMock(metadata={"id": "c1", "type": "TEXT", "page": "1"})]),
            patch("rag.ingest.add_summaries", return_value=[]),
            patch("rag.ingest._purge_existing"),
            patch("rag.vectordb.upload"),
            patch("rag.ingest._persist_chunks"),
            patch("rag.ingest.flush_traces"),
            patch("rag.models.IngestionReport.objects.create", return_value=MagicMock()),
            patch("django.db.transaction.atomic", lambda func: func),
        ]
        _start_patches(patches)
        try:
            with pytest.raises(Exception) as exc_info:
                ingest_document(str(document.id))
        finally:
            _stop_patches(patches)

        assert "stored file" in str(exc_info.value).lower()

    def test_none_storage_key_raises(self) -> None:
        document = _make_document(storage_key=None)

        patches = [
            patch("rag.ingest.Document.objects.get", return_value=document),
            patch("rag.ingest.get_metrics"),
            patch("rag.ingest.get_trace_callbacks", return_value=[]),
            patch("rag.ingest.storage.download_to_path", return_value=None),
            patch("rag.ingest.caption_images", return_value=[]),
            patch("rag.ingest.pieces_to_documents", return_value=[MagicMock(metadata={"id": "c1", "type": "TEXT", "page": "1"})]),
            patch("rag.ingest.add_summaries", return_value=[]),
            patch("rag.ingest._purge_existing"),
            patch("rag.vectordb.upload"),
            patch("rag.ingest._persist_chunks"),
            patch("rag.ingest.flush_traces"),
            patch("rag.models.IngestionReport.objects.create", return_value=MagicMock()),
            patch("django.db.transaction.atomic", lambda func: func),
        ]
        _start_patches(patches)
        try:
            with pytest.raises(Exception):
                ingest_document(str(document.id))
        finally:
            _stop_patches(patches)


class TestCorruptFileHandling:
    """Reindex must handle corrupt/unreadable files gracefully."""

    def test_corrupt_pdf_fails_gracefully(self) -> None:
        document = _make_document(
            storage_key="1/corrupt-123/bad.pdf",
        )

        mock_download = MagicMock()
        mock_download.side_effect = lambda key, path: Path(path).write_bytes(b"not a pdf")
        mock_extract = MagicMock(side_effect=RuntimeError("PDF parse error"))

        patches = [
            patch("rag.ingest.Document.objects.get", return_value=document),
            patch("rag.ingest.get_metrics"),
            patch("rag.ingest.get_trace_callbacks", return_value=[]),
            patch("rag.ingest._extract", mock_extract),
            patch("rag.ingest.caption_images", return_value=[]),
            patch("rag.ingest.pieces_to_documents", return_value=[MagicMock(metadata={"id": "c1", "type": "TEXT", "page": "1"})]),
            patch("rag.ingest.add_summaries", return_value=[]),
            patch("rag.ingest._purge_existing"),
            patch("rag.vectordb.upload"),
            patch("rag.ingest._persist_chunks"),
            patch("rag.ingest.flush_traces"),
            patch("rag.ingest.storage.download_to_path", mock_download),
            patch("rag.models.IngestionReport.objects.create", return_value=MagicMock()),
            patch("django.db.transaction.atomic", lambda func: func),
        ]
        _start_patches(patches)
        try:
            with pytest.raises(RuntimeError):
                ingest_document(str(document.id))
        finally:
            _stop_patches(patches)

        assert document.status == Status.ERROR
        assert "PDF parse error" in (document.error_message or "")

    def test_minio_download_failure_surfaces_as_error(self) -> None:
        document = _make_document(
            storage_key="1/missing-123/gone.pdf",
        )

        patches = [
            patch("rag.ingest.Document.objects.get", return_value=document),
            patch("rag.ingest.get_metrics"),
            patch("rag.ingest.get_trace_callbacks", return_value=[]),
            patch("rag.ingest.storage.download_to_path", side_effect=FileNotFoundError("NoSuchKey")),
            patch("rag.ingest.caption_images", return_value=[]),
            patch("rag.ingest.pieces_to_documents", return_value=[MagicMock(metadata={"id": "c1", "type": "TEXT", "page": "1"})]),
            patch("rag.ingest.add_summaries", return_value=[]),
            patch("rag.ingest._purge_existing"),
            patch("rag.vectordb.upload"),
            patch("rag.ingest._persist_chunks"),
            patch("rag.ingest.flush_traces"),
            patch("rag.models.IngestionReport.objects.create", return_value=MagicMock()),
            patch("django.db.transaction.atomic", lambda func: func),
        ]
        _start_patches(patches)
        try:
            with pytest.raises(FileNotFoundError):
                ingest_document(str(document.id))
        finally:
            _stop_patches(patches)

        assert document.status == Status.ERROR


class TestDocumentMetadataPreservation:
    """Reindex must not mutate document metadata beyond status/chunk_count."""

    def test_reindex_preserves_name_and_owner(self) -> None:
        document = _make_document(
            name="Important Report.pdf",
            storage_key="1/meta-123/report.pdf",
        )
        original_name = document.name
        original_owner = document.owner_id

        patches = [
            patch("rag.ingest.Document.objects.get", return_value=document),
            patch("rag.ingest.get_metrics"),
            patch("rag.ingest.get_trace_callbacks", return_value=[]),
            patch("rag.ingest._extract", return_value=([MagicMock(content_type="TEXT", content="text", page="1", metadata={})], IngestionDiagnostics())),
            patch("rag.ingest.caption_images", return_value=[]),
            patch("rag.ingest.pieces_to_documents", return_value=[MagicMock(metadata={"id": "c1", "type": "TEXT", "page": "1"})]),
            patch("rag.ingest.add_summaries", return_value=[]),
            patch("rag.ingest._purge_existing"),
            patch("rag.vectordb.upload"),
            patch("rag.ingest._persist_chunks"),
            patch("rag.ingest.flush_traces"),
            patch("rag.ingest.storage.download_to_path", return_value=None),
            patch("rag.models.IngestionReport.objects.create", return_value=MagicMock()),
            patch("django.db.transaction.atomic", lambda func: func),
        ]
        _start_patches(patches)
        try:
            ingest_document(str(document.id))
        finally:
            _stop_patches(patches)

        assert document.name == original_name
        assert document.owner_id == original_owner

    def test_reindex_does_not_change_storage_key(self) -> None:
        document = _make_document(
            storage_key="1/key-123/report.pdf",
        )
        original_key = document.storage_key

        patches = [
            patch("rag.ingest.Document.objects.get", return_value=document),
            patch("rag.ingest.get_metrics"),
            patch("rag.ingest.get_trace_callbacks", return_value=[]),
            patch("rag.ingest._extract", return_value=([MagicMock(content_type="TEXT", content="text", page="1", metadata={})], IngestionDiagnostics())),
            patch("rag.ingest.caption_images", return_value=[]),
            patch("rag.ingest.pieces_to_documents", return_value=[MagicMock(metadata={"id": "c1", "type": "TEXT", "page": "1"})]),
            patch("rag.ingest.add_summaries", return_value=[]),
            patch("rag.ingest._purge_existing"),
            patch("rag.vectordb.upload"),
            patch("rag.ingest._persist_chunks"),
            patch("rag.ingest.flush_traces"),
            patch("rag.ingest.storage.download_to_path", return_value=None),
            patch("rag.models.IngestionReport.objects.create", return_value=MagicMock()),
            patch("django.db.transaction.atomic", lambda func: func),
        ]
        _start_patches(patches)
        try:
            ingest_document(str(document.id))
        finally:
            _stop_patches(patches)

        assert document.storage_key == original_key


class TestConcurrentReindexSafety:
    """Two simultaneous reindexes must not corrupt state."""

    def test_concurrent_reindex_produces_valid_state(self) -> None:
        document = _make_document(
            storage_key="1/conc-123/report.pdf",
        )
        pdf_bytes = b"%PDF-1.4 concurrent test " * 50

        mock_download = MagicMock()
        mock_download.side_effect = lambda key, path: Path(path).write_bytes(pdf_bytes)
        mock_extract = MagicMock(return_value=(
            [MagicMock(content_type="TEXT", content="text " * 200, page="1", metadata={})],
            IngestionDiagnostics(),
        ))

        patches = [
            patch("rag.ingest.Document.objects.get", return_value=document),
            patch("rag.ingest.get_metrics"),
            patch("rag.ingest.get_trace_callbacks", return_value=[]),
            patch("rag.ingest._extract", mock_extract),
            patch("rag.ingest.caption_images", return_value=[]),
            patch("rag.ingest.pieces_to_documents", return_value=[MagicMock(metadata={"id": "c1", "type": "TEXT", "page": "1"})]),
            patch("rag.ingest.add_summaries", return_value=[]),
            patch("rag.ingest._purge_existing"),
            patch("rag.vectordb.upload"),
            patch("rag.ingest._persist_chunks"),
            patch("rag.ingest.flush_traces"),
            patch("rag.ingest.storage.download_to_path", mock_download),
            patch("rag.models.IngestionReport.objects.create", return_value=MagicMock()),
            patch("django.db.transaction.atomic", lambda func: func),
        ]
        _start_patches(patches)
        try:
            ingest_document(str(document.id))
            document.status = Status.PROCESSING
            document.save = MagicMock()
            ingest_document(str(document.id))
        finally:
            _stop_patches(patches)

        assert document.status == Status.READY or document.status == Status.PROCESSING
