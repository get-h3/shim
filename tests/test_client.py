"""Tests for client.py — async H3Client HTTP behavior.

We exercise H3Client by patching ``httpx.AsyncClient`` on the instance so
the real socket layer is never touched. ``AsyncMock`` stands in for each
HTTP verb, returning a fake ``Response`` whose ``.json()`` and
``.raise_for_status()`` we control per-test.
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.request
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from h3_shim.cli import scaffold_project
from h3_shim.client import H3Client
from h3_shim.protocol import (
    CancelResponse,
    Context,
    Decision,
    DecisionType,
    EndReason,
    ExecutionResult,
    HealthResponse,
    HealthStatus,
    Identity,
    Message,
    ProcessRequest,
)
from h3_shim.shim_loop import H3ShimLoop

# ── helpers ─────────────────────────────────────────────────────────────────


def _fake_response(
    status_code: int = 200, json_payload: dict | None = None
) -> MagicMock:
    """Return a MagicMock that quacks like an ``httpx.Response``."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_payload or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        # Mirror httpx semantics: raise_for_status() raises on 4xx/5xx.
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"{status_code} error",
            request=MagicMock(),
            response=resp,
        )
    return resp


def _make_client(endpoint: str = "http://localhost:9000", **kw) -> H3Client:
    """Instantiate an H3Client and replace its internal AsyncClient with a mock.

    The constructor still creates an ``httpx.AsyncClient`` (we can't easily
    prevent that without touching source) — we immediately swap it out so
    no real HTTP traffic ever leaves the test process.
    """
    client = H3Client(endpoint=endpoint, **kw)
    client._rest = MagicMock()
    # Each HTTP verb is awaited, so they must be AsyncMocks.
    client._rest.get = AsyncMock()
    client._rest.post = AsyncMock()
    client._rest.aclose = AsyncMock()
    return client


# ── construction ────────────────────────────────────────────────────────────


class TestConstruction:
    def test_endpoint_trailing_slash_stripped(self):
        c = H3Client(endpoint="http://localhost:9000/")
        assert c.endpoint == "http://localhost:9000"

    def test_no_trailing_slash_preserved(self):
        c = H3Client(endpoint="http://localhost:9000")
        assert c.endpoint == "http://localhost:9000"

    def test_timeout_ms_converted_to_seconds(self):
        c = H3Client(endpoint="http://localhost:9000", timeout_ms=5000)
        assert c.timeout == 5.0

    def test_default_timeout_30_seconds(self):
        c = H3Client(endpoint="http://localhost:9000")
        assert c.timeout == 30.0

    def test_transport_default_rest(self):
        c = H3Client(endpoint="http://localhost:9000")
        assert c.transport == "rest"

    def test_transport_grpc_rejected(self):
        """GAP-030: unsupported transports fail fast instead of silent REST."""
        with pytest.raises(ValueError, match="grpc transport not supported yet"):
            H3Client(endpoint="http://localhost:9000", transport="grpc")

    def test_multiple_trailing_slashes_stripped(self):
        c = H3Client(endpoint="http://localhost:9000///")
        assert c.endpoint == "http://localhost:9000"

    def test_auth_headers_present_when_token_and_identity_provided(self):
        c = H3Client(
            endpoint="http://localhost:9000",
            hermes_token="h3_hx_abc123def456",
            hermes_identity="hermes-main",
            protocol_version="1.1",
        )
        assert c.hermes_token == "h3_hx_abc123def456"
        assert c.hermes_identity == "hermes-main"
        assert c.protocol_version == "1.1"

    def test_no_auth_headers_when_token_is_none(self):
        """Backward compat: no auth when hermes_token not provided, H3_API_KEY unset."""
        with patch.dict(os.environ, {}, clear=True):
            c = H3Client(endpoint="http://localhost:9000")
            assert c.hermes_token is None
            assert c.hermes_identity is None
            assert c.protocol_version == "1.0"

    def test_h3_api_key_env_var_fallback(self):
        """H3_API_KEY env var used when hermes_token not explicitly provided."""
        with patch.dict(os.environ, {"H3_API_KEY": "h3_testkey123"}):
            c = H3Client(endpoint="http://localhost:9000")
            assert c.hermes_token == "h3_testkey123"

    def test_explicit_token_overrides_env_var(self):
        """Explicit hermes_token takes priority over H3_API_KEY env var."""
        with patch.dict(os.environ, {"H3_API_KEY": "h3_envkey"}):
            c = H3Client(endpoint="http://localhost:9000", hermes_token="h3_explicit")
            assert c.hermes_token == "h3_explicit"

    def test_token_only_sends_auth_and_protocol_version(self):
        """Token without identity still sends Authorization + Protocol-Version."""
        c = H3Client(
            endpoint="http://localhost:9000",
            hermes_token="h3_hx_token_only",
        )
        assert c.hermes_token == "h3_hx_token_only"
        assert c.hermes_identity is None

    def test_identity_only_sends_identity_and_protocol_version(self):
        """Identity without token sends H3-Hermes-Identity + Protocol-Version."""
        c = H3Client(
            endpoint="http://localhost:9000",
            hermes_identity="hermes-alt",
            protocol_version="1.1",
        )
        assert c.hermes_token is None
        assert c.hermes_identity == "hermes-alt"
        assert c.protocol_version == "1.1"


# ── health() ────────────────────────────────────────────────────────────────


class TestHealth:
    async def test_ok_response(self):
        c = _make_client()
        c._rest.get.return_value = _fake_response(
            200,
            {
                "status": "ok",
                "version": "1.2.3",
                "capabilities": ["tool_call", "text"],
            },
        )
        result = await c.health()
        assert isinstance(result, HealthResponse)
        assert result.status == HealthStatus.OK
        assert result.version == "1.2.3"
        c._rest.get.assert_awaited_once_with("/v1/health")

    async def test_degraded_response(self):
        c = _make_client()
        c._rest.get.return_value = _fake_response(
            200,
            {
                "status": "degraded",
                "version": "1.0.0",
                "degraded_reason": "model unreachable",
                "capabilities": ["text"],
            },
        )
        result = await c.health()
        assert result.status == HealthStatus.DEGRADED
        assert result.degraded_reason == "model unreachable"

    async def test_down_response(self):
        c = _make_client()
        c._rest.get.return_value = _fake_response(
            200,
            {
                "status": "down",
                "version": "1.0.0",
                "error": "database connection lost",
            },
        )
        result = await c.health()
        assert result.status == HealthStatus.DOWN
        assert result.error == "database connection lost"

    async def test_health_http_error_propagates(self):
        c = _make_client()
        c._rest.get.return_value = _fake_response(503)
        with pytest.raises(httpx.HTTPStatusError):
            await c.health()


# ── process() ───────────────────────────────────────────────────────────────


class TestProcess:
    async def _run_process(self):
        c = _make_client()
        c._rest.post.return_value = _fake_response(
            200,
            {
                "decision": "tool_call",
                "decision_id": "d_proc_1",
                "tool_call": {
                    "name": "terminal",
                    "params": {"command": "ls"},
                    "reasoning": "list",
                },
            },
        )
        decision = await c.process(
            session_id="s_001",
            message=Message(role="user", content="run ls"),
            identity=Identity(platform="telegram", chat_id="-100"),
            context=Context(),
        )
        return c, decision

    async def test_returns_decision(self):
        c, decision = await self._run_process()
        assert isinstance(decision, Decision)
        assert decision.decision == DecisionType.TOOL_CALL
        assert decision.decision_id == "d_proc_1"
        assert decision.tool_call is not None
        assert decision.tool_call.name == "terminal"

    async def test_sends_process_request_payload(self):
        c, _ = await self._run_process()
        c._rest.post.assert_awaited_once()
        args, kwargs = c._rest.post.call_args
        assert args[0] == "/v1/process"
        assert "json" in kwargs
        body = kwargs["json"]
        assert body["session_id"] == "s_001"
        assert body["message"]["role"] == "user"
        assert body["identity"]["platform"] == "telegram"
        assert body["context"] == {} or "history" in body["context"]

    async def test_http_error_raises(self):
        c = _make_client()
        c._rest.post.return_value = _fake_response(500)
        with pytest.raises(httpx.HTTPStatusError):
            await c.process(
                session_id="s",
                message=Message(role="user", content="x"),
                identity=Identity(platform="cli", chat_id="0"),
                context=Context(),
            )

    async def test_timeout_returns_error_decision(self):
        """Harness timeout yields a user-visible END decision, not an exception."""
        c = _make_client(timeout_ms=5000)
        c._rest.post.side_effect = httpx.TimeoutException("timed out")
        decision = await c.process(
            session_id="s_timeout",
            message=Message(role="user", content="hi"),
            identity=Identity(platform="cli", chat_id="0"),
            context=Context(),
        )
        assert isinstance(decision, Decision)
        assert decision.decision == DecisionType.END
        assert decision.end is not None
        assert decision.end.reason == EndReason.TIMEOUT
        assert "timed out" in (decision.end.summary or "").lower()
        assert decision.decision_id.startswith("error-")


# ── result() ────────────────────────────────────────────────────────────────


class TestResult:
    async def test_returns_decision(self):
        c = _make_client()
        c._rest.post.return_value = _fake_response(
            200,
            {
                "decision": "end",
                "decision_id": "d_after_result",
                "end": {"reason": "task_complete", "summary": "ok"},
            },
        )
        decision = await c.result(
            session_id="s_001",
            decision_id="d_001",
            result=ExecutionResult(
                type="tool_result",
                tool_name="terminal",
                data={"output": "ok"},
                success=True,
            ),
        )
        assert isinstance(decision, Decision)
        assert decision.decision == DecisionType.END
        assert decision.end.reason == EndReason.TASK_COMPLETE

    async def test_sends_result_request_payload(self):
        c = _make_client()
        c._rest.post.return_value = _fake_response(
            200,
            {
                "decision": "end",
                "decision_id": "d_x",
                "end": {"reason": "task_complete"},
            },
        )
        await c.result(
            session_id="s_007",
            decision_id="d_006",
            result=ExecutionResult(type="tool_result", tool_name="x", data={"y": 1}),
        )
        args, kwargs = c._rest.post.call_args
        assert args[0] == "/v1/result"
        body = kwargs["json"]
        assert body["session_id"] == "s_007"
        assert body["decision_id"] == "d_006"
        assert body["result"]["type"] == "tool_result"

    async def test_4xx_raises(self):
        c = _make_client()
        c._rest.post.return_value = _fake_response(404)
        with pytest.raises(httpx.HTTPStatusError):
            await c.result(
                session_id="s",
                decision_id="d",
                result=ExecutionResult(type="tool_result"),
            )


# ── cancel() ────────────────────────────────────────────────────────────────


class TestCancel:
    async def test_returns_cancel_response(self):
        c = _make_client()
        c._rest.post.return_value = _fake_response(
            200,
            {
                "cancelled": True,
                "cancelled_decision_id": "d_42",
            },
        )
        result = await c.cancel("s_001")
        assert isinstance(result, CancelResponse)
        assert result.cancelled is True
        assert result.cancelled_decision_id == "d_42"

    async def test_sends_session_id_and_reason(self):
        c = _make_client()
        c._rest.post.return_value = _fake_response(200, {"cancelled": True})
        await c.cancel("s_007", reason="user_interrupt")
        args, kwargs = c._rest.post.call_args
        assert args[0] == "/v1/cancel"
        assert kwargs["json"]["session_id"] == "s_007"
        assert kwargs["json"]["reason"] == "user_interrupt"

    async def test_default_reason(self):
        c = _make_client()
        c._rest.post.return_value = _fake_response(200, {"cancelled": True})
        await c.cancel("s_007")
        args, _kwargs = c._rest.post.call_args
        assert args[0] == "/v1/cancel"

    async def test_http_error_raises(self):
        c = _make_client()
        c._rest.post.return_value = _fake_response(500)
        with pytest.raises(httpx.HTTPStatusError):
            await c.cancel("s_007")


# ── close() ─────────────────────────────────────────────────────────────────


class TestClose:
    async def test_close_calls_aclose(self):
        c = _make_client()
        await c.close()
        c._rest.aclose.assert_awaited_once()


# ── smoke / payload round-trip ──────────────────────────────────────────────


class TestPayloadShape:
    async def test_process_payload_round_trip(self):
        """The serialized ProcessRequest is exactly what the protocol wants."""
        req = ProcessRequest(
            session_id="s_rt",
            message=Message(role="user", content="hi"),
            identity=Identity(platform="discord", chat_id="c1", thread_id="t1"),
            context=Context(),
        )
        # Round-trip through json to mimic wire transport.
        as_json = json.loads(json.dumps(req.model_dump()))
        assert as_json["session_id"] == "s_rt"
        assert as_json["identity"]["thread_id"] == "t1"

    async def test_timestamped_payload_is_json_serializable(self):
        """DF3-H3-SHIM-1: datetime payload values must reach httpx as JSON.

        ``Message.timestamp`` is typed ``datetime | None``. The POST sites used
        ``req.model_dump()`` (python mode), which left the ``datetime`` object
        in the payload dict; httpx then ran ``json.dumps`` on it and raised
        ``TypeError: Object of type datetime is not JSON serializable``, which
        ``H3ShimLoop.run()`` masks as EndReason 'error'. The same class of value
        is reachable inside the free-form ``ExecutionResult.data``, so both POST
        sites are pinned here.
        """
        ts = datetime(2026, 9, 18, 6, 16, 3, tzinfo=timezone.utc)
        c = _make_client()
        c._rest.post.return_value = _fake_response(
            200,
            {
                "decision": "end",
                "decision_id": "d_ts",
                "end": {"reason": "task_complete"},
            },
        )

        await c.process(
            session_id="s_ts",
            message=Message(role="user", content="hi", timestamp=ts),
            identity=Identity(platform="telegram", chat_id="-100"),
            context=Context(
                history=[Message(role="user", content="prev", timestamp=ts)]
            ),
        )
        process_body = c._rest.post.call_args.kwargs["json"]
        # httpx runs exactly this on the payload — a datetime object raises.
        json.dumps(process_body)
        raw = process_body["message"]["timestamp"]
        assert isinstance(raw, str)
        assert datetime.fromisoformat(raw.replace("Z", "+00:00")) == ts
        assert process_body["context"]["history"][0]["timestamp"] == raw

        await c.result(
            session_id="s_ts",
            decision_id="d_ts",
            result=ExecutionResult(
                type="tool_result",
                data={"finished_at": ts},
                success=True,
            ),
        )
        result_body = c._rest.post.call_args.kwargs["json"]
        json.dumps(result_body)
        raw_result = result_body["result"]["data"]["finished_at"]
        assert isinstance(raw_result, str)
        assert datetime.fromisoformat(raw_result.replace("Z", "+00:00")) == ts


# ── strict-wire regression (DF4-H3-SHIM-1) ──────────────────────────────────


class _Strict400Error(Exception):
    """Raised by the strict handler where the real zod path would 400."""


class _StrictZodLikeHandler:
    """In-process handler that mirrors the ts scaffold's zod strictness.

    The generated ts harness validates ``ProcessRequest`` /
    ``ResultRequest`` with the ``@get-h3/h3-harness-sdk`` zod schema, where
    optional fields such as ``identity.thread_id`` are
    ``.optional()`` but NOT ``.nullable()``: the field may be ABSENT from
    the JSON, but an explicit ``null`` is a type error. So a pydantic
    ``model_dump`` that materializes unset ``None`` optionals fails with
    HTTP 400 on the documented default path.

    This handler reimplements that rule for the fields the wire requests
    carry and returns a minimal valid H3 ``Decision``. Usable directly as
    an ``httpx.MockTransport`` handler.
    """

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8")
        if request.method == "POST" and request.url.path == "/v1/process":
            self._reject_explicit_null_optionals(
                body,
                path="identity",
                optionals=("thread_id", "user_name", "user_id"),
            )
            return httpx.Response(
                200,
                json={
                    "decision": "text",
                    "decision_id": "d_strict_1",
                    "text": {"content": "Echo: hi", "finished": True},
                },
            )
        if request.method == "POST" and request.url.path == "/v1/result":
            self._reject_explicit_null_optionals(
                body,
                path="result",
                optionals=("tool_name",),
            )
            return httpx.Response(
                200,
                json={
                    "decision": "end",
                    "decision_id": "d_strict_end",
                    "end": {"reason": "task_complete", "summary": "ok"},
                },
            )
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})

    @staticmethod
    def _reject_explicit_null_optionals(
        body: str, *, path: str, optionals: tuple[str, ...]
    ) -> None:
        payload = json.loads(body)
        section = payload.get(path, {})
        for field in optionals:
            if field in section and section[field] is None:
                raise _Strict400Error(
                    f"zod: {path}.{field} must be string or absent, got explicit null"
                )


class TestStrictHarnessWireShape:
    """DF4-H3-SHIM-1: no explicit nulls on the wire for unset optionals.

    Strict zod harnesses (ts scaffold via @get-h3/h3-harness-sdk) type
    optional request fields as ``.optional()`` — ABSENT is accepted,
    explicit ``null`` is rejected with 400. The pre-fix client serialized
    with plain ``model_dump(mode="json")``, which materializes every unset
    optional (``thread_id``, ``user_name``, …) as ``null`` and broke the
    DOCUMENTED default path (``H3ShimLoop(...)`` with identity omitted,
    docs/api.md §process) end-to-end.
    """

    async def test_default_identity_process_wire_has_no_explicit_nulls(self):
        """Default-path /v1/process body carries no explicit-null optionals."""
        captured: dict[str, str] = {}
        strict = _StrictZodLikeHandler()
        real_transport = httpx.MockTransport(strict.handle_request)

        client = H3Client(endpoint="http://harness.test")

        async def capture_and_reject(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content.decode("utf-8")
            return await real_transport.handle_async_request(request)

        client._rest = httpx.AsyncClient(
            base_url=client.endpoint,
            transport=httpx.MockTransport(capture_and_reject),
        )
        try:
            decision = await client.process(
                session_id="s_df4",
                message=Message(role="user", content="hi"),
                identity=Identity(platform="shim", chat_id="s_df4"),
                context=Context(),
            )
        finally:
            await client._rest.aclose()

        assert decision.decision_id == "d_strict_1"
        assert '"thread_id": null' not in captured["body"]
        assert '"user_name": null' not in captured["body"]
        assert '"user_id": null' not in captured["body"]

    async def test_shim_loop_default_path_end_to_end_strict_harness(self):
        """H3ShimLoop with NO identity kwarg completes against strict zod.

        This is the docs/api.md documented default: the loop fabricates
        ``Identity(platform="shim", chat_id=session_id)`` whose optional
        fields are unset. Both POSTs (process and result) must survive a
        harness that rejects explicit nulls exactly like the ts scaffold.
        """
        strict = _StrictZodLikeHandler()

        async def strict_send(request: httpx.Request) -> httpx.Response:
            try:
                return strict.handle_request(request)
            except _Strict400Error as exc:
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "code": "VALIDATION_ERROR",
                            "message": str(exc),
                        }
                    },
                )

        client = H3Client(endpoint="http://harness.test")
        client._rest = httpx.AsyncClient(
            base_url=client.endpoint,
            transport=httpx.MockTransport(strict_send),
        )

        loop = H3ShimLoop(  # identity intentionally omitted — default path
            client,
            session_id="s_df4_loop",
            context=Context(),
        )
        try:
            reason = await loop.run(Message(role="user", content="hi"))
        finally:
            await client._rest.aclose()

        assert reason == "task_complete"
        assert loop.last_error is None

    async def test_set_optional_still_sent_and_absent_one_omitted(self):
        """exclude_unset only drops UNSET fields — explicit values survive.

        Guards against overcorrecting: a set ``thread_id`` must still
        reach the wire, and a never-touched optional should be absent
        (not ``null``) from the serialized dict.
        """
        c = _make_client()
        c._rest.post.return_value = _fake_response(
            200,
            {
                "decision": "end",
                "decision_id": "d_opt",
                "end": {"reason": "task_complete"},
            },
        )
        await c.process(
            session_id="s_opt",
            message=Message(role="user", content="hi"),
            identity=Identity(platform="cli", chat_id="c1", thread_id="t9"),
            context=Context(),
        )
        identity_body = c._rest.post.call_args.kwargs["json"]["identity"]
        assert identity_body["thread_id"] == "t9"
        assert "user_name" not in identity_body
        assert "user_id" not in identity_body


# ── real-stack regression (DF4-H3-SHIM-1) — real ts scaffold + zod ──────────


def _free_port() -> int:
    """Bind a throwaway socket to get a free port, then release it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_healthy(port: int, timeout_s: float = 60.0) -> None:
    """Poll /v1/health until the ts harness answers or the timeout elapses."""
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
    raise AssertionError(f"ts scaffold harness on :{port} never became healthy")


def _stop_process_tree(proc: subprocess.Popen) -> None:
    """SIGTERM the harness process group, escalating to SIGKILL if needed.

    The harness is started with ``start_new_session=True`` (tsx spawns a
    child for the actual module), so the group kill reaches the listener
    even when the wrapper dies first.
    """
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:  # pragma: no cover - already gone
        pass
    try:
        proc.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:  # pragma: no cover - already gone
        pass
    proc.wait(timeout=10)


@pytest.fixture(scope="module")
def ts_scaffold_zod(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Scaffold, install and RUN the real ts harness once for the module.

    GAP-047 doctrine (mirrors ``py_scaffold`` in test_scaffold_build.py):
    the heavy work — scaffold, ``npm install`` (pulls the real zod
    schemas via @get-h3/h3-harness-sdk) and harness boot — happens ONCE
    per module run; the consuming test drives the live server over real
    HTTP. stdout/stderr go to a FILE, never an undrained PIPE (known
    anon_pipe_write deadlock). Teardown terminates the process group and
    verifies the port is actually freed.
    """
    npm = shutil.which("npm")
    if npm is None:
        pytest.skip("npm toolchain not installed")

    base = tmp_path_factory.mktemp("ts-scaffold-zod")
    proj = scaffold_project("ts", base, overwrite=True)
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

    # Equivalent to the dev path (`npx tsx index.ts`): run the template's
    # TypeScript source directly under tsx so the SDK's zod validation is
    # exercised exactly as a developer runs the scaffold.
    node = shutil.which("node")
    assert node is not None, "node not found on PATH"
    tsx_cli = proj / "node_modules" / "tsx" / "dist" / "cli.mjs"
    assert tsx_cli.is_file(), f"tsx CLI missing after npm install: {tsx_cli}"

    port = _free_port()
    log_path = base / "harness.log"
    log_file = log_path.open("w")
    proc = subprocess.Popen(
        [node, str(tsx_cli), "index.ts"],
        cwd=proj,
        env={**os.environ, "PORT": str(port)},
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        _wait_healthy(port)
        yield f"http://127.0.0.1:{port}"
    finally:
        _stop_process_tree(proc)
        log_file.close()
        # The listener must actually be gone — an orphaned tsx child would
        # keep the port and poison the next module run.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) != 0:
                    break
            time.sleep(0.2)
        else:  # pragma: no cover - only on a leaked listener
            raise AssertionError(f"port {port} still in use after harness teardown")


class TestRealTsScaffoldInterop:
    """DF4-H3-SHIM-1 — the REAL ts scaffold accepts the default-identity wire.

    ``TestStrictHarnessWireShape`` pins the wire contract with a
    hand-rolled Python mirror of the ts zod rules; these tests drive the
    REAL stack instead: the scaffolded harness (``templates/ts``) running
    under tsx, validating every POST with ``@get-h3/h3-harness-sdk``'s
    zod schema over real HTTP — wire-shape interop, not a
    re-implementation of it.

    Live-verified SDK facts these tests are built on (dist/protocol.js of
    the github:get-h3/sdk-typescript install):

    * ``IdentitySchema`` types ``thread_id`` / ``user_name`` / ``user_id``
      as ``.optional()`` (NOT ``.nullable()``) — the DF4-H3-SHIM-1
      subject: explicit nulls on the wire are 400s.
    * ``ContextSchema`` types ``config`` and ``session_state`` as
      REQUIRED objects (``history``/``tools``/``models`` carry
      ``.default()``, these two do not), so the tests pass them
      explicitly — explicitly-passed fields survive ``exclude_unset``.
      Invisible to the Python-mock layer; flagged as a follow-up
      protocol-doc gap, not fixed here (this task is test-only).
    * ``ResultRequestSchema.result.duration_ms`` is
      ``z.number().int().min(0).optional()`` while the SDK's own
      ``ResultPayloadSchema`` types the same field
      ``z.number().min(0)`` — the request wrapper deviates from both the
      protocol spec (py ``ExecutionResult.duration_ms: float``) and the
      SDK's own payload schema. ``H3ShimLoop``'s executors therefore
      round their measured duration to a non-negative integer before
      assigning it, so a loop-driven result POST validates on every
      real execution (proven end-to-end below).
    """

    async def test_real_ts_scaffold_accepts_default_identity_loop(
        self, ts_scaffold_zod: str
    ) -> None:
        """Full echo cycle against the real zod stack — no 400 anywhere.

        Drives ``H3Client`` over real HTTP exactly the way
        ``H3ShimLoop.run`` drives it: the default-path identity the loop
        fabricates when the kwarg is omitted
        (``Identity(platform="shim", chat_id=session_id)``,
        shim_loop.py ``__init__``), whose optional fields are unset and
        must be ABSENT from the wire, and the cycle process → text →
        result → second text → result → END(task_complete). The
        pre-fix client (plain ``model_dump(mode="json")``) materialized
        the unset identity optionals as nulls and the real SDK 400'd the
        very first POST. The result POSTs omit ``duration_ms`` (unset →
        excluded → zod-optional) because of the SDK int violation
        documented on the class — that limitation is what the strict
        xfail loop test tracks.
        """
        session_id = f"s_df4_real_{uuid.uuid4().hex[:8]}"
        # The exact identity H3ShimLoop fabricates for the default path.
        identity = Identity(platform="shim", chat_id=session_id)
        context = Context(config={}, session_state={})

        client = H3Client(endpoint=ts_scaffold_zod)
        decisions: list[Decision] = []
        try:
            decision = await client.process(
                session_id=session_id,
                message=Message(role="user", content="hi"),
                identity=identity,
                context=context,
            )
            decisions.append(decision)

            # Two result round-trips are what the echo harness needs to
            # reach its END (resultCount >= 2).
            for _ in range(2):
                assert decision.decision == DecisionType.TEXT
                assert decision.text is not None
                decision = await client.result(
                    session_id=session_id,
                    decision_id=decision.decision_id,
                    # duration_ms left UNSET: excluded from the wire.
                    result=ExecutionResult(
                        type="text_sent",
                        data={"content": decision.text.content, "finished": True},
                        success=True,
                    ),
                )
                decisions.append(decision)
        finally:
            await client.close()

        assert [d.decision for d in decisions[:2]] == [
            DecisionType.TEXT,
            DecisionType.TEXT,
        ]
        assert decisions[0].text is not None
        assert decisions[0].text.content == "Echo: hi"
        assert decisions[-1].decision == DecisionType.END
        assert decisions[-1].end is not None
        assert decisions[-1].end.reason == EndReason.TASK_COMPLETE

    async def test_shim_loop_real_ts_scaffold_default_identity_end_to_end(
        self, ts_scaffold_zod: str
    ) -> None:
        """H3ShimLoop, NO identity kwarg, completes against the REAL zod stack.

        This is the brief's literal end-to-end shape. A 400 on either
        POST surfaces as reason ``"error"`` + ``last_error``; the natural
        ``task_complete`` end proves every request validated — including
        the loop-driven result POSTs, whose ``duration_ms`` the executors
        now round to schema-legal integers.
        """
        texts: list[str] = []
        client = H3Client(endpoint=ts_scaffold_zod)
        loop = H3ShimLoop(  # identity intentionally omitted — default path
            client,
            session_id=f"s_df4_loop_{uuid.uuid4().hex[:8]}",
            # Explicitly-passed so exclude_unset keeps them on the wire
            # (the SDK zod schema requires both as objects).
            context=Context(config={}, session_state={}),
            on_text=texts.append,
        )
        try:
            reason = await loop.run(Message(role="user", content="hi"))
        finally:
            await client.close()

        assert reason == "task_complete"
        assert loop.last_error is None
        assert loop.iteration == 2  # two result round-trips, then END
        assert texts[0] == "Echo: hi"
        assert texts[1].startswith("Result received: ")
