"""Hermes Core plugin: ``hermes h3 <subcommand>``.

Registers an ``h3`` top-level CLI group with Hermes Core
(:meth:`PluginContext.register_cli_command`) that delegates to the
``hermes-h3`` console script shipped by the ``hermes-h3-shim`` package
(``h3_shim.cli``).  The argparse tree mirrors the click CLI so
``hermes h3 --help`` lists the same nine subcommands, and every
invocation is passed through to the real CLI so behaviour is identical:

* **In-process** — when ``h3_shim`` is importable in the interpreter
  that runs Hermes (e.g. the shim was pip-installed into the same
  virtualenv), the click group is invoked directly.
* **Subprocess** — otherwise (the common case: ``hermes`` runs from its
  own venv while ``hermes-h3`` lives on PATH from a different
  interpreter), the reconstructed argv is forwarded to the
  ``hermes-h3`` executable and its exit code is propagated.

Install: copy this directory to ``~/.hermes/plugins/h3/`` and run
``hermes plugins enable h3``.  See docs/integration.md in this repo.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from typing import Any

logger = logging.getLogger(__name__)

# Optional imports — the plugin must degrade gracefully when the shim or
# click live in a different interpreter than the one running Hermes.
try:  # pragma: no cover - host-dependent
    import click  # noqa: F401
except ImportError:  # pragma: no cover - host-dependent
    click = None  # type: ignore[assignment]

try:  # pragma: no cover - host-dependent
    from h3_shim.cli import (
        hermes_h3 as _CLICK_GROUP,  # noqa: N812 - module alias, intentional
    )
except Exception:  # pragma: no cover - host-dependent
    _CLICK_GROUP = None


# ---------------------------------------------------------------------------
# argparse mirror of the hermes-h3 click group (h3_shim/cli.py)
# ---------------------------------------------------------------------------
# dest → click flag name for boolean flags
_FLAG_FLAGS = {
    "as_json": "--json",
    "set_default": "--set-default",
    "fallback": "--fallback",
    "force": "--force",
}

# dest → click option name for value options
_VALUE_FLAGS = {
    "harness": "--harness",
    "endpoint": "--endpoint",
    "categories": "--categories",
    "transport": "--transport",
    "timeout_ms": "--timeout-ms",
    "lang": "--lang",
    "output_dir": "--output-dir",
    "versions_yaml_path": "--versions-yaml",
}

# click positional arguments per subcommand (in order)
_POSITIONALS: dict[str, tuple[str, ...]] = {
    "install": ("name",),
    "uninstall": ("name",),
    "use": ("name",),
    "pre-update-check": ("target_version",),
}

# value options declared by the mirror per subcommand
_OPTIONS: dict[str, tuple[str, ...]] = {
    "test": ("harness", "endpoint", "as_json", "categories"),
    "install": ("endpoint", "transport", "timeout_ms", "set_default"),
    "verify": ("harness", "endpoint", "fallback"),
    "scaffold": ("force", "lang", "output_dir"),
    "pre-update-check": ("versions_yaml_path",),
}

# argparse defaults per subcommand — options left at their default are
# omitted from the reconstructed argv (click applies the same default).
_DEFAULTS: dict[str, dict[str, Any]] = {
    "test": {"harness": None, "endpoint": None, "as_json": False, "categories": None},
    "install": {
        "endpoint": None,
        "transport": "rest",
        "timeout_ms": 30000,
        "set_default": False,
    },
    "verify": {"harness": None, "endpoint": None, "fallback": False},
    "scaffold": {"force": False, "lang": None, "output_dir": None},
    "pre-update-check": {"versions_yaml_path": None},
}


def _add_config_option(parser: argparse.ArgumentParser) -> None:
    """Register ``--config`` (dest ``h3_config``) on a subparser.

    The subparser copy uses ``default=argparse.SUPPRESS`` so that when
    ``--config`` is given *before* the subcommand (parsed by the parent
    parser), the subparser does not overwrite ``ns.h3_config`` with
    ``None`` — argparse copies every subparser attribute back onto the
    parent namespace, and an explicit ``default=None`` would clobber a
    parent-set value.  With ``SUPPRESS``, the attribute is only written
    when the user actually passes ``--config`` after the subcommand.
    """
    parser.add_argument(
        "--config",
        dest="h3_config",
        default=argparse.SUPPRESS,
        metavar="PATH",
        help="Override config path (default: ~/.hermes/h3/config.yaml).",
    )


def _setup(parser: argparse.ArgumentParser) -> None:
    """Build the ``hermes h3`` argparse tree (mirrors ``hermes-h3``).

    ``--config`` is accepted both *before* the subcommand (parent parser)
    and *after* it (every subparser).  This matches the standalone
    ``hermes-h3`` click CLI, which accepts ``--config`` in either
    position, and lets ``hermes h3 list --config X`` work just like
    ``hermes h3 --config X list``.
    """
    parser.add_argument(
        "--config",
        dest="h3_config",
        default=None,
        metavar="PATH",
        help="Override config path (default: ~/.hermes/h3/config.yaml).",
    )
    sub = parser.add_subparsers(dest="h3_command", metavar="COMMAND")

    p = sub.add_parser("test", help="Run the H3 compliance test battery.")
    _add_config_option(p)
    p.add_argument(
        "--harness",
        "-H",
        default=None,
        help="Named harness from config (defaults to default_harness).",
    )
    p.add_argument(
        "--endpoint", default=None, help="Override endpoint URL (skip config lookup)."
    )
    p.add_argument(
        "--json", dest="as_json", action="store_true", help="Emit JSON report."
    )
    p.add_argument(
        "--categories", default=None, help="Comma-separated categories to run."
    )

    p = sub.add_parser("list", help="List harnesses known to the config.")
    _add_config_option(p)

    p = sub.add_parser("install", help="Register a harness in the config.")
    _add_config_option(p)
    p.add_argument("name", help="Harness name.")
    p.add_argument("--endpoint", required=True, help="Harness endpoint URL.")
    p.add_argument(
        "--transport",
        default="rest",
        help="Transport protocol (only 'rest' is implemented).",
    )
    p.add_argument(
        "--timeout-ms",
        dest="timeout_ms",
        type=int,
        default=30000,
        help="Default request timeout in milliseconds.",
    )
    p.add_argument(
        "--set-default",
        dest="set_default",
        action="store_true",
        help="Promote this harness to default_harness.",
    )
    p.add_argument(
        "--no-set-default",
        dest="set_default",
        action="store_false",
        help="Do not promote this harness to default_harness.",
    )

    p = sub.add_parser("uninstall", help="Remove a harness from the config.")
    _add_config_option(p)
    p.add_argument("name", help="Harness name.")

    p = sub.add_parser("verify", help="Health-check a harness via the H3 REST client.")
    _add_config_option(p)
    p.add_argument(
        "--harness",
        "-H",
        default=None,
        help="Named harness from config (defaults to default_harness).",
    )
    p.add_argument(
        "--endpoint", default=None, help="Override endpoint URL (skip config lookup)."
    )
    p.add_argument(
        "--fallback", action="store_true", help="Also test the native fallback path."
    )

    p = sub.add_parser(
        "scaffold",
        help="Create an empty config file or scaffold a new harness project.",
    )
    _add_config_option(p)
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing config file or project directory.",
    )
    p.add_argument(
        "--lang",
        choices=("go", "py", "ts"),
        default=None,
        help="Generate a complete harness project for the given language.",
    )
    p.add_argument(
        "--output-dir",
        dest="output_dir",
        default=None,
        help=(
            "Parent directory under which the new project is created "
            "(default: current directory)."
        ),
    )

    p = sub.add_parser("route", help="Show the session → harness routing table.")
    _add_config_option(p)

    p = sub.add_parser(
        "pre-update-check",
        help="Run pre-flight compatibility checks before hermes update.",
    )
    _add_config_option(p)
    p.add_argument(
        "target_version", help="Hermes version you plan to upgrade to (e.g. 0.19.0)."
    )
    p.add_argument(
        "--versions-yaml",
        dest="versions_yaml_path",
        default=None,
        help="Path to versions.yaml (default: auto-detect in protocol repo).",
    )

    p = sub.add_parser("use", help="Set the default harness.")
    _add_config_option(p)
    p.add_argument("name", help="Harness name.")


def _argv_from_namespace(ns: argparse.Namespace) -> list[str]:
    """Rebuild the click-style argv from the parsed namespace.

    Independent of ``sys.argv`` layout so ``hermes h3 ...`` works no
    matter what top-level flags the user passed before ``h3``.
    """
    argv: list[str] = []
    config = getattr(ns, "h3_config", None)
    if config:
        argv += ["--config", str(config)]
    cmd = getattr(ns, "h3_command", None)
    if cmd is None:
        return argv
    argv.append(cmd)

    ns_dict = vars(ns)
    for field in _POSITIONALS.get(cmd, ()):
        argv.append(str(ns_dict[field]))

    defaults = _DEFAULTS.get(cmd, {})
    for field in _OPTIONS.get(cmd, ()):
        value = ns_dict[field]
        if value == defaults.get(field):
            continue
        if field in _FLAG_FLAGS:
            argv.append(_FLAG_FLAGS[field])
        elif field in _VALUE_FLAGS:
            argv += [_VALUE_FLAGS[field], str(value)]
    return argv


def _handler(args: argparse.Namespace) -> int | None:
    """Dispatch ``hermes h3 ...`` to the real CLI.

    Returns ``None`` on success (exit 0) or the exit code to propagate.
    """
    argv = _argv_from_namespace(args)

    if _CLICK_GROUP is not None and click is not None:
        try:
            _CLICK_GROUP.main(args=argv, prog_name="hermes h3", standalone_mode=False)
        except click.ClickException as exc:
            click.echo(f"Error: {exc.format_message()}", err=True)
            return exc.exit_code
        except SystemExit as exc:
            code = exc.code
            return code if isinstance(code, int) and code != 0 else None
        return None

    exe = shutil.which("hermes-h3")
    if exe is None:
        print(
            "Error: hermes-h3 not found on PATH — install the hermes-h3-shim "
            "package (pip install git+https://github.com/get-h3/shim).",
            file=sys.stderr,
        )
        return 1
    proc = subprocess.run([exe, *argv], check=False)
    return proc.returncode or None


def register(ctx: Any) -> None:
    """Register the ``h3`` CLI subcommand group with Hermes Core."""
    ctx.register_cli_command(
        name="h3",
        help="H3 harness management (delegates to the hermes-h3 CLI)",
        setup_fn=_setup,
        handler_fn=_handler,
        description=(
            "Manage H3 harnesses from the Hermes CLI: install, list, verify, "
            "test, scaffold, route, use, uninstall, and pre-update-check. "
            "Requires the hermes-h3-shim package (hermes-h3 on PATH)."
        ),
    )
    logger.info("h3 plugin loaded: registered 'hermes h3' command group")
