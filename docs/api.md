# H3 Shim — Programmatic API Reference

This document is the developer-facing reference for the public surface of
`hermes-h3-shim`. It covers the four main classes:

| Class | Module | Purpose |
|---|---|---|
| `H3Client` | `h3_shim.client` | REST client that talks to an H3 harness (`/v1/process`, `/v1/result`, …) |
| `H3Loader` | `h3_shim.loader` | Discovers harnesses from config, health-checks them, routes sessions |
| `H3ShimLoop` | `h3_shim.shim_loop` | Drives a single session through the process → execute → result loop |
| `H3TestBattery` | `h3_shim.test_battery` | 45-test compliance battery that verifies ANY harness against the H3 protocol |

All networking classes are `asyncio`-native: methods that talk to a harness
must be awaited. Pydantic models for requests/responses live in
`h3_shim.protocol` (generated from the `get-h3/protocol` OpenAPI spec).

---

## Wire protocol (REST / JSON)

Harness communication is plain HTTP + JSON. The full schema lives in
`get-h3/protocol` (OpenAPI 3.1); the examples below are the concrete shapes
a harness must accept and return. **The `Decision` discriminator is a
top-level `decision` field — NOT `type`** — and the sub-payload for that
kind nests under the same name (`llm_call`, `text`, `end`, …).

### POST /v1/process — request

```json
{
  "session_id": "sess-7f3a9c",
  "message": {
    "role": "user",
    "content": "Book a flight to Medellín",
    "timestamp": "2026-08-20T14:03:11Z"
  },
  "identity": {
    "platform": "telegram",
    "chat_id": "-1001234567890",
    "user_id": "6849342682"
  },
  "context": {
    "history": [],
    "tools": [],
    "models": [],
    "memory": "",
    "skills": [],
    "config": {},
    "session_state": {}
  }
}
```

### POST /v1/process — Decision response (`text`)

The harness answers a process (or result) request with a `Decision`:

```json
{
  "decision": "text",
  "decision_id": "d_0042",
  "text": {
    "content": "On it — checking flights now.",
    "finished": false
  }
}
```

### POST /v1/process — Decision response (`end`)

```json
{
  "decision": "end",
  "decision_id": "d_0043",
  "end": {
    "reason": "task_complete",
    "summary": "Flight AV-0142 booked for Friday."
  }
}
```

Other decision kinds use the identical shape:
`{"decision": "tool_call", "tool_call": {...}}`,
`{"decision": "llm_call", "llm_call": {...}}`,
`{"decision": "wait", "wait": {...}}`,
`{"decision": "delegate", "delegate": {...}}`.
The shim loop executes each decision locally and POSTs the result back;
the harness replies with the next `Decision`, until it sends
`"decision": "end"`.

### POST /v1/result — request

```json
{"session_id": "sess-7f3a9c", "decision_id": "d_0042", "result": {"type": "text_sent", "data": {"content": "On it — checking flights now."}, "success": true}}
```

`/v1/result` returns the harness's next `Decision` — the loop repeats until
an `end` decision arrives.

---

## H3Client

`H3Client(endpoint, transport="rest", timeout_ms=30000, hermes_token=None, hermes_identity=None, protocol_version="1.0")`

Constructor parameters:

| Parameter | Default | Description |
|---|---|---|
| `endpoint` | *(required)* | Base URL of the H3 harness, e.g. `http://localhost:9191` |
| `transport` | `"rest"` | Transport to use (`"rest"` is the implemented one) |
| `timeout_ms` | `30000` | Per-request timeout in milliseconds |
| `hermes_token` | `None` | Bearer token; falls back to the `H3_API_KEY` env var when omitted |
| `hermes_identity` | `None` | Value for the `H3-Hermes-Identity` header |
| `protocol_version` | `"1.0"` | Value for the `H3-Protocol-Version` header (sent when auth is present) |

### Methods

```python
async def health(self) -> HealthResponse
```

GET `/v1/health`. Returns the parsed `HealthResponse` (protocol version,
uptime, etc.). Raises `httpx.HTTPStatusError` on a non-2xx status.

```python
async def process(self, session_id: str, message: Message,
                  identity: Identity, context: Context) -> Decision
```

POST `/v1/process` with the session's message, identity and context. Returns
the harness's `Decision` (e.g. `DecisionType.END`). On a harness timeout it
returns a synthetic END decision with `EndReason.TIMEOUT` instead of raising.

```python
async def result(self, session_id: str, decision_id: str,
                 result: ExecutionResult) -> Decision
```

POST `/v1/result` with the execution result of a decision. Returns the next
`Decision` from the harness (loop continues until END). Timeouts behave like
`process()` — synthetic END with `EndReason.TIMEOUT`.

```python
async def cancel(self, session_id: str, reason: str = "user_interrupt") -> CancelResponse
```

POST `/v1/cancel` to abort an in-flight session.

```python
async def close(self)
```

Close the underlying HTTP client. Always call this when done.

### Quickstart

```python
from h3_shim.client import H3Client

client = H3Client(endpoint="http://localhost:9191", timeout_ms=10000)
health = await client.health()  # discover the harness's protocol state
print(health.version, health.uptime_seconds)
await client.close()
```

---

## H3Loader

`H3Loader(config: dict)`

Discovers harnesses from `config["harnesses"]` and routes sessions via
`config["sessions"]`. The special harness name `"native"` is always
available and maps to Hermes' own agent loop (no HTTP client).

Notable attributes after construction:

| Attribute | Type | Description |
|---|---|---|
| `harnesses` | `dict[str, H3Client]` | Harness name → client (`None`-like absent for `native`) |
| `default_harness` | `str` | Fallback harness name, default `"native"` |
| `max_consecutive_failures` | `int` | Consecutive failures before a harness is marked unhealthy (default 3) |

### Methods

```python
async def resolve(self, platform: str, chat_id: str, session_id: str) -> str | None
```

Resolve the harness name for a session from the routing config. Returns
`None` when no route matches.

```python
def route_session(self, session_id: str, harness_name: str) -> None
def get_session_harness(self, session_id: str) -> str | None
```

Manually pin / look up the harness for a session.

```python
async def start_health_checks(self) -> None
async def stop_health_checks(self) -> None
```

Start/stop the background health-check loop (periodically probes every
harness, reroutes sessions away from failed harnesses).

```python
async def close(self) -> None
```

Stop health checks and close all harness clients.

### Usage

```python
from h3_shim.loader import H3Loader

loader = H3Loader(config)  # config = {"harnesses": {...}, "sessions": {...}}
await loader.start_health_checks()

harness = loader.resolve("telegram", "-100", "84802")
client = loader.harnesses.get(harness)  # None for native

await loader.close()
```

---

## H3ShimLoop

`H3ShimLoop(client, session_id, context, max_iterations=50, identity=None, llm_provider=None, on_text=None)`

Drives one H3 session through the process / result loop. `client` is the
`H3Client` talking to the harness, `session_id` is the stable session
identifier, and `context` is the per-session `Context` (history, tools,
models, memory, …). `max_iterations` (default 50) caps `/v1/result`
round-trips per `run()` — mirroring the canonical Hermes agent loop. When
`identity` is omitted a placeholder `("shim", session_id)` identity is used.

Optional hooks let the embedding host supply the two things the loop
itself does not own — the LLM client and the user-facing transport:

| Parameter | Type | Behaviour |
|---|---|---|
| `llm_provider` | `Callable[[str, dict], str]` | Executes `LLM_CALL` decisions. Called as `llm_provider(prompt, context)` where `prompt` is the flattened message list (`role: content` lines) and `context` carries `model`, `system_prompt`, `temperature`, `max_tokens`, `messages`, `session_id`. Its return value becomes a successful `llm_response` result with `data.content`. **Without it, `LLM_CALL` decisions are refused** — the loop returns an `error` result (`"LLM not configured: no LLM provider wired in this shim"`) rather than fabricate model output. |
| `on_text` | `Callable[[str], None]` | Receives the content of every `TEXT` decision so the host can deliver it to the user (chat gateway, terminal, …). |

### Methods

```python
def register_tool(self, name: str, fn: Callable[..., object]) -> None
```

Register `fn` as the implementation for tool `name`. When the harness issues
a `TOOL_CALL` decision, `_execute_tool` invokes `fn(**params)` and treats the
return value as the tool output.

```python
async def run(self, message: Message) -> str
```

Execute the full loop for one user message: POST `/v1/process`, execute the
returned decision (tool call, LLM call, text, wait, delegate), POST the
`/v1/result`, repeat until the harness returns END or `max_iterations` is
reached. **Returns the `EndReason` string** of the terminating END decision
(`"task_complete"`, `"error"`, `"timeout"`, …) — not the assistant text.
Final assistant text is delivered incrementally through the `on_text`
callback as the harness emits `TEXT` decisions.

```python
async def register_tool(...)  # see above
```

---

## H3TestBattery

`H3TestBattery(endpoint, transport="rest", config=None)`

The compliance battery — 45 tests across 6 categories (health, process
flows, decision types, result handling, error & edge cases, stress). Runs
against ANY harness endpoint without code changes. The 45th test
(`test_5_11_session_status_completed`, added in GAP-045) drives a full
process → result(END) loop and asserts a finished session reports status
`completed` when the harness tracks session state.

### Methods

```python
async def probe(self) -> None
```

Pre-flight check: GET `/v1/health` must return 200 with an H3-shaped JSON
payload. Raises `NotH3EndpointError` on anything clearly not an H3 endpoint
(wrong status, non-JSON body, missing fields, connection refused).

```python
async def run_all(self) -> TestReport
```

Run every category sequentially and assemble a `TestReport` (see
`TestResult` / `TestReport` below; `report.all_passing` tells you whether
all 45 passed).

### Result types

```python
class TestResult:        # one test outcome
    name: str
    category: str
    passed: bool
    detail: str
    duration_ms: float

class TestReport:        # aggregate
    results: list[TestResult]
    duration_ms: float
    def all_passing(self) -> bool
```

### Usage

```python
from h3_shim.test_battery import H3TestBattery

battery = H3TestBattery(endpoint="http://localhost:9191")
report = await battery.run_all()
assert report.all_passing(), "harness is not H3-compliant"
```

CLI equivalent: `h3-test --endpoint http://localhost:9191` (exit code 0 =
compliant, 1 = compliance failure, 2 = not an H3 endpoint).

---

## Notes

- The battery is THE gate for this package: the 3 SDK echo examples
  (Go / Python / TypeScript) must all pass 45/45 before release.
- Request/response types (`Message`, `Identity`, `Context`, `Decision`,
  `ExecutionResult`, `HealthResponse`, `CancelResponse`) are Pydantic models
  in `h3_shim.protocol` — see `docs/integration.md` for the CLI/plugin
  integration story.
