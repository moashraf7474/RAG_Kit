"""Phase 3 guarantees: unusable ingestion is visible and actionable."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from rag.ingest import ingest_document
from rag.models import Document, SourceType, Status
from rag.quality import IngestionDiagnostics


def _document() -> Document:
    document = Document(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        name="scanned.pdf",
        source_type=SourceType.FILE,
        storage_key="org/scanned.pdf",
        status=Status.PROCESSING,
    )
    document.save = MagicMock()
    return document


def test_empty_extraction_creates_report_and_terminal_error():
    document = _document()
    diagnostics = IngestionDiagnostics(
        pages_detected=3,
        pages_with_text=0,
        pages_without_text=3,
        extractor_used="pypdf_poor",
    )
    report = MagicMock()

    with (
        patch("rag.ingest.Document.objects.get", return_value=document),
        patch("rag.ingest.get_metrics"),
        patch("rag.ingest.get_trace_callbacks", return_value=[]),
        patch("rag.ingest._extract", return_value=([], diagnostics)),
        patch("rag.ingest._purge_existing"),
        patch("rag.ingest._create_ingestion_report", return_value=report) as create_report,
        patch("rag.ingest.flush_traces"),
    ):
        result = ingest_document(str(document.id))

    assert result == 0
    assert document.status == Status.ERROR
    assert "scanned" in document.error_message.lower()
    create_report.assert_called_once()
    assert document.chunk_count == 0


def test_short_extraction_is_not_marked_ready():
    document = _document()
    diagnostics = IngestionDiagnostics(
        pages_detected=1,
        pages_with_text=1,
        extractor_used="pypdf_poor",
        total_extracted_chars=14,
    )
    piece = MagicMock(content_type="TEXT", content="too short", page="1")

    with (
        patch("rag.ingest.Document.objects.get", return_value=document),
        patch("rag.ingest.get_metrics"),
        patch("rag.ingest.get_trace_callbacks", return_value=[]),
        patch("rag.ingest._extract", return_value=([piece], diagnostics)),
        patch("rag.ingest._purge_existing"),
        patch("rag.ingest._create_ingestion_report", return_value=MagicMock()),
        patch("rag.ingest.flush_traces"),
        patch("rag.ingest.caption_images") as caption,
    ):
        result = ingest_document(str(document.id))

    assert result == 0
    assert document.status == Status.ERROR
    caption.assert_not_called()
