# 2026-09-06 — Deep integration: custom harness + embedding host, from docs alone

**Verdict: SHIPPABLE (again).** The two 09-05 P1s were **fixed by the foreman and
verified live this cycle** — first cycle in this project's history where the board
loop actually closed its own dogfood findings.

## What was proven this cycle

1. **DF2-H3-SHIM-1 FIXED (foreman, tick #371, commit 4232ed3):** the per-decision
   wire-shape reference in `docs/api.md` is real and complete. A custom
   multi-decision harness written **from those docs alone** parsed every decision
   the shim sent and drove the full tour on the first run — `llm_call.model` as
   string, `wait.reason` required, all six shapes correct. Battery on the scaffold:
   46/46.
2. **DF-H3-SHIM-FOREMAN-1 FIXED (foreman, tick #373, commit 5762d6f):** the py
   scaffold now implements `GET /v1/sessions/{id}` and the battery grew a new test
   for it (45→46). The 09-05 hole (battery blessing a 405-ing scaffold) is closed.
3. **Fresh-install leg (bunker las-bunker-03, agent 39a24489):** clone from
   documented origin (github.com/get-h3/shim) → `pip install -e ~/app` in **12s** →
   scaffold + harness venv in **5s** → harness up → **46/46 battery, exit 0,
   0.64s** on a bare Debian user with no sudo, no compose, no hidden deps.
   Regression note: a fresh harness still reports `active_sessions: 98` after one
   battery run — DF2-H3-SHIM-3 (session GC) reproduces.

## The deep integration (the part tests never do)

Wrote a consumer embedding `H3ShimLoop` as a library against the custom harness,
with every hook wired: `register_tool` (tool_call), `llm_provider` (llm_call),
`on_text` (text), natural WAIT+poll, END `task_complete`. Full tour works.

What cost iterations (evidence for DF3 tasks):

| Trap | Symptom | Root cause |
|---|---|---|
| `Message(timestamp=...)` | `TypeError: Object of type datetime is not JSON serializable` inside httpx, masked by the loop as `END_REASON: error` | `client.py:111,141` use `json=req.model_dump()` instead of `model_dump(mode="json")` — **library bug**, filed DF3-H3-SHIM-1 |
| `identity=("shim", sess)` | pydantic `Input should be a valid dictionary or instance of Identity` | docs' `("shim", session_id)` placeholder reads as a tuple literal; real shape is `Identity` dict (wire example is correct) — filed DF3-H3-SHIM-4 |
| `context={"memory": {}}` | `memory Input should be a valid string` | `Context.memory` is `str`, only visible in `protocol.py` — filed DF3-H3-SHIM-4 |
| `H3Loader(config)` without `default_harness` | `resolve()` falls to `"native"`, then `harnesses["native"]` KeyError | Usage example omits the key — filed DF3-H3-SHIM-4 |
| Opaque error surface | every one of the above surfaced as bare `error` end-reason | DF2-H3-SHIM-2 (open, reproduced twice more this cycle) |

## Battery behavior on a minimal harness (informative, not a finding)

My docs-only harness scored **41/46** on first battery run. The five failures were
all *behavioral contract* demands beyond payload shapes — history preservation,
tool name must exist in `context.tools`, no tool hallucination on empty context,
`POST /v1/cancel`, session-not-found 404 — and every failure detail was
self-explanatory without reading battery source. Good black-box design; the docs
could still list the behavioral contract explicitly (folded into DF3-H3-SHIM-4's
quickstart idea).

## Foreman meta-finding

DF-H3-SHIM-FOREMAN-2 (README ProcessRequest example) took **3 ticks on 09-06**
(04:13, 08:09, 09:24) all `worker=dry-run guard=not_run verdict=null commit=none`
— the worker-dispatch lane is still barren even though foreman-direct commits
landed twice. Filed DF3-H3-SHIM-2 with the fix recipe that worked
(re-scope to foreman-direct single-commit task, like DF2-H3-SHIM-1).
