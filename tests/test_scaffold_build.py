"""GAP-045 — build & battery coverage for scaffolded harnesses (go/py/ts).

Each test scaffolds a fresh harness from THIS repo's own templates
(``src/h3_shim/templates/<lang>/``) into a tmp dir, then exercises the
real toolchain the way CI does:

* go — ``go mod tidy && go build .`` must produce the harness binary.
* ts  — ``npm install && npm run build`` must produce ``dist/index.js``.
* py  — a fresh venv must ``pip install -r requirements.txt`` cleanly,
  and the harness must pass the full 46-test battery via ``h3-test``
  (exit 0 AND ``TOTAL 46/46 PASSED`` asserted — the exit-code contract
  is checked explicitly, never masked).

Toolchain-dependent tests skip when the toolchain is absent: CI's
``test`` job installs Python only, so the go/ts legs skip there — the
``scaffold-compliance`` matrix job (which installs Go/Node) is where CI
covers go/ts build + battery. On developer machines with the toolchains
installed, all legs run locally. The py battery test needs only Python,
so it always runs.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from h3_shim.cli import scaffold_project

# ── helpers ────────────────────────────────────────────────────────────────


def _scaffold(lang: str, tmp_path: Path) -> Path:
    """Scaffold a fresh ``lang`` harness and return its project dir."""
    dest = scaffold_project(lang, tmp_path, overwrite=True)
    assert dest.is_dir(), f"scaffold produced no dir: {dest}"
    assert dest.name == f"h3-harness-{lang}"
    return dest


def _free_port() -> int:
    """Bind a throwaway socket to get a free port, then release it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _h3_test_bin() -> str:
    """Path to the h3-test entry point next to the running interpreter."""
    candidate = Path(sys.executable).parent / "h3-test"
    if candidate.is_file():
        return str(candidate)
    found = shutil.which("h3-test")
    assert found, "h3-test not found next to sys.executable nor on PATH"
    return found


def _wait_healthy(port: int, timeout_s: float = 30.0) -> None:
    """Poll /v1/health until the harness answers or the timeout elapses."""
    import urllib.request

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/v1/health", timeout=1
            ) as resp:
                if resp.status == 200:
                    return
        except Exception:  # noqa: BLE001 — connection refused while booting
            pass
        time.sleep(0.25)
    raise AssertionError(f"harness on :{port} never became healthy")


# ── go ──────────────────────────────────────────────────────────────────────


class TestGoScaffoldBuild:
    """Scaffolded go output must build with ``go build .``."""

    def test_scaffold_go_builds(self, tmp_path: Path) -> None:
        go = shutil.which("go")
        if go is None:
            pytest.skip("go toolchain not installed")
        proj = _scaffold("go", tmp_path)
        (main, mod) = (proj / "main.go", proj / "go.mod")
        assert main.is_file() and mod.is_file()

        # go mod tidy downloads the pinned sdk-go dep (needs network, same
        # as CI); go build . must then produce the harness binary.
        tidy = subprocess.run(
            [go, "mod", "tidy"], cwd=proj, capture_output=True, text=True, timeout=300
        )
        assert tidy.returncode == 0, (
            f"go mod tidy failed:\n{tidy.stdout}\n{tidy.stderr}"
        )
        build = subprocess.run(
            [go, "build", "."], cwd=proj, capture_output=True, text=True, timeout=300
        )
        assert build.returncode == 0, (
            f"go build . failed:\n{build.stdout}\n{build.stderr}"
        )
        assert (proj / "h3-harness-go").is_file(), "go build . produced no binary"


# ── ts ──────────────────────────────────────────────────────────────────────


class TestTsScaffoldBuild:
    """Scaffolded ts output must build with ``npm install && npm run build``."""

    def test_scaffold_ts_builds(self, tmp_path: Path) -> None:
        npm = shutil.which("npm")
        if npm is None:
            pytest.skip("npm toolchain not installed")
        proj = _scaffold("ts", tmp_path)
        assert (proj / "package.json").is_file()
        assert (proj / "index.ts").is_file()

        install = subprocess.run(
            [npm, "install", "--no-audit", "--no-fund"],
            cwd=proj,
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert install.returncode == 0, (
            f"npm install failed:\n{install.stdout}\n{install.stderr}"
        )
        build = subprocess.run(
            [npm, "run", "build"], cwd=proj, capture_output=True, text=True, timeout=300
        )
        assert build.returncode == 0, (
            f"npm run build failed:\n{build.stdout}\n{build.stderr}"
        )
        assert (proj / "dist" / "index.js").is_file(), (
            "npm run build produced no dist/index.js"
        )


# ── py — build + full battery ───────────────────────────────────────────────


class TestPyScaffoldBuild:
    """Scaffolded py output must install into a fresh venv."""

    def test_scaffold_py_installs(self, tmp_path: Path) -> None:
        proj = _scaffold("py", tmp_path)
        assert (proj / "main.py").is_file()
        assert (proj / "requirements.txt").is_file()

        venv = proj / ".venv"
        mkvenv = subprocess.run(
            [sys.executable, "-m", "venv", str(venv)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert mkvenv.returncode == 0, mkvenv.stderr
        pip = venv / ("Scripts" if sys.platform == "win32" else "bin") / "pip"
        install = subprocess.run(
            [str(pip), "install", "-q", "-r", str(proj / "requirements.txt")],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert install.returncode == 0, (
            f"pip install -r requirements.txt failed:\n{install.stderr}"
        )


class TestPyScaffoldBattery:
    """The scaffolded py harness must pass the full 46-test battery.

    This is the in-suite twin of the CI ``scaffold-compliance`` py leg and
    of ``scripts/test_battery.sh``: h3-test must exit 0 AND the output must
    assert ``TOTAL 46/46 PASSED``. A non-zero exit (1 = compliance failure,
    2 = unreachable) or a missing 46/46 assertion fails the test — the
    exit-code contract is never masked.
    """

    def test_py_scaffold_passes_46_46_battery(self, tmp_path: Path) -> None:
        proj = _scaffold("py", tmp_path)
        port = _free_port()

        # Fresh venv + deps (mirrors the CI py build step).
        venv = proj / ".venv"
        mkvenv = subprocess.run(
            [sys.executable, "-m", "venv", str(venv)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert mkvenv.returncode == 0, mkvenv.stderr
        bin_dir = venv / ("Scripts" if sys.platform == "win32" else "bin")
        install = subprocess.run(
            [
                str(bin_dir / "pip"),
                "install",
                "-q",
                "-r",
                str(proj / "requirements.txt"),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert install.returncode == 0, install.stderr

        # Start the harness with PORT override, wait for health, run battery.
        proc = subprocess.Popen(
            [str(bin_dir / "python"), "main.py"],
            cwd=proj,
            env={"PORT": str(port), "PATH": "/usr/bin:/bin"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_healthy(port)
            battery = subprocess.run(
                [_h3_test_bin(), "--endpoint", f"http://127.0.0.1:{port}"],
                capture_output=True,
                text=True,
                timeout=300,
            )
            assert battery.returncode == 0, (
                f"h3-test exited {battery.returncode} "
                f"(1=compliance failure, 2=unreachable) — battery:\n{battery.stdout}"
            )
            assert "TOTAL" in battery.stdout and "46/46" in battery.stdout, (
                f"battery output missing 46/46 assertion:\n{battery.stdout}"
            )
            assert "PASSED" in battery.stdout, (
                f"battery did not PASS:\n{battery.stdout}"
            )
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                proc.kill()
