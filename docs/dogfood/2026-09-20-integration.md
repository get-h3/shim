# Dogfood Integration — 2026-09-20 (h3-shim, HEAD 9e7a3f1)

**Verdict: SHIPPABLE.** Fifth dogfood cycle; first to drive the untouched
surfaces: go/ts scaffolds end-to-end, the SDK echo release gate, the full
2026-09-18 CLI wave, and the py shim loop driving a NON-python harness.

## Angle (why not the usual quickstart)

Cycles 1-5 (08-07 → 09-07) all exercised the py scaffold + battery + py
embedding. This cycle deliberately changed surface, not depth:

1. go + ts scaffolds: build, run, battery, session lifecycle
2. AGENTS.md's release gate: battery vs all three SDK echo examples
3. The 09-18 CLI wave: `install` health-check, `route --session`, `verify`
   positional, `install --name`, `pre-update-check`
4. Cross-language interop: py `H3ShimLoop` → go/ts harnesses
5. Fresh-box install leg on an ephemeral bunker (Debian 13, Python 3.13)

## What was run (all live numbers)

| Surface | Result |
|---|---|
| fresh venv, `pip install git+https://github.com/get-h3/shim` | 12s, both CLIs on PATH |
| `scaffold --lang py/go/ts` | all exit 0, correct run instructions |
| go scaffold: `go mod tidy` + `go build` | 0s + 1s |
| **go scaffold battery** | **46/46 PASS, exit 0, 0.36s** (p50 1.35ms) — first ever |
| **ts scaffold: `npm install` + `tsc`** | 10s + 2s, clean |
| **ts scaffold battery** | **46/46 PASS, exit 0, 0.46s** (p50 2.74ms) — first ever |
| README sample POST `/v1/process` → go | verbatim, 200, echo text decision |
| `/v1/result` → next decision; GET/DELETE session | full loop, 200/200 |
| malformed payload → go | 400 `INVALID_REQUEST` envelope with field path |
| battery vs dead endpoint | exit 2 "not an H3 endpoint" (correct) |
| sdk-python `examples/echo.py` battery | 46/46 PASS, 0.75s |
| sdk-go `examples/echo` battery | 46/46 PASS, 0.79s |
| py `H3ShimLoop.run()` → go harness | `task_complete`, on_text fired 2x, no errors |
| `Message(timestamp=datetime)` via H3Client | **works** (DF3-H3-SHIM-1 fix verified) |
| `H3ShimLoop` default identity → ts scaffold | **400 → run() = 'error'** (new P1, DF4-H3-SHIM-1) |
| bunker: clone → install → scaffold → battery | 11s install, 46/46 exit 0 in 0.60s |

CLI wave (isolated `HERMES_H3_CONFIG=/tmp/.../h3-config.yaml`):

- `install --name ts-demo --endpoint <live> --set-default` → health-check
  reported `ok (version 1.0.0)` then config written, `*` default marker in list.
- `install dead-harness --endpoint http://localhost:9999` → **exit 1,
  "failed its health check … Nothing was written"** — the 09-05 P2
  (silent dead-endpoint installs) is CLOSED, fix verified live.
- `route` (empty) → friendly page explaining where routes come from
  (FOREMAN-5 fix verified). `route --session A --set-harness ts-demo` →
  saved; `route --session A` → lookup; unknown harness → exit 1 with
  known-names list; `--remove` → gone. Full lifecycle, no dead ends.
- `verify ts-demo` (positional, H3-GAP-092) and `install --name`
  (DF-H3-3) both work.
- `pre-update-check` (no args) → exit 2 usage error (missing argument).
  `pre-update-check 2.0.0` → exit 1, names the versions.yaml matrix it
  consulted and the supported list, suggests `--versions-yaml`.
- `uninstall ts-demo` → config emptied including `default_harness: null`
  (no dangling default).

## The two P1s (details on the board)

**DF4-H3-SHIM-1 — null-vs-absent optional kills py-client → ts-scaffold.**
`H3Client` posts `model_dump(mode="json")`, which still serializes unset
optionals as explicit `null` (`Identity.thread_id: null`). The ts stack's
zod schema (`z.string().optional()`) accepts absent but rejects null.
Two first-party artifacts of this repo fail each other on the DOCUMENTED
default path (`H3ShimLoop` without identity). 46/46 PASS cannot see it:
the battery bypasses H3Client by design and always sets thread_id.
Repro: `curl -d '{"identity":{"thread_id":null,...}}'` → 400; with
`thread_id:"7"` → 200.

**DF4-H3-SHIM-2 — session GC never left the py template.** go scaffold:
`active_sessions` 98 after ONE battery run (never decrements; grep finds
no purge in main.go). ts: 197 after ~4 runs. sdk-python BaseHarness (the
release-gate echo): 87 after one run. `dfa9d99` fixed only
`templates/py/main.py` and its message says the siblings were
"unchanged on purpose".

## Friction log (chronological)

1. `scaffold --dir` guessed and rejected (real flag: `--output-dir`) —
   minor; help text is clear.
2. README quickstart venv ambiguity burned one bunker attempt (harness
   venv vs outer venv for `h3-test` vs `python main.py`) → DF4-H3-SHIM-3.
3. Bunker `/tmp` not writable by the agent user → logs must go to
   `$HOME` (environment quirk, not a project bug).
4. ts health `capabilities` says `["text","end"]`, go says `["text"]`
   while both emit `end` decisions → DF4-H3-SHIM-4.
5. Non-py harnesses leak sessions (see P1 #2) — noticed because the same
   health probe kept climbing across runs.

## Fresh-box install leg (mandated)

Host bunker-las-03, agent 344005f2, ttl 2h, destroyed after (exit 0).
Python 3.13.5, Debian 13, no project toolchains. Steps: `git clone
https://github.com/get-h3/shim` (public, no creds needed) →
`python3 -m venv .venv && pip install .` (11s) → README quickstart
verbatim (`scaffold --lang py`, harness venv, `python main.py`) →
`h3-test` 46/46 exit 0 in 0.60s. One retry was mine (venv activation
sequencing), one was the README ambiguity (filed). No sudo, no compose,
no hidden deps. Note: the fresh py scaffold still shows `active_sessions:
96` after one battery — DF2-H3-SHIM-3's fix does not cover the battery's
own flow (battery never sends `/v1/result` with `end`-worthy flow for
every session; sessions end via stress-END but status-poll sessions
persist). Sub-filed under DF4-H3-SHIM-2's umbrella.

## Verdict rationale

- Works? Yes — every promised workflow completes; 4× 46/46 batteries on
  4 different harness stacks, all <1s, plus a clean 11s fresh-box install.
- Useful? Yes — it is the compliance gate for the whole get-h3 ecosystem
  and the brain-swap runtime for Hermes.
- Usable? Good and improving: the 09-18 CLI wave closed real 09-05
  frictions (dead-endpoint installs, route dead-end). Remaining friction
  is cross-language (null vs absent) and template parity (GC/capabilities).
- Trustworthy? Mostly: config writes are transactional (health-check
  before write), errors are actionable now; but non-py harness state
  grows unbounded, and health.capabilities lies on go.
