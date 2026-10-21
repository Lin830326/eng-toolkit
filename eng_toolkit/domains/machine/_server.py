"""Shared server for the machine-element domain."""

from fastmcp import FastMCP

ENGINE = "eng_toolkit.machine"
server = FastMCP("machine", instructions="Unit-checked machine-element closed forms. All results are unreviewed.")
