"""Extractor routing and diagnostics tests."""

from unittest.mock import patch

from rag.extract import Piece, _is_poor_extraction, extract_file
from rag.quality import IngestionDiagnostics


def _diagnostics(*, chars: int) -> IngestionDiagnostics:
    return IngestionDiagnostics(
        pages_detected=1,
        pages_with_text=1,
        total_extracted_chars=chars,
        text_chunks=1,
    )


def test_quality_fallback_retries_markitdown_after_poor_docling(tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"placeholder")
    docling_pieces = [Piece(content="x", content_type="TEXT", page="1")]
    markitdown_pieces = [
        Piece(
            content="A usable extracted report with enough text for the quality gate.",
            content_type="TEXT",
        )
    ]

    with patch("rag.extract._try_docling", return_value=(docling_pieces, _diagnostics(chars=1))):
        with patch(
            "rag.extract._try_markitdown",
            return_value=(markitdown_pieces, _diagnostics(chars=42)),
        ) as markitdown:
            pieces, diagnostics = extract_file(path, path.name)

    assert pieces == markitdown_pieces
    assert diagnostics.extractor_used == "markitdown"
    markitdown.assert_called_once()


def test_table_content_counts_as_usable_extraction():
    pieces = [
        Piece(
            content="| header | value |\n| --- | --- |\n| a long table value | "
            "another long table value |",
            content_type="TABLE",
        )
    ]
    assert _is_poor_extraction(pieces) is False


def test_plain_text_diagnostics_report_one_page(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("This is a markdown document with readable content.")

    pieces, diagnostics = extract_file(path, path.name)

    assert pieces
    assert diagnostics.extractor_used == "plain_text"
    assert diagnostics.pages_detected == 1
    assert diagnostics.pages_with_text == 1


def test_restructuredtext_files_are_read_as_plain_text(tmp_path):
    path = tmp_path / "guide.rst"
    path.write_text("RAG_Kit\n=======\nA reStructuredText document.")

    pieces, diagnostics = extract_file(path, path.name)

    assert len(pieces) == 1
    assert pieces[0].content == "RAG_Kit\n=======\nA reStructuredText document."
    assert diagnostics.extractor_used == "plain_text"
