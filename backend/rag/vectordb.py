"""Qdrant hybrid vector store.

Ported from `rag_core_api/impl/vector_databases/qdrant_database.py`, with the
upstream `from_documents` re-assignment replaced by an explicit
create-collection-once + `add_documents` flow (the upstream version silently
rebuilt the store on every upload, which is not safe for concurrent ingests).

Chunk metadata contract (kept identical to upstream so ports stay faithful):
    id           str  - sha256 content hash, the point's stable identity
    document_id  str  - owning Document UUID, used for deletes
    organization_id str - organization UUID, enforced as a hard retrieval filter
    uploaded_by_id int - uploader id, used for private-document visibility
    is_private  bool - whether only the uploader and org admins may retrieve it
    type         str  - ContentType: TEXT | TABLE | IMAGE | SUMMARY
    related      list - ids of sibling chunks (summaries point at their pages)
    document_url str  - pre-signed link back to the original file
    page         any  - page number or page title
"""

from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.documents import Document
from qdrant_client import QdrantClient
from qdrant_client.http import models
from qdrant_client.models import Distance, VectorParams

from rag.conf import get_config
from rag.llm import get_embedder, get_sparse_embedder

logger = logging.getLogger(__name__)

CONTENT_TYPES = ("TEXT", "TABLE", "IMAGE", "SUMMARY")
_FINGERPRINT_POINT_ID = "00000000-0000-5000-8000-000000000001"
_FINGERPRINT_METADATA_KEY = "_embedder"
_FINGERPRINT_CONTENT_ID = "__rag_system_embedder__"


class EmbedderMismatchError(RuntimeError):
    """Raised when the configured embedder cannot safely use the collection."""


@lru_cache(maxsize=1)
def get_client() -> QdrantClient:
    settings = get_config().vector_db
    return QdrantClient(
        url=settings.url,
        api_key=settings.api_key.get_secret_value() or None,
        timeout=int(settings.timeout),
        prefer_grpc=settings.prefer_grpc,
    )


def _retrieval_mode():
    from langchain_qdrant import RetrievalMode

    mode = get_config().vector_db.retrieval_mode.upper()
    if mode == "HYBRID" and get_sparse_embedder() is None:
        logger.warning("HYBRID requested but sparse embedder unavailable; using DENSE.")
        return RetrievalMode.DENSE
    return getattr(RetrievalMode, mode, RetrievalMode.DENSE)


def ensure_collection() -> None:
    """Create and validate the collection before any vector operation.

    Qdrant validates vector width, but it cannot know whether two models with
    the same width produce comparable vectors.  A reserved point stores the
    configured embedder identity so changing models becomes an explicit,
    recoverable error instead of silent retrieval corruption.
    """
    settings = get_config().vector_db
    client = get_client()
    name = settings.collection_name

    if not client.collection_exists(name):
        vectors_config = {
            "dense": VectorParams(
                size=get_config().embedder.dimensions, distance=Distance.COSINE
            )
        }
        sparse_config = (
            {"sparse": models.SparseVectorParams(index=models.SparseIndexParams())}
            if get_sparse_embedder() is not None
            else None
        )
        client.create_collection(
            collection_name=name,
            vectors_config=vectors_config,
            sparse_vectors_config=sparse_config,
        )
        _store_fingerprint(name, _embedder_fingerprint())
        logger.info("Created Qdrant collection '%s'.", name)
    else:
        _verify_fingerprint(name, _embedder_fingerprint())

    # Payload indexes make the per-type / per-owner filters cheap instead of a scan.
    for field in (
        "metadata.type",
        "metadata.document_id",
        "metadata.organization_id",
        "metadata.uploaded_by_id",
        "metadata.is_private",
        "metadata.owner_id",
        "metadata.id",
    ):
        try:
            client.create_payload_index(
                collection_name=name,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        except Exception:
            # Already exists — Qdrant has no "create if not exists" for indexes.
            logger.debug("Payload index %s already present.", field)


@lru_cache(maxsize=1)
def get_vectorstore():
    """Long-lived QdrantVectorStore bound to the configured collection."""
    from langchain_qdrant import QdrantVectorStore

    ensure_collection()
    settings = get_config().vector_db
    kwargs = {
        "client": get_client(),
        "collection_name": settings.collection_name,
        "embedding": get_embedder(),
        "retrieval_mode": _retrieval_mode(),
        "vector_name": "dense",
    }
    sparse = get_sparse_embedder()
    if sparse is not None:
        kwargs["sparse_embedding"] = sparse
        kwargs["sparse_vector_name"] = "sparse"
    return QdrantVectorStore(**kwargs)


def build_filter(
    filter_kwargs: dict | None, extra_must: list[models.Filter] | None = None
) -> models.Filter | None:
    """Translate a flat dict into a Qdrant `must` filter over `metadata.*`."""
    if not filter_kwargs and not extra_must:
        return None
    must = [
        models.FieldCondition(key=f"metadata.{key}", match=models.MatchValue(value=value))
        for key, value in (filter_kwargs or {}).items()
    ]
    must.extend(extra_must or [])
    return models.Filter(must=must)


def collection_available() -> bool:
    """True when the collection exists and holds at least one point."""
    settings = get_config().vector_db
    client = get_client()
    try:
        if not client.collection_exists(settings.collection_name):
            return False
        ensure_collection()
        points, _ = client.scroll(
            collection_name=settings.collection_name,
            scroll_filter=models.Filter(
                must_not=[
                    models.FieldCondition(
                        key="metadata.id",
                        match=models.MatchValue(value=_FINGERPRINT_CONTENT_ID),
                    )
                ]
            ),
            limit=1,
            with_payload=False,
            with_vectors=False,
        )
        return bool(points)
    except EmbedderMismatchError:
        raise
    except Exception:
        logger.exception("Could not determine collection availability.")
        return False


async def asearch(
    query: str,
    *,
    k: int,
    score_threshold: float,
    filter_kwargs: dict | None = None,
    extra_must: list[models.Filter] | None = None,
) -> list[Document]:
    """Similarity search returning documents with a `score` in metadata."""
    store = get_vectorstore()
    try:
        results = await store.asimilarity_search_with_score(
            query, k=k, filter=build_filter(filter_kwargs, extra_must)
        )
    except Exception:
        logger.exception("Vector search failed for query=%r filters=%s", query, filter_kwargs)
        raise

    documents: list[Document] = []
    for doc, score in results:
        if score < score_threshold:
            continue
        doc.metadata["score"] = score
        documents.append(doc)
    return documents


def get_documents_by_ids(ids: list[str]) -> list[Document]:
    """Batch-fetch chunks by their `metadata.id`.

    Upstream looped one scroll per id; this issues a single `should` (OR) scroll.
    """
    if not ids:
        return []
    settings = get_config().vector_db
    points, _ = get_client().scroll(
        collection_name=settings.collection_name,
        scroll_filter=models.Filter(
            should=[
                models.FieldCondition(key="metadata.id", match=models.MatchValue(value=i))
                for i in ids
            ]
        ),
        limit=max(len(ids), 1),
        with_payload=True,
    )
    return [
        Document(
            page_content=point.payload.get("page_content", ""),
            metadata=point.payload.get("metadata", {}),
        )
        for point in points
    ]


def upload(documents: list[Document]) -> None:
    """Upsert chunks. Point ids are derived from `metadata.id`, so re-ingesting
    the same content overwrites rather than duplicates."""
    if not documents:
        return
    ensure_collection()
    fingerprint = _embedder_fingerprint()
    for document in documents:
        # Keep the identity beside every normal point as well as on the
        # reserved collection marker. This makes future diagnostics possible
        # without depending on the marker surviving a manual Qdrant edit.
        document.metadata.setdefault(_FINGERPRINT_METADATA_KEY, fingerprint)
    ids = [_point_id(doc.metadata["id"]) for doc in documents]
    get_vectorstore().add_documents(documents, ids=ids)


def delete_by(**filter_kwargs) -> None:
    """Delete every point matching the given metadata equality filters."""
    settings = get_config().vector_db
    qdrant_filter = build_filter(filter_kwargs)
    if qdrant_filter is None:
        raise ValueError("Refusing to delete without a filter.")
    get_client().delete(
        collection_name=settings.collection_name,
        points_selector=models.FilterSelector(filter=qdrant_filter),
    )


def _point_id(content_hash: str) -> str:
    """Qdrant point ids must be UUIDs or ints; map the sha256 to a stable UUID."""
    import uuid

    return str(uuid.UUID(content_hash[:32]))


def _embedder_fingerprint() -> dict[str, object]:
    """Return the identity that must remain stable for a vector collection."""
    settings = get_config().embedder
    provider = settings.provider.lower()
    model = settings.local_model if provider == "local" else settings.model
    return {
        "provider": provider,
        "model": model,
        "dimensions": settings.dimensions,
        "base_url": settings.base_url or "",
    }


def _store_fingerprint(name: str, fingerprint: dict[str, object]) -> None:
    """Write the reserved identity point after a collection is created."""
    client = get_client()
    dimensions = int(fingerprint["dimensions"])
    client.upsert(
        collection_name=name,
        points=[
            models.PointStruct(
                id=_FINGERPRINT_POINT_ID,
                vector={"dense": [0.0] * dimensions},
                payload={
                    _FINGERPRINT_METADATA_KEY: fingerprint,
                    "metadata": {"id": _FINGERPRINT_CONTENT_ID},
                },
            )
        ],
        wait=True,
    )


def _verify_fingerprint(name: str, expected: dict[str, object]) -> None:
    """Refuse to use a collection built by another or unknown embedder."""
    client = get_client()
    collection = client.get_collection(name)
    vectors = collection.config.params.vectors
    dense = vectors.get("dense") if isinstance(vectors, dict) else vectors
    actual_size = getattr(dense, "size", None)
    expected_size = expected["dimensions"]
    if actual_size != expected_size:
        raise EmbedderMismatchError(
            f"Collection '{name}' has {actual_size}-dimensional vectors but "
            f"the configured embedder requires {expected_size} "
            f"(model={expected['model']}). Rebuild it with:\n"
            "    uv run python manage.py reembed_all --confirm"
        )

    points = client.retrieve(
        collection_name=name,
        ids=[_FINGERPRINT_POINT_ID],
        with_payload=True,
        with_vectors=False,
    )
    if not points:
        raise EmbedderMismatchError(
            f"Collection '{name}' has no embedder fingerprint. It may contain "
            "legacy vectors and cannot be used safely. Rebuild it with:\n"
            "    uv run python manage.py reembed_all --confirm"
        )
    actual = points[0].payload.get(_FINGERPRINT_METADATA_KEY)
    if actual != expected:
        raise EmbedderMismatchError(
            f"Collection '{name}' was built with embedder {actual!r}, but the "
            f"configured embedder is {expected!r}. Rebuild it with:\n"
            "    uv run python manage.py reembed_all --confirm"
        )
