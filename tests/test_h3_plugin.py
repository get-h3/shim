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
