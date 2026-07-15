"""Tests for loader.py — H3Loader config parsing, session routing, and health.

H3Client construction hits the network (httpx.AsyncClient) so we monkey-patch
``H3Client`` inside the loader module to a fake that records calls. This keeps
tests fast and side-effect-free.

The health-check loop is exercised in a real asyncio event loop with a
shortened sleep so cancellation behavior can be verified.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from h3_shim.client import H3Client
from h3_shim.loader import H3Loader
from h3_shim.protocol import HealthResponse, HealthStatus

# ── helpers ─────────────────────────────────────────────────────────────────


def _fake_client(endpoint: str = "http://h:1", **kw) -> H3Client:
    """Return a real H3Client whose httpx internals are stubbed."""
    c = H3Client(endpoint=endpoint, **kw)
    c._rest = MagicMock()
    c._rest.aclose = AsyncMock()
    return c


def _patch_h3_client_factory(monkeypatch, **attrs):
    """Replace H3Client inside h3_shim.loader with a tracking fake.

    Returns the fake class so individual tests can further customise it.
    """
    fake = MagicMock(wraps=H3Client)

    def _factory(endpoint, transport="rest", timeout_ms=30_000):
        c = _fake_client(endpoint=endpoint, transport=transport, timeout_ms=timeout_ms)
        # Pre-program the .health() mock if the test set one.
        if "health_return" in attrs:
            c.health = AsyncMock(return_value=attrs["health_return"])
        else:
            c.health = AsyncMock(side_effect=Exception("health not stubbed"))
        return c

    fake.side_effect = _factory
    monkeypatch.setattr("h3_shim.loader.H3Client", fake)
    return fake


# ── _load ───────────────────────────────────────────────────────────────────


class TestLoad:
    def test_parses_named_harnesses(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        cfg = {
            "harnesses": {
                "alpha": {
                    "endpoint": "http://a:1",
                    "transport": "rest",
                    "timeout_ms": 5000,
                },
                "beta": {"endpoint": "http://b:1"},
            }
        }
        loader = H3Loader(cfg)
        assert "alpha" in loader.harnesses
        assert "beta" in loader.harnesses
        assert all(isinstance(c, H3Client) for c in loader.harnesses.values())

    def test_skips_native_entry(self, monkeypatch):
        fake = _patch_h3_client_factory(monkeypatch)
        cfg = {"harnesses": {"native": {"endpoint": "ignored"}, "alpha": {"endpoint": "http://a:1"}}}
        loader = H3Loader(cfg)
        # ``native`` must not produce an H3Client instance.
        assert "native" not in loader.harnesses
        assert "alpha" in loader.harnesses
        # And H3Client should not have been instantiated for ``native``.
        names = [c.kwargs["endpoint"] for c in fake.call_args_list]
        assert all(e != "ignored" for e in names)

    def test_skips_endpoint_none(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        cfg = {
            "harnesses": {
                "broken": {"endpoint": None},
                "alpha": {"endpoint": "http://a:1"},
            }
        }
        loader = H3Loader(cfg)
        assert "broken" not in loader.harnesses
        assert "alpha" in loader.harnesses

    def test_skips_endpoint_missing_key(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        cfg = {"harnesses": {"broken": {}, "alpha": {"endpoint": "http://a:1"}}}
        loader = H3Loader(cfg)
        assert "broken" not in loader.harnesses
        assert "alpha" in loader.harnesses

    def test_default_timeout_applied(self, monkeypatch):
        fake = _patch_h3_client_factory(monkeypatch)
        cfg = {"harnesses": {"alpha": {"endpoint": "http://a:1"}}}
        H3Loader(cfg)
        kwargs = fake.call_args.kwargs
        assert kwargs["timeout_ms"] == 30_000

    def test_custom_timeout_passed_through(self, monkeypatch):
        fake = _patch_h3_client_factory(monkeypatch)
        cfg = {"harnesses": {"alpha": {"endpoint": "http://a:1", "timeout_ms": 9999}}}
        H3Loader(cfg)
        assert fake.call_args.kwargs["timeout_ms"] == 9999

    def test_initial_health_is_false(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        cfg = {"harnesses": {"alpha": {"endpoint": "http://a:1"}}}
        loader = H3Loader(cfg)
        assert loader._harness_healthy["alpha"] is False


# ── resolve() ───────────────────────────────────────────────────────────────


class TestResolve:
    @pytest.mark.asyncio
    async def test_no_config_returns_default(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        loader = H3Loader({"default_harness": "native"})
        h = await loader.resolve("telegram", "-100", "42")
        assert h == "native"

    @pytest.mark.asyncio
    async def test_exact_platform_chat_thread_match(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        cfg = {
            "default_harness": "native",
            "sessions": {
                "telegram:-100:42": {"harness": "alpha"},
                "telegram:-100": {"harness": "beta"},
                "telegram": {"harness": "gamma"},
            },
        }
        loader = H3Loader(cfg)
        assert await loader.resolve("telegram", "-100", "42") == "alpha"

    @pytest.mark.asyncio
    async def test_platform_chat_fallback(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        cfg = {
            "default_harness": "native",
            "sessions": {
                "telegram:-100": {"harness": "beta"},
                "telegram": {"harness": "gamma"},
            },
        }
        loader = H3Loader(cfg)
        # No thread-specific route — falls back to platform:chat_id.
        assert await loader.resolve("telegram", "-100", "99") == "beta"

    @pytest.mark.asyncio
    async def test_platform_only_fallback(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        cfg = {
            "default_harness": "native",
            "sessions": {"telegram": {"harness": "gamma"}},
        }
        loader = H3Loader(cfg)
        assert await loader.resolve("telegram", "anything", None) == "gamma"

    @pytest.mark.asyncio
    async def test_default_harness_fallback(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        loader = H3Loader({"default_harness": "alpha"})
        assert await loader.resolve("discord", "1", None) == "alpha"

    @pytest.mark.asyncio
    async def test_string_form_session_entry(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        cfg = {"sessions": {"telegram:-100": "alpha"}}
        loader = H3Loader(cfg)
        assert await loader.resolve("telegram", "-100", None) == "alpha"

    @pytest.mark.asyncio
    async def test_thread_id_none_skipped_in_candidates(self, monkeypatch):
        """When thread_id is None the candidates list must NOT include it."""
        _patch_h3_client_factory(monkeypatch)
        cfg = {
            "default_harness": "native",
            "sessions": {
                "telegram:-100:42": {"harness": "alpha"},  # should NOT match
                "telegram:-100": {"harness": "beta"},
            },
        }
        loader = H3Loader(cfg)
        # thread_id=None → platform:chat_id wins, not platform:chat_id:thread_id.
        assert await loader.resolve("telegram", "-100", None) == "beta"

    @pytest.mark.asyncio
    async def test_empty_thread_id_falls_back_to_chat(self, monkeypatch):
        """Empty thread_id is falsy → should not be appended to candidates."""
        _patch_h3_client_factory(monkeypatch)
        cfg = {
            "default_harness": "native",
            "sessions": {
                "telegram:-100:42": {"harness": "alpha"},
                "telegram:-100": {"harness": "beta"},
            },
        }
        loader = H3Loader(cfg)
        assert await loader.resolve("telegram", "-100", "") == "beta"


# ── route_session / get_session_harness ─────────────────────────────────────


class TestSessionRouting:
    @pytest.mark.asyncio
    async def test_route_then_get(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        loader = H3Loader({})
        loader.route_session("sess_1", "alpha")
        assert loader.get_session_harness("sess_1") == "alpha"

    def test_get_unknown_returns_none(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        loader = H3Loader({})
        assert loader.get_session_harness("never_seen") is None

    @pytest.mark.asyncio
    async def test_route_overwrites(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        loader = H3Loader({})
        loader.route_session("s", "alpha")
        loader.route_session("s", "beta")
        assert loader.get_session_harness("s") == "beta"


# ── _reroute_sessions ───────────────────────────────────────────────────────


class TestRerouteSessions:
    @pytest.mark.asyncio
    async def test_reroutes_only_matching_sessions(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        loader = H3Loader({"default_harness": "native"})
        loader.route_session("a", "alpha")
        loader.route_session("b", "beta")
        loader.route_session("c", "alpha")
        loader._reroute_sessions("alpha")
        assert loader.get_session_harness("a") == "native"
        assert loader.get_session_harness("b") == "beta"  # untouched
        assert loader.get_session_harness("c") == "native"

    @pytest.mark.asyncio
    async def test_reroute_no_matching_sessions(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        loader = H3Loader({"default_harness": "native"})
        loader.route_session("a", "alpha")
        loader._reroute_sessions("ghost")
        assert loader.get_session_harness("a") == "alpha"


# ── health check loop / start-stop ──────────────────────────────────────────


class TestHealthChecks:
    @pytest.mark.asyncio
    async def test_start_creates_task(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        loader = H3Loader({})
        assert loader._health_task is None
        await loader.start_health_checks()
        try:
            assert loader._health_task is not None
            assert not loader._health_task.done()
        finally:
            await loader.stop_health_checks()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        loader = H3Loader({})
        await loader.start_health_checks()
        first = loader._health_task
        await loader.start_health_checks()  # second call must NOT replace task
        assert loader._health_task is first
        await loader.stop_health_checks()

    @pytest.mark.asyncio
    async def test_stop_idempotent(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        loader = H3Loader({})
        await loader.stop_health_checks()  # nothing to stop
        await loader.start_health_checks()
        await loader.stop_health_checks()
        assert loader._health_task is None
        await loader.stop_health_checks()  # second call is also a no-op

    @pytest.mark.asyncio
    async def test_close_stops_checks_and_closes_clients(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        cfg = {"harnesses": {"alpha": {"endpoint": "http://a:1"}}}
        loader = H3Loader(cfg)
        await loader.start_health_checks()
        await loader.close()
        assert loader._health_task is None
        loader.harnesses["alpha"]._rest.aclose.assert_awaited()


# ── health_check_loop with running event loop ──────────────────────────────


class TestHealthLoop:
    @pytest.mark.asyncio
    async def test_loop_marks_healthy_on_ok(self, monkeypatch):
        ok = HealthResponse(status=HealthStatus.OK, version="1")
        _patch_h3_client_factory(monkeypatch, health_return=ok)

        async def fast_loop():
            # Replace the 30s sleep with a short one so the test ends.
            orig = asyncio.sleep

            async def short_sleep(_t):
                await orig(0.001)

            monkeypatch.setattr(asyncio, "sleep", short_sleep)
            try:
                await loader.health_check_loop()
            except asyncio.CancelledError:
                pass

        loader = H3Loader({"harnesses": {"alpha": {"endpoint": "http://a:1"}}})
        task = asyncio.create_task(fast_loop())
        await asyncio.sleep(0.05)  # let the loop run one iteration
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert loader._harness_healthy.get("alpha") is True

    @pytest.mark.asyncio
    async def test_loop_reroutes_on_failure(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)  # default health raises

        loader = H3Loader(
            {
                "default_harness": "native",
                "harnesses": {"alpha": {"endpoint": "http://a:1"}},
            }
        )
        loader.route_session("sess_x", "alpha")
        loader._harness_healthy["alpha"] = True  # pretend we were healthy

        async def fast_loop():
            orig = asyncio.sleep

            async def short_sleep(_t):
                await orig(0.001)

            monkeypatch.setattr(asyncio, "sleep", short_sleep)
            try:
                await loader.health_check_loop()
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(fast_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # The session that was on alpha must have been moved to native.
        assert loader.get_session_harness("sess_x") == "native"
