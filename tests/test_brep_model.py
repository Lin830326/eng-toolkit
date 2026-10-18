"""The brep modelling API checked against hand calculations: primitives, profiles, booleans, fillets, writers.

Lengths are millimetres.  Needs the optional cad engine; the whole module skips without it.
"""

import hashlib
import json
import math
import os
import struct
import subprocess
import sys

import pytest

pytest.importorskip("OCP", reason="OCP is not installed; run: uv sync --extra cad")

from eng_toolkit import brep  # noqa: E402
from eng_toolkit.errors import ToolFailure  # noqa: E402

PROBE_VOLUME = 100000 - 25 * math.pi * 20 - 4 * (4 - math.pi) * 20
XY, XZ, YZ = (brep.SketchPlane(name, 0.0) for name in ("xy", "xz", "yz"))


def volume(solid) -> float:
    return brep.mass_properties(solid).volume_mm3


def size(solid) -> tuple[float, float, float]:
    props = brep.mass_properties(solid)
    return tuple(hi - lo for lo, hi in zip(props.bbox_min_mm, props.bbox_max_mm))


def cut(base, tool):
    return brep.combine(base, tool, "cut")


def probe_part():
    drilled = cut(brep.make_box((100, 50, 20), (0, 0, 0)), brep.make_hole_tool([(25, 25, 20)], (0, 0, -1), 10.0, None, None, None, 200.0))
    return brep.fillet(drilled, brep.parse_edge_selector("|z & convex"), 2.0)


# --------------------------------------------------------------------------- primitives


def test_box_matches_the_hand_calculation():
    # 80 x 40 x 10: volume 32000, area 2*(3200 + 800 + 400) = 8800
    box = brep.make_box((80, 40, 10), (0, 0, 0))
    props = brep.mass_properties(box)
    assert props.volume_mm3 == pytest.approx(32000.0, abs=1e-6)
    assert props.area_mm2 == pytest.approx(8800.0, abs=1e-6)
    assert size(box) == pytest.approx((80, 40, 10), abs=1e-9)
    assert (box.solids, box.faces) == (1, 6)


def test_box_at_puts_the_minimum_corner_there():
    props = brep.mass_properties(brep.make_box((2, 3, 4), (10, 20, 30)))
    assert props.bbox_min_mm == pytest.approx((10, 20, 30)) and props.bbox_max_mm == pytest.approx((12, 23, 34))


def test_cylinder_matches_the_hand_calculation():
    # diameter 30, height 40: volume pi*225*40, area 2*pi*15*40 + 2*pi*225
    cylinder = brep.make_cylinder(30.0, 40.0, (0, 0, 0), (0, 0, 1))
    props = brep.mass_properties(cylinder)
    assert props.volume_mm3 == pytest.approx(math.pi * 225 * 40, abs=1e-6)
    assert props.area_mm2 == pytest.approx(2 * math.pi * 15 * 40 + 2 * math.pi * 225, abs=1e-6)
    assert size(cylinder) == pytest.approx((30, 30, 40), abs=1e-9)


@pytest.mark.parametrize(
    ("axis", "extent"),
    [((1, 0, 0), (40, 30, 30)), ((0, -1, 0), (30, 40, 30)), ((0, 0, -1), (30, 30, 40)), ((0, 1, 0), (30, 40, 30))],
)
def test_cylinder_axis_sets_the_direction(axis, extent):
    cylinder = brep.make_cylinder(30.0, 40.0, (0, 0, 0), axis)
    assert size(cylinder) == pytest.approx(extent, abs=1e-9)
    props = brep.mass_properties(cylinder)
    base_centre = (0.0, 0.0, 0.0)
    far = [c + 40.0 * a for c, a in zip(base_centre, axis)]
    for i in range(3):
        assert min(base_centre[i], far[i]) - 15 * (1 - abs(axis[i])) == pytest.approx(props.bbox_min_mm[i], abs=1e-9)


def test_a_zero_axis_is_input_invalid():
    with pytest.raises(ToolFailure) as info:
        brep.make_cylinder(30.0, 40.0, (0, 0, 0), (0, 0, 0))
    assert info.value.family == "input.invalid"


@pytest.mark.parametrize("args", [(0.0, 5.0), (5.0, 0.0), (-1.0, 5.0)])
def test_a_flat_cylinder_is_input_invalid(args):
    with pytest.raises(ToolFailure) as info:
        brep.make_cylinder(args[0], args[1], (0, 0, 0), (0, 0, 1))
    assert info.value.family == "input.invalid"


def test_sphere_matches_the_hand_calculation():
    # diameter 30: volume 4/3*pi*15^3, area 4*pi*15^2
    ball = brep.make_sphere(30.0, (0, 0, 0))
    props = brep.mass_properties(ball)
    assert props.volume_mm3 == pytest.approx(4 / 3 * math.pi * 15**3, abs=1e-6)
    assert props.area_mm2 == pytest.approx(4 * math.pi * 225, abs=1e-6)
    assert size(ball) == pytest.approx((30, 30, 30), abs=1e-9)
    assert ball.solids == 1


def test_sphere_at_is_its_centre():
    props = brep.mass_properties(brep.make_sphere(10.0, (10, 20, 30)))
    assert props.bbox_min_mm == pytest.approx((5, 15, 25), abs=1e-9) and props.bbox_max_mm == pytest.approx((15, 25, 35), abs=1e-9)


@pytest.mark.parametrize("diameter", [0.0, -1.0, math.nan, math.inf])
def test_a_flat_sphere_is_input_invalid(diameter):
    with pytest.raises(ToolFailure) as info:
        brep.make_sphere(diameter, (0, 0, 0))
    assert info.value.family == "input.invalid"


def test_frustum_matches_the_hand_calculation():
    # radii 20 and 10, height 30: pi*h/3*(R^2 + R*r + r^2) = pi*10*700
    cone = brep.make_cone(40.0, 20.0, 30.0, (0, 0, 0), (0, 0, 1))
    assert brep.mass_properties(cone).volume_mm3 == pytest.approx(math.pi * 10 * 700, abs=1e-6)
    assert size(cone) == pytest.approx((40, 40, 30), abs=1e-9)
    slant = math.hypot(30.0, 10.0)
    assert brep.mass_properties(cone).area_mm2 == pytest.approx(math.pi * (400 + 100) + math.pi * (20 + 10) * slant, abs=1e-6)


def test_a_full_cone_comes_to_a_point():
    # radius 15, height 20: pi*15^2*20/3
    cone = brep.make_cone(30.0, 0.0, 20.0, (0, 0, 0), (0, 0, 1))
    assert brep.mass_properties(cone).volume_mm3 == pytest.approx(math.pi * 225 * 20 / 3, abs=1e-6)
    assert (cone.solids, cone.faces) == (1, 2)


def test_a_cone_with_equal_diameters_is_a_cylinder():
    cone = brep.make_cone(30.0, 30.0, 40.0, (0, 0, 0), (0, 0, 1))
    assert brep.mass_properties(cone).volume_mm3 == pytest.approx(math.pi * 225 * 40, abs=1e-6)


def test_a_cone_may_widen_towards_its_top():
    cone = brep.make_cone(20.0, 40.0, 30.0, (0, 0, 0), (0, 0, 1))
    assert brep.mass_properties(cone).volume_mm3 == pytest.approx(math.pi * 10 * 700, abs=1e-6)


@pytest.mark.parametrize(
    ("axis", "extent"),
    [((1, 0, 0), (30, 40, 40)), ((0, -1, 0), (40, 30, 40)), ((0, 0, -1), (40, 40, 30))],
)
def test_cone_axis_sets_the_direction(axis, extent):
    cone = brep.make_cone(40.0, 20.0, 30.0, (0, 0, 0), axis)
    assert size(cone) == pytest.approx(extent, abs=1e-9)
    props = brep.mass_properties(cone)
    far = [30.0 * a for a in axis]
    for i in range(3):
        low = min(0.0, far[i]) - 20 * (1 - abs(axis[i]))
        assert low == pytest.approx(props.bbox_min_mm[i], abs=1e-9)


def test_cone_at_is_the_centre_of_its_base():
    props = brep.mass_properties(brep.make_cone(40.0, 20.0, 30.0, (10, 20, 30), (0, 0, 1)))
    assert props.bbox_min_mm == pytest.approx((-10, 0, 30), abs=1e-9) and props.bbox_max_mm == pytest.approx((30, 40, 60), abs=1e-9)


@pytest.mark.parametrize("args", [(0.0, 5.0, 5.0), (-1.0, 0.0, 5.0), (10.0, -1.0, 5.0), (10.0, 5.0, 0.0), (10.0, 5.0, -2.0), (10.0, math.nan, 5.0)])
def test_a_degenerate_cone_is_input_invalid(args):
    with pytest.raises(ToolFailure) as info:
        brep.make_cone(*args, (0, 0, 0), (0, 0, 1))
    assert info.value.family == "input.invalid"


def test_a_cone_with_a_zero_axis_is_input_invalid():
    with pytest.raises(ToolFailure) as info:
        brep.make_cone(10.0, 5.0, 5.0, (0, 0, 0), (0, 0, 0))
    assert info.value.family == "input.invalid"


def test_a_flat_box_is_input_invalid():
    with pytest.raises(ToolFailure) as info:
        brep.make_box((1, 0, 1), (0, 0, 0))
    assert info.value.family == "input.invalid"


# --------------------------------------------------------------------------- profiles


def test_prism_of_a_rectangle_in_each_plane():
    rect = brep.Rect2((20.0, 10.0), (0.0, 0.0), False)
    xy = brep.make_prism(rect, XY, 5.0)
    assert volume(xy) == pytest.approx(1000.0, abs=1e-9) and size(xy) == pytest.approx((20, 10, 5))
    xz = brep.make_prism(rect, XZ, 5.0)  # u = x, v = z, extruded along +y
    assert size(xz) == pytest.approx((20, 5, 10))
    yz = brep.make_prism(rect, YZ, 5.0)  # u = y, v = z, extruded along +x
    assert size(yz) == pytest.approx((5, 20, 10))


def test_the_plane_offset_moves_along_the_plane_normal_and_a_negative_distance_goes_the_other_way():
    rect = brep.Rect2((20.0, 10.0), (0.0, 0.0), False)
    props = brep.mass_properties(brep.make_prism(rect, brep.SketchPlane("xy", 7.0), -5.0))
    assert props.bbox_min_mm[2] == pytest.approx(2.0) and props.bbox_max_mm[2] == pytest.approx(7.0)
    props = brep.mass_properties(brep.make_prism(rect, brep.SketchPlane("xz", 3.0), 5.0))
    assert props.bbox_min_mm[1] == pytest.approx(3.0) and props.bbox_max_mm[1] == pytest.approx(8.0)
    props = brep.mass_properties(brep.make_prism(rect, brep.SketchPlane("yz", -2.0), 5.0))
    assert props.bbox_min_mm[0] == pytest.approx(-2.0) and props.bbox_max_mm[0] == pytest.approx(3.0)


def test_rect_at_is_the_minimum_corner_unless_centered():
    corner = brep.mass_properties(brep.make_prism(brep.Rect2((20.0, 10.0), (3.0, 4.0), False), XY, 1.0))
    assert corner.bbox_min_mm[:2] == pytest.approx((3.0, 4.0)) and corner.bbox_max_mm[:2] == pytest.approx((23.0, 14.0))
    centred = brep.mass_properties(brep.make_prism(brep.Rect2((20.0, 10.0), (3.0, 4.0), True), XY, 1.0))
    assert centred.bbox_min_mm[:2] == pytest.approx((-7.0, -1.0)) and centred.bbox_max_mm[:2] == pytest.approx((13.0, 9.0))


def test_prism_of_a_circle_a_triangle_and_a_slot():
    assert volume(brep.make_prism(brep.Circle2(10.0, (0.0, 0.0)), XY, 20.0)) == pytest.approx(math.pi * 25 * 20, abs=1e-6)
    triangle = brep.Polygon2(((0.0, 0.0), (10.0, 0.0), (0.0, 6.0)))
    assert volume(brep.make_prism(triangle, XY, 4.0)) == pytest.approx(120.0, abs=1e-9)
    clockwise = brep.Polygon2(((0.0, 0.0), (0.0, 6.0), (10.0, 0.0)))
    assert volume(brep.make_prism(clockwise, XY, 4.0)) == pytest.approx(120.0, abs=1e-9)
    # slot 30 long overall and 10 wide: (30 - 10)*10 + pi*5^2
    slot = brep.make_prism(brep.Slot2(30.0, 10.0, (0.0, 0.0), 0.0), XY, 2.0)
    assert volume(slot) == pytest.approx(((30 - 10) * 10 + math.pi * 25) * 2, abs=1e-6)
    assert size(slot) == pytest.approx((30, 10, 2), abs=1e-9)
    turned = brep.make_prism(brep.Slot2(30.0, 10.0, (0.0, 0.0), math.pi / 2), XY, 2.0)
    assert size(turned) == pytest.approx((10, 30, 2), abs=1e-9)


def test_a_self_crossing_polygon_is_a_build_failure():
    bow_tie = brep.Polygon2(((0.0, 0.0), (10.0, 10.0), (10.0, 0.0), (0.0, 10.0)))
    with pytest.raises(ToolFailure) as info:
        brep.make_prism(bow_tie, XY, 1.0)
    assert info.value.family in ("geometry.build_failed", "geometry.invalid_result")


def test_revolving_a_rectangle_makes_a_washer():
    # inner radius 8, outer radius 14, height 3: pi*(14^2 - 8^2)*3
    profile = brep.Rect2((6.0, 3.0), (8.0, 0.0), False)
    washer = brep.make_revolution(profile, XZ, "v", 2 * math.pi)
    assert volume(washer) == pytest.approx(math.pi * (196 - 64) * 3, abs=1e-6)
    assert size(washer) == pytest.approx((28, 28, 3), abs=1e-9)
    half = brep.make_revolution(profile, XZ, "v", math.pi)
    assert volume(half) == pytest.approx(math.pi * (196 - 64) * 3 / 2, abs=1e-6)


def test_revolving_about_the_u_axis():
    # the rectangle u 0..4, v 5..8 turned about the u axis: pi*(8^2 - 5^2)*4
    solid = brep.make_revolution(brep.Rect2((4.0, 3.0), (0.0, 5.0), False), XZ, "u", 2 * math.pi)
    assert volume(solid) == pytest.approx(math.pi * (64 - 25) * 4, abs=1e-6)


def test_a_profile_across_the_axis_does_not_make_a_valid_solid():
    with pytest.raises(ToolFailure) as info:
        brep.make_revolution(brep.Rect2((10.0, 3.0), (-5.0, 0.0), False), XZ, "v", 2 * math.pi)
    assert info.value.family in ("geometry.build_failed", "geometry.invalid_result")


# --------------------------------------------------------------------------- holes and booleans


def test_a_through_hole_matches_the_hand_calculation():
    # 60 x 30 x 15 with a diameter-8 through hole: 27000 - pi*16*15 = 26246.018
    block = brep.make_box((60, 30, 15), (0, 0, 0))
    drilled = cut(block, brep.make_hole_tool([(30, 15, 15)], (0, 0, -1), 8.0, None, None, None, 100.0))
    assert volume(drilled) == pytest.approx(27000 - math.pi * 16 * 15, abs=1e-6)
    assert (drilled.solids, drilled.faces) == (1, 7)


def test_four_holes_in_one_tool_match_the_hand_calculation():
    plate = brep.make_box((100, 60, 8), (0, 0, 0))
    tool = brep.make_hole_tool([(10, 10, 8), (90, 10, 8), (10, 50, 8), (90, 50, 8)], (0, 0, -1), 6.0, None, None, None, 100.0)
    assert volume(cut(plate, tool)) == pytest.approx(48000 - 4 * math.pi * 9 * 8, abs=1e-6)


def test_overlapping_holes_are_cut_once():
    plate = brep.make_box((40, 20, 10), (0, 0, 0))
    tool = brep.make_hole_tool([(10, 10, 10), (14, 10, 10)], (0, 0, -1), 6.0, None, None, None, 100.0)
    # two circles of radius 3 with centres 4 apart overlap in a lens of 2*r^2*acos(d/2r) - (d/2)*sqrt(4r^2 - d^2)
    lens = 2 * 9 * math.acos(4 / 6) - 2 * math.sqrt(36 - 16)
    assert volume(cut(plate, tool)) == pytest.approx(8000 - (2 * math.pi * 9 - lens) * 10, abs=1e-6)


def test_a_blind_hole_stops_at_its_depth():
    block = brep.make_box((40, 40, 30), (0, 0, 0))
    drilled = cut(block, brep.make_hole_tool([(20, 20, 30)], (0, 0, -1), 10.0, 15.0, None, None, 100.0))
    assert volume(drilled) == pytest.approx(48000 - math.pi * 25 * 15, abs=1e-6)


def test_a_hole_along_x_from_the_far_side():
    block = brep.make_box((40, 40, 30), (0, 0, 0))
    drilled = cut(block, brep.make_hole_tool([(40, 20, 15)], (-1, 0, 0), 10.0, 12.0, None, None, 100.0))
    assert volume(drilled) == pytest.approx(48000 - math.pi * 25 * 12, abs=1e-6)


def test_counterbore_and_countersink_tools():
    block = brep.make_box((40, 40, 20), (0, 0, 0))
    bored = cut(block, brep.make_hole_tool([(20, 20, 20)], (0, 0, -1), 6.6, None, (11.0, 6.5), None, 100.0))
    assert volume(bored) == pytest.approx(32000 - math.pi / 4 * (11**2 * 6.5 + 6.6**2 * 13.5), abs=1e-6)
    sunk = cut(block, brep.make_hole_tool([(20, 20, 20)], (0, 0, -1), 6.0, None, None, (12.0, math.pi / 2), 100.0))
    assert volume(sunk) == pytest.approx(32000 - (math.pi * 63 + math.pi * 9 * 17), abs=1e-6)


def test_a_tube_by_cutting_one_cylinder_from_another():
    outer = brep.make_cylinder(40.0, 100.0, (0, 0, 0), (0, 0, 1))
    tube = cut(outer, brep.make_cylinder(30.0, 100.0, (0, 0, 0), (0, 0, 1)))
    assert volume(tube) == pytest.approx(math.pi * (400 - 225) * 100, abs=1e-6)


def test_add_fuses_touching_solids_into_one_and_merges_coplanar_faces():
    base = brep.make_box((60, 40, 5), (0, 0, 0))
    wall = brep.make_box((5, 40, 25), (0, 0, 5))
    bracket = brep.combine(base, wall, "add")
    assert volume(bracket) == pytest.approx(17000.0, abs=1e-6)
    assert size(bracket) == pytest.approx((60, 40, 30), abs=1e-9)
    assert bracket.solids == 1
    assert brep.validity(bracket) == ()
    assert bracket.faces == 8  # the L section extruded: 6 side faces and 2 ends


def test_disjoint_solids_added_stay_two_solids():
    two = brep.combine(brep.make_box((10, 10, 10), (0, 0, 0)), brep.make_box((10, 10, 10), (50, 0, 0)), "add")
    assert two.solids == 2 and volume(two) == pytest.approx(2000.0)
    assert brep.validity(two) == ()


def test_intersect_keeps_the_common_part():
    common = brep.combine(brep.make_box((20, 20, 20), (0, 0, 0)), brep.make_box((20, 20, 20), (10, 10, 10)), "intersect")
    assert volume(common) == pytest.approx(1000.0, abs=1e-9)


def test_cutting_everything_away_is_a_build_failure():
    block = brep.make_box((10, 10, 10), (0, 0, 0))
    with pytest.raises(ToolFailure) as info:
        cut(block, brep.make_box((20, 20, 20), (-5, -5, -5)))
    assert info.value.family == "geometry.build_failed"


def test_an_unknown_combine_mode_is_input_invalid():
    box = brep.make_box((1, 1, 1), (0, 0, 0))
    with pytest.raises(ToolFailure) as info:
        brep.combine(box, box, "xor")
    assert info.value.family == "input.invalid"


# --------------------------------------------------------------------------- fillets, chamfers and selectors


def test_fillet_matches_the_hand_calculation():
    # 50 x 50 x 20 with R3 on the vertical edges: 50000 - 4*(9 - 9*pi/4)*20
    filleted, used = brep.fillet(brep.make_box((50, 50, 20), (0, 0, 0)), brep.parse_edge_selector("|z"), 3.0)
    assert used == 4
    assert volume(filleted) == pytest.approx(50000 - 4 * (9 - 9 * math.pi / 4) * 20, abs=1e-6)


def test_chamfer_matches_the_hand_calculation():
    chamfered, used = brep.chamfer(brep.make_box((60, 40, 20), (0, 0, 0)), brep.parse_edge_selector("|z"), 2.0)
    assert used == 4
    assert volume(chamfered) == pytest.approx(48000 - 4 * 2 * 20, abs=1e-6)


def test_the_probe_part_matches_the_hand_calculation():
    part, used = probe_part()
    assert used == 4
    assert volume(part) == pytest.approx(PROBE_VOLUME, abs=1e-6)
    assert brep.mass_properties(part).area_mm2 == pytest.approx(16395.699052, abs=1e-3)
    assert brep.validity(part) == ()
    assert brep.inventory(part).holes[0].diameter_mm == pytest.approx(10.0)


def edges_selected(solid, text: str) -> int:
    return brep.select_edges(solid, brep.parse_edge_selector(text)).count


def test_convex_vertical_edges_exclude_the_seam_line_of_the_hole():
    drilled = cut(brep.make_box((100, 50, 20), (0, 0, 0)), brep.make_hole_tool([(25, 25, 20)], (0, 0, -1), 10.0, None, None, None, 100.0))
    assert edges_selected(drilled, "|z") == 5  # the four outer corners and the seam line of the hole
    assert edges_selected(drilled, "|z & convex") == 4
    assert edges_selected(drilled, "|z & concave") == 0  # the seam joins a face to itself: neither convex nor concave
    assert edges_selected(drilled, "circle & convex") == 2  # both rims of the hole are convex
    assert edges_selected(drilled, "circle") == 2


def l_prism():
    """An L section (6 vertices, one concave corner) extruded 10 mm along z."""
    outline = brep.Polygon2(((0.0, 0.0), (30.0, 0.0), (30.0, 10.0), (10.0, 10.0), (10.0, 30.0), (0.0, 30.0)))
    return brep.make_prism(outline, XY, 10.0)


@pytest.mark.parametrize(
    ("text", "count"),
    [
        ("all", 18),
        ("|z", 6),
        ("|x", 6),
        ("|y", 6),
        ("|z & convex", 5),
        ("|z & concave", 1),
        ("concave", 1),
        ("convex", 17),
        (">z", 6),
        ("<z", 6),
        (">x", 4),
        ("<x", 4),
        (">y", 4),
        ("<y", 4),
        ("line", 18),
        ("circle", 0),
        (">z & convex", 6),
        ("<x & |z", 2),
        ("|x & <y & >z", 1),
    ],
)
def test_selectors_on_an_l_section(text, count):
    # L outline (0,0) (30,0) (30,10) (10,10) (10,30) (0,30), extruded 10 mm: 18 edges, three along each axis on top and bottom
    assert edges_selected(l_prism(), text) == count


def test_selectors_on_a_cylinder():
    cylinder = brep.make_cylinder(30.0, 40.0, (0, 0, 0), (0, 0, 1))
    assert edges_selected(cylinder, "circle") == 2
    assert edges_selected(cylinder, "line") == 1
    assert edges_selected(cylinder, "all") == 3
    assert edges_selected(cylinder, "circle & >z") == 1
    assert edges_selected(cylinder, "circle & <z") == 1
    assert edges_selected(cylinder, "convex") == 2  # the seam line joins a face to itself: neither convex nor concave
    assert edges_selected(cylinder, "concave") == 0
    assert edges_selected(cylinder, "line & convex") == 0


def test_the_rim_of_a_blind_hole_is_convex_and_its_floor_edge_is_concave():
    drilled = cut(brep.make_box((40, 40, 30), (0, 0, 0)), brep.make_hole_tool([(20, 20, 30)], (0, 0, -1), 10.0, 15.0, None, None, 100.0))
    assert edges_selected(drilled, "circle & convex") == 1
    assert edges_selected(drilled, "circle & concave") == 1
    assert edges_selected(drilled, "circle & >z") == 1


def test_an_empty_selection_reports_the_matches_per_term():
    box = brep.make_box((10, 10, 10), (0, 0, 0))
    with pytest.raises(ToolFailure) as info:
        brep.fillet(box, brep.parse_edge_selector("|z & concave"), 1.0)
    error = info.value
    assert error.family == "geometry.empty_selection"
    assert error.details["selector"] == "|z & concave"
    assert error.details["matches_per_term"] == {"|z": 4, "concave": 0}
    with pytest.raises(ToolFailure) as info:
        brep.fillet(box, brep.parse_edge_selector("circle"), 1.0)
    assert info.value.details["matches_per_term"] == {"circle": 0}


def test_an_impossible_fillet_is_a_build_failure_with_the_kernel_message():
    cube = brep.make_box((10, 10, 10), (0, 0, 0))
    with pytest.raises(ToolFailure) as info:
        brep.fillet(cube, brep.parse_edge_selector("all"), 8.0)
    assert info.value.family == "geometry.build_failed"
    assert info.value.details["occt"]


def test_an_impossible_chamfer_is_a_build_failure():
    cube = brep.make_box((10, 10, 10), (0, 0, 0))
    with pytest.raises(ToolFailure) as info:
        brep.chamfer(cube, brep.parse_edge_selector("all"), 8.0)
    assert info.value.family == "geometry.build_failed"


@pytest.mark.parametrize("radius", [0.0, -1.0])
def test_a_flat_fillet_radius_is_input_invalid(radius):
    with pytest.raises(ToolFailure) as info:
        brep.fillet(brep.make_box((10, 10, 10), (0, 0, 0)), brep.parse_edge_selector("all"), radius)
    assert info.value.family == "input.invalid"


# --------------------------------------------------------------------------- validity


def test_validity_is_empty_for_a_good_solid():
    assert brep.validity(brep.make_box((1, 2, 3), (0, 0, 0))) == ()


# --------------------------------------------------------------------------- rigid motion


def test_transform_translates_a_solid_and_keeps_its_volume():
    moved = brep.transform(brep.make_box((10, 20, 30), (0, 0, 0)), ((1, 0, 0), (0, 1, 0), (0, 0, 1)), (100.0, -5.0, 7.0))
    props = brep.mass_properties(moved)
    assert props.bbox_min_mm == pytest.approx((100, -5, 7), abs=1e-9) and props.bbox_max_mm == pytest.approx((110, 15, 37), abs=1e-9)
    assert props.volume_mm3 == pytest.approx(6000.0, abs=1e-6)
    assert (moved.solids, moved.faces) == (1, 6)


def test_transform_rotates_before_it_translates():
    # a quarter turn about z sends (x, y) to (-y, x): the box [0,10] x [0,20] becomes [-20,0] x [0,10]; then +5 on x
    quarter = ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    props = brep.mass_properties(brep.transform(brep.make_box((10, 20, 30), (0, 0, 0)), quarter, (5.0, 0.0, 0.0)))
    assert props.bbox_min_mm == pytest.approx((-15, 0, 0), abs=1e-9) and props.bbox_max_mm == pytest.approx((5, 10, 30), abs=1e-9)


@pytest.mark.parametrize(
    "rotation",
    [
        ((2.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),  # scales
        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, -1.0)),  # mirrors
        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),  # not 3 x 3
    ],
)
def test_transform_refuses_anything_but_a_rotation(rotation):
    with pytest.raises(ToolFailure) as info:
        brep.transform(brep.make_box((1, 1, 1), (0, 0, 0)), rotation, (0.0, 0.0, 0.0))
    assert info.value.family == "input.invalid"


# --------------------------------------------------------------------------- writers


def test_step_round_trips_and_names_the_product(tmp_path):
    part, _ = probe_part()
    target = tmp_path / "p.step"
    brep.write_step(part, target, product_name="bracket")
    text = target.read_text(encoding="ascii")
    assert "PRODUCT('bracket'" in text
    assert "FILE_NAME('bracket'" in text
    assert "1970-01-01T00:00:00" in text
    back = brep.read_solid(target)
    assert back.solids == 1
    assert brep.mass_properties(back).volume_mm3 == pytest.approx(PROBE_VOLUME, abs=1e-6)


def test_step_bytes_are_identical_for_the_same_shape(tmp_path):
    part, _ = probe_part()
    digests = set()
    for i in range(3):
        target = tmp_path / f"p{i}.step"
        brep.write_step(part, target, product_name="bracket")
        digests.add(hashlib.sha256(target.read_bytes()).hexdigest())
    assert len(digests) == 1


def test_step_bytes_are_identical_across_processes(tmp_path):
    script = (
        "import hashlib, sys\n"
        "from pathlib import Path\n"
        "from eng_toolkit import brep\n"
        "box = brep.make_box((100, 50, 20), (0, 0, 0))\n"
        "tool = brep.make_hole_tool([(25, 25, 20)], (0, 0, -1), 10.0, None, None, None, 100.0)\n"
        "part, _ = brep.fillet(brep.combine(box, tool, 'cut'), brep.parse_edge_selector('|z & convex'), 2.0)\n"
        "path = Path(sys.argv[1])\n"
        "brep.write_step(part, path, product_name='bracket')\n"
        "print(hashlib.sha256(path.read_bytes()).hexdigest())\n"
    )
    digests = []
    for i in range(2):
        done = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path / f"proc{i}.step")],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONHASHSEED": str(i + 1)},
            check=False,
        )
        assert done.returncode == 0, done.stderr
        assert done.stdout.strip().count("\n") == 0
        digests.append(done.stdout.strip())
    assert digests[0] == digests[1]
    part, _ = probe_part()
    in_process = tmp_path / "in.step"
    brep.write_step(part, in_process, product_name="bracket")
    assert hashlib.sha256(in_process.read_bytes()).hexdigest() == digests[0]


def test_the_step_product_name_changes_the_bytes(tmp_path):
    box = brep.make_box((1, 2, 3), (0, 0, 0))
    brep.write_step(box, tmp_path / "a.step", product_name="a")
    brep.write_step(box, tmp_path / "b.step", product_name="b")
    assert (tmp_path / "a.step").read_bytes() != (tmp_path / "b.step").read_bytes()


def test_brep_file_round_trips(tmp_path):
    part, _ = probe_part()
    brep.write_brep(part, tmp_path / "p.brep")
    assert brep.mass_properties(brep.read_solid(tmp_path / "p.brep")).volume_mm3 == pytest.approx(PROBE_VOLUME, abs=1e-6)


def stl_triangles(data: bytes):
    (count,) = struct.unpack_from("<I", data, 80)
    assert len(data) == 84 + 50 * count
    return [struct.unpack_from("<12fH", data, 84 + 50 * i) for i in range(count)]


def test_stl_of_a_box_is_binary_with_a_fixed_header(tmp_path):
    # 12 triangles: 84 + 12*50 = 684 bytes
    box = brep.make_box((80, 40, 10), (0, 0, 0))
    target = tmp_path / "b.stl"
    brep.write_mesh(brep.triangulate(box, 0.01), target, fmt="stl", name="box")
    data = target.read_bytes()
    assert len(data) == 684
    assert len(data[:80]) == 80 and not data.startswith(b"solid")
    triangles = stl_triangles(data)
    assert len(triangles) == 12
    signed = 0.0
    for t in triangles:
        nx, ny, nz, ax, ay, az, bx, by, bz, cx, cy, cz, attribute = t
        assert attribute == 0
        assert math.sqrt(nx * nx + ny * ny + nz * nz) == pytest.approx(1.0, abs=1e-6)
        signed += (ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)) / 6.0
    assert signed == pytest.approx(32000.0, abs=1e-3)


def test_stl_header_and_bytes_do_not_depend_on_the_name_or_the_call(tmp_path):
    box = brep.make_box((80, 40, 10), (0, 0, 0))
    mesh = brep.triangulate(box, 0.01)
    brep.write_mesh(mesh, tmp_path / "a.stl", fmt="stl", name="one")
    brep.write_mesh(mesh, tmp_path / "b.stl", fmt="stl", name="two")
    assert (tmp_path / "a.stl").read_bytes() == (tmp_path / "b.stl").read_bytes()


def read_glb(data: bytes):
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    assert magic == b"glTF" and version == 2 and length == len(data)
    json_length, json_type = struct.unpack_from("<I4s", data, 12)
    assert json_type == b"JSON"
    document = json.loads(data[20 : 20 + json_length])
    offset = 20 + json_length
    bin_length, bin_type = struct.unpack_from("<I4s", data, offset)
    assert bin_type == b"BIN\x00"
    return document, data[offset + 8 : offset + 8 + bin_length]


def test_glb_is_metres_with_y_up(tmp_path):
    box = brep.make_box((100, 50, 20), (0, 0, 0))
    target = tmp_path / "b.glb"
    brep.write_mesh(brep.triangulate(box, 0.01), target, fmt="glb", name="box")
    document, blob = read_glb(target.read_bytes())
    assert document["asset"]["version"] == "2.0"
    positions = document["accessors"][document["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    assert positions["count"] == 8 and positions["type"] == "VEC3" and positions["componentType"] == 5126
    # the 100 mm edge is 0.1 m on x, the 20 mm height is 0.02 m on glTF +Y, and the 50 mm depth runs along -z
    assert positions["max"] == pytest.approx([0.1, 0.02, 0.0], abs=1e-7)
    assert positions["min"] == pytest.approx([0.0, 0.0, -0.05], abs=1e-7)
    view = document["bufferViews"][positions["bufferView"]]
    values = struct.unpack_from(f"<{positions['count'] * 3}f", blob, view["byteOffset"])
    assert max(values[1::3]) == pytest.approx(0.02, abs=1e-7)
    index = document["accessors"][document["meshes"][0]["primitives"][0]["indices"]]
    assert index["count"] == 36 and index["componentType"] == 5125
    assert document["nodes"][0]["name"] == "box"


def test_glb_winding_stays_counter_clockwise_seen_from_outside(tmp_path):
    box = brep.make_box((100, 50, 20), (0, 0, 0))
    target = tmp_path / "b.glb"
    brep.write_mesh(brep.triangulate(box, 0.01), target, fmt="glb", name="box")
    document, blob = read_glb(target.read_bytes())
    primitive = document["meshes"][0]["primitives"][0]
    pos = document["accessors"][primitive["attributes"]["POSITION"]]
    idx = document["accessors"][primitive["indices"]]
    pv, iv = document["bufferViews"][pos["bufferView"]], document["bufferViews"][idx["bufferView"]]
    vertices = [struct.unpack_from("<3f", blob, pv["byteOffset"] + 12 * i) for i in range(pos["count"])]
    indices = struct.unpack_from(f"<{idx['count']}I", blob, iv["byteOffset"])
    signed = 0.0
    for k in range(0, len(indices), 3):
        a, b, c = (vertices[i] for i in indices[k : k + 3])
        signed += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0
    assert signed == pytest.approx(0.1 * 0.05 * 0.02, rel=1e-5)


def test_glb_bytes_are_deterministic(tmp_path):
    part, _ = probe_part()
    for i in range(2):
        brep.write_mesh(brep.triangulate(part, 0.05), tmp_path / f"g{i}.glb", fmt="glb", name="p")
    assert (tmp_path / "g0.glb").read_bytes() == (tmp_path / "g1.glb").read_bytes()


def test_an_unknown_mesh_format_is_input_invalid(tmp_path):
    mesh = brep.triangulate(brep.make_box((1, 1, 1), (0, 0, 0)), 0.1)
    with pytest.raises(ToolFailure) as info:
        brep.write_mesh(mesh, tmp_path / "x.obj", fmt="obj", name="x")
    assert info.value.family == "input.invalid"


def test_modelling_writes_nothing_to_stdout(capfd, tmp_path):
    part, _ = probe_part()
    brep.write_step(part, tmp_path / "p.step", product_name="p")
    brep.write_brep(part, tmp_path / "p.brep")
    brep.write_mesh(brep.triangulate(part, 0.05), tmp_path / "p.glb", fmt="glb", name="p")
    for call in (
        lambda: brep.fillet(brep.make_box((10, 10, 10), (0, 0, 0)), brep.parse_edge_selector("all"), 8.0),
        lambda: brep.fillet(brep.make_box((10, 10, 10), (0, 0, 0)), brep.parse_edge_selector("circle"), 1.0),
        lambda: brep.make_prism(brep.Polygon2(((0.0, 0.0), (10.0, 10.0), (10.0, 0.0), (0.0, 10.0))), XY, 1.0),
    ):
        with pytest.raises(ToolFailure):
            call()
    assert capfd.readouterr().out == ""
