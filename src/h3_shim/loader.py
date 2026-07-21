"""Harness discovery, health check, and session routing.

Discovers H3 harnesses from config, health-checks them every 30s,
and routes sessions to the correct harness. Falls back to native
when harnesses are unreachable or no route matches.
"""

import asyncio
import logging

from h3_shim.client import H3Client
from h3_shim.protocol import HealthStatus

logger = logging.getLogger(__name__)


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

        # ------------------------------------------------------------------
        # Harness state
        # ------------------------------------------------------------------
        self.harnesses: dict[str, H3Client] = {}
        self._harness_healthy: dict[str, bool] = {}  # name → healthy?
        self._consecutive_failures: dict[str, int] = {}

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
        * The loop runs until cancelled.
        """
        try:
            while True:
                for name, client in self.harnesses.items():
                    try:
                        health = await client.health()
                        self._consecutive_failures[name] = 0
                        was_healthy = self._harness_healthy.get(name, False)
                        self._harness_healthy[name] = (
                            health.status == HealthStatus.OK
                        )
                        if self._harness_healthy[name]:
                            logger.debug("Harness %s: healthy", name)
                        elif was_healthy:
                            logger.warning(
                                "Harness %s: degraded — %s",
                                name,
                                health.degraded_reason or "unknown",
                            )
                    except Exception:
                        failure_count = self._consecutive_failures.get(name, 0) + 1
                        self._consecutive_failures[name] = failure_count
                        logger.warning(
                            "Harness %s: health check failed", name,
                            exc_info=True,
                        )
                        if failure_count >= self.max_consecutive_failures:
                            self._harness_healthy[name] = False
                            logger.warning(
                                "Harness %s: falling back after %d "
                                "consecutive failures",
                                name,
                                failure_count,
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
