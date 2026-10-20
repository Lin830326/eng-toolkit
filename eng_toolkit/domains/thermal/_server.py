"""The one FastMCP server of the ``thermal`` domain; the tool modules register on it."""

from __future__ import annotations

from fastmcp import FastMCP

ENGINE = "eng_toolkit.thermal"

server = FastMCP(
    "thermal",
    instructions=(
        "Textbook heat-transfer calculations (conduction, heat exchangers, fins, transient conduction, radiation, convection "
        "correlations). Every input must carry a unit; temperatures are ABSOLUTE (degC, K, degF), never differences. "
        "Fluid properties are inputs (no property tables). Results are unreviewed engineering estimates."
    ),
)

__all__ = ["ENGINE", "server"]
