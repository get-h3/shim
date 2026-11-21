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
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import Enum
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
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

    def active_sessions(self) -> int:
        """Live sessions — the count ``health()`` reports as active_sessions."""
        self.sweep_idle_sessions()
        with self._lock:
            return sum(1 for st in self._sessions.values() if st.live)

    def health(self) -> HealthResponse:
        active = self.active_sessions()
        uptime = int((datetime.utcnow() - self._started_at).total_seconds())
        return HealthResponse(
            status=HealthStatus.OK,
            version=self.VERSION,
            transport="rest",
            protocol_version=self.PROTOCOL_VERSION,
            uptime_seconds=uptime,
            active_sessions=active,
            # Capabilities must match what this harness actually emits:
            # declaring fewer capabilities than you emit lies to
            # capability-routing consumers. This harness emits text
            # (on_process, and the non-ending on_result branch) and end
            # (on_result once the second result arrives).
            capabilities=[DecisionType.TEXT.value, DecisionType.END.value],
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


# ── Metrics (H3 harness SDK contract, obs-spec §5.4) ────────────────────────
# Every scaffolded harness serves the protocol's four harness metric families on
# GET /metrics, on the same listener as the H3 routes, in Prometheus text
# exposition format:
#
#   h3_harness_requests_total            counter   endpoint, decision, lang
#   h3_harness_request_duration_seconds  histogram endpoint, lang
#   h3_harness_errors_total              counter   endpoint, status_class
#   h3_harness_sessions_active           gauge     lang
#
# The registry is hand-rolled (two counters, one gauge, one fixed-bucket
# histogram) so the generated project stays dependency-light: four families do
# not justify a Prometheus client library in every scaffolded harness.
#
# Labels stay BOUNDED, which is what keeps the series count cheap to scrape:
# ``lang`` is fixed by this template, ``endpoint`` is always a route TEMPLATE
# ("process", "result", "health", "cancel", "sessions") and never a session id or
# a raw path, and ``decision`` is the H3 decision type the turn produced, "none"
# for an endpoint that answers no decision, or "error" for a 4xx/5xx.

METRICS_LANG = "py"

# The Prometheus default bucket grid: resolves a decision turn's p50/p95 from
# 5 ms up.
_DURATION_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)
# The bounded ``endpoint`` label set: one entry per H3 route template served.
_ENDPOINT_TEMPLATES: tuple[str, ...] = (
    "cancel",
    "health",
    "process",
    "result",
    "sessions",
)
# Route templates that answer without an H3 decision; their request series is
# registered at zero under decision="none".
_DECISIONLESS_ENDPOINTS: tuple[str, ...] = ("cancel", "health", "sessions")
# The bounded ``status_class`` label set for errors_total.
_STATUS_CLASSES: tuple[str, ...] = ("4xx", "5xx")
# The bounded ``decision`` label set: the protocol's decision types, so a
# malformed response body can never widen the label.
_DECISION_LABELS = frozenset(
    {"text", "end", "tool_call", "llm_call", "wait", "delegate"}
)


def _label_value(value: str) -> str:
    """Escape a label value for the text exposition format."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _status_class(status: int) -> str | None:
    """Map an HTTP status onto the errors_total label, or None when it is not one."""
    if status >= 500:
        return "5xx"
    if status >= 400:
        return "4xx"
    return None


def _endpoint_for_path(path: str) -> str | None:
    """Map a request path onto its route TEMPLATE.

    Path parameters collapse ("/v1/sessions/<id>" -> "sessions") so a session id
    can never become a label; paths outside the documented H3 surface return
    None and are not recorded.
    """
    if path == "/v1/process":
        return "process"
    if path == "/v1/result":
        return "result"
    if path == "/v1/health":
        return "health"
    if path == "/v1/cancel":
        return "cancel"
    if path == "/v1/sessions" or path.startswith("/v1/sessions/"):
        return "sessions"
    return None


class _LatencyHistogram:
    """Fixed-bucket histogram; ``counts[i]`` covers (bucket i-1, bucket i]."""

    __slots__ = ("counts", "sum", "observations")

    def __init__(self) -> None:
        self.counts = [0] * (len(_DURATION_BUCKETS) + 1)
        self.sum = 0.0
        self.observations = 0

    def observe(self, seconds: float) -> None:
        self.sum += seconds
        self.observations += 1
        for index, upper in enumerate(_DURATION_BUCKETS):
            if seconds <= upper:
                self.counts[index] += 1
                return
        self.counts[len(_DURATION_BUCKETS)] += 1


class MetricsRegistry:
    """Hand-rolled Prometheus registry backing GET /metrics.

    Thread-safe (uvicorn serves these routes from a worker pool) and bounded: the
    label sets are fixed constants, so the series count cannot grow with traffic.
    """

    def __init__(self, lang: str) -> None:
        self._lang = lang
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str], int] = {}
        self._errors: dict[tuple[str, str], int] = {}
        self._durations = {ep: _LatencyHistogram() for ep in _ENDPOINT_TEMPLATES}
        self._sessions_active = 0
        # Register the ZERO STATE of every route template this harness serves, so
        # a scrape arriving before any traffic still shows all four families (and
        # rate() over a freshly started harness starts at 0 instead of "no
        # data"). Nothing is fabricated: the seeded series are exactly the routes
        # this harness answers, every value starts at 0, and values only ever
        # move by observation.
        for endpoint in _ENDPOINT_TEMPLATES:
            for status_class in _STATUS_CLASSES:
                self._errors[(endpoint, status_class)] = 0
        for endpoint in _DECISIONLESS_ENDPOINTS:
            self._requests[(endpoint, "none")] = 0

    def observe_request(
        self, endpoint: str, decision: str, status: int, seconds: float
    ) -> None:
        """Record one finished request: counter, latency histogram, error counter."""
        with self._lock:
            request_key = (endpoint, decision)
            self._requests[request_key] = self._requests.get(request_key, 0) + 1
            histogram = self._durations.get(endpoint)
            if histogram is not None:
                histogram.observe(seconds)
            status_class = _status_class(status)
            if status_class is not None:
                error_key = (endpoint, status_class)
                self._errors[error_key] = self._errors.get(error_key, 0) + 1

    def set_sessions_active(self, count: int) -> None:
        """Publish the harness's own live-session count.

        The caller passes the SAME count /v1/health reports
        (``EchoHarness.active_sessions``), so the gauge measures the session set
        the harness keeps — it is never a recount from thin air.
        """
        with self._lock:
            self._sessions_active = count

    def render(self) -> str:
        """Render the whole registry in Prometheus text exposition format."""
        with self._lock:
            lang = _label_value(self._lang)
            lines = [
                "# HELP h3_harness_requests_total Requests handled by this"
                " harness, by endpoint and decision.",
                "# TYPE h3_harness_requests_total counter",
            ]
            for (endpoint, decision), value in sorted(self._requests.items()):
                lines.append(
                    "h3_harness_requests_total{"
                    f'endpoint="{_label_value(endpoint)}",'
                    f'decision="{_label_value(decision)}",'
                    f'lang="{lang}"}} {value}'
                )

            lines.append(
                "# HELP h3_harness_request_duration_seconds Request latency in"
                " seconds, by endpoint."
            )
            lines.append("# TYPE h3_harness_request_duration_seconds histogram")
            for endpoint in _ENDPOINT_TEMPLATES:
                histogram = self._durations[endpoint]
                label = f'endpoint="{_label_value(endpoint)}",lang="{lang}"'
                cumulative = 0
                for index, upper in enumerate(_DURATION_BUCKETS):
                    cumulative += histogram.counts[index]
                    lines.append(
                        "h3_harness_request_duration_seconds_bucket{"
                        f'{label},le="{upper:g}"}} {cumulative}'
                    )
                cumulative += histogram.counts[len(_DURATION_BUCKETS)]
                lines.append(
                    "h3_harness_request_duration_seconds_bucket{"
                    f'{label},le="+Inf"}} {cumulative}'
                )
                lines.append(
                    "h3_harness_request_duration_seconds_sum{"
                    f"{label}}} {histogram.sum:g}"
                )
                lines.append(
                    "h3_harness_request_duration_seconds_count{"
                    f"{label}}} {histogram.observations}"
                )

            lines.append(
                "# HELP h3_harness_errors_total Requests answered with a 4xx or"
                " 5xx, by endpoint."
            )
            lines.append("# TYPE h3_harness_errors_total counter")
            for (endpoint, status_class), value in sorted(self._errors.items()):
                lines.append(
                    "h3_harness_errors_total{"
                    f'endpoint="{_label_value(endpoint)}",'
                    f'status_class="{_label_value(status_class)}"}} {value}'
                )

            lines.append(
                "# HELP h3_harness_sessions_active Sessions this harness still"
                " owes work, by lang."
            )
            lines.append("# TYPE h3_harness_sessions_active gauge")
            lines.append(
                f'h3_harness_sessions_active{{lang="{lang}"}} {self._sessions_active}'
            )
            return "\n".join(lines) + "\n"


METRICS = MetricsRegistry(METRICS_LANG)


def _record_request(
    endpoint: str,
    started: float,
    *,
    decision: str = "none",
    status: int = 200,
) -> None:
    """Record one finished request against the harness metrics registry."""
    METRICS.observe_request(
        endpoint=endpoint,
        decision=decision,
        status=status,
        seconds=time.perf_counter() - started,
    )


def _decision_label(decision: Decision) -> str:
    """The bounded decision label for a decision this harness produced."""
    value = decision.decision.value
    return value if value in _DECISION_LABELS else "none"


def _request_started(request: Request) -> float:
    """The request's stamped start time, for paths that never reach a route body."""
    started = getattr(request.state, "metrics_started", None)
    return started if isinstance(started, float) else time.perf_counter()


class _MetricsStartStamp:
    """Pure-ASGI middleware stamping the request start time.

    Pure ASGI (not ``BaseHTTPMiddleware``) on purpose: it never wraps the
    response, so the H3 wire contract — status, body and headers — is identical
    with and without it. The stamp gives the 4xx paths that never reach a route
    body (the validation handler) a real latency observation instead of a
    made-up zero.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            scope.setdefault("state", {})["metrics_started"] = time.perf_counter()
        await self.app(scope, receive, send)


# ── FastAPI Wiring ───────────────────────────────────────────────────────────

app = FastAPI(title="h3-harness (py)", version=EchoHarness.VERSION)
app.add_middleware(_MetricsStartStamp)
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
    # A rejected body is a real 4xx on a real H3 route: count it (and its
    # latency) against the route TEMPLATE the request targeted.
    endpoint = _endpoint_for_path(request.url.path)
    if endpoint is not None:
        _record_request(
            endpoint,
            _request_started(request),
            decision="error",
            status=400,
        )
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
    started = time.perf_counter()
    try:
        response = harness.health()
    except Exception:
        _record_request("health", started, decision="error", status=500)
        raise
    _record_request("health", started)
    return response


@app.post("/v1/process", response_model=Decision)
def process(req: ProcessRequest) -> Decision:
    started = time.perf_counter()
    try:
        decision = harness.on_process(req)
    except Exception as exc:  # pragma: no cover - defensive
        _record_request("process", started, decision="error", status=500)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    _record_request("process", started, decision=_decision_label(decision))
    return decision


@app.post("/v1/result", response_model=Decision)
def result(req: ResultRequest) -> Decision:
    started = time.perf_counter()
    try:
        decision = harness.on_result(req)
    except Exception as exc:  # pragma: no cover - defensive
        _record_request("result", started, decision="error", status=500)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    _record_request("result", started, decision=_decision_label(decision))
    return decision


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
    started = time.perf_counter()
    try:
        response = harness.on_cancel(req)
    except HTTPException as exc:
        _record_request("cancel", started, decision="error", status=exc.status_code)
        raise
    _record_request("cancel", started)
    return response


@app.delete("/v1/sessions/{session_id}", response_model=SessionTerminated)
def delete_session(session_id: str) -> SessionTerminated:
    started = time.perf_counter()
    harness.on_session_terminate(session_id)
    # DF-H3-53: the route must not fabricate the teardown claim. Re-ask the
    # harness's own session view after the hook: a session that is gone was
    # really torn down (terminated=True; the later GET 404s); a session that
    # is still present means the hook did nothing, so the route answers
    # terminated=False honestly — test_5_14 accepts that honest no-teardown
    # claim and fails any harness that stamps a teardown that never
    # happened. (The SDK router was fixed the same way; live repro:
    # docs/dogfood/2026-11-18-dfh353-delete-repro.md.)
    try:
        harness.get_session(session_id)
        terminated = False
    except HTTPException:
        terminated = True
    _record_request("sessions", started)
    return SessionTerminated(terminated=terminated, session_id=session_id)


@app.get("/v1/sessions/{session_id}", response_model=SessionResponse)
def get_session(session_id: str) -> SessionResponse:
    started = time.perf_counter()
    try:
        response = harness.get_session(session_id)
    except HTTPException as exc:
        _record_request("sessions", started, decision="error", status=exc.status_code)
        raise
    _record_request("sessions", started)
    return response


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    """Serve the h3_harness_* families in Prometheus text exposition format."""
    # The gauge is SET from the harness's own live-session count at scrape time —
    # the same set /v1/health reports.
    METRICS.set_sessions_active(harness.active_sessions())
    return Response(
        content=METRICS.render(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "9191"))
    uvicorn.run(app, host="0.0.0.0", port=port)
