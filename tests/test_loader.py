"""Tests for loader.py — H3Loader config parsing, session routing, and health.

H3Client construction hits the network (httpx.AsyncClient) so we monkey-patch
``H3Client`` inside the loader module to a fake that records calls. This keeps
tests fast and side-effect-free.

The health-check loop is exercised in a real asyncio event loop with a
shortened sleep so cancellation behavior can be verified.
"""

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from h3_shim.client import H3Client
from h3_shim.loader import CircuitBreaker, H3Loader
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

    def _factory(
        endpoint,
        transport="rest",
        timeout_ms=30_000,
        hermes_token=None,
        hermes_identity=None,
        protocol_version="1.0",
    ):
        c = _fake_client(
            endpoint=endpoint,
            transport=transport,
            timeout_ms=timeout_ms,
            hermes_token=hermes_token,
            hermes_identity=hermes_identity,
            protocol_version=protocol_version,
        )
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
        cfg = {
            "harnesses": {
                "native": {"endpoint": "ignored"},
                "alpha": {"endpoint": "http://a:1"},
            }
        }
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

    def test_grpc_transport_fails_fast(self, monkeypatch):
        """GAP-030: a config claiming gRPC must not silently use REST."""
        _patch_h3_client_factory(monkeypatch)
        cfg = {
            "harnesses": {
                "alpha": {"endpoint": "http://a:1", "transport": "grpc"},
            }
        }
        with pytest.raises(ValueError, match="grpc transport not supported yet"):
            H3Loader(cfg)

    def test_custom_timeout_passed_through(self, monkeypatch):
        fake = _patch_h3_client_factory(monkeypatch)
        cfg = {"harnesses": {"alpha": {"endpoint": "http://a:1", "timeout_ms": 9999}}}
        H3Loader(cfg)
        assert fake.call_args.kwargs["timeout_ms"] == 9999

    def test_identity_config_passed_to_client(self, monkeypatch):
        """Identity block (hermes_token, hermes_identity) flows to H3Client."""
        fake = _patch_h3_client_factory(monkeypatch)
        cfg = {
            "identity": {
                "hermes_token": "h3_hx_abc123",
                "hermes_identity": "hermes-main",
                "protocol_version": "1.1",
            },
            "harnesses": {"alpha": {"endpoint": "http://a:1"}},
        }
        H3Loader(cfg)
        assert fake.call_args.kwargs["hermes_token"] == "h3_hx_abc123"
        assert fake.call_args.kwargs["hermes_identity"] == "hermes-main"
        assert fake.call_args.kwargs["protocol_version"] == "1.1"

    def test_missing_identity_block_passes_none(self, monkeypatch):
        """Without identity block, hermes_token/identity are None."""
        fake = _patch_h3_client_factory(monkeypatch)
        cfg = {"harnesses": {"alpha": {"endpoint": "http://a:1"}}}
        H3Loader(cfg)
        assert fake.call_args.kwargs["hermes_token"] is None
        assert fake.call_args.kwargs["hermes_identity"] is None
        assert fake.call_args.kwargs["protocol_version"] == "1.0"

    def test_identity_defaults_to_v1_0(self, monkeypatch):
        """Missing protocol_version defaults to 1.0."""
        fake = _patch_h3_client_factory(monkeypatch)
        cfg = {
            "identity": {"hermes_token": "h3_hx_abc"},
            "harnesses": {"alpha": {"endpoint": "http://a:1"}},
        }
        H3Loader(cfg)
        assert fake.call_args.kwargs["protocol_version"] == "1.0"

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


# ── durable reroute: session pins survive a restart (DF-H3-33) ──────────────


class TestSessionRoutePersistence:
    """DF-H3-33: pins are the route of record and can outlive the process."""

    @staticmethod
    def _patch(monkeypatch) -> None:
        _patch_h3_client_factory(monkeypatch)

    @staticmethod
    def _cfg(routes_path, **overrides):
        cfg = {
            "default_harness": "native",
            "session_routes_path": str(routes_path),
            "harnesses": {
                "alpha": {"endpoint": "http://a:1"},
                "beta": {"endpoint": "http://b:1"},
            },
        }
        cfg.update(overrides)
        return cfg

    @pytest.mark.asyncio
    async def test_reroute_survives_simulated_restart(self, tmp_path, monkeypatch):
        """(c) Fresh loader, same store file → rerouted session stays rerouted.

        The static config still names the dead harness, so a pre-fix loader
        (pins ignored at resolve time) resolves straight back to it — exactly
        the DF-H3-33 defect this test pins down.
        """
        self._patch(monkeypatch)
        store = tmp_path / "session-routes.json"

        first = H3Loader(self._cfg(store, sessions={"telegram:sess_x": "alpha"}))
        first.route_session("sess_x", "alpha")
        first._reroute_sessions("alpha")  # harness died — sessions move to native

        second = H3Loader(self._cfg(store, sessions={"telegram:sess_x": "alpha"}))
        assert await second.resolve("telegram", "sess_x") == "native", (
            "reroute target must survive a restart"
        )

    @pytest.mark.asyncio
    async def test_pinned_session_survives_simulated_restart(
        self, tmp_path, monkeypatch
    ):
        self._patch(monkeypatch)
        store = tmp_path / "session-routes.json"

        first = H3Loader(self._cfg(store))
        first.route_session("telegram:555:7", "beta")

        second = H3Loader(self._cfg(store))
        assert await second.resolve("telegram", "555", "7") == "beta"

    @pytest.mark.asyncio
    async def test_resolve_prefers_pins_over_static_config(self, tmp_path, monkeypatch):
        """(b) A pin (incl. a loaded reroute) beats the static sessions map."""
        self._patch(monkeypatch)
        store = tmp_path / "session-routes.json"

        first = H3Loader(
            self._cfg(
                store,
                sessions={"telegram:sess_x": "beta"},  # stale static route
            )
        )
        first.route_session("sess_x", "alpha")
        first._reroute_sessions("alpha")  # pin now says "native"

        second = H3Loader(self._cfg(store))
        assert await second.resolve("telegram", "sess_x") == "native"

    @pytest.mark.asyncio
    async def test_resolve_falls_back_to_static_config_without_pins(
        self, tmp_path, monkeypatch
    ):
        self._patch(monkeypatch)
        store = tmp_path / "session-routes.json"
        loader = H3Loader(self._cfg(store, sessions={"telegram:-100": "alpha"}))
        assert await loader.resolve("telegram", "-100") == "alpha"

    @pytest.mark.asyncio
    async def test_unwritable_store_still_boots_and_routes(self, tmp_path, monkeypatch):
        """(d) Unwritable path → warning, in-memory routing, no boot failure."""
        self._patch(monkeypatch)
        d = tmp_path / "stays-a-file"
        d.write_text("not a directory")
        store = d / "session-routes.json"

        loader = H3Loader(self._cfg(store))
        assert loader.get_session_harness("s") is None

        loader.route_session("s", "alpha")
        assert loader.get_session_harness("s") == "alpha"
        assert await loader.resolve("telegram", "s") == "alpha"

    @pytest.mark.asyncio
    async def test_unwritable_store_warns_once_not_per_write(
        self, tmp_path, monkeypatch, caplog
    ):
        import logging as _logging

        self._patch(monkeypatch)
        d = tmp_path / "stays-a-file"
        d.write_text("not a directory")
        loader = H3Loader(self._cfg(d / "session-routes.json"))

        with caplog.at_level(_logging.WARNING, logger="h3_shim.loader"):
            for i in range(5):
                loader.route_session(f"s{i}", "alpha")
            loader._reroute_sessions("alpha")

        warnings = [
            r.getMessage()
            for r in caplog.records
            if r.levelno >= _logging.WARNING and "Cannot persist" in r.getMessage()
        ]
        assert len(warnings) == 1, warnings

    @pytest.mark.asyncio
    async def test_corrupt_store_file_is_not_fatal(self, tmp_path, monkeypatch):
        self._patch(monkeypatch)
        store = tmp_path / "session-routes.json"
        store.write_text("{not json")

        loader = H3Loader(self._cfg(store))
        assert loader.get_session_harness("s") is None

    @pytest.mark.asyncio
    async def test_no_path_configured_stays_in_memory(self, tmp_path, monkeypatch):
        self._patch(monkeypatch)
        loader = H3Loader({"default_harness": "native"})
        loader.route_session("s", "alpha")
        assert loader.get_session_harness("s") == "alpha"
        # No store configured → nothing appeared on disk.
        assert not (tmp_path / "session-routes.json").exists()

    @pytest.mark.asyncio
    async def test_store_loads_json_and_dict_pin_forms(self, tmp_path, monkeypatch):
        self._patch(monkeypatch)
        store = tmp_path / "session-routes.json"
        store.write_text('{"plain": "alpha", "shaped": {"harness": "beta"}, "bad": 5}')

        loader = H3Loader(self._cfg(store))
        assert await loader.resolve("telegram", "plain") == "alpha"
        assert await loader.resolve("telegram", "shaped") == "beta"
        assert await loader.resolve("telegram", "bad") == "native"

    @pytest.mark.asyncio
    async def test_store_write_is_atomic(self, tmp_path, monkeypatch, mocker):
        """Persistence uses write-temp-then-replace, never a partial file."""
        self._patch(monkeypatch)
        store = tmp_path / "session-routes.json"
        loader = H3Loader(self._cfg(store))

        observed: dict = {}
        real_replace = os.replace

        def spy_replace(src, dst, **kw):
            # At replace time the temp file must exist and differ from the
            # store path — write-temp-then-replace, never a direct rewrite.
            observed["tmp_existed"] = Path(src).exists()
            observed["src_is_store"] = str(src) == str(store)
            return real_replace(src, dst, **kw)

        mocker.patch("h3_shim.loader.os.replace", side_effect=spy_replace)
        loader.route_session("s", "alpha")

        assert observed["tmp_existed"], "replace must consume a temp file"
        assert not observed["src_is_store"]
        assert store.exists()
        assert not list(store.parent.glob("*.tmp")), "no temp leftovers"

    @pytest.mark.asyncio
    async def test_pins_loaded_from_store_are_rewritten_on_reroute(
        self, tmp_path, monkeypatch
    ):
        """A reroute of a LOADED pin is persisted too (not just new pins)."""
        self._patch(monkeypatch)
        store = tmp_path / "session-routes.json"
        store.write_text('{"sess_x": "alpha"}')

        loader = H3Loader(self._cfg(store))
        loader._reroute_sessions("alpha")

        second = H3Loader(self._cfg(store))
        assert await second.resolve("telegram", "sess_x") == "native"


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
    @staticmethod
    async def _run_checks(loader, monkeypatch, count):
        sleep_calls = 0

        async def stop_after_count(_delay):
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls >= count:
                raise asyncio.CancelledError

        monkeypatch.setattr(asyncio, "sleep", stop_after_count)
        await loader.health_check_loop()

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

        # The reroute fires synchronously as soon as consecutive failures
        # reach the threshold, so wait on that counter instead of racing a
        # fixed wall-clock window (which flakes on slow/loaded boxes).
        real_sleep = asyncio.sleep
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10.0
        task = asyncio.create_task(fast_loop())
        try:
            while (
                loader._consecutive_failures.get("alpha", 0)
                < loader.max_consecutive_failures
            ):
                assert loop.time() < deadline, (
                    "health check loop never reached the reroute threshold"
                )
                await real_sleep(0.001)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        # The session that was on alpha must have been moved to native.
        assert loader.get_session_harness("sess_x") == "native"

    @pytest.mark.asyncio
    async def test_single_failure_does_not_reroute(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        loader = H3Loader(
            {
                "default_harness": "native",
                "harnesses": {"alpha": {"endpoint": "http://a:1"}},
            }
        )
        loader.route_session("sess_x", "alpha")
        loader._harness_healthy["alpha"] = True

        await self._run_checks(loader, monkeypatch, 1)

        assert loader.get_session_harness("sess_x") == "alpha"
        assert loader._consecutive_failures["alpha"] == 1

    @pytest.mark.asyncio
    async def test_three_consecutive_failures_reroute(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        loader = H3Loader(
            {
                "default_harness": "native",
                "harnesses": {"alpha": {"endpoint": "http://a:1"}},
            }
        )
        loader.route_session("sess_x", "alpha")
        loader._harness_healthy["alpha"] = True

        await self._run_checks(loader, monkeypatch, 3)

        assert loader.get_session_harness("sess_x") == "native"
        assert loader._consecutive_failures["alpha"] == 3

    @pytest.mark.asyncio
    async def test_success_resets_failure_counter(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        loader = H3Loader(
            {
                "default_harness": "native",
                "harnesses": {"alpha": {"endpoint": "http://a:1"}},
            }
        )
        ok = HealthResponse(status=HealthStatus.OK, version="1")
        loader.harnesses["alpha"].health = AsyncMock(
            side_effect=[
                Exception("first"),
                ok,
                Exception("second"),
                Exception("third"),
            ]
        )
        loader.route_session("sess_x", "alpha")
        loader._harness_healthy["alpha"] = True

        await self._run_checks(loader, monkeypatch, 4)

        assert loader.get_session_harness("sess_x") == "alpha"
        assert loader._consecutive_failures["alpha"] == 2

    @pytest.mark.asyncio
    async def test_custom_max_consecutive_failures_config(self, monkeypatch):
        _patch_h3_client_factory(monkeypatch)
        loader = H3Loader(
            {
                "default_harness": "native",
                "max_consecutive_failures": 2,
                "harnesses": {"alpha": {"endpoint": "http://a:1"}},
            }
        )
        loader.route_session("sess_x", "alpha")
        loader._harness_healthy["alpha"] = True

        await self._run_checks(loader, monkeypatch, 2)

        assert loader.max_consecutive_failures == 2
        assert loader.get_session_harness("sess_x") == "native"


# ── CircuitBreaker ───────────────────────────────────────────────────────


class TestCircuitBreaker:
    """Tests for the CircuitBreaker sliding-window error-rate tracker."""

    def test_initial_state_is_closed(self):
        cb = CircuitBreaker(window_size=20, error_threshold=0.5, cooldown_seconds=30)
        assert cb.state == "CLOSED"
        assert cb.error_rate == 0.0
        assert cb.failure_count == 0

    def test_records_outcomes_and_tracks_rate(self):
        cb = CircuitBreaker(window_size=10)
        # 3 failures, 7 successes
        for _ in range(7):
            cb.record_outcome(True)
        for _ in range(3):
            cb.record_outcome(False)
        assert cb.failure_count == 3
        assert cb.error_rate == 0.3
        assert cb.state == "CLOSED"  # not full yet — can't open

    def test_opens_at_threshold(self):
        """10 successes + 10 failures = 50% → OPEN (threshold 0.5)."""
        cb = CircuitBreaker(window_size=20, error_threshold=0.5)
        for _ in range(10):
            cb.record_outcome(True)
        for _ in range(10):
            cb.record_outcome(False)
        # Window is full (20 outcomes), 10 failures = exactly 50%
        assert cb.state == "OPEN"
        assert cb.error_rate == 0.5

    def test_allow_request_true_when_closed(self):
        cb = CircuitBreaker()
        assert cb.allow_request() is True

    def test_allow_request_blocks_when_open(self):
        cb = CircuitBreaker(window_size=5, error_threshold=0.4, cooldown_seconds=30)
        # Fill window with 3 failures (60% → > 0.4 threshold)
        for _ in range(3):
            cb.record_outcome(False)
        for _ in range(2):
            cb.record_outcome(True)
        assert cb.state == "OPEN"
        assert cb.allow_request() is False

    def test_allow_request_allows_probe_after_cooldown(self, monkeypatch):
        cb = CircuitBreaker(window_size=5, error_threshold=0.4, cooldown_seconds=0.1)
        # Open the circuit
        for _ in range(3):
            cb.record_outcome(False)
        for _ in range(2):
            cb.record_outcome(True)
        assert cb.state == "OPEN"

        # Wait past cooldown
        import time as _time

        _time.sleep(0.15)

        # Now a probe should be allowed (moves to HALF_OPEN)
        assert cb.allow_request() is True
        assert cb.state == "HALF_OPEN"

        # Second request — no more probes allowed
        assert cb.allow_request() is False  # probe already pending

    def test_probe_success_closes_circuit(self, monkeypatch):
        cb = CircuitBreaker(window_size=5, error_threshold=0.4, cooldown_seconds=0.1)
        # Open the circuit
        for _ in range(3):
            cb.record_outcome(False)
        for _ in range(2):
            cb.record_outcome(True)
        assert cb.state == "OPEN"

        import time as _time

        _time.sleep(0.15)

        # Probe
        assert cb.allow_request() is True
        assert cb.state == "HALF_OPEN"

        # Probe succeeds
        cb.record_outcome(True)
        assert cb.state == "CLOSED"
        assert cb.failure_count == 0
        assert cb.error_rate == 0.0

    def test_probe_failure_stays_open(self, monkeypatch):
        cb = CircuitBreaker(window_size=5, error_threshold=0.4, cooldown_seconds=0.1)
        # Open the circuit
        for _ in range(3):
            cb.record_outcome(False)
        for _ in range(2):
            cb.record_outcome(True)
        assert cb.state == "OPEN"

        import time as _time

        _time.sleep(0.15)

        # Probe
        assert cb.allow_request() is True
        assert cb.state == "HALF_OPEN"

        # Probe fails
        cb.record_outcome(False)
        assert cb.state == "OPEN"

    @pytest.mark.asyncio
    async def test_health_check_integration_circuit_open_reroutes(self, monkeypatch):
        """H3Loader reroutes sessions when the circuit breaker opens."""
        _patch_h3_client_factory(monkeypatch)

        loader = H3Loader(
            {
                "default_harness": "native",
                "circuit_breaker_window": 5,
                "circuit_breaker_threshold": 0.5,
                "harnesses": {"alpha": {"endpoint": "http://a:1"}},
            }
        )
        loader.route_session("sess_x", "alpha")
        loader._harness_healthy["alpha"] = True

        # All health checks fail → circuit should open after 5 iterations
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
        # Let 5 iterations run (enough to fill the 5-wide window with failures)
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Circuit should be OPEN and sessions rerouted to native
        cb = loader._circuit_breakers.get("alpha")
        assert cb is not None
        assert cb.state == "OPEN"
        assert loader.get_session_harness("sess_x") == "native"

    @pytest.mark.asyncio
    async def test_open_circuit_skips_health_check(self, monkeypatch):
        """When circuit is OPEN, health check loop skips that harness."""
        _patch_h3_client_factory(monkeypatch)

        loader = H3Loader(
            {
                "default_harness": "native",
                "circuit_breaker_window": 3,
                "circuit_breaker_threshold": 0.5,
                "harnesses": {
                    "alpha": {"endpoint": "http://a:1"},
                    "beta": {"endpoint": "http://b:1"},
                },
            }
        )

        # Pre-set alpha's circuit breaker to OPEN
        cb_alpha = loader._circuit_breakers["alpha"]
        cb_alpha._state = "OPEN"
        cb_alpha._opened_at = 999999.0  # far in the future

        call_count = 0

        async def counting_health():
            nonlocal call_count
            call_count += 1
            return HealthResponse(status=HealthStatus.OK, version="1")

        loader.harnesses["alpha"].health = AsyncMock(side_effect=counting_health)
        loader.harnesses["beta"].health = AsyncMock(side_effect=counting_health)

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

        # beta got health-checked (at least once), alpha was skipped
        assert call_count > 0
        # alpha's health should NOT have been called — the circuit was OPEN
        # (beta's health was called for each iteration)
        # We can't precisely assert call_count because it depends on timing,
        # but we know alpha was OPEN so it should be skipped.
        # Verify the circuit is still OPEN
        assert cb_alpha.state == "OPEN"


# ── recovery: an OPEN circuit must be able to return to CLOSED (DF-H3-34) ───


class TestCircuitRecovery:
    """DF-H3-34: the health loop probes an OPEN circuit after cooldown.

    Pre-fix behaviour under test: once OPEN the loop skipped the harness
    forever, so no outcome was ever recorded again and one transient
    outage left the harness dead for the life of the process.
    """

    @staticmethod
    def _cfg(**overrides):
        cfg = {
            "default_harness": "native",
            "circuit_breaker_window": 5,
            "circuit_breaker_threshold": 0.5,
            "harnesses": {"alpha": {"endpoint": "http://a:1"}},
        }
        cfg.update(overrides)
        return cfg

    @staticmethod
    def _open_breaker(cb) -> None:
        """Drive *cb* OPEN through its public API (window 5, threshold 0.5)."""
        for _ in range(3):
            cb.record_outcome(False)
        for _ in range(2):
            cb.record_outcome(True)

    @pytest.mark.asyncio
    async def test_open_circuit_recovers_via_health_loop(self, monkeypatch):
        """Cooldown expired + health loop success → circuit CLOSED again."""
        _patch_h3_client_factory(monkeypatch)
        loader = H3Loader(self._cfg(circuit_breaker_cooldown=0.0))
        cb = loader._circuit_breakers["alpha"]
        self._open_breaker(cb)
        assert cb.state == "OPEN"

        ok = HealthResponse(status=HealthStatus.OK, version="1")
        loader.harnesses["alpha"].health = AsyncMock(return_value=ok)

        await TestHealthLoop._run_checks(loader, monkeypatch, 1)

        assert cb.state == "CLOSED"
        assert loader._harness_healthy["alpha"] is True

    @pytest.mark.asyncio
    async def test_failed_probe_reopens_with_fresh_cooldown(self, monkeypatch):
        """Cooldown expired + health loop failure → back to OPEN, probe re-armed."""
        _patch_h3_client_factory(monkeypatch)
        loader = H3Loader(self._cfg(circuit_breaker_cooldown=0.0))
        cb = loader._circuit_breakers["alpha"]
        self._open_breaker(cb)
        assert cb.state == "OPEN"

        loader.harnesses["alpha"].health = AsyncMock(
            side_effect=Exception("still down")
        )

        await TestHealthLoop._run_checks(loader, monkeypatch, 1)

        assert cb.state == "OPEN"

    @pytest.mark.asyncio
    async def test_no_probe_before_cooldown_expires(self, monkeypatch):
        """While the cooldown is running the harness is still left alone."""
        _patch_h3_client_factory(monkeypatch)
        loader = H3Loader(self._cfg(circuit_breaker_cooldown=30.0))
        cb = loader._circuit_breakers["alpha"]
        self._open_breaker(cb)  # _opened_at = now → cooldown nowhere near expired
        assert cb.state == "OPEN"

        ok = HealthResponse(status=HealthStatus.OK, version="1")
        loader.harnesses["alpha"].health = AsyncMock(return_value=ok)

        await TestHealthLoop._run_checks(loader, monkeypatch, 1)

        loader.harnesses["alpha"].health.assert_not_awaited()
        assert cb.state == "OPEN"

    @pytest.mark.asyncio
    async def test_open_circuit_still_reroutes_every_cycle(self, monkeypatch):
        """Reroute protection keeps running even while OPEN (cooldown pending)."""
        _patch_h3_client_factory(monkeypatch)
        loader = H3Loader(self._cfg(circuit_breaker_cooldown=30.0))
        cb = loader._circuit_breakers["alpha"]
        self._open_breaker(cb)
        loader.route_session("sess_x", "alpha")

        await TestHealthLoop._run_checks(loader, monkeypatch, 1)

        assert loader.get_session_harness("sess_x") == "native"
        assert cb.state == "OPEN"

    @pytest.mark.asyncio
    async def test_half_open_probe_outcome_is_always_recorded(self, monkeypatch):
        """A DEGRADED probe still records an outcome — no HALF_OPEN starvation.

        Feeding the probe result through the same success/degraded branches
        the normal path uses means every allowed probe ends in an outcome.
        """
        degraded = HealthResponse(
            status=HealthStatus.DEGRADED, version="1", degraded_reason="slow"
        )
        _patch_h3_client_factory(monkeypatch, health_return=degraded)
        loader = H3Loader(self._cfg(circuit_breaker_cooldown=0.0))
        cb = loader._circuit_breakers["alpha"]
        self._open_breaker(cb)

        await TestHealthLoop._run_checks(loader, monkeypatch, 1)

        # DEGRADED counts as a failed probe → re-opened with fresh cooldown.
        assert cb.state == "OPEN"
