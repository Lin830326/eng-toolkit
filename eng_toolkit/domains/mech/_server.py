"""The one FastMCP server of the ``mech`` domain; the tool modules register on it."""

from __future__ import annotations

from fastmcp import FastMCP

ENGINE = "eng_toolkit.mech"

server = FastMCP(
    "mech",
    instructions=(
        "Closed-form mechanical-design checks (bolts, bearings, springs, beams). Every input must carry a unit. "
        "Results are unreviewed engineering estimates and must be checked by a qualified engineer."
    ),
)

__all__ = ["ENGINE", "server"]
