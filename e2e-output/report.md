# H3 Shim — E2E-001 Report (tick #167, 2026-08-02)

Compliance battery (`h3-test`) against all three SDK echo harnesses, run
foreman-direct (CLI/plugin project — no browser surface).

## Environment

| Item | Value |
|---|---|
| Battery | `h3-test` (43 tests, 6 categories: health, process, decisions, results, errors, stress) |
| Shim | `hermes-h3-shim` HEAD `13bae0f` (clean, no drift from origin/main) |
| Harnesses | Go SDK echo `:9191`, Python SDK echo `:9192`, TypeScript SDK echo `:9193` |
| Unit suite | 242/242 passed (1.60s) |
| GitReins guard | Tier 1 PASS (secrets, lint, tests) |
| Spec sync | `sync_protocol.py --diff` exit 0 — standing state 87 schema fields / 95 model fields (8 intentional extra optional fields) |
| CI | 5/5 success (latest tick #166 commit) |

## Results

| Harness | Endpoint | Tests | Passed | Failed | Duration | all_passing |
|---|---|---|---|---|---|---|
| Go SDK echo | `http://localhost:9191` | 43 | 43 | 0 | 225.2 ms | ✅ |
| Python SDK echo | `http://localhost:9192` | 43 | 43 | 0 | 315.7 ms | ✅ |
| TypeScript SDK echo | `http://localhost:9193` | 43 | 43 | 0 | 233.1 ms | ✅ |

## Latency percentiles (`h3-test --json` top-level `latency` key)

| Metric | Go :9191 | Python :9192 | TS :9193 |
|---|---|---|---|
| min_ms | 0.49 | 0.78 | 0.64 |
| p50_ms | 0.88 | 1.67 | 1.26 |
| p90_ms | 9.30 | 11.38 | 8.32 |
| p95_ms | 29.41 | 42.36 | 34.42 |
| p99_ms | 88.10 | 111.92 | 80.47 |
| max_ms | 88.10 | 111.92 | 80.47 |
| mean_ms | 5.23 | 7.34 | 5.42 |

Latency percentiles present in `--json` output for all three harnesses
(OBS-IMPL-03 deliverable, PERF-ND-03 — re-verified live). Latencies
consistent with tick #162 (same order of magnitude; p99 slightly lower
this run).

## Health endpoints

- Go: `{"status":"ok","version":"1.0.0","transport":"rest","protocol_version":"1.0","capabilities":["text"]}`
- Python: `{"status":"ok",...,"capabilities":["tool_call","llm_call","text","wait","delegate","end"],"uptime_seconds":N}`
- TS: `{"status":"ok","version":"1.0.0","transport":"rest","protocol_version":"1.0","capabilities":["text","end"]}`

## Feature status vs spec

| Feature | Status |
|---|---|
| 43-test compliance battery vs any harness endpoint | ✅ working |
| JSON report (total/passed/failed/duration_ms/timestamp/results/all_passing) | ✅ working |
| Latency percentiles in `--json` | ✅ working |
| Go/Python/TS SDK echo harness compatibility | ✅ working |

## Findings

No regressions, no new gaps. Zero P0/P1/P2 issues. All verdicts green —
see `tasks.md` (empty task matrix).
