"""Shared electric-domain server, with no external engines."""

from fastmcp import FastMCP

ENGINE = "eng_toolkit.electric"
server = FastMCP("electric", instructions="Unit-checked steady-state electrical estimates. All results are unreviewed.")
