"""Hole callouts and ordinate coordinates: what to say about the holes of a model and in which view.  Pure arithmetic.

Every number comes from the geometry: ``brep.inventory`` lists the holes (diameter, direction, entry point, depth or
through), this module groups them, writes the callout text and finds the position of each hole in each view.  The
declared intent of ``features.json`` adds only the fit and the thread, and only to a hole that
:func:`eng_toolkit.domains.drawing.features.bind` matched.

* Holes are grouped by diameter, direction, through or depth, fit and thread.  Each group gets one callout:
  ``Ø10 THRU``, ``4× Ø6.6 THRU``, ``Ø5 DEEP 12``, ``Ø10 H7 THRU``, ``Ø5 M6x1-6H THRU``.  ISO symbols that Arial lacks
  (the depth and counterbore signs) are written as words.
* A group is called out in the first requested view that looks along its axis, preferring a view that sees the hole's
  entry (the viewing direction points the way the hole was drilled).  A group no requested view looks along is not
  called out and the result says which view to add.
* In that view the holes are positioned by ordinate dimensions from the view's datum, the lower left corner of the
  visible geometry: one ordinate per distinct coordinate, plus 0 for the datum.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from eng_toolkit.brep import HoleInfo, Projection
from eng_toolkit.domains.drawing.features import IntentHole

Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]

DIAMETER = "Ø"
TIMES = "×"
MAX_GROUPS_PER_VIEW = 6  # callouts one view carries; the margin of the layout is reserved for these
MAX_ORDINATES = 16  # distinct coordinates per axis and view (the datum not counted)
ALONG = 1.0 - 1e-6  # |cos| of the angle between a view's direction and a hole axis that counts as looking along it
KEY_DIGITS = 3  # millimetres


def format_mm(value: float) -> str:
    """A length as the callouts print it: two decimals at most, no trailing zeros (the same rule as the dimension style)."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


@dataclass(frozen=True)
class HoleGroup:
    count: int
    diameter_mm: float
    axis: Vec3
    thru: bool
    depth_mm: float | None
    fit: str
    thread: str
    hole_ids: tuple[str, ...]
    entries: tuple[Vec3, ...]  # entry points, in the order of ``hole_ids``
    intent: bool  # a declared hole of the bound features file was matched to every hole of the group

    @property
    def text(self) -> str:
        words = [f"{DIAMETER}{format_mm(self.diameter_mm)}", self.fit, self.thread, "THRU" if self.thru else f"DEEP {format_mm(self.depth_mm or 0.0)}"]
        return (f"{self.count}{TIMES} " if self.count > 1 else "") + " ".join(w for w in words if w)


@dataclass(frozen=True)
class CalloutPlan:
    group: HoleGroup
    center: Vec2  # of the first hole, in view coordinates (mm, the coordinates of the projection)


@dataclass(frozen=True)
class OrdinatePlan:
    axis: Literal["x", "y"]
    value: float  # view coordinate of the hole centres (mm)
    anchor: Vec2  # a hole centre with that coordinate, or the datum corner: where the extension line starts


@dataclass(frozen=True)
class ViewHolePlan:
    view: str
    datum: Vec2  # lower left corner of the visible geometry, in view coordinates
    callouts: tuple[CalloutPlan, ...]
    ordinates: tuple[OrdinatePlan, ...]


def _signed(axis: Vec3) -> Vec3:
    return tuple(round(c, 6) + 0.0 for c in axis)  # type: ignore[return-value]


def _canonical(axis: Vec3) -> Vec3:
    """The axis, flipped when needed so that its first non-zero component is positive: the direction without the sense."""
    a = _signed(axis)
    first = next((c for c in a if c != 0.0), 0.0)
    return tuple(-c + 0.0 for c in a) if first < 0 else a  # type: ignore[return-value]


def group_holes(holes: Sequence[HoleInfo], intent: dict[str, IntentHole] | None = None) -> list[HoleGroup]:
    """Holes grouped for one callout each; the groups are ordered by diameter, then direction, then first hole id."""
    intent = intent or {}
    buckets: dict[tuple, list[HoleInfo]] = {}
    for hole in holes:
        declared = intent.get(hole.id)
        axis = _canonical(hole.axis) if hole.thru else _signed(hole.axis)  # a blind hole has a side it is drilled from
        key = (
            round(hole.diameter_mm, KEY_DIGITS),
            axis,
            hole.thru,
            None if hole.thru else round(hole.depth_mm, KEY_DIGITS),
            declared.fit if declared else "",
            declared.thread if declared else "",
        )
        buckets.setdefault(key, []).append(hole)
    groups = []
    for (diameter, axis, thru, depth, fit, thread), members in buckets.items():
        members.sort(key=lambda h: h.id)
        groups.append(
            HoleGroup(
                count=len(members),
                diameter_mm=diameter,
                axis=axis,
                thru=thru,
                depth_mm=depth,
                fit=fit,
                thread=thread,
                hole_ids=tuple(h.id for h in members),
                entries=tuple(h.entry_mm for h in members),
                intent=all(h.id in intent for h in members),
            )
        )
    return sorted(groups, key=lambda g: (g.diameter_mm, g.axis, g.thru, g.depth_mm or 0.0, g.hole_ids))


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _looks_along(p: Projection, axis: Vec3) -> float | None:
    """The cosine of the angle between the viewing direction and the axis when the view looks along it, else ``None``."""
    cosine = _dot(p.direction, axis)
    return cosine if abs(cosine) >= ALONG else None


def _view_of(group: HoleGroup, projections: Sequence[Projection]) -> Projection | None:
    candidates = [(p, c) for p in projections if (c := _looks_along(p, group.axis)) is not None]
    if not candidates:
        return None
    seen = [p for p, c in candidates if c > 0.0]  # the hole is entered from the side the eye is on
    return (seen or [p for p, _ in candidates])[0]


def _uv(p: Projection, point: Vec3) -> Vec2:
    return (round(_dot(point, p.x_axis), 6) + 0.0, round(_dot(point, p.y_axis), 6) + 0.0)


@dataclass(frozen=True)
class Plan:
    views: tuple[ViewHolePlan, ...]
    groups: tuple[HoleGroup, ...]
    placed: dict[tuple[str, ...], str]  # hole ids of a group -> the view it is called out in
    warnings: tuple[str, ...]


def plan(groups: Sequence[HoleGroup], projections: Sequence[Projection]) -> Plan:
    """Which view carries which callout, and the ordinates of its holes.  ``warnings`` say what was left out and why."""
    warnings: list[str] = []
    by_view: dict[str, list[HoleGroup]] = {}
    for group in groups:
        p = _view_of(group, projections)
        if p is None:
            warnings.append(
                f"Holes {', '.join(group.hole_ids)} ({group.text}): no requested view looks along their axis "
                f"{tuple(group.axis)}, so they are not called out; add a view that does (the top view for a vertical hole)"
            )
            continue
        by_view.setdefault(p.view, []).append(group)
    view_plans: list[ViewHolePlan] = []
    placed: dict[tuple[str, ...], str] = {}
    for p in projections:
        listed = by_view.get(p.view, [])
        if not listed:
            continue
        if len(listed) > MAX_GROUPS_PER_VIEW:
            left = listed[MAX_GROUPS_PER_VIEW:]
            warnings.append(
                f"{p.view} view: {len(listed)} different hole groups, more than the {MAX_GROUPS_PER_VIEW} a view carries; not called out: "
                + "; ".join(f"{g.text} ({', '.join(g.hole_ids)})" for g in left)
            )
            listed = listed[:MAX_GROUPS_PER_VIEW]
        callouts = tuple(CalloutPlan(g, _uv(p, g.entries[0])) for g in listed)
        for g in listed:
            placed[g.hole_ids] = p.view
        centres = [_uv(p, e) for g in listed for e in g.entries]
        ordinates = _ordinates(p, centres, warnings)
        view_plans.append(ViewHolePlan(p.view, (p.bbox_min[0], p.bbox_min[1]), callouts, ordinates))
    return Plan(tuple(view_plans), tuple(groups), placed, tuple(warnings))


def _ordinates(p: Projection, centres: list[Vec2], warnings: list[str]) -> tuple[OrdinatePlan, ...]:
    datum = (p.bbox_min[0], p.bbox_min[1])
    out: list[OrdinatePlan] = []
    for axis, index in (("x", 0), ("y", 1)):
        other = 1 - index
        anchors: dict[float, Vec2] = {}
        for c in sorted(centres, key=lambda c: (c[index], c[other])):
            key = round(c[index], KEY_DIGITS)
            if key not in anchors or c[other] < anchors[key][other]:
                anchors[key] = c  # the hole nearest the datum edge, so the extension line is the shortest
        values = sorted(k for k in anchors if abs(k - datum[index]) > 10 ** -KEY_DIGITS)
        if len(values) > MAX_ORDINATES:
            warnings.append(
                f"{p.view} view: the holes lie at {len(values)} different {axis} coordinates, more than the {MAX_ORDINATES} that can be "
                f"dimensioned legibly; no {axis} ordinates are drawn there (the hole positions are in data.callouts)"
            )
            continue
        out.append(OrdinatePlan(axis, datum[index], datum))  # type: ignore[arg-type]
        out += [OrdinatePlan(axis, value, anchors[value]) for value in values]  # type: ignore[arg-type]
    return tuple(out)


__all__ = [
    "CalloutPlan",
    "HoleGroup",
    "OrdinatePlan",
    "Plan",
    "ViewHolePlan",
    "format_mm",
    "group_holes",
    "plan",
]
