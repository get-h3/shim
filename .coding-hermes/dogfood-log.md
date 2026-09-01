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
