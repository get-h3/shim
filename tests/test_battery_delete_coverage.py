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
