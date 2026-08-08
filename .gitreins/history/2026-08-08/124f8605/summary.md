# Verdict: gap-010

**Task:** h3-test exit codes 0/1/2 documented
**Evaluated:** 2026-08-08T02:55:23.833886
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ guard: Tier 1 Guards: PASS  (test mode: diff, full suite — safety trigger)
  ✓ secrets — clean
  ✓ lint — o
- ✓ **tier2**
  - COMPLETE
  ✓ README.md contains an Exit codes section distinguishing 0 (compliant), 1 (compliance failure), 2 (not an H3 endpoint): README.md lines 24-35 contain '### Exit codes' section with a table distinguishing 0 (Compliant), 1 (Compliance failure), 2 (Not an H3 endpoint).
  ✓ docs/integration.md troubleshooting updated: exit 2 means wrong URL/harness down/connection refused, NOT a protocol regression: docs/integration.md lines 239-253 have Exit codes section and line 273 troubleshooting row states exit 2 = not an H3 endpoint (wrong URL / harness down / connection refused / HTTP error) NOT a protocol regression.
  ✓ Documentation matches actual code: cli.py returns 0 when report.all_passing else 1 (line ~403), returns 2 on NotH3EndpointError / unknown categories (lines ~370/387), test_battery.py raises NotH3EndpointError for connection errors, HTTP >= 400, non-JSON bodies, and missing H3 fields: cli.py line 403 `return 0 if report.all_passing else 1`; line 370 `return 2` on NotH3EndpointError; line 387 `return 2` on unknown categories. test_battery.py raises NotH3EndpointError for connection errors (line 138), HTTP >= 400 (line 140-145), non-JSON bodies (line 155), missing H3 fields (line 173).
  ✓ Live check: h3-test --endpoint http://localhost:9999 exits 2 with the does-not-look-like-an-H3-endpoint warning (stderr) and --json mode emits not_h3_endpoint true: Live run of `.venv/bin/h3-test --endpoint http://localhost:9999` exits 2 with 'does not look like an H3 endpoint' warning on stderr; `--json` mode emits "not_h3_endpoint": true in the JSON payload.
All four criteria verified: README and docs/integration.md document exit codes 0/1/2 correctly, the code matches the documentation, and the live check confirms exit 2 with the not_h3_endpoint warning and JSON flag.

## Summary

Judge Result: gap-010

Stage tier1: PASS
    ✓ guard: Tier 1 Guards: PASS  (test mode: diff, full suite — safety trigger)
  ✓ secrets — clean
  ✓ lint — o

Stage tier2: PASS
  COMPLETE
  ✓ README.md contains an Exit codes section distinguishing 0 (compliant), 1 (compliance failure), 2 (not an H3 endpoint): README.md lines 24-35 contain '### Exit codes' section with a table distinguishing 0 (Compliant), 1 (Compliance failure), 2 (Not an H3 endpoint).
  ✓ docs/integration.md troubleshooting updated: exit 2 means wrong URL/harness down/connection refused, NOT a protocol regression: docs/integration.md lines 239-253 have Exit codes section and line 273 troubleshooting row states exit 2 = not an H3 endpoint (wrong URL / harness down / connection refused / HTTP error) NOT a protocol regression.
  ✓ Documentation matches actual code: cli.py returns 0 when report.all_passing else 1 (line ~403), returns 2 on NotH3EndpointError / unknown categories (lines ~370/387), test_battery.py raises NotH3EndpointError for connection errors, HTTP >= 400, non-JSON bodies, and missing H3 fields: cli.py line 403 `return 0 if report.all_passing else 1`; line 370 `return 2` on NotH3EndpointError; line 387 `return 2` on unknown categories. test_battery.py raises NotH3EndpointError for connection errors (line 138), HTTP >= 400 (line 140-145), non-JSON bodies (line 155), missing H3 fields (line 173).
  ✓ Live check: h3-test --endpoint http://localhost:9999 exits 2 with the does-not-look-like-an-H3-endpoint warning (stderr) and --json mode emits not_h3_endpoint true: Live run of `.venv/bin/h3-test --endpoint http://localhost:9999` exits 2 with 'does not look like an H3 endpoint' warning on stderr; `--json` mode emits "not_h3_endpoint": true in the JSON payload.
All four criteria verified: README and docs/integration.md document exit codes 0/1/2 correctly, the code matches the documentation, and the live check confirms exit 2 with the not_h3_endpoint warning and JSON flag.

Overall: PASS ✓
