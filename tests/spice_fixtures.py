"""Helpers for the spice tests: a gateway client call that returns a validated ToolResult, and the skip marker for tests that need ngspice."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from fastmcp import Client

from eng_toolkit import external as X
from eng_toolkit.contract import ToolResult
from eng_toolkit.external_catalog import NGSPICE
from eng_toolkit.gateway.server import gateway

_FOUND, _WHY = X.locate(NGSPICE)
needs_ngspice = pytest.mark.skipif(_FOUND is None, reason=f"ngspice is not installed here ({_WHY})")


async def call(name: str, args: dict) -> ToolResult:
    """Call a tool through a real MCP client against the gateway (so inf/NaN or a bad schema would fail here, as in a real client)."""
    async with Client(gateway) as client:
        raw = await client.call_tool(name, args, raise_on_error=False)
    payload = raw.structured_content
    assert payload is not None, raw
    json.dumps(payload, allow_nan=False)  # no inf or NaN may ever reach a client
    return ToolResult.model_validate(payload)


def artifact_path(result: ToolResult, name: str) -> Path:
    found = [a.path for a in result.artifacts if Path(a.path).name == name]
    assert found, (name, [a.path for a in result.artifacts])
    return Path(found[0])


def read_csv(path: Path) -> tuple[list[str], list[list[float]]]:
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    return rows[0], [[float(v) for v in row] for row in rows[1:]]


def interp(header: list[str], rows: list[list[float]], column: str, x: float) -> float:
    """Linear interpolation of ``column`` at ``x`` in a table read by :func:`read_csv` (first column = x)."""
    k = header.index(column)
    for a, b in zip(rows, rows[1:]):
        if a[0] <= x <= b[0]:
            return a[k] + (b[k] - a[k]) * (x - a[0]) / (b[0] - a[0])
    raise AssertionError(f"x = {x} outside the table")


DIVIDER = "divider 5 V over 1k + 1k\nV1 in 0 DC 5\nR1 in mid 1k\nR2 mid 0 1k\n"
