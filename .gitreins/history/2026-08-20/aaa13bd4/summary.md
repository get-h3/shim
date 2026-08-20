# Verdict: gap-037

**Task:** P3 h3-test wrong-server warning dumps raw non-JSON body
**Evaluated:** 2026-08-20T16:50:10.490386
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ guard: Tier 1 Guards: PASS  (test mode: diff, full suite — safety trigger)
  ✓ secrets — clean
  ✓ lint — o
- ✓ **tier2**
  - COMPLETE

(auto-parsed from non-JSON response) All criteria verified. The criterion is fully satisfied:

1. **Non-JSON bodies truncated (~200 chars) in probe error while status/headers info kept** — PASS. The `_truncated_body` function (line 77-85) collapses whitespace and truncates to 200 chars. It's applied in both the `status >= 400` branch (

## Summary

Judge Result: gap-037

Stage tier1: PASS
    ✓ guard: Tier 1 Guards: PASS  (test mode: diff, full suite — safety trigger)
  ✓ secrets — clean
  ✓ lint — o

Stage tier2: PASS
  COMPLETE

(auto-parsed from non-JSON response) All criteria verified. The criterion is fully satisfied:

1. **Non-JSON bodies truncated (~200 chars) in probe error while status/headers info kept** — PASS. The `_truncated_body` function (line 77-85) collapses whitespace and truncates to 200 chars. It's applied in both the `status >= 400` branch (

Overall: PASS ✓
