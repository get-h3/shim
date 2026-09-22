"""Stale/nested plugin install detection (DF5-H3-SHIM-3).

The documented install command ``cp -r h3 ~/.hermes/plugins/h3/`` copies
INTO the target once it exists (cp -r semantics), NESTING the fresh copy
inside the stale one — which keeps serving silently (live proof
2026-09-22: a stale 2026-08-07 mirror served ``unrecognized arguments``
for ``verify ts-plugin`` / ``route --session`` while the repo copy had
both).  The plugin ``register()`` path must emit a loud warning when the
installed directory shows either classic signature:

* a nested ``h3/`` directory inside the installed plugin directory; or
* a ``_plugin_version.txt`` marker older than this code's
  ``PLUGIN_VERSION`` (or no marker at all — a pre-marker install).

Tests build fake installed-plugin dirs under ``tmp_path``; the real
``~/.hermes`` is never touched.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

_PLUGIN_PATH = Path(__file__).resolve().parent.parent / "h3" / "__init__.py"

assert _PLUGIN_PATH.name == "__init__.py" and _PLUGIN_PATH.parent.name == "h3"


@pytest.fixture()
def plugin() -> object:
    """Load the ``h3`` plugin module from its repo path (mirrors test_h3_plugin)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "h3_plugin_staleness_test", _PLUGIN_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class _FakeCtx:
    """Minimal PluginContext stand-in recording register_cli_command calls."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def register_cli_command(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


def _write_marker(base: Path, version: str) -> None:
    (base / "_plugin_version.txt").write_text(f"{version}\n", encoding="utf-8")


def _warning_texts(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


# ── nested-copy signature (the classic cp -r nest) ─────────────────────────


def test_register_warns_on_nested_install(
    plugin: object, tmp_path: Path, caplog
) -> None:
    """register() warns loudly when the installed dir contains a nested h3/."""
    nested = tmp_path / "h3"
    nested.mkdir()
    (nested / "__init__.py").write_text("# fresh (nested) copy\n", encoding="utf-8")
    _write_marker(tmp_path, str(getattr(plugin, "PLUGIN_VERSION")))

    ctx = _FakeCtx()
    with caplog.at_level(logging.WARNING):
        plugin.register(ctx, base=tmp_path)  # type: ignore[attr-defined]

    warnings = _warning_texts(caplog)
    assert ctx.calls, "register() must still register the command group"
    assert any("nested" in w.lower() for w in warnings), warnings
    assert any("stale" in w.lower() for w in warnings), warnings
    # The fix command must be named, and it must NOT be the nesting form.
    assert any("cp -r h3 ~/.hermes/plugins/" in w for w in warnings), warnings
    assert any("rsync -a --delete" in w for w in warnings), warnings


# ── stale version-marker signature ──────────────────────────────────────────


def test_register_warns_on_older_version_marker(
    plugin: object, tmp_path: Path, caplog
) -> None:
    """register() warns when the installed marker is older than PLUGIN_VERSION."""
    _write_marker(tmp_path, "0.0.9")

    ctx = _FakeCtx()
    with caplog.at_level(logging.WARNING):
        plugin.register(ctx, base=tmp_path)  # type: ignore[attr-defined]

    warnings = _warning_texts(caplog)
    assert ctx.calls, "register() must still register the command group"
    assert any("0.0.9" in w for w in warnings), warnings
    assert any("stale" in w.lower() for w in warnings), warnings
    assert any("rsync -a --delete" in w for w in warnings), warnings


def test_register_warns_on_missing_version_marker(
    plugin: object, tmp_path: Path, caplog
) -> None:
    """A pre-marker install (no _plugin_version.txt) is treated as stale."""
    with caplog.at_level(logging.WARNING):
        plugin.register(_FakeCtx(), base=tmp_path)  # type: ignore[attr-defined]

    warnings = _warning_texts(caplog)
    assert any("marker" in w.lower() for w in warnings), warnings


# ── fresh install: silent ───────────────────────────────────────────────────


def test_register_quiet_on_fresh_install(
    plugin: object, tmp_path: Path, caplog
) -> None:
    """Current marker + no nested dir → no staleness warning at all."""
    _write_marker(tmp_path, str(getattr(plugin, "PLUGIN_VERSION")))

    with caplog.at_level(logging.WARNING):
        plugin.register(_FakeCtx(), base=tmp_path)  # type: ignore[attr-defined]

    assert _warning_texts(caplog) == []


# ── helper-level unit checks ────────────────────────────────────────────────


def test_stale_install_reasons_nested(plugin: object, tmp_path: Path) -> None:
    (tmp_path / "h3").mkdir()
    reasons: list[str] = plugin._stale_install_reasons(tmp_path)  # type: ignore[attr-defined]
    assert any("nested" in r.lower() for r in reasons)


def test_stale_install_reasons_version_compare(plugin: object, tmp_path: Path) -> None:
    """Older marker → stale; equal marker → clean; junk marker → stale."""
    _write_marker(tmp_path, "0.0.1")
    reasons: list[str] = plugin._stale_install_reasons(tmp_path)  # type: ignore[attr-defined]
    assert any("0.0.1" in r for r in reasons)

    _write_marker(tmp_path, str(getattr(plugin, "PLUGIN_VERSION")))
    reasons = plugin._stale_install_reasons(tmp_path)  # type: ignore[attr-defined]
    assert reasons == []

    _write_marker(tmp_path, "not-a-version")
    reasons = plugin._stale_install_reasons(tmp_path)  # type: ignore[attr-defined]
    assert reasons != [], "junk marker must be treated as stale (safe direction)"


def test_repo_copy_is_clean(plugin: object) -> None:
    """The repo's own h3/ must never trigger the warning (no nested dir,
    current marker shipped)."""
    base = Path(str(_PLUGIN_PATH)).parent
    reasons: list[str] = plugin._stale_install_reasons(base)  # type: ignore[attr-defined]
    assert reasons == []
