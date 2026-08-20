# Verdict: gap-035

**Task:** P2 run() returns EndReason not final text
**Evaluated:** 2026-08-20T16:50:22.385488
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ guard: Tier 1 Guards: PASS  (test mode: diff, full suite — safety trigger)
  ✓ secrets — clean
  ✓ lint — o
- ✓ **tier2**
  - COMPLETE
  ✓ run() result contains the harness's final text OR api.md documents the EndReason contract + a text delivery hook exists: Second branch satisfied. docs/api.md:267 documents the EndReason contract: 'Returns the EndReason string of the terminating END decision ("task_complete", "error", "timeout", …) — not the assistant text.' A text delivery hook exists: on_text callback added at src/h3_shim/shim_loop.py:85 (constructor param), stored at :96, invoked at :373-374 in _execute_text, and documented at docs/api.md:233,248,269. Verified by running `.venv/bin/python -m pytest -x --tb=short -q` → 315 passed, including new TestOnText tests asserting run() returns 'task_complete' while text 'hello there' is delivered via the callback. Ruff lint clean; no LSP diagnostics.
GAP-035 is complete: run() returns the EndReason string, api.md documents the contract, and the on_text callback delivers TEXT decision content; all 315 tests pass.

## Summary

Judge Result: gap-035

Stage tier1: PASS
    ✓ guard: Tier 1 Guards: PASS  (test mode: diff, full suite — safety trigger)
  ✓ secrets — clean
  ✓ lint — o

Stage tier2: PASS
  COMPLETE
  ✓ run() result contains the harness's final text OR api.md documents the EndReason contract + a text delivery hook exists: Second branch satisfied. docs/api.md:267 documents the EndReason contract: 'Returns the EndReason string of the terminating END decision ("task_complete", "error", "timeout", …) — not the assistant text.' A text delivery hook exists: on_text callback added at src/h3_shim/shim_loop.py:85 (constructor param), stored at :96, invoked at :373-374 in _execute_text, and documented at docs/api.md:233,248,269. Verified by running `.venv/bin/python -m pytest -x --tb=short -q` → 315 passed, including new TestOnText tests asserting run() returns 'task_complete' while text 'hello there' is delivered via the callback. Ruff lint clean; no LSP diagnostics.
GAP-035 is complete: run() returns the EndReason string, api.md documents the contract, and the on_text callback delivers TEXT decision content; all 315 tests pass.

Overall: PASS ✓
