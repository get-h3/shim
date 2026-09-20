"""Tests for ``scripts/sync_protocol.py`` — union-typed schema properties.

The upstream protocol corpus (get-h3/protocol) gained JSON-Schema 2020-12
union types, e.g. ``"cancelled_decision_id": {"type": ["string", "null"]}``
in ``cancel-response.json``. ``field_signature()`` assumed ``prop["type"]``
is a plain string and used it directly as a dict key, so any union-typed
property crashed the whole sync run with
``TypeError: unhashable type: 'list'`` (GAP-048).

The script lives in ``scripts/``, which is not an importable package, so it
is loaded by file path (same pattern as ``tests/test_h3_plugin.py``).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

# ── module loading ──────────────────────────────────────────────────────────
# scripts/ is not a package — load the script by file path. The module-level
# ``if __name__ == "__main__"`` guard does not fire under a non-main name.

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "sync_protocol.py"


@pytest.fixture(scope="module")
def sync_protocol() -> Any:
    """Load ``scripts/sync_protocol.py`` as a module."""
    spec = importlib.util.spec_from_file_location(
        "sync_protocol_under_test", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None, f"cannot load {_SCRIPT_PATH}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ── GAP-048: union-typed ``type`` must not raise ────────────────────────────


def test_union_string_null_is_nullable_optional(sync_protocol: Any) -> None:
    """The live corpus shape: ``{"type": ["string", "null"]}``.

    First non-null member picks the py type via TYPE_MAP; the presence of
    ``"null"`` marks the field ``:optional`` (same suffix the ``anyOf``
    -with-null path already adds).
    """
    sig = sync_protocol.field_signature(
        "cancelled_decision_id", {"type": ["string", "null"]}
    )
    assert sig == "cancelled_decision_id:str:optional"


def test_union_null_first_member_order(sync_protocol: Any) -> None:
    """Member order must not matter: null first still resolves to str."""
    sig = sync_protocol.field_signature("f", {"type": ["null", "string"]})
    assert sig == "f:str:optional"


@pytest.mark.parametrize(
    "prop",
    [
        {"type": []},  # empty union
        {"type": ["weird"]},  # member unknown to TYPE_MAP
        {"type": ["null"]},  # all-null union
    ],
)
def test_union_fallbacks_degrade_to_any(
    sync_protocol: Any, prop: dict[str, Any]
) -> None:
    """Empty / unknown / all-null unions fall back to Any — never raise."""
    sig = sync_protocol.field_signature("f", prop)
    assert sig.startswith("f:Any")


# ── plain string types: output byte-identical to pre-fix behavior ──────────


def test_plain_string_signature_unchanged(sync_protocol: Any) -> None:
    """Suffix order (enum, optional, default) and text are unchanged."""
    prop = {"type": "string", "enum": ["a", "b"], "default": "a"}
    sig = sync_protocol.field_signature("mode", prop)
    assert sig == "mode:str:enum=a,b:optional:default=a"


def test_required_plain_type_has_no_optional(sync_protocol: Any) -> None:
    assert (
        sync_protocol.field_signature("count", {"type": "integer", "required": True})
        == "count:int"
    )


def test_anyof_null_path_unchanged(sync_protocol: Any) -> None:
    """The pre-existing anyOf-with-null path keeps its exact signature."""
    prop = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert sync_protocol.field_signature("f", prop) == "f:str:optional"


# ── end-to-end: the --diff gate survives a union-typed corpus ──────────────


def test_main_diff_exit_zero_with_union_schema(
    sync_protocol: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A schema dir with a union-typed property must not kill ``main()``."""
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "CancelResponse",
        "type": "object",
        "properties": {
            "cancelled": {"type": "boolean"},
            "cancelled_decision_id": {"type": ["string", "null"]},
        },
    }
    (schema_dir / "cancel-response.json").write_text(json.dumps(schema))

    protocol_py = tmp_path / "protocol.py"
    protocol_py.write_text(
        "class CancelResponse(BaseModel):\n"
        "    cancelled: bool = False\n"
        "    cancelled_decision_id: str | None = None\n"
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sync_protocol.py",
            "--schema-dir",
            str(schema_dir),
            "--protocol-py",
            str(protocol_py),
            "--diff",
        ],
    )
    assert sync_protocol.main() == 0
    out = capsys.readouterr().out
    assert "cancelled_decision_id:str:optional" in out
