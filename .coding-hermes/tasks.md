
## Dogfood Findings (2026-09-05)
Verdict: SHIPPABLE
Promise: install from source → scaffold harness (py/go/ts) → verify with the 45-test battery → drive real H3 sessions through H3ShimLoop (the brain-swap).

Install proven on a clean machine this cycle (ephemeral bunker, las-bunker-03): clone from documented origin → 14s shim install → 7s harness setup → 45/45 battery in 0.61s, exit 0. No sudo/compose/hidden deps.

- [P1] Wire-shape reference absent from shim docs: building a custom multi-decision harness from docs/api.md alone, llm_call.model crashed as "should be a valid string" (it's a plain string, not an object) and wait crashed on required `reason` + int `duration_seconds` — shapes live only in src/h3_shim/protocol.py + get-h3/protocol schemas (other repo); docs/api.md resolve() signature also drifted (session_id vs actual thread_id=None). Fix: one JSON example per decision type in docs/api.md + signature fix. (Board: DF2-H3-SHIM-1)
- [P1] Foreman cannot work its own board: 9 ticks on DF-H3-SHIM-FOREMAN-1 since 09-01 (events 315-325) with verdicts REJECTED/NO_CHANGES/dry-run-null, zero code changes — while the P1 is hand-reproducible in minutes (scaffold 405s documented GET /v1/sessions/{id}; battery still says 45/45). Re-scope the P1s into single-commit tasks with exact file+assertion pointers, or investigate foreman worker health. (Board: DF2-H3-SHIM-4)
- [P2] Malformed decision payloads collapse to opaque EndReason "error": pydantic ValidationError is log-only; the harness author sees FINAL: error with no field/message. Fix: surface decision_id + first error line (or on_error callback). (Board: DF2-H3-SHIM-2)
- [P2] Scaffolded harness never GCs session state: active_sessions grew to 97 over 26h on a leftover instance; END never purges; only DELETE /v1/sessions/{id} does — unbounded growth + meaningless health metric; stale instance also silently owns :9191. Fix: purge on END (or TTL/LRU) in the scaffold template. (Board: DF2-H3-SHIM-3)
- [P2] INSTALL-h3-shim: fresh install on ephemeral bunker (las-bunker-03) took 14s (shim) + 7s (harness scaffold setup); smoke passed — 45/45 battery, exit 0, on bare Debian container from documented path only.
- [P2] SKIPPED-install-bunker — none; bunker leg completed (agent aa13a24b spawned, used, destroyed).
- Regression check: all five 09-01 DF tasks (DF-H3-SHIM-FOREMAN-1..5) re-verified live — still open, none worked by the foreman in 4 days. GAP-034 (LLMCall refusal) and GAP-035 (text log-only) are FIXED in code (llm_provider + on_text hooks) but docs/api.md still documents the old behavior.
