"""The one structured error carrier.

A :class:`ToolFailure` is a failure the caller can act on.  ``run_tool`` converts it into an
``ok=False`` result with its family, message and details.  Families are stable strings:
``input.*``, ``units.*``, ``geometry.*``, ``drawing.*``, ``engine.*``.

It derives from ``ValueError`` so that code written before it existed, which catches ``ValueError``
around unit parsing, keeps working.
"""

from __future__ import annotations

import re
from typing import Any


_INDEX = re.compile(r"\[(\d+)\]")


def json_pointer(param: str) -> str:
    """The JSON pointer for a parameter written as ``name``, ``name[2]`` or ``/name/2`` (the contract's ``details.param``)."""
    if param.startswith("/") or not param:
        return param
    return "/" + _INDEX.sub(r"/\1", param).replace(".", "/")


class ToolFailure(ValueError):
    def __init__(self, family: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.family = family
        self.message = message
        self.details = dict(details or {})
        # The contract says details.param is a JSON pointer; the carrier enforces it so that no caller can leak a bare name.
        if isinstance(self.details.get("param"), str):
            self.details["param"] = json_pointer(self.details["param"])

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


__all__ = ["EngineUnavailable", "ToolFailure", "json_pointer"]
