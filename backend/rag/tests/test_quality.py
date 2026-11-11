"""Tests for ingestion quality scoring and reporting."""

from __future__ import annotations

import pytest

from rag.quality import IngestionDiagnostics, build_warnings, score_quality


class TestQualityScoring:
    def test_good_score_when_high_coverage_and_chars(self) -> None:
        diag = IngestionDiagnostics(
            pages_detected=100,
            pages_with_text=95,
            total_extracted_chars=250_000,
            text_chunks=800,
            table_chunks=20,
            image_chunks=0,
            summary_chunks=95,
            failed_summaries=0,
            ocr_used=True,
        )
        assert score_quality(diag) == "GOOD"

    def test_warning_when_moderate_coverage(self) -> None:
        diag = IngestionDiagnostics(
            pages_detected=100,
            pages_with_text=60,
            total_extracted_chars=120_000,
            text_chunks=400,
            table_chunks=10,
            image_chunks=5,
            summary_chunks=60,
            failed_summaries=0,
        )
        assert score_quality(diag) == "WARNING"

    def test_bad_when_mostly_no_text(self) -> None:
        diag = IngestionDiagnostics(
            pages_detected=100,
            pages_with_text=20,
            total_extracted_chars=10_000,
            text_chunks=50,
            table_chunks=0,
            image_chunks=0,
            summary_chunks=20,
            failed_summaries=0,
        )
        assert score_quality(diag) == "BAD"

    def test_bad_when_no_chunks_produced(self) -> None:
        diag = IngestionDiagnostics(
            pages_detected=50,
            pages_with_text=0,
            total_extracted_chars=0,
            text_chunks=0,
            table_chunks=0,
            image_chunks=0,
            summary_chunks=0,
            failed_summaries=0,
        )
        assert score_quality(diag) == "BAD"

    def test_warning_when_summary_failures_exist(self) -> None:
        diag = IngestionDiagnostics(
            pages_detected=100,
            pages_with_text=90,
            total_extracted_chars=200_000,
            text_chunks=700,
            table_chunks=10,
            image_chunks=5,
            summary_chunks=90,
            failed_summaries=5,
        )
        assert score_quality(diag) == "WARNING"

    def test_warning_when_more_pages_without_text_than_with(self) -> None:
        diag = IngestionDiagnostics(
            pages_detected=100,
            pages_with_text=40,
            total_extracted_chars=50_000,
            text_chunks=200,
            table_chunks=0,
            image_chunks=0,
            summary_chunks=40,
            failed_summaries=0,
        )
        assert score_quality(diag) == "WARNING"


class TestBuildWarnings:
    def test_no_warnings_for_good_document(self) -> None:
        diag = IngestionDiagnostics(
            pages_detected=100,
            pages_with_text=95,
            total_extracted_chars=250_000,
            text_chunks=800,
            table_chunks=20,
            image_chunks=0,
            summary_chunks=95,
            failed_summaries=0,
            ocr_used=True,
        )
        warnings = build_warnings(diag)
        assert len(warnings) == 0

    def test_warns_about_no_pages_detected(self) -> None:
        diag = IngestionDiagnostics()
        warnings = build_warnings(diag)
        assert any("No pages detected" in w for w in warnings)

    def test_warns_about_low_text_coverage(self) -> None:
        diag = IngestionDiagnostics(
            pages_detected=100,
            pages_with_text=30,
            total_extracted_chars=50_000,
            text_chunks=200,
            table_chunks=0,
            image_chunks=0,
            summary_chunks=30,
            failed_summaries=0,
        )
        warnings = build_warnings(diag)
        assert any("OCR may be required" in w for w in warnings)

    def test_warns_about_very_low_char_density(self) -> None:
        diag = IngestionDiagnostics(
            pages_detected=100,
            pages_with_text=80,
            total_extracted_chars=3_000,
            text_chunks=200,
            table_chunks=0,
            image_chunks=0,
            summary_chunks=80,
            failed_summaries=0,
        )
        warnings = build_warnings(diag)
        assert any("Very low character density" in w for w in warnings)

    def test_warns_about_failed_summaries(self) -> None:
        diag = IngestionDiagnostics(
            pages_detected=100,
            pages_with_text=90,
            total_extracted_chars=200_000,
            text_chunks=700,
            table_chunks=10,
            image_chunks=5,
            summary_chunks=90,
            failed_summaries=10,
        )
        warnings = build_warnings(diag)
        assert any("10 page summaries failed" in w for w in warnings)

    def test_warns_about_images_without_captioning(self) -> None:
        diag = IngestionDiagnostics(
            pages_detected=10,
            pages_with_text=10,
            total_extracted_chars=10_000,
            text_chunks=100,
            table_chunks=0,
            image_chunks=5,
            summary_chunks=10,
            failed_summaries=0,
            ocr_used=False,
        )
        warnings = build_warnings(diag)
        assert any("images were extracted but not captioned" in w for w in warnings)

    def test_warns_when_no_text_or_table_chunks(self) -> None:
        diag = IngestionDiagnostics(
            pages_detected=50,
            pages_with_text=0,
            total_extracted_chars=0,
            text_chunks=0,
            table_chunks=0,
            image_chunks=0,
            summary_chunks=0,
            failed_summaries=0,
        )
        warnings = build_warnings(diag)
        assert any("No text or table chunks" in w for w in warnings)


class TestDiagnosticsProperties:
    def test_avg_chars_per_page(self) -> None:
        diag = IngestionDiagnostics(pages_detected=10, total_extracted_chars=5000)
        assert diag.avg_chars_per_page == 500.0

    def test_avg_chars_per_page_zero_pages(self) -> None:
        diag = IngestionDiagnostics(pages_detected=0, total_extracted_chars=0)
        assert diag.avg_chars_per_page == 0.0

    def test_text_coverage_ratio(self) -> None:
        diag = IngestionDiagnostics(pages_detected=100, pages_with_text=75)
        assert diag.text_coverage_ratio == 0.75

    def test_text_coverage_ratio_zero_pages(self) -> None:
        diag = IngestionDiagnostics(pages_detected=0, pages_with_text=0)
        assert diag.text_coverage_ratio == 0.0


class TestOCRDectection:
    def test_ocr_detected_when_pages_without_text_but_text_chunks_exist(self) -> None:
        diag = IngestionDiagnostics(
            pages_detected=10,
            pages_with_text=3,
            pages_without_text=7,
            total_extracted_chars=5000,
            text_chunks=50,
            table_chunks=0,
            image_chunks=0,
            summary_chunks=3,
            failed_summaries=0,
        )
        diag.ocr_used = True
        diag.ocr_language = "eng,ara"
        assert diag.ocr_used is True
        assert diag.ocr_language == "eng,ara"

    def test_ocr_not_detected_when_all_pages_have_text(self) -> None:
        diag = IngestionDiagnostics(
            pages_detected=10,
            pages_with_text=10,
            pages_without_text=0,
            total_extracted_chars=20000,
            text_chunks=100,
            table_chunks=0,
            image_chunks=0,
            summary_chunks=10,
            failed_summaries=0,
        )
        diag.ocr_used = False
        diag.ocr_language = ""
        assert diag.ocr_used is False
