# H3 Shim — Real Integration Report (2026-08-20)

Follow-up dogfood run. The 2026-08-07 run found 8 gaps (GAP-005..009 + stand-in
GAP-010..032); this run verifies every fix against a fresh wheel-built venv and
drives the **shim loop itself** — the actual brain-swap — which no previous run
had exercised. Everything below was executed for real from a fresh venv in
`/tmp/dogfood-h3-shim2` with a scratch `HOME` (real `~/.hermes` untouched).

## Verdict

🟡 **PROMISING-BUT-ROUGH → now SHIPPABLE-CORE.** The install → scaffold →
verify → battery flow is genuinely excellent and all 32 prior gaps are fixed.
The remaining roughness lives in the programmatic brain-swap surface
(LLMCall refusal, `run()` return contract, undocumented wire shape) — the
parts that only show up when you integrate the loop into a host, not when you
run the CLI.

## What was verified fixed (all 32 prior GAPs, live)

| Prior gap | Verification (this run) |
|---|---|
| GAP-005 P0 wheel missing `__init__.py` | `git archive HEAD` → `pip wheel` → wheel contains `h3_shim/__init__.py` + templates + `data/versions.yaml`; fresh-venv `pre-update-check` no longer tracebacks |
| GAP-006 `--categories` 0/0 false green | `h3-test --endpoint :9191 --categories health` → **7/7**, exit 0; unknown token → `Error: unknown categories: bogus`, exit 2 |
| GAP-003 wrong-server detection | `h3-test` vs `python -m http.server` :9123 → stderr warning `does not look like an H3 endpoint`, exit 2; `--json` carries `not_h3_endpoint: true` + reason |
| GAP-007 scaffold `--config` / port table | `hermes-h3 scaffold --config x.yaml` → writes empty skeleton, exit 0 |
| GAP-008 installed-artifact smoke test | `scripts/smoke_test.sh` exists (19 checks, wheel → fresh venv → all entry points) |
| GAP-009 plugin `--config` ordering | `hermes h3 list --config X` AND `hermes h3 --config X list` both work (scratch `HERMES_HOME` + real `hermes` CLI, plugin copied to scratch plugins dir) |
| GAP-023 fake LLM response | `_execute_llm` now refuses with structured error (verified live below) |
| Count drift 43/44, exit-code docs, README quickstart, etc. | README/AGENTS.md/integration.md consistent; banner shows real version `v0.1.0` |

## The working example (proven this run)

```bash
# Zero-to-verified, ~6 min, from a clean wheel install:
python3 -m venv venv && ./venv/bin/pip install <wheel or git+https://github.com/get-h3/shim>
hermes-h3 scaffold --lang py            # -> h3-harness-py/ (self-contained FastAPI app)
cd h3-harness-py && pip install -e . && python main.py &   # honours PORT; default :9191
h3-test --endpoint http://localhost:9191    # TOTAL 44/44 PASSED in ~0.4s, exit 0
```

**All three scaffold templates pass the battery** (first time the Go and TS
templates were verified end-to-end by a dogfood run):

| Template | Command | Battery |
|---|---|---|
| Python | `scaffold --lang py` + `pip install -e . && python main.py` | 44/44 ✅ |
| Go | `scaffold --lang go` + `go mod tidy && go run .` | 44/44 ✅ |
| TypeScript | `scaffold --lang ts` + `npm install && npm run build && npm start` (`PORT=9192` honored) | 44/44 ✅ |

## NEW: the brain-swap, driven for real (the part tests never touch)

A consumer OUTSIDE the repo (`/tmp/dogfood-h3-shim2/consumer/consumer.py`,
installed wheel only) exercised the full programmatic surface from
`docs/api.md`:

```python
client = H3Client(endpoint="http://localhost:9191", timeout_ms=10000)
health = await client.health()                    # status=OK version=1.0.0 ✅

loader = H3Loader(config)                          # harnesses + sessions routing
await loader.start_health_checks()
await loader.resolve("telegram", "-1001234567890", "42")   # most-specific-first ✅
loader.route_session(...) / loader.get_session_harness(...) # pinning ✅

loop = H3ShimLoop(client, session_id="dogfood-session-001", context=Context(), ...)
loop.register_tool("get_weather", get_weather)     # tool implementation
result = await loop.run(Message(role="user", content="what is the weather in Berlin?"))
# -> 'task_complete'  (the EndReason, not the final text — see GAP-035)
```

A full session (process → execute → result → END) ran cleanly against the
scaffolded harness. **The core loop works.**

## New findings (all hit live, all on the board as GAP-033..038)

1. **GAP-033 (P1) `pre-update-check` is a permanent red light.** Package ships
   as v0.1.0 but `versions.yaml` requires h3_shim ≥ 1.0.0 for every supported
   Hermes version (0.18.0/0.19.0/0.20.0). Verified: all three exit 1 "Update
   blocked. H3 shim v0.1.0 is too old". The check can never pass.
2. **GAP-034 (P1) LLMCall decisions are refused, not executed.** Probe harness
   issuing `decision: llm_call` through H3ShimLoop → `LLM call refused: model=
   deepseek-v4-flash (no LLM provider configured)`, `ExecutionResult(type=
   "error", success=False)`. Safe (GAP-023's fix is solid) but `docs/api.md`
   claims the loop executes "LLM call" decisions — it doesn't, and no doc says
   so.
3. **GAP-035 (P2) `run()` returns the EndReason string, not the final
   assistant text** (`docs/api.md` says "Returns the final assistant text").
   The harness's final `text` payload is discarded; TEXT decisions are only
   logged. A host can't deliver user-facing text without subclassing or log
   parsing.
4. **GAP-036 (P2) Wire shape is undocumented.** Building a valid `Decision`
   response required reading `protocol.py` — the discriminator is a top-level
   `decision` field (`llm_call`/`text`/`end`/…), not `type`; sub-payloads nest
   under `llm_call`/`text`/`end`; `text` must be a dict. Two of my probe
   attempts failed with raw pydantic tracebacks. `docs/api.md` has zero JSON
   examples.
5. **GAP-037 (P3)** wrong-server warning dumps the full raw HTML body.
6. **GAP-038 (P3)** `hermes-h3 --version` errors; `h3-test --version` works.

## Errors hit and their fixes (this run)

| Symptom | Cause / fix |
|---|---|
| `pre-update-check 0.18.0` → "Update blocked. H3 shim v0.1.0 is too old" | Package version vs compat matrix mismatch (GAP-033). Tracked. |
| Probe harness: `ValidationError ... Field required [decision]` | Wire shape: discriminator is `decision`, not `type` (GAP-036 — docs gap; my bug, but undocumented). |
| Probe harness: `ValidationError ... text: Input should be a valid dictionary` | `text` must be a `TextResponse` dict (GAP-036). |
| `go run .` on :9191 while py harness still up | `bind: address already in use` — the Go "44/44" was the py harness; re-ran on a free port and got the real Go 44/44. Port collisions are silent in the battery's eyes. |
| `h3-test` vs `http.server` | Works as designed (exit 2 + warning), but the warning embeds the whole HTML page (GAP-037). |

## What a new user needs that isn't documented

- The `Decision` wire shape (GAP-036) — the single biggest "had to read
  source" moment.
- That `run()` yields an EndReason, and that text delivery needs a hook
  (GAP-035).
- That `LLMCall` decisions come back as errors until a provider is wired
  (GAP-034).
- That `pre-update-check` will say blocked no matter what (GAP-033).
