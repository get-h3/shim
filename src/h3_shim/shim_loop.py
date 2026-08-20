"""H3ShimLoop — main process / execute / result loop for H3 harnesses.

The :class:`H3ShimLoop` is the runtime that drives a single H3 session:

    1. POST the user :class:`Message` to the harness (``/v1/process``).
    2. Inspect the returned :class:`Decision`.
    3. Execute the decision locally (run a tool, send LLM call, emit text,
       wait, delegate, …).
    4. POST the :class:`ExecutionResult` back (``/v1/result``).
    5. Repeat until the harness returns an ``END`` decision.

The loop also enforces a hard iteration cap so a misbehaving harness
can't spin a session forever, and propagates ``asyncio.CancelledError``
by asking the harness to cancel its session before yielding control
back to the caller.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from h3_shim.client import H3Client
from h3_shim.protocol import (
    Context,
    Decision,
    DecisionType,
    Delegate,
    ExecutionResult,
    Identity,
    LLMCall,
    Message,
    TextResponse,
    ToolCall,
    Wait,
)

logger = logging.getLogger(__name__)


class H3ShimLoop:
    """Drive a single H3 session through a process / result loop.

    Parameters
    ----------
    client:
        The :class:`H3Client` that talks to the harness.
    session_id:
        Stable identifier the harness will associate with this session.
    context:
        Per-session :class:`Context` (history, tools, models, memory, …).
    max_iterations:
        Hard cap on the number of ``/v1/result`` round-trips per ``run``.
        Defaults to ``50`` to mirror the canonical Hermes agent loop.
    llm_provider:
        Optional callable that executes ``LLM_CALL`` decisions:
        ``llm_provider(prompt: str, context: dict) -> str``. When set,
        ``_execute_llm`` invokes it and returns the model text as a
        successful ``llm_response`` result. When unset, ``LLM_CALL``
        decisions are refused with a structured error rather than
        fabricating model output.

    The ``identity`` kwarg is forwarded on every ``/v1/process`` call;
    if omitted, a placeholder ``("unknown", session_id)`` identity is
    used.  In real deployments the identity will be supplied by the
    chat gateway that owns the session.
    """

    def __init__(
        self,
        client: H3Client,
        session_id: str,
        context: Context,
        max_iterations: int = 50,
        identity: Identity | None = None,
        llm_provider: Callable[[str, dict[str, Any]], str] | None = None,
    ):
        self.client = client
        self.session_id = session_id
        self.context = context
        self.max_iterations = max_iterations
        self.identity = identity or Identity(
            platform="shim",
            chat_id=session_id,
        )
        self.llm_provider = llm_provider
        self.iteration = 0
        self._available_tools: dict[str, Callable[..., object]] = {}

        # Polling configuration for WAIT decisions that carry a
        # ``poll_endpoint``.  Class-level so tests/subclasses can tune
        # without touching ``__init__``.
        self.max_polls: int = 30
        self.poll_interval: float = 1.0
        self.poll_timeout: float = 5.0

    # ── tool registry ────────────────────────────────────────────────

    def register_tool(self, name: str, fn: Callable[..., object]) -> None:
        """Register *fn* as the implementation for tool *name*.

        When the harness issues a ``TOOL_CALL`` decision with that name,
        ``_execute_tool`` will invoke ``fn(**params)`` and treat its
        return value as the tool output.
        """
        self._available_tools[name] = fn

    # ── main loop ────────────────────────────────────────────────────

    async def run(self, message: Message) -> str:
        """Run the loop until the harness ends the session.

        Returns the string value of the terminating :class:`EndReason`
        (``"task_complete"``, ``"error"``, ``"timeout"``, …).  On
        cancellation or unexpected errors a plain sentinel string is
        returned instead so callers can react without having to inspect
        exceptions.
        """
        try:
            process_start = time.monotonic()
            decision: Decision = await self.client.process(
                self.session_id,
                message,
                self.identity,
                self.context,
            )
            process_latency_ms = (time.monotonic() - process_start) * 1000
            logger.info(
                "H3ShimLoop: process session=%s iteration=%d "
                "decision_type=%s process_latency_ms=%.2f",
                self.session_id,
                self.iteration,
                decision.decision.value,
                process_latency_ms,
            )

            while decision.decision != DecisionType.END:
                self.iteration += 1
                if self.iteration > self.max_iterations:
                    logger.warning(
                        "H3ShimLoop: max iterations (%d) exceeded for session %s",
                        self.max_iterations,
                        self.session_id,
                    )
                    return "timeout"

                result = await self._execute(decision)
                logger.info(
                    "H3ShimLoop: executed hop %d session=%s "
                    "decision_type=%s execution_ms=%.2f",
                    self.iteration,
                    self.session_id,
                    decision.decision.value,
                    result.duration_ms,
                )

                result_start = time.monotonic()
                decision = await self.client.result(
                    self.session_id,
                    decision.decision_id,
                    result,
                )
                result_latency_ms = (time.monotonic() - result_start) * 1000
                logger.info(
                    "H3ShimLoop: result session=%s decision_id=%s "
                    "decision_type=%s result_latency_ms=%.2f",
                    self.session_id,
                    decision.decision_id,
                    decision.decision.value,
                    result_latency_ms,
                )

            # decision.decision == DecisionType.END is guaranteed here
            assert decision.end is not None  # for type-checkers
            return decision.end.reason.value

        except asyncio.CancelledError:
            logger.info("H3ShimLoop: cancelled for session %s", self.session_id)
            try:
                await self.client.cancel(self.session_id)
            except Exception:
                logger.warning(
                    "H3ShimLoop: cancel RPC failed for session %s",
                    self.session_id,
                    exc_info=True,
                )
            return "cancelled"

        except Exception:
            logger.error(
                "H3ShimLoop: error in session %s", self.session_id, exc_info=True
            )
            return "error"

    # ── dispatch ─────────────────────────────────────────────────────

    async def _execute(self, decision: Decision) -> ExecutionResult:
        """Dispatch *decision* to the correct executor.

        Each branch sets ``result.duration_ms`` before returning so the
        harness can correlate work effort with decisions.
        """
        kind = decision.decision
        try:
            if kind == DecisionType.TOOL_CALL:
                assert decision.tool_call is not None
                return await self._execute_tool(decision.tool_call)
            if kind == DecisionType.LLM_CALL:
                assert decision.llm_call is not None
                return await self._execute_llm(decision.llm_call)
            if kind == DecisionType.TEXT:
                assert decision.text is not None
                return await self._execute_text(decision.text)
            if kind == DecisionType.WAIT:
                assert decision.wait is not None
                return await self._execute_wait(decision.wait)
            if kind == DecisionType.DELEGATE:
                assert decision.delegate is not None
                return await self._execute_delegate(decision.delegate)
            if kind == DecisionType.END:
                return ExecutionResult(type="end", success=True)

            # Unknown future decision type — surface as a structured error
            # rather than crash, so the harness can react.
            return ExecutionResult(
                type="error",
                data={"error": f"Unknown decision type: {kind!r}"},
                success=False,
            )
        except Exception as e:
            logger.error("H3ShimLoop: dispatch failed (%s)", kind, exc_info=True)
            return ExecutionResult(
                type="error",
                data={"error": str(e), "phase": f"dispatch:{kind}"},
                success=False,
            )

    # ── executors ────────────────────────────────────────────────────

    async def _execute_tool(self, tc: ToolCall) -> ExecutionResult:
        """Run a tool registered via :meth:`register_tool`.

        Unknown tools and tool exceptions are reported back as
        structured :class:`ExecutionResult` errors — never re-raised —
        so the harness always sees a result for its decision.
        """
        start = time.monotonic()
        try:
            fn = self._available_tools.get(tc.name)
            if fn is None:
                logger.warning("Unknown tool requested: %s", tc.name)
                result = ExecutionResult(
                    type="error",
                    tool_name=tc.name,
                    data={"error": f"Unknown tool: {tc.name}"},
                    success=False,
                )
            else:
                output = fn(**tc.params)
                result = ExecutionResult(
                    type="tool_result",
                    tool_name=tc.name,
                    data={"output": output},
                    success=True,
                )
        except Exception as e:
            logger.error("Tool %s raised: %s", tc.name, e, exc_info=True)
            result = ExecutionResult(
                type="error",
                tool_name=tc.name,
                data={"error": str(e)},
                success=False,
            )
        result.duration_ms = (time.monotonic() - start) * 1000
        return result

    @staticmethod
    def _llm_prompt(llm: LLMCall) -> str:
        """Flatten an ``LLMCall``'s messages into a single prompt string.

        Each message becomes a ``role: content`` line (chronological, so
        a chat-style provider sees the conversation). Falls back to the
        harness's ``system_prompt`` when no messages were sent.
        """
        if llm.messages:
            return "\n".join(f"{m.role}: {m.content}" for m in llm.messages)
        return llm.system_prompt or ""

    async def _execute_llm(self, llm: LLMCall) -> ExecutionResult:
        """Execute an ``LLMCall`` decision.

        When an ``llm_provider`` callable was injected at construction it
        is invoked as ``llm_provider(prompt, context)`` and its text is
        returned as a successful ``llm_response`` result — the host wires
        its real model client here. Without a provider the shim refuses
        with a structured error instead of fabricating model output: a
        harness issuing an ``LLMCall`` must never receive fake content
        presented as a successful answer.
        """
        start = time.monotonic()
        if self.llm_provider is not None:
            try:
                prompt = self._llm_prompt(llm)
                context: dict[str, Any] = {
                    "model": llm.model,
                    "system_prompt": llm.system_prompt,
                    "temperature": llm.temperature,
                    "max_tokens": llm.max_tokens,
                    "messages": [m.model_dump() for m in llm.messages],
                    "session_id": self.session_id,
                }
                text = self.llm_provider(prompt, context)
            except Exception as e:
                logger.error("LLM provider raised: %s", e, exc_info=True)
                result = ExecutionResult(
                    type="error",
                    data={
                        "error": f"LLM provider failed: {e}",
                        "phase": "llm_call",
                    },
                    success=False,
                )
            else:
                result = ExecutionResult(
                    type="llm_response",
                    data={"content": text},
                    success=True,
                )
            result.duration_ms = (time.monotonic() - start) * 1000
            return result

        logger.warning(
            "LLM call refused: model=%s messages=%d (no LLM provider configured)",
            llm.model,
            len(llm.messages),
        )
        result = ExecutionResult(
            type="error",
            data={
                "error": (
                    "LLM not configured: no LLM provider wired in this shim; "
                    "refusing to fabricate a response"
                ),
                "phase": "llm_call",
            },
            success=False,
        )
        result.duration_ms = (time.monotonic() - start) * 1000
        return result

    async def _execute_text(self, text: TextResponse) -> ExecutionResult:
        """Send a text payload to the user.

        The shim doesn't own a transport here, so we just log and
        forward a ``text_sent`` marker back to the harness.  When
        ``finished`` is set the harness will receive that flag in the
        result so it can treat the turn as closed.
        """
        start = time.monotonic()
        logger.info("TEXT: %s", text.content[:100])
        data: dict[str, object] = {"content": text.content}
        if text.finished:
            data["finished"] = True
        result = ExecutionResult(type="text_sent", data=data, success=True)
        result.duration_ms = (time.monotonic() - start) * 1000
        return result

    async def _execute_wait(self, wait: Wait) -> ExecutionResult:
        """Handle a ``WAIT`` decision.

        If the harness supplied a ``duration_seconds`` we sleep that
        long first.  When a ``poll_endpoint`` is present we then poll it
        with GET requests until the remote side reports completion —
        a 2xx response whose JSON body contains ``{"status": "complete"}``
        or ``{"finished": true}`` — or until ``max_polls`` attempts are
        exhausted.  Non-2xx responses are treated as transient and
        retried after ``poll_interval`` seconds.
        """
        start = time.monotonic()
        if wait.duration_seconds:
            await asyncio.sleep(wait.duration_seconds)

        polls = 0
        success = True
        error: str | None = None

        if wait.poll_endpoint:
            try:
                async with httpx.AsyncClient(timeout=self.poll_timeout) as http:
                    while polls < self.max_polls:
                        polls += 1
                        try:
                            resp = await http.get(wait.poll_endpoint)
                        except httpx.RequestError as e:
                            logger.warning(
                                "WAIT: poll %d to %s failed: %s",
                                polls,
                                wait.poll_endpoint,
                                e,
                            )
                            error = f"poll request failed: {e}"
                            success = False
                            break

                        if 200 <= resp.status_code < 300:
                            try:
                                body = resp.json()
                            except ValueError:
                                body = {}
                            if isinstance(body, dict) and (
                                body.get("status") == "complete"
                                or body.get("finished") is True
                            ):
                                logger.info(
                                    "WAIT: poll endpoint %s reported complete "
                                    "after %d poll(s)",
                                    wait.poll_endpoint,
                                    polls,
                                )
                                break
                        else:
                            logger.debug(
                                "WAIT: poll %d to %s returned HTTP %d; retrying",
                                polls,
                                wait.poll_endpoint,
                                resp.status_code,
                            )

                        if polls < self.max_polls:
                            await asyncio.sleep(self.poll_interval)
                    else:
                        # Loop exhausted without break — timed out.
                        success = False
                        error = (
                            f"poll endpoint did not complete within "
                            f"{self.max_polls} polls"
                        )
                        logger.warning("WAIT: %s (%s)", error, wait.poll_endpoint)
            except httpx.RequestError as e:
                logger.error(
                    "WAIT: polling %s failed: %s",
                    wait.poll_endpoint,
                    e,
                    exc_info=True,
                )
                success = False
                error = f"poll request failed: {e}"

        total_seconds = time.monotonic() - start
        data: dict[str, object] = {
            "reason": wait.reason,
            "duration": total_seconds,
            "polls": polls,
        }
        if wait.poll_endpoint:
            data["poll_endpoint"] = wait.poll_endpoint
        if error is not None:
            data["error"] = error

        result = ExecutionResult(
            type="wait_complete",
            data=data,
            success=success,
        )
        result.duration_ms = total_seconds * 1000
        return result

    async def _execute_delegate(self, delegate: Delegate) -> ExecutionResult:
        """Acknowledge a ``DELEGATE`` decision.

        Actual sub-agent spawning is the responsibility of the host
        (Hermes Core).  The shim confirms acceptance so the harness can
        move on.
        """
        start = time.monotonic()
        logger.info("Delegate: task=%s", delegate.task)
        result = ExecutionResult(
            type="delegate_result",
            data={"task": delegate.task, "status": "accepted"},
            success=True,
        )
        result.duration_ms = (time.monotonic() - start) * 1000
        return result
