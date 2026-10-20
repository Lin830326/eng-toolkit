"""Both netlist tools require exactly one source, before reading files or running ngspice."""

import pytest
from spice_fixtures import DIVIDER, call

from eng_toolkit.domains.spice import tools


async def invoke(tool, args, gateway):
    if tool == "simulate":
        args = {"analysis": "op", **args}
    if gateway:
        return await call(f"spice_{tool}", args)
    function = getattr(tools, tool)
    return getattr(function, "fn", function)(**args)


@pytest.mark.parametrize("tool", ["simulate", "check_netlist"])
@pytest.mark.parametrize("gateway", [False, True], ids=["direct", "gateway"])
@pytest.mark.parametrize("file_exists", [False, True], ids=["missing-file", "existing-file"])
async def test_netlist_text_and_file_conflict(tool, gateway, file_exists, tmp_path):
    path = tmp_path / "divider.cir"
    if file_exists:
        path.write_text(DIVIDER, encoding="utf-8")
    result = await invoke(tool, {"netlist": DIVIDER, "netlist_file": str(path)}, gateway)
    assert result.ok is False and result.error.family == "input.invalid"
    assert result.error.message == "give exactly one of 'netlist' (the text) and 'netlist_file' (a path)"
    assert result.error.details == {"params": ["netlist", "netlist_file"]}
    assert result.provenance.input_files == []
    assert result.review_status == "unreviewed" and result.warnings == []


@pytest.mark.parametrize("tool", ["simulate", "check_netlist"])
@pytest.mark.parametrize("gateway", [False, True], ids=["direct", "gateway"])
@pytest.mark.parametrize("args", [{}, {"netlist": " \n\t", "netlist_file": " \t"}], ids=["omitted", "blank"])
async def test_neither_netlist_source_keeps_missing_error(tool, gateway, args):
    result = await invoke(tool, args, gateway)
    assert result.ok is False and result.error.family == "input.missing"
    assert result.error.message == "give the netlist as text in 'netlist' or as a path in 'netlist_file'"
    assert result.error.details == {"params": ["netlist", "netlist_file"]}
    assert result.review_status == "unreviewed" and result.warnings == []
