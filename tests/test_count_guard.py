"""Guard tests for scripts/check-test-count.sh (H3-GAP-079).

The shim's compliance-test count is declared in exactly two machine-readable
places — ``scripts/test-count.txt`` (the canonical count) and
``EXPECTED_TEST_COUNT`` in ``src/h3_shim/test_battery.py`` — and quoted in prose
everywhere else. Nothing in this repo policed that prose, so the stale-count
class re-offended six-plus times. These tests drive the guard's outcomes
hermetically through its documented env overrides, so a plain ``pytest`` run
catches prose drift before CI does:

* exit 0 — canonical count, battery and current-state prose all agree;
* exit 1 — drift (the battery disagrees, a tracked current-state surface still
  quotes a retired count, or a dated report quotes one without a banner);
* exit 2 — the guard is misconfigured (missing / non-numeric canonical count,
  or a battery whose count cannot be parsed).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD = REPO_ROOT / "scripts" / "check-test-count.sh"
CANON = REPO_ROOT / "scripts" / "test-count.txt"
BATTERY = REPO_ROOT / "src" / "h3_shim" / "test_battery.py"
MAKEFILE = REPO_ROOT / "Makefile"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test.yml"

# A count the battery has retired (it shipped this many before growing to the
# current canonical count). Assembled from digit fragments so this file's own
# source carries no stale-count literal — the guard sweeps tracked *.py too.
RETIRED = "4" + "4"

BANNER_NOTE = "> **Historical (2026-01-01):** the compliance battery stood at"


def _run_guard(
    *,
    canon_file: Path | None = None,
    battery: Path | None = None,
    scan_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the guard, overriding its documented inputs when given."""
    env = dict(os.environ)
    if canon_file is not None:
        env["H3_SHIM_TEST_COUNT_FILE"] = str(canon_file)
    if battery is not None:
        env["H3_SHIM_BATTERY"] = str(battery)
    if scan_root is not None:
        env["H3_SHIM_SCAN_ROOT"] = str(scan_root)
    return subprocess.run(
        ["sh", str(GUARD)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_canonical_count_matches_the_battery() -> None:
    """The two machine-readable declarations must agree."""
    canon = CANON.read_text().strip()
    assert canon.isdigit(), f"test-count.txt must be a bare number, got {canon!r}"
    pattern = r"^EXPECTED_TEST_COUNT\s*=\s*(\d+)\s*$"
    match = re.search(pattern, BATTERY.read_text(), re.M)
    assert match, "the battery no longer declares EXPECTED_TEST_COUNT"
    assert match.group(1) == canon


def test_guard_passes_on_the_current_tree() -> None:
    """Prose, battery and canonical count all agree — exit 0 with a summary."""
    result = _run_guard()
    assert result.returncode == 0, result.stderr + result.stdout
    assert "PASS" in result.stdout
    assert CANON.read_text().strip() in result.stdout


def test_guard_fails_when_the_canonical_count_file_is_missing(tmp_path: Path) -> None:
    result = _run_guard(canon_file=tmp_path / "absent.txt")
    assert result.returncode == 2, result.stdout
    assert "canonical count file missing" in result.stderr


def test_guard_fails_when_the_canonical_count_is_not_a_number(tmp_path: Path) -> None:
    bad = tmp_path / "test-count.txt"
    bad.write_text("forty-six\n")
    result = _run_guard(canon_file=bad)
    assert result.returncode == 2, result.stdout
    assert "not a bare number" in result.stderr


def test_guard_fails_when_the_battery_disagrees_with_the_canonical_count(
    tmp_path: Path,
) -> None:
    """The 46 -> 47 case: the battery moves before the prose notices."""
    ahead = tmp_path / "test-count.txt"
    ahead.write_text(f"{int(CANON.read_text().strip()) + 1}\n")
    result = _run_guard(canon_file=ahead)
    assert result.returncode == 1, result.stdout
    assert "battery drift" in result.stderr


def test_guard_flags_a_stale_literal_in_a_scanned_surface(tmp_path: Path) -> None:
    (tmp_path / "drift.md").write_text(f"a {RETIRED}-test compliance battery\n")
    result = _run_guard(battery=BATTERY, scan_root=tmp_path)
    assert result.returncode == 1, result.stderr
    assert "drift.md:1" in result.stdout


def test_guard_exempts_a_line_marked_as_historical(tmp_path: Path) -> None:
    marked = tmp_path / "narration.py"
    marked.write_text(f'"""era score: {RETIRED}/{RETIRED} (count-ok-historical)."""\n')
    result = _run_guard(battery=BATTERY, scan_root=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stdout


def test_guard_requires_a_banner_on_dated_reports_quoting_a_retired_count(
    tmp_path: Path,
) -> None:
    report = tmp_path / "docs" / "dogfood" / "2026-01-01-integration.md"
    report.parent.mkdir(parents=True)
    report.write_text(f"# Report\n\nthe {RETIRED} tests all passed\n")

    bare = _run_guard(battery=BATTERY, scan_root=tmp_path)
    assert bare.returncode == 1, bare.stdout
    assert "no point-in-time banner" in bare.stderr
    assert "2026-01-01-integration.md" in bare.stderr

    report.write_text(
        "# Report\n\n"
        f"{BANNER_NOTE} {RETIRED} tests on\n"
        f"> this date; the current count is {CANON.read_text().strip()}"
        " (`scripts/test-count.txt`).\n\n"
        f"the {RETIRED} tests all passed\n"
    )
    banner = _run_guard(battery=BATTERY, scan_root=tmp_path)
    assert banner.returncode == 0, banner.stderr
    assert "PASS" in banner.stdout


def test_guard_is_wired_into_make_and_ci() -> None:
    """A guard nobody runs is not a guard."""
    assert GUARD.is_file(), "scripts/check-test-count.sh is missing"
    makefile = MAKEFILE.read_text()
    assert re.search(r"^\.PHONY:.*\bverify-counts\b", makefile, re.M)
    target = r"^verify-counts:\n\tsh scripts/check-test-count\.sh$"
    assert re.search(target, makefile, re.M)
    assert re.search(r"^all:.*\bverify-counts\b", makefile, re.M)
    assert "bash scripts/check-test-count.sh" in WORKFLOW.read_text()
