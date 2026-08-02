# H3 Shim — E2E-001 Report (tick #172, 2026-08-02)

Compliance battery (`h3-test`) against all three SDK echo harnesses, run
foreman-direct (CLI/plugin project — no browser surface).

## Environment

| Item | Value |
|---|---|
| Battery | `h3-test` (43 tests, 6 categories: health, process, decisions, results, errors, stress) |
| Shim | `hermes-h3-shim` HEAD `c8b0026` (clean, no drift from origin/main) |
| Harnesses | Go SDK echo `:9191`, Python SDK echo `:9192`, TypeScript SDK echo `:9193` |
| Unit suite | 242/242 passed (1.88s) |
| GitReins guard | Tier 1 PASS (secrets, lint, tests) |
| Spec sync | `sync_protocol.py --diff` exit 0, 0 diff markers — standing state 87 schema fields / 95 model fields (8 intentional extra optional fields) |
| CI | 5/5 success (latest tick #171 commit) |

## Results

| Harness | Endpoint | Tests | Passed | Failed | Duration | all_passing |
|---|---|---|---|---|---|---|
| Go SDK echo | `http://localhost:9191` | 43 | 43 | 0 | 200.0 ms | ✅ |
| Python SDK echo | `http://localhost:9192` | 43 | 43 | 0 | 266.6 ms | ✅ |
| TypeScript SDK echo | `http://localhost:9193` | 43 | 43 | 0 | 219.8 ms | ✅ |

## Latency percentiles (`h3-test --json` top-level `latency` key)

| Metric | Go :9191 | Python :9192 | TS :9193 |
|---|---|---|---|
| min_ms | 0.51 | 0.66 | 0.60 |
| p50_ms | 0.83 | 1.33 | 1.19 |
| p90_ms | 9.17 | 16.18 | 9.72 |
| p95_ms | 29.92 | 36.40 | 32.05 |
| p99_ms | 74.64 | 106.79 | 83.13 |
| max_ms | 74.64 | 106.79 | 83.13 |
| mean_ms | 4.65 | 6.20 | 5.11 |

Latency percentiles present in `--json` output for all three harnesses
(OBS-IMPL-03 deliverable, PERF-ND-03 — re-verified live). Latencies
consistent with tick #167 (same order of magnitude; all three harnesses
slightly faster this run — 200-267ms total vs 225-316ms).

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

Minor note (non-actionable): shim venv drifted back to older fastapi
0.140.13 / ruff 0.16.0 / annotated-doc 0.0.4 (last refreshed tick #151 to
0.141.1/0.16.1/0.0.5). pyproject pins are loose (`>=`), all gates still pass
with installed versions, and the security scanner blocks `pip install` of
fastapi (false-positive package-name similarity). Not a board task; will
refresh opportunistically if the scanner allows.
