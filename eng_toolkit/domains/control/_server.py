"""The one FastMCP server of the ``control`` domain; the tool modules register on it."""

from __future__ import annotations

from fastmcp import FastMCP

ENGINE = "eng_toolkit.control"

server = FastMCP(
    "control",
    instructions=(
        "Classical control analysis of single-input single-output linear systems. Transfer functions are coefficient lists in "
        "DESCENDING powers as JSON text (for example '[1, 3, 2]' is s^2 + 3 s + 2) or small polynomial text ('s^2 + 3 s + 2'); "
        "state space is A, B, C, D as nested lists. Continuous by default; domain='discrete' with a sample_time for z-domain models. "
        "Results are unreviewed estimates; the tools never execute the text you give them."
    ),
)

__all__ = ["ENGINE", "server"]
