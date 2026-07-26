<!--
  ⚠️  BOARD FORMAT — coding-hermes-model-router v1.3 (2026-07-24)
  All tasks MUST use matrix format: | ID | Task | Pri | Cpx | Deps | Tags | Model | Reasoning | Fallback |
  Before editing this file, load the skill: skill_view(name='coding-hermes-model-router')
  Validate: python3 ~/.hermes/scripts/validate-board-format.py .coding-hermes/tasks.md
- [ ] **GITREINS-JUDGE — Configure LLM evaluator for commit quality review**
  | 🔴 Critical | — | — | deepseek-v4-flash @ deepseek-foreman | GITREINS_LLM_API_KEY in ~/.hermes/.env | foreman-direct |

  Run: `python3 ~/.hermes/scripts/check-gitreins-judge.py .` to verify.
  Default limits (adjust per-project based on codebase size and task complexity):
  - Fast/small projects: `max_iterations: 50`, `max_time: 10m`, tokens: `0.2M/0.4M`
  - Large repos (Go monorepos, 100+ files): `max_iterations: 100`, `max_time: 30m`, tokens: `1M/2M`
  - C++/Rust (slow compiles): `max_time: 30m` minimum
  - Scheduler/production infra: `max_time: 30m`, tokens: `1M/2M`
  Supervisor auto-flags projects where limits are too low for codebase size.

| 🔴 Critical | — | — | deepseek-v4-flash @ deepseek-foreman | GITREINS_LLM_API_KEY in ~/.hermes/.env | foreman-direct |

  Run: `python3 ~/.hermes/scripts/check-gitreins-judge.py .` to verify.
  If missing, create/edit .gitreins/config.yaml with evaluator section using deepseek-v4-flash.
  This is CRITICAL for code quality — no automated review of worker output without it.

  NEVER remove the matrix header row or NEVER-DONE / E2E-001 fixtures.
-->

# H3 Shim — Model Router Task Matrix

**Core purpose:** Hermes H3 plugin — bridges Hermes agent loop to external AI harnesses via the H3 protocol. Python, 178 unit tests, GitReins guard PASS. CLI: `hermes h3` (8 subcommands).

## Active Tasks

| ID | Task | Pri | Cpx | Deps | Tags | Model | Reasoning | Fallback |
|----|------|-----|-----|------|------|-------|-----------|----------|
| GITREINS-JUDGE | Configure LLM evaluator for commit quality review | 🔴 Open | 1 | — | ++gitreins, +quality | deepseek-v4-flash | foreman-direct |
| P4-01 | `hermes h3 install` — plugin registration, version check | 🔴 Open | 3 | — | ++cli, +python | DeepSeek V4 Pro | Shim CLI extension | GLM-5.2 |
| P4-02 | `hermes h3 scaffold --lang go/python/ts` — template gen | 🔴 Open | 4 | P4-01 | ++cli, ++code-generation | GLM-5.2 | Template generator | DeepSeek V4 Pro |
| P4-03 | `hermes h3 verify` — post-install verification | 🔴 Open | 2 | P4-01 | ++cli, +testing | DeepSeek V4 Flash | Verification CLI | Step 3.7 Flash |
| P4-05 | Hermes update pre-flight hook (S11 §3) | 🔴 Open | 3 | — | ++cli, +integration | DeepSeek V4 Pro | Upgrade survival hook | GLM-5.2 |
| QV-SHIM-02 | Test report JSON matches TestReport schema | 🔴 Open | 2 | QV-SHIM-01 | ++testing, +format | DeepSeek V4 Flash | Report validation | Step 3.7 Flash |
| QV-SHIM-03 | Shim handles harness timeout gracefully | 🔴 Open | 3 | — | +++resilience, +testing | DeepSeek V4 Pro | Timeout handling | GLM-5.2 |
| QV-SHIM-04 | Health check detects dead harness, falls back to native | 🔴 Open | 3 | — | +++resilience, ++integration | DeepSeek V4 Pro | Health + fallback | GLM-5.2 |
| RES-IMPL-01 | 3 consecutive harness failures → auto-fallback to native | 🔴 Open | 4 | — | +++resilience, ++concurrency | DeepSeek V4 Pro | Resilience circuit breaker | GLM-5.2 |
| RES-IMPL-02 | Circuit breaker: error rate tracking, open at 50% failures | 🔴 Open | 3 | RES-IMPL-01 | ++resilience, +concurrency | DeepSeek V4 Pro | Circuit breaker | GLM-5.2 |
| RES-IMPL-03 | `hermes h3 verify` tests fallback path explicitly | 🔴 Open | 3 | QV-SHIM-04 | ++testing, +integration | DeepSeek V4 Pro | Fallback testing | GLM-5.2 |
| OBS-IMPL-02 | Shim loop logs every hop: process_latency, result_latency, decision_type | Low | 2 | — | ++observability, +python | DeepSeek V4 Flash | Structured logging | Step 3.7 Flash |
| OBS-IMPL-03 | `h3-test --json` report includes latency percentiles | Low | 2 | QV-SHIM-02 | ++observability, +python | DeepSeek V4 Flash | Report enhancement | Step 3.7 Flash |
| DEPS-01 | Package upgrades: 16→18→2 remaining (gitreins 0.11.0 via pipx, pydantic-core pinned by pydantic 2.13.4) | Low | 2 | — | +python, +deps | DeepSeek V4 Flash | 16/18 upgraded tick #76 | Step 3.7 Flash |
| PERF-ND-03 | Zero performance benchmarks — test battery latency tracking | Low | 2 | — | ++performance, +python | Step 3.7 Flash | Benchmark authoring | DeepSeek V4 Flash |
| NEVER-DONE | 11-point audit sweep | High | 2 | — | ++code-review, +testing | DeepSeek V4 Pro | Audit runs every tick | GLM-5.2 |
| E2E-001 | E2E Testing Tick (self-improving loop) 🔁 Every 5-10 ticks | Medium | 3 | — | ++testing, +e2e | Step 3.7 Flash | Playwright/API testing | DeepSeek V4 Pro |

**Assumptions:** Python 3.11+. 178 unit tests pass. GitReins guard PASS. Hilo: 116 edges/18 files. CLI: 8 subcommands (health, process, result, cancel, install, scaffold, verify, test).

**Routing Notes:** P3-10 blocked on PYPI_API_TOKEN (moved to completed). QV-SHIM tasks require live harness endpoint. P4 tasks extend shim CLI. DEPS-01: 16/18 upgraded tick #76, 2 blocked (gitreins via pipx, pydantic-core pinned by pydantic). PERF/OBS are low-priority.

**Execution Order:** DEPS-01 (mechanical) → QV-SHIM tasks → P4 tasks → RES tasks → OBS tasks → PERF-ND-03 → NEVER-DONE.

**Escalation Conditions:** Project idle for 76 ticks. All core implementation complete. Remaining tasks depend on: PYPI_API_TOKEN (Bane), live harness endpoints, or are low-priority. Escalation re-filed.

## Completed

| ID | Task | Pri | Cpx | Commit | Model |
|----|------|-----|-----|--------|-------|
| INIT | Verify project structure, dependencies, DuckBrain namespace | High | 1 | — | DeepSeek V4 Flash |
| SPEC | Audit API surface vs H3 spec, identify gaps | High | 2 | — | DeepSeek V4 Pro |
| CORE-001 | protocol.py: Pydantic models (Hermes-side) | Critical | 4 | ec134f1 | DeepSeek V4 Pro |
| CORE-002 | client.py: H3Client (REST + async) | Critical | 3 | a32ae58 | DeepSeek V4 Pro |
| CORE-003 | loader.py: H3Loader — config, routing, health checks | High | 3 | 8685996 | DeepSeek V4 Pro |
| CORE-004 | shim_loop.py: H3ShimLoop — run, execute, 6 decision executors | Critical | 5 | ab8b574 | DeepSeek V4 Pro |
| CORE-005 | native.py: NativeH3Harness — Hermes native loop adapter | High | 2 | — | Foreman-direct |
| CORE-006 | cli.py: `hermes h3` (8 subcommands) | High | 3 | a9bfd23 | DeepSeek V4 Pro |
| P3-01..09 | Test battery: runner, 6 regions (43 tests), CLI, CI | High | 4 | 0b02c55, 94e82cd | DeepSeek V4 Pro |
| P5-05 | Sync-protocol + PyPI publish pipeline | Medium | 2 | 372b32b | DeepSeek V4 Pro |
| DOC-04 | Missing CONTRIBUTING.md added | Low | 1 | — | DeepSeek V4 Flash |
| QV-SHIM-01 | h3-test 43/43 against live Go harness | High | 3 | — | GitReins verdict: PASS (2026-07-19) |
| QV-CROSS-01 | Scaffold-to-test developer flow | High | 2 | — | GitReins verdict: PASS (2026-07-19) |
| GITREINS-JUDGE | GitReins Tier 2 pipeline configured | Critical | 1 | tick #76 | deepseek-v4-flash |
| DEPS-01 | 16/18 outdated packages upgraded | Low | 2 | tick #76 | DeepSeek V4 Flash |
