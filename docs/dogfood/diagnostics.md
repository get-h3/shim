# H3 Shim — Diagnostics Trail

How the shim is built, how it works, the errors found during the 2026-08-07
dogfood run, and the right way to do things. Written as explanation, not log
dumps — read this to understand the system, not to replay a session.

## How it's built

- **Layout:** `src/h3_shim/` with `protocol.py` (Pydantic models generated
  from get-h3/protocol's OpenAPI/JSON Schema), `client.py` (httpx REST client
  for `/v1/health`, `/v1/process`, `/v1/result`, `/v1/cancel`,
  `/v1/sessions/{id}`), `loader.py` (config discovery, 30s health-check loop,
  circuit breaker, most-specific-first session routing), `shim_loop.py`
  (process → execute decision → result → loop, hard cap 50 iterations),
  `test_battery.py` (the 44-test gate), `cli.py` (both CLIs), and
  `templates/{go,py,ts}/` for scaffolding.
- **Packaging:** hatchling wheel from `packages = ["src/h3_shim"]`, two
  console scripts (`h3-test`, `hermes-h3`). NOT on PyPI yet — install from
  git/source. The `h3/` directory is an optional Hermes Core plugin that
  registers an `h3` command group delegating to the same CLI.
- **The battery:** 44 tests / 6 categories, E2E region-style. Runs a
  pre-flight `probe()` that raises `NotH3EndpointError` (non-JSON, foreign
  shape, 401, or connection error) → CLI prints a warning to stderr and
  exits 2. Each category is a coroutine on `H3TestBattery`; results carry
  `category` as the **display label** (e.g. `"Health & Protocol"`).

## Errors encountered and the right way

### 1. Wheel missing `h3_shim/__init__.py` (GAP-005, P0)

- **Symptom:** fresh-venv `hermes-h3 pre-update-check 1.0` →
  `ImportError: cannot import name '__version__' from 'h3_shim' (unknown
  location)`, exit 1.
- **Why it happens:** `[tool.hatch.build.targets.wheel]` declares BOTH
  `packages = ["src/h3_shim"]` AND an `include = [...]` list. With hatchling,
  the `include` patterns override the default package file selection, so
  `__init__.py` (matching no pattern) is dropped from the wheel. The package
  then installs as an implicit namespace package: submodules import fine —
  which is why `hermes-h3`, `h3-test`, and the whole CLI keep working — but
  `from h3_shim import __version__` fails.
- **Proof:** `pip wheel` of main → 23-file wheel, no `__init__.py`.
  Experiment A (remove `include` block): `__init__.py` present AND all 8
  templates still ship. Experiment B (remove `packages`): still no
  `__init__.py`. → the `include` block is pure harm; delete it.
- **Right way:** delete the `include` block from pyproject.toml; add a
  wheel-install smoke test (GAP-008) so ship-vs-dev drift can never hide
  again. This is the classic "green in the dev tree, broken in the artifact"
  gap: 248/248 tests and 267 foreman ticks never noticed because the dev
  tree imports from source.

### 2. `--categories` filter silently runs nothing (GAP-006, P1)

- **Symptom:** `h3-test --endpoint <live> --categories health` →
  `TOTAL 0/0 PASSED`, **exit 0**. Same for `health,errors` and any token.
- **Why:** `cli.py::_run_battery` filters `report.results` with
  `r.category in wanted` where `wanted` holds CLI tokens (`"health"`) but
  `TestResult.category` stores display labels (`"Health & Protocol"`).
  Nothing ever matches; `failed == 0` → `all_passing` is True → exit 0.
- **Right way:** match token OR label (or normalize both sides); validate
  tokens up front and exit non-zero on unknown ones. Never let a filter
  that matched zero tests report success — that's a false-green path.

### 3. Docs drift (GAP-007, P2)

- `docs/integration.md` §1: "Every `hermes-h3` command accepts
  `--config <path>`" — `scaffold` has no such option (verified exit 2).
  The `--config` flag is per-command click wiring, not a global option.
- §4.1: "All [SDK echo examples] listen on http://localhost:9191" —
  sdk-python `examples/echo.py` calls `uvicorn.run(app, host="0.0.0.0",
  port=8000)` and never reads `PORT` (verified; port 8000 conflict on the
  dogfood host). Go/TS examples do use 9191.
- **Right way:** docs must be verified against the running artifact; the
  foreman's docs gate counts files/TODOs, not claims-vs-behavior. The
  scaffolded harness template (templates/py/main.py) is the good citizen
  here: it reads `PORT` and its own docstring is accurate.

### 4. What works (the right way to use it)

- `h3-test` full battery: 44/44 in ~0.3s, exit 0; JSON mode
  (`--json`) gives `total/passed/failed/latency/results`; wrong-server
  detection exits 2 with a clear stderr warning (GAP-003 fix — verified
  against both a plain http.server 404 and an unreachable port).
- Lifecycle: `install` (auto-creates config, first harness becomes default)
  → `list` → `use` → `route` (renders sessions map) → `test` → `uninstall`
  (clean error + exit 1 for unknown names). `verify` exit 1 on dead
  endpoints with a precise message.
- `hermes h3` plugin: enable → all 9 subcommands, delegated in-process or
  via the console script; `--config` must precede the subcommand
  (`hermes h3 --config <path> list`).
- Scaffold: generates a self-contained harness (inlined protocol models,
  zero shim dependency at runtime) that passes the battery out of the box —
  the single best onboarding artifact in the project. All three templates
  (py/go/ts) verified battery-passing on 2026-08-20.

## 2026-08-20 follow-up run — new lessons

### 5. `pre-update-check` always blocks (GAP-033, P1)

- **Symptom:** fresh wheel-installed venv → `hermes-h3 pre-update-check
  0.18.0` (and 0.19.0, 0.20.0) → exit 1, "Update blocked. H3 shim v0.1.0 is
  too old (requires H3 ≥ 1.0.0)".
- **Why:** `pyproject.toml` version is still 0.1.0 while `versions.yaml`
  (bundled in the wheel since GAP-011) declares h3_shim ≥ 1.0.0 for every
  supported Hermes version. The check is therefore a constant: it can never
  pass, so users learn to ignore it or believe the install is broken.
- **Right way:** version numbers are a compatibility contract — the package
  version and the compat matrix must move together, and the release smoke
  test (GAP-008) should assert `pre-update-check` passes for the shipped
  version. GAP-011 fixed *loading* the matrix; nobody ever checked the
  matrix against the package's own version.

### 6. The shim loop: what actually happens on each decision type (GAP-034/035)

Driving H3ShimLoop against a live harness revealed the true decision
execution table (docs/api.md says "execute tool call, LLM call, text, wait,
delegate" — reality):

| Decision | Actual behavior |
|---|---|
| `tool_call` | ✅ Executes registered tool fn; result POSTed back |
| `text` | ⚠️ Logged only — no delivery hook; host sees nothing |
| `wait` / `delegate` | Executed locally (pause / sub-agent result) |
| `llm_call` | ❌ **Refused**: `ExecutionResult(type="error", data={error: "LLM not configured..."})` — safe, honest, but unimplemented |
| `end` | ✅ Terminates; `run()` returns the **EndReason string** ('task_complete'/'error'/'timeout'), NOT the final assistant text — the harness's final `text` payload is discarded |

- **Why:** the loop is a protocol skeleton for a host to embed; the host is
  supposed to provide the LLM provider and the transport. Neither hook
  exists yet, so the two most user-visible decision types (llm_call, text)
  degrade to a log line / an error result.
- **Right way:** injectable `llm_provider` callable + `on_text` callback on
  H3ShimLoop (or documented subclass points), and docs must state the actual
  contract (`run()` → EndReason) until then.

### 7. The Decision wire shape is the undocumented tax (GAP-036, P2)

- **Symptom:** building a probe harness from docs/api.md alone, the first two
  payloads failed: `Field required [decision]` (I sent `type: "llm_call"`),
  then `text: Input should be a valid dictionary` (I sent a plain string).
- **Why:** the discriminator is a top-level `decision` field
  (`llm_call`/`text`/`end`/...), NOT `type`; sub-payloads nest under
  `llm_call`/`text`/`end`; `text` is a `TextResponse` dict. `protocol.py`
  models document the fields, but no doc shows a single JSON example.
- **Right way:** 2-3 concrete JSON examples in docs/api.md (or a pointer to
  the get-h3/protocol OpenAPI spec). SDK repos have examples; the Hermes-side
  consumer docs have none.

### 8. Port-collision blindness (dogfood-only lesson)

The Go scaffold's first run failed with `bind: address already in use`
(py harness still on :9191) — and the battery against :9191 still reported
44/44 against the *py* harness. For real verification, kill the previous
harness (or use a distinct port) before starting a new one. Worth a
troubleshooting line in integration.md: "if the battery passes but you're
not sure which server answered, check `lsof -i :9191`."

## How the project got here

The board history (267 ticks) shows a healthy self-improving loop: GAP-001
(CLI mismatch) → plugin shipped; GAP-003 (wrong-server detection) → fixed
and live-verified; JSONL-NORM-001 → canonical JSONL board. The 2026-08-07
dogfood run continues that loop with the first *installed-artifact* findings
(GAP-005..008). The one structural weakness the trail exposes: the foreman's
gates exercise the source tree, not the wheel — GAP-008 makes that
impossible to miss again.
