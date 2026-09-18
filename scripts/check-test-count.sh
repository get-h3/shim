#!/bin/sh
# check-test-count.sh — the shim repo polices its OWN prose (H3-GAP-079).
#
# Why this exists: src/h3_shim/test_battery.py is the single source of truth
# for the compliance-test count, but nothing in THIS repo checked the prose
# around it. The stale-count class re-offended six-plus times (shim rows
# GAP-012, GAP-016, GAP-017, DOC-1, DF3-H3-SHIM-3, DF-H3-SHIM-FOREMAN-1,
# QA-H3-SHIM-FOREMAN-3) because the only sweep lived in the umbrella repo,
# where a shim-only doc edit is never seen. The shim owns its truth now.
#
# Checks:
#   a. canonical count     — read scripts/test-count.txt; it must exist and be
#                            a bare number (otherwise exit 2).
#   b. battery parity      — EXPECTED_TEST_COUNT in src/h3_shim/test_battery.py
#                            must equal the canonical count. This is what
#                            catches the battery going 46 -> 47 before any doc
#                            notices.
#   c. stale-literal sweep — no tracked current-state surface (*.md, *.html,
#                            *.py, *.ts, *.toml, Makefile) may still advertise
#                            a RETIRED count. Historical paths are skipped
#                            (CHANGELOG.md, dated field reports
#                            docs/dogfood/<YYYY-MM-DD>-*.md, e2e-output/,
#                            .coding-hermes/, .gitreins/), and so is any single
#                            line carrying the inline marker
#                            `count-ok-historical` (era-correct narration of a
#                            past score only — never a current-state claim).
#   d. dated-report banner — a dated field report that DOES quote a retired
#                            count must open with a point-in-time banner
#                            "> **Historical (YYYY-MM-DD):** ...", which is what
#                            makes the exemption in (c) unambiguous to a reader.
#
# Exit codes: 0 = pass, 1 = drift, 2 = guard misconfigured (missing/bad inputs).
# Zero dependencies: POSIX sh + coreutils + grep. No venv, no network.
#
# Overrides (used by tests/test_count_guard.py to drive the drift paths
# hermetically): H3_SHIM_TEST_COUNT_FILE, H3_SHIM_BATTERY, H3_SHIM_SCAN_ROOT.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=${H3_SHIM_SCAN_ROOT:-$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)}

# ---- (a) canonical count ---------------------------------------------------
CANON_FILE=${H3_SHIM_TEST_COUNT_FILE:-$SCRIPT_DIR/test-count.txt}
if [ ! -f "$CANON_FILE" ]; then
    echo "FAIL: canonical count file missing: $CANON_FILE" >&2
    echo "      Fix: create scripts/test-count.txt holding the battery's EXPECTED_TEST_COUNT." >&2
    exit 2
fi
CANON=$(tr -d ' \t\r\n' < "$CANON_FILE")
case "$CANON" in
    '' | *[!0-9]*)
        echo "FAIL: $CANON_FILE is not a bare number: '$CANON'" >&2
        echo "      Fix: it must contain exactly the canonical count, e.g. '46'." >&2
        exit 2
        ;;
esac

# ---- (b) battery parity ----------------------------------------------------
BATTERY=${H3_SHIM_BATTERY:-$ROOT/src/h3_shim/test_battery.py}
if [ ! -f "$BATTERY" ]; then
    echo "FAIL: compliance battery not found: $BATTERY" >&2
    echo "      Fix: point H3_SHIM_BATTERY at it — the guard cannot police a count" >&2
    echo "      it cannot read." >&2
    exit 2
fi
ACTUAL=$(sed -n 's/^[[:space:]]*EXPECTED_TEST_COUNT[[:space:]]*=[[:space:]]*\([0-9][0-9]*\).*$/\1/p' "$BATTERY" | head -n 1)
if [ -z "$ACTUAL" ]; then
    echo "FAIL: could not parse EXPECTED_TEST_COUNT from $BATTERY" >&2
    echo "      The battery moved or renamed the constant — update this guard." >&2
    exit 2
fi
if [ "$ACTUAL" != "$CANON" ]; then
    echo "FAIL: battery drift — $BATTERY says EXPECTED_TEST_COUNT=$ACTUAL" >&2
    echo "      but scripts/test-count.txt says $CANON." >&2
    echo "      Fix: decide which is truth, then update scripts/test-count.txt AND every doc below." >&2
    exit 1
fi
echo "check-test-count: battery agrees ($ACTUAL tests at $BATTERY)"

# ---- (c) stale-literal sweep over current-state surfaces -------------------
# Retired counts only (43/44/45 in count-shaped forms) — never a bare number,
# which would also match ports, dates and durations.
STALE_PATTERN='4[345]-tests?|4[345] tests?|4[345]/4[345]|4[345] compliance|4[345] passed|out of 4[345]'

is_scanned() {
    case "$1" in
        *.md | *.html | *.py | *.ts | *.toml) return 0 ;;
        Makefile | */Makefile) return 0 ;;
        *) return 1 ;;
    esac
}

# Dated, era-correct records: these carry the number that was TRUE when they
# were written and must keep it (a banner marks them). Board/state stores are
# out of scope for the same reason.
is_excluded() {
    case "$1" in
        CHANGELOG.md | e2e-output/* | .coding-hermes/* | .gitreins/*) return 0 ;;
        docs/dogfood/[0-9][0-9][0-9][0-9]-*) return 0 ;;
        *) return 1 ;;
    esac
}

if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    FILES=$(git -C "$ROOT" ls-files)
else
    FILES=$(cd "$ROOT" && find . -type f | sed 's|^\./||')
fi

# The scans below word-split the file list, so a path with whitespace would be
# scanned as fragments. Fail loudly instead of scanning the wrong thing.
SPACED=$(printf '%s\n' "$FILES" | grep ' ' || true)
if [ -n "$SPACED" ]; then
    echo "FAIL: whitespace in a tracked path breaks this scan: '$SPACED'" >&2
    exit 2
fi

HITS=0
for f in $FILES; do
    if ! is_scanned "$f"; then continue; fi
    if is_excluded "$f"; then continue; fi
    if [ ! -f "$ROOT/$f" ]; then continue; fi
    OUT=$(grep -n -I -E -- "$STALE_PATTERN" "$ROOT/$f" 2>/dev/null || true)
    if [ -z "$OUT" ]; then continue; fi
    OUT=$(printf '%s\n' "$OUT" | grep -v 'count-ok-historical' || true)
    if [ -z "$OUT" ]; then continue; fi
    printf '%s\n' "$OUT" | while IFS= read -r hit; do
        printf '%s:%s\n' "$f" "$hit"
    done
    N=$(printf '%s\n' "$OUT" | wc -l | tr -d ' ')
    HITS=$((HITS + N))
done

if [ "$HITS" -ne 0 ]; then
    echo "FAIL: $HITS stale compliance-test count literal(s) above." >&2
    echo "      The battery ships $CANON tests — replace each hit with $CANON (or" >&2
    echo "      count-agnostic wording), or mark era-correct narration with the" >&2
    echo "      inline marker count-ok-historical." >&2
    exit 1
fi
echo "check-test-count: no stale count literals in current-state surfaces"

# ---- (d) dated field reports must carry a point-in-time banner -------------
BANNER_PATTERN='^[[:space:]]*>[[:space:]]*\*\*Historical \([0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]\):'
UNBANNERED=0
for f in $FILES; do
    case "$f" in
        docs/dogfood/[0-9][0-9][0-9][0-9]-*) ;;
        *) continue ;;
    esac
    if [ ! -f "$ROOT/$f" ]; then continue; fi
    if ! grep -q -I -E -- "$STALE_PATTERN" "$ROOT/$f" 2>/dev/null; then continue; fi
    if grep -q -I -E -- "$BANNER_PATTERN" "$ROOT/$f"; then continue; fi
    echo "FAIL: $f quotes a retired compliance-test count with no point-in-time banner." >&2
    UNBANNERED=$((UNBANNERED + 1))
done
if [ "$UNBANNERED" -ne 0 ]; then
    echo "      Fix: add one line directly under the first heading:" >&2
    echo "        > **Historical (YYYY-MM-DD):** the compliance battery stood at <N> tests" >&2
    echo "        > on this date; the current count is $CANON (\`scripts/test-count.txt\`)." >&2
    exit 1
fi

# ---- (e) PASS summary ------------------------------------------------------
echo "check-test-count: PASS — canonical compliance-test count is $CANON; current-state prose agrees"
exit 0
