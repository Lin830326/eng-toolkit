"""A closed outline of lines and circular arcs in a sketch plane: the data, and the check that it is a valid profile.

Pure Python (no engine).  An arc is given by its radius (the shorter way round, turning left or right along the
direction of travel) or by a point it passes through; either way it is reduced to three points, which is what the
kernel builds it from.  :func:`check_path` finds the problems a solid made from the outline would have: not closed,
a zero-length piece, an arc whose radius is too small or whose points are collinear, and any self-crossing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np

Vec2 = tuple[float, float]
MAX_PATH_SEGMENTS = 64
CLOSE_TOL_MM = 1e-6  # the last point must equal the start within this
ARC_STEP_RAD = math.radians(5.0)  # arcs are tested for self-crossing as polylines of at most this much turn per piece
_REL = 1e-9


@dataclass(frozen=True)
class PathSegment:
    to: Vec2  # where the piece ends; it starts where the previous one ended
    radius: float | None = None  # an arc of this radius, the shorter way round (at most a half turn)
    turn: Literal["left", "right"] = "left"  # left = counter-clockwise along the direction of travel
    via: Vec2 | None = None  # an arc through this point (any size), instead of a radius


@dataclass(frozen=True)
class Path2:
    start: Vec2
    segments: tuple[PathSegment, ...]


@dataclass(frozen=True)
class PathProblem:
    index: int | None  # segment index, None for the path as a whole
    field: str  # the field to point at: "to", "radius", "via", "segments" or "start"
    message: str


@dataclass(frozen=True)
class Piece:
    """One resolved piece: a line (``via`` None) or an arc through three points."""

    start: Vec2
    end: Vec2
    via: Vec2 | None = None


def _circle(a: Vec2, v: Vec2, b: Vec2) -> tuple[Vec2, float] | None:
    """Centre and radius of the circle through three points, None when they are (nearly) collinear."""
    ax, ay = a
    bx, by = v
    cx, cy = b
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    scale = max(abs(ax - bx), abs(ay - by), abs(bx - cx), abs(by - cy), 1e-300)
    if abs(d) <= 1e-9 * scale * scale:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    return (ux, uy), math.hypot(ax - ux, ay - uy)


def radius_arc_via(a: Vec2, b: Vec2, radius: float, turn: str) -> Vec2 | None:
    """The mid-point of the shorter arc of ``radius`` from ``a`` to ``b`` turning ``turn``; None when the radius is too small."""
    chord = math.dist(a, b)
    half = chord / 2.0
    if not chord > 0.0 or half > radius * (1.0 + _REL):
        return None
    h = math.sqrt(max(0.0, radius * radius - half * half))
    dx, dy = (b[0] - a[0]) / chord, (b[1] - a[1]) / chord
    left = (-dy, dx)
    mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    sagitta = radius - h  # the arc bulges away from its centre: to the right of the chord when turning left
    sign = -1.0 if turn == "left" else 1.0
    return (mid[0] + sign * sagitta * left[0], mid[1] + sign * sagitta * left[1])


def resolve(path: Path2) -> tuple[list[Piece], list[PathProblem]]:
    """The pieces of the outline, and every problem found while resolving them (an empty list means resolvable)."""
    problems: list[PathProblem] = []
    pieces: list[Piece] = []
    here = path.start
    if not 2 <= len(path.segments) <= MAX_PATH_SEGMENTS:
        problems.append(PathProblem(None, "segments", f"a path has 2 to {MAX_PATH_SEGMENTS} segments, this one has {len(path.segments)}"))
        return pieces, problems
    for i, seg in enumerate(path.segments):
        if not math.dist(here, seg.to) > CLOSE_TOL_MM:
            problems.append(PathProblem(i, "to", f"the segment ends where it starts ({here[0]:g}, {here[1]:g}) mm: it has no length"))
            here = seg.to
            continue
        if seg.via is not None:
            if seg.radius is not None:
                problems.append(PathProblem(i, "via", "give either radius or via, not both"))
            elif not (math.dist(seg.via, here) > CLOSE_TOL_MM and math.dist(seg.via, seg.to) > CLOSE_TOL_MM) or _circle(here, seg.via, seg.to) is None:
                problems.append(PathProblem(i, "via", "the start, via and end points are on one line or coincide, so no arc passes through them"))
            else:
                pieces.append(Piece(here, seg.to, seg.via))
        elif seg.radius is not None:
            via = radius_arc_via(here, seg.to, seg.radius, seg.turn)
            if via is None:
                problems.append(
                    PathProblem(
                        i,
                        "radius",
                        f"an arc of radius {seg.radius:g} mm cannot span the chord of {math.dist(here, seg.to):g} mm: the radius must be at least half the chord",
                    )
                )
            else:
                pieces.append(Piece(here, seg.to, via))
        else:
            pieces.append(Piece(here, seg.to))
        here = seg.to
    if not math.dist(here, path.start) <= CLOSE_TOL_MM:
        problems.append(
            PathProblem(
                len(path.segments) - 1,
                "to",
                f"the path is not closed: it ends at ({here[0]:g}, {here[1]:g}) mm but starts at ({path.start[0]:g}, {path.start[1]:g}) mm; the last segment must end at start",
            )
        )
    return pieces, problems


def _flatten(piece: Piece) -> list[Vec2]:
    """The points of a piece as a polyline: its two ends for a line, points at most 5 degrees of turn apart for an arc."""
    if piece.via is None:
        return [piece.start, piece.end]
    centre, radius = _circle(piece.start, piece.via, piece.end)  # type: ignore[misc]
    a0 = math.atan2(piece.start[1] - centre[1], piece.start[0] - centre[0])
    two_pi = 2.0 * math.pi
    d_via = (math.atan2(piece.via[1] - centre[1], piece.via[0] - centre[0]) - a0) % two_pi
    d_end = (math.atan2(piece.end[1] - centre[1], piece.end[0] - centre[0]) - a0) % two_pi
    sweep = d_end if d_via < d_end else d_end - two_pi  # counter-clockwise when the via point comes first going that way
    steps = max(2, math.ceil(abs(sweep) / ARC_STEP_RAD))
    points = [piece.start]
    for k in range(1, steps):
        angle = a0 + sweep * k / steps
        points.append((centre[0] + radius * math.cos(angle), centre[1] + radius * math.sin(angle)))
    points.append(piece.end)
    return points


def polyline(pieces: Sequence[Piece]) -> np.ndarray:
    """The closed outline as an (n, 2) array of points, without repeating the first at the end."""
    points: list[Vec2] = []
    for piece in pieces:
        points.extend(_flatten(piece)[:-1])
    return np.asarray(points, dtype=np.float64).reshape(-1, 2)


def self_crossings(points: np.ndarray) -> list[tuple[int, int]]:
    """Pairs of edges ``(i, j)`` of a closed polygon that cross or touch, or that fold back along each other."""
    n = len(points)
    if n < 3:
        return []
    a = points
    b = np.roll(points, -1, axis=0)
    extent = float(np.ptp(points, axis=0).max()) or 1.0
    eps = _REL * extent * extent
    found: list[tuple[int, int]] = []
    for i in range(n):
        p, q = a[i], b[i]
        d = q - p
        j = np.arange(i + 1, n)
        if not len(j):
            continue
        r, s = a[j], b[j]
        e = s - r
        o1 = d[0] * (r[:, 1] - p[1]) - d[1] * (r[:, 0] - p[0])
        o2 = d[0] * (s[:, 1] - p[1]) - d[1] * (s[:, 0] - p[0])
        o3 = e[:, 0] * (p[1] - r[:, 1]) - e[:, 1] * (p[0] - r[:, 0])
        o4 = e[:, 0] * (q[1] - r[:, 1]) - e[:, 1] * (q[0] - r[:, 0])
        straddle = (np.minimum(o1, o2) <= eps) & (np.maximum(o1, o2) >= -eps) & (np.minimum(o3, o4) <= eps) & (np.maximum(o3, o4) >= -eps)
        # collinear pairs only count when their extents overlap
        collinear = (np.abs(o1) <= eps) & (np.abs(o2) <= eps)
        lo = np.minimum(p, q) - np.sqrt(eps)
        hi = np.maximum(p, q) + np.sqrt(eps)
        overlap = ~((np.maximum(r, s) < lo).any(axis=1) | (np.minimum(r, s) > hi).any(axis=1))
        hit = straddle & (~collinear | overlap)
        for jj, flag in zip(j, hit):
            if not flag:
                continue
            adjacent = jj == i + 1 or (i == 0 and jj == n - 1)
            if adjacent:
                # neighbours share an end point; they only count when they fold back along each other
                nxt = b[jj] - a[jj] if jj == i + 1 else d
                prv = d if jj == i + 1 else b[jj] - a[jj]
                cross = float(prv[0] * nxt[1] - prv[1] * nxt[0])
                dot = float(prv @ nxt)
                if abs(cross) <= 1e-9 * float(np.linalg.norm(prv) * np.linalg.norm(nxt)) and dot < 0.0:
                    found.append((i, int(jj)))
                continue
            found.append((i, int(jj)))
    return found


def check_path(path: Path2) -> list[PathProblem]:
    """Everything wrong with an outline: closure, lengths, arcs, self-crossing, and an area of zero."""
    pieces, problems = resolve(path)
    if problems:
        return problems
    points = polyline(pieces)
    owner: list[int] = []
    for k, piece in enumerate(pieces):
        owner.extend([k] * (len(_flatten(piece)) - 1))
    crossings = self_crossings(points)
    if crossings:
        i, j = crossings[0]
        first, second = sorted((owner[i], owner[j]))
        where = f"segments {first} and {second}" if first != second else f"segment {first}"
        problems.append(PathProblem(second, "to", f"the outline crosses itself or folds back on itself ({where}); a profile must be one simple closed curve"))
        return problems
    x, y = points[:, 0], points[:, 1]
    if abs(float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))) / 2.0 <= 1e-12 * float(np.ptp(points, axis=0).max()) ** 2:
        problems.append(PathProblem(None, "segments", "the outline encloses no area"))
    return problems
