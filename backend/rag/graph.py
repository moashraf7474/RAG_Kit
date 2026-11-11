"""The chat graph.

Ported from `rag_core_api/impl/graph/chat_graph.py`, preserving the node
topology exactly, with a small-talk gate added before the main pipeline:

    START -> (small talk?) -> conversational --------------> END
                       |
                       v
         determine_language -> rephrase -> retrieve
                                            |
                               +------------+------------+
                               v                         v
                            generate                 error_node
                               |                         |
                               +-----------> END <-------+

Added on top of upstream: `astream_answer`, which streams answer tokens after
running the same retrieval pipeline, so the Next.js UI can render progressively.
"""

from __future__ import annotations

import json
import logging
import operator
import re
from enum import StrEnum
from typing import Annotated, Any, TypedDict

from langchain_core.documents import Document
from langgraph.graph import END, START, StateGraph

from rag.conf import get_config
from rag.llm import get_chat_model
from rag.storage import presigned_url
from rag.usage import record_usage_async
from rag.prompts import (
    ANSWER_GENERATION_PROMPT,
    ANSWER_GENERATION_BEGINNER_PROMPT,
    ANSWER_GENERATION_DEEP_DIVE_PROMPT,
    LANGUAGE_DETECTION_PROMPT,
    QUESTION_REPHRASING_PROMPT,
    SMALL_TALK_PROMPT,
)
from rag.retrieval import NoOrEmptyCollectionError, retrieve

logger = logging.getLogger(__name__)


class GraphNodeNames(StrEnum):
    DETERMINE_LANGUAGE = "determine_language"
    REPHRASE = "rephrase"
    RETRIEVE = "retrieve"
    GENERATE = "generate"
    ERROR_NODE = "error_node"
    CONVERSATIONAL = "conversational"


class AnswerGraphState(TypedDict, total=False):
    question: str
    language: str
    rephrased_question: str
    history: str
    organization_id: str
    user_id: int
    is_org_admin: bool
    owner_id: int
    document_id: str | None
    answer_mode: str
    documents: Annotated[list[Document], operator.add]
    answer_text: str | None
    citations: list[dict]
    finish_reason: str
    error_messages: Annotated[list[str], operator.add]
    finish_reasons: Annotated[list[str], operator.add]


def get_answer_prompt(answer_mode: str = "default"):
    """Select the answer prompt based on the requested mode."""
    mode = (answer_mode or "default").lower()
    if mode == "beginner":
        return ANSWER_GENERATION_BEGINNER_PROMPT
    if mode == "deep_dive":
        return ANSWER_GENERATION_DEEP_DIVE_PROMPT
    return ANSWER_GENERATION_PROMPT


def format_history(messages: list[dict]) -> str:
    """Trim and format chat history. Ported from `DefaultChatGraph.ainvoke`."""
    settings = get_config().chat_history
    if not messages:
        return ""

    history = messages[-settings.limit :]
    if settings.reverse:
        # Upstream reverses in role-pairs so the most recent exchange leads.
        pairs = list(zip(history[::2], history[1::2], strict=False))
        history = [message for pair in pairs[::-1] for message in pair]
    return "\n".join(f"{message['role']}: {message['content']}" for message in history)


def document_to_citation(document: Document) -> dict:
    """Shape a retrieved chunk for the API. Mirrors upstream's InformationPiece."""
    metadata = dict(document.metadata)
    return {
        "page_content": document.page_content,
        "type": metadata.get("type", "TEXT"),
        "document_id": metadata.get("document_id", ""),
        "document_name": metadata.get("document_name", ""),
        # Presign fresh: the signed URL is valid for ~15 minutes, so a baked one
        # is stale (host + expiry) by the time the user clicks a citation.
        "document_url": _citation_url(metadata),
        "page": metadata.get("page", ""),
        "score": metadata.get("relevance_score", metadata.get("score")),
    }


def _confidence(citations: list[dict]) -> str:
    """Map the strongest citation relevance score to a displayable band."""
    scores = [citation["score"] for citation in citations if citation.get("score") is not None]
    if not scores:
        return "unknown"
    top = max(scores)
    if top >= 0.5:
        return "high"
    if top >= 0.1:
        return "medium"
    return "low"


def _citation_url(metadata: dict) -> str:
    """A clickable link for a citation.

    Order: a fresh pre-signed URL for object-stored files, then the stable
    source URI for web sources, then whatever was stored on older chunks.
    """
    storage_key = metadata.get("storage_key") or metadata.get("document_url") or ""
    if storage_key and not storage_key.startswith("http"):
        url = presigned_url(storage_key)
        if url:
            return url
        return ""
    return metadata.get("document_url", "")


def build_context(documents: list[Document]) -> str:
    """Render retrieved chunks as numbered, delimited context.

    Numbering lets the model reference sources; the delimiters make it visible
    where untrusted document text starts and stops. When a `section_path` is
    present it is appended so the model sees the structural context.
    """
    blocks = []
    for index, document in enumerate(documents, start=1):
        name = document.metadata.get("document_name", "unknown")
        page = document.metadata.get("page", "")
        location = f"{name}, page {page}" if page else name
        section = document.metadata.get("section_path", "")
        if section:
            location = f"{location}, {section}"
        blocks.append(f"[{index}] ({location})\n{document.page_content}")
    return "\n\n---\n\n".join(blocks)


# --- small-talk gate -------------------------------------------------------


def is_small_talk(question: str) -> bool:
    """Decide whether a message is pure greeting/small talk, skipping retrieval.

    Matches the full, trimmed question against the configured regex patterns.
    An anchored match means there is no real knowledge request, so answering
    conversationally (no citations) is the right behaviour.
    """
    settings = get_config().small_talk
    if not settings.enabled:
        return False
    text = question.strip().lower().rstrip("!?.,; ")
    if not text:
        return False
    return any(
        pattern.strip() and re.fullmatch(pattern.strip(), text)
        for pattern in settings.patterns.split(",")
    )


def _route_from_start(state: AnswerGraphState) -> str:
    return (
        GraphNodeNames.CONVERSATIONAL
        if is_small_talk(state["question"])
        else GraphNodeNames.DETERMINE_LANGUAGE
    )


async def _record_query_usage(state: AnswerGraphState, response) -> None:
    await record_usage_async(
        organization_id=state.get("organization_id"),
        operation="query",
        response=response,
    )


# --- nodes ----------------------------------------------------------------


async def _determine_language_node(state: AnswerGraphState, config=None) -> dict:
    """LLM language detection with a langdetect fallback. Ported upstream."""
    question = state["question"]
    try:
        chain = LANGUAGE_DETECTION_PROMPT | get_chat_model()
        response = await chain.ainvoke({"question": question}, config=config)
        await _record_query_usage(state, response)
        content = getattr(response, "content", str(response))
        language = json.loads(content).get("language", "en")
    except Exception:
        try:
            import langdetect

            language = langdetect.detect(question)
        except Exception:
            language = "en"
    logger.debug("Detected language for %r: %s", question, language)
    return {"language": language or "en"}


async def _rephrase_node(state: AnswerGraphState, config=None) -> dict:
    """Rewrite a follow-up into a standalone query. No history means no rewrite."""
    if not state.get("history"):
        return {"rephrased_question": state["question"]}
    try:
        chain = QUESTION_REPHRASING_PROMPT | get_chat_model()
        response = await chain.ainvoke(
            {
                "question": state["question"],
                "history": state["history"],
                "language": state.get("language", "en"),
            },
            config=config,
        )
        await _record_query_usage(state, response)
        rephrased = getattr(response, "content", response)
        rephrased = rephrased.strip() if isinstance(rephrased, str) else str(rephrased).strip()
    except Exception:
        logger.warning("Rephrasing failed; using the original question.", exc_info=True)
        rephrased = ""
    return {"rephrased_question": rephrased or state["question"]}


async def _retrieve_node(state: AnswerGraphState) -> dict:
    errors = get_config().errors
    query = state.get("rephrased_question") or state["question"]
    try:
        documents = await retrieve(
            query,
            organization_id=state.get("organization_id"),
            user_id=state.get("user_id"),
            is_org_admin=state.get("is_org_admin", False),
            owner_id=state.get("owner_id"),
            document_id=state.get("document_id"),
        )
    except NoOrEmptyCollectionError:
        logger.warning("Query hit an empty collection.")
        return {
            "error_messages": [errors.no_or_empty_collection],
            "finish_reasons": ["NoOrEmptyCollectionError"],
        }
    except Exception:
        logger.exception("Vector search failed.")
        return {
            "error_messages": [errors.no_documents_message],
            "finish_reasons": ["RetrievalError"],
        }

    if not documents:
        return {
            "error_messages": [errors.no_documents_message],
            "finish_reasons": ["No documents found"],
        }
    return {"documents": documents}


async def _generate_node(state: AnswerGraphState, config=None) -> dict:
    errors = get_config().errors
    try:
        prompt = get_answer_prompt(state.get("answer_mode", "default"))
        chain = prompt | get_chat_model()
        response = await chain.ainvoke(
            {
                "question": state["question"],
                "history": state.get("history", ""),
                "context": build_context(state["documents"]),
                "language": state.get("language", "en"),
            },
            config=config,
        )
        await _record_query_usage(state, response)
    except Exception as exc:
        logger.exception("Answer generation failed.")
        # Retrieval succeeded but the model could not respond. Say so honestly
        # instead of lying that no documents were found.
        from rag.ingest import _explain

        return {
            "answer_text": f"{errors.generation_failed_message}\n\n_{_explain(exc)}_",
            "citations": [document_to_citation(d) for d in state["documents"]],
            "finish_reason": "GenerationError",
        }
    answer = getattr(response, "content", response)
    answer = answer if isinstance(answer, str) else str(answer)
    return {
        "answer_text": answer,
        "citations": [document_to_citation(d) for d in state["documents"]],
        "finish_reason": "stop",
    }


async def _error_node(state: AnswerGraphState) -> dict:
    return {
        "answer_text": " ".join(dict.fromkeys(state.get("error_messages", []))),
        "citations": [],
        "finish_reason": " ".join(dict.fromkeys(state.get("finish_reasons", []))),
    }


async def _conversational_node(state: AnswerGraphState, config=None) -> dict:
    """Answer greetings/small talk without touching retrieval or the KB.

    `reply_mode=template` returns the configured static text (zero LLM calls,
    instant, fully offline). `reply_mode=llm` asks the chat model for a short,
    language-aware reply.
    """
    settings = get_config().small_talk
    if settings.reply_mode == "llm":
        try:
            chain = SMALL_TALK_PROMPT | get_chat_model()
            response = await chain.ainvoke(
                {"question": state["question"], "language": state.get("language", "en")},
                config=config,
            )
            await _record_query_usage(state, response)
            answer = getattr(response, "content", response)
            answer = answer if isinstance(answer, str) else str(answer)
        except Exception:
            logger.warning("Small-talk LLM reply failed; using template.", exc_info=True)
            answer = settings.response
    else:
        answer = settings.response
    return {"answer_text": answer, "citations": [], "finish_reason": "stop"}


def _docs_retrieved_edge(state: AnswerGraphState) -> str:
    return GraphNodeNames.GENERATE if state.get("documents") else GraphNodeNames.ERROR_NODE


def build_graph():
    graph = StateGraph(AnswerGraphState)
    graph.add_node(GraphNodeNames.CONVERSATIONAL, _conversational_node)
    graph.add_node(GraphNodeNames.DETERMINE_LANGUAGE, _determine_language_node)
    graph.add_node(GraphNodeNames.REPHRASE, _rephrase_node)
    graph.add_node(GraphNodeNames.RETRIEVE, _retrieve_node)
    graph.add_node(GraphNodeNames.GENERATE, _generate_node)
    graph.add_node(GraphNodeNames.ERROR_NODE, _error_node)

    graph.add_conditional_edges(
        START,
        _route_from_start,
        [GraphNodeNames.CONVERSATIONAL, GraphNodeNames.DETERMINE_LANGUAGE],
    )
    graph.add_edge(GraphNodeNames.CONVERSATIONAL, END)
    graph.add_edge(GraphNodeNames.DETERMINE_LANGUAGE, GraphNodeNames.REPHRASE)
    graph.add_edge(GraphNodeNames.REPHRASE, GraphNodeNames.RETRIEVE)
    graph.add_conditional_edges(
        GraphNodeNames.RETRIEVE,
        _docs_retrieved_edge,
        [GraphNodeNames.GENERATE, GraphNodeNames.ERROR_NODE],
    )
    graph.add_edge(GraphNodeNames.GENERATE, END)
    graph.add_edge(GraphNodeNames.ERROR_NODE, END)
    return graph.compile()


_compiled_graph = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


async def answer(
    question: str,
    *,
    organization_id: str | None = None,
    user_id: int | None = None,
    is_org_admin: bool = False,
    owner_id: int | None = None,
    history: list[dict] | None = None,
    document_id: str | None = None,
    answer_mode: str = "default",
    callbacks: list | None = None,
) -> dict:
    """Run the full graph and return `{answer, citations, finish_reason}`."""
    errors = get_config().errors
    if not question.strip():
        return {
            "answer": errors.empty_message,
            "citations": [],
            "confidence": "unknown",
            "finish_reason": "empty_message",
        }

    if organization_id is None and owner_id is None:
        raise ValueError("organization_id is required")
    state: dict[str, Any] = {
        "question": question,
        "history": format_history(history or []),
        "organization_id": organization_id,
        "user_id": user_id,
        "is_org_admin": is_org_admin,
        "owner_id": owner_id,
        "document_id": document_id,
        "answer_mode": answer_mode,
        "documents": [],
        "error_messages": [],
        "finish_reasons": [],
    }
    result = await get_graph().ainvoke(state, config={"callbacks": callbacks or []})
    return {
        "answer": result.get("answer_text") or errors.no_documents_message,
        "citations": result.get("citations", []),
        "confidence": _confidence(result.get("citations", [])),
        "finish_reason": result.get("finish_reason", ""),
    }


async def astream_answer(
    question: str,
    *,
    organization_id: str | None = None,
    user_id: int | None = None,
    is_org_admin: bool = False,
    owner_id: int | None = None,
    history: list[dict] | None = None,
    document_id: str | None = None,
    answer_mode: str = "default",
    callbacks: list | None = None,
):
    """Stream the answer.

    Yields dicts: `{"type": "citations"|"token"|"done"|"error", ...}`.
    Retrieval runs to completion first so the UI can render sources immediately,
    then answer tokens stream in.
    """
    errors = get_config().errors
    if organization_id is None and owner_id is None:
        raise ValueError("organization_id is required")
    config = {"callbacks": callbacks or []}

    if not question.strip():
        yield {"type": "error", "message": errors.empty_message}
        return

    if is_small_talk(question):
        reply = await _conversational_node(
            {"question": question, "organization_id": organization_id}
        )
        yield {"type": "citations", "citations": []}
        yield {"type": "token", "token": reply["answer_text"]}
        yield {"type": "done", "finish_reason": "stop"}
        return

    formatted_history = format_history(history or [])

    try:
        language = (
            await _determine_language_node(
                {"question": question, "organization_id": organization_id}, config
            )
        )["language"]
        rephrased = (
            await _rephrase_node(
                {
                    "question": question,
                    "history": formatted_history,
                    "language": language,
                    "organization_id": organization_id,
                },
                config,
            )
        )["rephrased_question"]

        retrieval = await _retrieve_node(
            {
                "question": question,
                "rephrased_question": rephrased,
                "organization_id": organization_id,
                "user_id": user_id,
                "is_org_admin": is_org_admin,
                "owner_id": owner_id,
                "document_id": document_id,
            }
        )
    except Exception:
        logger.exception("Streaming pipeline failed before generation.")
        yield {"type": "error", "message": errors.no_documents_message}
        return

    documents = retrieval.get("documents") or []
    if not documents:
        message = " ".join(retrieval.get("error_messages") or [errors.no_documents_message])
        yield {"type": "error", "message": message}
        return

    citations = [document_to_citation(document) for document in documents]
    yield {"type": "citations", "citations": citations}

    prompt = get_answer_prompt(answer_mode)
    chain = prompt | get_chat_model()
    payload = {
        "question": question,
        "history": formatted_history,
        "context": build_context(documents),
        "language": language,
    }
    last_chunk = None
    try:
        async for chunk in chain.astream(payload, config=config):
            last_chunk = chunk
            token = getattr(chunk, "content", "")
            if token:
                yield {"type": "token", "token": token}
    except Exception:
        logger.exception("Answer streaming failed.")
        yield {"type": "error", "message": errors.generation_failed_message}
        return

    await _record_query_usage({"organization_id": organization_id}, last_chunk)
    yield {"type": "done", "finish_reason": "stop"}
