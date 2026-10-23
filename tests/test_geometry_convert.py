"""geometry_convert checked against hand calculations and against independent readers of the written files.

Numbers (all derived by hand, none taken from the tool):

* box 100 x 50 x 20 mm: volume 100*50*20 = 100000 mm^3, area 2*(100*50 + 100*20 + 50*20) = 16000 mm^2, 12 triangles.
* cylinder diameter 20 mm x height 30 mm: V = pi*10^2*30 = 9424.7780 mm^3.  A polygon of n sides inscribed in the circle has
  area n/2 * r^2 * sin(2 pi / n); the tessellation puts n = (triangles + 4) / 4 nodes on the rim (2n triangles on the wall, n-2 on
  each cap).  The missing volume is at most perimeter * deflection * height = 2 pi 10 * 0.01 * 30 = 18.8496 mm^3.
* GLB: model (x, y, z) mm -> glTF (x, z, -y) / 1000 m.  The box [0,100] x [0,50] x [0,20] becomes X [0, 0.1], Y [0, 0.02], Z [-0.05, 0]:
  accessor max [0.1, 0.02, 0], min [0, 0, -0.05].  A 100 mm edge along model Z reads 0.1 along glTF +Y.
* inch: a 25.4 mm cube is a 1 inch cube; 25.4^3 = 16387.064 mm^3.
Engine tests skip without OCP.
"""

import hashlib
import json
import math
import re
import struct
import sys
from pathlib import Path

import pytest
from convert_readers import (
    ascii_stl,
    binary_stl,
    box_triangles,
    glb_volume_mm3,
    read_binary_stl,
    read_glb,
    signed_volume,
    total_area,
)
from fastmcp import Client

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.geometry import convert as conversion
from eng_toolkit.domains.geometry.tools import CONVERT_DESCRIPTION, convert
from eng_toolkit.errors import ToolFailure
from eng_toolkit.gateway.server import gateway

NEED_OCP = "OCP is not installed; run: uv sync --extra cad"
BOX = (100.0, 50.0, 20.0)
BOX_VOLUME, BOX_AREA = 100000.0, 16000.0
CYL_VOLUME = math.pi * 100.0 * 30.0  # 9424.7780


@pytest.fixture(scope="module")
def ocp():
    return pytest.importorskip("OCP", reason=NEED_OCP)


@pytest.fixture()
def cad(ocp):
    import cad_fixtures

    return cad_fixtures


def run(path, fmt, **kwargs) -> ToolResult:
    fn = getattr(convert, "fn", convert)
    result = fn(input_path=str(path), output_format=fmt, **kwargs)
    assert isinstance(result, ToolResult)
    text = result.model_dump_json()
    assert not re.search(r"\b(?:Infinity|NaN|-Infinity)\b", text), "a non-finite number reached the result"
    ToolResult.model_validate(json.loads(text))
    return result


def failed(result: ToolResult, family: str) -> ToolResult:
    assert result.ok is False, "expected a failure"
    assert result.error.family == family, result.error
    assert result.warnings == []
    assert result.value is None and result.values == {}
    return result


def artifact(result: ToolResult, suffix: str) -> Path:
    (found,) = [a for a in result.artifacts if a.path.endswith(suffix)]
    assert hashlib.sha256(Path(found.path).read_bytes()).hexdigest() == found.sha256
    return Path(found.path)


def stl_file(tmp_path: Path, triangles, name="m.stl") -> Path:
    path = tmp_path / name
    path.write_bytes(binary_stl(triangles))
    return path


def loss_kinds(result: ToolResult) -> set[str]:
    return {loss["kind"] for loss in result.data["losses"]}


# --------------------------------------------------------------------------- pure: the STL reader


def test_the_hand_listed_box_triangles_enclose_100000():
    triangles = box_triangles(*BOX)
    assert len(triangles) == 12
    assert signed_volume(triangles) == pytest.approx(100 * 50 * 20)
    assert total_area(triangles) == pytest.approx(2 * (100 * 50 + 100 * 20 + 50 * 20))


def test_parse_stl_reads_binary_and_ascii_alike():
    triangles = box_triangles(*BOX)
    binary = conversion.parse_stl(binary_stl(triangles))
    text = conversion.parse_stl(ascii_stl(triangles).encode())
    assert binary.shape == text.shape == (12, 3, 3)
    assert (binary == text).all()
    volume, area = conversion.mesh_volume_area(binary.reshape(-1, 3), list(range(36)))
    assert volume == pytest.approx(BOX_VOLUME) and area == pytest.approx(BOX_AREA)


def test_a_binary_stl_whose_header_starts_with_solid_is_still_binary():
    triangles = box_triangles(*BOX)
    data = binary_stl(triangles, header=b"solid exported by a CAD system".ljust(80, b" "))
    assert conversion.parse_stl(data).shape == (12, 3, 3)


def test_mesh_volume_is_negative_inside_out_and_independent_of_position():
    triangles = box_triangles(*BOX)
    flipped = [(a, c, b) for a, b, c in triangles]
    v = lambda ts: conversion.mesh_volume_area(__import__("numpy").array(ts).reshape(-1, 3), list(range(3 * len(ts))))[0]  # noqa: E731
    assert v(flipped) == pytest.approx(-BOX_VOLUME)
    moved = [tuple((x + 5e5, y - 3e5, z + 1e5) for x, y, z in t) for t in triangles]
    assert v(moved) == pytest.approx(BOX_VOLUME, rel=1e-9)  # 100000 mm^3 at 500 m from the origin


@pytest.mark.parametrize(
    ("data", "why"),
    [
        (b"", "empty"),
        (b"not an stl at all", "text without solid"),
        (b"solid x\nendsolid x\n", "ascii without facets"),
        (b"solid x\n vertex 1 2 3\n vertex 4 5 6\nendsolid x\n", "two vertex lines"),
        (b"solid x\n vertex 1 2 3\n vertex 4 5 six\n vertex 7 8 9\nendsolid x\n", "not a number"),
        (b"solid x\n vertex 1 2 3\n vertex 4 5 nan\n vertex 7 8 9\nendsolid x\n", "nan"),
        (b"solid x\n vertex 1 2 3\n vertex 4 5 inf\n vertex 7 8 9\nendsolid x\n", "inf"),
        (b"\x00" * 80 + struct.pack("<I", 0), "binary with zero triangles"),
        (binary_stl(box_triangles(*BOX))[:-7], "truncated binary"),
        (binary_stl(box_triangles(*BOX)) + b"\x00", "binary with a trailing byte"),
    ],
)
def test_parse_stl_refuses_what_is_not_an_stl(data, why):
    with pytest.raises(ToolFailure) as caught:
        conversion.parse_stl(data)
    assert caught.value.family == "input.invalid", why


@pytest.mark.parametrize("text", ["1e150", "1e308", "2e6", "-1e30"])
def test_parse_stl_refuses_absurd_coordinates(text):
    data = f"solid x\n vertex 0 0 0\n vertex {text} 0 0\n vertex 0 1 0\nendsolid x\n".encode()
    with pytest.raises(ToolFailure) as caught:
        conversion.parse_stl(data)
    assert caught.value.family == "input.out_of_range"


def test_parse_stl_accepts_a_coordinate_just_inside_the_limit():
    data = b"solid x\n vertex 0 0 0\n vertex 999999 0 0\n vertex 0 1 0\nendsolid x\n"
    assert conversion.parse_stl(data).shape == (1, 3, 3)


# --------------------------------------------------------------------------- pure: parameters and STEP units


@pytest.mark.parametrize(("text", "mm"), [("0.01 mm", 0.01), ("0.001 mm", 0.001), ("100 mm", 100.0), ("0.0001 m", 0.1), ("0.01mm", 0.01), ("0.001 inch", 0.0254)])
def test_deflection_inside_the_bounds_parses(text, mm):
    assert conversion.parse_deflection(text) == pytest.approx(mm)


@pytest.mark.parametrize("text", ["0.0009 mm", "100.1 mm", "1 m", "1e150 mm", "1e308 mm", "1e308 m", "1e300 km", "1e-300 mm", "1e-5 inch"])  # 1e-5 inch = 0.000254 mm, under the floor
def test_deflection_outside_the_bounds_is_out_of_range_without_printing_infinity(text):
    with pytest.raises(ToolFailure) as caught:
        conversion.parse_deflection(text)
    assert caught.value.family == "input.out_of_range"
    assert "inf" not in caught.value.message.lower()


def test_deflection_needs_a_length_unit():
    with pytest.raises(ToolFailure) as caught:
        conversion.parse_deflection("0.01")
    assert caught.value.family == "units.bare_number"
    with pytest.raises(ToolFailure) as caught:
        conversion.parse_deflection("0.01 kg")
    assert caught.value.family == "units.dimension_mismatch"


def test_output_format_is_one_of_four():
    assert [conversion.parse_format(f) for f in (" STEP", "Stl", "glb", "BREP ")] == ["step", "stl", "glb", "brep"]
    for bad in ("", "obj", "stp", ".step", "dxf"):
        with pytest.raises(ToolFailure) as caught:
            conversion.parse_format(bad)
        assert caught.value.details["allowed"] == ["step", "stl", "glb", "brep"]


def test_output_name_defaults_to_a_safe_input_stem():
    assert conversion.output_name("", "C:/cad/my part (v2).step") == "my_part__v2_"
    assert conversion.output_name("", "x/零件.step") == "__"  # every non-ASCII character becomes '_'
    assert conversion.output_name("", "a" * 80 + ".step") == "a" * 64
    assert conversion.output_name("widget-1", "a.step") == "widget-1"
    for bad in ("a b", "a/b", "../x", "x" * 65, "ü"):
        with pytest.raises(ToolFailure):
            conversion.output_name(bad, "a.step")


STEP_MM = "#9=( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );\n#10=( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) );"
STEP_M = "#9=( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT($,.METRE.) );"
STEP_INCH = "#9=( CONVERSION_BASED_UNIT('INCH',#12) LENGTH_UNIT() NAMED_UNIT(#11) );"


@pytest.mark.parametrize(("text", "unit"), [(STEP_MM, "millimetre"), (STEP_M, "metre"), (STEP_INCH, "inch"), ("DATA;\nENDSEC;", None)])
def test_the_declared_step_length_unit(text, unit):
    assert conversion.step_length_unit(text) == unit


# --------------------------------------------------------------------------- engine-free: STL in, STL or GLB out


def test_stl_to_stl_keeps_the_triangles_and_the_volume(tmp_path):
    source = stl_file(tmp_path, box_triangles(*BOX), "box.stl")
    before = source.read_bytes()
    r = run(source, "stl")
    assert r.ok, r.error
    out = read_binary_stl(artifact(r, "box.stl"))
    assert sorted(out) == sorted(box_triangles(*BOX))  # float32 holds 100, 50, 20 exactly
    assert signed_volume(out) == pytest.approx(BOX_VOLUME)
    assert r.values["volume_in"].quantity == pytest.approx(BOX_VOLUME)
    assert r.values["volume_out"].quantity == pytest.approx(BOX_VOLUME)
    assert r.value == r.values["volume_out"]
    assert r.values["triangle_count"].quantity == 12
    assert r.values["volume_relative_change"].quantity == pytest.approx(0.0, abs=1e-12)
    assert source.read_bytes() == before


def test_stl_to_glb_converts_to_metres_and_y_up(tmp_path):
    r = run(stl_file(tmp_path, box_triangles(*BOX)), "glb")
    assert r.ok, r.error
    parsed = read_glb(artifact(r, "m.glb"))
    assert parsed["min"] == pytest.approx([0.0, 0.0, -0.05], rel=1e-6, abs=1e-9)
    assert parsed["max"] == pytest.approx([0.1, 0.02, 0.0], rel=1e-6, abs=1e-9)
    assert len(parsed["vertices"]) == 8 and len(parsed["triangles"]) == 12
    assert glb_volume_mm3(parsed) == pytest.approx(BOX_VOLUME, rel=1e-6)  # outward orientation survived the axis change
    assert "glb_axes_and_unit" in loss_kinds(r)
    assert r.data["output"]["glb"]["conversion"] == "model (x, y, z) mm -> glTF (x, z, -y) / 1000 m"


def test_an_ascii_stl_converts_like_the_binary_one(tmp_path):
    path = tmp_path / "text.stl"
    path.write_text(ascii_stl(box_triangles(*BOX)))
    r = run(path, "stl")
    assert r.ok, r.error
    assert r.values["volume_in"].quantity == pytest.approx(BOX_VOLUME)
    assert signed_volume(read_binary_stl(artifact(r, "text.stl"))) == pytest.approx(BOX_VOLUME)


def test_stl_input_says_its_unit_is_assumed_and_the_deflection_is_ignored(tmp_path):
    r = run(stl_file(tmp_path, box_triangles(*BOX)), "stl", mesh_deflection="5 mm")
    assert {"input_units", "mesh_deflection_ignored", "coordinate_precision", "stl_format"} <= loss_kinds(r)
    assert r.data["output"]["source"] == "the input triangles, copied"
    assert "deflection_mm" not in r.data["output"]


@pytest.mark.parametrize("fmt", ["step", "brep"])
def test_an_stl_is_never_turned_into_a_solid(tmp_path, fmt):
    r = failed(run(stl_file(tmp_path, box_triangles(*BOX)), fmt), "geometry.needs_brep")
    assert "not a solid" in r.error.message and "STL" in r.error.message
    assert r.error.details["allowed"] == ["stl", "glb"]


def test_an_open_stl_has_no_volume_and_says_so(tmp_path):
    r = run(stl_file(tmp_path, box_triangles(*BOX)[:-1]), "stl")
    assert r.ok, r.error
    assert "volume_in" not in r.values and "volume_out" not in r.values and "volume_relative_change" not in r.values
    assert r.value == r.values["surface_area_out"]
    assert any("not a closed" in w for w in r.warnings)
    assert r.data["input"]["closed"] is False
    # 11 of the 12 triangles: the missing one is a half of the x = 100 face, 50 * 20 / 2 = 500 mm^2
    assert r.values["surface_area_out"].quantity == pytest.approx(BOX_AREA - 500.0)


def test_an_inside_out_stl_is_refused(tmp_path):
    flipped = [(a, c, b) for a, b, c in box_triangles(*BOX)]
    r = failed(run(stl_file(tmp_path, flipped), "stl"), "input.invalid")
    assert "inside out" in r.error.message


def test_a_stl_with_an_absurd_coordinate_is_out_of_range(tmp_path):
    path = tmp_path / "huge.stl"
    path.write_text("solid x\n vertex 0 0 0\n vertex 1e150 0 0\n vertex 0 1 0\nendsolid x\n")
    failed(run(path, "glb"), "input.out_of_range")


@pytest.mark.parametrize("content", [b"", b"garbage", b"solid x\nendsolid x\n"])
def test_empty_or_unreadable_stl_files_are_refused(tmp_path, content):
    path = tmp_path / "bad.stl"
    path.write_bytes(content)
    failed(run(path, "stl"), "input.invalid")


# --------------------------------------------------------------------------- B-rep to mesh


def test_a_box_to_stl_has_12_triangles_and_volume_100000(cad, tmp_path):
    source = cad.write_step(cad.box(), tmp_path / "box.step")
    r = run(source, "stl")
    assert r.ok, r.error
    triangles = read_binary_stl(artifact(r, "box.stl"))
    assert len(triangles) == 12
    assert signed_volume(triangles) == pytest.approx(BOX_VOLUME, abs=1e-6)
    assert total_area(triangles) == pytest.approx(BOX_AREA, abs=1e-6)
    assert r.value == r.values["volume_out"]
    assert r.values["volume_in"].quantity == pytest.approx(BOX_VOLUME, abs=1e-6)
    assert r.values["volume_out"].quantity == pytest.approx(BOX_VOLUME, abs=1e-6)
    assert abs(r.values["volume_relative_change"].quantity) < 1e-9
    assert r.values["triangle_count"].quantity == 12
    assert r.values["volume_out"].unit == "millimeter ** 3"
    assert r.data["output"]["watertight"] is True
    assert {"exact_surfaces_to_triangles", "face_structure", "coordinate_precision", "stl_format", "names_colours_structure"} <= loss_kinds(r)


@pytest.mark.parametrize("deflection", ["0.5 mm", "0.05 mm", "0.01 mm", "0.002 mm"])
def test_a_cylinder_loses_less_volume_than_the_chord_bound(cad, tmp_path, deflection):
    source = cad.write_step(cad.cylinder(0, 0, 0, 10.0, 30.0), tmp_path / "cyl.step")
    r = run(source, "stl", mesh_deflection=deflection)
    assert r.ok, r.error
    delta = float(deflection.split()[0])
    triangles = read_binary_stl(artifact(r, "cyl.stl"))
    assert len(triangles) % 4 == 0
    n = (len(triangles) + 4) // 4  # 2n wall triangles and n - 2 on each cap
    assert 4 * n - 4 == len(triangles)
    sagitta = 10.0 * (1.0 - math.cos(math.pi / n))
    assert sagitta <= delta
    mesh_volume = signed_volume(triangles)
    assert mesh_volume == pytest.approx(30.0 * n / 2.0 * 100.0 * math.sin(2.0 * math.pi / n), abs=1e-3)  # the inscribed prism
    deficit = CYL_VOLUME - mesh_volume
    bound = 2.0 * math.pi * 10.0 * delta * 30.0
    assert 0.0 < deficit <= bound
    assert r.values["volume_in"].quantity == pytest.approx(CYL_VOLUME, abs=1e-6)
    assert r.values["volume_out"].quantity == pytest.approx(mesh_volume, abs=1e-6)
    assert r.values["volume_relative_change"].quantity == pytest.approx(-deficit / CYL_VOLUME, rel=1e-6)
    assert len(r.warnings) == 1  # only the standing warning: the tessellation met its deflection


def test_the_design_example_cylinder_bound_is_18_85_mm3(cad, tmp_path):
    # 2 pi * 10 mm * 0.01 mm * 30 mm = 0.6283185 * 30 = 18.84956
    assert 2 * math.pi * 10 * 0.01 * 30 == pytest.approx(18.8496, abs=1e-4)
    r = run(cad.write_step(cad.cylinder(0, 0, 0, 10.0, 30.0), tmp_path / "cyl.step"), "stl", mesh_deflection="0.01 mm")
    assert 0.0 < CYL_VOLUME - r.values["volume_out"].quantity <= 18.8496


def test_a_finer_deflection_gets_closer(cad, tmp_path):
    source = cad.write_step(cad.cylinder(0, 0, 0, 10.0, 30.0), tmp_path / "cyl.step")
    coarse = run(source, "stl", mesh_deflection="0.5 mm").values["volume_out"].quantity
    fine = run(source, "stl", mesh_deflection="0.002 mm").values["volume_out"].quantity
    assert CYL_VOLUME - fine < CYL_VOLUME - coarse


def test_a_box_to_glb_is_in_metres_and_y_up(cad, tmp_path):
    r = run(cad.write_step(cad.box(), tmp_path / "box.step"), "glb")
    assert r.ok, r.error
    parsed = read_glb(artifact(r, "box.glb"))
    assert parsed["min"] == pytest.approx([0.0, 0.0, -0.05], rel=1e-6, abs=1e-9)
    assert parsed["max"] == pytest.approx([0.1, 0.02, 0.0], rel=1e-6, abs=1e-9)  # the 20 mm edge (model z) reads 0.02 on glTF +Y
    assert (0.1, 0.02, 0.0) == pytest.approx(max(parsed["vertices"], key=lambda p: (p[0], p[1], p[2])), rel=1e-6, abs=1e-9)
    assert len(parsed["vertices"]) == 8 and len(parsed["triangles"]) == 12
    assert parsed["document"]["nodes"][0]["name"] == "box"
    assert glb_volume_mm3(parsed) == pytest.approx(BOX_VOLUME, rel=1e-6)
    assert r.values["volume_out"].quantity == pytest.approx(glb_volume_mm3(parsed), rel=1e-9)  # read back from the file
    assert abs(r.values["volume_relative_change"].quantity) < 1e-7  # float32 metres: 0.1, 0.05 and 0.02 are not exact in binary
    assert r.data["output"]["glb"]["up_axis"] == "+Y" and r.data["output"]["glb"]["unit"] == "metre"


def test_a_point_100_mm_up_the_model_is_0_1_metres_up_the_file(cad, tmp_path):
    tall = cad.box(length=20.0, width=30.0, height=100.0)
    r = run(cad.write_step(tall, tmp_path / "tall.step"), "glb")
    parsed = read_glb(artifact(r, "tall.glb"))
    assert parsed["max"][1] == pytest.approx(0.1, rel=1e-6)  # 100 mm along model +Z
    assert parsed["max"][0] == pytest.approx(0.02, rel=1e-6)  # 20 mm along model +X
    assert parsed["min"][2] == pytest.approx(-0.03, rel=1e-6)  # 30 mm along model +Y points to glTF -Z


def test_a_cylinder_to_glb_keeps_the_same_triangle_count_and_a_closed_mesh(cad, tmp_path):
    r = run(cad.write_step(cad.cylinder(0, 0, 0, 10.0, 30.0), tmp_path / "cyl.step"), "glb")
    parsed = read_glb(artifact(r, "cyl.glb"))
    assert r.values["triangle_count"].quantity == len(parsed["triangles"]) == 396
    assert r.data["output"]["closed"] is True
    assert 0.0 < CYL_VOLUME - glb_volume_mm3(parsed) <= 18.8496


def test_the_mesh_of_an_open_shell_has_no_volume_and_the_area_is_primary(cad, tmp_path):
    source = cad.write_step(cad.planar_shell(), tmp_path / "plate.step")  # one 100 x 50 mm face
    r = run(source, "stl")
    assert r.ok, r.error
    assert "volume_out" not in r.values and "volume_in" not in r.values
    assert r.value == r.values["surface_area_out"]
    assert r.values["surface_area_out"].quantity == pytest.approx(5000.0, abs=1e-6)
    assert read_binary_stl(artifact(r, "plate.stl")) and total_area(read_binary_stl(artifact(r, "plate.stl"))) == pytest.approx(5000.0)
    assert any("no closed solid" in w for w in r.warnings)


# --------------------------------------------------------------------------- B-rep to B-rep


def test_step_to_brep_to_step_keeps_the_volume_to_1e_9(cad, tmp_path):
    probe_volume = 100000 - 25 * math.pi * 20 - 4 * (4 - math.pi) * 20  # 98360.531085: box - hole - 4 fillet corners, all by hand
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    first = run(source, "brep")
    assert first.ok, first.error
    assert abs(first.values["volume_in"].quantity - probe_volume) / probe_volume < 1e-9
    brep_file = artifact(first, "probe.brep")
    second = run(brep_file, "step", name="again")
    assert second.ok, second.error
    step_file = artifact(second, "again.step")
    third = run(step_file, "brep")
    for result in (first, second, third):
        assert abs(result.values["volume_out"].quantity - probe_volume) / probe_volume < 1e-9
        assert abs(result.values["volume_relative_change"].quantity) < 1e-9
    assert second.data["output"]["faces"] == first.data["input"]["faces"]
    assert second.data["output"]["schema"].startswith("AP214")
    assert "names_colours_structure" in loss_kinds(second) and "exact_surfaces_to_triangles" not in loss_kinds(second)


def _step_coordinates(text: str) -> list[float]:
    """Every number inside a CARTESIAN_POINT of a STEP text."""
    found = re.findall(r"CARTESIAN_POINT\('[^']*',\(([^)]*)\)\)", text)
    return [float(v) for row in found for v in row.split(",")]


def test_step_in_inches_is_converted_to_millimetres(cad, tmp_path):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    source = tmp_path / "cube_inch.step"
    writer = STEPControl_Writer()
    assert Interface_Static.SetCVal_s("write.step.unit", "INCH")
    try:
        writer.Transfer(cad.box(length=25.4, width=25.4, height=25.4), STEPControl_AsIs)
        assert writer.Write(str(source)) == IFSelect_RetDone
    finally:
        Interface_Static.SetCVal_s("write.step.unit", "MM")
    text = source.read_text(errors="replace")
    assert "'INCH'" in text
    assert max(_step_coordinates(text)) == pytest.approx(1.0)  # the file itself is in inches: the cube edge is 1 inch

    r = run(source, "step", name="cube_mm")
    assert r.ok, r.error
    assert r.values["volume_in"].quantity == pytest.approx(25.4**3, rel=1e-9)  # 16387.064 mm^3
    assert r.values["volume_in"].quantity == pytest.approx(16387.064, abs=1e-3)
    assert r.values["volume_out"].quantity == pytest.approx(16387.064, abs=1e-3)
    assert r.data["input"]["declared_length_unit"] == "inch"
    units_loss = next(loss for loss in r.data["losses"] if loss["kind"] == "units")
    assert "inch" in units_loss["detail"] and "millimetres" in units_loss["detail"]
    out_text = artifact(r, "cube_mm.step").read_text(errors="replace")
    assert "'INCH'" not in out_text and ".MILLI." in out_text
    assert max(_step_coordinates(out_text)) == pytest.approx(25.4)


def test_iges_and_brep_inputs_convert_to_step(cad, tmp_path):
    part = cad.probe_part()
    probe_volume = 100000 - 25 * math.pi * 20 - 4 * (4 - math.pi) * 20
    for path in (cad.write_iges(part, tmp_path / "probe.iges"), cad.write_brep(part, tmp_path / "probe.brep")):
        r = run(path, "step")
        assert r.ok, r.error
        assert r.values["volume_out"].quantity == pytest.approx(probe_volume, rel=1e-6)
        assert "units" in loss_kinds(r)


def test_a_shell_converts_to_step_with_the_area_as_primary_value(cad, tmp_path):
    r = run(cad.write_step(cad.planar_shell(), tmp_path / "plate.step"), "step")
    assert r.ok, r.error
    assert r.value == r.values["surface_area_out"]
    assert r.values["surface_area_out"].quantity == pytest.approx(5000.0, abs=1e-6)
    assert "volume_out" not in r.values


def test_two_solids_are_written_together_with_a_warning(cad, tmp_path):
    pair = cad.compound(cad.box(), cad.box(x=200.0))
    r = run(cad.write_step(pair, tmp_path / "pair.step"), "step")
    assert r.ok, r.error
    assert r.values["volume_out"].quantity == pytest.approx(2 * BOX_VOLUME, abs=1e-6)
    assert r.data["output"]["solids"] == 2
    assert any("2 solids" in w for w in r.warnings)


# --------------------------------------------------------------------------- files, names, the input stays as it was


def test_the_input_is_never_overwritten_and_the_output_is_in_the_job_directory(cad, tmp_path):
    source = cad.write_step(cad.box(), tmp_path / "box.step")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    r = run(source, "step")  # same stem and same format as the input
    assert r.ok, r.error
    out = artifact(r, "box.step")
    assert out.resolve() != source.resolve()
    assert Path(r.data["job_dir"]).resolve() == out.parent.resolve()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert r.provenance.input_files[0].sha256 == before == r.data["input_sha256"]
    assert r.data["output"]["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()


def test_name_sets_the_file_and_the_step_product(cad, tmp_path):
    r = run(cad.write_step(cad.box(), tmp_path / "in.step"), "step", name="widget")
    out = artifact(r, "widget.step")
    assert "widget" in out.read_text(errors="replace")
    r2 = run(cad.write_step(cad.box(), tmp_path / "in.step"), "stl")
    artifact(r2, "in.stl")  # empty name: the input's stem


def test_a_missing_file_a_directory_and_a_wrong_extension_are_refused(cad, tmp_path):
    failed(run(tmp_path / "nothing.step", "stl"), "input.file_not_found")
    (tmp_path / "dir.step").mkdir()
    failed(run(tmp_path / "dir.step", "stl"), "input.file_not_found")
    (tmp_path / "x.obj").write_text("v 0 0 0\n")
    r = failed(run(tmp_path / "x.obj", "stl"), "input.unsupported_format")
    assert ".stl" in r.error.details["supported"]


@pytest.mark.parametrize("name", ["empty.step", "empty.iges", "empty.brep"])
def test_empty_and_garbage_cad_files_are_refused(cad, tmp_path, name):
    (tmp_path / name).write_bytes(b"")
    failed(run(tmp_path / name, "stl"), "input.invalid")
    (tmp_path / ("junk_" + name)).write_bytes(b"this is not a CAD file\n" * 20)
    failed(run(tmp_path / ("junk_" + name), "stl"), "input.invalid")


def test_an_unknown_output_format_is_refused_before_the_file_is_read(tmp_path):
    r = failed(run(tmp_path / "does-not-matter.step", "obj"), "input.invalid")
    assert r.error.details["param"] == "/output_format"  # JSON pointer (was the bare name)
    assert r.provenance.input_files == []


def test_a_bad_name_is_refused(cad, tmp_path):
    r = failed(run(cad.write_step(cad.box(), tmp_path / "b.step"), "stl", name="a b"), "input.invalid")
    assert r.error.details["param"] == "/name"


def test_missing_engine_gives_an_install_hint_but_stl_to_stl_still_works(cad, tmp_path, monkeypatch):
    step = cad.write_step(cad.box(), tmp_path / "box.step")
    stl = stl_file(tmp_path, box_triangles(*BOX))
    monkeypatch.setitem(sys.modules, "OCP", None)
    r = failed(run(step, "stl"), "engine.unavailable")
    assert "uv sync --extra cad" in r.error.message
    assert run(stl, "glb").ok


# --------------------------------------------------------------------------- magnitudes: nothing absurd, nothing non-finite


def test_a_model_far_from_physical_size_is_out_of_range(cad, tmp_path):
    big = cad.write_step(cad.box(length=1e9, width=1e9, height=1e9), tmp_path / "big.step")
    failed(run(big, "step"), "input.out_of_range")
    failed(run(big, "stl"), "input.out_of_range")


def test_a_step_with_1e150_coordinates_never_yields_a_non_finite_result(cad, tmp_path):
    source = cad.write_step(cad.box(length=1e9, width=1e9, height=1e9), tmp_path / "big.step")
    text = source.read_text()
    assert "1.E+09" in text
    edited = tmp_path / "huge.step"
    edited.write_text(text.replace("1.E+09", "1.E+150"))
    for fmt in ("step", "stl", "glb", "brep"):
        r = run(edited, fmt)
        assert not r.ok
        assert r.error.family in ("input.out_of_range", "input.invalid"), (fmt, r.error)  # OpenCASCADE drops such a face: no faces left
    moderate = tmp_path / "moderate.step"  # 1e20 mm is read as written, and is beyond a kilometre
    moderate.write_text(text.replace("1.E+09", "1.E+20"))
    for fmt in ("step", "glb"):
        failed(run(moderate, fmt), "input.out_of_range")


def test_a_mesh_too_fine_for_the_model_size_is_refused(cad, tmp_path):
    cube = cad.write_step(cad.box(length=5000.0, width=5000.0, height=5000.0), tmp_path / "cube.step")  # 8660 mm across
    r = failed(run(cube, "stl", mesh_deflection="0.001 mm"), "input.out_of_range")  # ratio 8.7e6 > 5e6
    assert r.error.details["param"] == "/mesh_deflection"
    assert run(cube, "stl", mesh_deflection="0.01 mm").ok  # ratio 8.7e5


# --------------------------------------------------------------------------- through the gateway (a real MCP client)


async def _call(name: str, args: dict) -> ToolResult:
    async with Client(gateway) as client:
        raw = await client.call_tool(name, args)
    data = raw.structured_content if getattr(raw, "structured_content", None) else json.loads(raw.content[0].text)
    return ToolResult.model_validate(data)


@pytest.mark.asyncio
async def test_the_gateway_lists_the_tool_with_its_description():
    async with Client(gateway) as client:
        tools = await client.list_tools()
    tool = next(t for t in tools if t.name == "geometry_convert")
    assert tool.description == CONVERT_DESCRIPTION
    schema = tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema
    assert schema["required"] == ["input_path", "output_format"]
    assert set(schema["properties"]) == {"input_path", "output_format", "mesh_deflection", "name"}
    assert all(p["type"] == "string" and p["description"] for p in schema["properties"].values())


@pytest.mark.asyncio
async def test_a_real_mcp_call_converts_a_step_box_to_stl(cad, tmp_path):
    source = cad.write_step(cad.box(), tmp_path / "box.step")
    r = await _call("geometry_convert", {"input_path": str(source), "output_format": "stl"})
    assert r.ok, r.error
    assert r.review_status == "unreviewed"
    out = artifact(r, "box.stl")
    assert signed_volume(read_binary_stl(out)) == pytest.approx(BOX_VOLUME, abs=1e-6)
    assert r.provenance.input_files[0].sha256 == hashlib.sha256(source.read_bytes()).hexdigest()


HUGE_DEFLECTIONS = ["1e150 mm", "1e200 mm", "1e308 mm", "1e308 m", "1e308 km", "1e150 m", "1e-308 mm", "1e400 mm", "-1e308 mm"]


@pytest.mark.asyncio
@pytest.mark.parametrize("deflection", HUGE_DEFLECTIONS)
async def test_huge_deflections_through_the_gateway_are_refused_cleanly(tmp_path, deflection):
    source = stl_file(tmp_path, box_triangles(*BOX))
    r = await _call("geometry_convert", {"input_path": str(source), "output_format": "glb", "mesh_deflection": deflection})
    assert not r.ok
    assert r.error.family in ("input.out_of_range", "input.invalid")
    assert r.value is None and r.values == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("coordinate", ["1e150", "1e308", "1e30"])
async def test_huge_stl_coordinates_through_the_gateway_are_refused_cleanly(tmp_path, coordinate):
    path = tmp_path / "huge.stl"
    path.write_text(f"solid x\n vertex 0 0 0\n vertex {coordinate} 0 0\n vertex 0 1 0\nendsolid x\n")
    r = await _call("geometry_convert", {"input_path": str(path), "output_format": "stl"})
    assert not r.ok and r.error.family == "input.out_of_range"


@pytest.mark.asyncio
async def test_a_huge_step_through_the_gateway_is_refused_cleanly(cad, tmp_path):
    source = cad.write_step(cad.box(length=1e9, width=1e9, height=1e9), tmp_path / "big.step")
    edited = tmp_path / "huge.step"
    edited.write_text(source.read_text().replace("1.E+09", "1.E+300"))
    for path in (source, edited):
        r = await _call("geometry_convert", {"input_path": str(path), "output_format": "glb"})
        assert not r.ok and r.error.family in ("input.out_of_range", "input.invalid")


@pytest.mark.asyncio
async def test_a_real_mcp_call_stl_to_glb_without_an_engine(tmp_path):
    source = stl_file(tmp_path, box_triangles(*BOX))
    r = await _call("geometry_convert", {"input_path": str(source), "output_format": "glb", "name": "crate"})
    assert r.ok, r.error
    parsed = read_glb(artifact(r, "crate.glb"))
    assert parsed["max"] == pytest.approx([0.1, 0.02, 0.0], rel=1e-6, abs=1e-9)
