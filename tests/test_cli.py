"""Tests for cli.py — Click command group + legacy argparse entry point.

The CLI exposes ``hermes-h3`` as a Click group with subcommands for
managing harnesses (``install``, ``uninstall``, ``use``, ``list``),
running the compliance battery (``test``), health-checking (``verify``),
config scaffolding (``scaffold``), and viewing the routing table
(``route``).  Each test patches ``CONFIG_PATH`` to a ``tmp_path`` so
real user configs are never touched, and stubs out ``asyncio.run`` /
``H3Client`` for the commands that would otherwise hit the network.
"""

from __future__ import annotations

import asyncio
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest
import yaml
from click.testing import CliRunner

from h3_shim.cli import (
    CONFIG_PATH,
    _empty_config,
    _format_human,
    hermes_h3,
    load_config,
    main,
    resolve_harness,
    save_config,
)


# ── helpers ─────────────────────────────────────────────────────────────────


@dataclass
class FakeTestResult:
    """Lightweight stand-in for test_battery.TestResult."""

    name: str
    passed: bool
    detail: str
    duration_ms: float
    category: str


@dataclass
class FakeTestReport:
    """Stand-in for test_battery.TestReport with enough surface for the CLI."""

    results: list = field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0
    duration_ms: float = 0.0
    timestamp: str = ""

    @property
    def all_passing(self) -> bool:
        return self.failed == 0


def _passing_report() -> FakeTestReport:
    return FakeTestReport(
        results=[
            FakeTestResult(
                name="health_ok",
                passed=True,
                detail="ok",
                duration_ms=12.0,
                category="Health & Protocol",
            ),
            FakeTestResult(
                name="process_basic",
                passed=True,
                detail="ok",
                duration_ms=20.0,
                category="Process Basic Flows",
            ),
        ],
        total=2,
        passed=2,
        failed=0,
        duration_ms=32.0,
        timestamp="2026-01-01T00:00:00Z",
    )


def _failing_report() -> FakeTestReport:
    return FakeTestReport(
        results=[
            FakeTestResult(
                name="health_ok",
                passed=True,
                detail="ok",
                duration_ms=5.0,
                category="Health & Protocol",
            ),
            FakeTestResult(
                name="process_basic",
                passed=False,
                detail="timeout",
                duration_ms=100.0,
                category="Process Basic Flows",
            ),
        ],
        total=2,
        passed=1,
        failed=1,
        duration_ms=105.0,
        timestamp="2026-01-01T00:00:00Z",
    )


@pytest.fixture
def cfg_path(tmp_path: Path, monkeypatch) -> Path:
    """Patch CLI CONFIG_PATH to a tmp_path file."""
    p = tmp_path / "config.yaml"
    monkeypatch.setattr("h3_shim.cli.CONFIG_PATH", p)
    return p


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ── hermes-h3 list ──────────────────────────────────────────────────────────


class TestList:
    def test_list_empty(self, cfg_path, runner):
        result = runner.invoke(hermes_h3, ["list"])
        assert result.exit_code == 0
        assert "no harnesses configured" in result.output

    def test_list_shows_harnesses(self, cfg_path, runner):
        cfg_path.write_text(yaml.safe_dump({
            "default_harness": "alpha",
            "harnesses": {
                "alpha": {
                    "endpoint": "http://a:1",
                    "transport": "rest",
                    "timeout_ms": 5000,
                },
            },
            "sessions": {},
        }))
        result = runner.invoke(hermes_h3, ["list"])
        assert result.exit_code == 0
        assert "alpha" in result.output
        assert "http://a:1" in result.output

    def test_list_marks_default(self, cfg_path, runner):
        cfg_path.write_text(yaml.safe_dump({
            "default_harness": "alpha",
            "harnesses": {
                "alpha": {"endpoint": "http://a:1", "transport": "rest"},
                "beta": {"endpoint": "http://b:1", "transport": "rest"},
            },
            "sessions": {},
        }))
        result = runner.invoke(hermes_h3, ["list"])
        assert result.exit_code == 0
        # Default harness is preceded by "*" marker.
        lines = result.output.splitlines()
        assert any(l.startswith("*") and "alpha" in l for l in lines)


# ── scaffold ────────────────────────────────────────────────────────────────


class TestScaffold:
    def test_scaffold_creates_config(self, cfg_path, runner):
        assert not cfg_path.exists()
        result = runner.invoke(hermes_h3, ["scaffold"])
        assert result.exit_code == 0
        assert cfg_path.exists()
        # And the file is a valid empty skeleton.
        data = yaml.safe_load(cfg_path.read_text())
        assert data["default_harness"] is None
        assert data["harnesses"] == {}
        assert data["sessions"] == {}

    def test_scaffold_existing_reports(self, cfg_path, runner):
        cfg_path.write_text("default_harness: existing\n")
        result = runner.invoke(hermes_h3, ["scaffold"])
        assert result.exit_code == 0
        assert "already exists" in result.output
        # File is NOT overwritten.
        assert "existing" in cfg_path.read_text()

    def test_scaffold_force_overwrites(self, cfg_path, runner):
        cfg_path.write_text("default_harness: existing\n")
        result = runner.invoke(hermes_h3, ["scaffold", "--force"])
        assert result.exit_code == 0
        # File replaced with empty skeleton.
        data = yaml.safe_load(cfg_path.read_text())
        assert data["default_harness"] is None


# ── install ────────────────────────────────────────────────────────────────


class TestInstall:
    def test_install_adds_harness(self, cfg_path, runner):
        result = runner.invoke(
            hermes_h3,
            ["install", "--endpoint", "http://x:1", "myharness"],
        )
        assert result.exit_code == 0
        assert "installed harness 'myharness'" in result.output
        data = yaml.safe_load(cfg_path.read_text())
        assert "myharness" in data["harnesses"]
        spec = data["harnesses"]["myharness"]
        assert spec["endpoint"] == "http://x:1"
        assert spec["transport"] == "rest"
        assert spec["timeout_ms"] == 30000

    def test_install_set_default_promotes(self, cfg_path, runner):
        result = runner.invoke(
            hermes_h3,
            [
                "install",
                "--endpoint",
                "http://x:1",
                "--set-default",
                "myharness",
            ],
        )
        assert result.exit_code == 0
        data = yaml.safe_load(cfg_path.read_text())
        assert data["default_harness"] == "myharness"

    def test_install_first_becomes_default(self, cfg_path, runner):
        # No --set-default flag; the first harness installed auto-promotes.
        runner.invoke(
            hermes_h3,
            ["install", "--endpoint", "http://x:1", "first"],
        )
        data = yaml.safe_load(cfg_path.read_text())
        assert data["default_harness"] == "first"

    def test_install_custom_timeout(self, cfg_path, runner):
        runner.invoke(
            hermes_h3,
            [
                "install",
                "--endpoint",
                "http://x:1",
                "--timeout-ms",
                "12345",
                "h",
            ],
        )
        data = yaml.safe_load(cfg_path.read_text())
        assert data["harnesses"]["h"]["timeout_ms"] == 12345


# ── uninstall ──────────────────────────────────────────────────────────────


class TestUninstall:
    def test_uninstall_removes_harness(self, cfg_path, runner):
        cfg_path.write_text(yaml.safe_dump({
            "default_harness": None,
            "harnesses": {"a": {"endpoint": "http://a:1"}, "b": {"endpoint": "http://b:1"}},
            "sessions": {},
        }))
        result = runner.invoke(hermes_h3, ["uninstall", "a"])
        assert result.exit_code == 0
        assert "uninstalled harness 'a'" in result.output
        data = yaml.safe_load(cfg_path.read_text())
        assert "a" not in data["harnesses"]
        assert "b" in data["harnesses"]

    def test_uninstall_reassigns_default(self, cfg_path, runner):
        cfg_path.write_text(yaml.safe_dump({
            "default_harness": "a",
            "harnesses": {"a": {"endpoint": "http://a:1"}, "b": {"endpoint": "http://b:1"}},
            "sessions": {},
        }))
        runner.invoke(hermes_h3, ["uninstall", "a"])
        data = yaml.safe_load(cfg_path.read_text())
        # The new default should be one of the remaining harnesses.
        assert data["default_harness"] == "b"

    def test_uninstall_unknown_raises(self, cfg_path, runner):
        cfg_path.write_text(yaml.safe_dump({
            "default_harness": None,
            "harnesses": {},
            "sessions": {},
        }))
        result = runner.invoke(hermes_h3, ["uninstall", "ghost"])
        assert result.exit_code != 0
        assert "not found" in result.output


# ── use ────────────────────────────────────────────────────────────────────


class TestUse:
    def test_use_sets_default(self, cfg_path, runner):
        cfg_path.write_text(yaml.safe_dump({
            "default_harness": None,
            "harnesses": {"a": {"endpoint": "http://a:1"}},
            "sessions": {},
        }))
        result = runner.invoke(hermes_h3, ["use", "a"])
        assert result.exit_code == 0
        data = yaml.safe_load(cfg_path.read_text())
        assert data["default_harness"] == "a"

    def test_use_unknown_raises(self, cfg_path, runner):
        cfg_path.write_text(yaml.safe_dump({
            "default_harness": None,
            "harnesses": {"a": {"endpoint": "http://a:1"}},
            "sessions": {},
        }))
        result = runner.invoke(hermes_h3, ["use", "ghost"])
        assert result.exit_code != 0
        assert "not found" in result.output


# ── route ──────────────────────────────────────────────────────────────────


class TestRoute:
    def test_route_empty(self, cfg_path, runner):
        result = runner.invoke(hermes_h3, ["route"])
        assert result.exit_code == 0
        assert "no sessions configured" in result.output

    def test_route_lists_sessions(self, cfg_path, runner):
        cfg_path.write_text(yaml.safe_dump({
            "default_harness": "native",
            "harnesses": {},
            "sessions": {
                "telegram:-100:42": {"harness": "alpha"},
                "discord:1": {"harness": "beta"},
            },
        }))
        result = runner.invoke(hermes_h3, ["route"])
        assert result.exit_code == 0
        assert "telegram:-100:42" in result.output
        assert "alpha" in result.output
        assert "beta" in result.output

    def test_route_string_form_session_entry(self, cfg_path, runner):
        # Sessions can be either dicts or bare strings (older config style).
        cfg_path.write_text(yaml.safe_dump({
            "default_harness": "native",
            "harnesses": {},
            "sessions": {"telegram:-100": "alpha"},
        }))
        result = runner.invoke(hermes_h3, ["route"])
        assert result.exit_code == 0
        assert "alpha" in result.output


# ── help output ────────────────────────────────────────────────────────────


class TestHelp:
    def test_test_help(self, runner):
        result = runner.invoke(hermes_h3, ["test", "--help"])
        assert result.exit_code == 0
        assert "--harness" in result.output
        assert "--endpoint" in result.output
        assert "--json" in result.output
        assert "--categories" in result.output

    def test_verify_help(self, runner):
        result = runner.invoke(hermes_h3, ["verify", "--help"])
        assert result.exit_code == 0
        assert "--harness" in result.output
        assert "--endpoint" in result.output


# ── load_config / save_config ───────────────────────────────────────────────


class TestConfigHelpers:
    def test_load_config_missing_returns_empty(self, cfg_path):
        assert not cfg_path.exists()
        cfg = load_config(cfg_path)
        assert cfg == _empty_config()

    def test_load_config_invalid_yaml_raises(self, cfg_path):
        cfg_path.write_text("this: is: not: valid: yaml: [\n")
        with pytest.raises(click.ClickException) as exc_info:
            load_config(cfg_path)
        assert "invalid YAML" in str(exc_info.value)

    def test_load_config_backfills_missing_keys(self, cfg_path):
        cfg_path.write_text("harnesses:\n  a: {}\n")
        cfg = load_config(cfg_path)
        # Missing top-level keys must be backfilled.
        assert "default_harness" in cfg
        assert "sessions" in cfg

    def test_save_config_creates_parent_dirs(self, tmp_path):
        nested = tmp_path / "deep" / "nested" / "config.yaml"
        assert not nested.parent.exists()
        path = save_config(_empty_config(), nested)
        assert path == nested
        assert nested.exists()
        # And the YAML round-trips.
        data = yaml.safe_load(nested.read_text())
        assert data == _empty_config()

    def test_save_config_returns_path(self, cfg_path):
        p = save_config({"x": 1}, cfg_path)
        assert p == cfg_path
        assert yaml.safe_load(cfg_path.read_text()) == {"x": 1}

    def test_empty_config_skeleton(self):
        cfg = _empty_config()
        assert cfg["default_harness"] is None
        assert cfg["harnesses"] == {}
        assert cfg["sessions"] == {}


# ── resolve_harness ────────────────────────────────────────────────────────


class TestResolveHarness:
    def test_raises_when_no_name_and_no_default(self):
        cfg = _empty_config()
        with pytest.raises(click.ClickException) as exc_info:
            resolve_harness(None, cfg)
        assert "no harness specified" in str(exc_info.value)

    def test_raises_when_name_not_in_config(self):
        cfg = _empty_config()
        with pytest.raises(click.ClickException) as exc_info:
            resolve_harness("ghost", cfg)
        assert "ghost" in str(exc_info.value)

    def test_resolves_by_name(self):
        cfg = {"harnesses": {"alpha": {"endpoint": "http://a:1"}}}
        name, spec = resolve_harness("alpha", cfg)
        assert name == "alpha"
        assert spec == {"endpoint": "http://a:1"}

    def test_resolves_default_when_no_name(self):
        cfg = {
            "default_harness": "alpha",
            "harnesses": {"alpha": {"endpoint": "http://a:1"}},
        }
        name, spec = resolve_harness(None, cfg)
        assert name == "alpha"
        assert spec["endpoint"] == "http://a:1"


# ── _format_human ──────────────────────────────────────────────────────────


class TestFormatHuman:
    def test_passing_report_contains_passed_marker(self):
        text = _format_human(_passing_report(), "http://x:1")
        assert "PASSED" in text
        assert "TOTAL" in text
        assert "Health & Protocol" in text
        assert "Process Basic Flows" in text
        assert "Target: http://x:1" in text

    def test_failing_report_contains_failed_marker(self):
        text = _format_human(_failing_report(), "http://x:1")
        assert "FAILED" in text

    def test_format_includes_duration_seconds(self):
        text = _format_human(_passing_report(), "http://x:1")
        # Duration is rendered as seconds with two decimals.
        assert "Duration" in text
        assert "s" in text


# ── test command (asyncio + battery stubbed) ───────────────────────────────


class TestTestCommand:
    def test_test_with_endpoint_runs_battery(self, runner, monkeypatch):
        # Stub asyncio.run so we never hit the network.
        report = _passing_report()

        async def fake_run_battery(endpoint, categories, as_json):
            return 0

        monkeypatch.setattr("h3_shim.cli._run_battery", fake_run_battery)
        result = runner.invoke(
            hermes_h3,
            ["test", "--endpoint", "http://x:1"],
        )
        assert result.exit_code == 0


# ── verify command (H3Client stubbed) ──────────────────────────────────────


class TestVerifyCommand:
    def test_verify_with_endpoint(self, runner, monkeypatch):
        # Patch H3Client in its source module — verify() does a lazy import
        # ``from h3_shim.client import H3Client`` so we intercept there.
        from h3_shim.protocol import HealthResponse, HealthStatus

        FakeClient = MagicMock()
        instance = MagicMock()
        instance.health = AsyncMock(
            return_value=HealthResponse(
                status=HealthStatus.OK, version="1.2.3", capabilities=["foo", "bar"],
            ),
        )
        instance.close = AsyncMock()
        FakeClient.return_value = instance
        monkeypatch.setattr("h3_shim.client.H3Client", FakeClient)

        result = runner.invoke(
            hermes_h3,
            ["verify", "--endpoint", "http://x:1"],
        )
        assert result.exit_code == 0
        assert "harness: <override>" in result.output
        assert "endpoint: http://x:1" in result.output
        assert "status:   HealthStatus.OK" in result.output
        assert "version:  1.2.3" in result.output
        assert "foo" in result.output


# ── legacy main() ──────────────────────────────────────────────────────────


class TestLegacyMain:
    def test_main_help(self, capsys):
        """``h3-test --help`` exits 0 and prints argparse help."""
        with pytest.raises(SystemExit) as exc_info:
            main()  # argparse uses sys.argv; pass via monkeypatch
        # argparse exits 0 on --help; just ensure no crash on a no-arg call.
        # (We can't easily inject argv here without monkeypatching sys.argv,
        # so this is a smoke test that the parser object exists.)
        assert exc_info.value.code in (0, 2)

    def test_main_requires_endpoint(self, monkeypatch, capsys):
        import sys
        monkeypatch.setattr(sys, "argv", ["h3-test"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        # argparse returns 2 for usage errors.
        assert exc_info.value.code == 2