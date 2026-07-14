"""Tests for protocol.py — Pydantic model validation and serialization."""

import json

from h3_shim.protocol import (
    Attachment,
    CancelResponse,
    Context,
    Decision,
    DecisionType,
    End,
    EndReason,
    ExecutionResult,
    HealthResponse,
    HealthStatus,
    Identity,
    Message,
    ProcessRequest,
    ToolCall,
)


class TestMessage:
    def test_basic_message(self):
        msg = Message(role="user", content="hello")
        d = msg.model_dump()
        assert d["role"] == "user"
        assert d["content"] == "hello"
        assert d["attachments"] == []

    def test_message_with_attachment(self):
        msg = Message(
            role="user",
            content="look at this",
            attachments=[
                Attachment(
                    type="image",
                    url="file:///img.png",
                    mime_type="image/png",
                )
            ],
        )
        d = msg.model_dump()
        assert len(d["attachments"]) == 1
        assert d["attachments"][0]["type"] == "image"


class TestDecision:
    def test_end_decision(self):
        decision = Decision(
            decision=DecisionType.END,
            decision_id="d_001",
            end=End(reason=EndReason.TASK_COMPLETE, summary="done"),
        )
        d = decision.model_dump()
        assert d["decision"] == "end"
        assert d["end"]["reason"] == "task_complete"

    def test_tool_call_decision(self):
        decision = Decision(
            decision=DecisionType.TOOL_CALL,
            decision_id="d_002",
            tool_call=ToolCall(
                name="terminal",
                params={"command": "ls"},
                reasoning="list files",
            ),
        )
        d = decision.model_dump()
        assert d["decision"] == "tool_call"
        assert d["tool_call"]["name"] == "terminal"

    def test_decision_types_enum(self):
        assert DecisionType.TOOL_CALL == "tool_call"
        assert DecisionType.LLM_CALL == "llm_call"
        assert DecisionType.TEXT == "text"
        assert DecisionType.WAIT == "wait"
        assert DecisionType.DELEGATE == "delegate"
        assert DecisionType.END == "end"


class TestHealthResponse:
    def test_ok_response(self):
        health = HealthResponse(
            status=HealthStatus.OK,
            version="1.0.0",
            capabilities=["tool_call"],
        )
        d = health.model_dump()
        assert d["status"] == "ok"
        assert d["version"] == "1.0.0"

    def test_degraded_response(self):
        health = HealthResponse(
            status=HealthStatus.DEGRADED,
            version="1.0.0",
            degraded_reason="Model backend unreachable",
            capabilities=["text", "end"],
        )
        d = health.model_dump()
        assert d["status"] == "degraded"
        assert d["degraded_reason"] == "Model backend unreachable"

    def test_down_response(self):
        health = HealthResponse(
            status=HealthStatus.DOWN,
            version="1.0.0",
            error="Database connection lost",
        )
        d = health.model_dump()
        assert d["status"] == "down"
        assert d["error"] == "Database connection lost"


class TestExecutionResult:
    def test_tool_result(self):
        result = ExecutionResult(
            type="tool_result",
            tool_name="terminal",
            data={"output": "ok"},
            success=True,
        )
        d = result.model_dump()
        assert d["type"] == "tool_result"
        assert d["success"] is True
        assert d["data"]["output"] == "ok"

    def test_error_result(self):
        result = ExecutionResult(
            type="error",
            data={"error": "something broke", "phase": "tool_exec"},
            success=False,
        )
        d = result.model_dump()
        assert d["type"] == "error"
        assert d["success"] is False


class TestProcessRequest:
    def test_full_roundtrip(self):
        req = ProcessRequest(
            session_id="s_test",
            message=Message(role="user", content="test"),
            identity=Identity(platform="telegram", chat_id="-100"),
            context=Context(history=[]),
        )
        req_json = json.loads(json.dumps(req.model_dump()))
        assert req_json["session_id"] == "s_test"
        assert req_json["message"]["role"] == "user"
        assert req_json["identity"]["platform"] == "telegram"


class TestCancelResponse:
    def test_cancel_response(self):
        resp = CancelResponse(cancelled=True, cancelled_decision_id="d_001")
        d = resp.model_dump()
        assert d["cancelled"] is True
        assert d["cancelled_decision_id"] == "d_001"


class TestEndReasons:
    def test_all_reasons(self):
        reasons = [
            EndReason.TASK_COMPLETE,
            EndReason.USER_REQUESTED,
            EndReason.ERROR,
            EndReason.TIMEOUT,
            EndReason.RATE_LIMITED,
            EndReason.CANCELLED,
        ]
        assert len(reasons) == 6
        assert all(isinstance(r, str) for r in reasons)
