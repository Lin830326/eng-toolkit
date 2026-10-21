"""Domain-local tool registry."""

from fastmcp import FastMCP

ENGINE = "eng_toolkit.structures"
server = FastMCP("structures", instructions="Unit-checked linear structural analysis. All results are unreviewed; no design-code checks.")
