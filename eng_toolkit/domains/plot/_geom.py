"""Exact geometric clipping in pixel space (before any number is formatted), and lengths of drawn lines inside a box.

* Segments: Liang-Barsky, vectorised over all segments of a polyline.  A polyline is cut into the runs that lie inside the
  rectangle; the points where it crosses the rectangle are computed from the exact floating-point endpoints, so the visible part
  is drawn exactly where it belongs however far away the other endpoint is (no per-coordinate clamping).
* Polygons (fill to zero): Sutherland-Hodgman against the four edges.
* The rectangle is the plot area grown by ``CLIP_GROW`` px on every side; the SVG clip path then trims the line width at the frame.
"""

from __future__ import annotations

import numpy as np

CLIP_GROW = 4.0


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


def clip_polyline(xs: np.ndarray, ys: np.ndarray, rect: tuple[float, float, float, float]) -> list[tuple[np.ndarray, np.ndarray]]:
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
    runs: list[tuple[list[float], list[float]]] = []
    current: tuple[list[float], list[float]] | None = None
    for i in range(len(x0)):
        if not ok[i]:
            current = None
            continue
        sx, sy = x0[i] + t0[i] * dx[i], y0[i] + t0[i] * dy[i]
        ex, ey = x0[i] + t1[i] * dx[i], y0[i] + t1[i] * dy[i]
        if current is None or t0[i] > 0.0:
            current = ([float(sx)], [float(sy)])
            runs.append(current)
        current[0].append(float(ex))
        current[1].append(float(ey))
        if t1[i] < 1.0:
            current = None
    return [(np.array(a), np.array(b)) for a, b in runs if len(a) >= 2]


def clip_polygon(xs: np.ndarray, ys: np.ndarray, rect: tuple[float, float, float, float]) -> tuple[np.ndarray, np.ndarray]:
    """Sutherland-Hodgman: the part of a closed polygon inside ``rect``."""
    pts = list(zip(xs.tolist(), ys.tolist()))
    xmin, ymin, xmax, ymax = rect
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
    return np.array([p[0] for p in pts]), np.array([p[1] for p in pts])


def length_inside(segments: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray], box: tuple[float, float, float, float]) -> float:
    """Total length (px) of the given segments inside ``box``."""
    x0, y0, x1, y1 = segments
    if len(x0) == 0:
        return 0.0
    t0, t1, ok = clip_segments(x0, y0, x1, y1, box)
    seg = np.hypot(x1 - x0, y1 - y0)
    return float(np.sum(np.where(ok, (t1 - t0) * seg, 0.0)))


__all__ = ["CLIP_GROW", "clip_polygon", "clip_polyline", "clip_segments", "inside", "length_inside"]
