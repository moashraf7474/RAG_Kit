"""Page-summary enhancement.

Ported from `admin_api_lib/impl/information_enhancer/page_summary_enhancer.py`
and `admin_api_lib/impl/summarizer/langchain_summarizer.py`, collapsed into one
module and stripped of the DI container.

Summaries are indexed as SUMMARY chunks whose `related` field points at the
chunks they were built from. At retrieval time a summary hit expands into those
underlying chunks — that is what makes "what does this document say about X"
work when no single chunk contains the answer.
"""

from __future__ import annotations

import asyncio
import logging
from hashlib import sha256

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag.conf import get_config
from rag.llm import get_chat_model
from rag.prompts import SUMMARIZE_PROMPT
from rag.usage import record_usage_async

logger = logging.getLogger(__name__)

DEFAULT_PAGE = "1"


async def add_summaries(
    documents: list[Document],
    *,
    callbacks: list | None = None,
    organization_id: str | None = None,
) -> list[Document]:
    """Return SUMMARY documents for the given chunks, one per page group.

    Never raises: a failed summary degrades retrieval quality but must not fail
    an ingest that otherwise succeeded.
    """
    settings = get_config().summarizer
    if not settings.enabled:
        return []

    relevant = [
        document
        for document in documents
        if document.metadata.get("type") not in ("SUMMARY", "IMAGE")
    ]
    if not relevant:
        return []

    groups = _group_by_page(relevant)
    semaphore = asyncio.Semaphore(settings.maximum_concurrency)

    async def summarize(group: list[Document]) -> Document | None:
        async with semaphore:
            try:
                return await _summarize_group(
                    group, callbacks=callbacks, organization_id=organization_id
                )
            except Exception:
                logger.warning("Summary failed for a page group; skipping.", exc_info=True)
                return None

    results = await asyncio.gather(*(summarize(group) for group in groups))
    summaries = [result for result in results if result is not None]
    logger.info("Generated %d page summaries from %d chunks.", len(summaries), len(relevant))
    return summaries


def _group_by_page(documents: list[Document]) -> list[list[Document]]:
    """Group chunks into summary units. Ported from `_group_key` upstream."""
    ordered_keys: list[tuple] = []
    groups: dict[tuple, list[Document]] = {}

    for document in documents:
        document_url = document.metadata.get("document_url")
        page = document.metadata.get("page") or DEFAULT_PAGE
        # Paged formats (PDF/Office) summarise per page; web sources per URL.
        if page and page != "Unknown Title":
            key = ("page", document_url, page)
        elif document_url:
            key = ("document_url", document_url)
        else:
            key = ("page", DEFAULT_PAGE)

        if key not in groups:
            ordered_keys.append(key)
            groups[key] = []
        groups[key].append(document)

    return [groups[key] for key in ordered_keys]


async def _summarize_group(
    group: list[Document], *, callbacks: list | None, organization_id: str | None
) -> Document:
    full_content = " ".join(document.page_content for document in group)
    summary_text = await _summarize_text(
        full_content, callbacks=callbacks, organization_id=organization_id
    )

    metadata = {
        key: value
        for key, value in group[0].metadata.items()
        if key not in ("base64_image", "score")
    }
    metadata["id"] = sha256(full_content.encode("utf-8")).hexdigest()
    metadata["type"] = "SUMMARY"
    metadata["related"] = sorted(
        {document.metadata["id"] for document in group} | set(metadata.get("related") or [])
    )
    return Document(page_content=summary_text, metadata=metadata)


async def _summarize_text(
    text: str, *, callbacks: list | None, organization_id: str | None
) -> str:
    """Map-reduce summarisation for text longer than the model's comfort zone."""
    settings = get_config().summarizer
    chain = SUMMARIZE_PROMPT | get_chat_model()
    config = {"callbacks": callbacks or []}

    if len(text) <= settings.maximum_input_size:
        response = await chain.ainvoke({"text": text}, config=config)
        await record_usage_async(
            organization_id=organization_id, operation="ingest", response=response
        )
        return _as_text(response)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.maximum_input_size, chunk_overlap=0
    )
    parts = splitter.split_text(text)
    logger.debug("Summarising %d chunks before reducing.", len(parts))

    semaphore = asyncio.Semaphore(settings.maximum_concurrency)

    async def summarize_part(part: str) -> str:
        async with semaphore:
            response = await chain.ainvoke({"text": part}, config=config)
            await record_usage_async(
                organization_id=organization_id, operation="ingest", response=response
            )
            return _as_text(response)

    partials = await asyncio.gather(*(summarize_part(part) for part in parts))
    merged = " ".join(partials)

    # Reduce step: one more pass so the result reads as a single summary.
    response = await chain.ainvoke({"text": merged[: settings.maximum_input_size]}, config=config)
    await record_usage_async(
        organization_id=organization_id, operation="ingest", response=response
    )
    return _as_text(response)


def _as_text(response) -> str:
    content = getattr(response, "content", response)
    return content.strip() if isinstance(content, str) else str(content).strip()
