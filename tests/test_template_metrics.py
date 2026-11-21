"""H3-OBS-06 — the observability contract (obs-spec §5.4) in the scaffold templates.

Every harness ``hermes-h3 scaffold --lang {go,py,ts}`` emits must serve a
Prometheus ``/metrics`` endpoint exposing the four H3 harness SDK metric
families:

    h3_harness_requests_total            counter   endpoint, decision, lang
    h3_harness_request_duration_seconds  histogram endpoint, lang
    h3_harness_errors_total              counter   endpoint, status_class
    h3_harness_sessions_active           gauge     lang

The py template is imported directly (``h3_shim.templates.py.main``), so the
registry, the route wiring and the text exposition are proven without a server,
a subprocess or a toolchain. go/ts cannot run here (CI's ``test`` job installs
Python only), so their templates are held to the same contract by source
invariants: the four family names, the exposition content type, the fixed
``lang`` label value and a ``/metrics`` route all have to be present, and no
template may pull in a Prometheus client library.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from h3_shim.templates.py import main as py_harness

TEMPLATES = Path(py_harness.__file__).resolve().parent.parent

REQUESTS = "h3_harness_requests_total"
DURATION = "h3_harness_request_duration_seconds"
ERRORS = "h3_harness_errors_total"
SESSIONS = "h3_harness_sessions_active"

REQUIRED_FAMILIES = (REQUESTS, DURATION, ERRORS, SESSIONS)
EXPOSITION_CONTENT_TYPE = "text/plain; version=0.0.4"


def _series(name: str, **labels: str) -> str:
    """Build an exact ``name{labels}`` series, labels in the order given."""
    inner = ",".join(f'{key}="{value}"' for key, value in labels.items())
    return f"{name}{{{inner}}}"


def _raw(body: str, series: str) -> str | None:
    """The sample value of one exact series, or None when it is absent."""
    for line in body.splitlines():
        if line.startswith(series + " "):
            return line[len(series) + 1 :]
    return None


def _value(body: str, series: str) -> int:
    """The integer sample value of one exact series; 0 when it is absent."""
    raw = _raw(body, series)
    return 0 if raw is None else int(raw)


def _process_body(session_id: str) -> dict[str, object]:
    """A minimal valid ``/v1/process`` body for the py template."""
    return {
        "session_id": session_id,
        "message": {"role": "user", "content": "hello"},
        "identity": {"platform": "cli", "chat_id": "c1"},
        "context": {},
    }


# ── Registry ────────────────────────────────────────────────────────────────


def test_registry_counts_a_request_and_renders_prometheus_text() -> None:
    """A counted request shows up as a counter sample and a histogram sample."""
    registry = py_harness.MetricsRegistry("py")
    registry.observe_request(
        endpoint="process", decision="text", status=200, seconds=0.02
    )
    registry.set_sessions_active(3)

    body = registry.render()

    assert body.endswith("\n"), "exposition must end with a newline"
    for family in REQUIRED_FAMILIES:
        assert f"# TYPE {family} " in body, f"{family} declares no TYPE line"

    counted = _series(REQUESTS, endpoint="process", decision="text", lang="py")
    observed = _series(DURATION + "_count", endpoint="process", lang="py")
    summed = _series(DURATION + "_sum", endpoint="process", lang="py")
    assert _value(body, counted) == 1
    assert _raw(body, observed) == "1"
    assert _raw(body, summed) == "0.02"
    assert _value(body, _series(SESSIONS, lang="py")) == 3
    # The 0.02s observation lands in the 0.025 bucket and every larger one, never
    # in the 0.01 bucket: cumulative counts are what Prometheus scrapes.
    for le, expected in (("0.01", 0), ("0.025", 1), ("+Inf", 1)):
        bucket = _series(DURATION + "_bucket", endpoint="process", lang="py", le=le)
        assert _value(body, bucket) == expected, f"bucket le={le}"
    # A 200 is not an error, but the zero series exists for every route.
    assert _value(body, _series(ERRORS, endpoint="process", status_class="4xx")) == 0


def test_registry_classifies_errors_by_status_class() -> None:
    """4xx and 5xx land on their own errors_total series."""
    registry = py_harness.MetricsRegistry("py")
    registry.observe_request(
        endpoint="sessions", decision="error", status=404, seconds=0.001
    )
    registry.observe_request(
        endpoint="process", decision="error", status=500, seconds=0.001
    )

    body = registry.render()

    assert _value(body, _series(ERRORS, endpoint="sessions", status_class="4xx")) == 1
    assert _value(body, _series(ERRORS, endpoint="process", status_class="5xx")) == 1
    assert _value(body, _series(ERRORS, endpoint="process", status_class="4xx")) == 0
    failed = _series(REQUESTS, endpoint="process", decision="error", lang="py")
    assert _value(body, failed) == 1


def test_registry_keeps_both_labels_bounded() -> None:
    """endpoint/decision/status_class only ever take contract values.

    The label sets are what keep the series count cheap to scrape, so a hostile
    or malformed request must not be able to grow them.
    """
    registry = py_harness.MetricsRegistry("py")
    for endpoint in py_harness._ENDPOINT_TEMPLATES:
        registry.observe_request(endpoint, "none", 200, 0.001)
        registry.observe_request(endpoint, "error", 503, 0.001)

    body = registry.render()

    endpoints = set(re.findall(r'endpoint="([^"]+)"', body))
    assert endpoints <= set(py_harness._ENDPOINT_TEMPLATES)
    assert set(re.findall(r'decision="([^"]+)"', body)) <= {"none", "error"}
    status_classes = set(re.findall(r'status_class="([^"]+)"', body))
    assert status_classes <= set(py_harness._STATUS_CLASSES)


def test_endpoint_label_is_a_route_template_never_a_path_parameter() -> None:
    """A session id collapses onto its route template."""
    resolve = py_harness._endpoint_for_path
    assert resolve("/v1/process") == "process"
    assert resolve("/v1/result") == "result"
    assert resolve("/v1/health") == "health"
    assert resolve("/v1/cancel") == "cancel"
    assert resolve("/v1/sessions") == "sessions"
    assert resolve("/v1/sessions/abc-123") == "sessions"
    # Off-contract paths (and the metrics route itself) are not recorded.
    assert resolve("/v1/nope") is None
    assert resolve("/metrics") is None


# ── HTTP surface (real FastAPI routes) ──────────────────────────────────────


def test_metrics_route_serves_the_text_exposition() -> None:
    """GET /metrics answers the Prometheus text format on the harness listener."""
    response = TestClient(py_harness.app).get("/metrics")

    assert response.status_code == 200
    expected = f"{EXPOSITION_CONTENT_TYPE}; charset=utf-8"
    assert response.headers["content-type"] == expected
    body = response.text
    for family in REQUIRED_FAMILIES:
        assert f"# TYPE {family} " in body, f"{family} missing from /metrics"
    assert sum(1 for line in body.splitlines() if line.startswith("h3_harness_")) >= 4
    # A fresh scrape already carries every family, so rate() has a zero to grow from.
    assert _value(body, _series(SESSIONS, lang="py")) >= 0


def test_process_request_increments_the_request_counter() -> None:
    """One /v1/process call moves requests_total{endpoint="process"} by exactly 1."""
    client = TestClient(py_harness.app)
    session_id = f"obs06-{uuid.uuid4().hex[:8]}"
    counted = _series(REQUESTS, endpoint="process", decision="text", lang="py")

    before = client.get("/metrics").text
    response = client.post("/v1/process", json=_process_body(session_id))
    assert response.status_code == 200
    assert response.json()["decision"] == "text"
    after = client.get("/metrics").text

    assert _value(after, counted) - _value(before, counted) == 1
    # The session id is a path/body value, never a label.
    assert session_id not in after


def test_4xx_on_a_route_counts_an_error_and_keeps_the_wire_shape() -> None:
    """A 404 on an H3 route increments errors_total and changes nothing else."""
    client = TestClient(py_harness.app)
    session_id = f"obs06-missing-{uuid.uuid4().hex[:8]}"
    four_xx = _series(ERRORS, endpoint="sessions", status_class="4xx")
    failed = _series(REQUESTS, endpoint="sessions", decision="error", lang="py")

    before = client.get("/metrics").text
    response = client.get(f"/v1/sessions/{session_id}")
    assert response.status_code == 404
    # The instrumentation must not reshape the wire: the 404 is still the
    # template's own FastAPI error body, untouched.
    assert response.json() == {"detail": "Session not found"}
    after = client.get("/metrics").text

    assert _value(after, four_xx) - _value(before, four_xx) == 1
    assert _value(after, failed) - _value(before, failed) == 1
    assert session_id not in after


def test_invalid_body_is_counted_as_a_4xx() -> None:
    """A schema-rejected body is still a request: counted, and classified 4xx.

    The validation handler runs before any route body, so this arm also proves
    the pure-ASGI start stamp feeds it a real latency observation.
    """
    client = TestClient(py_harness.app)
    four_xx = _series(ERRORS, endpoint="process", status_class="4xx")
    observed = _series(DURATION + "_count", endpoint="process", lang="py")

    before = client.get("/metrics").text
    response = client.post("/v1/process", json={"session_id": "obs06-invalid"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    after = client.get("/metrics").text

    assert _value(after, four_xx) - _value(before, four_xx) == 1
    assert _value(after, observed) - _value(before, observed) == 1


def test_sessions_gauge_is_set_from_the_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route SETS the gauge from the harness's own live-session count.

    Pinning ``active_sessions`` proves the wiring (it is the same count
    /v1/health reports) instead of asserting a number traffic could move.
    """
    monkeypatch.setattr(py_harness.harness, "active_sessions", lambda: 7)

    body = TestClient(py_harness.app).get("/metrics").text

    assert _value(body, _series(SESSIONS, lang="py")) == 7


# ── go / ts: the same contract, proven at the source ────────────────────────


@pytest.mark.parametrize(
    ("lang", "filename", "lang_declaration"),
    [
        ("go", "main.go", 'metricsLang = "go"'),
        ("ts", "index.ts", "METRICS_LANG = 'ts'"),
    ],
)
def test_go_and_ts_templates_declare_the_observability_contract(
    lang: str, filename: str, lang_declaration: str
) -> None:
    """The go/ts templates carry the same four families and a /metrics route."""
    source = (TEMPLATES / lang / filename).read_text(encoding="utf-8")

    for family in REQUIRED_FAMILIES:
        assert family in source, f"{filename} never mentions {family}"
    assert EXPOSITION_CONTENT_TYPE in source, f"{filename}: wrong content type"
    assert lang_declaration in source, f"{filename} does not pin its lang label"
    assert "/metrics" in source, f"{filename} serves no /metrics route"


def test_templates_add_no_prometheus_client_dependency() -> None:
    """The families are hand-rolled: no template may add a client library."""
    for path in (
        TEMPLATES / "go" / "go.mod",
        TEMPLATES / "py" / "requirements.txt",
        TEMPLATES / "py" / "pyproject.toml",
        TEMPLATES / "ts" / "package.json",
    ):
        text = path.read_text(encoding="utf-8").lower()
        assert "prometheus" not in text, (
            f"{path.name} pulls in a Prometheus client; the registry is hand-rolled"
        )
