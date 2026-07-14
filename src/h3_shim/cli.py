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
import sys
from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path
from typing import Any

import click
import yaml

from h3_shim.test_battery import H3TestBattery, TestReport

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

CONFIG_PATH = Path.home() / ".hermes" / "h3" / "config.yaml"


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
# Reporting (shared by ``h3-test`` and ``hermes-h3 test``)
# ---------------------------------------------------------------------------


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
    lines.append(
        f"  {'TOTAL':35s} {report.passed}/{report.total}  {totals}"
    )
    lines.append(
        f"  {'Duration':35s} {report.duration_ms / 1000.0:.2f}s"
    )
    return "\n".join(lines)


async def _run_battery(
    endpoint: str,
    categories: str | None,
    as_json: bool,
) -> int:
    """Drive the battery and emit results. Returns the process exit code."""
    battery = H3TestBattery(endpoint)
    try:
        report = await battery.run_all()
    finally:
        await battery.close()

    if categories:
        wanted = {c.strip() for c in categories.split(",") if c.strip()}
        report.results = [r for r in report.results if r.category in wanted]
        report.total = len(report.results)
        report.passed = sum(1 for r in report.results if r.passed)
        report.failed = report.total - report.passed

    if as_json:
        payload = asdict(report)
        payload["all_passing"] = report.all_passing
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
    parser = argparse.ArgumentParser(prog="h3-test")
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


@hermes_h3.command(help="Run the H3 compliance test battery.")
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
    harness: str | None,
    endpoint: str | None,
    as_json: bool,
    categories: str | None,
) -> None:
    """Run the compliance battery against a harness."""
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
            _run_battery(endpoint, categories, as_json)
        )
    except KeyboardInterrupt:  # pragma: no cover
        click.echo("\nhermes h3 test: interrupted", err=True)
        sys.exit(130)
    sys.exit(exit_code)


@hermes_h3.command(name="list", help="List harnesses known to the config.")
@click.pass_context
def list_cmd(ctx: click.Context) -> None:
    """Print a table of harnesses."""
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
    name: str,
    endpoint: str,
    transport: str,
    timeout_ms: int,
    set_default: bool,
) -> None:
    """Add or update a harness entry."""
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
@click.argument("name")
@click.pass_context
def uninstall(ctx: click.Context, name: str) -> None:
    """Delete a harness entry."""
    config = load_config(_config_path(ctx))
    harnesses = config.setdefault("harnesses", {})
    if name not in harnesses:
        raise click.ClickException(f"harness {name!r} not found")
    del harnesses[name]
    if config.get("default_harness") == name:
        config["default_harness"] = (
            next(iter(harnesses), None) if harnesses else None
        )
    save_config(config, _config_path(ctx))
    click.echo(f"uninstalled harness {name!r}")


@hermes_h3.command(help="Health-check a harness via the H3 REST client.")
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
@click.pass_context
def verify(ctx: click.Context, harness: str | None, endpoint: str | None) -> None:
    """Hit ``GET /health`` and report status."""
    if endpoint is None:
        config = load_config(_config_path(ctx))
        name, spec = resolve_harness(harness, config)
        endpoint = spec.get("endpoint")
        if not endpoint:
            raise click.ClickException(
                f"harness {name!r} has no endpoint configured"
            )
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
        raise click.ClickException(f"verify failed for {name!r}: {exc}") from exc

    payload = result.model_dump()
    click.echo(f"harness: {name}")
    click.echo(f"endpoint: {endpoint}")
    click.echo(f"status:   {payload.get('status', 'unknown')}")
    if "version" in payload:
        click.echo(f"version:  {payload['version']}")
    if "capabilities" in payload:
        click.echo(f"caps:     {', '.join(payload['capabilities'])}")


@hermes_h3.command(help="Create an empty config file if it doesn't exist.")
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing config with an empty skeleton.",
)
@click.pass_context
def scaffold(ctx: click.Context, force: bool) -> None:
    """Initialise the config file at ``~/.hermes/h3/config.yaml``."""
    path = _config_path(ctx)
    if path.exists() and not force:
        click.echo(f"config already exists at {path}")
        return
    save_config(_empty_config(), path)
    click.echo(f"wrote empty config to {path}")


@hermes_h3.command(help="Show the session → harness routing table.")
@click.pass_context
def route(ctx: click.Context) -> None:
    """Pretty-print the ``sessions`` map from the config."""
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


@hermes_h3.command(help="Set the default harness.")
@click.argument("name")
@click.pass_context
def use(ctx: click.Context, name: str) -> None:
    """Promote ``name`` to default_harness (and create config if needed)."""
    config = load_config(_config_path(ctx))
    harnesses = config.setdefault("harnesses", {})
    if name not in harnesses:
        raise click.ClickException(
            f"harness {name!r} not found; install it first"
        )
    config["default_harness"] = name
    save_config(config, _config_path(ctx))
    click.echo(f"default harness set to {name!r}")


# Allow ``python -m h3_shim.cli`` to invoke either interface.
if __name__ == "__main__":  # pragma: no cover
    if len(sys.argv) > 1 and sys.argv[1] != "test":
        hermes_h3()
    else:
        main()
