# Verdict: df-h3-shim-foreman-3

**Task:** py scaffold error-contract drift (422 detail -> protocol 400 INVALID_REQUEST envelope)
**Evaluated:** 2026-09-18T22:23:00.802070
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ guard: Tier 1: DEGRADED PASS (skips: lint=no staged files, tests=no staged files)  (test mode: diff, full s
- ✓ **tier2**
  - COMPLETE
  ✓ src/h3_shim/templates/py/main.py returns HTTP 400 with body {"error":{"code":"INVALID_REQUEST","message":...}} for malformed-JSON and schema-invalid request bodies (no top-level "detail", no 422); tests/test_scaffold_errors.py proves the 400 envelope for both cases plus a 200 for a valid request; py scaffold's intentional 404s unchanged; test_battery.py and the canonical compliance count (46) untouched; pytest + ruff check + ruff format --check + scripts/check-test-count.sh all green: main.py:319-378 registers @app.exception_handler(RequestValidationError) returning JSONResponse(status_code=400, content={"error":{"code":"INVALID_REQUEST","message":_summarize_validation_errors(errors),"details":{"errors":jsonable_encoder(errors)}}}). Live probe via TestClient(main.app): POST /v1/process json={} -> 400 {'error': {'code': 'INVALID_REQUEST', 'message': 'Invalid request body: 4 validation errors, first at body.session_id: Field required', ...}}; POST content=b"{not json" -> 400 same envelope with errors[0].type=='json_invalid'; neither response has a top-level "detail" and neither is 422. 404s unchanged: main.py:292 and :306 still raise HTTPException(status_code=404, detail="Session not found"); probe POST /v1/cancel -> 404 {'detail': 'Session not found'}; `git diff 8e6de10 HEAD --name-only` = only src/h3_shim/templates/py/main.py + tests/test_scaffold_errors.py, so test_battery.py is untouched. tests/test_scaffold_errors.py:5 tests all PASSED (test_missing_required_fields_is_400_invalid_request, test_malformed_json_is_400_invalid_request, test_valid_process_request_still_200_text_decision asserting 200 + decision=='text' + 'Echo: hello', test_deliberate_404_is_untouched, test_400_body_matches_protocol_error_response_schema). Gates run fresh: `.venv/bin/python -m pytest -x --tb=short -q` -> '357 passed, 4 warnings in 19.51s' exit_code=0; `.venv/bin/ruff check .` -> 'All checks passed!' exit_code=0; `.venv/bin/ruff format --check .` -> '48 files already formatted' exit_code=0; `sh scripts/check-test-count.sh` -> 'check-test-count: PASS — canonical compliance-test count is 46; current-state prose agrees' exit_code=0 (scripts/test-count.txt=46, src/h3_shim/test_battery.py:104 EXPECTED_TEST_COUNT = 46).


## Summary

Judge Result: df-h3-shim-foreman-3

Stage tier1: PASS
    ✓ guard: Tier 1: DEGRADED PASS (skips: lint=no staged files, tests=no staged files)  (test mode: diff, full s

Stage tier2: PASS
  COMPLETE
  ✓ src/h3_shim/templates/py/main.py returns HTTP 400 with body {"error":{"code":"INVALID_REQUEST","message":...}} for malformed-JSON and schema-invalid request bodies (no top-level "detail", no 422); tests/test_scaffold_errors.py proves the 400 envelope for both cases plus a 200 for a valid request; py scaffold's intentional 404s unchanged; test_battery.py and the canonical compliance count (46) untouched; pytest + ruff check + ruff format --check + scripts/check-test-count.sh all green: main.py:319-378 registers @app.exception_handler(RequestValidationError) returning JSONResponse(status_code=400, content={"error":{"code":"INVALID_REQUEST","message":_summarize_validation_errors(errors),"details":{"errors":jsonable_encoder(errors)}}}). Live probe via TestClient(main.app): POST /v1/process json={} -> 400 {'error': {'code': 'INVALID_REQUEST', 'message': 'Invalid request body: 4 validation errors, first at body.session_id: Field required', ...}}; POST content=b"{not json" -> 400 same envelope with errors[0].type=='json_invalid'; neither response has a top-level "detail" and neither is 422. 404s unchanged: main.py:292 and :306 still raise HTTPException(status_code=404, detail="Session not found"); probe POST /v1/cancel -> 404 {'detail': 'Session not found'}; `git diff 8e6de10 HEAD --name-only` = only src/h3_shim/templates/py/main.py + tests/test_scaffold_errors.py, so test_battery.py is untouched. tests/test_scaffold_errors.py:5 tests all PASSED (test_missing_required_fields_is_400_invalid_request, test_malformed_json_is_400_invalid_request, test_valid_process_request_still_200_text_decision asserting 200 + decision=='text' + 'Echo: hello', test_deliberate_404_is_untouched, test_400_body_matches_protocol_error_response_schema). Gates run fresh: `.venv/bin/python -m pytest -x --tb=short -q` -> '357 passed, 4 warnings in 19.51s' exit_code=0; `.venv/bin/ruff check .` -> 'All checks passed!' exit_code=0; `.venv/bin/ruff format --check .` -> '48 files already formatted' exit_code=0; `sh scripts/check-test-count.sh` -> 'check-test-count: PASS — canonical compliance-test count is 46; current-state prose agrees' exit_code=0 (scripts/test-count.txt=46, src/h3_shim/test_battery.py:104 EXPECTED_TEST_COUNT = 46).


Overall: PASS ✓
