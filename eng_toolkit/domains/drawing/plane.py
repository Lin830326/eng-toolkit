"""Plane geometry for reading drawings: arcs and bulges as points and lengths, convex hulls, affine maps, clustering.  Pure."""

from __future__ import annotations

import math
from collections.abc import Iterable

Pt = tuple[float, float]
Box = tuple[float, float, float, float]  # x0, y0, x1, y1
Affine = tuple[float, float, float, float, float, float]  # x' = a x + c y + e, y' = b x + d y + f
IDENTITY: Affine = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
STEP_DEG = 15.0


def arc_points(cx: float, cy: float, r: float, start_deg: float, sweep_deg: float) -> list[Pt]:
    """Points along an arc (positive sweep counterclockwise): every 15 degrees, both ends, and the axis extremes it passes."""
    n = max(1, math.ceil(abs(sweep_deg) / STEP_DEG))
    angles = [start_deg + sweep_deg * k / n for k in range(n + 1)]
    for axis in (0.0, 90.0, 180.0, 270.0):
        rel = (axis - start_deg) % 360.0 if sweep_deg >= 0 else (start_deg - axis) % 360.0
        if 0.0 < rel < abs(sweep_deg):
            angles.append(axis)
    return [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in angles]


def circle_points(cx: float, cy: float, r: float) -> list[Pt]:
    return arc_points(cx, cy, r, 0.0, 360.0)


def bulge_segment(p0: Pt, p1: Pt, bulge: float) -> tuple[float, list[Pt]]:
    """Length and points of the polyline segment p0 -> p1 with a DXF bulge (tangent of a quarter of the included angle)."""
    chord = math.dist(p0, p1)
    if abs(bulge) < 1e-12 or chord < 1e-12:
        return chord, [p0, p1]
    theta = 4.0 * math.atan(bulge)  # signed included angle
    radius = chord / (2.0 * math.sin(abs(theta) / 2.0))
    mid = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
    left = (-(p1[1] - p0[1]) / chord, (p1[0] - p0[0]) / chord)
    d = (chord / 2.0) / math.tan(theta / 2.0)
    centre = (mid[0] + left[0] * d, mid[1] + left[1] * d)
    start = math.degrees(math.atan2(p0[1] - centre[1], p0[0] - centre[0]))
    return radius * abs(theta), arc_points(centre[0], centre[1], radius, start, math.degrees(theta))


def polyline(vertices: list[tuple[float, float, float]], closed: bool) -> tuple[float, list[Pt]]:
    """Length and points of a polyline given as (x, y, bulge of the segment that starts here)."""
    length, points = 0.0, []
    count = len(vertices)
    for i in range(count if closed else count - 1):
        a, b = vertices[i], vertices[(i + 1) % count]
        seg_length, seg_points = bulge_segment((a[0], a[1]), (b[0], b[1]), a[2])
        length += seg_length
        points += seg_points
    if count == 1:
        points.append((vertices[0][0], vertices[0][1]))
    return length, points


def ellipse_points(cx: float, cy: float, major: Pt, ratio: float, start: float, end: float, segments: int = 64) -> tuple[float, list[Pt]]:
    """Length (by 64 chords) and points of an ellipse arc; ``start``/``end`` are parameters in radians."""
    sweep = (end - start) % (2.0 * math.pi)
    if sweep < 1e-12:
        sweep = 2.0 * math.pi
    minor = (-major[1] * ratio, major[0] * ratio)
    pts = []
    for k in range(segments + 1):
        t = start + sweep * k / segments
        pts.append((cx + major[0] * math.cos(t) + minor[0] * math.sin(t), cy + major[1] * math.cos(t) + minor[1] * math.sin(t)))
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:])), pts


def hull(points: Iterable[Pt]) -> list[Pt]:
    """Convex hull (Andrew's monotone chain); the hull of one or two distinct points is those points."""
    unique = sorted(set(points))
    if len(unique) <= 2:
        return unique

    def cross(o: Pt, a: Pt, b: Pt) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[Pt] = []
    for p in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[Pt] = []
    for p in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def box_of(points: Iterable[Pt]) -> Box | None:
    xs, ys = [], []
    for x, y in points:
        xs.append(x)
        ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def union(a: Box | None, b: Box | None) -> Box | None:
    if a is None or b is None:
        return a or b
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def compose(scale: tuple[float, float], rotation_deg: float, at: Pt, base: Pt = (0.0, 0.0)) -> Affine:
    """The map of a block reference: subtract the block base point, scale, rotate, then move to the insertion point."""
    c, s = math.cos(math.radians(rotation_deg)), math.sin(math.radians(rotation_deg))
    sx, sy = scale
    a, b, cc, d = c * sx, s * sx, -s * sy, c * sy
    return (a, b, cc, d, at[0] - (a * base[0] + cc * base[1]), at[1] - (b * base[0] + d * base[1]))


def apply(m: Affine, p: Pt) -> Pt:
    return (m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5])


def cluster(boxes: list[Box], gap: float, max_clusters: int = 500) -> list[tuple[Box, list[int]]] | None:
    """Group boxes that overlap or lie within ``gap`` of each other; None if there are more than ``max_clusters`` groups."""
    groups: list[tuple[Box, list[int]]] = []
    for index, box in enumerate(boxes):
        merged_box, members = box, [index]
        merging = True
        while merging:  # a grown box can reach groups it did not reach before
            merging = False
            keep: list[tuple[Box, list[int]]] = []
            for other, others in groups:
                near = other[0] - gap <= merged_box[2] and merged_box[0] - gap <= other[2] and other[1] - gap <= merged_box[3] and merged_box[1] - gap <= other[3]
                if near:
                    merged_box = union(merged_box, other)  # type: ignore[assignment]
                    if len(others) > len(members):  # extend the longer list: copying a long one for every box would be quadratic
                        others.extend(members)
                        members = others
                    else:
                        members.extend(others)
                    merging = True
                else:
                    keep.append((other, others))
            groups = keep
        groups = groups + [(merged_box, members)]
        if len(groups) > max_clusters:
            return None
    return groups
