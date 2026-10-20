"""The one FastMCP server of the ``spice`` domain; the tool modules register on it."""

from __future__ import annotations

from fastmcp import FastMCP

ENGINE = "eng_toolkit.spice"

server = FastMCP(
    "spice",
    instructions=(
        "Circuit simulation with ngspice (a program the user installed). A netlist is read as DATA through an allow-list: control sections, includes, "
        "libraries, code models and file output are refused. Quantities carry units. Results are unreviewed numerical estimates."
    ),
)

__all__ = ["ENGINE", "server"]
