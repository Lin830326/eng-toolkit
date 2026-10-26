"""Regression tests for the DFM and thickness problems the user-acceptance round found (scenarios C1 and C4).

Expected numbers are construction dimensions, never values read back from the tool:

* heatsink (dfm_fixtures.heatsink): three tapered fins, 4 mm at the root and 2 mm at y = 30 (lean atan(1/27) = 2.12 deg),
  semicircular tips that meet the flanks at a 182.12 deg concave kink, R0.25 root fillets, three diameter-3.2 through
  holes 35 deep (35 / 3.2 = 10.9375) whose bottoms are 2.54 - 1.6 = 0.94 mm above the base bottom
* the KiCad heatsink (when installed): 11 fins, 22 R0.25 root fillets, 22 kinks of 182.01 deg, 3 x d3.2 through 35 mm,
  the same 0.94 mm ligament; the fin at x = 48 spans x 46.432..49.568 at y = 15
* the L bracket of scenario C4: 3 mm plate, 80 x 40 base, 60 high upright, inner bend radius 2 or 3 mm (outer 5 or 6),
  built from radius arcs or from 3-point arcs through via points rounded to 4 decimals, two 3 mm triangular ribs at
  y = 11.833 and 25.167 standing in the bend (four concave junction edges each), four diameter-5.5 holes
* wall block 60 x 40 x 20 with a pocket x 1..31: the wall x 0..1 is 1 mm, the pocket 30 mm wide, the block beyond 29 mm
* slot: a 3 mm slot through a 40 x 30 x 10 plate
"""

import json
import math
from pathlib import Path

import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.geometry import dfm
from eng_toolkit.domains.geometry.tools import check_dfm, make_part, measure

NEED_OCP = "OCP is not installed; run: uv sync --extra cad"
TOL = 1e-6
KICAD_HEATSINK = Path(
    r"C:\Users\USER\AppData\Local\Programs\KiCad\10.0\share\kicad\3dmodels\Heatsink.3dshapes"
    r"\Heatsink_125x35x50mm_3xFixationM3.step"
)
SHOP = json.dumps({"min_wall": "1 mm", "tool_radius": "1.5 mm"})  # C1: a 3 mm DIAMETER end mill, 1 mm walls


@pytest.fixture(scope="module")
def ocp():
    return pytest.importorskip("OCP", reason=NEED_OCP)


@pytest.fixture(scope="module")
def models(ocp, tmp_path_factory):
    import cad_fixtures as cad
    import dfm_fixtures as fx

    root = tmp_path_factory.mktemp("uat_models")
    slot = cad.cut(cad.box(length=40, width=30, height=10), cad.box(x=18, y=-1, z=-1, length=3, width=32, height=12))
    return {
        "heatsink": cad.write_step(fx.heatsink(), root / "heatsink.step"),
        "wall": cad.write_step(fx.wall_block(1.0), root / "wall.step"),
        "sharp": cad.write_step(fx.pocket_block(0), root / "sharp.step"),
        "slot": cad.write_step(slot, root / "slot.step"),
        "clean": cad.write_step(fx.clean_part(), root / "clean.step"),
        "block": cad.write_step(cad.box(), root / "block.step"),
        "sharp_l": cad.write_step(fx.sharp_l_sheet(), root / "sharp_l.step"),
        "stepped": cad.write_step(fx.stepped_plate(), root / "stepped.step"),
        "hollow": cad.write_step(fx.hollow_block(), root / "hollow.step"),
    }


def tool(fn, **kwargs) -> ToolResult:
    result = getattr(fn, "fn", fn)(**kwargs)
    assert isinstance(result, ToolResult)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    return result


def dfm_run(path, **kwargs) -> ToolResult:
    result = tool(check_dfm, model_path=str(path), max_findings=500, **kwargs)
    assert result.ok, result.error
    return result


def rules(result: ToolResult, rule: str) -> list[dict]:
    return [f for f in result.data["findings"] if f["rule"] == rule]


# --------------------------------------------------------------------------- 1. near-tangent kinks are not corners


def test_the_heatsink_fin_tip_kinks_are_not_sharp_corners_but_the_root_fillets_are_reported(models):
    r = dfm_run(models["heatsink"], limits=SHOP)
    corners = rules(r, "cnc.internal_corner_radius")
    assert len(corners) == 6  # the six R0.25 root fillets, nothing else
    assert [c["measured"]["value"] for c in corners] == pytest.approx([0.25] * 6, abs=TOL)
    m = r.data["measurements"]
    assert m["sharp_internal_corners"] == 0 and m["near_tangent_edges_skipped"] == 6 and m["internal_fillets_checked"] == 6
    assert any("near-tangent" in w and "6 concave edges" in w for w in r.warnings)
    assert r.data["limits"]["near_tangent_deg"] == {"value": 5.0, "unit": "degree", "source": "default", "meaning": dfm.NEAR_TANGENT[2]}


def test_near_tangent_deg_is_a_limit_and_a_smaller_one_reports_the_kinks_again(models):
    r = dfm_run(models["heatsink"], limits=json.dumps({"min_wall": "1 mm", "tool_radius": "1.5 mm", "near_tangent_deg": 2}))
    sharp = [c for c in rules(r, "cnc.internal_corner_radius") if c["measured"]["value"] == 0.0]
    assert len(sharp) == 6
    lean = math.degrees(math.atan(1 / 27))
    assert all(c["details"]["material_angle_deg"] == pytest.approx(180 + lean, abs=1e-6) for c in sharp)
    # the material a 1.5 mm tool leaves in that kink: r (1 / sin(a / 2) - 1), a = 180 - lean, the angle of the air
    cusp = 1.5 * (1 / math.sin(math.radians(180 - lean) / 2) - 1)
    assert all(c["details"]["uncut_depth_mm"] == pytest.approx(cusp, rel=1e-6) for c in sharp)
    assert r.data["limits"]["near_tangent_deg"]["source"] == "caller"


def test_an_obviously_sharp_pocket_corner_is_still_flagged_whatever_the_near_tangent_limit(models):
    for limit in (None, 89):
        given = {} if limit is None else {"near_tangent_deg": limit}
        r = dfm_run(models["sharp"], limits=json.dumps(given))
        corners = rules(r, "cnc.internal_corner_radius")
        assert len(corners) == 4 and all(c["measured"]["value"] == 0.0 for c in corners)
        assert all(c["details"]["material_angle_deg"] == pytest.approx(270.0, abs=1e-6) for c in corners)
        assert all(c["details"]["uncut_depth_mm"] == pytest.approx(3.0 * (math.sqrt(2) - 1), rel=1e-6) for c in corners)


@pytest.mark.parametrize("bad", [0, -1, 91, "x", True])
def test_a_bad_near_tangent_limit_is_refused_at_its_key(bad):
    from eng_toolkit.errors import ToolFailure

    with pytest.raises(ToolFailure) as caught:
        dfm.parse_limits("cnc_milling", json.dumps({"near_tangent_deg": bad}))
    assert caught.value.details["param"] == "/limits/near_tangent_deg"


# --------------------------------------------------------------------------- 2. walls between faces that are not parallel


def test_the_hole_to_bottom_ligament_is_an_exact_0_94_mm_wall(models):
    r = dfm_run(models["heatsink"], limits=SHOP)
    walls = rules(r, "cnc.min_wall")
    assert len(walls) == 3
    assert [w["measured"]["value"] for w in walls] == pytest.approx([0.94] * 3, abs=TOL)
    assert all(w["check"] == "exact" and w["severity"] == "error" for w in walls)
    xs = sorted(w["location_mm"][0] for w in walls)
    assert xs == pytest.approx([-12.0, 0.0, 12.0], abs=TOL)
    assert all(w["location_mm"][1] == pytest.approx((-2.54 - 1.6) / 2, abs=TOL) for w in walls)  # half way down the ligament
    assert r.data["measurements"]["smallest_wall_mm"] == pytest.approx(0.94, abs=TOL)
    assert r.values["smallest_wall"].quantity == pytest.approx(0.94, abs=TOL)
    (holes,) = [r.data["summary"]["by_rule"]["cnc.hole_depth_ratio"]]
    assert holes == {"error": 3}
    assert all(f["measured"]["value"] == pytest.approx(35 / 3.2, abs=TOL) for f in rules(r, "cnc.hole_depth_ratio"))
    assert r.data["summary"]["errors"] == 6 + 3 + 3  # root fillets, deep holes, ligaments: the real problems only


def test_tapered_fin_flanks_are_measured_although_they_are_not_parallel(models):
    # min_wall 2.5: the fins are 2 mm at y = 30 (4 at the root); the flanks lean 2.12 deg, so no parallel pair exists
    r = dfm_run(models["heatsink"], limits=json.dumps({"min_wall": "2.5 mm", "tool_radius": "0.2 mm"}))
    flanks = [w for w in rules(r, "cnc.min_wall") if w["details"]["kind"] == "normal_ray" and w["location_mm"][1] > 3]
    assert flanks, "the tapered fins were not measured"
    assert all(2.0 - TOL <= w["measured"]["value"] < 2.5 for w in flanks)
    assert all(w["check"] == "heuristic" and w["severity"] == "warn" for w in flanks)
    assert r.data["measurements"]["wall_rays_cast"] > 0


def test_no_wall_within_the_search_range_is_said_not_silently_null(models):
    r = dfm_run(models["block"])  # a 100 x 50 x 20 block; the search range is 5 x 0.8 = 4 mm
    assert r.data["measurements"]["smallest_wall_mm"] is None and "smallest_wall" not in r.values
    assert any("No wall thinner than the search range of 4 mm" in w for w in r.warnings)
    assert r.data["measurements"]["wall_faces_sampled"] == 6


def test_a_hole_2_mm_from_a_side_face_is_measured(models):
    # clean_part: the diameter-6 hole at x = 45 leaves 50 - 45 - 3 = 2 mm to the side x = 50 and 45 - 3 - 40 = 2 mm to the
    # pocket wall x = 40; no parallel pair is that thin, the rays from the hole wall find it
    r = dfm_run(models["clean"])
    assert r.data["measurements"]["smallest_wall_mm"] == pytest.approx(2.0, abs=TOL)
    assert rules(r, "cnc.min_wall") == []  # 2 mm is above the default 0.8 mm
    walls = rules(dfm_run(models["clean"], limits=json.dumps({"min_wall": "2.5 mm"})), "cnc.min_wall")
    exact = [w for w in walls if w["details"]["kind"] == "normal_ray"]
    assert len(exact) == 2 and all(w["measured"]["value"] == pytest.approx(2.0, abs=TOL) and w["check"] == "exact" for w in exact)
    assert sorted(round(w["location_mm"][0], 6) for w in exact) == [41.0, 49.0]


def test_parallel_walls_are_found_once_not_again_by_the_rays(models):
    r = dfm_run(models["hollow"], limits=json.dumps({"min_wall": "2.5 mm"}))
    walls = rules(r, "cnc.min_wall")
    assert len(walls) == 6 and all(w["details"]["kind"] == "plane" for w in walls)


# --------------------------------------------------------------------------- 3. thickness from a point inside the material


def test_thickness_from_inside_the_wall_is_that_wall_not_the_next_gap(models):
    r = tool(measure, model_path=str(models["wall"]), queries=(
        "thickness P(0.5 mm, 20 mm, 10 mm) +x; thickness P(-5 mm, 20 mm, 10 mm) +x; thickness P(0 mm, 20 mm, 10 mm) -x; "
        "thickness P(0 mm, 20 mm, 10 mm) +x; thickness P(10 mm, 20 mm, 10 mm) +x"
    ))
    assert r.ok, r.error
    inside, outside, behind, ahead, air = r.data["queries"]
    assert inside["start"] == "inside" and inside["value"] == pytest.approx(1.0, abs=TOL)  # was 30: the pocket after the wall
    assert inside["entry_mm"] == pytest.approx([0.0, 20.0, 10.0], abs=TOL) and inside["exit_mm"] == pytest.approx([1.0, 20.0, 10.0], abs=TOL)
    assert outside["start"] == "outside" and outside["value"] == pytest.approx(1.0, abs=TOL)
    assert behind["start"] == "surface" and behind["value"] == pytest.approx(1.0, abs=TOL)
    assert ahead["start"] == "surface" and ahead["value"] == pytest.approx(1.0, abs=TOL)
    assert air["start"] == "outside" and air["value"] == pytest.approx(29.0, abs=TOL)  # the block beyond the pocket
    assert any(w.startswith("q1: the start point") and "inside the material" in w for w in r.warnings)
    assert any(w.startswith("q3:") and "behind" in w for w in r.warnings)
    assert not any(w.startswith(("q2:", "q4:", "q5:")) for w in r.warnings)


def test_thickness_inside_a_heatsink_fin_is_the_fin(models):
    # fin at x = 12: at y = 15 the flanks are at 12 +- (2 - 12 / 27)
    half = 2.0 - (15.0 - 3.0) / 27.0
    r = tool(measure, model_path=str(models["heatsink"]), queries="thickness P(12.2 mm, 15 mm, 17.5 mm) +x; thickness P(0 mm, 10 mm, 17.5 mm) -y")
    fin, above_hole = r.data["queries"]
    assert fin["start"] == "inside" and fin["value"] == pytest.approx(2 * half, abs=TOL)
    # from inside the fin above the hole, downward: the material from the hole top (y = 1.6) to the tip (y = 31)
    assert above_hole["start"] == "inside" and above_hole["value"] == pytest.approx(31.0 - 1.6, abs=TOL)


# --------------------------------------------------------------------------- 4 and 5. the C4 bracket: one bend, ribs are joints


def bracket_spec(inner, *, arcs, ribs=True):
    """The C4 bracket as the assistant built it: a 'path' profile in plane xz extruded 40 mm, ribs, four M5 holes."""
    t, outer = 3.0, inner + 3.0
    if arcs == "via":  # 3-point arcs through via points rounded to 4 decimals, as in the transcript
        k = 1 - math.sqrt(0.5)
        bend_out = {"to": [f"{outer} mm", "0.0 mm"], "via": [f"{round(outer * k, 4)} mm"] * 2}
        bend_in = {"to": ["3.0 mm", f"{outer} mm"], "via": [f"{round(t + inner * k, 4)} mm"] * 2}
    else:
        bend_out = {"to": [f"{outer} mm", "0.0 mm"], "radius": f"{outer} mm", "turn": "left"}
        bend_in = {"to": ["3.0 mm", f"{outer} mm"], "radius": f"{inner} mm", "turn": "right"}
    segments = [
        {"to": ["0.0 mm", f"{outer} mm"]}, bend_out, {"to": ["80.0 mm", "0.0 mm"]}, {"to": ["80.0 mm", "3.0 mm"]},
        {"to": [f"{outer} mm", "3.0 mm"]}, bend_in, {"to": ["3.0 mm", "60.0 mm"]}, {"to": ["0.0 mm", "60.0 mm"]},
    ]
    features = [{"op": "extrude", "name": "bracket_body", "plane": "xz", "offset": "0.0 mm", "distance": "40.0 mm", "mode": "add",
                 "profile": {"path": {"start": ["0.0 mm", "60.0 mm"], "segments": segments}}}]
    if ribs:
        for name, offset in (("gusset_1", "11.833 mm"), ("gusset_2", "25.167 mm")):
            features.append({"op": "extrude", "name": name, "plane": "xz", "offset": offset, "distance": "3.0 mm", "mode": "add",
                             "profile": {"polygon": {"points": [["3.0 mm", "3.0 mm"], ["28.0 mm", "3.0 mm"], ["3.0 mm", "28.0 mm"]]}}})
    features.append({"op": "hole", "name": "base_holes", "at": [["60.0 mm", "10.0 mm", "3.0 mm"], ["60.0 mm", "30.0 mm", "3.0 mm"]],
                     "axis": "-z", "diameter": "5.5 mm"})
    features.append({"op": "hole", "name": "flange_holes", "at": [["0.0 mm", "10.0 mm", "45.0 mm"], ["0.0 mm", "30.0 mm", "45.0 mm"]],
                     "axis": "+x", "diameter": "5.5 mm"})
    return {"features": features}


def build(spec, name) -> str:
    r = tool(make_part, spec=json.dumps(spec), name=name)
    assert r.ok, r.error
    return next(a.path for a in r.artifacts if a.path.endswith(".step"))


@pytest.mark.parametrize("arcs", ["radius", "via"])
def test_the_ribbed_r2_bracket_has_one_bend_at_0_667_t_and_its_ribs_are_joints(ocp, arcs):
    r = dfm_run(build(bracket_spec(2.0, arcs=arcs), f"L_{arcs}_r2"), process="sheet_metal")
    (bend,) = rules(r, "sheet.min_bend_radius")
    assert bend["message"].startswith("Bend inner radius 2 mm is 0.667 times the thickness 3 mm, below 1")
    assert bend["severity"] == "error" and bend["check"] == "exact"
    assert bend["measured"]["value"] == pytest.approx(2.0, abs=1e-3) and bend["details"]["face_pairs"] == 3
    m = r.data["measurements"]
    assert (m["bends"], m["bend_face_pairs"], m["sharp_bends"], m["joints"]) == (1, 3, 0, 8)
    assert m["thickness_mm"] == pytest.approx(3.0, abs=TOL) and m["unrecognised_bend_faces"] == []
    (joint,) = rules(r, "sheet.joint")
    assert joint["severity"] == "info" and joint["check"] == "heuristic" and joint["measured"]["value"] == 8.0
    assert r.data["summary"]["errors"] == 1 and r.data["summary"]["warnings"] == 0


@pytest.mark.parametrize("arcs", ["radius", "via"])
def test_the_bend_is_seen_without_ribs_and_r3_passes(ocp, arcs):
    r2 = dfm_run(build(bracket_spec(2.0, arcs=arcs, ribs=False), f"L_{arcs}_r2_plain"), process="sheet_metal")
    assert r2.data["measurements"]["bends"] == 1
    (bend,) = rules(r2, "sheet.min_bend_radius")
    assert bend["message"] == "Bend inner radius 2 mm is 0.667 times the thickness 3 mm, below 1"
    r3 = dfm_run(build(bracket_spec(3.0, arcs=arcs), f"L_{arcs}_r3"), process="sheet_metal")
    assert r3.data["measurements"]["bends"] == 1 and rules(r3, "sheet.min_bend_radius") == []  # 3 = 1.0 x 3, inclusive
    assert r3.data["summary"]["errors"] == 0 and r3.data["summary"]["warnings"] == 0


def test_via_arc_bend_cylinders_off_axis_by_7e_5_mm_are_one_bend(ocp):
    from eng_toolkit import brep

    solid = brep.read_solid(Path(build(bracket_spec(3.0, arcs="via", ribs=False), "L_via_axes")), param="model_path")
    inv = brep.inventory(solid)
    bends = [c for c in brep.cylinder_facts(solid, inv) if c.span_rad < 6]
    assert len(bends) == 2
    first, second = (sorted(bends, key=lambda c: c.radius_mm))
    offset = [a - b for a, b in zip(first.start_mm, second.start_mm)]
    off_axis = math.hypot(offset[0], offset[2])  # the axes run along y
    assert 1e-6 < off_axis < brep.coaxial_tolerance_mm(3.0)  # beyond the old 1e-6 mm, within 1e-3 of the thickness
    pairs = brep.facing_pairs(solid, inv, tuple(bends), max_gap_mm=10.0).pairs
    assert [p.kind for p in pairs].count("cylinder") == 1


def test_ribs_flush_with_the_plate_edges_are_joints_and_the_sharp_corner_is_the_one_bend(ocp):
    # the first C4 model: two boxes (no bend radius) and ribs flush with the side faces y = 0..3 and 37..40
    spec = {"features": [
        {"op": "box", "name": "base", "at": ["0 mm", "0 mm", "0 mm"], "size": ["80 mm", "40 mm", "3 mm"], "mode": "add"},
        {"op": "box", "name": "flange", "at": ["0 mm", "0 mm", "0 mm"], "size": ["3 mm", "40 mm", "60 mm"], "mode": "add"},
        {"op": "extrude", "name": "rib_front", "plane": "xz", "offset": "0 mm", "distance": "3 mm", "mode": "add",
         "profile": {"polygon": {"points": [["3 mm", "3 mm"], ["33 mm", "3 mm"], ["3 mm", "33 mm"]]}}},
        {"op": "extrude", "name": "rib_back", "plane": "xz", "offset": "37 mm", "distance": "3 mm", "mode": "add",
         "profile": {"polygon": {"points": [["3 mm", "3 mm"], ["33 mm", "3 mm"], ["3 mm", "33 mm"]]}}},
    ]}
    r = dfm_run(build(spec, "L_boxes"), process="sheet_metal")
    (bend,) = rules(r, "sheet.min_bend_radius")
    assert bend["measured"]["value"] == 0.0 and bend["check"] == "heuristic" and bend["severity"] == "warn"
    assert bend["location_mm"][0] == pytest.approx(3.0, abs=TOL) and bend["location_mm"][2] == pytest.approx(3.0, abs=TOL)
    assert r.data["measurements"]["sharp_bends"] == 1 and r.data["measurements"]["joints"] == 4


def test_a_sharp_l_is_still_a_sharp_bend(models):
    r = dfm_run(models["sharp_l"], process="sheet_metal")
    (bend,) = rules(r, "sheet.min_bend_radius")
    assert bend["severity"] == "warn" and bend["check"] == "heuristic" and r.data["measurements"]["joints"] == 0


# --------------------------------------------------------------------------- 6. severity follows the convention


def test_heuristic_findings_are_warnings_except_the_two_documented_sheet_rules(models, ocp):
    results = [
        dfm_run(models["heatsink"], limits=json.dumps({"min_wall": "2.5 mm", "tool_radius": "0.2 mm"})),
        dfm_run(models["hollow"]),
        dfm_run(models["sharp_l"], process="sheet_metal"),
        dfm_run(models["stepped"], process="sheet_metal"),
        dfm_run(models["sharp"], process="sheet_metal", limits=json.dumps({"max_thickness": "1 mm"})),
        dfm_run(build(bracket_spec(2.0, arcs="via"), "L_sev"), process="sheet_metal"),
    ]
    heuristic = [f for r in results for f in r.data["findings"] if f["check"] == "heuristic"]
    assert {f["rule"] for f in heuristic} >= {"cnc.min_wall", "cnc.tool_access", "sheet.min_bend_radius", "sheet.joint", "sheet.thickness_uniform", "sheet.thickness"}
    for f in heuristic:
        expected = {"error"} if f["rule"] in ("sheet.thickness", "sheet.thickness_uniform") else {"warn", "info"}
        assert f["severity"] in expected, f
    assert "sheet.thickness_uniform" in results[0].data["conventions"]["severity"]


# --------------------------------------------------------------------------- 7. tool_radius is a radius


def test_a_tool_wider_than_the_narrowest_slot_warns_that_tool_radius_is_a_radius(models):
    wide = dfm_run(models["slot"], limits=json.dumps({"tool_radius": "3 mm"}))  # a 6 mm tool for a 3 mm slot
    assert wide.data["measurements"]["narrowest_slot_mm"] == pytest.approx(3.0, abs=TOL)
    (warning,) = [w for w in wide.warnings if "RADIUS" in w]
    assert "6 mm diameter" in warning and "tool_radius '1.5 mm'" in warning
    fits = dfm_run(models["slot"], limits=json.dumps({"tool_radius": "1.5 mm"}))  # exactly as wide as the slot
    assert not any("RADIUS" in w for w in fits.warnings)


def test_the_tool_radius_parameter_says_radius_not_diameter():
    meaning = dfm.LIMIT_DEFAULTS["cnc_milling"]["tool_radius"][2]
    assert "RADIUS (not the diameter)" in meaning and "6 mm end mill is '3 mm'" in meaning
    from eng_toolkit.domains.geometry.tools import CHECK_DFM_DESCRIPTION

    assert "3 mm end mill is '1.5 mm'" in CHECK_DFM_DESCRIPTION


# --------------------------------------------------------------------------- the real KiCad file


@pytest.mark.skipif(not KICAD_HEATSINK.is_file(), reason="KiCad 10 with its 3D models is not installed")
def test_the_kicad_heatsink_shows_only_its_real_problems(ocp):
    r = dfm_run(KICAD_HEATSINK, limits=SHOP)
    by_rule = r.data["summary"]["by_rule"]
    assert by_rule == {"cnc.hole_depth_ratio": {"error": 3}, "cnc.internal_corner_radius": {"error": 22}, "cnc.min_wall": {"error": 3}}
    assert all(c["measured"]["value"] == pytest.approx(0.25, abs=TOL) for c in rules(r, "cnc.internal_corner_radius"))
    assert all(h["measured"]["value"] == pytest.approx(35 / 3.2, abs=TOL) for h in rules(r, "cnc.hole_depth_ratio"))
    assert [w["measured"]["value"] for w in rules(r, "cnc.min_wall")] == pytest.approx([0.94] * 3, abs=1e-6)
    m = r.data["measurements"]
    assert m["near_tangent_edges_skipped"] == 22 and m["sharp_internal_corners"] == 0
    assert m["smallest_wall_mm"] == pytest.approx(0.94, abs=1e-6)
    q = tool(measure, model_path=str(KICAD_HEATSINK), queries="thickness P(46.5 mm, 15 mm, 17.5 mm) +x; thickness P(0 mm, -10 mm, 17.5 mm) +y")
    fin, ligament = q.data["queries"]
    assert fin["start"] == "inside" and fin["entry_mm"][0] == pytest.approx(46.432, abs=1e-3) and fin["exit_mm"][0] == pytest.approx(49.568, abs=1e-3)
    assert ligament["start"] == "outside" and ligament["value"] == pytest.approx(0.94, abs=1e-6)
