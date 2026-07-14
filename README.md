# H3 Shim

Python plugin for Hermes Core. Implements the H3 protocol on the Hermes side.

## Install

```bash
pip install hermes-h3-shim
```

## Quickstart

```bash
# Run the test battery against any H3 harness
h3-test --endpoint http://localhost:9191
```

## Components

- `protocol.py` — Pydantic models (generated from get-h3/protocol)
- `client.py` — REST/gRPC client for harness communication
- `loader.py` — Harness discovery, health check, session routing
- `shim_loop.py` — Main H3ShimLoop
- `native.py` — Native Hermes loop wrapper
- `test_battery.py` — 43 compliance tests (THE GATE)
- `cli.py` — `hermes h3` subcommands

## Development

```bash
make install   # create venv + install deps
make build     # verify imports
make test      # run tests
make lint      # ruff check
make fmt       # ruff format
```

## Reference

Specs: `get-h3/h3` → `specs/05-Test-Battery.md`, `specs/06-Hermes-Core-Integration.md`
