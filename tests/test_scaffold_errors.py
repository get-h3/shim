"""DF-H3-SHIM-FOREMAN-3 — the scaffolded py harness's error contract.

``hermes-h3 scaffold --lang py`` copies ``src/h3_shim/templates/py/main.py``,
so this module exercises the TEMPLATE's own FastAPI app in-process (no server,
no subprocess): ``TestClient(main.app)``.

The H3 protocol requires a rejected request body to answer with the standard
error envelope (``protocol/schemas/v1/error-response.json``; ``x-h3-errors``
names ``INVALID_REQUEST`` at HTTP 400 for "Malformed JSON or missing required
fields"). FastAPI's default is HTTP 422 ``{"detail": [...]}``, which the
compliance battery cannot catch — its ``errors`` category accepts ANY 4xx. So
these tests are the only thing standing between the template and a silent
regression back to the framework default.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from h3_shim.templates.py import main

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The protocol repo is a sibling checkout (get-h3/{shim,protocol}). Resolve it
# from this file first, allow an explicit override, and only then fall back to
# the canonical absolute path.
_PROTOCOL_SCHEMA = Path("schemas") / "v1" / "error-response.json"


def _protocol_schema_path() -> Path | None:
    """Locate the protocol's error-response schema, or ``None`` if absent."""
    candidates: list[Path] = []
    override = os.environ.get("H3_PROTOCOL_DIR")
    if override:
        candidates.append(Path(override) / _PROTOCOL_SCHEMA)
    candidates.append(_REPO_ROOT.parent / "protocol" / _PROTOCOL_SCHEMA)
    candidates.append(Path("/home/kara/get-h3/protocol") / _PROTOCOL_SCHEMA)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


@pytest.fixture()
def client() -> TestClient:
    """A TestClient over the template's app (fresh app-level state per use)."""
    return TestClient(main.app)


def _valid_process_body() -> dict[str, Any]:
    """A ProcessRequest carrying every schema-required field, nested included."""
    return {
        "session_id": "df-h3-shim-foreman-3",
        "message": {
            "role": "user",
            "content": "hello",
            "timestamp": "2026-09-18T12:00:00+00:00",
        },
        "identity": {
            "platform": "cli",
            "chat_id": "chat-1",
            "thread_id": "thread-1",
            "user_name": "kara",
            "user_id": "u-1",
        },
        "context": {
            "history": [],
            "tools": [],
            "models": [],
            "config": {},
            "session_state": {},
        },
    }


def _assert_invalid_request_envelope(body: dict[str, Any]) -> None:
    """Assert the H3 standard error envelope, not FastAPI's 422 detail body."""
    assert "detail" not in body, f"FastAPI default body leaked through: {body}"
    error = body["error"]
    assert error["code"] == "INVALID_REQUEST"
    assert isinstance(error["message"], str) and error["message"]
    errors = error["details"]["errors"]
    assert isinstance(errors, list) and errors, f"empty pydantic error list: {body}"


class TestScaffoldErrorContract:
    """Malformed / schema-invalid bodies get HTTP 400 + INVALID_REQUEST."""

    def test_missing_required_fields_is_400_invalid_request(
        self, client: TestClient
    ) -> None:
        """``{}`` (every required field missing) → 400, not 422."""
        resp = client.post("/v1/process", json={})
        assert resp.status_code == 400, f"status={resp.status_code} body={resp.text}"
        _assert_invalid_request_envelope(resp.json())

    def test_malformed_json_is_400_invalid_request(self, client: TestClient) -> None:
        """A body that does not parse as JSON → 400, same envelope."""
        resp = client.post(
            "/v1/process",
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400, f"status={resp.status_code} body={resp.text}"
        _assert_invalid_request_envelope(resp.json())
        # The decode failure is surfaced, not swallowed into a generic message.
        errors = resp.json()["error"]["details"]["errors"]
        assert errors[0]["type"] == "json_invalid"

    def test_valid_process_request_still_200_text_decision(
        self, client: TestClient
    ) -> None:
        """The 400 handler must not swallow good requests."""
        resp = client.post("/v1/process", json=_valid_process_body())
        assert resp.status_code == 200, f"status={resp.status_code} body={resp.text}"
        body = resp.json()
        assert body["decision"] == "text"
        assert body["text"]["content"] == "Echo: hello"

    def test_deliberate_404_is_untouched(self, client: TestClient) -> None:
        """The harness's own 4xx (HTTPException) keeps its existing response."""
        resp = client.post("/v1/cancel", json={"session_id": "no-such-session"})
        assert resp.status_code == 404, f"status={resp.status_code} body={resp.text}"
        assert resp.json() == {"detail": "Session not found"}

    def test_400_body_matches_protocol_error_response_schema(
        self, client: TestClient
    ) -> None:
        """The 400 body validates against the protocol's error-response schema."""
        jsonschema = pytest.importorskip("jsonschema")
        schema_path = _protocol_schema_path()
        if schema_path is None:
            pytest.skip("h3 protocol error-response schema not found (sibling repo)")
        schema = json.loads(schema_path.read_text())

        for resp in (
            client.post("/v1/process", json={}),
            client.post(
                "/v1/process",
                content=b"{not json",
                headers={"Content-Type": "application/json"},
            ),
        ):
            assert resp.status_code == 400
            jsonschema.validate(instance=resp.json(), schema=schema)
