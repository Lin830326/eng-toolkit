"""Form errors from measured points: flatness, straightness, circularity, cylindricity (ISO 1101 / ISO 12180-12181 / ISO 12781).

Pure NumPy, millimetres in and out; knows nothing about MCP, units or the contract.  Every function takes an ``(n, d)`` array of
**distinct** points and returns a :class:`FormResult`.

The value of a form error is the width of the **minimum zone**: the thinnest pair of parallel lines / parallel planes / concentric
circles / coaxial cylinders that contains every measured point.  What each function can promise about its answer:

* ``straightness``  exact.  The thinnest strip around a planar point set is found from its convex hull by rotating calipers.
* ``flatness``      thinnest slab, measured perpendicular to the planes.  The optimum is fixed by 4 points (3 on one plane and 1 on
  the other, or 2 and 2 across two edges), so the candidate zones are enumerated from points that are extreme under the current
  plane and every candidate is scored against *all* points.  With at most :data:`EXHAUSTIVE_FLATNESS` points every point is a
  candidate, which makes the result the global optimum.  Above that the enumeration starts from a nested golden-section search
  and repeats until it stops improving; the result is then first-order optimal (checked) but not proven globally minimal.
* ``circularity``   thinnest annulus.  The optimum is fixed by 4 points (2 outer and 2 inner, or the degenerate 3 + 1 / 2 + 1
  collinear forms); centres built from such point sets are scored against all points.  Up to :data:`EXHAUSTIVE_CIRCULARITY`
  points every point is a candidate (global optimum); above that the same pool-and-polish iteration as flatness, from the
  least-squares centre and a ring of other starts.
* ``cylindricity``  a search, never exact: for each axis direction the minimum-zone annulus of the projected points is found as
  for circularity and a compass search moves the direction.  The answer is a zone that really contains every point, so it is an
  upper bound on the true minimum-zone cylindricity.

Every returned zone contains all points, so each value is an upper bound of the minimum-zone value: a part that passes with it
passes with the true minimum zone.  ``exact`` says when the value *is* the minimum.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from eng_toolkit.errors import ToolFailure

FEATURES = ("flatness", "straightness", "circularity", "cylindricity")
MIN_POINTS = {"flatness": 4, "straightness": 3, "circularity": 4, "cylindricity": 6}
MAX_POINTS = {"flatness": 100_000, "straightness": 500_000, "circularity": 250_000, "cylindricity": 100_000}
EXHAUSTIVE_FLATNESS = 24
EXHAUSTIVE_CIRCULARITY = 32
POOL_FLATNESS = 8  # extreme points kept on each side per iteration
SEARCH_SUBSET = 400  # points the cylinder axis is first searched on
ADD_PER_ROUND = 60  # most-violating points added to the search set per round
MAX_ROUNDS = 12
POOL_CIRCLE = 10  # farthest and nearest points kept per iteration
DEGENERATE_RATIO = 1e-9  # smaller singular value / larger one below which a point set counts as flat or collinear
_CHUNK = 4_000_000  # elements of a candidates-by-points matrix held at once


@dataclass(frozen=True)
class FormResult:
    value: float  # mm, width of the zone found
    least_squares_value: float | None  # mm, zone about the least-squares reference, for comparison (None: not computed)
    exact: bool  # True when ``value`` is the global minimum zone
    first_order_optimal: bool | None  # KKT check of the active points; None where it does not apply
    search: str
    estimator: str
    zone: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


def fail(family: str, message: str, **details: Any) -> ToolFailure:
    return ToolFailure(family, message, details)


# --------------------------------------------------------------------------- point-set checks


def unique_rows(points: np.ndarray) -> np.ndarray:
    return np.unique(points, axis=0)


def check_count(feature: str, distinct: int, given: int) -> None:
    need = MIN_POINTS[feature]
    if distinct < need:
        dup = f" ({given} given, {given - distinct} duplicates)" if given != distinct else ""
        raise fail(
            "input.too_few_points",
            f"{feature} needs at least {need} distinct points, got {distinct}{dup}: a smaller set fits the reference "
            "shape exactly and the form error would be 0 by construction, not by measurement",
            feature=feature,
            minimum=need,
            distinct=distinct,
            given=given,
        )
    if distinct > MAX_POINTS[feature]:
        raise fail(
            "input.too_large",
            f"{feature} accepts at most {MAX_POINTS[feature]} distinct points, got {distinct}; thin the point set",
            feature=feature,
            maximum=MAX_POINTS[feature],
            distinct=distinct,
        )


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def _extremes(values: np.ndarray, k: int, largest: bool, coords: np.ndarray, tie: float) -> np.ndarray:
    """Indices of the ``k`` largest (or smallest) ``values``.  Points within ``tie`` of the extreme are spread out with a
    farthest-point pick, so a row of tied points does not crowd out the rest of the face that is in contact with the zone."""
    n = len(values)
    if n <= k:
        return np.arange(n)
    signed = values if largest else -values
    top = signed.max()
    near = np.flatnonzero(signed >= top - tie)
    if len(near) <= k:
        return np.argpartition(-signed, k - 1)[:k]
    chosen = [int(near[np.argmax(np.linalg.norm(coords[near] - coords[near].mean(axis=0), axis=1))])]
    gap = np.linalg.norm(coords[near] - coords[chosen[0]], axis=1)
    while len(chosen) < k:
        j = int(np.argmax(gap))
        chosen.append(int(near[j]))
        gap = np.minimum(gap, np.linalg.norm(coords[near] - coords[near[j]], axis=1))
    return np.array(chosen)


def _zero_in_hull_2d(vectors: np.ndarray, rel: float = 1e-6) -> bool:
    """True when the origin lies in the convex hull of the 2-D ``vectors`` (within ``rel`` of their largest length)."""
    d = np.asarray(vectors, dtype=float).reshape(-1, 2)
    scale = float(np.abs(d).max()) if len(d) else 0.0
    if scale == 0.0:
        return True
    tol = rel * scale
    if (np.hypot(d[:, 0], d[:, 1]) <= tol).any():
        return True
    m = len(d)
    tol2 = rel * scale * scale
    if m >= 2:
        i, j = np.triu_indices(m, 1)
        a, b = d[i], d[j]
        cross = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
        dot = (a * b).sum(axis=1)
        if ((np.abs(cross) <= tol2) & (dot <= tol2)).any():
            return True
    if m >= 3:
        idx = np.array(list(itertools.combinations(range(m), 3)))
        a, b, c = d[idx[:, 0]], d[idx[:, 1]], d[idx[:, 2]]
        s1 = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
        s2 = b[:, 0] * c[:, 1] - b[:, 1] * c[:, 0]
        s3 = c[:, 0] * a[:, 1] - c[:, 1] * a[:, 0]
        inside = ((s1 >= -tol2) & (s2 >= -tol2) & (s3 >= -tol2)) | ((s1 <= tol2) & (s2 <= tol2) & (s3 <= tol2))
        if inside.any():
            return True
    return False


def _golden(fun, lo: float, hi: float, iters: int) -> tuple[float, float]:
    gr = (math.sqrt(5.0) - 1.0) / 2.0
    c = hi - gr * (hi - lo)
    d = lo + gr * (hi - lo)
    fc, fd = fun(c), fun(d)
    for _ in range(iters):
        if fc < fd:
            hi, d, fd = d, c, fc
            c = hi - gr * (hi - lo)
            fc = fun(c)
        else:
            lo, c, fc = c, d, fd
            d = lo + gr * (hi - lo)
            fd = fun(d)
    x = 0.5 * (lo + hi)
    return x, fun(x)


# --------------------------------------------------------------------------- straightness (exact)


def convex_hull_2d(points: np.ndarray) -> np.ndarray:
    """Counter-clockwise hull vertices of distinct 2-D points (Andrew's monotone chain, collinear points dropped)."""
    pts = points[np.lexsort((points[:, 1], points[:, 0]))].tolist()
    if len(pts) < 3:
        return np.array(pts)

    def half(seq):
        out: list[list[float]] = []
        for p in seq:
            while len(out) >= 2 and (
                (out[-1][0] - out[-2][0]) * (p[1] - out[-2][1]) - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0]) <= 0.0
            ):
                out.pop()
            out.append(p)
        return out

    lower = half(pts)
    upper = half(reversed(pts))
    return np.array(lower[:-1] + upper[:-1])


def min_width_2d(points: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """Thinnest strip containing all ``points``: ``(width, unit direction of the strip, unit normal)``.

    The strip has a side on a hull edge (a rotating-calipers sweep visits each edge once and keeps the farthest vertex)."""
    hull = convex_hull_2d(points)
    h = len(hull)
    if h < 3:
        direction = _unit(hull[-1] - hull[0]) if h == 2 else np.array([1.0, 0.0])
        return 0.0, direction, np.array([-direction[1], direction[0]])
    best, best_dir = math.inf, None
    j = 1
    for i in range(h):
        a, b = hull[i], hull[(i + 1) % h]
        ex, ey = b[0] - a[0], b[1] - a[1]
        length = math.hypot(ex, ey)

        def dist(k: int) -> float:
            p = hull[k % h]
            return (ex * (p[1] - a[1]) - ey * (p[0] - a[0])) / length

        if j < i + 1:
            j = i + 1
        while dist(j + 1) > dist(j):
            j += 1
        w = dist(j)
        if w < best:
            best, best_dir = w, np.array([ex, ey]) / length
    return best, best_dir, np.array([-best_dir[1], best_dir[0]])


def straightness(points: np.ndarray) -> FormResult:
    """Straightness of a profile in a plane: the thinnest strip around the ``(n, 2)`` points (exact)."""
    centre = points.mean(axis=0)
    pc = points - centre
    width, direction, normal = min_width_2d(pc)
    res = pc @ normal
    # Least-squares line = first principal axis (orthogonal regression); zone = range of the perpendicular residuals.
    _, _, vt = np.linalg.svd(pc, full_matrices=False)
    ls_normal = np.array([-vt[0][1], vt[0][0]])
    ls_res = pc @ ls_normal
    notes = ["all points are collinear: the strip has zero width"] if width == 0.0 else []
    return FormResult(
        value=float(width),
        least_squares_value=float(np.ptp(ls_res)),
        exact=True,
        first_order_optimal=None,
        search="convex hull and rotating calipers (closed form)",
        estimator="minimum zone: thinnest strip between two parallel lines",
        zone={
            "direction": direction.tolist(),
            "normal": normal.tolist(),
            "offset_low": float(normal @ centre + res.min()),
            "offset_high": float(normal @ centre + res.max()),
            "offset_definition": "line positions are normal . x, with x in the input frame",
        },
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------- flatness


def _slab_widths(normals: np.ndarray, pts: np.ndarray) -> np.ndarray:
    out = np.empty(len(normals))
    step = max(1, _CHUNK // max(1, len(pts)))
    for s in range(0, len(normals), step):
        proj = normals[s : s + step] @ pts.T
        out[s : s + step] = proj.max(axis=1) - proj.min(axis=1)
    return out


def _plane_candidates(pool: np.ndarray) -> np.ndarray:
    """Unit normals of every slab that 4 points of ``pool`` can fix: a face of 3 points, and two edges of 2 + 2 points."""
    k = len(pool)
    out = []
    tri = np.array(list(itertools.combinations(range(k), 3)))
    if len(tri):
        v1 = pool[tri[:, 1]] - pool[tri[:, 0]]
        v2 = pool[tri[:, 2]] - pool[tri[:, 0]]
        out.append(_cross_unit(v1, v2))
    edges = np.array(list(itertools.combinations(range(k), 2)))
    if len(edges) >= 2:
        i0, i1 = np.triu_indices(len(edges), 1)
        a0, b0, a1, b1 = edges[i0, 0], edges[i0, 1], edges[i1, 0], edges[i1, 1]
        keep = (a0 != a1) & (a0 != b1) & (b0 != a1) & (b0 != b1)
        a0, b0, a1, b1 = a0[keep], b0[keep], a1[keep], b1[keep]
        out.append(_cross_unit(pool[b0] - pool[a0], pool[b1] - pool[a1]))
    cands = np.vstack([c for c in out if len(c)]) if out else np.empty((0, 3))
    return cands


def _cross_unit(v1: np.ndarray, v2: np.ndarray) -> np.ndarray:
    c = np.cross(v1, v2)
    nc = np.linalg.norm(c, axis=1)
    sin = nc / np.maximum(np.linalg.norm(v1, axis=1) * np.linalg.norm(v2, axis=1), 1e-300)
    ok = sin > 1e-9
    return c[ok] / nc[ok, None]


def _best_normal(pts: np.ndarray, cands: np.ndarray, pool_idx: np.ndarray, exhaustive: bool) -> tuple[float, np.ndarray]:
    """The candidate normal with the thinnest slab over *all* ``pts``.  A slab over the pool alone can only be thinner, so the
    candidates are scored on the pool first and the full set is only consulted until the pool value passes the best so far."""
    if exhaustive:
        w = _slab_widths(cands, pts)
        j = int(np.argmin(w))
        return float(w[j]), cands[j]
    pw = _slab_widths(cands, pts[pool_idx])
    order = np.argsort(pw)
    best, best_n = math.inf, None
    batch = max(1, _CHUNK // max(1, len(pts)))
    for s in range(0, len(order), batch):
        idx = order[s : s + batch]
        if pw[idx[0]] >= best:
            break
        w = _slab_widths(cands[idx], pts)
        j = int(np.argmin(w))
        if w[j] < best:
            best, best_n = float(w[j]), cands[idx[j]]
    return best, best_n


def _polish_plane(pts: np.ndarray, n0: np.ndarray, exhaustive: bool, scale: float) -> tuple[float, np.ndarray]:
    n = _unit(n0)
    best = float(_slab_widths(n[None], pts)[0])
    for _ in range(40):
        if best <= 1e-15 * scale:
            break
        if exhaustive:
            pool = np.arange(len(pts))
        else:
            s = pts @ n
            tie = 1e-6 * best + 1e-12 * scale
            pool = np.unique(
                np.r_[_extremes(s, POOL_FLATNESS, True, pts, tie), _extremes(s, POOL_FLATNESS, False, pts, tie)]
            )
        cands = np.vstack([_plane_candidates(pts[pool]), n[None]])
        w, n_new = _best_normal(pts, cands, pool, exhaustive)
        if n_new is None or not w < best * (1.0 - 1e-12):
            break
        best, n = w, n_new
    return best, n


def _plane_kkt(pts: np.ndarray, n: np.ndarray, width: float, scale: float) -> bool:
    """First-order optimality of the slab: the contact faces seen along the normal must overlap (0 in the hull of top - bottom)."""
    s = pts @ n
    tol = 1e-8 * width + 1e-13 * scale
    top = _extremes(s, 8, True, pts, tol)
    bot = _extremes(s, 8, False, pts, tol)
    top = top[s[top] >= s.max() - tol]
    bot = bot[s[bot] <= s.min() + tol]
    helper = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = _unit(np.cross(n, helper))
    e2 = np.cross(n, e1)
    diff = pts[top][:, None, :] - pts[bot][None, :, :]
    return _zero_in_hull_2d(np.stack([diff @ e1, diff @ e2], axis=-1))


def flatness(points: np.ndarray) -> FormResult:
    """Flatness of ``(n, 3)`` points: the thinnest slab between two parallel planes, measured perpendicular to the planes."""
    n_pts = len(points)
    centre = points.mean(axis=0)
    pc = points - centre
    _, s, vt = np.linalg.svd(pc, full_matrices=False)
    scale = float(s[0])
    if s[1] <= DEGENERATE_RATIO * s[0]:
        raise fail(
            "geometry.degenerate",
            "the points are collinear (they span a line, not a surface): flatness is not defined; check the point list",
            singular_values=[float(v) for v in s],
        )
    q = pc @ vt.T  # x, y in the best-fit plane, z along its normal
    x, y, z = q[:, 0], q[:, 1], q[:, 2]
    ls_width = float(np.ptp(z))
    exhaustive = n_pts <= EXHAUSTIVE_FLATNESS
    notes: list[str] = []
    if ls_width <= 1e-15 * scale:
        width, n_pca, kkt = 0.0, np.array([0.0, 0.0, 1.0]), True
        exhaustive_result = True
        search = "all points coplanar"
    else:
        footprint, _, _ = min_width_2d(unique_rows(q[:, :2]))
        if ls_width > 0.5 * footprint:
            raise fail(
                "geometry.degenerate",
                f"the points do not form a surface that is flat to within its own footprint: the spread about the best-fit plane "
                f"({ls_width:.6g} mm) exceeds half the thinnest extent in the plane ({footprint:.6g} mm); flatness of this point "
                "set is not meaningful",
                spread_mm=ls_width,
                footprint_mm=footprint,
            )
        t_max = 6.0 * ls_width / footprint

        def g(a: float, b: float) -> float:
            u = z - a * x - b * y
            return float(np.ptp(u)) / math.sqrt(1.0 + a * a + b * b)

        if exhaustive:
            start = np.array([0.0, 0.0, 1.0])
            g_val = ls_width
        else:
            a_opt, _ = _golden(lambda a: _golden(lambda b: g(a, b), -t_max, t_max, 40)[1], -t_max, t_max, 40)
            b_opt, g_val = _golden(lambda b: g(a_opt, b), -t_max, t_max, 40)
            start = np.array([-a_opt, -b_opt, 1.0])
        width, n_pca = _polish_plane(q, start, exhaustive, scale)
        if not exhaustive and g_val < width * (1.0 - 1e-12):
            notes.append(
                "the vertex enumeration did not improve on the golden-section plane; its slightly thinner slab is reported"
            )
            width, n_pca = g_val, _unit(start)
        kkt = _plane_kkt(q, n_pca, width, scale)
        exhaustive_result = exhaustive
        search = (
            f"exhaustive over every face and edge pair of all {n_pts} points (global minimum)"
            if exhaustive
            else (
                f"golden-section start, then vertex enumeration from the {POOL_FLATNESS} extreme points on each side, repeated "
                "until no improvement; every candidate scored against all points"
            )
        )
    n_orig = vt.T @ n_pca
    if n_orig @ vt[2] < 0:
        n_orig = -n_orig
    sv = pc @ n_orig
    zone = {
        "normal": n_orig.tolist(),
        "offset_low": float(n_orig @ centre + sv.min()),
        "offset_high": float(n_orig @ centre + sv.max()),
        "offset_definition": "plane positions are normal . x, with x in the input frame",
    }
    if not exhaustive_result and not kkt:
        notes.append("the optimality check of the contact points failed: treat the value as an upper bound only")
    return FormResult(
        value=float(width),
        least_squares_value=ls_width,
        exact=bool(exhaustive_result),
        first_order_optimal=bool(kkt),
        search=search,
        estimator="minimum zone: thinnest slab between two parallel planes, distances perpendicular to the planes",
        zone=zone,
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------- circularity


def _radial(p: np.ndarray, c: np.ndarray) -> np.ndarray:
    return np.hypot(p[:, 0] - c[0], p[:, 1] - c[1])


def _annulus_widths(p: np.ndarray, centres: np.ndarray) -> np.ndarray:
    out = np.empty(len(centres))
    step = max(1, _CHUNK // max(1, len(p)))
    for s in range(0, len(centres), step):
        c = centres[s : s + step]
        d = np.hypot(p[None, :, 0] - c[:, None, 0], p[None, :, 1] - c[:, None, 1])
        out[s : s + step] = d.max(axis=1) - d.min(axis=1)
    return out


def _bisector(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return b - a, ((b * b).sum(axis=-1) - (a * a).sum(axis=-1)) / 2.0


def _meet(n1, k1, n2, k2) -> np.ndarray:
    """Intersection of the lines n1.x = k1 and n2.x = k2 (rows); parallel pairs give NaN."""
    det = n1[..., 0] * n2[..., 1] - n1[..., 1] * n2[..., 0]
    ok = np.abs(det) > 1e-9 * np.linalg.norm(n1, axis=-1) * np.linalg.norm(n2, axis=-1)
    with np.errstate(divide="ignore", invalid="ignore"):
        cx = (k1 * n2[..., 1] - k2 * n1[..., 1]) / det
        cy = (n1[..., 0] * k2 - n2[..., 0] * k1) / det
    out = np.stack([cx, cy], axis=-1)
    out[~ok] = np.nan
    return out


def _bisector_meets_line(n, k, p, q) -> np.ndarray:
    """Intersection of the bisector n.x = k with the line through p and q (rows)."""
    v = q - p
    nv = (n * v).sum(axis=-1)
    ok = np.abs(nv) > 1e-9 * np.linalg.norm(n, axis=-1) * np.linalg.norm(v, axis=-1)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (k - (n * p).sum(axis=-1)) / nv
    with np.errstate(invalid="ignore"):
        out = p + t[..., None] * v
    out[~ok] = np.nan
    return out


def _circle_candidates(outer: np.ndarray, inner: np.ndarray) -> np.ndarray:
    """Centres of every annulus that 4 of the given points can fix:
    2 outer + 2 inner (two bisectors), 3 on one circle (circumcentre), and the degenerate forms in which an outer and an inner
    point lie on one ray from the centre (a bisector and a line through two points)."""
    out = []
    op = np.array(list(itertools.combinations(range(len(outer)), 2)))
    ip = np.array(list(itertools.combinations(range(len(inner)), 2)))
    if len(op) and len(ip):
        n1, k1 = _bisector(outer[op[:, 0]], outer[op[:, 1]])
        n2, k2 = _bisector(inner[ip[:, 0]], inner[ip[:, 1]])
        out.append(_meet(n1[:, None, :], k1[:, None], n2[None, :, :], k2[None, :]).reshape(-1, 2))
    for pts in (outer, inner):
        tri = np.array(list(itertools.combinations(range(len(pts)), 3)))
        if len(tri):
            a, b, c = pts[tri[:, 0]], pts[tri[:, 1]], pts[tri[:, 2]]
            n1, k1 = _bisector(a, b)
            n2, k2 = _bisector(a, c)
            out.append(_meet(n1, k1, n2, k2))
    if len(op) and len(inner):
        n, k = _bisector(outer[op[:, 0]], outer[op[:, 1]])
        for through in (0, 1):
            a = outer[op[:, through]][:, None, :]
            out.append(_bisector_meets_line(n[:, None, :], k[:, None], a, inner[None, :, :]).reshape(-1, 2))
    if len(ip) and len(outer):
        n, k = _bisector(inner[ip[:, 0]], inner[ip[:, 1]])
        for through in (0, 1):
            d = inner[ip[:, through]][:, None, :]
            out.append(_bisector_meets_line(n[:, None, :], k[:, None], d, outer[None, :, :]).reshape(-1, 2))
    if not out:
        return np.empty((0, 2))
    cands = np.vstack(out)
    return cands[np.isfinite(cands).all(axis=1)]


def _best_centre(p: np.ndarray, cands: np.ndarray, pool_idx: np.ndarray, exhaustive: bool) -> tuple[float, np.ndarray | None]:
    if len(cands) == 0:
        return math.inf, None
    if exhaustive:
        w = _annulus_widths(p, cands)
        j = int(np.argmin(w))
        return float(w[j]), cands[j]
    pw = _annulus_widths(p[pool_idx], cands)
    order = np.argsort(pw)
    best, best_c = math.inf, None
    batch = max(1, _CHUNK // max(1, len(p)))
    for s in range(0, len(order), batch):
        idx = order[s : s + batch]
        if pw[idx[0]] >= best:
            break
        w = _annulus_widths(p, cands[idx])
        j = int(np.argmin(w))
        if w[j] < best:
            best, best_c = float(w[j]), cands[idx[j]]
    return best, best_c


def _polish_centre(p: np.ndarray, c0: np.ndarray, k: int, scale: float, max_iter: int = 60) -> tuple[float, np.ndarray]:
    c = np.asarray(c0, dtype=float)
    best = float(_annulus_widths(p, c[None])[0])
    for _ in range(max_iter):
        if best <= 1e-15 * scale:
            break
        d = _radial(p, c)
        tie = 1e-6 * best + 1e-12 * scale
        out_i = _extremes(d, k, True, p, tie)
        in_i = _extremes(d, k, False, p, tie)
        cands = _circle_candidates(p[out_i], p[in_i])
        w, c_new = _best_centre(p, cands, np.unique(np.r_[out_i, in_i]), False)
        if c_new is None or not w < best * (1.0 - 1e-12):
            break
        best, c = w, c_new
    return best, c


def lsq_circle(p: np.ndarray) -> tuple[np.ndarray, float]:
    """Geometric least-squares circle (Gauss-Newton from the algebraic fit); ``(centre, radius)``."""
    x, y = p[:, 0], p[:, 1]
    a = np.column_stack([2 * x, 2 * y, np.ones(len(x))])
    sol = np.linalg.lstsq(a, x * x + y * y, rcond=None)[0]
    c = sol[:2].copy()
    r = math.sqrt(max(sol[2] + c @ c, 0.0))
    best = (np.sum((_radial(p, c) - r) ** 2), c.copy(), r)
    for _ in range(50):
        d = np.maximum(_radial(p, c), 1e-300)
        jac = np.column_stack([(c[0] - x) / d, (c[1] - y) / d, -np.ones(len(x))])
        step = np.linalg.lstsq(jac, -(d - r), rcond=None)[0]
        c = c + step[:2]
        r = r + step[2]
        cost = float(np.sum((_radial(p, c) - r) ** 2))
        if cost < best[0]:
            best = (cost, c.copy(), r)
        if np.linalg.norm(step) <= 1e-14 * max(r, 1e-300):
            break
    return best[1], float(best[2])


def _circle_kkt(p: np.ndarray, c: np.ndarray, width: float, scale: float) -> bool:
    d = _radial(p, c)
    tol = 1e-8 * width + 1e-13 * scale
    out_i = _extremes(d, 8, True, p, tol)
    in_i = _extremes(d, 8, False, p, tol)
    out_i = out_i[d[out_i] >= d.max() - tol]
    in_i = in_i[d[in_i] <= d.min() + tol]

    def unit_vectors(idx):
        v = c - p[idx]
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-300)

    diff = unit_vectors(out_i)[:, None, :] - unit_vectors(in_i)[None, :, :]
    return _zero_in_hull_2d(diff)


def _mz_circle(p: np.ndarray, scale: float, exhaustive: bool) -> tuple[float, np.ndarray, np.ndarray, float]:
    """Minimum-zone centre of centred 2-D points: ``(width, centre, lsq centre, lsq width)``."""
    lsc, _ = lsq_circle(p)
    ls_width = float(_annulus_widths(p, lsc[None])[0])
    if exhaustive:
        w, c = _best_centre(p, _circle_candidates(p, p), np.arange(len(p)), True)
        if c is None or ls_width < w:
            w, c = ls_width, lsc
        return w, c, lsc, ls_width
    step = max(ls_width, 1e-9 * scale)
    starts = [lsc] + [lsc + 0.5 * step * np.array([math.cos(a), math.sin(a)]) for a in np.arange(8) * math.pi / 4]
    best_w, best_c = math.inf, lsc
    for s in starts:
        w, c = _polish_centre(p, s, POOL_CIRCLE, scale)
        if w < best_w:
            best_w, best_c = w, c
    # A last look around the winner at several scales: a polish that stalls short of the optimum has a better centre nearby.
    rng = np.random.default_rng(0)
    for _ in range(3):
        trial = best_c + best_w * rng.normal(size=(24, 2))
        tw = _annulus_widths(p, trial)
        j = int(np.argmin(tw))
        if tw[j] < best_w * (1 - 1e-9):
            w, c = _polish_centre(p, trial[j], POOL_CIRCLE, scale)
            if w < best_w:
                best_w, best_c = w, c
    if ls_width < best_w:
        best_w, best_c = ls_width, lsc
    return best_w, best_c, lsc, ls_width


def circularity(points: np.ndarray) -> FormResult:
    """Circularity (roundness) of ``(n, 2)`` points: the thinnest annulus between two concentric circles."""
    centre = points.mean(axis=0)
    pc = points - centre
    _, s, _ = np.linalg.svd(pc, full_matrices=False)
    if s[1] <= DEGENERATE_RATIO * s[0]:
        raise fail(
            "geometry.degenerate",
            "the points are collinear: they do not lie on any circle, so roundness is not defined; check the point list",
            singular_values=[float(v) for v in s],
        )
    scale = float(s[0])
    exhaustive = len(pc) <= EXHAUSTIVE_CIRCULARITY
    width, c, lsc, ls_width = _mz_circle(pc, scale, exhaustive)
    d = _radial(pc, c)
    kkt = _circle_kkt(pc, c, width, scale) if width > 1e-15 * scale else True
    notes: list[str] = []
    if not exhaustive and not kkt:
        notes.append("the optimality check of the contact points failed: treat the value as an upper bound only")
    lsc_r = float(_radial(pc, lsc).mean())
    return FormResult(
        value=float(width),
        least_squares_value=float(ls_width),
        exact=bool(exhaustive),
        first_order_optimal=bool(kkt),
        search=(
            f"exhaustive over every 2 + 2 / 3 + 1 point combination of all {len(pc)} points (global minimum)"
            if exhaustive
            else (
                f"polish from the least-squares centre and 8 ring starts: centres built from the {POOL_CIRCLE} farthest and "
                f"{POOL_CIRCLE} nearest points, every candidate scored against all points, repeated until no improvement"
            )
        ),
        estimator="minimum zone: thinnest annulus between two concentric circles (MZC, ISO 12181-1)",
        zone={
            "centre": (c + centre).tolist(),
            "inner_radius": float(d.min()),
            "outer_radius": float(d.max()),
            "least_squares_centre": (lsc + centre).tolist(),
            "least_squares_radius": lsc_r,
        },
        notes=tuple(notes),
    )


def project_to_plane(points: np.ndarray) -> tuple[np.ndarray, float]:
    """Coordinates of 3-D points in their best-fit plane, and the spread (ptp) of the points off that plane."""
    centre = points.mean(axis=0)
    _, s, vt = np.linalg.svd(points - centre, full_matrices=False)
    if s[1] <= DEGENERATE_RATIO * s[0]:
        raise fail("geometry.degenerate", "the points are collinear: no plane, no circle", singular_values=[float(v) for v in s])
    q = (points - centre) @ vt.T
    return q[:, :2], float(np.ptp(q[:, 2]))


# --------------------------------------------------------------------------- cylindricity


def _axis_basis(d: np.ndarray) -> np.ndarray:
    helper = np.array([1.0, 0.0, 0.0]) if abs(d[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = _unit(np.cross(d, helper))
    return np.stack([e1, np.cross(d, e1)])  # rows e1, e2


def _axis_zone(p: np.ndarray, d: np.ndarray, start3: np.ndarray | None, k: int, scale: float) -> tuple[float, np.ndarray]:
    """Width of the thinnest annulus around an axis along ``d`` and the point of the axis nearest the origin."""
    basis = _axis_basis(d)
    q = p @ basis.T
    if start3 is None:
        c0, _ = lsq_circle(q)
    else:
        c0 = basis @ start3
    w, c = _polish_centre(q, c0, k, scale, max_iter=12)
    return w, basis.T @ c


def _fibonacci_hemisphere(count: int) -> np.ndarray:
    i = np.arange(count) + 0.5
    z = i / count
    r = np.sqrt(1 - z * z)
    phi = i * math.pi * (3 - math.sqrt(5))
    return np.stack([r * np.cos(phi), r * np.sin(phi), z], axis=1)


def _compass(fun, x0: np.ndarray, step: float, min_step: float, stop_below: float = 0.0) -> tuple[float, np.ndarray]:
    """Compass (pattern) search in 2-D: try 8 directions, the last one that worked first; halve the step when none works."""
    dirs = [np.array(d) / math.hypot(*d) for d in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))]
    x = np.asarray(x0, dtype=float)
    fx = fun(x)
    first = 0
    while step > min_step and fx > stop_below:
        improved = False
        for k in range(8):
            idx = (first + k) % 8
            trial = x + step * dirs[idx]
            ft = fun(trial)
            if ft < fx:
                x, fx, improved, first = trial, ft, True, idx
                step *= 1.5
                break
        if not improved:
            step *= 0.5
    return fx, x


def cylindricity(points: np.ndarray) -> FormResult:
    """Cylindricity of ``(n, 3)`` points: the thinnest pair of coaxial cylinders found by an axis search (an upper bound)."""
    centre = points.mean(axis=0)
    pc = points - centre
    _, s, vt = np.linalg.svd(pc, full_matrices=False)
    scale = float(s[0])
    if s[1] <= DEGENERATE_RATIO * s[0]:
        raise fail(
            "geometry.degenerate",
            "the points are collinear: they do not lie on any cylinder; check the point list",
            singular_values=[float(v) for v in s],
        )
    if s[2] <= DEGENERATE_RATIO * s[0]:
        raise fail(
            "geometry.degenerate",
            "the points lie in one plane: a cylinder needs points in at least two cross-sections along its axis "
            "(use circularity for a single section)",
            singular_values=[float(v) for v in s],
        )
    n_pts = len(pc)
    # The axis is searched on a seeded subset, then every point outside the zone found is added and the search resumes
    # from the axis found (constraint generation): the optimum is fixed by 6 points, so a few rounds suffice.
    if n_pts > SEARCH_SUBSET:
        idx = np.random.default_rng(0).permutation(n_pts)[:SEARCH_SUBSET]
    else:
        idx = np.arange(n_pts)

    def make_zone(sub: np.ndarray):
        return lambda d, warm: _axis_zone(sub, d, warm, 5, scale)

    def refine(zone_at, d0: np.ndarray, p3: np.ndarray, step: float, min_step: float):
        basis = _axis_basis(d0)
        holder = {"p3": p3}

        def fun(st: np.ndarray) -> float:
            d = _unit(d0 + st[0] * basis[0] + st[1] * basis[1])
            w, p = zone_at(d, holder["p3"])
            holder["p3"] = p
            return w

        fx, st = _compass(fun, np.zeros(2), step, min_step, 1e-12 * scale)
        d = _unit(d0 + st[0] * basis[0] + st[1] * basis[1])
        w, p = zone_at(d, holder["p3"])
        return w, d, p

    def radial_about(d: np.ndarray, p3: np.ndarray) -> np.ndarray:
        rel = pc - p3
        return np.linalg.norm(rel - np.outer(rel @ d, d), axis=1)

    zone_at = make_zone(pc[idx])
    starts = [vt[0], vt[1], vt[2]] + list(_fibonacci_hemisphere(24) @ vt)
    scored = []
    for d0 in starts:
        d0 = _unit(np.asarray(d0, dtype=float))
        w, p3 = zone_at(d0, None)
        scored.append((w, d0, p3))
    scored.sort(key=lambda t: t[0])
    start_value = scored[0][0]
    coarse = [refine(zone_at, d0, p3, 0.1, 1e-3) for _, d0, p3 in scored[:2]]
    coarse.sort(key=lambda t: t[0])
    w, d, p3 = refine(zone_at, coarse[0][1], coarse[0][2], 1e-3, 1e-9)
    rounds = 1
    for _ in range(MAX_ROUNDS):
        radial = radial_about(d, p3)
        sub_r = radial[idx]
        lo, hi = sub_r.min(), sub_r.max()
        over = np.maximum(lo - radial, radial - hi)
        tol = 1e-12 * scale
        bad = np.flatnonzero(over > tol)
        if len(bad) == 0:
            break
        add = bad[np.argsort(-over[bad])[:ADD_PER_ROUND]]
        idx = np.union1d(idx, add)
        zone_at = make_zone(pc[idx])
        w, d, p3 = refine(zone_at, d, p3, 5e-4, 1e-9)
        rounds += 1
    radial = radial_about(d, p3)
    w, rad = float(radial.max() - radial.min()), (float(radial.min()), float(radial.max()))
    if d[np.argmax(np.abs(d))] < 0:
        d = -d
    notes = [
        "the axis was searched, not solved: the value is a valid enclosing zone and so an upper bound of the true "
        "minimum-zone cylindricity, but not proven minimal"
    ]
    return FormResult(
        value=w,
        least_squares_value=None,
        exact=False,
        first_order_optimal=None,
        search=(
            "axis direction by compass search from the 3 principal axes and 24 other starts (best 2 refined) on a subset of "
            "at most 400 points, the minimum-zone annulus of the projected points at each direction; points outside the "
            "zone are added and the search resumes until all points are inside"
        ),
        estimator="minimum zone (axis-searched): thinnest pair of coaxial cylinders, not proven minimal",
        zone={
            "axis_direction": d.tolist(),
            "axis_point": (p3 + centre).tolist(),
            "inner_radius": rad[0],
            "outer_radius": rad[1],
            "starting_value_best_principal_or_scan_axis": float(start_value),
        },
        notes=tuple(notes),
    )


__all__ = [
    "FEATURES",
    "FormResult",
    "MAX_POINTS",
    "MIN_POINTS",
    "check_count",
    "circularity",
    "convex_hull_2d",
    "cylindricity",
    "fail",
    "flatness",
    "lsq_circle",
    "min_width_2d",
    "project_to_plane",
    "straightness",
    "unique_rows",
]
