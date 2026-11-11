"""Composite retrieval.

Ported from `rag_core_api/impl/retriever/composite_retriever.py` and
`retriever_quark.py`, preserving the pipeline exactly:

    fan out one search per content type (concurrently)
      -> expand SUMMARY hits into their related chunks
        -> drop the summaries themselves
          -> deduplicate by chunk id
            -> early prune to total_k
              -> rerank to reranker_k

One hard change from upstream: every search carries an `owner_id` filter.
Upstream ran single-tenant behind basic auth; here documents belong to users,
so tenant isolation is enforced in the query itself rather than after the fact.
"""

from __future__ import annotations
from rag.conf import RetrieverSettings

import asyncio
import logging

from langchain_core.documents import Document

from rag.conf import get_config
from qdrant_client.http import models

from rag.vectordb import asearch, collection_available, get_documents_by_ids

logger = logging.getLogger(__name__)


class NoOrEmptyCollectionError(RuntimeError):
    """Raised when the vector collection is missing or empty."""


def _quarks() -> list[dict]:
    """Per-content-type search budgets. Upstream calls these 'retriever quarks'."""
    settings: RetrieverSettings = get_config().retriever
    return [
        {"type": "TEXT", "k": settings.k_documents, "threshold": settings.threshold},
        {"type": "TABLE", "k": settings.table_k_documents, "threshold": settings.table_threshold},
        {"type": "SUMMARY", "k": settings.summary_k_documents, "threshold": settings.summary_threshold},
        {"type": "IMAGE", "k": settings.image_k_documents, "threshold": settings.image_threshold},
    ]


async def retrieve(
    query: str,
    *,
    organization_id: str | None = None,
    user_id: int | None = None,
    is_org_admin: bool = False,
    document_id: str | None = None,
    owner_id: int | None = None,
) -> list[Document]:
    """Retrieve chunks scoped to an organization and document visibility."""
    if not collection_available():
        raise NoOrEmptyCollectionError()

    if organization_id is None:
        if owner_id is None:
            raise ValueError("organization_id is required")
        base_filter = {"owner_id": owner_id}
        visibility_filter = None
    else:
        base_filter = {"organization_id": str(organization_id)}
        visibility_filter = None
        if not is_org_admin:
            if user_id is None:
                raise ValueError("user_id is required for non-admin retrieval")
            visibility_filter = models.Filter(
                should=[
                    models.FieldCondition(
                        key="metadata.is_private",
                        match=models.MatchValue(value=False),
                    ),
                    models.FieldCondition(
                        key="metadata.uploaded_by_id",
                        match=models.MatchValue(value=user_id),
                    ),
                ]
            )
    if document_id:
        base_filter["document_id"] = document_id

    async def search(quark: dict) -> list[Document]:
        try:
            search_kwargs = {
                "k": quark["k"],
                "score_threshold": quark["threshold"],
                "filter_kwargs": {**base_filter, "type": quark["type"]},
            }
            if visibility_filter is not None:
                search_kwargs["extra_must"] = [visibility_filter]
            return await asearch(query, **search_kwargs)
        except Exception:
            # One failing content type must not sink the whole query.
            logger.warning("Retriever quark '%s' failed.", quark["type"], exc_info=True)
            return []

    groups = await asyncio.gather(*(search(quark) for quark in _quarks()))
    results = [document for group in groups for document in group]

    results = _expand_summaries(results)
    results = _remove_duplicates(results)
    results = _early_prune(results)
    return await _rerank(results, query)


def _expand_summaries(results: list[Document]) -> list[Document]:
    """Replace SUMMARY hits with the chunks they summarise.

    Ported from `CompositeRetriever._use_summaries`.
    """
    summaries = [d for d in results if d.metadata.get("type") == "SUMMARY"]
    if not summaries:
        return results

    try:
        existing_ids = {d.metadata.get("id") for d in results}
        missing: set[str] = set()
        for summary in summaries:
            for related_id in summary.metadata.get("related") or []:
                if related_id and related_id not in existing_ids:
                    missing.add(related_id)

        if missing:
            expanded = get_documents_by_ids(list(missing))
            results = results + expanded
            logger.debug(
                "Summary expansion added %d chunks from %d summaries.",
                len(expanded),
                len(summaries),
            )
    except Exception:
        logger.warning("Summary expansion failed; continuing without it.", exc_info=True)

    # Summaries are a retrieval aid, never a citation.
    return [d for d in results if d.metadata.get("type") != "SUMMARY"]


def _remove_duplicates(documents: list[Document]) -> list[Document]:
    seen: set[str] = set()
    unique: list[Document] = []
    for document in documents:
        document_id = document.metadata.get("id")
        if document_id not in seen:
            seen.add(document_id)
            unique.append(document)
    return unique


def _early_prune(documents: list[Document]) -> list[Document]:
    """Cap candidates before the (more expensive) reranker sees them."""
    total_k = get_config().retriever.total_k_documents
    if total_k is None or len(documents) <= total_k:
        return documents
    if all("score" in d.metadata for d in documents):
        documents = sorted(documents, key=lambda d: d.metadata["score"], reverse=True)
    return documents[:total_k]


async def _rerank(documents: list[Document], query: str) -> list[Document]:
    settings = get_config().reranker
    if not settings.enabled:
        return documents
    try:
        from rag.rerank import rerank

        return await rerank(documents, query)
    except Exception:
        # Fail soft, exactly as upstream does: unreranked beats no answer.
        logger.warning("Reranker failed; returning unreranked results.", exc_info=True)
        results = sorted(
            (d for d in documents if "score" in d.metadata),
            key=lambda d: d.metadata["score"],
            reverse=True,
        )
        return results[: settings.k_documents] or documents
