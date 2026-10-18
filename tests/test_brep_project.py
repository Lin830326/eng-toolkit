"""brep.project: exact hidden-line projection checked against hand calculations.

Lengths are millimetres, view coordinates (u, v) = (p . x_axis, p . y_axis).  The frame tests are pure; everything
else needs the optional cad engine and skips without it.
"""

import math

import numpy as np
import pytest

from eng_toolkit import brep
from eng_toolkit.errors import ToolFailure

try:
    import OCP  # noqa: F401

    HAVE_OCP = True
except (ImportError, OSError):
    HAVE_OCP = False

needs_ocp = pytest.mark.skipif(not HAVE_OCP, reason="the OCP engine is not installed; run: uv sync --extra cad")

TOL = 1e-3
CHORD_TOL = 1e-2  # OCCT returns some fillet silhouettes as general curves, which become polylines within 0.01 mm
S2, S3, S6 = math.sqrt(2), math.sqrt(3), math.sqrt(6)

# (normal toward the viewer, screen-right x_axis, screen-up y_axis = normal x x_axis), worked out by hand
FRAMES = {
    "front": ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
    "back": ((0, 1, 0), (-1, 0, 0), (0, 0, 1)),
    "top": ((0, 0, 1), (1, 0, 0), (0, 1, 0)),
    "bottom": ((0, 0, -1), (1, 0, 0), (0, -1, 0)),
    "right": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "left": ((-1, 0, 0), (0, -1, 0), (0, 0, 1)),
    "iso": ((1 / S3, -1 / S3, 1 / S3), (1 / S2, 1 / S2, 0), (-1 / S6, 1 / S6, 2 / S6)),
}
VIEWS = tuple(FRAMES)


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


# --------------------------------------------------------------------------- frames (pure)


def test_the_frame_table_names_the_seven_standard_views():
    assert set(brep.VIEW_FRAMES) == set(VIEWS)


@pytest.mark.parametrize("view", VIEWS)
def test_every_frame_is_a_right_handed_orthonormal_triple(view):
    frame = brep.VIEW_FRAMES[view]
    n, x, y = frame.normal, frame.x_axis, frame.y_axis
    for vector in (n, x, y):
        assert dot(vector, vector) == pytest.approx(1.0, abs=1e-12)
    assert dot(n, x) == pytest.approx(0.0, abs=1e-12)
    assert dot(n, y) == pytest.approx(0.0, abs=1e-12)
    assert dot(x, y) == pytest.approx(0.0, abs=1e-12)
    assert cross(x, y) == pytest.approx(n, abs=1e-12)  # x to y is counter-clockwise seen from the viewer
    assert cross(n, x) == pytest.approx(y, abs=1e-12)


@pytest.mark.parametrize("view", VIEWS)
def test_the_frames_match_the_documented_table(view):
    n, x, y = FRAMES[view]
    frame = brep.VIEW_FRAMES[view]
    for actual, expected in ((frame.normal, n), (frame.x_axis, x), (frame.y_axis, y)):
        assert actual == pytest.approx(expected, abs=1e-12)


def test_an_unknown_view_is_input_invalid():
    with pytest.raises(ToolFailure) as info:
        brep.project(_unused_solid(), "side")
    assert info.value.family == "input.invalid"
    assert "front" in info.value.message


def _unused_solid():
    return brep.Solid(_handle=None, solids=0, faces=0)


# --------------------------------------------------------------------------- helpers


def lines(prims):
    return [p for p in prims if isinstance(p, brep.Line2)]


def arcs(prims):
    return [p for p in prims if isinstance(p, brep.Arc2)]


def length(prim) -> float:
    if isinstance(prim, brep.Line2):
        return math.dist(prim.a, prim.b)
    if isinstance(prim, brep.Arc2):
        return prim.radius * (prim.end_rad - prim.start_rad)
    if isinstance(prim, brep.Poly2):
        return sum(math.dist(p, q) for p, q in zip(prim.points, prim.points[1:]))
    # ellipse: a polyline of 20000 chords is exact to 1e-8 relative
    a = math.hypot(*prim.major_axis)
    b = a * prim.ratio
    steps = 20000
    ts = [prim.start_rad + (prim.end_rad - prim.start_rad) * i / steps for i in range(steps + 1)]
    pts = [(a * math.cos(t), b * math.sin(t)) for t in ts]
    return sum(math.dist(p, q) for p, q in zip(pts, pts[1:]))


def total(prims) -> float:
    return sum(length(p) for p in prims)


def has_segment(prims, a, b, tol=1e-6) -> bool:
    for p in lines(prims):
        if (math.dist(p.a, a) <= tol and math.dist(p.b, b) <= tol) or (math.dist(p.a, b) <= tol and math.dist(p.b, a) <= tol):
            return True
    return False


def box_solid(size=(100, 50, 20)):
    return brep.make_box(size, (0, 0, 0))


def cut(base, tool):
    return brep.combine(base, tool, "cut")


def probe_part():
    """100 x 50 x 20 box, a vertical diameter-10 hole through it at (25, 25), R2 fillets on the 4 outer vertical edges."""
    drilled = cut(box_solid(), brep.make_hole_tool([(25, 25, 20)], (0, 0, -1), 10.0, None, None, None, 200.0))
    return brep.fillet(drilled, brep.parse_edge_selector("|z & convex"), 2.0)[0]


def side_hole_part():
    """The box with a diameter-10 hole through it along y at (x, z) = (50, 10), entering the front face (y = 0)."""
    return cut(box_solid(), brep.make_hole_tool([(50, 0, 10)], (0, 1, 0), 10.0, None, None, None, 200.0))


def rotation_z(degrees: float):
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def turned_cylinder(degrees: float):
    return brep.transform(brep.make_cylinder(30.0, 40.0, (0, 0, 0), (0, 0, 1)), rotation_z(degrees), (0, 0, 0))


# --------------------------------------------------------------------------- the box: hand values for every view

# view: (visible length, bbox min, bbox max); rectangles 100 x 20, 100 x 50 and 50 x 20 of the box [0,100] x [0,50] x [0,20]
BOX_VIEWS = {
    "front": (2 * (100 + 20), (0, 0), (100, 20)),  # u = x, v = z
    "back": (2 * (100 + 20), (-100, 0), (0, 20)),  # u = -x, v = z
    "top": (2 * (100 + 50), (0, 0), (100, 50)),  # u = x, v = y
    "bottom": (2 * (100 + 50), (0, -50), (100, 0)),  # u = x, v = -y
    "right": (2 * (50 + 20), (0, 0), (50, 20)),  # u = y, v = z
    "left": (2 * (50 + 20), (-50, 0), (0, 20)),  # u = -y, v = z
}


@needs_ocp
@pytest.mark.parametrize("view", list(BOX_VIEWS))
def test_box_view_is_its_rectangle_and_nothing_hidden(view):
    visible, lo, hi = BOX_VIEWS[view]
    projection = brep.project(box_solid(), view)
    assert projection.view == view
    assert projection.method == "exact"
    assert total(projection.visible) == pytest.approx(visible, abs=TOL)
    assert len(projection.visible) == 4 and len(lines(projection.visible)) == 4
    assert projection.hidden == ()
    assert projection.bbox_min == pytest.approx(lo, abs=TOL)
    assert projection.bbox_max == pytest.approx(hi, abs=TOL)
    assert projection.dropped["hidden_coincident"] > 0  # the far edges lie exactly under the near ones
    assert projection.dropped["tangent"] == 0 and projection.dropped["seam"] == 0
    assert projection.approximated == 0


@needs_ocp
@pytest.mark.parametrize("view", VIEWS)
def test_the_projection_reports_its_frame(view):
    n, x, y = FRAMES[view]
    projection = brep.project(box_solid(), view)
    assert projection.x_axis == pytest.approx(x, abs=1e-12)
    assert projection.y_axis == pytest.approx(y, abs=1e-12)
    assert projection.direction == pytest.approx(tuple(-c for c in n), abs=1e-12)  # from the eye towards the part


@needs_ocp
def test_without_hidden_lines_only_the_visible_ones_are_returned():
    projection = brep.project(probe_part(), "front", hidden=False)
    assert projection.hidden == ()
    assert total(projection.visible) == pytest.approx(240.0, abs=TOL)


# --------------------------------------------------------------------------- the probe part


@needs_ocp
@pytest.mark.parametrize(("view", "sign"), [("top", 1.0), ("bottom", -1.0)])
def test_probe_part_seen_along_the_hole_is_its_outline_and_the_hole_circle(view, sign):
    # straight sides 2*(100-4) + 2*(50-4) = 284; four R2 quarter arcs 4 * (pi/2 * 2) = 4*pi; hole circle pi*10;
    # 284 + 14*pi = 327.9823.  Everything underneath coincides with a visible edge, so nothing is hidden.
    projection = brep.project(probe_part(), view)
    assert total(projection.visible) == pytest.approx(284 + 14 * math.pi, abs=TOL)
    assert total(projection.visible) == pytest.approx(327.9823, abs=TOL)
    assert projection.hidden == ()
    assert len(projection.visible) == 9
    assert len(lines(projection.visible)) == 4

    spans = sorted(a.end_rad - a.start_rad for a in arcs(projection.visible))
    assert spans == pytest.approx([math.pi / 2] * 4 + [2 * math.pi], abs=1e-9)
    corners = [a for a in arcs(projection.visible) if a.radius == pytest.approx(2.0)]
    circle = [a for a in arcs(projection.visible) if a.radius == pytest.approx(5.0)]
    assert len(corners) == 4 and len(circle) == 1
    assert circle[0].center == pytest.approx((25.0, sign * 25.0), abs=1e-6)
    centres = sorted(a.center for a in corners)
    assert centres == pytest.approx(sorted([(2.0, sign * 2.0), (98.0, sign * 2.0), (98.0, sign * 48.0), (2.0, sign * 48.0)]), abs=1e-6)

    middle = ((projection.bbox_min[0] + projection.bbox_max[0]) / 2, (projection.bbox_min[1] + projection.bbox_max[1]) / 2)
    for corner in corners:  # a corner arc bulges away from the middle of the part, so it was not turned the wrong way round
        mid = (corner.start_rad + corner.end_rad) / 2
        point = (corner.center[0] + 2 * math.cos(mid), corner.center[1] + 2 * math.sin(mid))
        assert math.dist(point, middle) > math.dist(corner.center, middle)


@needs_ocp
def test_probe_part_front_view_hides_the_two_hole_silhouettes():
    # outline 2*(100+20) = 240; the hole (centre x = 25, radius 5) shows as lines at x = 20 and x = 30, each 20 high
    projection = brep.project(probe_part(), "front")
    assert total(projection.visible) == pytest.approx(240.0, abs=TOL)
    assert total(projection.hidden) == pytest.approx(40.0, abs=TOL)
    assert len(projection.hidden) == 2
    assert has_segment(projection.hidden, (20, 0), (20, 20))
    assert has_segment(projection.hidden, (30, 0), (30, 20))
    assert projection.dropped["tangent"] > 0  # the fillets' tangent lines are not drawn
    assert projection.dropped["hidden_coincident"] > 0


@needs_ocp
def test_probe_part_right_view_hides_the_two_hole_silhouettes():
    # outline 2*(50+20) = 140; hole centre y = 25: lines at y = 20 and y = 30 (u = y), each 20 high
    projection = brep.project(probe_part(), "right")
    assert total(projection.visible) == pytest.approx(140.0, abs=TOL)
    assert total(projection.hidden) == pytest.approx(40.0, abs=TOL)
    assert has_segment(projection.hidden, (20, 0), (20, 20))
    assert has_segment(projection.hidden, (30, 0), (30, 20))


# --------------------------------------------------------------------------- a hole along y

# view: (visible length, hidden segments).  The axis of the hole is y at (x, z) = (50, 10), radius 5, through 50 mm of y.
SIDE_HOLE = {
    # looking along y: the rectangle 240 plus the full circle pi*10; the far circle lies exactly under the near one
    "front": (240 + 10 * math.pi, []),
    "back": (240 + 10 * math.pi, []),
    # looking along z: the hole is inside the part, silhouettes at x = 50 +- 5, each as long as the part is deep (50)
    "top": (300.0, [((45, 0), (45, 50)), ((55, 0), (55, 50))]),
    "bottom": (300.0, [((45, 0), (45, -50)), ((55, 0), (55, -50))]),
    # looking along x: silhouettes at z = 10 +- 5 = 5 and 15, running over the depth 0..50 (u = y)
    "right": (140.0, [((0, 5), (50, 5)), ((0, 15), (50, 15))]),
    "left": (140.0, [((0, 5), (-50, 5)), ((0, 15), (-50, 15))]),
}


@needs_ocp
@pytest.mark.parametrize("view", list(SIDE_HOLE))
def test_side_hole_is_a_circle_end_on_and_two_hidden_lines_side_on(view):
    visible, hidden = SIDE_HOLE[view]
    projection = brep.project(side_hole_part(), view)
    assert total(projection.visible) == pytest.approx(visible, abs=TOL)
    assert len(projection.hidden) == len(hidden)
    for a, b in hidden:
        assert has_segment(projection.hidden, a, b)
    assert total(projection.hidden) == pytest.approx(50.0 * len(hidden), abs=TOL)
    if view in ("front", "back"):
        (circle,) = arcs(projection.visible)
        assert circle.radius == pytest.approx(5.0, abs=1e-9)
        assert circle.end_rad - circle.start_rad == pytest.approx(2 * math.pi, abs=1e-9)
        assert circle.center == pytest.approx((50.0 if view == "front" else -50.0, 10.0), abs=1e-6)
        assert length(circle) == pytest.approx(10 * math.pi, abs=TOL)
    else:
        assert arcs(projection.visible) == []


# --------------------------------------------------------------------------- cylinder and sphere


@needs_ocp
@pytest.mark.parametrize("degrees", [0, 30, 90])
def test_cylinder_top_view_is_one_circle(degrees):
    # a circle of diameter 30 seen end on: pi*30 = 94.2478; the bottom circle lies exactly under it
    projection = brep.project(turned_cylinder(degrees), "top")
    (circle,) = projection.visible
    assert isinstance(circle, brep.Arc2)
    assert circle.radius == pytest.approx(15.0, abs=1e-9)
    assert circle.center == pytest.approx((0.0, 0.0), abs=1e-9)
    assert length(circle) == pytest.approx(math.pi * 30, abs=TOL)
    assert length(circle) == pytest.approx(94.2478, abs=TOL)
    assert projection.hidden == ()


@needs_ocp
@pytest.mark.parametrize("degrees", [0, 30, 90, 135, 180, 270])
def test_cylinder_front_view_is_a_rectangle_with_no_seam_line(degrees):
    # two silhouettes 2*40 and the top and bottom circles seen edge on 2*30: 140, whatever the angle of the seam
    projection = brep.project(turned_cylinder(degrees), "front")
    assert total(projection.visible) == pytest.approx(2 * 40 + 2 * 30, abs=TOL)
    assert projection.hidden == ()
    assert len(projection.visible) == 4 and len(lines(projection.visible)) == 4
    assert has_segment(projection.visible, (-15, 0), (-15, 40))
    assert has_segment(projection.visible, (15, 0), (15, 40))
    assert has_segment(projection.visible, (-15, 0), (15, 0))
    assert has_segment(projection.visible, (-15, 40), (15, 40))  # and nothing at u = 0 or between the silhouettes


@needs_ocp
def test_a_seam_behind_the_cylinder_is_counted_and_not_drawn():
    # turned 30 degrees the seam line sits behind the front half (y > 0): HLR reports it as a smooth edge
    projection = brep.project(turned_cylinder(30), "front")
    assert projection.dropped["seam"] >= 1
    assert projection.hidden == ()


@needs_ocp
@pytest.mark.parametrize("view", VIEWS)
def test_sphere_is_one_circle_in_every_view(view):
    # the silhouette of a sphere of diameter 30 is a great circle: pi*30 = 94.2478, nothing hidden
    projection = brep.project(brep.make_sphere(30.0, (0, 0, 0)), view)
    (circle,) = projection.visible
    assert isinstance(circle, brep.Arc2)
    assert circle.radius == pytest.approx(15.0, abs=1e-6)
    assert circle.center == pytest.approx((0.0, 0.0), abs=1e-6)
    assert circle.end_rad - circle.start_rad == pytest.approx(2 * math.pi, abs=1e-9)
    assert length(circle) == pytest.approx(94.2478, abs=TOL)
    assert projection.hidden == ()
    assert projection.bbox_min == pytest.approx((-15.0, -15.0), abs=1e-6)
    assert projection.bbox_max == pytest.approx((15.0, 15.0), abs=1e-6)


# --------------------------------------------------------------------------- curves that are not circles


def tee_part():
    """A vertical cylinder (diameter 20, z 0..40) crossed by a horizontal one (diameter 12, along x through z = 20)."""
    vertical = brep.make_cylinder(20.0, 40.0, (0, 0, 0), (0, 0, 1))
    horizontal = brep.make_cylinder(12.0, 60.0, (-30, 0, 20), (1, 0, 0))
    return brep.combine(vertical, horizontal, "add")


def tee_branch_length() -> float:
    """Front-view length of one branch of the intersection curve.

    The curves meet where x^2 + y^2 = 100 and y^2 + (z - 20)^2 = 36.  With y = 6 sin(f) a branch is
    x = sqrt(100 - 36 sin^2 f), z = 20 +- 6 cos(f) for f from 0 to pi/2, so ds = sqrt(dx^2 + dz^2) with
    dz = 6 sin(f) df and dx = 36 sin(f) cos(f) / sqrt(100 - 36 sin^2 f) df.
    """
    steps = 200_000
    f = (np.arange(steps) + 0.5) * (math.pi / 2 / steps)
    ds = np.sqrt((6 * np.sin(f)) ** 2 + (36 * np.sin(f) * np.cos(f) / np.sqrt(100 - 36 * np.sin(f) ** 2)) ** 2)
    return float(ds.sum() * (math.pi / 2 / steps))


@needs_ocp
def test_the_tee_front_view_has_its_intersection_curves_once_and_nothing_hidden():
    # silhouettes of the vertical cylinder outside the branch: 2 * (14 + 14) = 56; of the branch outside the vertical
    # cylinder: 2 * 2 * 20 = 80, with its two end discs seen edge on 2 * 12 = 24; top and bottom edge on 2 * 20 = 40;
    # the front half of each of the two intersection loops is two branches: 4 * tee_branch_length()
    projection = brep.project(tee_part(), "front")
    expected = 56 + 80 + 24 + 40 + 4 * tee_branch_length()
    assert expected == pytest.approx(225.6163, abs=1e-3)
    assert total(projection.visible) == pytest.approx(expected, abs=0.05)  # the curves are polylines within 0.01 mm
    assert projection.hidden == ()
    assert projection.approximated > 0
    assert any(isinstance(p, brep.Poly2) for p in projection.visible)
    for poly in (p for p in projection.visible if isinstance(p, brep.Poly2)):
        # the vertices are samples of the curve, and y^2 = 100 - x^2 = 36 - (z - 20)^2 gives x^2 - (z - 20)^2 = 64
        assert all(abs(x * x - (z - 20.0) ** 2 - 64.0) < 1e-6 for x, z in poly.points)


@needs_ocp
@pytest.mark.parametrize(("one", "other"), [("front", "back"), ("right", "left")])
def test_a_part_symmetric_about_a_plane_gives_mirror_views(one, other):
    # the tee is symmetric about y = 0 and about x = 0, so opposite views have the same visible and hidden length
    part = tee_part()
    a, b = brep.project(part, one), brep.project(part, other)
    assert total(a.visible) == pytest.approx(total(b.visible), abs=0.05)
    assert total(a.hidden) == pytest.approx(total(b.hidden), abs=0.05)


# --------------------------------------------------------------------------- rigid motion


@needs_ocp
def test_a_turned_and_far_away_part_has_the_same_top_view_lengths():
    # rotated 37 degrees about z and moved by (3000, 4000, 0), that is 5000 mm from the origin: the top view is still
    # 284 + 14*pi = 327.9823 with nothing hidden
    moved = brep.transform(probe_part(), rotation_z(37.0), (3000.0, 4000.0, 0.0))
    projection = brep.project(moved, "top")
    assert total(projection.visible) == pytest.approx(284 + 14 * math.pi, abs=TOL)
    assert projection.hidden == ()
    assert len(arcs(projection.visible)) == 5 and len(lines(projection.visible)) == 4


# --------------------------------------------------------------------------- the iso view

def iso_uv(point):
    """The iso frame worked out by hand: x_axis = (1,1,0)/sqrt(2), y_axis = (-1,1,2)/sqrt(6)."""
    x, y, z = point
    return ((x + y) / S2, (-x + y + 2 * z) / S6)


@needs_ocp
def test_iso_view_of_a_cube_shows_nine_edges_and_hides_the_three_at_the_far_corner():
    # from (1,-1,1) the eye sees the front (y=0), right (x=20) and top (z=20) faces; the far corner is (0,20,0).
    # Each cube edge of length 20 projects to 20 * sqrt(1/2 + 1/6) = 20 * sqrt(2/3) = 16.3299.
    projection = brep.project(brep.make_box((20, 20, 20), (0, 0, 0)), "iso")
    corners = [(x, y, z) for x in (0, 20) for y in (0, 20) for z in (0, 20)]
    edges = [(p, q) for p in corners for q in corners if p < q and sum(a != b for a, b in zip(p, q)) == 1]
    assert len(edges) == 12
    far = (0, 20, 0)
    hidden = [e for e in edges if far in e]
    visible = [e for e in edges if far not in e]
    assert (len(visible), len(hidden)) == (9, 3)

    assert len(lines(projection.visible)) == 9 and len(projection.visible) == 9
    assert len(lines(projection.hidden)) == 3 and len(projection.hidden) == 3
    for p, q in visible:
        assert has_segment(projection.visible, iso_uv(p), iso_uv(q))
    for p, q in hidden:
        assert has_segment(projection.hidden, iso_uv(p), iso_uv(q))
    assert total(projection.visible) == pytest.approx(9 * 20 * math.sqrt(2 / 3), abs=TOL)
    assert total(projection.hidden) == pytest.approx(3 * 20 * math.sqrt(2 / 3), abs=TOL)


# --------------------------------------------------------------------------- determinism, accounting, fallback


@needs_ocp
@pytest.mark.parametrize("view", VIEWS)
def test_the_same_shape_and_view_give_the_same_primitives_in_the_same_order(view):
    part = probe_part()
    first = brep.project(part, view)
    second = brep.project(part, view)
    again = brep.project(probe_part(), view)  # a rebuilt copy of the part, not the same handle
    assert first == second
    assert first.visible == again.visible and first.hidden == again.hidden


@needs_ocp
def test_dropped_counts_are_reported_under_stable_keys():
    projection = brep.project(probe_part(), "front")
    assert set(projection.dropped) == {"hidden_coincident", "tangent", "seam", "verified_hidden", "verified_removed"}
    assert all(isinstance(count, int) and count >= 0 for count in projection.dropped.values())


@needs_ocp
def test_more_faces_than_the_limit_fall_back_to_the_polygonal_algorithm():
    # a box has only straight edges, so the tessellation loses nothing: the same 240 mm in the front view
    exact = brep.project(box_solid(), "front")
    fallback = brep.project(box_solid(), "front", exact_face_limit=1)
    assert exact.method == "exact"
    assert fallback.method == "polygonal"
    assert total(fallback.visible) == pytest.approx(total(exact.visible), abs=TOL)
    assert total(fallback.visible) == pytest.approx(240.0, abs=TOL)
    assert fallback.hidden == ()
    assert all(isinstance(p, brep.Line2) for p in fallback.visible)
    assert fallback.approximated == 0


@needs_ocp
def test_a_part_at_the_face_limit_still_uses_the_exact_algorithm():
    assert brep.project(box_solid(), "front", exact_face_limit=6).method == "exact"


# --------------------------------------------------------------------------- errors


@needs_ocp
def test_an_empty_compound_has_no_geometry():
    from cad_fixtures import compound

    with pytest.raises(ToolFailure) as info:
        brep.project(brep.Solid(_handle=compound(), solids=0, faces=0), "front")
    assert info.value.family == "drawing.no_geometry"


@needs_ocp
def test_a_shape_with_edges_but_no_faces_has_no_geometry():
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.gp import gp_Pnt

    edge = BRepBuilderAPI_MakeEdge(gp_Pnt(0, 0, 0), gp_Pnt(10, 0, 0)).Edge()
    with pytest.raises(ToolFailure) as info:
        brep.project(brep.Solid(_handle=edge, solids=0, faces=0), "top")
    assert info.value.family == "drawing.no_geometry"


@needs_ocp
def test_a_single_planar_face_projects_to_its_outline_and_to_a_segment_edge_on():
    # one face of 100 x 50 in the plane z = 0 has no volume and nothing to hide: seen from above it is the rectangle
    # 2*(100+50) = 300; seen from the front it is edge on and its four edges collapse into one segment of length 100
    from cad_fixtures import planar_shell

    face = brep.Solid(_handle=planar_shell(), solids=0, faces=1)
    top = brep.project(face, "top")
    assert total(top.visible) == pytest.approx(300.0, abs=TOL) and len(lines(top.visible)) == 4
    assert top.hidden == ()
    front = brep.project(face, "front")
    assert front.visible == (brep.Line2((0.0, 0.0), (100.0, 0.0)),)
    assert front.hidden == ()


@needs_ocp
def test_a_kernel_failure_is_a_projection_failure_naming_the_view(monkeypatch):
    from eng_toolkit.brep import _project

    def broken(*args, **kwargs):
        raise RuntimeError("Standard_Failure: the algorithm gave up")

    monkeypatch.setattr(_project, "_run_hlr", broken)
    with pytest.raises(ToolFailure) as info:
        brep.project(box_solid(), "right")
    assert info.value.family == "drawing.projection_failed"
    assert "right" in info.value.message and info.value.details["view"] == "right"
    assert "gave up" in info.value.details["occt"]


# --------------------------------------------------------------------------- a known fault of the exact algorithm, verified away


def filleted_hole_plate():
    """A 60 x 40 x 15 plate with a diameter-5 hole at (20, 20) and R0.5 fillets on both rims of the hole."""
    drilled = cut(brep.make_box((60, 40, 15), (0, 0, 0)), brep.make_hole_tool([(20, 20, 15)], (0, 0, -1), 5.0, None, None, None, 100.0))
    return brep.fillet(drilled, brep.parse_edge_selector("circle"), 0.5)[0]


@pytest.fixture(scope="module")
def grid_plate():
    """A 120 x 80 x 15 plate with 12 x 8 = 96 diameter-5 holes at (5 + 10 i, 5 + 10 j), R0.5 fillets on both rims of each."""
    points = [(5 + 10 * i, 5 + 10 * j, 15) for i in range(12) for j in range(8)]
    drilled = cut(brep.make_box((120, 80, 15), (0, 0, 0)), brep.make_hole_tool(points, (0, 0, -1), 5.0, None, None, None, 100.0))
    return brep.fillet(drilled, brep.parse_edge_selector("circle"), 0.5)[0]


# OCCT's exact HLR returns the silhouette of a hole-rim fillet on the far wall as visible although the plate hides it
# (front view of the 60 x 40 x 15 plate: visible 151.563, right view 113.127).  The polygonal visibility check moves it
# to the hidden lines.
#
# Hand values.  Front view (u = x, v = z): outline 2 * (60 + 15) = 150.  The hole wall shows as two lines at
# x = 20 -+ 2.5, from z = 0.5 to 14.5 (2 * 14 = 28), and each of the two rim fillets as a quarter circle of radius 0.5 on
# each of the two walls (2 * 2 * (pi/2 * 0.5) = pi): hidden 28 + pi.  The arcs are exact primitives, so the sum is exact.
# Right view (u = y): the same with the hole at y = 20: visible 2 * (40 + 15) = 110, hidden 28 + pi.
# Top view: outline 2 * (60 + 40) = 200 plus the wall circle 2 * pi * 2.5 = 5 pi; nothing hidden.
@needs_ocp
def test_the_exact_projection_hides_the_hole_rim_fillets_behind_the_front_face():
    projection = brep.project(filleted_hole_plate(), "front")
    assert projection.method == "exact" and projection.verified is True
    assert total(projection.visible) == pytest.approx(150.0, abs=TOL)
    assert total(projection.hidden) == pytest.approx(28 + math.pi, abs=CHORD_TOL)
    assert projection.dropped["verified_hidden"] == 2  # one visible curve per rim on the +x wall
    assert projection.dropped["verified_removed"] == 0
    assert len(projection.hidden) == 6 and len(lines(projection.hidden)) == 2  # the two wall lines and the four quarter circles
    assert all(isinstance(p, brep.Line2) for p in projection.visible) and len(projection.visible) == 4


@needs_ocp
def test_the_right_view_hides_the_hole_rim_fillets_too():
    projection = brep.project(filleted_hole_plate(), "right")
    assert projection.method == "exact" and projection.verified is True
    assert total(projection.visible) == pytest.approx(110.0, abs=TOL)
    assert total(projection.hidden) == pytest.approx(28 + math.pi, abs=2 * CHORD_TOL)  # all four quarter circles are polylines here: 0.015 short
    assert projection.dropped["verified_hidden"] == 4 and projection.dropped["verified_removed"] == 0


@needs_ocp
def test_without_hidden_lines_the_corrected_pieces_are_counted_and_not_returned():
    projection = brep.project(filleted_hole_plate(), "front", hidden=False)
    assert projection.hidden == ()
    assert total(projection.visible) == pytest.approx(150.0, abs=TOL)
    assert projection.dropped["verified_hidden"] == 2


@needs_ocp
def test_the_top_view_of_the_filleted_hole_plate_is_not_touched_by_the_check():
    projection = brep.project(filleted_hole_plate(), "top")
    assert projection.method == "exact" and projection.verified is True
    assert total(projection.visible) == pytest.approx(200 + 5 * math.pi, abs=TOL)
    assert projection.hidden == ()
    assert projection.dropped["verified_hidden"] == 0 and projection.dropped["verified_removed"] == 0


@needs_ocp
def test_the_checked_projection_is_the_same_on_every_call():
    part = filleted_hole_plate()
    first, second = brep.project(part, "front"), brep.project(part, "front")
    assert first == second
    assert brep.project(filleted_hole_plate(), "front").hidden == first.hidden


# Front view of the grid plate: outline 2 * (120 + 15) = 270 and nothing else is visible.  Hidden: the 12 hole columns
# (the 8 holes of a column lie behind each other) each give 28 + pi, 12 * (28 + pi) = 373.699.
# Right view: outline 2 * (80 + 15) = 190; hidden: the 8 hole rows, 8 * (28 + pi) = 249.133.
# Top view: outline 2 * (120 + 80) = 400 plus 96 wall circles 5 pi each; nothing hidden.
@needs_ocp
@pytest.mark.parametrize(
    ("view", "visible", "hidden"),
    [
        ("front", 2 * (120 + 15), 12 * (28 + math.pi)),
        ("right", 2 * (80 + 15), 8 * (28 + math.pi)),
        ("top", 2 * (120 + 80) + 96 * 5 * math.pi, 0.0),
    ],
)
def test_the_grid_plate_shows_its_outline_and_the_hole_circles_and_nothing_more(grid_plate, view, visible, hidden):
    projection = brep.project(grid_plate, view)
    assert projection.method == "exact" and projection.verified is True
    assert total(projection.visible) == pytest.approx(visible, abs=TOL)
    assert total(projection.hidden) == pytest.approx(hidden, rel=1e-3)  # the quarter circles are polylines, 0.5 percent short
    assert projection.dropped["verified_removed"] == 0


@needs_ocp
def test_the_polygonal_algorithm_hides_the_hole_rim_fillets_behind_the_front_face():
    projection = brep.project(filleted_hole_plate(), "front", exact_face_limit=1)
    assert projection.method == "polygonal"
    assert total(projection.visible) == pytest.approx(150.0, abs=TOL)
    assert total(projection.hidden) == pytest.approx(28 + math.pi, abs=0.05)  # the quarter circles are chords


@needs_ocp
def test_collinear_edges_of_two_touching_solids_are_merged_into_single_lines():
    # two boxes 50 x 50 x 20 side by side (not fused): seen from the front the top and bottom edges are two pieces
    # each, and the shared edge at x = 50 is there twice.  One line each of 100, 100, 20, 20, 20 remain: 260.
    from cad_fixtures import box, compound

    solid = brep.Solid(_handle=compound(box(0, 0, 0, 50, 50, 20), box(50, 0, 0, 50, 50, 20)), solids=2, faces=12)
    projection = brep.project(solid, "front")
    assert total(projection.visible) == pytest.approx(260.0, abs=TOL)
    assert len(projection.visible) == 5
    assert has_segment(projection.visible, (0, 0), (100, 0)) and has_segment(projection.visible, (0, 20), (100, 20))
    assert has_segment(projection.visible, (50, 0), (50, 20))
    assert projection.hidden == ()


# --------------------------------------------------------------------------- the visibility check, flags and safeguards


@needs_ocp
def test_an_exact_projection_is_checked_and_a_polygonal_one_is_not():
    checked_box = brep.project(box_solid(), "front")
    assert checked_box.method == "exact" and checked_box.verified is True
    assert checked_box.dropped["verified_hidden"] == 0 and checked_box.dropped["verified_removed"] == 0
    fallback = brep.project(box_solid(), "front", exact_face_limit=1)
    assert fallback.method == "polygonal" and fallback.verified is False
    assert fallback.dropped["verified_hidden"] == 0 and fallback.dropped["verified_removed"] == 0


@needs_ocp
def test_an_oracle_that_fails_leaves_the_exact_result_marked_unverified(monkeypatch):
    from eng_toolkit.brep import _project

    plain = brep.project(probe_part(), "front")

    def broken(*args, **kwargs):
        raise RuntimeError("the tessellation failed")

    monkeypatch.setattr(_project, "_oracle", broken)
    unchecked = brep.project(probe_part(), "front")
    assert unchecked.method == "exact_unverified" and unchecked.verified is False
    assert unchecked.visible == plain.visible and unchecked.hidden == plain.hidden


def _fake_oracle(monkeypatch, visible_segments, tolerance=0.02):
    from eng_toolkit.brep import _project

    empty = np.empty((0, 4))
    rows = np.array(visible_segments, dtype=float).reshape(-1, 4)
    monkeypatch.setattr(_project, "_oracle", lambda shape, frame: _project._Oracle(tolerance, rows, lambda: empty))


@needs_ocp
def test_an_oracle_that_sees_nothing_visible_would_drop_everything_so_the_exact_result_is_kept(monkeypatch):
    plain = brep.project(box_solid(), "front")
    _fake_oracle(monkeypatch, [])
    kept = brep.project(box_solid(), "front")
    assert kept.method == "exact_unverified" and kept.verified is False
    assert kept.visible == plain.visible
    assert kept.dropped["verified_hidden"] == 0 and kept.dropped["verified_removed"] == 0


@needs_ocp
def test_an_oracle_with_visible_lines_the_exact_result_lacks_leaves_it_unverified(monkeypatch):
    # the rectangle of the box front view is covered; a 30 mm line next to it is 30 / 270 = 11 percent of the polygonal length
    rectangle = [(0, 0, 100, 0), (100, 0, 100, 20), (100, 20, 0, 20), (0, 20, 0, 0)]
    _fake_oracle(monkeypatch, rectangle)
    covered = brep.project(box_solid(), "front")
    assert covered.method == "exact" and covered.verified is True
    _fake_oracle(monkeypatch, [*rectangle, (200, 0, 230, 0)])
    result = brep.project(box_solid(), "front")
    assert result.method == "exact_unverified" and result.verified is False
    assert total(result.visible) == pytest.approx(240.0, abs=TOL)


@needs_ocp
def test_a_little_extra_polygonal_length_is_tolerated(monkeypatch):
    # 2 mm next to 240 mm is 0.8 percent, under the 2 percent limit
    rectangle = [(0, 0, 100, 0), (100, 0, 100, 20), (100, 20, 0, 20), (0, 20, 0, 0)]
    _fake_oracle(monkeypatch, [*rectangle, (200, 0, 202, 0)])
    result = brep.project(box_solid(), "front")
    assert result.method == "exact" and result.verified is True


# --------------------------------------------------------------------------- the coverage test on hand-built polylines (pure)


def chords(centre, radius, start, end, count):
    """Straight segments (x0, y0, x1, y1) of a counter-clockwise arc, with the vertices on the circle."""
    t = np.linspace(start, end, count + 1)
    xs, ys = centre[0] + radius * np.cos(t), centre[1] + radius * np.sin(t)
    return np.column_stack((xs[:-1], ys[:-1], xs[1:], ys[1:]))


def index(*segments, reach=0.1):
    from eng_toolkit.brep import _project

    rows = np.array([s for group in segments for s in np.atleast_2d(group)], dtype=float).reshape(-1, 4)
    return _project._Segments(rows, reach)


def checked(prim, visible, hidden=(), tolerance=0.02, sampled=False):
    from eng_toolkit.brep import _project

    return _project._check(prim, visible, lambda: index(*hidden), tolerance, sampled)


@pytest.mark.parametrize("length", [0.0, 0.3, 4.0, 9.9, 10.0, 10.1, 120.0, 1000.0])
def test_a_primitive_is_sampled_at_least_five_times_and_every_two_millimetres_with_its_midpoint(length):
    from eng_toolkit.brep import _project

    t = _project._fractions(length)
    assert len(t) >= 5 and len(t) % 2 == 1
    assert 0.0 < t[0] and t[-1] < 1.0  # the end points are not sampled
    assert t[len(t) // 2] == pytest.approx(0.5, abs=1e-12)  # the midpoint is
    assert np.diff(t).max() * length <= 2.0 + 1e-9


def test_the_segment_index_agrees_with_a_brute_force_distance():
    rng = np.random.default_rng(7)
    segments = rng.uniform(-20, 20, size=(300, 4))
    segments[::3, 2:] = segments[::3, :2] + rng.uniform(-0.4, 0.4, size=(100, 2))  # short ones and
    segments[1::50, 2:] = segments[1::50, :2] + rng.uniform(-9, 9, size=(6, 2))  # a few long ones
    points = rng.uniform(-22, 22, size=(2000, 2))
    allowed = 0.35
    found = index(segments, reach=0.5).within(points, allowed)

    a, b = segments[:, None, :2], segments[:, None, 2:]
    ab = b - a
    t = np.clip(((points[None] - a) * ab).sum(2) / np.maximum((ab * ab).sum(2), 1e-30), 0, 1)
    nearest = np.linalg.norm(points[None] - (a + t[..., None] * ab), axis=2).min(axis=0)
    # points within 1e-9 of the threshold may go either way
    assert not (found & (nearest > allowed + 1e-9)).any()
    assert not (~found & (nearest < allowed - 1e-9)).any()
    assert found.any() and not found.all()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("11111", "11111"),
        ("00000", "00000"),
        ("11011", "11111"),  # one point off a line
        ("00100", "00000"),  # one point on a line
        ("00001", "00000"),  # at an end too
        ("10000", "00000"),
        ("11101", "11111"),
        ("11010", "11111"),  # lone runs are absorbed first to last, and the merged neighbours are not lone any more
        ("10101", "00000"),  # noise all through: the first sample decides, the same way every time
        ("11000", "11000"),
        ("00111", "00111"),
        ("1100110", "1100111"),
    ],
)
def test_a_single_sample_takes_the_value_of_its_neighbours(raw, expected):
    from eng_toolkit.brep import _project

    seen = np.array([c == "1" for c in raw])
    assert "".join("1" if v else "0" for v in _project._denoise(seen)) == expected
    assert seen.tolist() == [c == "1" for c in raw]  # the input is not changed


def test_the_segment_index_of_nothing_covers_nothing():
    assert not index(np.empty((0, 4))).within(np.array([[0.0, 0.0]]), 0.1).any()


def test_a_primitive_on_a_visible_polyline_is_kept_as_it_is():
    line = brep.Line2((2.0, 0.0), (8.0, 0.0))
    result = checked(line, index((0, 0, 10, 0)))
    assert result.kept == [line] and result.moved == [] and result.dropped == []


def test_a_primitive_within_the_tolerance_of_the_polyline_is_covered_and_one_beyond_it_is_not():
    visible = index((0, 0, 10, 0))
    near = checked(brep.Line2((0.0, 0.015), (10.0, 0.015)), visible, tolerance=0.02)
    assert near.moved == [] and near.dropped == []
    far = checked(brep.Line2((0.0, 0.03), (10.0, 0.03)), visible, tolerance=0.02)
    assert far.kept == [] and len(far.dropped) == 1


def test_a_primitive_away_from_every_polyline_is_dropped():
    line = brep.Line2((0.0, 5.0), (10.0, 5.0))
    result = checked(line, index((0, 0, 10, 0)), hidden=[(0, 1, 10, 1)])
    assert result.kept == [] and result.moved == [] and result.dropped == [line]


def test_a_primitive_under_the_hidden_polylines_moves_to_hidden_as_itself():
    line = brep.Line2((0.0, 1.0), (10.0, 1.0))
    result = checked(line, index((0, 0, 10, 0)), hidden=[(0, 1, 10, 1)])
    assert result.kept == [] and result.moved == [line] and result.dropped == []


def test_a_line_that_runs_past_the_visible_polyline_is_split_where_the_cover_ends():
    line = brep.Line2((0.0, 0.0), (20.0, 0.0))
    result = checked(line, index((0, 0, 10, 0)), hidden=[(10, 0, 20, 0)], tolerance=0.02)
    (kept,), (moved,) = result.kept, result.moved
    assert result.dropped == []
    assert kept.a == (0.0, 0.0) and moved.b == (20.0, 0.0)
    assert kept.b == moved.a  # one cut, shared
    assert 10.0 <= kept.b[0] <= 10.0 + 0.02 + 0.05  # the first point further than the tolerance from the visible end, to the bisection step
    assert kept.b[1] == 0.0 and moved.a[1] == 0.0


def test_the_uncovered_rest_of_a_line_is_dropped_when_nothing_hidden_lies_there():
    line = brep.Line2((0.0, 0.0), (20.0, 0.0))
    result = checked(line, index((0, 0, 10, 0)))
    assert len(result.kept) == 1 and result.moved == [] and len(result.dropped) == 1
    assert result.dropped[0].b == (20.0, 0.0)


def test_a_middle_stretch_cut_out_leaves_two_kept_pieces():
    line = brep.Line2((0.0, 0.0), (30.0, 0.0))
    result = checked(line, index((0, 0, 10, 0), (20, 0, 30, 0)), hidden=[(10, 0, 20, 0)])
    assert len(result.kept) == 2 and len(result.moved) == 1 and result.dropped == []
    assert result.moved[0].a[0] == pytest.approx(10.0, abs=0.1) and result.moved[0].b[0] == pytest.approx(20.0, abs=0.1)
    assert sum(math.dist(p.a, p.b) for p in (*result.kept, *result.moved)) == pytest.approx(30.0, abs=1e-6)


def test_a_circle_half_under_the_visible_lines_gives_an_arc_to_hide_and_the_rest_kept():
    circle = brep.Arc2((0.0, 0.0), 5.0, 0.0, 2 * math.pi)
    right, left = chords((0, 0), 5.0, -math.pi / 2, math.pi / 2, 80), chords((0, 0), 5.0, math.pi / 2, 3 * math.pi / 2, 80)
    result = checked(circle, index(right), hidden=[left])
    (hide,) = result.moved
    assert isinstance(hide, brep.Arc2) and result.dropped == []
    assert hide.start_rad == pytest.approx(math.pi / 2, abs=0.02) and hide.end_rad == pytest.approx(3 * math.pi / 2, abs=0.02)
    assert hide.center == (0.0, 0.0) and hide.radius == 5.0
    assert sum(p.radius * (p.end_rad - p.start_rad) for p in (*result.kept, hide)) == pytest.approx(10 * math.pi, abs=1e-6)
    assert len(result.kept) == 2  # the two ends of the half that stays, either side of angle 0


def test_a_fillet_arc_that_ends_tangent_on_a_visible_line_is_hidden_whole_and_leaves_no_stub():
    # R0.5 quarter circle from (23, 15) down to (22.5, 14.5), centre (23, 14.5): its last sample is 0.006 below the top
    # edge, within the tolerance, but one covered sample is no evidence that the arc is visible
    arc = brep.Arc2((23.0, 14.5), 0.5, math.pi / 2, math.pi)
    result = checked(arc, index((0, 15, 60, 15)), hidden=[chords((23, 14.5), 0.5, math.pi / 2, math.pi, 8)], tolerance=0.02)
    assert result.kept == [] and result.dropped == []
    assert result.moved == [arc]


def test_one_stray_sample_beyond_the_tolerance_does_not_cut_a_visible_line():
    visible = index((0, 0, 9, 0), (9, 0.05, 9.4, 0.05), (9.4, 0, 20, 0))  # a 0.05 mm jog in the polyline
    line = brep.Line2((0.0, 0.0), (20.0, 0.0))
    result = checked(line, visible, tolerance=0.02)
    assert result.kept == [line] and result.moved == [] and result.dropped == []


def test_a_sampled_curve_is_allowed_its_own_deflection_on_top_of_the_tolerance():
    poly = brep.Poly2(((0.0, 0.03), (5.0, 0.03), (10.0, 0.03)))
    visible = index((0, 0, 10, 0))
    assert checked(poly, visible, tolerance=0.02, sampled=False).dropped != []
    assert checked(poly, visible, tolerance=0.02, sampled=True).dropped == []  # CURVE_DEFLECTION_MM = 0.01 more


def test_a_polyline_is_cut_at_a_point_on_its_path():
    poly = brep.Poly2(((0.0, 0.0), (10.0, 0.0), (10.0, 10.0)))  # 20 mm along, the corner at 10
    result = checked(poly, index((0, 0, 10, 0)), hidden=[(10, 0, 10, 10)], sampled=True)
    (kept,), (moved,) = result.kept, result.moved
    assert isinstance(kept, brep.Poly2) and isinstance(moved, brep.Line2)  # what is left of the second leg is straight
    assert kept.points[0] == (0.0, 0.0) and moved.b == (10.0, 10.0)
    assert kept.points[1] == (10.0, 0.0)  # the corner is kept
    assert kept.points[-1][1] == pytest.approx(0.0, abs=0.05) and moved.a[0] == 10.0
    assert kept.points[-1] == moved.a


def test_an_ellipse_cut_keeps_its_axes_and_changes_only_the_parameter_range():
    ellipse = brep.Ellipse2((0.0, 0.0), (10.0, 0.0), 0.5, 0.0, 2 * math.pi)

    def half(start, end):
        t = np.linspace(start, end, 161)
        return np.column_stack((10 * np.cos(t[:-1]), 5 * np.sin(t[:-1]), 10 * np.cos(t[1:]), 5 * np.sin(t[1:])))

    result = checked(ellipse, index(half(0.0, math.pi)), hidden=[half(math.pi, 2 * math.pi)])
    (hide,) = result.moved
    assert isinstance(hide, brep.Ellipse2) and (hide.center, hide.major_axis, hide.ratio) == (ellipse.center, ellipse.major_axis, 0.5)
    assert hide.start_rad == pytest.approx(math.pi, abs=0.03) and hide.end_rad == pytest.approx(2 * math.pi, abs=0.03)
