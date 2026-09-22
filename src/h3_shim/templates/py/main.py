"""Generated H3 echo harness (Python / FastAPI).

This file was scaffolded by ``hermes-h3 scaffold --lang py`` from
``get-h3/shim/src/h3_shim/templates/py/main.py``. It implements a minimal
but H3-compliant harness: every user message is echoed back as text,
session state is tracked per ``session_id``, and the loop ends after two
result callbacks. Sessions are counted by ``/v1/health`` only while the
harness still owes them work, and are reclaimed by an idle TTL + hard
cap, so a long-lived harness does not accumulate one entry per
conversation (DF5-H3-SHIM-2).

Run with::

    # PEP 668 distros (Ubuntu 24+, Debian 12+) refuse bare pip installs —
    # always use a venv:
    python3 -m venv .venv
    source .venv/bin/activate
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
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import Enum
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
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


class SessionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


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
        self.last_active = self.created_at
        self.result_count = 0
        self.turn_count = 0
        self.streaming_mode = False
        self.status = SessionStatus.ACTIVE.value
        # DF5-H3-SHIM-2: a session is LIVE only while the harness still owes
        # it work — from the /v1/process that opened the turn until that turn
        # is answered by /v1/result, is cancelled, or ends. It is the field
        # ``health().active_sessions`` counts.
        self.live = True


class EchoHarness:
    """Echoes every user message back as text.

    Mirrors the Go echo example from get-h3/sdk-go/examples/echo/main.go:
    messages containing ``"do not finish"`` enable streaming mode, and the
    session ends after two result callbacks in normal mode.

    Session liveness + GC (DF5-H3-SHIM-2). A session is *live* only while
    the harness still owes it work: it stops being live the moment a
    non-streaming turn is answered by ``on_result``, is cancelled, or ends.
    ``health().active_sessions`` counts live sessions only, so the metric
    cannot grow with every finished conversation — the leak that survived
    DF2/DF4, where the purge fired only on a *second* result and every
    one-shot, error-path, cancel and streaming session stayed counted for
    the life of the process (a live dogfood harness accumulated 1440+
    sessions; the repo's own battery added ~96 per run).

    The entry itself is retained for a bounded closing window — so the
    loop's closing ``/v1/result`` can still be answered with the END
    decision and ``GET /v1/sessions/{id}`` keeps reporting a truthful
    ``completed`` status — and is then dropped by
    :meth:`sweep_idle_sessions`: an idle TTL (``H3_SESSION_TTL_S``, default
    30 s) plus a hard ``H3_SESSION_MAX`` cap, both applied on every request
    path. The END decision still drops the entry outright.
    """

    VERSION = "1.0.0"
    PROTOCOL_VERSION = "1.0"

    #: Idle seconds after which any session entry — live or retained — is
    #: dropped. ``H3_SESSION_TTL_S``; 0 or less disables the TTL sweep.
    DEFAULT_SESSION_TTL_S = 30.0
    #: Hard cap on tracked entries; the least-recently-active are evicted
    #: beyond it (``H3_SESSION_MAX``). Backstop so the table stays bounded
    #: even with the TTL disabled.
    DEFAULT_MAX_SESSIONS = 1024

    def __init__(
        self,
        *,
        session_ttl_s: float | None = None,
        max_sessions: int | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionState] = {}
        self._started_at = datetime.utcnow()
        self._session_ttl_s = (
            float(os.environ.get("H3_SESSION_TTL_S", self.DEFAULT_SESSION_TTL_S))
            if session_ttl_s is None
            else float(session_ttl_s)
        )
        self._max_sessions = (
            int(os.environ.get("H3_SESSION_MAX", self.DEFAULT_MAX_SESSIONS))
            if max_sessions is None
            else int(max_sessions)
        )

    def _state(self, session_id: str) -> SessionState:
        with self._lock:
            st = self._sessions.get(session_id)
            if st is None:
                st = SessionState()
                self._sessions[session_id] = st
            st.last_active = datetime.utcnow()
            return st

    def sweep_idle_sessions(self, *, now: datetime | None = None) -> int:
        """Drop sessions that outlived the idle TTL / max-entry cap.

        Returns how many entries were dropped. Called on every request path
        (health, process, result, cancel, session read), so a long-lived
        harness bounds its session table instead of accumulating one entry
        per conversation forever — the sessions that never receive a second
        result (one-shot ``finished=true`` turns, error paths, cancelled and
        streaming sessions) can never reach the END purge, and are reclaimed
        here once they go quiet (DF5-H3-SHIM-2).
        """
        with self._lock:
            return self._sweep_locked(now or datetime.utcnow())

    def _sweep_locked(self, now: datetime) -> int:
        dropped = 0
        if self._session_ttl_s > 0:
            for session_id, st in list(self._sessions.items()):
                if (now - st.last_active).total_seconds() > self._session_ttl_s:
                    del self._sessions[session_id]
                    dropped += 1
        overflow = len(self._sessions) - self._max_sessions
        if overflow > 0:
            oldest = sorted(
                self._sessions.items(), key=lambda item: item[1].last_active
            )
            for session_id, _ in oldest[:overflow]:
                del self._sessions[session_id]
                dropped += 1
        return dropped

    def health(self) -> HealthResponse:
        self.sweep_idle_sessions()
        with self._lock:
            active = sum(1 for st in self._sessions.values() if st.live)
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
        self.sweep_idle_sessions()
        st = self._state(req.session_id)
        with self._lock:
            # A new user turn re-activates a session that had gone quiet
            # (DF5-H3-SHIM-2).
            st.streaming_mode = "do not finish" in req.message.content
            st.turn_count += 1
            st.live = True
            st.status = SessionStatus.ACTIVE.value
            st.last_active = datetime.utcnow()

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
        self.sweep_idle_sessions()
        st = self._state(req.session_id)
        with self._lock:
            st.result_count += 1
            result_count = st.result_count
            streaming = st.streaming_mode
            st.last_active = datetime.utcnow()

        if not streaming and result_count >= 2:
            # DF2-H3-SHIM-3: the loop is over, so drop the session entry here.
            # ``_state()`` auto-creates entries and only an explicit DELETE
            # used to remove them, so a long-lived harness accumulated every
            # finished session (97 sessions / 26h observed on a live dogfood
            # instance) and ``health().active_sessions`` grew monotonically
            # instead of measuring health. The decision returned below is
            # unchanged. A naturally ended session is therefore gone:
            # ``GET /v1/sessions/{id}`` answers 404 "Session not found" — the
            # documented answer for a harness that keeps no state for it (the
            # compliance battery accepts 404 for an ended session). An explicit
            # ``DELETE /v1/sessions/{id}`` still tears down an in-flight
            # session and stays idempotent.
            self.on_session_terminate(req.session_id)
            return Decision(
                decision=DecisionType.END,
                decision_id="echo-end",
                end=EndDecision(
                    reason=EndReason.TASK_COMPLETE,
                    summary="Echo conversation complete",
                ),
            )

        if not streaming:
            # DF5-H3-SHIM-2: the harness has answered the only result of a
            # non-streaming turn with its final text, so nothing is
            # outstanding any more — the session stops being *live* and
            # ``health().active_sessions`` immediately returns to its
            # baseline. DF2/DF4 purged only on a *second* result, so every
            # one-shot, error-path, cancel and streaming session stayed
            # counted forever. The entry itself is retained for a bounded
            # closing window so the loop's closing ``/v1/result`` is still
            # answered with the END decision above and
            # ``GET /v1/sessions/{id}`` reports a truthful ``completed``
            # status; :meth:`sweep_idle_sessions` forgets it once it goes
            # quiet.
            with self._lock:
                st.live = False
                st.status = SessionStatus.COMPLETED.value

        return Decision(
            decision=DecisionType.TEXT,
            decision_id="echo-result",
            text=TextResponse(
                content=f"Result received: {req.decision_id}",
                finished=not streaming,
            ),
        )

    def on_cancel(self, req: CancelRequest) -> CancelResponse:
        # Battery (test_5_9b cancel_unknown_session): cancelling a
        # nonexistent session must 404 when the harness tracks sessions.
        # Check the dict directly — _state() would auto-create the session.
        self.sweep_idle_sessions()
        with self._lock:
            st = self._sessions.get(req.session_id)
            if st is None:
                raise HTTPException(status_code=404, detail="Session not found")
            # DF5-H3-SHIM-2: an interrupted conversation owes no more work,
            # so it stops being live at once; the entry is retained for the
            # closing callbacks and reclaimed by the idle sweep.
            st.live = False
            st.status = SessionStatus.CANCELLED.value
            st.last_active = datetime.utcnow()
        return CancelResponse(cancelled=True, cancelled_decision_id=None)

    def on_session_terminate(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def get_session(self, session_id: str) -> SessionResponse:
        # Protocol GET /v1/sessions/{session_id}: metadata for an active or
        # completed session. Check the dict directly — _state() would
        # auto-create the session (mirrors on_cancel's 404 handling).
        # DF5-H3-SHIM-2: a session whose turn is over is retained for the
        # bounded closing window and reports ``completed``; once the sweep
        # forgets it (idle TTL / max cap) or the loop returns END, this 404s,
        # which is the answer the compliance battery documents for a harness
        # that keeps no state for an ended session.
        self.sweep_idle_sessions()
        with self._lock:
            st = self._sessions.get(session_id)
            if st is None:
                raise HTTPException(status_code=404, detail="Session not found")
            return SessionResponse(
                session_id=session_id,
                started_at=st.created_at.isoformat(),
                last_active=st.last_active.isoformat(),
                turn_count=st.turn_count,
                status=st.status,
            )


# ── FastAPI Wiring ───────────────────────────────────────────────────────────

app = FastAPI(title="h3-harness (py)", version=EchoHarness.VERSION)
harness = EchoHarness()


# ── Error Envelope ───────────────────────────────────────────────────────────
# The H3 protocol (protocol/h3-protocol.yaml, ``x-h3-errors``) requires a
# rejected request to answer with the standard error-response envelope:
#
#     {"error": {"code": "INVALID_REQUEST",
#                "message": "<human-readable summary>",
#                "details": {"errors": [ ...pydantic errors... ]}}}
#
# FastAPI's default answer for a malformed or schema-invalid body is HTTP 422
# with ``{"detail": [...]}``, which is NOT the H3 contract — the compliance
# battery accepts any 4xx, so the drift is invisible to it. The handler below
# replaces that default on the request-validation path only. It is inlined on
# purpose: the generated project must not import ``h3_shim`` at runtime.


def _summarize_validation_errors(errors: Sequence[Mapping[str, Any]]) -> str:
    """One short human-readable line describing a list of pydantic errors."""
    if not errors:
        return "Invalid request body"
    first = errors[0]
    where = ".".join(str(part) for part in first.get("loc", ())) or "body"
    msg = str(first.get("msg", "invalid value"))
    if len(errors) == 1:
        return f"Invalid request body: {where}: {msg}"
    return (
        f"Invalid request body: {len(errors)} validation errors, "
        f"first at {where}: {msg}"
    )


@app.exception_handler(RequestValidationError)
async def invalid_request_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Answer malformed / schema-invalid bodies with the H3 error envelope.

    FastAPI raises ``RequestValidationError`` both for a body that does not
    parse as JSON (a ``json_invalid`` entry) and for one that parses but fails
    the pydantic model, so this single handler covers every case the protocol
    names under ``INVALID_REQUEST`` ("Malformed JSON or missing required
    fields"). The deliberate 4xx paths elsewhere in this harness (404 "Session
    not found" from ``on_cancel`` / ``get_session``) raise ``HTTPException``,
    which is untouched by this handler and keeps its existing response.
    """
    errors = exc.errors()
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "code": "INVALID_REQUEST",
                "message": _summarize_validation_errors(errors),
                "details": {"errors": jsonable_encoder(errors)},
            }
        },
    )


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


class SessionResponse(BaseModel):
    session_id: str
    started_at: str
    last_active: str
    turn_count: int = 0
    status: str = SessionStatus.ACTIVE.value
    current_decision: str | None = None
    current_decision_type: str | None = None


@app.post("/v1/cancel", response_model=CancelResponse)
def cancel(req: CancelRequest) -> CancelResponse:
    return harness.on_cancel(req)


@app.delete("/v1/sessions/{session_id}", response_model=SessionTerminated)
def delete_session(session_id: str) -> SessionTerminated:
    harness.on_session_terminate(session_id)
    return SessionTerminated(terminated=True, session_id=session_id)


@app.get("/v1/sessions/{session_id}", response_model=SessionResponse)
def get_session(session_id: str) -> SessionResponse:
    return harness.get_session(session_id)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "9191"))
    uvicorn.run(app, host="0.0.0.0", port=port)
