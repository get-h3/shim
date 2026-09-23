"""Target identity, stale-server warnings and ``--expect-fresh`` (DF-H3-26)
plus the ``test_2_4`` partial-turn hint (DF-H3-29).

The battery probes an *endpoint*, and an endpoint is a port any process may
already own.  A co-tenant harness left running from an earlier session
answered ``/v1/health`` on :9191 while the harness under test had silently
failed to bind (its bind error went to an unwritable log) — and the battery
still printed ``46/46 PASSED`` about the stranger's process.  These tests pin
the three defences: the identity line printed at connect, the advisory
``WARN:`` lines, and the ``--expect-fresh`` refusal.

Nothing here starts a server: the transport is a fake ``httpx.AsyncClient``
and the CLI-level tests stub ``H3TestBattery`` (the same patterns as
``tests/test_battery_detection.py`` and ``tests/test_cli.py``).
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest

from h3_shim.cli import _run_battery, main
from h3_shim.test_battery import (
    EXPECT_FRESH_MAX_UPTIME_S,
    NOT_REPORTED,
    STALE_UPTIME_WARN_S,
    H3TestBattery,
    NotH3EndpointError,
    TargetHealth,
)

# ── helpers ─────────────────────────────────────────────────────────────────


def _health_body(**overrides: object) -> dict:
    """A minimal valid ``/v1/health`` payload; *overrides* win."""
    body: dict = {
        "status": "ok",
        "version": "1.0.0",
        "protocol_version": "1.0",
        "transport": "rest",
        "capabilities": ["text", "end"],
    }
    body.update(overrides)
    return body


def _fake_response(status_code: int, json_data: object) -> MagicMock:
    """Build a stand-in ``httpx.Response``."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.reason_phrase = "OK"
    resp.text = json.dumps(json_data)
    resp.json = MagicMock(return_value=json_data)
    return resp


@pytest.fixture
def fake_client(monkeypatch):
    """Patch ``httpx.AsyncClient`` so the battery never touches a socket."""

    def _make(get_return=None, get_side_effect=None, post_return=None):
        fake = MagicMock()
        fake.get = AsyncMock(return_value=get_return, side_effect=get_side_effect)
        fake.post = AsyncMock(return_value=post_return)
        fake.aclose = AsyncMock()
        monkeypatch.setattr("httpx.AsyncClient", MagicMock(return_value=fake))
        return fake

    return _make


@dataclass
class _FakeResult:
    name: str = "health_ok"
    passed: bool = True
    detail: str = "ok"
    duration_ms: float = 1.0
    category: str = "Health & Protocol"


@dataclass
class _FakeReport:
    results: list = field(default_factory=lambda: [_FakeResult()])
    total: int = 1
    passed: int = 1
    failed: int = 0
    duration_ms: float = 1.0
    timestamp: str = "2026-01-01T00:00:00Z"

    @property
    def all_passing(self) -> bool:
        return self.failed == 0


def _stub_battery(monkeypatch, *, health: TargetHealth | None = None, report=None):
    """Install a stub ``H3TestBattery`` and return it for assertions."""
    fake = MagicMock()
    fake.connect = AsyncMock(return_value=health or TargetHealth(version="1.0.0"))
    fake.run_all = AsyncMock(return_value=report or _FakeReport())
    fake.close = AsyncMock()
    monkeypatch.setattr("h3_shim.cli.H3TestBattery", lambda *a, **kw: fake)
    return fake


# The exact false-PASS signature from the board row: a 2.6-day-old harness
# with 288 live sessions answered :9191 for someone else's failed bind.
STALE = TargetHealth(
    version="1.0.0",
    uptime_seconds=228122,
    active_sessions=288,
    capabilities=["text", "end"],
)


# ── identity line ───────────────────────────────────────────────────────────


class TestIdentityLine:
    def test_all_fields_present(self):
        line = TargetHealth(
            version="1.2.3",
            uptime_seconds=42,
            active_sessions=7,
            capabilities=["text"],
        ).identity_line()
        assert line == (
            "Target health: version=1.2.3 uptime_seconds=42 active_sessions=7"
        )

    def test_stale_co_tenant_signature_is_readable(self):
        """The row's evidence: 228122s uptime, 288 sessions, one line."""
        line = STALE.identity_line()
        assert "version=1.0.0" in line
        assert "uptime_seconds=228122" in line
        assert "active_sessions=288" in line
        assert line.count("\n") == 0

    @pytest.mark.parametrize(
        ("health", "expected"),
        [
            (
                TargetHealth(version=None, uptime_seconds=5, active_sessions=1),
                "version=(not reported) uptime_seconds=5 active_sessions=1",
            ),
            (
                TargetHealth(version="9.9", uptime_seconds=None, active_sessions=None),
                "version=9.9 uptime_seconds=(not reported) "
                "active_sessions=(not reported)",
            ),
            (
                TargetHealth(),
                "version=(not reported) uptime_seconds=(not reported) "
                "active_sessions=(not reported)",
            ),
        ],
    )
    def test_each_missing_field_degrades_independently(self, health, expected):
        """Every identity field is optional — never print a bare ``None``."""
        line = health.identity_line()
        assert expected in line
        assert "None" not in line

    def test_integral_float_renders_without_decimal(self):
        """``228122.0`` must not read as a precision bug."""
        line = TargetHealth(uptime_seconds=228122.0).identity_line()
        assert "uptime_seconds=228122 " in line

    def test_blank_version_is_not_reported(self):
        assert f"version={NOT_REPORTED}" in TargetHealth(version="  ").identity_line()


# ── warnings ────────────────────────────────────────────────────────────────


class TestWarnings:
    def test_no_warnings_for_a_fresh_capable_target(self):
        fresh = TargetHealth(version="1.0.0", uptime_seconds=12, capabilities=["text"])
        assert fresh.warnings() == []

    def test_uptime_over_threshold_warns(self):
        warnings = STALE.warnings()
        assert any(w.startswith("WARN:") for w in warnings)
        assert any("228122" in w for w in warnings)
        assert any(str(int(STALE_UPTIME_WARN_S)) in w for w in warnings)

    def test_uptime_at_threshold_does_not_warn(self):
        """Boundary: the rule is ``> 3600``, not ``>= 3600``."""
        health = TargetHealth(uptime_seconds=int(STALE_UPTIME_WARN_S))
        assert health.warnings() == []

    def test_missing_uptime_is_not_a_warning(self):
        """A field the target does not report cannot prove anything."""
        assert TargetHealth(uptime_seconds=None).warnings() == []

    def test_unparsable_uptime_is_not_a_warning(self):
        assert TargetHealth(uptime_seconds="two days").warnings() == []

    @pytest.mark.parametrize("caps", [[], ["audio"], ["end", "tool_call"]])
    def test_missing_text_capability_warns(self, caps):
        warnings = TargetHealth(uptime_seconds=1, capabilities=caps).warnings()
        assert any("omit" in w and "text" in w for w in warnings)

    def test_text_capability_present_does_not_warn(self):
        caps = ["text", "end", "tool_call"]
        assert TargetHealth(uptime_seconds=1, capabilities=caps).warnings() == []

    def test_unreported_capabilities_do_not_warn(self):
        """No layer here to claim the target omits ``text``."""
        assert TargetHealth(uptime_seconds=1, capabilities=None).warnings() == []

    def test_both_conditions_produce_two_lines(self):
        warnings = TargetHealth(uptime_seconds=99999, capabilities=["audio"]).warnings()
        assert len(warnings) == 2
        assert all(w.startswith("WARN:") for w in warnings)


# ── --expect-fresh ──────────────────────────────────────────────────────────


class TestExpectFreshViolation:
    def test_stale_target_message_is_exact(self):
        assert STALE.expect_fresh_violation() == (
            "expect-fresh violated: target uptime 228122s > 300s — you are "
            "probably testing a stale co-tenant process, not your harness"
        )

    def test_just_over_the_limit_fires(self):
        assert TargetHealth(uptime_seconds=301).expect_fresh_violation() is not None

    @pytest.mark.parametrize("uptime", [0, 12, 300, 299.5])
    def test_fresh_target_is_not_refused(self, uptime):
        assert TargetHealth(uptime_seconds=uptime).expect_fresh_violation() is None

    def test_unreported_uptime_is_not_refused(self):
        """Only evidence fires the flag — an honest harness may omit uptime."""
        assert TargetHealth(uptime_seconds=None).expect_fresh_violation() is None

    def test_limit_is_configurable(self):
        assert EXPECT_FRESH_MAX_UPTIME_S == 300
        assert TargetHealth(uptime_seconds=400).expect_fresh_violation(600) is None


# ── battery-level connect ───────────────────────────────────────────────────


class TestBatteryConnect:
    async def test_connect_returns_reported_identity(self, fake_client):
        fake_client(
            get_return=_fake_response(
                200,
                _health_body(
                    uptime_seconds=228122,
                    active_sessions=288,
                ),
            )
        )
        battery = H3TestBattery("http://localhost:9191")
        health = await battery.connect()
        assert health.uptime_seconds == 228122
        assert health.active_sessions == 288
        assert health.version == "1.0.0"
        assert health.capabilities == ["text", "end"]
        assert battery.health is health

    async def test_connect_degrades_unreported_and_unparsable_fields(self, fake_client):
        fake_client(
            get_return=_fake_response(
                200,
                _health_body(
                    uptime_seconds="unknown",
                    active_sessions=None,
                ),
            )
        )
        battery = H3TestBattery("http://localhost:9191")
        health = await battery.connect()
        assert health.uptime_seconds is None
        assert health.active_sessions is None
        assert NOT_REPORTED in health.identity_line()

    async def test_connect_keeps_whole_number_counts_integral(self, fake_client):
        """``288.0``/``"288"`` are 288 sessions; ``288.5`` is a malformed report."""
        fake_client(
            get_return=_fake_response(
                200, _health_body(uptime_seconds=5.0, active_sessions=288.0)
            )
        )
        battery = H3TestBattery("http://localhost:9191")
        health = await battery.connect()
        assert health.uptime_seconds == 5
        assert health.active_sessions == 288
        assert "uptime_seconds=5 " in health.identity_line()
        assert "active_sessions=288" in health.identity_line()

        fake_client(get_return=_fake_response(200, _health_body(active_sessions=288.5)))
        fractional = await H3TestBattery("http://localhost:9191").connect()
        assert fractional.active_sessions is None

    async def test_connect_raises_for_a_non_h3_target(self, fake_client):
        fake_client(
            get_return=_fake_response(
                200, {"status": "healthy", "service": "dexdat-api"}
            )
        )
        battery = H3TestBattery("http://localhost:9191")
        with pytest.raises(NotH3EndpointError):
            await battery.connect()
        assert battery.health is None

    async def test_health_is_none_until_probed(self, fake_client):
        fake_client(get_return=_fake_response(200, _health_body()))
        battery = H3TestBattery("http://localhost:9191")
        assert battery.health is None
        await battery.probe()
        assert battery.health is not None


# ── CLI reporting ───────────────────────────────────────────────────────────


class TestCliHealthReporting:
    @pytest.mark.asyncio
    async def test_identity_line_printed_before_the_battery(self, monkeypatch, capsys):
        _stub_battery(monkeypatch, health=STALE)
        code = await _run_battery("http://localhost:9191", None, False)
        assert code == 0
        out = capsys.readouterr().out
        assert "Target health: version=1.0.0" in out
        assert "uptime_seconds=228122" in out
        assert "active_sessions=288" in out
        # Printed at connect, i.e. above the report body.
        assert out.index("Target health:") < out.index("H3 Compliance Test Battery")

    @pytest.mark.asyncio
    async def test_identity_line_missing_fields_degrade(self, monkeypatch, capsys):
        _stub_battery(monkeypatch, health=TargetHealth(version="1.0.0"))
        await _run_battery("http://localhost:9191", None, False)
        out = capsys.readouterr().out
        assert "uptime_seconds=(not reported)" in out
        assert "active_sessions=(not reported)" in out

    @pytest.mark.asyncio
    async def test_stale_uptime_warns_on_stderr_and_still_runs(
        self, monkeypatch, capsys
    ):
        fake = _stub_battery(monkeypatch, health=STALE)
        code = await _run_battery("http://localhost:9191", None, False)
        err = capsys.readouterr().err
        assert "WARN:" in err
        assert "228122" in err
        # WARNs are advisory: the battery still ran, and the pass stayed a pass.
        assert code == 0
        assert fake.run_all.await_count == 1

    @pytest.mark.asyncio
    async def test_missing_text_capability_warns_on_stderr(self, monkeypatch, capsys):
        _stub_battery(
            monkeypatch,
            health=TargetHealth(uptime_seconds=5, capabilities=["audio"]),
        )
        code = await _run_battery("http://localhost:9191", None, False)
        assert code == 0
        err = capsys.readouterr().err
        assert "WARN:" in err
        assert "text" in err

    @pytest.mark.asyncio
    async def test_warnings_never_leak_into_stdout_json(self, monkeypatch, capsys):
        _stub_battery(monkeypatch, health=STALE)
        code = await _run_battery("http://localhost:9191", None, True)
        assert code == 0
        captured = capsys.readouterr()
        # stdout stays a pure JSON document...
        payload = json.loads(captured.out)
        # ...with the identity carried in the report instead.
        assert payload["target_health"]["uptime_seconds"] == 228122
        assert payload["target_health"]["active_sessions"] == 288
        assert payload["target_warnings"]
        assert "Target health:" in captured.err
        assert "WARN:" in captured.err

    @pytest.mark.asyncio
    async def test_failed_report_with_warnings_still_exits_1(self, monkeypatch, capsys):
        report = _FakeReport(
            results=[_FakeResult(passed=False, detail="boom")],
            total=1,
            passed=0,
            failed=1,
        )
        _stub_battery(monkeypatch, health=STALE, report=report)
        code = await _run_battery("http://localhost:9191", None, False)
        assert code == 1


class TestCliExpectFresh:
    @pytest.mark.asyncio
    async def test_stale_target_exits_1_with_the_cause(self, monkeypatch, capsys):
        fake = _stub_battery(monkeypatch, health=STALE)
        code = await _run_battery(
            "http://localhost:9191", None, False, expect_fresh=True
        )
        assert code == 1
        err = capsys.readouterr().err
        assert (
            "expect-fresh violated: target uptime 228122s > 300s — you are "
            "probably testing a stale co-tenant process, not your harness"
        ) in err
        # It stops BEFORE the battery: no test ran against the stranger.
        assert fake.run_all.await_count == 0

    @pytest.mark.asyncio
    async def test_fresh_target_runs_the_battery(self, monkeypatch, capsys):
        fresh = TargetHealth(
            version="1.0.0", uptime_seconds=11, active_sessions=0, capabilities=["text"]
        )
        fake = _stub_battery(monkeypatch, health=fresh)
        code = await _run_battery(
            "http://localhost:9191", None, False, expect_fresh=True
        )
        assert code == 0
        captured = capsys.readouterr()
        assert "expect-fresh violated" not in captured.err
        assert captured.out  # the report
        assert fake.run_all.await_count == 1

    @pytest.mark.asyncio
    async def test_boundary_300s_still_runs(self, monkeypatch):
        fake = _stub_battery(monkeypatch, health=TargetHealth(uptime_seconds=300))
        code = await _run_battery(
            "http://localhost:9191", None, False, expect_fresh=True
        )
        assert code == 0
        assert fake.run_all.await_count == 1

    @pytest.mark.asyncio
    async def test_unreported_uptime_still_runs(self, monkeypatch):
        """Absence of evidence is not a stale target."""
        fake = _stub_battery(monkeypatch, health=TargetHealth(uptime_seconds=None))
        code = await _run_battery(
            "http://localhost:9191", None, False, expect_fresh=True
        )
        assert code == 0
        assert fake.run_all.await_count == 1

    @pytest.mark.asyncio
    async def test_flag_is_off_by_default(self, monkeypatch):
        """Without the flag a stale target still gets a full run (and a WARN)."""
        fake = _stub_battery(monkeypatch, health=STALE)
        code = await _run_battery("http://localhost:9191", None, False)
        assert code == 0
        assert fake.run_all.await_count == 1

    @pytest.mark.asyncio
    async def test_json_mode_emits_a_violation_report(self, monkeypatch, capsys):
        _stub_battery(monkeypatch, health=STALE)
        code = await _run_battery(
            "http://localhost:9191", None, True, expect_fresh=True
        )
        assert code == 1
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["expect_fresh_violated"] is True
        assert payload["all_passing"] is False
        assert payload["total"] == 0
        assert payload["results"] == []
        assert payload["target_health"]["uptime_seconds"] == 228122
        assert "expect-fresh violated" in payload["reason"]
        assert "expect-fresh violated" in captured.err

    @pytest.mark.asyncio
    async def test_unreachable_target_still_exits_2(self, monkeypatch, capsys):
        """The connect failure keeps its own code — the flag adds none."""
        fake = MagicMock()
        fake.connect = AsyncMock(side_effect=NotH3EndpointError("connection error"))
        fake.run_all = AsyncMock(return_value=_FakeReport())
        fake.close = AsyncMock()
        monkeypatch.setattr("h3_shim.cli.H3TestBattery", lambda *a, **kw: fake)
        code = await _run_battery(
            "http://localhost:9191", None, False, expect_fresh=True
        )
        assert code == 2
        assert "does not look like an H3 endpoint" in capsys.readouterr().err
        assert fake.run_all.await_count == 0

    @pytest.mark.parametrize("as_json", [False, True])
    async def test_exit_code_vocabulary_never_grows(self, monkeypatch, as_json):
        """The documented 0/1/2 contract must not acquire a fourth value."""
        _stub_battery(monkeypatch, health=STALE)
        code = await _run_battery(
            "http://localhost:9191", None, as_json, expect_fresh=True
        )
        assert code in (0, 1, 2)


# ── flag plumbing ───────────────────────────────────────────────────────────


class TestFlagPlumbing:
    def test_help_lists_expect_fresh(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["h3-test", "--help"])
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert "--expect-fresh" in out
        # The help must say what it does, not just exist.
        assert "uptime" in out

    def test_h3_test_entry_point_plumbs_the_flag(self, monkeypatch):
        captured: dict = {}

        async def fake_run(args):
            captured["args"] = args
            return 0

        monkeypatch.setattr("h3_shim.cli._run", fake_run)
        monkeypatch.setattr(
            sys,
            "argv",
            ["h3-test", "--endpoint", "http://x:1", "--expect-fresh", "--json"],
        )
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 0
        assert captured["args"].expect_fresh is True
        assert captured["args"].json is True

    def test_h3_test_entry_point_defaults_the_flag_off(self, monkeypatch):
        captured: dict = {}

        async def fake_run(args):
            captured["args"] = args
            return 0

        monkeypatch.setattr("h3_shim.cli._run", fake_run)
        monkeypatch.setattr(sys, "argv", ["h3-test", "--endpoint", "http://x:1"])
        with pytest.raises(SystemExit):
            main()
        assert captured["args"].expect_fresh is False


# ── DF-H3-29: the partial-turn convention in the failure detail ─────────────


class TestPartialTurnHint:
    async def test_failure_detail_names_the_convention(self, fake_client):
        """``got True`` alone never told a docs-following developer the rule."""
        fake_client(
            post_return=_fake_response(
                200, {"decision": "text", "text": {"finished": True}}
            )
        )
        battery = H3TestBattery("http://localhost:9191")
        result = await battery.test_2_4_process_text_finished_false()
        assert result.passed is False
        assert "do not finish" in result.detail
        assert "finished=false" in result.detail
        assert "docs/integration.md" in result.detail
        assert "Partial turns" in result.detail

    async def test_passing_run_detail_stays_terse(self, fake_client):
        """The hint is a failure aid — a green test keeps its short detail."""
        fake_client(
            post_return=_fake_response(
                200, {"decision": "text", "text": {"finished": False}}
            )
        )
        battery = H3TestBattery("http://localhost:9191")
        result = await battery.test_2_4_process_text_finished_false()
        assert result.passed is True
        assert result.detail == "text.finished=false"

    async def test_skip_path_is_unchanged(self, fake_client):
        """A non-text decision is still skipped, not failed."""
        fake_client(post_return=_fake_response(200, {"decision": "end"}))
        battery = H3TestBattery("http://localhost:9191")
        result = await battery.test_2_4_process_text_finished_false()
        assert result.passed is True
        assert "docs/integration.md" not in result.detail


# ── sanity: the dataclass is the only identity carrier ──────────────────────


def test_target_health_is_plain_data():
    """No hidden state: the identity serialises for the JSON report."""
    assert asdict(STALE) == {
        "version": "1.0.0",
        "uptime_seconds": 228122,
        "active_sessions": 288,
        "capabilities": ["text", "end"],
    }
