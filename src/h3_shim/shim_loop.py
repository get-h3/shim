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
    ):
        self.client = client
        self.session_id = session_id
        self.context = context
        self.max_iterations = max_iterations
        self.identity = identity or Identity(
            platform="shim",
            chat_id=session_id,
        )
        self.iteration = 0
        self._available_tools: dict[str, Callable[..., object]] = {}

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
            decision: Decision = await self.client.process(
                self.session_id,
                message,
                self.identity,
                self.context,
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
                decision = await self.client.result(
                    self.session_id,
                    decision.decision_id,
                    result,
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
            logger.error(
                "H3ShimLoop: dispatch failed (%s)", kind, exc_info=True
            )
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

    async def _execute_llm(self, llm: LLMCall) -> ExecutionResult:
        """Stub the LLM-call executor.

        In a real shim this would forward to whichever LLM provider
        Hermes is configured to use.  For now the placeholder lets the
        loop remain end-to-end testable without dragging in a model
        client.
        """
        start = time.monotonic()
        try:
            logger.info("LLM call: model=%s messages=%d", llm.model, len(llm.messages))
            result = ExecutionResult(
                type="llm_response",
                data={"content": "[LLM response placeholder]", "model": llm.model},
                success=True,
            )
        except Exception as e:
            logger.error("LLM call failed: %s", e, exc_info=True)
            result = ExecutionResult(
                type="error",
                data={"error": str(e), "phase": "llm_call"},
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
        long; an explicit ``poll_endpoint`` is logged but not yet
        implemented (it would require per-harness webhook support).
        """
        start = time.monotonic()
        if wait.duration_seconds:
            await asyncio.sleep(wait.duration_seconds)
        if wait.poll_endpoint:
            logger.info(
                "WAIT: polling endpoint %s not implemented in shim",
                wait.poll_endpoint,
            )
        result = ExecutionResult(
            type="wait_complete",
            data={"reason": wait.reason, "duration": wait.duration_seconds or 0},
            success=True,
        )
        result.duration_ms = (time.monotonic() - start) * 1000
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
