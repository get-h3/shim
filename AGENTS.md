# AGENTS.md — H3 Shim

Python plugin for Hermes Core. Implements the H3 protocol on the Hermes side: client, loader, shim loop, test battery, CLI.

## Package

- PyPI: `hermes-h3-shim`
- Location in Hermes: `hermes_cli/agent/shims/h3/`

## Components

- `protocol.py` — Pydantic models (generated from get-h3/protocol)
- `client.py` — REST/gRPC client for harness communication
- `loader.py` — Harness discovery, health check loop, session routing
- `shim_loop.py` — Main H3ShimLoop: process → execute → result → loop
- `native.py` — Native Hermes loop as H3 harness wrapper
- `test_battery.py` — **43 compliance tests. THE GATE.**
- `cli.py` — `hermes h3` subcommands

## The Test Battery

`test_battery.py` is the single most important file. It verifies ANY harness against the H3 protocol. 43 tests, 6 categories (E2E region-style). Anyone can run:

```bash
h3-test --endpoint http://localhost:9191
```

Exit code 0 = compliant. Exit code non-zero = fix your harness.

## Development

- GitReins quality gate mandatory
- Test battery must pass against all 3 SDK echo examples before release

## Reference

Specs: `get-h3/h3` → `specs/05-Test-Battery.md`, `specs/06-Hermes-Core-Integration.md`
