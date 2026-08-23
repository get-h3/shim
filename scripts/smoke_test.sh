#!/usr/bin/env bash
# Smoke test: build wheel, install into fresh venv, verify all entry points.
# GAP-008 — installed-artifact smoke test.
# Must FAIL if __init__.py is missing from the wheel or any documented
# subcommand crashes.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMOKE_DIR="$(mktemp -d)"
WHEEL_DIR="$SMOKE_DIR/wheels"
VENV_DIR="$SMOKE_DIR/venv"
PASSED=0
FAILED=0
# PID of the scaffolded harness started in step 9c — killed by cleanup() on
# EVERY exit path (including mid-script failures under set -e).
SERVER_PID=""

cleanup() {
    if [ -n "$SERVER_PID" ]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    rm -rf "$SMOKE_DIR"
}
trap cleanup EXIT

pass() { echo "  ✅ PASS: $1"; PASSED=$((PASSED + 1)); }
fail() { echo "  ❌ FAIL: $1"; FAILED=$((FAILED + 1)); }

# Find a free TCP port starting at $1 (default 9191) — the harness may be
# occupied on the default by another process, in which case PORT overrides.
free_port() {
    python3 -c "
import socket, sys
start = int(sys.argv[1])
for port in range(start, start + 50):
    with socket.socket() as s:
        try:
            s.bind(('127.0.0.1', port))
        except OSError:
            continue
        print(port)
        sys.exit(0)
sys.exit(1)
" "$1"
}

echo "=== H3 Shim Smoke Test ==="
echo "Smoke dir: $SMOKE_DIR"
echo ""

# ── Build the wheel ────────────────────────────────────────────────────────
echo "── 1. Building wheel"
cd "$REPO_DIR"
rm -rf dist/
"$REPO_DIR/.venv/bin/python" -m build --wheel --outdir "$WHEEL_DIR" > /dev/null 2>&1
WHEEL="$(echo "$WHEEL_DIR"/hermes_h3_shim-*.whl)"
if [ ! -f "$WHEEL" ]; then
    fail "wheel not found at $WHEEL_DIR"
    exit 1
fi
pass "wheel built: $(basename "$WHEEL")"

# ── Verify wheel contents ──────────────────────────────────────────────────
echo "── 2. Checking wheel contents"
python3 -c "
import zipfile, sys
z = zipfile.ZipFile('$WHEEL')
names = z.namelist()

# __init__.py MUST be present
init_files = [n for n in names if n.endswith('h3_shim/__init__.py')]
if not init_files:
    print('FAIL: __init__.py missing from wheel')
    sys.exit(1)

# All 8 template files MUST be present
expected_templates = [
    'h3_shim/templates/go/main.go',
    'h3_shim/templates/go/go.mod',
    'h3_shim/templates/py/main.py',
    'h3_shim/templates/py/requirements.txt',
    'h3_shim/templates/py/pyproject.toml',
    'h3_shim/templates/ts/index.ts',
    'h3_shim/templates/ts/package.json',
    'h3_shim/templates/ts/tsconfig.json',
]
for tpl in expected_templates:
    if tpl not in names:
        print(f'FAIL: template missing from wheel: {tpl}')
        sys.exit(1)
print('OK: wheel contains __init__.py + all 8 templates')
" || { fail "wheel content verification"; exit 1; }
pass "wheel contains __init__.py + 8 templates"

# ── Install into fresh venv ────────────────────────────────────────────────
echo "── 3. Installing wheel into fresh venv"
python3 -m venv "$VENV_DIR" > /dev/null 2>&1
"$VENV_DIR/bin/pip" install "$WHEEL" > /dev/null 2>&1
pass "pip install into fresh venv"

# ── Verify __init__.py is importable ────────────────────────────────────────
echo "── 4. Verifying import"
"$VENV_DIR/bin/python" -c "import h3_shim; print(h3_shim.__version__)" > /dev/null 2>&1
pass "import h3_shim (version: $("$VENV_DIR/bin/python" -c 'import h3_shim; print(h3_shim.__version__)'))"

# ── Entry point: h3-test ────────────────────────────────────────────────────
echo "── 5. Entry point: h3-test --help"
"$VENV_DIR/bin/h3-test" --help > /dev/null 2>&1
pass "h3-test --help"

# ── Entry point: hermes-h3 --help ──────────────────────────────────────────
echo "── 6. Entry point: hermes-h3 --help"
"$VENV_DIR/bin/hermes-h3" --help > /dev/null 2>&1
pass "hermes-h3 --help"

# ── All 9 subcommand --help ─────────────────────────────────────────────────
echo "── 7. All 9 hermes-h3 subcommand --help"
SUBCMDS=(install list pre-update-check route scaffold test uninstall use verify)
for cmd in "${SUBCMDS[@]}"; do
    if "$VENV_DIR/bin/hermes-h3" "$cmd" --help > /dev/null 2>&1; then
        pass "hermes-h3 $cmd --help"
    else
        fail "hermes-h3 $cmd --help"
    fi
done

# ── pre-update-check (functional invocation) ────────────────────────────────
echo "── 8. hermes-h3 pre-update-check (bundled matrix loaded)"
# GAP-011: the bundled versions.yaml must be found post-install. With the
# matrix loaded, a matrix version (0.18.0) yields the version-specific
# "too old" check result — NOT the blanket empty-matrix "no compatibility
# data" message. Exit may be 1 (BLOCK) either way; the MESSAGE distinguishes.
OUT="$(set +e; "$VENV_DIR/bin/hermes-h3" pre-update-check 0.18.0 2>&1)" || true
if echo "$OUT" | grep -qi "traceback\|importerror\|modulenotfound"; then
    fail "pre-update-check 0.18.0 (ImportError traceback detected)"
elif echo "$OUT" | grep -qi "no compatibility data"; then
    fail "pre-update-check 0.18.0 (empty matrix — bundled versions.yaml not found)"
elif echo "$OUT" | grep -qi "too old"; then
    pass "pre-update-check 0.18.0 (bundled matrix loaded: version-specific check)"
else
    fail "pre-update-check 0.18.0 (unexpected output: $OUT)"
fi

# ── pre-update-check (shipped pairing must pass — GAP-033) ──────────────────
echo "── 8b. hermes-h3 pre-update-check (shipped 0.1.x pairing exits 0)"
# GAP-033: the package version and the compat matrix move together. The
# bundled matrix carries a 0.1.x row (Hermes 0.17.0) for the shipped
# package, so a fresh install must NOT be blocked by its own pairing.
set +e
"$VENV_DIR/bin/hermes-h3" pre-update-check 0.17.0 >/dev/null 2>&1
CODE=$?
set -e
if [ "$CODE" -eq 0 ]; then
    pass "pre-update-check 0.17.0 (shipped pairing passes — exit 0)"
else
    fail "pre-update-check 0.17.0 (shipped pairing blocked — exit $CODE)"
fi

# ── Scaffold (exercises template rendering) ─────────────────────────────────
echo "── 9. Scaffold a harness project (exercises templates)"
SCAFFOLD_DIR="$SMOKE_DIR/scaffold"
mkdir -p "$SCAFFOLD_DIR"
if "$VENV_DIR/bin/hermes-h3" scaffold --lang py --output-dir "$SCAFFOLD_DIR" --force > /dev/null 2>&1; then
    pass "hermes-h3 scaffold --lang py"
    # Verify the scaffolded files are present
    if [ -f "$SCAFFOLD_DIR/h3-harness-py/main.py" ]; then
        pass "scaffolded main.py exists"
    else
        fail "scaffolded main.py missing"
    fi
else
    fail "hermes-h3 scaffold --lang py"
fi

# ── Scaffolded project: non-editable wheel + install + serve (GAP-040) ──────
# GAP-040 regression: the template pyproject's hatchling include filter
# (include = ["main.py"]) silently dropped __init__.py from NON-EDITABLE
# wheels — the same GAP-005-class breakage the shim itself had. Editable
# installs mask it (source tree is used directly), so the scaffolded project
# must be built, installed non-editable, and actually serve end-to-end.
echo "── 9b. Scaffolded project: pip wheel . (non-editable) + contents"
SCAFFOLD_PROJ="$SCAFFOLD_DIR/h3-harness-py"
SCAFFOLD_WHEEL_DIR="$SMOKE_DIR/scaffold_wheels"
SCAFFOLD_VENV="$SMOKE_DIR/scaffold_venv"
mkdir -p "$SCAFFOLD_WHEEL_DIR"

if [ ! -f "$SCAFFOLD_PROJ/pyproject.toml" ]; then
    fail "scaffolded project missing ($SCAFFOLD_PROJ/pyproject.toml)"
    exit 1
fi

if (cd "$SCAFFOLD_PROJ" && "$VENV_DIR/bin/pip" wheel . --no-deps -w "$SCAFFOLD_WHEEL_DIR" > /dev/null 2>&1); then
    pass "scaffolded project: pip wheel . (non-editable build)"
else
    fail "scaffolded project: pip wheel ."
    exit 1
fi

SCAFFOLD_WHEEL="$(echo "$SCAFFOLD_WHEEL_DIR"/h3_harness_py-*.whl)"
if [ ! -f "$SCAFFOLD_WHEEL" ]; then
    fail "scaffolded wheel not found in $SCAFFOLD_WHEEL_DIR"
    exit 1
fi
pass "scaffolded wheel produced: $(basename "$SCAFFOLD_WHEEL")"

# main.py AND __init__.py must BOTH be in the wheel — a missing __init__.py
# here is the exact GAP-040/GAP-005 bug and must fail loudly.
python3 -c "
import zipfile, sys
z = zipfile.ZipFile('$SCAFFOLD_WHEEL')
# hatchling (packages = ['.']) may emit entries prefixed with './' — match
# by trailing path component instead.
names = z.namelist()
missing = [n for n in ('main.py', '__init__.py') if not any(x.endswith(n) for x in names)]
if missing:
    print('FAIL: scaffolded wheel missing: ' + ', '.join(missing))
    print('wheel entries: ' + ', '.join(sorted(names)))
    sys.exit(1)
print('OK: scaffolded wheel contains main.py + __init__.py')
" || { fail "scaffolded wheel contents (main.py/__init__.py)"; exit 1; }
pass "scaffolded wheel contains main.py + __init__.py"

# ── Non-editable install into a FRESH venv, then serve the INSTALLED code ───
echo "── 9c. Scaffolded project: non-editable install + serve :9191"
python3 -m venv "$SCAFFOLD_VENV" > /dev/null 2>&1
if "$SCAFFOLD_VENV/bin/pip" install "$SCAFFOLD_WHEEL" > /dev/null 2>&1; then
    pass "scaffolded wheel: pip install (non-editable, fresh venv)"
else
    fail "scaffolded wheel: pip install (non-editable, fresh venv)"
fi

PORT="$(free_port 9191)" || { fail "no free port found for scaffolded harness"; exit 1; }
# Run from a NEUTRAL directory via `python -m main` so the INSTALLED wheel
# artifact is exercised — `python main.py` from the project dir would run the
# working-tree copy and prove nothing about the non-editable install.
(
    cd "$SMOKE_DIR"
    PORT="$PORT" exec "$SCAFFOLD_VENV/bin/python" -m main
) > "$SMOKE_DIR/scaffold_harness.log" 2>&1 &
SERVER_PID=$!

HEALTH_OK=""
for _ in {1..30}; do
    if curl -sf -o /dev/null "http://127.0.0.1:$PORT/v1/health"; then
        HEALTH_OK=1
        break
    fi
    sleep 1
done

if [ -n "$HEALTH_OK" ]; then
    BODY="$(curl -s "http://127.0.0.1:$PORT/v1/health")"
    if echo "$BODY" | grep -q '"status"'; then
        pass "scaffolded harness serves /v1/health on :$PORT (H3 shape: $BODY)"
    else
        fail "scaffolded harness /v1/health missing H3 fields: $BODY"
    fi
else
    fail "scaffolded harness did not answer /v1/health on :$PORT (see scaffold_harness.log)"
fi

# Stop the harness now; cleanup() also guards every other exit path.
if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    SERVER_PID=""
fi

# ── Battery run (verify it can be invoked without ImportError) ──────────────
echo "── 10. Battery invocation (h3-test without endpoint)"
# Must not crash with ImportError — exit 2 is expected (no endpoint)
OUT="$("$VENV_DIR/bin/h3-test" 2>&1)" || true
if echo "$OUT" | grep -qi "traceback\|importerror\|modulenotfound"; then
    fail "h3-test invocation (ImportError detected)"
else
    pass "h3-test invocation (no ImportError)"
fi

# ── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo "=== Smoke Test Results ==="
echo "  Passed: $PASSED"
echo "  Failed: $FAILED"
echo ""

if [ "$FAILED" -gt 0 ]; then
    echo "❌ SMOKE TEST FAILED"
    exit 1
else
    echo "✅ SMOKE TEST PASSED"
    exit 0
fi
