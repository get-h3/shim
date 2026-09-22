"""Exit-code propagation for the ``hermes h3`` plugin (DF5-H3-SHIM-3).

``hermes h3 <cmd>`` delegates to the ``hermes-h3`` click CLI.  When that CLI
fails, the process MUST exit non-zero — otherwise a script or CI gate sees a
green run while the delegated command reported an error.  The plugin used to
signal failures only through its *return value*, which is a host convention,
not a contract: Hermes Core turns a non-zero int return into the exit code,
but a host that calls the handler and discards the value (``args.func(args)``,
no assignment) turned every delegated-CLI failure into exit 0 — the reported
"``hermes h3`` exits 0 on the failure" symptom.

These tests pin the invariant that makes both host conventions fail loudly:
a failing invocation raises ``SystemExit`` with the delegated CLI's code.
The click group is the real one (``h3_shim.cli``); only the failure *carrier*
is stubbed where the real carrier needs a broken harness on the network.
"""

from __future__ import annotations

import argparse
import importlib.util
import types
from pathlib import Path

import pytest

_PLUGIN_PATH = Path(__file__).resolve().parent.parent / "h3" / "__init__.py"

assert _PLUGIN_PATH.name == "__init__.py"


@pytest.fixture(scope="module")
def plugin() -> object:
    """Load the ``h3`` plugin module from its repo path (mirrors test_h3_plugin)."""
    spec = importlib.util.spec_from_file_location("h3_plugin_exit_codes", _PLUGIN_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _handler_call(plugin: object, argv: list[str], config: Path):
    """Parse ``argv`` with the plugin tree, then call ``_handler``.

    Returns the exception-free return value, or raises ``SystemExit`` exactly
    as the handler did — mirroring what the host process would experience.
    """
    parser = argparse.ArgumentParser(prog="hermes h3")
    plugin._setup(parser)  # type: ignore[attr-defined]
    ns = parser.parse_args([*argv, "--config", str(config)])
    return plugin._handler(ns)  # type: ignore[attr-defined]


@pytest.fixture()
def config(tmp_path: Path) -> Path:
    """Minimal valid config: no harnesses, no sessions."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "default_harness: null\nharnesses: {}\nsessions: {}\n", encoding="utf-8"
    )
    return path


def _fake_group(plugin: object, monkeypatch: pytest.MonkeyPatch, main) -> None:
    """Replace the plugin's click group with a stub carrying one behaviour.

    ``plugin.click`` is left as the real module: the handler's
    ``except click.ClickException`` clause resolves against it when the stub
    raises, so a namespace stub would explode instead of propagating.
    """
    monkeypatch.setattr(plugin, "_CLICK_GROUP", types.SimpleNamespace(main=main))


# ── the invariant: failures must raise, not just return ─────────────────────


def test_failing_invocation_raises_so_an_ignoring_host_still_fails(
    plugin: object, config: Path
) -> None:
    """A real click failure raises ``SystemExit``; it does not return a code.

    Returning a code only fails the process on a host that honours handler
    return values.  A host that discards them (``args.func(args)``) would see
    exit 0 — so the code must travel as ``SystemExit``.
    """
    assert getattr(plugin, "_CLICK_GROUP") is not None, "shim must be importable"
    with pytest.raises(SystemExit) as excinfo:
        _handler_call(plugin, ["route", "--session", "nope-not-in-config"], config)
    assert excinfo.value.code != 0


def test_failing_invocation_exit_code_matches_the_click_cli(
    plugin: object, config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The propagated code is the delegated CLI's own code (usage error → 1)."""
    from click.testing import CliRunner

    from h3_shim.cli import hermes_h3

    argv = ["--config", str(config), "route", "--session", "nope-not-in-config"]
    direct = CliRunner().invoke(hermes_h3, argv)
    assert direct.exit_code != 0

    with pytest.raises(SystemExit) as excinfo:
        _handler_call(plugin, ["route", "--session", "nope-not-in-config"], config)
    assert excinfo.value.code == direct.exit_code
    captured = capsys.readouterr()
    assert "no session 'nope-not-in-config'" in captured.err


def test_valid_invocation_returns_none_without_raising(
    plugin: object, config: Path
) -> None:
    """The healthy path is unchanged: exit 0, no exception, no error text."""
    assert _handler_call(plugin, ["list"], config) is None


# ── non-zero click main() return values are propagated ─────────────────────


def test_non_zero_click_main_return_is_propagated(
    plugin: object, config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """click's non-standalone ``main()`` RETURNS the code; discarding it was 0.

    With ``standalone_mode=False`` a command's int return value (or
    ``ctx.exit(n)``) comes back as the return value of ``main()`` instead of
    raising.  Ignoring that return value swallowed the failure.
    """
    _fake_group(plugin, monkeypatch, lambda **_kw: 2)
    with pytest.raises(SystemExit) as excinfo:
        _handler_call(plugin, ["list"], config)
    assert excinfo.value.code == 2


def test_zero_click_main_return_is_success(
    plugin: object, config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``0``/``None`` return from ``main()`` stays a successful exit."""
    _fake_group(plugin, monkeypatch, lambda **_kw: 0)
    assert _handler_call(plugin, ["list"], config) is None
    _fake_group(plugin, monkeypatch, lambda **_kw: None)
    assert _handler_call(plugin, ["list"], config) is None


# ── SystemExit payload normalisation ───────────────────────────────────────


def test_string_system_exit_is_normalised_to_non_zero(
    plugin: object,
    config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A string ``SystemExit`` payload is printed and exits 1, never 0."""

    def _raise(**_kw) -> None:
        raise SystemExit("delegated CLI failed")

    _fake_group(plugin, monkeypatch, _raise)
    with pytest.raises(SystemExit) as excinfo:
        _handler_call(plugin, ["list"], config)
    assert excinfo.value.code == 1
    assert "delegated CLI failed" in capsys.readouterr().err


def test_none_system_exit_is_success(
    plugin: object, config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``SystemExit(None)`` (help/version paths) stays exit 0."""

    def _exit_none(**_kw) -> None:
        raise SystemExit(None)

    _fake_group(plugin, monkeypatch, _exit_none)
    assert _handler_call(plugin, ["list"], config) is None


# ── argparse errors propagate as 2 (never swallowed into 0) ────────────────


@pytest.mark.parametrize(
    "argv",
    [["no-such-subcommand"], ["verify", "--no-such-flag"]],
    ids=["unknown-subcommand", "unknown-flag"],
)
def test_argparse_errors_exit_two(plugin: object, argv: list[str]) -> None:
    """Bad args die in argparse with exit 2 — the handler never eats them."""
    parser = argparse.ArgumentParser(prog="hermes h3")
    plugin._setup(parser)  # type: ignore[attr-defined]
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(argv)
    assert excinfo.value.code == 2


# ── the subprocess branch propagates the child's status ────────────────────


def _subprocess_only_plugin(
    plugin: object, monkeypatch: pytest.MonkeyPatch, *, which, returncode: int
) -> None:
    """Force the handler down its subprocess branch with a stub child."""
    monkeypatch.setattr(plugin, "_CLICK_GROUP", None, raising=True)
    monkeypatch.setattr(plugin, "click", None, raising=True)
    monkeypatch.setattr(
        plugin, "shutil", types.SimpleNamespace(which=which), raising=True
    )
    monkeypatch.setattr(
        plugin,
        "subprocess",
        types.SimpleNamespace(
            run=lambda *a, **kw: types.SimpleNamespace(returncode=returncode)
        ),
        raising=True,
    )


def test_subprocess_failure_propagates_its_exit_code(
    plugin: object, config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _subprocess_only_plugin(
        plugin, monkeypatch, which=lambda _n: "/fake/hermes-h3", returncode=2
    )
    with pytest.raises(SystemExit) as excinfo:
        _handler_call(plugin, ["list"], config)
    assert excinfo.value.code == 2


def test_subprocess_signal_death_reports_shell_convention(
    plugin: object, config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A signal-killed child (-9) is reported as 137, not a wrapped negative."""
    _subprocess_only_plugin(
        plugin, monkeypatch, which=lambda _n: "/fake/hermes-h3", returncode=-9
    )
    with pytest.raises(SystemExit) as excinfo:
        _handler_call(plugin, ["list"], config)
    assert excinfo.value.code == 137


def test_missing_hermes_h3_fails_loudly(
    plugin: object,
    config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No ``hermes-h3`` on PATH is a non-zero exit naming the package."""
    _subprocess_only_plugin(plugin, monkeypatch, which=lambda _n: None, returncode=0)
    with pytest.raises(SystemExit) as excinfo:
        _handler_call(plugin, ["list"], config)
    assert excinfo.value.code == 1
    assert "hermes-h3 not found on PATH" in capsys.readouterr().err


# ── the staleness warning must be visible without a log reader ─────────────


def test_stale_install_warning_reaches_stderr(
    plugin: object, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A nested install warns on stderr, not only in the Hermes log."""

    class _Ctx:
        def register_cli_command(self, **_kw: object) -> None:
            pass

    (tmp_path / "h3").mkdir()
    plugin.register(_Ctx(), base=tmp_path)  # type: ignore[attr-defined]

    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "nested" in err.lower()
    assert "cp -r h3 ~/.hermes/plugins/" in err
