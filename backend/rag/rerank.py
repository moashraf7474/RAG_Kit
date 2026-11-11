"""Cross-encoder reranking with FlashRank.

Ported from `rag_core_api/impl/reranking/flashrank_reranker.py`, including the
metadata restoration workaround — LangChain's FlashRank wrapper drops metadata,
and citations are worthless without it.

FlashRank is ONNX/CPU, so it adds no GPU requirement.
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

from langchain_core.documents import Document

from rag.conf import get_config

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_ranker():
    from flashrank import Ranker

    settings = get_config().reranker
    return Ranker(model_name=settings.model, cache_dir="/tmp/flashrank")


async def rerank(documents: list[Document], query: str) -> list[Document]:
    """Rerank by cross-encoder relevance and keep the top `k_documents`."""
    if not documents:
        return []
    # FlashRank is synchronous CPU work; keep it off the event loop.
    return await asyncio.to_thread(_rerank_sync, documents, query)


def _rerank_sync(documents: list[Document], query: str) -> list[Document]:
    from flashrank import RerankRequest

    settings = get_config().reranker
    passages = [
        {"id": index, "text": document.page_content}
        for index, document in enumerate(documents)
    ]
    ranked = _get_ranker().rerank(RerankRequest(query=query, passages=passages))

    results: list[Document] = []
    for entry in ranked:
        score = float(entry.get("score", 0.0))
        original = documents[int(entry["id"])]
        # Restore the metadata FlashRank drops, then attach the new score.
        original.metadata = {**original.metadata, "relevance_score": score}
        results.append(original)
        if len(results) >= settings.k_documents:
            break

    if not results:
        return []

    # Anchor rule: the best match is the query's anchor. When even the top hit
    # sits below the relevance bar the query only matched noise (e.g. a
    # butter-chicken recipe enjoying a user's marketing notes), so surface
    # none of it rather than hallucinate over irrelevant chunks.
    top_score = results[0].metadata.get("relevance_score", 0.0)
    if top_score < settings.min_relevance_score:
        logger.debug("Top reranked score below threshold; discarding %d matches.", len(results))
        return []

    # Weak-but-real matches (e.g. a Client Acquisition section queried as "how
    # we can sell") can score far below the anchor in FlashRank's sigmoid range.
    # Keep anything within a fixed ratio of the anchor: real matches survive
    # while the long noisy tail is cut.
    kept: list[Document] = []
    for document in results:
        if document.metadata.get("relevance_score", 0.0) >= top_score * settings.min_relevance_ratio:
            kept.append(document)
    return kept
