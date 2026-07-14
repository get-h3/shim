"""Adapter: Hermes native agent loop presented as an H3 harness.

This provides symmetry — the native Hermes loop can be referenced in
config alongside external H3 harnesses.  The :attr:`endpoint` is always
``None``, which signals to :class:`~h3_shim.loader.H3Loader` that no
HTTP client should be created.

When integrated into Hermes Core, :meth:`run` delegates to the real
native agent loop.  In this standalone shim package, it raises
:class:`NotImplementedError` with a clear message about the
integration point.
"""

import logging

logger = logging.getLogger(__name__)


class NativeH3Harness:
    """Wrapper that presents Hermes' native agent loop as an H3 harness.

    Harness name in config: ``"native"``.
    Always available; never health-checked via HTTP.
    """

    #: ``None`` — loader interprets this as "no HTTP client needed".
    endpoint: None = None  # type: ignore[assignment]

    async def run(self, session: object, message: object) -> str:
        """Delegate to the native Hermes agent loop.

        In Hermes Core this calls ``agent.loop.run(session, message)``.
        In the standalone shim package it raises ``NotImplementedError``
        because the shim has no access to Hermes internals.

        Returns
        -------
        str
            End reason (``"task_complete"``, ``"error"``, etc.).
        """
        logger.warning(
            "NativeH3Harness.run() called in standalone shim — "
            "this requires Hermes Core integration"
        )
        raise NotImplementedError(
            "NativeH3Harness requires Hermes Core. "
            "In production, this delegates to the native agent loop. "
            "Use an external H3 harness for standalone testing."
        )
