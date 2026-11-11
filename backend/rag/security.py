"""Trust-boundary validation for ingestion.

Two untrusted inputs reach this system: uploaded files and user-supplied URLs.
Upstream validated neither (it ran inside a trusted cluster); exposing the same
endpoints to authenticated end users makes both exploitable, so they are
validated here.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from rag.conf import get_config

logger = logging.getLogger(__name__)

# Extension -> MIME allowlist. Anything not listed is rejected outright.
ALLOWED_EXTENSIONS: dict[str, tuple[str, ...]] = {
    ".pdf": ("application/pdf",),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/zip"),
    ".pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/zip"),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/zip"),
    ".csv": ("text/csv", "text/plain", "application/csv"),
    ".txt": ("text/plain",),
    ".md": ("text/markdown", "text/plain"),
    ".rst": ("text/plain",),
    ".html": ("text/html",),
    ".htm": ("text/html",),
    ".xml": ("application/xml", "text/xml"),
    ".json": ("application/json", "text/plain"),
    ".epub": ("application/epub+zip", "application/zip"),
    ".png": ("image/png",),
    ".jpg": ("image/jpeg",),
    ".jpeg": ("image/jpeg",),
}

# Reject path separators, traversal and control characters in stored names.
_UNSAFE_NAME = re.compile(r"[\x00-\x1f\x7f/\\]")


def sanitize_filename(name: str) -> str:
    """Strip directory components and control characters from an upload name."""
    name = (name or "").strip()
    if not name:
        raise ValidationError(_("File name is required."))
    # Take the basename only: defeats "../../etc/passwd" and "C:\\evil.pdf".
    name = name.replace("\\", "/").split("/")[-1]
    name = _UNSAFE_NAME.sub("", name)
    name = name.lstrip(".") or "unnamed"
    if len(name) > 255:
        # Do not name this `_`: it would shadow the gettext alias for the
        # whole function and break every error message below.
        stem, _dot, ext = name.rpartition(".")
        name = f"{stem[: 250 - len(ext)]}.{ext}" if ext else name[:255]
    return name


def validate_upload(uploaded_file) -> tuple[str, str]:
    """Validate an uploaded file.

    Returns the sanitized name and resolved MIME type, or raises ValidationError.
    """
    config = get_config().ingestion
    name = sanitize_filename(uploaded_file.name)

    max_bytes = config.max_file_size_mb * 1024 * 1024
    if uploaded_file.size > max_bytes:
        raise ValidationError(
            _("File exceeds the %(limit)d MB limit.") % {"limit": config.max_file_size_mb}
        )
    if uploaded_file.size == 0:
        raise ValidationError(_("File is empty."))

    extension = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            _("Unsupported file type '%(ext)s'. Allowed: %(allowed)s")
            % {"ext": extension or "none", "allowed": ", ".join(sorted(ALLOWED_EXTENSIONS))}
        )

    # Sniff the real content type; a .pdf that is actually a zip bomb is rejected.
    declared = (getattr(uploaded_file, "content_type", "") or "").split(";")[0].strip()
    sniffed = _sniff_mime(uploaded_file)
    allowed = ALLOWED_EXTENSIONS[extension]

    resolved = sniffed or declared or allowed[0]
    if sniffed and sniffed not in allowed and not _is_benign_text_mismatch(sniffed, allowed):
        raise ValidationError(
            _("File content (%(sniffed)s) does not match extension '%(ext)s'.")
            % {"sniffed": sniffed, "ext": extension}
        )
    if extension == ".pdf":
        validate_pdf_readable(uploaded_file)
    return name, resolved


def validate_pdf_readable(uploaded_file) -> None:
    """Reject encrypted or structurally empty PDFs before they reach Celery."""
    try:
        from pypdf import PdfReader

        uploaded_file.seek(0)
        reader = PdfReader(uploaded_file)
        if reader.is_encrypted:
            raise ValidationError(
                _("This PDF is password-protected. Remove the password and re-upload.")
            )
        if len(reader.pages) == 0:
            raise ValidationError(_("This PDF has no pages."))
    except ValidationError:
        raise
    except Exception:
        # A malformed PDF is still allowed to reach extraction, which can
        # provide the durable document-level error and retry path.
        logger.debug("Pre-flight PDF check inconclusive.", exc_info=True)
    finally:
        try:
            uploaded_file.seek(0)
        except Exception:
            pass


def _sniff_mime(uploaded_file) -> str:
    """Best-effort magic-byte detection. Returns "" when libmagic is missing."""
    try:
        import magic
    except Exception:
        return ""
    try:
        head = uploaded_file.read(4096)
        uploaded_file.seek(0)
        return magic.from_buffer(head, mime=True) or ""
    except Exception:
        try:
            uploaded_file.seek(0)
        except Exception:
            pass
        return ""


def _is_benign_text_mismatch(sniffed: str, allowed: tuple[str, ...]) -> bool:
    """libmagic reports most text formats as text/plain; that is not an attack."""
    text_like = {"text/plain", "text/csv", "text/markdown", "text/html", "application/csv"}
    return sniffed in text_like and any(a in text_like for a in allowed)


# --- SSRF guard -----------------------------------------------------------

_BLOCKED_PORTS = {22, 23, 25, 3306, 5432, 6379, 9000, 11211, 27017}


def validate_public_url(raw_url: str) -> str:
    """Reject URLs that point at private, loopback or link-local addresses.

    Without this, "ingest this URL" is a server-side request forgery primitive
    against cloud metadata endpoints and internal services (Qdrant, MinIO, the
    database) that share this network.
    """
    url = (raw_url or "").strip()
    if not url:
        raise ValidationError(_("URL is required."))

    parsed = urlparse(url)
    allowed_schemes = {s.strip().lower() for s in get_config().ingestion.allowed_url_schemes.split(",")}
    if parsed.scheme.lower() not in allowed_schemes:
        raise ValidationError(
            _("URL scheme '%(scheme)s' is not allowed.") % {"scheme": parsed.scheme}
        )
    if not parsed.hostname:
        raise ValidationError(_("URL has no host."))
    if parsed.port and parsed.port in _BLOCKED_PORTS:
        raise ValidationError(_("Port %(port)d is not allowed.") % {"port": parsed.port})

    # Resolve every A/AAAA record: a public name can still resolve to 169.254.169.254.
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValidationError(
            _("Could not resolve host '%(host)s'.") % {"host": parsed.hostname}
        ) from exc

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise ValidationError(
                _("URL resolves to a non-public address (%(ip)s).") % {"ip": address}
            )
    return url
