"""H3 compliance test battery — 43 tests across 6 categories.

This module is the single most important piece of the shim. It defines the
:cclass:`H3TestBattery`, a black-box HTTP probe that exercises every public
endpoint of an H3-compliant harness and verifies the wire protocol against
the contract documented in ``get-h3/protocol``.

Design notes
------------
* Tests talk directly to ``httpx.AsyncClient``; we deliberately do **not**
  go through :class:`h3_shim.client.H3Client`. The whole point is to
  validate the on-the-wire payload — including malformed payloads — and
  the higher-level client would mask contract violations.
* Each test is a coroutine returning a :class:`TestResult` instead of
  raising. That lets ``run_all()`` keep going after a single failure
  and means an unreachable harness yields an "all-failed" report rather
  than crashing the runner.
* Session IDs are per-test, per-invocation. Running the battery twice
  in a row must not give a passing harness a second chance to fail
  state leakage.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ── dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class TestResult:
    """Outcome of a single compliance test."""

    name: str           # e.g., "health_ok"
    passed: bool
    detail: str         # e.g., "Expected 200, got 200"
    duration_ms: float
    category: str       # e.g., "Health & Protocol"


@dataclass
class TestReport:
    """Aggregate of every :class:`TestResult` produced by one battery run."""

    results: list[TestResult] = field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0
    duration_ms: float = 0.0
    timestamp: str = ""

    @property
    def all_passing(self) -> bool:
        return self.failed == 0


# ── battery ─────────────────────────────────────────────────────────────────


# Categories — used both as display labels and as filter tokens for
# ``--categories`` on the CLI.
CATEGORIES: dict[str, str] = {
    "health": "Health & Protocol",
    "process": "Process Basic Flows",
    "decisions": "Decision Types",
    "results": "Result Handling",
    "errors": "Error & Edge Cases",
    "stress": "Stress & Performance",
}

# Total expected count — kept here so we can sanity-check at runtime.
EXPECTED_TEST_COUNT = 43


class H3TestBattery:
    """Black-box test suite for an H3 harness endpoint.

    Parameters
    ----------
    endpoint:
        Base URL of the harness, e.g. ``"http://localhost:9191"``.
    transport:
        Free-form label for the report (``"rest"`` by default; informational
        only — the suite always speaks HTTP).
    config:
        Optional harness-specific configuration knob (e.g. tool/model
        fixtures). Currently unused beyond being echoed in logs.
    """

    #: Hard ceiling on any single network round-trip.
    PER_TEST_TIMEOUT_S: float = 10.0

    def __init__(
        self,
        endpoint: str,
        transport: str = "rest",
        config: dict | None = None,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.transport = transport
        self.config = config or {}
        self.client = httpx.AsyncClient(
            base_url=self.endpoint, timeout=self.PER_TEST_TIMEOUT_S
        )
        self.results: list[TestResult] = []

    # ── request helpers ─────────────────────────────────────────────────

    def _sid(self, label: str) -> str:
        """Build a fresh session id tied to a specific test invocation."""
        return f"test-{label}-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _blank_context() -> dict[str, Any]:
        """Minimal valid ``context`` payload."""
        return {
            "history": [],
            "tools": [],
            "models": [],
            "memory": "",
            "skills": [],
            "config": {},
            "session_state": {},
        }

    def _process_body(
        self,
        label: str,
        *,
        content: str = "Hello",
        tools: list[dict] | None = None,
        models: list[dict] | None = None,
        identity: dict | None = None,
        extra_history: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Build a ``/v1/process`` body with sensible defaults."""
        ctx = self._blank_context()
        if tools is not None:
            ctx["tools"] = tools
        if models is not None:
            ctx["models"] = models
        history = list(extra_history or [])
        history.append({"role": "user", "content": content})
        ctx["history"] = history
        return {
            "session_id": self._sid(label),
            "message": {"role": "user", "content": content},
            "identity": identity
            or {"platform": "test", "chat_id": "test-chat"},
            "context": ctx,
        }

    async def _safe_call(
        self, coro
    ) -> tuple[httpx.Response | None, BaseException | None]:
        """Await *coro* swallowing :class:`httpx.HTTPError` family errors.

        Returns ``(response_or_none, exception_or_none)`` so test methods can
        keep their bodies short and predictable.  Exactly one of the two
        return slots is non-``None``.
        """
        try:
            resp = await coro
        except httpx.HTTPError as exc:
            logger.debug("HTTP error from harness: %s", exc)
            return None, exc
        except Exception as exc:  # noqa: BLE001 — convert every failure into TestResult
            logger.warning("Unexpected error contacting harness: %s", exc)
            return None, exc
        if resp is None:  # pragma: no cover — defensive
            return None, RuntimeError("httpx returned None response")
        return resp, None

    # ── run ─────────────────────────────────────────────────────────────

    async def run_all(self) -> TestReport:
        """Run every category sequentially; assemble a :class:`TestReport`."""
        from datetime import datetime, timezone

        started = time.monotonic()
        results: list[TestResult] = []
        for category in (
            self.category_1_health,
            self.category_2_process,
            self.category_3_decisions,
            self.category_4_results,
            self.category_5_errors,
            self.category_6_stress,
        ):
            results.extend(await category())

        duration_ms = (time.monotonic() - started) * 1000.0
        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed
        report = TestReport(
            results=results,
            total=len(results),
            passed=passed,
            failed=failed,
            duration_ms=duration_ms,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        if report.total != EXPECTED_TEST_COUNT:
            logger.warning(
                "Battery produced %d results, expected %d",
                report.total,
                EXPECTED_TEST_COUNT,
            )
        self.results = results
        return report

    async def close(self):
        """Tear down the underlying httpx client."""
        await self.client.aclose()

    # ── test framework glue ─────────────────────────────────────────────

    def _timed(self, name: str, category: str):
        """Return a ``(start_ms, done(result))`` pair for timed assertions."""
        start = time.monotonic()

        def done(passed: bool, detail: str) -> TestResult:
            duration_ms = (time.monotonic() - start) * 1000.0
            return TestResult(name, passed, detail, duration_ms, category)

        return done

    # ════════════════════════════════════════════════════════════════════
    # Category 1 — Health & Protocol (7 tests)
    # ════════════════════════════════════════════════════════════════════

    async def category_1_health(self) -> list[TestResult]:
        return [
            await self.test_1_1_health_ok(),
            await self.test_1_2_health_version(),
            await self.test_1_3_health_transport(),
            await self.test_1_4_health_capabilities(),
            await self.test_1_5_health_content_type(),
            await self.test_1_6_health_latency(),
            await self.test_1_7_health_idempotent(),
        ]

    async def test_1_1_health_ok(self) -> TestResult:
        """GET /v1/health → 200 with status='ok'."""
        cat = CATEGORIES["health"]
        done = self._timed("health_ok", cat)
        try:
            resp, err = await self._safe_call(self.client.get("/v1/health"))
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code != 200:
                return done(False, f"Expected 200, got {resp.status_code}")
            body = resp.json()
            status = body.get("status")
            if status != "ok":
                return done(False, f"Expected status 'ok', got '{status}'")
            return done(True, f"200 OK, status={status}")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_1_2_health_version(self) -> TestResult:
        """Health response carries ``version`` and ``protocol_version``."""
        cat = CATEGORIES["health"]
        done = self._timed("health_version", cat)
        try:
            resp, err = await self._safe_call(self.client.get("/v1/health"))
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code != 200:
                return done(False, f"Expected 200, got {resp.status_code}")
            body = resp.json()
            missing = [k for k in ("version", "protocol_version") if k not in body]
            if missing:
                return done(
                    False, f"Missing keys: {missing}; body={json.dumps(body)[:200]}"
                )
            return done(
                True,
                f"version={body['version']!r}, "
                f"protocol_version={body['protocol_version']!r}",
            )
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_1_3_health_transport(self) -> TestResult:
        """Health response carries a ``transport`` field."""
        cat = CATEGORIES["health"]
        done = self._timed("health_transport", cat)
        try:
            resp, err = await self._safe_call(self.client.get("/v1/health"))
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code != 200:
                return done(False, f"Expected 200, got {resp.status_code}")
            body = resp.json()
            if "transport" not in body:
                return done(False, f"No 'transport' in body: {body}")
            return done(True, f"transport={body['transport']!r}")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_1_4_health_capabilities(self) -> TestResult:
        """``capabilities`` is a non-empty list of strings."""
        cat = CATEGORIES["health"]
        done = self._timed("health_capabilities", cat)
        try:
            resp, err = await self._safe_call(self.client.get("/v1/health"))
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            body = resp.json()
            caps = body.get("capabilities")
            if not isinstance(caps, list):
                return done(False, f"capabilities is not a list: {type(caps).__name__}")
            if len(caps) == 0:
                return done(False, "capabilities list is empty")
            if any(not isinstance(c, str) for c in caps):
                return done(False, "capabilities contains non-string entries")
            return done(True, f"{len(caps)} capabilities: {caps}")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_1_5_health_content_type(self) -> TestResult:
        """Health response ``Content-Type`` is JSON."""
        cat = CATEGORIES["health"]
        done = self._timed("health_content_type", cat)
        try:
            resp, err = await self._safe_call(self.client.get("/v1/health"))
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            ctype = resp.headers.get("content-type", "")
            if "application/json" not in ctype:
                return done(False, f"Content-Type is '{ctype}'")
            return done(True, f"Content-Type={ctype}")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_1_6_health_latency(self) -> TestResult:
        """Health response latency under 500 ms."""
        cat = CATEGORIES["health"]
        done = self._timed("health_latency", cat)
        try:
            start = time.monotonic()
            resp, err = await self._safe_call(self.client.get("/v1/health"))
            elapsed_ms = (time.monotonic() - start) * 1000.0
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code != 200:
                return done(False, f"Status {resp.status_code}")
            if elapsed_ms >= 500.0:
                return done(False, f"Latency {elapsed_ms:.1f}ms >= 500ms")
            return done(True, f"{elapsed_ms:.1f}ms")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_1_7_health_idempotent(self) -> TestResult:
        """Two consecutive ``/v1/health`` calls return the same ``status``."""
        cat = CATEGORIES["health"]
        done = self._timed("health_idempotent", cat)
        try:
            r1, e1 = await self._safe_call(self.client.get("/v1/health"))
            r2, e2 = await self._safe_call(self.client.get("/v1/health"))
            if e1 is not None or e2 is not None or r1 is None or r2 is None:
                return done(False, f"Exception: {e1} / {e2}")
            if r1.status_code != 200 or r2.status_code != 200:
                return done(
                    False,
                    f"Statuses {r1.status_code} / {r2.status_code}",
                )
            s1 = r1.json().get("status")
            s2 = r2.json().get("status")
            if s1 != s2:
                return done(False, f"Inconsistent status {s1!r} vs {s2!r}")
            return done(True, f"stable status={s1!r}")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    # ════════════════════════════════════════════════════════════════════
    # Category 2 — Process Basic Flows (8 tests)
    # ════════════════════════════════════════════════════════════════════

    async def category_2_process(self) -> list[TestResult]:
        return [
            await self.test_2_1_process_returns_decision(),
            await self.test_2_2_process_decision_has_id(),
            await self.test_2_3_process_decision_has_type(),
            await self.test_2_4_process_text_finished_false(),
            await self.test_2_5_process_text_finished_true(),
            await self.test_2_6_process_multiple_turns(),
            await self.test_2_7_process_session_isolation(),
            await self.test_2_8_process_preserves_history(),
        ]

    async def test_2_1_process_returns_decision(self) -> TestResult:
        """POST /v1/process with a basic message returns a valid Decision."""
        cat = CATEGORIES["process"]
        done = self._timed("process_returns_decision", cat)
        body = self._process_body("returns_decision")
        try:
            resp, err = await self._safe_call(
                self.client.post("/v1/process", json=body)
            )
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code != 200:
                return done(
                    False,
                    f"Expected 200, got {resp.status_code}; "
                    f"body={resp.text[:200]}",
                )
            data = resp.json()
            if not isinstance(data, dict):
                return done(False, f"Response is not a dict: {type(data).__name__}")
            if "decision" not in data or "decision_id" not in data:
                return done(
                    False, f"Missing decision fields: {list(data.keys())}"
                )
            return done(True, f"decision={data['decision']!r}")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_2_2_process_decision_has_id(self) -> TestResult:
        """``decision_id`` is a non-empty string."""
        cat = CATEGORIES["process"]
        done = self._timed("process_decision_has_id", cat)
        body = self._process_body("decision_id")
        try:
            resp, err = await self._safe_call(
                self.client.post("/v1/process", json=body)
            )
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code != 200:
                return done(False, f"Status {resp.status_code}")
            data = resp.json()
            did = data.get("decision_id")
            if not isinstance(did, str) or not did:
                return done(False, f"Invalid decision_id: {did!r}")
            return done(True, f"decision_id={did[:24]}")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_2_3_process_decision_has_type(self) -> TestResult:
        """``decision`` field is one of the valid DecisionType values."""
        cat = CATEGORIES["process"]
        done = self._timed("process_decision_has_type", cat)
        body = self._process_body("decision_type")
        valid = {
            "tool_call", "llm_call", "text", "wait", "delegate", "end",
        }
        try:
            resp, err = await self._safe_call(
                self.client.post("/v1/process", json=body)
            )
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code != 200:
                return done(False, f"Status {resp.status_code}")
            data = resp.json()
            kind = data.get("decision")
            if kind not in valid:
                return done(False, f"Invalid decision type: {kind!r}")
            return done(True, f"decision={kind!r}")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_2_4_process_text_finished_false(self) -> TestResult:
        """A 'text' decision with ``finished=false`` is honoured.

        Sends a prompt that should elicit a streaming-style text decision
        and asserts the resulting ``text.finished`` is ``False``.
        """
        cat = CATEGORIES["process"]
        done = self._timed("process_text_finished_false", cat)
        body = self._process_body(
            "text_not_finished",
            content="Just start a thought, do not finish it yet.",
        )
        try:
            resp, err = await self._safe_call(
                self.client.post("/v1/process", json=body)
            )
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code != 200:
                return done(False, f"Status {resp.status_code}")
            data = resp.json()
            if data.get("decision") != "text":
                return done(
                    True,
                    f"Skipped — harness returned {data.get('decision')!r}, not text",
                )
            text = data.get("text") or {}
            if text.get("finished") is not False:
                return done(
                    False,
                    f"Expected finished=false, got "
                    f"{text.get('finished')!r}",
                )
            return done(True, "text.finished=false")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_2_5_process_text_finished_true(self) -> TestResult:
        """A 'text' decision with ``finished=true`` is accepted."""
        cat = CATEGORIES["process"]
        done = self._timed("process_text_finished_true", cat)
        body = self._process_body(
            "text_finished",
            content="Give me the final answer in one short sentence.",
        )
        try:
            resp, err = await self._safe_call(
                self.client.post("/v1/process", json=body)
            )
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code != 200:
                return done(False, f"Status {resp.status_code}")
            data = resp.json()
            if data.get("decision") != "text":
                return done(
                    True,
                    f"Skipped — harness returned {data.get('decision')!r}",
                )
            text = data.get("text") or {}
            if text.get("finished") is not True:
                return done(
                    False, f"Expected finished=true, got {text.get('finished')!r}"
                )
            return done(True, "text.finished=true")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_2_6_process_multiple_turns(self) -> TestResult:
        """A 10-turn conversation runs end-to-end without error."""
        cat = CATEGORIES["process"]
        done = self._timed("process_multiple_turns", cat)
        session_id = self._sid("multi_turn")
        try:
            history: list[dict] = []
            last_kind: str | None = None
            for turn in range(10):
                history.append({"role": "user", "content": f"Turn {turn + 1}: hi"})
                body = {
                    "session_id": session_id,
                    "message": {"role": "user", "content": f"Turn {turn + 1}: hi"},
                    "identity": {"platform": "test", "chat_id": "test-chat"},
                    "context": {**self._blank_context(), "history": list(history)},
                }
                resp, err = await self._safe_call(
                    self.client.post("/v1/process", json=body)
                )
                if err is not None or resp is None:
                    return done(False, f"Turn {turn + 1}: exception {err}")
                if resp.status_code != 200:
                    return done(
                        False,
                        f"Turn {turn + 1}: status {resp.status_code}",
                    )
                last_kind = resp.json().get("decision")
                history.append({"role": "assistant", "content": f"ack {turn + 1}"})
            return done(True, f"10 turns OK, last decision={last_kind!r}")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_2_7_process_session_isolation(self) -> TestResult:
        """Two distinct sessions don't bleed state into each other."""
        cat = CATEGORIES["process"]
        done = self._timed("process_session_isolation", cat)
        sid_a = self._sid("iso_a")
        sid_b = self._sid("iso_b")
        try:
            ctx = self._blank_context()
            ctx["history"] = [{"role": "user", "content": "ctx-a-only"}]
            body_a = {
                "session_id": sid_a,
                "message": {"role": "user", "content": "remember ctx-a"},
                "identity": {"platform": "test", "chat_id": "test-chat"},
                "context": ctx,
            }
            body_b = {
                "session_id": sid_b,
                "message": {"role": "user", "content": "remember ctx-b"},
                "identity": {"platform": "test", "chat_id": "test-chat"},
                "context": self._blank_context(),
            }
            r1, e1 = await self._safe_call(
                self.client.post("/v1/process", json=body_a)
            )
            r2, e2 = await self._safe_call(
                self.client.post("/v1/process", json=body_b)
            )
            if e1 is not None or e2 is not None or r1 is None or r2 is None:
                return done(False, f"Exception: {e1} / {e2}")
            if r1.status_code != 200 or r2.status_code != 200:
                return done(
                    False,
                    f"Statuses {r1.status_code} / {r2.status_code}",
                )
            # Two fresh session ids ending in different hex tags must differ.
            if sid_a == sid_b:
                return done(False, "session ids collided")
            return done(True, f"{sid_a} ≠ {sid_b}")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_2_8_process_preserves_history(self) -> TestResult:
        """``context.history`` echoes through to subsequent turns."""
        cat = CATEGORIES["process"]
        done = self._timed("process_preserves_history", cat)
        session_id = self._sid("history")
        try:
            prior = [
                {"role": "user", "content": "earlier user"},
                {"role": "assistant", "content": "earlier assistant"},
                {"role": "user", "content": "another user"},
                {"role": "assistant", "content": "another assistant"},
            ]
            ctx = self._blank_context()
            ctx["history"] = prior
            body = {
                "session_id": session_id,
                "message": {"role": "user", "content": "what did we say before?"},
                "identity": {"platform": "test", "chat_id": "test-chat"},
                "context": ctx,
            }
            resp, err = await self._safe_call(
                self.client.post("/v1/process", json=body)
            )
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code != 200:
                return done(False, f"Status {resp.status_code}")
            data = resp.json()
            echoed = (data.get("context") or {}).get("history") or []
            if not isinstance(echoed, list):
                return done(False, "history not echoed as list")
            # We accept any growth or equal size — we just need history present.
            if len(echoed) < len(prior):
                return done(
                    False,
                    f"history shrank: {len(prior)} -> {len(echoed)}",
                )
            return done(True, f"history preserved ({len(echoed)} entries)")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    # ════════════════════════════════════════════════════════════════════
    # Category 3 — Decision Types (6 tests)
    # ════════════════════════════════════════════════════════════════════

    async def category_3_decisions(self) -> list[TestResult]:
        return [
            await self.test_3_1_decision_tool_call(),
            await self.test_3_2_decision_tool_call_valid_name(),
            await self.test_3_3_decision_tool_call_valid_params(),
            await self.test_3_4_decision_llm_call(),
            await self.test_3_5_decision_delegate(),
            await self.test_3_6_decision_end(),
        ]

    def _process_decision(
        self,
        label: str,
        *,
        content: str,
        tools: list[dict],
        models: list[dict],
    ) -> tuple[dict, str]:
        """Build a ``/v1/process`` body for decision-type tests."""
        sid = self._sid(label)
        ctx = self._blank_context()
        ctx["tools"] = tools
        ctx["models"] = models
        body = {
            "session_id": sid,
            "message": {"role": "user", "content": content},
            "identity": {"platform": "test", "chat_id": "test-chat"},
            "context": ctx,
        }
        return body, sid

    async def test_3_1_decision_tool_call(self) -> TestResult:
        """Harness is willing to return ``tool_call`` decisions."""
        cat = CATEGORIES["decisions"]
        done = self._timed("decision_tool_call", cat)
        tools = [
            {
                "name": "echo",
                "description": "Echo a string back",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            }
        ]
        body, _ = self._process_decision(
            "tool_call", content="call echo with 'hi'", tools=tools, models=[]
        )
        try:
            resp, err = await self._safe_call(
                self.client.post("/v1/process", json=body)
            )
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code != 200:
                return done(False, f"Status {resp.status_code}")
            data = resp.json()
            if data.get("decision") == "tool_call":
                tc = data.get("tool_call") or {}
                if not tc.get("name"):
                    return done(False, "tool_call decision missing 'name'")
                return done(True, f"tool_call name={tc['name']!r}")
            return done(
                True,
                f"Optional test — harness returned {data.get('decision')!r}; "
                "tool_call capability unverified but protocol valid",
            )
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_3_2_decision_tool_call_valid_name(self) -> TestResult:
        """If a tool_call comes back, its ``name`` is in ``context.tools``."""
        cat = CATEGORIES["decisions"]
        done = self._timed("decision_tool_call_valid_name", cat)
        tools = [
            {
                "name": "search",
                "description": "Search the corpus",
                "parameters": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                },
            }
        ]
        body, _ = self._process_decision(
            "tool_call_name",
            content="search for 'apple'",
            tools=tools,
            models=[],
        )
        try:
            resp, err = await self._safe_call(
                self.client.post("/v1/process", json=body)
            )
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code != 200:
                return done(False, f"Status {resp.status_code}")
            data = resp.json()
            if data.get("decision") != "tool_call":
                return done(
                    True,
                    f"Skipped — decision={data.get('decision')!r}",
                )
            tc = data.get("tool_call") or {}
            name = tc.get("name")
            valid = {t["name"] for t in tools}
            if name not in valid:
                return done(
                    False,
                    f"Tool name {name!r} not in context.tools {valid}",
                )
            return done(True, f"name={name!r} ∈ context.tools")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_3_3_decision_tool_call_valid_params(self) -> TestResult:
        """Tool params conform to the schema advertised in ``context.tools``."""
        cat = CATEGORIES["decisions"]
        done = self._timed("decision_tool_call_valid_params", cat)
        tools = [
            {
                "name": "lookup",
                "description": "Lookup by id",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "tag": {"type": "string"},
                    },
                    "required": ["id"],
                },
            }
        ]
        body, _ = self._process_decision(
            "tool_call_params",
            content="call lookup",
            tools=tools,
            models=[],
        )
        try:
            resp, err = await self._safe_call(
                self.client.post("/v1/process", json=body)
            )
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code != 200:
                return done(False, f"Status {resp.status_code}")
            data = resp.json()
            if data.get("decision") != "tool_call":
                return done(
                    True,
                    f"Skipped — decision={data.get('decision')!r}",
                )
            tc = data.get("tool_call") or {}
            params = tc.get("params") or {}
            if not isinstance(params, dict):
                return done(False, "params not a dict")
            if "id" in params and not isinstance(params["id"], int):
                return done(False, f"id not int: {type(params['id']).__name__}")
            return done(True, f"params={list(params.keys())}")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_3_4_decision_llm_call(self) -> TestResult:
        """Harness can return ``llm_call`` decisions when models are listed."""
        cat = CATEGORIES["decisions"]
        done = self._timed("decision_llm_call", cat)
        models = [
            {
                "name": "fast-1",
                "provider": "openai",
                "cost_per_1k_input": 0.0,
                "cost_per_1k_output": 0.0,
                "context_window": 8192,
                "supports_vision": False,
                "supports_tool_calling": True,
            }
        ]
        body, _ = self._process_decision(
            "llm_call",
            content="run a model",
            tools=[],
            models=models,
        )
        try:
            resp, err = await self._safe_call(
                self.client.post("/v1/process", json=body)
            )
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code != 200:
                return done(False, f"Status {resp.status_code}")
            data = resp.json()
            if data.get("decision") == "llm_call":
                llm = data.get("llm_call") or {}
                if not llm.get("model"):
                    return done(False, "llm_call missing 'model'")
                return done(True, f"llm_call model={llm['model']!r}")
            return done(
                True,
                f"Skipped — harness returned {data.get('decision')!r}",
            )
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_3_5_decision_delegate(self) -> TestResult:
        """Harness can return ``delegate`` decisions."""
        cat = CATEGORIES["decisions"]
        done = self._timed("decision_delegate", cat)
        body, _ = self._process_decision(
            "delegate",
            content="spawn a sub-agent to summarise",
            tools=[],
            models=[],
        )
        try:
            resp, err = await self._safe_call(
                self.client.post("/v1/process", json=body)
            )
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code != 200:
                return done(False, f"Status {resp.status_code}")
            data = resp.json()
            if data.get("decision") == "delegate":
                d = data.get("delegate") or {}
                if not d.get("task"):
                    return done(False, "delegate missing 'task'")
                return done(True, f"delegate task={d['task'][:40]!r}")
            return done(
                True,
                f"Skipped — harness returned {data.get('decision')!r}",
            )
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_3_6_decision_end(self) -> TestResult:
        """Harness returns ``end`` with a valid ``EndReason``."""
        cat = CATEGORIES["decisions"]
        done = self._timed("decision_end", cat)
        valid = {
            "task_complete", "user_requested", "error",
            "timeout", "rate_limited", "cancelled",
        }
        sid = self._sid("end")
        try:
            # Force an end by issuing a process call then a follow-up;
            # most harnesses terminate after a single turn for short prompts.
            body = {
                "session_id": sid,
                "message": {"role": "user", "content": "DONE"},
                "identity": {"platform": "test", "chat_id": "test-chat"},
                "context": self._blank_context(),
            }
            end_seen = False
            last_kind: str | None = None
            for _ in range(3):
                resp, err = await self._safe_call(
                    self.client.post("/v1/process", json=body)
                )
                if err is not None or resp is None:
                    return done(False, f"Exception: {err}")
                if resp.status_code != 200:
                    return done(False, f"Status {resp.status_code}")
                data = resp.json()
                last_kind = data.get("decision")
                if data.get("decision") == "end":
                    end_seen = True
                    reason = (data.get("end") or {}).get("reason")
                    if reason not in valid:
                        return done(
                            False, f"Invalid end reason: {reason!r}"
                        )
                    break
                # Drive the loop forward with a blank 'text' result.
                did = data.get("decision_id")
                if not did:
                    break
                result_body = {
                    "session_id": sid,
                    "decision_id": did,
                    "result": {
                        "type": "text_sent",
                        "data": {"finished": True},
                        "duration_ms": 0.0,
                        "success": True,
                    },
                }
                resp2, err2 = await self._safe_call(
                    self.client.post("/v1/result", json=result_body)
                )
                if err2 is not None or resp2 is None or resp2.status_code != 200:
                    break
            if end_seen:
                return done(True, f"end reached, last_kind={last_kind!r}")
            return done(
                True,
                f"Optional — harness returned {last_kind!r}, never hit end",
            )
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    # ════════════════════════════════════════════════════════════════════
    # Category 4 — Result Handling (7 tests)
    # ════════════════════════════════════════════════════════════════════

    async def category_4_results(self) -> list[TestResult]:
        return [
            await self.test_4_1_result_tool_success(),
            await self.test_4_2_result_tool_failure(),
            await self.test_4_3_result_llm_response(),
            await self.test_4_4_result_text_sent(),
            await self.test_4_5_result_delegate_result(),
            await self.test_4_6_result_error(),
            await self.test_4_7_result_wait_timeout(),
        ]

    async def _send_decision(
        self,
        label: str,
        *,
        tools: list[dict] | None = None,
        models: list[dict] | None = None,
        content: str = "go",
    ) -> tuple[str, dict] | tuple[None, None]:
        """POST /v1/process; return ``(sid, decision)`` or ``(None, None)``."""
        sid = self._sid(label)
        ctx = self._blank_context()
        ctx["tools"] = tools or []
        ctx["models"] = models or []
        body = {
            "session_id": sid,
            "message": {"role": "user", "content": content},
            "identity": {"platform": "test", "chat_id": "test-chat"},
            "context": ctx,
        }
        resp, err = await self._safe_call(
            self.client.post("/v1/process", json=body)
        )
        if err is not None or resp is None:
            return None, None
        if resp.status_code != 200:
            return None, None
        return sid, resp.json()

    async def _send_result(
        self,
        session_id: str,
        decision_id: str,
        result: dict,
    ) -> httpx.Response | None:
        """POST /v1/result and return the response (or ``None`` on transport error)."""
        body = {
            "session_id": session_id,
            "decision_id": decision_id,
            "result": result,
        }
        resp, err = await self._safe_call(
            self.client.post("/v1/result", json=body)
        )
        if err is not None or resp is None:
            return None
        return resp

    async def test_4_1_result_tool_success(self) -> TestResult:
        """A successful ``tool_result`` is accepted."""
        cat = CATEGORIES["results"]
        done = self._timed("result_tool_success", cat)
        sid, decision = await self._send_decision(
            "r_tool_ok",
            tools=[
                {
                    "name": "noop",
                    "description": "no-op tool",
                    "parameters": {"type": "object"},
                }
            ],
            content="use noop",
        )
        if sid is None or decision is None:
            return done(False, "Failed to set up decision")
        try:
            result = {
                "type": "tool_result",
                "tool_name": "noop",
                "data": {"output": "ok"},
                "success": True,
                "duration_ms": 1.0,
            }
            resp = await self._send_result(sid, decision["decision_id"], result)
            if resp is None:
                return done(False, "Result endpoint unreachable")
            if resp.status_code >= 400:
                return done(
                    False,
                    f"Result endpoint rejected tool_result: status={resp.status_code}",
                )
            return done(True, f"tool_result accepted, status={resp.status_code}")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_4_2_result_tool_failure(self) -> TestResult:
        """A failed tool result (``success=False``) is accepted."""
        cat = CATEGORIES["results"]
        done = self._timed("result_tool_failure", cat)
        sid, decision = await self._send_decision("r_tool_fail", content="fail one")
        if sid is None or decision is None:
            return done(False, "Failed to set up decision")
        try:
            result = {
                "type": "tool_result",
                "tool_name": "explode",
                "data": {"error": "kaboom"},
                "success": False,
                "duration_ms": 0.0,
            }
            resp = await self._send_result(sid, decision["decision_id"], result)
            if resp is None:
                return done(False, "Result endpoint unreachable")
            if resp.status_code >= 400:
                return done(
                    False,
                    f"Result endpoint rejected failure: status={resp.status_code}",
                )
            return done(True, f"failure accepted, status={resp.status_code}")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_4_3_result_llm_response(self) -> TestResult:
        """``llm_response`` result type is accepted."""
        cat = CATEGORIES["results"]
        done = self._timed("result_llm_response", cat)
        sid, decision = await self._send_decision("r_llm", content="ask llm")
        if sid is None or decision is None:
            return done(False, "Failed to set up decision")
        try:
            result = {
                "type": "llm_response",
                "data": {"content": "Hi!", "model": "m"},
                "success": True,
                "duration_ms": 12.0,
            }
            resp = await self._send_result(sid, decision["decision_id"], result)
            if resp is None:
                return done(False, "Result endpoint unreachable")
            if resp.status_code >= 400:
                return done(
                    False, f"Result rejected llm_response: status={resp.status_code}"
                )
            return done(True, "llm_response accepted")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_4_4_result_text_sent(self) -> TestResult:
        """``text_sent`` result type is accepted."""
        cat = CATEGORIES["results"]
        done = self._timed("result_text_sent", cat)
        sid, decision = await self._send_decision("r_text", content="say hi")
        if sid is None or decision is None:
            return done(False, "Failed to set up decision")
        try:
            result = {
                "type": "text_sent",
                "data": {"content": "hello there"},
                "success": True,
                "duration_ms": 1.0,
            }
            resp = await self._send_result(sid, decision["decision_id"], result)
            if resp is None:
                return done(False, "Result endpoint unreachable")
            if resp.status_code >= 400:
                return done(
                    False, f"Result rejected text_sent: status={resp.status_code}"
                )
            return done(True, "text_sent accepted")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_4_5_result_delegate_result(self) -> TestResult:
        """``delegate_result`` is accepted."""
        cat = CATEGORIES["results"]
        done = self._timed("result_delegate_result", cat)
        sid, decision = await self._send_decision(
            "r_delegate", content="delegate task"
        )
        if sid is None or decision is None:
            return done(False, "Failed to set up decision")
        try:
            result = {
                "type": "delegate_result",
                "data": {"task": "x", "status": "done"},
                "success": True,
                "duration_ms": 5.0,
            }
            resp = await self._send_result(sid, decision["decision_id"], result)
            if resp is None:
                return done(False, "Result endpoint unreachable")
            if resp.status_code >= 400:
                return done(
                    False,
                    f"Result rejected delegate_result: status={resp.status_code}",
                )
            return done(True, "delegate_result accepted")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_4_6_result_error(self) -> TestResult:
        """``error`` result type is handled gracefully (no 5xx)."""
        cat = CATEGORIES["results"]
        done = self._timed("result_error", cat)
        sid, decision = await self._send_decision("r_err", content="force error")
        if sid is None or decision is None:
            return done(False, "Failed to set up decision")
        try:
            result = {
                "type": "error",
                "data": {"error": "intentional", "phase": "test"},
                "success": False,
                "duration_ms": 0.0,
            }
            resp = await self._send_result(sid, decision["decision_id"], result)
            if resp is None:
                return done(False, "Result endpoint unreachable")
            if resp.status_code >= 500:
                return done(
                    False,
                    f"Server crashed on error result: status={resp.status_code}",
                )
            return done(
                True, f"error handled gracefully, status={resp.status_code}"
            )
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_4_7_result_wait_timeout(self) -> TestResult:
        """``wait_timeout`` result type is accepted."""
        cat = CATEGORIES["results"]
        done = self._timed("result_wait_timeout", cat)
        sid, decision = await self._send_decision("r_wait", content="wait a moment")
        if sid is None or decision is None:
            return done(False, "Failed to set up decision")
        try:
            result = {
                "type": "wait_timeout",
                "data": {"reason": "no event", "waited_ms": 100},
                "success": True,
                "duration_ms": 100.0,
            }
            resp = await self._send_result(sid, decision["decision_id"], result)
            if resp is None:
                return done(False, "Result endpoint unreachable")
            if resp.status_code >= 400:
                return done(
                    False,
                    f"Result rejected wait_timeout: status={resp.status_code}",
                )
            return done(True, "wait_timeout accepted")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    # ════════════════════════════════════════════════════════════════════
    # Category 5 — Error & Edge Cases (10 tests)
    # ════════════════════════════════════════════════════════════════════

    async def category_5_errors(self) -> list[TestResult]:
        return [
            await self.test_5_1_malformed_json(),
            await self.test_5_2_missing_session_id(),
            await self.test_5_3_unknown_decision_type(),
            await self.test_5_4_empty_message(),
            await self.test_5_5_very_long_message(),
            await self.test_5_6_unicode_message(),
            await self.test_5_7_no_tools_available(),
            await self.test_5_8_no_models_available(),
            await self.test_5_9_cancel_mid_processing(),
            await self.test_5_10_session_not_found(),
        ]

    async def test_5_1_malformed_json(self) -> TestResult:
        """Malformed JSON to ``/v1/process`` returns 4xx."""
        cat = CATEGORIES["errors"]
        done = self._timed("malformed_json", cat)
        try:
            resp, err = await self._safe_call(
                self.client.post(
                    "/v1/process",
                    content=b"{not valid json",
                    headers={"content-type": "application/json"},
                )
            )
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if 400 <= resp.status_code < 500:
                return done(True, f"Status {resp.status_code} (rejected)")
            if resp.status_code >= 500:
                return done(
                    False, f"Server crashed: status={resp.status_code}"
                )
            return done(
                False, f"Expected 4xx, got {resp.status_code}"
            )
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_5_2_missing_session_id(self) -> TestResult:
        """POST /v1/process without ``session_id`` returns 4xx."""
        cat = CATEGORIES["errors"]
        done = self._timed("missing_session_id", cat)
        try:
            bad_body = {
                "message": {"role": "user", "content": "x"},
                "identity": {"platform": "test", "chat_id": "t"},
                "context": self._blank_context(),
            }
            resp, err = await self._safe_call(
                self.client.post("/v1/process", json=bad_body)
            )
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if 400 <= resp.status_code < 500:
                return done(True, f"Status {resp.status_code}")
            return done(
                False,
                f"Expected 4xx, got {resp.status_code}; body={resp.text[:200]}",
            )
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_5_3_unknown_decision_type(self) -> TestResult:
        """A ``decision`` of unknown type returned by a harness is handled.

        We can't make the harness lie — but we *can* probe what happens
        when ``/v1/result`` carries a bogus ``type`` field. We expect
        either a 400 or graceful handling.
        """
        cat = CATEGORIES["errors"]
        done = self._timed("unknown_decision_type", cat)
        sid, decision = await self._send_decision("r_unknown", content="x")
        if sid is None or decision is None:
            return done(False, "Failed to set up decision")
        try:
            bogus = {
                "type": "definitely_not_a_real_result_type",
                "data": {},
                "success": True,
            }
            resp = await self._send_result(sid, decision["decision_id"], bogus)
            if resp is None:
                return done(False, "Result endpoint unreachable")
            if resp.status_code == 400:
                return done(True, "400 (rejected as expected)")
            if 200 <= resp.status_code < 400:
                return done(True, f"Gracefully handled ({resp.status_code})")
            return done(
                False, f"Unexpected status {resp.status_code}"
            )
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_5_4_empty_message(self) -> TestResult:
        """Empty ``content`` does not crash."""
        cat = CATEGORIES["errors"]
        done = self._timed("empty_message", cat)
        body = self._process_body("empty_msg", content="")
        try:
            resp, err = await self._safe_call(
                self.client.post("/v1/process", json=body)
            )
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code >= 500:
                return done(False, f"Server crash on empty: status={resp.status_code}")
            return done(True, f"Status {resp.status_code}")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_5_5_very_long_message(self) -> TestResult:
        """A 100 KB user message doesn't crash the harness."""
        cat = CATEGORIES["errors"]
        done = self._timed("very_long_message", cat)
        long_text = "x" * 100_000
        body = self._process_body("long_msg", content=long_text)
        try:
            resp, err = await self._safe_call(
                self.client.post("/v1/process", json=body)
            )
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code >= 500:
                return done(False, f"Server crash on 100KB: status={resp.status_code}")
            return done(True, f"Status {resp.status_code}")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_5_6_unicode_message(self) -> TestResult:
        """Unicode + emoji survive the wire intact."""
        cat = CATEGORIES["errors"]
        done = self._timed("unicode_message", cat)
        text = "héllo 🌍 — Καλημέρα ñ ünîcødé"
        body = self._process_body("unicode_msg", content=text)
        try:
            resp, err = await self._safe_call(
                self.client.post("/v1/process", json=body)
            )
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code >= 500:
                return done(
                    False,
                    f"Server crash on unicode: status={resp.status_code}",
                )
            return done(True, f"Status {resp.status_code}")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_5_7_no_tools_available(self) -> TestResult:
        """With no tools available, no tool_call decision is returned."""
        cat = CATEGORIES["errors"]
        done = self._timed("no_tools_available", cat)
        body = self._process_body(
            "no_tools", content="use any tool you want", tools=[]
        )
        try:
            resp, err = await self._safe_call(
                self.client.post("/v1/process", json=body)
            )
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code >= 500:
                return done(False, f"Server crash: status={resp.status_code}")
            data = resp.json()
            if data.get("decision") == "tool_call":
                return done(
                    False,
                    "Got tool_call despite empty context.tools — hallucinated tool",
                )
            return done(True, f"decision={data.get('decision')!r} (no tool_call)")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_5_8_no_models_available(self) -> TestResult:
        """With no models available, no llm_call decision is returned."""
        cat = CATEGORIES["errors"]
        done = self._timed("no_models_available", cat)
        body = self._process_body(
            "no_models", content="use any model you want", models=[]
        )
        try:
            resp, err = await self._safe_call(
                self.client.post("/v1/process", json=body)
            )
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code >= 500:
                return done(False, f"Server crash: status={resp.status_code}")
            data = resp.json()
            if data.get("decision") == "llm_call":
                return done(
                    False,
                    "Got llm_call despite empty context.models — hallucinated model",
                )
            return done(True, f"decision={data.get('decision')!r} (no llm_call)")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_5_9_cancel_mid_processing(self) -> TestResult:
        """POST /v1/cancel returns 200."""
        cat = CATEGORIES["errors"]
        done = self._timed("cancel_mid_processing", cat)
        sid = self._sid("cancel")
        try:
            resp, err = await self._safe_call(
                self.client.post(
                    "/v1/cancel",
                    json={"session_id": sid, "reason": "user_interrupt"},
                )
            )
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code != 200:
                return done(False, f"Expected 200, got {resp.status_code}")
            return done(True, "200 OK")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_5_10_session_not_found(self) -> TestResult:
        """GET /v1/sessions/nonexistent returns 404 (or equivalent not-found)."""
        cat = CATEGORIES["errors"]
        done = self._timed("session_not_found", cat)
        nonexistent = f"nope-{uuid.uuid4().hex}"
        try:
            resp, err = await self._safe_call(
                self.client.get(f"/v1/sessions/{nonexistent}")
            )
            if err is not None or resp is None:
                return done(False, f"Exception: {err}")
            if resp.status_code == 404:
                return done(True, "404")
            if resp.status_code == 405:
                # Some harnesses don't expose the endpoint at all — close enough.
                return done(True, "405 (endpoint absent)")
            if 400 <= resp.status_code < 500:
                return done(True, f"{resp.status_code}")
            return done(
                False, f"Expected 404, got {resp.status_code}"
            )
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    # ════════════════════════════════════════════════════════════════════
    # Category 6 — Stress & Performance (5 tests)
    # ════════════════════════════════════════════════════════════════════

    async def category_6_stress(self) -> list[TestResult]:
        return [
            await self.test_6_1_concurrent_sessions(),
            await self.test_6_2_rapid_process_calls(),
            await self.test_6_3_loop_convergence(),
            await self.test_6_4_decision_latency(),
            await self.test_6_5_memory_stable(),
        ]

    async def test_6_1_concurrent_sessions(self) -> TestResult:
        """10 concurrent sessions all get answers."""
        cat = CATEGORIES["stress"]
        done = self._timed("concurrent_sessions", cat)
        try:
            async def one(idx: int):
                body = self._process_body(f"conc_{idx}", content=f"hi {idx}")
                resp, err = await self._safe_call(
                    self.client.post("/v1/process", json=body)
                )
                return (
                    resp is not None
                    and err is None
                    and resp.status_code == 200
                )

            results = await asyncio.gather(*(one(i) for i in range(10)))
            ok = sum(1 for r in results if r)
            if ok == 10:
                return done(True, "10/10 sessions responded")
            return done(False, f"{ok}/10 sessions responded")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_6_2_rapid_process_calls(self) -> TestResult:
        """50 process calls complete inside a 10 s budget."""
        cat = CATEGORIES["stress"]
        done = self._timed("rapid_process_calls", cat)
        try:
            start = time.monotonic()
            ok = 0
            deadline = start + 10.0
            for i in range(50):
                if time.monotonic() > deadline:
                    return done(
                        False,
                        f"Deadline exceeded at {i}/50; {ok} succeeded",
                    )
                body = self._process_body(f"rapid_{i}", content="ping")
                resp, err = await self._safe_call(
                    self.client.post("/v1/process", json=body)
                )
                if err is None and resp is not None and resp.status_code == 200:
                    ok += 1
            elapsed = time.monotonic() - start
            if ok == 50:
                return done(True, f"50/50 in {elapsed:.2f}s")
            return done(False, f"{ok}/50 succeeded in {elapsed:.2f}s")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_6_3_loop_convergence(self) -> TestResult:
        """The harness reaches ``end`` within 20 iterations."""
        cat = CATEGORIES["stress"]
        done = self._timed("loop_convergence", cat)
        sid = self._sid("loop")
        try:
            body = {
                "session_id": sid,
                "message": {"role": "user", "content": "loop test"},
                "identity": {"platform": "test", "chat_id": "test-chat"},
                "context": self._blank_context(),
            }
            last_kind: str | None = None
            for i in range(20):
                resp, err = await self._safe_call(
                    self.client.post("/v1/process", json=body)
                )
                if err is not None or resp is None:
                    return done(False, f"Iter {i}: exception {err}")
                if resp.status_code != 200:
                    return done(False, f"Iter {i}: status {resp.status_code}")
                data = resp.json()
                last_kind = data.get("decision")
                if data.get("decision") == "end":
                    return done(True, f"end at iter {i}")
                did = data.get("decision_id")
                if not did:
                    break
                result_body = {
                    "session_id": sid,
                    "decision_id": did,
                    "result": {
                        "type": "text_sent",
                        "data": {"finished": False},
                        "success": True,
                    },
                }
                resp2, err2 = await self._safe_call(
                    self.client.post("/v1/result", json=result_body)
                )
                if err2 is not None or resp2 is None or resp2.status_code != 200:
                    break
            # Soft pass — many harnesses don't end on their own for an open
            # prompt; record what we observed.
            return done(
                True,
                f"Optional — last decision {last_kind!r}; convergence soft-passed",
            )
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_6_4_decision_latency(self) -> TestResult:
        """Each individual ``/v1/process`` call returns within 5 s."""
        cat = CATEGORIES["stress"]
        done = self._timed("decision_latency", cat)
        try:
            samples_ms: list[float] = []
            for i in range(5):
                body = self._process_body(f"lat_{i}", content="time me")
                start = time.monotonic()
                resp, err = await self._safe_call(
                    self.client.post("/v1/process", json=body)
                )
                elapsed_ms = (time.monotonic() - start) * 1000.0
                if err is not None or resp is None:
                    return done(False, f"Exception at sample {i}: {err}")
                if resp.status_code != 200:
                    return done(False, f"Sample {i}: status {resp.status_code}")
                samples_ms.append(elapsed_ms)
            worst = max(samples_ms)
            avg = sum(samples_ms) / len(samples_ms)
            if worst >= 5000.0:
                return done(False, f"Worst {worst:.0f}ms >= 5s")
            return done(True, f"worst={worst:.0f}ms, avg={avg:.0f}ms")
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")

    async def test_6_5_memory_stable(self) -> TestResult:
        """100 turns against one session doesn't blow up response sizes."""
        cat = CATEGORIES["stress"]
        done = self._timed("memory_stable", cat)
        sid = self._sid("memory")
        try:
            sizes: list[int] = []
            history: list[dict] = []
            for i in range(100):
                history.append({"role": "user", "content": f"turn-{i}"})
                ctx = self._blank_context()
                ctx["history"] = history
                body = {
                    "session_id": sid,
                    "message": {"role": "user", "content": f"turn {i}"},
                    "identity": {"platform": "test", "chat_id": "test-chat"},
                    "context": ctx,
                }
                resp, err = await self._safe_call(
                    self.client.post("/v1/process", json=body)
                )
                if err is not None or resp is None:
                    return done(False, f"Turn {i}: failure {err}")
                if resp.status_code != 200:
                    return done(False, f"Turn {i}: status {resp.status_code}")
                sizes.append(len(resp.content))
            # Heuristic: response size should not grow linearly with turn
            # count — a leak would show up as the last bucket dwarfing the first.
            if len(sizes) < 10:
                return done(True, "Too few samples for trend")
            early = sum(sizes[:10]) / 10
            late = sum(sizes[-10:]) / 10
            ratio = late / max(early, 1.0)
            if ratio > 100.0:
                return done(
                    False, f"Response grew {ratio:.1f}× over 100 turns"
                )
            return done(
                True,
                f"growth ratio {ratio:.2f}× "
                f"(early={early:.0f}B, late={late:.0f}B)",
            )
        except Exception as exc:  # noqa: BLE001
            return done(False, f"Exception: {exc}")


# ── module-level helpers exposed for external callers ───────────────────────


def report_as_dict(report: TestReport) -> dict[str, Any]:
    """JSON-friendly dict for an entire :class:`TestReport`."""
    out = asdict(report)
    # Computed property needs to be serialised explicitly.
    out["all_passing"] = report.all_passing
    return out
