"""Exact geometric clipping in pixel space (before any number is formatted), and lengths of drawn lines inside a box.

* Segments: Liang-Barsky, vectorised for ordinary pixel coordinates, exact rational arithmetic for extreme endpoints.
  The caller supplies the original data-to-pixel mapping for that fallback, before rounding can erase the axis offset.
* Polygons (fill to zero): Sutherland-Hodgman against the four edges.
* The rectangle is the plot area grown by ``CLIP_GROW`` px on every side; the SVG clip path then trims the line width at the frame.
"""

from __future__ import annotations

from collections.abc import Callable
from fractions import Fraction

import numpy as np

CLIP_GROW = 4.0
# Binary64 arithmetic at this magnitude has sub-micro-pixel roundoff. Beyond it, retain rational endpoints until clipped.
EXACT_THRESHOLD = 1e8
ExactPoint = Callable[[int], tuple[Fraction, Fraction]]


def clip_segments(x0: np.ndarray, y0: np.ndarray, x1: np.ndarray, y1: np.ndarray, rect: tuple[float, float, float, float]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each segment the parameters ``t0 <= t1`` of the part inside ``rect`` = (xmin, ymin, xmax, ymax) and whether there is one."""
    xmin, ymin, xmax, ymax = rect
    dx, dy = x1 - x0, y1 - y0
    t0 = np.zeros(len(x0))
    t1 = np.ones(len(x0))
    ok = np.ones(len(x0), dtype=bool)
    with np.errstate(all="ignore"):
        for p, q in ((-dx, x0 - xmin), (dx, xmax - x0), (-dy, y0 - ymin), (dy, ymax - y0)):
            ok &= ~((p == 0) & (q < 0))
            r = np.where(p != 0, q / np.where(p != 0, p, 1.0), 0.0)
            t0 = np.where(p < 0, np.maximum(t0, r), t0)
            t1 = np.where(p > 0, np.minimum(t1, r), t1)
    ok &= t0 <= t1
    return t0, t1, ok


def inside(xs: np.ndarray, ys: np.ndarray, rect: tuple[float, float, float, float]) -> np.ndarray:
    xmin, ymin, xmax, ymax = rect
    return (xs >= xmin) & (xs <= xmax) & (ys >= ymin) & (ys <= ymax)


def _exact_segment(a: tuple[Fraction, Fraction], b: tuple[Fraction, Fraction], rect: tuple[float, float, float, float]):
    xmin, ymin, xmax, ymax = map(Fraction, rect)
    x, y = a
    dx, dy = b[0] - x, b[1] - y
    t0, t1 = Fraction(0), Fraction(1)
    for p, q in ((-dx, x - xmin), (dx, xmax - x), (-dy, y - ymin), (dy, ymax - y)):
        if p == 0:
            if q < 0:
                return None
        elif p < 0:
            t0 = max(t0, q / p)
        else:
            t1 = min(t1, q / p)
        if t0 > t1:
            return None
    return t0, t1, float(x + t0 * dx), float(y + t0 * dy), float(x + t1 * dx), float(y + t1 * dy)


def clip_polyline(xs: np.ndarray, ys: np.ndarray, rect: tuple[float, float, float, float], *, exact_point: ExactPoint | None = None) -> list[tuple[np.ndarray, np.ndarray]]:
    """The runs of a polyline inside ``rect``, each with the exact crossing points as its ends."""
    if len(xs) == 0:
        return []
    if bool(np.all(inside(xs, ys, rect))):
        return [(xs, ys)]
    if len(xs) == 1:
        return []
    x0, y0, x1, y1 = xs[:-1], ys[:-1], xs[1:], ys[1:]
    t0, t1, ok = clip_segments(x0, y0, x1, y1, rect)
    dx, dy = x1 - x0, y1 - y0
    extreme = (np.maximum(np.abs(xs), np.abs(ys)) > EXACT_THRESHOLD)
    exact_cache: dict[int, tuple[Fraction, Fraction]] = {}

    def point(i: int) -> tuple[Fraction, Fraction]:
        if i not in exact_cache:
            exact_cache[i] = exact_point(i) if exact_point else (Fraction(float(xs[i])), Fraction(float(ys[i])))
        return exact_cache[i]

    runs: list[tuple[list[float], list[float]]] = []
    current: tuple[list[float], list[float]] | None = None
    for i in range(len(x0)):
        if extreme[i] or extreme[i + 1]:
            clipped = _exact_segment(point(i), point(i + 1), rect)
            if clipped is None:
                current = None
                continue
            start, end, sx, sy, ex, ey = clipped
        else:
            if not ok[i]:
                current = None
                continue
            start, end = t0[i], t1[i]
            # Reconstruct from the nearer endpoint to limit cancellation for ordinary coordinates too.
            sx, sy = ((x0[i] + start * dx[i], y0[i] + start * dy[i]) if start <= 0.5
                      else (x1[i] - (1 - start) * dx[i], y1[i] - (1 - start) * dy[i]))
            ex, ey = ((x0[i] + end * dx[i], y0[i] + end * dy[i]) if end <= 0.5
                      else (x1[i] - (1 - end) * dx[i], y1[i] - (1 - end) * dy[i]))
        if current is None or start > 0:
            current = ([float(sx)], [float(sy)])
            runs.append(current)
        current[0].append(float(ex))
        current[1].append(float(ey))
        if end < 1:
            current = None
    return [(np.array(a), np.array(b)) for a, b in runs if len(a) >= 2]


def clip_polygon(xs: np.ndarray, ys: np.ndarray, rect: tuple[float, float, float, float], *, exact_point: ExactPoint | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Sutherland-Hodgman: the part of a closed polygon inside ``rect``."""
    pts = list(zip(xs.tolist(), ys.tolist()))
    xmin, ymin, xmax, ymax = rect
    if any(max(abs(x), abs(y)) > EXACT_THRESHOLD for x, y in pts):
        pts = [exact_point(i) if exact_point else (Fraction(x), Fraction(y)) for i, (x, y) in enumerate(pts)]
        xmin, ymin, xmax, ymax = map(Fraction, rect)
    edges = (
        (lambda p: p[0] >= xmin, lambda a, b: (xmin, a[1] + (b[1] - a[1]) * (xmin - a[0]) / (b[0] - a[0]))),
        (lambda p: p[0] <= xmax, lambda a, b: (xmax, a[1] + (b[1] - a[1]) * (xmax - a[0]) / (b[0] - a[0]))),
        (lambda p: p[1] >= ymin, lambda a, b: (a[0] + (b[0] - a[0]) * (ymin - a[1]) / (b[1] - a[1]), ymin)),
        (lambda p: p[1] <= ymax, lambda a, b: (a[0] + (b[0] - a[0]) * (ymax - a[1]) / (b[1] - a[1]), ymax)),
    )
    for keep, cross in edges:
        if not pts:
            break
        out = []
        prev = pts[-1]
        for cur in pts:
            if keep(cur):
                if not keep(prev):
                    out.append(cross(prev, cur))
                out.append(cur)
            elif keep(prev):
                out.append(cross(prev, cur))
            prev = cur
        pts = out
    return np.array([float(p[0]) for p in pts]), np.array([float(p[1]) for p in pts])


def length_inside(segments: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray], box: tuple[float, float, float, float]) -> float:
    """Total length (px) of the given segments inside ``box``."""
    x0, y0, x1, y1 = segments
    if len(x0) == 0:
        return 0.0
    t0, t1, ok = clip_segments(x0, y0, x1, y1, box)
    seg = np.hypot(x1 - x0, y1 - y0)
    return float(np.sum(np.where(ok, (t1 - t0) * seg, 0.0)))


__all__ = ["CLIP_GROW", "clip_polygon", "clip_polyline", "clip_segments", "inside", "length_inside"]
