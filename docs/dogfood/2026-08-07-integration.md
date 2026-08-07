# H3 Shim — Real Integration Report (2026-08-07)

How a real user gets from zero to a verified, battery-passing H3 harness.
Written from an actual dogfood run (2026-08-07): every command below was
executed against a fresh venv in `/tmp/dogfood-h3-shim`, with a scratch
`HOME` so nothing touched the real `~/.hermes/h3/`.

## What this project is

The H3 ("Hermes Harness Hooks") protocol lets an external agent system
(OpenCode, CrewAI, LangChain, …) act as the *brain* of Hermes. This package is
the Hermes-side implementation: a management CLI, the REST client/loader/shim
loop, and — the crown jewel — a **43-test black-box compliance battery** that
judges whether ANY harness endpoint speaks H3.

## Install (what actually works)

```bash
python3 -m venv venv && source venv/bin/activate
pip install /path/to/get-h3/shim        # or: pip install git+https://github.com/get-h3/shim
```

Gives you two scripts: `h3-test` and `hermes-h3` (9 subcommands:
`install list pre-update-check route scaffold test uninstall use verify`).
All config lives in `~/.hermes/h3/config.yaml`, auto-created on demand;
every command except `scaffold` accepts `--config <path>`.

> ⚠️ Known issue (GAP-005): the wheel currently ships WITHOUT
> `h3_shim/__init__.py`, so `hermes-h3 pre-update-check` crashes with an
> `ImportError` traceback. Everything else works. Fix is queued on the board.

## The working example (the "aha")

```bash
# 1. Scaffold a complete harness (self-contained FastAPI app, no shim dep)
hermes-h3 scaffold --lang py            # -> ./h3-harness-py
cd h3-harness-py && pip install -e . && python main.py &   # listens :9191

# 2. Health-check it
hermes-h3 verify --endpoint http://localhost:9191
#   status: HealthStatus.OK  version: 1.0.0  caps: text   (exit 0)

# 3. THE gate — 43 tests, ~0.3s
h3-test --endpoint http://localhost:9191
#   Health & Protocol   7/7  ✅   Process Basic Flows   8/8  ✅
#   Decision Types      6/6  ✅   Result Handling       7/7  ✅
#   Error & Edge Cases  10/10 ✅   Stress & Performance  5/5  ✅
#   TOTAL 43/43 PASSED  (exit 0)

# 4. Register + manage
hermes-h3 install my-harness --endpoint http://localhost:9191 --set-default
hermes-h3 list            # *my-harness  http://localhost:9191  rest  30000
hermes-h3 route           # session -> harness table (most-specific-first)
hermes-h3 test            # battery against the default harness
hermes-h3 uninstall my-harness

# 5. Or through Hermes Core itself (the plugin)
cp -r h3 ~/.hermes/plugins/h3/ && hermes plugins enable h3
hermes h3 --config ~/.hermes/h3/config.yaml list     # --config goes BEFORE the subcommand
hermes h3 verify          # same commands, delegated to the real CLI
```

Scaffolded harness honours `PORT` env (`PORT=9192 python main.py`).
The battery also passes 43/43 against the SDK reference examples
(sdk-python `examples/echo.py` — note: it hardcodes port **8000**, not 9191).

## Errors hit and their fixes

| Symptom | Cause / fix |
|---|---|
| `hermes-h3 pre-update-check 1.0` → ImportError traceback | Wheel missing `__init__.py` (GAP-005). Tracked on board. |
| `h3-test --categories health` → `TOTAL 0/0 PASSED`, exit 0 | Category filter compares tokens vs display labels — never matches (GAP-006). Don't rely on `--categories` until fixed; the full battery is 0.3s anyway. |
| `hermes-h3 scaffold --config x` → `No such option` | `scaffold` has no `--config` (GAP-007). Run it from the directory you want the project in, or set `HOME` to a scratch dir. |
| `python echo.py` (SDK) binds :8000, not :9191 | The example hardcodes port 8000 and ignores `PORT` (GAP-007). Run it on a free port or accept 8000. |
| Battery vs a non-H3 server | Correctly warns `does not look like an H3 endpoint` and exits 2. This works — the GAP-003 fix is solid. |

## Verdict

🟡 **PROMISING-BUT-ROUGH.** The promise holds: ~6 minutes from a fresh venv
to a 43/43-verified harness, and the battery is genuinely excellent (fast,
black-box, wrong-server-proof, machine-readable `--json`). The blockers are
shipment quality (broken wheel → broken subcommand) and two silent
false-green paths — exactly the gaps green test suites can't see.
