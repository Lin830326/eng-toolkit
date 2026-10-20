"""Round 2 of geometry_make_part checked against hand calculations.  Every expected number is derived in a comment.

Engine tests skip without OCP.  The refusals go through the gateway client, as a real MCP caller would meet them, and
their serialised results are checked for NaN and Infinity.
"""

import hashlib
import json
import math
from pathlib import Path

import pytest
from fastmcp import Client
from part_fixtures import box, cylinder, hole, spec

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.geometry.tools import make_part, measure
from eng_toolkit.gateway.server import gateway

pi = math.pi


@pytest.fixture(scope="module")
def ocp():
    return pytest.importorskip("OCP", reason="OCP is not installed; run: uv sync --extra cad")


def run(features, **kwargs) -> ToolResult:
    fn = getattr(make_part, "fn", make_part)
    result = fn(spec=features if isinstance(features, str) else spec(features), **kwargs)
    assert isinstance(result, ToolResult)
    return result


def volume(features, **kwargs) -> float:
    r = run(features, **kwargs)
    assert r.ok, r.error
    return r.values["volume"].quantity


def ok(features, **kwargs) -> ToolResult:
    r = run(features, **kwargs)
    assert r.ok, r.error
    return r


def artifact(result: ToolResult, filename: str) -> Path:
    (found,) = [a for a in result.artifacts if Path(a.path).name == filename]
    return Path(found.path)


def size(r: ToolResult) -> list[float]:
    return [r.values[k].quantity for k in ("size_x", "size_y", "size_z")]


def named(feature: dict, name: str) -> dict:
    return {**feature, "name": name}


def pattern(of, count, **extra):
    return {"op": "pattern", "of": of, "count": count, **extra}


def entries(r: ToolResult) -> list[tuple[float, float, float]]:
    return [tuple(h["entry_mm"]) for h in r.data["holes"]]


# --------------------------------------------------------------------------- (1) pattern


PLATE = box(("100 mm", "60 mm", "8 mm"))  # 100 * 60 * 8 = 48000 mm^3
HOLE6 = named(hole([("10 mm", "10 mm", "8 mm")], "6 mm"), "h")  # one through hole d6 in the 8 mm plate: pi * 3^2 * 8 = 226.19467 mm^3


def test_a_linear_pattern_of_four_holes_removes_exactly_four_times_one_hole(ocp):
    one = volume([PLATE, HOLE6])
    four = ok([PLATE, HOLE6, pattern("h", 4, step=["20 mm", "0 mm", "0 mm"])])
    assert 48000 - one == pytest.approx(pi * 9 * 8, abs=1e-6)  # 226.19467
    # four holes: 48000 - 4 * pi * 3^2 * 8 = 48000 - 904.77868 = 47095.22132
    assert four.values["volume"].quantity == pytest.approx(48000 - 4 * pi * 9 * 8, abs=1e-6)
    assert 48000 - four.values["volume"].quantity == pytest.approx(4 * (48000 - one), abs=1e-6)
    assert four.data["holes_total"] == 4
    assert entries(four) == [(10.0, 10.0, 8.0), (30.0, 10.0, 8.0), (50.0, 10.0, 8.0), (70.0, 10.0, 8.0)]  # 10 + k * 20 for k = 0..3
    assert {h["diameter_mm"] for h in four.data["holes"]} == {6.0} and all(h["thru"] for h in four.data["holes"])


def test_the_pattern_holes_are_in_the_features_file_and_in_the_step(ocp):
    r = ok([PLATE, HOLE6, pattern("h", 4, step=["20 mm", "0 mm", "0 mm"], name="row")])
    document = json.loads(artifact(r, "part.features.json").read_text(encoding="utf8"))
    assert len(document["holes"]) == 4
    assert [h["feature"] for h in document["holes"]] == ["h", "row", "row", "row"]
    fn = getattr(measure, "fn", measure)
    m = fn(model_path=str(artifact(r, "part.step")))
    assert m.ok and m.data["holes_total"] == 4  # the kernel finds the four holes in the written STEP
    assert sorted(h["entry_mm"][0] for h in m.data["holes"]) == pytest.approx([10, 30, 50, 70])


def test_a_pattern_of_a_hole_with_several_entries_repeats_all_of_them(ocp):
    two = named(hole([("10 mm", "10 mm", "8 mm"), ("10 mm", "30 mm", "8 mm")], "6 mm"), "h")
    r = ok([PLATE, two, pattern("h", 3, step=["30 mm", "0 mm", "0 mm"])])
    assert r.data["holes_total"] == 6  # 2 entries * 3 instances
    assert r.values["volume"].quantity == pytest.approx(48000 - 6 * pi * 9 * 8, abs=1e-6)


def test_a_polar_pattern_of_six_holes_sits_on_the_circle(ocp):
    plate = box(("100 mm", "100 mm", "10 mm"))  # 100000 mm^3
    seed = named(hole([("80 mm", "50 mm", "10 mm")], "6 mm"), "h")  # 30 mm from the centre (50, 50)
    polar = pattern("h", 6, angle="360 deg", center=["50 mm", "50 mm", "0 mm"])
    r = ok([plate, seed, polar])
    assert r.values["volume"].quantity == pytest.approx(100000 - 6 * pi * 9 * 10, abs=1e-6)  # 100000 - 1696.46003
    assert r.data["holes_total"] == 6
    for k, (x, y, z) in enumerate(entries(r)):
        theta = 2 * pi * k / 6  # a full turn is divided evenly: 60 degrees apart
        assert (x, y, z) == pytest.approx((50 + 30 * math.cos(theta), 50 + 30 * math.sin(theta), 10.0), abs=1e-8)
        assert math.dist((x, y), (50, 50)) == pytest.approx(30.0, abs=1e-8)


def test_a_polar_pattern_over_less_than_a_turn_runs_from_the_first_to_the_last(ocp):
    plate = box(("100 mm", "100 mm", "10 mm"))
    seed = named(hole([("80 mm", "50 mm", "10 mm")], "6 mm"), "h")
    r = ok([plate, seed, pattern("h", 4, angle="90 deg", center=["50 mm", "50 mm", "0 mm"])])
    # 4 instances over 90 degrees: 0, 30, 60 and 90 degrees about (50, 50) at radius 30
    expected = [(80.0, 50.0), (50 + 30 * math.cos(pi / 6), 50 + 30 * math.sin(pi / 6)), (50 + 30 * math.cos(pi / 3), 50 + 30 * math.sin(pi / 3)), (50.0, 80.0)]
    assert [c for e in entries(r) for c in e[:2]] == pytest.approx([c for e in expected for c in e], abs=1e-8)
    assert r.values["volume"].quantity == pytest.approx(100000 - 4 * pi * 9 * 10, abs=1e-6)


def test_a_polar_pattern_about_another_axis_turns_about_that_axis(ocp):
    # a hole along y entering the +y face of a 40 cube, turned 4 times about the y axis through (20, *, 20)
    cube = box(("40 mm", "40 mm", "40 mm"))
    seed = named(hole([("30 mm", "40 mm", "20 mm")], "4 mm", axis="-y"), "h")
    r = ok([cube, seed, pattern("h", 4, angle="360 deg", center=["20 mm", "0 mm", "20 mm"], axis="+y")])
    # a hole d4 through y: pi * 2^2 * 40; the four holes 10 mm from the axis are separate: 40^3 - 4 * pi * 4 * 40
    assert r.values["volume"].quantity == pytest.approx(64000 - 4 * pi * 4 * 40, abs=1e-6)
    xz = sorted((round(e[0], 6), round(e[2], 6)) for e in entries(r))
    assert xz == [(10.0, 20.0), (20.0, 10.0), (20.0, 30.0), (30.0, 20.0)]


def test_a_pattern_of_a_boss_adds_the_boss_each_time(ocp):
    plate = box(("100 mm", "30 mm", "5 mm"))  # 15000
    boss = named(cylinder("10 mm", "10 mm", at=("10 mm", "15 mm", "5 mm")), "boss")  # pi * 5^2 * 10 = 785.39816
    r = ok([plate, boss, pattern("boss", 3, step=["40 mm", "0 mm", "0 mm"])])
    assert r.values["volume"].quantity == pytest.approx(15000 + 3 * pi * 25 * 10, abs=1e-6)  # 15000 + 2356.19449
    assert r.data["solids"] == 1 and size(r) == pytest.approx([100, 30, 15])


def test_a_pattern_of_a_cut_removes_the_pocket_each_time(ocp):
    base = box(("100 mm", "30 mm", "10 mm"))  # 30000
    pocket = named(box(("10 mm", "10 mm", "4 mm"), at=("10 mm", "10 mm", "6 mm"), mode="cut"), "pocket")  # 10 * 10 * 4 = 400
    r = ok([base, pocket, pattern("pocket", 3, step=["30 mm", "0 mm", "0 mm"])])
    assert r.values["volume"].quantity == pytest.approx(30000 - 3 * 400, abs=1e-6)


def test_a_copy_that_falls_off_the_part_changes_nothing_warns_and_declares_no_hole(ocp):
    r = ok([PLATE, HOLE6, pattern("h", 4, step=["40 mm", "0 mm", "0 mm"])])  # holes at x = 10, 50, 90 and 130: the last is past the plate
    assert r.values["volume"].quantity == pytest.approx(48000 - 3 * pi * 9 * 8, abs=1e-6)
    assert r.data["holes_total"] == 3
    assert any("copy 3" in w and "changed nothing" in w and "no hole is declared" in w for w in r.warnings), r.warnings


def test_the_same_pattern_spec_gives_the_same_step_bytes_twice(ocp):
    features = [PLATE, HOLE6, pattern("h", 5, step=["15 mm", "5 mm", "0 mm"]), {"op": "mirror", "plane": "yz", "offset": "50 mm", "of": "h"}]
    first, second = ok(features), ok(features)
    assert first.data["step_sha256"] == second.data["step_sha256"]
    assert first.data["spec_sha256"] == second.data["spec_sha256"]


# --------------------------------------------------------------------------- (2) mirror


def test_the_mirror_of_a_half_part_doubles_the_volume(ocp):
    half = box(("20 mm", "30 mm", "10 mm"))  # 6000
    r = ok([half, {"op": "mirror", "plane": "yz"}])  # reflected in x = 0: occupies x -20 .. 0 and touches the original
    assert r.values["volume"].quantity == pytest.approx(2 * 6000, abs=1e-6)
    assert r.data["solids"] == 1 and size(r) == pytest.approx([40, 30, 10])
    assert r.data["bbox"]["min_mm"] == pytest.approx([-20, 0, 0])


def test_a_mirror_plane_can_be_offset_and_the_holes_are_reflected_with_the_part(ocp):
    half = box(("20 mm", "30 mm", "10 mm"))  # 6000
    drilled = named(hole([("5 mm", "15 mm", "10 mm")], "4 mm"), "h")  # pi * 2^2 * 10 = 125.66371
    r = ok([half, drilled, {"op": "mirror", "plane": "yz", "offset": "20 mm"}])  # the reflection in x = 20 occupies x 20 .. 40
    assert r.values["volume"].quantity == pytest.approx(2 * (6000 - pi * 4 * 10), abs=1e-6)  # 12000 - 251.32741
    assert r.data["bbox"]["max_mm"] == pytest.approx([40, 30, 10])
    assert entries(r) == [(5.0, 15.0, 10.0), (35.0, 15.0, 10.0)]  # 2 * 20 - 5 = 35
    assert r.data["holes_total"] == 2


def test_mirroring_twice_declares_four_holes_without_duplicates(ocp):
    quarter = box(("20 mm", "20 mm", "10 mm"))
    drilled = named(hole([("5 mm", "5 mm", "10 mm")], "4 mm"), "h")
    r = ok([quarter, drilled, {"op": "mirror", "plane": "yz"}, {"op": "mirror", "plane": "xz"}])
    assert r.data["holes_total"] == 4 and r.data["solids"] == 1
    assert sorted(e[:2] for e in entries(r)) == [(-5.0, -5.0), (-5.0, 5.0), (5.0, -5.0), (5.0, 5.0)]
    assert r.values["volume"].quantity == pytest.approx(4 * (4000 - pi * 4 * 10), abs=1e-6)  # 4 * (20*20*10 - 125.66371)


def test_mirroring_one_hole_of_a_plate(ocp):
    plate = box(("100 mm", "40 mm", "10 mm"))  # 40000
    seed = named(hole([("10 mm", "20 mm", "10 mm")], "6 mm"), "h")
    r = ok([plate, seed, {"op": "mirror", "plane": "yz", "offset": "50 mm", "of": "h"}])
    assert r.values["volume"].quantity == pytest.approx(40000 - 2 * pi * 9 * 10, abs=1e-6)  # the second hole is at 2 * 50 - 10 = 90
    assert entries(r) == [(10.0, 20.0, 10.0), (90.0, 20.0, 10.0)]


def test_mirroring_a_body_feature_reflects_its_tool(ocp):
    plate = box(("100 mm", "40 mm", "5 mm"))  # 20000
    boss = named(box(("10 mm", "10 mm", "10 mm"), at=("5 mm", "5 mm", "5 mm")), "boss")  # 1000, on top of the plate
    r = ok([plate, boss, {"op": "mirror", "plane": "yz", "offset": "50 mm", "of": "boss"}])
    assert r.values["volume"].quantity == pytest.approx(20000 + 2 * 1000, abs=1e-6)
    assert r.data["bbox"]["max_mm"] == pytest.approx([100, 40, 15])


def test_a_mirror_of_an_already_symmetric_part_warns(ocp):
    r = ok([box(("20 mm", "20 mm", "20 mm"), at=("-10 mm", "-10 mm", "0 mm")), {"op": "mirror", "plane": "yz"}])
    assert r.values["volume"].quantity == pytest.approx(8000)
    assert any("added no material" in w for w in r.warnings)


# --------------------------------------------------------------------------- (3) shell


def test_a_shell_of_a_box_with_the_top_removed(ocp):
    r = ok([box(("40 mm", "30 mm", "20 mm")), {"op": "shell", "faces": ">z", "thickness": "2 mm"}])
    # outer 40 * 30 * 20 = 24000; the cavity is 36 * 26 * 18 (2 mm walls, 2 mm floor, open top) = 16848
    assert r.values["volume"].quantity == pytest.approx(40 * 30 * 20 - 36 * 26 * 18, abs=1e-6)  # 7152
    assert r.values["volume"].quantity == pytest.approx(7152.0, abs=1e-6)
    assert size(r) == pytest.approx([40, 30, 20])
    assert r.data["features"][1]["edges_used"] == 1  # one face removed


def test_a_shell_of_a_cylinder_with_the_bottom_removed(ocp):
    r = ok([cylinder("40 mm", "30 mm"), {"op": "shell", "faces": "<z & plane", "thickness": "2 mm"}])
    # outer pi * 20^2 * 30 = 12000 pi; cavity radius 18, height 28 (2 mm top, open bottom): pi * 18^2 * 28 = 9072 pi
    assert r.values["volume"].quantity == pytest.approx(pi * (12000 - 9072), abs=1e-5)  # 2928 pi = 9198.4
    assert r.data["faces"] == 5  # outer wall, outer top, inner wall, inner top and the ring left where the bottom was
    assert size(r) == pytest.approx([40, 40, 30])


def test_a_shell_can_remove_the_faces_a_feature_created(ocp):
    body = [named(box(("40 mm", "30 mm", "20 mm")), "body"), named(cylinder("10 mm", "10 mm", at=("20 mm", "15 mm", "20 mm")), "boss")]
    r = ok([*body, {"op": "shell", "faces": "of:boss & >z", "thickness": "1 mm"}])
    # outer 40 * 30 * 20 + pi * 5^2 * 10; the cavity is the inner box 38 * 28 * 18 plus the inner boss, radius 4, from z = 19 to the open top at z = 30
    expected = 40 * 30 * 20 + pi * 25 * 10 - (38 * 28 * 18 + pi * 16 * 11)
    assert r.values["volume"].quantity == pytest.approx(expected, abs=1e-5)  # 5080.48


def test_a_tube_open_at_both_ends_matches_the_annulus_volume(ocp):
    r = ok([cylinder("30 mm", "20 mm"), {"op": "shell", "faces": ">z or <z", "thickness": "2 mm"}])
    # pi * (15^2 - (15 - 2)^2) * 20 = pi * 56 * 20 = 1120 pi.
    assert r.values["volume"].quantity == pytest.approx(1120 * pi, abs=1e-6)
    assert r.data["features"][1]["edges_used"] == 2
    step = artifact(r, "part.step")
    measured = getattr(measure, "fn", measure)(model_path=str(step))
    assert measured.ok and measured.data["solids"] == 1
    assert measured.values["volume"].quantity == pytest.approx(1120 * pi, abs=1e-6)


@pytest.mark.parametrize(
    ("faces", "thickness", "expected", "removed"),
    [
        # 40*30*20 - (40-4)*(30-4)*20 = 24000 - 18720 = 5280.
        (">z or <z", "2 mm", 5280, 2),
        # The x ends are open: 24000 - 40*(30-4)*(20-4) = 24000 - 16640 = 7360.
        (">x or <x", "2 mm", 7360, 2),
        # x ends and top open: 24000 - 40*(30-4)*(20-2) = 24000 - 18720 = 5280.
        (">x or <x or >z", "2 mm", 5280, 3),
        # x/y rooms 20/15, z room 20: t=14 fits; 24000 - (40-28)*(30-28)*20 = 23520.
        (">z or <z", "14 mm", 23520, 2),
        # x/y/z rooms 40/15/10: t=9 fits; 24000 - 40*(30-18)*(20-18) = 23040.
        (">x or <x", "9 mm", 23040, 2),
        # Each face counts once, and a valid empty group does not empty the union.
        (">z or >z & plane or >z & <z", "2 mm", 7152, 1),  # 24000 - 36*26*18 = 7152.
        ("cylinder or >z or <z", "2 mm", 5280, 2),  # no cylinder faces on a box.
    ],
)
def test_shell_union_volumes_and_thickness_rooms(ocp, faces, thickness, expected, removed):
    r = ok([box(("40 mm", "30 mm", "20 mm")), {"op": "shell", "faces": faces, "thickness": thickness}])
    assert r.values["volume"].quantity == pytest.approx(expected, abs=1e-6)
    assert r.data["features"][1]["edges_used"] == removed
    assert r.data["solids"] == 1 and r.review_status == "unreviewed"


def test_a_shell_before_holes_warns_that_they_are_offset_with_the_part(ocp):
    r = ok([box(("40 mm", "30 mm", "20 mm")), named(hole([("10 mm", "10 mm", "20 mm")], "4 mm"), "h"), {"op": "shell", "faces": ">z", "thickness": "2 mm"}])
    assert any("shell" in w and "holes declared before" in w for w in r.warnings), r.warnings


# --------------------------------------------------------------------------- (4) path profiles


def stadium_path(segments=None, **extra):
    return {
        "path": {
            "start": ["-10 mm", "-10 mm"],
            "segments": segments
            or [
                {"to": ["10 mm", "-10 mm"]},
                {"to": ["10 mm", "10 mm"], "radius": "10 mm", "turn": "left"},
                {"to": ["-10 mm", "10 mm"]},
                {"to": ["-10 mm", "-10 mm"], "radius": "10 mm", "turn": "left"},
            ],
        }
    }


def test_an_extruded_stadium_has_the_closed_form_volume(ocp):
    r = ok([{"op": "extrude", "profile": stadium_path(), "distance": "5 mm"}])
    # two straight sides of 20 mm and two half circles of radius 10: area 20 * 20 + pi * 10^2 = 714.15927; times 5
    assert r.values["volume"].quantity == pytest.approx((400 + pi * 100) * 5, abs=1e-6)  # 3570.79633
    assert size(r) == pytest.approx([40, 20, 5])


def test_the_stadium_with_via_points_is_the_same_part(ocp):
    via = [
        {"to": ["10 mm", "-10 mm"]},
        {"to": ["10 mm", "10 mm"], "via": ["20 mm", "0 mm"]},
        {"to": ["-10 mm", "10 mm"]},
        {"to": ["-10 mm", "-10 mm"], "via": ["-20 mm", "0 mm"]},
    ]
    a = volume([{"op": "extrude", "profile": stadium_path(), "distance": "5 mm"}])
    b = volume([{"op": "extrude", "profile": stadium_path(via), "distance": "5 mm"}])
    assert b == pytest.approx((400 + pi * 100) * 5, abs=1e-6) and b == pytest.approx(a, abs=1e-6)


def test_a_d_shaped_profile_is_a_half_disc(ocp):
    # a vertical chord from (0, -10) to (0, 10) closed by a half circle of radius 10 through (10, 0): area pi * 10^2 / 2 = 157.0796
    d_shape = {"path": {"start": ["0 mm", "-10 mm"], "segments": [{"to": ["0 mm", "10 mm"], "radius": "10 mm", "turn": "right"}, {"to": ["0 mm", "-10 mm"]}]}}
    r = ok([{"op": "extrude", "profile": d_shape, "distance": "4 mm"}])
    assert r.values["volume"].quantity == pytest.approx(pi * 100 / 2 * 4, abs=1e-6)  # 628.31853
    assert r.data["bbox"]["min_mm"][0] == pytest.approx(-10.0) and r.data["bbox"]["max_mm"][0] == pytest.approx(0.0)  # turning right bulges to -x


def test_the_turn_decides_which_side_the_arc_bulges(ocp):
    chord = lambda turn: {"path": {"start": ["0 mm", "-10 mm"], "segments": [{"to": ["0 mm", "10 mm"], "radius": "10 mm", "turn": turn}, {"to": ["0 mm", "-10 mm"]}]}}
    left = ok([{"op": "extrude", "profile": chord("left"), "distance": "1 mm"}])
    right = ok([{"op": "extrude", "profile": chord("right"), "distance": "1 mm"}])
    assert left.data["bbox"]["min_mm"][0] == pytest.approx(0.0) and left.data["bbox"]["max_mm"][0] == pytest.approx(10.0)  # counter-clockwise from the bottom: bulges to +x
    assert right.data["bbox"]["max_mm"][0] == pytest.approx(0.0) and right.data["bbox"]["min_mm"][0] == pytest.approx(-10.0)


def test_a_minor_arc_with_a_radius_is_a_circular_segment(ocp):
    # a circular segment of radius 10 cut off by a chord of 10 sqrt(2) (a quarter turn): area = r^2 / 2 * (theta - sin theta) with theta = pi / 2
    chord_end = 10 * math.sqrt(2)
    segment = {"path": {"start": ["0 mm", "0 mm"], "segments": [{"to": [f"{chord_end!r} mm", "0 mm"], "radius": "10 mm", "turn": "right"}, {"to": ["0 mm", "0 mm"]}]}}
    r = ok([{"op": "extrude", "profile": segment, "distance": "2 mm"}])
    assert r.values["volume"].quantity == pytest.approx(100 / 2 * (pi / 2 - 1) * 2, abs=1e-5)  # 57.0796


def test_a_revolved_ring_with_a_round_section_is_a_torus(ocp):
    # a circle of radius 3 made of two half-turn arcs, centre 10 mm from the v axis: V = 2 pi^2 R r^2 = 2 pi^2 * 10 * 9
    section = {"path": {"start": ["7 mm", "0 mm"], "segments": [{"to": ["13 mm", "0 mm"], "via": ["10 mm", "3 mm"]}, {"to": ["7 mm", "0 mm"], "via": ["10 mm", "-3 mm"]}]}}
    r = ok([{"op": "revolve", "profile": section}])
    assert r.values["volume"].quantity == pytest.approx(2 * pi**2 * 10 * 9, abs=1e-6)  # 1776.5287
    assert size(r) == pytest.approx([26, 6, 26], abs=1e-5)  # turned about the y axis: 26 across in x and z, 6 along y


def test_a_revolved_half_disc_follows_pappus(ocp):
    # a half disc of radius 5 whose flat side is the line x = 10: area 12.5 pi, centroid 4 r / (3 pi) beyond the chord
    # V = 2 pi (10 + 20 / (3 pi)) * 12.5 pi = 250 pi^2 + 500 pi / 3
    half_disc = {"path": {"start": ["10 mm", "-5 mm"], "segments": [{"to": ["10 mm", "5 mm"], "radius": "5 mm", "turn": "left"}, {"to": ["10 mm", "-5 mm"]}]}}
    r = ok([{"op": "revolve", "profile": half_disc}])
    assert r.values["volume"].quantity == pytest.approx(250 * pi**2 + 500 * pi / 3, abs=1e-5)  # 2991.0
    assert size(r) == pytest.approx([30, 10, 30], abs=1e-5)  # about y: radius 15 at the bulge, 10 along y


def test_a_path_with_straight_segments_only_is_a_polygon(ocp):
    tri = {"path": {"start": ["0 mm", "0 mm"], "segments": [{"to": ["30 mm", "0 mm"]}, {"to": ["0 mm", "40 mm"]}, {"to": ["0 mm", "0 mm"]}]}}
    assert volume([{"op": "extrude", "profile": tri, "distance": "2 mm"}]) == pytest.approx(30 * 40 / 2 * 2, abs=1e-9)  # 1200


def test_a_path_cut_makes_a_slot_with_round_ends(ocp):
    plate = box(("60 mm", "30 mm", "5 mm"), at=("-30 mm", "-15 mm", "0 mm"))  # 9000
    r = ok([plate, {"op": "extrude", "profile": stadium_path(), "distance": "5 mm", "mode": "cut"}])
    assert r.values["volume"].quantity == pytest.approx(9000 - (400 + pi * 100) * 5, abs=1e-6)


# --------------------------------------------------------------------------- (5) of: selectors


def test_a_fillet_of_one_features_edge_follows_pappus_and_matches_the_explicit_selector(ocp):
    plate = named(box(("60 mm", "40 mm", "10 mm")), "plate")  # 24000
    boss = named(cylinder("20 mm", "15 mm", at=("30 mm", "20 mm", "10 mm")), "boss")  # pi * 10^2 * 15 = 4712.38898
    plain = volume([plate, boss])
    by_name = ok([plate, boss, {"op": "fillet", "edges": "of:boss & circle & >z", "radius": "2 mm"}])
    explicit = ok([plate, boss, {"op": "fillet", "edges": ">z & circle", "radius": "2 mm"}])
    # R2 on the convex rim of a cylinder of radius 10: the removed ring has area 2^2 (1 - pi/4) and its centroid is at
    # radius 8 + (4 * 9 - pi * (8 + 8 / (3 pi))) / (4 - pi), so dV = 2 pi (36 - 8 pi - 8/3) = 2 pi (100/3 - 8 pi) = 51.5276
    removed = 2 * pi * (100 / 3 - 8 * pi)
    assert plain - by_name.values["volume"].quantity == pytest.approx(removed, abs=1e-5)
    assert by_name.values["volume"].quantity == pytest.approx(explicit.values["volume"].quantity, abs=1e-9)
    assert by_name.data["features"][2]["edges_used"] == 1


def test_an_of_term_selects_only_the_edges_of_that_feature(ocp):
    plate = named(box(("60 mm", "40 mm", "10 mm")), "plate")
    boss = named(cylinder("20 mm", "15 mm", at=("30 mm", "20 mm", "10 mm")), "boss")
    # the boss owns three edges (top circle, base circle, seam); the plate keeps its 12 minus nothing it lost
    assert ok([plate, boss, {"op": "chamfer", "edges": "of:boss & circle", "distance": "1 mm"}]).data["features"][2]["edges_used"] == 2
    assert ok([plate, boss, {"op": "chamfer", "edges": "of:plate & |z", "distance": "1 mm"}]).data["features"][2]["edges_used"] == 4  # the plate's four vertical edges


def test_an_edge_union_is_the_explicit_union_of_the_single_group_sets(ocp):
    from eng_toolkit import brep
    from eng_toolkit.brep import _model

    plate = named(box(("40 mm", "40 mm", "10 mm")), "plate")
    boss = named(cylinder("20 mm", "15 mm", at=("20 mm", "20 mm", "10 mm")), "boss")
    # The boss circles are at z=25 and z=10. <z is the plate bottom at z=0, so
    # of:boss & circle & <z selects nothing. concave picks the base circle at z=10.
    groups = ["of:boss & circle & >z", "of:boss & circle & concave"]
    selection = " or ".join(groups)
    before = brep.make_box((40, 40, 10), (0, 0, 0))
    tool = brep.make_cylinder(20, 15, (20, 20, 10), (0, 0, 1))
    solid = brep.combine(before, tool, "add")
    created = {"boss": brep.created_by(before, solid, tool)}
    singles = [_model._select(solid._handle, brep.parse_edge_selector(group), created)[0] for group in groups]
    union = _model._select(solid._handle, brep.parse_edge_selector(selection), created)[0]
    assert [len(edges) for edges in singles] == [1, 1] and len(union) == 2
    assert all(any(edge.IsSame(other) for other in union) for edges in singles for edge in edges)
    assert all(any(edge.IsSame(other) for edges in singles for other in edges) for edge in union)
    assert brep.select_edges(solid, brep.parse_edge_selector("of:boss & circle & <z"), created).count == 0
    r = ok([plate, boss, {"op": "fillet", "edges": selection, "radius": "2 mm"}])
    # Pappus: top removes 2*pi*(100/3 - 8*pi). The base fills a square minus a quarter disc,
    # area 4-pi, radial moment 4*11 - pi*(12 - 8/(3*pi)) = 44 - 12*pi + 8/3.
    # Its added volume is 2*pi times that moment.
    # Base minus top = 2*pi*(40/3 - 4*pi); outer volume = 16000 + 1500*pi.
    assert r.values["volume"].quantity == pytest.approx(16000 + 4580 * pi / 3 - 8 * pi**2, abs=1e-5)
    assert r.data["features"][2]["edges_used"] == 2


def test_eight_box_edges_selected_by_or_have_the_hand_derived_fillet_volume(ocp):
    r = ok([box(("40 mm", "40 mm", "10 mm")), {"op": "fillet", "edges": "|z & convex or |x & convex", "radius": "2 mm"}])
    # Four x edges (40 mm) plus four z edges (10 mm): strip loss = 4*(40+10)*r^2*(1-pi/4).
    # At each of 8 corners two strips overlap. At depth y in [0,r], inset d=r-sqrt(r^2-(r-y)^2).
    # Integral d^2 dy = r^3*(5/3-pi/2), so add back 8*r^3*(5/3-pi/2).
    # r=2: 16000 - 800*(1-pi/4) + 64*(5/3-pi/2) = 45920/3 + 168*pi.
    assert r.values["volume"].quantity == pytest.approx(45920 / 3 + 168 * pi, abs=1e-5)
    assert r.data["features"][1]["edges_used"] == 8


@pytest.mark.parametrize("edges", ["|z & convex or |z", "|z & convex or circle", "circle or |z & convex"])
def test_chamfer_union_deduplicates_and_accepts_empty_groups(ocp, edges):
    r = ok([box(("40 mm", "40 mm", "10 mm")), {"op": "chamfer", "edges": edges, "distance": "2 mm"}])
    # Four disjoint vertical triangular prisms: 16000 - 4*(2*2/2)*10 = 15920.
    assert r.values["volume"].quantity == pytest.approx(15920, abs=1e-6)
    assert r.data["features"][1]["edges_used"] == 4


def test_the_hole_rims_of_one_hole_only_are_rounded(ocp):
    plate = named(box(("100 mm", "40 mm", "10 mm")), "plate")  # 40000
    h1 = named(hole([("25 mm", "20 mm", "10 mm")], "10 mm"), "h1")  # pi * 5^2 * 10 = 785.39816
    h2 = named(hole([("75 mm", "20 mm", "10 mm")], "10 mm"), "h2")
    bare = volume([plate, h1, h2])
    one = ok([plate, h1, h2, {"op": "fillet", "edges": "of:h1 & circle & convex", "radius": "1 mm"}])
    both = ok([plate, h1, h2, {"op": "fillet", "edges": "circle & convex", "radius": "1 mm"}])
    # R1 on the rim of a hole of radius 5 (material outside): area 1 - pi/4, centroid factor 5.5 - (pi/4)(6 - 4/(3 pi)),
    # dV = 2 pi (5.5 - 1.5 pi + 1/3) = 7.04312 per rim; a hole has two rims
    rim = 2 * pi * (5.5 - 1.5 * pi + 1 / 3)
    assert one.data["features"][3]["edges_used"] == 2 and both.data["features"][3]["edges_used"] == 4
    assert bare - one.values["volume"].quantity == pytest.approx(2 * rim, abs=1e-5)  # 14.0862
    assert bare - both.values["volume"].quantity == pytest.approx(4 * rim, abs=1e-5)


def test_the_name_of_a_pattern_selects_its_copies_and_the_name_of_the_source_selects_all(ocp):
    plate = named(box(("100 mm", "40 mm", "10 mm")), "plate")
    seed = named(hole([("15 mm", "20 mm", "10 mm")], "6 mm"), "h")
    row = {**pattern("h", 3, step=["30 mm", "0 mm", "0 mm"]), "name": "row"}
    on_all = ok([plate, seed, row, {"op": "chamfer", "edges": "of:h & circle & convex", "distance": "0.5 mm"}])
    on_copies = ok([plate, seed, row, {"op": "chamfer", "edges": "of:row & circle & convex", "distance": "0.5 mm"}])
    assert on_all.data["features"][3]["edges_used"] == 6  # three holes, two rims each
    assert on_copies.data["features"][3]["edges_used"] == 4  # the two copies


def test_an_of_term_that_matches_nothing_in_the_part_now_is_an_empty_selection(ocp):
    plate = named(box(("60 mm", "40 mm", "10 mm")), "plate")
    boss = named(cylinder("20 mm", "15 mm", at=("30 mm", "20 mm", "10 mm")), "boss")
    r = run([plate, boss, {"op": "fillet", "edges": "of:boss & |x", "radius": "1 mm"}])
    assert r.ok is False and r.error.family == "geometry.empty_selection"
    assert r.error.details["pointer"] == "/features/2"


# --------------------------------------------------------------------------- (6) tapered extrude


def frustum(a, h, t):
    b = a - 2 * h * math.tan(t)
    return h / 3 * (a * a + a * b + b * b)


def test_a_tapered_square_is_a_frustum(ocp):
    rect = {"rect": {"size": ["20 mm", "20 mm"]}}
    r = ok([{"op": "extrude", "profile": rect, "distance": "10 mm", "taper": "10 deg"}])
    # A1 = 20^2, A2 = (20 - 2 * 10 * tan 10 deg)^2 = 16.4706^2 and V = h / 3 (A1 + A2 + sqrt(A1 A2)) = 3336.147
    a2 = (20 - 20 * math.tan(math.radians(10))) ** 2
    assert r.values["volume"].quantity == pytest.approx(10 / 3 * (400 + a2 + math.sqrt(400 * a2)), abs=1e-6)
    assert r.values["volume"].quantity == pytest.approx(frustum(20, 10, math.radians(10)), abs=1e-9)
    assert size(r) == pytest.approx([20, 20, 10])


def test_a_negative_distance_tapers_downwards_and_widening_is_not_offered(ocp):
    rect = {"rect": {"size": ["20 mm", "20 mm"]}}
    down = ok([{"op": "extrude", "profile": rect, "distance": "-10 mm", "taper": "10 deg"}])
    assert down.values["volume"].quantity == pytest.approx(frustum(20, 10, math.radians(10)), abs=1e-6)
    assert down.data["bbox"]["min_mm"][2] == pytest.approx(-10.0) and down.data["bbox"]["max_mm"][2] == pytest.approx(0.0)
    assert size(down) == pytest.approx([20, 20, 10])
    # the kernel rounds the corners when it widens (4737.87 against the frustum 4746.76), so a negative taper is refused instead
    r = run([{"op": "extrude", "profile": rect, "distance": "10 mm", "taper": "-10 deg"}])
    assert r.ok is False and r.error.details["pointer"] == "/features/0/taper"


def test_a_tapered_circle_is_a_cone_frustum(ocp):
    r = ok([{"op": "extrude", "profile": {"circle": {"diameter": "20 mm"}}, "distance": "10 mm", "taper": "10 deg"}])
    r2 = 10 - 10 * math.tan(math.radians(10))
    assert r.values["volume"].quantity == pytest.approx(pi * 10 / 3 * (100 + 10 * r2 + r2 * r2), abs=1e-5)


def test_a_clockwise_polygon_tapers_the_same_way(ocp):
    ccw = {"polygon": {"points": [["0 mm", "0 mm"], ["20 mm", "0 mm"], ["20 mm", "20 mm"], ["0 mm", "20 mm"]]}}
    cw = {"polygon": {"points": [["0 mm", "0 mm"], ["0 mm", "20 mm"], ["20 mm", "20 mm"], ["20 mm", "0 mm"]]}}
    a = ok([{"op": "extrude", "profile": ccw, "distance": "10 mm", "taper": "10 deg"}])
    b = ok([{"op": "extrude", "profile": cw, "distance": "10 mm", "taper": "10 deg"}])
    assert a.values["volume"].quantity == pytest.approx(b.values["volume"].quantity, abs=1e-9)
    assert a.data["bbox"] == b.data["bbox"]


def test_a_taper_on_another_plane_leans_along_that_normal(ocp):
    rect = {"rect": {"size": ["20 mm", "20 mm"]}}
    r = ok([{"op": "extrude", "profile": rect, "distance": "10 mm", "taper": "10 deg", "plane": "xz"}])  # normal +y
    assert r.values["volume"].quantity == pytest.approx(frustum(20, 10, math.radians(10)), abs=1e-6)
    assert size(r) == pytest.approx([20, 10, 20])


def test_a_tapered_stadium_boss_follows_the_taper_of_every_wall(ocp):
    r = ok([{"op": "extrude", "profile": stadium_path(), "distance": "5 mm", "taper": "5 deg"}])
    # the stadium offset inward by d = 5 tan 5 deg stays a stadium with half width 10 - d: area A(d) = 4 a'... integrate
    # A(s) = (20 + 2 pi... closed form: a stadium of straight length 20 and radius R has area 40 R + pi R^2 with R = 10 - s tan t
    t = math.tan(math.radians(5))
    area = lambda s: 40 * (10 - s * t) + pi * (10 - s * t) ** 2
    # integrate the quadratic exactly with Simpson's rule
    expected = 5 / 6 * (area(0) + 4 * area(2.5) + area(5))
    assert r.values["volume"].quantity == pytest.approx(expected, rel=1e-6)


# --------------------------------------------------------------------------- (7) import


def write_block(path: Path, x=30.0, y=20.0, z=10.0) -> Path:
    import cad_fixtures as cad

    return cad.write_step(cad.box(0, 0, 0, x, y, z), path)


def write_step_inches(shape, path: Path) -> Path:
    """A STEP file in inches (the numbers in the file are inches).  The writer must exist before the unit is set, and the name is INCH."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    writer = STEPControl_Writer()
    assert Interface_Static.SetCVal_s("write.step.unit", "INCH")
    try:
        writer.Transfer(shape, STEPControl_AsIs)
        assert writer.Write(str(path)) == IFSelect_RetDone
    finally:
        Interface_Static.SetCVal_s("write.step.unit", "MM")
    return path


def imported(path, **extra):
    return {"op": "import", "file": str(path), **extra}


def test_an_imported_block_has_the_volume_of_the_block_defined_directly(ocp, tmp_path):
    step = write_block(tmp_path / "block.step")
    direct = ok([box(("30 mm", "20 mm", "10 mm"))])
    r = ok([imported(step)])
    assert r.values["volume"].quantity == pytest.approx(30 * 20 * 10, abs=1e-6)  # 6000
    assert r.values["volume"].quantity == pytest.approx(direct.values["volume"].quantity, abs=1e-9)
    assert size(r) == pytest.approx([30, 20, 10])
    assert r.data["imports"][0]["file"] == str(step)
    assert r.data["imports"][0]["sha256"] == hashlib.sha256(step.read_bytes()).hexdigest()
    assert r.provenance.input_files[0].sha256 == r.data["imports"][0]["sha256"]
    assert any("imported geometry" in w for w in r.warnings)


def test_an_import_is_positioned_by_its_offset(ocp, tmp_path):
    step = write_block(tmp_path / "block.step")
    r = ok([imported(step, at=["5 mm", "-2 cm", "1 in"])])
    assert r.data["bbox"]["min_mm"] == pytest.approx([5.0, -20.0, 25.4])
    assert r.data["bbox"]["max_mm"] == pytest.approx([35.0, 0.0, 35.4])


def test_an_inch_step_file_is_imported_in_millimetres(ocp, tmp_path):
    import cad_fixtures as cad

    shape = cad.box(0, 0, 0, 25.4, 50.8, 12.7)  # millimetres inside the kernel; the writer puts 1 x 2 x 0.5 into the file, labelled inch
    step = write_step_inches(shape, tmp_path / "inch.step")
    text = step.read_text(encoding="latin-1")
    assert "CONVERSION_BASED_UNIT('INCH'" in text and "50.8" not in text  # the file really holds inches (25.4 appears only as the unit's conversion factor)
    r = ok([imported(step)])
    assert size(r) == pytest.approx([25.4, 50.8, 12.7], abs=1e-9)
    assert r.values["volume"].quantity == pytest.approx(25.4 * 50.8 * 12.7, abs=1e-6)  # 16387.064
    fn = getattr(measure, "fn", measure)
    assert fn(model_path=str(step)).values["volume"].quantity == pytest.approx(r.values["volume"].quantity, abs=1e-9)  # the same rule as geometry_measure


def test_an_imported_solid_can_be_a_cut_tool_and_receive_holes(ocp, tmp_path):
    step = write_block(tmp_path / "block.step")
    base = named(box(("50 mm", "50 mm", "20 mm")), "base")  # 50000
    r = ok([base, imported(step, at=["10 mm", "10 mm", "10 mm"], mode="cut")])  # the block fills x 10..40, y 10..30, z 10..20: a pocket open at the top
    assert r.values["volume"].quantity == pytest.approx(50000 - 6000, abs=1e-6)
    drilled = ok([imported(step), hole([("15 mm", "10 mm", "10 mm")], "4 mm")])
    assert drilled.values["volume"].quantity == pytest.approx(6000 - pi * 4 * 10, abs=1e-6)  # 6000 - 125.66371
    assert drilled.data["holes_total"] == 1


def test_an_imported_solid_can_be_added_intersected_and_patterned(ocp, tmp_path):
    step = write_block(tmp_path / "block.step")
    plate = box(("100 mm", "40 mm", "5 mm"))  # 20000
    blocks = ok([plate, named(imported(step, at=["0 mm", "5 mm", "5 mm"]), "blk"), pattern("blk", 3, step=["35 mm", "0 mm", "0 mm"])])
    assert blocks.values["volume"].quantity == pytest.approx(20000 + 3 * 6000, abs=1e-6)  # three blocks 5 mm apart, all standing on the plate
    common = ok([box(("20 mm", "20 mm", "20 mm")), imported(step, mode="intersect")])
    assert common.values["volume"].quantity == pytest.approx(20 * 20 * 10, abs=1e-6)  # the 30 x 20 x 10 block clipped to x <= 20: 20 * 20 * 10


def test_an_imported_file_gives_the_same_step_bytes_twice(ocp, tmp_path):
    step = write_block(tmp_path / "block.step")
    features = [box(("50 mm", "50 mm", "20 mm")), imported(step, at=["10 mm", "10 mm", "10 mm"], mode="cut"), {"op": "mirror", "plane": "yz", "offset": "50 mm"}]
    assert ok(features).data["step_sha256"] == ok(features).data["step_sha256"]


def test_iges_and_brep_files_import_too(ocp, tmp_path):
    import cad_fixtures as cad

    shape = cad.box(0, 0, 0, 30.0, 20.0, 10.0)
    for path in (cad.write_iges(shape, tmp_path / "b.iges"), cad.write_brep(shape, tmp_path / "b.brep")):
        assert ok([imported(path)]).values["volume"].quantity == pytest.approx(6000, abs=1e-6), path.name


def test_the_features_file_lists_the_imports(ocp, tmp_path):
    step = write_block(tmp_path / "block.step")
    r = ok([imported(step)])
    document = json.loads(artifact(r, "part.features.json").read_text(encoding="utf8"))
    assert document["imports"] == [{"feature": "import1", "feature_index": 1, "file": str(step), "sha256": hashlib.sha256(step.read_bytes()).hexdigest()}]


# --------------------------------------------------------------------------- refusals through the gateway client


async def through_the_gateway(features, **kwargs):
    async with Client(gateway) as client:
        raw = await client.call_tool("geometry_make_part", {"spec": features if isinstance(features, str) else spec(features), **kwargs})
    json.dumps(raw.structured_content, allow_nan=False)  # raises on NaN or Infinity anywhere in the result
    text = " ".join(getattr(part, "text", "") for part in raw.content)
    assert "NaN" not in text and "Infinity" not in text
    return ToolResult.model_validate(raw.structured_content)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("op", "field", "size"),
    [("shell", "faces", {"thickness": "2 mm"}), ("fillet", "edges", {"radius": "2 mm"}), ("chamfer", "edges", {"distance": "2 mm"})],
)
@pytest.mark.parametrize("selector", [">z or", "or >z", ">z or or <z", ">z OR <z", ">z || <z", ">z, <z", ">z or nonsense", " or ".join([">z"] * 9)])
async def test_invalid_selector_groups_are_refused_through_the_gateway(op, field, size, selector):
    r = await through_the_gateway([cube(), {"op": op, field: selector, **size}])
    assert not r.ok and r.error.family == "input.invalid"
    assert r.error.details["pointer"] == f"/features/1/{field}"
    from eng_toolkit import brep

    terms = brep.FACE_TERMS if field == "faces" else brep.EDGE_TERMS
    assert set(r.error.details["allowed"]) == {*terms, "of:<feature name>"}
    if selector.count(" or ") == 8:
        assert r.error.details["limit"] == 8
    assert r.review_status == "unreviewed" and r.values == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["faces", "edges"])
async def test_a_hundred_thousand_character_selector_hits_the_existing_spec_size_limit(field):
    modifier = {"op": "shell", "thickness": "2 mm"} if field == "faces" else {"op": "fillet", "radius": "2 mm"}
    r = await through_the_gateway([cube(), {**modifier, field: "x" * 100000}])
    assert not r.ok and r.error.family == "input.invalid"
    assert r.error.details["pointer"] == "" and r.error.details["limit_bytes"] == 65536


@pytest.mark.asyncio
@pytest.mark.parametrize("thickness", [float("nan"), float("inf"), float("-inf")])
async def test_a_union_shell_with_nonfinite_thickness_is_refused_without_nonfinite_results(thickness):
    r = await through_the_gateway([cube(), {"op": "shell", "faces": ">z or <z", "thickness": thickness}])
    assert not r.ok and r.error.details["pointer"] == "/features/1/thickness"
    assert r.values == {} and r.review_status == "unreviewed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("faces", "thickness", "axis"),
    [
        (">z or <z", "15 mm", "y"), (">z or <z", "16 mm", "y"),  # y room = 30/2 = 15.
        (">x or <x", "10 mm", "z"), (">x or <x", "14 mm", "z"),  # z room = 20/2 = 10; x room = 40.
    ],
)
async def test_union_shells_refuse_walls_at_or_above_the_remaining_room(ocp, faces, thickness, axis):
    r = await through_the_gateway([box(("40 mm", "30 mm", "20 mm")), {"op": "shell", "faces": faces, "thickness": thickness}])
    assert not r.ok and r.error.family == "input.invalid"
    assert r.error.details["pointer"] == "/features/1/thickness"
    assert f"along {axis}" in r.error.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "modifier",
    [
        {"op": "shell", "faces": ">z & <z or cylinder", "thickness": "2 mm"},
        {"op": "fillet", "edges": "|z & |x or circle", "radius": "2 mm"},
        {"op": "chamfer", "edges": "|z & |x or circle", "distance": "2 mm"},
    ],
)
async def test_only_an_empty_whole_union_is_an_empty_selection(ocp, modifier):
    r = await through_the_gateway([cube(), modifier])
    assert not r.ok and r.error.family == "geometry.empty_selection"
    assert r.error.details["pointer"] == "/features/1" and r.error.details["feature_index"] == 2


@pytest.mark.asyncio
async def test_a_valid_union_works_through_the_gateway_with_finite_results(ocp):
    r = await through_the_gateway([box(("40 mm", "30 mm", "20 mm")), {"op": "shell", "faces": ">z or <z", "thickness": "14 mm"}])
    # 40*30*20 - (40-28)*(30-28)*20 = 24000 - 480 = 23520.
    assert r.ok and r.values["volume"].quantity == pytest.approx(23520, abs=1e-6)
    assert r.review_status == "unreviewed"


def cube(n=40):
    return named(box((f"{n} mm",) * 3), "body")


def stadium_with(segments):
    return {"op": "extrude", "profile": {"path": {"start": ["0 mm", "0 mm"], "segments": segments}}, "distance": "5 mm"}


REFUSALS = {
    "count_0": ([PLATE, HOLE6, pattern("h", 0, step=["1 mm", "0 mm", "0 mm"])], "/features/2/count"),
    "count_1": ([PLATE, HOLE6, pattern("h", 1, step=["1 mm", "0 mm", "0 mm"])], "/features/2/count"),
    "count_1e9": ([PLATE, HOLE6, pattern("h", 1e9, step=["1 mm", "0 mm", "0 mm"])], "/features/2/count"),
    "count_text": ([PLATE, HOLE6, pattern("h", "4", step=["1 mm", "0 mm", "0 mm"])], "/features/2/count"),
    "step_1e308": ([PLATE, HOLE6, pattern("h", 3, step=["1e308 mm", "0 mm", "0 mm"])], "/features/2/step/0"),
    "step_tower": ([PLATE, HOLE6, pattern("h", 3, step=["9**9**9 mm", "0 mm", "0 mm"])], "/features/2/step/0"),
    "step_and_angle": ([PLATE, HOLE6, pattern("h", 3, step=["1 mm", "0 mm", "0 mm"], angle="90 deg")], "/features/2/angle"),
    "taper_90": ([{"op": "extrude", "profile": {"rect": {"size": ["10 mm", "10 mm"]}}, "distance": "5 mm", "taper": "90 deg"}], "/features/0/taper"),
    "taper_47": ([{"op": "extrude", "profile": {"rect": {"size": ["10 mm", "10 mm"]}}, "distance": "5 mm", "taper": "47 deg"}], "/features/0/taper"),
    "bow_tie": ([stadium_with([{"to": ["10 mm", "10 mm"]}, {"to": ["10 mm", "0 mm"]}, {"to": ["0 mm", "10 mm"]}, {"to": ["0 mm", "0 mm"]}])], "/features/0/profile/path/segments/2/to"),
    "path_open": ([stadium_with([{"to": ["10 mm", "0 mm"]}, {"to": ["10 mm", "10 mm"]}])], "/features/0/profile/path/segments/1/to"),
    "arc_too_small": ([stadium_with([{"to": ["10 mm", "0 mm"], "radius": "1 mm"}, {"to": ["0 mm", "0 mm"]}])], "/features/0/profile/path/segments/0/radius"),
    "shell_too_thick": ([cube(), {"op": "shell", "faces": ">z", "thickness": "25 mm"}], "/features/1/thickness"),
    "shell_half_the_width": ([cube(), {"op": "shell", "faces": ">z", "thickness": "20 mm"}], "/features/1/thickness"),
    "of_no_feature": ([cube(), {"op": "fillet", "edges": "of:ghost & all", "radius": "1 mm"}], "/features/1/edges"),
    "of_a_fillet": ([cube(), {"op": "fillet", "name": "f", "edges": "|z", "radius": "1 mm"}, {"op": "chamfer", "edges": "of:f", "distance": "0.1 mm"}], "/features/2/edges"),
    "pattern_of_a_fillet": ([cube(), {"op": "fillet", "name": "f", "edges": "|z", "radius": "1 mm"}, pattern("f", 2, step=["1 mm", "0 mm", "0 mm"])], "/features/2/of"),
    "mirror_of_nothing": ([cube(), {"op": "mirror", "plane": "yz", "of": "ghost"}], "/features/1/of"),
    "import_missing_file": ([{"op": "import", "file": "C:/no/such/block.step"}], "/features/0/file"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", list(REFUSALS))
async def test_invalid_specs_are_refused_with_a_field_level_pointer(ocp, case):
    features, pointer = REFUSALS[case]
    r = await through_the_gateway(features)
    assert r.ok is False, case
    assert r.error.details["pointer"] == pointer, (r.error.family, r.error.message)
    assert r.error.family.startswith("input."), r.error.family
    assert r.value is None and r.values == {} and not any(a.path.endswith((".step", ".json")) and "inputs" not in a.path for a in r.artifacts)


@pytest.mark.asyncio
async def test_a_step_json_number_of_1e308_is_refused_cleanly(ocp):
    r = await through_the_gateway([PLATE, HOLE6, pattern("h", 3, step=[1e308, 0, 0])])
    assert r.ok is False and r.error.family == "units.bare_number" and r.error.details["pointer"] == "/features/2/step/0"


@pytest.mark.asyncio
async def test_a_shell_thicker_than_the_part_names_the_thickness_and_the_size(ocp):
    r = await through_the_gateway([cube(), {"op": "shell", "faces": ">z", "thickness": "25 mm"}])
    assert r.error.family == "input.invalid"
    assert "a wall of 25 mm does not fit" in r.error.message
    assert r.error.details["pointer"] == "/features/1/thickness"


@pytest.mark.asyncio
async def test_a_taper_that_closes_the_profile_before_the_distance_is_refused(ocp):
    r = await through_the_gateway([{"op": "extrude", "profile": {"rect": {"size": ["10 mm", "10 mm"]}}, "distance": "20 mm", "taper": "30 deg"}])
    assert r.ok is False and r.error.details["pointer"] == "/features/0/taper" and "closes the profile" in r.error.message


@pytest.mark.asyncio
async def test_an_import_of_a_shell_is_refused(ocp, tmp_path):
    import cad_fixtures as cad

    step = cad.write_step(cad.planar_shell(), tmp_path / "shell.step")
    r = await through_the_gateway([imported(step)])
    assert r.ok is False and r.error.family == "input.invalid"
    assert r.error.details["pointer"] == "/features/0/file" and "no closed solid" in r.error.message


@pytest.mark.asyncio
async def test_an_import_of_several_solids_is_refused(ocp, tmp_path):
    import cad_fixtures as cad

    step = cad.write_step(cad.compound(cad.box(0, 0, 0, 10, 10, 10), cad.box(50, 0, 0, 10, 10, 10)), tmp_path / "two.step")
    r = await through_the_gateway([imported(step)])
    assert r.ok is False and r.error.details["pointer"] == "/features/0/file" and "2 solids" in r.error.message


@pytest.mark.asyncio
async def test_an_import_of_a_file_that_is_not_a_model_is_refused(ocp, tmp_path):
    junk = tmp_path / "junk.step"
    junk.write_text("this is not a STEP file", encoding="utf8")
    r = await through_the_gateway([imported(junk)])
    assert r.ok is False and r.error.details["pointer"] == "/features/0/file"


@pytest.mark.asyncio
async def test_an_import_outside_the_allowed_directories_is_refused(ocp, tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    step = write_block(tmp_path / "outside.step")
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(allowed))
    r = await through_the_gateway([imported(step)])
    assert r.ok is False and r.error.family == "input.path_not_allowed" and r.error.details["pointer"] == "/features/0/file"


@pytest.mark.asyncio
async def test_a_pattern_and_an_import_work_through_the_gateway_and_leave_no_nan(ocp, tmp_path):
    step = write_block(tmp_path / "block.step")
    r = await through_the_gateway([PLATE, HOLE6, pattern("h", 4, step=["20 mm", "0 mm", "0 mm"]), {"op": "mirror", "plane": "xz", "offset": "30 mm", "of": "h"}])
    assert r.ok, r.error
    assert r.data["holes_total"] == 5  # four in the row and the mirror of the first one only
    assert r.values["volume"].quantity == pytest.approx(48000 - 5 * pi * 9 * 8, abs=1e-6)
    s = await through_the_gateway([imported(step)])
    assert s.ok and s.values["volume"].quantity == pytest.approx(6000, abs=1e-6)
