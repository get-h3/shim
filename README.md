# H3 Shim

Python plugin for Hermes Core. Implements the H3 protocol on the Hermes side.

## Install

The package is **not published to PyPI** — install from source:

```bash
# PEP 668 distros (Ubuntu 24+, Debian 12+) refuse bare pip installs —
# always use a venv:
python3 -m venv .venv
source .venv/bin/activate
pip install git+https://github.com/get-h3/shim
```

This installs two CLI entry points:

- `h3-test` — the 46-test H3 compliance battery (`h3-test --endpoint <url>`)
- `hermes-h3` — harness management (`install`, `list`, `test`, `verify`, `scaffold`, ...)

## Quickstart

```bash
# 1. Stand up a demo harness first (scaffolds h3-harness-py/ + run instructions):
hermes-h3 scaffold --lang py
cd h3-harness-py
# PEP 668 distros (Ubuntu 24+, Debian 12+) refuse bare pip installs —
# always use a venv:
python3 -m venv .venv
source .venv/bin/activate
pip install -e . && python main.py   # listens on :9191

# 2. In another terminal, run the test battery against it:
# The new terminal does not inherit step 1's venv activation — reactivate it so h3-test is on PATH:
source .venv/bin/activate
h3-test --endpoint http://localhost:9191
```

The scaffolded harness is a minimal H3 echo server — see `hermes-h3 scaffold --help`
for `--lang go|py|ts`. Ready-made echo examples also live in the sibling SDK repos
(`get-h3/sdk-go/examples/echo`, `get-h3/sdk-python/src/h3_harness/examples/echo.py`,
`get-h3/sdk-typescript/src/examples/echo.ts`).

### Exit codes

`h3-test` (and `hermes h3 test` / `hermes-h3 test`) uses three exit codes
so CI can distinguish a real compliance failure from a wrong server:

| Code | Meaning |
|------|---------|
| `0`  | **Compliant** — the target is an H3 endpoint and all checks passed. |
| `1`  | **Compliance failure** — the target is a real H3 endpoint, but some protocol checks failed. Fix the harness. |
| `2`  | **Not an H3 endpoint** — connection refused, non-JSON body, HTTP >= 400, or the `/v1/health` payload is missing required H3 fields. This is NOT a compliance failure: check the URL and that the harness is actually running. |

See `docs/integration.md` for the full troubleshooting matrix.

### Send a request

A harness is driven by `POST /v1/process` with a `ProcessRequest` body. Its four
top-level fields — `session_id`, `message`, `identity`, `context` — are all
required, and each nested object has its own required fields (see
`get-h3/protocol` → `schemas/v1/process-request.json` and `common.json`).
A valid, copyable payload:

```json
{
  "session_id": "sess-7f3a9c",
  "message": {
    "role": "user",
    "content": "Book a flight to Medellín",
    "timestamp": "2026-08-20T14:05:42Z"
  },
  "identity": {
    "platform": "telegram",
    "chat_id": "-1001234567890",
    "user_name": "Alice",
    "user_id": "424242"
  },
  "context": {
    "history": [
      {"role": "user", "content": "Hi, I need help with travel"},
      {"role": "assistant", "content": "Sure — where are you flying from, and when?"}
    ],
    "tools": [
      {
        "name": "terminal",
        "description": "Execute shell commands on a Linux environment",
        "parameters": {
          "command": {"type": "string", "description": "Shell command to execute"}
        }
      }
    ],
    "models": [
      {
        "name": "deepseek-v4-pro",
        "provider": "deepseek",
        "cost_per_1k_input": 0.0011,
        "cost_per_1k_output": 0.0044,
        "context_window": 128000,
        "supports_vision": false,
        "supports_tool_calling": true
      }
    ],
    "config": {
      "max_iterations": 50,
      "timeout_seconds": 600
    },
    "session_state": {
      "turn_count": 1,
      "total_tool_calls": 0,
      "total_llm_calls": 1,
      "cost_so_far": 0.0003,
      "started_at": "2026-08-20T14:03:11Z"
    }
  }
}
```

Save it as `process-request.json` and POST it to the running harness:

```bash
curl -sS http://localhost:9191/v1/process \
  -H 'Content-Type: application/json' \
  -d @process-request.json
```

The harness answers with a `Decision` (`{"decision": "tool_call", ...}`,
`{"decision": "text", ...}`, …) rather than a final answer — the shim loop
executes that decision and POSTs the result back, so the harness never has to
call a tool or a model itself. Optional fields (`message.attachments`,
`identity.thread_id`, `context.memory`, `context.skills`, `context.config.*`
beyond the two required keys) may be omitted.

## Configuration

`hermes-h3` keeps its harness/session state in one YAML file
(`~/.hermes/h3/config.yaml` by default). The path is resolved in this
order, highest first: `--config <path>` (accepted before **or** after a
subcommand) → `$HERMES_H3_CONFIG` → `~/.hermes/h3/config.yaml`. So
`HERMES_H3_CONFIG=/tmp/mine.yaml hermes-h3 list` reads the override
instead of your real config, and every command that writes the file
(`install`, `use`, `scaffold`, `uninstall`) writes it there too. An
unset or blank `$HERMES_H3_CONFIG` falls back to the default path. See
`docs/integration.md` for details.

## Components

- `protocol.py` — Pydantic models (generated from get-h3/protocol)
- `client.py` — REST client for harness communication
- `loader.py` — Harness discovery, health check, session routing
- `shim_loop.py` — Main H3ShimLoop
- `native.py` — Native Hermes loop wrapper
- `test_battery.py` — 46 compliance tests (THE GATE)
- `cli.py` — the `hermes-h3` CLI (9 subcommands: install, list, pre-update-check, route, scaffold, test, uninstall, use, verify); the `h3/` plugin in this repo exposes them as `hermes h3 <cmd>`

## Development

Prerequisite: [uv](https://docs.astral.sh/uv/) (`make typecheck` runs `uv run --with mypy mypy src/`; install via `curl -LsSf https://astral.sh/uv/install.sh | sh` or `pip install uv`). Everything else uses plain `python3` + `pip` (see the Makefile).

```bash
make install   # create venv + install deps
make build     # verify imports
make test      # run tests
make lint      # ruff check
make fmt       # ruff format
```

## Reference

- `docs/integration.md` — full install → register → route → verify guide
- Specs: `get-h3/h3` → `specs/05-Test-Battery.md`, `specs/06-Hermes-Core-Integration.md`
