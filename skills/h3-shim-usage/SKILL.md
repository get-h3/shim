---
name: h3-shim-usage
description: >-
  How to USE the H3 shim (get-h3/shim) for real: install, scaffold a
  harness, run the 43-test compliance battery, manage harnesses and
  routing, and use the hermes h3 plugin. Includes known pitfalls from the
  2026-08-07 dogfood run. Load this before touching the shim, its tests,
  or any get-h3 harness verification task.
version: 1.0.0
category: software-development
---

# H3 Shim — Usage Skill

The H3 shim is the Hermes-side implementation of the H3 "brain-swap"
protocol: external agent systems (OpenCode, CrewAI, LangChain, ...) become
the thinking brain of Hermes. This skill teaches how to actually run it.

## What it does / entry points

- `h3-test` — the 43-test H3 compliance battery (black-box, ~0.3s).
- `hermes-h3` — harness management CLI: `install`, `list`,
  `pre-update-check`, `route`, `scaffold`, `test`, `uninstall`, `use`,
  `verify`.
- `hermes h3 <cmd>` — same 9 commands via the optional `h3/` plugin.
- Config: `~/.hermes/h3/config.yaml` (auto-created; `--config` overrides
  except on `scaffold`).

## Quickstart (proven working, 2026-08-07)

```bash
# Install (not on PyPI — from source/git)
pip install git+https://github.com/get-h3/shim    # or: pip install /path/to/shim

# Zero-to-verified in ~6 minutes:
hermes-h3 scaffold --lang py            # generates ./h3-harness-py (self-contained)
cd h3-harness-py && pip install -e . && python main.py &   # :9191
h3-test --endpoint http://localhost:9191        # expect TOTAL 44/44 PASSED, exit 0
hermes-h3 install my-harness --endpoint http://localhost:9191 --set-default
hermes-h3 verify && hermes-h3 test && hermes-h3 route
```

## Common pitfalls (learned the hard way)

1. **`pre-update-check` crashes** with `ImportError: cannot import name
   '__version__' from 'h3_shim'` — the wheel ships without
   `__init__.py` (GAP-005, P0, queued). Don't use it until fixed; don't
   "fix" it by editing site-packages — the real fix is in pyproject.toml
   (delete the hatchling `include` block).
2. **`h3-test --categories X` reports `0/0 PASSED` exit 0** — the filter
   is broken (GAP-006). Always run the FULL battery (it's 0.3s anyway).
   Never trust a subset run's green.
3. **`scaffold` has no `--config`** (GAP-007) — run it from the directory
   you want the project in; or set `HOME` to a scratch dir to keep
   `~/.hermes/h3/config.yaml` untouched.
4. **SDK python echo example binds :8000, not :9191**, and ignores `PORT`
   (GAP-007). The scaffolded harness DOES honour `PORT`.
5. **`hermes h3 --config <path> <cmd>`** — `--config` goes BEFORE the
   subcommand.
6. **Wrong-server detection is real**: pointing `h3-test` at a non-H3
   server prints `does not look like an H3 endpoint` and exits 2 — that's
   correct behaviour (GAP-003 fix), not a bug.

## Doing verification tasks (the gate)

The battery is THE gate for any harness. To judge a harness:
`h3-test --endpoint <url>` → exit 0 = compliant. For CI/reporting use
`--json` (`total/passed/failed/latency/results`). To test a harness you
don't want to run yet: scaffold it, run it, battery it.

## Reference

- `docs/integration.md` — full user guide (note: has drift, see GAP-007).
- `docs/dogfood/2026-08-07-integration.md` — verified end-to-end walkthrough.
- `docs/dogfood/diagnostics.md` — how it's built + the error trail.
- Specs: `get-h3/h3` → `specs/05-Test-Battery.md`, `specs/06-Hermes-Core-Integration.md`.
