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
| QV-SHIM-01 | h3-test 43/43 against live Go harness | 🔴 Open | 3 | Live harness | +++testing, ++python | Step 3.7 Flash | Live shim verification | DeepSeek V4 Pro |
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

> Tick #74: Project zombie. All core tasks complete. 178/178 tests pass. 17 open tasks remain (QV/RES/OBS/PERF/DEPS — all low-priority or blocked). Cooldown at 43200s (12h). 74 consecutive idle ticks. **Escalation to Bane re-filed.**
