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

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import click
import pytest
import yaml
from click.testing import CliRunner
from pydantic import ValidationError

from h3_shim.cli import (
    CONFIG_PATH,
    CONFIG_PATH_ENV,
    _empty_config,
    _format_human,
    _latency_stats,
    _run_battery,
    default_config_path,
    hermes_h3,
    load_config,
    main,
    resolve_harness,
    save_config,
)
from h3_shim.protocol import HealthResponse, HealthStatus
from h3_shim.test_battery import CATEGORIES, category_token

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


def _stub_health_client(
    monkeypatch,
    *,
    status: HealthStatus | None = None,
    version: str = "1.2.3",
    degraded_reason: str | None = None,
    error: Exception | None = None,
) -> MagicMock:
    """Stub the health client that ``install`` / ``verify`` import lazily.

    Keeps every install/verify test deterministic — no socket is ever
    opened.  *error* makes ``health()`` raise (unreachable endpoint or a
    non-H3 payload); otherwise the stub answers like a healthy H3
    harness.  Returns the patched class so a caller can assert how the
    probe was constructed (endpoint / transport / timeout_ms).
    """
    fake_client = MagicMock()
    instance = MagicMock()
    if error is not None:
        instance.health = AsyncMock(side_effect=error)
    else:
        instance.health = AsyncMock(
            return_value=HealthResponse(
                status=status if status is not None else HealthStatus.OK,
                version=version,
                capabilities=[],
                degraded_reason=degraded_reason,
            )
        )
    instance.close = AsyncMock()
    fake_client.return_value = instance
    monkeypatch.setattr("h3_shim.client.H3Client", fake_client)
    return fake_client


@pytest.fixture
def cfg_path(tmp_path: Path, monkeypatch) -> Path:
    """Patch CLI CONFIG_PATH to a tmp_path file."""
    p = tmp_path / "config.yaml"
    monkeypatch.setattr("h3_shim.cli.CONFIG_PATH", p)
    return p


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch):
    """Keep every test independent of an ambient ``$HERMES_H3_CONFIG``.

    The config path now honors that variable (DF-H3-10), so a developer
    or judge shell with it exported would otherwise silently repoint
    every un-``--config``'d invocation away from the patched
    ``CONFIG_PATH``.
    """
    monkeypatch.delenv(CONFIG_PATH_ENV, raising=False)


# ── hermes-h3 list ──────────────────────────────────────────────────────────


class TestList:
    def test_list_empty(self, cfg_path, runner):
        result = runner.invoke(hermes_h3, ["list"])
        assert result.exit_code == 0
        assert "no harnesses configured" in result.output

    def test_list_shows_harnesses(self, cfg_path, runner):
        cfg_path.write_text(
            yaml.safe_dump(
                {
                    "default_harness": "alpha",
                    "harnesses": {
                        "alpha": {
                            "endpoint": "http://a:1",
                            "transport": "rest",
                            "timeout_ms": 5000,
                        },
                    },
                    "sessions": {},
                }
            )
        )
        result = runner.invoke(hermes_h3, ["list"])
        assert result.exit_code == 0
        assert "alpha" in result.output
        assert "http://a:1" in result.output

    def test_list_marks_default(self, cfg_path, runner):
        cfg_path.write_text(
            yaml.safe_dump(
                {
                    "default_harness": "alpha",
                    "harnesses": {
                        "alpha": {"endpoint": "http://a:1", "transport": "rest"},
                        "beta": {"endpoint": "http://b:1", "transport": "rest"},
                    },
                    "sessions": {},
                }
            )
        )
        result = runner.invoke(hermes_h3, ["list"])
        assert result.exit_code == 0
        # Default harness is preceded by "*" marker.
        lines = result.output.splitlines()
        assert any(line.startswith("*") and "alpha" in line for line in lines)

    def test_list_accepts_config_after_subcommand(self, tmp_path, runner):
        """--config after the subcommand works (GAP-014)."""
        custom = tmp_path / "custom.yaml"
        custom.write_text(
            yaml.safe_dump(
                {
                    "default_harness": "zeta",
                    "harnesses": {
                        "zeta": {
                            "endpoint": "http://z:9",
                            "transport": "rest",
                            "timeout_ms": 5000,
                        },
                    },
                    "sessions": {},
                }
            )
        )
        result = runner.invoke(hermes_h3, ["list", "--config", str(custom)])
        assert result.exit_code == 0
        assert "zeta" in result.output
        assert "http://z:9" in result.output

    def test_config_after_subcommand_beats_group_option(self, tmp_path, runner):
        """Subcommand --config overrides the group-level --config (GAP-014)."""
        group_cfg = tmp_path / "group.yaml"
        group_cfg.write_text(yaml.safe_dump({"harnesses": {}, "sessions": {}}))
        sub_cfg = tmp_path / "sub.yaml"
        sub_cfg.write_text(
            yaml.safe_dump(
                {
                    "default_harness": "omega",
                    "harnesses": {
                        "omega": {
                            "endpoint": "http://o:7",
                            "transport": "rest",
                        },
                    },
                    "sessions": {},
                }
            )
        )
        result = runner.invoke(
            hermes_h3,
            ["--config", str(group_cfg), "list", "--config", str(sub_cfg)],
        )
        assert result.exit_code == 0
        assert "omega" in result.output
        assert "http://o:7" in result.output


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

    def test_scaffold_lang_go_generates_files(self, tmp_path, runner):
        result = runner.invoke(
            hermes_h3, ["scaffold", "--lang", "go", "--output-dir", str(tmp_path)]
        )
        assert result.exit_code == 0
        project = tmp_path / "h3-harness-go"
        assert project.is_dir()
        assert (project / "main.go").is_file()
        assert (project / "go.mod").is_file()
        # go.mod has the substituted module name.
        mod = (project / "go.mod").read_text()
        assert "module h3-harness-go" in mod
        assert "github.com/get-h3/sdk-go" in mod
        # Instructions printed.
        assert "h3-test --endpoint http://localhost:9191" in result.output

    def test_scaffold_lang_py_generates_files(self, tmp_path, runner):
        result = runner.invoke(
            hermes_h3, ["scaffold", "--lang", "py", "--output-dir", str(tmp_path)]
        )
        assert result.exit_code == 0
        project = tmp_path / "h3-harness-py"
        assert project.is_dir()
        assert (project / "main.py").is_file()
        assert (project / "requirements.txt").is_file()
        assert (project / "pyproject.toml").is_file()
        assert "h3-test --endpoint http://localhost:9191" in result.output
        # GAP-039: banner must guide the generated-harness dev through a venv
        # before any pip install (PEP 668 distros refuse bare pip).
        assert "python3 -m venv .venv" in result.output
        assert "source .venv/bin/activate" in result.output
        assert "pip install -e ." in result.output

    def test_scaffold_lang_ts_generates_files(self, tmp_path, runner):
        result = runner.invoke(
            hermes_h3, ["scaffold", "--lang", "ts", "--output-dir", str(tmp_path)]
        )
        assert result.exit_code == 0
        project = tmp_path / "h3-harness-ts"
        assert project.is_dir()
        assert (project / "index.ts").is_file()
        assert (project / "package.json").is_file()
        assert (project / "tsconfig.json").is_file()
        assert "h3-test --endpoint http://localhost:9191" in result.output

    def test_scaffold_lang_invalid_rejected(self, tmp_path, runner):
        result = runner.invoke(
            hermes_h3, ["scaffold", "--lang", "rust", "--output-dir", str(tmp_path)]
        )
        assert result.exit_code != 0
        output = result.output.lower()
        assert "unsupported" in output or "invalid" in output

    def test_scaffold_lang_aliases_accepted(self, tmp_path, runner):
        """``--lang python``/``typescript`` map to py/ts (README surface)."""
        for alias, short in (("python", "py"), ("typescript", "ts")):
            result = runner.invoke(
                hermes_h3,
                ["scaffold", "--lang", alias, "--output-dir", str(tmp_path)],
            )
            assert result.exit_code == 0, result.output
            assert (tmp_path / f"h3-harness-{short}").is_dir()

    def test_scaffold_lang_project_exists_without_force(self, tmp_path, runner):
        project = tmp_path / "h3-harness-go"
        project.mkdir()
        result = runner.invoke(
            hermes_h3, ["scaffold", "--lang", "go", "--output-dir", str(tmp_path)]
        )
        assert result.exit_code != 0
        assert "already exists" in result.output

    def test_scaffold_lang_force_overwrites_project(self, tmp_path, runner):
        project = tmp_path / "h3-harness-go"
        project.mkdir()
        result = runner.invoke(
            hermes_h3,
            ["scaffold", "--lang", "go", "--output-dir", str(tmp_path), "--force"],
        )
        assert result.exit_code == 0
        assert (project / "main.go").is_file()

    def test_scaffold_custom_config_path(self, tmp_path, runner):
        """``scaffold --config <path>`` creates config at the custom path."""
        custom = tmp_path / "custom.yaml"
        assert not custom.exists()
        result = runner.invoke(hermes_h3, ["scaffold", "--config", str(custom)])
        assert result.exit_code == 0
        assert custom.exists()
        data = yaml.safe_load(custom.read_text())
        assert data["default_harness"] is None
        assert data["harnesses"] == {}
        assert data["sessions"] == {}


# ── install ────────────────────────────────────────────────────────────────


class TestInstall:
    def test_install_adds_harness(self, cfg_path, runner, monkeypatch):
        _stub_health_client(monkeypatch)
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

    def test_install_rejects_grpc_transport(self, cfg_path, runner):
        """GAP-030: unsupported transports fail fast, never silently stored."""
        result = runner.invoke(
            hermes_h3,
            [
                "install",
                "--endpoint",
                "http://x:1",
                "--transport",
                "grpc",
                "myharness",
            ],
        )
        assert result.exit_code != 0
        assert "grpc transport not supported yet" in result.output
        # The config must not be written with a silently-ignored transport.
        assert not cfg_path.exists()

    def test_install_set_default_promotes(self, cfg_path, runner, monkeypatch):
        _stub_health_client(monkeypatch)
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

    def test_install_first_becomes_default(self, cfg_path, runner, monkeypatch):
        # No --set-default flag; the first harness installed auto-promotes.
        _stub_health_client(monkeypatch)
        runner.invoke(
            hermes_h3,
            ["install", "--endpoint", "http://x:1", "first"],
        )
        data = yaml.safe_load(cfg_path.read_text())
        assert data["default_harness"] == "first"

    def test_install_custom_timeout(self, cfg_path, runner, monkeypatch):
        _stub_health_client(monkeypatch)
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

    def test_install_name_flag_alias(self, cfg_path, runner, monkeypatch):
        """DF-H3-3: ``--name`` is an alias for the positional NAME."""
        _stub_health_client(monkeypatch)
        result = runner.invoke(
            hermes_h3,
            ["install", "--name", "flagharness", "--endpoint", "http://x:1"],
        )
        assert result.exit_code == 0
        assert "installed harness 'flagharness'" in result.output
        data = yaml.safe_load(cfg_path.read_text())
        assert "flagharness" in data["harnesses"]
        assert data["harnesses"]["flagharness"]["endpoint"] == "http://x:1"
        assert data["default_harness"] == "flagharness"

    def test_install_without_name_fails_loudly(self, cfg_path, runner):
        """Neither NAME nor --name: fail loudly, never install unnamed."""
        result = runner.invoke(hermes_h3, ["install", "--endpoint", "http://x:1"])
        assert result.exit_code != 0
        assert "requires a harness name" in result.output
        # Nothing may be written — no phantom unnamed entry.
        assert not cfg_path.exists()

    def test_install_positional_wins_over_name_flag(
        self, cfg_path, runner, monkeypatch
    ):
        """With both forms given, the positional NAME wins (verify's rule)."""
        _stub_health_client(monkeypatch)
        result = runner.invoke(
            hermes_h3,
            ["install", "pos", "--name", "flag", "--endpoint", "http://x:1"],
        )
        assert result.exit_code == 0
        data = yaml.safe_load(cfg_path.read_text())
        assert "pos" in data["harnesses"]
        assert "flag" not in data["harnesses"]
        assert data["default_harness"] == "pos"


# ── install: endpoint health check (DF-H3-SHIM-FOREMAN-3) ───────────────────
# Verified behaviour before the fix: ``install dead-harness --endpoint
# http://localhost:9999`` exited 0 and wrote the entry; the failure only
# appeared later, at ``verify``/first session.  The install path must now
# probe ``GET /v1/health`` first and fail without writing.  Every test
# stubs the health client, so nothing here touches the network.


class TestInstallHealthCheck:
    @staticmethod
    def _seed_config(cfg_path: Path, harness: str = "alpha") -> str:
        """Write a one-harness config and return its exact bytes."""
        cfg_path.write_text(
            yaml.safe_dump(
                {
                    "default_harness": harness,
                    "harnesses": {
                        harness: {
                            "endpoint": "http://a:1",
                            "transport": "rest",
                            "timeout_ms": 5000,
                        },
                    },
                    "sessions": {},
                }
            )
        )
        return cfg_path.read_text()

    def test_install_unreachable_endpoint_fails_and_writes_nothing(
        self, cfg_path, runner, monkeypatch
    ):
        """The dogfood scenario: a dead endpoint is refused, exit non-zero."""
        _stub_health_client(monkeypatch, error=ConnectionError("connection refused"))
        result = runner.invoke(
            hermes_h3,
            [
                "install",
                "dead-harness",
                "--endpoint",
                "http://localhost:9999",
            ],
        )
        assert result.exit_code != 0
        # Actionable: names the endpoint, the cause, and what to do next.
        assert "http://localhost:9999" in result.output
        assert "health check" in result.output
        assert "connection refused" in result.output
        assert "Nothing was written" in result.output
        assert "hermes-h3 verify --endpoint http://localhost:9999" in result.output
        # …and the harness/config really are absent.
        assert not cfg_path.exists()

    def test_install_unreachable_leaves_existing_config_untouched(
        self, cfg_path, runner, monkeypatch
    ):
        """A failed install must not touch the config or the default."""
        before = self._seed_config(cfg_path, "alpha")
        _stub_health_client(monkeypatch, error=ConnectionError("refused"))
        result = runner.invoke(
            hermes_h3,
            [
                "install",
                "beta",
                "--endpoint",
                "http://localhost:9999",
                "--set-default",
            ],
        )
        assert result.exit_code != 0
        assert cfg_path.read_text() == before
        data = yaml.safe_load(cfg_path.read_text())
        assert "beta" not in data["harnesses"]
        assert data["default_harness"] == "alpha"

    def test_install_failure_does_not_create_config_dir(
        self, tmp_path, runner, monkeypatch
    ):
        """No partial write either: the config dir is not even created."""
        nested = tmp_path / "nested" / "h3" / "config.yaml"
        _stub_health_client(monkeypatch, error=ConnectionError("refused"))
        result = runner.invoke(
            hermes_h3,
            [
                "install",
                "dead",
                "--endpoint",
                "http://localhost:9999",
                "--config",
                str(nested),
            ],
        )
        assert result.exit_code != 0
        assert not nested.parent.exists()
        assert not nested.exists()

    def test_install_unhealthy_status_fails_and_writes_nothing(
        self, cfg_path, runner, monkeypatch
    ):
        """A reachable-but-degraded harness is not installable either."""
        _stub_health_client(
            monkeypatch,
            status=HealthStatus.DEGRADED,
            degraded_reason="database unreachable",
        )
        result = runner.invoke(
            hermes_h3,
            ["install", "wobbly", "--endpoint", "http://x:1"],
        )
        assert result.exit_code != 0
        assert "'degraded'" in result.output
        assert "database unreachable" in result.output
        assert "Nothing was written" in result.output
        assert not cfg_path.exists()

    @staticmethod
    def _invalid_payload_errors() -> tuple[ValidationError, json.JSONDecodeError]:
        """Real decoder/validator errors a non-H3 200 response produces."""
        try:
            HealthResponse.model_validate({"status": "ok"})
        except ValidationError as exc:  # pragma: no cover - defensive else
            validation = exc
        else:
            raise AssertionError("expected a ValidationError (version is missing)")
        try:
            json.loads("<html>not an H3 harness</html>")
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive else
            decode = exc
        else:
            raise AssertionError("expected a JSONDecodeError")
        return validation, decode

    def test_install_non_h3_payload_fails_and_writes_nothing(
        self, cfg_path, runner, monkeypatch
    ):
        """A 200 that is not an H3 health payload is refused, not stored."""
        validation, _ = self._invalid_payload_errors()
        _stub_health_client(monkeypatch, error=validation)
        result = runner.invoke(
            hermes_h3,
            ["install", "not-h3", "--endpoint", "http://x:1"],
        )
        assert result.exit_code != 0
        assert "not a valid H3 health payload" in result.output
        assert "version" in result.output
        assert not cfg_path.exists()

    def test_install_non_json_response_fails_with_clear_reason(
        self, cfg_path, runner, monkeypatch
    ):
        """Something else on the port (a web server) is named as such."""
        _, decode = self._invalid_payload_errors()
        _stub_health_client(monkeypatch, error=decode)
        result = runner.invoke(
            hermes_h3,
            ["install", "wrong-port", "--endpoint", "http://x:1"],
        )
        assert result.exit_code != 0
        assert "answered, but not with JSON" in result.output
        assert not cfg_path.exists()

    def test_install_healthy_endpoint_succeeds(self, cfg_path, runner, monkeypatch):
        """The healthy path still installs, and reports the probe result."""
        fake_client = _stub_health_client(monkeypatch, version="9.9.9")
        result = runner.invoke(
            hermes_h3,
            ["install", "healthy", "--endpoint", "http://x:1"],
        )
        assert result.exit_code == 0
        assert "installed harness 'healthy'" in result.output
        assert "health:   ok (version 9.9.9)" in result.output
        data = yaml.safe_load(cfg_path.read_text())
        assert data["harnesses"]["healthy"] == {
            "endpoint": "http://x:1",
            "transport": "rest",
            "timeout_ms": 30000,
        }
        # First harness installed still becomes the default.
        assert data["default_harness"] == "healthy"
        # The probe used the endpoint that was about to be persisted and
        # the connection was closed (no leaked client).
        assert fake_client.call_args.kwargs["endpoint"] == "http://x:1"
        assert fake_client.call_args.kwargs["transport"] == "rest"
        assert fake_client.call_args.kwargs["timeout_ms"] == 30000

    def test_install_healthy_with_existing_flags(self, cfg_path, runner, monkeypatch):
        """--transport/--timeout-ms/--set-default/--name keep working."""
        self._seed_config(cfg_path, "alpha")
        fake_client = _stub_health_client(monkeypatch)
        result = runner.invoke(
            hermes_h3,
            [
                "install",
                "--name",
                "beta",
                "--endpoint",
                "http://b:2",
                "--transport",
                "rest",
                "--timeout-ms",
                "12345",
                "--set-default",
            ],
        )
        assert result.exit_code == 0
        data = yaml.safe_load(cfg_path.read_text())
        assert data["harnesses"]["beta"] == {
            "endpoint": "http://b:2",
            "transport": "rest",
            "timeout_ms": 12345,
        }
        assert data["harnesses"]["alpha"]["endpoint"] == "http://a:1"
        assert data["default_harness"] == "beta"
        # The probe is made with the options being persisted — including the
        # custom timeout — not hardcoded defaults.
        assert fake_client.call_args.kwargs == {
            "endpoint": "http://b:2",
            "transport": "rest",
            "timeout_ms": 12345,
        }

    def test_install_probe_client_is_closed(self, cfg_path, runner, monkeypatch):
        """The probe must release its client even on the happy path."""
        fake_client = _stub_health_client(monkeypatch)
        result = runner.invoke(
            hermes_h3,
            ["install", "h", "--endpoint", "http://x:1"],
        )
        assert result.exit_code == 0
        fake_client.return_value.close.assert_awaited()

    def test_install_transport_validation_precedes_health_check(
        self, cfg_path, runner, monkeypatch
    ):
        """Unsupported transports fail on their own error, without probing."""
        fake_client = _stub_health_client(monkeypatch)
        result = runner.invoke(
            hermes_h3,
            [
                "install",
                "h",
                "--endpoint",
                "http://x:1",
                "--transport",
                "grpc",
            ],
        )
        assert result.exit_code != 0
        assert "grpc transport not supported yet" in result.output
        assert not fake_client.called
        assert not cfg_path.exists()


# ── uninstall ──────────────────────────────────────────────────────────────


class TestUninstall:
    def test_uninstall_removes_harness(self, cfg_path, runner):
        cfg_path.write_text(
            yaml.safe_dump(
                {
                    "default_harness": None,
                    "harnesses": {
                        "a": {"endpoint": "http://a:1"},
                        "b": {"endpoint": "http://b:1"},
                    },
                    "sessions": {},
                }
            )
        )
        result = runner.invoke(hermes_h3, ["uninstall", "a"])
        assert result.exit_code == 0
        assert "uninstalled harness 'a'" in result.output
        data = yaml.safe_load(cfg_path.read_text())
        assert "a" not in data["harnesses"]
        assert "b" in data["harnesses"]

    def test_uninstall_reassigns_default(self, cfg_path, runner):
        cfg_path.write_text(
            yaml.safe_dump(
                {
                    "default_harness": "a",
                    "harnesses": {
                        "a": {"endpoint": "http://a:1"},
                        "b": {"endpoint": "http://b:1"},
                    },
                    "sessions": {},
                }
            )
        )
        runner.invoke(hermes_h3, ["uninstall", "a"])
        data = yaml.safe_load(cfg_path.read_text())
        # The new default should be one of the remaining harnesses.
        assert data["default_harness"] == "b"

    def test_uninstall_unknown_raises(self, cfg_path, runner):
        cfg_path.write_text(
            yaml.safe_dump(
                {
                    "default_harness": None,
                    "harnesses": {},
                    "sessions": {},
                }
            )
        )
        result = runner.invoke(hermes_h3, ["uninstall", "ghost"])
        assert result.exit_code != 0
        assert "not found" in result.output


# ── use ────────────────────────────────────────────────────────────────────


class TestUse:
    def test_use_sets_default(self, cfg_path, runner):
        cfg_path.write_text(
            yaml.safe_dump(
                {
                    "default_harness": None,
                    "harnesses": {"a": {"endpoint": "http://a:1"}},
                    "sessions": {},
                }
            )
        )
        result = runner.invoke(hermes_h3, ["use", "a"])
        assert result.exit_code == 0
        data = yaml.safe_load(cfg_path.read_text())
        assert data["default_harness"] == "a"

    def test_use_unknown_raises(self, cfg_path, runner):
        cfg_path.write_text(
            yaml.safe_dump(
                {
                    "default_harness": None,
                    "harnesses": {"a": {"endpoint": "http://a:1"}},
                    "sessions": {},
                }
            )
        )
        result = runner.invoke(hermes_h3, ["use", "ghost"])
        assert result.exit_code != 0
        assert "not found" in result.output


# ── route ──────────────────────────────────────────────────────────────────


class TestRoute:
    def test_route_empty(self, cfg_path, runner):
        result = runner.invoke(hermes_h3, ["route"])
        assert result.exit_code == 0
        assert "no sessions configured" in result.output

    # ── DF-H3-SHIM-FOREMAN-5: actionable empty state ─────────────────────

    def test_route_empty_explains_how_to_add_a_route(self, cfg_path, runner):
        # An empty table must not be a dead end: the message has to name the
        # resolved config file, the `sessions:` key, and show a concrete
        # session → harness example a CLI-only user can copy.
        result = runner.invoke(hermes_h3, ["route"])
        assert result.exit_code == 0
        out = result.output
        assert str(cfg_path) in out
        assert "sessions:" in out
        assert "telegram:-1001234567890" in out
        assert "harness: my-harness" in out
        # …and it must say why the table can be empty without a manual edit:
        # the loader/shim can pin routes programmatically at runtime.
        assert "route_session" in out
        assert "running shim" in out
        assert "never written back" in out
        # An empty state is not a listing — no table header.
        assert "SESSION" not in out

    def test_route_empty_reports_resolved_config_path(self, tmp_path, runner):
        # The path printed is the one the command actually read (`--config` /
        # `$HERMES_H3_CONFIG`), not a hardcoded default.
        custom = tmp_path / "scratch" / "h3" / "config.yaml"
        result = runner.invoke(hermes_h3, ["route", "--config", str(custom)])
        assert result.exit_code == 0
        assert str(custom) in result.output
        assert "sessions:" in result.output

    def test_route_listing_output_unchanged(self, cfg_path, runner):
        # Byte-for-byte contract for a NON-empty table (header + fixed-width
        # session column): only the empty state changed in this task.
        cfg_path.write_text(
            yaml.safe_dump(
                {
                    "default_harness": "native",
                    "harnesses": {},
                    "sessions": {"telegram:1": {"harness": "alpha"}},
                }
            )
        )
        result = runner.invoke(hermes_h3, ["route"])
        assert result.exit_code == 0
        assert result.output == (
            f"{'SESSION':40s} HARNESS\n"
            + "-" * 60
            + "\n"
            + f"{'telegram:1':40s} alpha\n"
        )

    def test_route_empty_example_is_valid_and_reusable(self, cfg_path, runner):
        # The documented example is the deliverable: it must be valid YAML
        # and, dropped into a config verbatim, must produce the route it
        # promises (both binding forms).
        out = runner.invoke(hermes_h3, ["route"]).output
        lines = out.splitlines()
        start = next(i for i, ln in enumerate(lines) if ln.startswith("  harnesses:"))
        block: list[str] = []
        for ln in lines[start:]:
            if not ln.strip() or not ln.startswith("  "):
                break
            block.append(ln[2:])
        example = yaml.safe_load("\n".join(block))
        assert example["harnesses"]["my-harness"]["endpoint"] == (
            "http://localhost:9191"
        )
        assert example["sessions"]["telegram:-1001234567890"] == "my-harness"
        assert example["sessions"]["telegram:-1001234567890:42"] == {
            "harness": "my-harness"
        }

        # Round-trip: write the pasted example as the config and confirm the
        # table and the single-session lookup both resolve it.
        cfg_path.write_text("\n".join(block))
        result = runner.invoke(hermes_h3, ["route"])
        assert result.exit_code == 0
        assert "SESSION" in result.output
        assert "telegram:-1001234567890" in result.output
        assert "my-harness" in result.output
        one = runner.invoke(
            hermes_h3, ["route", "--session", "telegram:-1001234567890:42"]
        )
        assert one.exit_code == 0
        assert one.output.strip() == "telegram:-1001234567890:42 -> my-harness"

    def test_route_lists_sessions(self, cfg_path, runner):
        cfg_path.write_text(
            yaml.safe_dump(
                {
                    "default_harness": "native",
                    "harnesses": {},
                    "sessions": {
                        "telegram:-100:42": {"harness": "alpha"},
                        "discord:1": {"harness": "beta"},
                    },
                }
            )
        )
        result = runner.invoke(hermes_h3, ["route"])
        assert result.exit_code == 0
        assert "telegram:-100:42" in result.output
        assert "alpha" in result.output
        assert "beta" in result.output

    def test_route_string_form_session_entry(self, cfg_path, runner):
        # Sessions can be either dicts or bare strings (older config style).
        cfg_path.write_text(
            yaml.safe_dump(
                {
                    "default_harness": "native",
                    "harnesses": {},
                    "sessions": {"telegram:-100": "alpha"},
                }
            )
        )
        result = runner.invoke(hermes_h3, ["route"])
        assert result.exit_code == 0
        assert "alpha" in result.output

    # ── DF-H3-11: single-session lookup ──────────────────────────────────

    def test_route_session_dict_binding(self, cfg_path, runner):
        # ``route --session <id>`` answers the operator's actual question:
        # which harness does THIS session use?
        cfg_path.write_text(
            yaml.safe_dump(
                {
                    "default_harness": "native",
                    "harnesses": {},
                    "sessions": {
                        "telegram:1": {"harness": "alpha"},
                        "discord:2": {"harness": "beta"},
                    },
                }
            )
        )
        result = runner.invoke(hermes_h3, ["route", "--session", "telegram:1"])
        assert result.exit_code == 0
        assert result.output.strip() == "telegram:1 -> alpha"

    def test_route_session_string_binding(self, cfg_path, runner):
        # Bare-string bindings (older config style) must resolve too.
        cfg_path.write_text(
            yaml.safe_dump(
                {
                    "default_harness": "native",
                    "harnesses": {},
                    "sessions": {"discord:2": "beta"},
                }
            )
        )
        result = runner.invoke(hermes_h3, ["route", "--session", "discord:2"])
        assert result.exit_code == 0
        assert result.output.strip() == "discord:2 -> beta"

    def test_route_session_unknown_fails_loudly(self, cfg_path, runner):
        # Fail-closed: an unknown id must never look like a clean answer.
        cfg_path.write_text(
            yaml.safe_dump(
                {
                    "default_harness": "native",
                    "harnesses": {},
                    "sessions": {"telegram:1": {"harness": "alpha"}},
                }
            )
        )
        result = runner.invoke(hermes_h3, ["route", "--session", "nope"])
        assert result.exit_code != 0
        assert "nope" in result.output

    def test_route_session_unknown_on_empty_config_fails_loudly(self, cfg_path, runner):
        # The pathological case this finding exists for: empty routing
        # table must not answer an unknown id with a zero exit.
        result = runner.invoke(hermes_h3, ["route", "--session", "nope"])
        assert result.exit_code != 0
        assert "nope" in result.output

    # ── DF-H3-SHIM-FOREMAN-4: route can WRITE a binding ──────────────────

    @staticmethod
    def _config_with_harnesses(sessions: dict | None = None) -> dict:
        """A config with two registered harnesses and (usually) no routes."""
        return {
            "default_harness": "native",
            "harnesses": {
                "native": {"transport": "native"},
                "alpha": {"endpoint": "http://localhost:9191"},
            },
            "sessions": sessions or {},
        }

    def _write_cfg(self, cfg_path: Path, sessions: dict | None = None) -> bytes:
        """Write a routable config; return its bytes for unchanged-checks."""
        cfg_path.write_text(yaml.safe_dump(self._config_with_harnesses(sessions)))
        return cfg_path.read_bytes()

    def test_route_set_harness_writes_binding(self, cfg_path, runner):
        # The finding itself: a binding must be writable from the CLI instead
        # of only by hand-editing `sessions:`.
        self._write_cfg(cfg_path)
        result = runner.invoke(
            hermes_h3, ["route", "--session", "demo", "--set-harness", "alpha"]
        )
        assert result.exit_code == 0, result.output
        # The confirmation names the session, the harness AND the file written.
        assert "demo" in result.output
        assert "alpha" in result.output
        assert str(cfg_path) in result.output
        assert yaml.safe_load(cfg_path.read_text())["sessions"] == {
            "demo": {"harness": "alpha"}
        }
        # The read path sees it immediately, both modes.
        listed = runner.invoke(hermes_h3, ["route"])
        assert listed.exit_code == 0
        assert "demo" in listed.output and "alpha" in listed.output
        one = runner.invoke(hermes_h3, ["route", "--session", "demo"])
        assert one.exit_code == 0
        assert one.output.strip() == "demo -> alpha"

    def test_route_set_harness_creates_missing_sessions_map(self, cfg_path, runner):
        # The empty state the finding is about: no `sessions:` key at all.
        # The write must create the map, not silently no-op.
        cfg_path.write_text(
            "default_harness: native\nharnesses:\n  native:\n    transport: native\n"
        )
        assert "sessions" not in (yaml.safe_load(cfg_path.read_text()) or {})
        result = runner.invoke(
            hermes_h3, ["route", "--session", "demo", "--set-harness", "native"]
        )
        assert result.exit_code == 0, result.output
        written = yaml.safe_load(cfg_path.read_text())
        assert written["sessions"] == {"demo": {"harness": "native"}}
        # Unrelated keys survive the rewrite.
        assert written["harnesses"]["native"] == {"transport": "native"}
        assert written["default_harness"] == "native"

    def test_route_set_harness_absent_config_fails_closed(self, cfg_path, runner):
        # Nothing is registered yet, so no harness name can be valid: the
        # command must fail closed and must NOT create the config file.
        assert not cfg_path.exists()
        result = runner.invoke(
            hermes_h3, ["route", "--session", "demo", "--set-harness", "native"]
        )
        assert result.exit_code != 0
        assert "not found in config" in result.output
        assert not cfg_path.exists()

    def test_route_set_harness_unknown_harness_leaves_file_unchanged(
        self, cfg_path, runner
    ):
        before = self._write_cfg(cfg_path, {"telegram:1": {"harness": "alpha"}})
        result = runner.invoke(
            hermes_h3, ["route", "--session", "demo", "--set-harness", "ghost"]
        )
        assert result.exit_code != 0
        # Wording mirrors resolve_harness(): the name plus the known set.
        assert "harness 'ghost' not found in config" in result.output
        assert "known:" in result.output
        assert "alpha" in result.output and "native" in result.output
        assert cfg_path.read_bytes() == before

    def test_route_set_harness_without_session_errors(self, cfg_path, runner):
        before = self._write_cfg(cfg_path)
        result = runner.invoke(hermes_h3, ["route", "--set-harness", "alpha"])
        assert result.exit_code != 0
        assert "--session" in result.output
        assert cfg_path.read_bytes() == before

    def test_route_set_harness_and_remove_together_error(self, cfg_path, runner):
        before = self._write_cfg(cfg_path, {"telegram:1": {"harness": "alpha"}})
        result = runner.invoke(
            hermes_h3,
            [
                "route",
                "--session",
                "telegram:1",
                "--set-harness",
                "alpha",
                "--remove",
            ],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output
        assert cfg_path.read_bytes() == before

    def test_route_remove_deletes_binding(self, cfg_path, runner):
        self._write_cfg(
            cfg_path,
            {"telegram:1": {"harness": "alpha"}, "discord:2": {"harness": "native"}},
        )
        result = runner.invoke(
            hermes_h3, ["route", "--session", "telegram:1", "--remove"]
        )
        assert result.exit_code == 0, result.output
        assert "telegram:1" in result.output
        assert str(cfg_path) in result.output
        written = yaml.safe_load(cfg_path.read_text())
        # Only the named binding goes; the sibling route survives.
        assert written["sessions"] == {"discord:2": {"harness": "native"}}
        gone = runner.invoke(hermes_h3, ["route", "--session", "telegram:1"])
        assert gone.exit_code != 0
        assert "no session 'telegram:1' in the routing table" in gone.output

    def test_route_remove_unknown_session_fails_closed(self, cfg_path, runner):
        before = self._write_cfg(cfg_path, {"telegram:1": {"harness": "alpha"}})
        result = runner.invoke(hermes_h3, ["route", "--session", "nope", "--remove"])
        assert result.exit_code != 0
        assert "nope" in result.output
        assert cfg_path.read_bytes() == before

    def test_route_remove_without_session_errors(self, cfg_path, runner):
        before = self._write_cfg(cfg_path)
        result = runner.invoke(hermes_h3, ["route", "--remove"])
        assert result.exit_code != 0
        assert "--session" in result.output
        assert cfg_path.read_bytes() == before

    def test_route_set_harness_is_idempotent(self, cfg_path, runner):
        self._write_cfg(cfg_path)
        first = runner.invoke(
            hermes_h3, ["route", "--session", "demo", "--set-harness", "alpha"]
        )
        assert first.exit_code == 0, first.output
        once = cfg_path.read_bytes()
        second = runner.invoke(
            hermes_h3, ["route", "--session", "demo", "--set-harness", "alpha"]
        )
        assert second.exit_code == 0, second.output
        # Same state, byte for byte, and still exit 0.
        assert cfg_path.read_bytes() == once

    def test_route_read_paths_unchanged_by_write_flags(self, cfg_path, runner):
        # The read contracts this task must not move: empty state, listing
        # bytes, fail-closed lookup, and no read-mode write.
        empty = runner.invoke(hermes_h3, ["route"])
        assert empty.exit_code == 0
        assert empty.output.startswith("no sessions configured")
        # The empty state now points at the CLI write path (one line).
        assert "--set-harness" in empty.output
        assert "SESSION" not in empty.output

        before = self._write_cfg(cfg_path, {"telegram:1": {"harness": "alpha"}})
        listing = runner.invoke(hermes_h3, ["route"])
        assert listing.exit_code == 0
        assert listing.output == (
            f"{'SESSION':40s} HARNESS\n"
            + "-" * 60
            + "\n"
            + f"{'telegram:1':40s} alpha\n"
        )
        lookup = runner.invoke(hermes_h3, ["route", "--session", "nope"])
        assert lookup.exit_code != 0
        assert "no session 'nope' in the routing table" in lookup.output
        assert cfg_path.read_bytes() == before

    def test_route_help_documents_write_flags(self, runner):
        result = runner.invoke(hermes_h3, ["route", "--help"])
        assert result.exit_code == 0
        assert "--set-harness" in result.output
        assert "--remove" in result.output


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
        assert "[NAME]" in result.output
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


# ── HERMES_H3_CONFIG override (DF-H3-10) ────────────────────────────────────


class TestConfigPathEnvOverride:
    """``$HERMES_H3_CONFIG`` overrides the default config path (DF-H3-10).

    Precedence, highest first: subcommand ``--config`` > group
    ``--config`` > ``$HERMES_H3_CONFIG`` > ``CONFIG_PATH``.
    """

    def _seed(self, path: Path, harness: str) -> Path:
        """Write a one-harness config at *path*; return the path."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(
                {
                    "default_harness": harness,
                    "harnesses": {
                        harness: {
                            "endpoint": "http://e:1",
                            "transport": "rest",
                            "timeout_ms": 5000,
                        },
                    },
                    "sessions": {},
                }
            )
        )
        return path

    # ── the resolver itself ────────────────────────────────────────────

    def test_default_config_path_honors_env(self, tmp_path, monkeypatch):
        env_cfg = tmp_path / "env-config.yaml"
        monkeypatch.setenv(CONFIG_PATH_ENV, str(env_cfg))
        assert default_config_path() == env_cfg

    def test_default_config_path_expands_user(self, monkeypatch):
        monkeypatch.setenv(CONFIG_PATH_ENV, "~/h3-scratch/config.yaml")
        assert default_config_path() == Path.home() / "h3-scratch" / "config.yaml"

    def test_default_config_path_strips_whitespace(self, tmp_path, monkeypatch):
        env_cfg = tmp_path / "env-config.yaml"
        monkeypatch.setenv(CONFIG_PATH_ENV, f"  {env_cfg}  ")
        assert default_config_path() == env_cfg

    @pytest.mark.parametrize("value", ["", "   "])
    def test_blank_env_falls_back_to_default(self, value, monkeypatch):
        monkeypatch.setenv(CONFIG_PATH_ENV, value)
        assert default_config_path() == CONFIG_PATH

    # ── read path ──────────────────────────────────────────────────────

    def test_env_var_alone_drives_list(self, tmp_path, runner, monkeypatch):
        """A read command uses the env-var config, not the home default."""
        home_default = self._seed(tmp_path / "home-default.yaml", "home-harness")
        monkeypatch.setattr("h3_shim.cli.CONFIG_PATH", home_default)
        env_cfg = self._seed(tmp_path / "env-config.yaml", "env-harness")
        monkeypatch.setenv(CONFIG_PATH_ENV, str(env_cfg))

        result = runner.invoke(hermes_h3, ["list"])

        assert result.exit_code == 0
        assert "env-harness" in result.output
        assert "home-harness" not in result.output

    def test_empty_env_var_falls_back_to_home_default(
        self, tmp_path, runner, monkeypatch
    ):
        home_default = self._seed(tmp_path / "home-default.yaml", "home-harness")
        monkeypatch.setattr("h3_shim.cli.CONFIG_PATH", home_default)
        monkeypatch.setenv(CONFIG_PATH_ENV, "")

        result = runner.invoke(hermes_h3, ["list"])

        assert result.exit_code == 0
        assert "home-harness" in result.output

    def test_load_config_uses_env_path(self, tmp_path, monkeypatch):
        env_cfg = self._seed(tmp_path / "env-config.yaml", "env-harness")
        monkeypatch.setenv(CONFIG_PATH_ENV, str(env_cfg))
        assert load_config()["default_harness"] == "env-harness"

    # ── explicit --config still wins ───────────────────────────────────

    def test_group_config_beats_env(self, tmp_path, runner, monkeypatch):
        env_cfg = self._seed(tmp_path / "env-config.yaml", "env-harness")
        monkeypatch.setenv(CONFIG_PATH_ENV, str(env_cfg))
        explicit = self._seed(tmp_path / "explicit.yaml", "explicit-harness")

        result = runner.invoke(hermes_h3, ["--config", str(explicit), "list"])

        assert result.exit_code == 0
        assert "explicit-harness" in result.output
        assert "env-harness" not in result.output

    def test_subcommand_config_beats_env(self, tmp_path, runner, monkeypatch):
        env_cfg = self._seed(tmp_path / "env-config.yaml", "env-harness")
        monkeypatch.setenv(CONFIG_PATH_ENV, str(env_cfg))
        explicit = self._seed(tmp_path / "explicit.yaml", "explicit-harness")

        result = runner.invoke(hermes_h3, ["list", "--config", str(explicit)])

        assert result.exit_code == 0
        assert "explicit-harness" in result.output
        assert "env-harness" not in result.output

    def test_subcommand_config_beats_group_and_env(self, tmp_path, runner, monkeypatch):
        env_cfg = self._seed(tmp_path / "env-config.yaml", "env-harness")
        monkeypatch.setenv(CONFIG_PATH_ENV, str(env_cfg))
        group_cfg = self._seed(tmp_path / "group.yaml", "group-harness")
        sub_cfg = self._seed(tmp_path / "sub.yaml", "sub-harness")

        result = runner.invoke(
            hermes_h3,
            ["--config", str(group_cfg), "list", "--config", str(sub_cfg)],
        )

        assert result.exit_code == 0
        assert "sub-harness" in result.output
        assert "group-harness" not in result.output
        assert "env-harness" not in result.output

    # ── write paths ────────────────────────────────────────────────────

    def test_save_config_uses_env_path(self, tmp_path, monkeypatch):
        env_cfg = tmp_path / "nested" / "env-config.yaml"
        monkeypatch.setenv(CONFIG_PATH_ENV, str(env_cfg))
        assert save_config(_empty_config()) == env_cfg
        assert env_cfg.exists()

    def test_scaffold_writes_to_env_path(self, tmp_path, runner, monkeypatch):
        home_default = tmp_path / "home-default.yaml"
        monkeypatch.setattr("h3_shim.cli.CONFIG_PATH", home_default)
        env_cfg = tmp_path / "env-config.yaml"
        monkeypatch.setenv(CONFIG_PATH_ENV, str(env_cfg))

        result = runner.invoke(hermes_h3, ["scaffold"])

        assert result.exit_code == 0
        assert env_cfg.exists()
        assert str(env_cfg) in result.output
        assert not home_default.exists()

    def test_install_writes_to_env_path(self, tmp_path, runner, monkeypatch):
        home_default = tmp_path / "home-default.yaml"
        monkeypatch.setattr("h3_shim.cli.CONFIG_PATH", home_default)
        env_cfg = tmp_path / "env-config.yaml"
        monkeypatch.setenv(CONFIG_PATH_ENV, str(env_cfg))
        # install health-checks the endpoint first (DF-H3-SHIM-FOREMAN-3).
        _stub_health_client(monkeypatch)

        result = runner.invoke(
            hermes_h3,
            ["install", "probe", "--endpoint", "http://127.0.0.1:9191"],
        )

        assert result.exit_code == 0
        assert not home_default.exists()
        data = yaml.safe_load(env_cfg.read_text())
        assert data["harnesses"]["probe"]["endpoint"] == "http://127.0.0.1:9191"
        assert data["default_harness"] == "probe"


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

    def test_format_includes_latency_p50_p95_line(self):
        text = _format_human(_passing_report(), "http://x:1")
        assert "Latency p50/p95" in text


# ── latency stats ───────────────────────────────────────────────────────────


class TestLatencyStats:
    def test_known_duration_list(self):
        results = [
            FakeTestResult(
                name=f"t{i}",
                passed=True,
                detail="ok",
                duration_ms=float(d),
                category="Health & Protocol",
            )
            for i, d in enumerate([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], start=1)
        ]
        stats = _latency_stats(results)
        assert stats == {
            "min_ms": 1.0,
            "p50_ms": 5.0,
            "p90_ms": 9.0,
            "p95_ms": 10.0,
            "p99_ms": 10.0,
            "max_ms": 10.0,
            "mean_ms": 5.5,
        }

    def test_empty_results_are_zeros(self):
        assert _latency_stats([]) == {
            "min_ms": 0.0,
            "p50_ms": 0.0,
            "p90_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "max_ms": 0.0,
            "mean_ms": 0.0,
        }

    def test_single_result_all_equal(self):
        results = [
            FakeTestResult(
                name="only",
                passed=True,
                detail="ok",
                duration_ms=7.5,
                category="Health & Protocol",
            )
        ]
        stats = _latency_stats(results)
        assert all(v == 7.5 for v in stats.values())

    def test_mean_rounded_to_two_decimals(self):
        results = [
            FakeTestResult(
                name="a",
                passed=True,
                detail="ok",
                duration_ms=1.234,
                category="Health & Protocol",
            ),
            FakeTestResult(
                name="b",
                passed=True,
                detail="ok",
                duration_ms=2.345,
                category="Health & Protocol",
            ),
        ]
        stats = _latency_stats(results)
        assert stats["mean_ms"] == round((1.234 + 2.345) / 2, 2)


# ── _run_battery JSON output ────────────────────────────────────────────────


class TestRunBatteryJSON:
    @staticmethod
    def _stub_battery(monkeypatch, report):
        fake = MagicMock()
        fake.run_all = AsyncMock(return_value=report)
        fake.close = AsyncMock()
        monkeypatch.setattr("h3_shim.cli.H3TestBattery", lambda *a, **kw: fake)

    @pytest.mark.asyncio
    async def test_json_payload_includes_latency(self, monkeypatch, capsys):
        self._stub_battery(monkeypatch, _passing_report())
        code = await _run_battery("http://x:1", None, True)
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["latency"] == {
            "min_ms": 12.0,
            "p50_ms": 12.0,
            "p90_ms": 20.0,
            "p95_ms": 20.0,
            "p99_ms": 20.0,
            "max_ms": 20.0,
            "mean_ms": 16.0,
        }

    @pytest.mark.asyncio
    async def test_json_payload_preserves_schema_keys(self, monkeypatch, capsys):
        self._stub_battery(monkeypatch, _passing_report())
        await _run_battery("http://x:1", None, True)
        payload = json.loads(capsys.readouterr().out)
        for key in (
            "total",
            "passed",
            "failed",
            "duration_ms",
            "timestamp",
            "results",
            "all_passing",
            "latency",
        ):
            assert key in payload

    @pytest.mark.asyncio
    async def test_json_empty_results_yield_zero_latency(self, monkeypatch, capsys):
        report = FakeTestReport(
            results=[],
            total=0,
            passed=0,
            failed=0,
            duration_ms=0.0,
            timestamp="2026-01-01T00:00:00Z",
        )
        self._stub_battery(monkeypatch, report)
        await _run_battery("http://x:1", None, True)
        payload = json.loads(capsys.readouterr().out)
        assert payload["latency"] == {
            "min_ms": 0.0,
            "p50_ms": 0.0,
            "p90_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "max_ms": 0.0,
            "mean_ms": 0.0,
        }


# ── _run_battery category filtering (GAP-006) ──────────────────────────────


def _full_category_report() -> FakeTestReport:
    """Return a report with all 46 tests across all 6 categories."""
    cat_map: dict[str, int] = {
        "Health & Protocol": 7,
        "Process Basic Flows": 8,
        "Decision Types": 6,
        "Result Handling": 7,
        "Error & Edge Cases": 13,
        "Stress & Performance": 5,
    }
    results: list[FakeTestResult] = []
    seq = 1
    for cat, count in cat_map.items():
        for i in range(count):
            results.append(
                FakeTestResult(
                    name=f"test_{seq}",
                    passed=True,
                    detail="ok",
                    duration_ms=1.0,
                    category=cat,
                )
            )
            seq += 1
    return FakeTestReport(
        results=results,
        total=46,
        passed=46,
        failed=0,
        duration_ms=44.0,
        timestamp="2026-01-01T00:00:00Z",
    )


class TestRunBatteryCategories:
    @staticmethod
    def _stub_battery(monkeypatch, report):
        fake = MagicMock()
        fake.run_all = AsyncMock(return_value=report)
        fake.close = AsyncMock()
        monkeypatch.setattr("h3_shim.cli.H3TestBattery", lambda *a, **kw: fake)

    @pytest.mark.asyncio
    async def test_health_token_runs_seven_tests(self, monkeypatch, capsys):
        """--categories health runs exactly the 7 Health & Protocol tests."""
        self._stub_battery(monkeypatch, _full_category_report())
        code = await _run_battery("http://x:1", "health", False)
        assert code == 0
        out = capsys.readouterr().out
        # 7/7 for Health & Protocol
        assert "Health & Protocol" in out
        assert "7/7" in out
        # None of the other categories should appear
        assert "Process Basic Flows" not in out
        assert "Decision Types" not in out
        assert "Result Handling" not in out
        assert "Error & Edge Cases" not in out
        assert "Stress & Performance" not in out
        # TOTAL line shows 7 passed
        assert "TOTAL" in out

    @pytest.mark.asyncio
    async def test_multiple_tokens_runs_both_subsets(self, monkeypatch, capsys):
        """--categories health,errors runs Health (7) + Errors (13) = 20 tests."""
        self._stub_battery(monkeypatch, _full_category_report())
        code = await _run_battery("http://x:1", "health,errors", False)
        assert code == 0
        out = capsys.readouterr().out
        assert "Health & Protocol" in out
        assert "7/7" in out
        assert "Error & Edge Cases" in out
        assert "13/13" in out
        # None of the other categories
        assert "Process Basic Flows" not in out
        assert "Decision Types" not in out
        assert "Result Handling" not in out
        assert "Stress & Performance" not in out

    @pytest.mark.asyncio
    async def test_unknown_category_exits_nonzero(self, monkeypatch, capsys):
        """An unknown category token exits non-zero with a clear error."""
        self._stub_battery(monkeypatch, _full_category_report())
        code = await _run_battery("http://x:1", "bogus", False)
        assert code != 0
        err = capsys.readouterr().err
        assert "unknown" in err.lower()
        assert "bogus" in err
        # It should list valid categories
        for token in ("health", "process", "decisions", "results", "errors", "stress"):
            assert token in err

    @pytest.mark.asyncio
    async def test_unknown_among_valid_tokens_exits_nonzero(self, monkeypatch, capsys):
        """--categories health,bogus exits non-zero and reports bogus."""
        self._stub_battery(monkeypatch, _full_category_report())
        code = await _run_battery("http://x:1", "health,bogus", False)
        assert code != 0
        err = capsys.readouterr().err
        assert "unknown" in err.lower()
        assert "bogus" in err

    @pytest.mark.asyncio
    async def test_all_tokens_runs_all_forty_six(self, monkeypatch, capsys):
        """--categories health,process,decisions,results,errors,stress runs all 46."""
        self._stub_battery(monkeypatch, _full_category_report())
        code = await _run_battery(
            "http://x:1",
            "health,process,decisions,results,errors,stress",
            False,
        )
        assert code == 0
        out = capsys.readouterr().out
        # All six category labels appear
        for label in (
            "Health & Protocol",
            "Process Basic Flows",
            "Decision Types",
            "Result Handling",
            "Error & Edge Cases",
            "Stress & Performance",
        ):
            assert label in out

    @pytest.mark.asyncio
    async def test_category_filter_preserves_json_output(self, monkeypatch, capsys):
        """--categories health --json produces correct filtered JSON."""
        self._stub_battery(monkeypatch, _full_category_report())
        code = await _run_battery("http://x:1", "health", True)
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["total"] == 7
        assert payload["passed"] == 7
        assert payload["failed"] == 0
        categories = {r["category"] for r in payload["results"]}
        assert categories == {"Health & Protocol"}

    @pytest.mark.asyncio
    async def test_display_label_runs_only_that_category(self, monkeypatch, capsys):
        """--categories "Stress & Performance" (the banner label) runs stress."""
        self._stub_battery(monkeypatch, _full_category_report())
        code = await _run_battery("http://x:1", "Stress & Performance", False)
        assert code == 0
        out = capsys.readouterr().out
        assert "Stress & Performance" in out
        assert "5/5" in out
        # None of the other categories should appear
        assert "Health & Protocol" not in out
        assert "Process Basic Flows" not in out
        assert "Decision Types" not in out
        assert "Result Handling" not in out
        assert "Error & Edge Cases" not in out

    @pytest.mark.asyncio
    async def test_labels_for_every_category_are_accepted(self, monkeypatch, capsys):
        """Every display label resolves to its category token."""
        for token, label in CATEGORIES.items():
            self._stub_battery(monkeypatch, _full_category_report())
            code = await _run_battery("http://x:1", label, True)
            assert code == 0, label
            payload = json.loads(capsys.readouterr().out)
            assert {r["category"] for r in payload["results"]} == {label}
            assert payload["total"] == _LABEL_COUNTS[label], token

    @pytest.mark.asyncio
    async def test_label_matching_ignores_case_and_whitespace(
        self, monkeypatch, capsys
    ):
        """Shell-typed spacing/casing still resolves to the same category."""
        self._stub_battery(monkeypatch, _full_category_report())
        code = await _run_battery("http://x:1", "  stress  &   PERFORMANCE ", False)
        assert code == 0
        out = capsys.readouterr().out
        assert "Stress & Performance" in out
        assert "5/5" in out
        assert "Health & Protocol" not in out

    @pytest.mark.asyncio
    async def test_tokens_and_labels_can_be_mixed(self, monkeypatch, capsys):
        """A mixed token/label list runs the union of both categories."""
        self._stub_battery(monkeypatch, _full_category_report())
        code = await _run_battery("http://x:1", "errors, Stress & Performance", False)
        assert code == 0
        out = capsys.readouterr().out
        assert "Error & Edge Cases" in out
        assert "13/13" in out
        assert "Stress & Performance" in out
        assert "5/5" in out
        # 13 + 5 = 18 tests total
        assert "18/18" in out
        assert "Health & Protocol" not in out

    @pytest.mark.asyncio
    async def test_unknown_category_error_lists_tokens_and_labels(
        self, monkeypatch, capsys
    ):
        """The exit-2 message names both accepted forms."""
        self._stub_battery(monkeypatch, _full_category_report())
        code = await _run_battery("http://x:1", "Stress & Reliability", False)
        assert code == 2
        err = capsys.readouterr().err
        assert "unknown" in err.lower()
        assert "Stress & Reliability" in err
        assert "Valid categories:" in err
        assert "Valid labels:" in err
        assert '"Stress & Performance"' in err


#: Test counts per category label — mirrors the real battery's 46-test split.
_LABEL_COUNTS: dict[str, int] = {
    "Health & Protocol": 7,
    "Process Basic Flows": 8,
    "Decision Types": 6,
    "Result Handling": 7,
    "Error & Edge Cases": 13,
    "Stress & Performance": 5,
}


class TestCategoryTokenResolution:
    """Unit tests for the token/label alias map in h3_shim.test_battery."""

    def test_protocol_tokens_resolve(self):
        for token in CATEGORIES:
            assert category_token(token) == token

    def test_display_labels_resolve(self):
        for token, label in CATEGORIES.items():
            assert category_token(label) == token

    def test_case_and_whitespace_insensitive(self):
        assert category_token("STRESS") == "stress"
        assert category_token(" Health   &  Protocol ") == "health"
        assert category_token("stress   &   performance") == "stress"

    def test_unknown_values_return_none(self):
        assert category_token("bogus") is None
        assert category_token("Stress & Reliability") is None
        assert category_token("") is None


# ── test command (asyncio + battery stubbed) ───────────────────────────────


class TestTestCommand:
    def test_test_with_endpoint_runs_battery(self, runner, monkeypatch):
        # Stub asyncio.run so we never hit the network.

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

        fake_client = MagicMock()
        instance = MagicMock()
        instance.health = AsyncMock(
            return_value=HealthResponse(
                status=HealthStatus.OK,
                version="1.2.3",
                capabilities=["foo", "bar"],
            ),
        )
        instance.close = AsyncMock()
        fake_client.return_value = instance
        monkeypatch.setattr("h3_shim.client.H3Client", fake_client)

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

    def test_verify_with_endpoint_and_fallback_healthy(self, runner, monkeypatch):
        """Healthy harness + --fallback flag → shows STANDBY fallback info."""
        from h3_shim.protocol import HealthResponse, HealthStatus

        fake_client = MagicMock()
        instance = MagicMock()
        instance.health = AsyncMock(
            return_value=HealthResponse(
                status=HealthStatus.OK,
                version="1.2.3",
                capabilities=["foo"],
            ),
        )
        instance.close = AsyncMock()
        fake_client.return_value = instance
        monkeypatch.setattr("h3_shim.client.H3Client", fake_client)

        result = runner.invoke(
            hermes_h3,
            ["verify", "--endpoint", "http://x:1", "--fallback"],
        )
        assert result.exit_code == 0
        assert "status:   HealthStatus.OK" in result.output
        assert "Fallback path" in result.output
        assert "STANDBY" in result.output
        assert "harness: <override>" in result.output

    def test_verify_with_endpoint_and_fallback_unreachable(self, runner, monkeypatch):
        """Unreachable harness + --fallback flag → shows ENGAGED fallback."""
        fake_client = MagicMock(side_effect=ConnectionError("connection refused"))
        monkeypatch.setattr("h3_shim.client.H3Client", fake_client)

        result = runner.invoke(
            hermes_h3,
            ["verify", "--endpoint", "http://x:1", "--fallback"],
        )
        assert result.exit_code == 0
        assert "UNREACHABLE" in result.output
        assert "Fallback path" in result.output
        assert "ENGAGED" in result.output
        assert "connection refused" in result.output

    # ── positional NAME (DOGFOOD-11) ────────────────────────────────────────

    @staticmethod
    def _write_two_harness_config(cfg_path: Path) -> None:
        """Config with default alpha + second harness beta."""
        cfg_path.write_text(
            yaml.safe_dump(
                {
                    "default_harness": "alpha",
                    "harnesses": {
                        "alpha": {
                            "endpoint": "http://a:1",
                            "transport": "rest",
                            "timeout_ms": 5000,
                        },
                        "beta": {
                            "endpoint": "http://b:2",
                            "transport": "rest",
                            "timeout_ms": 5000,
                        },
                    },
                    "sessions": {},
                }
            )
        )

    @staticmethod
    def _stub_healthy_client(monkeypatch) -> None:
        """Patch H3Client (lazy-imported inside verify) with a healthy stub."""
        from h3_shim.protocol import HealthResponse, HealthStatus

        fake_client = MagicMock()
        instance = MagicMock()
        instance.health = AsyncMock(
            return_value=HealthResponse(
                status=HealthStatus.OK,
                version="1.2.3",
                capabilities=["foo", "bar"],
            ),
        )
        instance.close = AsyncMock()
        fake_client.return_value = instance
        monkeypatch.setattr("h3_shim.client.H3Client", fake_client)

    def test_verify_positional_name(self, runner, monkeypatch, cfg_path):
        """`verify NAME` resolves the positional like --harness (DOGFOOD-11)."""
        self._write_two_harness_config(cfg_path)
        self._stub_healthy_client(monkeypatch)

        result = runner.invoke(hermes_h3, ["verify", "beta"])
        assert result.exit_code == 0
        assert "harness: beta" in result.output
        assert "endpoint: http://b:2" in result.output
        assert "status:   HealthStatus.OK" in result.output

    def test_verify_no_args_falls_back_to_default(self, runner, monkeypatch, cfg_path):
        """`verify` with no NAME/--harness still uses default_harness."""
        self._write_two_harness_config(cfg_path)
        self._stub_healthy_client(monkeypatch)

        result = runner.invoke(hermes_h3, ["verify"])
        assert result.exit_code == 0
        assert "harness: alpha" in result.output
        assert "endpoint: http://a:1" in result.output

    def test_verify_harness_flag_still_works(self, runner, monkeypatch, cfg_path):
        """`verify --harness NAME` keeps working (backwards compat)."""
        self._write_two_harness_config(cfg_path)
        self._stub_healthy_client(monkeypatch)

        result = runner.invoke(hermes_h3, ["verify", "--harness", "beta"])
        assert result.exit_code == 0
        assert "harness: beta" in result.output
        assert "endpoint: http://b:2" in result.output

    def test_verify_positional_wins_over_flag(self, runner, monkeypatch, cfg_path):
        """Both NAME and --harness given → positional NAME wins."""
        self._write_two_harness_config(cfg_path)
        self._stub_healthy_client(monkeypatch)

        result = runner.invoke(
            hermes_h3,
            ["verify", "alpha", "--harness", "beta"],
        )
        assert result.exit_code == 0
        assert "harness: alpha" in result.output
        assert "endpoint: http://a:1" in result.output


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


class TestReportSchema:
    """Validate that the test battery JSON output matches the schema."""

    # Minimal inline schema for self-contained tests.
    # protocol/schemas/v1/test-report.json is the canonical full schema
    # with $defs — this mirrors its constraints.
    _REPORT_SCHEMA = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": [
            "results",
            "total",
            "passed",
            "failed",
            "duration_ms",
            "timestamp",
            "all_passing",
        ],
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "name",
                        "passed",
                        "detail",
                        "duration_ms",
                        "category",
                    ],
                    "properties": {
                        "name": {"type": "string"},
                        "passed": {"type": "boolean"},
                        "detail": {"type": "string"},
                        "duration_ms": {"type": "number"},
                        "category": {"type": "string"},
                    },
                },
            },
            "total": {"type": "integer"},
            "passed": {"type": "integer"},
            "failed": {"type": "integer"},
            "duration_ms": {"type": "number"},
            "timestamp": {"type": "string"},
            "all_passing": {"type": "boolean"},
        },
    }

    # Simpler variant for negative tests (loose array item constraints).
    _REPORT_SCHEMA_LOOSE = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": [
            "results",
            "total",
            "passed",
            "failed",
            "duration_ms",
            "timestamp",
            "all_passing",
        ],
        "properties": {
            "results": {"type": "array"},
            "total": {"type": "integer"},
            "passed": {"type": "integer"},
            "failed": {"type": "integer"},
            "duration_ms": {"type": "number"},
            "timestamp": {"type": "string"},
            "all_passing": {"type": "boolean"},
        },
    }

    @staticmethod
    def _write_schema(tmp_path, schema):
        import json

        sp = tmp_path / "test-report.json"
        sp.write_text(json.dumps(schema))
        return str(sp)

    def test_passing_report_validates(self, tmp_path):
        """A passing report serialises to JSON that validates against the schema."""
        from h3_shim.test_battery import validate_test_report

        report = _passing_report()
        data = asdict(report)
        data["all_passing"] = report.all_passing  # type: ignore[union-attr]

        schema_path = self._write_schema(tmp_path, self._REPORT_SCHEMA)
        errors = validate_test_report(data, schema_path=schema_path)
        assert errors == [], f"Schema validation errors: {errors}"

    def test_failing_report_validates(self, tmp_path):
        """A failing report also validates against the schema."""
        from h3_shim.test_battery import validate_test_report

        report = _failing_report()
        data = asdict(report)
        data["all_passing"] = report.all_passing  # type: ignore[union-attr]

        schema_path = self._write_schema(tmp_path, self._REPORT_SCHEMA)
        errors = validate_test_report(data, schema_path=schema_path)
        assert errors == [], f"Schema validation errors: {errors}"

    def test_missing_field_fails(self, tmp_path):
        """Report with missing required field produces validation errors."""
        from h3_shim.test_battery import validate_test_report

        schema_path = self._write_schema(tmp_path, self._REPORT_SCHEMA_LOOSE)
        data = {
            "results": [],
            "passed": 0,
            "failed": 0,
            "duration_ms": 0.0,
            "timestamp": "2024-01-01T00:00:00",
            "all_passing": True,
        }
        errors = validate_test_report(data, schema_path=schema_path)
        assert len(errors) > 0
        assert any("total" in e for e in errors)

    def test_bad_type_fails(self, tmp_path):
        """A field with wrong type produces validation errors."""
        from h3_shim.test_battery import validate_test_report

        schema_path = self._write_schema(tmp_path, self._REPORT_SCHEMA_LOOSE)
        data = {
            "results": [],
            "total": "not-an-integer",
            "passed": 0,
            "failed": 0,
            "duration_ms": 0.0,
            "timestamp": "2024-01-01T00:00:00",
            "all_passing": True,
        }
        errors = validate_test_report(data, schema_path=schema_path)
        assert len(errors) > 0


# ── GAP-005 regression: __init__.py must be importable ──────────────────────


class TestPackageIntegrity:
    """GAP-005 regression — the installed wheel must ship __init__.py."""

    def test_version_accessible(self):
        """``h3_shim.__version__`` returns a non-empty string."""
        import h3_shim

        assert h3_shim.__version__ == "0.1.0"
        assert isinstance(h3_shim.__version__, str)

    def test_import_does_not_raise(self):
        """``import h3_shim`` does not raise ImportError."""
        import h3_shim  # noqa: F811 — re-import is harmless

        assert h3_shim is not None


# ── GAP-008 regression: all subcommand --help must work ─────────────────────


class TestAllSubcommandHelp:
    """GAP-008 regression — every documented subcommand must respond to --help."""

    ALL_SUBCMDS = [
        "install",
        "list",
        "pre-update-check",
        "route",
        "scaffold",
        "test",
        "uninstall",
        "use",
        "verify",
    ]

    def test_hermes_h3_top_level_help(self, runner):
        """``hermes-h3 --help`` exits 0."""
        result = runner.invoke(hermes_h3, ["--help"])
        assert result.exit_code == 0
        assert "Usage:" in result.output

    @pytest.mark.parametrize("subcmd", ALL_SUBCMDS)
    def test_every_subcommand_help(self, runner, subcmd):
        """Each subcommand --help exits 0."""
        result = runner.invoke(hermes_h3, [subcmd, "--help"])
        assert result.exit_code == 0, (
            f"hermes-h3 {subcmd} --help failed: {result.output}"
        )

    def test_h3_test_help_smoke(self, runner):
        """``h3-test --help`` exit code is 0 (via argparse SystemExit)."""
        import sys

        try:
            sys.argv = ["h3-test", "--help"]
            main()
        except SystemExit as e:
            assert e.code == 0


# ── GAP-038 regression: hermes-h3 --version must work ───────────────────────


class TestHermesH3VersionFlag:
    """GAP-038 — ``hermes-h3 --version`` mirrors ``h3-test --version``."""

    def test_version_exits_zero_and_prints_prog_and_path(self, runner):
        result = runner.invoke(hermes_h3, ["--version"])
        assert result.exit_code == 0, result.output
        assert result.output.startswith("hermes-h3 ")
        assert "h3_shim:" in result.output

    def test_version_does_not_require_config(self, runner, tmp_path, monkeypatch):
        """Version is a pure flag — no config file needed."""
        monkeypatch.setattr("h3_shim.cli.CONFIG_PATH", tmp_path / "no-such-config.yaml")
        result = runner.invoke(hermes_h3, ["--version"])
        assert result.exit_code == 0
        assert "h3_shim:" in result.output


# ── GAP-033 regression: pre-update-check must pass for the shipped pairing ──


class TestPreUpdateCheck:
    """GAP-033 — the compat matrix must accept the shipped package version.

    Before GAP-033 the bundled versions.yaml required h3_shim >= 1.0.0 for
    every Hermes version while the package was still 0.1.0, so
    ``hermes-h3 pre-update-check`` could never exit 0. The bundled matrix
    now carries a 0.1.x row (Hermes 0.17.0) that the shipped package
    satisfies — the CLI must exit 0 against it, and still block against
    future Hermes versions that legitimately need a newer shim.
    """

    def _write_config(self, tmp_path: Path) -> Path:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            yaml.safe_dump(
                {
                    "_schema": 1,
                    "default_harness": None,
                    "harnesses": {},
                    "sessions": {},
                }
            )
        )
        return cfg

    def test_passes_for_shipped_pairing(self, runner, tmp_path):
        """Current package (0.1.x) vs its own matrix row → exit 0."""
        cfg = self._write_config(tmp_path)
        result = runner.invoke(
            hermes_h3,
            ["pre-update-check", "0.17.0", "--config", str(cfg)],
        )
        assert result.exit_code == 0, result.output
        assert "All checks passed" in result.output

    def test_still_blocks_for_future_hermes(self, runner, tmp_path):
        """Hermes 0.18.0 needs shim >= 1.0.0 — 0.1.x must still block."""
        cfg = self._write_config(tmp_path)
        result = runner.invoke(
            hermes_h3,
            ["pre-update-check", "0.18.0", "--config", str(cfg)],
        )
        assert result.exit_code == 1
        assert "too old" in result.output

    def test_unknown_version_names_matrix_and_supported(self, runner, tmp_path):
        """DF-H3-SHIM-FOREMAN-4 — an unlisted Hermes version must not dead-end.

        Exit stays 1 (BLOCK), but the message now names the versions.yaml
        that was consulted and lists the Hermes versions it supports.
        """
        from h3_shim.upgrade_check import VERSIONS_YAML_PATH

        cfg = self._write_config(tmp_path)
        result = runner.invoke(
            hermes_h3,
            ["pre-update-check", "0.99.0", "--config", str(cfg)],
        )
        assert result.exit_code == 1, result.output
        assert str(VERSIONS_YAML_PATH) in result.output
        assert "no compatibility data" in result.output.lower()
        assert "Supported Hermes versions:" in result.output
        assert "0.17.0" in result.output
        assert "0.20.0" in result.output
        assert "--versions-yaml" in result.output


# ── GAP-008 regression: template files must exist in source ─────────────────


class TestTemplatesExist:
    """GAP-008 regression — all 8 template files must exist in the source tree."""

    TEMPLATES = [
        "go/main.go",
        "go/go.mod",
        "py/main.py",
        "py/requirements.txt",
        "py/pyproject.toml",
        "ts/index.ts",
        "ts/package.json",
        "ts/tsconfig.json",
    ]

    def test_all_templates_present(self):
        """All 8 template files exist under templates/."""
        from h3_shim.cli import TEMPLATES_DIR

        for rel in self.TEMPLATES:
            full = TEMPLATES_DIR / rel
            assert full.is_file(), f"template missing: {rel}"


class TestPyTemplateWheelConfig:
    """GAP-040 regression — scaffolded py projects must build non-editable wheels.

    The template pyproject.toml previously shipped a hatchling ``include``
    filter (``include = ["main.py"]``) that silently dropped ``__init__.py``
    from non-editable wheels — the same GAP-005-class breakage the shim
    itself had. Editable installs mask it (they use the source tree), so the
    template must carry an ``__init__.py`` and must NOT restrict wheel
    contents via ``include``.
    """

    def test_template_has_init_py(self):
        """templates/py ships an __init__.py so wheels include it."""
        from h3_shim.cli import TEMPLATES_DIR

        init = TEMPLATES_DIR / "py" / "__init__.py"
        assert init.is_file(), "template py/__init__.py missing"

    def test_template_pyproject_has_no_include_filter(self):
        """The template wheel target must not use an ``include`` filter."""
        from h3_shim.cli import TEMPLATES_DIR

        text = (TEMPLATES_DIR / "py" / "pyproject.toml").read_text()
        assert "include" not in text, (
            "template pyproject.toml still restricts wheel contents via "
            "include — GAP-005-class breakage"
        )
        # The positive contract: hatchling with a plain packages selection.
        assert "hatchling" in text
        assert 'packages = ["."]' in text
