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
    TestReport,
    TestResult,
)

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

CONFIG_PATH = Path.home() / ".hermes" / "h3" / "config.yaml"

# Templates directory shipped with the package. Each language gets its
# own subdirectory under ``templates/<lang>/``.
TEMPLATES_DIR = Path(__file__).parent / "templates"

# Languages supported by ``hermes-h3 scaffold --lang``.
SUPPORTED_LANGS = ("go", "py", "ts")


def _empty_config() -> dict[str, Any]:
    """Return a fresh empty config skeleton."""
    return {
        "default_harness": None,
        "harnesses": {},
        "sessions": {},
    }


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Read config from disk; return an empty skeleton if absent."""
    p = path or CONFIG_PATH
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
    p = path or CONFIG_PATH
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
        "H3 Compliance Test Battery v1.0.0",
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


async def _run_battery(
    endpoint: str,
    categories: str | None,
    as_json: bool,
) -> int:
    """Drive the battery and emit results. Returns the process exit code."""
    from datetime import datetime, timezone

    battery = H3TestBattery(endpoint)
    try:
        report = await battery.run_all()
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
                f"\nH3 Compliance Test Battery v1.0.0\n"
                f"Target: {endpoint}\n"
                f"Transport: REST\n\n"
                f"{warning}"
            )
        return 2
    finally:
        await battery.close()

    if categories:
        wanted_tokens = {c.strip() for c in categories.split(",") if c.strip()}
        # Validate all tokens are known.
        unknown = wanted_tokens - set(CATEGORIES)
        if unknown:
            print(
                f"Error: unknown categories: {', '.join(sorted(unknown))}",
                file=sys.stderr,
            )
            print(
                f"Valid categories: {', '.join(sorted(CATEGORIES))}",
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
    )


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
            "Comma-separated categories to run "
            "(health,process,decisions,results,errors,stress)"
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


@click.group(name="hermes-h3", help="H3 harness management for Hermes.")
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=f"Override config path (default: {CONFIG_PATH})",
)
@click.pass_context
def hermes_h3(ctx: click.Context, config_path: Path | None) -> None:
    """Top-level command group."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path


def _config_path(ctx: click.Context) -> Path:
    return ctx.obj.get("config_path") or CONFIG_PATH


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
        help=f"Override config path (default: {CONFIG_PATH}).",
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
    help="Comma-separated categories to run.",
)
@click.pass_context
def test(
    ctx: click.Context,
    config_path: Path | None,
    harness: str | None,
    endpoint: str | None,
    as_json: bool,
    categories: str | None,
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
        exit_code = asyncio.run(_run_battery(endpoint, categories, as_json))
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


@hermes_h3.command(help="Register a harness in the config.")
@_config_option
@click.argument("name")
@click.option("--endpoint", required=True, help="Harness endpoint URL.")
@click.option(
    "--transport",
    default="rest",
    show_default=True,
    help="Transport protocol (rest, grpc, ...).",
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
    name: str,
    endpoint: str,
    transport: str,
    timeout_ms: int,
    set_default: bool,
) -> None:
    """Add or update a harness entry."""
    if config_path is not None:
        ctx.obj["config_path"] = config_path
    config = load_config(_config_path(ctx))
    harnesses = config.setdefault("harnesses", {})
    harnesses[name] = {
        "endpoint": endpoint,
        "transport": transport,
        "timeout_ms": timeout_ms,
    }
    if set_default or not config.get("default_harness"):
        config["default_harness"] = name
    path = save_config(config, _config_path(ctx))
    click.echo(f"installed harness {name!r} at {endpoint} ({transport})")
    click.echo(f"config: {path}")


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
    harness: str | None,
    endpoint: str | None,
    fallback: bool,
) -> None:
    """Hit ``GET /health`` and report status.

    When **--fallback** is passed, also simulates the fallback path:
    if the harness is unreachable, the output shows that sessions would
    be rerouted to the native harness.  Native availability is always
    checked when **--fallback** is used.
    """
    if config_path is not None:
        ctx.obj["config_path"] = config_path
    if endpoint is None:
        config = load_config(_config_path(ctx))
        name, spec = resolve_harness(harness, config)
        endpoint = spec.get("endpoint")
        if not endpoint:
            raise click.ClickException(f"harness {name!r} has no endpoint configured")
    else:
        name = harness or "<override>"

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
            _report_fallback(name, endpoint, exc)
            return
        raise click.ClickException(f"verify failed for {name!r}: {exc}") from exc

    payload = result.model_dump()
    click.echo(f"harness: {name}")
    click.echo(f"endpoint: {endpoint}")
    click.echo(f"status:   {payload.get('status', 'unknown')}")
    if "version" in payload:
        click.echo(f"version:  {payload['version']}")
    if "capabilities" in payload:
        click.echo(f"caps:     {', '.join(payload['capabilities'])}")

    # Fallback report when harness is reachable
    if fallback:
        click.echo("")
        _report_fallback(name, endpoint, None)


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
    help=f"Override config path (default: {CONFIG_PATH}).",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing config file or project directory.",
)
@click.option(
    "--lang",
    "lang",
    type=click.Choice(SUPPORTED_LANGS, case_sensitive=False),
    default=None,
    help=(
        "Generate a complete harness project for the given language "
        "(go, py, ts) in a new 'h3-harness-<lang>/' subdirectory. "
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
        ``--config``) if it doesn't already exist. Existing files are
        preserved unless ``--force`` is passed.

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


@hermes_h3.command(help="Show the session → harness routing table.")
@_config_option
@click.pass_context
def route(ctx: click.Context, config_path: Path | None) -> None:
    """Pretty-print the ``sessions`` map from the config."""
    if config_path is not None:
        ctx.obj["config_path"] = config_path
    config = load_config(_config_path(ctx))
    sessions: dict[str, Any] = config.get("sessions", {}) or {}
    if not sessions:
        click.echo("no sessions configured")
        return
    click.echo(f"{'SESSION':40s} HARNESS")
    click.echo("-" * 60)
    for sid, binding in sessions.items():
        if isinstance(binding, dict):
            target = binding.get("harness", "?")
        else:
            target = str(binding)
        click.echo(f"{sid:40s} {target}")


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
