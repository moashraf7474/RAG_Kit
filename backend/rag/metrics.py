"""Observability hooks for the RAG pipeline.

Structured logging and metrics are intentionally opt-in via callbacks so the
core pipeline stays dependency-free. Every hook accepts `**extra` so new
dimensions can be added without changing call sites.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class PipelineMetrics:
    """In-process metrics collector. Replace with Prometheus/Datadog in prod."""

    def __init__(self) -> None:
        self.ingestion_total = 0
        self.ingestion_success = 0
        self.ingestion_failure = 0
        self.chunks_created = 0
        self.summaries_created = 0
        self.images_captioned = 0
        self.retrieval_total = 0
        self.last_ingestion_latency: float | None = None

    def record_ingestion_start(self, document_id: str, source_type: str) -> None:
        self.ingestion_total += 1
        logger.info(
            "Ingestion started",
            extra={"document_id": document_id, "source_type": source_type},
        )

    def record_ingestion_success(
        self, document_id: str, chunks: int, summaries: int, images: int, latency: float
    ) -> None:
        self.ingestion_success += 1
        self.chunks_created += chunks
        self.summaries_created += summaries
        self.images_captioned += images
        self.last_ingestion_latency = latency
        logger.info(
            "Ingestion succeeded",
            extra={
                "document_id": document_id,
                "chunks": chunks,
                "summaries": summaries,
                "images": images,
                "latency_s": round(latency, 3),
            },
        )

    def record_ingestion_failure(self, document_id: str, error: str, latency: float) -> None:
        self.ingestion_failure += 1
        self.last_ingestion_latency = latency
        logger.error(
            "Ingestion failed",
            extra={"document_id": document_id, "error": error, "latency_s": round(latency, 3)},
        )

    def record_retrieval(self, query: str, results: int, latency: float) -> None:
        self.retrieval_total += 1
        logger.debug(
            "Retrieval completed",
            extra={"query": query[:200], "results": results, "latency_s": round(latency, 3)},
        )

    def get_success_rate(self) -> float:
        if self.ingestion_total == 0:
            return 0.0
        return self.ingestion_success / self.ingestion_total


_pipeline_metrics = PipelineMetrics()


def get_metrics() -> PipelineMetrics:
    return _pipeline_metrics


def log_context(stage: str, **extra: Any) -> None:
    logger.info("RAG.%s", stage, extra=extra)
