# Contributing to H3 Shim

The shim is the Hermes-side implementation of the H3 protocol — the bridge between Hermes Core and external agent harnesses. It's a Python package published to PyPI as `hermes-h3-shim`.

## Development Setup

```bash
cd shim/
python -m venv .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Project Structure

```
shim/
├── src/h3_shim/
│   ├── protocol.py       # Pydantic models (generated from protocol repo)
│   ├── client.py         # REST client for harness communication
│   ├── loader.py         # Harness discovery, health check, session routing
│   ├── shim_loop.py      # H3ShimLoop: process → execute → result → loop
│   ├── native.py         # Native Hermes loop as H3 harness wrapper
│   ├── cli.py            # `hermes h3` subcommands (8 commands)
│   ├── test_battery.py   # 43 compliance tests — THE GATE
│   └── upgrade_check.py  # Hermes update pre-flight hook
├── tests/
│   ├── test_protocol.py
│   ├── test_client.py
│   ├── test_loader.py
│   ├── test_shim_loop.py
│   ├── test_cli.py
│   └── test_upgrade_check.py
└── scripts/
    └── sync_protocol.py  # Regenerate types from upstream protocol
```

## Before Making Changes

### Run Tests

```bash
python -m pytest tests/ -v
# 151 unit tests
```

### Run the Test Battery

```bash
# Against the Go echo harness
h3-test --endpoint http://localhost:9191
# 43 compliance tests, 6 regions
```

### Sync Protocol Types

If the upstream protocol changed:

```bash
python scripts/sync_protocol.py
```

This regenerates `src/h3_shim/protocol.py` from `get-h3/protocol` schemas.

## Making Changes

### Protocol Changes

- Regenerate types with `sync_protocol.py` after upstream protocol changes
- Never hand-edit generated Pydantic models
- If a protocol change breaks Pydantic validation, fix the generation script, not the output

### Shim Loop Changes

- `shim_loop.py` is the core — changes here affect every session
- All new decision types must be handled by the loop's executor
- Test with a real harness endpoint, not mocks

### CLI Changes

- New subcommands go in `cli.py`
- Every subcommand gets a `--help` entry
- CLI integration tests in `tests/test_cli.py`

### Test Battery Changes

- New test regions go in `test_battery.py`
- Tests must pass against all 3 SDK echo examples
- Tests are protocol-level — they verify harnesses, not the shim itself

## Quality Gates

### Pre-Commit

```bash
make lint       # ruff check + ruff format --check
make test       # pytest (151 tests)
make typecheck  # mypy src/
```

### CI Pipeline

GitHub Actions runs on every PR:
1. Lint (ruff)
2. Type check (mypy)
3. Unit tests (pytest, 151 tests)
4. Test battery against Go echo harness (43 compliance tests)
5. Test battery against Python echo harness
6. Test battery against TypeScript echo harness

All must pass.

## Release

```bash
# After merge to main, create a tag:
git tag v1.0.0
git push origin v1.0.0
# CI publishes to PyPI automatically
```

## Review Checklist

- [ ] `make test` passes (151 tests)
- [ ] `make lint` passes
- [ ] `make typecheck` passes
- [ ] `h3-test --endpoint http://localhost:9191` passes against all 3 SDKs
- [ ] New features have test battery coverage
- [ ] Protocol changes regenerated via `sync_protocol.py`
- [ ] CLI changes have help text and tests

## Questions?

See the umbrella project at [get-h3/h3](https://github.com/get-h3/h3) for architecture, specs, and the cross-repo task board.
