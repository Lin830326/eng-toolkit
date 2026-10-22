"""Domain-local registry for three-dimensional structural estimates."""

from fastmcp import FastMCP

ENGINE = "eng_toolkit.structures3d"
server = FastMCP("structures3d", instructions="Unit-checked linear elastic estimates. All results are unreviewed.")
