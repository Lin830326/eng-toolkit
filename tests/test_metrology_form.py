"""Minimum-zone form errors (eng_toolkit.domains.metrology.form) against closed-form answers and independent brute force.

Closed-form cases (all numbers in mm, worked by hand):

* Flatness, 6 x 6 grid on a plane tilted 20 degrees about y, every other point displaced +0.05 / -0.05 along the plane
  normal: two parallel planes 0.10 apart hold all points and no thinner slab can, because both planes carry coplanar points
  that no tilt separates.  flatness = 0.10.
* Flatness, unit square with one corner lifted by h: the two diagonals are the opposite edges that fix the slab, normal
  (-h, -h, 2) / sqrt(2 h^2 + 4); the slab is the distance between the diagonals, h / sqrt(4 + 2 h^2).  h = 0.1 gives 0.049875...
* Straightness, (0,0) (10,0.3) (20,0): the strip rests on the long side, 0.3.
* Circularity, ideal points on a circle: 0 whatever the centre; six points alternately at R + A and R - A at 60 degree steps:
  the sum of the three outer unit vectors is 0 and so is the sum of the inner ones, so the centre is stationary and the
  annulus is 2 A = 0.2 (A = 0.1); the independent zoom search below confirms nothing thinner exists.
* Circularity of an ellipse (10, 9.5) sampled every degree: R = a, r = b at the four vertices, zone 0.5.
* Cylindricity, circles of radius 10 at z = 0 and 10.3 at z = 30: the axis that holds both circles centred gives 0.3 = the
  radius difference; a tilted or displaced axis only widens the range of the radii (see docs/metrology-gdt/design.md).
* A perfect cylinder in an arbitrary pose: 0.

The brute-force oracles below share no code with the module: the slab oracle scores every triple and every pair of edges of
the whole point set, the strip oracle every pair of points, and the annulus oracle zooms a dense grid of centres.
"""

import itertools
import math

import numpy as np
import pytest

from eng_toolkit.domains.metrology import form
from eng_toolkit.errors import ToolFailure

# --------------------------------------------------------------------------- independent oracles


def brute_slab(points: np.ndarray) -> float:
    """Thinnest slab: every normal a face (3 points) or two opposite edges (2 + 2 points) can fix, scored on all points."""
    best = math.inf
    n = len(points)
    for i, j, k in itertools.combinations(range(n), 3):
        nrm = np.cross(points[j] - points[i], points[k] - points[i])
        if np.linalg.norm(nrm) > 1e-12:
            s = points @ (nrm / np.linalg.norm(nrm))
            best = min(best, s.max() - s.min())
    edges = list(itertools.combinations(range(n), 2))
    for (a, b), (c, d) in itertools.combinations(edges, 2):
        if len({a, b, c, d}) < 4:
            continue
        nrm = np.cross(points[b] - points[a], points[d] - points[c])
        if np.linalg.norm(nrm) > 1e-12:
            s = points @ (nrm / np.linalg.norm(nrm))
            best = min(best, s.max() - s.min())
    return best


def brute_strip(points: np.ndarray) -> float:
    best = math.inf
    for a, b in itertools.combinations(range(len(points)), 2):
        v = points[b] - points[a]
        if np.linalg.norm(v) == 0:
            continue
        nrm = np.array([-v[1], v[0]]) / np.linalg.norm(v)
        s = points @ nrm
        best = min(best, s.max() - s.min())
    return best


def zoom_annulus(points: np.ndarray, levels: int = 14, grid: int = 81, keep: int = 10) -> float:
    """Global zoom search of the centre: a dense grid, then the best cells refined over and over."""
    lo, hi = points.min(axis=0), points.max(axis=0)
    half = (hi - lo).max() / 2 * 1.2
    centre = (lo + hi) / 2
    boxes = [(centre, half)]
    best = math.inf
    for _ in range(levels):
        found = []
        for c, h in boxes:
            xs = c[0] + np.linspace(-h, h, grid)
            ys = c[1] + np.linspace(-h, h, grid)
            gx, gy = np.meshgrid(xs, ys)
            d = np.hypot(points[:, 0, None, None] - gx, points[:, 1, None, None] - gy)
            w = d.max(axis=0) - d.min(axis=0)
            order = np.argsort(w, axis=None)[:keep]
            for idx in order:
                iy, ix = np.unravel_index(idx, w.shape)
                found.append((w[iy, ix], np.array([gx[iy, ix], gy[iy, ix]]), 2 * h / (grid - 1)))
        found.sort(key=lambda t: t[0])
        best = found[0][0]
        boxes = [(c, 3 * step) for _, c, step in found[:keep]]
    return best


def tilted_plane_points(angle_deg: float, offsets: np.ndarray, spacing: float = 10.0, origin=(0.0, 0.0, 0.0)):
    th = math.radians(angle_deg)
    normal = np.array([math.sin(th), 0.0, math.cos(th)])
    e1 = np.array([math.cos(th), 0.0, -math.sin(th)])
    e2 = np.array([0.0, 1.0, 0.0])
    side = int(round(math.sqrt(len(offsets))))
    grid = np.array([(i, j) for i in range(side) for j in range(side)], dtype=float) * spacing
    return grid[:, :1] * e1 + grid[:, 1:] * e2 + offsets[:, None] * normal + np.array(origin), normal


# --------------------------------------------------------------------------- flatness


@pytest.mark.parametrize("origin", [(0.0, 0.0, 0.0), (100.0, 200.0, 300.0), (1e6, -1e6, 1e6)])
def test_flatness_tilted_plane_with_pm_offsets_is_the_offset_span(origin):
    offsets = np.where(np.arange(36) % 2 == 0, 0.05, -0.05)
    pts, _ = tilted_plane_points(20.0, offsets, origin=origin)
    r = form.flatness(pts)
    # the data resolution at 1e6 mm is 1.2e-10, so the closed form is met to a few ulps of the coordinates
    assert r.value == pytest.approx(0.10, abs=1e-12 + 8 * float(np.spacing(np.abs(pts).max())))
    assert r.first_order_optimal
    # the reference plane tilts 20 degrees: its normal is the one built into the points (up to sign)
    expected = np.array([math.sin(math.radians(20)), 0.0, math.cos(math.radians(20))])
    assert abs(np.dot(r.zone["normal"], expected)) == pytest.approx(1.0, abs=1e-9)


def test_flatness_perfect_plane_is_zero_for_any_orientation():
    rng = np.random.default_rng(3)
    uv = rng.uniform(-20, 20, size=(40, 2))
    q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    pts = uv @ q[:, :2].T + np.array([5.0, -3.0, 8.0])
    r = form.flatness(pts)
    assert r.value < 1e-12
    assert r.least_squares_value < 1e-12


@pytest.mark.parametrize("h", [0.1, 0.02, 0.5])
def test_flatness_of_a_twisted_square_is_the_diagonal_distance(h):
    pts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, h]], dtype=float)
    r = form.flatness(pts)
    assert r.value == pytest.approx(h / math.sqrt(4 + 2 * h * h), rel=1e-12)
    assert r.exact
    assert r.value == pytest.approx(brute_slab(pts), rel=1e-12)


@pytest.mark.parametrize("seed", range(8))
def test_flatness_minimum_zone_matches_brute_force_on_small_random_sets(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(5, 11))
    pts = np.c_[rng.uniform(0, 30, n), rng.uniform(0, 30, n), rng.normal(0, 0.05, n)]
    pts[:, 2] += 0.02 * pts[:, 0] - 0.01 * pts[:, 1]
    r = form.flatness(pts)
    assert r.exact
    assert r.value == pytest.approx(brute_slab(pts), rel=1e-10)
    # never thinner than the true minimum, never thicker than the least-squares zone
    assert r.value <= r.least_squares_value + 1e-12


@pytest.mark.parametrize("seed", range(3))
def test_flatness_pooled_search_equals_exhaustive_on_larger_sets(seed, monkeypatch):
    rng = np.random.default_rng(100 + seed)
    n = 30
    pts = np.c_[rng.uniform(0, 50, n), rng.uniform(0, 50, n), rng.normal(0, 0.05, n)]
    pts[:, 2] += 0.01 * pts[:, 0] - 0.005 * pts[:, 1]
    pooled = form.flatness(pts)
    assert not pooled.exact  # more than EXHAUSTIVE_FLATNESS points: polished, not proven
    monkeypatch.setattr(form, "EXHAUSTIVE_FLATNESS", 10**6)
    exhaustive = form.flatness(pts)
    assert exhaustive.exact
    assert pooled.value == pytest.approx(exhaustive.value, rel=1e-9)
    assert pooled.first_order_optimal


def test_flatness_slab_holds_every_point_and_is_perpendicular_not_vertical():
    # A plane steeper than 45 degrees: the vertical distance would be 1/cos(70) = 2.9 times the perpendicular one.
    offsets = np.where(np.arange(25) % 2 == 0, 0.05, -0.05)
    pts, normal = tilted_plane_points(70.0, offsets)
    r = form.flatness(pts)
    n = np.array(r.zone["normal"])
    s = pts @ n
    assert s.max() - s.min() == pytest.approx(r.value, abs=1e-12)
    assert r.value == pytest.approx(0.10, abs=1e-10)


def test_flatness_scales_linearly_with_the_point_set():
    rng = np.random.default_rng(9)
    pts = np.c_[rng.uniform(0, 30, 9), rng.uniform(0, 30, 9), rng.normal(0, 0.05, 9)]
    a = form.flatness(pts).value
    assert form.flatness(pts * 25.4).value == pytest.approx(a * 25.4, rel=1e-10)


# --------------------------------------------------------------------------- straightness


def test_straightness_three_points_closed_form():
    r = form.straightness(np.array([[0, 0], [10, 0.3], [20, 0]], dtype=float))
    assert r.value == pytest.approx(0.3, abs=1e-12)
    assert r.exact


def test_straightness_zigzag():
    pts = np.c_[np.arange(10.0), np.where(np.arange(10) % 2 == 0, 0.1, -0.1)]
    assert form.straightness(pts).value == pytest.approx(0.2, abs=1e-12)


def test_straightness_of_a_tilted_line_with_scatter_has_no_tilt_effect():
    th = math.radians(33.0)
    t = np.linspace(0, 100, 11)
    off = np.where(np.arange(11) % 2 == 0, 0.04, -0.04)
    pts = np.c_[t * math.cos(th) - off * math.sin(th), t * math.sin(th) + off * math.cos(th)] + 1e6
    assert form.straightness(pts).value == pytest.approx(0.08, abs=1e-9)


@pytest.mark.parametrize("seed", range(10))
def test_straightness_matches_pairwise_brute_force(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(3, 40))
    pts = np.c_[np.sort(rng.uniform(0, 100, n)), rng.normal(0, 0.3, n)]
    assert form.straightness(pts).value == pytest.approx(brute_strip(pts), rel=1e-10, abs=1e-12)


def test_straightness_many_hull_points_runs_in_linear_time():
    a = np.linspace(0, 2 * np.pi, 50_000, endpoint=False)
    pts = np.c_[1000 * np.cos(a), 1000 * np.sin(a)]  # every point is a hull vertex: width of a circle = diameter
    assert form.straightness(pts).value == pytest.approx(2000.0, rel=1e-6)


def test_straightness_collinear_is_zero():
    r = form.straightness(np.array([[0, 0], [1, 1], [2, 2], [5, 5]], dtype=float))
    assert r.value == pytest.approx(0.0, abs=1e-12)


# --------------------------------------------------------------------------- circularity


@pytest.mark.parametrize("centre", [(0.0, 0.0), (5.0, -7.0), (1e6, 1e6)])
@pytest.mark.parametrize("count", [5, 40, 300])
def test_ideal_circle_has_zero_circularity_whatever_its_centre(centre, count):
    rng = np.random.default_rng(count)
    a = rng.uniform(0, 2 * np.pi, count)
    pts = np.c_[10 * np.cos(a) + centre[0], 10 * np.sin(a) + centre[1]]
    r = form.circularity(pts)
    assert r.value < 4 * float(np.spacing(abs(centre[0]) + 10)) + 1e-12
    assert r.zone["inner_radius"] == pytest.approx(10.0, abs=1e-6)
    assert r.zone["centre"] == pytest.approx(list(centre), abs=1e-6)


def test_partial_arc_is_still_a_circle():
    a = np.linspace(0.2, 1.4, 25)
    pts = np.c_[50 * np.cos(a), 50 * np.sin(a)]
    assert form.circularity(pts).value < 1e-9


def test_three_lobe_points_have_an_annulus_of_two_amplitudes():
    ang = np.arange(6) * math.pi / 3
    rad = np.where(np.arange(6) % 2 == 0, 10.1, 9.9)
    pts = np.c_[rad * np.cos(ang), rad * np.sin(ang)] + [3.0, -2.0]
    r = form.circularity(pts)
    assert r.value == pytest.approx(0.2, abs=1e-12)
    assert r.exact
    assert r.zone["centre"] == pytest.approx([3.0, -2.0], abs=1e-9)
    assert zoom_annulus(pts) == pytest.approx(0.2, abs=1e-8)  # nothing thinner exists


def test_dense_ellipse_roundness_is_the_axis_difference():
    a = np.deg2rad(np.arange(360))
    pts = np.c_[10 * np.cos(a), 9.5 * np.sin(a)]
    r = form.circularity(pts)
    assert r.value == pytest.approx(0.5, abs=1e-12)
    assert r.first_order_optimal
    assert r.least_squares_value >= r.value - 1e-12  # concentric by symmetry: the least-squares zone happens to be minimal too
    assert zoom_annulus(pts[::6]) == pytest.approx(0.5, abs=1e-9)


@pytest.mark.parametrize("seed", range(12))
def test_small_sets_are_the_global_minimum_by_independent_zoom_search(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(5, 14))
    t = rng.uniform(0, 2 * np.pi, n)
    r_ = 10 + rng.normal(0, 0.1, n)
    pts = np.c_[r_ * np.cos(t), r_ * np.sin(t)]
    r = form.circularity(pts)
    assert r.exact
    z = zoom_annulus(pts)
    assert r.value <= z + 1e-9
    assert r.value == pytest.approx(z, abs=1e-6)


@pytest.mark.parametrize("seed", range(2))
def test_pooled_search_equals_exhaustive_on_larger_sets(seed, monkeypatch):
    rng = np.random.default_rng(50 + seed)
    n = 36
    t = rng.uniform(0, 2 * np.pi, n)
    r_ = 10 + rng.normal(0, 0.05, n) + 0.05 * np.cos(3 * t)
    pts = np.c_[r_ * np.cos(t) + 2, r_ * np.sin(t) - 1]
    pooled = form.circularity(pts)
    assert not pooled.exact
    monkeypatch.setattr(form, "EXHAUSTIVE_CIRCULARITY", 10**6)
    exhaustive = form.circularity(pts)
    assert exhaustive.exact
    assert pooled.value == pytest.approx(exhaustive.value, rel=1e-9)
    assert pooled.first_order_optimal


def test_large_set_is_fast_and_valid():
    rng = np.random.default_rng(1)
    t = rng.uniform(0, 2 * np.pi, 30_000)
    r_ = 25 + 0.003 * np.cos(5 * t) + rng.normal(0, 0.0005, t.size)
    pts = np.c_[r_ * np.cos(t), r_ * np.sin(t)]
    r = form.circularity(pts)
    assert 0.006 <= r.value <= 0.0105  # 2 * 0.003 for the lobes, plus the +-4 sigma reach of the noise
    assert r.value <= r.least_squares_value + 1e-12
    assert r.first_order_optimal


def test_circularity_scales_linearly():
    a = np.deg2rad(np.arange(0, 360, 5))
    pts = np.c_[10 * np.cos(a), 9.8 * np.sin(a)]
    v = form.circularity(pts).value
    assert form.circularity(pts * 0.0254).value == pytest.approx(v * 0.0254, rel=1e-9)


# --------------------------------------------------------------------------- cylindricity


def _pose(points, seed):
    rng = np.random.default_rng(seed)
    q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    return points @ q.T + np.array([5.0, 6.0, 7.0])


def test_perfect_cylinder_in_any_pose_is_zero():
    rng = np.random.default_rng(2)
    a = rng.uniform(0, 2 * np.pi, 120)
    z = rng.uniform(0, 40, 120)
    pts = _pose(np.c_[10 * np.cos(a), 10 * np.sin(a), z], 5)
    r = form.cylindricity(pts)
    assert r.value < 1e-6
    assert not r.exact


def test_cone_taper_is_the_radius_difference():
    a = np.tile(np.arange(24) * 2 * np.pi / 24, 2)
    z = np.repeat([0.0, 30.0], 24)
    rad = np.repeat([10.0, 10.3], 24)
    r = form.cylindricity(_pose(np.c_[rad * np.cos(a), rad * np.sin(a), z], 6))
    assert r.value == pytest.approx(0.3, abs=1e-8)
    assert r.value >= 0.3 - 1e-8  # the radii differ by 0.3 whichever axis is chosen


def test_elliptical_cylinder_is_its_roundness():
    a = np.deg2rad(np.arange(0, 360, 6))
    z = np.repeat([0.0, 15.0, 30.0], len(a))
    pts = _pose(np.c_[np.tile(10 * np.cos(a), 3), np.tile(9.8 * np.sin(a), 3), z], 7)
    assert form.cylindricity(pts).value == pytest.approx(0.2, abs=1e-6)


def test_short_wide_cylinder_axis_is_not_taken_from_the_longest_extent():
    a = np.tile(np.arange(36) * 2 * np.pi / 36, 2)
    z = np.repeat([0.0, 4.0], 36)  # radius 50, height 4: the principal axes say "flat disc", the axis is the short one
    pts = _pose(np.c_[50 * np.cos(a), 50 * np.sin(a), z], 8)
    assert form.cylindricity(pts).value < 1e-6


# --------------------------------------------------------------------------- degenerate input


@pytest.mark.parametrize(
    "feature, count",
    [("flatness", 3), ("straightness", 2), ("circularity", 3), ("cylindricity", 5)],
)
def test_too_few_points_is_a_clear_error(feature, count):
    with pytest.raises(ToolFailure) as exc:
        form.check_count(feature, count, count)
    assert exc.value.family == "input.too_few_points"
    assert exc.value.details["minimum"] == form.MIN_POINTS[feature]


def test_duplicates_count_once():
    with pytest.raises(ToolFailure) as exc:
        form.check_count("flatness", 3, 10)
    assert "7 duplicates" in exc.value.message


def test_collinear_points_are_geometry_degenerate():
    line3 = np.array([[0, 0, 0], [1, 1, 1], [2, 2, 2], [3, 3, 3]], dtype=float)
    with pytest.raises(ToolFailure) as exc:
        form.flatness(line3)
    assert exc.value.family == "geometry.degenerate"
    with pytest.raises(ToolFailure) as exc:
        form.circularity(np.array([[0, 0], [1, 1], [2, 2], [3, 3]], dtype=float))
    assert exc.value.family == "geometry.degenerate"
    with pytest.raises(ToolFailure) as exc:
        form.cylindricity(np.vstack([line3, line3 + 5]))
    assert exc.value.family == "geometry.degenerate"


def test_coplanar_points_have_no_cylinder():
    a = np.arange(8) * math.pi / 4
    with pytest.raises(ToolFailure) as exc:
        form.cylindricity(np.c_[np.cos(a), np.sin(a), np.zeros(8)])
    assert exc.value.family == "geometry.degenerate"
    assert "two cross-sections" in exc.value.message


def test_a_cloud_that_is_not_a_surface_is_refused_for_flatness():
    rng = np.random.default_rng(4)
    with pytest.raises(ToolFailure) as exc:
        form.flatness(rng.uniform(-1, 1, size=(30, 3)))
    assert exc.value.family == "geometry.degenerate"
    assert "do not form a surface" in exc.value.message


def test_every_result_zone_contains_all_points():
    rng = np.random.default_rng(11)
    pts = np.c_[rng.uniform(0, 30, 20), rng.uniform(0, 30, 20), rng.normal(0, 0.05, 20)]
    z = form.flatness(pts).zone
    s = pts @ np.array(z["normal"])
    assert z["offset_low"] <= s.min() + 1e-9 and s.max() <= z["offset_high"] + 1e-9
    ang = rng.uniform(0, 2 * np.pi, 20)
    c = np.c_[10 * np.cos(ang), 10 * np.sin(ang)] * rng.uniform(0.98, 1.02, (20, 1))
    zc = form.circularity(c).zone
    d = np.hypot(c[:, 0] - zc["centre"][0], c[:, 1] - zc["centre"][1])
    assert zc["inner_radius"] <= d.min() + 1e-9 and d.max() <= zc["outer_radius"] + 1e-9


# --------------------------------------------------------------------------- many tied contact points


def test_flatness_checkerboard_of_four_hundred_points_has_many_tied_contacts():
    # 200 points sit exactly on each of the two planes: the contact faces are huge tied sets
    offsets = np.where((np.arange(400) // 20 + np.arange(400)) % 2 == 0, 0.05, -0.05)
    pts, _ = tilted_plane_points(12.0, offsets, spacing=5.0)
    r = form.flatness(pts)
    assert r.value == pytest.approx(0.10, abs=1e-10)
    assert r.first_order_optimal


def test_circularity_three_lobes_at_forty_eight_points_pooled_equals_exhaustive(monkeypatch):
    t = np.deg2rad(np.arange(48) * 7.5)
    rad = 10 + 0.1 * np.cos(3 * t)
    pts = np.c_[rad * np.cos(t), rad * np.sin(t)]
    pooled = form.circularity(pts)
    assert not pooled.exact
    monkeypatch.setattr(form, "EXHAUSTIVE_CIRCULARITY", 10**6)
    exhaustive = form.circularity(pts)
    assert pooled.value == pytest.approx(exhaustive.value, rel=1e-9)
    assert pooled.value == pytest.approx(0.2, abs=2e-3)  # 2 A, within the small second-order effect of a 1 % lobe
