# Verdict: df-h3-shim-foreman-2-t378

**Task:** README ProcessRequest payload example
**Evaluated:** 2026-09-18T21:43:02.022922
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ guard: Tier 1: DEGRADED PASS (skips: lint=no staged files, tests=no staged files)  (test mode: diff, full s
- ✓ **tier2**
  - COMPLETE
  ✓ README quickstart includes a valid ProcessRequest JSON payload with session_id, message role/content/timestamp, identity platform/chat_id/user_name/user_id, and context history/tools/models/config/session_state; tests or documented verification prove the fields are accurate.: README.md:58 '### Send a request' contains the payload at README.md:65-118. Extracted and parsed: top-level keys [session_id, message, identity, context]; message [role, content, timestamp]; identity [platform, chat_id, user_name, user_id]; context [history, tools, models, config, session_state]; config [max_iterations, timeout_seconds]; session_state [turn_count, total_tool_calls, total_llm_calls, cost_so_far, started_at]. Validated OK against Pydantic h3_shim.protocol.ProcessRequest.model_validate and against the canonical authored schema ../protocol/schemas/v1/process-request.json + common.json via Draft202012Validator ('README payload VALID against authored process-request.json schema'). README.md:62-63 documents the source of truth (get-h3/protocol -> schemas/v1/process-request.json and common.json) and README.md:120-124 correctly lists optional fields (message.attachments, identity.thread_id, context.memory, context.skills, context.config.* beyond the two required keys), matching the schema required lists exactly. Field accuracy is proven by tests/test_battery_payload_schema.py, which validates the identical field set against the same authored schemas (7 passed); full suite `.venv/bin/python -m pytest -x --tb=short -q` -> '352 passed in 17.31s' (exit 0).
README quickstart includes a ProcessRequest payload that validates against both the Pydantic model and the canonical authored JSON schema, with field accuracy backed by the documented schema pointer and the passing schema-validation test suite.

## Summary

Judge Result: df-h3-shim-foreman-2-t378

Stage tier1: PASS
    ✓ guard: Tier 1: DEGRADED PASS (skips: lint=no staged files, tests=no staged files)  (test mode: diff, full s

Stage tier2: PASS
  COMPLETE
  ✓ README quickstart includes a valid ProcessRequest JSON payload with session_id, message role/content/timestamp, identity platform/chat_id/user_name/user_id, and context history/tools/models/config/session_state; tests or documented verification prove the fields are accurate.: README.md:58 '### Send a request' contains the payload at README.md:65-118. Extracted and parsed: top-level keys [session_id, message, identity, context]; message [role, content, timestamp]; identity [platform, chat_id, user_name, user_id]; context [history, tools, models, config, session_state]; config [max_iterations, timeout_seconds]; session_state [turn_count, total_tool_calls, total_llm_calls, cost_so_far, started_at]. Validated OK against Pydantic h3_shim.protocol.ProcessRequest.model_validate and against the canonical authored schema ../protocol/schemas/v1/process-request.json + common.json via Draft202012Validator ('README payload VALID against authored process-request.json schema'). README.md:62-63 documents the source of truth (get-h3/protocol -> schemas/v1/process-request.json and common.json) and README.md:120-124 correctly lists optional fields (message.attachments, identity.thread_id, context.memory, context.skills, context.config.* beyond the two required keys), matching the schema required lists exactly. Field accuracy is proven by tests/test_battery_payload_schema.py, which validates the identical field set against the same authored schemas (7 passed); full suite `.venv/bin/python -m pytest -x --tb=short -q` -> '352 passed in 17.31s' (exit 0).
README quickstart includes a ProcessRequest payload that validates against both the Pydantic model and the canonical authored JSON schema, with field accuracy backed by the documented schema pointer and the passing schema-validation test suite.

Overall: PASS ✓
