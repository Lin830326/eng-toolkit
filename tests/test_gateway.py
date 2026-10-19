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
    "metrology_fit_limits",
    "metrology_stackup_1d",
    "metrology_fit_search",
    "metrology_check_scan",
    "geometry_measure",
    "geometry_make_part",
    "drawing_make_views",
    "drawing_read",
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
    for dist in ("fastmcp", "pint", "pydantic", "fluids", "numpy"):
        assert deps[dist]["status"] == "ok", deps[dist]
        assert deps[dist]["detected_tier"] == "permissive"
    for dist in ("open3d", "cadquery-ocp-novtk", "ezdxf"):
        assert deps[dist]["status"] in ("ok", "not-installed (optional)"), deps[dist]
    assert deps["pymupdf"]["status"] == "not-installed (optional)", deps["pymupdf"]  # the AGPL guard row: it must never be installed
    assert deps["cadquery-ocp-novtk"]["declared_tier"] == "lgpl-dynamic"
    assert "OCCT" in deps["cadquery-ocp-novtk"]["bundled_license"]
    assert deps["open3d"]["optional"] is True and deps["numpy"]["optional"] is False
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
    metrology = next(d for d in r.data["domains"] if d["name"] == "metrology")
    assert set(metrology["tools"]) == {
        "metrology_fit_limits",
        "metrology_stackup_1d",
        "metrology_fit_search",
        "metrology_check_scan",
    }
    assert {e["dist"]: e["tier"] for e in metrology["engines"]} == {
        "pint": "permissive",
        "numpy": "permissive",
        "open3d": "permissive",
        "cadquery-ocp-novtk": "lgpl-dynamic",
    }
    ocp = next(e for e in metrology["engines"] if e["dist"] == "cadquery-ocp-novtk")
    assert ocp["optional"] is True
    assert "OCCT" in ocp["bundled_license"]
    geometry = next(d for d in r.data["domains"] if d["name"] == "geometry")
    assert set(geometry["tools"]) == {"geometry_measure", "geometry_make_part"}
    assert {e["dist"]: e["tier"] for e in geometry["engines"]} == {
        "pint": "permissive",
        "numpy": "permissive",
        "cadquery-ocp-novtk": "lgpl-dynamic",
    }
    ocp = next(e for e in geometry["engines"] if e["dist"] == "cadquery-ocp-novtk")
    assert ocp["optional"] is True
    assert "OCCT" in ocp["bundled_license"]
    drawing = next(d for d in r.data["domains"] if d["name"] == "drawing")
    assert set(drawing["tools"]) == {"drawing_make_views", "drawing_read"}
    assert {e["dist"]: e["tier"] for e in drawing["engines"]} == {
        "pint": "permissive",
        "numpy": "permissive",
        "cadquery-ocp-novtk": "lgpl-dynamic",
        "ezdxf": "permissive",
        "pdfminer.six": "permissive",
    }
    ezdxf = next(e for e in drawing["engines"] if e["dist"] == "ezdxf")
    assert ezdxf["optional"] is True and ezdxf["declared_license"] == "MIT"


def _doctor(monkeypatch, detected: dict[str, tuple[str | None, str | None]]) -> ToolResult:
    """Run the doctor with ``installed_license`` overridden for some distributions."""
    from eng_toolkit.gateway import server as srv

    real = srv.installed_license
    monkeypatch.setattr(srv, "installed_license", lambda dist: detected.get(dist) or real(dist))
    return getattr(srv.toolkit_doctor, "fn", srv.toolkit_doctor)(scan_env=False)


def _dep(r: ToolResult, dist: str) -> dict:
    return next(d for d in r.data["dependencies"] if d["dist"] == dist)


def test_doctor_reports_a_missing_optional_engine_without_failing(monkeypatch):
    r = _doctor(monkeypatch, {"open3d": (None, None), "cadquery-ocp-novtk": (None, None), "ezdxf": (None, None)})
    for dist in ("open3d", "cadquery-ocp-novtk", "ezdxf"):
        row = _dep(r, dist)
        assert row["status"] == "not-installed (optional)"
        assert row["version"] is None
        assert row["optional"] is True
    assert r.ok
    assert r.data["failures"] == []
    assert not any("open3d" in w or "cadquery" in w or "ezdxf" in w for w in r.warnings)


def test_doctor_still_fails_on_a_missing_required_engine(monkeypatch):
    r = _doctor(monkeypatch, {"numpy": (None, None)})
    assert _dep(r, "numpy")["status"] == "missing"
    assert not r.ok
    assert any("numpy" in f for f in r.data["failures"])


def test_doctor_accepts_a_stricter_declared_tier_explained_by_a_bundled_license(monkeypatch):
    r = _doctor(monkeypatch, {"cadquery-ocp-novtk": ("8.0.1.1.0", "Apache-2.0")})
    row = _dep(r, "cadquery-ocp-novtk")
    assert row["status"] == "ok"
    assert row["declared_tier"] == "lgpl-dynamic" and row["detected_tier"] == "permissive"
    assert "OCCT" in row["bundled_license"]
    assert r.ok
    assert not any("cadquery" in w for w in r.warnings)


def test_doctor_still_warns_on_a_real_license_mismatch(monkeypatch):
    # permissive declared, GPL installed: not explained by anything
    r = _doctor(monkeypatch, {"pint": ("0.0.0", "GPL-3.0-only")})
    assert _dep(r, "pint")["status"] == "license-mismatch"
    assert any("'pint'" in w and "declared" in w for w in r.warnings)
    # a bundled license never excuses a looser declaration than the detected tier
    r = _doctor(monkeypatch, {"cadquery-ocp-novtk": ("8.0.1.1.0", "GPL-3.0-only")})
    assert _dep(r, "cadquery-ocp-novtk")["status"] == "license-mismatch"


@pytest.mark.parametrize("tier", ["gpl-isolated", "forbidden"])
@pytest.mark.parametrize("bundled", ["GPL-3.0-only solver bundled in the wheel", ""])
@pytest.mark.parametrize("metadata", ["MIT", "GPL-3.0-only"])
def test_doctor_fails_on_an_installed_engine_declared_gpl_or_forbidden(monkeypatch, tier, bundled, metadata):
    from eng_toolkit.gateway import server as srv
    from eng_toolkit.licenses import Engine

    engine = Engine("numpy", "MIT", tier, "hypothetical in-process engine", ("metrology",), optional=True, bundled_license=bundled)
    monkeypatch.setitem(srv.KNOWN, "numpy", engine)
    r = _doctor(monkeypatch, {"numpy": ("1.0", metadata)})
    row = _dep(r, "numpy")
    assert row["status"] == "policy-violation"
    assert not r.ok
    assert any("numpy" in f and tier in f for f in r.data["failures"]), r.data["failures"]


def test_doctor_does_not_fail_on_an_optional_gpl_engine_that_is_not_installed(monkeypatch):
    from eng_toolkit.gateway import server as srv
    from eng_toolkit.licenses import Engine

    engine = Engine("numpy", "GPL-3.0-only", "gpl-isolated", "separate-process engine", ("metrology",), optional=True)
    monkeypatch.setitem(srv.KNOWN, "numpy", engine)
    r = _doctor(monkeypatch, {"numpy": (None, None)})
    assert _dep(r, "numpy")["status"] == "not-installed (optional)"
    assert r.ok


def test_doctor_fails_when_pymupdf_is_installed(monkeypatch):
    """ezdxf[draw] would pull in PyMuPDF (AGPL): the guard row makes that a policy failure, whatever its metadata says."""
    for metadata in ("AGPL-3.0-only", "MIT"):
        r = _doctor(monkeypatch, {"pymupdf": ("1.24.0", metadata)})
        row = _dep(r, "pymupdf")
        assert row["status"] == "policy-violation" and row["declared_tier"] == "forbidden"
        assert not r.ok
        assert any("pymupdf" in f and "forbidden" in f for f in r.data["failures"]), r.data["failures"]


def test_doctor_names_ezdxf_with_its_license(monkeypatch):
    r = _doctor(monkeypatch, {"ezdxf": ("1.4.5", "MIT")})
    row = _dep(r, "ezdxf")
    assert (row["status"], row["declared_tier"], row["detected_tier"], row["optional"]) == ("ok", "permissive", "permissive", True)
    assert r.ok


def test_doctor_names_pdfminer_with_its_license(monkeypatch):
    r = _doctor(monkeypatch, {"pdfminer.six": ("20260107", "MIT")})
    row = _dep(r, "pdfminer.six")
    assert (row["status"], row["declared_tier"], row["detected_tier"], row["optional"]) == ("ok", "permissive", "permissive", True)
    assert r.ok


def test_doctor_says_whether_file_reads_are_restricted(monkeypatch, tmp_path):
    monkeypatch.delenv("ENG_TOOLKIT_READ_DIRS", raising=False)
    open_ = _doctor(monkeypatch, {}).data["file_reads"]
    assert open_["restricted"] is False and open_["directories"] is None and "http" in open_["note"]
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(tmp_path))
    closed = _doctor(monkeypatch, {}).data["file_reads"]
    assert closed["restricted"] is True and closed["directories"] == 1 and closed["env"] == "ENG_TOOLKIT_READ_DIRS"


def test_doctor_reports_an_empty_variable_as_workspace_only_not_as_unrestricted(monkeypatch):
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", "")
    empty = _doctor(monkeypatch, {}).data["file_reads"]
    assert empty["restricted"] is True and empty["directories"] == 0
    assert "workspace" in empty["note"] and "empty" in empty["note"] and "unrestricted" not in empty["note"]


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
# The budget is per domain, so adding a domain (or a tool inside one) edits no shared number: every domain may expose a few coarse
# tools, the gateway itself two.  A long flat list is what small local models are bad at.
MAX_TOOLS_PER_DOMAIN = 6
GATEWAY_TOOLS = 2


@pytest.mark.asyncio
async def test_tool_schemas_are_small_model_friendly():
    """Few coarse tools; every input is a basic JSON type with a description (no unions/objects)."""
    from collections import Counter

    from eng_toolkit.domains import all_domains

    async with Client(gateway) as client:
        tools = await client.list_tools()
    names = [d.name for d in all_domains()]
    per_domain = Counter(t.name.split("_", 1)[0] for t in tools)
    for domain, count in per_domain.items():
        limit = GATEWAY_TOOLS if domain == "toolkit" else MAX_TOOLS_PER_DOMAIN
        assert count <= limit, (domain, count, [t.name for t in tools if t.name.startswith(domain + "_")])
    assert set(per_domain) <= {"toolkit", *names}, per_domain
    assert len(tools) <= GATEWAY_TOOLS + MAX_TOOLS_PER_DOMAIN * len(names), [t.name for t in tools]
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
