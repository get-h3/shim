# H3 Shim

Python plugin for Hermes Core. Implements the H3 protocol on the Hermes side.

## Install

The package is **not published to PyPI** — install from source:

```bash
pip install git+https://github.com/get-h3/shim
```

This installs two CLI entry points:

- `h3-test` — the 43-test H3 compliance battery (`h3-test --endpoint <url>`)
- `hermes-h3` — harness management (`install`, `list`, `test`, `verify`, `scaffold`, ...)

## Quickstart

```bash
# Run the test battery against any H3 harness
h3-test --endpoint http://localhost:9191
```

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
- `client.py` — REST/gRPC client for harness communication
- `loader.py` — Harness discovery, health check, session routing
- `shim_loop.py` — Main H3ShimLoop
- `native.py` — Native Hermes loop wrapper
- `test_battery.py` — 43 compliance tests (THE GATE)
- `cli.py` — the `hermes-h3` CLI (9 subcommands: install, list, pre-update-check, route, scaffold, test, uninstall, use, verify); the `h3/` plugin in this repo exposes them as `hermes h3 <cmd>`

## Development

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
