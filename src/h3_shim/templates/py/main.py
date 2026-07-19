"""Generated H3 echo harness (Python / FastAPI).

This file was scaffolded by ``hermes-h3 scaffold --lang py`` from
``get-h3/shim/src/h3_shim/templates/py/main.py``. It implements a minimal
but H3-compliant harness: every user message is echoed back as text,
session state is tracked per ``session_id``, and the loop ends after two
result callbacks.

Run with::

    pip install -e .
    python main.py

The harness listens on http://localhost:9191 by default. Verify with::

    h3-test --endpoint http://localhost:9191

To customise:

1. Replace ``on_process`` / ``on_result`` with your own logic.
2. Re-run ``python main.py`` — that's it.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from enum import Enum
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# ── Protocol Models ──────────────────────────────────────────────────────────
# Inlined from the H3 protocol spec so the generated project has no runtime
# dependency on the shim itself. Mirror get-h3/shim/src/h3_shim/protocol.py.


class DecisionType(str, Enum):
    TOOL_CALL = "tool_call"
    LLM_CALL = "llm_call"
    TEXT = "text"
    WAIT = "wait"
    DELEGATE = "delegate"
    END = "end"


class EndReason(str, Enum):
    TASK_COMPLETE = "task_complete"
    USER_REQUESTED = "user_requested"
    ERROR = "error"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    CANCELLED = "cancelled"


class HealthStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


class Attachment(BaseModel):
    type: str
    url: str
    mime_type: str = ""


class Message(BaseModel):
    role: str
    content: str
    attachments: list[Attachment] = Field(default_factory=list)
    timestamp: datetime | None = None


class Identity(BaseModel):
    platform: str
    chat_id: str
    thread_id: str | None = None
    user_name: str | None = None
    user_id: str | None = None


class ToolDef(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ModelDef(BaseModel):
    name: str
    provider: str
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    context_window: int = 0
    supports_vision: bool = False
    supports_tool_calling: bool = False


class Context(BaseModel):
    history: list[Message] = Field(default_factory=list)
    tools: list[ToolDef] = Field(default_factory=list)
    models: list[ModelDef] = Field(default_factory=list)
    memory: str = ""
    skills: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    session_state: dict[str, Any] = Field(default_factory=dict)


class ProcessRequest(BaseModel):
    session_id: str
    message: Message
    identity: Identity
    context: Context


class CancelRequest(BaseModel):
    session_id: str
    reason: str | None = None


class HealthResponse(BaseModel):
    status: HealthStatus = HealthStatus.OK
    version: str
    transport: str = "rest"
    protocol_version: str = "1.0"
    uptime_seconds: int | None = None
    active_sessions: int | None = None
    capabilities: list[str] = Field(default_factory=list)


class CancelResponse(BaseModel):
    cancelled: bool
    cancelled_decision_id: str | None = None


class TextResponse(BaseModel):
    content: str
    finished: bool = False


class EndDecision(BaseModel):
    reason: EndReason
    summary: str | None = None


class HistoryEntry(BaseModel):
    role: str
    content: str


class Decision(BaseModel):
    decision: DecisionType
    decision_id: str
    history: list[HistoryEntry] | None = None
    text: TextResponse | None = None
    end: EndDecision | None = None


class ExecutionResult(BaseModel):
    type: str
    tool_name: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0
    success: bool = True


class ResultRequest(BaseModel):
    session_id: str
    decision_id: str
    result: ExecutionResult


# ── Harness State ────────────────────────────────────────────────────────────


class SessionState:
    """Per-session bookkeeping for the echo harness."""

    def __init__(self) -> None:
        self.created_at = datetime.utcnow()
        self.result_count = 0
        self.streaming_mode = False


class EchoHarness:
    """Echoes every user message back as text.

    Mirrors the Go echo example from get-h3/sdk-go/examples/echo/main.go:
    messages containing ``"do not finish"`` enable streaming mode, and the
    session ends after two result callbacks in normal mode.
    """

    VERSION = "1.0.0"
    PROTOCOL_VERSION = "1.0"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionState] = {}
        self._started_at = datetime.utcnow()

    def _state(self, session_id: str) -> SessionState:
        with self._lock:
            st = self._sessions.get(session_id)
            if st is None:
                st = SessionState()
                self._sessions[session_id] = st
            return st

    def health(self) -> HealthResponse:
        with self._lock:
            active = len(self._sessions)
        uptime = int((datetime.utcnow() - self._started_at).total_seconds())
        return HealthResponse(
            status=HealthStatus.OK,
            version=self.VERSION,
            transport="rest",
            protocol_version=self.PROTOCOL_VERSION,
            uptime_seconds=uptime,
            active_sessions=active,
            capabilities=[DecisionType.TEXT.value],
        )

    def on_process(self, req: ProcessRequest) -> Decision:
        st = self._state(req.session_id)
        st.streaming_mode = "do not finish" in req.message.content

        content = f"Echo: {req.message.content}"
        history = [
            HistoryEntry(role=msg.role, content=msg.content)
            for msg in req.context.history
        ]
        return Decision(
            decision=DecisionType.TEXT,
            decision_id="echo-process",
            text=TextResponse(content=content, finished=not st.streaming_mode),
            history=history,
        )

    def on_result(self, req: ResultRequest) -> Decision:
        st = self._state(req.session_id)
        st.result_count += 1

        if not st.streaming_mode and st.result_count >= 2:
            return Decision(
                decision=DecisionType.END,
                decision_id="echo-end",
                end=EndDecision(
                    reason=EndReason.TASK_COMPLETE,
                    summary="Echo conversation complete",
                ),
            )

        return Decision(
            decision=DecisionType.TEXT,
            decision_id="echo-result",
            text=TextResponse(
                content=f"Result received: {req.decision_id}",
                finished=not st.streaming_mode,
            ),
        )

    def on_cancel(self, req: CancelRequest) -> CancelResponse:
        return CancelResponse(cancelled=True, cancelled_decision_id=None)

    def on_session_terminate(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


# ── FastAPI Wiring ───────────────────────────────────────────────────────────

app = FastAPI(title="h3-harness (py)", version=EchoHarness.VERSION)
harness = EchoHarness()


@app.get("/v1/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return harness.health()


@app.post("/v1/process", response_model=Decision)
def process(req: ProcessRequest) -> Decision:
    try:
        return harness.on_process(req)
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/v1/result", response_model=Decision)
def result(req: ResultRequest) -> Decision:
    try:
        return harness.on_result(req)
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class SessionTerminated(BaseModel):
    terminated: bool
    session_id: str


@app.post("/v1/cancel", response_model=CancelResponse)
def cancel(req: CancelRequest) -> CancelResponse:
    return harness.on_cancel(req)


@app.delete("/v1/sessions/{session_id}", response_model=SessionTerminated)
def delete_session(session_id: str) -> SessionTerminated:
    harness.on_session_terminate(session_id)
    return SessionTerminated(terminated=True, session_id=session_id)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "9191"))
    uvicorn.run(app, host="0.0.0.0", port=port)
