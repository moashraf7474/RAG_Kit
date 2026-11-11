"""Chunking and metadata assembly.

Ported from `admin_api_lib/impl/chunker/text_chunker.py`. Uses structure-aware
splitting when the content has markdown headers, falling back to the recursive
character splitter for unstructured text. Tables are never split.
"""

from __future__ import annotations

import logging
import re
from hashlib import sha256
from typing import Any

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from rag.conf import get_config
from rag.extract import Piece

logger = logging.getLogger(__name__)

_MARKDOWN_HEADERS = [
    ("#", "Header 1"),
    ("##", "Header 2"),
    ("###", "Header 3"),
]

_RECURSIVE_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]


def get_splitter() -> RecursiveCharacterTextSplitter:
    settings = get_config().chunker
    return RecursiveCharacterTextSplitter(
        chunk_size=settings.max_size,
        chunk_overlap=settings.overlap,
        length_function=len,
        separators=list(_RECURSIVE_SEPARATORS),
    )


def _looks_like_markdown(text: str) -> bool:
    lines = text.splitlines()
    for line in lines[:30]:
        stripped = line.strip()
        if stripped.startswith("#"):
            return True
    return False


def _heading_path(metadata: dict[str, Any]) -> str:
    parts = [metadata[key] for key in ("Header 1", "Header 2", "Header 3") if key in metadata]
    return " > ".join(parts) if parts else ""


_HEADING_LIKE = re.compile(r"^(\d+[.)]\s+\S|#{1,6}\s+\S)")


def _looks_like_bare_heading(text: str) -> bool:
    """True for a short, unpunctuated label like "8. Model Providers".

    A piece like this carries no answerable content on its own — it only
    makes sense attached to whatever follows it, TEXT or not (a table's
    caption is exactly this shape). Deliberately narrow: only a numbered
    ("8. Model Providers") or markdown ("## Model Providers") heading
    qualifies, so an ordinary short sentence ("Some text before") is never
    mistaken for one and merged into unrelated content.
    """
    text = text.strip()
    if not text or len(text) >= 80 or text[-1] in ".!?:":
        return False
    return bool(_HEADING_LIKE.match(text))


def _merge_fragmented_pieces(pieces: list[Piece], settings) -> list[Piece]:
    """Coalesce consecutive small TEXT pieces on the same page into one block.

    Docling (and some other extractors) emit one piece per paragraph/heading —
    a numbered heading like "4. Key Concepts" arrives as its own 33-character
    piece, immediately followed by the paragraph that actually explains it.
    Chunking each piece independently turns that heading into a permanent,
    contextless chunk that nothing ever answers from. Joining consecutive TEXT
    pieces on the same page (up to the configured chunk size) keeps a heading
    glued to the text beneath it, so retrieval returns something a model can
    actually use instead of a lone label.

    TABLE and IMAGE pieces are never merged: a table's structure and an
    image's caption must stay atomic.
    """
    merged: list[Piece] = []
    buffer: list[Piece] = []
    buffer_len = 0

    def flush() -> None:
        nonlocal buffer, buffer_len
        if buffer:
            merged.append(
                Piece(
                    content="\n\n".join(p.content for p in buffer),
                    content_type="TEXT",
                    page=buffer[0].page,
                    metadata=buffer[0].metadata,
                )
            )
        buffer = []
        buffer_len = 0

    for piece in pieces:
        if piece.content_type != "TEXT":
            # A bare heading immediately preceding a table (or image) is what
            # that item's caption — glue it onto the item's page/content
            # rather than stranding it as a standalone chunk.
            if (
                buffer
                and len(buffer) == 1
                and _looks_like_bare_heading(buffer[0].content)
            ):
                heading = buffer[0]
                piece = Piece(
                    content=f"{heading.content}\n\n{piece.content}",
                    content_type=piece.content_type,
                    page=piece.page,
                    metadata=piece.metadata,
                )
                buffer = []
                buffer_len = 0
            else:
                flush()
            merged.append(piece)
            continue

        # A same-page TEXT piece joins the buffer as long as the combined
        # block would not exceed the chunk size the splitter targets anyway.
        joins_buffer = buffer and buffer[-1].page == piece.page
        projected = buffer_len + len(piece.content) + 2
        if joins_buffer and projected <= settings.max_size:
            buffer.append(piece)
            buffer_len = projected
        else:
            flush()
            buffer = [piece]
            buffer_len = len(piece.content)

    flush()
    return merged


# A section whose text is nothing but its own markdown heading line(s) — no
# body ever followed it in the source. Deliberately does NOT match a heading
# followed by a short-but-real line of content: only an empty tail qualifies.
_HEADING_ONLY_SECTION = re.compile(r"^(#{1,6}\s+.+\n?)+$")


def _merge_undersized_sections(
    sections: list[tuple[str, dict]], settings
) -> list[tuple[str, dict]]:
    """Fold a heading-only markdown section into its neighbour.

    `MarkdownHeaderTextSplitter` keeps the heading in `page_content`
    (`strip_headers=False`), so a trailing heading with nothing after it in
    the source — "# 12. Quick Reference: Ports" at the end of a document —
    becomes its own section that is just that heading line and no body. Merge
    it into the previous section, since a heading with no body almost always
    continues what came before; fall back to the next section only when it is
    the very first section and there is nothing earlier to extend.

    A section that has real (if short) body text under its heading is left
    alone — its `section_path` is a meaningful retrieval/citation signal on
    its own and must not be silently absorbed into a neighbour.
    """
    if not sections:
        return sections

    merged: list[tuple[str, dict]] = []
    for text, extra in sections:
        if (
            merged
            and _HEADING_ONLY_SECTION.match(text)
            and len(merged[-1][0]) + len(text) + 2 <= settings.max_size
        ):
            prev_text, prev_extra = merged[-1]
            merged[-1] = (f"{prev_text}\n\n{text}", prev_extra)
        else:
            merged.append((text, extra))

    if (
        len(merged) > 1
        and _HEADING_ONLY_SECTION.match(merged[0][0])
        and len(merged[0][0]) + len(merged[1][0]) + 2 <= settings.max_size
    ):
        first_text, _ = merged[0]
        next_text, next_extra = merged[1]
        merged[1] = (f"{first_text}\n\n{next_text}", next_extra)
        merged = merged[1:]

    return merged


def _chunk_with_structure(content: str, settings) -> list[tuple[str, dict]]:
    if _looks_like_markdown(content):
        md_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=list(_MARKDOWN_HEADERS),
            strip_headers=False,
        )
        sections = md_splitter.split_text(content)

        result: list[tuple[str, dict]] = []
        for doc in sections:
            text = doc.page_content.strip()
            if not text:
                continue

            path = _heading_path(doc.metadata)
            extra = {"section_path": path} if path else {}

            if len(text) > settings.max_size:
                sub_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=settings.max_size,
                    chunk_overlap=settings.overlap,
                    length_function=len,
                    separators=list(_RECURSIVE_SEPARATORS),
                )
                for sub in sub_splitter.split_text(text):
                    result.append((sub, extra))
            else:
                result.append((text, extra))

        return _merge_undersized_sections(result, settings)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.max_size,
        chunk_overlap=settings.overlap,
        length_function=len,
        separators=list(_RECURSIVE_SEPARATORS),
    )
    return [(text, {}) for text in splitter.split_text(content)]


def pieces_to_documents(
    pieces: list[Piece],
    *,
    document_id: str,
    organization_id: str | None = None,
    owner_id: int | None = None,
    document_name: str,
    storage_key: str = "",
    document_url: str = "",
    uploaded_by_id: int | None = None,
    is_private: bool = False,
) -> list[Document]:
    """Chunk extracted pieces into LangChain documents carrying full metadata.

    Tables are never split: a half table is worse than a long one.
    Markdown-like content is split on headers first, then recursively for
    oversized sections, preserving the heading hierarchy as `section_path`.
    """
    settings = get_config().chunker
    documents: list[Document] = []
    # owner_id remains a compatibility input for callers/tests that construct
    # chunks directly. Production ingestion always supplies organization_id.
    if organization_id is None:
        if owner_id is None:
            raise ValueError("organization_id is required")
        organization_id = str(owner_id)
        uploaded_by_id = uploaded_by_id or owner_id

    for piece in _merge_fragmented_pieces(pieces, settings):
        base_metadata = {
            "document_id": document_id,
            "organization_id": str(organization_id),
            "uploaded_by_id": uploaded_by_id,
            "is_private": is_private,
            "document_name": document_name,
            "storage_key": storage_key,
            "document_url": document_url,
            "type": piece.content_type,
            "page": piece.page or "",
            "related": [],
        }
        if owner_id is not None:
            # Preserve metadata compatibility for old local test fixtures and
            # vectors until the collection is re-embedded under Phase 1.
            base_metadata["owner_id"] = owner_id

        if piece.content_type == "TABLE":
            if not any(character.isalnum() for character in piece.content):
                continue
            chunks = [(piece.content, {})]
        else:
            chunks = _chunk_with_structure(piece.content, settings)

        for position, (text, extra) in enumerate(chunks):
            text = text.strip()
            if not text:
                continue
            metadata = dict(base_metadata)
            metadata.update(extra)
            metadata["id"] = sha256(
                f"{document_id}:{piece.content_type}:{piece.page}:{position}:{text}".encode()
            ).hexdigest()
            documents.append(Document(page_content=text, metadata=metadata))

    logger.info("Chunked %d pieces into %d documents.", len(pieces), len(documents))
    return documents
