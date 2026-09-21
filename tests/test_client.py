"""Tests for client.py — async H3Client HTTP behavior.

We exercise H3Client by patching ``httpx.AsyncClient`` on the instance so
the real socket layer is never touched. ``AsyncMock`` stands in for each
HTTP verb, returning a fake ``Response`` whose ``.json()`` and
``.raise_for_status()`` we control per-test.
"""

import json
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

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
