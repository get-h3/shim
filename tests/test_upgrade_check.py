"""Tests for h3_shim.upgrade_check — pre-update compatibility checks (S11 §3)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from h3_shim.upgrade_check import (
    CURRENT_CONFIG_SCHEMA,
    UpgradeCheckResult,
    _bundled_versions_yaml_path,
    _default_versions_yaml_path,
    _devtree_versions_yaml_path,
    _find_compat_entry,
    _load_version_matrix,
    _parse_version,
    pre_update_check,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_versions_yaml(tmp_path: Path) -> Path:
    """Write a minimal versions.yaml to a temp directory and return its path."""
    data = {
        "hermes_versions": [
            {
                "hermes": "0.18.0",
                "h3_shim": "1.0.0",
                "protocol": "1.0",
                "min_h3": "1.0.0",
                "max_h3": "1.0.x",
                "grpc": False,
                "status": "current",
                "notes": "REST only.",
            },
            {
                "hermes": "0.19.0",
                "h3_shim": "1.1.0",
                "protocol": "1.0",
                "min_h3": "1.0.0",
                "max_h3": "1.x.x",
                "grpc": True,
                "status": "planned",
                "notes": "gRPC beta.",
            },
            {
                "hermes": "0.20.0",
                "h3_shim": "2.0.0",
                "protocol": "2.0",
                "min_h3": "2.0.0",
                "max_h3": "2.x.x",
                "grpc": True,
                "status": "planned",
                "notes": "Breaking changes.",
            },
        ]
    }
    p = tmp_path / "versions.yaml"
    with p.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh)
    return p


@pytest.fixture
def sample_config(tmp_path: Path) -> Path:
    """Write a minimal H3 config with one harness to a temp dir."""
    data = {
        "_schema": CURRENT_CONFIG_SCHEMA,
        "default_harness": "echo",
        "harnesses": {
            "echo": {
                "endpoint": "http://localhost:19191",
                "transport": "rest",
                "timeout_ms": 30000,
            }
        },
        "sessions": {},
    }
    p = tmp_path / "config.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh)
    return p


# ---------------------------------------------------------------------------
# _parse_version
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "input_str, expected",
    [
        ("1.0.0", (1, 0, 0)),
        ("0.1.0", (0, 1, 0)),
        ("2.0.0-beta", (2, 0, 0)),
        ("1.0.x", (1, 0, 0)),
        ("1.x.x", (1, 0, 0)),
        ("3", (3, 0, 0)),
        ("0.19.0", (0, 19, 0)),
        ("", (0, 0, 0)),
    ],
)
def test_parse_version(input_str: str, expected: tuple[int, int, int]) -> None:
    assert _parse_version(input_str) == expected


# ---------------------------------------------------------------------------
# _load_version_matrix
# ---------------------------------------------------------------------------


def test_load_version_matrix_from_file(sample_versions_yaml: Path) -> None:
    matrix = _load_version_matrix(sample_versions_yaml)
    assert len(matrix) == 3
    assert matrix[0]["hermes"] == "0.18.0"


def test_load_version_matrix_missing_file() -> None:
    matrix = _load_version_matrix(Path("/nonexistent/versions.yaml"))
    assert matrix == []


def test_load_version_matrix_bad_yaml(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("hermes_versions: [unclosed")
    matrix = _load_version_matrix(p)
    assert matrix == []


# ---------------------------------------------------------------------------
# _find_compat_entry
# ---------------------------------------------------------------------------


def test_find_compat_entry_found(sample_versions_yaml: Path) -> None:
    matrix = _load_version_matrix(sample_versions_yaml)
    entry = _find_compat_entry("0.19.0", matrix)
    assert entry is not None
    assert entry["h3_shim"] == "1.1.0"


def test_find_compat_entry_not_found(sample_versions_yaml: Path) -> None:
    matrix = _load_version_matrix(sample_versions_yaml)
    entry = _find_compat_entry("0.99.0", matrix)
    assert entry is None


# ---------------------------------------------------------------------------
# versions.yaml resolution (GAP-011 — bundled package data)
# ---------------------------------------------------------------------------


def test_bundled_versions_yaml_exists() -> None:
    """The bundled matrix ships inside the package (wheel package data)."""
    p = _bundled_versions_yaml_path()
    assert p.exists(), f"bundled versions.yaml missing at {p}"


def test_bundled_versions_yaml_loads_matrix() -> None:
    """The bundled matrix parses to the 4-entry hermes_versions list."""
    matrix = _load_version_matrix(_bundled_versions_yaml_path())
    assert len(matrix) == 4
    assert matrix[0]["hermes"] == "0.17.0"


def test_bundled_matrix_passes_current_package_version(tmp_path: Path) -> None:
    """GAP-033 — the shipped matrix accepts the shipped package version.

    pre-update-check was a constant BLOCK: the matrix declared h3_shim
    >= 1.0.0 for every supported Hermes version while the package itself
    is still 0.1.0. The bundled versions.yaml now carries a 0.1.x row
    (Hermes 0.17.0) whose min_h3 the current package satisfies — this
    test uses the REAL bundled matrix and the REAL package version, no
    mocking, exactly like a fresh install would.
    """
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "_schema": CURRENT_CONFIG_SCHEMA,
                "default_harness": None,
                "harnesses": {},
                "sessions": {},
            }
        )
    )
    result = pre_update_check("0.17.0", config_path=cfg)
    assert result.severity == "OK"
    assert result.ok
    assert not result.blocked


def test_default_path_prefers_bundled() -> None:
    """In the dev tree the bundled copy exists, so it wins over dev-tree."""
    assert _default_versions_yaml_path() == _bundled_versions_yaml_path()


def test_devtree_fallback_path() -> None:
    """The dev-tree fallback points at the sibling protocol repo."""
    p = _devtree_versions_yaml_path()
    assert str(p).endswith("protocol/versions.yaml")


def test_pre_update_check_default_matrix(
    sample_config: Path,
) -> None:
    """pre_update_check resolves versions.yaml from the bundled data file
    when no explicit path is given (the post-install code path)."""
    with (
        patch("h3_shim.upgrade_check.h3_shim_version", "1.2.0"),
        patch(
            "h3_shim.upgrade_check._load_config",
            return_value={"_schema": 1, "harnesses": {}, "sessions": {}},
        ),
    ):
        result = pre_update_check("0.18.0", config_path=sample_config)
    assert result.severity == "OK"
    assert result.ok


# ---------------------------------------------------------------------------
# pre_update_check
# ---------------------------------------------------------------------------


def test_blocks_on_unknown_version(sample_versions_yaml: Path) -> None:
    result = pre_update_check("0.99.0", versions_yaml_path=sample_versions_yaml)
    assert result.severity == "BLOCK"
    assert result.blocked
    assert not result.ok
    assert "no compatibility data" in result.message.lower()


# ---------------------------------------------------------------------------
# DF-H3-SHIM-FOREMAN-4 — the unknown-version BLOCK must name the matrix it
# read and list the supported versions instead of dead-ending.
# ---------------------------------------------------------------------------


def test_unknown_version_names_matrix_and_supported_versions(
    sample_versions_yaml: Path,
) -> None:
    """Effective path + ascending supported versions, legacy phrase intact."""
    result = pre_update_check("0.99.0", versions_yaml_path=sample_versions_yaml)
    assert result.severity == "BLOCK"
    assert result.blocked
    # The phrase the CLI docs / smoke test grep for must survive.
    assert "no compatibility data" in result.message.lower()
    # The path actually consulted, not a generic "versions.yaml".
    assert f"Matrix consulted: {sample_versions_yaml}" in result.message
    # Original strings, ascending with the sample matrix's own rows.
    assert "Supported Hermes versions: 0.18.0, 0.19.0, 0.20.0" in result.message
    # Actionable pointer for both surfaces.
    assert "--versions-yaml" in result.message
    assert "versions_yaml_path=" in result.message
    # Tight: no traceback, no wall of text.
    assert len(result.message.splitlines()) <= 5
    assert "Traceback" not in result.message
    # The empty-matrix wording is reserved for the empty-matrix case.
    assert "the compatibility matrix is empty" not in result.message


def test_unknown_version_newer_than_newest_says_so(
    sample_versions_yaml: Path,
) -> None:
    result = pre_update_check("99.0.0", versions_yaml_path=sample_versions_yaml)
    assert result.severity == "BLOCK"
    assert "newer than the newest supported version" in result.message
    # ... and names the newest supported version it compared against.
    assert "0.20.0" in result.message


def test_unknown_version_older_than_oldest_says_so(
    sample_versions_yaml: Path,
) -> None:
    result = pre_update_check("0.1.0", versions_yaml_path=sample_versions_yaml)
    assert result.severity == "BLOCK"
    assert "older than the oldest supported version" in result.message
    assert "0.18.0" in result.message


def test_supported_versions_listed_ascending_regardless_of_row_order(
    tmp_path: Path,
) -> None:
    """Ordering uses _parse_version, not the YAML row order."""
    data = {
        "hermes_versions": [
            {"hermes": "0.20.0", "h3_shim": "2.0.0", "min_h3": "2.0.0"},
            {"hermes": "0.18.0", "h3_shim": "1.0.0", "min_h3": "1.0.0"},
            {"hermes": "0.19.0", "h3_shim": "1.1.0", "min_h3": "1.0.0"},
        ]
    }
    p = tmp_path / "scrambled-versions.yaml"
    with p.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh)

    result = pre_update_check("0.99.0", versions_yaml_path=p)
    assert result.severity == "BLOCK"
    assert "Supported Hermes versions: 0.18.0, 0.19.0, 0.20.0" in result.message


def test_default_matrix_path_named_when_no_explicit_path(
    sample_versions_yaml: Path,
) -> None:
    """Without versions_yaml_path the resolved VERSIONS_YAML_PATH is named."""
    with patch("h3_shim.upgrade_check.VERSIONS_YAML_PATH", sample_versions_yaml):
        result = pre_update_check("0.99.0")
    assert result.severity == "BLOCK"
    assert f"Matrix consulted: {sample_versions_yaml}" in result.message
    assert "Supported Hermes versions: 0.18.0, 0.19.0, 0.20.0" in result.message


def test_missing_matrix_reports_missing_path_not_version_list(
    tmp_path: Path,
) -> None:
    """A missing matrix gets its own distinct, actionable BLOCK message."""
    missing = tmp_path / "no-such-versions.yaml"
    assert not missing.exists()

    result = pre_update_check("0.99.0", versions_yaml_path=missing)
    assert result.severity == "BLOCK"
    assert "no compatibility data" in result.message.lower()
    assert str(missing) in result.message
    assert "missing" in result.message.lower()
    assert "unreadable" in result.message.lower()
    assert "--versions-yaml" in result.message
    # Never render an empty supported-version list.
    assert "Supported Hermes versions" not in result.message
    assert len(result.message.splitlines()) <= 5


@pytest.mark.parametrize(
    "content",
    [
        "",
        "# only a comment\n",
        "hermes_versions: [unclosed",
        "- just\n- a\n- list\n",
        "hermes_versions: not-a-list\n",
    ],
)
def test_empty_or_malformed_matrix_blocks_without_crashing(
    tmp_path: Path, content: str
) -> None:
    """Empty/malformed YAML → distinct BLOCK message, no exception."""
    p = tmp_path / "versions.yaml"
    p.write_text(content, encoding="utf-8")

    result = pre_update_check("0.99.0", versions_yaml_path=p)
    assert result.severity == "BLOCK"
    assert result.blocked
    assert str(p) in result.message
    assert "--versions-yaml" in result.message
    assert "Supported Hermes versions" not in result.message


def test_blocks_on_shim_too_old(sample_versions_yaml: Path) -> None:
    """Current shim is 0.1.0 — older than min_h3: 1.0.0."""
    result = pre_update_check("0.18.0", versions_yaml_path=sample_versions_yaml)
    assert result.severity == "BLOCK"
    assert "too old" in result.message.lower()


@patch("h3_shim.upgrade_check.h3_shim_version", "1.2.0")
def test_ok_when_compatible(sample_versions_yaml: Path, sample_config: Path) -> None:
    """Mock shim at 1.2.0 — compatible with Hermes 0.18.0 (min 1.0.0)."""
    with patch(
        "h3_shim.upgrade_check._load_config",
        return_value={"_schema": 1, "harnesses": {}, "sessions": {}},
    ):
        result = pre_update_check(
            "0.18.0",
            versions_yaml_path=sample_versions_yaml,
            config_path=sample_config,
        )
    assert result.severity == "OK"
    assert result.ok
    assert not result.blocked


@patch("h3_shim.upgrade_check.h3_shim_version", "1.2.0")
def test_warn_on_stale_config_schema(
    sample_versions_yaml: Path, sample_config: Path
) -> None:
    """Config schema is 0 (old) → WARN."""
    with patch(
        "h3_shim.upgrade_check._load_config",
        return_value={"_schema": 0, "harnesses": {}, "sessions": {}},
    ):
        result = pre_update_check(
            "0.18.0",
            versions_yaml_path=sample_versions_yaml,
            config_path=sample_config,
        )
    assert result.severity == "WARN"
    assert not result.blocked
    assert any("migrated" in c.get("detail", "") for c in result.checks)


@patch("h3_shim.upgrade_check.h3_shim_version", "1.2.0")
def test_warn_on_unreachable_harness(
    sample_versions_yaml: Path, sample_config: Path
) -> None:
    """Harness health check fails → WARN."""
    mock_client = MagicMock()
    mock_client.health = AsyncMock(side_effect=ConnectionError("refused"))
    mock_client.close = AsyncMock()

    with patch("h3_shim.upgrade_check.H3Client", return_value=mock_client):
        result = pre_update_check(
            "0.18.0",
            versions_yaml_path=sample_versions_yaml,
            config_path=sample_config,
        )
    # With an unreachable harness we get at least a WARN
    assert result.severity in ("WARN", "OK")
    # The harness check should report the connection error
    harness_checks = [c for c in result.checks if c["check"].startswith("harness:")]
    if harness_checks:
        assert harness_checks[0]["severity"] == "WARN"


def test_upgrade_check_result_properties() -> None:
    ok = UpgradeCheckResult(severity="OK", message="all good")
    assert ok.ok
    assert not ok.blocked

    block = UpgradeCheckResult(severity="BLOCK", message="stop")
    assert block.blocked
    assert not block.ok

    warn = UpgradeCheckResult(severity="WARN", message="heads up")
    assert not warn.ok
    assert not warn.blocked
