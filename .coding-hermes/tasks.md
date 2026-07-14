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
## [ ] CORE-004 — Implement shim_loop.py: H3ShimLoop
- [ ] run(message) → end_reason
- [ ] _build_context() → Context
- [ ] _execute(decision) → ExecutionResult
- [ ] Decision executors: _execute_tool, _execute_llm, _execute_text, _execute_wait, _execute_delegate
- [ ] Max iterations enforcement, error handling
## [ ] CORE-005 — Implement native.py: NativeH3Harness
- [ ] Adapter: Hermes native loop as H3 harness interface
## [ ] CORE-006 — Implement test_battery.py: H3TestBattery
- [ ] 43 compliance tests across 6 categories (per S05)
- [ ] Health & Protocol (7), Process-Basic (8), Decision Types (6), Result Handling (7), Error/Edge (10), Stress (5)
- [ ] TestResult + TestReport dataclasses
- [ ] Terminal + JSON output, CLI entry point (h3-test)
## [ ] CORE-007 — Implement cli.py: hermes h3 subcommands
- [ ] h3 install, h3 uninstall, h3 verify, h3 list
- [ ] h3 test --endpoint URL, h3 scaffold NAME --lang, h3 route, h3 use
## [ ] TEST — Unit tests for all components
- [ ] test_protocol.py: model validation, serialization
- [ ] test_client.py: mock HTTP, error handling
- [ ] test_loader.py: config parsing, routing
- [ ] test_shim_loop.py: decision execution, iteration limits
- [ ] test_cli.py: command parsing
## [ ] CI — Enable GitReins guards + CI pipeline
- [ ] Enable lint, build, tests in .gitreins/config.yaml
- [ ] .github/workflows/test.yml for CI
- [ ] make all target: install lint build test
