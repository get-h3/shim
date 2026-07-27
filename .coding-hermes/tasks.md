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

**Core purpose:** Hermes H3 plugin — bridges Hermes agent loop to external AI harnesses via the H3 protocol. Python, 225 unit tests, GitReins guard PASS. CLI: `hermes h3` (8 subcommands).

## Active Tasks

| ID | Task | Pri | Cpx | Deps | Tags | Model | Reasoning | Fallback |
|----|------|-----|-----|------|------|-------|-----------|----------|
| GITREINS-JUDGE | Configure LLM evaluator for commit quality review | 🟢 Done | 1 | tick #76 | ++gitreins, +quality | deepseek-v4-flash | foreman-direct |
| P4-01 | `hermes h3 install` — plugin registration, version check | 🟢 Done | 3 | — | ++cli, +python | DeepSeek V4 Pro | Shim CLI extension — install/verify/scaffold/pre-update-check all implemented cli.py tick #79 | GLM-5.2 |
| P4-02 | `hermes h3 scaffold --lang go/python/ts` — template gen | 🟢 Done | 4 | P4-01 | ++cli, ++code-generation | GLM-5.2 | Template generator — scaffold command, 3 template dirs tick #79 | DeepSeek V4 Pro |
| P4-03 | `hermes h3 verify` — post-install verification | 🟢 Done | 2 | P4-01 | ++cli, +testing | DeepSeek V4 Flash | Verification CLI — h3-test, hermes-h3 verify via H3Client health() tick #79 | Step 3.7 Flash |
| P4-05 | Hermes update pre-flight hook (S11 §3) | 🟢 Done | 3 | — | ++cli, +integration | DeepSeek V4 Pro | Upgrade survival hook — upgrade_check.py, pre_update_check_cmd cli.py tick #79 | GLM-5.2 |
| QV-SHIM-02 | Test report JSON matches TestReport schema | 🟢 Done | 2 | QV-SHIM-01 | ++testing, +format | DeepSeek V4 Flash | Report validation — 4 tests PASS tick #77; validates against real schema at protocol/schemas/v1/test-report.json | Step 3.7 Flash |
| QV-SHIM-03 | Shim handles harness timeout gracefully | 🟢 Done | 3 | — | +++resilience, +testing | DeepSeek V4 Pro | Timeout handling tick #78 — 7 timeout unit tests PASS, max_iterations/max_polls/poll_timeout in shim_loop.py, test_4_7 wait_timeout in test_battery | GLM-5.2 |
|| QV-SHIM-04 | Health check detects dead harness, falls back to native | 🟢 Done | 3 | — | +++resilience, ++integration | DeepSeek V4 Pro | Health + fallback — loader.py health_check_loop + 33 tests tick #79 | GLM-5.2 |
| RES-IMPL-01 | 3 consecutive harness failures → auto-fallback to native | 🟢 Done | 4 | — | +++resilience, ++concurrency | DeepSeek V4 Pro | Health fallback — loader.py health_check_loop max_consecutive_failures=3, _reroute_sessions, tested tick #79 | GLM-5.2 |
| RES-IMPL-02 | Circuit breaker: error rate tracking, open at 50% failures | 🟢 Done | 3 | RES-IMPL-01 | ++resilience, +concurrency | DeepSeek V4 Pro | Circuit breaker + 35 unit+integration tests PASS tick #79 — CircuitBreaker class in loader.py | GLM-5.2 |
|| RES-IMPL-03 | `hermes h3 verify` tests fallback path explicitly | 🟢 Done | 3 | QV-SHIM-04 | ++testing, +integration | DeepSeek V4 Pro | Fallback testing — --fallback flag, _report_fallback() ENGAGED/STANDBY, 2 tests tick #80 | GLM-5.2 |
| OBS-IMPL-02 | Shim loop logs every hop: process_latency, result_latency, decision_type | Low | 2 | — | ++observability, +python | DeepSeek V4 Flash | Structured logging | Step 3.7 Flash |
| OBS-IMPL-03 | `h3-test --json` report includes latency percentiles | Low | 2 | QV-SHIM-02 | ++observability, +python | DeepSeek V4 Flash | Report enhancement | Step 3.7 Flash |
| DEPS-01 | Package upgrades: 17/18 done (gitreins 0.11.0 ✅ tick #77, pydantic-core 2.47.0 blocked by pydantic 2.13.4 — verified tick #82) | Low | 2 | — | +python, +deps | DeepSeek V4 Flash | 17/18 upgraded tick #77 — pydantic-core 2.47.0 still blocked by pydantic 2.13.4 constraint tick #82 | Step 3.7 Flash |
| PERF-ND-03 | Zero performance benchmarks — test battery latency tracking | Low | 2 | — | ++performance, +python | Step 3.7 Flash | Benchmark authoring | DeepSeek V4 Flash |
| NEVER-DONE | 11-point audit sweep | 🔵 PASS | 2 | — | ++code-review, +testing | DeepSeek V4 Pro | 11/11 PASS tick #84 — clean repo, 225/225 tests, GitReins PASS, Hilo 139e/26f | GLM-5.2 |
| E2E-001 | E2E Testing Tick (self-improving loop) 🔁 Every 5-10 ticks | Medium | 3 | — | ++testing, +e2e | Step 3.7 Flash | Playwright/API testing — tick #85 due | DeepSeek V4 Pro |

**Assumptions:** Python 3.11+. 225 unit tests pass. GitReins guard PASS. Hilo: 139 edges/26 files. CLI: 8 subcommands (health, process, result, cancel, install, scaffold, verify, test) + pre-update-check. QV-SHIM-02/03/04 Done. RES-IMPL-01/02/03 Done. P4-01/02/03/05 Done. DEPS-01: 17/18 upgraded, 1 blocked (pydantic-core 2.47.0 — pydantic 2.13.4 is latest, incompatible).

**Routing Notes:** QV-SHIM-03/04 Done tick #78-79 (health fallback verified). P4 tasks Done tick #79 (all CLI commands implemented). RES-IMPL-01/02/03 Done tick #79-80. DEPS-01: 17/18 upgraded tick #77 (gitreins 0.11.0 ✅), pydantic-core blocked by pydantic 2.13.4 (verified tick #82). CI-FIX-RUFF Done tick #81 (ruff 0.16.0 lint fixes). DEP-GROUPS-FIX Done tick #82 (missing build/jsonschema/ruff added to [dependency-groups]). PERF/OBS are low-priority. E2E-001 due ~tick #85 (every 5-10 ticks). No open tasks — maintenance mode.

**Execution Order:** PROGRESS: QV-SHIM-04 → RES-IMPL-01 → P4 tasks all Done tick #79 → RES-IMPL-02 Done tick #79. REMAINING: RES-IMPL-03 → OBS tasks → PERF-ND-03 → NEVER-DONE.

**Escalation Conditions:** Core implementation complete. All RES-IMPL tasks Done tick #80. Remaining tasks: PYPI_API_TOKEN (Bane), low-priority maintenance (OBS-IMPL-02/03, PERF-ND-03), or blocked deps (pydantic-core). Cooldown: 900s.

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
| P4-01..05 | CLI: install, scaffold, verify, pre-update-check | 🟢 Done | 3 | — | DeepSeek V4 Pro |
| QV-SHIM-04 | Health check detects dead harness, falls back to native | 🟢 Done | 3 | 4e085b4 | DeepSeek V4 Pro |
| RES-IMPL-01 | 3 consecutive failures → auto-fallback to native | 🟢 Done | 4 | 4e085b4 | DeepSeek V4 Pro |
| RES-IMPL-02 | Circuit breaker + 35 tests (sliding window, cooldown, half-open probe) | 🟢 Done | 3 | tick #79 | DeepSeek V4 Pro |
| RES-IMPL-03 | `hermes h3 verify` --fallback tests fallback path explicitly | 🟢 Done | 3 | tick #80 | DeepSeek V4 Pro |
|| CI-FIX-RUFF | Fix ruff 0.16.0 lint failures (unused imports, trailing newline) | 🟢 Done | 1 | tick #81 | DeepSeek V4 Flash |

## Tick Log

### Tick #83 — 2026-07-27 04:58 UTC (DeepSeek V4 Flash)

| # | Gate | Result | Detail |
|---|------|--------|--------|
| 1 | Git status | ✅ PASS | Clean workdir |
| 2 | GitReins tasks | ✅ PASS | 2 tasks complete — in sync with board |
| 3 | Hilo graph | ✅ PASS | 139 edges / 26 files |
| 4 | Tests | ✅ PASS | 225/225 in 1.47s |
| 5 | TODO/FIXME | ✅ PASS | None found |
| 6 | Deps check | ✅ PASS | pydantic-core 2.47.0 still blocked by pydantic 2.13.4 (known, DEPS-01) |
| 7 | GitReins config | ✅ PASS | Config valid (Tier 1 + Tier 2, evaluator 50iter/10m/0.2M) |
| 8 | GitReins guard | ✅ PASS | secrets ✅ lint ✅ (clean workdir) |
| 9 | Static analysis | ⚠️ SKIP | mypy not installed in venv |
| 10 | Board consistency | ✅ PASS | Dual-source: board ✅ GitReins ✅ — in sync |
| 11 | Dispatch | ⏭️ DEFER | All tasks Done, maintenance mode. Load 20.14 — high |

**Verdict:** IDLE — All gates green. Project in maintenance mode. Scheduler cooldown: 2700s. Host load high (20.14). No dispatch warranted. 4 low-priority items remain (OBS-IMPL-02/03, PERF-ND-03, DEPS-01).

### Tick #84 — 2026-07-27 08:10 UTC (DeepSeek V4 Pro)

| # | Gate | Result | Detail |
|---|------|--------|--------|
| 1 | Git status | ✅ PASS | Clean workdir |
| 2 | GitReins guard | ✅ PASS | secrets ✅ lint ✅ (no staged files) |
| 3 | Hilo graph | ✅ PASS | 139 edges / 26 files |
| 4 | Tests | ✅ PASS | 225/225 in 1.43s |
| 5 | TODO/FIXME | ✅ PASS | None found |
| 6 | Deps check | ✅ PASS | fastapi 0.140.0→0.140.1 (minor); pydantic-core 2.46.4→2.47.0 blocked — pydantic 2.13.4 is latest, incompatible (known DEPS-01) |
| 7 | GitReins config | ✅ PASS | Config valid (Tier 1 + Tier 2, evaluator 50iter/10m/0.2M/0.4M) |
| 8 | Secrets | ✅ PASS | Covered by Gate 2 — GitReins secrets clean |
| 9 | Static analysis | ✅ PASS | ruff: All checks passed; mypy: not installed (consistent) |
| 10 | Board consistency | ✅ PASS | Dual-source: GitReins 2/2 complete, board in sync. Scheduler CooldownS=2700 matches |
| 11 | Dispatch | ⏭️ DEFER | All tasks Done. 3 low-priority + 1 blocked dep. E2E-001 due ~tick #85. Maintenance mode. |

**Verdict:** IDLE — All gates green. Project in maintenance mode. Scheduler cooldown: 2700s. No dispatch warranted. 3 low-priority items remain (OBS-IMPL-02/03, PERF-ND-03) + DEPS-01 blocked (pydantic-core 2.47.0 incompatible with pydantic 2.13.4 latest). E2E-001 due next tick (#85).

### Tick #85 — 2026-07-27 08:58 UTC (DeepSeek V4 Pro)

| # | Gate | Result | Detail |
|---|------|--------|--------|
| 1 | Git status | ✅ PASS | Clean workdir |
| 2 | GitReins guard | ✅ PASS | secrets ✅ lint ✅ (no staged files) |
| 3 | Hilo graph | ✅ PASS | 139 edges / 26 files |
| 4 | Tests | ✅ PASS | 225/225 in 1.56s |
| 5 | TODO/FIXME | ✅ PASS | None found (only _audit.py script) |
| 6 | Deps check | ✅ PASS | fastapi 0.140.0→0.140.1 (minor); pydantic-core 2.47.0 blocked by pydantic 2.13.4 (known DEPS-01) |
| 7 | GitReins config | ✅ PASS | Config valid (Tier 1 + Tier 2, 50iter/10m/0.2M/0.4M) |
| 8 | Ruff lint | ✅ PASS | All checks passed |
| 9 | Static analysis | ⚠️ SKIP | mypy not installed (consistent) |
| 10 | Board consistency | ✅ PASS | Dual-source: GitReins 2/2 complete, board in sync |
| 11 | E2E-001 dispatch | ✅ PASS | 43/43 tests PASS against Go echo harness in 0.17s — all 6 regions green |

**Verdict:** IDLE — All gates green. E2E-001 executed: 43/43 compliance tests passed against live Go echo harness. Project in maintenance mode. Scheduler cooldown: 2700s. 3 low-priority items remain (OBS-IMPL-02/03, PERF-ND-03) + DEPS-01 blocked (pydantic-core 2.47.0 incompatible with pydantic 2.13.4 latest). E2E-001 due ~tick #90.
