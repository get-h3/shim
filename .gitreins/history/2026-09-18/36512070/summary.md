# Verdict: gap-043

**Task:** GAP-043: mypy gate drift in the py scaffold template — _summarize_validation_errors annotated list[dict] but fed pydantic's Sequence[Any]
**Evaluated:** 2026-09-18T23:14:33.178171
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ guard: Tier 1: DEGRADED PASS (skips: lint=no staged files, tests=no staged files)  (test mode: diff, full s
- ✓ **tier2**
  - COMPLETE
  ✓ uv run --with mypy mypy src/ returns to the 3 stub-only WARN envelope (no arg-type error at templates/py/main.py); full pytest suite green; ruff check + ruff format --check rc=0: Fresh runs at committed tree 7db2e08: (1) `uv run --with mypy mypy src/` -> 'Found 3 errors in 3 files (checked 11 source files)'; grep -c 'arg-type' = 0 and no output line references templates/py/main.py; the 3 errors are exactly the stub-only envelope (test_battery.py:1976 jsonschema [import-untyped], upgrade_check.py:26 yaml [import-untyped], cli.py:37 yaml [import-untyped]). (2) `.venv/bin/python -m pytest -q` -> exit 0, '369 passed, 4 warnings in 25.85s'. (3) `.venv/bin/ruff check .` -> exit 0, 'All checks passed!'; `.venv/bin/ruff format --check .` -> exit 0, '48 files already formatted'. Diff confirms the annotation fix: templates/py/main.py `_summarize_validation_errors(errors: Sequence[Mapping[str, Any]])` with `from collections.abc import Mapping, Sequence`, and shim_loop.py `_format_pydantic_error(error: Mapping[str, Any])` with `Mapping` added to the collections.abc import.
All three gates pass fresh: mypy src/ is back to the 3 stub-only import-untyped errors with zero arg-type findings, pytest is 369 passed, and ruff check/format both exit 0.

## Summary

Judge Result: gap-043

Stage tier1: PASS
    ✓ guard: Tier 1: DEGRADED PASS (skips: lint=no staged files, tests=no staged files)  (test mode: diff, full s

Stage tier2: PASS
  COMPLETE
  ✓ uv run --with mypy mypy src/ returns to the 3 stub-only WARN envelope (no arg-type error at templates/py/main.py); full pytest suite green; ruff check + ruff format --check rc=0: Fresh runs at committed tree 7db2e08: (1) `uv run --with mypy mypy src/` -> 'Found 3 errors in 3 files (checked 11 source files)'; grep -c 'arg-type' = 0 and no output line references templates/py/main.py; the 3 errors are exactly the stub-only envelope (test_battery.py:1976 jsonschema [import-untyped], upgrade_check.py:26 yaml [import-untyped], cli.py:37 yaml [import-untyped]). (2) `.venv/bin/python -m pytest -q` -> exit 0, '369 passed, 4 warnings in 25.85s'. (3) `.venv/bin/ruff check .` -> exit 0, 'All checks passed!'; `.venv/bin/ruff format --check .` -> exit 0, '48 files already formatted'. Diff confirms the annotation fix: templates/py/main.py `_summarize_validation_errors(errors: Sequence[Mapping[str, Any]])` with `from collections.abc import Mapping, Sequence`, and shim_loop.py `_format_pydantic_error(error: Mapping[str, Any])` with `Mapping` added to the collections.abc import.
All three gates pass fresh: mypy src/ is back to the 3 stub-only import-untyped errors with zero arg-type findings, pytest is 369 passed, and ruff check/format both exit 0.

Overall: PASS ✓
