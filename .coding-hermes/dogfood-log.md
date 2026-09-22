# Dogfood Log

Real-use field tests of this project. Each entry records what a real user
actually experienced, the verdict, and where the findings landed.

---

## 2026-08-07 — h3-shim dogfood run

- **Verdict:** 🟡 PROMISING-BUT-ROUGH
- **Promise:** *A user can install the H3 shim, register an external agent
  harness, verify it against the 43-test H3 compliance battery, and manage
  session routing — from the `hermes-h3` CLI or the `hermes h3` plugin.*
- **Reality:** The core loop fully works (install → scaffold → run → 43/43 in
  ~0.3s), but the shipped wheel is missing `h3_shim/__init__.py` (broken
  `pre-update-check` for every installed user), `--categories` silently
  false-greens (0/0, exit 0), and the integration docs drift from reality.
- **Time-to-first-success:** ~6 min (venv + pip install + scaffold + run +
  battery 43/43).
- **Friction count:** 6 (P0 wheel missing __init__; P1 --categories 0/0;
  scaffold lacks --config despite docs; SDK echo port mismatch 8000 vs 9191;
  hermes h3 --config must precede subcommand; SDK reference example needs an
  extra pip install not shown in the guide).
- **Top 3 findings:**
  1. P0 — wheel ships without `h3_shim/__init__.py`; `pre-update-check`
     crashes with an ImportError traceback. Root cause: hatchling `include`
     block in pyproject.toml drops `__init__.py` (proven by experiment).
  2. P1 — `h3-test --categories <anything>` runs 0/0 tests, exits 0 (false
     green). Token-vs-display-label mismatch in the filter.
  3. P2 — docs/reality drift: "every command accepts --config" (scaffold
     doesn't) and "all SDK echoes listen on :9191" (python example binds
     :8000, ignores PORT).
- **Tasks added:** GAP-005 (P0), GAP-006 (P1), GAP-007 (P2), GAP-008 (P2).
- **Artifacts:** `docs/dogfood/2026-08-07-integration.md`,
  `docs/dogfood/diagnostics.md`, `skills/h3-shim-usage/SKILL.md`.
- **Foreman:** not woken (cooldown 7200s < 14400s — not paused).

## 2026-08-20 — h3-shim dogfood run (follow-up)

- **Verdict:** 🟡 PROMISING-BUT-ROUGH → core is SHIPPABLE; the remaining
  roughness is in the programmatic brain-swap surface.
- **Promise:** *A user can install the H3 shim from source, scaffold/run a
  harness in any of 3 languages, verify it against the 44-test battery,
  register & route harnesses via CLI or the `hermes h3` plugin, and drive
  real H3 sessions through the shim loop.*
- **Reality:** All 32 prior GAPs verified fixed live (wheel `__init__.py`,
  `--categories`, wrong-server exit 2, scaffold `--config`, smoke test,
  plugin config ordering, no fake LLM output). All three scaffold templates
  (py/go/ts) pass 44/44. Full H3ShimLoop session works end-to-end against a
  live harness (client → loader routing → process → tool call → result →
  END). Six new findings: pre-update-check always blocks (version 0.1.0 vs
  matrix ≥1.0.0), LLMCall decisions refused while api.md claims execution,
  run() returns EndReason not final text, wire shape undocumented, raw HTML
  in wrong-server warning, `--version` asymmetry.
- **Time-to-first-success:** ~5 min (venv + wheel install + scaffold + run +
  battery 44/44).
- **Friction count:** 6 (P1 pre-update-check always-block; P1 LLMCall
  refusal vs docs; P2 run() contract drift; P2 wire-shape docs gap — 2
  failed probe attempts; P3 HTML dump in warning; P3 --version asymmetry).
- **Top 3 findings:**
  1. P1 — `pre-update-check` can never pass: shipped package v0.1.0 vs
     versions.yaml min h3_shim 1.0.0 for all supported Hermes versions
     (verified all 3 versions exit 1 "Update blocked").
  2. P1 — LLMCall decisions are refused (`ExecutionResult type=error`), not
     executed; docs/api.md claims the loop executes "LLM call" decisions.
     Safe refusal (GAP-023 fixed the fake), but the brain-swap's key
     decision type is unimplemented and undocumented as such.
  3. P2 — `H3ShimLoop.run()` returns only the EndReason string; the
     harness's final text is discarded and TEXT decisions are log-only —
     docs/api.md says "Returns the final assistant text". Plus: wire shape
     (`decision` discriminator, nested payloads) is nowhere documented.
- **Tasks added:** GAP-033 (P1), GAP-034 (P1), GAP-035 (P2), GAP-036 (P2),
  GAP-037 (P3), GAP-038 (P3) — appended to `.coding-hermes/board/tasks.jsonl`
  (v2.1 JSONL format) + event 260.
- **Artifacts:** `docs/dogfood/2026-08-20-integration.md` (new),
  `docs/dogfood/diagnostics.md` (extended), `skills/h3-shim-usage/SKILL.md`
  (v1.1.0), `.coding-hermes/dogfood-log.md` (this entry).
- **Foreman:** woken — cooldown 21600s ≥ 14400s and real work added → PUT
  CooldownS=900 via scheduler API.


## 2026-09-01 — h3-shim dogfood run

- **Verdict:** SHIPPABLE
- **Time-to-first-success:** ~91s
- **Friction count:** 6
- **Findings:** 5 (2 P1, 3 P2) — see .coding-hermes/tasks.md and DF-H3-SHIM-FOREMAN-1..5 on the board.

## 2026-09-05 — h3-shim dogfood run

- **Verdict:** SHIPPABLE
- **Time-to-first-success:** ~50s cold (31s locally; 14s install + 7s
  harness setup + 0.6s battery on a fresh bunker container)
- **Friction count:** 4
- **Findings:** 4 (2 P1, 2 P2) — DF2-H3-SHIM-1..4 on the board; all five
  09-01 DF tasks re-verified live and still open.
- **Firsts this cycle:** bunker install leg (installability proven);
  all-six-decision-types tour through H3ShimLoop (tool_call → llm_call →
  wait+poll → delegate → text → end, final task_complete).
- **Artifacts:** `docs/dogfood/2026-09-05-integration.md` (new),
  `docs/dogfood/diagnostics.md` (§9-12 appended),
  `skills/h3-shim-usage/SKILL.md` (v1.2.0 — 45 tests, on_text/
  llm_provider documented, asyncio loader pitfalls), `.coding-hermes/tasks.md`.
- **Foreman:** woken (cooldown 43200s ≥ 14400s + real work added → PUT
  CooldownS=900). The new P1s are deliberately worker-actionable
  (docs wire-shape examples, scaffold session GC) unlike the
  diagnosis-shaped DF-1 that produced 9 barren ticks; DF2-H3-SHIM-4
  tracks the foreman-pathology follow-up.

## 2026-09-06 — h3-shim dogfood run

- **Verdict:** SHIPPABLE
- **Time-to-first-success:** ~20s for scaffold+battery path (12s install +
  5s harness + 0.64s battery on fresh bunker container); deep embedding
  integration (all six decision types through H3ShimLoop with real hooks)
  ~25 min including 5 consumer-side traps, 3 of which are library/docs
  findings rather than user error.
- **Friction count:** 5 (P1 datetime serialization crash; P2 worker-dispatch
  barren ticks; P2 test-count drift 44/45/46 across docs; P3 constructor
  shape docs; open DF2-H3-SHIM-2 opaque error collapse).
- **Findings:** 4 new (DF3-H3-SHIM-1..4) on the board.
- **Firsts this cycle:** both 09-05 P1s verified FIXED by the foreman
  (4232ed3 docs wire-shapes, 5762d6f scaffold GET + test_5_12) — first
  cycle where the board loop closed its own dogfood findings; deep
  embedding-host integration with every hook wired; battery run against a
  minimal docs-only harness (41/46, all 5 failures legitimate behavioral
  contract demands with self-explanatory details).
- **Artifacts:** `docs/dogfood/2026-09-06-integration.md` (new),
  `docs/dogfood/diagnostics.md` (§13 appended),
  `skills/h3-shim-usage/SKILL.md` (v1.3.0 — 46 tests, timestamp trap,
  constructor shapes, loader default_harness), `.coding-hermes/tasks.md`.
- **Foreman:** NOT woken — live scheduler API shows cooldown_s=900
  (briefing's 259200s was stale) and ticks flowing (latest completed 09-06
  04:28, outcome=committed). Enabled, healthy, already fast.
2026-09-07 | SHIPPABLE | 22s t2fs | friction 6 | 5 findings

## 2026-09-20 — h3-shim dogfood run (6th cycle)

- **Verdict:** SHIPPABLE
- **Promise:** install from source → scaffold harness (py/go/ts) → verify
  with the 46-test battery → manage/route via CLI → embed H3ShimLoop as
  the brain-swap.
- **Angle change (pitfall doctrine):** prior cycles covered py scaffold +
  py embedding + battery; this cycle took the untouched surfaces: go/ts
  scaffolds end-to-end (first battery runs ever on them), the AGENTS.md
  release gate (battery vs all three SDK echo examples), the 09-18 CLI
  wave, and cross-language interop (py loop → go/ts harnesses).
- **Time-to-first-success:** ~25s cold (12s shim install + 5s scaffold +
  1s go build + 0.36s battery, 46/46 exit 0); bunker fresh-box 11s
  install → 46/46 in 0.60s on Python 3.13.
- **Friction count:** 5 (2× P1, 2× P2, 1 env quirk).
- **Top findings:** DF4-H3-SHIM-1 (P1: py client sends explicit nulls,
  ts/zod rejects → documented default loop path fails against ts
  scaffold; battery is blind by design — bypasses H3Client);
  DF4-H3-SHIM-2 (P1: session-GC fix dfa9d99 never left the py template —
  go 98/ts 197/sdk-python 87 active_sessions, unbounded);
  DF4-H3-SHIM-3 (P2 README quickstart venv trap, burned a bunker
  attempt); DF4-H3-SHIM-4 (P2 health.capabilities drift: go declares
  ['text'] while emitting end).
- **Fixes verified live:** DF3-H3-SHIM-1 (datetime serialization) FIXED;
  09-05 P2 dead-endpoint install FIXED (health-check before write);
  FOREMAN-5 route empty-explain FIXED; route --session lifecycle works.
- **Artifacts:** docs/dogfood/2026-09-20-integration.md (new),
  docs/dogfood/diagnostics.md (§ appended), skills/h3-shim-usage/SKILL.md
  (v1.4.0: pitfalls 0/6/10/13 rewritten), board rows DF4-H3-SHIM-1..4.
- **Foreman:** NOT woken per 2026-09-09 fleet law (21600s pin; injected
  rows picked up at normal cadence). Enabled=True, healthy (tick #386
  completed 2026-09-20 04:23).
- **Bunker leg:** agent 344005f2 spawned/used/destroyed (exit 0);
  INSTALL proven 11s, smoke PASS; no silent passes.

## 2026-09-22 — h3-shim dogfood run (7th cycle)

- **Verdict:** SHIPPABLE (core); one closed P1 reopened (DF5-H3-SHIM-1),
  one resurrected by scope (DF5-H3-SHIM-2).
- **Promise:** install from source → scaffold harness (py/go/ts) →
  verify with the 46-test battery → manage/route via the CLI or the
  `hermes h3` plugin → embed H3ShimLoop as the brain-swap.
- **Angle change (pitfall doctrine):** cycles 1-6 never drove the
  `hermes h3` PLUGIN end-to-end (first this cycle), and tick #388 had
  closed DF4-H3-SHIM-1/2 the day before — so this cycle re-proved both
  against real artifacts (live ts zod stack, session counters) instead
  of trusting the board. Neither survived intact.
- **Time-to-first-success:** ~35s cold (21s shim install + 9s harness
  venv + battery green first try); battery warm 477ms ±21ms (hyperfine
  ×10). Bunker: agent 16d8d4ad — clone → 11s install → 46/46 smoke
  0.59s on Python 3.13.5, destroyed (`bunker list` empty).
- **Friction count:** 6 (3 constructor/signature guesses the docs
  never show: `H3Client(endpoint=)`, `H3ShimLoop(session_id, context)`
  required, `run(Message)` not `run(str)`; `go mod tidy -q` invalid;
  categories label≠token; ~50s cold venv setup timeout).
- **Top findings:** DF5-H3-SHIM-1 (P1: default-Context embed still 400s
  vs ts zod — closing merge f897019 only changed duration_ms to int;
  the closing test admits "invisible to the Python-mock layer" in its
  own docstring); DF5-H3-SHIM-2 (P1: END-purge only on result_count>=2
  → ~96 leaks/battery on ALL scaffolds, py 1440→1536 live, fresh
  bunker 192 after ONE run); DF5-H3-SHIM-3 (P1: documented
  `cp -r h3 ~/.hermes/plugins/h3/` nests the fresh copy into an
  existing dir → stale Aug-7 mirror served "unrecognized arguments"
  for verify/route AND exited 0; refresh in place → all 9 commands
  green with correct RCs); DF5-H3-SHIM-4 (P2: --categories takes
  tokens, not the display labels the battery prints).
- **Also verified live:** battery triad post-#388: py 46/46 0.54s, ts
  46/46 0.28s, go 46/46 0.23s (all exit 0); DF4-H3-SHIM-4
  (capabilities drift) still reproduces, still pending; battery exit
  codes honest (2 on dead endpoint / unknown category, never 0/0).
- **Artifacts:** `docs/dogfood/2026-09-22-integration.md` (new),
  `docs/dogfood/diagnostics.md` (2026-09-22 section appended),
  `skills/h3-shim-usage/SKILL.md` (v1.5.0: pitfalls 6/10 rewritten,
  14/15 added), `.coding-hermes/tasks.md`, board rows
  DF5-H3-SHIM-1..4 + events 424-427, this log.
- **Foreman:** NOT woken per 2026-09-09 fleet law (21600s pin;
  injected rows picked up at normal cadence). Enabled=True,
  healthy (tick #388 completed 2026-09-21 06:16).

