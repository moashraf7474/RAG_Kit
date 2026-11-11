"""Tests for metrics and resilience utilities."""

from __future__ import annotations

import asyncio

import pytest

from rag.metrics import PipelineMetrics, get_metrics, log_context
from rag.resilience import CircuitBreaker, retry


class TestPipelineMetrics:
    def setup_method(self) -> None:
        from rag.metrics import _pipeline_metrics

        _pipeline_metrics.ingestion_total = 0
        _pipeline_metrics.ingestion_success = 0
        _pipeline_metrics.ingestion_failure = 0
        _pipeline_metrics.chunks_created = 0
        _pipeline_metrics.summaries_created = 0
        _pipeline_metrics.images_captioned = 0
        _pipeline_metrics.retrieval_total = 0
        _pipeline_metrics.last_ingestion_latency = None

    def test_record_ingestion_start(self) -> None:
        metrics = get_metrics()
        metrics.record_ingestion_start("doc-1", "FILE")
        assert metrics.ingestion_total == 1

    def test_record_ingestion_success(self) -> None:
        metrics = get_metrics()
        metrics.record_ingestion_success("doc-1", chunks=10, summaries=2, images=1, latency=0.5)
        assert metrics.ingestion_success == 1
        assert metrics.chunks_created == 10
        assert metrics.summaries_created == 2
        assert metrics.images_captioned == 1
        assert metrics.last_ingestion_latency == 0.5

    def test_record_ingestion_failure(self) -> None:
        metrics = get_metrics()
        metrics.record_ingestion_failure("doc-1", "timeout", latency=1.0)
        assert metrics.ingestion_failure == 1
        assert metrics.last_ingestion_latency == 1.0

    def test_success_rate_calculation(self) -> None:
        metrics = get_metrics()
        metrics.record_ingestion_start("doc-1", "FILE")
        metrics.record_ingestion_start("doc-2", "FILE")
        metrics.record_ingestion_success("doc-1", chunks=5, summaries=0, images=0, latency=0.1)
        metrics.record_ingestion_failure("doc-2", "error", latency=0.2)
        assert metrics.get_success_rate() == 0.5

    def test_success_rate_zero_total(self) -> None:
        metrics = get_metrics()
        assert metrics.get_success_rate() == 0.0


class TestCircuitBreaker:
    def test_closed_initially(self) -> None:
        cb = CircuitBreaker(failure_threshold=3, cooldown=1.0)
        assert not cb.is_open

    def test_opens_after_threshold(self) -> None:
        cb = CircuitBreaker(failure_threshold=3, cooldown=1.0)
        cb.record_failure(RuntimeError("fail"))
        cb.record_failure(RuntimeError("fail"))
        assert not cb.is_open
        cb.record_failure(RuntimeError("fail"))
        assert cb.is_open

    def test_records_last_failure(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, cooldown=1.0)
        cb.record_failure(ValueError("first"))
        cb.record_failure(TypeError("second"))
        assert "second" in str(cb._last_failure)

    def test_success_resets(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, cooldown=1.0)
        cb.record_failure(RuntimeError("fail"))
        cb.record_success()
        assert not cb.is_open
        assert cb.failures == 0

    def test_raise_if_open(self) -> None:
        cb = CircuitBreaker(failure_threshold=1, cooldown=1.0)
        cb.record_failure(RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="Circuit breaker is open"):
            cb.raise_if_open()


class TestRetry:
    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self) -> None:
        call_count = 0

        @retry(max_attempts=3, base_delay=0.01, retryable=(RuntimeError,))
        async def flaky() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RuntimeError("transient")
            return "ok"

        result = await flaky()
        assert result == "ok"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retry_exhausts_attempts(self) -> None:
        @retry(max_attempts=2, base_delay=0.01, retryable=(RuntimeError,))
        async def always_fails() -> str:
            raise RuntimeError("always")

        with pytest.raises(RuntimeError, match="always"):
            await always_fails()

    @pytest.mark.asyncio
    async def test_retry_non_retryable_raises_immediately(self) -> None:
        call_count = 0

        @retry(max_attempts=3, base_delay=0.01, retryable=(RuntimeError,))
        async def non_retryable() -> str:
            nonlocal call_count
            call_count += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError, match="not retryable"):
            await non_retryable()
        assert call_count == 1
