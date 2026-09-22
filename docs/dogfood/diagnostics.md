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
  `test_battery.py` (the 46-test gate), `cli.py` (both CLIs), and
  `templates/{go,py,ts}/` for scaffolding.
- **Packaging:** hatchling wheel from `packages = ["src/h3_shim"]`, two
  console scripts (`h3-test`, `hermes-h3`). Published to PyPI as
  `hermes-h3-shim` (v0.1.0 and later) — `pip install hermes-h3-shim`, or from
  git/source for an unreleased commit; build/upload/verification flow in
  `docs/publishing.md`. The `h3/` directory is an optional Hermes Core plugin that
  registers an `h3` command group delegating to the same CLI.
- **The battery:** 46 tests / 6 categories, E2E region-style. Runs a
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

- `h3-test` full battery: 46/46, exit 0; JSON mode
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
a full-marks pass against the *py* harness. For real verification, kill the
previous harness (or use a distinct port) before starting a new one. Worth a
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

---

## 2026-09-05 cycle — decision-tour diagnostics

### 9. Decision payload shapes: the three sharp edges (live-verified)

The loop validates every harness Decision through `src/h3_shim/protocol.py`
pydantic models. Building a custom harness from docs/api.md alone, three
shapes bit within ten minutes:

- `llm_call.model` is a plain **string** (`"model": "mini"`), not an
  object — a natural guess is `{"name": ..., "provider": ...}` because
  `context.models[]` uses that shape. Guessing object → pydantic
  ValidationError, hop dead.
- `wait.reason` is **required**; `wait.duration_seconds` is `int` (a
  float like `0.2` fails validation). The poll loop treats non-2xx as
  transient and retries `poll_interval` (1s) up to `max_polls` (30).
- `delegate` is `{"task": ..., "context": ...}` — no `harness`/`prompt`
  keys (those were my guess from the docs prose "delegate to a
  sub-agent").

**Why:** these models are generated from get-h3/protocol JSON Schemas
(`schemas/v1/llm-call.json`, `wait.json`, `delegate.json`) — the truth
lives in a *different repo* than the shim docs. **Right way:** one JSON
example per decision type in docs/api.md (DF2-H3-SHIM-1), or a prominent
pointer to the protocol repo's `schemas/v1/*.json` files.

### 10. Error collapse: how a contract violation becomes "error"

`H3ShimLoop.run()` wraps the whole session in try/except. A
`pydantic.ValidationError` raised while parsing a harness decision is
logged (`H3ShimLoop: error in session <id>` + traceback) and the method
returns the end-reason string `"error"`. The caller — the person who
just wrote the harness — sees `FINAL: error` with no field names. The
traceback *is* in the shim process's logs, so the fix pattern today is
"run the shim with logging at INFO and read its stderr"
(DF2-H3-SHIM-2 asks for decision_id + first error line in the surfaced
error or an `on_error` callback).

### 11. Session leak in the scaffold: 97 phantom sessions

Found a leftover dogfood harness (26h uptime) answering :9191 with
`active_sessions: 97`. Root cause in the template
(`src/h3_shim/templates/py/main.py`): `EchoHarness._state()`
auto-creates session entries, and the only removal path is
`on_session_terminate()` via `DELETE /v1/sessions/{id}` — the loop's
natural END decision never purges. Consequences: unbounded growth on
long-lived harnesses and a meaningless `active_sessions` health metric.
Same-side effect: a stale harness silently owns :9191 and the next
user's battery tests the *old* server (hit in this cycle; DF-5/DF2-3).

### 12. Foreman-vs-P1 pathology (fleet meta)

Board events 315-325 (09-01 → 09-05): the shim foreman picked
DF-H3-SHIM-FOREMAN-1 nine times. Verdict trajectory: dispatched+guard
pass → REJECTED → NO_CHANGES → worker dry-run with `verdict: null`,
then dry-run repeats. Zero commits. Meanwhile the finding is
hand-reproducible in minutes (a 405 plus a full-marks PASS). Reading: the
task as scoped ("battery overstates compliance") is a *diagnosis*, not *work* —
the foreman has no concrete failing test to add, and its battery
integration likely re-runs the green gate, concluding "nothing to do".
Fix direction recorded as DF2-H3-SHIM-4: re-scope P1s into
single-commit tasks with exact file+assertion pointers
(e.g. "add `tests/test_scaffold.py::test_get_session_not_405`, then add
the GET route to `templates/py/main.py`").

### 13. 2026-09-06 cycle — the loop closed itself, then showed its remaining seam

**Foreman-direct works now.** After DF2-H3-SHIM-4 diagnosed the barren-tick
pathology, both 09-05 P1s closed with real commits the same weekend:
DF2-H3-SHIM-1 (docs wire-shape reference, 4232ed3, tick #371) and
DF-H3-SHIM-FOREMAN-1 (scaffold GET /v1/sessions + battery test 5_12,
5762d6f, tick #373, judge PASS). This cycle verified both by real use —
a docs-only custom harness parsed every decision shape first try, and
the new 46th test exists because the old scaffold really did 405.

**The datetime serialization trap (why `mode="json"` matters).**
`Message.timestamp` is `datetime | None`. Pydantic coerces the ISO string
on construction; `H3Client` then does `json=req.model_dump()` (client.py
111/141), leaving a live `datetime` object for httpx's json.dumps →
`TypeError: Object of type datetime is not JSON serializable`. The loop's
broad exception handling converts that into the end-reason string
`"error"`, so the consumer sees nothing actionable. Lesson: any
`BaseModel` crossing an HTTP boundary needs `model_dump(mode="json")`, and
a loop that swallows exceptions must surface *something* (DF3-H3-SHIM-1,
building on DF2-H3-SHIM-2). The battery cannot catch this class: the shim
side never sends timestamps, so the round-trip only breaks from the
embedding-host direction — another L3 (works-for-a-user) gap that green
tests don't cover.

**Foreman-direct vs worker-dispatch asymmetry.** Same foreman, same day:
foreman-direct tasks closed with judged commits while DF-2 took three
worker-dispatch ticks (events 349/354/355), all `worker=dry-run
guard=not_run verdict=null commit=none`. The DF2-H3-SHIM-4 lesson
generalizes: when a foreman has a working direct lane, the fastest fix for
a stuck task is re-scoping it INTO the working lane, not repairing the
broken one first (the repair is DF3-H3-SHIM-2's second option).

**Fresh-install regression watch.** Bunker run (agent 39a24489, 12s
install + 5s harness + 46/46) re-confirmed the scaffold session leak:
`active_sessions: 98` after a single battery pass on a brand-new harness
(DF2-H3-SHIM-3 still open). Installability itself remains clean — no
sudo, no compose, no toolchain surprises on bare Debian.

## 2026-09-20 cycle — the green-battery blind spot, mapped

**Why five cycles of 46/46 could still hide a P1.** The battery's own
docstring (test_battery.py:10) says tests deliberately bypass H3Client
and talk raw httpx. That choice makes the battery a pure *server*
contract test — and leaves the *client's* wire serialization with zero
coverage. This cycle drove the py client against harnesses in OTHER
languages, which is the only way null-vs-absent JSON semantics can
surface: pydantic's `model_dump` (even with `mode="json"`) serializes
unset optionals as explicit `null`s, Go's `encoding/json` silently
accepts them, and the ts stack's zod `z.string().optional()` rejects
null with 400. The fix is wire-level: `exclude_unset`/`exclude_none` on
the shim side or `.nullable()` on the SDK side (DF4-H3-SHIM-1). Lesson
for the repo's gates: a compliance battery and an interop test are
different artifacts; the ecosystem needed both and only had one.

**Template parity is a process gap, not a code gap.** dfa9d99 fixed the
py scaffold's session leak and its message honestly says the go/ts
siblings were "unchanged on purpose" — but nothing then propagated the
fix. Live numbers: go 98 active_sessions after one battery, ts 197
after four, sdk-python BaseHarness 87 after one (DF4-H3-SHIM-2). The
repo now has three scaffold templates and one SDK BaseHarness that all
implement the same echo contract; any behavioral fix lands in one and
silently diverges in the others. The right way: a cross-template
lifecycle test matrix (the TestPyScaffoldSessionLifecycle pattern
applied to go/ts/sdk-python), plus a battery assertion tying observed
decision types to health.capabilities (DF4-H3-SHIM-4 — go declares
`["text"]` while emitting `end`).

**The 09-18 CLI wave closed real 09-05 frictions — verified, not
trusted.** `install` now health-checks before writing (dead endpoint →
exit 1, config byte-identical, message names `verify --endpoint` as the
probe); `route --session --set-harness/--remove` turned the docs-says-
hand-edit dead end into a full lifecycle with unknown-harness guards;
`pre-update-check` takes TARGET_VERSION and honestly reports matrix
gaps instead of always blocking. Pattern worth keeping: each fix went
in with its own regression test, and every one of them held up under
independent live probing the same week.

**Bunker leg mechanics (what actually happened).** spawn 344005f2 ttl
2h → clone (public repo, no creds) → venv + pip install . 11s on
Python 3.13 → README quickstart → 46/46 in 0.60s → destroy exit 0.
Two retries, both instructive: (1) the bunker agent's `/tmp` is not
writable — logs belong in `$HOME`; (2) the README's step-2 venv hint
resolves to the outer venv in the same terminal, so the harness died
with ModuleNotFoundError until activated in its own venv — that
ambiguity is DF4-H3-SHIM-3, not a bunker quirk. The destroy-first
discipline (before writing findings) is what keeps a 2h TTL from
becoming the only cleanup.

## 2026-09-22 cycle — read the merge, not the board

The seventh run re-proved the two P1s that tick #388 had closed the
previous day, and the re-proof changed the verdict on both. The
transferable lessons:

**Closed ≠ fixed; the merge is the evidence.** DF4-H3-SHIM-1 closed
with merge f897019, whose only src change is `duration_ms` float→int.
A task titled "loop passes real ts zod stack" cannot be closed by a
commit that never touches the wire path — and indeed the default
`Context()` embed still 400s against the live zod stack (config/
session_state unset → dropped by `exclude_unset` → zod requires them
as objects). Reopened as DF5-H3-SHIM-1. The repo's own test docstring
(tests/test_client.py:794-799) had flagged the gap as a follow-up and
the closing test worked around it with an explicit
`Context(config={}, session_state={})`. When a closing commit's tests
dodge the exact path named in the title, the board is optimistic and
the diff is not.

**A green battery is not a lifecycle gate.** DF4-H3-SHIM-2's purge
(89886e3) only fires on `result_count >= 2`, so ~96 battery sessions
per run leak on every scaffold (py 1440→1536 across runs; fresh bunker
scaffold 192 after ONE battery). The originally-requested
active_sessions-returns-to-0 battery assertion was never added — that
assertion is what would have caught the resurrection before a human
did. Resurrected as DF5-H3-SHIM-2.

**Install commands that assume a fresh target are docs defects.** The
documented `cp -r h3 ~/.hermes/plugins/h3/` silently nested the fresh
copy into the existing Aug-7 dir; the stale mirror then rejected
`verify NAME` / `route --session` with "unrecognized arguments" — and
the plugin exited 0 on the failure. Three fixes belong in the repo
(DF5-H3-SHIM-3): parent-dir copy target, a staleness warning in
`register()`, honest exit codes. Refreshing the deployed copy in place
fixed the behavior immediately — the mirror itself was already correct.

**Bunker leg, seventh iteration.** agent 16d8d4ad ttl 2h → clone
(public) → 11s install (Python 3.13.5, Debian 13) → scaffold → 46/46
smoke 0.59s → destroy verified via `bunker list` (No agents found).
No new bunker quirks — the 09-20 lessons (logs in $HOME, venv
ambiguity) both held.

