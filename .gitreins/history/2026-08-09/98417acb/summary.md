# Verdict: gap-011

**Task:** GAP-011: bundle protocol/versions.yaml as package data
**Evaluated:** 2026-08-09T01:29:43.180827
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ guard: Tier 1 Guards: PASS  (test mode: diff, full suite — safety trigger)
  ✓ secrets — clean
  ✓ lint — o
- ✓ **tier2**
  - COMPLETE
  ✓ src/h3_shim/data/versions.yaml exists and is byte-identical to /home/kara/get-h3/protocol/versions.yaml: src/h3_shim/data/versions.yaml exists (1362 bytes) and `cmp` confirms BYTE-IDENTICAL to /home/kara/get-h3/protocol/versions.yaml
  ✓ h3_shim.upgrade_check._default_versions_yaml_path() returns the bundled data file when it exists (no dev-tree dependency): upgrade_check.py:50-55: _default_versions_yaml_path() checks _bundled_versions_yaml_path() (src/h3_shim/data/versions.yaml) first and returns it since it exists. Verified via import: default=/home/kara/get-h3/shim/src/h3_shim/data/versions.yaml, exists=True
  ✓ A wheel built from the repo contains h3_shim/data/versions.yaml (zipfile namelist check): Built dist/hermes_h3_shim-0.1.0-py3-none-any.whl; zipfile namelist contains h3_shim/data/versions.yaml, byte-identical to source (sha256 0fad4831a242d370871ab919f12d1e10f0d617ec61f5d65266e2ea894ca795a0)
  ✓ pip install of the built wheel into a fresh venv -> hermes-h3 pre-update-check 0.18.0 returns the version-specific 'too old' result (matrix loaded), NOT the blanket 'no compatibility data' empty-matrix message: Fresh venv install of wheel + `hermes-h3 pre-update-check 0.18.0` from /tmp returns 'H3 shim v0.1.0 is too old for Hermes 0.18.0 (requires H3 ≥ 1.0.0)' — version-specific result, not 'no compatibility data'. Installed module resolves to site-packages/h3_shim/data/versions.yaml
  ✓ scripts/smoke_test.sh step 8 asserts the version-specific check result post-install (fails on 'no compatibility data'): scripts/smoke_test.sh lines ~104-118: step 8 runs pre-update-check 0.18.0, FAILS on 'no compatibility data', PASSES only on 'too old'. Smoke test ran 19/19 PASS with step 8 confirming 'bundled matrix loaded: version-specific check'
  ✓ Full pytest suite passes (291 tests): `pytest -q` reports '291 passed in 1.89s'. ruff check clean, LSP diagnostics empty
All 6 GAP-011 criteria verified: versions.yaml bundled as package data, resolution prefers bundled file, wheel contains it, fresh-venv install returns version-specific 'too old' result, smoke test step 8 asserts it, and all 291 tests pass.

## Summary

Judge Result: gap-011

Stage tier1: PASS
    ✓ guard: Tier 1 Guards: PASS  (test mode: diff, full suite — safety trigger)
  ✓ secrets — clean
  ✓ lint — o

Stage tier2: PASS
  COMPLETE
  ✓ src/h3_shim/data/versions.yaml exists and is byte-identical to /home/kara/get-h3/protocol/versions.yaml: src/h3_shim/data/versions.yaml exists (1362 bytes) and `cmp` confirms BYTE-IDENTICAL to /home/kara/get-h3/protocol/versions.yaml
  ✓ h3_shim.upgrade_check._default_versions_yaml_path() returns the bundled data file when it exists (no dev-tree dependency): upgrade_check.py:50-55: _default_versions_yaml_path() checks _bundled_versions_yaml_path() (src/h3_shim/data/versions.yaml) first and returns it since it exists. Verified via import: default=/home/kara/get-h3/shim/src/h3_shim/data/versions.yaml, exists=True
  ✓ A wheel built from the repo contains h3_shim/data/versions.yaml (zipfile namelist check): Built dist/hermes_h3_shim-0.1.0-py3-none-any.whl; zipfile namelist contains h3_shim/data/versions.yaml, byte-identical to source (sha256 0fad4831a242d370871ab919f12d1e10f0d617ec61f5d65266e2ea894ca795a0)
  ✓ pip install of the built wheel into a fresh venv -> hermes-h3 pre-update-check 0.18.0 returns the version-specific 'too old' result (matrix loaded), NOT the blanket 'no compatibility data' empty-matrix message: Fresh venv install of wheel + `hermes-h3 pre-update-check 0.18.0` from /tmp returns 'H3 shim v0.1.0 is too old for Hermes 0.18.0 (requires H3 ≥ 1.0.0)' — version-specific result, not 'no compatibility data'. Installed module resolves to site-packages/h3_shim/data/versions.yaml
  ✓ scripts/smoke_test.sh step 8 asserts the version-specific check result post-install (fails on 'no compatibility data'): scripts/smoke_test.sh lines ~104-118: step 8 runs pre-update-check 0.18.0, FAILS on 'no compatibility data', PASSES only on 'too old'. Smoke test ran 19/19 PASS with step 8 confirming 'bundled matrix loaded: version-specific check'
  ✓ Full pytest suite passes (291 tests): `pytest -q` reports '291 passed in 1.89s'. ruff check clean, LSP diagnostics empty
All 6 GAP-011 criteria verified: versions.yaml bundled as package data, resolution prefers bundled file, wheel contains it, fresh-venv install returns version-specific 'too old' result, smoke test step 8 asserts it, and all 291 tests pass.

Overall: PASS ✓
