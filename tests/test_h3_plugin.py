"""Tests for the ``hermes h3`` Core plugin (``h3/__init__.py``).

The plugin is a thin argparse mirror that delegates to the
``hermes-h3`` click CLI.  These tests load the plugin module directly
from the repo path (it lives outside ``src/`` and may import
hermes-core, which is not available in the test environment) and verify
the ``_setup()`` / ``_argv_from_namespace()`` contract without hitting
any external CLI or network.

Coverage focus (GAP-009): ``--config`` must be accepted *both* before
and after the subcommand, matching the standalone ``hermes-h3`` click
CLI.  Before the fix, ``--config`` was registered only on the parent
parser, so ``hermes h3 list --config X`` failed with
``unrecognized arguments: --config``.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest

# ── module loading ──────────────────────────────────────────────────────────
# The plugin file lives at <repo>/h3/__init__.py, outside the importable
# package tree, and its top-level imports (click, h3_shim.cli) are guarded
# with try/except so they degrade gracefully.  Load it by file path.

_PLUGIN_PATH = Path(__file__).resolve().parent.parent / "h3" / "__init__.py"


@pytest.fixture(scope="module")
def plugin() -> object:
    """Load the ``h3`` plugin module from its repo path."""
    spec = importlib.util.spec_from_file_location("h3_plugin_under_test", _PLUGIN_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {_PLUGIN_PATH}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _new_parser(plugin: object) -> argparse.ArgumentParser:
    """Build a fresh parser wired with the plugin's ``_setup``."""
    parser = argparse.ArgumentParser(prog="hermes h3")
    plugin._setup(parser)  # type: ignore[attr-defined]
    return parser


# ── GAP-009: --config accepted before AND after the subcommand ─────────────

CONFIG_PATH = "/tmp/h3shim_gap009_test.yaml"


@pytest.mark.parametrize(
    "argv",
    [
        # --config AFTER the subcommand (was broken before GAP-009)
        ["list", "--config", CONFIG_PATH],
        # --config BEFORE the subcommand (always worked)
        ["--config", CONFIG_PATH, "list"],
    ],
    ids=["config-after-subcommand", "config-before-subcommand"],
)
def test_config_accepted_both_orders(plugin: object, argv: list[str]) -> None:
    """``--config`` parses in either position for ``hermes h3 list``."""
    parser = _new_parser(plugin)
    ns = parser.parse_args(argv)
    assert ns.h3_command == "list"
    assert getattr(ns, "h3_config", None) == CONFIG_PATH


@pytest.mark.parametrize(
    "argv",
    [
        ["list", "--config", CONFIG_PATH],
        ["--config", CONFIG_PATH, "list"],
    ],
    ids=["config-after", "config-before"],
)
def test_config_argv_rebuild(plugin: object, argv: list[str]) -> None:
    """The rebuilt click argv prepends ``--config <path>`` for delegation."""
    parser = _new_parser(plugin)
    ns = parser.parse_args(argv)
    rebuilt = plugin._argv_from_namespace(ns)  # type: ignore[attr-defined]
    assert "--config" in rebuilt
    assert CONFIG_PATH in rebuilt
    assert "list" in rebuilt


def test_list_without_config_defaults_to_none(plugin: object) -> None:
    """``hermes h3 list`` with no --config leaves h3_config unset/None."""
    parser = _new_parser(plugin)
    ns = parser.parse_args(["list"])
    assert ns.h3_command == "list"
    # Parent default is None; subparser uses SUPPRESS so it never clobbers.
    assert getattr(ns, "h3_config", None) is None


@pytest.mark.parametrize(
    "subcommand",
    [
        "test",
        "list",
        "install",
        "uninstall",
        "verify",
        "scaffold",
        "route",
        "pre-update-check",
        "use",
    ],
)
def test_every_subparser_accepts_config_after(plugin: object, subcommand: str) -> None:
    """Every subcommand accepts ``--config`` after it (GAP-009 requirement).

    For subcommands requiring positional args, we supply minimal stubs so
    argparse does not reject the invocation on missing-positionals grounds.
    """
    parser = _new_parser(plugin)
    positionals: dict[str, list[str]] = {
        "install": ["myharness", "--endpoint", "http://localhost:9191"],
        "uninstall": ["myharness"],
        "use": ["myharness"],
        "pre-update-check": ["0.19.0"],
    }
    argv = [subcommand, *positionals.get(subcommand, []), "--config", CONFIG_PATH]
    ns = parser.parse_args(argv)
    assert ns.h3_command == subcommand
    assert getattr(ns, "h3_config", None) == CONFIG_PATH


def test_config_before_does_not_get_clobbered(plugin: object) -> None:
    """Subparser SUPPRESS default must not overwrite a parent-parsed --config.

    Regression guard: if the subparser ``--config`` used ``default=None``
    instead of ``argparse.SUPPRESS``, parsing ``hermes h3 --config X list``
    would reset ``ns.h3_config`` to ``None``.
    """
    parser = _new_parser(plugin)
    ns = parser.parse_args(["--config", CONFIG_PATH, "list"])
    assert ns.h3_config == CONFIG_PATH
    # And the rebuilt argv still carries it.
    rebuilt = plugin._argv_from_namespace(ns)  # type: ignore[attr-defined]
    assert CONFIG_PATH in rebuilt


def test_no_subcommand_parses_cleanly(plugin: object) -> None:
    """``hermes h3`` alone (help case) does not raise on _setup."""
    parser = _new_parser(plugin)
    ns = parser.parse_args([])
    assert getattr(ns, "h3_command", None) is None
    rebuilt = plugin._argv_from_namespace(ns)  # type: ignore[attr-defined]
    assert rebuilt == []


def test_test_subcommand_with_config_and_flags(plugin: object) -> None:
    """``hermes h3 test --config X --json`` parses end-to-end."""
    parser = _new_parser(plugin)
    ns = parser.parse_args(["test", "--config", CONFIG_PATH, "--json"])
    assert ns.h3_command == "test"
    assert ns.h3_config == CONFIG_PATH
    assert ns.as_json is True


# ── DF-H3-26: --expect-fresh in the plugin mirror ──────────────────────────
# ``h3-test --expect-fresh`` refuses to run against a stale co-tenant server.
# ``hermes h3 test`` is documented as the same battery (README, AGENTS.md), so
# the mirror must forward the flag too — the mirror-drift class of GAP-009 /
# DF-H3-3 / GAP-091: an option the click CLI has and the mirror does not is
# ``unrecognized arguments`` for the ``hermes h3`` user.


def test_expect_fresh_flag_parses(plugin: object) -> None:
    parser = _new_parser(plugin)
    ns = parser.parse_args(["test", "--endpoint", INSTALL_ENDPOINT, "--expect-fresh"])
    assert ns.h3_command == "test"
    assert ns.expect_fresh is True


def test_expect_fresh_rebuilds_the_click_argv(plugin: object) -> None:
    parser = _new_parser(plugin)
    ns = parser.parse_args(["test", "--endpoint", INSTALL_ENDPOINT, "--expect-fresh"])
    assert plugin._argv_from_namespace(ns) == [  # type: ignore[attr-defined]
        "test",
        "--endpoint",
        INSTALL_ENDPOINT,
        "--expect-fresh",
    ]


def test_expect_fresh_default_is_omitted_from_argv(plugin: object) -> None:
    """Unset, the mirror drops the flag: the forwarded argv is unchanged."""
    parser = _new_parser(plugin)
    ns = parser.parse_args(["test", "--endpoint", INSTALL_ENDPOINT])
    assert ns.expect_fresh is False
    assert plugin._argv_from_namespace(ns) == [  # type: ignore[attr-defined]
        "test",
        "--endpoint",
        INSTALL_ENDPOINT,
    ]


# ── DF-H3-3: install --name is an alias for the positional NAME ────────────
# The plugin mirror must expose the same two surfaces as the click CLI.
# Before the fix, ``hermes h3 install --name X --endpoint URL`` died in
# argparse with ``unrecognized arguments: --name`` — the same mirror-drift
# class as H3-GAP-091.

INSTALL_ENDPOINT = "http://localhost:9191"


def test_install_name_flag_parses(plugin: object) -> None:
    """``hermes h3 install --name X`` reaches the namespace (no argparse error)."""
    parser = _new_parser(plugin)
    ns = parser.parse_args(
        ["install", "--name", "scout", "--endpoint", INSTALL_ENDPOINT]
    )
    assert ns.h3_command == "install"
    assert ns.name is None
    assert ns.name_opt == "scout"
    assert ns.endpoint == INSTALL_ENDPOINT


def test_install_name_flag_rebuilds_positional_argv(plugin: object) -> None:
    """``--name X`` rebuilds the byte-identical argv of ``install X``."""
    parser = _new_parser(plugin)
    flag_ns = parser.parse_args(
        ["install", "--name", "scout", "--endpoint", INSTALL_ENDPOINT]
    )
    pos_ns = parser.parse_args(["install", "scout", "--endpoint", INSTALL_ENDPOINT])
    flag_argv = plugin._argv_from_namespace(flag_ns)  # type: ignore[attr-defined]
    pos_argv = plugin._argv_from_namespace(pos_ns)  # type: ignore[attr-defined]
    assert flag_argv == pos_argv == ["install", "scout", "--endpoint", INSTALL_ENDPOINT]
    # An unset --name is omitted entirely (and an unset positional too), so
    # click reports the missing NAME instead of installing an unnamed entry.
    none_ns = parser.parse_args(["install", "--endpoint", INSTALL_ENDPOINT])
    assert plugin._argv_from_namespace(none_ns) == [  # type: ignore[attr-defined]
        "install",
        "--endpoint",
        INSTALL_ENDPOINT,
    ]


def test_install_flag_and_positional_behave_identically(
    plugin: object, tmp_path: Path, monkeypatch
) -> None:
    """Drive the real click group with both rebuilt argvs (behaviour parity)."""
    from unittest.mock import AsyncMock, MagicMock

    import yaml
    from click.testing import CliRunner

    from h3_shim.cli import hermes_h3
    from h3_shim.protocol import HealthResponse, HealthStatus

    # ``install`` health-checks the endpoint before writing it
    # (DF-H3-SHIM-FOREMAN-3).  Stub the lazily-imported client so this
    # parity test stays hermetic — it must never depend on a harness
    # actually listening on INSTALL_ENDPOINT.
    fake_client = MagicMock()
    instance = MagicMock()
    instance.health = AsyncMock(
        return_value=HealthResponse(status=HealthStatus.OK, version="1.2.3")
    )
    instance.close = AsyncMock()
    fake_client.return_value = instance
    monkeypatch.setattr("h3_shim.client.H3Client", fake_client)

    parser = _new_parser(plugin)
    runner = CliRunner()
    configs: dict[str, dict] = {}
    for label, raw in (
        (
            "flag",
            ["install", "--name", "scout", "--endpoint", INSTALL_ENDPOINT],
        ),
        ("positional", ["install", "scout", "--endpoint", INSTALL_ENDPOINT]),
    ):
        cfg = tmp_path / f"{label}.yaml"
        ns = parser.parse_args([*raw, "--config", str(cfg)])
        argv = plugin._argv_from_namespace(ns)  # type: ignore[attr-defined]
        result = runner.invoke(hermes_h3, argv)
        assert result.exit_code == 0, result.output
        assert "installed harness 'scout'" in result.output
        configs[label] = yaml.safe_load(cfg.read_text())
    assert configs["flag"] == configs["positional"]
    assert configs["flag"]["harnesses"]["scout"]["endpoint"] == INSTALL_ENDPOINT


# ── H3-GAP-092: verify NAME positional in the plugin mirror ────────────────
# ``hermes-h3 verify [NAME]`` accepts an optional positional alias for
# ``--harness``, but the plugin mirror declared only ``--harness/-H``, so
# ``hermes h3 verify myharness`` died in argparse with
# ``unrecognized arguments: myharness`` while the click command's own help
# advertised ``[NAME]``.  Mirror drift again — the same class as GAP-009 /
# DF-H3-3.  These tests drive the REAL plugin reconstruction and the real
# click group (helpers are only plumbing; every assertion consumes the
# rebuilt argv or the click invocation it produces).

VERIFY_ENDPOINT_A = "http://a:1"
VERIFY_ENDPOINT_B = "http://b:2"


def _write_two_harness_config(path: Path) -> None:
    """Config with default ``alpha`` plus a second harness ``beta``."""
    import yaml

    path.write_text(
        yaml.safe_dump(
            {
                "default_harness": "alpha",
                "harnesses": {
                    "alpha": {
                        "endpoint": VERIFY_ENDPOINT_A,
                        "transport": "rest",
                        "timeout_ms": 5000,
                    },
                    "beta": {
                        "endpoint": VERIFY_ENDPOINT_B,
                        "transport": "rest",
                        "timeout_ms": 5000,
                    },
                },
                "sessions": {},
            }
        )
    )


def _stub_healthy_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the lazily-imported ``H3Client`` in its source module."""
    from unittest.mock import AsyncMock, MagicMock

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


def _rebuild_and_invoke(
    plugin: object,
    argv: list[str],
    config_path: Path,
):
    """Parse ``argv`` with the plugin, rebuild the click argv, invoke it.

    Returns ``(rebuilt_argv, click_result)`` — the rebuilt argv is produced
    by the plugin's own ``_argv_from_namespace`` (no hand-written argv), and
    the invocation runs the real ``hermes-h3`` click group.
    """
    from click.testing import CliRunner

    from h3_shim.cli import hermes_h3

    parser = _new_parser(plugin)
    ns = parser.parse_args([*argv, "--config", str(config_path)])
    rebuilt = plugin._argv_from_namespace(ns)  # type: ignore[attr-defined]
    result = CliRunner().invoke(hermes_h3, rebuilt)
    return rebuilt, result


def test_verify_positional_name_parses(plugin: object) -> None:
    """``hermes h3 verify myharness`` parses (was exit 2: unrecognized args)."""
    parser = _new_parser(plugin)
    ns = parser.parse_args(["verify", "myharness"])
    assert ns.h3_command == "verify"
    assert ns.name == "myharness"
    assert ns.harness is None
    assert plugin._argv_from_namespace(ns) == [  # type: ignore[attr-defined]
        "verify",
        "myharness",
    ]


def test_verify_argv_rebuild_preserves_all_three_forms(plugin: object) -> None:
    """Positional / bare / --harness rebuild to distinct, faithful argvs."""
    parser = _new_parser(plugin)
    expected = {
        ("verify", "myharness"): ["verify", "myharness"],
        ("verify",): ["verify"],
        ("verify", "--harness", "beta"): ["verify", "--harness", "beta"],
    }
    for raw, want in expected.items():
        ns = parser.parse_args(list(raw))
        assert plugin._argv_from_namespace(ns) == want  # type: ignore[attr-defined]


def test_verify_positional_name_matches_direct_click(
    plugin: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``hermes h3 verify NAME`` reaches the same command path as ``hermes-h3``.

    Parity is asserted on the click OUTPUT, not just the argv shape: the
    plugin-reconstructed invocation and the direct click invocation must
    render identically.
    """
    from click.testing import CliRunner

    from h3_shim.cli import hermes_h3

    cfg = tmp_path / "config.yaml"
    _write_two_harness_config(cfg)
    _stub_healthy_client(monkeypatch)

    rebuilt, plugin_result = _rebuild_and_invoke(plugin, ["verify", "beta"], cfg)
    assert rebuilt == ["--config", str(cfg), "verify", "beta"]

    direct = CliRunner().invoke(hermes_h3, ["verify", "beta", "--config", str(cfg)])
    assert plugin_result.exit_code == direct.exit_code == 0
    assert plugin_result.output == direct.output
    assert "harness: beta" in plugin_result.output
    assert f"endpoint: {VERIFY_ENDPOINT_B}" in plugin_result.output


def test_verify_no_name_behavior_unchanged(
    plugin: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``hermes h3 verify`` with no NAME still resolves default_harness."""
    from click.testing import CliRunner

    from h3_shim.cli import hermes_h3

    cfg = tmp_path / "config.yaml"
    _write_two_harness_config(cfg)
    _stub_healthy_client(monkeypatch)

    rebuilt, result = _rebuild_and_invoke(plugin, ["verify"], cfg)
    assert rebuilt == ["--config", str(cfg), "verify"]
    assert result.exit_code == 0
    assert "harness: alpha" in result.output
    assert f"endpoint: {VERIFY_ENDPOINT_A}" in result.output

    direct = CliRunner().invoke(hermes_h3, ["verify", "--config", str(cfg)])
    assert direct.exit_code == 0
    assert result.output == direct.output


def test_verify_harness_flag_path_unchanged(
    plugin: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``hermes h3 verify --harness NAME`` keeps its existing behaviour."""
    cfg = tmp_path / "config.yaml"
    _write_two_harness_config(cfg)
    _stub_healthy_client(monkeypatch)

    rebuilt, result = _rebuild_and_invoke(plugin, ["verify", "--harness", "beta"], cfg)
    assert rebuilt == ["--config", str(cfg), "verify", "--harness", "beta"]
    assert result.exit_code == 0
    assert "harness: beta" in result.output
    assert f"endpoint: {VERIFY_ENDPOINT_B}" in result.output


def test_verify_positional_wins_over_harness_flag(
    plugin: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both given → the click command's own rule applies (NAME wins).

    The mirror forwards both tokens and lets click resolve them, so no new
    precedence rule is invented in the plugin.
    """
    cfg = tmp_path / "config.yaml"
    _write_two_harness_config(cfg)
    _stub_healthy_client(monkeypatch)

    rebuilt, result = _rebuild_and_invoke(
        plugin, ["verify", "alpha", "--harness", "beta"], cfg
    )
    assert rebuilt == ["--config", str(cfg), "verify", "alpha", "--harness", "beta"]
    assert result.exit_code == 0
    assert "harness: alpha" in result.output
    assert f"endpoint: {VERIFY_ENDPOINT_A}" in result.output
    assert "harness: beta" not in result.output


# ── H3-GAP-091: route --session in the plugin mirror ───────────────────────
# ``hermes-h3 route [--session <id>]`` narrows the routing table to one
# session binding (dict *or* bare-string form) and fails closed on an
# unknown id.  The plugin mirror registered no options for ``route`` at
# all, so ``hermes h3 route --session <id>`` died in argparse with
# ``unrecognized arguments: --session`` — the same mirror-drift class as
# GAP-009 / DF-H3-3 / H3-GAP-092.  These tests drive the REAL plugin
# reconstruction and the real click group; every assertion consumes the
# rebuilt argv or the click invocation it produces.

ROUTE_DICT_SID = "sess-dict-1"
ROUTE_STR_SID = "sess-str-2"
ROUTE_UNKNOWN_SID = "sess-not-there"


def _write_session_config(path: Path) -> None:
    """Config holding one dict binding and one bare-string binding."""
    import yaml

    path.write_text(
        yaml.safe_dump(
            {
                "default_harness": "alpha",
                "harnesses": {
                    "alpha": {
                        "endpoint": VERIFY_ENDPOINT_A,
                        "transport": "rest",
                        "timeout_ms": 5000,
                    },
                    "beta": {
                        "endpoint": VERIFY_ENDPOINT_B,
                        "transport": "rest",
                        "timeout_ms": 5000,
                    },
                },
                "sessions": {
                    ROUTE_DICT_SID: {"harness": "alpha"},
                    ROUTE_STR_SID: "beta",
                },
            }
        )
    )


def test_route_session_flag_parses(plugin: object) -> None:
    """``hermes h3 route --session X`` parses (was exit 2: unrecognized args)."""
    parser = _new_parser(plugin)
    ns = parser.parse_args(["route", "--session", ROUTE_DICT_SID])
    assert ns.h3_command == "route"
    assert ns.session == ROUTE_DICT_SID
    assert plugin._argv_from_namespace(ns) == [  # type: ignore[attr-defined]
        "route",
        "--session",
        ROUTE_DICT_SID,
    ]


def test_route_no_flag_argv_unchanged(plugin: object) -> None:
    """An unset ``--session`` is omitted, so the forwarded argv is ``route``.

    The mirror drops options left at their default, which is what keeps
    the no-flag invocation byte-identical to what it was before GAP-091.
    """
    parser = _new_parser(plugin)
    ns = parser.parse_args(["route"])
    assert ns.session is None
    assert plugin._argv_from_namespace(ns) == ["route"]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("sid", "harness"),
    [(ROUTE_DICT_SID, "alpha"), (ROUTE_STR_SID, "beta")],
    ids=["dict-binding", "bare-string-binding"],
)
def test_route_session_binding_found(
    plugin: object, tmp_path: Path, sid: str, harness: str
) -> None:
    """Both binding shapes print ``<id> -> <harness>`` through the real click job."""
    from click.testing import CliRunner

    from h3_shim.cli import hermes_h3

    cfg = tmp_path / "config.yaml"
    _write_session_config(cfg)

    rebuilt, result = _rebuild_and_invoke(plugin, ["route", "--session", sid], cfg)
    assert rebuilt == ["--config", str(cfg), "route", "--session", sid]
    assert result.exit_code == 0, result.output
    assert result.output == f"{sid} -> {harness}\n"

    direct = CliRunner().invoke(
        hermes_h3, ["route", "--session", sid, "--config", str(cfg)]
    )
    assert direct.exit_code == 0
    assert result.output == direct.output


def test_route_unknown_session_fails_closed(plugin: object, tmp_path: Path) -> None:
    """An unknown id exits non-zero and names the id (no silent empty table)."""
    from click.testing import CliRunner

    from h3_shim.cli import hermes_h3

    cfg = tmp_path / "config.yaml"
    _write_session_config(cfg)

    rebuilt, result = _rebuild_and_invoke(
        plugin, ["route", "--session", ROUTE_UNKNOWN_SID], cfg
    )
    assert rebuilt == ["--config", str(cfg), "route", "--session", ROUTE_UNKNOWN_SID]
    assert result.exit_code != 0
    assert ROUTE_UNKNOWN_SID in result.output
    assert "->" not in result.output

    direct = CliRunner().invoke(
        hermes_h3, ["route", "--session", ROUTE_UNKNOWN_SID, "--config", str(cfg)]
    )
    assert direct.exit_code == result.exit_code
    assert direct.output == result.output


def test_route_no_flag_behavior_unchanged(plugin: object, tmp_path: Path) -> None:
    """``hermes h3 route`` with no flag still prints the full table, unchanged."""
    from click.testing import CliRunner

    from h3_shim.cli import hermes_h3

    cfg = tmp_path / "config.yaml"
    _write_session_config(cfg)

    rebuilt, result = _rebuild_and_invoke(plugin, ["route"], cfg)
    assert rebuilt == ["--config", str(cfg), "route"]

    direct = CliRunner().invoke(hermes_h3, ["route", "--config", str(cfg)])
    assert result.exit_code == direct.exit_code == 0
    assert result.output == direct.output
    assert f"{'SESSION':40s} HARNESS" in result.output
    assert f"{ROUTE_DICT_SID:40s} alpha" in result.output
    assert f"{ROUTE_STR_SID:40s} beta" in result.output
