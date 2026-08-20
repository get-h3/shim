# Verdict: gap-038

**Task:** P3 hermes-h3 --version missing (CLI asymmetry)
**Evaluated:** 2026-08-20T16:50:20.684710
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ guard: Tier 1 Guards: PASS  (test mode: diff, full suite — safety trigger)
  ✓ secrets — clean
  ✓ lint — o
- ✓ **tier2**
  - COMPLETE
  ✓ hermes-h3 --version prints version + install path (parity with h3-test): Runtime verified: `.venv/bin/hermes-h3 --version` outputs `hermes-h3 0.1.0 (h3_shim: /home/kara/get-h3/shim/src/h3_shim/__init__.py)` with exit 0, matching `h3-test --version` parity. Code in src/h3_shim/cli.py: hermes_h3 click group has invoke_without_command=True, a --version flag, and calls `_version_string("hermes-h3")` then `ctx.exit()`; `_version_string(prog)` returns `f"{prog} {version} (h3_shim: {Path(h3_shim.__file__).resolve()})"` (version + install path). Regression tests in tests/test_cli.py TestHermesH3VersionFlag (2 tests). Full suite: 315 passed (exit 0). ruff clean; no LSP diagnostics.
GAP-038 fixed: hermes-h3 --version now prints version + install path with parity to h3-test, verified by runtime execution and passing tests.

## Summary

Judge Result: gap-038

Stage tier1: PASS
    ✓ guard: Tier 1 Guards: PASS  (test mode: diff, full suite — safety trigger)
  ✓ secrets — clean
  ✓ lint — o

Stage tier2: PASS
  COMPLETE
  ✓ hermes-h3 --version prints version + install path (parity with h3-test): Runtime verified: `.venv/bin/hermes-h3 --version` outputs `hermes-h3 0.1.0 (h3_shim: /home/kara/get-h3/shim/src/h3_shim/__init__.py)` with exit 0, matching `h3-test --version` parity. Code in src/h3_shim/cli.py: hermes_h3 click group has invoke_without_command=True, a --version flag, and calls `_version_string("hermes-h3")` then `ctx.exit()`; `_version_string(prog)` returns `f"{prog} {version} (h3_shim: {Path(h3_shim.__file__).resolve()})"` (version + install path). Regression tests in tests/test_cli.py TestHermesH3VersionFlag (2 tests). Full suite: 315 passed (exit 0). ruff clean; no LSP diagnostics.
GAP-038 fixed: hermes-h3 --version now prints version + install path with parity to h3-test, verified by runtime execution and passing tests.

Overall: PASS ✓
