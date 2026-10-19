"""Hole grouping, callout text, the view each callout goes in, and the ordinates: pure arithmetic on hand-made holes and projections."""

import pytest
from drawing_fixtures import projection

from eng_toolkit.brep import HoleInfo
from eng_toolkit.domains.drawing import callouts as K
from eng_toolkit.domains.drawing.features import IntentHole

DOWN, UP = (0.0, 0.0, -1.0), (0.0, 0.0, 1.0)


def hole(n, at, d=10.0, axis=DOWN, depth=20.0, thru=True):
    return HoleInfo(id=f"H{n}", face_ids=(n,), diameter_mm=d, axis=axis, entry_mm=at, depth_mm=depth, thru=thru)


def texts(groups):
    return [g.text for g in groups]


# --------------------------------------------------------------------------- grouping and text


def test_one_through_hole_reads_diameter_thru():
    assert texts(K.group_holes([hole(1, (25.0, 25.0, 20.0))])) == ["Ø10 THRU"]


def test_four_equal_holes_are_one_callout_with_a_count():
    holes = [hole(i + 1, (x, y, 20.0), d=6.6) for i, (x, y) in enumerate([(10, 10), (90, 10), (10, 40), (90, 40)])]
    (g,) = K.group_holes(holes)
    assert g.text == "4× Ø6.6 THRU" and g.count == 4 and g.hole_ids == ("H1", "H2", "H3", "H4")


def test_diameters_are_printed_with_two_decimals_at_most():
    assert texts(K.group_holes([hole(1, (0, 0, 0), d=6.60004), hole(2, (9, 0, 0), d=8.0), hole(3, (19, 0, 0), d=12.345)])) == ["Ø6.6 THRU", "Ø8 THRU", "Ø12.35 THRU"]


def test_a_blind_hole_gives_its_depth_and_holes_of_different_depth_are_two_groups():
    groups = K.group_holes([hole(1, (0, 0, 20), d=5, depth=12.0, thru=False), hole(2, (10, 0, 20), d=5, depth=12.0, thru=False), hole(3, (20, 0, 20), d=5, depth=8.5, thru=False)])
    assert texts(groups) == ["Ø5 DEEP 8.5", "2× Ø5 DEEP 12"]


def test_through_holes_drilled_from_either_side_are_one_group_blind_holes_are_not():
    both = K.group_holes([hole(1, (0, 0, 0), axis=DOWN), hole(2, (9, 0, 0), axis=UP)])
    assert texts(both) == ["2× Ø10 THRU"]
    blind = K.group_holes([hole(1, (0, 0, 0), axis=DOWN, depth=5, thru=False), hole(2, (9, 0, 0), axis=UP, depth=5, thru=False)])
    assert texts(blind) == ["Ø10 DEEP 5", "Ø10 DEEP 5"]


def test_the_fit_and_the_thread_of_a_matched_declared_hole_are_appended():
    declared = {"H1": IntentHole("bore", (25, 25, 20), DOWN, 10.0, None, "H7", ""), "H2": IntentHole("tap", (50, 25, 20), DOWN, 5.0, None, "", "M6x1-6H")}
    groups = K.group_holes([hole(1, (25, 25, 20)), hole(2, (50, 25, 20), d=5.0)], declared)
    assert texts(groups) == ["Ø5 M6x1-6H THRU", "Ø10 H7 THRU"] and all(g.intent for g in groups)


def test_a_hole_with_a_fit_and_one_without_are_different_groups():
    declared = {"H1": IntentHole("a", (0, 0, 0), DOWN, 10.0, None, "H7", "")}
    groups = K.group_holes([hole(1, (0, 0, 0)), hole(2, (30, 0, 0))], declared)
    assert sorted(texts(groups)) == ["Ø10 H7 THRU", "Ø10 THRU"]
    assert [g.intent for g in groups if g.text == "Ø10 THRU"] == [False]


def test_the_leader_of_a_group_points_at_its_hole_nearest_the_callout_column_lowest_first():
    holes = [hole(i + 1, (x, y, 20.0), d=6.6) for i, (x, y) in enumerate([(10, 10), (90, 10), (10, 40), (90, 40)])]
    (view,) = K.plan(K.group_holes(holes), [projection("top")]).views
    assert [c.center for c in view.callouts] == [(90.0, 10.0)]  # largest x, then smallest y: the leader crosses no other hole


# --------------------------------------------------------------------------- which view


def test_a_vertical_hole_is_called_out_in_the_top_view_not_the_front_view():
    front, top = projection("front"), projection("top")
    plan = K.plan(K.group_holes([hole(1, (25.0, 25.0, 20.0))]), [front, top])
    assert [v.view for v in plan.views] == ["top"] and plan.placed == {("H1",): "top"}
    assert plan.warnings == ()


def test_a_hole_no_requested_view_looks_along_is_left_out_with_a_hint():
    plan = K.plan(K.group_holes([hole(1, (25.0, 25.0, 20.0))]), [projection("front"), projection("right")])
    assert plan.views == () and plan.placed == {}
    assert len(plan.warnings) == 1 and "top view" in plan.warnings[0] and "H1" in plan.warnings[0] and "Ø10 THRU" in plan.warnings[0]


def test_a_blind_hole_goes_in_the_view_that_sees_its_entry():
    top, bottom = projection("top"), projection("bottom")
    from_top = hole(1, (25, 25, 20), axis=DOWN, depth=5, thru=False)
    from_bottom = hole(2, (60, 25, 0), axis=UP, depth=5, thru=False)
    plan = K.plan(K.group_holes([from_top, from_bottom]), [bottom, top])
    assert plan.placed == {("H1",): "top", ("H2",): "bottom"}


def test_with_only_the_far_side_view_the_hole_is_still_called_out():
    plan = K.plan(K.group_holes([hole(1, (25, 25, 20), axis=DOWN, depth=5, thru=False)]), [projection("bottom")])
    assert plan.placed == {("H1",): "bottom"}


def test_a_side_hole_goes_in_the_front_view_in_the_view_coordinates_of_the_front_view():
    h = hole(1, (30.0, 0.0, 10.0), axis=(0.0, 1.0, 0.0))  # drilled in +y from the front face: x = 30, z = 10
    plan = K.plan(K.group_holes([h]), [projection("front")])
    (callout,) = plan.views[0].callouts
    assert plan.views[0].view == "front" and callout.center == (30.0, 10.0)


# --------------------------------------------------------------------------- ordinates


def test_the_probe_hole_has_ordinates_0_and_25_in_x_and_y():
    top = projection("top")  # box 100 x 50 x 20, datum (0, 0)
    (view,) = K.plan(K.group_holes([hole(1, (25.0, 25.0, 20.0))]), [top]).views
    assert view.datum == (0.0, 0.0)
    assert [(o.axis, o.value) for o in view.ordinates] == [("x", 0.0), ("x", 25.0), ("y", 0.0), ("y", 25.0)]
    assert [o.anchor for o in view.ordinates if o.value == 25.0] == [(25.0, 25.0), (25.0, 25.0)]


def test_ordinates_are_one_per_distinct_coordinate_and_start_at_the_datum():
    holes = [hole(i + 1, (x, y, 20.0), d=6.6) for i, (x, y) in enumerate([(10, 10), (90, 10), (10, 40), (90, 40)])]
    (view,) = K.plan(K.group_holes(holes), [projection("top")]).views
    assert [o.value for o in view.ordinates if o.axis == "x"] == [0.0, 10.0, 90.0]
    assert [o.value for o in view.ordinates if o.axis == "y"] == [0.0, 10.0, 40.0]
    assert {o.anchor for o in view.ordinates if o.axis == "x" and o.value == 10.0} == {(10.0, 10.0)}  # the hole nearest the datum edge


def test_a_hole_on_the_datum_edge_does_not_repeat_the_zero():
    (view,) = K.plan(K.group_holes([hole(1, (0.0, 20.0, 20.0))]), [projection("top")]).views
    assert [o.value for o in view.ordinates if o.axis == "x"] == [0.0]


def test_too_many_coordinates_give_no_ordinates_and_a_warning():
    holes = [hole(i + 1, (3.0 + 5.0 * i, 25.0, 20.0), d=2.0) for i in range(K.MAX_ORDINATES + 1)]
    plan = K.plan(K.group_holes(holes), [projection("top", box=(200.0, 50.0, 20.0))])
    (view,) = plan.views
    assert [o.axis for o in view.ordinates] == ["y", "y"]  # the y coordinates are still two: 0 and 25
    assert any("17 different x coordinates" in w for w in plan.warnings)


def test_more_groups_than_a_view_carries_are_listed_and_not_drawn():
    holes = [hole(i + 1, (5.0 + 10.0 * i, 25.0, 20.0), d=2.0 + i) for i in range(K.MAX_GROUPS_PER_VIEW + 2)]
    plan = K.plan(K.group_holes(holes), [projection("top", box=(200.0, 50.0, 20.0))])
    assert len(plan.views[0].callouts) == K.MAX_GROUPS_PER_VIEW and len(plan.placed) == K.MAX_GROUPS_PER_VIEW
    assert any("more than the 6" in w and "Ø8 THRU" in w and "Ø9 THRU" in w for w in plan.warnings)


def test_no_holes_no_plan():
    plan = K.plan([], [projection("top")])
    assert plan.views == () and plan.warnings == ()
