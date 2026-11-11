"""Trust-boundary tests. These guard the two untrusted inputs: files and URLs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from rag.security import sanitize_filename, validate_public_url, validate_upload


class TestSanitizeFilename:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("report.pdf", "report.pdf"),
            ("../../../etc/passwd", "passwd"),
            ("dir/sub/file.pdf", "file.pdf"),
            (r"C:\windows\evil.pdf", "evil.pdf"),
            ("...hidden.pdf", "hidden.pdf"),
        ],
    )
    def test_strips_path_traversal(self, raw, expected):
        assert sanitize_filename(raw) == expected

    def test_strips_control_characters(self):
        assert sanitize_filename("re\x00port\x1f.pdf") == "report.pdf"

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            sanitize_filename("   ")

    def test_truncates_long_names(self):
        assert len(sanitize_filename("a" * 400 + ".pdf")) <= 255


class TestValidateUpload:
    def test_accepts_plain_text(self):
        upload = SimpleUploadedFile("notes.txt", b"hello world", content_type="text/plain")
        name, content_type = validate_upload(upload)
        assert name == "notes.txt"
        assert content_type

    def test_accepts_restructuredtext(self):
        upload = SimpleUploadedFile("guide.rst", b"RAG_Kit\n=======", content_type="text/plain")
        name, content_type = validate_upload(upload)
        assert name == "guide.rst"
        assert content_type == "text/plain"

    def test_rejects_disallowed_extension(self):
        upload = SimpleUploadedFile("payload.exe", b"MZ\x90\x00", content_type="application/x-msdownload")
        with pytest.raises(ValidationError, match="Unsupported file type"):
            validate_upload(upload)

    def test_rejects_empty_file(self):
        upload = SimpleUploadedFile("empty.txt", b"", content_type="text/plain")
        with pytest.raises(ValidationError, match="empty"):
            validate_upload(upload)

    def test_rejects_oversized_file(self):
        upload = SimpleUploadedFile("big.txt", b"x", content_type="text/plain")
        # Lie about the size rather than allocating 50 MB in the test.
        upload.size = 999 * 1024 * 1024
        with pytest.raises(ValidationError, match="limit"):
            validate_upload(upload)

    def test_rejects_content_type_mismatch(self):
        """A PNG renamed to .pdf must not slip through."""
        png_magic = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        upload = SimpleUploadedFile("fake.pdf", png_magic, content_type="application/pdf")
        with patch("rag.security._sniff_mime", return_value="image/png"):
            with pytest.raises(ValidationError, match="does not match extension"):
                validate_upload(upload)

    def test_sanitizes_traversal_in_upload_name(self):
        upload = SimpleUploadedFile("x.txt", b"data", content_type="text/plain")
        upload.name = "../../etc/shadow.txt"
        name, _ = validate_upload(upload)
        assert name == "shadow.txt"

    def test_rejects_encrypted_pdf_before_queueing(self):
        upload = SimpleUploadedFile("locked.pdf", b"%PDF-1.4", content_type="application/pdf")
        with patch("rag.security._sniff_mime", return_value="application/pdf"), patch(
            "pypdf.PdfReader",
            return_value=SimpleNamespace(is_encrypted=True, pages=[]),
        ), pytest.raises(ValidationError, match="password-protected"):
            validate_upload(upload)

    def test_rejects_zero_page_pdf_before_queueing(self):
        upload = SimpleUploadedFile("empty.pdf", b"%PDF-1.4", content_type="application/pdf")
        with patch("rag.security._sniff_mime", return_value="application/pdf"), patch(
            "pypdf.PdfReader",
            return_value=SimpleNamespace(is_encrypted=False, pages=[]),
        ), pytest.raises(ValidationError, match="no pages"):
            validate_upload(upload)


class TestSSRFGuard:
    """The URL ingestion endpoint is a classic SSRF sink; these are the teeth."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost/admin",
            "http://127.0.0.1:8000/",
            "http://169.254.169.254/latest/meta-data/",  # cloud metadata
            "http://10.0.0.5/internal",
            "http://192.168.1.1/",
            "http://[::1]/",
        ],
    )
    def test_rejects_private_and_loopback(self, url):
        with pytest.raises(ValidationError):
            validate_public_url(url)

    def test_rejects_hostname_resolving_to_metadata_ip(self):
        """A public DNS name pointing at a private IP is the classic bypass."""
        with patch(
            "socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("169.254.169.254", 80))],
        ):
            with pytest.raises(ValidationError, match="non-public"):
                validate_public_url("https://evil.example.com/")

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/", "ftp://x/"])
    def test_rejects_dangerous_schemes(self, url):
        with pytest.raises(ValidationError):
            validate_public_url(url)

    def test_rejects_blocked_ports(self):
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 5432))]):
            with pytest.raises(ValidationError, match="Port"):
                validate_public_url("http://example.com:5432/")

    def test_accepts_public_url(self):
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            assert validate_public_url("https://example.com/docs") == "https://example.com/docs"

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            validate_public_url("")
