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

cleanup() { rm -rf "$SMOKE_DIR"; }
trap cleanup EXIT

pass() { echo "  ✅ PASS: $1"; PASSED=$((PASSED + 1)); }
fail() { echo "  ❌ FAIL: $1"; FAILED=$((FAILED + 1)); }

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
