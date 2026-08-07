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
