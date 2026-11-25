"""The fem tools through the gateway, as an MCP client sees them: schemas, doctor rows, hostile input refused cleanly, valid JSON out.

Hostile input is tried through ``fastmcp.Client`` and not only by calling the functions, because the client is where a NaN or an infinity in
a result would be rejected, and where a parameter of the wrong type is turned into text.  Cases that fail while the text is parsed need no
program at all; cases that need the model file and the meshed model are skipped when OCP, Gmsh or CalculiX is missing.
"""

import json
import math
import os
from pathlib import Path

import pytest
from fastmcp import Client

from eng_toolkit import external as X
from eng_toolkit.contract import ToolResult
from eng_toolkit.external_catalog import CCX, GMSH
from eng_toolkit.gateway.server import gateway

HAVE_PROGRAMS = X.locate(GMSH)[0] is not None and X.locate(CCX)[0] is not None
try:
    import OCP  # noqa: F401

    HAVE_OCP = True
except ImportError:
    HAVE_OCP = False
needs_everything = pytest.mark.skipif(not (HAVE_PROGRAMS and HAVE_OCP), reason="needs OCP, Gmsh and CalculiX")

BASE = dict(
    element_size="5 mm",
    youngs_modulus="210 GPa",
    poisson_ratio=0.3,
    supports="x = 0 mm",
    loads="force (0 N, 0 N, -1 kN) on x = 100 mm",
)


def _refuse_constant(name):  # pragma: no cover - only runs on a violation
    raise AssertionError(f"{name} in a result: MCP clients reject it")


def parse_strict(text: str):
    return json.loads(text, parse_constant=_refuse_constant)


async def call(name, args, *, raise_on_error=False):
    async with Client(gateway) as client:
        raw = await client.call_tool(name, args, raise_on_error=raise_on_error)
    assert raw.structured_content is not None, raw.content
    for block in raw.content:
        if hasattr(block, "text"):
            parse_strict(block.text)  # the text of the result is strict JSON: no NaN, no Infinity
    parse_strict(json.dumps(raw.structured_content, allow_nan=True))
    return ToolResult.model_validate(raw.structured_content)


def finite(obj):
    if isinstance(obj, float):
        assert math.isfinite(obj), obj
    elif isinstance(obj, dict):
        for value in obj.values():
            finite(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            finite(value)


@pytest.fixture
def bar(tmp_path):
    pytest.importorskip("OCP")
    from tests import cad_fixtures as cad

    return str(cad.write_step(cad.box(0, 0, 0, 100, 10, 10), tmp_path / "bar.step"))


# --------------------------------------------------------------------------- the surface


@pytest.mark.asyncio
async def test_the_fem_tools_are_listed_with_basic_parameter_types_and_the_contract_as_output():
    async with Client(gateway) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert {"fem_mesh", "fem_static"} <= set(tools)
    assert len([n for n in tools if n.startswith("fem_")]) <= 6
    # UAT S3: the headline is a magnitude and the per-axis maxima are values
    assert "magnitude; see values.max_displacement_ux/uy/uz" in tools["fem_static"].description and "data.displacement_note" in tools["fem_static"].description
    for name in ("fem_mesh", "fem_static"):
        tool = tools[name]
        assert tool.output_schema is not None and "review_status" in tool.output_schema.get("properties", {})
        for pname, spec in (getattr(tool, "input_schema", None) or tool.inputSchema)["properties"].items():
            assert spec.get("type") in ("string", "number", "integer"), (name, pname, spec)
            assert spec.get("description"), (name, pname)
    static = tools["fem_static"].description
    for word in ("normal +z at z = 20 mm", "ux uy uz", "pressure", "force (", "rigid-body", "Linear only", "no plasticity"):
        assert word in static, word
    assert "never supplies" in static or "caller never" in static


@pytest.mark.asyncio
async def test_doctor_lists_gmsh_and_calculix_with_their_licences():
    r = await call("toolkit_doctor", {"scan_env": False})
    rows = {row["name"]: row for row in r.data["external_tools"]}
    assert {"gmsh", "ccx"} <= set(rows)
    for name in ("gmsh", "ccx"):
        assert rows[name]["declared_tier"] == "gpl-isolated" and rows[name]["domains"] == ["fem"]
        assert rows[name]["status"] in ("ok", "not-installed (optional)")
        assert rows[name]["found"] == (rows[name]["status"] == "ok")
    assert r.ok, r.data["failures"]


@pytest.mark.asyncio
async def test_a_missing_program_is_reported_before_anything_else_happens(bar, monkeypatch, tmp_path):
    monkeypatch.setenv("ENG_TOOLKIT_GMSH", str(tmp_path / "no-such-gmsh.exe"))
    r = await call("fem_mesh", {"model_path": bar, "element_size": "5 mm"})
    assert not r.ok and r.error.family == "engine.unavailable"
    assert "gmsh.info" in r.error.message and "ENG_TOOLKIT_GMSH" in r.error.message
    assert r.provenance.input_files == []  # nothing was read
    monkeypatch.delenv("ENG_TOOLKIT_GMSH")
    if X.locate(GMSH)[0] is None:  # without Gmsh fem_static names Gmsh first, as it should
        return
    monkeypatch.setenv("ENG_TOOLKIT_CCX", str(tmp_path / "no-such-ccx.exe"))
    r = await call("fem_static", {"model_path": bar, **BASE})
    assert not r.ok and r.error.family == "engine.unavailable" and "calculix.de" in r.error.message


# --------------------------------------------------------------------------- refused while the text is parsed (no program needed)

PARSE_STAGE = [
    ("shell metacharacter", dict(supports="x = 0 mm; rm -rf /"), "input.invalid"),
    ("pipe", dict(supports="x = 0 mm | calc"), "input.invalid"),
    ("ampersand", dict(supports="x = 0 mm & calc"), "input.invalid"),
    ("newline", dict(supports="x = 0 mm\nSystemCall"), "input.invalid"),
    ("carriage return", dict(supports="x = 0 mm\r\nMerge"), "input.invalid"),
    ("backtick", dict(supports="x = `calc` mm"), "input.invalid"),
    ("dollar", dict(loads="force (0 N, 0 N, $(calc) N) on x = 100 mm"), "input.invalid"),
    ("gmsh Include", dict(supports='x = 0 mm; Include "evil.geo";'), "input.invalid"),
    ("calculix include", dict(supports="x = 0 mm; *INCLUDE, INPUT=evil.inp"), "input.invalid"),
    ("calculix keyword", dict(loads="force (0 N, 0 N, 1 N) on x = 100 mm\n*INPUT, INPUT=evil.inp"), "input.invalid"),
    ("path traversal", dict(supports="x = 0 mm\\..\\..\\evil"), "input.invalid"),
    ("load 1e308 N", dict(loads="force (0 N, 0 N, 1e308 N) on x = 100 mm"), "input.out_of_range"),
    ("load 1e308 kN", dict(loads="force (0 N, 0 N, 1e308 kN) on x = 100 mm"), "input.out_of_range"),
    ("pressure 1e308 GPa", dict(loads="pressure 1e308 GPa on x = 100 mm"), "input.out_of_range"),
    ("load NaN text", dict(loads="force (0 N, 0 N, nan N) on x = 100 mm"), "input.invalid"),
    ("load inf text", dict(loads="force (0 N, 0 N, inf N) on x = 100 mm"), "input.invalid"),
    ("zero load", dict(loads="force (0 N, 0 N, 0 N) on x = 100 mm"), "input.invalid"),
    ("load without unit", dict(loads="force (0, 0, 1000) on x = 100 mm"), "units.bare_number"),
    ("load in metres", dict(loads="force (0 m, 0 m, 1 m) on x = 100 mm"), "units.dimension_mismatch"),
    ("E zero", dict(youngs_modulus="0 GPa"), "input.invalid"),
    ("E negative", dict(youngs_modulus="-210 GPa"), "input.invalid"),
    ("E NaN text", dict(youngs_modulus="nan GPa"), "units.parse_error"),
    ("E bare number", dict(youngs_modulus="210000"), "units.bare_number"),
    ("E huge", dict(youngs_modulus="1e308 GPa"), "input.out_of_range"),
    ("E tiny", dict(youngs_modulus="1e-12 Pa"), "input.out_of_range"),
    ("E in metres", dict(youngs_modulus="210 m"), "units.dimension_mismatch"),
    ("nu 0.5", dict(poisson_ratio=0.5), "input.invalid"),
    ("nu 0.7", dict(poisson_ratio=0.7), "input.invalid"),
    ("nu -1", dict(poisson_ratio=-1.0), "input.invalid"),
    ("nu NaN text", dict(poisson_ratio="NaN"), "input.invalid"),
    ("nu Infinity text", dict(poisson_ratio="Infinity"), "input.invalid"),
    ("element size 0", dict(element_size="0 mm"), "input.invalid"),
    ("element size negative", dict(element_size="-5 mm"), "input.invalid"),
    ("element size NaN text", dict(element_size="nan mm"), "units.parse_error"),
    ("element size bare", dict(element_size="5"), "units.bare_number"),
    ("empty supports", dict(supports=""), "input.invalid"),
    ("empty loads", dict(loads="  ;  "), "input.invalid"),
    ("unknown face", dict(supports="w = 0 mm"), "input.invalid"),
    ("unknown dof", dict(supports="x = 0 mm : rotx"), "input.invalid"),
    ("point with two numbers", dict(supports="point(1 mm, 2 mm)"), "input.invalid"),
    ("coordinate 1e308 mm", dict(supports="x = 1e308 mm"), "input.out_of_range"),
    ("far point", dict(supports="point(0 mm, 0 mm, 1e30 m)"), "input.out_of_range"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("label, override, family", PARSE_STAGE, ids=[c[0] for c in PARSE_STAGE])
async def test_hostile_text_is_refused_while_it_is_parsed(label, override, family, tmp_path):
    args = {"model_path": str(tmp_path / "never-read.step"), **BASE, **override}
    r = await call("fem_static", args)
    assert not r.ok and r.error.family == family, r.error
    assert r.review_status == "unreviewed" and r.value is None and r.warnings == []
    assert r.provenance.input_files == []  # the model file was never opened
    finite(r.model_dump(mode="json"))
    assert not list(tmp_path.glob("*.geo")) and not list(tmp_path.glob("*.inp"))


# --------------------------------------------------------------------------- refused after the model is read and meshed


@needs_everything
@pytest.mark.asyncio
async def test_a_good_call_through_the_client_returns_strict_json_and_the_right_numbers(bar):
    r = await call("fem_static", {"model_path": bar, **BASE, "supports": "x = 0 mm : ux; point(0 mm, 5 mm, 0 mm) : uy; point(0 mm, 5 mm, 10 mm) : uy; point(0 mm, 0 mm, 5 mm) : uz", "loads": "force (10 kN, 0 N, 0 N) on x = 100 mm"})
    assert r.ok, r.error
    finite(r.model_dump(mode="json"))
    assert r.data["max_displacement_components_mm"][0] == pytest.approx(10000.0 * 100.0 / (100.0 * 210000.0), rel=1e-4)  # F L / (A E) = 0.047619 mm
    assert r.values["max_von_mises"].quantity == pytest.approx(100.0, rel=1e-4)
    assert r.provenance.engine == "eng_toolkit.fem.static" and r.provenance.input_files[0].bytes > 0
    assert {"cadquery-ocp-novtk", "numpy"} <= set(r.provenance.engine_versions)
    assert r.data["solver"]["ccx_version"] and r.data["solver"]["gmsh_version"]


@needs_everything
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "label, override, family",
    [
        ("tiny element size", dict(element_size="1e-9 mm"), "input.too_large"),
        ("element size 0.5 mm on the bar", dict(element_size="0.5 mm"), "input.too_large"),
        ("element size above the part", dict(element_size="1e9 mm"), "input.out_of_range"),
        ("empty support selection", dict(supports="x = 55 mm"), "fem.empty_selection"),
        ("empty load selection", dict(loads="force (1 kN, 0 N, 0 N) on z = -3 mm"), "fem.empty_selection"),
        ("wrong normal", dict(loads="force (1 kN, 0 N, 0 N) on normal -x at x = 100 mm"), "fem.empty_selection"),
        ("rigid body", dict(supports="x = 0 mm : ux"), "fem.not_constrained"),
        ("loaded support", dict(supports="x = 100 mm"), "fem.overlapping_selection"),
        ("load far beyond the modulus", dict(loads="force (1e12 N, 0 N, 0 N) on x = 100 mm"), "input.out_of_range"),
    ],
)
async def test_refusals_that_need_the_meshed_model(bar, label, override, family):
    r = await call("fem_static", {"model_path": bar, **BASE, **override})
    assert not r.ok and r.error.family == family, r.error
    finite(r.model_dump(mode="json"))
    assert r.value is None and r.warnings == []


@needs_everything
@pytest.mark.asyncio
async def test_non_solid_zero_volume_and_two_solid_files_are_refused(tmp_path):
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeSolid
    from OCP.gp import gp_Pln
    from OCP.TopoDS import TopoDS_Shell

    from tests import cad_fixtures as cad

    plane = BRepBuilderAPI_MakeFace(gp_Pln(), 0.0, 100.0, 0.0, 50.0).Face()
    builder, shell = BRep_Builder(), TopoDS_Shell()
    builder.MakeShell(shell)
    builder.Add(shell, plane)
    builder.Add(shell, plane.Reversed())  # two coincident faces: a "solid" of zero volume
    zero = cad.write_brep(BRepBuilderAPI_MakeSolid(shell).Solid(), tmp_path / "zero.brep")
    sheet = cad.write_step(cad.planar_shell(), tmp_path / "sheet.step")
    two = cad.write_step(cad.compound(cad.box(0, 0, 0, 10, 10, 10), cad.box(50, 0, 0, 10, 10, 10)), tmp_path / "two.step")
    text = tmp_path / "notes.txt"
    text.write_text("x = 0 mm")
    for path, family, fragment in (
        (zero, "input.invalid", "no closed solid with positive volume"),
        (sheet, "input.invalid", "no closed solid with positive volume"),
        (two, "input.unsupported", "2 solids"),
        (text, "input.unsupported_format", ".txt"),
        (tmp_path / "missing.step", "input.file_not_found", "no such file"),
    ):
        for tool, extra in (("fem_mesh", {}), ("fem_static", BASE)):
            args = {"model_path": str(path), "element_size": "5 mm", **({k: v for k, v in extra.items() if k != "element_size"})}
            r = await call(tool, args)
            assert not r.ok and r.error.family == family and fragment in r.error.message, (path.name, tool, r.error)


@needs_everything
@pytest.mark.asyncio
async def test_a_hostile_file_name_cannot_reach_a_program(tmp_path):
    from tests import cad_fixtures as cad

    odd = tmp_path / "bar & calc $(x).step"
    cad.write_step(cad.box(0, 0, 0, 100, 10, 10), odd)
    r = await call("fem_mesh", {"model_path": str(odd), "element_size": "5 mm"})
    assert r.ok, r.error
    job = Path(r.data["job_dir"])
    assert sorted(p.name for p in (job / "gmsh").iterdir() if p.is_file() and p.suffix in (".brep", ".msh")) == ["part.brep", "part.msh"]
    assert os.path.basename(r.provenance.input_files[0].path) == odd.name  # recorded as read; used only as a copy with this name under input/
