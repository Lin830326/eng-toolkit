"""Domain-local registration."""

from fastmcp import FastMCP

ENGINE = "eng_toolkit.optimize"
server = FastMCP("optimize", instructions="Bounded numerical optimization. Expressions use plain numeric values, without unit tracking.")
