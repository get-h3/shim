"""Command-line entry point for the H3 compliance test battery.

Registered as the ``h3-test`` console script in ``pyproject.toml``.

Usage::

    h3-test --endpoint http://localhost:9191
    h3-test --endpoint http://localhost:9191 --json
    h3-test --endpoint http://localhost:9191 --categories health,process

Exit code is ``0`` only when every test passes.
"""

import argparse
import asyncio
import json
import sys
from collections import OrderedDict
from dataclasses import asdict

from h3_shim.test_battery import H3TestBattery, TestReport


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


async def _run(args: argparse.Namespace) -> int:
    """Drive the battery; honour ``--categories`` filter if provided."""
    battery = H3TestBattery(args.endpoint)
    try:
        report = await battery.run_all()
    finally:
        await battery.close()

    if args.categories:
        wanted = {c.strip() for c in args.categories.split(",") if c.strip()}
        report.results = [r for r in report.results if r.category in wanted]
        report.total = len(report.results)
        report.passed = sum(1 for r in report.results if r.passed)
        report.failed = report.total - report.passed

    if args.json:
        payload = asdict(report)
        payload["all_passing"] = report.all_passing
        print(json.dumps(payload, indent=2))
    else:
        print(_format_human(report, args.endpoint))

    return 0 if report.all_passing else 1


def main() -> None:
    """Console-script entry point."""
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


if __name__ == "__main__":  # pragma: no cover
    main()
