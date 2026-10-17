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
                    "flow_rate": f"{math.pi / 4 * 1e-4} m^3/s",
                    "density": "870 kg/m^3",
                    "viscosity": "0.04 Pa*s",
                    "output_unit": "Pa",
                },
            )
        )
    assert r.ok, r.error
    assert math.isclose(r.value.quantity, 12800.0, rel_tol=1e-9)


BASIC_JSON_TYPES = {"string", "number", "integer", "boolean"}
MAX_PHASE0_TOOLS = 6


@pytest.mark.asyncio
async def test_tool_schemas_are_small_model_friendly():
    """Few coarse tools; every input is a basic JSON type with a description (no unions/objects)."""
    async with Client(gateway) as client:
        tools = await client.list_tools()
    assert len(tools) <= MAX_PHASE0_TOOLS, [t.name for t in tools]
    for tool in tools:
        assert tool.description and len(tool.description) > 20, tool.name
        schema = tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema
        for pname, prop in schema.get("properties", {}).items():
            assert "anyOf" not in prop and "oneOf" not in prop, (tool.name, pname, prop)
            assert prop.get("type") in BASIC_JSON_TYPES, (tool.name, pname, prop)
            assert prop.get("description"), f"{tool.name}.{pname} has no description"


@pytest.mark.asyncio
async def test_json_string_quantity_is_accepted_through_string_schema():
    async with Client(gateway) as client:
        r = _as_result(
            await client.call_tool(
                "calc_cylinder_force",
                {"bore": '{"value": 100, "unit": "mm"}', "rod": "50 mm", "pressure": "210 bar", "output_unit": "N"},
            )
        )
    assert r.ok, r.error
    assert math.isclose(r.value.quantity, 21.0e6 * math.pi / 4 * 0.1**2, rel_tol=1e-9)


@pytest.mark.asyncio
async def test_doctor_reports_llm_free_operation():
    async with Client(gateway) as client:
        r = _as_result(await client.call_tool("toolkit_doctor", {"scan_env": False}))
    assert r.data["llm_free"]["calls_llm"] is False
    assert r.data["llm_free"]["requires_api_key"] is False
    assert r.data["llm_free"]["llm_sdks_installed"] == []
    assert r.data["transports"] == ["stdio", "http"]


def test_source_tree_never_imports_an_llm_sdk():
    import re
    from pathlib import Path

    import eng_toolkit

    root = Path(eng_toolkit.__file__).parent
    pattern = re.compile(r"^\s*(import|from)\s+(anthropic|openai|litellm|langchain|google\.genai|ollama|cohere|mistralai)\b", re.M)
    hits = [str(p) for p in root.rglob("*.py") if pattern.search(p.read_text(encoding="utf-8"))]
    assert hits == []


def test_cli_parses_serve_and_doctor(monkeypatch):
    """argparse wiring only: serve dispatches to gateway.run with the chosen transport."""
    from eng_toolkit.gateway import server as srv

    calls = []
    monkeypatch.setattr(srv.gateway, "run", lambda **kw: calls.append(kw))

    srv.main([])
    srv.main(["serve"])
    srv.main(["serve", "--transport", "http", "--port", "9000", "--host", "0.0.0.0"])
    assert calls[0]["transport"] == "stdio"
    assert calls[1]["transport"] == "stdio"
    assert calls[2]["transport"] == "http" and calls[2]["port"] == 9000 and calls[2]["host"] == "0.0.0.0"
    assert calls[2]["path"] == "/mcp"

    with pytest.raises(SystemExit) as ei:
        srv.main(["doctor", "--no-scan"])
    assert ei.value.code == 0


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
