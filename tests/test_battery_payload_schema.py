"""The battery's own default ``/v1/process`` payload must satisfy the schema.

H3-GAP-082: :meth:`H3TestBattery._blank_context` /
:meth:`H3TestBattery._process_body` build the "minimal valid" request that the
whole compliance gate runs on, yet the shape they emitted was the one shape the
authored schemas reject — ``config: {}``, ``session_state: {}``, a message with
no ``timestamp`` and an identity with no ``user_name`` / ``user_id``.

These tests validate the REAL default payload (no hand-copied fixture) against
the REAL authored schemas: ``schemas/v1/process-request.json`` plus
``schemas/v1/common.json``, whose relative ``$ref`` is resolved through a
:class:`referencing.Registry` keyed on ``common.json``'s ``$id``.  The schema
files are resolved from the sibling ``protocol`` checkout the same way
:func:`h3_shim.upgrade_check._devtree_versions_yaml_path` resolves
``versions.yaml`` — anchored on the installed package file, so the tests do not
depend on the directory pytest happens to be invoked from.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

import h3_shim
from h3_shim.test_battery import H3TestBattery

#: Never contacted — ``_blank_context`` / ``_process_body`` are pure helpers.
DUMMY_ENDPOINT = "http://localhost:1"

#: Required leaf properties of ``ProcessRequest``, straight from
#: ``process-request.json`` + ``common.json``.  ``None`` marks a leaf.
REQUIRED_LEAVES: dict[str, Any] = {
    "session_id": None,
    "message": {
        "role": None,
        "content": None,
        "timestamp": None,
    },
    "identity": {
        "platform": None,
        "chat_id": None,
        "user_name": None,
        "user_id": None,
    },
    "context": {
        "history": None,
        "tools": None,
        "models": None,
        "config": {
            "max_iterations": None,
            "timeout_seconds": None,
        },
        "session_state": {
            "turn_count": None,
            "total_tool_calls": None,
            "total_llm_calls": None,
            "cost_so_far": None,
            "started_at": None,
        },
    },
}


# ── helpers ─────────────────────────────────────────────────────────────────


def _devtree_schema_dir() -> Path:
    """Sibling protocol checkout's ``schemas/v1`` directory.

    Mirrors :func:`h3_shim.upgrade_check._devtree_versions_yaml_path`
    (``Path(__file__).resolve().parents[3] / "protocol" / ...``): the walk is
    anchored on the installed package file, not on pytest's cwd.
    """
    return Path(h3_shim.__file__).resolve().parents[3] / "protocol" / "schemas" / "v1"


def _missing_leaves(payload: Any, spec: dict[str, Any], prefix: str = "") -> list[str]:
    """Every required leaf of *spec* absent from *payload*, as dotted paths."""
    missing: list[str] = []
    for key, sub in spec.items():
        path = f"{prefix}{key}"
        if not isinstance(payload, dict) or key not in payload:
            missing.append(path)
            continue
        if isinstance(sub, dict):
            missing.extend(_missing_leaves(payload[key], sub, f"{path}."))
    return missing


def _default_body() -> dict[str, Any]:
    """The battery's default ``/v1/process`` body, exactly as the gate sends it."""
    return H3TestBattery(DUMMY_ENDPOINT)._process_body("schema_default")


def _errors(validator: Draft202012Validator, payload: Any) -> list[str]:
    """Sorted ``"<json path>: <message>"`` strings for *payload*."""
    out = []
    for err in validator.iter_errors(payload):
        path = "/".join(str(p) for p in err.path) or "<root>"
        out.append(f"{path}: {err.message}")
    return sorted(out)


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def schema_paths() -> tuple[Path, Path]:
    """``(process-request.json, common.json)`` from the sibling checkout."""
    schema_dir = _devtree_schema_dir()
    request_path = schema_dir / "process-request.json"
    common_path = schema_dir / "common.json"
    if not request_path.is_file() or not common_path.is_file():
        pytest.skip(
            "sibling protocol checkout not present — expected "
            f"{request_path} and {common_path} "
            "(clone get-h3/protocol next to this repo)"
        )
    return request_path, common_path


@pytest.fixture(scope="module")
def validator(schema_paths: tuple[Path, Path]) -> Draft202012Validator:
    """Draft 2020-12 validator for ``ProcessRequest`` with ``common.json`` reachable.

    ``process-request.json`` references ``common.json#/definitions/...``
    relatively; the registry is keyed on ``common.json``'s own ``$id`` so the
    ``$ref`` resolves instead of raising ``Unresolvable`` (or, worse, silently
    validating nothing).
    """
    request_path, common_path = schema_paths
    request_schema = json.loads(request_path.read_text())
    common_schema = json.loads(common_path.read_text())
    registry = Registry().with_resource(
        common_schema["$id"], Resource.from_contents(common_schema)
    )
    return Draft202012Validator(request_schema, registry=registry)


# ── the default payload carries every schema-required leaf ──────────────────


def test_default_process_body_has_every_required_leaf() -> None:
    body = _default_body()
    assert _missing_leaves(body, REQUIRED_LEAVES) == []

    # Leaf-shape sanity: the values the schema constrains must be the right kind.
    assert body["message"]["role"] == "user"  # Message.role is enum ["user"]
    assert isinstance(body["message"]["timestamp"], str)
    assert isinstance(body["context"]["config"]["max_iterations"], int)
    assert isinstance(body["context"]["config"]["timeout_seconds"], int)
    assert isinstance(body["context"]["session_state"]["turn_count"], int)
    assert isinstance(body["context"]["session_state"]["cost_so_far"], (int, float))


def test_default_timestamps_are_iso8601_utc() -> None:
    body = _default_body()
    for value in (
        body["message"]["timestamp"],
        body["context"]["session_state"]["started_at"],
    ):
        parsed = datetime.fromisoformat(value)
        assert parsed.tzinfo is not None, f"timestamp is not timezone-aware: {value!r}"
        assert parsed.utcoffset().total_seconds() == 0, value


def test_default_payload_validates_against_authored_schema(
    validator: Draft202012Validator,
) -> None:
    assert _errors(validator, _default_body()) == []


def test_default_blank_context_validates_in_a_minimal_body(
    validator: Draft202012Validator,
) -> None:
    """``_blank_context()`` alone — its ``config`` / ``session_state`` were the gap."""
    ctx = H3TestBattery._blank_context()
    assert ctx["config"] != {}
    assert ctx["session_state"] != {}
    body = {
        "session_id": "ctx-only",
        "message": {
            "role": "user",
            "content": "x",
            "timestamp": datetime.now().astimezone().isoformat(),
        },
        "identity": {
            "platform": "test",
            "chat_id": "test-chat",
            "user_name": "h3-test",
            "user_id": "h3-test-user",
        },
        "context": ctx,
    }
    assert _errors(validator, body) == []


def test_explicit_identity_override_is_still_respected(
    validator: Draft202012Validator,
) -> None:
    """The default identity is a fallback only; explicit callers win."""
    explicit = {
        "platform": "telegram",
        "chat_id": "-100",
        "user_name": "someone",
        "user_id": "42",
    }
    body = H3TestBattery(DUMMY_ENDPOINT)._process_body("explicit", identity=explicit)
    assert body["identity"] == explicit
    assert _errors(validator, body) == []


# ── the validator itself is not a no-op ─────────────────────────────────────


def test_validator_rejects_each_missing_required_leaf(
    validator: Draft202012Validator,
) -> None:
    """Control: the validator must FAIL on the very shapes GAP-082 was about.

    ``context/config`` and ``context/session_state`` live in ``common.json``,
    so errors naming those paths also prove the relative ``$ref`` resolved.
    """
    good = _default_body()
    assert _errors(validator, good) == []

    # ``(parent path, leaf, how)`` — the pre-fix shapes, one at a time.  A
    # missing property is reported against its PARENT path with the leaf named
    # in the message; an emptied object is reported against the object path.
    cases: list[tuple[str, str, str]] = [
        ("message", "timestamp", "del"),
        ("identity", "user_name", "del"),
        ("identity", "user_id", "del"),
        ("context", "config", "empty"),
        ("context", "session_state", "empty"),
    ]
    for parent, leaf, how in cases:
        broken = copy.deepcopy(good)
        if how == "empty":
            broken[parent][leaf] = {}
        else:
            del broken[parent][leaf]
        errors = _errors(validator, broken)
        assert errors, f"validator accepted a payload missing {parent}/{leaf}"
        assert any(
            e.startswith(f"{parent}/{leaf}")
            or (e.startswith(f"{parent}:") and leaf in e)
            for e in errors
        ), f"no error pointed at {parent}/{leaf}: {errors}"


def test_validator_rejects_partial_config_and_session_state(
    validator: Draft202012Validator,
) -> None:
    """Every leaf of ``Config`` / ``SessionState`` is enforced, not just presence."""
    good = _default_body()
    for leaf in ("max_iterations", "timeout_seconds"):
        broken = copy.deepcopy(good)
        del broken["context"]["config"][leaf]
        assert _errors(validator, broken), f"validator accepted config without {leaf}"
    for leaf in (
        "turn_count",
        "total_tool_calls",
        "total_llm_calls",
        "cost_so_far",
        "started_at",
    ):
        broken = copy.deepcopy(good)
        del broken["context"]["session_state"][leaf]
        assert _errors(validator, broken), (
            f"validator accepted session_state without {leaf}"
        )
