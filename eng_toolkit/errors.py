"""The one structured error carrier.

A :class:`ToolFailure` is a failure the caller can act on.  ``run_tool`` converts it into an
``ok=False`` result with its family, message and details.  Families are stable strings:
``input.*``, ``units.*``, ``geometry.*``, ``drawing.*``, ``engine.*``.

It derives from ``ValueError`` so that code written before it existed, which catches ``ValueError``
around unit parsing, keeps working.
"""

from __future__ import annotations

from typing import Any


class ToolFailure(ValueError):
    def __init__(self, family: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.family = family
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"family": self.family, "message": self.message, "details": self.details}


class EngineUnavailable(ToolFailure):
    """An optional engine cannot be imported; ``install`` is the command that installs it (from ``licenses.install_hint``)."""

    def __init__(self, module: str, dist: str, cause: BaseException, *, install: str, needed_by: str = ""):
        context = f"{needed_by}; install them with" if needed_by else "install it with"
        super().__init__(
            "engine.unavailable",
            f"{module} ({dist}) could not be imported: {cause}. {context}: {install}",
            {"module": module, "dist": dist, "install": install},
        )


__all__ = ["EngineUnavailable", "ToolFailure"]
