# AGENTS.md — H3 Shim

Python plugin for Hermes Core. Implements the H3 protocol on the Hermes side: client, loader, shim loop, test battery, CLI.

## Package

- PyPI: `hermes-h3-shim`
- Location in Hermes: standalone pip package — **not** inside Hermes Core
  (`hermes_cli/agent/shims/h3/` does not exist there). Optional `hermes h3`
  plugin in `h3/` (copy to `~/.hermes/plugins/h3/`); see `docs/integration.md`.

## Components

- `protocol.py` — Pydantic models (generated from get-h3/protocol)
- `client.py` — REST/gRPC client for harness communication
- `loader.py` — Harness discovery, health check loop, session routing
- `shim_loop.py` — Main H3ShimLoop: process → execute → result → loop
- `native.py` — Native Hermes loop as H3 harness wrapper
- `test_battery.py` — **44 compliance tests. THE GATE.**
- `cli.py` — `hermes-h3` subcommands (install, list, pre-update-check, route, scaffold, test, uninstall, use, verify); the `h3/` plugin exposes them as `hermes h3 <cmd>`

## The Test Battery

`test_battery.py` is the single most important file. It verifies ANY harness against the H3 protocol. 44 tests, 6 categories (E2E region-style). Anyone can run:

```bash
h3-test --endpoint http://localhost:9191
```

`h3-test` (and `hermes h3 test` / `hermes-h3 test`) uses three exit codes
so CI can distinguish a real compliance failure from a wrong server:

| Code | Meaning |
|------|---------|
| `0`  | **Compliant** — the target is an H3 endpoint and all checks passed. |
| `1`  | **Compliance failure** — the target is a real H3 endpoint, but some protocol checks failed. Fix the harness. |
| `2`  | **Not an H3 endpoint** — connection refused, non-JSON body, HTTP >= 400, or the `/v1/health` payload is missing required H3 fields. This is NOT a compliance failure: check the URL and that the harness is actually running. |

## Development

- GitReins quality gate mandatory
- Test battery must pass against all 3 SDK echo examples before release

## Reference

Specs: `get-h3/h3` → `specs/05-Test-Battery.md`, `specs/06-Hermes-Core-Integration.md`
