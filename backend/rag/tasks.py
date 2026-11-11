"""Celery tasks.

Ingestion is slow (extraction, LLM summaries, embeddings) so it never runs in
the request cycle. The worker is the same Django image with a different command
— one codebase, not a second service.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.core.cache import cache

from rag.ingest import enrich_document, ingest_text
from rag.models import Document, Status

logger = logging.getLogger(__name__)

# A worker killed mid-task (OOM, host restart) must not silently drop the
# document — acks_late + reject_on_worker_lost requeues it. But a document
# that deterministically OOMs (e.g. a large/complex PDF exceeding the
# container's memory limit) fails identically every time, and nothing caps
# how many times that requeue can happen: max_retries/autoretry_for only
# govern self.retry() and the exception types passed to autoretry_for —
# reject_on_worker_lost instead calls Request.reject(requeue=True), a
# broker-level nack. The message comes back as a brand-new delivery with a
# fresh Request object, so self.request.retries is 0 on every attempt; it
# never reflects worker-lost requeues. Track attempts ourselves, in Redis
# (already the Celery broker/Django cache here), keyed by document id.
_MAX_WORKER_LOSS_ATTEMPTS = 3
_ATTEMPT_TTL_SECONDS = 3600


def _attempt_count(cache_key: str) -> int:
    """Atomically record one more attempt and return the running total."""
    try:
        count = cache.incr(cache_key)
    except ValueError:
        # Key doesn't exist yet (first attempt, or TTL expired).
        cache.set(cache_key, 1, timeout=_ATTEMPT_TTL_SECONDS)
        count = 1
    return count


@shared_task(
    bind=True,
    name="rag.ingest_document",
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=3,
    acks_late=True,
    reject_on_worker_lost=True,
)
def ingest_document_task(self, document_id: str) -> int:
    """Index extracted text quickly, then queue optional enrichment."""
    cache_key = f"rag:ingest_attempts:{document_id}"
    attempts = _attempt_count(cache_key)
    if attempts > _MAX_WORKER_LOSS_ATTEMPTS:
        logger.error(
            "Ingestion for %s lost its worker on %d attempts; giving up rather "
            "than retrying forever.",
            document_id,
            attempts - 1,
        )
        cache.delete(cache_key)
        Document.objects.filter(pk=document_id).update(
            status=Status.ERROR,
            error_message=(
                "Ingestion repeatedly ran out of memory or crashed the worker "
                "process. This document may be too large or complex for the "
                "current memory limit. Try a smaller file, or contact an "
                "administrator to increase worker memory."
            ),
        )
        return 0

    logger.info(
        "Starting fast text-ingestion task for %s (attempt %d/%d).",
        document_id,
        attempts,
        _MAX_WORKER_LOSS_ATTEMPTS,
    )
    chunk_count = ingest_text(document_id)
    cache.delete(cache_key)  # succeeded — don't carry the count into a future re-index
    document = Document.objects.get(pk=document_id)
    if document.status == Status.ENRICHING:
        try:
            result = enrich_document_task.delay(document_id)
            Document.objects.filter(pk=document_id).update(task_id=result.id)
        except Exception:
            logger.exception("Could not queue enrichment for %s.", document_id)
            Document.objects.filter(pk=document_id).update(
                status=Status.READY,
                error_message="Text is indexed, but enrichment could not be queued.",
            )
    return chunk_count


@shared_task(
    bind=True,
    name="rag.enrich_document",
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=3,
    acks_late=True,
    reject_on_worker_lost=True,
)
def enrich_document_task(self, document_id: str) -> int:
    """Add summaries and image captions after text is already queryable."""
    cache_key = f"rag:enrich_attempts:{document_id}"
    attempts = _attempt_count(cache_key)
    if attempts > _MAX_WORKER_LOSS_ATTEMPTS:
        logger.error(
            "Enrichment for %s lost its worker on %d attempts; giving up.",
            document_id,
            attempts - 1,
        )
        cache.delete(cache_key)
        # Text is already indexed and queryable — enrichment is a quality
        # add-on, not a hard requirement, so leave the document usable.
        Document.objects.filter(pk=document_id).update(
            status=Status.READY,
            error_message=(
                "Text is indexed, but summary/caption enrichment repeatedly "
                "crashed the worker and was skipped."
            ),
        )
        return 0

    logger.info(
        "Starting enrichment task for %s (attempt %d/%d).",
        document_id,
        attempts,
        _MAX_WORKER_LOSS_ATTEMPTS,
    )
    result = enrich_document(document_id)
    cache.delete(cache_key)
    return result


@shared_task(name="rag.reap_stuck_documents")
def reap_stuck_documents(max_age_minutes: int = 60) -> int:
    """Flag documents stuck in PROCESSING past a deadline.

    Covers the case where a worker dies so hard that even `acks_late` cannot
    requeue the job, which would otherwise leave a spinner in the UI forever.
    """
    from datetime import timedelta

    from django.utils import timezone

    cutoff = timezone.now() - timedelta(minutes=max_age_minutes)
    stuck = Document.objects.filter(
        status__in=[Status.PROCESSING, Status.UPLOADING, Status.ENRICHING],
        modified_at__lt=cutoff,
    )
    count = stuck.update(
        status=Status.ERROR,
        error_message="Ingestion or enrichment timed out. Please retry.",
    )
    if count:
        logger.warning("Reaped %d stuck document(s).", count)
    return count
