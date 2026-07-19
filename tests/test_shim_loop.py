"""Tests for shim_loop.py — H3ShimLoop process / execute / result loop.

The shim loop drives a single H3 session by calling H3Client.process to
get a Decision, executing it locally (tool, LLM, text, wait, delegate, …),
and POSTing the ExecutionResult back via H3Client.result.  All H3Client
methods are stubbed via AsyncMock so no network traffic leaves the test
process.  Each executor is exercised in isolation, plus the top-level
run() orchestration including the cancellation, timeout, and error
sentinels.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from h3_shim.protocol import (
    Context,
    Decision,
    DecisionType,
    Delegate,
    End,
    EndReason,
    ExecutionResult,
    Identity,
    LLMCall,
    Message,
    TextResponse,
    ToolCall,
    Wait,
)
from h3_shim.shim_loop import H3ShimLoop

# ── helpers ─────────────────────────────────────────────────────────────────


def _msg() -> Message:
    return Message(role="user", content="hi")


def _ctx() -> Context:
    return Context()


def _decision(
    decision_type: DecisionType = DecisionType.END,
    decision_id: str = "d_001",
    **kw,
) -> Decision:
    """Build a Decision with the given type and an appropriate sub-object."""
    data: dict = {"decision": decision_type, "decision_id": decision_id}
    if decision_type == DecisionType.END:
        data["end"] = kw.get("end", End(reason=EndReason.TASK_COMPLETE, summary="done"))
    elif decision_type == DecisionType.TOOL_CALL:
        data["tool_call"] = kw.get("tool_call", ToolCall(name="test_tool", params={}))
    elif decision_type == DecisionType.LLM_CALL:
        data["llm_call"] = kw.get("llm_call", LLMCall(model="gpt-4", messages=[]))
    elif decision_type == DecisionType.TEXT:
        data["text"] = kw.get("text", TextResponse(content="hello"))
    elif decision_type == DecisionType.WAIT:
        data["wait"] = kw.get("wait", Wait(reason="poll"))
    elif decision_type == DecisionType.DELEGATE:
        data["delegate"] = kw.get("delegate", Delegate(task="subtask"))
    return Decision(**data)


def _mock_client() -> MagicMock:
    """Return a MagicMock H3Client with AsyncMock methods."""
    c = MagicMock()
    c.process = AsyncMock()
    c.result = AsyncMock()
    c.cancel = AsyncMock()
    return c


def _make_loop(client=None, **kw) -> H3ShimLoop:
    """Convenience constructor with sensible defaults."""
    return H3ShimLoop(
        client=client or _mock_client(),
        session_id="sess_test",
        context=_ctx(),
        **kw,
    )


# ── construction & identity ─────────────────────────────────────────────────


class TestConstruction:
    def test_identity_default_uses_shim_and_session_id(self):
        loop = _make_loop()
        assert loop.identity.platform == "shim"
        assert loop.identity.chat_id == "sess_test"

    def test_identity_supplied_when_provided(self):
        ident = Identity(platform="telegram", chat_id="-100", thread_id="42")
        loop = H3ShimLoop(
            client=_mock_client(),
            session_id="sess_test",
            context=_ctx(),
            identity=ident,
        )
        assert loop.identity is ident
        assert loop.identity.thread_id == "42"

    def test_iteration_starts_at_zero(self):
        loop = _make_loop()
        assert loop.iteration == 0

    def test_max_iterations_default_is_50(self):
        loop = _make_loop()
        assert loop.max_iterations == 50

    def test_max_iterations_override(self):
        loop = _make_loop(max_iterations=3)
        assert loop.max_iterations == 3

    def test_available_tools_starts_empty(self):
        loop = _make_loop()
        assert loop._available_tools == {}


# ── register_tool ───────────────────────────────────────────────────────────


class TestRegisterTool:
    def test_register_tool_stores_callable(self):
        loop = _make_loop()

        def fn(**kwargs):
            return "result"

        loop.register_tool("my_tool", fn)
        assert loop._available_tools["my_tool"] is fn

    def test_register_tool_overwrites(self):
        loop = _make_loop()

        def a(**kw):
            return "a"

        def b(**kw):
            return "b"

        loop.register_tool("name", a)
        loop.register_tool("name", b)
        assert loop._available_tools["name"] is b

    def test_register_multiple_tools(self):
        loop = _make_loop()

        def f1(**kw):
            pass

        def f2(**kw):
            pass

        loop.register_tool("a", f1)
        loop.register_tool("b", f2)
        assert set(loop._available_tools) == {"a", "b"}


# ── run() ───────────────────────────────────────────────────────────────────


class TestRun:
    @pytest.mark.asyncio
    async def test_run_immediate_end_returns_reason(self):
        client = _mock_client()
        client.process.return_value = _decision(
            DecisionType.END,
            end=End(reason=EndReason.TASK_COMPLETE, summary="ok"),
        )
        loop = _make_loop(client=client)
        reason = await loop.run(_msg())
        assert reason == "task_complete"
        client.process.assert_awaited_once()
        client.result.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_tool_call_then_end(self):
        client = _mock_client()
        client.process.return_value = _decision(DecisionType.TOOL_CALL)
        # After the tool executes, the harness returns END.
        client.result.return_value = _decision(
            DecisionType.END, decision_id="d_002",
            end=End(reason=EndReason.TASK_COMPLETE, summary="done"),
        )
        loop = _make_loop(client=client)
        loop.register_tool("test_tool", lambda **kw: "ok")
        reason = await loop.run(_msg())
        assert reason == "task_complete"
        assert client.process.await_count == 1
        assert client.result.await_count == 1
        # Iteration counter advanced exactly once.
        assert loop.iteration == 1

    @pytest.mark.asyncio
    async def test_run_tool_then_llm_then_end(self):
        client = _mock_client()
        client.process.return_value = _decision(DecisionType.TOOL_CALL)
        # Two follow-up decisions: LLM_CALL then END.
        client.result.side_effect = [
            _decision(DecisionType.LLM_CALL, decision_id="d_002"),
            _decision(
                DecisionType.END, decision_id="d_003",
                end=End(reason=EndReason.USER_REQUESTED, summary="bye"),
            ),
        ]
        loop = _make_loop(client=client)
        loop.register_tool("test_tool", lambda **kw: "ok")
        reason = await loop.run(_msg())
        assert reason == "user_requested"
        assert loop.iteration == 2
        assert client.process.await_count == 1
        assert client.result.await_count == 2

    @pytest.mark.asyncio
    async def test_run_enforces_max_iterations(self):
        client = _mock_client()
        # Always return TOOL_CALL → loop spins until max_iterations.
        client.process.return_value = _decision(DecisionType.TOOL_CALL)
        client.result.return_value = _decision(
            DecisionType.TOOL_CALL, decision_id="d_loop"
        )
        loop = _make_loop(client=client, max_iterations=3)
        loop.register_tool("test_tool", lambda **kw: "ok")
        reason = await loop.run(_msg())
        assert reason == "timeout"
        # The loop increments first, then checks `iteration > max_iterations`.
        # With max_iterations=3: iter→1,2,3 (execute each), iter→4 (>3) → exit.
        assert loop.iteration == 4
        assert client.result.await_count == 3

    @pytest.mark.asyncio
    async def test_run_handles_cancelled_error(self):
        client = _mock_client()
        client.process.side_effect = asyncio.CancelledError()
        loop = _make_loop(client=client)
        reason = await loop.run(_msg())
        assert reason == "cancelled"
        client.cancel.assert_awaited_once_with("sess_test")

    @pytest.mark.asyncio
    async def test_run_swallows_cancel_rpc_failure(self):
        client = _mock_client()
        client.process.side_effect = asyncio.CancelledError()
        client.cancel.side_effect = RuntimeError("rpc down")
        loop = _make_loop(client=client)
        reason = await loop.run(_msg())
        # Cancellation still propagates as the sentinel; we don't re-raise.
        assert reason == "cancelled"

    @pytest.mark.asyncio
    async def test_run_handles_generic_exception(self):
        client = _mock_client()
        client.process.side_effect = RuntimeError("boom")
        loop = _make_loop(client=client)
        reason = await loop.run(_msg())
        assert reason == "error"

    @pytest.mark.asyncio
    async def test_run_passes_session_message_identity_context(self):
        client = _mock_client()
        end = _decision(
            DecisionType.END, end=End(reason=EndReason.TASK_COMPLETE, summary="ok"),
        )
        client.process.return_value = end
        msg = _msg()
        ctx = _ctx()
        ident = Identity(platform="telegram", chat_id="-100")
        loop = H3ShimLoop(
            client=client, session_id="s_x", context=ctx, identity=ident,
        )
        await loop.run(msg)
        # process(session_id, message, identity, context)
        args, kwargs = client.process.call_args
        assert args[0] == "s_x"
        assert args[1] is msg
        assert args[2] is ident
        assert args[3] is ctx

    @pytest.mark.asyncio
    async def test_run_result_includes_decision_id(self):
        client = _mock_client()
        client.process.return_value = _decision(
            DecisionType.TOOL_CALL, decision_id="d_xyz",
            tool_call=ToolCall(name="my_tool", params={"x": 1}),
        )
        client.result.return_value = _decision(
            DecisionType.END, decision_id="d_end",
            end=End(reason=EndReason.TASK_COMPLETE, summary="ok"),
        )
        loop = _make_loop(client=client)
        loop.register_tool("my_tool", lambda **kw: "ok")
        await loop.run(_msg())
        # result(session_id, decision_id, execution_result)
        args, _ = client.result.call_args
        assert args[0] == "sess_test"
        assert args[1] == "d_xyz"
        assert isinstance(args[2], ExecutionResult)


# ── _execute_tool ───────────────────────────────────────────────────────────


class TestExecuteTool:
    @pytest.mark.asyncio
    async def test_calls_registered_tool_with_params(self):
        loop = _make_loop()
        called: dict = {}

        def my_tool(name: str = "world") -> str:
            called["name"] = name
            return f"hello {name}"

        loop.register_tool("greet", my_tool)
        result = await loop._execute_tool(
            ToolCall(name="greet", params={"name": "alice"})
        )
        assert called == {"name": "alice"}
        assert result.type == "tool_result"
        assert result.success is True
        assert result.tool_name == "greet"
        assert result.data == {"output": "hello alice"}

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error_result(self):
        loop = _make_loop()
        result = await loop._execute_tool(ToolCall(name="ghost", params={}))
        assert result.type == "error"
        assert result.success is False
        assert result.tool_name == "ghost"
        assert "Unknown tool" in result.data.get("error", "")

    @pytest.mark.asyncio
    async def test_tool_exception_returns_error_result(self):
        loop = _make_loop()

        def bad_tool(**kw):
            raise ValueError("nope")

        loop.register_tool("bad", bad_tool)
        result = await loop._execute_tool(ToolCall(name="bad", params={}))
        assert result.type == "error"
        assert result.success is False
        assert result.tool_name == "bad"
        assert "nope" in result.data.get("error", "")

    @pytest.mark.asyncio
    async def test_tool_result_has_duration_ms(self):
        loop = _make_loop()
        loop.register_tool("noop", lambda **kw: None)
        result = await loop._execute_tool(ToolCall(name="noop", params={}))
        assert result.duration_ms >= 0.0


# ── _execute_llm ────────────────────────────────────────────────────────────


class TestExecuteLLM:
    @pytest.mark.asyncio
    async def test_returns_placeholder_response(self):
        loop = _make_loop()
        llm = LLMCall(
            model="claude-opus-4",
            messages=[Message(role="user", content="hi")],
        )
        result = await loop._execute_llm(llm)
        assert result.type == "llm_response"
        assert result.success is True
        assert result.data.get("model") == "claude-opus-4"
        assert "content" in result.data
        assert result.duration_ms >= 0.0


# ── _execute_text ───────────────────────────────────────────────────────────


class TestExecuteText:
    @pytest.mark.asyncio
    async def test_returns_text_sent_with_content(self):
        loop = _make_loop()
        result = await loop._execute_text(TextResponse(content="hi there"))
        assert result.type == "text_sent"
        assert result.success is True
        assert result.data.get("content") == "hi there"
        assert "finished" not in result.data

    @pytest.mark.asyncio
    async def test_finished_flag_propagated(self):
        loop = _make_loop()
        result = await loop._execute_text(TextResponse(content="bye", finished=True))
        assert result.data.get("finished") is True

    @pytest.mark.asyncio
    async def test_result_has_duration_ms(self):
        loop = _make_loop()
        result = await loop._execute_text(TextResponse(content="x"))
        assert result.duration_ms >= 0.0


# ── _execute_wait ───────────────────────────────────────────────────────────


class TestExecuteWait:
    @pytest.mark.asyncio
    async def test_sleeps_for_duration(self, monkeypatch):
        loop = _make_loop()
        slept: list[float] = []

        async def fake_sleep(t):
            slept.append(t)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        result = await loop._execute_wait(Wait(reason="poll", duration_seconds=5))
        assert slept == [5]
        assert result.type == "wait_complete"
        assert result.success is True
        # ``duration`` is the measured wall-clock wait time; with sleep
        # mocked out it is ~0, but must be present and non-negative.
        assert result.data.get("duration") >= 0
        assert result.data.get("reason") == "poll"
        assert result.data.get("polls") == 0

    @pytest.mark.asyncio
    async def test_no_duration_does_not_sleep(self, monkeypatch):
        loop = _make_loop()
        slept: list = []

        async def fake_sleep(t):
            slept.append(t)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        result = await loop._execute_wait(Wait(reason="no-time"))
        assert slept == []
        assert result.data.get("duration") >= 0
        assert result.success is True

    @pytest.mark.asyncio
    async def test_result_has_duration_ms(self):
        loop = _make_loop()
        result = await loop._execute_wait(Wait(reason="r"))
        assert result.duration_ms >= 0.0

    # ── poll_endpoint polling ────────────────────────────────────────

    @staticmethod
    def _mock_http_client(responses=None, side_effect=None):
        """Build an AsyncMock standing in for ``httpx.AsyncClient``.

        ``responses`` is a list of (status_code, json_body) tuples
        returned in order; the last one repeats once exhausted.
        ``side_effect`` (e.g. an exception instance) is raised from
        every ``get`` call instead.
        """
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = False

        if side_effect is not None:
            mock_client.get.side_effect = side_effect
        else:
            calls = {"n": 0}

            def _next_response(*_args, **_kw):
                idx = min(calls["n"], len(responses) - 1)
                calls["n"] += 1
                status, body = responses[idx]
                resp = MagicMock()
                resp.status_code = status
                resp.json.return_value = body
                return resp

            mock_client.get.side_effect = _next_response
        return mock_client

    @pytest.mark.asyncio
    async def test_polling_endpoint_completes(self, monkeypatch):
        loop = _make_loop()
        mock_client = self._mock_http_client(
            responses=[(200, {"status": "complete"})]
        )
        monkeypatch.setattr(
            "h3_shim.shim_loop.httpx.AsyncClient",
            lambda *a, **kw: mock_client,
        )
        result = await loop._execute_wait(
            Wait(reason="poll", poll_endpoint="http://h/job/1")
        )
        assert result.type == "wait_complete"
        assert result.success is True
        assert result.data.get("polls") == 1
        assert result.data.get("poll_endpoint") == "http://h/job/1"
        assert result.data.get("reason") == "poll"
        assert "error" not in result.data
        assert mock_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_polling_endpoint_retries_then_completes(self, monkeypatch):
        loop = _make_loop()
        loop.poll_interval = 0  # keep the test fast
        mock_client = self._mock_http_client(
            responses=[
                (200, {"status": "pending"}),
                (200, {"status": "pending"}),
                (200, {"status": "pending"}),
                (200, {"status": "complete"}),
            ]
        )
        monkeypatch.setattr(
            "h3_shim.shim_loop.httpx.AsyncClient",
            lambda *a, **kw: mock_client,
        )
        result = await loop._execute_wait(
            Wait(reason="poll", poll_endpoint="http://h/job/2")
        )
        assert result.success is True
        assert result.data.get("polls") == 4
        assert mock_client.get.call_count == 4

    @pytest.mark.asyncio
    async def test_polling_endpoint_timeout(self, monkeypatch):
        loop = _make_loop()
        loop.max_polls = 5
        loop.poll_interval = 0
        mock_client = self._mock_http_client(
            responses=[(200, {"status": "pending"})]
        )
        monkeypatch.setattr(
            "h3_shim.shim_loop.httpx.AsyncClient",
            lambda *a, **kw: mock_client,
        )
        result = await loop._execute_wait(
            Wait(reason="poll", poll_endpoint="http://h/job/3")
        )
        assert result.type == "wait_complete"
        assert result.success is False
        assert result.data.get("polls") == 5
        assert "did not complete" in result.data.get("error", "")
        assert mock_client.get.call_count == 5

    @pytest.mark.asyncio
    async def test_polling_endpoint_http_error(self, monkeypatch):
        import httpx

        loop = _make_loop()
        mock_client = self._mock_http_client(
            side_effect=httpx.RequestError("connection refused")
        )
        monkeypatch.setattr(
            "h3_shim.shim_loop.httpx.AsyncClient",
            lambda *a, **kw: mock_client,
        )
        result = await loop._execute_wait(
            Wait(reason="poll", poll_endpoint="http://h/job/4")
        )
        assert result.type == "wait_complete"
        assert result.success is False
        assert "poll request failed" in result.data.get("error", "")
        assert result.data.get("polls") == 1

    @pytest.mark.asyncio
    async def test_polling_endpoint_with_duration_sleep(self, monkeypatch):
        loop = _make_loop()
        slept: list[float] = []

        async def fake_sleep(t):
            slept.append(t)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        mock_client = self._mock_http_client(
            responses=[(200, {"status": "complete"})]
        )
        monkeypatch.setattr(
            "h3_shim.shim_loop.httpx.AsyncClient",
            lambda *a, **kw: mock_client,
        )
        result = await loop._execute_wait(
            Wait(
                reason="sleep-then-poll",
                duration_seconds=3,
                poll_endpoint="http://h/job/5",
            )
        )
        # Duration sleep happens BEFORE any polling.
        assert slept == [3]
        assert mock_client.get.call_count == 1
        assert result.success is True
        assert result.data.get("polls") == 1
        assert result.data.get("poll_endpoint") == "http://h/job/5"


# ── _execute_delegate ───────────────────────────────────────────────────────


class TestExecuteDelegate:
    @pytest.mark.asyncio
    async def test_returns_delegate_result(self):
        loop = _make_loop()
        result = await loop._execute_delegate(Delegate(task="do-something"))
        assert result.type == "delegate_result"
        assert result.success is True
        assert result.data.get("task") == "do-something"
        assert result.data.get("status") == "accepted"
        assert result.duration_ms >= 0.0


# ── _execute dispatch & errors ──────────────────────────────────────────────


class TestExecuteDispatch:
    @pytest.mark.asyncio
    async def test_end_returns_simple_success(self):
        loop = _make_loop()
        result = await loop._execute(_decision(DecisionType.END))
        assert result.type == "end"
        assert result.success is True

    @pytest.mark.asyncio
    async def test_unknown_decision_type_returns_error(self):
        loop = _make_loop()
        # Construct a Decision with a valid but unusual type, then patch the
        # executor dispatch table to fall through to the unknown branch.
        # Simpler: forge a Decision with an enum value not handled.
        fake_decision = Decision(decision=DecisionType.END, decision_id="d")  # baseline
        # Now mutate .decision to a sentinel not in the dispatch table.
        object.__setattr__(fake_decision, "decision", "not_a_real_type")
        result = await loop._execute(fake_decision)
        assert result.type == "error"
        assert result.success is False
        assert "Unknown decision type" in result.data.get("error", "")

    @pytest.mark.asyncio
    async def test_executor_exception_returns_error_result(self, monkeypatch):
        loop = _make_loop()

        async def boom(self, llm):
            raise RuntimeError("llm dispatch exploded")

        monkeypatch.setattr(H3ShimLoop, "_execute_llm", boom)
        result = await loop._execute(_decision(DecisionType.LLM_CALL))
        assert result.type == "error"
        assert result.success is False
        assert "llm dispatch exploded" in result.data.get("error", "")
        assert result.data.get("phase", "").startswith("dispatch:")

    @pytest.mark.asyncio
    async def test_all_executors_set_duration_ms(self):
        loop = _make_loop()
        loop.register_tool("t", lambda **kw: None)
        cases = [
            (DecisionType.TOOL_CALL, ToolCall(name="t", params={})),
            (DecisionType.LLM_CALL, LLMCall(model="m", messages=[])),
            (DecisionType.TEXT, TextResponse(content="c")),
            (DecisionType.WAIT, Wait(reason="r")),
            (DecisionType.DELEGATE, Delegate(task="t")),
            (DecisionType.END, None),
        ]
        for dtype, sub in cases:
            data = {"decision": dtype, "decision_id": "d"}
            if sub is not None:
                if dtype == DecisionType.TOOL_CALL:
                    data["tool_call"] = sub
                elif dtype == DecisionType.LLM_CALL:
                    data["llm_call"] = sub
                elif dtype == DecisionType.TEXT:
                    data["text"] = sub
                elif dtype == DecisionType.WAIT:
                    data["wait"] = sub
                elif dtype == DecisionType.DELEGATE:
                    data["delegate"] = sub
            elif dtype == DecisionType.END:
                data["end"] = End(reason=EndReason.TASK_COMPLETE, summary="ok")
            r = await loop._execute(Decision(**data))
            assert r.duration_ms >= 0.0, f"missing duration_ms for {dtype}"
