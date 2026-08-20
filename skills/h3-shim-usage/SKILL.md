---
name: h3-shim-usage
description: >-
  How to USE the H3 shim (get-h3/shim) for real: install, scaffold a
  harness, run the 44-test compliance battery, manage harnesses and
  routing, drive sessions through the shim loop, and use the hermes h3
  plugin. Includes pitfalls from the 2026-08-07 and 2026-08-20 dogfood
  runs. Load this before touching the shim, its tests, or any get-h3
  harness verification task.
version: 1.1.0
category: software-development
---

# H3 Shim — Usage Skill

The H3 shim is the Hermes-side implementation of the H3 "brain-swap"
protocol: external agent systems (OpenCode, CrewAI, LangChain, ...) become
the thinking brain of Hermes. This skill teaches how to actually run it.

## What it does / entry points

- `h3-test` — the 44-test H3 compliance battery (black-box, ~0.4s; exit
  0 = compliant, 1 = compliance failure, 2 = not an H3 endpoint).
- `hermes-h3` — harness management CLI: `install`, `list`,
  `pre-update-check`, `route`, `scaffold`, `test`, `uninstall`, `use`,
  `verify`.
- `hermes h3 <cmd>` — same 9 commands via the optional `h3/` plugin
  (`cp -r h3 ~/.hermes/plugins/h3/` + `hermes plugins enable h3`).
- Config: `~/.hermes/h3/config.yaml` (auto-created; every command accepts
  `--config <path>`, including `scaffold` since GAP-007).
- Programmatic: `H3Client`, `H3Loader`, `H3ShimLoop`, `H3TestBattery` —
  see `docs/api.md`.

## Quickstart (proven working, 2026-08-20)

```bash
# Install (not on PyPI — from source/git; PEP 668: use a venv)
python3 -m venv venv && source venv/bin/activate
pip install git+https://github.com/get-h3/shim   # or: pip install /path/to/shim

# Zero-to-verified in ~6 minutes:
hermes-h3 scaffold --lang py            # generates ./h3-harness-py (self-contained)
cd h3-harness-py && pip install -e . && python main.py &   # :9191 (honours PORT)
h3-test --endpoint http://localhost:9191        # expect TOTAL 44/44 PASSED, exit 0
hermes-h3 install my-harness --endpoint http://localhost:9191 --set-default
hermes-h3 verify && hermes-h3 test && hermes-h3 route
```

All three scaffold templates (py/go/ts) build and pass 44/44 (verified
2026-08-20). The battery also passes against the SDK echo examples
(sdk-python `examples/echo.py` binds :8000, not :9191).

## Driving a session (the brain-swap) — proven working

```python
from h3_shim.client import H3Client
from h3_shim.protocol import Context, Identity, Message
from h3_shim.shim_loop import H3ShimLoop

client = H3Client(endpoint="http://localhost:9191")
loop = H3ShimLoop(client, session_id="s1", context=Context(),
                  identity=Identity(platform="telegram", chat_id="-100"))
loop.register_tool("get_weather", lambda city: f"sunny, 24C in {city}")
result = await loop.run(Message(role="user", content="weather in Berlin?"))
# result is the EndReason string ('task_complete'/'error'/'timeout') — NOT the final text
```

## Common pitfalls (learned the hard way)

1. **`pre-update-check` ALWAYS blocks** (GAP-033): the package ships as
   v0.1.0 while `data/versions.yaml` requires h3_shim ≥ 1.0.0 for every
   supported Hermes version — every supported version exits 1 "Update
   blocked". Don't treat this as a real signal until GAP-033 lands.
2. **`LLMCall` decisions are refused, not executed** (GAP-034): the loop
   returns `ExecutionResult(type="error", data={error: "LLM not configured"})`
   and logs `LLM call refused`. This is intentional and safe (GAP-023 removed
   the old fabricated response) — but plan for it: harnesses that delegate
   LLM calls to Hermes will see errors.
3. **`H3ShimLoop.run()` returns the EndReason string**, not the assistant's
   final text (GAP-035). `docs/api.md` is wrong on this ("Returns the final
   assistant text"). TEXT decisions are only logged — there is no delivery
   hook yet. If you need the text, capture it yourself or subclass.
4. **The Decision wire shape is undocumented** (GAP-036): the discriminator
   is a top-level `decision` field (`llm_call`/`text`/`end`/...), NOT `type`;
   sub-payloads nest under `llm_call`/`text`/`end`; `text` must be a dict
   `{"content": ..., "finished": ...}`. Follow `protocol.py` or the SDK
   examples until docs/api.md grows JSON examples.
5. **Wrong-server detection is real**: `h3-test` vs a non-H3 server prints
   `does not look like an H3 endpoint` and exits 2 — correct (GAP-003), not a
   bug. The warning may dump raw HTML bodies (GAP-037, cosmetic).
6. **Port collisions are silent**: if a previous harness still holds :9191,
   a new one fails to bind and the battery happily tests the OLD one. Always
   confirm which process answers (`lsof -i :9191`) or use a distinct port.
7. **`--categories` now works** (GAP-006): tokens map to display labels;
   unknown tokens error with exit 2. `h3-test --categories health` runs 7/7.
8. **Plugin `--config` works before OR after the subcommand** (GAP-009):
   `hermes h3 list --config X` and `hermes h3 --config X list` both work.

## Doing verification tasks (the gate)

The battery is THE gate for any harness. To judge a harness:
`h3-test --endpoint <url>` → exit 0 = compliant. For CI/reporting use
`--json` (`total/passed/failed/latency/results`, plus
`not_h3_endpoint: true` + `reason` when the target isn't an H3 server).
To test a harness you don't want to run yet: scaffold it, run it, battery it.

## Reference

- `docs/integration.md` — full user guide (accurate as of 2026-08-20).
- `docs/api.md` — programmatic API (has drift: run() return contract, LLM
  call execution — see GAP-034/035).
- `docs/dogfood/2026-08-20-integration.md` — latest verified walkthrough +
  shim-loop consumer example.
- `docs/dogfood/diagnostics.md` — how it's built + the error trail.
- Specs: `get-h3/h3` → `specs/05-Test-Battery.md`, `specs/06-Hermes-Core-Integration.md`.
