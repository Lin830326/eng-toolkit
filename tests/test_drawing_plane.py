"""Plane geometry used by the drawing readers: arcs, bulges, hulls, affine maps, clustering.  Pure, hand-calculated."""

import math
import time

import pytest

from eng_toolkit.domains.drawing import plane as P


def test_a_quarter_arc_has_its_ends_and_the_axis_extreme_it_passes():
    pts = P.arc_points(0, 0, 10, 0, 90)
    assert pts[0] == pytest.approx((10, 0)) and pts[-1] == pytest.approx((0, 10), abs=1e-9)
    box = P.box_of(pts)
    assert box == pytest.approx((0, 0, 10, 10), abs=1e-9)


def test_an_arc_from_45_to_135_degrees_reaches_the_top_but_not_the_sides():
    box = P.box_of(P.arc_points(0, 0, 10, 45, 90))
    assert box[3] == pytest.approx(10.0) and box[0] == pytest.approx(-10 * math.cos(math.radians(45))) and box[2] == pytest.approx(10 * math.cos(math.radians(45)))


def test_a_clockwise_sweep_passes_the_extremes_too():
    box = P.box_of(P.arc_points(0, 0, 10, 45, -90))
    assert box[2] == pytest.approx(10.0) and box[1] == pytest.approx(-10 * math.sin(math.radians(45)))


def test_a_positive_bulge_is_a_counterclockwise_arc_a_semicircle_for_one():
    # from (0, 0) to (2, 0) counterclockwise about the centre (1, 0) passes through (1, -1): the arc is to the right of the chord direction
    length, pts = P.bulge_segment((0, 0), (2, 0), 1.0)
    assert length == pytest.approx(math.pi)
    assert P.box_of(pts) == pytest.approx((0, -1, 2, 0), abs=1e-9)


def test_a_negative_bulge_curves_the_other_way_with_the_same_length():
    length, pts = P.bulge_segment((0, 0), (2, 0), -1.0)
    assert length == pytest.approx(math.pi)
    assert P.box_of(pts) == pytest.approx((0, 0, 2, 1), abs=1e-9)


def test_a_bulge_of_tan_one_eighth_of_a_turn_is_a_quarter_circle():
    bulge = math.tan(math.radians(90) / 4)
    length, _ = P.bulge_segment((0, 0), (math.sqrt(2), 0), bulge)  # chord of a quarter circle of radius 1
    assert length == pytest.approx(math.pi / 2)


def test_a_closed_square_polyline_has_perimeter_four_sides():
    length, pts = P.polyline([(0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0)], closed=True)
    assert length == pytest.approx(16.0) and P.box_of(pts) == (0, 0, 4, 4)


def test_the_hull_of_a_square_with_interior_points_is_its_corners():
    hull = P.hull([(0, 0), (4, 0), (4, 4), (0, 4), (2, 2), (1, 3), (2, 0)])
    assert sorted(hull) == [(0, 0), (0, 4), (4, 0), (4, 4)]


def test_an_insert_map_scales_rotates_then_moves_and_honours_the_block_base_point():
    m = P.compose((2.0, 3.0), 90.0, (10.0, 20.0), base=(1.0, 1.0))
    x, y = P.apply(m, (2.0, 1.0))  # (1, 0) from the base point -> scaled (2, 0) -> rotated 90 degrees (0, 2) -> moved
    assert (x, y) == pytest.approx((10.0, 22.0))


def test_boxes_that_touch_or_are_within_the_gap_are_one_cluster_and_chains_join():
    boxes = [(0, 0, 10, 10), (12, 0, 20, 10), (22, 0, 30, 10), (100, 0, 110, 10)]
    groups = P.cluster(boxes, gap=3.0)
    assert sorted(sorted(members) for _, members in groups) == [[0, 1, 2], [3]]
    far = P.cluster(boxes, gap=0.5)
    assert len(far) == 4 or len(far) == 3


def test_a_late_box_can_join_two_clusters_that_were_apart():
    boxes = [(0, 0, 10, 10), (30, 0, 40, 10), (10, 0, 30, 10)]
    groups = P.cluster(boxes, gap=0.0)
    assert len(groups) == 1 and sorted(groups[0][1]) == [0, 1, 2]


def test_too_many_separate_groups_is_none():
    boxes = [(i * 100, 0, i * 100 + 1, 1) for i in range(10)]
    assert P.cluster(boxes, gap=1.0, max_clusters=5) is None


def test_a_hundred_thousand_boxes_in_a_chain_cluster_in_linear_time():
    """Merging used to copy the growing member list for every box: 200 000 lines took 72 s instead of 1."""
    boxes = [(i, 0.0, i + 1.5, 1.0) for i in range(100_000)]
    started = time.time()
    groups = P.cluster(boxes, gap=0.0)
    assert time.time() - started < 5.0
    assert len(groups) == 1 and len(groups[0][1]) == 100_000
