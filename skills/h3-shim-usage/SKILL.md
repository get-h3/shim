---
name: h3-shim-usage
description: >-
  How to USE the H3 shim (get-h3/shim) for real: install, scaffold a
  harness, run the 46-test compliance battery, manage harnesses and
  routing, drive sessions through the shim loop, and use the hermes h3
  plugin. Includes pitfalls from the 2026-08-07, 2026-08-20, 2026-09-05
  and 2026-09-06 dogfood runs. Load this before touching the shim, its
  tests, or any get-h3 harness verification task.
version: 1.3.0
category: software-development
---

# H3 Shim — Usage Skill

The H3 shim is the Hermes-side implementation of the H3 "brain-swap"
protocol: external agent systems (OpenCode, CrewAI, LangChain, ...) become
the thinking brain of Hermes. This skill teaches how to actually run it.

## What it does / entry points

- `h3-test` — the 46-test H3 compliance battery (black-box, <1s; exit
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

## Quickstart (proven working, 2026-09-06 — 46/46 in ~20s cold)

```bash
# Install (not on PyPI — from source/git; PEP 668: use a venv)
python3 -m venv venv && source venv/bin/activate
pip install git+https://github.com/get-h3/shim   # or: pip install /path/to/shim

hermes-h3 scaffold --lang py            # generates ./h3-harness-py (self-contained)
cd h3-harness-py && python3 -m venv .venv && source .venv/bin/activate
pip install -e . && python main.py      # :9191 (honours PORT)
h3-test --endpoint http://localhost:9191        # expect TOTAL 46/46 PASSED, exit 0
hermes-h3 install my-harness --endpoint http://localhost:9191 --set-default
hermes-h3 verify && hermes-h3 test && hermes-h3 route
```

All three scaffold templates (py/go/ts) build and pass 46/46 (py verified
again 2026-09-06 on a fresh bunker container: 12s shim install + 5s harness
setup + 0.64s battery, 46/46 exit 0).

## Driving a session (the brain-swap) — proven working, updated 2026-09-05

```python
from h3_shim.client import H3Client
from h3_shim.protocol import Context, Identity, Message
from h3_shim.shim_loop import H3ShimLoop

client = H3Client(endpoint="http://localhost:9191")
texts = []
loop = H3ShimLoop(
    client,
    session_id="s1",
    context=Context(),
    identity=Identity(platform="telegram", chat_id="-100"),
    llm_provider=lambda prompt, kw: f"model text for: {prompt[:40]}",  # makes LLM_CALL executable
    on_text=texts.append,          # every TEXT decision's content lands here
)
loop.register_tool("get_weather", lambda city: f"sunny, 24C in {city}")
result = await loop.run(Message(role="user", content="weather in Berlin?"))
# result is the EndReason string ('task_complete'/'error'/'timeout') — NOT the final text
```

## Common pitfalls (learned the hard way)

1. **`pre-update-check` ALWAYS blocks** (GAP-033 / DF-4, still open
   2026-09-05): the package ships as v0.1.0 while `data/versions.yaml`
   requires h3_shim ≥ 1.0.0 for every supported Hermes version — every
   path exits 1 "Update blocked". Don't treat this as a real signal
   until the version matrix ships real values.
2. **`LLM_CALL` is now executable — provide `llm_provider`** (GAP-034
   fixed in code, verified 2026-09-05): pass
   `llm_provider=lambda prompt, kw: "<model text>"` to `H3ShimLoop`;
   without it, LLM_CALL decisions are honestly refused with a structured
   error result (never fabricated).
3. **`run()` returns the EndReason string; text arrives via `on_text`**
   (GAP-035 fixed in code, verified 2026-09-05): pass
   `on_text=callback` to `H3ShimLoop` — it fires for every TEXT decision
   with the content. `docs/api.md`'s "returns final assistant text" is
   still wrong on the return contract.
4. **Decision wire shapes: learn them from `protocol.py`, not api.md**
   (GAP-036 + DF2-H3-SHIM-1): the discriminator is a top-level
   `decision` field; sub-payloads nest under `llm_call`/`text`/`end`/
   `tool_call`/`wait`/`delegate`. Sharp edges verified live 2026-09-05:
   `llm_call.model` is a plain **string** (not an object); `wait.reason`
   is **required** and `duration_seconds` is an int ≥ 1; `delegate` is
   `{"task": ...}` (no `harness`/`prompt` keys). A wrong shape crashes
   the hop with a pydantic ValidationError and the loop surfaces only
   end-reason `"error"` (DF2-H3-SHIM-2) — cause is in the shim's logs.
5. **Wrong-server detection is real**: `h3-test` vs a non-H3 server prints
   `does not look like an H3 endpoint` and exits 2 — correct (GAP-003),
   not a bug. The warning may dump raw HTML bodies (GAP-037, cosmetic).
6. **Port collisions are silent**: if a previous harness still holds :9191,
   a new one fails to bind and the battery happily tests the OLD one —
   hit again on 2026-09-05 (a stale harness with 97 phantom sessions was
   answering). Always confirm which process answers (`lsof -i :9191`) or
   use a distinct port (DF2-H3-SHIM-3: scaffolded harness never GCs
   sessions; use `DELETE /v1/sessions/{id}` for teardown).
7. **`--categories` now works** (GAP-006): tokens map to display labels;
   unknown tokens error with exit 2. `h3-test --categories health` runs 7/7.
8. **Plugin `--config` works before OR after the subcommand** (GAP-009):
   `hermes h3 list --config X` and `hermes h3 --config X list` both work.
9. **`H3Loader` is asyncio-native** (DF2-H3-SHIM-1 follow-up):
   `resolve()` is a coroutine — `await loader.resolve(platform, chat_id,
   thread_id)`; clients live in `loader.harnesses` (dict name →
   `H3Client`). `H3Client` has NO generic `.get()` — methods are
   `health/process/result/cancel/close`, all async.
10. **NEVER set `Message.timestamp`** (DF3-H3-SHIM-1, found 2026-09-06):
   `H3Client` serializes with `model_dump()` not `model_dump(mode="json")`,
   so the pydantic-coerced `datetime` reaches httpx's json.dumps and the
   whole run() collapses to end-reason `"error"` (traceback log-only).
   Omit timestamp until DF3-H3-SHIM-1 closes.
11. **Constructor shapes the docs imply wrong** (DF3-H3-SHIM-4):
   `identity` takes an Identity-shaped dict (or the model), NOT the tuple
   `("shim", session_id)` from api.md's prose; `context["memory"]` is a
   **string**, not a dict; and give `H3Loader(config)` a
   `default_harness` or un-routed sessions resolve to `"native"` and
   `harnesses["native"]` KeyErrors.
12. **Battery is now 46 tests** (test_5_12 session GET, commit 5762d6f):
   any doc saying 44 or 45 is stale (DF3-H3-SHIM-3 tracks the sweep).

## Doing verification tasks (the gate)

The battery is THE gate for any harness. To judge a harness:
`h3-test --endpoint <url>` → exit 0 = compliant. For CI/reporting use
`--json` (`total/passed/failed/latency/results`, plus
`not_h3_endpoint: true` + `reason` when the target isn't an H3 server).
To test a harness you don't want to run yet: scaffold it, run it, battery it.

## Reference

- `docs/integration.md` — full user guide (accurate as of 2026-08-20).
- `docs/api.md` — programmatic API; Decision wire-shape section is
  accurate and complete (fixed by the foreman 2026-09-05, commit 4232ed3);
  H3ShimLoop constructor prose still implies tuple identity (DF3-H3-SHIM-4).
- `docs/dogfood/2026-09-06-integration.md` — latest verified walkthrough:
  full all-six-decisions embedding-host consumer + fresh bunker install.
- `docs/dogfood/diagnostics.md` — how it's built + the error trail.
- Specs: `get-h3/h3` → `specs/05-Test-Battery.md`, `specs/06-Hermes-Core-Integration.md`.
