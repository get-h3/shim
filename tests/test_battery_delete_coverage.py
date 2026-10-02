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

The route is removed at the app level (``app.router.routes``), never by
editing the template file, so every arm starts from a byte-identical tree.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

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
