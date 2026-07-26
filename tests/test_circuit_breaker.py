"""
Circuit breaker tests — unit tests for CircuitBreaker and integration
into H3Loader's health_check_loop.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from h3_shim.loader import (
    CLOSED,
    HALF_OPEN,
    OPEN,
    CircuitBreaker,
    H3Loader,
)
from h3_shim.protocol import HealthResponse, HealthStatus

# ══════════════════════════════════════════════════════════════════════
# CircuitBreaker unit tests
# ══════════════════════════════════════════════════════════════════════


class TestCircuitBreakerInit:
    def test_default_window(self):
        cb = CircuitBreaker()
        assert cb._window_size == 20

    def test_custom_window(self):
        cb = CircuitBreaker(window_size=10)
        assert cb._window_size == 10

    def test_custom_threshold(self):
        cb = CircuitBreaker(error_threshold=0.75)
        assert cb._error_threshold == 0.75

    def test_custom_cooldown(self):
        cb = CircuitBreaker(cooldown_seconds=60.0)
        assert cb._cooldown_seconds == 60.0

    def test_initial_state_closed(self):
        cb = CircuitBreaker()
        assert cb.state == CLOSED

    def test_initial_error_rate_zero(self):
        cb = CircuitBreaker()
        assert cb.error_rate == 0.0

    def test_initial_failure_count_zero(self):
        cb = CircuitBreaker()
        assert cb.failure_count == 0

    @pytest.mark.parametrize("window", [0, -1, -10])
    def test_invalid_window(self, window):
        with pytest.raises(ValueError, match="window_size"):
            CircuitBreaker(window_size=window)

    @pytest.mark.parametrize("threshold", [0, -0.5, 1.5, 2.0])
    def test_invalid_threshold(self, threshold):
        with pytest.raises(ValueError, match="error_threshold"):
            CircuitBreaker(error_threshold=threshold)

    def test_negative_cooldown(self):
        with pytest.raises(ValueError, match="cooldown_seconds"):
            CircuitBreaker(cooldown_seconds=-1)


class TestCircuitBreakerClosed:
    def test_allow_requests_when_closed(self):
        cb = CircuitBreaker()
        assert cb.allow_request() is True

    def test_single_success_stays_closed(self):
        cb = CircuitBreaker(window_size=5, error_threshold=0.5)
        cb.record_outcome(True)
        assert cb.state == CLOSED
        assert cb.failure_count == 0

    def test_successes_dont_affect_failure_count(self):
        cb = CircuitBreaker(window_size=5, error_threshold=0.5)
        for _ in range(3):
            cb.record_outcome(True)
        assert cb.failure_count == 0
        assert cb.error_rate == 0.0

    def test_stays_closed_below_threshold(self):
        """Window full but error rate below threshold."""
        cb = CircuitBreaker(window_size=5, error_threshold=0.5)
        for _ in range(2):
            cb.record_outcome(False)
        for _ in range(3):
            cb.record_outcome(True)
        assert cb.state == CLOSED


class TestCircuitBreakerOpen:
    def test_opens_at_threshold(self):
        """Window full error rate >= threshold  OPEN."""
        cb = CircuitBreaker(window_size=5, error_threshold=0.5)
        for _ in range(2):
            cb.record_outcome(True)
        for _ in range(3):
            cb.record_outcome(False)
        assert cb.state == OPEN

    def test_blocks_requests_when_open(self):
        cb = CircuitBreaker(window_size=5, error_threshold=0.5)
        for _ in range(2):
            cb.record_outcome(True)
        for _ in range(3):
            cb.record_outcome(False)
        assert cb.allow_request() is False

    def test_failure_count(self):
        cb = CircuitBreaker(window_size=10, error_threshold=0.3)
        for _ in range(7):
            cb.record_outcome(True)
        for _ in range(3):
            cb.record_outcome(False)
        assert cb.failure_count == 3

    def test_error_rate_reflects_window(self):
        cb = CircuitBreaker(window_size=8, error_threshold=0.3)
        for _ in range(5):
            cb.record_outcome(True)
        for _ in range(3):
            cb.record_outcome(False)
        assert cb.error_rate == 3 / 8

    def test_exact_threshold_opens(self):
        """Error rate == threshold should also open."""
        cb = CircuitBreaker(window_size=4, error_threshold=0.5)
        for _ in range(2):
            cb.record_outcome(True)
        for _ in range(2):
            cb.record_outcome(False)
        assert cb.state == OPEN


class TestCircuitBreakerHalfOpen:
    def test_cooldown_transitions_to_half_open(self):
        """After cooldown, allow_request triggers HALF_OPEN."""
        cb = CircuitBreaker(window_size=5, error_threshold=0.5, cooldown_seconds=0.01)
        for _ in range(2):
            cb.record_outcome(True)
        for _ in range(3):
            cb.record_outcome(False)
        assert cb.state == OPEN

        time.sleep(0.02)
        assert cb.allow_request() is True
        assert cb.state == HALF_OPEN

    def test_half_open_allows_one_probe(self):
        cb = CircuitBreaker(window_size=5, error_threshold=0.5, cooldown_seconds=0.01)
        for _ in range(2):
            cb.record_outcome(True)
        for _ in range(3):
            cb.record_outcome(False)
        assert cb.state == OPEN

        time.sleep(0.02)
        assert cb.allow_request() is True
        assert cb.state == HALF_OPEN
        assert cb.allow_request() is False

    def test_probe_success_closes(self):
        cb = CircuitBreaker(window_size=5, error_threshold=0.5, cooldown_seconds=0.01)
        for _ in range(2):
            cb.record_outcome(True)
        for _ in range(3):
            cb.record_outcome(False)
        assert cb.state == OPEN

        time.sleep(0.02)
        assert cb.allow_request() is True
        assert cb.state == HALF_OPEN

        cb.record_outcome(True)
        assert cb.state == CLOSED
        assert cb.error_rate == 0.0

    def test_probe_failure_reopens(self):
        cb = CircuitBreaker(window_size=5, error_threshold=0.5, cooldown_seconds=0.01)
        for _ in range(2):
            cb.record_outcome(True)
        for _ in range(3):
            cb.record_outcome(False)
        assert cb.state == OPEN

        time.sleep(0.02)
        assert cb.allow_request() is True
        assert cb.state == HALF_OPEN

        cb.record_outcome(False)
        assert cb.state == OPEN
        assert cb.allow_request() is False

    def test_probe_failure_restarts_cooldown(self):
        """After probe failure, must wait cooldown again."""
        cb = CircuitBreaker(window_size=5, error_threshold=0.5, cooldown_seconds=0.05)
        for _ in range(2):
            cb.record_outcome(True)
        for _ in range(3):
            cb.record_outcome(False)
        assert cb.state == OPEN

        time.sleep(0.07)
        assert cb.allow_request() is True
        assert cb.state == HALF_OPEN

        cb.record_outcome(False)
        assert cb.state == OPEN
        assert cb.allow_request() is False

        time.sleep(0.07)
        assert cb.allow_request() is True
        assert cb.state == HALF_OPEN

    def test_outcome_after_open_recalc(self):
        """Recording an outcome after OPEN stays OPEN (cooldown not expired)."""
        cb = CircuitBreaker(window_size=5, error_threshold=0.5, cooldown_seconds=30.0)
        for _ in range(2):
            cb.record_outcome(True)
        for _ in range(3):
            cb.record_outcome(False)
        assert cb.state == OPEN
        cb.record_outcome(True)
        assert cb.state == OPEN


# ══════════════════════════════════════════════════════════════════════
# H3Loader integration tests — circuit breaker in health_check_loop
# ══════════════════════════════════════════════════════════════════════


class TestCircuitBreakerIntegration:
    """Circuit breaker records outcomes during health_check_loop."""

    @staticmethod
    def _make_loader(**cfg_overrides):
        cfg = {
            "harnesses": {"alpha": {"endpoint": "http://a:1"}},
            "circuit_breaker_window": 5,
            "circuit_breaker_threshold": 0.5,
            "circuit_breaker_cooldown": 0.05,
            **cfg_overrides,
        }
        return H3Loader(cfg)

    @pytest.mark.asyncio
    async def test_healthy_harness_records_success(self, monkeypatch):
        ok = HealthResponse(status=HealthStatus.OK, version="1")

        class FakeClient:
            async def health(self):
                return ok

            async def aclose(self):
                pass

        monkeypatch.setattr("h3_shim.loader.H3Client", lambda **kw: FakeClient())

        loader = self._make_loader()
        loader._harness_healthy["alpha"] = True

        # Run 1 health check cycle
        await self._run_checks_n(loader, monkeypatch, 1)

        cb = loader._circuit_breakers["alpha"]
        assert cb.failure_count == 0
        assert cb.state == CLOSED

    @pytest.mark.asyncio
    async def test_degraded_harness_records_failure(self, monkeypatch):
        degraded = HealthResponse(
            status=HealthStatus.DEGRADED, version="1", degraded_reason="slow"
        )

        class FakeClient:
            async def health(self):
                return degraded

            async def aclose(self):
                pass

        monkeypatch.setattr("h3_shim.loader.H3Client", lambda **kw: FakeClient())

        loader = self._make_loader()
        loader._harness_healthy["alpha"] = True

        await self._run_checks_n(loader, monkeypatch, 1)

        cb = loader._circuit_breakers["alpha"]
        assert cb.failure_count == 1
        assert cb.state == CLOSED

    @pytest.mark.asyncio
    async def test_failing_harness_records_failure(self, monkeypatch):
        class FakeClient:
            async def health(self):
                raise Exception("connection refused")

            async def aclose(self):
                pass

        monkeypatch.setattr("h3_shim.loader.H3Client", lambda **kw: FakeClient())

        loader = self._make_loader()
        loader._harness_healthy["alpha"] = True

        await self._run_checks_n(loader, monkeypatch, 1)

        cb = loader._circuit_breakers["alpha"]
        assert cb.failure_count == 1
        assert cb.state == CLOSED

    @pytest.mark.asyncio
    async def test_circuit_breaker_config_passed_to_h3loader(self, monkeypatch):
        """Custom circuit breaker config values are passed to CircuitBreaker."""
        monkeypatch.setattr(
            "h3_shim.loader.H3Client",
            MagicMock(
                return_value=MagicMock(
                    health=AsyncMock(
                        return_value=HealthResponse(status=HealthStatus.OK, version="1")
                    )
                )
            ),
        )

        loader = H3Loader(
            {
                "harnesses": {"alpha": {"endpoint": "http://a:1"}},
                "circuit_breaker_window": 10,
                "circuit_breaker_cooldown": 60.0,
                "circuit_breaker_threshold": 0.75,
            }
        )

        cb = loader._circuit_breakers["alpha"]
        assert cb._window_size == 10
        assert cb._cooldown_seconds == 60.0
        assert cb._error_threshold == 0.75

    @pytest.mark.asyncio
    async def test_default_circuit_breaker_config(self, monkeypatch):
        """Default circuit breaker config when not specified."""
        monkeypatch.setattr(
            "h3_shim.loader.H3Client",
            MagicMock(
                return_value=MagicMock(
                    health=AsyncMock(
                        return_value=HealthResponse(status=HealthStatus.OK, version="1")
                    )
                )
            ),
        )

        loader = H3Loader({"harnesses": {"alpha": {"endpoint": "http://a:1"}}})

        cb = loader._circuit_breakers["alpha"]
        assert cb._window_size == 20
        assert cb._cooldown_seconds == 30.0
        assert cb._error_threshold == 0.5

    @staticmethod
    async def _run_checks_n(loader, monkeypatch, count):
        """Run N iterations of health_check_loop."""
        asyncio_sleep = asyncio.sleep
        iter_count = 0

        async def counted_sleep(_delay):
            nonlocal iter_count
            iter_count += 1
            if iter_count >= count:
                raise asyncio.CancelledError()
            await asyncio_sleep(0.001)

        monkeypatch.setattr(asyncio, "sleep", counted_sleep)
        try:
            await loader.health_check_loop()
        except asyncio.CancelledError:
            pass
