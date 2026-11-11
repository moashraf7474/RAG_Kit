"""Resilience utilities for the RAG pipeline.

Provides retry with exponential backoff and circuit-breaker semantics for
external calls (LLM, embeddings, vector DB, S3).
"""

from __future__ import annotations

import asyncio
import logging
import time
from functools import wraps
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def retry(
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    retryable: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[F], F]:
    """Retry a function with exponential backoff on retryable exceptions."""

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            delay = base_delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        break
                    logger.warning(
                        "Attempt %d/%d failed for %s: %s. Retrying in %.1fs.",
                        attempt,
                        max_attempts,
                        func.__name__,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * backoff_factor, max_delay)
            raise last_exc or RuntimeError(f"{func.__name__} failed after {max_attempts} attempts")

        return wrapper  # type: ignore[return-value]

    return decorator


class CircuitBreaker:
    """Simple circuit breaker: open after N consecutive failures, half-open after a cooldown."""

    def __init__(self, *, failure_threshold: int = 5, cooldown: float = 60.0) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self.failures = 0
        self.opened_at: float | None = None
        self._last_failure: Exception | None = None

    @property
    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if time.monotonic() - self.opened_at > self.cooldown:
            self.failures = 0
            self.opened_at = None
            return False
        return True

    def record_failure(self, exc: Exception) -> None:
        self.failures += 1
        self._last_failure = exc
        if self.failures >= self.failure_threshold:
            self.opened_at = time.monotonic()
            logger.error(
                "Circuit breaker opened after %d consecutive failures. Last error: %s",
                self.failures,
                exc,
            )

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None
        self._last_failure = None

    def raise_if_open(self) -> None:
        if self.is_open:
            raise RuntimeError(
                f"Circuit breaker is open (failures={self.failures}, cooldown={self.cooldown}s). "
                f"Last error: {self._last_failure}"
            )
