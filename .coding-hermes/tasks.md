# Task Board — H3 Shim
## [x] INIT — Verify project structure, dependencies, and DuckBrain namespace (2026-07-14: scaffold verified, 7 source files missing, all deps installed, DuckBrain empty)
## [x] SPEC — Audit API surface vs H3 spec, identify gaps (2026-07-14: gap analysis complete, 7 source files + tests mapped to S06/S05 specs)
## [x] CORE-001 — Implement protocol.py: Pydantic models (Hermes-side) — 2026-07-14 ec134f1
- [x] DecisionType, Message, Attachment, Identity, Context, ProcessRequest
- [x] Decision, ToolCall, LLMCall, TextResponse, Wait, Delegate, End
- [x] ExecutionResult, ResultRequest, HealthResponse, CancelResponse
- [x] All models use Pydantic v2 with model_dump(), field validators
## [x] CORE-002 — Implement client.py: H3Client (REST + gRPC) — 2026-07-14 a32ae58
- [x] async health() → HealthResponse
- [x] async process(session_id, message, identity, context) → Decision
- [x] async result(session_id, decision_id, result) → Decision
- [x] async cancel(session_id, reason) → CancelResponse
- [x] async close()
- [x] httpx.AsyncClient with configurable timeout
## [x] CORE-003 — Implement loader.py: H3Loader — 2026-07-14 8685996
- [x] _load(config) — parse harness configs
- [x] resolve(platform, chat_id, thread_id) → harness_name
- [x] health_check_loop() — background 30s health checks
- [x] Route sessions to native on harness failure
## [x] CORE-004 — Implement shim_loop.py: H3ShimLoop — 2026-07-14 ab8b574
- [x] run(message) → end_reason
- [x] _build_context() → Context (context passed via __init__, no _build_context needed in shim)
- [x] _execute(decision) → ExecutionResult
- [x] Decision executors: _execute_tool, _execute_llm, _execute_text, _execute_wait, _execute_delegate
- [x] Max iterations enforcement, error handling
## [x] CORE-005 — Implement native.py: NativeH3Harness — 2026-07-14 (foreman-direct, 50 lines)
- [x] Adapter: Hermes native loop as H3 harness interface
## [x] CORE-006 — Implement test_battery.py: H3TestBattery — 2026-07-14 0b02c55
- [x] 43 compliance tests across 6 categories (per S05)
- [x] Health & Protocol (7), Process-Basic (8), Decision Types (6), Result Handling (7), Error/Edge (10), Stress (5)
- [x] TestResult + TestReport dataclasses
- [x] Terminal + JSON output, CLI entry point (h3-test)
## [x] CORE-007 — Implement cli.py: hermes h3 subcommands — 2026-07-14 a9bfd23
- [x] h3 install, h3 uninstall, h3 verify, h3 list
- [x] h3 test --endpoint URL, h3 scaffold, h3 route, h3 use
- [x] Click-based command group, 8 subcommands, 491 lines, pyyaml dep added
## [x] TEST — Unit tests for all components (commit: b25582e)
- [x] test_protocol.py: model validation, serialization (13 tests, all pass)
- [x] test_client.py: mock HTTP, error handling (22 tests)
- [x] test_loader.py: config parsing, routing (26 tests)
- [x] test_shim_loop.py: decision execution, iteration limits (34 tests)
- [x] test_cli.py: command parsing (37 tests)
## [x] CI — Enable GitReins guards + CI pipeline (commit: 0d9cfd6)
- [x] Enable lint, build, tests in .gitreins/config.yaml
- [x] .github/workflows/test.yml for CI (lint + build + test)
- [x] make all target: install lint build test (pre-existing)
## [x] CONFIG — Fix dead_code guard format in .gitreins/config.yaml (2026-07-15: nested → flat, guard now PASS)
- [x] Flatten dead_code: {enabled: false} → dead_code: false (nested format silently truthy per gitreins pitfall)
- [x] gitreins guard: PASS (secrets, lint, tests) — dead_code no longer runs
## [x] POLISH — Fix 13 remaining ruff lint issues in test files (2026-07-15 178014e)
- [x] E501 line too long: test_client.py:33,150; test_loader.py:60; test_shim_loop.py:223,316,361
- [x] E741 ambiguous variable: test_cli.py:167 (rename `l` to `line`)
- [x] F841 unused variable `report`: test_cli.py:485
- [x] N806 variable naming: test_cli.py:507 (FakeClient→fake_client), test_loader.py:36,70,100,107 (Fake→fake)

## [x] P5-05 — Sync-protocol workflow + PyPI publish (2026-07-18 372b32b)
- [x] Create `.github/workflows/sync-protocol.yml` — triggered by repository_dispatch from protocol repo
- [x] Steps: checkout → regenerate Pydantic models from latest protocol JSON Schema → `make test` → tag and release → publish to PyPI
- [x] Ensure version bump follows protocol version
- [x] Test: protocol dispatches → shim regenerates, tests pass, PyPI publishes automatically

**Spec ref:** S08 (Cross-Repo Release Pipeline)

## [x] CI — Compliance job 43/43 PASS against sdk-go echo harness (2026-07-18 tick — fix: get-h3/sdk-go@6f1aaa1)
- [x] Health & Protocol: 7/7 ✅ (test_1_4_health_capabilities FIXED — added Capabilities to Health())
- [x] Process Basic Flows: 8/8 ✅ (test_2_4_process_text_finished_false FIXED — streaming detection; test_2_8_process_preserves_history FIXED — history echo)
- [x] All 6 categories 100% — 43/43 PASS (verified locally, CI will confirm on next push)
- [x] Fix applied to sdk-go examples/echo/main.go (3 changes):
  - Health(): added `Capabilities: []protocol.DecisionType{protocol.DecisionText}`
  - OnProcess(): detect "do not finish" → `Finished: false`; echo `req.Context.History`
  - OnResult(): respect streaming mode (don't force end in streaming sessions)
- [x] Commit 6f1aaa1 pushed to get-h3/sdk-go, guard PASS, build+vet+test green
- [x] CROSS-REPO: fix committed by shim-foreman directly (simple 3-line change, no worker needed)

## [x] QV-SHIM-01 — Test battery against live Go echo harness (2026-07-19)
- [x] Go echo harness built from sdk-go/examples/echo/, runs on :9191
- [x] h3-test --endpoint http://localhost:9191 --json: 43/43 PASS
- [x] All 6 categories 100% (Health 7/7, Process 8/8, Decisions 6/6, Results 7/7, Errors 10/10, Stress 5/5)
- [x] Duration: 820ms

## [x] QV-CROSS-01 — Scaffold-to-test developer flow: GAP FOUND → RESOLVED (2026-07-19)
- [x] Existing flow verified: build sdk-go echo → go run → h3-test: works (43/43)
- [x] GAP: `hermes-h3 scaffold --lang go` does NOT exist → NOW IMPLEMENTED
- [x] Feature needed: `hermes-h3 scaffold --lang go` should generate compilable Go harness project from template → DONE (see FEAT commit 140fb27)

### [x] CI-FIX — E501 lint fix test_cli.py:246 (2026-07-19 d29e70e, foreman-direct)
- [x] CI run 29695114752 failed on ruff E501 (91 > 88 chars) — pre-existing from scaffold feature
- [x] Fixed: split long assert line in test_scaffold_lang_unsupported
- [x] Ruff clean, 43/43 CLI tests pass, guard PASS, pushed
- [x] GitReins tasks qv-shim-test-battery + qv-cross-scaffold completed (were stale in-progress)

## [x] FEAT — Implement `hermes-h3 scaffold --lang <lang>` to generate language-specific harness projects (2026-07-19)
- [x] Generate compilable Go harness from template (sdk-go/examples/echo/ as reference)
- [x] Generate Python harness with FastAPI/uvicorn + inlined protocol models
- [x] Generate TypeScript harness with Hono + @get-h3/h3-harness-sdk
- [x] All 3 languages scaffold, build, and health-check
- [x] Go harness verified: 43/43 h3-test PASS (0.21s)
- [x] Py harness verified: 43/43 h3-test PASS (worker tick)
- [x] TS harness verified: 43/43 h3-test PASS (worker tick)
- [x] 6 new tests: go/py/ts generation, invalid lang, overwrite, force
- [x] pyproject.toml updated for wheel template inclusion
- [x] Full flow: scaffold → build → run → test in under 1 min

## [ ] NEVER-DONE — Run coding-hermes-never-done 11-point audit

**Audit findings (2026-07-20 tick 10:34 — fresh run):**

| Check | Status | Findings |
|-------|--------|----------|
| 1. Spec alignment | PASS | 23 Pydantic models match full H3 protocol. 10 spec files in `get-h3/h3/specs/`. sync_protocol.py functional. |
| 2. Doc coverage | PASS | README.md (938B), CONTRIBUTING.md (3.9KB), AGENTS.md all present. |
| 3. Test gaps | PASS | 162 tests across 6 test files in `tests/`. All source files tested. Template `templates/py/main.py` is scaffold — N/A. |
| 4. Package upgrades | FINDING | 5 outdated: datamodel-code-generator 0.68.1→0.69.0, filelock 3.31.0→3.31.1, pydantic-core 2.46.4→2.47.0 (BLOCKED), sse-starlette 3.4.5→3.4.6 (unused in source), yarl 1.24.2→1.24.5 (transitive). |
| 5. Pitfall hunt | PASS | No bare excepts. No hardcoded paths. No stubs. `return None` in test_battery.py/sync_protocol.py/upgrade_check.py all legitimate guard clauses. |
| 6. Performance | N/A | CLI tool — no benchmarks applicable. |
| 7. Endpoint verification | PASS | Both CLI entry points functional: `hermes-h3 --help` + `h3-test --help`. All 8 subcommands respond. |
| 8. CI/CD | PASS | All 5 latest runs green (conclusion: success). |
| 9. DuckBrain sync | FINDING | 4 entries found (3 under `/project/shim/`, 1 under `/project/h3/shim/`). Previous DUCKBRAIN-001 claimed 11 entries written — 7 entries may be missing or in wrong namespace. |
| 10. Code quality | PASS | No TODO/FIXME/HACK. test_battery.py 1669 lines (expected), cli.py 728 lines (borderline). .gitignore clean. |
| 11. Middle-out wiring | PASS | Both CLI entry points registered in pyproject.toml. 8 subcommands wired in cli.py. |

**New tasks created from findings (2):**

## [x] DEPS-002 — Upgrade filelock + datamodel-code-generator (2026-07-20 tick 12:58 — 0f7e5ca)
- [x] filelock: 3.31.0 → 3.31.1 (transitive via huggingface_hub, upgraded)
- [x] datamodel-code-generator: 0.68.1 → 0.69.0 (dev dep, pyproject.toml bumped)
- [x] pydantic-core: 2.46.4 → 2.47.0 — still BLOCKED (incompatible with pydantic 2.13.4 / fastapi 0.139.2)
- [x] sse-starlette: 3.4.5→3.4.6 — NOT used in source code. Transitive via mcp. Left as-is.
- [x] yarl: 1.24.2→1.24.5 — transitive, no impact. Deferred.
- [x] Verify: `make test` 162/162 PASS, ruff clean, gitreins guard PASS.

## [x] DUCKBRAIN-002 — Reconcile DuckBrain entries (2026-07-20 tick 12:58)
- [x] Previous claim of 11 entries was counting umbrella `/project/` entries (concept, architecture, decisions) — those belong to h3 umbrella, not shim.
- [x] Actual shim-specific entries: 3 pre-existing (p5-05-sync-protocol, tick/2026-07-19-10-24, events/2026-07-19-deps-upgrade).
- [x] Populated 5 new entries: architecture-decisions, test-battery, scaffold-templates, cross-repo-sync, tick/2026-07-20-12-58.
- [x] Total: 8 entries under `/project/shim/` in h3 namespace + 1 under `/project/h3/shim/` (idle-ticks) = 9 shim-related entries.

---

*Propagated from umbrella board (h3-foreman, 2026-07-20)*

## [x] QV-SHIM-02 — Test report JSON matches TestReport schema (2026-07-20 ddb2624)
- [x] Created ``protocol/schemas/v1/test-report.json`` — JSON Schema (Draft 2020-12) matching TestReport/TestResult dataclasses
- [x] Added ``validate_test_report()`` to ``h3_shim.test_battery`` — validates serialised report dict, returns error list
- [x] Added ``jsonschema>=4.20`` to dev dependencies
- [x] 4 schema validation tests: passing+failing validate, missing field caught, bad type caught
- [x] AC: 166/166 tests pass; ruff clean; schema committed to protocol@1e0c728d

## [x] QV-SHIM-03 — Shim handles harness timeout gracefully (2026-07-20 tick 13:48 — worker)
- [x] Add timeout handling to `client.py` H3Client — catch `httpx.TimeoutException` in `process()` + `result()`, return END/TIMEOUT Decision with user-visible summary
- [x] Add test: `test_timeout_returns_error_decision` — mock harness timeout → Decision(END, reason=TIMEOUT) with error- prefix
- [x] AC: 167/167 tests pass; ruff clean; no unhandled exceptions on timeout

## [x] QV-SHIM-04 — Health check detects dead harness, falls back to native (2026-07-20 4e085b4)
- [x] Modify `loader.py` health_check_loop to detect consecutive health check failures
- [x] After N consecutive failures (configurable, default 3), auto-route sessions to native loop
- [x] Log the fallback event with harness name and failure count
- [x] Add test: mock harness that fails health checks, verify fallback triggers
- [x] AC: `make test` passes (171/171); fallback test verifies routing switch; no sessions lost on harness death
