"""Docs regression guards.

README (DF4-H3-SHIM-3): the quickstart was rewritten so each role has its
OWN venv, explicitly activated: the harness venv (.venv inside
h3-harness-py) for scaffold deps, and a separate .venv-h3-test for the shim
test battery.

docs/api.md (SHIM-DF3-H3-SHIM-4): the H3ShimLoop / Context / H3Loader
constructor shapes are documented as the code REALLY is — a pydantic
`Identity` default (not a tuple), `Context.memory` as a `str`, and an
H3Loader config that names `default_harness`. These tests keep the prose
honest going forward.
"""

from __future__ import annotations

import re
from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
API_DOC = REPO_ROOT / "docs/api.md"
INTEGRATION_DOC = REPO_ROOT / "docs/integration.md"


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


# ── docs/api.md — real constructor shapes (SHIM-DF3-H3-SHIM-4) ───────────────


def test_api_doc_no_tuple_identity_default() -> None:
    """The tuple-identity wording is gone; the pydantic default is documented.

    The old prose read as a literal ``("shim", session_id)`` Python tuple,
    which pydantic rejects — a real embedding host burned iterations on it.
    """
    text = API_DOC.read_text(encoding="utf-8")
    assert '("shim", session_id)' not in text
    assert 'Identity(platform="shim", chat_id=session_id)' in text


def test_api_doc_identity_default_matches_source() -> None:
    """The documented default identity IS the code's default (not a paraphrase)."""
    source = (REPO_ROOT / "src/h3_shim/shim_loop.py").read_text(encoding="utf-8")
    assert re.search(
        r'Identity\(\s*platform="shim",\s*chat_id=session_id,?\s*\)', source
    ), "shim_loop default identity changed — update docs/api.md with it"


def test_api_doc_embedding_host_quickstart_exists() -> None:
    text = API_DOC.read_text(encoding="utf-8")
    assert "Embedding-host quickstart" in text
    assert "from h3_shim.shim_loop import H3ShimLoop" in text


def test_api_doc_loader_example_includes_default_harness() -> None:
    """The H3Loader usage config must name default_harness.

    A config with only ``harnesses``/``sessions`` falls back to the
    ``"native"`` route, which has no entry in ``loader.harnesses``.
    """
    text = API_DOC.read_text(encoding="utf-8")
    section = text[text.index("## H3Loader") : text.index("## H3ShimLoop")]
    assert '"default_harness"' in section
    assert 'config = {"harnesses": {...}, "sessions": {...}}' not in section


def test_api_doc_context_documents_memory_as_string() -> None:
    text = API_DOC.read_text(encoding="utf-8")
    assert "`memory` is a plain `str`" in text, "Context prose must say str"
    assert "`memory` is a str" in text, "quickstart must repeat memory-is-str"
    protocol = (REPO_ROOT / "src/h3_shim/protocol.py").read_text(encoding="utf-8")
    assert re.search(r'memory:\s*str\s*=\s*""', protocol)


# ── docs/integration.md — target identity + partial turns (DF-H3-26/29) ─────
# The battery's new failure detail tells the developer where to read; the
# identity line and the flag are user-facing behaviour.  A doc pointer that
# resolves to nothing is worse than no pointer, so these tests exist.


def test_integration_doc_documents_expect_fresh() -> None:
    """The battery reference documents the connect identity and the flag."""
    text = INTEGRATION_DOC.read_text(encoding="utf-8")
    assert "--expect-fresh" in text
    assert "Target health: version=" in text
    assert "(not reported)" in text, "missing-field degradation must be documented"


def test_partial_turn_hint_points_at_a_real_section() -> None:
    """``test_2_4``'s hint names a section that actually exists."""
    from h3_shim.test_battery import PARTIAL_TURN_HINT

    section = PARTIAL_TURN_HINT.split("docs/integration.md '")[1].rstrip("')")
    assert section == "Partial turns", PARTIAL_TURN_HINT
    text = INTEGRATION_DOC.read_text(encoding="utf-8")
    assert f"### {section}" in text, f"docs/integration.md lost its {section!r} section"
    body = text[text.index(f"### {section}") :]
    assert "do not finish" in body[:800]
    assert "finished=false" in body[:800]
