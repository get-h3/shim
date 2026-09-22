"""GAP-045 — build & battery coverage for scaffolded harnesses (go/py/ts).

Each test scaffolds a fresh harness from THIS repo's own templates
(``src/h3_shim/templates/<lang>/``) into a tmp dir, then exercises the
real toolchain the way CI does:

* go — ``go mod tidy && go build .`` must produce the harness binary.
* ts  — ``npm install && npm run build`` must produce ``dist/index.js``.
* py  — a fresh venv must ``pip install -r requirements.txt`` cleanly,
  and the harness must pass the full 46-test battery via ``h3-test``
  (exit 0 AND ``TOTAL 46/46 PASSED`` asserted — the exit-code contract
  is checked explicitly, never masked). GAP-047 load hygiene: the venv
  is built ONCE per module (module-scoped ``py_scaffold`` fixture) and
  each test runs against its own ``shutil.copytree`` copy, so the heavy
  dependency-install chain runs a single time per module run.

Toolchain-dependent tests skip when the toolchain is absent: CI's
``test`` job installs Python only, so the go/ts legs skip there — the
``scaffold-compliance`` matrix job (which installs Go/Node) is where CI
covers go/ts build + battery. On developer machines with the toolchains
installed, all legs run locally. The py battery test needs only Python,
so it always runs.

The py leg additionally carries in-process session-lifecycle regression
tests (DF2-H3-SHIM-3): they import the py TEMPLATE and drive
``EchoHarness`` plus the real FastAPI routes directly, so a leak in the
generated harness is caught without a server, a subprocess or a toolchain.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from h3_shim.cli import scaffold_project
from h3_shim.templates.py import main

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


def _with_harness_log(message: str, log_path: Path, lines: int = 20) -> str:
    """Append the scaffolded harness's log tail to a failure ``message``.

    The harness's stdout/stderr are redirected to ``log_path`` (a real file,
    never an undrained PIPE), so a failing assertion can still show what the
    harness said. Reading is best-effort: an unreadable/absent log must not
    mask the original assertion.
    """
    try:
        text = log_path.read_text(errors="replace")
        tail = "\n".join(text.splitlines()[-lines:])
    except OSError as exc:  # pragma: no cover - defensive
        tail = f"<unreadable: {exc}>"
    return f"{message}\n--- harness log (tail) ---\n{tail}"


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
#
# GAP-047 LOAD-HYGIENE: the two tests below used to run a FULL
# ``python -m venv`` + ``pip install -r requirements.txt`` chain each —
# 2 fresh venv builds per module run, the heaviest spawn pattern in the
# suite. The process boundary is the subject only for the HARNESS the
# venv serves, not for the dependency install itself, so one module-
# scoped venv is built against a pristine scaffold copy and each test
# gets a cheap ``shutil.copytree`` snapshot (the harness subprocess then
# still runs from a real per-test tree). All assertions are unchanged;
# the pip chain itself is still exercised end-to-end, once per module.
# (Measured on the GAP-047 audit base 456bca2: module wall 20.9s -> ~14s,
# subprocess spawn count 10 -> 8, venv builds 2 -> 1.)


@pytest.fixture(scope="module")
def py_scaffold(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Scaffold the py harness once, with its venv, for the whole module.

    Yields the pristine project dir (scaffold + installed ``.venv``).
    Tests copy it via :func:`_copy_scaffold` before mutating or serving —
    they must never write into the shared tree.
    """
    base = tmp_path_factory.mktemp("py-scaffold-base")
    proj = _scaffold("py", base)

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

    yield proj


def _copy_scaffold(shared: Path, dest: Path) -> Path:
    """Copy the shared scaffold (incl. its venv) into the test's own tree."""
    proj = dest / "h3-harness-py"
    shutil.copytree(shared, proj, symlinks=True)
    return proj


class TestPyScaffoldBuild:
    """Scaffolded py output must install into a fresh venv."""

    def test_scaffold_py_installs(self, tmp_path: Path, py_scaffold: Path) -> None:
        # The pip install itself ran in the module fixture against the
        # pristine tree; this test's contract is that the venv it produced
        # serves the scaffold: main.py present, requirements declared, and
        # every declared requirement importable from the installed venv.
        proj = _copy_scaffold(py_scaffold, tmp_path)
        assert (proj / "main.py").is_file()
        assert (proj / "requirements.txt").is_file()
        venv = proj / ".venv"
        pip = venv / ("Scripts" if sys.platform == "win32" else "bin") / "pip"
        freeze = subprocess.run(
            [str(pip), "freeze"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert freeze.returncode == 0, freeze.stderr
        declared = {
            line.split(">=")[0].split("==")[0].strip().lower()
            for line in (proj / "requirements.txt").read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        installed = {
            line.split("==")[0].split(" @ ")[0].strip().lower()
            for line in freeze.stdout.splitlines()
            if line.strip() and not line.startswith("#")
        }
        missing = {name for name in declared if name not in installed}
        assert not missing, f"requirements.txt entries absent from the venv: {missing}"


class TestPyScaffoldBattery:
    """The scaffolded py harness must pass the full 46-test battery.

    This is the in-suite twin of the CI ``scaffold-compliance`` py leg and
    of ``scripts/test_battery.sh``: h3-test must exit 0 AND the output must
    assert ``TOTAL 46/46 PASSED``. A non-zero exit (1 = compliance failure,
    2 = unreachable) or a missing 46/46 assertion fails the test — the
    exit-code contract is never masked.
    """

    def test_py_scaffold_passes_46_46_battery(
        self, tmp_path: Path, py_scaffold: Path
    ) -> None:
        proj = _copy_scaffold(py_scaffold, tmp_path)
        port = _free_port()
        bin_dir = proj / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")

        # Start the harness with PORT override, wait for health, run battery.
        # The harness's stdout/stderr go to a FILE under tmp_path — never an
        # undrained PIPE. The scaffolded harness logs several KB while the
        # stress category runs, so a PIPE nobody reads fills (~8089 bytes) and
        # parks the child in anon_pipe_write: it stops answering mid-battery
        # (deterministic 42/46, every stress test hitting the client's 10s
        # timeout) even though the harness itself is compliant. The file also
        # keeps the log available for failure messages.
        log_path = tmp_path / "harness.log"
        log_file = log_path.open("w")
        proc = subprocess.Popen(
            [str(bin_dir / "python"), "main.py"],
            cwd=proj,
            env={"PORT": str(port), "PATH": "/usr/bin:/bin"},
            stdout=log_file,
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
            assert battery.returncode == 0, _with_harness_log(
                f"h3-test exited {battery.returncode} "
                f"(1=compliance failure, 2=unreachable) — battery:\n{battery.stdout}",
                log_path,
            )
            assert "TOTAL" in battery.stdout and "46/46" in battery.stdout, (
                _with_harness_log(
                    f"battery output missing 46/46 assertion:\n{battery.stdout}",
                    log_path,
                )
            )
            assert "PASSED" in battery.stdout, _with_harness_log(
                f"battery did not PASS:\n{battery.stdout}", log_path
            )
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                proc.kill()
            log_file.close()


# ── py — session lifecycle (DF2-H3-SHIM-3) ──────────────────────────────────


class TestPyScaffoldSessionLifecycle:
    """DF2-H3-SHIM-3 — a naturally ended session must not leak.

    ``EchoHarness._state()`` auto-creates an entry for every callback, and
    only an explicit ``DELETE /v1/sessions/{id}`` used to remove one: the
    loop's natural END left the entry behind, so ``health().active_sessions``
    (``len(_sessions)``) grew monotonically — 97 sessions over 26h on a live
    dogfood instance. These tests drive the TEMPLATE's own harness in-process
    (``from h3_shim.templates.py import main``, the convention established by
    ``tests/test_scaffold_errors.py`` — no server, no subprocess) plus the
    real FastAPI routes via ``TestClient``, and assert the metric stays
    honest across repeated and concurrent sessions.
    """

    @staticmethod
    def _harness() -> main.EchoHarness:
        """A fresh harness — never the module-level app singleton."""
        return main.EchoHarness()

    @staticmethod
    def _process(sid: str, content: str = "hello") -> main.ProcessRequest:
        return main.ProcessRequest(
            session_id=sid,
            message=main.Message(role="user", content=content),
            identity=main.Identity(platform="test", chat_id="test-chat"),
            context=main.Context(),
        )

    @staticmethod
    def _result(sid: str, decision_id: str = "echo-process") -> main.ResultRequest:
        return main.ResultRequest(
            session_id=sid,
            decision_id=decision_id,
            result=main.ExecutionResult(type="text_sent", data={"finished": True}),
        )

    def _end_session(self, harness: main.EchoHarness, sid: str) -> main.Decision:
        """Send the two result callbacks the echo needs to reach its END."""
        first = harness.on_result(self._result(sid))
        assert first.decision is main.DecisionType.TEXT
        end = harness.on_result(self._result(sid, "echo-result"))
        assert end.decision is main.DecisionType.END
        return end

    def test_natural_end_drops_state_and_keeps_the_wire_decision(self) -> None:
        harness = self._harness()
        sid = f"end-{uuid.uuid4().hex}"

        assert harness.on_process(self._process(sid)).decision is main.DecisionType.TEXT
        assert harness.health().active_sessions == 1

        end = self._end_session(harness, sid)

        # The END decision itself is unchanged by the purge.
        assert end.decision_id == "echo-end"
        assert end.end is not None
        assert end.end.reason is main.EndReason.TASK_COMPLETE
        assert end.end.summary == "Echo conversation complete"

        # Core acceptance: the finished session is no longer counted.
        assert harness.health().active_sessions == 0

        # Chosen (documented) GET contract for a session that ended naturally:
        # the harness keeps no state for it, so 404 — the branch the compliance
        # battery reserves for exactly this harness shape.
        with pytest.raises(HTTPException) as excinfo:
            harness.get_session(sid)
        assert excinfo.value.status_code == 404
        assert excinfo.value.detail == "Session not found"

    def test_repeated_natural_ends_do_not_grow_active_sessions(self) -> None:
        harness = self._harness()
        peak = 0

        for i in range(20):
            sid = f"repeat-{i}-{uuid.uuid4().hex}"
            harness.on_process(self._process(sid))
            peak = max(peak, harness.health().active_sessions)
            self._end_session(harness, sid)
            assert harness.health().active_sessions == 0, (
                f"session {sid} still counted after a natural END"
            )

        # Only an in-flight session is ever counted; the metric never grows.
        assert peak == 1

    def test_http_flow_health_metric_tracks_the_natural_end(self) -> None:
        """The wire path agrees: END returns the session table to its baseline."""
        client = TestClient(main.app)
        sid = f"http-end-{uuid.uuid4().hex}"
        before = client.get("/v1/health").json()["active_sessions"]

        body = {
            "session_id": sid,
            "message": {"role": "user", "content": "finish the session"},
            "identity": {"platform": "test", "chat_id": "test-chat"},
            "context": {},
        }
        assert client.post("/v1/process", json=body).status_code == 200
        assert client.get("/v1/health").json()["active_sessions"] == before + 1

        decision_id = "echo-process"
        for _ in range(2):
            resp = client.post(
                "/v1/result",
                json={
                    "session_id": sid,
                    "decision_id": decision_id,
                    "result": {"type": "text_sent", "data": {"finished": True}},
                },
            )
            assert resp.status_code == 200
            payload = resp.json()
            decision_id = payload["decision_id"]

        assert payload["decision"] == "end"
        assert payload["end"]["reason"] == "task_complete"
        assert client.get("/v1/health").json()["active_sessions"] == before
        assert client.get(f"/v1/sessions/{sid}").status_code == 404
        # A finished session is unknown, so cancelling it is a 404 too —
        # consistent with on_cancel's unknown-session contract.
        assert (
            client.post(
                "/v1/cancel", json={"session_id": sid, "reason": "user_interrupt"}
            ).status_code
            == 404
        )

    def test_unknown_session_404s_are_preserved(self) -> None:
        harness = self._harness()
        missing = f"missing-{uuid.uuid4().hex}"

        with pytest.raises(HTTPException) as excinfo:
            harness.on_cancel(main.CancelRequest(session_id=missing))
        assert excinfo.value.status_code == 404
        with pytest.raises(HTTPException) as excinfo:
            harness.get_session(missing)
        assert excinfo.value.status_code == 404

        # Unknown sessions are never counted (no auto-create on these paths).
        assert harness.health().active_sessions == 0

        # Cancelling an in-flight session still succeeds.
        live = f"live-{uuid.uuid4().hex}"
        harness.on_process(self._process(live, "do not finish this yet"))
        cancelled = harness.on_cancel(
            main.CancelRequest(session_id=live, reason="user_interrupt")
        )
        assert cancelled.cancelled is True

    def test_explicit_delete_still_terminates_and_stays_idempotent(self) -> None:
        harness = self._harness()
        sid = f"delete-{uuid.uuid4().hex}"

        harness.on_process(self._process(sid, "do not finish this yet"))
        assert harness.health().active_sessions == 1

        harness.on_session_terminate(sid)
        assert harness.health().active_sessions == 0
        with pytest.raises(HTTPException) as excinfo:
            harness.get_session(sid)
        assert excinfo.value.status_code == 404

        # Idempotent: a second DELETE on an already-dropped session is a no-op.
        harness.on_session_terminate(sid)
        assert harness.health().active_sessions == 0

    def test_streaming_sessions_are_not_purged_by_result_callbacks(self) -> None:
        """The purge is END-scoped: a live (streaming) session stays counted."""
        harness = self._harness()
        sid = f"stream-{uuid.uuid4().hex}"

        harness.on_process(self._process(sid, "keep going, do not finish"))
        for _ in range(3):
            decision = harness.on_result(self._result(sid, "echo-result"))
            assert decision.decision is main.DecisionType.TEXT
            assert harness.health().active_sessions == 1

        harness.on_session_terminate(sid)
        assert harness.health().active_sessions == 0

    def test_concurrent_natural_ends_are_thread_safe(self) -> None:
        harness = self._harness()
        errors: list[Exception] = []
        errors_lock = threading.Lock()

        def drive(indices: range) -> None:
            try:
                for i in indices:
                    sid = f"thread-{i}"
                    harness.on_process(self._process(sid))
                    self._end_session(harness, sid)
            except Exception as exc:  # noqa: BLE001 - reported below
                with errors_lock:
                    errors.append(exc)

        threads = [
            threading.Thread(target=drive, args=(range(i * 5, i * 5 + 5),))
            for i in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert harness.health().active_sessions == 0


class TestPyScaffoldOneShotSessionLeak:
    """DF5-H3-SHIM-2 — a session with no second result must not leak either.

    The DF2-era purge fires only inside ``on_result`` when
    ``result_count >= 2``, so every battery session that never receives a
    second result — the one-shot ``finished=true`` path, the error-path and
    cancel tests, and streaming sessions — stays in the session table
    forever. On HEAD the repo's own battery leaks ~90+ sessions per run
    (py 1440 -> 1536 over two runs; ts 96 after one; go 96 -> 192). This
    test is the RED-proof: it drives one session through a SINGLE result
    (the one-shot path) and asserts the conversation leaves the live
    table. It fails on the pre-fix template because the entry is still
    counted after the turn is over.
    """

    @staticmethod
    def _harness() -> main.EchoHarness:
        return main.EchoHarness()

    @staticmethod
    def _process(sid: str, content: str = "hello") -> main.ProcessRequest:
        return main.ProcessRequest(
            session_id=sid,
            message=main.Message(role="user", content=content),
            identity=main.Identity(platform="test", chat_id="test-chat"),
            context=main.Context(),
        )

    def test_one_shot_session_leaves_the_live_table(self) -> None:
        harness = self._harness()
        sid = f"oneshot-{uuid.uuid4().hex}"

        # One process (finished=true) and one result — the one-shot path.
        # No second result ever arrives, exactly like the battery's
        # process-only sessions.
        harness.on_process(self._process(sid))
        assert harness.health().active_sessions == 1
        decision = harness.on_result(
            main.ResultRequest(
                session_id=sid,
                decision_id="echo-process",
                result=main.ExecutionResult(type="text_sent", data={}),
            )
        )
        assert decision.decision is main.DecisionType.TEXT
        assert decision.text is not None and decision.text.finished is True

        # The turn is over: the session must drop out of the LIVE table.
        # (Pre-fix it stays counted — the leak this task closes.)
        assert harness.health().active_sessions == 0, (
            "one-shot session still counted after its only result"
        )


class TestPyScaffoldSessionGcBounds:
    """DF5-H3-SHIM-2 — the live table is bounded, not merely emptied.

    The END decision alone cannot reclaim every conversation: a session that
    is opened and never receives a second result (a one-shot turn, an error
    path, a cancelled turn, a streaming turn) never reaches the END branch.
    Those entries are held for a bounded closing window — so the loop's
    closing callback and ``GET /v1/sessions/{id}`` still resolve — and are
    then reclaimed by the idle TTL / hard cap, which is what makes the
    battery's ``active_sessions == 0`` assertion hold run after run.
    """

    @staticmethod
    def _harness(**kwargs: float) -> main.EchoHarness:
        return main.EchoHarness(**kwargs)

    @staticmethod
    def _process(sid: str, content: str = "hello") -> main.ProcessRequest:
        return main.ProcessRequest(
            session_id=sid,
            message=main.Message(role="user", content=content),
            identity=main.Identity(platform="test", chat_id="test-chat"),
            context=main.Context(),
        )

    @staticmethod
    def _result(sid: str, decision_id: str = "echo-process") -> main.ResultRequest:
        return main.ResultRequest(
            session_id=sid,
            decision_id=decision_id,
            result=main.ExecutionResult(type="text_sent", data={"finished": True}),
        )

    def test_idle_session_is_reclaimed_after_the_ttl(self) -> None:
        """A process-only session (no second result ever) is swept once idle."""
        harness = self._harness(session_ttl_s=30.0)
        sid = f"ttl-{uuid.uuid4().hex}"

        harness.on_process(self._process(sid))
        assert harness.health().active_sessions == 1

        # Nothing has happened for longer than the TTL: the entry is stale.
        later = datetime.utcnow() + timedelta(seconds=31)
        assert harness.sweep_idle_sessions(now=later) == 1
        assert harness.health().active_sessions == 0
        with pytest.raises(HTTPException) as excinfo:
            harness.get_session(sid)
        assert excinfo.value.status_code == 404

        # A session that is still active inside the TTL is never swept.
        fresh = self._harness(session_ttl_s=30.0)
        live = f"ttl-live-{uuid.uuid4().hex}"
        fresh.on_process(self._process(live))
        assert fresh.sweep_idle_sessions(now=datetime.utcnow()) == 0
        assert fresh.health().active_sessions == 1

    def test_closing_result_still_ends_and_a_new_turn_reactivates(self) -> None:
        """The retained window answers the END callback, then re-activates."""
        harness = self._harness()
        sid = f"retained-{uuid.uuid4().hex}"

        harness.on_process(self._process(sid))
        first = harness.on_result(self._result(sid))
        assert first.decision is main.DecisionType.TEXT
        # The turn is over, so the session is no longer counted as live...
        assert harness.health().active_sessions == 0

        # ...but the loop's closing callback for that same turn still reaches
        # the END decision, because the entry is retained, not dropped.
        end = harness.on_result(self._result(sid, "echo-result"))
        assert end.decision is main.DecisionType.END
        assert end.end is not None
        assert end.end.reason is main.EndReason.TASK_COMPLETE

        # A fresh user turn on the same id re-activates it for counting.
        harness.on_process(self._process(sid, "one more turn"))
        assert harness.health().active_sessions == 1

    def test_max_sessions_cap_evicts_the_oldest_entry(self) -> None:
        """The hard cap bounds the table even with the TTL disabled."""
        harness = self._harness(session_ttl_s=0, max_sessions=2)
        for index in range(3):
            harness.on_process(self._process(f"cap-{index}"))

        # Two live sessions survive; the least-recently-active is evicted.
        assert harness.health().active_sessions == 2
        with pytest.raises(HTTPException) as excinfo:
            harness.get_session("cap-0")
        assert excinfo.value.status_code == 404
        assert harness.get_session("cap-2").session_id == "cap-2"


def _battery_assertion_block(log_path: Path) -> str:
    """Standard failure tail appended by :func:`_assert_sessions_drain`."""
    try:
        body = log_path.read_text(errors="replace")
        tail = "\n".join(body.splitlines()[-20:])
    except OSError as exc:  # pragma: no cover - defensive
        tail = f"<unreadable: {exc}>"
    return f"--- harness log (tail) ---\n{tail}"


def _assert_sessions_drain(
    proc: subprocess.Popen, port: int, log_path: Path, budget_s: float
) -> int:
    """DF5-H3-SHIM-2 battery assertion: ended sessions must leave the table.

    After the battery all conversations are over; under DF5 semantics the
    harness drains them within its idle TTL (default 30s) instead of
    holding every session forever. Polls ``GET /v1/health`` until
    ``active_sessions`` hits 0 or *budget_s* elapses, then returns the
    observed value for the caller's assertion (so the failure message can
    name the exact leak). Reads are best-effort: a crashed harness must
    not mask the battery's own failure.
    """
    import urllib.request

    deadline = time.monotonic() + budget_s
    last = -1
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break  # harness died; battery assertion already failed
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/v1/health", timeout=2
            ) as resp:
                body = json.loads(resp.read().decode("utf-8", errors="replace"))
                value = body.get("active_sessions")
                if isinstance(value, int):
                    last = value
                    if last == 0:
                        break
        except Exception:  # noqa: BLE001 — transient: boot, restart, teardown
            pass
        time.sleep(0.25)
    return last


class TestPyScaffoldBatterySessionDrain:
    """After a full battery run the harness must reach ``active_sessions 0``.

    This is the assertion DF4-H3-SHIM-2 was closed without: every battery
    session ends, so under END-decision + TTL semantics the live table
    returns to zero once the harness's idle TTL (default 30s) expires —
    and it stays zero, run after run. On the pre-fix templates the count
    never returns to 0 (it carries every one-shot, error-path and
    streaming session forward), so this test fails there.
    """

    def test_battery_leaves_zero_active_sessions(
        self, tmp_path: Path, py_scaffold: Path
    ) -> None:
        proj = _copy_scaffold(py_scaffold, tmp_path)
        port = _free_port()
        bin_dir = proj / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")

        log_path = tmp_path / "harness.log"
        log_file = log_path.open("w")
        proc = subprocess.Popen(
            [str(bin_dir / "python"), "main.py"],
            cwd=proj,
            env={"PORT": str(port), "PATH": "/usr/bin:/bin"},
            stdout=log_file,
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
            assert battery.returncode == 0, _with_harness_log(
                f"h3-test exited {battery.returncode} "
                f"(1=compliance failure, 2=unreachable) — battery:\n{battery.stdout}",
                log_path,
            )
            assert "TOTAL" in battery.stdout and "46/46" in battery.stdout, (
                _with_harness_log(
                    f"battery output missing 46/46 assertion:\n{battery.stdout}",
                    log_path,
                )
            )
            # DF5-H3-SHIM-2: every battery session ended, so the live table
            # must drain to 0 within the harness's idle TTL (+ slack).
            leaked = _assert_sessions_drain(proc, port, log_path, budget_s=60.0)
            assert leaked == 0, (
                f"active_sessions={leaked} after a full 46/46 battery + "
                f"{60:.0f}s drain budget — ended sessions are leaking "
                f"(DF5-H3-SHIM-2)\n{_battery_assertion_block(log_path)}"
            )
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                proc.kill()
            log_file.close()
