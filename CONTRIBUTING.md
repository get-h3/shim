# Contributing to H3 Shim

Thanks for contributing. The H3 Shim is a Python plugin for Hermes Core that implements the H3 agent protocol. This guide covers everything you need to submit a change.

## Development Setup

```bash
# Clone
git clone https://github.com/get-h3/shim.git
cd shim

# Create venv and install
make install

# Verify everything works
make test
```

All development happens inside a `.venv` virtual environment. `make install` creates it and installs the package in editable mode with all dev dependencies.

## Code Style

We use ruff for both formatting and linting. No configuration needed — the settings in `pyproject.toml` are authoritative.

```bash
make fmt    # ruff format — auto-fix formatting
make lint   # ruff check — catch issues
make build  # verify imports resolve
```

Run `make lint` before committing. CI enforces a clean lint pass.

## Running Tests

```bash
make test        # fast: pytest -x --tb=short -q
make test-full   # verbose: pytest -x -v
```

**162 tests must pass before any PR is merged.** Tests use pytest with `--tb=short` for readable output. Run the full suite — partial test runs are not accepted.

### Test Structure

| File | Tests | What it covers |
|---|---|---|
| `tests/test_protocol.py` | 13 | Pydantic model validation and serialization |
| `tests/test_client.py` | 22 | REST client with mocked HTTP |
| `tests/test_loader.py` | 26 | Harness config parsing and routing |
| `tests/test_shim_loop.py` | 39 | Decision execution, iteration limits, WAIT polling |
| `tests/test_cli.py` | 37 | CLI command parsing and scaffold generation |
| `tests/test_battery.py` | 25 | Compliance battery helpers + assertions |

The test battery (`test_battery.py`) is the gate — it verifies ANY H3 harness against the protocol spec (43 checks across 6 categories). Use `h3-test --endpoint http://localhost:9191` to run it against a live harness.

## Commit Rules

This repo uses GitReins as its quality gate. Every commit runs guards:

```bash
gitreins guard   # secrets, lint, tests
```

- Secrets violations block the commit (no exceptions)
- Tests must pass for changed code
- All commits must include a `Co-authored-by` trailer

## Cross-Repo Protocol Sync

Protocol models in `src/h3_shim/protocol.py` are generated from the canonical JSON Schema in the `get-h3/protocol` repository. When the protocol schema changes, a GitHub Actions workflow automatically regenerates and publishes a new release.

### How it works

1. The `get-h3/protocol` repo dispatches a `protocol-updated` event
2. `.github/workflows/sync-protocol.yml` triggers in this repo
3. The workflow regenerates Pydantic models, runs tests, tags a release, and publishes to PyPI

### Manual sync

```bash
# Generate models from a local protocol checkout
make sync-protocol

# Diff-only — check what WOULD change without writing
make sync-protocol-diff
```

Both commands invoke `scripts/sync_protocol.py`, which reads JSON Schema from `get-h3/protocol/schemas/v1/` and generates Pydantic v2 models with `datamodel-code-generator`.

### Adding a new protocol field

1. Update the JSON Schema in `get-h3/protocol`
2. Run `make sync-protocol-diff` to preview model changes
3. Run `make sync-protocol` to regenerate
4. Update any shim code that uses the changed models
5. Run `make test` — tests will catch schema mismatches

## Release Process

Releases are automated via CI. When the protocol schema changes:

1. `repository_dispatch` triggers `.github/workflows/sync-protocol.yml`
2. The workflow validates against the Go SDK echo harness (43/43 compliance)
3. A git tag is created matching the protocol version (e.g., `v1.0.0`)
4. The package is published to PyPI as `hermes-h3-shim`

For manual releases, use `make build-dist` to create the wheel and sdist.

## Questions?

Check the specs in `get-h3/h3/specs/`, especially:
- `specs/05-Test-Battery.md` — compliance test design
- `specs/06-Hermes-Core-Integration.md` — shim loop architecture
