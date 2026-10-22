"""Domain-local registry; no optional runtime engines."""

from fastmcp import FastMCP

ENGINE = "eng_toolkit.fluids"
server = FastMCP("fluids", instructions="Unit-checked textbook fluid mechanics. Results require engineering review.")
