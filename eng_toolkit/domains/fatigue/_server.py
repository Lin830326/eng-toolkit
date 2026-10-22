"""Domain-local registry."""

from fastmcp import FastMCP

ENGINE = "eng_toolkit.fatigue"
server = FastMCP("fatigue", instructions="Unit-checked uniaxial fatigue and LEFM estimates. All results are unreviewed.")
