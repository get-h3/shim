"""Tests for wrong-server / non-H3 endpoint detection in the test battery.

These tests mock ``httpx.AsyncClient`` so no live server is needed.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from h3_shim.cli import _run_battery
from h3_shim.test_battery import H3TestBattery, NotH3EndpointError


def _fake_response(
    status_code: int,
    json_data: object,
    text: str | None = None,
    reason_phrase: str = "OK",
) -> MagicMock:
    """Build a stand-in httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.reason_phrase = reason_phrase
    resp.text = text if text is not None else json.dumps(json_data)
    resp.json = MagicMock(return_value=json_data)
    return resp


@pytest.fixture
def fake_client(monkeypatch):
    """Patch ``httpx.AsyncClient`` construction to return a controllable fake."""

    def _make(get_return=None, get_side_effect=None):
        fake = MagicMock()
        fake.get = AsyncMock(return_value=get_return, side_effect=get_side_effect)
        fake.aclose = AsyncMock()
        # Stub post/cancel in case anything leaks through the probe.
        fake.post = AsyncMock(return_value=_fake_response(200, {}, reason_phrase="OK"))
        fake_client_cls = MagicMock(return_value=fake)
        monkeypatch.setattr("httpx.AsyncClient", fake_client_cls)
        return fake

    return _make


class TestProbe:
    async def test_401_session_token_missing(self, fake_client):
        fake_client(
            get_return=_fake_response(
                401,
                {"error": {"code": "SESSION_TOKEN_MISSING"}},
                text='{"error":{"code":"SESSION_TOKEN_MISSING"}}',
                reason_phrase="Unauthorized",
            )
        )
        battery = H3TestBattery("http://localhost:9123")
        with pytest.raises(NotH3EndpointError) as exc_info:
            await battery.probe()
        assert "does not look like an H3 endpoint" in str(exc_info.value)
        assert "status 401" in exc_info.value.reason

    async def test_foreign_json_health(self, fake_client):
        fake_client(
            get_return=_fake_response(
                200,
                {"status": "healthy", "service": "dexdat-api"},
            )
        )
        battery = H3TestBattery("http://localhost:9123")
        with pytest.raises(NotH3EndpointError) as exc_info:
            await battery.probe()
        assert "status field is 'healthy'" in exc_info.value.reason

    async def test_connection_error(self, fake_client):
        fake_client(get_side_effect=httpx.ConnectError("connection refused"))
        battery = H3TestBattery("http://localhost:9123")
        with pytest.raises(NotH3EndpointError) as exc_info:
            await battery.probe()
        assert "connection error" in exc_info.value.reason

    async def test_healthy_h3_shape(self, fake_client):
        fake_client(
            get_return=_fake_response(
                200,
                {
                    "status": "ok",
                    "version": "1.0.0",
                    "transport": "rest",
                    "protocol_version": "1.0",
                    "capabilities": ["text"],
                },
            )
        )
        battery = H3TestBattery("http://localhost:9191")
        assert await battery.probe() is None


class TestRunBatteryDetection:
    async def test_run_battery_401_emits_warning_and_exit_code_2(
        self, fake_client, capsys
    ):
        fake_client(
            get_return=_fake_response(
                401,
                {"error": {"code": "SESSION_TOKEN_MISSING"}},
                reason_phrase="Unauthorized",
            )
        )
        code = await _run_battery("http://localhost:9123", None, False)
        assert code == 2
        captured = capsys.readouterr()
        assert "does not look like an H3 endpoint" in captured.err
        assert "SESSION_TOKEN_MISSING" in captured.err
        assert "H3 Compliance Test Battery" in captured.out

    async def test_run_battery_401_json_mode(self, fake_client, capsys):
        fake_client(
            get_return=_fake_response(
                401,
                {"error": {"code": "SESSION_TOKEN_MISSING"}},
                reason_phrase="Unauthorized",
            )
        )
        code = await _run_battery("http://localhost:9123", None, True)
        assert code == 2
        captured = capsys.readouterr()
        assert "does not look like an H3 endpoint" in captured.err
        payload = json.loads(captured.out)
        assert payload["not_h3_endpoint"] is True
        assert payload["total"] == 0
        assert payload["all_passing"] is False
        assert "SESSION_TOKEN_MISSING" in payload["reason"]
