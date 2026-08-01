"""Tests for the NativeH3Harness adapter (src/h3_shim/native.py).

The native harness presents Hermes' own agent loop as an H3 harness entry.
In the standalone shim package it has no HTTP endpoint and its run() raises
NotImplementedError with a clear message about the Hermes Core integration
point. These tests lock that contract so the adapter cannot silently drift
into a stub or a half-wired implementation.
"""

import pytest

from h3_shim.native import NativeH3Harness


def test_endpoint_is_none():
    """The loader interprets endpoint=None as 'no HTTP client needed'."""
    assert NativeH3Harness.endpoint is None


def test_run_raises_not_implemented_in_standalone():
    """Standalone shim cannot delegate to Hermes internals — must raise."""
    harness = NativeH3Harness()
    with pytest.raises(NotImplementedError) as excinfo:
        # async run() — drive it without needing an event loop
        import asyncio

        asyncio.run(harness.run(session=object(), message="hello"))
    assert "Hermes Core" in str(excinfo.value)


def test_run_error_message_mentions_external_harness():
    """Users must be told the standalone fallback (external H3 harness)."""
    harness = NativeH3Harness()
    import asyncio

    with pytest.raises(NotImplementedError) as excinfo:
        asyncio.run(harness.run(session=object(), message="hello"))
    assert "external H3 harness" in str(excinfo.value)
