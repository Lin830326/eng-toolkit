"""Second independent review: gateway regressions with closed-form reference values."""

import json
import math
from pathlib import Path

import pytest
from fastmcp import Client

from eng_toolkit import brep
from eng_toolkit.contract import ToolResult
from eng_toolkit.gateway.server import gateway


@pytest.fixture
def models(tmp_path):
    pytest.importorskip("OCP")
    import cad_fixtures as cad
    from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert, BRepBuilderAPI_Transform
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
    from OCP.gp import gp_Trsf, gp_Vec

    sphere = BRepBuilderAPI_NurbsConvert(BRepPrimAPI_MakeSphere(10).Shape(), True).Shape()
    translation = gp_Trsf()
    translation.SetTranslation(gp_Vec(-999990.05, 0, 0))
    shapes = {
        "sphere": sphere,
        "sphere30": BRepBuilderAPI_NurbsConvert(BRepPrimAPI_MakeSphere(30).Shape(), True).Shape(),
        "translated": BRepBuilderAPI_Transform(sphere, translation, True).Shape(),
        "box": cad.box(),
        "plate": cad.box(length=1_000_000, width=100, height=0.5),
        "blind": cad.cut(cad.box(length=1_000_000, width=100, height=20), cad.cylinder(50, 50, 0.5, 2, 20)),
        "cup": cad.cut(cad.cylinder(0, 0, 0, 10, 20), cad.cylinder(0, 0, 5, 9.5, 16)),
        "huge": cad.box(length=1e9, width=1e9, height=1e9),
    }
    return {name: cad.write_brep(shape, tmp_path / f"{name}.brep") for name, shape in shapes.items()}


def arguments(tool, path, **extra):
    if tool == "make_part":
        args = {"spec": json.dumps({"features": [{"op": "import", "file": str(path)}]}), "formats": "step"}
    elif tool == "convert":
        args = {"input_path": str(path), "output_format": "brep"}
    else:
        args = {"model_path": str(path)}
    return {**args, **extra}


async def call(tool, path, **extra):
    async with Client(gateway) as client:
        result = await client.call_tool(f"geometry_{tool}", arguments(tool, path, **extra))
    r = ToolResult.model_validate(result.structured_content)
    assert r.review_status == "unreviewed"
    json.dumps(r.model_dump(mode="json"), allow_nan=False)
    return r


def refused(r, pointer, family="input.out_of_range"):
    assert not r.ok, r
    assert r.error.family == family, r.error
    assert r.error.details["param"] == pointer
    assert "traceback" not in str(r.error).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["measure", "view", "convert", "make_part"])
async def test_1_nurbs_sphere_integrals(models, tool):
    r = await call(tool, models["sphere"])
    assert r.ok, r.error
    volume, area = 4 * math.pi * 10**3 / 3, 4 * math.pi * 10**2
    if tool == "view":
        assert r.data["output"]["area_exact_mm2"] == pytest.approx(area, rel=1e-8)
    else:
        assert r.values["volume_in" if tool == "convert" else "volume"].quantity == pytest.approx(volume, rel=1e-8)
        assert r.values["surface_area_out" if tool == "convert" else "surface_area"].quantity == pytest.approx(area, rel=1e-8)
    if tool == "measure":
        assert sum(f["area_mm2"] for f in r.data["faces"]) == pytest.approx(area, rel=1e-8)


@pytest.mark.asyncio
async def test_2_bounds_enclose_nurbs_sphere(models):
    r = await call("measure", models["sphere"])
    assert r.ok, r.error
    assert all(c <= -10 for c in r.data["bbox_min_mm"])
    assert all(c >= 10 for c in r.data["bbox_max_mm"])


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["view", "convert", "measure", "check_dfm"])
async def test_2_translated_sphere_coordinate_limit(models, tool):
    refused(await call(tool, models["translated"]), "/input_path" if tool == "convert" else "/model_path")


@pytest.mark.asyncio
@pytest.mark.parametrize("density", ["1e308 kg/m^3", "1e308 g/mm^3", "1e100 kg/m^3"])
@pytest.mark.parametrize("tool", ["measure", "make_part"])
async def test_3_density_overflow(models, tool, density):
    refused(await call(tool, models["box"], density=density), "/density")


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["plate", "cup"])
async def test_4_5_thin_walls(models, model):
    r = await call("check_dfm", models[model])
    assert r.ok, r.error
    assert r.data["measurements"]["smallest_wall_mm"] == pytest.approx(0.5)
    assert any(f["rule"] == "cnc.min_wall" for f in r.data["findings"])


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["plate", "cup"])
async def test_4_5_thin_wall_colours(models, model):
    r = await call("view", models[model], color_by="dfm", mesh_deflection="1 mm")
    assert r.ok, r.error
    assert r.values["highlighted_face_count"].quantity >= 2


@pytest.mark.asyncio
async def test_4_blind_hole_floor(models):
    r = await call("measure", models["blind"])
    assert r.ok, r.error
    hole, = r.data["holes"]
    assert hole["depth_mm"] == pytest.approx(19.5)
    assert hole["thru"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["view", "convert"])
async def test_6_fine_nurbs_refused_before_meshing(models, tool, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("preflight must refuse before entering the mesher")

    monkeypatch.setattr(brep, "tessellate_faces" if tool == "view" else "triangulate", unexpected)
    extra = {"output_format": "glb"} if tool == "convert" else {}
    r = await call(tool, models["sphere30"], mesh_deflection="0.001 mm", **extra)
    refused(r, "/mesh_deflection")
    assert r.error.details["estimated"] is True
    assert r.error.details["triangles"] > 600_000


def test_6_nurbs_estimate_covers_actual_mesh(models):
    solid = brep.read_solid(models["sphere"])
    estimate = brep.estimate_triangles(solid, 0.005).total
    actual = len(brep.tessellate_faces(solid, 0.005, with_edges=False).triangles)
    assert actual <= estimate, (actual, estimate)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["measure", "check_dfm"])
async def test_7_absurd_model_size(models, tool):
    refused(await call(tool, models["huge"]), "/model_path")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,param,value", [
    ("measure", "output_unit", "mm**400/m**399"),
    ("check_dfm", "output_unit", "mm**400/m**399"),
    ("make_part", "output_unit", "mm**400/m**399"),
    ("convert", "mesh_deflection", "10**400 mm"),
    ("make_part", "mesh_deflection", "10**400 mm"),
    ("view", "mesh_deflection", "10**400 mm"),
])
async def test_8_arithmetic_refusals(models, tool, param, value):
    refused(await call(tool, models["box"], **{param: value}), f"/{param}")


@pytest.mark.asyncio
async def test_1_nonconvergence_is_a_structured_refusal(models, monkeypatch):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    original = BRepGProp.SurfaceProperties_s
    attempts = []

    def unstable(shape, properties, eps):
        measured = GProp_GProps()
        error = original(shape, measured, eps)
        attempts.append(eps)
        properties.Add(measured, 1.0 if len(attempts) % 2 else 2.0)
        return error

    monkeypatch.setattr(BRepGProp, "SurfaceProperties_s", unstable)
    r = await call("measure", models["sphere"])
    refused(r, "/model_path", "geometry.integration_not_converged")
    assert len(attempts) >= 2 and all(b < a for a, b in zip(attempts, attempts[1:]))


@pytest.mark.asyncio
async def test_2_glb_vertices_stay_inside_reported_bounds(models):
    from view_readers import read_view_glb, gltf_to_model_mm

    r = await call("view", models["sphere"])
    assert r.ok, r.error
    parsed = read_view_glb(next(Path(a.path) for a in r.artifacts if a.path.endswith(".glb")))
    bounds = parsed["node"]["extras"]["model_bbox_mm"]
    for primitive in parsed["primitives"]:
        for point in primitive["positions"]:
            assert all(lo <= c <= hi for lo, c, hi in zip(bounds["min"], gltf_to_model_mm(point), bounds["max"]))


@pytest.mark.asyncio
async def test_2_cached_triangulation_keeps_its_deflection_gap(tmp_path):
    pytest.importorskip("OCP")
    import cad_fixtures as cad
    from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere

    shape = BRepBuilderAPI_NurbsConvert(BRepPrimAPI_MakeSphere(10).Shape(), True).Shape()
    BRepMesh_IncrementalMesh(shape, 0.5, False, 0.5, False)
    path = cad.write_brep(shape, tmp_path / "cached.brep")
    r = await call("measure", path)
    assert r.ok, r.error
    assert all(c <= -10 for c in r.data["bbox_min_mm"])
    assert all(c >= 10 for c in r.data["bbox_max_mm"])
    assert r.values["volume"].quantity == pytest.approx(4 * math.pi * 1000 / 3, rel=1e-8)


@pytest.mark.asyncio
async def test_6_worker_timeout_is_structured(models, monkeypatch):
    from eng_toolkit.brep import _mesh_process

    monkeypatch.setattr(_mesh_process, "MESH_SECONDS", 0.0)
    refused(await call("view", models["sphere"]), "/mesh_deflection", "geometry.mesh_budget_exceeded")


def test_6_worker_memory_limit_is_enforced():
    import subprocess
    import sys

    code = (
        "from eng_toolkit.brep._mesh_process import _memory_limit, MESH_MEMORY_BYTES\n"
        "job = _memory_limit()\n"
        "try:\n"
        "    allocation = bytearray(MESH_MEMORY_BYTES + 1)\n"
        "except MemoryError:\n"
        "    raise SystemExit(42)\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=15)
    assert result.returncode == 42, result.stderr.decode(errors="replace")


@pytest.mark.asyncio
async def test_5_partial_angular_and_axial_overlap(tmp_path):
    pytest.importorskip("OCP")
    import cad_fixtures as cad
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    # Outer half-cylinder spans 0..pi; the cutter spans pi/2..3pi/2
    # and only z=5..21. The remaining common wall is 0.5 mm thick.
    outer = BRepPrimAPI_MakeCylinder(10, 20, math.pi).Shape()
    inner = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, 5), gp_Dir(0, 0, 1), gp_Dir(0, 1, 0)), 9.5, 16, math.pi).Shape()
    path = cad.write_brep(cad.cut(outer, inner), tmp_path / "partial.brep")
    r = await call("check_dfm", path)
    assert r.ok, r.error
    assert r.data["measurements"]["smallest_wall_mm"] == pytest.approx(0.5)
    assert any(f["rule"] == "cnc.min_wall" for f in r.data["findings"])
