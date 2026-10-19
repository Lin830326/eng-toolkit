"""geometry_check_dfm checked against numbers that are the construction dimensions of CAD files built with OCP.

Every expected value below is a dimension chosen when the part is built (tests/dfm_fixtures.py), never a value read back
from the tool:

* wall block 60 x 40 x 20, pocket x 1..31, y 10..30, z 5..20: wall 1.0 mm, floor 5.0 mm, four sharp vertical inside corners at
  (1, 10), (31, 10), (31, 30), (1, 30)
* pocket 30 x 20 with corner radius 0.5 centred on (10.5, 10.5): the arc mid point is 0.5 * (1 - cos 45) = 0.146446609 mm from the
  sharp corner along both axes
* blind hole of diameter 2 and depth 30: depth/diameter = 15
* hollow block 40 x 30 x 20 with a 36 x 26 x 16 cavity: six 2.0 mm walls
* tube outer radius 10, inner radius 7: wall 3.0 mm
* sheet 2 mm thick: flat plate with a diameter-5 hole whose wall is 3.5 mm from the edge; a 90 degree bend of inner radius 3 mm
  (outer 5 mm); an L with a sharp inside corner; a plate with a 3 mm boss on 2 mm
* an inch file: 1.27 mm wall = 0.05 inch
"""

import json
import math
import time
from pathlib import Path

import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.geometry import DOMAIN
from eng_toolkit.domains.geometry.tools import check_dfm

NEED_OCP = "OCP is not installed; run: uv sync --extra cad"
TOL = 1e-6


@pytest.fixture(scope="module")
def ocp():
    return pytest.importorskip("OCP", reason=NEED_OCP)


@pytest.fixture(scope="module")
def models(ocp, tmp_path_factory):
    import cad_fixtures as cad
    import dfm_fixtures as fx

    root = tmp_path_factory.mktemp("dfm_models")
    slot = cad.cut(cad.box(length=40, width=30, height=10), cad.box(x=18, y=-1, z=-1, length=3, width=32, height=12))
    return {
        "wall": cad.write_step(fx.wall_block(1.0), root / "wall.step"),
        "sharp": cad.write_step(fx.pocket_block(0), root / "sharp.step"),
        "r05": cad.write_step(fx.pocket_block(0.5), root / "r05.step"),
        "r25": cad.write_step(fx.pocket_block(2.5), root / "r25.step"),
        "r3": cad.write_step(fx.pocket_block(3.0), root / "r3.step"),
        "hole": cad.write_step(fx.deep_hole_block(), root / "hole.step"),
        "clean": cad.write_step(fx.clean_part(), root / "clean.step"),
        "hollow": cad.write_step(fx.hollow_block(), root / "hollow.step"),
        "tube": cad.write_step(fx.tube(), root / "tube.step"),
        "slot": cad.write_step(slot, root / "slot.step"),
        "flat": cad.write_step(fx.flat_sheet(), root / "flat.step"),
        "bent": cad.write_step(fx.bent_sheet(), root / "bent.step"),
        "sharp_l": cad.write_step(fx.sharp_l_sheet(), root / "sharp_l.step"),
        "stepped": cad.write_step(fx.stepped_plate(), root / "stepped.step"),
        "inch": fx.write_step_inches(fx.wall_block(1.27), root / "inch.step"),
        "brep": cad.write_brep(fx.wall_block(1.0), root / "wall.brep"),
        "two_blocks": cad.write_step(cad.compound(fx.wall_block(1.0), cad.box(x=200, length=30, width=30, height=30)), root / "two.step"),
        "shell": cad.write_step(cad.planar_shell(), root / "shell.step"),
    }


def run(model_path, **kwargs) -> ToolResult:
    fn = getattr(check_dfm, "fn", check_dfm)
    result = fn(model_path=str(model_path), **kwargs)
    assert isinstance(result, ToolResult)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    return result


def failed(result: ToolResult, family: str) -> ToolResult:
    assert result.ok is False, "expected a failure"
    assert result.error.family == family, result.error
    assert result.value is None and result.values == {}
    return result


def rules(result: ToolResult, rule: str) -> list[dict]:
    return [f for f in result.data["findings"] if f["rule"] == rule]


def limits(**kwargs) -> str:
    return json.dumps(kwargs)


# --------------------------------------------------------------------------- contract


def test_the_tool_is_registered_in_the_domain():
    assert "check_dfm" in DOMAIN.tools


def test_a_result_follows_the_contract_and_says_what_it_did_not_check(models):
    r = run(models["wall"], limits=limits(min_wall="1.5 mm"))
    assert r.ok, r.error
    assert r.review_status == "unreviewed"
    assert r.provenance.engine == "eng_toolkit.geometry.check_dfm"
    assert r.provenance.input_files[0].sha256 == r.data["face_ids_valid_for"]
    assert r.data["process"] == "cnc_milling" and r.data["supported_processes"] == ["cnc_milling", "sheet_metal"]
    assert r.data["checked"] and len(r.data["not_performed"]) >= 4
    text = " ".join(r.warnings).lower()
    assert "standard" in text and "not checked" in text or "was not" in text or "data.not_performed" in text
    assert "rules of thumb" in text
    assert (r.data["summary"]["errors"], r.data["summary"]["warnings"], r.data["summary"]["info"]) == (5, 0, 0)
    assert r.value.quantity == 5.0 and r.values["errors"].quantity == 5.0
    names = {Path(a.path).name for a in r.artifacts}
    assert "dfm_findings.json" in names
    saved = json.loads(next(Path(a.path) for a in r.artifacts if a.path.endswith("dfm_findings.json")).read_text(encoding="utf-8"))
    assert saved["summary"] == r.data["summary"] and len(saved["findings"]) == 5


def test_every_finding_has_the_required_fields(models):
    r = run(models["hollow"], limits=limits(min_wall="2.5 mm"))
    assert r.data["findings"]
    for f in r.data["findings"]:
        assert f["rule"] and f["severity"] in ("error", "warn", "info")
        assert f["check"] in ("exact", "heuristic")
        assert f["message"] and f["basis"]
        assert f["measured"] is not None and f["limit"] is not None
        assert f["measured"]["unit"] and f["limit"]["unit"]
        assert f["face_ids"] and all(isinstance(i, int) for i in f["face_ids"])
        assert len(f["location_mm"]) == 3 and all(isinstance(v, float) for v in f["location_mm"])


# --------------------------------------------------------------------------- cnc_milling: walls


def test_a_1_mm_wall_is_found_with_its_exact_thickness_and_a_point_inside_it(models):
    r = run(models["wall"], limits=limits(min_wall="1.5 mm"))
    (wall,) = rules(r, "cnc.min_wall")
    assert wall["measured"]["value"] == pytest.approx(1.0, abs=TOL) and wall["measured"]["unit"] == "millimeter"
    assert wall["limit"]["value"] == pytest.approx(1.5, abs=TOL)
    assert wall["severity"] == "error" and wall["check"] == "exact"
    x, y, z = wall["location_mm"]
    assert x == pytest.approx(0.5, abs=TOL) and 10.0 <= y <= 30.0 and 5.0 <= z <= 20.0
    assert r.values["smallest_wall"].quantity == pytest.approx(1.0, abs=TOL)


def test_the_floor_and_walls_pass_when_the_limit_is_below_them(models):
    r = run(models["wall"], limits=limits(min_wall="0.9 mm"))
    assert rules(r, "cnc.min_wall") == []
    assert r.values["smallest_wall"].quantity == pytest.approx(1.0, abs=TOL)  # the smallest wall found is still reported
    at_limit = run(models["wall"], limits=limits(min_wall="1 mm"))
    assert rules(at_limit, "cnc.min_wall") == []  # exactly the limit passes


def test_the_floor_is_a_wall_too(models):
    r = run(models["wall"], limits=limits(min_wall="5.5 mm"))
    gaps = sorted(f["measured"]["value"] for f in rules(r, "cnc.min_wall"))
    assert gaps == pytest.approx([1.0, 5.0], abs=TOL)  # x = 0 to 1, and the floor z = 0 to 5


def test_a_cavity_has_six_2_mm_walls(models):
    r = run(models["hollow"], limits=limits(min_wall="2.5 mm"))
    walls = rules(r, "cnc.min_wall")
    assert len(walls) == 6
    assert [w["measured"]["value"] for w in walls] == pytest.approx([2.0] * 6, abs=TOL)
    assert len({tuple(w["face_ids"]) for w in walls}) == 6


def test_a_slot_between_two_blocks_is_air_not_a_wall(models):
    # a 3 mm slot through a 10 mm plate: the two slot faces face each other across air; only the 10 mm plate is material
    r = run(models["slot"], limits=limits(min_wall="3.5 mm", tool_radius="0.5 mm"))
    assert rules(r, "cnc.min_wall") == []
    assert r.values["smallest_wall"].quantity == pytest.approx(10.0, abs=TOL)


def test_a_tube_wall_is_the_difference_of_the_radii(models):
    r = run(models["tube"], limits=limits(min_wall="4 mm"))
    (wall,) = rules(r, "cnc.min_wall")
    assert wall["measured"]["value"] == pytest.approx(3.0, abs=TOL)
    assert wall["details"]["kind"] == "cylinder"
    x, y, z = wall["location_mm"]
    assert math.hypot(x, y) == pytest.approx(8.5, abs=TOL) and z == pytest.approx(15.0, abs=TOL)  # half way between radius 7 and 10


# --------------------------------------------------------------------------- cnc_milling: internal corners


def test_sharp_inside_corners_are_radius_zero_at_the_pocket_corners(models):
    r = run(models["sharp"])
    corners = rules(r, "cnc.internal_corner_radius")
    assert len(corners) == 4
    assert [c["measured"]["value"] for c in corners] == [0.0] * 4
    assert all(c["limit"]["value"] == pytest.approx(3.0, abs=TOL) and c["check"] == "exact" for c in corners)
    found = sorted((round(c["location_mm"][0], 6), round(c["location_mm"][1], 6), round(c["location_mm"][2], 6)) for c in corners)
    assert found == [(10.0, 10.0, 12.5), (10.0, 30.0, 12.5), (40.0, 10.0, 12.5), (40.0, 30.0, 12.5)]
    assert all(len(c["edge_ids"]) == 1 and len(c["face_ids"]) == 2 for c in corners)


def test_a_0_5_mm_corner_radius_fails_against_a_3_mm_tool(models):
    r = run(models["r05"], limits=limits(tool_radius="3 mm"))
    corners = rules(r, "cnc.internal_corner_radius")
    assert [c["measured"]["value"] for c in corners] == pytest.approx([0.5] * 4, abs=TOL)
    d = 0.5 * (1 - math.cos(math.pi / 4))  # arc mid point to the sharp corner along each axis
    found = sorted((c["location_mm"][0], c["location_mm"][1]) for c in corners)
    expected = sorted((x, y) for x in (10 + d, 40 - d) for y in (10 + d, 30 - d))
    assert [v for xy in found for v in xy] == pytest.approx([v for xy in expected for v in xy], abs=TOL)
    assert all(c["location_mm"][2] == pytest.approx(12.5, abs=TOL) for c in corners)
    assert all(len(c["face_ids"]) == 1 and c["edge_ids"] == [] for c in corners)


def test_a_corner_radius_equal_to_the_tool_radius_passes_and_a_smaller_one_fails(models):
    assert rules(run(models["r3"]), "cnc.internal_corner_radius") == []  # R3 against the default 3 mm tool radius
    smaller = rules(run(models["r25"]), "cnc.internal_corner_radius")
    assert [c["measured"]["value"] for c in smaller] == pytest.approx([2.5] * 4, abs=TOL)
    assert rules(run(models["r25"], limits=limits(tool_radius="2.5 mm")), "cnc.internal_corner_radius") == []


def test_corners_are_judged_along_the_tool_axis(models):
    # tool along x: the vertical sharp edges are no longer along it; the two sharp floor-to-wall edges at y = 10 and y = 30 are
    r = run(models["sharp"], tool_axis="+x")
    corners = rules(r, "cnc.internal_corner_radius")
    assert len(corners) == 2
    assert sorted(round(c["location_mm"][1], 6) for c in corners) == [10.0, 30.0]
    assert run(models["sharp"], tool_axis="-z").data["summary"]["errors"] == 4  # the sign of the axis does not matter


# --------------------------------------------------------------------------- cnc_milling: holes


def test_a_diameter_2_hole_30_deep_has_ratio_15(models):
    r = run(models["hole"])
    (hole,) = rules(r, "cnc.hole_depth_ratio")
    assert hole["measured"]["value"] == pytest.approx(15.0, abs=TOL) and hole["measured"]["unit"] == "ratio"
    assert hole["limit"]["value"] == 10.0
    assert hole["details"]["diameter_mm"] == pytest.approx(2.0, abs=TOL) and hole["details"]["depth_mm"] == pytest.approx(30.0, abs=TOL)
    assert hole["location_mm"] == pytest.approx([10.0, 10.0, 40.0], abs=TOL)  # where the hole starts, on the top face
    assert rules(r, "cnc.min_hole_diameter") == []  # 2 mm is above the default 1 mm


def test_the_ratio_limit_is_inclusive_and_the_smallest_hole_limit_is_a_diameter(models):
    assert rules(run(models["hole"], limits=limits(max_hole_depth_ratio=15)), "cnc.hole_depth_ratio") == []
    assert len(rules(run(models["hole"], limits=limits(max_hole_depth_ratio=14.99)), "cnc.hole_depth_ratio")) == 1
    (small,) = rules(run(models["hole"], limits=limits(min_hole_diameter="2.5 mm")), "cnc.min_hole_diameter")
    assert small["measured"]["value"] == pytest.approx(2.0, abs=TOL) and small["limit"]["value"] == pytest.approx(2.5, abs=TOL)


# --------------------------------------------------------------------------- cnc_milling: access and a clean part


def test_faces_nothing_can_reach_are_heuristic_warnings(models):
    r = run(models["hollow"])
    access = rules(r, "cnc.tool_access")
    assert len(access) == 6
    assert all(f["severity"] == "warn" and f["check"] == "heuristic" and f["measured"]["value"] == 0.0 for f in access)
    assert all(2.0 - TOL <= f["location_mm"][0] <= 38.0 + TOL and 2.0 - TOL <= f["location_mm"][1] <= 28.0 + TOL and 2.0 - TOL <= f["location_mm"][2] <= 18.0 + TOL for f in access)
    assert r.data["summary"]["heuristic_findings"] >= 6


def test_a_clean_part_has_no_errors_and_no_warnings(models):
    r = run(models["clean"])
    assert r.ok, r.error
    assert r.data["summary"]["errors"] == 0 and r.data["summary"]["warnings"] == 0 and r.data["summary"]["total"] == 0
    assert r.value.quantity == 0.0
    assert r.data["measurements"]["internal_fillets_checked"] == 4 and r.data["measurements"]["holes"] == 1


def test_default_limits_are_reported_with_their_source(models):
    r = run(models["clean"], limits=limits(tool_radius="2 mm"))
    assert r.data["limits"]["tool_radius"]["source"] == "caller" and r.data["limits"]["min_wall"]["source"] == "default"
    assert r.data["limits"]["tool_radius"]["value"] == pytest.approx(2.0)
    assert any("Default limits were used" in w for w in r.warnings)


# --------------------------------------------------------------------------- sheet_metal


def test_sheet_thickness_is_the_exact_plate_thickness(models):
    r = run(models["flat"], process="sheet_metal")
    (t,) = rules(r, "sheet.thickness")
    assert t["measured"]["value"] == pytest.approx(2.0, abs=TOL) and t["severity"] == "info"
    assert r.values["thickness"].quantity == pytest.approx(2.0, abs=TOL)
    assert r.data["summary"]["errors"] == 0


def test_hole_to_edge_distance_is_measured_from_the_hole_wall(models):
    # diameter-5 hole at x = 6: its wall is 6 - 2.5 = 3.5 mm from the edge x = 0 (y: 15 - 2.5 = 12.5 from the others)
    default = run(models["flat"], process="sheet_metal")  # 1.5 * 2 = 3 mm: passes
    assert rules(default, "sheet.hole_to_edge") == []
    r = run(models["flat"], process="sheet_metal", limits=limits(min_hole_edge_ratio=2))
    (edge,) = rules(r, "sheet.hole_to_edge")
    assert edge["measured"]["value"] == pytest.approx(3.5, abs=TOL) and edge["limit"]["value"] == pytest.approx(4.0, abs=TOL)
    assert edge["check"] == "exact" and edge["location_mm"] == pytest.approx([6.0, 15.0, 2.0], abs=TOL)
    assert rules(run(models["flat"], process="sheet_metal", limits=limits(min_hole_edge_ratio=1.75)), "sheet.hole_to_edge") == []  # 3.5 = 1.75 * 2


def test_bend_inner_radius_3_in_2_mm_sheet(models):
    ok = run(models["bent"], process="sheet_metal", limits=limits(min_bend_radius_ratio=1.5))
    assert rules(ok, "sheet.min_bend_radius") == []  # 3 = 1.5 * 2 exactly
    assert ok.data["measurements"]["bends"] == 1 and ok.data["summary"]["errors"] == 0
    assert rules(ok, "sheet.thickness")[0]["measured"]["value"] == pytest.approx(2.0, abs=TOL)
    r = run(models["bent"], process="sheet_metal", limits=limits(min_bend_radius_ratio=2))
    (bend,) = rules(r, "sheet.min_bend_radius")
    assert bend["measured"]["value"] == pytest.approx(3.0, abs=TOL) and bend["limit"]["value"] == pytest.approx(4.0, abs=TOL)
    assert bend["details"]["bend_angle_deg"] == pytest.approx(90.0, abs=1e-6)
    assert bend["details"]["outer_radius_mm"] == pytest.approx(5.0, abs=TOL)
    x, y, z = bend["location_mm"]  # on the bisector, half way between radius 3 and 5 from the axis through (40, ., 5)
    assert (x, z) == pytest.approx((40 + 4 * math.cos(math.pi / 4), 5 - 4 * math.sin(math.pi / 4)), abs=TOL) and 0.0 <= y <= 30.0


def test_a_sharp_bend_is_radius_zero_and_heuristic(models):
    r = run(models["sharp_l"], process="sheet_metal")
    (bend,) = rules(r, "sheet.min_bend_radius")
    assert bend["measured"]["value"] == 0.0 and bend["limit"]["value"] == pytest.approx(2.0, abs=TOL)
    assert bend["check"] == "heuristic" and len(bend["edge_ids"]) == 1
    assert bend["location_mm"][0] == pytest.approx(38.0, abs=TOL) and bend["location_mm"][2] == pytest.approx(2.0, abs=TOL)


def test_two_thicknesses_are_reported_as_not_uniform(models):
    r = run(models["stepped"], process="sheet_metal")
    (uniform,) = rules(r, "sheet.thickness_uniform")
    assert uniform["severity"] == "error" and uniform["check"] == "heuristic"
    assert uniform["details"]["other_thicknesses_mm"] == pytest.approx([3.0], abs=TOL)
    assert rules(r, "sheet.thickness")[0]["measured"]["value"] == pytest.approx(2.0, abs=TOL)
    clean = run(models["flat"], process="sheet_metal")
    assert rules(clean, "sheet.thickness_uniform") == []


def test_a_block_is_not_sheet_metal(models):
    r = run(models["hole"], process="sheet_metal", limits=limits(max_thickness="5 mm"))
    (none,) = rules(r, "sheet.thickness")
    assert none["severity"] == "error" and none["check"] == "heuristic"
    assert r.data["summary"]["errors"] == 1


# --------------------------------------------------------------------------- units and file kinds


def test_an_inch_file_is_read_in_millimetres_and_reported_in_the_output_unit(models):
    mm = run(models["inch"], limits=limits(min_wall="2 mm"))
    (wall,) = rules(mm, "cnc.min_wall")
    assert wall["measured"]["value"] == pytest.approx(1.27, abs=TOL)
    inch = run(models["inch"], limits=limits(min_wall="0.1 inch"), output_unit="inch")
    (wall,) = rules(inch, "cnc.min_wall")
    assert wall["measured"]["value"] == pytest.approx(0.05, abs=1e-9) and wall["measured"]["unit"] == "inch"
    assert wall["limit"]["value"] == pytest.approx(0.1, abs=1e-9)
    assert wall["location_mm"][0] == pytest.approx(0.635, abs=TOL)  # locations stay in millimetres
    assert inch.values["smallest_wall"].unit == "inch"


def test_a_brep_file_gives_the_same_wall(models):
    (wall,) = rules(run(models["brep"], limits=limits(min_wall="1.5 mm")), "cnc.min_wall")
    assert wall["measured"]["value"] == pytest.approx(1.0, abs=TOL)


def test_several_solids_are_checked_together_with_a_warning(models):
    r = run(models["two_blocks"], limits=limits(min_wall="1.5 mm"))
    assert r.ok and r.data["solids"] == 2
    assert any("2 solids" in w for w in r.warnings)
    (wall,) = rules(r, "cnc.min_wall")
    assert wall["measured"]["value"] == pytest.approx(1.0, abs=TOL)


def test_a_shell_without_a_solid_is_refused(models):
    r = failed(run(models["shell"]), "geometry.not_a_solid")
    assert "no closed solid" in r.error.message and r.error.details["solids"] == 0


def test_an_empty_file_is_input_invalid(ocp, tmp_path):
    empty = tmp_path / "empty.step"
    empty.write_bytes(b"")
    failed(run(empty), "input.invalid")


def test_a_mesh_file_is_refused(ocp, tmp_path):
    failed(run(tmp_path / "x.stl"), "input.unsupported_format")


# --------------------------------------------------------------------------- inputs


@pytest.mark.parametrize("process", ["turning", "injection_molding", "additive", "cnc_3axis"])
def test_a_recognised_but_unsupported_process_is_refused_not_guessed(models, process):
    r = failed(run(models["wall"], process=process), "geometry.unsupported_process")
    assert "not supported" in r.error.message and "cnc_milling" in r.error.message and "sheet_metal" in r.error.message
    assert r.error.details["supported"] == ["cnc_milling", "sheet_metal"]


def test_an_unknown_process_says_it_is_unknown(models):
    r = failed(run(models["wall"], process="witchcraft"), "geometry.unsupported_process")
    assert "not a known process" in r.error.message


@pytest.mark.parametrize(
    ("raw", "family"),
    [
        ('{"nope": "1 mm"}', "input.invalid"),
        ('{"min_wall": 1}', "units.bare_number"),
        ('{"min_wall": "1"}', "units.bare_number"),
        ('{"min_wall": "-1 mm"}', None),
        ('{"min_wall": "1 kg"}', None),
        ('{"max_hole_depth_ratio": 0}', "input.invalid"),
        ('{"max_hole_depth_ratio": "ten"}', "input.invalid"),
        ("[1, 2]", "input.invalid"),
        ("{not json", "input.invalid"),
        ('{"min_bend_radius_ratio": 1.5}', "input.invalid"),  # a sheet_metal limit given to cnc_milling
    ],
)
def test_bad_limits_are_refused_with_a_reason(models, raw, family):
    r = run(models["wall"], limits=raw)
    assert r.ok is False and r.error.message
    if family:
        assert r.error.family == family


def test_bad_tool_axis_and_max_findings_are_refused(models):
    failed(run(models["wall"], tool_axis="w"), "input.invalid")
    failed(run(models["wall"], max_findings=0), "input.invalid")


def test_findings_beyond_max_findings_are_counted_but_not_listed(models):
    r = run(models["sharp"], max_findings=2)
    assert r.data["summary"]["errors"] == 4 and len(r.data["findings"]) == 2 and r.data["findings_listed"] == 2
    assert any("dfm_findings.json" in w for w in r.warnings)
    saved = json.loads(next(Path(a.path) for a in r.artifacts if a.path.endswith("dfm_findings.json")).read_text(encoding="utf-8"))
    assert len(saved["findings"]) == 4


# --------------------------------------------------------------------------- the geometry facts underneath


def test_edge_facts_tell_convex_concave_and_smooth(models, ocp):
    from eng_toolkit import brep

    solid = brep.read_solid(models["wall"])
    edges = brep.edge_facts(solid)
    assert sorted({e.convexity for e in edges}) == ["concave", "convex"]
    assert sum(e.convexity == "concave" for e in edges) == 8  # four pocket verticals and four floor edges
    assert all(e.material_angle_deg == pytest.approx(270.0 if e.convexity == "concave" else 90.0, abs=1e-9) for e in edges)
    assert all(e.kind == "line" for e in edges)
    rounded = brep.edge_facts(brep.read_solid(models["r05"]))
    assert sum(e.convexity == "smooth" for e in rounded) == 8  # each of the four fillets is tangent to two walls
    assert all(e.material_angle_deg == 180.0 for e in rounded if e.convexity == "smooth")


# --------------------------------------------------------------------------- size and time


def test_a_plate_with_hundreds_of_faces_is_checked_in_reasonable_time(ocp, tmp_path):
    import cad_fixtures as cad
    import dfm_fixtures as fx

    path = cad.write_step(fx.hole_grid(12, 12, pitch=5.0, diameter=2.0), tmp_path / "grid.step")  # 144 holes, 150 faces
    started = time.perf_counter()
    r = run(path, limits=limits(min_wall="2 mm"))
    elapsed = time.perf_counter() - started
    assert r.ok and r.data["faces_total"] == 150 and r.data["holes_total"] == 144
    assert r.data["summary"]["errors"] == 0
    assert elapsed < 60.0


@pytest.mark.asyncio
async def test_the_tool_works_through_a_real_mcp_client(models):
    from fastmcp import Client

    from eng_toolkit.gateway.server import gateway

    async with Client(gateway) as client:
        tool = next(t for t in await client.list_tools() if t.name == "geometry_check_dfm")
        assert tool.input_schema["required"] == ["model_path"]
        assert all(p.get("description") for p in tool.input_schema["properties"].values())
        call = await client.call_tool(
            "geometry_check_dfm", {"model_path": str(models["wall"]), "limits": limits(min_wall="1.5 mm")}
        )
    result = ToolResult.model_validate(call.structured_content)
    assert result.ok and result.data["summary"]["errors"] == 5
    (wall,) = rules(result, "cnc.min_wall")
    assert wall["measured"]["value"] == pytest.approx(1.0, abs=TOL)


def test_a_model_over_the_face_limit_is_refused(models, monkeypatch):
    from eng_toolkit.domains.geometry import dfm

    monkeypatch.setattr(dfm, "MAX_FACES", 5)
    r = failed(run(models["wall"]), "input.too_large")
    assert r.error.details == {"faces": 11, "limit": 5}
