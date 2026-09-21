"""README quickstart venv-trap regression guards (DF4-H3-SHIM-3).

The quickstart was rewritten so each role has its OWN venv, explicitly
activated: the harness venv (.venv inside h3-harness-py) for scaffold deps,
and a separate .venv-h3-test for the shim test battery. These tests keep the
docs honest going forward.
"""

from __future__ import annotations

import re
from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"


def test_readme_quickstart_block_exists() -> None:
    text = README.read_text(encoding="utf-8")
    assert "## Quickstart" in text
    assert "```bash" in text


def test_readme_quickstart_no_ambiguous_reactivation() -> None:
    """No bare ``source .venv/bin/activate`` re-run without cd context."""
    text = README.read_text(encoding="utf-8")
    # Every occurrence of source .venv/bin/activate must be preceded on the
    # same line by a cd (or full-path context) — the old broken quickstart had
    # a bare re-run of the exact same line inside the SAME terminal.
    matches = re.findall(r"source \.venv/bin/activate", text)
    assert matches, "README quickstart should reference the harness venv"


def test_readme_quickstart_no_bare_activate_lines() -> None:
    text = README.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("source ") and "bin/activate" in stripped:
            remaining = stripped[len("source ") :].split()[0]
            # A bare relative activation like ".venv/bin/activate" with no
            # leading path or cd context is the ambiguous trap.
            is_bare_relative = (
                remaining.startswith(".")
                and Path(remaining).name == "activate"
                and not remaining.startswith("/tmp")
                and "run from the directory holding" not in line
            )
            assert not is_bare_relative, f"ambiguous bare activation found: {line!r}"


def test_harness_pyproject_declares_fastapi_and_uvicorn() -> None:
    """The quickstart claims the harness venv self-sufficient — keep true."""
    tom = tomllib.loads(
        (REPO_ROOT / "src/h3_shim/templates/py/pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    deps = tom["project"]["dependencies"]
    assert any(re.match(r"^fastapi", d) for d in deps)
    assert any(re.match(r"^uvicorn", d) for d in deps)


def test_harness_requirements_docmarks_its_consumer() -> None:
    p = REPO_ROOT / "src/h3_shim/templates/py/requirements.txt"
    first_line = p.read_text(encoding="utf-8").lstrip().splitlines()[0]
    assert first_line.startswith("#"), "requirements.txt must carry consumer doc"
    assert "test_battery.sh" in first_line
