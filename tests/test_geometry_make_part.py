"""geometry_make_part checked against hand calculations (see tests/part_fixtures.py for the numbers).

Probe part = a 100 x 50 x 20 box, a vertical diameter-10 through hole at (25, 25), R2 fillets on the 4 outer vertical edges:
    volume = 100000 - pi*5^2*20 - 4*(2^2 - pi*2^2/4)*20 = 98360.531085 mm^3, area 16395.699 mm^2.
Engine tests skip without OCP; the failures that happen before the engine is needed run without it.
"""

import hashlib
import json
import math
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from part_fixtures import PROBE_AREA, PROBE_SPEC, PROBE_VOLUME, VOLUME_CASES, box, chamfer, cone, cylinder, fillet, flange_case, hole, sphere, spec

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.geometry.part import EXAMPLE_SPEC
from eng_toolkit.domains.geometry.tools import MAKE_PART_DESCRIPTION, make_part, measure
from eng_toolkit.gateway.server import gateway

NEED_OCP = "OCP is not installed; run: uv sync --extra cad"


@pytest.fixture(scope="module")
def ocp():
    return pytest.importorskip("OCP", reason=NEED_OCP)


def run(text, **kwargs) -> ToolResult:
    fn = getattr(make_part, "fn", make_part)
    result = fn(spec=text, **kwargs)
    assert isinstance(result, ToolResult)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    return result


def failed(result: ToolResult, family: str) -> ToolResult:
    assert result.ok is False, "expected a failure"
    assert result.error.family == family, result.error
    assert result.warnings == []
    assert result.value is None and result.values == {}
    return result


def artifact(result: ToolResult, filename: str) -> Path:
    (found,) = [a for a in result.artifacts if Path(a.path).name == filename]
    assert hashlib.sha256(Path(found.path).read_bytes()).hexdigest() == found.sha256
    return Path(found.path)


def measured(path: Path):
    fn = getattr(measure, "fn", measure)
    result = fn(model_path=str(path))
    assert result.ok, result.error
    return result


# --------------------------------------------------------------------------- hand calculations, one per op


@pytest.mark.parametrize(("case", "text", "expected", "hand"), VOLUME_CASES, ids=[c[0] for c in VOLUME_CASES])
def test_volume_matches_the_hand_calculation(ocp, case, text, expected, hand):
    r = run(text)
    assert r.ok, r.error
    assert r.value == r.values["volume"]
    assert r.value.unit == "millimeter ** 3"
    assert r.values["volume"].quantity == pytest.approx(expected, abs=1e-6), hand
    assert r.data["solids"] == 1


def test_flange_on_a_bolt_circle(ocp):
    text, expected = flange_case()
    r = run(text)
    assert r.ok, r.error
    assert r.values["volume"].quantity == pytest.approx(expected, abs=1e-5)
    assert r.values["volume"].quantity == pytest.approx(45427.43, abs=0.01)
    assert len(r.data["holes"]) == 7


def test_box_sizes_and_area(ocp):
    r = run(spec([box(("80 mm", "40 mm", "10 mm"))]))
    assert [r.values[k].quantity for k in ("size_x", "size_y", "size_z")] == pytest.approx([80, 40, 10], abs=1e-9)
    assert r.values["surface_area"].quantity == pytest.approx(2 * (3200 + 800 + 400), abs=1e-6)
    assert r.values["surface_area"].unit == "millimeter ** 2"
    assert r.data["bbox"] == {"min_mm": [0.0, 0.0, 0.0], "max_mm": [80.0, 40.0, 10.0]}


def test_cylinder_area(ocp):
    r = run(spec([cylinder("30 mm", "40 mm")]))
    assert r.values["surface_area"].quantity == pytest.approx(2 * math.pi * 15 * 40 + 2 * math.pi * 225, abs=1e-6)


def test_sphere_size_and_area(ocp):
    r = run(spec([sphere("30 mm")]))
    assert [r.values[k].quantity for k in ("size_x", "size_y", "size_z")] == pytest.approx([30, 30, 30], abs=1e-9)
    assert r.values["surface_area"].quantity == pytest.approx(4 * math.pi * 225, abs=1e-6)
    assert r.values["surface_area"].quantity == pytest.approx(2827.4334, abs=1e-4)


def test_sphere_at_is_its_centre(ocp):
    r = run(spec([sphere("10 mm", at=("10 mm", "20 mm", "30 mm"))]))
    assert r.data["bbox"] == {"min_mm": [5.0, 15.0, 25.0], "max_mm": [15.0, 25.0, 35.0]}


def test_frustum_size_follows_the_axis(ocp):
    up = run(spec([cone("40 mm", "20 mm", "30 mm")]))
    assert [up.values[k].quantity for k in ("size_x", "size_y", "size_z")] == pytest.approx([40, 40, 30], abs=1e-9)
    along_x = run(spec([cone("40 mm", "20 mm", "30 mm", axis="+x")]))
    assert [along_x.values[k].quantity for k in ("size_x", "size_y", "size_z")] == pytest.approx([30, 40, 40], abs=1e-9)
    assert along_x.values["volume"].quantity == pytest.approx(math.pi * 10 * 700, abs=1e-6)


def test_a_cone_can_widen_towards_its_top(ocp):
    r = run(spec([cone("20 mm", "40 mm", "30 mm")]))
    assert r.values["volume"].quantity == pytest.approx(math.pi * 10 * 700, abs=1e-6)


def test_a_cone_cut_makes_a_conical_recess(ocp):
    text = spec([box(("40 mm", "40 mm", "20 mm")), cone("20 mm", "0 mm", "10 mm", at=("20 mm", "20 mm", "10 mm"), mode="cut")])
    r = run(text)
    assert r.values["volume"].quantity == pytest.approx(32000 - math.pi * 100 * 10 / 3, abs=1e-6)


def test_a_cone_with_a_negative_height_names_its_field(ocp):
    r = failed(run(spec([cone("20 mm", "10 mm", "-5 mm")])), "input.invalid")
    assert r.error.details["pointer"] == "/features/0/height"


def test_a_cone_without_a_height_names_the_missing_field(ocp):
    bare = {"op": "cone", "diameter": "20 mm", "top_diameter": "0 mm"}
    r = failed(run(spec([bare])), "input.invalid")
    assert r.error.details["pointer"] == "/features/0/height"


def test_a_sphere_of_zero_diameter_names_its_field(ocp):
    r = failed(run(spec([sphere("0 mm")])), "input.invalid")
    assert r.error.details["pointer"] == "/features/0/diameter"


def test_l_bracket_is_one_solid_with_the_hand_size(ocp):
    text = spec([box(("60 mm", "40 mm", "5 mm")), box(("5 mm", "40 mm", "25 mm"), at=("0 mm", "0 mm", "5 mm"))])
    r = run(text)
    assert r.data["solids"] == 1
    assert [r.values[k].quantity for k in ("size_x", "size_y", "size_z")] == pytest.approx([60, 40, 30], abs=1e-9)


# --------------------------------------------------------------------------- the probe part end to end


def test_probe_part_volume_area_and_mass(ocp):
    r = run(PROBE_SPEC, density="2700 kg/m^3")
    assert r.ok, r.error
    assert r.values["volume"].quantity == pytest.approx(PROBE_VOLUME, abs=1e-5)
    assert r.values["volume"].quantity == pytest.approx(98360.531085, abs=1e-5)
    assert r.values["surface_area"].quantity == pytest.approx(PROBE_AREA, abs=1e-3)
    assert r.values["mass"].quantity == pytest.approx(PROBE_VOLUME * 1e-9 * 2700, abs=1e-9)
    assert r.values["mass"].unit == "kilogram"
    assert "mass" not in run(PROBE_SPEC).values


def test_geometry_measure_finds_the_declared_hole_in_the_produced_step(ocp):
    r = run(PROBE_SPEC)
    step = artifact(r, "part.step")
    m = measured(step)
    assert m.values["volume"].quantity == pytest.approx(PROBE_VOLUME, abs=1e-5)
    assert m.values["surface_area"].quantity == pytest.approx(PROBE_AREA, abs=1e-3)
    (found,) = m.data["holes"]
    assert found["thru"] is True and found["diameter_mm"] == pytest.approx(10.0)
    assert found["entry_mm"] == pytest.approx([25.0, 25.0, 20.0])


def test_the_selector_picks_only_the_four_outer_vertical_edges(ocp):
    r = run(PROBE_SPEC)
    fillet_record = r.data["features"][2]
    assert fillet_record["op"] == "fillet" and fillet_record["edges_used"] == 4
    assert r.data["features"][1]["edges_used"] is None


def test_the_example_in_the_tool_description_builds_the_probe_part(ocp):
    r = run(json.dumps(EXAMPLE_SPEC))
    assert r.ok, r.error
    assert r.values["volume"].quantity == pytest.approx(PROBE_VOLUME, abs=1e-5)
    assert r.data["params"] == {"L": "100.0 mm", "T": "20.0 mm", "W": "50.0 mm", "d": "10.0 mm"}


def test_changing_a_parameter_changes_the_volume_by_the_hand_difference(ocp):
    def probe(length: str) -> str:
        return spec(
            [
                box(("$L", "50 mm", "20 mm")),
                hole([("25 mm", "25 mm", "20 mm")], "10 mm"),
                fillet("|z & convex", "2 mm"),
            ],
            L=length,
        )

    base = run(probe("100 mm")).values["volume"].quantity
    longer = run(probe("110 mm")).values["volume"].quantity
    assert base == pytest.approx(PROBE_VOLUME, abs=1e-5)
    assert longer - base == pytest.approx(10 * 50 * 20, abs=1e-6)  # the hole and the fillets do not move


def test_the_per_feature_volumes_follow_the_features(ocp):
    r = run(PROBE_SPEC)
    after = [f["volume_after_mm3"] for f in r.data["features"]]
    assert [f["op"] for f in r.data["features"]] == ["box", "hole", "fillet"]
    assert [f["index"] for f in r.data["features"]] == [1, 2, 3]
    assert [f["name"] for f in r.data["features"]] == ["box1", "hole2", "fillet3"]
    assert after == pytest.approx([100000.0, 100000 - 25 * math.pi * 20, PROBE_VOLUME], abs=1e-5)


def test_declared_holes_are_resolved_to_millimetres(ocp):
    text = spec(
        [
            box(("40 mm", "40 mm", "20 mm")),
            hole(
                [("1 cm", "1 cm", "20 mm"), ("3 cm", "3 cm", "20 mm")],
                "6.6 mm",
                counterbore={"diameter": "11 mm", "depth": "6.5 mm"},
                thread="M6x1-6H",
                fit="H7",
            ),
        ]
    )
    r = run(text)
    assert r.ok, r.error
    first, second = r.data["holes"]
    assert first["entry_mm"] == [10.0, 10.0, 20.0] and second["entry_mm"] == [30.0, 30.0, 20.0]
    assert first["axis"] == [0.0, 0.0, -1.0] and first["diameter_mm"] == 6.6 and first["thru"] is True
    assert first["depth_mm"] is None and first["counterbore"] == {"diameter_mm": 11.0, "depth_mm": 6.5}
    assert first["thread"] == "M6x1-6H" and first["fit"] == "H7" and first["countersink"] is None
    assert first["feature"] == "hole2" and first["feature_index"] == 2
    assert any("M6x1-6H" in w and "callout" in w for w in r.warnings)


def test_a_hole_that_misses_the_part_warns(ocp):
    text = spec([box(("40 mm", "40 mm", "20 mm")), hole([("20 mm", "20 mm", "0 mm")], "6 mm")])
    r = run(text)
    assert r.ok, r.error
    assert any("removed no material" in w and "hole2" in w for w in r.warnings)


def test_a_cut_body_that_misses_the_part_warns(ocp):
    text = spec([box(("40 mm", "40 mm", "20 mm")), box(("5 mm", "5 mm", "5 mm"), at=("100 mm", "0 mm", "0 mm"), mode="cut")])
    r = run(text)
    assert r.ok, r.error
    assert any("removed no material" in w and "box2" in w for w in r.warnings)
    assert r.values["volume"].quantity == pytest.approx(32000.0, abs=1e-9)


def test_data_params_are_the_resolved_parameters(ocp):
    r = run(spec([box(("$a", "$b", "1 mm"))], a="2 cm", b="$a / 4"))
    assert r.data["params"] == {"a": "20.0 mm", "b": "5.0 mm"}


# --------------------------------------------------------------------------- artifacts, read-back and the features document


def test_artifacts_and_the_step_readback(ocp):
    r = run(PROBE_SPEC, name="bracket")
    names = [Path(a.path).name for a in r.artifacts]
    assert names[0] == "inputs.json"
    assert {"bracket.step", "bracket.features.json", "spec.json"} <= set(names)
    assert not any(n.endswith((".glb", ".stl", ".brep")) for n in names)
    readback = r.data["readback"]
    assert readback["relative_difference"] < 1e-9
    assert readback["volume_written_mm3"] == pytest.approx(PROBE_VOLUME, abs=1e-5)
    assert readback["volume_read_mm3"] == pytest.approx(PROBE_VOLUME, abs=1e-5)
    assert readback["solids_read"] == 1
    step = artifact(r, "bracket.step")
    assert "PRODUCT('bracket'" in step.read_text(encoding="ascii")


def test_the_features_document_binds_the_holes_to_the_step_by_hash(ocp):
    r = run(PROBE_SPEC, name="bracket")
    step = artifact(r, "bracket.step")
    document = json.loads(artifact(r, "bracket.features.json").read_text(encoding="utf-8"))
    assert document["step_sha256"] == hashlib.sha256(step.read_bytes()).hexdigest()
    assert document["spec_sha256"] == r.data["spec_sha256"]
    assert document["part"] == "bracket" and document["unit"] == "mm"
    (declared,) = document["holes"]
    assert declared["diameter_mm"] == 10.0 and declared["entry_mm"] == [25.0, 25.0, 20.0] and declared["thru"] is True
    assert r.data["step_sha256"] == document["step_sha256"]


def test_spec_json_is_the_canonical_spec(ocp):
    r = run(PROBE_SPEC)
    path = artifact(r, "spec.json")
    written = path.read_text(encoding="utf-8")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == r.data["spec_sha256"]
    assert json.loads(written)["features"][0]["op"] == "box"
    again = run(written)
    assert again.data["spec_sha256"] == r.data["spec_sha256"]
    assert artifact(again, "part.step").read_bytes() == artifact(r, "part.step").read_bytes()


def test_whitespace_and_key_order_give_the_same_spec_hash_and_bytes(ocp):
    document = json.loads(PROBE_SPEC)
    scrambled = {
        "features": [dict(reversed(list(f.items()))) for f in document["features"]],
        "params": document["params"],
    }
    first, second = run(PROBE_SPEC), run(json.dumps(scrambled, indent=3))
    assert first.data["spec_sha256"] == second.data["spec_sha256"]
    assert artifact(first, "spec.json").read_bytes() == artifact(second, "spec.json").read_bytes()
    assert artifact(first, "part.step").read_bytes() == artifact(second, "part.step").read_bytes()
    assert first.provenance.inputs_hash != second.provenance.inputs_hash  # the raw text differs, the geometry does not


def test_two_builds_give_the_same_step_bytes(ocp):
    first, second = run(PROBE_SPEC), run(PROBE_SPEC)
    a, b = artifact(first, "part.step").read_bytes(), artifact(second, "part.step").read_bytes()
    assert a == b
    assert first.data["step_sha256"] == second.data["step_sha256"] == hashlib.sha256(a).hexdigest()


def test_two_processes_give_the_same_step_bytes(ocp, tmp_path):
    script = (
        "import json, sys\n"
        "from eng_toolkit.domains.geometry.tools import make_part\n"
        "fn = getattr(make_part, 'fn', make_part)\n"
        "r = fn(spec=sys.argv[1], name='bracket')\n"
        "print(json.dumps([r.ok, r.data['step_sha256'], r.data['spec_sha256']]))\n"
    )
    outputs = []
    for seed in ("1", "2"):
        done = subprocess.run(
            [sys.executable, "-c", script, PROBE_SPEC],
            capture_output=True,
            text=True,
            env={**os.environ, "ENG_TOOLKIT_WS": str(tmp_path / f"ws{seed}"), "PYTHONHASHSEED": seed},
            check=False,
        )
        assert done.returncode == 0, done.stderr
        outputs.append(json.loads(done.stdout))
    assert outputs[0] == outputs[1] and outputs[0][0] is True
    assert outputs[0][1] == run(PROBE_SPEC, name="bracket").data["step_sha256"]


def test_the_step_name_is_the_part_name(ocp):
    assert artifact(run(PROBE_SPEC, name="a1"), "a1.step").read_bytes() != artifact(run(PROBE_SPEC, name="b2"), "b2.step").read_bytes()


# --------------------------------------------------------------------------- optional formats


def test_stl_glb_and_brep_are_written_when_asked(ocp):
    r = run(spec([box(("100 mm", "50 mm", "20 mm"))]), formats="step, glb ,stl,brep,step")
    assert r.ok, r.error
    assert [Path(a.path).suffix for a in r.artifacts if Path(a.path).stem == "part"] == [".step", ".glb", ".stl", ".brep"]
    stl = artifact(r, "part.stl").read_bytes()
    assert len(stl) == 84 + 12 * 50
    assert struct.unpack_from("<I", stl, 80)[0] == 12
    glb = artifact(r, "part.glb").read_bytes()
    assert glb[:4] == b"glTF"
    json_length = struct.unpack_from("<I", glb, 12)[0]
    document = json.loads(glb[20 : 20 + json_length])
    position = document["accessors"][document["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    assert position["count"] == 8
    assert max(position["max"]) == pytest.approx(0.1, abs=1e-7)
    assert position["max"][1] == pytest.approx(0.02, abs=1e-7)
    assert {a.format for a in r.artifacts if Path(a.path).stem == "part"} == {"step", "glb", "stl", "brep"}


def test_step_is_written_even_when_not_listed(ocp):
    r = run(spec([box(("1 mm", "1 mm", "1 mm"))]), formats="stl")
    assert artifact(r, "part.step")
    assert artifact(r, "part.stl")


def test_mesh_deflection_changes_the_mesh_of_a_curved_part_not_the_volume(ocp):
    text = spec([cylinder("30 mm", "40 mm")])
    coarse = run(text, formats="stl", mesh_deflection="1 mm")
    fine = run(text, formats="stl", mesh_deflection="0.01 mm")
    assert coarse.values["volume"].quantity == fine.values["volume"].quantity
    assert artifact(fine, "part.stl").stat().st_size > artifact(coarse, "part.stl").stat().st_size


# --------------------------------------------------------------------------- units, provenance and the contract


def test_results_follow_the_output_unit(ocp):
    r = run(spec([box(("100 mm", "50 mm", "20 mm"))]), output_unit="cm", density="2.7 g/cm^3")
    assert r.value.unit == "centimeter ** 3" and r.value.quantity == pytest.approx(100.0, abs=1e-9)
    assert r.values["surface_area"].quantity == pytest.approx(160.0, abs=1e-9)
    assert r.values["size_x"].quantity == pytest.approx(10.0, abs=1e-9)
    assert r.values["mass"].quantity == pytest.approx(0.27, abs=1e-9)
    assert r.data["bbox"]["max_mm"] == [100.0, 50.0, 20.0]


def test_provenance_names_the_engine_and_lists_no_input_files(ocp):
    r = run(PROBE_SPEC)
    assert r.provenance.engine == "eng_toolkit.geometry.make_part"
    assert r.provenance.input_files == []
    assert set(r.provenance.engine_versions) == {"cadquery-ocp-novtk", "numpy"}
    assert r.review_status == "unreviewed"
    assert r.warnings and any("unreviewed" in w.lower() or "engineering" in w.lower() for w in r.warnings)


def test_the_result_is_a_json_round_trip(ocp):
    r = run(PROBE_SPEC, formats="step,glb,stl,brep", density="7850 kg/m^3")
    again = ToolResult.model_validate(json.loads(r.model_dump_json()))
    assert again == r
    assert len(json.dumps(r.data)) < 20000


# --------------------------------------------------------------------------- errors that need the engine


def test_an_empty_selection_names_the_feature_and_the_matches_per_term(ocp):
    r = failed(run(spec([box(("10 mm", "10 mm", "10 mm")), fillet("|z & concave", "1 mm")])), "geometry.empty_selection")
    d = r.error.details
    assert d["feature_index"] == 2 and d["feature_name"] == "fillet2" and d["op"] == "fillet" and d["pointer"] == "/features/1"
    assert d["selector"] == "|z & concave" and d["matches_per_term"] == {"|z": 4, "concave": 0}
    assert r.error.message.startswith("feature 2")


def test_an_impossible_fillet_radius_names_the_feature(ocp):
    r = failed(run(spec([box(("10 mm", "10 mm", "10 mm")), fillet("all", "8 mm")])), "geometry.build_failed")
    d = r.error.details
    assert d["feature_index"] == 2 and d["feature_name"] == "fillet2" and d["op"] == "fillet" and d["pointer"] == "/features/1"
    assert d["occt"]


def test_an_impossible_chamfer_names_the_feature(ocp):
    r = failed(run(spec([box(("10 mm", "10 mm", "10 mm")), chamfer("all", "8 mm")])), "geometry.build_failed")
    assert r.error.details["op"] == "chamfer"


def test_two_disjoint_solids_are_an_invalid_result(ocp):
    text = spec([box(("10 mm", "10 mm", "10 mm")), box(("10 mm", "10 mm", "10 mm"), at=("50 mm", "0 mm", "0 mm"))])
    r = failed(run(text), "geometry.invalid_result")
    assert r.error.details["solids"] == 2
    assert not any(Path(a.path).suffix == ".step" for a in r.artifacts)


def test_a_cut_that_removes_everything_is_a_build_failure_naming_the_feature(ocp):
    text = spec([box(("10 mm", "10 mm", "10 mm")), box(("30 mm", "30 mm", "30 mm"), at=("-5 mm", "-5 mm", "-5 mm"), mode="cut")])
    r = failed(run(text), "geometry.build_failed")
    assert r.error.details["feature_index"] == 2 and r.error.details["pointer"] == "/features/1"


def test_a_self_crossing_profile_is_a_failure_that_names_the_feature(ocp):
    bow_tie = {"polygon": {"points": [["0 mm", "0 mm"], ["10 mm", "10 mm"], ["10 mm", "0 mm"], ["0 mm", "10 mm"]]}}
    r = run(spec([{"op": "extrude", "profile": bow_tie, "distance": "5 mm"}]))
    assert r.ok is False and r.error.family in ("geometry.build_failed", "geometry.invalid_result")
    assert r.error.details["feature_index"] == 1 and r.warnings == []


def test_a_revolve_profile_across_its_axis_is_a_failure(ocp):
    text = spec([{"op": "revolve", "plane": "xz", "profile": {"rect": {"size": ["10 mm", "3 mm"], "at": ["-5 mm", "0 mm"]}}, "axis": "v"}])
    r = run(text)
    assert r.ok is False and r.error.family in ("geometry.build_failed", "geometry.invalid_result")


def test_failures_write_no_part_files(ocp):
    r = run(spec([box(("10 mm", "10 mm", "10 mm")), fillet("all", "8 mm")]))
    assert [Path(a.path).name for a in r.artifacts] == ["inputs.json"]


# --------------------------------------------------------------------------- errors that need no engine


@pytest.mark.parametrize(
    ("kwargs", "family"),
    [
        ({"text": "{not json"}, "input.invalid"),
        ({"text": "[]"}, "input.invalid"),
        ({"text": spec([{"op": "torus"}])}, "input.invalid"),
        ({"text": spec([box((100, "1 mm", "1 mm"))])}, "units.bare_number"),
        ({"text": spec([box(("1 kg", "1 mm", "1 mm"))])}, "units.dimension_mismatch"),
        ({"text": spec([box(("9**9**9 mm", "1 mm", "1 mm"))])}, "input.invalid"),
        ({"text": PROBE_SPEC, "name": ""}, "input.invalid"),
        ({"text": PROBE_SPEC, "name": "a b"}, "input.invalid"),
        ({"text": PROBE_SPEC, "name": "a/b"}, "input.invalid"),
        ({"text": PROBE_SPEC, "name": "x" * 65}, "input.invalid"),
        ({"text": PROBE_SPEC, "name": "零件"}, "input.invalid"),
        ({"text": PROBE_SPEC, "name": "é"}, "input.invalid"),
        ({"text": PROBE_SPEC, "formats": "step,iges"}, "input.invalid"),
        ({"text": PROBE_SPEC, "formats": "obj"}, "input.invalid"),
        ({"text": PROBE_SPEC, "mesh_deflection": "0.01"}, "units.bare_number"),
        ({"text": PROBE_SPEC, "mesh_deflection": "0 mm"}, "input.invalid"),
        ({"text": PROBE_SPEC, "mesh_deflection": "1 kg"}, "units.dimension_mismatch"),
        ({"text": PROBE_SPEC, "mesh_deflection": "1 m"}, "input.invalid"),
        ({"text": PROBE_SPEC, "density": "2700"}, "units.bare_number"),
        ({"text": PROBE_SPEC, "density": "5 mm"}, "units.dimension_mismatch"),
        ({"text": PROBE_SPEC, "density": "0 kg/m^3"}, "input.invalid"),
        ({"text": PROBE_SPEC, "output_unit": "kg"}, "units.dimension_mismatch"),
        ({"text": "x" * 70000}, "input.invalid"),
        ({"text": spec([box(("1 mm", "1 mm", "1 mm"))] * 201)}, "input.invalid"),
    ],
)
def test_input_errors_are_contract_results_without_warnings(kwargs, family):
    text = kwargs.pop("text")
    failed(run(text, **kwargs), family)


def test_a_bad_name_reports_the_parameter():
    r = failed(run(PROBE_SPEC, name="a b"), "input.invalid")
    assert r.error.details["param"] == "/name"  # a JSON pointer, like every geometry refusal


def test_a_bad_format_lists_the_allowed_ones():
    r = failed(run(PROBE_SPEC, formats="step,iges"), "input.invalid")
    assert r.error.details["param"] == "/formats" and r.error.details["allowed"] == ["step", "glb", "stl", "brep"]


def test_spec_errors_carry_the_pointer_through_the_runner():
    r = failed(run(spec([{"op": "box", "sise": ["1 mm", "1 mm", "1 mm"]}])), "input.invalid")
    assert r.error.details["pointer"] == "/features/0/sise"
    assert "size" in r.error.details["allowed"]


def test_a_missing_engine_gives_an_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "OCP", None)
    r = failed(run(PROBE_SPEC), "engine.unavailable")
    assert "uv sync --extra cad" in r.error.message
    assert r.error.details["install"] == "uv sync --extra cad"


# --------------------------------------------------------------------------- through the gateway


@pytest.mark.asyncio
async def test_the_gateway_lists_the_tool_with_the_generated_description():
    async with Client(gateway) as client:
        tools = await client.list_tools()
    tool = next(t for t in tools if t.name == "geometry_make_part")
    assert tool.description == MAKE_PART_DESCRIPTION
    schema = tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema
    assert schema["required"] == ["spec"]
    assert set(schema["properties"]) == {"spec", "name", "formats", "mesh_deflection", "density", "output_unit"}
    assert all(p["type"] == "string" and p["description"] for p in schema["properties"].values())
    assert len(json.dumps(schema)) < 2000


@pytest.mark.asyncio
async def test_a_real_mcp_call_builds_the_probe_part_and_measure_reads_it_back(ocp):
    async with Client(gateway) as client:
        raw = await client.call_tool("geometry_make_part", {"spec": PROBE_SPEC, "name": "bracket", "density": "2700 kg/m^3"})
        made = ToolResult.model_validate(raw.structured_content)
        assert made.ok, made.error
        step = artifact(made, "bracket.step")
        again = await client.call_tool("geometry_measure", {"model_path": str(step)})
        measured_result = ToolResult.model_validate(again.structured_content)
    assert made.values["volume"].quantity == pytest.approx(PROBE_VOLUME, abs=1e-5)
    assert measured_result.values["volume"].quantity == pytest.approx(PROBE_VOLUME, abs=1e-5)
    assert measured_result.values["surface_area"].quantity == pytest.approx(PROBE_AREA, abs=1e-3)
    (found,) = measured_result.data["holes"]
    assert found["thru"] is True and found["diameter_mm"] == pytest.approx(10.0)
    assert measured_result.provenance.input_files[0].sha256 == made.data["step_sha256"]


# --------------------------------------------------------------------------- process hygiene


def test_nothing_is_written_to_stdout_on_success_or_failure(ocp, capfd):
    assert run(PROBE_SPEC, formats="step,glb,stl,brep").ok
    assert run(spec([box(("10 mm", "10 mm", "10 mm")), fillet("all", "8 mm")])).ok is False
    assert run(spec([box(("10 mm", "10 mm", "10 mm")), fillet("circle", "1 mm")])).ok is False
    assert run("{not json").ok is False
    assert capfd.readouterr().out == ""


def test_a_fresh_process_writes_nothing_to_stdout_not_even_at_exit(ocp, tmp_path):
    script = (
        "import sys\n"
        "from eng_toolkit.domains.geometry.tools import make_part\n"
        "fn = getattr(make_part, 'fn', make_part)\n"
        "fn(spec=sys.argv[1], formats='step,glb,stl,brep')\n"
        "fn(spec=sys.argv[2])\n"
        "fn(spec=sys.argv[3])\n"
    )
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            PROBE_SPEC,
            spec([box(("10 mm", "10 mm", "10 mm")), fillet("all", "8 mm")]),
            "{not json",
        ],
        capture_output=True,
        env={**os.environ, "ENG_TOOLKIT_WS": str(tmp_path / "ws")},
        check=False,
    )
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    assert done.stdout == b""


def test_a_non_ascii_workspace_path_works(ocp, tmp_path, monkeypatch):
    monkeypatch.setenv("ENG_TOOLKIT_WS", str(tmp_path / "工作區_é" / "ws"))
    r = run(PROBE_SPEC, formats="step,glb,stl,brep")
    assert r.ok, r.error
    assert "工作區_é" in r.artifacts[0].path
    assert r.data["readback"]["relative_difference"] < 1e-9
    assert artifact(r, "part.step").stat().st_size > 0


def test_many_holes_build_and_the_result_stays_small(ocp):
    # 1000 holes (the limit) took 26 s on the development machine, 10 s of it reading the STEP back; 250 keep the suite quick
    points = [(f"{3 + 6 * (i % 40)} mm", f"{3 + 6 * (i // 40)} mm", "4 mm") for i in range(250)]
    r = run(spec([box(("240 mm", "150 mm", "4 mm")), hole(points, "2 mm")]))
    assert r.ok, r.error
    assert r.values["volume"].quantity == pytest.approx(240 * 150 * 4 - 250 * math.pi * 1 * 4, abs=1e-3)
    assert r.data["holes_total"] == 250 and len(r.data["holes"]) == 50
    assert len(json.loads(artifact(r, "part.features.json").read_text(encoding="utf-8"))["holes"]) == 250
    assert len(json.dumps(r.data)) < 40000


# --------------------------------------------------------------------------- loose bodies joined later, edge-size limits, hole limit
# Two loose 20 x 20 x 10 boxes (x = 0 and x = 50) and a 30 x 20 x 10 bridge between them (x = 20 to 50).  The final part is one
# solid of 14000 mm^3 minus what the features before the bridge removed, which is why the edge operations below run while there
# are still two solids.  A 10 mm cube with every edge and corner rounded by r is the Minkowski sum of a cube of side s = 10 - 2r
# and a ball: V = s^3 + 6 s^2 r + 3 pi s r^2 + 4/3 pi r^3.
LOOSE_A = box(("20 mm", "20 mm", "10 mm"))
LOOSE_B = box(("20 mm", "20 mm", "10 mm"), at=("50 mm", "0 mm", "0 mm"))
BRIDGE = box(("30 mm", "20 mm", "10 mm"), at=("20 mm", "0 mm", "0 mm"))


def _volume_by_independent_read(result: ToolResult) -> float:
    return measured(artifact(result, "part.step")).values["volume"].quantity


@pytest.mark.parametrize(
    ("edge_op", "loss"),
    [(fillet("|z & convex", "2 mm"), 8 * (4 - math.pi) * 10), (chamfer("|z & convex", "2 mm"), 8 * 2 * 10)],
    ids=["fillet", "chamfer"],
)
def test_edges_of_loose_bodies_are_rounded_before_a_later_feature_joins_them(ocp, edge_op, loss):
    r = run(spec([LOOSE_A, LOOSE_B, edge_op, BRIDGE]))
    assert r.ok, r.error
    want = 14000 - loss
    assert r.data["solids"] == 1
    assert r.value.quantity == pytest.approx(want, abs=1e-6)
    assert _volume_by_independent_read(r) == pytest.approx(want, abs=1e-6)
    assert r.data["features"][2]["edges_used"] == 8  # 4 vertical edges on each loose body


def test_holes_are_drilled_into_loose_bodies_before_they_are_joined(ocp):
    holes = hole([("10 mm", "10 mm", "10 mm"), ("60 mm", "10 mm", "10 mm")], "4 mm")
    r = run(spec([LOOSE_A, LOOSE_B, holes, BRIDGE]))
    assert r.ok, r.error
    want = 14000 - 2 * math.pi * 4 * 10
    assert r.data["solids"] == 1
    assert r.value.quantity == pytest.approx(want, abs=1e-6)
    assert _volume_by_independent_read(r) == pytest.approx(want, abs=1e-6)


def test_an_extreme_face_selector_picks_edges_of_the_outermost_loose_body_only(ocp):
    r = run(spec([LOOSE_A, LOOSE_B, fillet(">x & |z", "2 mm"), BRIDGE]))
    assert r.ok, r.error
    want = 14000 - 2 * (4 - math.pi) * 10
    assert r.data["features"][2]["edges_used"] == 2  # the two vertical edges at x = 70, none of the box at x = 0
    assert r.value.quantity == pytest.approx(want, abs=1e-6)
    assert _volume_by_independent_read(r) == pytest.approx(want, abs=1e-6)


CUBE = box(("10 mm", "10 mm", "10 mm"))


@pytest.mark.parametrize("edge_op", [fillet("all", "5 mm"), chamfer("all", "5 mm")], ids=["fillet", "chamfer"])
def test_an_edge_operation_of_exactly_half_the_part_is_refused_naming_the_feature(ocp, edge_op):
    r = failed(run(spec([CUBE, edge_op])), "geometry.build_failed")
    assert r.error.details["feature_index"] == 2 and r.error.details["pointer"] == "/features/1"


def test_a_fillet_just_under_half_the_part_builds_the_exact_rounded_cube(ocp):
    radius = 4.9
    side = 10 - 2 * radius
    want = side**3 + 6 * side**2 * radius + 3 * math.pi * side * radius**2 + 4 / 3 * math.pi * radius**3
    r = run(spec([CUBE, fillet("all", "4.9 mm")]))
    assert r.ok, r.error
    assert r.data["solids"] == 1
    assert r.value.quantity == pytest.approx(want, rel=1e-9)
    assert _volume_by_independent_read(r) == pytest.approx(want, rel=1e-9)


def test_the_hole_limit_counts_positions_over_all_hole_features_and_fails_before_the_engine():
    row = [(f"{10 + i} mm", "10 mm", "10 mm") for i in range(600)]
    text = spec([box(("1000 mm", "100 mm", "10 mm")), hole(row, "1 mm"), hole(row, "2 mm")])
    r = failed(run(text), "input.invalid")
    assert r.error.details["limit"] == 1000
    assert "in all" in r.error.message


def test_a_fillet_larger_than_the_plate_is_thick_is_refused(ocp):
    r = failed(run(spec([box(("100 mm", "50 mm", "2 mm")), fillet("all", "3 mm")])), "geometry.build_failed")
    assert r.error.details["op"] == "fillet" and r.error.details["feature_index"] == 2


@pytest.mark.parametrize("radius", ["0 mm", "-1 mm"])
def test_a_zero_or_negative_fillet_radius_is_an_input_error_before_the_engine(radius):
    r = failed(run(spec([CUBE, fillet("all", radius)])), "input.invalid")
    assert r.error.details["pointer"] == "/features/1/radius"


def test_an_intersection_with_a_disjoint_solid_is_a_build_failure_naming_the_feature(ocp):
    far = box(("5 mm", "5 mm", "5 mm"), at=("100 mm", "0 mm", "0 mm"), mode="intersect")
    r = failed(run(spec([CUBE, far])), "geometry.build_failed")
    assert r.error.details["feature_index"] == 2 and "nothing is left" in r.error.message
