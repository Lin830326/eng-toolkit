"""geometry_measure checked against hand calculations on CAD files built with OCP.

Box 100 x 50 x 20 mm:
    volume   = 100 * 50 * 20                          = 100000 mm^3
    area     = 2 * (100*50 + 100*20 + 50*20)          = 16000 mm^2
    centroid = (50, 25, 10), bounding box 0..100, 0..50, 0..20
    mass at 2700 kg/m^3 = 1e-4 m^3 * 2700            = 0.27 kg

Probe part = that box, a vertical diameter-10 hole through it at (25, 25), R2 fillets on the 4 outer vertical edges:
    volume = 100000 - 25*pi*20 - 4*(4 - pi)*20       = 98360.531085 mm^3
             (hole: pi*5^2*20; one fillet removes (2^2 - pi*2^2/4)*20 = (4 - pi)*20)
    area   = 16395.699052 mm^2, mass at 2700 kg/m^3  = 0.26557343 kg
    faces  : 6 planes + 4 fillet cylinders + 1 hole cylinder
    hole   : diameter 10, depth 20, drilled downwards (0, 0, -1) from the top face at (25, 25, 20)
    distance(face x = 0, hole) = 25 - 5 = 20, angle(face x = 0, top face) = 90 deg
    area(hole) = pi * 10 * 20 = 628.3185
    thickness from (10, 10, 30) along -z: hits at z = 20 and z = 0 -> 20 mm

Blind hole: the box with a vertical diameter-6 hole 8 mm deep at (50, 25): volume 100000 - pi*9*8 = 99773.80533.
"""

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.geometry.tools import measure

NEED_OCP = "OCP is not installed; run: uv sync --extra cad"
PROBE_VOLUME = 100000 - 25 * math.pi * 20 - 4 * (4 - math.pi) * 20
PROBE_AREA = 16395.699052
PROBE_MASS = PROBE_VOLUME * 1e-9 * 2700


@pytest.fixture(scope="module")
def ocp():
    return pytest.importorskip("OCP", reason=NEED_OCP)


@pytest.fixture(scope="module")
def models(ocp, tmp_path_factory):
    import cad_fixtures as cad

    root = tmp_path_factory.mktemp("models")
    probe = cad.probe_part()
    return {
        "box": cad.write_step(cad.box(), root / "box.step"),
        "probe": cad.write_step(probe, root / "probe.step"),
        "probe_iges": cad.write_iges(probe, root / "probe.iges"),
        "probe_brep": cad.write_brep(probe, root / "probe.brep"),
        "blind": cad.write_step(cad.blind_part(), root / "blind.stp"),
        "two_boxes": cad.write_step(cad.compound(cad.box(), cad.box(x=200.0)), root / "two.step"),
        "shell": cad.write_step(cad.planar_shell(), root / "shell.step"),
        "side": cad.write_step(cad.side_hole_part(), root / "side.step"),
        "drill_point": cad.write_step(cad.drill_point_part(), root / "drill_point.step"),
        "groove": cad.write_step(cad.groove_part(), root / "groove.step"),
        "counterbore": cad.write_step(cad.counterbore_part(), root / "counterbore.step"),
        "split": cad.write_step(cad.split_faces(probe), root / "split.step"),
    }


def run(model_path, **kwargs) -> ToolResult:
    fn = getattr(measure, "fn", measure)
    result = fn(model_path=str(model_path), **kwargs)
    assert isinstance(result, ToolResult)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    return result


def failed(result: ToolResult, family: str) -> ToolResult:
    assert result.ok is False, "expected a failure"
    assert result.error.family == family, result.error
    assert result.warnings == []
    assert result.value is None and result.values == {}
    return result


def face_with_normal(result: ToolResult, normal) -> int:
    (found,) = [f["id"] for f in result.data["faces"] if f["normal"] and f["normal"] == pytest.approx(normal, abs=1e-9)]
    return found


# --------------------------------------------------------------------------- box


def test_box_mass_properties_match_the_hand_calculation(models):
    r = run(models["box"], density="2700 kg/m^3")
    assert r.ok, r.error
    assert r.provenance.engine == "eng_toolkit.geometry.measure"
    assert r.value == r.values["volume"]
    assert r.value.unit == "millimeter ** 3"
    assert r.values["volume"].quantity == pytest.approx(100000.0, abs=1e-6)
    assert r.values["surface_area"].quantity == pytest.approx(16000.0, abs=1e-6)
    assert r.values["surface_area"].unit == "millimeter ** 2"
    assert [r.values[k].quantity for k in ("size_x", "size_y", "size_z")] == pytest.approx([100.0, 50.0, 20.0], abs=1e-9)
    assert r.values["size_x"].unit == "millimeter"
    assert r.values["mass"].quantity == pytest.approx(0.27, abs=1e-9)
    assert r.values["mass"].unit == "kilogram"
    assert r.data["centroid_mm"] == pytest.approx([50.0, 25.0, 10.0], abs=1e-9)
    assert r.data["bbox_min_mm"] == pytest.approx([0.0, 0.0, 0.0], abs=1e-9)
    assert r.data["bbox_max_mm"] == pytest.approx([100.0, 50.0, 20.0], abs=1e-9)
    assert r.data["solids"] == 1
    assert r.data["faces_by_kind"] == {"plane": 6}
    assert r.data["holes"] == []
    assert r.data["step_roots"] == {"in_file": 1, "transferred": 1}
    assert "mass" not in run(models["box"]).values


def test_results_are_converted_to_the_output_unit(models):
    r = run(models["box"], output_unit="cm", density="2.7 g/cm^3")
    assert r.value.unit == "centimeter ** 3"
    assert r.value.quantity == pytest.approx(100.0, abs=1e-9)
    assert r.values["surface_area"].quantity == pytest.approx(160.0, abs=1e-9)
    assert r.values["size_x"].quantity == pytest.approx(10.0, abs=1e-9)
    assert r.values["mass"].quantity == pytest.approx(0.27, abs=1e-9)
    assert r.data["bbox_max_mm"] == pytest.approx([100.0, 50.0, 20.0], abs=1e-9)


def test_a_standing_warning_says_what_the_numbers_are(models):
    text = " ".join(run(models["box"]).warnings).lower()
    assert "exact" in text and "millimetre" in text and "ids" in text


def test_ids_are_reported_with_the_hash_of_the_file_they_are_valid_for(models):
    r = run(models["box"])
    assert r.data["face_ids_valid_for"] == hashlib.sha256(Path(models["box"]).read_bytes()).hexdigest()


# --------------------------------------------------------------------------- probe part


def test_probe_part_matches_the_hand_calculation(models):
    r = run(models["probe"], density="2700 kg/m^3")
    assert r.ok, r.error
    assert r.values["volume"].quantity == pytest.approx(98360.531085, abs=1e-4)
    assert r.values["surface_area"].quantity == pytest.approx(PROBE_AREA, abs=1e-3)
    assert r.values["mass"].quantity == pytest.approx(0.26557343, abs=1e-6)
    assert r.data["faces_by_kind"] == {"plane": 6, "cylinder": 5}
    assert r.data["bbox_max_mm"] == pytest.approx([100.0, 50.0, 20.0], abs=1e-9)
    assert r.data["bbox_min_mm"] == pytest.approx([0.0, 0.0, 0.0], abs=1e-9)


def test_the_hole_is_recognised_and_the_fillets_are_not(models):
    r = run(models["probe"])
    (hole,) = r.data["holes"]
    assert hole["id"] == "H1"
    assert hole["diameter_mm"] == pytest.approx(10.0, abs=1e-9)
    assert hole["depth_mm"] == pytest.approx(20.0, abs=1e-9)
    assert hole["thru"] is True
    assert hole["axis"] == pytest.approx([0.0, 0.0, -1.0], abs=1e-9)
    assert hole["entry_mm"] == pytest.approx([25.0, 25.0, 20.0], abs=1e-9)
    assert hole["counterbore"] is None and hole["countersink"] is None
    assert len(hole["face_ids"]) == 1
    cylinders = [f for f in r.data["faces"] if f["kind"] == "cylinder"]
    assert sorted(f["sense"] for f in cylinders) == ["concave"] + ["convex"] * 4
    assert sorted(f["radius_mm"] for f in cylinders) == pytest.approx([2.0] * 4 + [5.0])
    assert [f["id"] for f in cylinders if f["sense"] == "concave"] == hole["face_ids"]
    assert {f["sense"] for f in r.data["faces"] if f["kind"] == "plane"} == {"flat"}


def test_coverage_says_what_this_slice_does_not_detect(models):
    text = " ".join(map(str, run(models["probe"]).data["coverage"])).lower()
    assert "counterbore" in text and "countersink" in text and "thread" in text


def test_every_query_matches_its_hand_value(models):
    first = run(models["probe"])
    left = face_with_normal(first, (-1.0, 0.0, 0.0))
    top = face_with_normal(first, (0.0, 0.0, 1.0))
    queries = (
        f"distance F{left} H1; angle F{left} F{top}; diameter H1; radius H1; area H1; "
        "thickness P(10 mm, 10 mm, 30 mm) -z"
    )
    r = run(models["probe"], queries=queries)
    assert r.ok, r.error
    assert r.values["q1"].quantity == pytest.approx(20.0, abs=1e-6) and r.values["q1"].unit == "millimeter"
    assert r.values["q2"].quantity == pytest.approx(90.0, abs=1e-7) and r.values["q2"].unit == "degree"
    assert r.values["q3"].quantity == pytest.approx(10.0, abs=1e-9)
    assert r.values["q4"].quantity == pytest.approx(5.0, abs=1e-9)
    assert r.values["q5"].quantity == pytest.approx(628.3185307, abs=1e-6) and r.values["q5"].unit == "millimeter ** 2"
    assert r.values["q6"].quantity == pytest.approx(20.0, abs=1e-6)
    echoed = r.data["queries"]
    assert [q["id"] for q in echoed] == ["q1", "q2", "q3", "q4", "q5", "q6"]
    assert [q["verb"] for q in echoed] == ["distance", "angle", "diameter", "radius", "area", "thickness"]
    assert echoed[0]["refs"][0]["face_id"] == left and echoed[0]["refs"][1]["hole_id"] == "H1"
    assert echoed[5]["found"] is True


def test_queries_follow_the_output_unit(models):
    first = run(models["probe"])
    left = face_with_normal(first, (-1.0, 0.0, 0.0))
    r = run(models["probe"], queries=f"distance F{left} H1; area H1; diameter H1", output_unit="cm")
    assert r.values["q1"].quantity == pytest.approx(2.0, abs=1e-9) and r.values["q1"].unit == "centimeter"
    assert r.values["q2"].quantity == pytest.approx(6.283185307, abs=1e-8) and r.values["q2"].unit == "centimeter ** 2"
    assert r.values["q3"].quantity == pytest.approx(1.0, abs=1e-9)


def test_a_point_in_another_unit_is_converted(models):
    r = run(models["probe"], queries="thickness P(0.01 m, 10 mm, 1.181102362 inch) -z")
    assert r.values["q1"].quantity == pytest.approx(20.0, abs=1e-5)


def test_a_point_to_face_distance_and_a_point_to_hole_distance(models):
    r = run(models["probe"], queries="distance P(10 mm, 10 mm, 30 mm) H1; distance P(10 mm, 10 mm, 30 mm) P(10 mm, 10 mm, 20 mm)")
    # the nearest point of the hole wall is on its top rim (radius 5 about (25, 25), z = 20)
    rim = math.hypot(25 - 10, 25 - 10) - 5
    assert r.values["q1"].quantity == pytest.approx(math.hypot(rim, 10), abs=1e-6)
    assert r.values["q2"].quantity == pytest.approx(10.0, abs=1e-9)


def test_a_ray_through_the_hole_finds_no_wall_and_says_so(models):
    r = run(models["probe"], queries="thickness P(25 mm, 25 mm, 30 mm) -z; area H1")
    assert r.ok, r.error
    assert "q1" not in r.values
    assert r.values["q2"].quantity == pytest.approx(628.3185307, abs=1e-6)
    entry = r.data["queries"][0]
    assert entry["found"] is False and entry["value"] is None
    assert any("q1" in w and "two" in w for w in r.warnings)


def test_inventory_limit_caps_the_lists_but_not_the_artifact(models):
    r = run(models["probe"], inventory_limit=3)
    assert len(r.data["faces"]) == 3
    assert len(r.data["holes"]) == 1
    capped = run(models["probe"], inventory_limit=0)
    assert capped.data["faces"] == [] and capped.data["holes"] == []
    artifact = next(a for a in capped.artifacts if a.path.endswith("inventory.json"))
    inventory = json.loads(Path(artifact.path).read_text(encoding="utf-8"))
    assert len(inventory["faces"]) == 11 and len(inventory["holes"]) == 1
    assert inventory["faces"][0]["id"] == 1
    assert inventory["edges"] > 0 and inventory["vertices"] > 0
    assert hashlib.sha256(Path(artifact.path).read_bytes()).hexdigest() == artifact.sha256


# --------------------------------------------------------------------------- other shapes


def test_a_blind_hole_is_not_thru(models):
    r = run(models["blind"])
    assert r.values["volume"].quantity == pytest.approx(100000 - math.pi * 9 * 8, abs=1e-4)
    (hole,) = r.data["holes"]
    assert hole["thru"] is False
    assert hole["diameter_mm"] == pytest.approx(6.0, abs=1e-9)
    assert hole["depth_mm"] == pytest.approx(8.0, abs=1e-9)
    assert hole["axis"] == pytest.approx([0.0, 0.0, -1.0], abs=1e-9)
    assert hole["entry_mm"] == pytest.approx([50.0, 25.0, 20.0], abs=1e-9)


def test_a_through_hole_along_x_is_drilled_from_the_positive_end(models):
    # volume = 100000 - pi*4^2*100
    r = run(models["side"])
    assert r.values["volume"].quantity == pytest.approx(100000 - math.pi * 16 * 100, abs=1e-4)
    (hole,) = r.data["holes"]
    assert hole["thru"] is True and hole["diameter_mm"] == pytest.approx(8.0, abs=1e-9)
    assert hole["depth_mm"] == pytest.approx(100.0, abs=1e-9)
    assert hole["axis"] == pytest.approx([-1.0, 0.0, 0.0], abs=1e-9)
    assert hole["entry_mm"] == pytest.approx([100.0, 25.0, 10.0], abs=1e-9)


def test_a_drill_point_cone_does_not_make_a_blind_hole_through(models):
    r = run(models["drill_point"])
    assert r.data["faces_by_kind"] == {"cone": 1, "cylinder": 1, "plane": 6}
    (hole,) = r.data["holes"]
    assert hole["thru"] is False
    assert hole["diameter_mm"] == pytest.approx(6.0, abs=1e-9)
    assert hole["depth_mm"] == pytest.approx(8.0, abs=1e-9)
    assert hole["axis"] == pytest.approx([0.0, 0.0, -1.0], abs=1e-9)
    assert hole["entry_mm"] == pytest.approx([50.0, 25.0, 20.0], abs=1e-9)
    cone = next(f for f in r.data["faces"] if f["kind"] == "cone")
    assert cone["sense"] == "concave" and cone["axis"] is not None


def test_the_outer_wall_of_an_annular_groove_is_not_a_hole(models):
    r = run(models["groove"])
    assert r.data["faces_by_kind"]["cylinder"] == 2
    assert {f["sense"] for f in r.data["faces"] if f["kind"] == "cylinder"} == {"concave", "convex"}
    assert r.data["holes"] == []


def test_a_counterbore_is_listed_as_a_blind_hole_beside_the_bore(models):
    r = run(models["counterbore"])
    bore, step = r.data["holes"]
    assert (bore["id"], bore["diameter_mm"], bore["thru"]) == ("H1", 6.0, True)
    assert bore["depth_mm"] == pytest.approx(15.0, abs=1e-9)
    assert (step["id"], step["diameter_mm"], step["thru"]) == ("H2", 12.0, False)
    assert step["depth_mm"] == pytest.approx(5.0, abs=1e-9)
    assert step["entry_mm"] == pytest.approx([50.0, 25.0, 20.0], abs=1e-9)
    assert bore["counterbore"] is None and step["counterbore"] is None


def test_a_bore_split_into_two_half_faces_is_one_hole(models):
    r = run(models["split"])
    assert r.data["faces_by_kind"] == {"plane": 6, "cylinder": 6}
    (hole,) = r.data["holes"]
    assert len(hole["face_ids"]) == 2
    assert hole["diameter_mm"] == pytest.approx(10.0, abs=1e-9) and hole["thru"] is True
    assert hole["depth_mm"] == pytest.approx(20.0, abs=1e-9)
    assert r.values["volume"].quantity == pytest.approx(PROBE_VOLUME, abs=1e-4)
    area = run(models["split"], queries="area H1")
    assert area.values["q1"].quantity == pytest.approx(628.3185307, abs=1e-6)


def test_two_disjoint_boxes_are_two_solids_with_the_summed_volume(models):
    r = run(models["two_boxes"])
    assert r.data["solids"] == 2
    assert r.values["volume"].quantity == pytest.approx(200000.0, abs=1e-5)
    assert r.values["size_x"].quantity == pytest.approx(300.0, abs=1e-9)
    assert r.data["faces_by_kind"] == {"plane": 12}


def test_a_shell_has_no_volume_and_the_area_becomes_primary(models):
    r = run(models["shell"], density="2700 kg/m^3")
    assert r.ok, r.error
    assert "volume" not in r.values and "mass" not in r.values
    assert r.value == r.values["surface_area"]
    assert r.value.quantity == pytest.approx(5000.0, abs=1e-6)
    text = " ".join(r.warnings).lower()
    assert "closed solid" in text and "volume" in text
    assert "density" in text


@pytest.mark.parametrize("key", ["probe_iges", "probe_brep"])
def test_the_same_part_in_iges_and_brep_gives_the_same_numbers(models, key):
    step = run(models["probe"])
    other = run(models[key])
    assert other.ok, other.error
    assert other.values["volume"].quantity == pytest.approx(step.values["volume"].quantity, rel=1e-3)
    assert other.values["surface_area"].quantity == pytest.approx(PROBE_AREA, rel=1e-3)
    assert other.data["faces_by_kind"] == {"plane": 6, "cylinder": 5}
    (hole,) = other.data["holes"]
    assert hole["diameter_mm"] == pytest.approx(10.0, abs=1e-6) and hole["thru"] is True
    assert hole["depth_mm"] == pytest.approx(20.0, abs=1e-6)
    assert other.data["step_roots"]["transferred"] == 1


def test_the_brep_numbers_are_exact(models):
    r = run(models["probe_brep"])
    assert r.values["volume"].quantity == pytest.approx(PROBE_VOLUME, abs=1e-4)
    assert r.data["step_roots"] == {"in_file": 1, "transferred": 1}


# --------------------------------------------------------------------------- errors


@pytest.fixture
def unread(tmp_path):
    """A file the call must reject before it reads it: these tests need no CAD engine."""
    path = tmp_path / "unread.step"
    path.write_text("not read\n")
    return path


def test_a_bare_number_density_is_rejected(unread):
    failed(run(unread, density="2700"), "units.bare_number")


def test_a_density_with_the_wrong_dimension_is_rejected(unread):
    failed(run(unread, density="5 mm"), "units.dimension_mismatch")


def test_a_density_must_be_positive(unread):
    failed(run(unread, density="0 kg/m^3"), "input.invalid")


def test_a_missing_file(tmp_path):
    r = failed(run(tmp_path / "nope.step"), "input.file_not_found")
    assert r.error.details["param"] == "model_path"


def test_an_stl_file_says_mesh_files_come_later(tmp_path):
    stl = tmp_path / "part.stl"
    stl.write_text("solid part\nendsolid part\n")
    r = failed(run(stl), "input.unsupported_format")
    assert "later" in r.error.message and ".step" in r.error.message


def test_an_unknown_extension_lists_the_supported_ones(tmp_path):
    other = tmp_path / "part.obj"
    other.write_text("v 0 0 0\n")
    r = failed(run(other), "input.unsupported_format")
    assert r.error.details["supported"] == [".step", ".stp", ".iges", ".igs", ".brep"]


@pytest.mark.parametrize("name", ["bad.step", "bad.iges", "bad.brep"])
def test_a_text_file_with_a_cad_extension_is_input_invalid(ocp, tmp_path, name):
    bad = tmp_path / name
    bad.write_text("this is not a CAD file\n")
    failed(run(bad), "input.invalid")


def test_an_empty_step_file_is_input_invalid(ocp, tmp_path):
    empty = tmp_path / "empty.step"
    empty.write_bytes(b"")
    failed(run(empty), "input.invalid")


def test_twenty_one_queries_are_too_many(unread):
    r = failed(run(unread, queries="; ".join(["area F1"] * 21)), "input.invalid")
    assert r.error.details["limit"] == 20


def test_a_malformed_query_reports_its_index(unread):
    r = failed(run(unread, queries="area F1; distance F1"), "input.invalid")
    assert r.error.details["query_index"] == 1 and r.error.details["query"] == "distance F1"


def test_an_unknown_face_reports_how_many_exist(models):
    r = failed(run(models["probe"], queries="area F1; diameter F999"), "geometry.unknown_entity")
    assert r.error.details["ref"] == "F999"
    assert r.error.details["query_index"] == 1
    assert r.error.details["faces"] == 11 and r.error.details["holes"] == 1


def test_an_unknown_hole_reports_how_many_exist(models):
    r = failed(run(models["box"], queries="diameter H1"), "geometry.unknown_entity")
    assert r.error.details["ref"] == "H1" and r.error.details["holes"] == 0 and r.error.details["faces"] == 6


def test_diameter_of_a_plane_is_unsupported(models):
    r = failed(run(models["box"], queries="diameter F1"), "geometry.unsupported_query")
    assert r.error.details["query_index"] == 0


@pytest.mark.parametrize(
    "query",
    ["radius F1", "diameter P(1 mm, 2 mm, 3 mm)", "area P(1 mm, 2 mm, 3 mm)", "angle F1 P(1 mm, 2 mm, 3 mm)"],
)
def test_queries_on_entities_that_have_no_such_measure_are_unsupported(models, query):
    failed(run(models["box"], queries=query), "geometry.unsupported_query")


def test_a_point_without_units_is_rejected(unread):
    r = failed(run(unread, queries="area F1; thickness P(10, 10, 30) -z"), "units.bare_number")
    assert r.error.details["query_index"] == 1


def test_a_point_with_a_non_length_unit_is_rejected(unread):
    failed(run(unread, queries="thickness P(10 kg, 10 mm, 30 mm) -z"), "units.dimension_mismatch")


def test_a_bad_axis_is_input_invalid(unread):
    failed(run(unread, queries="thickness P(10 mm, 10 mm, 30 mm) +w"), "input.invalid")


@pytest.mark.parametrize("limit", [-1, 501])
def test_inventory_limit_out_of_range(unread, limit):
    r = failed(run(unread, inventory_limit=limit), "input.invalid")
    assert r.error.details["param"] == "inventory_limit"


def test_output_unit_must_be_a_length(unread):
    failed(run(unread, output_unit="kg"), "units.dimension_mismatch")


# --------------------------------------------------------------------------- engine and process hygiene


def test_a_missing_engine_gives_an_install_hint(tmp_path, monkeypatch):
    model = tmp_path / "model.step"
    model.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n")
    monkeypatch.setitem(sys.modules, "OCP", None)
    r = failed(run(model), "engine.unavailable")
    assert "uv sync --extra cad" in r.error.message
    assert r.error.details["install"] == "uv sync --extra cad"


def test_nothing_is_written_to_stdout_on_success_or_failure(models, tmp_path, capfd):
    for path in (models["probe"], models["probe_iges"], models["probe_brep"]):
        assert run(path, queries="thickness P(10 mm, 10 mm, 30 mm) -z").ok
    bad = tmp_path / "bad.step"
    bad.write_text("not a step file\n")
    assert run(bad).ok is False
    bad_iges = tmp_path / "bad.iges"
    bad_iges.write_text("not an iges file\n")
    assert run(bad_iges).ok is False
    bad_brep = tmp_path / "bad.brep"
    bad_brep.write_text("not a brep file\n")
    assert run(bad_brep).ok is False
    captured = capfd.readouterr()
    assert captured.out == ""


def test_a_fresh_process_writes_nothing_to_stdout_not_even_at_exit(models, tmp_path):
    # OCCT writes some messages through C++ std::cout, which is buffered until the process exits, so the
    # in-process capture above cannot see all of them; stdout is the MCP stdio channel.
    cases = {"bad.step": "not a step\n", "bad.iges": "not an iges\n", "bad.brep": "not a brep\n"}
    for name, text in cases.items():
        (tmp_path / name).write_text(text)
    script = (
        "import sys\n"
        "from eng_toolkit.domains.geometry.tools import measure\n"
        "fn = getattr(measure, 'fn', measure)\n"
        "for path in sys.argv[1:]:\n"
        "    fn(model_path=path, queries='thickness P(10 mm, 10 mm, 30 mm) -z')\n"
    )
    paths = [str(tmp_path / n) for n in cases] + [str(models[k]) for k in ("probe", "probe_iges", "probe_brep")]
    done = subprocess.run(
        [sys.executable, "-c", script, *paths],
        capture_output=True,
        env={**os.environ, "ENG_TOOLKIT_WS": str(tmp_path / "ws")},
        check=False,
    )
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    assert done.stdout == b""


def test_a_non_ascii_path_is_read(models, tmp_path):
    for key, name in (("probe", "零件_é.step"), ("probe_iges", "零件_é.iges"), ("probe_brep", "零件_é.brep")):
        copy = tmp_path / name
        shutil.copyfile(models[key], copy)
        r = run(copy)
        assert r.ok, r.error
        assert r.values["volume"].quantity == pytest.approx(PROBE_VOLUME, rel=1e-3)
        assert len(r.data["holes"]) == 1


def test_the_result_lists_the_model_as_an_input_file(models):
    r = run(models["box"])
    (ref,) = r.provenance.input_files
    assert ref.path == str(Path(models["box"]).resolve())
    assert ref.bytes == Path(models["box"]).stat().st_size
    assert ref.sha256 == r.data["face_ids_valid_for"]
