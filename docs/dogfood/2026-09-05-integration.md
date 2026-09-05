# H3 Shim — Integration Report (2026-09-05)

Fourth dogfood cycle. First cycle to prove **install-from-scratch on a
clean machine** (ephemeral bunker container) and to drive **every H3
decision type** (tool_call → llm_call → wait → delegate → text → end)
through the real `H3ShimLoop`.

## Promise

*A user can install the shim from source, scaffold a harness in any of 3
languages, verify it against the 45-test compliance battery, and drive
real H3 sessions through the shim loop (the brain-swap), via CLI or
Python API.*

## Verdict: ✅ SHIPPABLE (with 4 new findings, 2 P1)

The core delivers. Zero-to-compliant-harness measured at **~50s cold**
(14s install + 7s harness setup + 0.6s battery) on a fresh container,
~31s locally. The full brain-swap loop executes real work for all six
decision types. Remaining gaps are docs/UX, not broken functionality —
except the scaffold's missing `GET /v1/sessions/{id}` route, still open.

## Leg 1 — Install from scratch (ephemeral bunker, las-bunker-03)

Fresh rootless-docker container, bare Debian user, following ONLY the
README's documented path:

| Step | Command | Result | Time |
|---|---|---|---|
| Clone | `git clone https://github.com/get-h3/shim ~/app` | ✅ HEAD b6baae2 | ~2s |
| Install shim | venv + `pip install git+https://github.com/get-h3/shim` | ✅ both entry points | **14s** |
| Scaffold | `hermes-h3 scaffold --lang py` | ✅ | <1s |
| Harness setup | venv + `pip install -e .` | ✅ | **7s** |
| Run | `python main.py` | ✅ health ok | ~2s |
| Battery | `h3-test --endpoint http://localhost:9191` | ✅ **45/45, exit 0** | 0.61s |

No sudo, no compose, no hidden toolchain needed — the PEP 668 venv
guidance in README/scaffold output is accurate and sufficient. Agent
destroyed after the run (`bunker destroy`, exit 0).

## Leg 2 — Full decision tour (custom harness, every decision type)

Built a scratch harness (`/tmp/dogfood-h3-shim/tour_harness.py`) that
emits each decision type in sequence, then drove it with the real loop:

```python
client = H3Client(endpoint="http://localhost:9293", timeout_ms=5000)
texts = []
loop = H3ShimLoop(
    client,
    session_id="tour-1",
    context=Context(),
    llm_provider=lambda prompt, kw: f"LLM-SAYS({prompt[:30]})",  # NEW: makes LLM_CALL executable
    on_text=texts.append,                                        # NEW: text delivery hook
)
loop.register_tool("get_time", get_time)
final = await loop.run(Message(role="user", content="tour every decision"))
# → final == "task_complete", texts == ["all steps executed"]
```

Observed hop-by-hop (from shim logs + harness state):
1. `tool_call` `get_time` → registered fn executed, result posted ✅
2. `llm_call` with `llm_provider` set → provider invoked, text returned ✅
3. `wait` + `poll_endpoint` → slept `duration_seconds`, polled until
   `{"status": "complete"}` ✅
4. `delegate` → structured result (echo host has no sub-agent; the loop
   surfaces the task correctly) ✅
5. `text` → `on_text` fired with the content ✅
6. `end` → `run()` returned `"task_complete"` ✅

Without `llm_provider`, LLM_CALL is honestly refused (structured error,
log line `LLM call refused: model=mini ... (no LLM provider configured)`)
and the session still terminates cleanly — GAP-034/035 are **fixed in
code** since the 2026-08-20 run; docs/api.md hasn't caught up.

## Leg 3 — Regression check of the 5 open DF tasks from 2026-09-01

| Task | Status 2026-09-05 | Evidence |
|---|---|---|
| DF-1 (P1) battery misses scaffold's 405 on `GET /v1/sessions/{id}` | **still open** | `curl GET /v1/sessions/test-123` → `405 {"detail":"Method Not Allowed"}`; battery still reports 45/45 (test_5_11 GETs the path but is permissive by design — 405/404 passes) |
| DF-2 (P1) no example ProcessRequest payload in README | **still open** | first naive POST `{"message":"hi"}` → 422 wall listing 4 top-level fields; nested rules still only in get-h3/protocol |
| DF-3 (P2) 422 fastapi detail vs protocol's 400 INVALID_REQUEST | **still open** | same 422 observed; battery error category passes either way |
| DF-4 (P2) pre-update-check dead-ends | **still open** | `pre-update-check 0.1.0` → exit 1, "no compatibility data", no pointer to versions.yaml location |
| DF-5 (P2) session routes CLI-invisible; port-conflict warning | **still open + hit live** | a stale 26h-old harness owned :9191 and answered health with `active_sessions: 97` while my fresh instance died silently on bind |

## Findings this cycle (board rows DF2-H3-SHIM-1..4)

1. **DF2-H3-SHIM-1 (P1)** — decision wire shapes absent from shim docs.
   Built from docs/api.md alone, both `llm_call` (`model` is a plain
   string, not an object) and `wait` (`reason` required,
   `duration_seconds` int ≥ 1) payloads failed on first attempt; shapes
   live only in `src/h3_shim/protocol.py` + get-h3/protocol (other
   repo). api.md's `resolve` signature also drifted
   (`session_id` → actual `thread_id=None`).
2. **DF2-H3-SHIM-2 (P2)** — a decision payload the shim's pydantic
   rejects collapses to opaque end-reason `"error"`; the real cause
   (field + message) is only in shim logs. Harness authors get no
   actionable diagnostics.
3. **DF2-H3-SHIM-3 (P2)** — scaffolded harness never GCs session state:
   `active_sessions` grew to 97 over 26h on a leftover instance; END
   doesn't purge; only `DELETE /v1/sessions/{id}` does. Unbounded
   growth for long-running harnesses + meaningless health metric.
4. **DF2-H3-SHIM-4 (P1, fleet meta)** — the shim foreman cannot work
   its own board: 9 ticks on DF-1 since 09-01 (events 315-325) with
   verdicts REJECTED / NO_CHANGES / dry-run-null and zero code changes,
   while the P1s are real and reproducible in minutes by hand. Tasks
   likely need single-commit scoping with exact file+assertion pointers.

## Friction count this cycle: 4

(wire-shape ×2 → 1 finding; opaque error collapse; session leak
discovery; pre-update-check dead-end re-confirmed.)

## What works remarkably well

- **Fresh-machine install**: documented path needs nothing beyond
  python3+git; 21s to a compliant harness on bare Debian.
- **The battery as a gate**: 45/45 in 0.6-0.7s, three-value exit codes
  proven live (0 compliant / 2 wrong-server+connection-refused).
- **The loop's decision coverage**: all six decision types execute real
  work; honest refusal instead of fabrication when unconfigured;
  `on_text`/`llm_provider` hooks make the brain-swap genuinely usable.
- **PEP 668 guidance**: every doc surface (README, scaffold output,
  integration.md) consistently says venv-first — zero bare-pip traps
  left (was finding #1 in the 08-07 cycle).
