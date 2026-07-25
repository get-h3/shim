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

**Core purpose:** Hermes H3 plugin — bridges Hermes agent loop to external AI harnesses via the H3 protocol. Python, 151 unit tests, 43/43 test battery passes. CLI: `hermes h3` (8 subcommands).

## Active Tasks

- [ ] **E2E-001 — E2E Testing Tick (self-improving loop)** 🔁 Every 5-10 ticks
  Spawn Luna (browser/screenshots) or Step 3.7 Flash (CLI/API). Deploy/build, Playwright, screenshots, endpoints, console. → e2e-output/tasks.md → inject into board.

| ID | Task | Pri | Cpx | Deps | Tags | Model | Reasoning | Fallback |
|----|------|-----|-----|------|------|-------|-----------|----------|
| P3-10 | Publish `hermes-h3-shim` to PyPI | 🔴 BLOCKED | 1 | PYPI_API_TOKEN | ++devops, +python | — | Blocked: needs PYPI_API_TOKEN | — |
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
| DEPS-01 | 16 Python packages outdated | Low | 2 | — | +python, +deps | DeepSeek V4 Flash | Mechanical upgrades | Step 3.7 Flash |
| PERF-ND-03 | Zero performance benchmarks — test battery latency tracking | Low | 2 | — | ++performance, +python | Step 3.7 Flash | Benchmark authoring | DeepSeek V4 Flash |
| NEVER-DONE | 11-point audit sweep | High | 2 | — | ++code-review, +testing | DeepSeek V4 Pro | Audit runs every tick | GLM-5.2 |

**Assumptions:** Python 3.11+. 151 unit tests pass. 43/43 test battery passes. CLI: 8 subcommands (health, process, result, cancel, install, scaffold, verify, test). Pydantic v2 models.

**Routing Notes:** P3-10 blocked on PYPI_API_TOKEN. QV-SHIM tasks require live harness endpoint. P4 tasks extend shim CLI. DEPS/PERF/OBS are low-priority mechanical tasks. Use DeepSeek V4 Flash or Step 3.7 Flash for mechanical tasks.

**Execution Order:** DEPS-01 (mechanical) → QV-SHIM tasks → P4 tasks → RES tasks → OBS tasks → PERF-ND-03 → NEVER-DONE.

**Escalation Conditions:** P3-10 blocked on PYPI_API_TOKEN — Bane action. QV tasks require live harness. Cooldown at 43200s (12h) — tick #73 zombie, escalation pending.

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

> Tick #75: Board staleness corrected — QV-SHIM-01 and QV-CROSS-01 were complete in GitReins since 2026-07-19 but board showed Open. SECURITY.md + CHANGELOG.md created. 11-point audit: 10/11 PASS (1 recurring: P3-10 blocked on PYPI_API_TOKEN). 178/178 tests pass. Idle ticks reset to 1. Cooldown: 900s.
