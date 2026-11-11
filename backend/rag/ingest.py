"""The ingestion pipeline.

Replaces the upstream admin-backend -> document-extractor -> rag-backend
round-trip (three services, two HTTP hops, two generated OpenAPI clients) with
one in-process function:

    Fast task: fetch -> extract -> chunk text/tables -> embed+upsert -> mark ENRICHING
    Enrichment task: caption images + summarise -> append vectors -> mark READY

Re-ingesting a source deletes its previous chunks first, so ingestion is
idempotent and never leaves orphaned vectors behind.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from pathlib import Path

from django.db import transaction
from langchain_core.documents import Document as LangDocument

from rag import storage, vectordb
from rag.chunking import pieces_to_documents
from rag.conf import get_config
from rag.enhance import add_summaries
from rag.extract import (
    Piece,
    extract_confluence,
    extract_file,
    extract_sitemap,
    extract_url,
)
from rag.image_captioner import caption_images
from rag.llm import flush_traces, get_trace_callbacks
from rag.metrics import get_metrics, log_context
from rag.models import Chunk, Document, IngestionReport, SourceType, Status
from rag.quality import IngestionDiagnostics, build_warnings, score_quality

logger = logging.getLogger(__name__)


class IngestionError(RuntimeError):
    """Raised when a document cannot be ingested."""


def _document_chunk_kwargs(document: Document) -> dict:
    return {
        "document_id": str(document.id),
        "organization_id": str(document.organization_id),
        "document_name": document.name,
        "storage_key": document.storage_key,
        "document_url": document.source_uri,
        "uploaded_by_id": document.uploaded_by_id,
        "is_private": document.is_private,
    }


def _needs_enrichment(pieces: list[Piece], documents: list) -> bool:
    settings = get_config()
    has_images = any(piece.content_type == "IMAGE" for piece in pieces)
    has_text = bool(documents)
    return bool(
        (settings.image_captioner.enabled and has_images)
        or (settings.summarizer.enabled and has_text)
    )


def _report_diagnostics(report: IngestionReport | None) -> IngestionDiagnostics:
    if report is None:
        return IngestionDiagnostics()
    return IngestionDiagnostics(
        pages_detected=report.pages_detected,
        pages_with_text=report.pages_with_text,
        pages_without_text=report.pages_without_text,
        total_extracted_chars=report.total_extracted_chars,
        extractor_used=report.extractor_used,
        ocr_used=report.ocr_used,
        ocr_language=report.ocr_language,
        text_chunks=report.text_chunks,
        table_chunks=report.table_chunks,
        image_chunks=report.image_chunks,
        summary_chunks=report.summary_chunks,
        failed_summaries=report.failed_summaries,
        embedding_status=report.embedding_status,
        vector_upload_status=report.vector_upload_status,
        warnings=list(report.warnings or []),
        duration_seconds=report.ingestion_duration_seconds,
    )


def _documents_from_chunks(document: Document) -> list[LangDocument]:
    """Rehydrate text chunks so enrichment never has to re-run text extraction."""
    chunks = document.chunks.exclude(content_type__in=["SUMMARY", "IMAGE"])
    documents = []
    for chunk in chunks:
        metadata = dict(chunk.metadata or {})
        metadata.setdefault("id", chunk.content_hash)
        metadata["type"] = chunk.content_type
        metadata.setdefault("page", chunk.page)
        documents.append(LangDocument(page_content=chunk.content, metadata=metadata))
    return documents


def ingest_text(document_id: str) -> int:
    """Extract and index text/table chunks, without waiting for enrichment."""
    start = time.perf_counter()
    document = Document.objects.get(pk=document_id)
    metrics = get_metrics()
    metrics.record_ingestion_start(str(document.id), document.source_type)
    document.status = Status.PROCESSING
    document.error_message = ""
    document.save(update_fields=["status", "error_message", "modified_at"])
    diagnostics = IngestionDiagnostics()
    stage = "extraction"

    try:
        pieces, diagnostics = _extract(document)
        _complete_diagnostics(diagnostics, pieces)
        quality_score = score_quality(diagnostics)
        usable_chars = diagnostics.total_extracted_chars
        if not pieces or (quality_score == "BAD" and usable_chars < get_config().quality.min_usable_chars):
            message = _diagnose_empty(diagnostics)
            _mark_document_error(document, diagnostics, message, time.perf_counter() - start)
            metrics.record_ingestion_failure(str(document.id), message[:500], time.perf_counter() - start)
            return 0

        text_pieces = [piece for piece in pieces if piece.content_type in {"TEXT", "TABLE"}]
        documents = pieces_to_documents(text_pieces, **_document_chunk_kwargs(document))
        if not documents:
            raise IngestionError("Extraction produced no text or table chunks.")

        stage = "embedding_and_upload"
        _purge_existing(document)
        vectordb.upload(documents)
        stage = "persisting"
        _persist_chunks(document, documents)

        diagnostics.text_chunks = sum(1 for item in documents if item.metadata.get("type") == "TEXT")
        diagnostics.table_chunks = sum(1 for item in documents if item.metadata.get("type") == "TABLE")
        diagnostics.image_chunks = sum(1 for piece in pieces if piece.content_type == "IMAGE")
        diagnostics.summary_chunks = 0
        diagnostics.embedding_status = "completed"
        diagnostics.vector_upload_status = "completed"
        diagnostics.duration_seconds = time.perf_counter() - start
        diagnostics.warnings = build_warnings(diagnostics)
        quality_score = score_quality(diagnostics)
        report = _create_ingestion_report(document, diagnostics, quality_score, diagnostics.duration_seconds)
        document.last_ingestion_report = report
        document.chunk_count = len(documents)
        document.status = Status.ENRICHING if _needs_enrichment(pieces, documents) else Status.READY
        document.save(
            update_fields=["chunk_count", "status", "last_ingestion_report", "modified_at"]
        )
        if document.status == Status.READY:
            metrics.record_ingestion_success(
                document_id=str(document.id), chunks=len(documents), summaries=0, images=0,
                latency=diagnostics.duration_seconds,
            )
        logger.info(
            "Indexed '%s': %d text/table chunks in %.2fs; status=%s.",
            document.name, len(documents), diagnostics.duration_seconds, document.status,
        )
        return len(documents)
    except Exception as exc:
        latency = time.perf_counter() - start
        metrics.record_ingestion_failure(str(document.id), str(exc)[:500], latency)
        logger.exception("Fast text ingestion failed for document %s.", document_id)
        if stage == "embedding_and_upload":
            diagnostics.embedding_status = "failed"
            diagnostics.vector_upload_status = "failed"
        elif stage == "persisting":
            diagnostics.embedding_status = "completed"
            diagnostics.vector_upload_status = "completed"
        _mark_document_error(document, diagnostics, _explain(exc)[:2000], latency)
        raise


def enrich_document(document_id: str) -> int:
    """Add summaries/captions after text is already queryable."""
    start = time.perf_counter()
    document = Document.objects.get(pk=document_id)
    if document.status not in {Status.ENRICHING, Status.READY}:
        return document.chunk_count

    document.status = Status.ENRICHING
    document.save(update_fields=["status", "modified_at"])
    report = document.last_ingestion_report
    diagnostics = _report_diagnostics(report)
    callbacks = get_trace_callbacks(
        user_id=str(document.organization_id), tags=["enrich", document.source_type]
    )

    try:
        text_documents = _documents_from_chunks(document)
        image_documents: list = []
        raw_image_count = 0
        settings = get_config()
        if settings.image_captioner.enabled and diagnostics.image_chunks:
            pieces, _ = _extract(document)
            raw_image_count = sum(1 for piece in pieces if piece.content_type == "IMAGE")
            if raw_image_count:
                captioned = asyncio.run(
                    caption_images(
                        pieces,
                        document_id=str(document.id),
                        organization_id=str(document.organization_id),
                    )
                )
                image_pieces = [piece for piece in captioned if piece.content_type == "IMAGE"]
                image_documents = pieces_to_documents(
                    image_pieces, **_document_chunk_kwargs(document)
                )
                image_documents = [
                    item for item in image_documents if item.metadata.get("type") == "IMAGE"
                ]

        summaries = asyncio.run(
            add_summaries(
                text_documents,
                callbacks=callbacks,
                organization_id=str(document.organization_id),
            )
        )
        enriched = image_documents + summaries
        if enriched:
            vectordb.upload(enriched)
            _persist_chunks(document, enriched)

        diagnostics.image_chunks = raw_image_count or diagnostics.image_chunks
        diagnostics.summary_chunks = len(summaries)
        diagnostics.embedding_status = "completed"
        diagnostics.vector_upload_status = "completed"
        diagnostics.duration_seconds += time.perf_counter() - start
        diagnostics.warnings = build_warnings(
            diagnostics,
            images_captioned=(raw_image_count == len(image_documents))
            if raw_image_count
            else None,
        )
        if raw_image_count and len(image_documents) < raw_image_count:
            diagnostics.warnings.append(
                f"{raw_image_count - len(image_documents)} images could not be captioned."
            )
        quality_score = score_quality(diagnostics)
        if report is None:
            report = _create_ingestion_report(
                document, diagnostics, quality_score, diagnostics.duration_seconds
            )
            document.last_ingestion_report = report
        else:
            report.text_chunks = diagnostics.text_chunks
            report.table_chunks = diagnostics.table_chunks
            report.image_chunks = diagnostics.image_chunks
            report.summary_chunks = diagnostics.summary_chunks
            report.failed_summaries = diagnostics.failed_summaries
            report.embedding_status = diagnostics.embedding_status
            report.vector_upload_status = diagnostics.vector_upload_status
            report.warnings = diagnostics.warnings
            report.quality_score = quality_score
            report.ingestion_duration_seconds = diagnostics.duration_seconds
            report.save()

        document.chunk_count = document.chunks.count()
        document.status = Status.READY
        document.error_message = ""
        document.save(
            update_fields=[
                "chunk_count", "status", "error_message", "last_ingestion_report", "modified_at"
            ]
        )
        metrics = get_metrics()
        metrics.record_ingestion_success(
            document_id=str(document.id),
            chunks=document.chunk_count,
            summaries=len(summaries),
            images=len(image_documents),
            latency=diagnostics.duration_seconds,
        )
        logger.info(
            "Enriched '%s': %d summaries, %d images in %.2fs.",
            document.name, len(summaries), len(image_documents), time.perf_counter() - start,
        )
        return document.chunk_count
    except Exception as exc:
        message = _explain(exc)[:2000]
        logger.exception("Enrichment failed for document %s; text remains queryable.", document_id)
        if report is not None:
            report.warnings = list(report.warnings or []) + [f"Enrichment failed: {message}"]
            report.save(update_fields=["warnings"])
        document.status = Status.READY
        document.error_message = message
        document.save(update_fields=["status", "error_message", "modified_at"])
        get_metrics().record_ingestion_failure(str(document.id), message[:500], time.perf_counter() - start)
        return document.chunk_count
    finally:
        flush_traces()


def ingest_document(document_id: str) -> int:
    """Run the pipeline for one document. Returns the number of chunks indexed.

    Always leaves the document in a terminal state (READY or ERROR) — a stuck
    PROCESSING row is worse than a visible failure.
    """
    start = time.perf_counter()
    document = Document.objects.get(pk=document_id)
    metrics = get_metrics()
    metrics.record_ingestion_start(str(document.id), document.source_type)

    document.status = Status.PROCESSING
    document.error_message = ""
    document.save(update_fields=["status", "error_message", "modified_at"])
    extract_diagnostics = IngestionDiagnostics()
    stage = "extraction"

    callbacks = get_trace_callbacks(
        user_id=str(document.organization_id), tags=["ingest", document.source_type]
    )

    try:
        pieces, extract_diagnostics = _extract(document)
        _complete_diagnostics(extract_diagnostics, pieces)
        quality_score = score_quality(extract_diagnostics)
        usable_chars = extract_diagnostics.total_extracted_chars
        if not pieces or (
            quality_score == "BAD"
            and usable_chars < get_config().quality.min_usable_chars
        ):
            message = _diagnose_empty(extract_diagnostics)
            _mark_document_error(
                document,
                extract_diagnostics,
                message,
                time.perf_counter() - start,
            )
            metrics.record_ingestion_failure(str(document.id), message[:500], time.perf_counter() - start)
            logger.warning("Document '%s' produced no usable content.", document.name)
            return 0

        stage = "captioning"
        pieces = asyncio.run(
            caption_images(
                pieces,
                document_id=str(document.id),
                organization_id=str(document.organization_id),
            )
        )

        stage = "chunking"
        documents = pieces_to_documents(
            pieces,
            document_id=str(document.id),
            organization_id=str(document.organization_id),
            document_name=document.name,
            storage_key=document.storage_key,
            document_url=document.source_uri,
            uploaded_by_id=document.uploaded_by_id,
            is_private=document.is_private,
        )
        if not documents:
            raise IngestionError("Extraction produced no indexable chunks.")

        stage = "summarization"
        summaries = asyncio.run(
            add_summaries(
                documents,
                callbacks=callbacks,
                organization_id=str(document.organization_id),
            )
        )
        all_documents = documents + summaries

        stage = "embedding_and_upload"
        _purge_existing(document)
        vectordb.upload(all_documents)
        stage = "persisting"
        _persist_chunks(document, all_documents)

        image_count = sum(1 for d in all_documents if d.metadata.get("type") == "IMAGE")
        latency = time.perf_counter() - start

        # Build ingestion quality report
        extract_diagnostics.text_chunks = sum(1 for d in documents if d.metadata.get("type") == "TEXT")
        extract_diagnostics.table_chunks = sum(1 for d in documents if d.metadata.get("type") == "TABLE")
        extract_diagnostics.image_chunks = sum(1 for d in documents if d.metadata.get("type") == "IMAGE")
        extract_diagnostics.summary_chunks = len(summaries)
        extract_diagnostics.embedding_status = "completed"
        extract_diagnostics.vector_upload_status = "completed"
        extract_diagnostics.duration_seconds = latency
        extract_diagnostics.warnings = build_warnings(extract_diagnostics)
        quality_score = score_quality(extract_diagnostics)

        report = _create_ingestion_report(document, extract_diagnostics, quality_score, latency)
        try:
            document.last_ingestion_report = report
        except ValueError:
            pass
        document.chunk_count = len(all_documents)
        document.status = Status.READY
        document.save(
            update_fields=["chunk_count", "status", "last_ingestion_report", "modified_at"]
        )

        metrics.record_ingestion_success(
            document_id=str(document.id),
            chunks=len(all_documents),
            summaries=len(summaries),
            images=image_count,
            latency=latency,
        )
        logger.info(
            "Ingested '%s': %d chunks (%d summaries, %d images) in %.2fs. Quality=%s",
            document.name,
            len(all_documents),
            len(summaries),
            image_count,
            latency,
            quality_score,
        )
        return len(all_documents)

    except Exception as exc:
        latency = time.perf_counter() - start
        metrics.record_ingestion_failure(str(document.id), str(exc)[:500], latency)
        logger.exception("Ingestion failed for document %s.", document_id)
        if stage == "embedding_and_upload":
            extract_diagnostics.embedding_status = "failed"
            extract_diagnostics.vector_upload_status = "failed"
        elif stage == "persisting":
            extract_diagnostics.embedding_status = "completed"
            extract_diagnostics.vector_upload_status = "completed"
        _mark_document_error(
            document,
            extract_diagnostics,
            _explain(exc)[:2000],
            latency,
        )
        raise
    finally:
        flush_traces()


def _explain(exc: Exception) -> str:
    """Turn opaque provider errors into something a user can act on."""
    from rag.conf import get_config

    name = type(exc).__name__
    text = str(exc)

    if name in ("APIConnectionError", "OpenAIError") and "onnection" in text.lower():
        base_url = get_config().embedder.base_url or get_config().llm.base_url or "the OpenAI API"
        return (
            f"Could not reach the model provider at {base_url}. "
            "Check LLM_API_KEY / EMBEDDER_API_KEY and the *_BASE_URL settings "
            "in .env.backend, then re-index."
        )
    if name in ("AuthenticationError", "PermissionDeniedError"):
        return "The model provider rejected the API key. Check LLM_API_KEY / EMBEDDER_API_KEY."
    if name == "RateLimitError":
        return "The model provider rate-limited this request. Re-index in a few minutes."
    if name == "NotFoundError":
        return f"The configured model was not found by the provider: {text}"
    if "timeout" in text.lower():
        return "A request timed out. Check network connectivity and provider status, then retry."
    return text or name


def _complete_diagnostics(
    diagnostics: IngestionDiagnostics, pieces: list[Piece]
) -> IngestionDiagnostics:
    """Fill page/character metrics for source extractors without diagnostics."""
    if diagnostics.total_extracted_chars == 0:
        diagnostics.total_extracted_chars = sum(
            len(piece.content)
            for piece in pieces
            if piece.content_type in {"TEXT", "TABLE"}
        )
    if diagnostics.pages_detected == 0 and pieces:
        pages = {piece.page for piece in pieces if piece.page}
        diagnostics.pages_detected = len(pages) or 1
    if diagnostics.pages_with_text == 0 and diagnostics.total_extracted_chars > 0:
        diagnostics.pages_with_text = min(diagnostics.pages_detected, 1)
    diagnostics.pages_without_text = max(
        diagnostics.pages_detected - diagnostics.pages_with_text, 0
    )
    return diagnostics


def _diagnose_empty(diagnostics: IngestionDiagnostics) -> str:
    """Explain an unusable source in language an operator can act on."""
    if diagnostics.pages_detected and not diagnostics.pages_with_text:
        if diagnostics.ocr_used:
            return (
                "This document appears to be scanned images, and OCR could not read "
                "any text from it. Try a higher-quality scan, or upload a text PDF."
            )
        return (
            "No text could be extracted — this looks like a scanned document. "
            "Enable OCR (INGESTION_OCR_ENABLED=true) and re-index."
        )
    return (
        "Very little readable content was found. The file may be empty, "
        "password-protected, or in an unsupported layout."
    )


def _create_ingestion_report(
    document: Document,
    diagnostics: IngestionDiagnostics,
    quality_score: str,
    latency: float,
) -> IngestionReport:
    """Persist one complete diagnostic snapshot for an ingestion attempt."""
    return IngestionReport.objects.create(
        document=document,
        pages_detected=diagnostics.pages_detected,
        pages_with_text=diagnostics.pages_with_text,
        pages_without_text=diagnostics.pages_without_text,
        total_extracted_chars=diagnostics.total_extracted_chars,
        avg_chars_per_page=diagnostics.avg_chars_per_page,
        extractor_used=diagnostics.extractor_used,
        ocr_used=diagnostics.ocr_used,
        ocr_language=diagnostics.ocr_language,
        text_chunks=diagnostics.text_chunks,
        table_chunks=diagnostics.table_chunks,
        image_chunks=diagnostics.image_chunks,
        summary_chunks=diagnostics.summary_chunks,
        failed_summaries=diagnostics.failed_summaries,
        embedding_status=diagnostics.embedding_status,
        vector_upload_status=diagnostics.vector_upload_status,
        warnings=diagnostics.warnings,
        quality_score=quality_score,
        ingestion_duration_seconds=latency,
    )


def _mark_document_error(
    document: Document,
    diagnostics: IngestionDiagnostics,
    message: str,
    latency: float,
) -> None:
    """Persist a terminal ERROR state and its diagnostic report."""
    try:
        _purge_existing(document)
    except Exception:
        logger.warning("Could not clear stale index for failed document %s.", document.id, exc_info=True)

    _complete_diagnostics(diagnostics, [])
    if diagnostics.embedding_status == "pending":
        diagnostics.embedding_status = "not_started"
    if diagnostics.vector_upload_status == "pending":
        diagnostics.vector_upload_status = "not_started"
    diagnostics.duration_seconds = latency
    diagnostics.warnings = build_warnings(diagnostics)
    quality_score = score_quality(diagnostics)

    report = None
    try:
        report = _create_ingestion_report(document, diagnostics, quality_score, latency)
    except Exception:
        logger.exception("Could not persist failure report for document %s.", document.id)

    document.status = Status.ERROR
    document.error_message = message[:2000]
    document.chunk_count = 0
    fields = ["status", "error_message", "chunk_count", "modified_at"]
    if report is not None:
        try:
            document.last_ingestion_report = report
            fields.append("last_ingestion_report")
        except ValueError:
            # Unit-test doubles may return a mock report; a real Django row
            # always receives the concrete IngestionReport instance.
            logger.debug("Failure report relation could not be assigned.", exc_info=True)
    document.save(update_fields=fields)


def _extract(document: Document) -> tuple[list[Piece], IngestionDiagnostics]:
    """Dispatch to the right extractor for the source type."""
    if document.source_type == SourceType.FILE:
        if not document.storage_key:
            raise IngestionError("Document has no stored file.")
        suffix = Path(document.name).suffix or ".bin"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as handle:
            storage.download_to_path(document.storage_key, handle.name)
            return extract_file(Path(handle.name), document.name)

    if document.source_type == SourceType.URL:
        return extract_url(document.source_uri), IngestionDiagnostics(
            extractor_used="url_fetcher", pages_detected=1
        )

    if document.source_type == SourceType.SITEMAP:
        return extract_sitemap(document.source_uri), IngestionDiagnostics(
            extractor_used="sitemap", pages_detected=1
        )

    if document.source_type == SourceType.CONFLUENCE:
        import os

        options = document.source_options or {}
        space_key = options.get("space_key")
        if not space_key:
            raise IngestionError("Confluence sources require a 'space_key'.")
        token = options.get("token") or os.environ.get("CONFLUENCE_TOKEN", "")
        if not token:
            raise IngestionError("Confluence sources require CONFLUENCE_TOKEN to be set.")
        return extract_confluence(
            url=document.source_uri,
            space_key=space_key,
            token=token,
            verify_ssl=options.get("verify_ssl", True),
        ), IngestionDiagnostics(extractor_used="confluence", pages_detected=1)

    raise IngestionError(f"Unsupported source type '{document.source_type}'.")


def _purge_existing(document: Document) -> None:
    """Remove a document's prior vectors and chunk rows."""
    try:
        vectordb.delete_by(document_id=str(document.id))
    except Exception:
        logger.warning("Could not purge old vectors for %s.", document.id, exc_info=True)
    Chunk.objects.filter(document=document).delete()


@transaction.atomic
def _persist_chunks(document: Document, documents: list) -> None:
    """Mirror chunks into Postgres for the admin UI and future re-indexing."""
    Chunk.objects.bulk_create(
        [
            Chunk(
                document=document,
                content_hash=item.metadata["id"],
                content=item.page_content,
                content_type=item.metadata.get("type", "TEXT"),
                page=str(item.metadata.get("page", ""))[:255],
                metadata={
                    key: value
                    for key, value in item.metadata.items()
                    if key not in ("base64_image",)
                },
            )
            for item in documents
        ],
        batch_size=500,
        ignore_conflicts=True,
    )


def delete_document(document: Document) -> None:
    """Delete a document everywhere: vectors, object storage, database."""
    try:
        vectordb.delete_by(document_id=str(document.id))
    except Exception:
        logger.warning("Could not delete vectors for %s.", document.id, exc_info=True)
    if document.storage_key:
        storage.delete(document.storage_key)
    document.delete()
