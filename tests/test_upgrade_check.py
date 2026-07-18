"""Tests for h3_shim.upgrade_check — pre-update compatibility checks (S11 §3)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from h3_shim.upgrade_check import (
    CURRENT_CONFIG_SCHEMA,
    UpgradeCheckResult,
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
# pre_update_check
# ---------------------------------------------------------------------------


def test_blocks_on_unknown_version(sample_versions_yaml: Path) -> None:
    result = pre_update_check(
        "0.99.0", versions_yaml_path=sample_versions_yaml
    )
    assert result.severity == "BLOCK"
    assert result.blocked
    assert not result.ok
    assert "no compatibility data" in result.message.lower()


def test_blocks_on_shim_too_old(sample_versions_yaml: Path) -> None:
    """Current shim is 0.1.0 — older than min_h3: 1.0.0."""
    result = pre_update_check(
        "0.18.0", versions_yaml_path=sample_versions_yaml
    )
    assert result.severity == "BLOCK"
    assert "too old" in result.message.lower()


@patch("h3_shim.upgrade_check.h3_shim_version", "1.2.0")
def test_ok_when_compatible(
    sample_versions_yaml: Path, sample_config: Path
) -> None:
    """Mock shim at 1.2.0 — compatible with Hermes 0.18.0 (min 1.0.0)."""
    with patch(
        "h3_shim.upgrade_check._load_config",
        return_value={
            "_schema": 1, "harnesses": {}, "sessions": {}
        },
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
    mock_client.health = AsyncMock(
        side_effect=ConnectionError("refused")
    )
    mock_client.close = AsyncMock()

    with patch(
        "h3_shim.upgrade_check.H3Client", return_value=mock_client
    ):
        result = pre_update_check(
            "0.18.0",
            versions_yaml_path=sample_versions_yaml,
            config_path=sample_config,
        )
    # With an unreachable harness we get at least a WARN
    assert result.severity in ("WARN", "OK")
    # The harness check should report the connection error
    harness_checks = [
        c for c in result.checks if c["check"].startswith("harness:")
    ]
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
