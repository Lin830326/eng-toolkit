"""Wall-clock budgets, including the real MCP transport and cooperative phase checks."""

import json
import math
from types import SimpleNamespace

import pytest
from fastmcp import Client

from eng_toolkit import brep
from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.geometry import dfm
from eng_toolkit.domains.geometry.tools import check_dfm
from eng_toolkit.gateway.server import gateway

PHASES = {"read", "inventory", "edges", "cylinders", "pairs", "access", "rules", "distance"}


def run(path, **kwargs):
    return getattr(check_dfm, "fn", check_dfm)(model_path=str(path), **kwargs)


@pytest.fixture(scope="module")
def comb_path(tmp_path_factory):
    pytest.importorskip("OCP")
    import cad_fixtures as cad
    import dfm_fixtures as fx

    return cad.write_step(fx.comb(250), tmp_path_factory.mktemp("budget_comb") / "comb.step")


@pytest.fixture
def small_path(tmp_path):
    pytest.importorskip("OCP")
    import cad_fixtures as cad
    import dfm_fixtures as fx

    return cad.write_step(fx.flat_sheet(), tmp_path / "sheet.step")


def assert_timeout(result, limit, phase=None):
    assert not result.ok
    assert result.review_status == "unreviewed"
    assert result.value is None and result.values == {}
    assert result.error.family == "geometry.time_budget_exceeded"
    details = result.error.details
    assert set(details) == {"phase", "elapsed_s", "max_seconds", "param"}
    assert details["param"] == "/max_seconds"  # every geometry refusal names its JSON pointer (before: no param)
    assert details["phase"] in PHASES
    if phase is not None:
        assert details["phase"] == phase
    assert math.isfinite(details["elapsed_s"]) and details["elapsed_s"] >= limit
    assert details["max_seconds"] == limit
    assert details["phase"] in result.error.message
    assert f"limit {limit} seconds" in result.error.message
    json.dumps(result.model_dump(mode="json"), allow_nan=False)
    assert ToolResult.model_validate_json(result.model_dump_json()) == result


def test_one_second_stops_a_250_fin_comb(comb_path):
    assert_timeout(run(comb_path, max_seconds=1), 1)


@pytest.mark.parametrize("seconds", [-1, 0, 601])
def test_budget_out_of_range(seconds):
    result = run("unused.step", max_seconds=seconds)
    assert not result.ok
    assert result.error.family == "input.out_of_range"
    assert result.error.message == f"max_seconds: must be between 1 and 600, got {seconds}"
    assert result.error.details == {"param": "/max_seconds"}  # a JSON pointer, as every geometry refusal names its parameter


@pytest.mark.parametrize("process", ["cnc_milling", "sheet_metal"])
def test_default_and_generous_budgets_preserve_full_findings(small_path, process, monkeypatch):
    default = run(small_path, process=process)
    generous = run(small_path, process=process, max_seconds=600)
    with monkeypatch.context() as patch:
        patch.setattr(dfm, "Deadline", lambda seconds: lambda phase: None)
        unlimited = run(small_path, process=process)
    assert default.ok and generous.ok and unlimited.ok
    expected = json.dumps(unlimited.data["findings"], sort_keys=True, allow_nan=False)
    for result in (default, generous):
        assert json.dumps(result.data["findings"], sort_keys=True, allow_nan=False) == expected


def test_budget_includes_time_since_tool_entry(small_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(dfm, "time", SimpleNamespace(monotonic=lambda: now[0]))
    original = brep.read_solid

    def slow_read(*args, **kwargs):
        solid = original(*args, **kwargs)
        now[0] = 1.0
        return solid

    monkeypatch.setattr(brep, "read_solid", slow_read)
    assert_timeout(run(small_path, max_seconds=1), 1, "read")


@pytest.mark.parametrize("phase", sorted(PHASES - {"read"}))
def test_deadline_stops_inside_each_phase(small_path, monkeypatch, phase):
    now = [0.0]
    monkeypatch.setattr(dfm, "time", SimpleNamespace(monotonic=lambda: now[0]))
    original = dfm.Deadline
    visits = 0

    def deadline(seconds):
        budget = original(seconds)

        def check(current):
            nonlocal visits
            if current == phase:
                visits += 1
                # Let the phase start, then expire during its repeated work.
                if visits == 2:
                    now[0] = 1.0
            budget(current)

        return check

    monkeypatch.setattr(dfm, "Deadline", deadline)
    process = "sheet_metal" if phase == "distance" else "cnc_milling"
    result = run(small_path, process=process, max_seconds=1, limits='{"min_hole_edge_ratio": 3}' if phase == "distance" else "")
    assert_timeout(result, 1, phase)
    assert visits == 2


@pytest.mark.asyncio
async def test_budget_error_and_schema_through_gateway(comb_path):
    async with Client(gateway) as client:
        tool = next(t for t in await client.list_tools() if t.name == "geometry_check_dfm")
        parameter = tool.input_schema["properties"]["max_seconds"]
        assert parameter["type"] == "integer" and parameter["default"] == 120
        assert "1 to 600" in parameter["description"]
        assert "cannot be interrupted" in tool.description
        call = await client.call_tool("geometry_check_dfm", {"model_path": str(comb_path), "max_seconds": 1})
        result = ToolResult.model_validate(call.structured_content)
        assert_timeout(result, 1)
        invalid = await client.call_tool("geometry_check_dfm", {"model_path": str(comb_path), "max_seconds": 601})
        assert ToolResult.model_validate(invalid.structured_content).error.family == "input.out_of_range"
