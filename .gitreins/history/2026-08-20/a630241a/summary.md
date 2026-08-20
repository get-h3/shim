# Verdict: gap-036

**Task:** P2 no wire-shape examples in shim docs
**Evaluated:** 2026-08-20T16:50:35.664563
**Result:** ✓ PASS

## Pipeline Stages

- ✓ **tier1**
  -   ✓ guard: Tier 1 Guards: PASS  (test mode: diff, full suite — safety trigger)
  ✓ secrets — clean
  ✓ lint — o
- ✓ **tier2**
  - COMPLETE
  ✓ a consumer can write a working harness endpoint from the docs without opening protocol.py: docs/api.md (commit 40fb43d) adds a 'Wire protocol (REST / JSON)' section with complete JSON examples: POST /v1/process request (session_id/message/identity/context), Decision responses for 'text' and 'end' plus all other kinds (tool_call, llm_call, wait, delegate), and POST /v1/result request. It explicitly documents the discriminator is top-level 'decision' (NOT 'type') with sub-payloads nesting under the same name. I validated every documented example against the actual Pydantic models in src/h3_shim/protocol.py (ProcessRequest, Decision for all 6 DecisionType kinds, ResultRequest) — all parse successfully with required fields present. Test suite: 315 passed (exit 0). No LSP diagnostics.
The docs now provide accurate, complete wire-shape JSON examples for the full process/result loop, verified against protocol.py, so a consumer can build a working harness endpoint from the docs alone.

## Summary

Judge Result: gap-036

Stage tier1: PASS
    ✓ guard: Tier 1 Guards: PASS  (test mode: diff, full suite — safety trigger)
  ✓ secrets — clean
  ✓ lint — o

Stage tier2: PASS
  COMPLETE
  ✓ a consumer can write a working harness endpoint from the docs without opening protocol.py: docs/api.md (commit 40fb43d) adds a 'Wire protocol (REST / JSON)' section with complete JSON examples: POST /v1/process request (session_id/message/identity/context), Decision responses for 'text' and 'end' plus all other kinds (tool_call, llm_call, wait, delegate), and POST /v1/result request. It explicitly documents the discriminator is top-level 'decision' (NOT 'type') with sub-payloads nesting under the same name. I validated every documented example against the actual Pydantic models in src/h3_shim/protocol.py (ProcessRequest, Decision for all 6 DecisionType kinds, ResultRequest) — all parse successfully with required fields present. Test suite: 315 passed (exit 0). No LSP diagnostics.
The docs now provide accurate, complete wire-shape JSON examples for the full process/result loop, verified against protocol.py, so a consumer can build a working harness endpoint from the docs alone.

Overall: PASS ✓
