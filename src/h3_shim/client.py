"""Async HTTP client for the H3 protocol (REST transport).

Talks to an H3 harness over HTTP/1.1 using ``httpx.AsyncClient``.
Mirrors the methods specified in S06 §3 of the Hermes Core integration spec.
"""

import logging
from uuid import uuid4

import httpx

from h3_shim.protocol import (
    CancelResponse,
    Context,
    Decision,
    DecisionType,
    End,
    EndReason,
    ExecutionResult,
    HealthResponse,
    Identity,
    Message,
    ProcessRequest,
    ResultRequest,
)

logger = logging.getLogger(__name__)


class H3Client:
    """Async REST client for an H3 harness.

    Endpoints consumed:
        GET  /v1/health
        POST /v1/process
        POST /v1/result
        POST /v1/cancel

    All response payloads are validated against the Pydantic models in
    ``h3_shim.protocol``; non-2xx responses raise ``httpx.HTTPStatusError``
    via ``resp.raise_for_status()``.

    When *hermes_token* is provided, every request carries an
    ``Authorization: Bearer h3_hx_<token>`` header per S12 §5.1.
    *hermes_identity* is sent as ``H3-Hermes-Identity`` and the
    protocol version as ``H3-Protocol-Version``.
    """

    def __init__(
        self,
        endpoint: str,
        transport: str = "rest",
        timeout_ms: int = 30000,
        hermes_token: str | None = None,
        hermes_identity: str | None = None,
        protocol_version: str = "1.0",
    ):
        self.endpoint = endpoint.rstrip("/")
        self.transport = transport
        self.timeout = timeout_ms / 1000
        self.hermes_token = hermes_token
        self.hermes_identity = hermes_identity
        self.protocol_version = protocol_version

        headers: dict[str, str] = {}
        if hermes_token:
            headers["Authorization"] = f"Bearer {hermes_token}"
        if hermes_identity:
            headers["H3-Hermes-Identity"] = hermes_identity
        if hermes_token or hermes_identity:
            headers["H3-Protocol-Version"] = protocol_version

        self._rest = httpx.AsyncClient(
            base_url=self.endpoint,
            timeout=self.timeout,
            headers=headers if headers else None,
        )

    async def health(self) -> HealthResponse:
        resp = await self._rest.get("/v1/health")
        resp.raise_for_status()
        return HealthResponse(**resp.json())

    async def process(
        self,
        session_id: str,
        message: Message,
        identity: Identity,
        context: Context,
    ) -> Decision:
        req = ProcessRequest(
            session_id=session_id,
            message=message,
            identity=identity,
            context=context,
        )
        try:
            resp = await self._rest.post("/v1/process", json=req.model_dump())
            resp.raise_for_status()
            return Decision(**resp.json())
        except httpx.TimeoutException:
            logger.warning(
                "H3Client: harness timed out after %.1fs for session %s",
                self.timeout,
                session_id,
            )
            return Decision(
                decision=DecisionType.END,
                decision_id=f"error-{uuid4().hex[:8]}",
                end=End(
                    reason=EndReason.TIMEOUT,
                    summary=f"Harness request timed out after {self.timeout:.0f}s",
                ),
            )

    async def result(
        self,
        session_id: str,
        decision_id: str,
        result: ExecutionResult,
    ) -> Decision:
        req = ResultRequest(
            session_id=session_id,
            decision_id=decision_id,
            result=result,
        )
        try:
            resp = await self._rest.post("/v1/result", json=req.model_dump())
            resp.raise_for_status()
            return Decision(**resp.json())
        except httpx.TimeoutException:
            logger.warning(
                "H3Client: harness timed out during result POST after %.1fs "
                "for session %s (decision %s)",
                self.timeout,
                session_id,
                decision_id,
            )
            return Decision(
                decision=DecisionType.END,
                decision_id=f"error-{uuid4().hex[:8]}",
                end=End(
                    reason=EndReason.TIMEOUT,
                    summary=(
                        f"Harness result request timed out after "
                        f"{self.timeout:.0f}s"
                    ),
                ),
            )

    async def cancel(
        self, session_id: str, reason: str = "user_interrupt"
    ) -> CancelResponse:
        resp = await self._rest.post(
            "/v1/cancel",
            json={"session_id": session_id, "reason": reason},
        )
        resp.raise_for_status()
        return CancelResponse(**resp.json())

    async def close(self):
        await self._rest.aclose()
