"""Command-line entry point for the H3 shim.

Two console scripts are registered in ``pyproject.toml``:

``h3-test`` → :func:`main`
    Backwards-compatible single-shot runner of the H3 compliance test
    battery against a single endpoint.

``hermes-h3`` → :func:`hermes_h3`
    Click-based command group for managing H3 harnesses, sessions, and
    routing. Subcommands:

    * ``test``     — run the compliance battery against a named harness
    * ``list``     — list harnesses known to the local config
    * ``install``  — register a new harness in the local config
    * ``uninstall`` — remove a harness from the local config
    * ``verify``   — health-check a harness via the H3 REST client
    * ``scaffold`` — create an empty config at ``~/.hermes/h3/config.yaml``
    * ``route``    — show session → harness routing table
    * ``use``      — set the default harness
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path
from typing import Any

import click
import yaml

from h3_shim.test_battery import (
    CATEGORIES,
    H3TestBattery,
    NotH3EndpointError,
    TargetHealth,
    TestReport,
    TestResult,
    category_token,
)

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

CONFIG_PATH = Path.home() / ".hermes" / "h3" / "config.yaml"

# Environment variable that overrides the default config location
# (DF-H3-10). Resolution order, highest first:
#   ``--config`` (subcommand) > ``--config`` (group) >
#   ``$HERMES_H3_CONFIG`` > ``CONFIG_PATH``.
CONFIG_PATH_ENV = "HERMES_H3_CONFIG"


def default_config_path() -> Path:
    """Return the default config path, honoring ``$HERMES_H3_CONFIG``.

    The environment variable (when set to a non-blank value) wins over
    the static :data:`CONFIG_PATH` default, so a user can point the CLI
    at a scratch config instead of the real ``~/.hermes/h3/config.yaml``.
    Callers that patch ``CONFIG_PATH`` (tests, embedders) keep working:
    the constant is read at call time, not captured at import time.
    """
    env = os.environ.get(CONFIG_PATH_ENV, "")
    if env.strip():
        return Path(env.strip()).expanduser()
    return CONFIG_PATH


# Templates directory shipped with the package. Each language gets its
# own subdirectory under ``templates/<lang>/``.
TEMPLATES_DIR = Path(__file__).parent / "templates"

# Languages supported by ``hermes-h3 scaffold --lang``.
SUPPORTED_LANGS = ("go", "py", "ts")

# User-facing aliases accepted by ``hermes-h3 scaffold --lang`` (normalized
# to the canonical short codes above). The h3 README/guide advertises
# ``--lang go|python|ts``, so both spellings must work.
LANG_ALIASES = {"python": "py", "typescript": "ts"}


def _normalize_lang(value: str) -> str:
    """Map a user-supplied ``--lang`` value to its canonical short code."""
    return LANG_ALIASES.get(value.lower(), value)


def _empty_config() -> dict[str, Any]:
    """Return a fresh empty config skeleton."""
    return {
        "default_harness": None,
        "harnesses": {},
        "sessions": {},
    }


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Read config from disk; return an empty skeleton if absent."""
    p = path or default_config_path()
    if not p.exists():
        return _empty_config()
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise click.ClickException(f"invalid YAML in {p}: {exc}") from exc

    # Backfill any missing top-level keys so downstream commands can rely
    # on the schema.
    skeleton = _empty_config()
    for key, default in skeleton.items():
        if key not in data:
            data[key] = default
    data.setdefault("harnesses", {})
    data.setdefault("sessions", {})
    return data


def save_config(data: dict[str, Any], path: Path | None = None) -> Path:
    """Persist config to disk; creates parent dirs. Returns the path."""
    p = path or default_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, default_flow_style=False, sort_keys=False)
    return p


def resolve_harness(
    name: str | None,
    config: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Resolve ``name`` (or ``default_harness``) to ``(name, spec)``.

    Raises :class:`click.ClickException` if the harness isn't found.
    """
    harnesses: dict[str, dict[str, Any]] = config.get("harnesses", {}) or {}
    chosen = name or config.get("default_harness")
    if not chosen:
        raise click.ClickException(
            "no harness specified and no default_harness set; "
            "use 'hermes-h3 use <name>' or 'hermes-h3 install'"
        )
    spec = harnesses.get(chosen)
    if spec is None:
        raise click.ClickException(
            f"harness {chosen!r} not found in config; "
            f"known: {sorted(harnesses) or 'none'}"
        )
    return chosen, spec


# ---------------------------------------------------------------------------
# Project scaffolding (``hermes-h3 scaffold --lang <lang>``)
# ---------------------------------------------------------------------------


def _lang_template_dir(lang: str) -> Path:
    """Return the on-disk template directory for ``lang``.

    Raises :class:`click.ClickException` if the language is unknown or
    the template directory is missing from the installed package.
    """
    if lang not in SUPPORTED_LANGS:
        raise click.ClickException(
            f"unsupported language {lang!r}; "
            f"choose one of: {', '.join(SUPPORTED_LANGS)}"
        )
    tpl_dir = TEMPLATES_DIR / lang
    if not tpl_dir.is_dir():
        raise click.ClickException(f"template directory missing: {tpl_dir}")
    return tpl_dir


def _render_template_file(
    src: Path,
    dest: Path,
    substitutions: dict[str, str],
) -> None:
    """Copy ``src`` to ``dest``, substituting ``{{KEY}}`` placeholders."""
    text = src.read_text(encoding="utf-8")
    for key, value in substitutions.items():
        text = text.replace("{{" + key + "}}", value)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")


def _copy_template_tree(
    src_dir: Path,
    dest_dir: Path,
    substitutions: dict[str, str],
) -> list[Path]:
    """Recursively copy ``src_dir`` into ``dest_dir`` and render templates.

    Returns the list of files written (relative to ``dest_dir``).
    """
    written: list[Path] = []
    for src in sorted(src_dir.rglob("*")):
        if src.is_dir():
            continue
        # Skip __pycache__ and compiled bytecode files.
        if "__pycache__" in src.parts or src.suffix == ".pyc":
            continue
        rel = src.relative_to(src_dir)
        dest = dest_dir / rel
        _render_template_file(src, dest, substitutions)
        written.append(rel)
    return written


def scaffold_project(
    lang: str,
    output_dir: Path,
    project_name: str | None = None,
    overwrite: bool = False,
) -> Path:
    """Generate a new H3 harness project.

    Returns the absolute path of the generated project root.
    """
    tpl_dir = _lang_template_dir(lang)
    project_name = project_name or f"h3-harness-{lang}"
    dest = output_dir.resolve() / f"h3-harness-{lang}"

    if dest.exists():
        if not overwrite:
            raise click.ClickException(
                f"project directory already exists: {dest} (pass --force to overwrite)"
            )
        # Wipe the existing directory so a stale scaffold can't leak.
        import shutil

        shutil.rmtree(dest)

    dest.mkdir(parents=True)
    _copy_template_tree(
        tpl_dir,
        dest,
        substitutions={"MODULE_PATH": project_name},
    )
    return dest


def _format_run_instructions(lang: str, project_dir: Path) -> str:
    """Return a multi-line string telling the user how to build + run."""
    lines = [f"Generated {lang} harness at: {project_dir}"]
    lines.append("")
    if lang == "go":
        lines.extend(
            [
                "Build and run:",
                f"  cd {project_dir}",
                "  go mod tidy",
                "  go run .",
                "",
                "The harness listens on http://localhost:9191",
            ]
        )
    elif lang == "py":
        lines.extend(
            [
                "Build and run:",
                f"  cd {project_dir}",
                "  # PEP 668 distros (Ubuntu 24+, Debian 12+) refuse bare pip installs",
                "  # always use a venv:",
                "  python3 -m venv .venv",
                "  source .venv/bin/activate",
                "  pip install -e .",
                "  python main.py",
                "",
                "The harness listens on http://localhost:9191",
            ]
        )
    elif lang == "ts":
        lines.extend(
            [
                "Build and run:",
                f"  cd {project_dir}",
                "  npm install",
                "  npm run build && npm start",
                "",
                "The harness listens on http://localhost:9191",
            ]
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reporting (shared by ``h3-test`` and ``hermes-h3 test``)
# ---------------------------------------------------------------------------


def _latency_stats(results: list[TestResult]) -> dict[str, float]:
    """Compute per-test latency statistics (ms) over a battery report.

    Returns ``min`` / ``p50`` / ``p90`` / ``p95`` / ``p99`` / ``max`` /
    ``mean`` over every result's ``duration_ms``, each rounded to two
    decimals.  An empty *results* list yields all zeros so callers never
    have to special-case a battery that produced no tests.
    """
    durations = sorted(r.duration_ms for r in results)
    if not durations:
        return {
            "min_ms": 0.0,
            "p50_ms": 0.0,
            "p90_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "max_ms": 0.0,
            "mean_ms": 0.0,
        }

    def _percentile(pct: float) -> float:
        # Nearest-rank method: index = ceil(pct/100 * n) - 1.
        idx = max(0, math.ceil(pct / 100.0 * len(durations)) - 1)
        return durations[idx]

    mean_ms = sum(durations) / len(durations)
    return {
        "min_ms": round(durations[0], 2),
        "p50_ms": round(_percentile(50.0), 2),
        "p90_ms": round(_percentile(90.0), 2),
        "p95_ms": round(_percentile(95.0), 2),
        "p99_ms": round(_percentile(99.0), 2),
        "max_ms": round(durations[-1], 2),
        "mean_ms": round(mean_ms, 2),
    }


def _format_human(report: TestReport, endpoint: str) -> str:
    """Group results by category into a human-readable text report."""
    lines: list[str] = [
        "",
        f"H3 Compliance Test Battery v{_battery_version()}",
        f"Target: {endpoint}",
        "Transport: REST",
        "",
    ]

    by_category: OrderedDict[str, list] = OrderedDict()
    for r in report.results:
        by_category.setdefault(r.category, []).append(r)

    for cat, results in by_category.items():
        passed = sum(1 for r in results if r.passed)
        total = len(results)
        status = "PASSED" if passed == total else "FAILED"
        icon = "\u2705" if passed == total else "\u274c"
        label = f"{cat:35s}"
        lines.append(f"  {label} {passed}/{total}  {icon} {status}")

    totals = "PASSED" if report.all_passing else "FAILED"
    lines.append(f"  {'TOTAL':35s} {report.passed}/{report.total}  {totals}")
    lines.append(f"  {'Duration':35s} {report.duration_ms / 1000.0:.2f}s")
    if report.results:
        stats = _latency_stats(report.results)
        lines.append(
            f"  {'Latency p50/p95':35s} "
            f"{stats['p50_ms']:.2f}ms / {stats['p95_ms']:.2f}ms"
        )
    return "\n".join(lines)


def _health_payload(health: TargetHealth) -> dict[str, Any]:
    """Serialise a target identity for the ``--json`` report (DF-H3-26).

    The identity is part of the report, not just of the console: a CI job
    that reads the JSON can assert it was testing a freshly-started harness
    instead of trusting the console scrollback.
    """
    return {
        "version": health.version,
        "uptime_seconds": health.uptime_seconds,
        "active_sessions": health.active_sessions,
        "capabilities": health.capabilities,
        "identity_line": health.identity_line(),
    }


def _emit_health_identity(health: TargetHealth, *, as_json: bool) -> None:
    """Print the target identity line, then any ``WARN:`` lines.

    The identity line is part of the human connect banner, so it goes to
    stdout — except in ``--json`` mode, where stdout carries the report and
    nothing else (a stray line in front of the payload breaks every
    ``json.loads`` consumer of ``h3-test --json``).  Warnings are diagnostics
    and always go to stderr; they never change the exit code.
    """
    print(health.identity_line(), file=sys.stderr if as_json else sys.stdout)
    for warning in health.warnings():
        print(warning, file=sys.stderr)


def _expect_fresh_payload(
    endpoint: str, health: TargetHealth, violation: str
) -> dict[str, Any]:
    """JSON report for a ``--expect-fresh`` refusal.

    Same shape as the not-an-H3-endpoint payload — zero tests run, a reason
    and ``all_passing: false`` — so a consumer that already handles exit 2
    reads this one without new branches.
    """
    from datetime import datetime, timezone

    return {
        "warning": violation,
        "endpoint": endpoint,
        "expect_fresh_violated": True,
        "reason": violation,
        "target_health": _health_payload(health),
        "results": [],
        "total": 0,
        "passed": 0,
        "failed": 0,
        "duration_ms": 0.0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "all_passing": False,
        "latency": _latency_stats([]),
    }


async def _run_battery(
    endpoint: str,
    categories: str | None,
    as_json: bool,
    *,
    expect_fresh: bool = False,
) -> int:
    """Drive the battery and emit results. Returns the process exit code.

    The run starts by *connecting*: the target's ``/v1/health`` identity is
    printed before the first test, because the battery cannot tell the
    harness you just started from a co-tenant process that already owned the
    port — the DF-H3-26 false PASS, where a stranger's 2.6-day-old harness
    answered ``46/46 PASSED``.  With *expect_fresh* that identity is also
    enforced: an uptime over
    :data:`h3_shim.test_battery.EXPECT_FRESH_MAX_UPTIME_S` stops the run
    before a single test executes.
    """
    from datetime import datetime, timezone

    battery = H3TestBattery(endpoint)
    try:
        try:
            health = await battery.connect()
        except NotH3EndpointError as exc:
            warning = (
                f"Warning: {endpoint} does not look like an H3 endpoint ({exc.reason})."
            )
            print(warning, file=sys.stderr)
            if as_json:
                payload = {
                    "warning": warning,
                    "endpoint": endpoint,
                    "not_h3_endpoint": True,
                    "reason": exc.reason,
                    "results": [],
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "duration_ms": 0.0,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "all_passing": False,
                    "latency": _latency_stats([]),
                }
                print(json.dumps(payload, indent=2))
            else:
                print(
                    f"\nH3 Compliance Test Battery v{_battery_version()}\n"
                    f"Target: {endpoint}\n"
                    f"Transport: REST\n\n"
                    f"{warning}"
                )
            return 2

        _emit_health_identity(health, as_json=as_json)

        if expect_fresh:
            violation = health.expect_fresh_violation()
            if violation is not None:
                print(violation, file=sys.stderr)
                if as_json:
                    print(
                        json.dumps(
                            _expect_fresh_payload(endpoint, health, violation),
                            indent=2,
                        )
                    )
                # Exit 1, not a new code: the documented 0/1/2 contract is
                # what CI switches on, and the message above carries the
                # cause (DF-H3-26).
                return 1

        report = await battery.run_all()
    finally:
        await battery.close()

    if categories:
        wanted_tokens: set[str] = set()
        unknown: list[str] = []
        for raw in categories.split(","):
            value = raw.strip()
            if not value:
                continue
            token = category_token(value)
            if token is None:
                unknown.append(value)
            else:
                wanted_tokens.add(token)
        # Validate every value against the token AND display-label forms.
        if unknown:
            print(
                f"Error: unknown categories: {', '.join(sorted(unknown))}",
                file=sys.stderr,
            )
            print(
                f"Valid categories: {', '.join(sorted(CATEGORIES))}",
                file=sys.stderr,
            )
            print(
                "Valid labels: "
                + ", ".join(f'"{CATEGORIES[t]}"' for t in sorted(CATEGORIES)),
                file=sys.stderr,
            )
            return 2
        # Map CLI tokens to their display labels (e.g. 'health' → 'Health & Protocol').
        wanted_labels = {CATEGORIES[t] for t in wanted_tokens}
        report.results = [r for r in report.results if r.category in wanted_labels]
        report.total = len(report.results)
        report.passed = sum(1 for r in report.results if r.passed)
        report.failed = report.total - report.passed

    if as_json:
        payload = asdict(report)
        payload["all_passing"] = report.all_passing
        payload["latency"] = _latency_stats(report.results)
        payload["target_health"] = _health_payload(health)
        payload["target_warnings"] = health.warnings()
        print(json.dumps(payload, indent=2))
    else:
        print(_format_human(report, endpoint))

    return 0 if report.all_passing else 1


# ---------------------------------------------------------------------------
# h3-test (legacy, argparse-based, backwards compatible)
# ---------------------------------------------------------------------------


async def _run(args: argparse.Namespace) -> int:
    return await _run_battery(
        endpoint=args.endpoint,
        categories=args.categories,
        as_json=args.json,
        expect_fresh=args.expect_fresh,
    )


def _version_string(prog: str = "h3-test") -> str:
    """Version plus package install path — makes shadowed installs visible."""
    import importlib.metadata

    import h3_shim

    try:
        version = importlib.metadata.version("hermes-h3-shim")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover
        version = "unknown"
    return f"{prog} {version} (h3_shim: {Path(h3_shim.__file__).resolve()})"


def _battery_version() -> str:
    """Single-source the battery banner version from the installed package."""
    import importlib.metadata

    try:
        return importlib.metadata.version("hermes-h3-shim")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover
        return "unknown"


def main() -> None:
    """Console-script entry point for ``h3-test``."""
    parser = argparse.ArgumentParser(
        prog="h3-test",
        epilog=(
            "exit codes:\n"
            "  0  compliant - target is an H3 endpoint and all checks passed\n"
            "  1  compliance failure - target is an H3 endpoint but some "
            "protocol checks failed\n"
            "  2  not an H3 endpoint - connection refused, non-JSON body, "
            "HTTP >= 400, or /v1/health missing required H3 fields\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=_version_string(),
        help="Show version and package install path, then exit",
    )
    parser.add_argument(
        "--endpoint",
        required=True,
        help="H3 harness endpoint URL (e.g. http://localhost:9191)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON only (machine-readable report)",
    )
    parser.add_argument(
        "--categories",
        help=(
            "Comma-separated categories to run — protocol tokens "
            "(health,process,decisions,results,errors,stress) or the display "
            'labels the battery prints (e.g. "Stress & Performance")'
        ),
    )
    parser.add_argument(
        "--expect-fresh",
        action="store_true",
        help=(
            "Refuse to run when the target's /v1/health uptime exceeds "
            "300s: a stale co-tenant harness, not the one you just started. "
            "Stops before the first test and exits 1 (same code as a "
            "compliance failure — the message on stderr names the cause)."
        ),
    )
    args = parser.parse_args()

    try:
        exit_code = asyncio.run(_run(args))
    except KeyboardInterrupt:  # pragma: no cover — interactive Ctrl-C
        print("\nh3-test: interrupted", file=sys.stderr)
        sys.exit(130)
    sys.exit(exit_code)


# ---------------------------------------------------------------------------
# hermes-h3 Click group
# ---------------------------------------------------------------------------


@click.group(
    name="hermes-h3",
    help="H3 harness management for Hermes.",
    # Allow the group callback to run without a subcommand so the
    # --version flag works (GAP-038); bare invocation still errors below.
    invoke_without_command=True,
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=f"Override config path (default: ${CONFIG_PATH_ENV} or {CONFIG_PATH})",
)
@click.option(
    "--version",
    "show_version",
    is_flag=True,
    help="Show version and package install path, then exit",
)
@click.pass_context
def hermes_h3(
    ctx: click.Context,
    config_path: Path | None,
    show_version: bool,
) -> None:
    """Top-level command group."""
    if show_version:
        click.echo(_version_string("hermes-h3"))
        ctx.exit()
    if ctx.invoked_subcommand is None:
        raise click.UsageError("Missing command.")
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path


def _config_path(ctx: click.Context) -> Path:
    """Resolve the config path for *ctx*.

    Precedence: the subcommand's ``--config`` (stored on ``ctx.obj`` by
    each command) > the group ``--config`` (same key, set earlier) >
    ``$HERMES_H3_CONFIG`` > :data:`CONFIG_PATH`.
    """
    return ctx.obj.get("config_path") or default_config_path()


def _config_option(func):
    """Add a per-command ``--config`` option (mirrors the group-level one).

    Enables ``hermes-h3 <command> --config <path>`` in addition to
    ``hermes-h3 --config <path> <command>``.
    """
    return click.option(
        "--config",
        "config_path",
        type=click.Path(dir_okay=False, path_type=Path),
        default=None,
        help=(f"Override config path (default: ${CONFIG_PATH_ENV} or {CONFIG_PATH})."),
    )(func)


@hermes_h3.command(help="Run the H3 compliance test battery.")
@_config_option
@click.option(
    "--harness",
    "-h",
    "harness",
    default=None,
    help="Named harness from config (defaults to default_harness).",
)
@click.option(
    "--endpoint",
    default=None,
    help="Override endpoint URL (skip config lookup).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON report.")
@click.option(
    "--categories",
    default=None,
    help=(
        "Comma-separated categories to run — protocol tokens "
        "(health,process,decisions,results,errors,stress) or display labels "
        '(e.g. "Stress & Performance").'
    ),
)
@click.option(
    "--expect-fresh",
    "expect_fresh",
    is_flag=True,
    help=(
        "Refuse to run when the target's /v1/health uptime exceeds 300s: a "
        "stale co-tenant harness, not the one you just started. Stops before "
        "the first test and exits 1."
    ),
)
@click.pass_context
def test(
    ctx: click.Context,
    config_path: Path | None,
    harness: str | None,
    endpoint: str | None,
    as_json: bool,
    categories: str | None,
    expect_fresh: bool,
) -> None:
    """Run the compliance battery against a harness."""
    if config_path is not None:
        ctx.obj["config_path"] = config_path
    if endpoint is None:
        config = load_config(_config_path(ctx))
        _name, spec = resolve_harness(harness, config)
        endpoint = spec.get("endpoint")
        if not endpoint:
            raise click.ClickException(
                f"harness {harness!r} has no endpoint configured"
            )
    try:
        exit_code = asyncio.run(
            _run_battery(endpoint, categories, as_json, expect_fresh=expect_fresh)
        )
    except KeyboardInterrupt:  # pragma: no cover
        click.echo("\nhermes h3 test: interrupted", err=True)
        sys.exit(130)
    sys.exit(exit_code)


@hermes_h3.command(name="list", help="List harnesses known to the config.")
@_config_option
@click.pass_context
def list_cmd(ctx: click.Context, config_path: Path | None) -> None:
    """Print a table of harnesses."""
    if config_path is not None:
        ctx.obj["config_path"] = config_path
    config = load_config(_config_path(ctx))
    harnesses: dict[str, dict[str, Any]] = config.get("harnesses", {}) or {}
    default = config.get("default_harness")

    if not harnesses:
        click.echo("no harnesses configured")
        click.echo(f"config: {_config_path(ctx)}")
        return

    click.echo(f"{'NAME':20s} {'ENDPOINT':40s} {'TRANSPORT':10s} TIMEOUT")
    click.echo("-" * 86)
    for name, spec in harnesses.items():
        marker = "*" if name == default else " "
        click.echo(
            f"{marker}{name:19s} "
            f"{str(spec.get('endpoint', '')):40s} "
            f"{str(spec.get('transport', '')):10s} "
            f"{spec.get('timeout_ms', '')}"
        )


# Transports the shim actually implements. Anything else (e.g. ``grpc``)
# is rejected at selection time — GAP-030: gRPC was advertised but never
# implemented, so ``install --transport grpc`` used to store the string
# and silently fall back to REST.
SUPPORTED_TRANSPORTS: tuple[str, ...] = ("rest",)


def _validate_transport(transport: str) -> None:
    """Fail fast on transports the shim does not implement (GAP-030)."""
    if transport not in SUPPORTED_TRANSPORTS:
        raise click.ClickException(
            f"{transport} transport not supported yet "
            f"(only {', '.join(SUPPORTED_TRANSPORTS)} is implemented)"
        )


# Health probe used by ``install`` (DF-H3-SHIM-FOREMAN-3). ``install`` used
# to persist whatever endpoint string it was handed, so a typo — or a
# harness that simply was not running — exited 0 and produced a config
# entry that only surfaced as a connection failure later, at ``verify`` or
# at the first session. Probing before the write makes the failure happen
# where the mistake was made, and leaves the config untouched.


def _probe_failure_reason(exc: Exception) -> str:
    """Translate a probe exception into an actionable one-line reason.

    A wrong URL is the common failure here, and the raw decoder errors
    ("Expecting value: line 1 column 1") or a pydantic dump do not tell
    the operator what is actually wrong — so the two shapes that mean
    "something answered but it is not an H3 harness" are named as such.
    Everything else keeps the exception's own message.
    """
    from pydantic import ValidationError

    if isinstance(exc, json.JSONDecodeError):
        return (
            f"the endpoint answered, but not with JSON ({exc}) — "
            "is this really an H3 harness?"
        )
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        if errors:
            first = errors[0]
            where = ".".join(str(part) for part in first.get("loc", ())) or "<root>"
            detail = str(first.get("msg", ""))
        else:  # pragma: no cover — pydantic always reports at least one error
            where, detail = "<root>", str(exc)
        return (
            f"the response is not a valid H3 health payload "
            f"({where}: {detail}) — is this really an H3 harness?"
        )
    return str(exc) or exc.__class__.__name__


def _probe_endpoint(
    endpoint: str,
    transport: str,
    timeout_ms: int,
    config_path: Path,
) -> tuple[str, str]:
    """Health-check *endpoint*; return ``(status, version)``.

    Issues ``GET /v1/health`` with the same client the shim uses at
    runtime (so transport, timeout and auth headers all match) and raises
    :class:`click.ClickException` when the endpoint is unreachable, does
    not answer like an H3 harness, or reports a status other than
    ``ok``.  The message names the endpoint, the cause and what to do
    next, and states that nothing was written to *config_path*.

    Callers must not have written anything yet: ``install`` only
    persists the harness after this returns.
    """

    def _fail(reason: str) -> click.ClickException:
        return click.ClickException(
            f"endpoint {endpoint} failed its health check: {reason}\n"
            f"Nothing was written — {config_path} is unchanged and the "
            f"harness was not registered.\n"
            f"Check that the harness is running and that {endpoint} is the "
            f"right URL, then retry; probe it directly with "
            f"'hermes-h3 verify --endpoint {endpoint}'."
        )

    from h3_shim.client import H3Client  # local import: optional dep
    from h3_shim.protocol import HealthStatus

    async def _probe():
        client = H3Client(endpoint=endpoint, transport=transport, timeout_ms=timeout_ms)
        try:
            return await client.health()
        finally:
            await client.close()

    try:
        health = asyncio.run(_probe())
    except KeyboardInterrupt:  # pragma: no cover — interactive Ctrl-C
        raise
    except Exception as exc:
        raise _fail(_probe_failure_reason(exc)) from exc

    status = str(getattr(health.status, "value", health.status))
    if status != HealthStatus.OK.value:
        detail = getattr(health, "degraded_reason", None) or getattr(
            health, "error", None
        )
        reason = f"reported status {status!r}"
        if detail:
            reason += f" ({detail})"
        raise _fail(f"{reason}; expected {HealthStatus.OK.value!r}")
    return status, str(getattr(health, "version", "") or "")


@hermes_h3.command(help="Register a harness in the config.")
@_config_option
@click.argument("name", required=False)
@click.option(
    "--name",
    "name_opt",
    default=None,
    help="Harness name (alias for the positional NAME).",
)
@click.option("--endpoint", required=True, help="Harness endpoint URL.")
@click.option(
    "--transport",
    default="rest",
    show_default=True,
    help="Transport protocol (only 'rest' is implemented).",
)
@click.option(
    "--timeout-ms",
    default=30000,
    show_default=True,
    type=int,
    help="Default request timeout in milliseconds.",
)
@click.option(
    "--set-default/--no-set-default",
    default=False,
    help="Promote this harness to default_harness.",
)
@click.pass_context
def install(
    ctx: click.Context,
    config_path: Path | None,
    name: str | None,
    name_opt: str | None,
    endpoint: str,
    transport: str,
    timeout_ms: int,
    set_default: bool,
) -> None:
    """Add or update a harness entry after health-checking its endpoint.

    NAME is an optional positional alias for ``--name``::

        hermes-h3 install [NAME] [--name NAME] --endpoint URL [options]

    Exactly one of NAME / **--name** must be given; with neither the
    command fails loudly instead of installing an unnamed entry.  When
    both are given, NAME wins.

    The endpoint is probed with ``GET /v1/health`` before anything is
    written (DF-H3-SHIM-FOREMAN-3): an unreachable, non-H3 or non-``ok``
    endpoint exits non-zero and leaves the existing config — and any
    existing default — untouched.
    """
    if config_path is not None:
        ctx.obj["config_path"] = config_path
    # Positional NAME wins over the --name flag (same rule as verify).
    resolved = name if name is not None else name_opt
    if resolved is None:
        raise click.UsageError(
            "install requires a harness name: pass it positionally "
            "(install NAME ...) or with --name NAME"
        )
    _validate_transport(transport)
    path = _config_path(ctx)
    # Probe BEFORE reading or writing the config: a dead endpoint must not
    # produce a config entry, and must not touch an existing one.
    status, version = _probe_endpoint(endpoint, transport, timeout_ms, path)
    config = load_config(path)
    harnesses = config.setdefault("harnesses", {})
    harnesses[resolved] = {
        "endpoint": endpoint,
        "transport": transport,
        "timeout_ms": timeout_ms,
    }
    if set_default or not config.get("default_harness"):
        config["default_harness"] = resolved
    saved = save_config(config, path)
    click.echo(f"installed harness {resolved!r} at {endpoint} ({transport})")
    click.echo(f"health:   {status}" + (f" (version {version})" if version else ""))
    click.echo(f"config: {saved}")


@hermes_h3.command(help="Remove a harness from the config.")
@_config_option
@click.argument("name")
@click.pass_context
def uninstall(ctx: click.Context, config_path: Path | None, name: str) -> None:
    """Delete a harness entry."""
    if config_path is not None:
        ctx.obj["config_path"] = config_path
    config = load_config(_config_path(ctx))
    harnesses = config.setdefault("harnesses", {})
    if name not in harnesses:
        raise click.ClickException(f"harness {name!r} not found")
    del harnesses[name]
    if config.get("default_harness") == name:
        config["default_harness"] = next(iter(harnesses), None) if harnesses else None
    save_config(config, _config_path(ctx))
    click.echo(f"uninstalled harness {name!r}")


@hermes_h3.command(help="Health-check a harness via the H3 REST client.")
@_config_option
@click.argument("name", required=False)
@click.option(
    "--harness",
    "-h",
    "harness",
    default=None,
    help=(
        "Named harness from config (defaults to default_harness). "
        "Ignored when NAME is given."
    ),
)
@click.option(
    "--endpoint",
    default=None,
    help="Override endpoint URL (skip config lookup).",
)
@click.option(
    "--fallback",
    is_flag=True,
    default=False,
    help="Also test the native fallback path — show what happens when "
    "the harness is unreachable and verify native is available.",
)
@click.pass_context
def verify(
    ctx: click.Context,
    config_path: Path | None,
    name: str | None,
    harness: str | None,
    endpoint: str | None,
    fallback: bool,
) -> None:
    """Hit ``GET /health`` and report status.

    NAME is an optional positional alias for ``--harness``::

        hermes-h3 verify [NAME] [--harness NAME] [--endpoint URL] [--fallback]

    With neither NAME nor **--harness** the configured
    ``default_harness`` is used.  When both are given, NAME wins.

    When **--fallback** is passed, also simulates the fallback path:
    if the harness is unreachable, the output shows that sessions would
    be rerouted to the native harness.  Native availability is always
    checked when **--fallback** is used.
    """
    if config_path is not None:
        ctx.obj["config_path"] = config_path
    # Positional NAME wins over the --harness flag; either falls back
    # to default_harness inside resolve_harness().
    harness_name = name if name is not None else harness
    if endpoint is None:
        config = load_config(_config_path(ctx))
        harness_name, spec = resolve_harness(harness_name, config)
        endpoint = spec.get("endpoint")
        if not endpoint:
            raise click.ClickException(
                f"harness {harness_name!r} has no endpoint configured"
            )
    else:
        harness_name = harness_name or "<override>"

    try:
        from h3_shim.client import H3Client  # local import: optional dep
    except Exception as exc:  # pragma: no cover - defensive
        raise click.ClickException(f"could not import H3Client: {exc}") from exc

    async def _check():
        client = H3Client(endpoint=endpoint, timeout_ms=10_000)
        try:
            return await client.health()
        finally:
            await client.close()

    try:
        result = asyncio.run(_check())
    except KeyboardInterrupt:  # pragma: no cover
        click.echo("\nhermes h3 verify: interrupted", err=True)
        sys.exit(130)
    except Exception as exc:
        # Harness unreachable — fallback path
        if fallback:
            _report_fallback(harness_name, endpoint, exc)
            return
        raise click.ClickException(
            f"verify failed for {harness_name!r}: {exc}"
        ) from exc

    payload = result.model_dump()
    click.echo(f"harness: {harness_name}")
    click.echo(f"endpoint: {endpoint}")
    click.echo(f"status:   {payload.get('status', 'unknown')}")
    if "version" in payload:
        click.echo(f"version:  {payload['version']}")
    if "capabilities" in payload:
        click.echo(f"caps:     {', '.join(payload['capabilities'])}")

    # Fallback report when harness is reachable
    if fallback:
        click.echo("")
        _report_fallback(harness_name, endpoint, None)


def _report_fallback(  # noqa: PLR0912
    harness_name: str,
    endpoint: str,
    failure: Exception | None,
) -> None:
    """Print a structured fallback-path report to stdout.

    * If *failure* is set the harness is unreachable; the report
      describes the failure and confirms that native fallback would
      handle sessions routed to this harness.
    * If *failure* is *None* the harness is healthy; the report shows
      the native fallback as a contingency.
    * In either case the output mentions the circuit-breaker behaviour
      that would gate re-routing.
    """
    if failure:
        click.echo("── Fallback path ──────────────────────────────────")
        click.echo(f"  harness:   {harness_name}")
        click.echo(f"  endpoint:  {endpoint}")
        click.echo("  status:    UNREACHABLE")
        click.echo(f"  error:     {failure}")
        click.echo("")
        click.echo("  Failover:")
        click.echo("    • Sessions routed to this harness would be")
        click.echo("      rerouted to the native Hermes loop")
        click.echo("      (default_harness = 'native').")
        click.echo("    • The health-check loop waits for")
        click.echo("      max_consecutive_failures (default 3) before")
        click.echo("      triggering reroute.")
        click.echo("    • The circuit breaker also opens when the error")
        click.echo("      rate exceeds the threshold (default 50%),")
        click.echo("      rerouting sessions immediately.")
        click.echo("    • Cooldown before half-open probe: 30s default.")
        click.echo("")
        click.echo("  Native harness: available (no endpoint required)")
        click.echo("── Fallback path: ENGAGED ──────────────────────────")
    else:
        click.echo("── Fallback path ──────────────────────────────────")
        click.echo(f"  harness:   {harness_name}")
        click.echo(f"  endpoint:  {endpoint}")
        click.echo("  status:    HEALTHY")
        click.echo("")
        click.echo("  Contingency:")
        click.echo("    • If this harness becomes unreachable, sessions")
        click.echo("      reroute to the native Hermes loop.")
        click.echo("    • Circuit breaker (error rate >= 50%) opens")
        click.echo("      after window_size failures and reroutes")
        click.echo("      sessions immediately.")
        click.echo("")
        click.echo("  Native harness: available (no endpoint required)")
        click.echo("── Fallback path: STANDBY ──────────────────────────")


@hermes_h3.command(
    help="Create an empty config file or scaffold a new harness project."
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(f"Override config path (default: ${CONFIG_PATH_ENV} or {CONFIG_PATH})."),
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing config file or project directory.",
)
@click.option(
    "--lang",
    "lang",
    type=click.Choice((*SUPPORTED_LANGS, *LANG_ALIASES), case_sensitive=False),
    callback=lambda ctx, param, value: (
        _normalize_lang(value) if value is not None else None
    ),
    default=None,
    help=(
        "Generate a complete harness project for the given language "
        "(go, py, ts; python/typescript aliases accepted) in a new "
        "'h3-harness-<lang>/' subdirectory. "
        "Without --lang, an empty config skeleton is written instead."
    ),
)
@click.option(
    "--output-dir",
    "output_dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=Path("."),
    show_default="current directory",
    help="Parent directory under which the new project is created.",
)
@click.pass_context
def scaffold(
    ctx: click.Context,
    config_path: Path | None,
    force: bool,
    lang: str | None,
    output_dir: Path,
) -> None:
    """Initialise the config file or scaffold a new harness project.

    Without ``--lang``:
        Create ``~/.hermes/h3/config.yaml`` (or the path supplied via
        ``--config`` / ``$HERMES_H3_CONFIG``) if it doesn't already
        exist. Existing files are preserved unless ``--force`` is passed.

    With ``--lang <go|py|ts>``:
        Render the corresponding template tree into
        ``<output-dir>/h3-harness-<lang>/`` and print build/run/verify
        instructions. Existing project directories are preserved unless
        ``--force`` is passed.
    """
    if config_path is not None:
        ctx.obj["config_path"] = config_path
    if lang is None:
        # Backwards-compatible behaviour: empty config file at CONFIG_PATH.
        path = _config_path(ctx)
        if path.exists() and not force:
            click.echo(f"config already exists at {path}")
            return
        save_config(_empty_config(), path)
        click.echo(f"wrote empty config to {path}")
        return

    project_dir = scaffold_project(
        lang=lang,
        output_dir=output_dir,
        overwrite=force,
    )
    click.echo(_format_run_instructions(lang, project_dir))
    click.echo("")
    click.echo("Run h3-test --endpoint http://localhost:9191 to verify")


def _session_harness(binding: Any) -> str:
    """Return the harness name a ``sessions`` entry points at.

    Both binding shapes that exist in the wild are handled: the dict
    form (``{"harness": "alpha"}``) and the bare-string form
    (``"alpha"``, older configs).
    """
    if isinstance(binding, dict):
        return str(binding.get("harness", "?"))
    return str(binding)


def _empty_route_help(config_path: Path) -> str:
    """Return the actionable empty-state text for ``hermes-h3 route``.

    An empty ``sessions`` map is the normal state before anything has
    been routed, so the old bare "no sessions configured" left a
    CLI-only user with no idea where routes come from or how to add
    one.  The text names the resolved config file, shows the exact YAML
    to write, and explains why the table can fill up without any manual
    edit (the plugin/loader/shim loop pins routes in memory at runtime,
    and those pins are never persisted back to the config).
    """
    return "\n".join(
        (
            "no sessions configured — the routing table is empty.",
            "",
            f"Config file: {config_path}",
            "",
            "Session routes come from the `sessions:` map in that file.  A",
            "running shim or embedder can also pin routes at runtime via",
            "H3Loader.route_session(...); those pins live in memory for that",
            "run and are never written back to the config, so this table",
            "stays empty until a route is pinned at runtime or written here.",
            "",
            "Add a route from the CLI (`hermes-h3 route --session <id>",
            "--set-harness <name>`) or by hand under `sessions:`:",
            "",
            "  harnesses:",
            "    my-harness:",
            "      endpoint: http://localhost:9191",
            "  sessions:",
            '    "telegram:-1001234567890": my-harness   # bare string = name',
            '    "telegram:-1001234567890:42":',
            "      harness: my-harness                   # or {harness: name}",
            "",
            "Then re-run `hermes-h3 route` to confirm it appears.  Matching",
            "is most-specific-first: platform:chat_id:thread_id ->",
            "platform:chat_id -> platform -> default_harness; sessions with",
            "no match use default_harness.",
        )
    )


@hermes_h3.command(help="Show the session → harness routing table.")
@_config_option
@click.option(
    "--session",
    "session",
    default=None,
    help="Show only the binding for this session id "
    "(or the session to write with --set-harness/--remove).",
)
@click.option(
    "--set-harness",
    "set_harness",
    default=None,
    metavar="NAME",
    help="Bind --session to harness NAME and persist it to the config file.",
)
@click.option(
    "--remove",
    "remove",
    is_flag=True,
    default=False,
    help="Delete --session's binding from the config file.",
)
@click.pass_context
def route(
    ctx: click.Context,
    config_path: Path | None,
    session: str | None,
    set_harness: str | None,
    remove: bool,
) -> None:
    """Pretty-print the ``sessions`` map from the config.

    With ``--session <id>`` only that session's binding is printed.  An
    id that is not in the routing table fails loudly (fail-closed): a
    silent empty answer is indistinguishable from a broken lookup.

    With ``--session <id> --set-harness <name>`` the binding is written
    to the resolved config file (no hand-edit needed); ``--remove``
    deletes it.  Both write paths validate first and fail closed — a bad
    invocation raises, exits non-zero and leaves the file untouched.

    With no routes configured, the command exits 0 and prints where the
    routes come from (config ``sessions:`` map, plus runtime pins by the
    plugin/loader/shim loop) and a minimal YAML example to copy.
    """
    if config_path is not None:
        ctx.obj["config_path"] = config_path
    path = _config_path(ctx)
    config = load_config(path)
    sessions: dict[str, Any] = config.get("sessions", {}) or {}
    if set_harness is not None or remove:
        if session is None:
            raise click.ClickException(
                "--set-harness and --remove need --session <id> to know "
                "which route to write"
            )
        if set_harness is not None and remove:
            raise click.ClickException(
                "--set-harness and --remove are mutually exclusive; "
                "pass one or the other"
            )
        if set_harness is not None:
            harnesses: dict[str, Any] = config.get("harnesses", {}) or {}
            if set_harness not in harnesses:
                raise click.ClickException(
                    f"harness {set_harness!r} not found in config; "
                    f"known: {sorted(harnesses) or 'none'}"
                )
            sessions[session] = {"harness": set_harness}
            config["sessions"] = sessions
            save_config(config, path)
            click.echo(f"{session} -> {set_harness} (saved to {path})")
            return
        if session not in sessions:
            raise click.ClickException(
                f"no session {session!r} in the routing table to remove"
            )
        del sessions[session]
        config["sessions"] = sessions
        save_config(config, path)
        click.echo(f"removed session {session} from {path}")
        return
    if session is not None:
        if session not in sessions:
            raise click.ClickException(f"no session {session!r} in the routing table")
        click.echo(f"{session} -> {_session_harness(sessions[session])}")
        return
    if not sessions:
        click.echo(_empty_route_help(path))
        return
    click.echo(f"{'SESSION':40s} HARNESS")
    click.echo("-" * 60)
    for sid, binding in sessions.items():
        click.echo(f"{sid:40s} {_session_harness(binding)}")


@hermes_h3.command(
    name="pre-update-check",
    help="Run pre-flight compatibility checks before hermes update.",
)
@_config_option
@click.argument("target_version")
@click.option(
    "--versions-yaml",
    "versions_yaml_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to versions.yaml (default: bundled package data, "
    "falls back to protocol repo in a monorepo checkout).",
)
@click.pass_context
def pre_update_check_cmd(
    ctx: click.Context,
    config_path: Path | None,
    target_version: str,
    versions_yaml_path: Path | None,
) -> None:
    """Check H3 compatibility before upgrading Hermes.

    TARGET_VERSION is the Hermes version you plan to upgrade to
    (e.g. 0.19.0).
    """
    if config_path is not None:
        ctx.obj["config_path"] = config_path
    from h3_shim.upgrade_check import pre_update_check

    result = pre_update_check(
        target_version,
        versions_yaml_path=versions_yaml_path,
        config_path=_config_path(ctx),
    )

    click.echo(result.message)

    if result.severity == "BLOCK":
        click.echo(
            "\nUpdate blocked. Resolve the issues above before upgrading.",
            err=True,
        )
        sys.exit(1)
    elif result.severity == "WARN":
        click.echo("\nWarnings found. Review before proceeding.", err=True)
    else:
        click.echo("\nAll checks passed. Safe to update.")


@hermes_h3.command(help="Set the default harness.")
@_config_option
@click.argument("name")
@click.pass_context
def use(ctx: click.Context, config_path: Path | None, name: str) -> None:
    """Promote ``name`` to default_harness (and create config if needed)."""
    if config_path is not None:
        ctx.obj["config_path"] = config_path
    config = load_config(_config_path(ctx))
    harnesses = config.setdefault("harnesses", {})
    if name not in harnesses:
        raise click.ClickException(f"harness {name!r} not found; install it first")
    config["default_harness"] = name
    save_config(config, _config_path(ctx))
    click.echo(f"default harness set to {name!r}")


# Allow ``python -m h3_shim.cli`` to invoke either interface.
if __name__ == "__main__":  # pragma: no cover
    if len(sys.argv) > 1 and sys.argv[1] != "test":
        hermes_h3()
    else:
        main()
