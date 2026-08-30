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

- `h3-test` — the 45-test H3 compliance battery (`h3-test --endpoint <url>`)
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

## Components

- `protocol.py` — Pydantic models (generated from get-h3/protocol)
- `client.py` — REST client for harness communication
- `loader.py` — Harness discovery, health check, session routing
- `shim_loop.py` — Main H3ShimLoop
- `native.py` — Native Hermes loop wrapper
- `test_battery.py` — 45 compliance tests (THE GATE)
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
