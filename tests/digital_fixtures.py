"""Helpers shared by the ``digital`` tests: skip when Yosys is absent, call a tool the way the gateway does, small designs."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastmcp import Client

from eng_toolkit import external
from eng_toolkit.contract import ToolResult
from eng_toolkit.external_catalog import YOSYS
from eng_toolkit.gateway.server import gateway

LOCATED, _WHY = external.locate(YOSYS)
HAVE_YOSYS = LOCATED is not None
needs_yosys = pytest.mark.skipif(not HAVE_YOSYS, reason="Yosys is not installed: see docs/external/design.md")
VERSION = external.version(YOSYS, LOCATED) if LOCATED else None


def fn(tool):
    return getattr(tool, "fn", tool)


def _strict(value):
    raise ValueError(f"{value} in a result")


def strict_json(result: ToolResult):
    """The result as JSON text a client would accept: no NaN, no Infinity anywhere."""
    return json.loads(result.model_dump_json(), parse_constant=_strict)


async def via_gateway(name: str, args: dict) -> ToolResult:
    """Call ``digital_<name>`` through the MCP client of the gateway (what a real LLM client does)."""
    async with Client(gateway) as client:
        raw = await client.call_tool(f"digital_{name}", args, raise_on_error=False)
    json.dumps(raw.structured_content, allow_nan=False)  # raises on a NaN or an infinity anywhere in the result
    return ToolResult.model_validate(raw.structured_content)


def refused(result: ToolResult, family: str) -> ToolResult:
    assert result.ok is False, "expected a refusal"
    assert result.error.family == family, result.error
    assert result.warnings == [] and result.value is None and result.values == {}
    assert result.review_status == "unreviewed"
    return result


def job_dir(result: ToolResult) -> Path:
    return Path(result.data["job_dir"])


def artifact(result: ToolResult, name: str) -> Path:
    return next(Path(a.path) for a in result.artifacts if Path(a.path).name == name)


def commands(result: ToolResult) -> list[str]:
    """The lines of the Yosys script a run used."""
    return artifact(result, "yosys-script.ys").read_text(encoding="utf-8").splitlines()


# Every line of every script the toolkit writes must match one of these: fixed commands, the toolkit's own file names,
# validated module names.  Anything the caller wrote would have to look like one of them to get through.
SCRIPT_LINE = re.compile(
    r"^(read_verilog( -sv)? src\d+\.v"
    r"|hierarchy -check( -top [A-Za-z_][A-Za-z0-9_]*)?"
    r"|synth -top [A-Za-z_][A-Za-z0-9_]*( -noabc)?"
    r"|flatten -noscopeinfo"
    r"|abc -g (simple|gates|aig|cmos2|NAND)"
    r"|opt_clean|proc( -noopt)?|flatten|memory|async2sync|stat|ltp -noff|check"
    r"|tee -o (stat|state)\.json stat -json"
    r"|write_verilog -noattr netlist\.v|write_json (netlist|design)\.json"
    r"|log eng-toolkit-marker-flat-check"
    r"|rename -top (gold|gate)|design -stash (gold_d|gate_d)|design -reset"
    r"|design -copy-from (gold_d -as gold gold|gate_d -as gate gate)"
    r"|miter -equiv -flatten -make_outputs gold gate miter"
    r"|sat -verify -prove trigger 0 -show-inputs -show-outputs( -seq \d+ -set-init-zero)? miter)$"
)

AND2 = "module t(input a, input b, output y);\n  assign y = a & b;\nendmodule\n"
OR2 = "module t(input a, input b, output y);\n  assign y = a | b;\nendmodule\n"
FULL_ADDER = (
    "module fa(input a, input b, input cin, output sum, output cout);\n"
    "  assign sum = a ^ b ^ cin;\n"
    "  assign cout = (a & b) | (a & cin) | (b & cin);\n"
    "endmodule\n"
)
