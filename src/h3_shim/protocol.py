"""Pydantic models for the H3 protocol (Hermes-side).

Mirrors the SDK types defined in the protocol specification (S02)
and Hermes Core integration spec (S06).

All models use Pydantic v2 with model_dump() for serialization.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ── Enums ───────────────────────────────────────────────────────────────────


class DecisionType(str, Enum):
    """Valid decision types a harness can return."""

    TOOL_CALL = "tool_call"
    LLM_CALL = "llm_call"
    TEXT = "text"
    WAIT = "wait"
    DELEGATE = "delegate"
    END = "end"


class EndReason(str, Enum):
    """Reasons a session can end."""

    TASK_COMPLETE = "task_complete"
    USER_REQUESTED = "user_requested"
    ERROR = "error"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    CANCELLED = "cancelled"


class HealthStatus(str, Enum):
    """Health check response statuses."""

    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


# ── Core Data Types ─────────────────────────────────────────────────────────


class Attachment(BaseModel):
    """A file, image, or other attachment to a message."""

    type: str  # "image", "file", "audio", etc.
    url: str
    mime_type: str = ""


class Message(BaseModel):
    """A chat message (user or assistant)."""

    role: str  # "user", "assistant", "system"
    content: str
    attachments: list[Attachment] = Field(default_factory=list)
    timestamp: datetime | None = None


class Identity(BaseModel):
    """Who sent the message and from where."""

    platform: str  # "telegram", "discord", "signal", etc.
    chat_id: str
    thread_id: str | None = None
    user_name: str | None = None
    user_id: str | None = None


class ToolDef(BaseModel):
    """A tool definition sent to the harness in context.tools."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ModelDef(BaseModel):
    """An available LLM model definition sent to the harness in context.models."""

    name: str
    provider: str
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    context_window: int = 0
    supports_vision: bool = False
    supports_tool_calling: bool = False


class Context(BaseModel):
    """Full session context sent to the harness with every /v1/process call."""

    history: list[Message] = Field(default_factory=list)
    tools: list[ToolDef] = Field(default_factory=list)
    models: list[ModelDef] = Field(default_factory=list)
    memory: str = ""
    skills: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    session_state: dict[str, Any] = Field(default_factory=dict)


# ── Request / Response Models ────────────────────────────────────────────────


class ProcessRequest(BaseModel):
    """POST /v1/process request body."""

    session_id: str
    message: Message
    identity: Identity
    context: Context


class HealthResponse(BaseModel):
    """GET /v1/health response."""

    status: HealthStatus = HealthStatus.OK
    version: str
    transport: str = "rest"
    protocol_version: str = "1.0"
    uptime_seconds: int | None = None
    active_sessions: int | None = None
    capabilities: list[str] = Field(default_factory=list)
    degraded_reason: str | None = None
    error: str | None = None

    @field_validator("capabilities", mode="before")
    @classmethod
    def _ensure_capabilities(cls, v: Any) -> list[str]:
        if v is None:
            return []
        return v


class CancelResponse(BaseModel):
    """POST /v1/cancel response (200)."""

    cancelled: bool
    cancelled_decision_id: str | None = None


# ── Decision Sub-Models ─────────────────────────────────────────────────────


class ToolCall(BaseModel):
    """A 'tool_call' decision — harness asks Hermes to execute a tool."""

    name: str
    params: dict[str, Any] = Field(default_factory=dict)
    reasoning: str | None = None


class LLMCall(BaseModel):
    """An 'llm_call' decision — harness asks Hermes to run an LLM prompt."""

    model: str
    messages: list[Message] = Field(default_factory=list)
    system_prompt: str | None = None
    temperature: float | None = 0.7
    max_tokens: int | None = None


class TextResponse(BaseModel):
    """A 'text' decision — harness asks Hermes to send text to the user."""

    content: str
    finished: bool = False


class Wait(BaseModel):
    """A 'wait' decision — harness asks Hermes to pause for external signal."""

    reason: str
    duration_seconds: int | None = None
    poll_endpoint: str | None = None


class Delegate(BaseModel):
    """A 'delegate' decision — harness asks Hermes to spawn a sub-agent."""

    task: str
    context: str | None = None
    model: str | None = None
    provider: str | None = None


class End(BaseModel):
    """An 'end' decision — harness terminates the session."""

    reason: EndReason
    summary: str | None = None


class Decision(BaseModel):
    """A single decision returned by the harness.

    Exactly one sub-field (tool_call, llm_call, text, wait, delegate, end)
    will be set depending on the 'decision' type.
    """

    decision: DecisionType
    decision_id: str
    tool_call: ToolCall | None = None
    llm_call: LLMCall | None = None
    text: TextResponse | None = None
    wait: Wait | None = None
    delegate: Delegate | None = None
    end: End | None = None


class ExecutionResult(BaseModel):
    """The result of executing a harness decision, sent back via POST /v1/result."""

    # tool_result | llm_response | text_sent | delegate_result | wait_timeout | error
    type: str
    tool_name: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0
    success: bool = True


class ResultRequest(BaseModel):
    """POST /v1/result request body."""

    session_id: str
    decision_id: str
    result: ExecutionResult


# ── Error Model ──────────────────────────────────────────────────────────────


class ErrorDetail(BaseModel):
    """Additional details for an error response."""

    valid_types: list[str] | None = None
    received: str | None = None


class H3Error(BaseModel):
    """Standard H3 error response body."""

    code: str
    message: str
    details: ErrorDetail | None = None


class H3ErrorResponse(BaseModel):
    """Wrapper for error responses per §9 of the protocol spec."""

    error: H3Error


# ── Session Info ─────────────────────────────────────────────────────────────


class SessionInfo(BaseModel):
    """GET /v1/sessions/:id response."""

    session_id: str
    started_at: datetime
    last_active: datetime
    turn_count: int = 0
    status: str = "active"  # active, completed, expired
    current_decision: str | None = None
    current_decision_type: str | None = None


class SessionTerminated(BaseModel):
    """DELETE /v1/sessions/:id response."""

    terminated: bool
    session_id: str
