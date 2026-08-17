# Verdict: gap-029

**Task:** scaffold --lang accepts python/typescript aliases
**Evaluated:** 2026-08-17T10:44:18.759978
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ guard: Tier 1 Guards: PASS  (test mode: diff, full suite — safety trigger)
  ✓ secrets — clean
  ✓ lint — o
- ✓ **tier2**
  - COMPLETE
  ✓ PASS: hermes-h3 scaffold --lang python exits 0 (and --lang typescript likewise), or the error for those values is a helpful alias message rather than 'invalid choice': src/h3_shim/cli.py:59-66 defines LANG_ALIASES={'python':'py','typescript':'ts'} and _normalize_lang(); the --lang option (cli.py:855-865) uses click.Choice((*SUPPORTED_LANGS,*LANG_ALIASES), case_sensitive=False) with a callback normalizing aliases to canonical codes. Direct CLI runs: 'scaffold --lang python' EXIT=0 generating h3-harness-py, and 'scaffold --lang typescript' EXIT=0 generating h3-harness-ts. Test test_scaffold_lang_aliases_accepted (tests/test_cli.py:310-320) passes; full suite 299 passed; ruff clean; no LSP diagnostics.
hermes-h3 scaffold --lang now accepts python/typescript aliases, mapping them to py/ts, with both direct CLI runs exiting 0 and the full test suite (299 passed) green.

## Summary

Judge Result: gap-029

Stage tier1: PASS
    ✓ guard: Tier 1 Guards: PASS  (test mode: diff, full suite — safety trigger)
  ✓ secrets — clean
  ✓ lint — o

Stage tier2: PASS
  COMPLETE
  ✓ PASS: hermes-h3 scaffold --lang python exits 0 (and --lang typescript likewise), or the error for those values is a helpful alias message rather than 'invalid choice': src/h3_shim/cli.py:59-66 defines LANG_ALIASES={'python':'py','typescript':'ts'} and _normalize_lang(); the --lang option (cli.py:855-865) uses click.Choice((*SUPPORTED_LANGS,*LANG_ALIASES), case_sensitive=False) with a callback normalizing aliases to canonical codes. Direct CLI runs: 'scaffold --lang python' EXIT=0 generating h3-harness-py, and 'scaffold --lang typescript' EXIT=0 generating h3-harness-ts. Test test_scaffold_lang_aliases_accepted (tests/test_cli.py:310-320) passes; full suite 299 passed; ruff clean; no LSP diagnostics.
hermes-h3 scaffold --lang now accepts python/typescript aliases, mapping them to py/ts, with both direct CLI runs exiting 0 and the full test suite (299 passed) green.

Overall: PASS ✓
