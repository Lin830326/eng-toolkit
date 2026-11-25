"""Regression tests for four gaps of the geometry domain (before -> after):

1. closed solids: ``geometry_check_dfm`` accepted a SOLID made of an open 5-face box shell (saved as .brep) and a box with a
   loose 10 x 10 face beside it, and ``geometry_measure`` reported 80000 mm3 for the open box and 99714.29 mm3 (the loose face's
   share mixed in) for box + face.  Now check_dfm refuses both (``geometry.not_a_solid`` at ``/model_path``) and measure
   reports no volume for the open box and the box's own 100000 mm3 beside the loose face, with the closure in
   ``data.closure``; geometry_view and check_dfm share one check (``dfm.require_closed_solids`` on ``brep.closure``).
2. a DFM finding's faces beyond the 50 listed: the plate with 8 x 8 bosses as sheet metal has 320 off-thickness faces;
   geometry_view coloured 50.  Now it colours all 320, while geometry_check_dfm still lists 50 ids and the total.
3. limits: ``{"min_wall": "<400 nines> mm"}`` and ``{"max_hole_depth_ratio": <400 nines>}`` gave ``engine.failure`` in
   geometry_check_dfm.  Now every bad limit is refused at its own pointer, by the one validator both tools use.
4. the STEP unit trap: ``write.step.unit`` set before a STEP writer exists is ignored (test_metrology_check_scan.py fixed).

Hand values: box 100 x 50 x 20 mm = 100000 mm3, area 16000 mm2; the 10 x 10 face adds 100 mm2 and lies at x 200..210, so
the size is 210 mm; the five-face box (without its last face, a 100 x 50 face) has area 16000 - 5000 = 11000 mm2 and four
free edges (the rim of the missing face).  Engine tests skip without OCP.
"""

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from eng_toolkit import brep
from eng_toolkit.contract import ToolResult, inputs_hash
from eng_toolkit.domains.geometry import dfm
from eng_toolkit.domains.geometry.tools import check_dfm, convert, measure, view

NEED_OCP = "OCP is not installed; run: uv sync --extra cad"
NINES = "9" * 400


@pytest.fixture(scope="module")
def ocp():
    return pytest.importorskip("OCP", reason=NEED_OCP)


@pytest.fixture(scope="module")
def cad(ocp):
    import cad_fixtures

    return cad_fixtures


def open_box(cad):
    """A SOLID whose one shell holds five of the box's six faces: open at the sixth."""
    from OCP.BRep import BRep_Builder
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Shell, TopoDS_Solid

    builder = BRep_Builder()
    shell = TopoDS_Shell()
    builder.MakeShell(shell)
    explorer = TopExp_Explorer(cad.box(), TopAbs_FACE)
    for _ in range(5):
        builder.Add(shell, explorer.Current())
        explorer.Next()
    body = TopoDS_Solid()
    builder.MakeSolid(body)
    builder.Add(body, shell)
    return body


def loose_face():
    """A 10 x 10 mm planar face at x 200..210, z = 0: no solid."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

    return BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(200, 0, 0), gp_Dir(0, 0, 1)), 0.0, 10.0, 0.0, 10.0).Face()


@pytest.fixture(scope="module")
def models(cad, tmp_path_factory):
    import dfm_fixtures as fx

    root = tmp_path_factory.mktemp("gaps")
    plate = cad.box(length=200, width=200, height=2)
    bosses = [cad.box(x=10 + 22 * i, y=10 + 22 * j, z=2, length=3, width=3, height=1) for i in range(8) for j in range(8)]
    return {
        "box": cad.write_step(cad.box(), root / "box.step"),
        "open_brep": cad.write_brep(open_box(cad), root / "open.brep"),
        "open_step": cad.write_step(open_box(cad), root / "open.step"),
        "loose_step": cad.write_step(cad.compound(cad.box(), loose_face()), root / "loose.step"),
        "loose_brep": cad.write_brep(cad.compound(cad.box(), loose_face()), root / "loose.brep"),
        "bosses": cad.write_step(fx.fuse(plate, cad.compound(*bosses)), root / "bosses.step"),
    }


def call(tool, **kwargs) -> ToolResult:
    result = getattr(tool, "fn", tool)(**kwargs)
    assert isinstance(result, ToolResult)
    text = result.model_dump_json()
    ToolResult.model_validate(json.loads(text))
    assert result.review_status == "unreviewed"
    assert "NaN" not in text and "Infinity" not in text or not result.ok
    return result


def refused(result: ToolResult, family: str, param: str) -> ToolResult:
    assert result.ok is False, "expected a refusal"
    assert result.error.family == family, result.error
    assert result.error.details["param"] == param, result.error
    assert result.value is None and result.values == {}
    return result


def artifact_json(result: ToolResult, name: str) -> dict:
    (found,) = [a for a in result.artifacts if Path(a.path).name == name]
    return json.loads(Path(found.path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- gap 1: closed solids


@pytest.mark.parametrize("key", ["open_brep", "loose_step", "loose_brep"])
def test_check_dfm_refuses_what_is_not_closed_solids_only(models, key):
    r = refused(call(check_dfm, model_path=str(models[key])), "geometry.not_a_solid", "/model_path")
    details = r.error.details
    if key == "open_brep":
        assert (details["open_shells"], details["free_edges"], details["faces_outside_solids"]) == (1, 4, 0)
        assert "1 of 1 shells of its solids are open (4 free edges)" in r.error.message
    else:
        assert (details["open_shells"], details["faces_outside_solids"], details["loose_face_ids"]) == (0, 1, [7])
        assert "1 faces lie outside any solid" in r.error.message


def test_check_dfm_still_refuses_the_open_box_as_step_and_accepts_a_closed_box(models):
    r = refused(call(check_dfm, model_path=str(models["open_step"])), "geometry.not_a_solid", "/model_path")
    assert "no closed solid" in r.error.message and r.error.details["solids"] == 0
    assert call(check_dfm, model_path=str(models["box"])).ok


def test_geometry_view_gives_the_same_refusal_as_check_dfm(models):
    for key in ("open_brep", "loose_step"):
        shown = refused(call(view, model_path=str(models[key])), "geometry.not_a_solid", "/model_path")
        checked = call(check_dfm, model_path=str(models[key]))
        assert shown.error.details == checked.error.details


def test_view_and_check_dfm_run_the_one_closure_check(models, monkeypatch):
    seen: list[str] = []
    original = dfm.require_closed_solids

    def recording(solid, volume_mm3, *, purpose):
        seen.append(purpose)
        return original(solid, volume_mm3, purpose=purpose)

    calls = []
    original_closure = brep.closure
    monkeypatch.setattr(dfm, "require_closed_solids", recording)
    monkeypatch.setattr(brep, "closure", lambda solid: calls.append(1) or original_closure(solid))
    assert call(check_dfm, model_path=str(models["box"])).ok
    assert call(view, model_path=str(models["box"])).ok
    assert len(seen) == 2 and "manufacturability" in seen[0] and "coloured view" in seen[1]
    assert len(calls) == 2


def test_measure_reports_no_volume_for_a_solid_with_an_open_shell(models):
    """Before: volume 80000 mm3 and mass 0.08 kg for a box with no lid."""
    r = call(measure, model_path=str(models["open_brep"]), density="1000 kg/m^3")
    assert r.ok, r.error
    assert "volume" not in r.values and "mass" not in r.values
    assert r.value == r.values["surface_area"] and r.value.quantity == pytest.approx(11000.0, abs=1e-6)
    text = " ".join(r.warnings)
    assert "1 of 1 shells are open (4 free edges" in text and "density was given" in text
    closure = r.data["closure"]
    assert closure == {
        "closed_solids_only": False,
        "solids": 1,
        "shells": 1,
        "open_shells": 1,
        "free_edges": 4,
        "faces_outside_solids": 0,
        "loose_face_ids": [],
    }
    # the centroid is the surface's, not the meaningless volume's: the five faces' area centre
    assert r.data["centroid_mm"] == pytest.approx([50.0, 25.0, r.data["centroid_mm"][2]], abs=1e-6)


@pytest.mark.parametrize("key", ["loose_step", "loose_brep"])
def test_measure_reports_the_solid_alone_beside_a_loose_face(models, key):
    """Before: 99714.2857 mm3 (the loose face's share of the volume integral mixed in) and no warning."""
    r = call(measure, model_path=str(models[key]), density="1000 kg/m^3")
    assert r.ok, r.error
    assert r.value == r.values["volume"] and r.values["volume"].quantity == pytest.approx(100000.0, abs=1e-6)
    assert r.values["mass"].quantity == pytest.approx(0.1, abs=1e-12)
    assert r.values["surface_area"].quantity == pytest.approx(16100.0, abs=1e-6)  # the loose face counts in the area
    assert r.values["size_x"].quantity == pytest.approx(210.0, abs=1e-6)  # and in the size
    assert r.data["centroid_mm"] == pytest.approx([50.0, 25.0, 10.0], abs=1e-6)  # the box's
    closure = r.data["closure"]
    assert closure["closed_solids_only"] is False and closure["faces_outside_solids"] == 1 and closure["loose_face_ids"] == [7]
    assert r.data["faces"][6]["id"] == 7 and r.data["faces"][6]["area_mm2"] == pytest.approx(100.0, abs=1e-9)
    (warning,) = [w for w in r.warnings if "outside every solid" in w]
    assert "F7" in warning and "not in volume or mass" in warning
    saved = artifact_json(r, "inventory.json")
    assert saved["closure"]["loose_face_ids"] == [7]


def test_measure_of_closed_solids_is_unchanged(models):
    r = call(measure, model_path=str(models["box"]))
    assert r.ok and r.values["volume"].quantity == pytest.approx(100000.0, abs=1e-6)
    assert r.data["closure"]["closed_solids_only"] is True and r.data["closure"]["loose_face_ids"] == []
    assert not any("open" in w or "outside every solid" in w for w in r.warnings)


def test_measure_lists_loose_faces_only_up_to_inventory_limit(models):
    r = call(measure, model_path=str(models["loose_step"]), inventory_limit=0)
    assert r.ok and r.data["closure"]["loose_face_ids"] == [] and r.data["closure"]["faces_outside_solids"] == 1


def test_the_brep_volume_choices(cad, models):
    open_solid = brep.read_solid(models["open_brep"])
    assert brep.mass_properties(open_solid).volume_mm3 == pytest.approx(80000.0)  # what OpenCASCADE integrates
    assert brep.mass_properties(open_solid, volume="none").volume_mm3 is None
    loose = brep.read_solid(models["loose_brep"])
    assert brep.mass_properties(loose).volume_mm3 == pytest.approx(99714.2857142857, rel=1e-9)
    solids_only = brep.mass_properties(loose, volume="solids")
    assert solids_only.volume_mm3 == pytest.approx(100000.0, abs=1e-6)
    assert solids_only.area_mm2 == pytest.approx(16100.0, abs=1e-6)
    with pytest.raises(ValueError):
        brep.mass_properties(loose, volume="all")
    found = brep.closure(loose)
    assert found.loose_face_ids == (7,) and not found.closed and found.problems() == ["1 faces lie outside any solid (loose faces or shells)"]
    assert brep.closure(brep.read_solid(models["box"])).closed


# --------------------------------------------------------------------------- gap 2: every face of a finding is coloured


def test_view_colours_all_320_faces_and_check_dfm_still_lists_50(models, ocp):
    from view_readers import groups_of_faces, read_view_glb

    report = call(check_dfm, model_path=str(models["bosses"]), process="sheet_metal")
    assert report.ok, report.error
    (uniform,) = [f for f in report.data["findings"] if f["rule"] == "sheet.thickness_uniform"]
    assert len(uniform["face_ids"]) == 50 and uniform["details"]["faces_total"] == 320
    assert "full_face_ids" not in report.model_dump_json()
    saved = artifact_json(report, "dfm_findings.json")
    (saved_uniform,) = [f for f in saved["findings"] if f["rule"] == "sheet.thickness_uniform"]
    assert saved_uniform == uniform and "full_face_ids" not in json.dumps(saved)

    shown = call(view, model_path=str(models["bosses"]), color_by="dfm", process="sheet_metal")
    assert shown.ok, shown.error
    (found,) = [Path(a.path) for a in shown.artifacts if Path(a.path).name == "view.glb"]
    groups = groups_of_faces(read_view_glb(found))
    table = artifact_json(shown, "view.json")
    (row,) = [f for f in table["findings"] if f["rule"] == "sheet.thickness_uniform"]
    assert len(row["face_ids"]) == 320 and row["face_ids"][:50] == uniform["face_ids"]
    assert all(groups[f"F{i}"] == "dfm_error" for i in row["face_ids"])
    assert (row["faces_named"], row["faces_total"], row["faces_not_coloured"], row["faces_listed_by_geometry_check_dfm"]) == (320, 320, 0, 50)
    assert shown.data["findings_partly_coloured"] == []
    assert not any("could not be coloured" in w or "names only" in w for w in shown.warnings)


def test_the_complete_list_is_in_process_only_and_check_dfm_stays_deterministic(models):
    solid = brep.read_solid(models["bosses"])
    inv = brep.inventory(solid)
    report = dfm.run("sheet_metal", solid, inv, dfm.parse_limits("sheet_metal", ""), "+z")
    (uniform,) = [f for f in report.findings if f.rule == "sheet.thickness_uniform"]
    assert len(uniform.face_ids) == dfm.MAX_FACE_IDS_LISTED == 50
    assert len(uniform.full_face_ids) == 320 and dfm.all_face_ids(uniform) == uniform.full_face_ids
    assert uniform.full_face_ids[:50] == uniform.face_ids
    others = [f for f in report.findings if f.rule != "sheet.thickness_uniform"]
    assert all(f.full_face_ids is None and dfm.all_face_ids(f) == f.face_ids for f in others)

    first = call(check_dfm, model_path=str(models["bosses"]), process="sheet_metal")
    second = call(check_dfm, model_path=str(models["bosses"]), process="sheet_metal")
    assert first.data["findings"] == second.data["findings"]
    expected = {
        "model_path": str(models["bosses"]),
        "process": "sheet_metal",
        "limits": "",
        "tool_axis": "",  # the default is empty: +z with a warning (check_dfm tool_axis_defaulted)
        "max_findings": 100,
        "max_seconds": 120,
        "output_unit": "mm",
    }
    assert first.provenance.inputs_hash == second.provenance.inputs_hash == inputs_hash(expected)


# --------------------------------------------------------------------------- gap 3: every limit on its own pointer


BAD_LIMITS = [
    (f'{{"min_wall": "{NINES} mm"}}', "input.out_of_range", "/limits/min_wall"),
    (f'{{"max_hole_depth_ratio": {NINES}}}', "input.out_of_range", "/limits/max_hole_depth_ratio"),
    (f'{{"max_hole_depth_ratio": "{NINES}"}}', "input.out_of_range", "/limits/max_hole_depth_ratio"),
    (f'{{"min_access_directions": -{NINES}}}', "input.out_of_range", "/limits/min_access_directions"),
    ('{"min_wall": "1e400 mm"}', "input.out_of_range", "/limits/min_wall"),
    ('{"min_wall": "inf mm"}', "units.parse_error", "/limits/min_wall"),  # no digits: not '<number> <unit>'
    ('{"min_wall": "NaN mm"}', "units.parse_error", "/limits/min_wall"),
    ('{"max_hole_depth_ratio": NaN}', "input.out_of_range", "/limits/max_hole_depth_ratio"),
    ('{"max_hole_depth_ratio": Infinity}', "input.out_of_range", "/limits/max_hole_depth_ratio"),
    ('{"max_hole_depth_ratio": 1e999}', "input.out_of_range", "/limits/max_hole_depth_ratio"),
    ('{"min_wall": "1e300 m"}', "input.out_of_range", "/limits/min_wall"),  # absurd after the conversion to mm
    ('{"min_wall": "1e306 km"}', "input.out_of_range", "/limits/min_wall"),  # infinite after the conversion to mm
    ('{"tool_radius": "100.001 m"}', "input.out_of_range", "/limits/tool_radius"),
    ('{"max_hole_depth_ratio": 1e7}', "input.out_of_range", "/limits/max_hole_depth_ratio"),
    ('{"min_access_directions": 7}', "input.out_of_range", "/limits/min_access_directions"),
    ('{"min_wall": "-1 mm"}', "input.invalid", "/limits/min_wall"),
    ('{"min_wall": "0 mm"}', "input.invalid", "/limits/min_wall"),
    ('{"max_hole_depth_ratio": 0}', "input.invalid", "/limits/max_hole_depth_ratio"),
    ('{"max_hole_depth_ratio": -2}', "input.invalid", "/limits/max_hole_depth_ratio"),
    ('{"max_hole_depth_ratio": "ten"}', "input.invalid", "/limits/max_hole_depth_ratio"),
    ('{"max_hole_depth_ratio": true}', "input.invalid", "/limits/max_hole_depth_ratio"),
    ('{"max_hole_depth_ratio": [1]}', "input.invalid", "/limits/max_hole_depth_ratio"),
    ('{"min_wall": "1 kg"}', "units.dimension_mismatch", "/limits/min_wall"),
    ('{"min_wall": 1}', "units.bare_number", "/limits/min_wall"),
    ('{"min_wall": "1"}', "units.bare_number", "/limits/min_wall"),
    ('{"min_wall": "1 mm", "tool_radius": "1e9 m"}', "input.out_of_range", "/limits/tool_radius"),  # each key on its own
    ('{"bogus": 1}', "input.invalid", "/limits/bogus"),
    ('{"min_bend_radius_ratio": 1.5}', "input.invalid", "/limits/min_bend_radius_ratio"),  # a sheet_metal key
    ('{"a/b~c": 1}', "input.invalid", "/limits/a~1b~0c"),  # RFC 6901 escaping
    ("[1, 2]", "input.invalid", "/limits"),
    ('"1 mm"', "input.invalid", "/limits"),
    ("{not json", "input.invalid", "/limits"),
    ("[" * 3000, "input.invalid", "/limits"),
    ('{"min_wall": "' + "1" * 5000 + ' mm"}', "input.invalid", "/limits"),  # over 4096 characters
]


@pytest.mark.parametrize(("limits", "family", "pointer"), BAD_LIMITS)
def test_check_dfm_refuses_each_bad_limit_at_its_own_pointer(models, limits, family, pointer):
    """Before: the first two cases (400 nines) were engine.failure (OverflowError) with no pointer."""
    r = refused(call(check_dfm, model_path=str(models["box"]), limits=limits), family, pointer)
    assert r.error.family != "engine.failure"


@pytest.mark.parametrize(("limits", "family", "pointer"), BAD_LIMITS)
def test_geometry_view_uses_the_same_validator(models, limits, family, pointer):
    refused(call(view, model_path=str(models["box"]), color_by="dfm", limits=limits), family, pointer)


def test_parse_limits_is_the_one_validator():
    with pytest.raises(Exception) as caught:
        dfm.parse_limits("cnc_milling", f'{{"min_wall": "{NINES} mm"}}')
    assert caught.value.family == "input.out_of_range" and caught.value.details["param"] == "/limits/min_wall"
    assert dfm.parse_limits("cnc_milling", '{"min_wall": "100 m", "max_hole_depth_ratio": 1000000, "min_access_directions": 6}')["min_wall"] == 100000.0
    assert dfm.parse_limits("sheet_metal", '{"max_thickness": "0.5 inch"}')["max_thickness"] == pytest.approx(12.7)
    assert all(math.isfinite(v) for v in dfm.parse_limits("cnc_milling", "").values())


# --------------------------------------------------------------------------- follow-up A: geometry_convert and closure


CONVERT_BEFORE = {  # measured with the convert.py of the parent commit (14860d8), mesh_deflection 0.1 mm
    ("open_brep", "step"): "geometry.readback_mismatch: read back as 0 solids with volume 0 instead of 1 solid with 80000 mm^3",
    ("open_brep", "brep"): "volume_in = volume_out = 80000 mm^3, primary volume_out",
    ("open_brep", "stl"): "volume_in 80000 mm^3",
    ("open_brep", "glb"): "volume_in 80000 mm^3",
    ("loose_step", "step"): "volume_in = volume_out = 99714.29 mm^3, primary volume_out",
    ("loose_step", "brep"): "volume_in = volume_out = 99714.29 mm^3, primary volume_out",
    ("loose_step", "stl"): "volume_in 99714.29 mm^3",
    ("loose_step", "glb"): "volume_in 99714.29 mm^3",
}


@pytest.mark.parametrize(("key", "fmt"), sorted(CONVERT_BEFORE))
def test_convert_claims_no_volume_for_what_is_not_closed_solids_only(models, key, fmt):
    r = call(convert, input_path=str(models[key]), output_format=fmt, mesh_deflection="0.1 mm")
    assert r.ok, (CONVERT_BEFORE[key, fmt], r.error)
    assert not {"volume_in", "volume_out", "volume_relative_change"} & set(r.values)
    area = 11000.0 if key == "open_brep" else 16100.0
    assert r.value == r.values["surface_area_out"] and r.value.quantity == pytest.approx(area, rel=1e-6)
    assert r.values["surface_area_in"].quantity == pytest.approx(area, abs=1e-6)
    (warning,) = [w for w in r.warnings if "not made of closed solids only" in w]
    (loss,) = [entry for entry in r.data["losses"] if entry["kind"] == "no_volume"]
    closure = r.data["input"]["closure"]
    if key == "open_brep":
        assert "1 of 1 shells of its solids are open (4 free edges)" in warning and "open" in loss["detail"]
        assert (closure["open_shells"], closure["free_edges"], closure["loose_face_ids"]) == (1, 4, [])
    else:
        assert "1 faces lie outside any solid" in warning and "F7" in warning and "outside" in loss["detail"]
        assert (closure["open_shells"], closure["faces_outside_solids"], closure["loose_face_ids"]) == (0, 1, [7])
    assert r.data["output"]["closed"] is False
    if (key, fmt) == ("open_brep", "step"):  # the STEP writer stores an open solid as a shell: said, not a crash
        assert r.data["output"]["solids"] == 0
        assert any("reads back as 0 solid(s) and 5 faces" in w for w in r.warnings)


@pytest.mark.parametrize("fmt", ["step", "brep", "stl", "glb"])
def test_convert_of_a_closed_box_still_verifies_its_volume(models, fmt):
    r = call(convert, input_path=str(models["box"]), output_format=fmt, mesh_deflection="0.1 mm")
    assert r.ok and r.values["volume_in"].quantity == pytest.approx(100000.0, abs=1e-6)
    assert r.data["input"]["closure"]["closed_solids_only"] is True
    assert not any(entry["kind"] == "no_volume" for entry in r.data["losses"])


# --------------------------------------------------------------------------- follow-up B: every refusal names a JSON pointer


def pointer_cases(models, tmp_path):
    empty = tmp_path / "empty.step"
    empty.write_bytes(b"")
    box, stl = str(models["box"]), str(tmp_path / "x.stl")
    return [
        (check_dfm, {"model_path": box, "max_seconds": 0}, "input.out_of_range", "/max_seconds"),
        (check_dfm, {"model_path": box, "tool_axis": "w"}, "input.invalid", "/tool_axis"),
        (check_dfm, {"model_path": box, "max_findings": 0}, "input.invalid", "/max_findings"),
        (check_dfm, {"model_path": box, "output_unit": "kg"}, "units.dimension_mismatch", "/output_unit"),
        (check_dfm, {"model_path": box, "process": "turning"}, "geometry.unsupported_process", "/process"),
        (check_dfm, {"model_path": str(empty)}, "input.invalid", "/model_path"),
        (check_dfm, {"model_path": stl}, "input.unsupported_format", "/model_path"),
        (check_dfm, {"model_path": str(tmp_path / "gone.step")}, "input.file_not_found", "/model_path"),
        (measure, {"model_path": box, "output_unit": "kg"}, "units.dimension_mismatch", "/output_unit"),
        (measure, {"model_path": box, "density": "5 mm"}, "units.dimension_mismatch", "/density"),
        (measure, {"model_path": box, "inventory_limit": 501}, "input.invalid", "/inventory_limit"),
        (measure, {"model_path": box, "queries": "frobnicate F1"}, "input.invalid", "/queries"),
        (measure, {"model_path": box, "queries": "distance F999 F1"}, "geometry.unknown_entity", "/queries"),
        (measure, {"model_path": box, "queries": "thickness P(10 kg, 10 mm, 30 mm) -z"}, "units.dimension_mismatch", "/queries"),
        (measure, {"model_path": str(empty)}, "input.invalid", "/model_path"),
        (measure, {"model_path": stl}, "input.unsupported_format", "/model_path"),
        (convert, {"input_path": box, "output_format": "obj"}, "input.invalid", "/output_format"),
        (convert, {"input_path": box, "output_format": "stl", "name": "a b"}, "input.invalid", "/name"),
        (convert, {"input_path": box, "output_format": "stl", "mesh_deflection": "0 mm"}, "input.out_of_range", "/mesh_deflection"),
        (convert, {"input_path": box, "output_format": "stl", "mesh_deflection": "1 kg"}, "units.dimension_mismatch", "/mesh_deflection"),
        (convert, {"input_path": str(empty), "output_format": "stl"}, "input.invalid", "/input_path"),
        (convert, {"input_path": str(tmp_path / "gone.step"), "output_format": "stl"}, "input.file_not_found", "/input_path"),
    ]


def test_every_refusal_of_measure_check_dfm_and_convert_names_a_json_pointer(models, tmp_path, monkeypatch):
    for tool, kwargs, family, param in pointer_cases(models, tmp_path):
        refused(call(tool, **kwargs), family, param)
    monkeypatch.setattr(dfm, "MAX_FACES", 5)  # the face-count limit (before: details without param)
    r = refused(call(check_dfm, model_path=str(models["box"])), "input.too_large", "/model_path")
    assert r.error.details == {"faces": 6, "limit": 5, "param": "/model_path"}


def test_stl_to_step_and_a_far_away_model_point_at_their_parameters(cad, tmp_path):
    stl = tmp_path / "t.stl"
    stl.write_bytes(b"solid t\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid t\n")
    refused(call(convert, input_path=str(stl), output_format="step"), "geometry.needs_brep", "/output_format")
    big = cad.write_step(cad.box(length=1e9, width=1e9, height=1e9), tmp_path / "big.step")
    refused(call(convert, input_path=str(big), output_format="step"), "input.out_of_range", "/input_path")


# --------------------------------------------------------------------------- gap 4: the STEP unit is set after the writer exists


def test_the_step_unit_is_ignored_before_a_writer_exists(ocp):
    """Why test_metrology_check_scan.py creates the writer first: in a fresh interpreter, write.step.unit is not yet a
    parameter, so setting it fails (and was silently ignored); after a writer exists it is set."""
    script = (
        "from OCP.Interface import Interface_Static\n"
        "from OCP.STEPControl import STEPControl_Writer\n"
        "before = Interface_Static.SetCVal_s('write.step.unit', 'M')\n"
        "STEPControl_Writer()\n"
        "after = Interface_Static.SetCVal_s('write.step.unit', 'M')\n"
        "print(before, after)\n"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120, check=True)
    assert out.stdout.strip().splitlines()[-1] == "False True"


def test_a_step_in_metres_written_after_the_writer_reads_back_in_millimetres(ocp, tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    writer = STEPControl_Writer()
    assert Interface_Static.SetCVal_s("write.step.unit", "M")
    path = tmp_path / "box_m.step"
    try:
        writer.Transfer(BRepPrimAPI_MakeBox(100.0, 50.0, 20.0).Shape(), STEPControl_AsIs)
        assert writer.Write(str(path)) == IFSelect_RetDone
    finally:
        Interface_Static.SetCVal_s("write.step.unit", "MM")
    assert "SI_UNIT($,.METRE.)" in path.read_text(errors="ignore")
    props = brep.mass_properties(brep.read_solid(path))
    assert props.bbox_max_mm == pytest.approx((100.0, 50.0, 20.0), abs=1e-6)
    assert props.volume_mm3 == pytest.approx(100000.0, rel=1e-9)
