"""Harness discovery, health check, and session routing.

Discovers H3 harnesses from config, health-checks them every 30s,
and routes sessions to the correct harness. Falls back to native
when harnesses are unreachable or no route matches.
"""

import asyncio
import logging
import time
from collections import deque

from h3_shim.client import H3Client
from h3_shim.protocol import HealthStatus

logger = logging.getLogger(__name__)

# ── Circuit Breaker ────────────────────────────────────────────────────

# Circuit breaker states
CLOSED = "CLOSED"
OPEN = "OPEN"
HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Sliding-window circuit breaker with cooldown and half-open probing.

    Tracks the last *window_size* outcomes (success / failure) in a
    sliding window.  When the error rate reaches *error_threshold*
    (default 0.5) the circuit opens and all requests are blocked until
    *cooldown_seconds* (default 30) have elapsed.  Once the cooldown
    expires the breaker moves to half-open and allows exactly one probe
    request.  A successful probe closes the circuit; a failed probe
    re-opens it immediately.

    Parameters
    ----------
    window_size:
        Number of recent outcomes to track (default 20).
    error_threshold:
        Fraction of failures that triggers OPEN (default 0.5).
    cooldown_seconds:
        Seconds to wait before allowing a half-open probe (default 30).
    """

    def __init__(
        self,
        window_size: int = 20,
        error_threshold: float = 0.5,
        cooldown_seconds: float = 30.0,
    ) -> None:
        if window_size < 1:
            raise ValueError("window_size must be >= 1")
        if not 0 < error_threshold <= 1:
            raise ValueError("error_threshold must be in (0, 1]")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be >= 0")

        self._window_size = window_size
        self._error_threshold = error_threshold
        self._cooldown_seconds = cooldown_seconds

        self._outcomes: deque[bool] = deque(maxlen=window_size)
        self._state: str = CLOSED
        self._opened_at: float | None = None
        self._half_open_probe_sent: bool = False

    # ── public API ──────────────────────────────────────────────────

    @property
    def state(self) -> str:
        """Current breaker state: ``CLOSED``, ``OPEN``, or ``HALF_OPEN``."""
        return self._state

    @property
    def failure_count(self) -> int:
        """Number of failures in the current window."""
        return sum(1 for ok in self._outcomes if not ok)

    @property
    def error_rate(self) -> float:
        """Current error rate (failures / window_size)."""
        if len(self._outcomes) == 0:
            return 0.0
        return self.failure_count / len(self._outcomes)

    def allow_request(self) -> bool:
        """Return ``True`` if a request should be allowed.

        * CLOSED → always allowed.
        * OPEN → allowed only when the cooldown has expired (moves to
          HALF_OPEN).
        * HALF_OPEN → allowed only when no probe has been sent yet.
        """
        self._recalc_state()
        if self._state == CLOSED:
            return True
        if self._state == OPEN:
            return False
        # HALF_OPEN — allow exactly one probe
        if self._half_open_probe_sent:
            return False
        self._half_open_probe_sent = True
        return True

    def record_outcome(self, success: bool) -> None:
        """Record a request outcome and recalculate state.

        Parameters
        ----------
        success:
            ``True`` for a successful request, ``False`` for a failure.
        """
        self._outcomes.append(success)

        if self._state == HALF_OPEN:
            if success:
                # Probe succeeded — close the circuit
                self._state = CLOSED
                self._outcomes.clear()
                self._opened_at = None
                self._half_open_probe_sent = False
            else:
                # Probe failed — re-open immediately
                self._state = OPEN
                self._opened_at = time.monotonic()
                self._half_open_probe_sent = False
            return

        self._recalc_state()

    # ── internal ────────────────────────────────────────────────────

    def _recalc_state(self) -> None:
        """Re-evaluate and transition state based on window + cooldown."""
        if self._state == CLOSED:
            if (len(self._outcomes) == self._window_size
                    and self.error_rate >= self._error_threshold):
                self._state = OPEN
                self._opened_at = time.monotonic()
                self._half_open_probe_sent = False
            return

        if self._state == OPEN:
            if self._opened_at is None:
                return
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self._cooldown_seconds:
                self._state = HALF_OPEN
                self._half_open_probe_sent = False
            return

        # HALF_OPEN — no automatic transition; record_outcome handles it


class H3Loader:
    """Discovers harnesses from config, health-checks them, routes sessions.

    Harness configs are loaded from ``config["harnesses"]`` and routed
    via ``config["sessions"]``.  The special harness name ``"native"`` is
    always available and never represented by an HTTP client — it maps
    to Hermes' own agent loop.

    Usage::

        loader = H3Loader(config)
        await loader.start_health_checks()

        harness = loader.resolve("telegram", "-100", "84802")
        client = loader.harnesses.get(harness)  # None for native

        await loader.close()
    """

    def __init__(self, config: dict):
        # ------------------------------------------------------------------
        # Configuration
        # ------------------------------------------------------------------
        self._config = config
        self.default_harness = config.get("default_harness", "native")
        self.max_consecutive_failures = config.get("max_consecutive_failures", 3)

        # Circuit breaker config
        self._cb_window = config.get("circuit_breaker_window", 20)
        self._cb_cooldown = config.get("circuit_breaker_cooldown", 30.0)
        self._cb_threshold = config.get("circuit_breaker_threshold", 0.5)

        # ------------------------------------------------------------------
        # Harness state
        # ------------------------------------------------------------------
        self.harnesses: dict[str, H3Client] = {}
        self._harness_healthy: dict[str, bool] = {}  # name → healthy?
        self._consecutive_failures: dict[str, int] = {}
        self._circuit_breakers: dict[str, CircuitBreaker] = {}

        # ------------------------------------------------------------------
        # Session routing
        # ------------------------------------------------------------------
        self._session_routes: dict[str, str] = {}  # session_id → harness_name

        # ------------------------------------------------------------------
        # Background health-check task
        # ------------------------------------------------------------------
        self._health_task: asyncio.Task[None] | None = None

        self._load(config)

    # ── config loading ──────────────────────────────────────────────────

    def _load(self, config: dict) -> None:
        """Parse harness configs and create :class:`H3Client` instances.

        Skips ``"native"`` (no HTTP endpoint) and entries whose
        ``endpoint`` is ``None`` or missing.

        If *config* contains an ``identity`` block (``hermes_token``,
        ``hermes_identity``), those values are passed to every
        :class:`H3Client` so that all requests carry auth headers per
        S12 §5.1.
        """
        identity = config.get("identity", {})
        hermes_token: str | None = identity.get("hermes_token")
        hermes_identity: str | None = identity.get("hermes_identity")
        protocol_version: str = identity.get("protocol_version", "1.0")

        for name, hconfig in config.get("harnesses", {}).items():
            if name == "native":
                continue
            endpoint = hconfig.get("endpoint")
            if endpoint is None:
                continue

            self.harnesses[name] = H3Client(
                endpoint=endpoint,
                transport=hconfig.get("transport", "rest"),
                timeout_ms=hconfig.get("timeout_ms", 30_000),
                hermes_token=hermes_token,
                hermes_identity=hermes_identity,
                protocol_version=protocol_version,
            )
            self._harness_healthy[name] = False
            self._circuit_breakers[name] = CircuitBreaker(
                window_size=self._cb_window,
                error_threshold=self._cb_threshold,
                cooldown_seconds=self._cb_cooldown,
            )

    # ── session routing ─────────────────────────────────────────────────

    async def resolve(
        self,
        platform: str,
        chat_id: str,
        thread_id: str | None = None,
    ) -> str:
        """Resolve which harness handles a session.

        Matching order (most-specific first):

        1. ``platform:chat_id:thread_id``
        2. ``platform:chat_id``
        3. ``platform``

        Falls back to :attr:`default_harness` when no route matches.
        """
        routes: dict[str, dict[str, str]] = self._config.get("sessions", {})

        # Build candidates, filtering out any that are None/empty.
        candidates: list[str] = []
        if thread_id:
            candidates.append(f"{platform}:{chat_id}:{thread_id}")
        candidates.append(f"{platform}:{chat_id}")
        candidates.append(platform)

        for key in candidates:
            if key in routes:
                entry = routes[key]
                if isinstance(entry, dict):
                    default = self.default_harness
                    harness: str = entry.get("harness", default) or default
                    return harness
                return entry  # plain string — harness name

        return self.default_harness

    def route_session(self, session_id: str, harness_name: str) -> None:
        """Explicitly pin *session_id* to *harness_name*."""
        self._session_routes[session_id] = harness_name

    def get_session_harness(self, session_id: str) -> str | None:
        """Return the harness name for *session_id*, or ``None``."""
        return self._session_routes.get(session_id)

    # ── health checks ───────────────────────────────────────────────────

    async def health_check_loop(self) -> None:
        """Background coroutine — health-check every harness every 30 s.

        * On success the harness is marked healthy and its failure count resets.
        * Once failures reach :attr:`max_consecutive_failures`, sessions routed
          to the failed harness are moved to :attr:`default_harness`.
        * The circuit breaker records every outcome.  When the circuit opens
          (error rate exceeds threshold) sessions are rerouted immediately
          without waiting for consecutive failures.
        * When the circuit is OPEN the health check is skipped for that
          harness until the cooldown expires (half-open probe).
        * The loop runs until cancelled.
        """
        try:
            while True:
                for name, client in self.harnesses.items():
                    cb = self._circuit_breakers.get(name)
                    # Skip health check when circuit is OPEN (saves resources)
                    if cb is not None and cb.state == OPEN:
                        logger.warning(
                            "Harness %s: circuit OPEN — skipping health check",
                            name,
                        )
                        self._harness_healthy[name] = False
                        self._reroute_sessions(name)
                        continue
                    try:
                        health = await client.health()
                        self._consecutive_failures[name] = 0
                        was_healthy = self._harness_healthy.get(name, False)
                        self._harness_healthy[name] = (
                            health.status == HealthStatus.OK
                        )
                        if self._harness_healthy[name]:
                            logger.debug("Harness %s: healthy", name)
                            if cb is not None:
                                cb.record_outcome(True)
                        elif was_healthy:
                            logger.warning(
                                "Harness %s: degraded — %s",
                                name,
                                health.degraded_reason or "unknown",
                            )
                            if cb is not None:
                                cb.record_outcome(False)
                    except Exception:
                        failure_count = self._consecutive_failures.get(name, 0) + 1
                        self._consecutive_failures[name] = failure_count
                        logger.warning(
                            "Harness %s: health check failed", name,
                            exc_info=True,
                        )
                        if cb is not None:
                            cb.record_outcome(False)
                        if failure_count >= self.max_consecutive_failures:
                            self._harness_healthy[name] = False
                            logger.warning(
                                "Harness %s: falling back after %d "
                                "consecutive failures",
                                name,
                                failure_count,
                            )
                            self._reroute_sessions(name)
                        # Circuit breaker open — reroute immediately
                        elif cb is not None and cb.state == OPEN:
                            self._harness_healthy[name] = False
                            logger.warning(
                                "Harness %s: circuit breaker OPEN at error "
                                "rate %.0f%% — rerouting sessions",
                                name,
                                cb.error_rate * 100,
                            )
                            self._reroute_sessions(name)

                await asyncio.sleep(30)
        except asyncio.CancelledError:
            logger.info("Health check loop cancelled")

    def _reroute_sessions(self, failed_harness: str) -> None:
        """Move every session pinned to *failed_harness* to native."""
        for sid, hname in list(self._session_routes.items()):
            if hname == failed_harness:
                self._session_routes[sid] = self.default_harness
                logger.info(
                    "Rerouted session %s: %s → %s",
                    sid,
                    failed_harness,
                    self.default_harness,
                )

    # ── lifecycle ───────────────────────────────────────────────────────

    async def start_health_checks(self) -> None:
        """Begin background health checks (idempotent)."""
        if self._health_task is None:
            self._health_task = asyncio.create_task(self.health_check_loop())

    async def stop_health_checks(self) -> None:
        """Cancel the background health-check task (idempotent)."""
        if self._health_task is not None:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None

    async def close(self) -> None:
        """Stop health checks and close every harness client."""
        await self.stop_health_checks()
        for client in self.harnesses.values():
            await client.close()
