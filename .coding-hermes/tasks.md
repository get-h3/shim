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
