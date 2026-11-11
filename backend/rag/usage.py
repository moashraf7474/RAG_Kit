"""Best-effort accounting for provider-reported LLM token usage."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from asgiref.sync import sync_to_async

from rag.conf import get_config
from rag.models import UsageRecord

logger = logging.getLogger(__name__)


def _as_nonnegative_int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _usage_mapping(response) -> Mapping:
    """Read usage from the common LangChain/OpenAI response shapes."""
    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, Mapping):
        return usage

    metadata = getattr(response, "response_metadata", None)
    if isinstance(metadata, Mapping):
        usage = metadata.get("token_usage") or metadata.get("usage")
        if isinstance(usage, Mapping):
            return usage
    return {}


def extract_token_usage(response) -> tuple[int, int]:
    usage = _usage_mapping(response)
    prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    completion = usage.get("completion_tokens", usage.get("output_tokens", 0))
    return _as_nonnegative_int(prompt), _as_nonnegative_int(completion)


def record_usage(
    *,
    organization_id: str | None,
    operation: str,
    response=None,
    model: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> None:
    """Persist usage without making accounting failures fail user requests.

    Providers that do not return token metadata are recorded as zero rather
    than guessed. This keeps the ledger honest and makes unsupported providers
    visible for later adapter work.
    """
    if not organization_id:
        return

    detected_prompt, detected_completion = extract_token_usage(response)
    try:
        UsageRecord.objects.create(
            organization_id=organization_id,
            operation=operation,
            model=model or get_config().llm.model,
            prompt_tokens=_as_nonnegative_int(
                detected_prompt if prompt_tokens is None else prompt_tokens
            ),
            completion_tokens=_as_nonnegative_int(
                detected_completion if completion_tokens is None else completion_tokens
            ),
        )
    except Exception:
        logger.warning("Could not persist LLM usage record.", exc_info=True)


record_usage_async = sync_to_async(record_usage, thread_sensitive=True)
