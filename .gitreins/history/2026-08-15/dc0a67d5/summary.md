# Verdict: dogfood-09

**Task:** DOGFOOD-09: TS scaffold template npm dep -> github form (unpublished npm E404)
**Evaluated:** 2026-08-15T22:29:11.026056
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ guard: Tier 1 Guards: PASS  (test mode: diff, full suite — safety trigger)
  ✓ secrets — clean
  ✓ lint — o
- ✓ **tier2**
  - COMPLETE
  ✓ src/h3_shim/templates/ts/package.json dependency @get-h3/h3-harness-sdk resolves from github:get-h3/sdk-typescript (github: form); fresh ts scaffold npm install succeeds; h3-test battery 44/44 exit 0 against the scaffold harness: (1) src/h3_shim/templates/ts/package.json line 14: "@get-h3/h3-harness-sdk": "github:get-h3/sdk-typescript" — confirmed in source template and generated scaffold; package-lock.json shows resolved=git+ssh://git@github.com/get-h3/sdk-typescript.git. (2) Fresh scaffold via `hermes-h3 scaffold --lang ts` then `npm install` succeeded (added 10 packages, 0 vulnerabilities). (3) Harness started on :9191, `hermes-h3 test --endpoint http://localhost:9191` returned TOTAL 44/44 PASSED with exit code 0.
The TS scaffold template dependency was correctly changed from npm ^0.1.0 to github:get-h3/sdk-typescript, fresh scaffold npm install succeeds, and the h3-test battery passes 44/44 with exit code 0.

## Summary

Judge Result: dogfood-09

Stage tier1: PASS
    ✓ guard: Tier 1 Guards: PASS  (test mode: diff, full suite — safety trigger)
  ✓ secrets — clean
  ✓ lint — o

Stage tier2: PASS
  COMPLETE
  ✓ src/h3_shim/templates/ts/package.json dependency @get-h3/h3-harness-sdk resolves from github:get-h3/sdk-typescript (github: form); fresh ts scaffold npm install succeeds; h3-test battery 44/44 exit 0 against the scaffold harness: (1) src/h3_shim/templates/ts/package.json line 14: "@get-h3/h3-harness-sdk": "github:get-h3/sdk-typescript" — confirmed in source template and generated scaffold; package-lock.json shows resolved=git+ssh://git@github.com/get-h3/sdk-typescript.git. (2) Fresh scaffold via `hermes-h3 scaffold --lang ts` then `npm install` succeeded (added 10 packages, 0 vulnerabilities). (3) Harness started on :9191, `hermes-h3 test --endpoint http://localhost:9191` returned TOTAL 44/44 PASSED with exit code 0.
The TS scaffold template dependency was correctly changed from npm ^0.1.0 to github:get-h3/sdk-typescript, fresh scaffold npm install succeeds, and the h3-test battery passes 44/44 with exit code 0.

Overall: PASS ✓
