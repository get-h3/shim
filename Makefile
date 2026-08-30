.PHONY: install build test test-battery lint typecheck fmt clean

VENV := .venv
PYTHON := $(VENV)/bin/python

$(VENV):
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

install: $(VENV)

build:
	$(PYTHON) -c "import h3_shim; print('build: OK')"

test:
	$(PYTHON) -m pytest -x --tb=short -q
	bash scripts/test_battery.sh

# GAP-043 — THE GATE: 44-test compliance battery against a live scaffolded
# harness (self-contained: scaffolds from the shim's own py template, no
# external sdk-go checkout). Fails the build on exit 1 (compliance) or 2
# (unreachable/not-H3) — never silently green.
test-battery:
	bash scripts/test_battery.sh

test-full:
	$(PYTHON) -m pytest -x -v

lint:
	$(PYTHON) -m ruff check src/ tests/

typecheck:
	uv run --with mypy mypy src/ --ignore-missing-imports

fmt:
	$(PYTHON) -m ruff format src/ tests/

clean:
	rm -rf $(VENV) __pycache__ src/h3_shim/__pycache__
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true

sync-protocol:
	$(PYTHON) scripts/sync_protocol.py --schema-dir ../protocol/schemas/v1

sync-protocol-diff:
	$(PYTHON) scripts/sync_protocol.py --schema-dir ../protocol/schemas/v1 --diff

build-dist:
	$(PYTHON) -m pip install build
	$(PYTHON) -m build

smoke-test:
	bash scripts/smoke_test.sh

all: install lint build test
