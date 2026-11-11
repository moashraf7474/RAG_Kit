"""Ingestion quality scoring and diagnostics.

Evaluates extracted pieces and produces a structured quality report with
warnings and a GOOD / WARNING / BAD score.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from rag.conf import get_config

logger = logging.getLogger(__name__)


@dataclass
class IngestionDiagnostics:
    """Collects metrics during extraction and chunking."""

    pages_detected: int = 0
    pages_with_text: int = 0
    pages_without_text: int = 0
    total_extracted_chars: int = 0
    extractor_used: str = ""
    ocr_used: bool = False
    ocr_language: str = ""
    text_chunks: int = 0
    table_chunks: int = 0
    image_chunks: int = 0
    summary_chunks: int = 0
    failed_summaries: int = 0
    embedding_status: str = "pending"
    vector_upload_status: str = "pending"
    warnings: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def avg_chars_per_page(self) -> float:
        if self.pages_detected == 0:
            return 0.0
        return self.total_extracted_chars / self.pages_detected

    @property
    def text_coverage_ratio(self) -> float:
        if self.pages_detected == 0:
            return 0.0
        return self.pages_with_text / self.pages_detected


def score_quality(diagnostics: IngestionDiagnostics) -> str:
    """Return GOOD, WARNING, or BAD based on diagnostics."""
    settings = get_config().quality

    # Bad conditions
    if diagnostics.pages_detected > 0 and diagnostics.text_coverage_ratio < settings.bad_text_coverage_threshold:
        return "BAD"
    if diagnostics.failed_summaries > diagnostics.summary_chunks + diagnostics.failed_summaries * settings.bad_summary_failure_ratio:
        return "BAD"
    if diagnostics.pages_detected > 0 and diagnostics.avg_chars_per_page < settings.bad_avg_chars_per_page:
        return "BAD"
    if diagnostics.pages_detected > 10 and diagnostics.text_chunks == 0 and diagnostics.table_chunks == 0:
        return "BAD"

    # Warning conditions
    if diagnostics.pages_detected > 0 and diagnostics.text_coverage_ratio < settings.warning_text_coverage_threshold:
        return "WARNING"
    if diagnostics.failed_summaries > 0:
        return "WARNING"
    if diagnostics.pages_without_text > diagnostics.pages_with_text:
        return "WARNING"
    if diagnostics.avg_chars_per_page < settings.warning_avg_chars_per_page:
        return "WARNING"

    return "GOOD"


def build_warnings(
    diagnostics: IngestionDiagnostics, *, images_captioned: bool | None = None
) -> list[str]:
    """Build a list of human-readable warnings from diagnostics."""
    warnings: list[str] = []

    if diagnostics.pages_detected == 0:
        warnings.append("No pages detected in document.")
        return warnings

    coverage = diagnostics.text_coverage_ratio
    if coverage < 0.5:
        warnings.append(
            f"Only {diagnostics.pages_with_text} of {diagnostics.pages_detected} pages have extractable text ({coverage:.0%}). OCR may be required."
        )
    elif coverage < 0.8:
        warnings.append(
            f"{diagnostics.pages_without_text} pages have no extractable text. Consider enabling OCR."
        )

    if diagnostics.avg_chars_per_page < 100:
        warnings.append(
            f"Very low character density ({diagnostics.avg_chars_per_page:.0f} chars/page). Document may be scanned or image-based."
        )
    elif diagnostics.avg_chars_per_page < 300:
        warnings.append(
            f"Low character density ({diagnostics.avg_chars_per_page:.0f} chars/page). Extraction may be incomplete."
        )

    if diagnostics.failed_summaries > 0:
        warnings.append(
            f"{diagnostics.failed_summaries} page summaries failed. Some pages lack summarised context."
        )

    if diagnostics.text_chunks == 0 and diagnostics.table_chunks == 0 and diagnostics.pages_detected > 0:
        warnings.append("No text or table chunks were produced. Document may be empty or unsupported.")

    if diagnostics.image_chunks > 0 and not diagnostics.ocr_used and images_captioned is not True:
        warnings.append(
            f"{diagnostics.image_chunks} images were extracted but not captioned. Enable image captioning for better retrieval."
        )

    return warnings
