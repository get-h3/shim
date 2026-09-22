# Dogfood Integration — 2026-09-22 (h3-shim, HEAD 0823ee9 = origin/main)

**Verdict: SHIPPABLE (core).** Seventh dogfood cycle; first to drive the
`hermes h3` PLUGIN end-to-end — which immediately surfaced a stale-copy
trap (DF5-H3-SHIM-3) — and the first to re-prove two just-closed P1s
against real artifacts instead of trusting the board. One closed P1 was
reopened (DF5-H3-SHIM-1), one resurrected by scope (DF5-H3-SHIM-2).

## Angle (why not the usual quickstart)

Cycles 1-6 covered py/go/ts scaffolds, the battery, py embedding, the
CLI wave, and the SDK release gate. Untouched this whole time:

1. **The `hermes h3` plugin** — installed per docs, driven through the
   full management lifecycle (install/list/verify/route/use/uninstall).
2. **The just-closed DF4-H3-SHIM-1/2 fixes** — tick #388 closed them the
   day before this run; this cycle re-proved them against live
   scaffolds, the real ts zod stack, and session counters.

## What was run (all live numbers)

| Surface | Result |
|---|---|
| fresh venv, `pip install git+https://github.com/get-h3/shim` | 21s, both CLIs on PATH |
| battery vs py scaffold (warm, hyperfine ×10) | 46/46, **477ms ±21ms** (p50 1.96ms) |
| battery vs ts scaffold (npm i + tsc + run) | 46/46, 0.28s |
| battery vs go scaffold (go build + run) | 46/46, 0.23s |
| `H3ShimLoop` default identity → ts scaffold | **400 → run()='error'** (DF5-H3-SHIM-1 — reopened) |
| `H3ShimLoop` + `Context(config={}, session_state={})` → ts | **task_complete, on_text 2x, live zod stack** |
| session counters post-battery | py 1440→1536 (+96/run), ts 96, go 96→192, bunker 192 (DF5-H3-SHIM-2) |
| `hermes h3 install/list` (plugin) | works, health-check gate, `*` default marker |
| `hermes h3 verify ts-plugin` | **failed on the stale copy** (DF5-H3-SHIM-3); works after refresh |
| `hermes h3 route --session tg:12345 --set-harness X` | **failed on stale copy**; set→lookup→uninstall all RC 0 after refresh |
| `h3-test --categories stress` / display label | 5/5 exit 0 / exit 2 "unknown categories" (DF5-H3-SHIM-4) |
| bunker (agent 16d8d4ad): clone → install → scaffold → smoke | 11s install, 46/46 exit 0 in 0.59s, Python 3.13.5, destroyed clean |

## The reopened P1 — read the merge, not the board

DF4-H3-SHIM-1 was closed by merge f897019. Its only `src/` change is
`duration_ms` float→int (6ad1bdf). The nulls fix (`exclude_unset`,
e9e16ff) was real, but `Context()` leaves `config`/`session_state`
UNSET — `exclude_unset` then drops them from the wire — while the SDK
zod schema types both as REQUIRED objects. The default embed path (the
one api.md:336 documents) still 400s. The repo's own test docstring
(tests/test_client.py:794-799) says the quiet part: *"Invisible to the
Python-mock layer; flagged as a follow-up protocol-doc gap, not fixed
here (this task is test-only)."* Boundary proof: the explicit
`Context(config={}, session_state={})` shape passes the live zod stack.

**Lesson:** a merge that closes a "loop passes real ts zod stack" task
must contain a change on that path — a test-only commit plus a
duration-int commit cannot have moved the wire behavior. Verify closed
fixes against the artifact they name, the same day you trust the board.

## The resurrected P1 — green batteries don't count sessions

The END-purge (89886e3) fires only in `on_result` when
`result_count >= 2`. Every battery session that never gets a second
result (error-path, one-shot, cancel tests) leaks — ~96/run, uniform
across all three scaffolds AND the fresh bunker box. The template
comment still claims active_sessions "cannot grow"; the battery is
46/46 green because the originally-requested returns-to-0 assertion was
never added. **A green battery is not a session-lifecycle gate.**

## The plugin trap — two jaws

`cp -r h3 ~/.hermes/plugins/h3/` (docs/integration.md:210, plugin
docstring) nests the fresh copy inside an existing dir, so the stale
2026-08-07 mirror kept serving. Its mirror predated `verify [name]` and
`route --session`, so both commands died with "unrecognized arguments"
— and `hermes h3` **exited 0** on the failure, so nothing automated
could catch it. After refreshing the deployed copy in place, the whole
lifecycle works with correct RCs — the plugin itself is sound; the
install doc and exit-code propagation are the defects.

## Friction log (chronological)

1. `H3Client(base_url=...)` — real kwarg is `endpoint=` (skill knew;
   README/api.md never shows a constructor call).
2. `H3ShimLoop(client)` — `session_id` and `context` are REQUIRED
   positionals; "default identity" means identity only.
3. `loop.run("text")` — signature is `run(message: Message)`; a bare
   string pydantic-crashes (masked to run()='error').
4. `go mod tidy -q` — `-q` is not a tidy flag (no silent mode).
5. `--categories "Stress & Performance"` — display labels are not
   accepted tokens (DF5-H3-SHIM-4).
6. Foreground timeout on the first harness venv setup (~50s incl. pip
   resolve) — trivial, but it is the cold-start feel for a new user.

## Perf (Step 2b — one measurement each, no campaign)

- Headline operation (`h3-test` vs py scaffold, warm): **477ms ±21ms**
  mean, p50 1.96ms/p95 70ms battery-internal. Cold (first-ever run in a
  fresh venv incl. interpreter + imports): 0.59s on the bunker box.
- Install (fresh machine): **11s** shim + ~30s harness venv.
- Verdict: nothing here is slow enough to be worth a PERF row — the
  battery is comfortably fast, installs are seconds. No PERF-* rows
  filed this cycle; the P1s above are the real cost drivers.
