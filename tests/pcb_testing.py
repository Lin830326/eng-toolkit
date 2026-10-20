"""Helpers for the ``pcb`` tests: the fixture folder, a call through the gateway client, and the skip marker for the real KiCad."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from eng_toolkit import external as X
from eng_toolkit.contract import ToolResult
from eng_toolkit.external_catalog import KICAD_CLI
from eng_toolkit.gateway.server import gateway

FIXTURES = Path(__file__).parent / "pcb_fixtures"

_found, _why = X.locate(KICAD_CLI)
needs_kicad = pytest.mark.skipif(_found is None, reason=f"kicad-cli is not installed here ({_why})")


async def call(tool: str, **arguments: Any) -> ToolResult:
    """Call a tool through the in-memory MCP client (the path a real client takes: JSON out, JSON back, no inf or NaN allowed)."""
    async with Client(gateway) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    payload = result.structured_content
    assert payload is not None, f"{tool} returned no structured content: {result}"
    return ToolResult.model_validate(payload)


def fixture(name: str) -> str:
    return str(FIXTURES / name)


def no_nonfinite(value: Any) -> bool:
    """True when no float inside nested dicts and lists is inf or NaN."""
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(no_nonfinite(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(no_nonfinite(v) for v in value)
    return True


def v(result: ToolResult, name: str) -> float:
    return result.values[name].quantity


def violation_count(result: ToolResult, kind: str) -> int:
    """How many violations of KiCad type ``kind`` a DRC or ERC result holds (from data.violations.by_type, which counts all of them)."""
    return sum(row["count"] for row in result.data["violations"]["by_type"] if row["type"] == kind)
