"""End-to-end through a real MCP client (in-memory transport) against the gateway."""

import math

import pytest
from fastmcp import Client

from eng_toolkit.contract import ToolResult
from eng_toolkit.gateway.server import gateway

EXPECTED_TOOLS = {
    "toolkit_list_tools",
    "toolkit_doctor",
    "calc_cylinder_force",
    "calc_pipe_pressure_drop",
}


def _as_result(call_result) -> ToolResult:
    payload = call_result.structured_content
    assert payload is not None, "tool must return structured content"
    # FastMCP may wrap non-object results under {"result": ...}; ToolResult is an object so it is not wrapped.
    return ToolResult.model_validate(payload)


@pytest.mark.asyncio
async def test_gateway_exposes_namespaced_tools():
    async with Client(gateway) as client:
        tools = await client.list_tools()
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names
    by_name = {t.name: t for t in tools}
    # every tool advertises the contract as its output schema
    for name in EXPECTED_TOOLS:
        schema = by_name[name].output_schema
        assert schema is not None
        assert "review_status" in schema.get("properties", {}) or "$defs" in schema


@pytest.mark.asyncio
async def test_toolkit_doctor_returns_contract_and_is_healthy():
    async with Client(gateway) as client:
        r = _as_result(await client.call_tool("toolkit_doctor", {"scan_env": True}))
    assert r.review_status == "unreviewed"
    assert r.provenance.engine == "eng_toolkit.gateway"
    assert r.ok, r.data["failures"]
    assert r.data["python"]["ok"] is True
    deps = {d["dist"]: d for d in r.data["dependencies"]}
    for dist in ("fastmcp", "pint", "pydantic", "fluids"):
        assert deps[dist]["status"] == "ok", deps[dist]
        assert deps[dist]["detected_tier"] == "permissive"
    assert r.data["workspace"]["writable"] is True
    assert r.data["license_scan"]["flagged"] == []
    assert r.data["units"]["pint_ok"] is True


@pytest.mark.asyncio
async def test_toolkit_list_tools_lists_domains_and_engines():
    async with Client(gateway) as client:
        r = _as_result(await client.call_tool("toolkit_list_tools", {}))
    assert r.ok
    assert EXPECTED_TOOLS <= set(r.data["tools"])
    calc = next(d for d in r.data["domains"] if d["name"] == "calc")
    assert set(calc["tools"]) == {"calc_cylinder_force", "calc_pipe_pressure_drop"}
    engines = {e["dist"]: e for e in calc["engines"]}
    assert engines["fluids"]["installed"] and engines["fluids"]["version"]
    assert engines["fluids"]["tier"] == "permissive"
    assert engines["pint"]["tier"] == "permissive"


@pytest.mark.asyncio
async def test_calc_cylinder_force_over_mcp():
    async with Client(gateway) as client:
        r = _as_result(
            await client.call_tool(
                "calc_cylinder_force",
                {"bore": "100 mm", "rod": "50 mm", "pressure": "210 bar", "output_unit": "N"},
            )
        )
    assert r.ok
    assert math.isclose(r.value.quantity, 21.0e6 * math.pi / 4 * 0.1**2, rel_tol=1e-9)
    assert r.value.unit == "newton"
    assert r.review_status == "unreviewed"
    assert r.artifacts and r.artifacts[0].sha256


@pytest.mark.asyncio
async def test_calc_pipe_pressure_drop_over_mcp():
    async with Client(gateway) as client:
        r = _as_result(
            await client.call_tool(
                "calc_pipe_pressure_drop",
                {
                    "diameter": "10 mm",
                    "length": "1 m",
                    "flow_rate": {"value": math.pi / 4 * 1e-4, "unit": "m^3/s"},
                    "density": "870 kg/m^3",
                    "viscosity": "0.04 Pa*s",
                    "output_unit": "Pa",
                },
            )
        )
    assert r.ok, r.error
    assert math.isclose(r.value.quantity, 12800.0, rel_tol=1e-9)


@pytest.mark.asyncio
async def test_bare_number_error_is_a_contract_result_not_a_protocol_error():
    async with Client(gateway) as client:
        r = _as_result(
            await client.call_tool(
                "calc_cylinder_force", {"bore": "100", "rod": "50 mm", "pressure": "210 bar"}
            )
        )
    assert r.ok is False
    assert r.error.family == "units.bare_number"
