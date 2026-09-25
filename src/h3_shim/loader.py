"""Harness discovery, health check, and session routing.

Discovers H3 harnesses from config, health-checks them every 30s,
and routes sessions to the correct harness. Falls back to native
when harnesses are unreachable or no route matches.

Runtime session pins (:meth:`H3Loader.route_session`, including reroutes
away from failed harnesses) are the route of record: they win over the
static ``sessions`` config, and — when ``session_routes_path`` is
configured — they are persisted to a JSON file so rerouted sessions do
not silently fall back to a dead harness across a process restart.
"""

import asyncio
import json
import logging
import os
import tempfile
import time
from collections import deque
from pathlib import Path

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

    The breaker never recovers on its own: a probe must be *attempted*
    and its outcome fed back via :meth:`record_outcome`.  The loader's
    health check loop is that driver — it asks :meth:`allow_request`
    whether a probe is due, performs the health call when the answer is
    yes, and records the outcome either way.

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
            if (
                len(self._outcomes) == self._window_size
                and self.error_rate >= self._error_threshold
            ):
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

        # Optional durable store for session pins (DF-H3-33).  When set,
        # pins are loaded at boot and every route change is written back
        # atomically.  An unwritable store degrades to in-memory routing
        # with a single warning — the loader must still boot.
        self._session_routes_path: Path | None = None
        path_value = config.get("session_routes_path")
        if path_value:
            self._session_routes_path = Path(path_value)
        self._routes_store_degraded = False

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

        # Restore persisted session pins (if any) after the harness map
        # exists — loaded pins become part of the live route of record.
        self._load_session_routes(self._session_routes_path)

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

        Runtime pins (see :meth:`route_session`) are the route of record:
        they are consulted first on the same candidate keys and win over
        the static ``sessions`` config, which remains the bootstrap
        default.
        """
        routes: dict[str, dict[str, str]] = self._config.get("sessions", {})

        # Build candidates, filtering out any that are None/empty.
        candidates: list[str] = []
        if thread_id:
            candidates.append(f"{platform}:{chat_id}:{thread_id}")
        candidates.append(f"{platform}:{chat_id}")
        candidates.append(platform)

        # 1. Runtime pins — reroutes away from failed harnesses and
        #    explicit route_session() calls must win over static config,
        #    otherwise a restart would send rerouted sessions straight
        #    back to the dead harness (DF-H3-33).  Pins are keyed by
        #    *session_id* (the route_session() parameter), so besides the
        #    composite candidates the bare chat_id is tried — for the
        #    shim loop and simple embedders the session id IS the chat id.
        for key in [*candidates, chat_id]:
            if key in self._session_routes:
                return self._session_routes[key]

        # 2. Static config — the bootstrap default.
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
        """Explicitly pin *session_id* to *harness_name*.

        The pin is the live route of record (it wins over the static
        ``sessions`` config in :meth:`resolve`) and, when
        ``session_routes_path`` is configured, it is persisted so it
        survives a process restart.
        """
        self._session_routes[session_id] = harness_name
        self._persist_session_routes()

    def get_session_harness(self, session_id: str) -> str | None:
        """Return the harness name for *session_id*, or ``None``."""
        return self._session_routes.get(session_id)

    # ── session-route persistence (DF-H3-33) ────────────────────────────

    def _load_session_routes(self, path: Path | None) -> None:
        """Load persisted pins from *path* into the live route map.

        A missing file is the normal first-boot case.  A corrupt or
        non-dict file is warned about and ignored — pins are an
        optimization, never a boot blocker.
        """
        if path is None:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            logger.warning(
                "Session routes file %s unreadable (%s) — "
                "starting with an empty route map",
                path,
                exc,
            )
            return
        if not isinstance(data, dict):
            logger.warning(
                "Session routes file %s is not a JSON object — ignoring",
                path,
            )
            return
        for sid, harness in data.items():
            if isinstance(harness, dict):
                harness = harness.get("harness")
            if isinstance(harness, str) and harness:
                self._session_routes[sid] = harness
            else:
                logger.warning(
                    "Session routes file %s: invalid entry for %r — ignoring",
                    path,
                    sid,
                )

    def _persist_session_routes(self) -> None:
        """Write the live route map to the store file, atomically.

        Best-effort: with no path configured this is a no-op, and an
        unwritable path degrades to in-memory routing with a single
        warning (the loader must keep booting and routing either way).
        """
        if self._session_routes_path is None:
            return
        path = self._session_routes_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(path.parent), prefix=path.name, suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self._session_routes, fh, indent=2, sort_keys=True)
                    fh.write("\n")
                os.replace(tmp_name, path)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
            self._routes_store_degraded = False
        except OSError as exc:
            if not self._routes_store_degraded:
                self._routes_store_degraded = True
                logger.warning(
                    "Cannot persist session routes to %s (%s) — "
                    "routes stay in memory for this run",
                    path,
                    exc,
                )

    # ── health checks ───────────────────────────────────────────────────

    async def health_check_loop(self) -> None:
        """Background coroutine — health-check every harness every 30 s.

        * On success the harness is marked healthy and its failure count resets.
        * Once failures reach :attr:`max_consecutive_failures`, sessions routed
          to the failed harness are moved to :attr:`default_harness`.
        * The circuit breaker records every outcome.  When the circuit opens
          (error rate exceeds threshold) sessions are rerouted immediately
          without waiting for consecutive failures.
        * While the circuit is OPEN the harness is left alone until its
          cooldown expires; from then on the loop performs the half-open
          probe itself (via :meth:`CircuitBreaker.allow_request`) and feeds
          the outcome back, so a recovered harness returns to CLOSED and
          serves traffic again without operator action (DF-H3-34).
        * The loop runs until cancelled.
        """
        try:
            while True:
                for name, client in self.harnesses.items():
                    cb = self._circuit_breakers.get(name)
                    if cb is not None and not cb.allow_request():
                        # CLOSED circuits always pass.  OPEN means the
                        # cooldown is still running — keep rerouting, do
                        # not touch the harness yet.  HALF_OPEN with the
                        # probe slot taken means another cycle is probing.
                        if cb.state == OPEN:
                            logger.warning(
                                "Harness %s: circuit OPEN — in cooldown, no probe yet",
                                name,
                            )
                            self._harness_healthy[name] = False
                            self._reroute_sessions(name)
                        continue
                    try:
                        health = await client.health()
                        self._consecutive_failures[name] = 0
                        was_healthy = self._harness_healthy.get(name, False)
                        self._harness_healthy[name] = health.status == HealthStatus.OK
                        if self._harness_healthy[name]:
                            logger.debug("Harness %s: healthy", name)
                        elif was_healthy:
                            logger.warning(
                                "Harness %s: degraded — %s",
                                name,
                                health.degraded_reason or "unknown",
                            )
                        # DF-H3-34: every attempted health call feeds the
                        # breaker — probe successes close it, probe
                        # failures re-open it with a fresh cooldown.  This
                        # includes DEGRADED responses: the probe was
                        # allowed, so an outcome must be recorded or the
                        # breaker would sit in HALF_OPEN forever.
                        if cb is not None:
                            cb.record_outcome(self._harness_healthy[name])
                    except Exception:
                        failure_count = self._consecutive_failures.get(name, 0) + 1
                        self._consecutive_failures[name] = failure_count
                        logger.warning(
                            "Harness %s: health check failed",
                            name,
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
        rerouted = False
        for sid, hname in list(self._session_routes.items()):
            if hname == failed_harness:
                self._session_routes[sid] = self.default_harness
                rerouted = True
                logger.info(
                    "Rerouted session %s: %s → %s",
                    sid,
                    failed_harness,
                    self.default_harness,
                )
        if rerouted:
            self._persist_session_routes()

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
