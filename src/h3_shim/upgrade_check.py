"""Hermes update pre-flight hook (S11 §3).

Runs before ``hermes update`` and returns OK, WARN, or BLOCK based on:
1. Protocol compatibility via versions.yaml
2. Current H3 shim version vs minimum required
3. Active harness health
4. Config schema version

Usage::

    from h3_shim.upgrade_check import pre_update_check, UpgradeCheckResult
    result = pre_update_check("0.19.0")
    if result.severity == "BLOCK":
        print(result.message)
        sys.exit(1)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from h3_shim import __version__ as h3_shim_version
from h3_shim.client import H3Client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CURRENT_CONFIG_SCHEMA = 1  # Current H3 config _schema version
VERSIONS_YAML_PATH = (
    Path(__file__).resolve().parents[3] / "protocol" / "versions.yaml"
)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class UpgradeCheckResult:
    """Result of a pre-update compatibility check."""

    severity: str  # "OK", "WARN", or "BLOCK"
    message: str
    checks: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Convenience — True if the check is a clean pass."""
        return self.severity == "OK"

    @property
    def blocked(self) -> bool:
        """Convenience — True if the update should be blocked."""
        return self.severity == "BLOCK"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_version_matrix(path: Path | None = None) -> list[dict[str, Any]]:
    """Load the Hermes→H3 compatibility matrix from versions.yaml.

    Returns the ``hermes_versions`` list, or an empty list if the file
    is missing / unreadable.
    """
    p = path or VERSIONS_YAML_PATH
    if not p.exists():
        logger.warning("versions.yaml not found at %s", p)
        return []
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("failed to read versions.yaml: %s", exc)
        return []
    return data.get("hermes_versions", [])


def _find_compat_entry(
    target_hermes: str,
    matrix: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the compatibility entry for *target_hermes* in the matrix."""
    for entry in matrix:
        if entry.get("hermes") == target_hermes:
            return entry
    return None


def _parse_version(v: str) -> tuple[int, int, int]:
    """Parse a semver-ish string into ``(major, minor, patch)``.

    Non-numeric suffixes (e.g. ``1.0.x``, ``2.0.0-beta``) are coerced:
    ``x`` → 0, trailing qualifier ignored.
    """
    v = v.strip()
    parts = []
    for chunk in v.split("."):
        chunk = chunk.split("-")[0].split("+")[0]  # strip qualifier
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)  # 'x' → 0
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


async def _check_harness_health(
    endpoint: str,
    harness_name: str,
) -> tuple[str, str] | None:
    """Health-check one harness. Returns ``(severity, message)`` or None if OK."""
    client = H3Client(endpoint=endpoint, timeout_ms=5_000)
    try:
        resp = await client.health()
        if resp.status != "ok":
            return (
                "WARN",
                f"Harness '{harness_name}' reports status '{resp.status}' "
                f"at {endpoint}",
            )
    except Exception as exc:
        return (
            "WARN",
            f"Harness '{harness_name}' is unreachable at {endpoint}: {exc}",
        )
    finally:
        await client.close()
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def pre_update_check(
    target_hermes_version: str,
    *,
    versions_yaml_path: Path | None = None,
    config_path: Path | None = None,
) -> UpgradeCheckResult:
    """Run the four S11 §3 pre-flight compatibility checks.

    Parameters
    ----------
    target_hermes_version:
        The Hermes version that ``hermes update`` is about to install,
        e.g. ``"0.19.0"``.
    versions_yaml_path:
        Optional override path to ``versions.yaml``. Defaults to the
        protocol repo copy shipped alongside the shim.
    config_path:
        Optional override path to the H3 config file. Defaults to
        ``~/.hermes/h3/config.yaml``.

    Returns
    -------
    UpgradeCheckResult
        ``severity`` is ``"OK"``, ``"WARN"``, or ``"BLOCK"``.
    """
    import asyncio

    checks: list[dict[str, str]] = []

    # ------------------------------------------------------------------
    # 1. Protocol compatibility (versions.yaml)
    # ------------------------------------------------------------------
    matrix = _load_version_matrix(versions_yaml_path)
    compat = _find_compat_entry(target_hermes_version, matrix)
    if not compat:
        return UpgradeCheckResult(
            severity="BLOCK",
            message=(
                f"H3 has no compatibility data for Hermes "
                f"{target_hermes_version}. "
                f"Check versions.yaml for supported versions."
            ),
        )

    # ------------------------------------------------------------------
    # 2. Current H3 shim version meets minimum
    # ------------------------------------------------------------------
    min_h3_str = compat.get("min_h3", "1.0.0")
    current = _parse_version(h3_shim_version)
    required = _parse_version(min_h3_str)
    if current < required:
        return UpgradeCheckResult(
            severity="BLOCK",
            message=(
                f"H3 shim v{h3_shim_version} is too old for Hermes "
                f"{target_hermes_version} (requires H3 ≥ "
                f"{compat['h3_shim']}). "
                f"Run: pip install --upgrade hermes-h3-shim"
            ),
        )

    checks.append(
        {
            "check": "protocol_compat",
            "severity": "OK",
            "detail": (
                f"Hermes {target_hermes_version} → H3 shim "
                f"{compat['h3_shim']} (protocol {compat['protocol']})"
            ),
        }
    )

    # ------------------------------------------------------------------
    # 3. Active harness health (async — run inline)
    # ------------------------------------------------------------------
    async def _check_all_harnesses() -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        cfg = _load_config(config_path)
        harnesses: dict[str, dict[str, Any]] = cfg.get("harnesses", {}) or {}
        for name, spec in harnesses.items():
            endpoint = spec.get("endpoint")
            if not endpoint:
                continue
            issue = await _check_harness_health(endpoint, name)
            if issue:
                results.append(
                    {
                        "check": f"harness:{name}",
                        "severity": issue[0],
                        "detail": issue[1],
                    }
                )
        return results

    try:
        harness_checks = asyncio.run(_check_all_harnesses())
    except RuntimeError:
        # Already in an event loop — skip async health checks
        harness_checks = []
        logger.debug("skipping harness health checks (event loop already running)")

    checks.extend(harness_checks)

    # ------------------------------------------------------------------
    # 4. Config schema version
    # ------------------------------------------------------------------
    cfg = _load_config(config_path)
    cfg_schema = cfg.get("_schema", 0)
    if cfg_schema < CURRENT_CONFIG_SCHEMA:
        checks.append(
            {
                "check": "config_schema",
                "severity": "WARN",
                "detail": (
                    f"H3 config schema v{cfg_schema} will be "
                    f"migrated to v{CURRENT_CONFIG_SCHEMA}"
                ),
            }
        )
    else:
        checks.append(
            {
                "check": "config_schema",
                "severity": "OK",
                "detail": f"Config schema v{cfg_schema} (current)",
            }
        )

    # ------------------------------------------------------------------
    # Determine overall severity
    # ------------------------------------------------------------------
    severities = {c["severity"] for c in checks}
    if "BLOCK" in severities:
        severity = "BLOCK"
    elif "WARN" in severities:
        severity = "WARN"
    else:
        severity = "OK"

    # Build human-readable message
    lines = [f"Pre-update check for Hermes {target_hermes_version}:"]
    for c in checks:
        lines.append(f"  [{c['severity']:4s}] {c['check']}: {c.get('detail', '')}")

    return UpgradeCheckResult(
        severity=severity,
        message="\n".join(lines),
        checks=checks,
    )


# ---------------------------------------------------------------------------
# Config loader (lightweight, no Click dependency)
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = Path.home() / ".hermes" / "h3" / "config.yaml"


def _load_config(path: Path | None = None) -> dict[str, Any]:
    """Load H3 config from disk, returning empty skeleton on failure."""
    p = path or DEFAULT_CONFIG_PATH
    if not p.exists():
        return _empty_config()
    try:
        with p.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except (yaml.YAMLError, OSError):
        return _empty_config()


def _empty_config() -> dict[str, Any]:
    return {"default_harness": None, "harnesses": {}, "sessions": {}}
