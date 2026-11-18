"""The battery must fail a harness that omits DELETE /v1/sessions handling.

H3-PM-019: the 46-test battery had ZERO coverage of ``DELETE
/v1/sessions/{id}`` (grep 'delete|terminate' hit only a comment), so a
harness could ship no terminate handling at all and still pass 46/46. These
tests prove the new cells (``test_5_13_session_delete_terminates`` /
``test_5_14_session_get_after_delete``) are live:

* a real harness (the py scaffold TEMPLATE exercised in-process via
  ``TestClient`` and an ``httpx.ASGITransport`` — no server, no subprocess)
  passes both cells;
* the SAME battery against the SAME harness with the DELETE route deleted
  FAILS both cells — the named assertions are
  ``Session DELETE status 405`` (5_13) and ``Session DELETE status 405
  (expected 200)`` (5_14).

DF-H3-53 adds the no-op-terminate arm: an SDK-shaped harness that tracks
sessions but does NOT override ``on_session_terminate`` answers
``terminated: true`` and stamps ``status: completed`` itself, so the battery
must FAIL it (naming the no-op terminate) while a harness that DOES override
the hook still passes both cells.

The fixed-router arm is also covered: an SDK-shaped harness whose DELETE
honestly answers ``terminated: false`` (the base hook did nothing, no
stamp) passes the honesty cell (``test_5_14``), while the pre-fix
fabrication (``terminated: true`` + stamped ``completed``) still fails it.

The route is removed at the app level (``app.router.routes``), never by
editing the template file, so every arm starts from a byte-identical tree.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request

from h3_shim.templates.py import main as template_main
from h3_shim.test_battery import H3TestBattery


@pytest.fixture()
def harness_app() -> FastAPI:
    """The py scaffold template's FastAPI app, freshly imported."""
    return template_main.app


async def _run_battery_against(app: FastAPI) -> tuple[bool, str]:
    """Drive the REAL battery's two new cells against an in-process app."""
    battery = H3TestBattery("http://h3-in-process")
    # Swap the loop's transport for ASGI: same httpx.AsyncClient API the
    # battery already uses, no sockets.
    await battery.client.aclose()
    battery.client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://h3-in-process"
    )
    try:
        r13 = await battery.test_5_13_session_delete_terminates()
        r14 = await battery.test_5_14_session_get_after_delete()
        detail = f"5_13: {r13.detail} | 5_14: {r14.detail}"
        return (r13.passed and r14.passed, detail)
    finally:
        await battery.client.aclose()


async def _run_battery_14_against(app: FastAPI) -> tuple[bool, str]:
    """Drive ONLY test_5_14 (the honesty cell) against an in-process app.

    test_5_13 still requires ``terminated: true`` — it is the "do you
    implement termination?" gate — so a fixed no-override harness (honest
    ``terminated: false``) legitimately fails that cell: it does not tear
    sessions down. test_5_14 is the cell DF-H3-53 teaches the honest
    no-teardown claim, and it is the one the fixed shape must pass.
    """
    battery = H3TestBattery("http://h3-in-process")
    await battery.client.aclose()
    battery.client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://h3-in-process"
    )
    try:
        r14 = await battery.test_5_14_session_get_after_delete()
        return (r14.passed, f"5_14: {r14.detail}")
    finally:
        await battery.client.aclose()


def _sdk_shaped_app(*, override_terminate: bool) -> FastAPI:
    """Build an in-process harness shaped like the SDK's ``create_router``.

    DF-H3-53 arm-D: a ``BaseHarness`` subclass that tracks sessions via
    ``get_session_info`` (a metadata-only dict with NO ``status`` key) but
    does NOT override ``on_session_terminate``. The router then:

    * answers ``DELETE /v1/sessions/{id}`` with ``{"terminated": true}``
      after the base no-op hook, and stamps ``status: completed`` itself;
    * makes ``GET /v1/sessions/{id}`` return 200 with ``status: completed``
      (the metadata dict carries no status, so the router's stamp wins).

    With ``override_terminate=True`` the hook instead forgets the session —
    the documented override — so the later GET 404s and the battery passes.
    """
    app = FastAPI()
    # harness-side store: on_process writes metadata only, no status key.
    sessions: dict[str, dict] = {}
    # router-side liveness stamp (create_router's ``_track``).
    tracked: dict[str, str] = {}

    @app.post("/v1/process")
    async def process(request: Request):
        body = await request.json()
        sid = body["session_id"]
        prev = sessions.get(sid, {})
        sessions[sid] = {
            "started_at": prev.get("started_at") or "2026-01-01T00:00:00+00:00",
            "last_active": "2026-01-01T00:00:00+00:00",
            "turn_count": prev.get("turn_count", 0) + 1,
        }
        tracked[sid] = "active"
        return {
            "decision": "text",
            "decision_id": "noop-process",
            "text": {
                "content": f"Echo: {body['message']['content']}",
                "finished": True,
            },
        }

    @app.delete("/v1/sessions/{session_id}")
    async def delete_session(session_id: str):
        if session_id not in sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        if override_terminate:
            # The documented on_session_terminate override: forget the session
            # so a later GET 404s.
            sessions.pop(session_id, None)
        # The router stamps completed either way (create_router's _track).
        tracked[session_id] = "completed"
        return {"session_id": session_id, "terminated": True}

    @app.get("/v1/sessions/{session_id}")
    async def get_session(session_id: str):
        info = sessions.get(session_id)
        if info is None:
            raise HTTPException(status_code=404, detail="Session not found")
        # Metadata-only dict carries no status -> the router's stamp wins.
        status = info.get("status") or tracked.get(session_id, "active")
        return {
            "session_id": session_id,
            "started_at": info.get("started_at", ""),
            "last_active": info.get("last_active", ""),
            "turn_count": info.get("turn_count", 0),
            "status": status,
        }

    return app


def _honest_sdk_shaped_app() -> FastAPI:
    """The FIXED DF-H3-53 SDK router shape: base no-op on_session_terminate.

    DELETE re-asks the harness's session view AFTER the no-op hook; the
    session is still present, so the router answers ``terminated: false``
    and stamps nothing — the session keeps its own live status. This is the
    honest no-teardown claim the battery's test_5_14 must accept.
    """
    app = FastAPI()
    sessions: dict[str, dict] = {}

    @app.post("/v1/process")
    async def process(request: Request):
        body = await request.json()
        sid = body["session_id"]
        prev = sessions.get(sid, {})
        sessions[sid] = {
            "started_at": prev.get("started_at") or "2026-01-01T00:00:00+00:00",
            "last_active": "2026-01-01T00:00:00+00:00",
            "turn_count": prev.get("turn_count", 0) + 1,
        }
        return {
            "decision": "text",
            "decision_id": "noop-process",
            "text": {
                "content": f"Echo: {body['message']['content']}",
                "finished": True,
            },
        }

    @app.delete("/v1/sessions/{session_id}")
    async def delete_session(session_id: str):
        if session_id not in sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        # Fixed router: the base hook is a no-op, so the session survives
        # and DELETE honestly answers terminated:false — no 'completed'
        # stamp, the session keeps its own live status.
        return {"session_id": session_id, "terminated": False}

    @app.get("/v1/sessions/{session_id}")
    async def get_session(session_id: str):
        info = sessions.get(session_id)
        if info is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return {
            "session_id": session_id,
            "started_at": info.get("started_at", ""),
            "last_active": info.get("last_active", ""),
            "turn_count": info.get("turn_count", 0),
            "status": info.get("status") or "active",
        }

    return app


@pytest.mark.asyncio
async def test_both_new_cells_pass_the_compliant_template(harness_app):
    ok, detail = await _run_battery_against(harness_app)
    assert ok, f"compliant template must pass the new DELETE cells — {detail}"


@pytest.mark.asyncio
async def test_battery_fails_a_harness_with_the_delete_route_removed(
    harness_app,
):
    """Omit terminate handling -> both new cells FAIL, naming the assertion.

    RED proof: identical app, identical battery, the only mutation is the
    absence of the ``DELETE /v1/sessions/{session_id}`` route.
    """
    deleted = [
        r
        for r in harness_app.router.routes
        if getattr(r, "path", None) == "/v1/sessions/{session_id}"
        and "DELETE" in getattr(r, "methods", set())
    ]
    assert deleted, "template must ship a DELETE session route for this proof"
    harness_app.router.routes = [
        r for r in harness_app.router.routes if r not in deleted
    ]
    try:
        ok, detail = await _run_battery_against(harness_app)
        assert not ok, (
            "the battery must FAIL a harness without DELETE /v1/sessions "
            f"handling — got a PASS ({detail})"
        )
        # The failure names the missing terminate handling...
        assert "Session DELETE" in detail, detail
        # ...and it is the DELETE step, not the setup process call, that failed.
        assert "Process" not in detail.split("Session DELETE")[0].split("5_13:")[-1], (
            detail
        )
    finally:
        # Never leak the mutated router into sibling tests — restore byte
        # identity by re-adding the deleted route objects in place.
        harness_app.router.routes.extend(deleted)


@pytest.mark.asyncio
async def test_battery_fails_a_noop_terminate_harness():
    """The battery must FAIL an arm-D harness (DF-H3-53).

    A harness that tracks sessions but does NOT override
    ``on_session_terminate`` fabricates teardown: DELETE answers
    ``terminated: true`` and stamps ``status: completed``, and test_5_14 must
    name that no-op terminate instead of accepting the stamped status.
    """
    app = _sdk_shaped_app(override_terminate=False)
    ok, detail = await _run_battery_against(app)
    assert not ok, (
        "the battery must FAIL a no-op-terminate harness (fabricated "
        f"teardown) — got a PASS ({detail})"
    )
    assert "no-op terminate" in detail, detail
    assert "completed" in detail, detail


@pytest.mark.asyncio
async def test_battery_passes_a_harness_that_overrides_terminate():
    """A harness that DOES override the hook keeps its documented behaviour.

    The documented override forgets the session, so the later GET 404s and
    both cells pass — the arm-D contrast: same router, real teardown.
    """
    app = _sdk_shaped_app(override_terminate=True)
    ok, detail = await _run_battery_against(app)
    assert ok, f"a harness that overrides on_session_terminate must pass — {detail}"


@pytest.mark.asyncio
async def test_battery_passes_an_honest_noop_terminate_harness():
    """DF-H3-53: the FIXED router answers terminated:false (honest no-op).

    A harness that does not override on_session_terminate must not fabricate
    a teardown: DELETE answers terminated:false and test_5_14 accepts that
    honest claim without evaluating the GET. (test_5_13 is not run here — it
    still requires terminated:true, so a no-override harness legitimately
    fails the "do you implement termination?" gate, without any fabrication
    accusation.)
    """
    app = _honest_sdk_shaped_app()
    ok, detail = await _run_battery_14_against(app)
    assert ok, f"an honest terminated:false DELETE must pass test_5_14 — {detail}"
    assert "terminated:false" in detail, detail


@pytest.mark.asyncio
async def test_battery_passes_the_real_py_template_base_hook():
    """DF-H3-53: the REAL shipped py template passes both cells.

    src/h3_shim/templates/py/main.py is the repo's own scaffold: its base
    ``on_session_terminate`` pops the session, so the route's post-hook
    re-ask sees the session gone and answers ``terminated:true`` honestly,
    and the later GET 404s. This arm drives the battery against the
    template app itself — not a hand-built shape that exists nowhere in
    src/ (the earlier coverage arms model the SDK router; this one runs
    the shipped router).
    """
    app = template_main.app
    ok, detail = await _run_battery_against(app)
    assert ok, f"the shipped py template must pass both cells — {detail}"


@pytest.mark.asyncio
async def test_battery_5_14_passes_template_with_noop_override():
    """DF-H3-53: a template-derived harness whose on_session_terminate is
    overridden as a no-op gets the honest terminated:false from the REAL
    route (post-hook re-ask) and test_5_14 passes via the honest branch.
    """
    original = template_main.harness

    class _NoopHarness(type(original)):
        def on_session_terminate(self, session_id: str) -> None:
            return None  # no-op override — the shape DF-H3-53 targets

    template_main.harness = _NoopHarness()
    try:
        app = template_main.app
        ok, detail = await _run_battery_14_against(app)
    finally:
        template_main.harness = original
    assert ok, (
        f"a no-op override on the real template must answer terminated:false — {detail}"
    )
    assert "terminated:false" in detail, detail
