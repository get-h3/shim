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

Install — copy this directory into the plugins PARENT, never into an
existing plugin directory::

    cp -r h3 ~/.hermes/plugins/          # -> ~/.hermes/plugins/h3/
    hermes plugins enable h3

Refreshing an existing install means REPLACING it in place::

    rsync -a --delete h3/ ~/.hermes/plugins/h3/

``cp -r h3 <plugins-dir>/h3/`` — the form this repo used to document —
copies INTO that directory once it exists, NESTING the fresh copy at
``<plugins-dir>/h3/h3/`` while the stale copy keeps serving.  The
symptom is confusing rather than loud: a subcommand form that
``hermes h3 --help`` documents dies with ``unrecognized arguments``
from the stale mirror.  ``register()`` warns (Hermes log + stderr)
when the installed copy looks nested or older than
``PLUGIN_VERSION``; see docs/integration.md §3.4.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

PLUGIN_VERSION = "0.3.0"
"""Installed-copy (mirror) version marker (DF5-H3-SHIM-3).

The same string ships in the repo as ``h3/_plugin_version.txt``.  An
installed copy keeps its own marker file, so ``register()`` can compare
the *installed* mirror version against the one this code expects and
warn when an install predates features the mirror claims to offer.
Bump together with plugin behaviour changes.
"""

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
    "expect_fresh": "--expect-fresh",
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
    "session": "--session",
}

# click positional arguments per subcommand (in order)
_POSITIONALS: dict[str, tuple[str, ...]] = {
    "install": ("name",),
    "uninstall": ("name",),
    "use": ("name",),
    "pre-update-check": ("target_version",),
    # ``verify [NAME]`` — optional positional alias for ``--harness``.
    "verify": ("name",),
}

# value options declared by the mirror per subcommand
_OPTIONS: dict[str, tuple[str, ...]] = {
    "test": ("harness", "endpoint", "as_json", "categories", "expect_fresh"),
    "install": ("endpoint", "transport", "timeout_ms", "set_default"),
    "verify": ("harness", "endpoint", "fallback"),
    "scaffold": ("force", "lang", "output_dir"),
    "pre-update-check": ("versions_yaml_path",),
    # ``route [--session <id>]`` — narrows the routing table to one binding.
    "route": ("session",),
}

# argparse defaults per subcommand — options left at their default are
# omitted from the reconstructed argv (click applies the same default).
_DEFAULTS: dict[str, dict[str, Any]] = {
    "test": {
        "harness": None,
        "endpoint": None,
        "as_json": False,
        "categories": None,
        "expect_fresh": False,
    },
    "install": {
        "endpoint": None,
        "transport": "rest",
        "timeout_ms": 30000,
        "set_default": False,
        "name_opt": None,
    },
    "verify": {"harness": None, "endpoint": None, "fallback": False},
    "scaffold": {"force": False, "lang": None, "output_dir": None},
    "pre-update-check": {"versions_yaml_path": None},
    "route": {"session": None},
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
    p.add_argument(
        "--expect-fresh",
        dest="expect_fresh",
        action="store_true",
        help=(
            "Refuse to run when the target's /v1/health uptime exceeds 300s "
            "(stale co-tenant harness); exits 1 before the first test."
        ),
    )

    p = sub.add_parser("list", help="List harnesses known to the config.")
    _add_config_option(p)

    p = sub.add_parser("install", help="Register a harness in the config.")
    _add_config_option(p)
    p.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Harness name (may also be given as --name; positional wins).",
    )
    p.add_argument(
        "--name",
        dest="name_opt",
        default=None,
        help="Harness name (alias for the positional NAME).",
    )
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
        "name",
        nargs="?",
        default=None,
        help="Named harness from config (alias for --harness; positional wins).",
    )
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
    p.add_argument(
        "--session",
        default=None,
        help="Show only the binding for this session id.",
    )

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
        value = ns_dict.get(field)
        if value is None:
            # Optional positionals (``install NAME``) have a ``--<field>``
            # alias; fall back to it so ``install --name X`` rebuilds the
            # argv as the positional form.  An unresolved name is left out
            # entirely so click reports the missing NAME.
            value = ns_dict.get(f"{field}_opt")
        if value is not None:
            argv.append(str(value))

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


def _propagate(code: int) -> None:
    """Fail the process with *code* instead of only returning it.

    Hermes Core turns a handler's non-zero int return into the process exit
    code, but that is a *host* convention, not a contract this plugin can
    rely on: a host that calls the handler and discards the value
    (``args.func(args)`` with no assignment) turns every delegated-CLI
    failure into exit 0 — the reported "``hermes h3`` exits 0 on the
    failure" symptom, which no script or CI gate can catch.
    ``SystemExit`` escapes both conventions, so failures raise it.
    """
    raise SystemExit(code)


def _exit_code_from_system_exit(exc: SystemExit) -> int:
    """Normalise an ``SystemExit`` payload to an int exit code.

    ``exc.code`` is ``None`` (exit 0), an int, or a *string* — CPython
    prints a string payload and exits 1.  Returning a string as if it were
    a code silently produced exit 0 with the message dropped.
    """
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    print(code, file=sys.stderr)
    return 1


def _handler(args: argparse.Namespace) -> None:
    """Dispatch ``hermes h3 ...`` to the real CLI.

    Returns ``None`` on success; every failure raises ``SystemExit`` with
    the delegated CLI's exit code (argparse/click usage errors 2, click
    command failures 1, ``hermes-h3 test`` 1 or 2).
    """
    argv = _argv_from_namespace(args)

    if _CLICK_GROUP is not None and click is not None:
        try:
            returned = _CLICK_GROUP.main(
                args=argv, prog_name="hermes h3", standalone_mode=False
            )
        except click.ClickException as exc:
            click.echo(f"Error: {exc.format_message()}", err=True)
            _propagate(exc.exit_code)
        except SystemExit as exc:
            code = _exit_code_from_system_exit(exc)
        else:
            # click documents that a non-standalone ``main()`` *returns* the
            # command's int return value and ``ctx.exit(n)``'s code instead
            # of raising — discarding it was an exit 0 for both.
            code = returned if isinstance(returned, int) else 0
        if code:
            _propagate(code)
        return

    exe = shutil.which("hermes-h3")
    if exe is None:
        print(
            "Error: hermes-h3 not found on PATH — install the hermes-h3-shim "
            "package (pip install git+https://github.com/get-h3/shim).",
            file=sys.stderr,
        )
        _propagate(1)
        return  # unreachable: _propagate raises (keeps type checkers honest)
    proc = subprocess.run([exe, *argv], check=False)
    if proc.returncode > 0:
        _propagate(proc.returncode)
    elif proc.returncode < 0:
        # Killed by signal N — report the shell convention (128 + N) rather
        # than a negative code, which Python would wrap to 256 - N.
        _propagate(128 - proc.returncode)


# Install-fix commands named in the staleness warning (DF5-H3-SHIM-3):
# parent-target copy, and the in-place refresh for an existing install.
_FIX_PARENT = "cp -r h3 ~/.hermes/plugins/"
_FIX_RSYNC = "rsync -a --delete h3/ ~/.hermes/plugins/h3/"


def _parse_version(value: str) -> tuple[int, ...]:
    """Best-effort ``1.2.3`` → ``(1, 2, 3)``; unparseable → ``(0,)``.

    ``(0,)`` deliberately compares OLDER than any real ``0.x``/``1.x``
    marker so a corrupt marker is reported stale (safe direction).
    """
    parts: list[int] = []
    for chunk in value.strip().split("."):
        if not chunk.isdigit():
            return (0,)
        parts.append(int(chunk))
    return tuple(parts) if parts else (0,)


def _stale_install_reasons(base: Path) -> list[str]:
    """Return human-readable reasons the install rooted at ``base`` is stale.

    ``base`` is the installed plugin directory (``~/.hermes/plugins/h3``).
    Two signatures are checked: the classic cp -r NEST (a nested ``h3/``
    subdirectory — the fresh copy buried inside the stale one, which
    keeps serving) and a ``_plugin_version.txt`` marker older than this
    copy's ``PLUGIN_VERSION`` (or missing/unparseable — a pre-marker or
    corrupted install).  Empty list = install looks current.
    """
    reasons: list[str] = []
    nested = base / "h3"
    if nested.is_dir():
        reasons.append(
            "nested install: "
            f"'{base / 'h3'}' exists — a previous install copied INTO this "
            "directory instead of replacing it, so the stale copy keeps "
            "serving"
        )
    marker = base / "_plugin_version.txt"
    if not marker.is_file():
        reasons.append(
            f"no version marker at '{marker}' — pre-marker or corrupted "
            "install, cannot prove it is current"
        )
        return reasons
    installed_text = marker.read_text(encoding="utf-8").strip()
    if _parse_version(installed_text) < _parse_version(PLUGIN_VERSION):
        reasons.append(
            f"installed mirror is stale: marker says '{installed_text}', "
            f"this copy is {PLUGIN_VERSION}"
        )
    return reasons


def _warn_on_stale_install(base: Path) -> None:
    """Warn loudly per staleness reason, naming the fix command.

    Emitted to BOTH the Hermes logger and stderr: a nested/stale install is
    exactly the failure mode that is otherwise silent, and ``hermes h3`` is
    usually run from a script whose operator never opens the log.
    """
    for reason in _stale_install_reasons(base):
        message = (
            f"h3 plugin install looks STALE ({reason}). The CLI mirror you "
            f"are running may not match the hermes-h3 CLI it delegates to. "
            f"Fix: reinstall with '{_FIX_PARENT}' (parent target, no "
            f"pre-existing h3 dir) or refresh in place with '{_FIX_RSYNC}'."
        )
        logger.warning(message)
        print(f"WARNING: {message}", file=sys.stderr)


def register(ctx: Any, base: Path | str | None = None) -> None:
    """Register the ``h3`` CLI subcommand group with Hermes Core.

    ``base`` overrides the installed-plugin directory for the
    staleness check (defaults to the directory containing this file);
    it exists so the check is testable without touching the real
    ``~/.hermes``.
    """
    install_base = Path(base) if base is not None else Path(__file__).resolve().parent
    _warn_on_stale_install(install_base)
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
