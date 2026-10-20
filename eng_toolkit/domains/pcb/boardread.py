"""Our own reader of a ``.kicad_pcb``: what is on the board, counted and measured from the file, with no KiCad installed.

Every number comes from the file and from arithmetic done here; nothing is asked of KiCad.  Works on the old format
(KiCad 5/6/7: ``(net 1 "GND")`` declarations and ``(net 1)`` references, ``fp_text`` for reference and value) and on the
current one (KiCad 8 to 10: nets by name, ``property "Reference"``).

What is read: the layer table, nets, footprints (reference, value, position, side, pads), tracks (``segment`` and ``arc``),
vias, zones (outline only), and the shapes on ``Edge.Cuts`` (``gr_line``, ``gr_rect``, ``gr_circle``, ``gr_arc``, ``gr_poly``,
``gr_curve``, and the same ``fp_*`` shapes inside footprints).  Everything else is counted by name in ``ignored_elements`` and
otherwise left alone.  An element that is damaged (a missing number, a name where a coordinate should be) is skipped and
listed in ``parse_issues`` rather than stopping the read; a number beyond :data:`MAX_ABS_MM` stops it (``input.out_of_range``).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Iterator

from eng_toolkit.domains.pcb import sexp
from eng_toolkit.domains.pcb.sexp import child, children, head
from eng_toolkit.errors import ToolFailure

MAX_ABS_MM = 10_000.0  # KiCad itself cannot place anything beyond +-2147 mm (a 32-bit nanometre coordinate)
MAX_ANGLE_DEG = 1.0e9
MAX_ELEMENTS = 1_000_000  # top-level elements (tracks, vias, footprints, zones, shapes ...)
MAX_ISSUES = 50
NAME_CHARS = 200
COPPER_TYPES = ("signal", "power", "mixed", "jumper")
ARC_SEGMENTS = 64  # sampling of a Bezier curve on Edge.Cuts (an arc is exact)

_NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?\Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


class Issues:
    """Damaged elements that were skipped; ``count`` is exact, ``items`` holds the first :data:`MAX_ISSUES`."""

    def __init__(self) -> None:
        self.count = 0
        self.items: list[str] = []

    def add(self, text: str) -> None:
        self.count += 1
        if len(self.items) < MAX_ISSUES:
            self.items.append(text)


class Skip(Exception):
    """One element cannot be read; the reader notes it and goes on."""


def clean(text: str, limit: int = NAME_CHARS) -> str:
    """Text from the file as it may appear in a result: control characters replaced, cut to ``limit`` characters."""
    text = _CONTROL.sub(" ", text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def num(raw: Any, what: str, limit: float = MAX_ABS_MM) -> float:
    """A finite number within +-``limit``.  A token that is not a decimal number raises :class:`Skip`; one that is a number but
    infinite or beyond the limit raises ``input.out_of_range`` (the whole file is refused)."""
    if not isinstance(raw, str) or not _NUMBER.match(raw):
        raise Skip(f"{what} is {raw!r}, not a number")
    value = float(raw)
    if not math.isfinite(value) or abs(value) > limit:
        raise ToolFailure(
            "input.out_of_range",
            f"{what} is {clean(raw, 40)}, beyond the +-{limit:g} mm this reader accepts (KiCad itself cannot place anything beyond +-2147 mm)",
            {"value": clean(raw, 40), "limit_mm": limit},
        )
    return value


def point(node: list | None, what: str) -> tuple[float, float]:
    if node is None or len(node) < 3:
        raise Skip(f"{what} has no coordinates")
    return num(node[1], f"{what} x"), num(node[2], f"{what} y")


def _pts(node: list | None, what: str) -> list[tuple[float, float]]:
    if node is None:
        raise Skip(f"{what} has no pts")
    return [point(xy, f"{what} point") for xy in children(node, "xy")]


# --------------------------------------------------------------------------- geometry


def arc_geometry(start, mid, end) -> tuple[float, float, float, float, float, bool] | None:
    """``(cx, cy, radius, sweep, start_angle, counter_clockwise)`` of the circular arc through three points, or ``None`` if they are collinear.

    The arc runs from ``start`` through ``mid`` to ``end``.  ``sweep`` (radians, 0 to 2*pi) is the angle it covers; the direction is
    read from the numbers as they stand (x right, y up), and the same reading is used for every other function here, so a board
    whose y axis points down gives the same lengths and extents.
    """
    (x1, y1), (x2, y2), (x3, y3) = start, mid, end
    d = 2.0 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    scale = max(abs(x1), abs(x2), abs(x3), abs(y1), abs(y2), abs(y3), 1e-9)
    if abs(d) < 1e-12 * scale * scale:
        return None
    a, b, c = x1 * x1 + y1 * y1, x2 * x2 + y2 * y2, x3 * x3 + y3 * y3
    cx = (a * (y2 - y3) + b * (y3 - y1) + c * (y1 - y2)) / d
    cy = (a * (x3 - x2) + b * (x1 - x3) + c * (x2 - x1)) / d
    radius = math.hypot(x1 - cx, y1 - cy)
    a1, a3 = math.atan2(y1 - cy, x1 - cx), math.atan2(y3 - cy, x3 - cx)
    ccw = (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1) > 0
    two_pi = 2.0 * math.pi
    sweep = (a3 - a1) % two_pi if ccw else (a1 - a3) % two_pi
    return cx, cy, radius, sweep, a1, ccw


def arc_length(start, mid, end) -> float:
    geometry = arc_geometry(start, mid, end)
    if geometry is None:
        return math.hypot(end[0] - start[0], end[1] - start[1])
    return geometry[2] * geometry[3]


def arc_points(start, mid, end, step_deg: float = 1.0) -> list[tuple[float, float]]:
    """Points along the arc, one per ``step_deg`` degrees (at least 8), both ends included."""
    geometry = arc_geometry(start, mid, end)
    if geometry is None:
        return [start, mid, end]
    cx, cy, radius, sweep, a1, ccw = geometry
    steps = max(8, int(math.degrees(sweep) / step_deg))
    sign = 1.0 if ccw else -1.0
    return [(cx + radius * math.cos(a1 + sign * sweep * i / steps), cy + radius * math.sin(a1 + sign * sweep * i / steps)) for i in range(steps + 1)]


def arc_bbox(start, mid, end) -> tuple[float, float, float, float]:
    """``(min_x, min_y, max_x, max_y)`` of the arc through three points, exact: the end points plus every axis extreme the arc passes."""
    xs = [start[0], end[0]]
    ys = [start[1], end[1]]
    geometry = arc_geometry(start, mid, end)
    if geometry is not None:
        cx, cy, radius, sweep, a1, ccw = geometry
        two_pi = 2.0 * math.pi
        for k, (dx, dy) in enumerate(((1, 0), (0, 1), (-1, 0), (0, -1))):
            cardinal = k * math.pi / 2.0
            reached = (cardinal - a1) % two_pi if ccw else (a1 - cardinal) % two_pi
            if reached <= sweep:
                xs.append(cx + radius * dx)
                ys.append(cy + radius * dy)
    return min(xs), min(ys), max(xs), max(ys)


def bezier_points(p0, p1, p2, p3, steps: int = ARC_SEGMENTS) -> list[tuple[float, float]]:
    out = []
    for i in range(steps + 1):
        t = i / steps
        u = 1.0 - t
        out.append(
            (
                u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
                u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
            )
        )
    return out


def polygon_area(points: list[tuple[float, float]]) -> float:
    """Absolute area by the shoelace formula (a self-crossing outline gives the net signed sum)."""
    if len(points) < 3:
        return 0.0
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def rotate(x: float, y: float, degrees: float) -> tuple[float, float]:
    """KiCad's rotation in board coordinates (y points down): a positive angle turns counter-clockwise on the screen."""
    if degrees == 0:
        return x, y
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    return x * c + y * s, -x * s + y * c


# --------------------------------------------------------------------------- the reader


class Box:
    def __init__(self) -> None:
        self.min_x = self.min_y = math.inf
        self.max_x = self.max_y = -math.inf
        self.items = 0

    def add(self, x: float, y: float) -> None:
        self.min_x, self.max_x = min(self.min_x, x), max(self.max_x, x)
        self.min_y, self.max_y = min(self.min_y, y), max(self.max_y, y)

    def add_box(self, box: tuple[float, float, float, float]) -> None:
        self.add(box[0], box[1])
        self.add(box[2], box[3])

    def result(self) -> dict[str, float] | None:
        if not self.items or self.min_x > self.max_x:
            return None
        return {
            "min_x": self.min_x,
            "min_y": self.min_y,
            "max_x": self.max_x,
            "max_y": self.max_y,
            "width": self.max_x - self.min_x,
            "height": self.max_y - self.min_y,
        }


class Reader:
    def __init__(self, max_list: int) -> None:
        self.max_list = max_list
        self.issues = Issues()
        self.header: dict[str, str] = {}
        self.thickness: float | None = None
        self.layers: list[dict[str, str]] = []
        self.declared: dict[str, str] = {}  # net id -> name, from "(net 1 "GND")" declarations
        self.names: set[str] = set()
        self.net_stats: dict[str, dict[str, float]] = {}
        self.outline = Box()
        self.footprints: list[dict[str, Any]] = []
        self.footprint_count = 0
        self.sides = Counter()
        self.pad_types = Counter()
        self.pad_drills: Counter = Counter()
        self.tracks: dict[str, dict[str, float]] = {}
        self.track_kinds = Counter()
        self.track_widths: Counter = Counter()
        self.vias = Counter()
        self.via_drills: Counter = Counter()
        self.via_sizes: Counter = Counter()
        self.zones: list[dict[str, Any]] = []
        self.zone_count = 0
        self.ignored = Counter()
        self.elements = 0

    # -- helpers
    def net_of(self, node: list | None, what: str) -> str:
        """The net name of an element: ``(net "GND")``, ``(net 3)`` through the declarations, or ``(net 3 "GND")``; ``''`` for none."""
        net = child(node, "net") if node else None
        if net is None or len(net) < 2:
            return ""
        if len(net) >= 3:
            return net[2]
        raw = net[1]
        if self.declared:
            if raw in self.declared:
                return self.declared[raw]
            self.issues.add(f"{what} refers to net {clean(raw, 40)!r}, which is not declared")
            return ""
        return raw

    def stat(self, net: str) -> dict[str, float]:
        if net:
            self.names.add(net)
        return self.net_stats.setdefault(net, {"pads": 0, "tracks": 0, "vias": 0, "track_length_mm": 0.0})

    def layer_of(self, node: list) -> str:
        layer = child(node, "layer")
        return clean(layer[1], 40) if layer and len(layer) > 1 else ""

    # -- elements
    def header_item(self, node: list) -> None:
        name = head(node)
        if name in ("version", "generator", "generator_version") and len(node) > 1:
            self.header[name] = clean(node[1], 60)
        elif name == "general":
            thickness = child(node, "thickness")
            if thickness and len(thickness) > 1:
                try:
                    self.thickness = num(thickness[1], "board thickness")
                except Skip as exc:
                    self.issues.add(str(exc))
        elif name == "layers":
            for layer in node[1:]:
                if isinstance(layer, list) and len(layer) >= 3:
                    entry = {"id": clean(layer[0], 10), "name": clean(layer[1], 40), "type": clean(layer[2], 20)}
                    if len(layer) > 3:
                        entry["alias"] = clean(layer[3], 40)
                    self.layers.append(entry)
                else:
                    self.issues.add("a layer entry is not (id name type)")
        elif name == "net" and len(node) >= 3:
            self.declared[node[1]] = node[2]
            if node[2]:
                self.names.add(node[2])

    def segment(self, node: list) -> None:
        start, end = point(child(node, "start"), "segment start"), point(child(node, "end"), "segment end")
        width = num((child(node, "width") or [None, None])[1], "segment width")
        self.add_track("segment", self.layer_of(node), width, math.hypot(end[0] - start[0], end[1] - start[1]), self.net_of(node, "a segment"))

    def arc_track(self, node: list) -> None:
        pts = [point(child(node, n), f"arc {n}") for n in ("start", "mid", "end")]
        width = num((child(node, "width") or [None, None])[1], "arc width")
        self.add_track("arc", self.layer_of(node), width, arc_length(*pts), self.net_of(node, "an arc"))

    def add_track(self, kind: str, layer: str, width: float, length: float, net: str) -> None:
        self.track_kinds[kind] += 1
        entry = self.tracks.setdefault(layer, {"count": 0, "length_mm": 0.0})
        entry["count"] += 1
        entry["length_mm"] += length
        self.track_widths[round(width, 6)] += 1
        stat = self.stat(net)
        stat["tracks"] += 1
        stat["track_length_mm"] += length

    def via(self, node: list) -> None:
        point(child(node, "at"), "via position")
        size = num((child(node, "size") or [None, None])[1], "via size")
        drill_node = child(node, "drill")
        drill = num(drill_node[1], "via drill") if drill_node and len(drill_node) > 1 else None
        kind = next((a for a in node[1:] if a in ("blind", "micro")), "through")
        self.vias[kind] += 1
        self.via_sizes[round(size, 6)] += 1
        if drill is not None:
            self.via_drills[round(drill, 6)] += 1
        self.stat(self.net_of(node, "a via"))["vias"] += 1

    def zone(self, node: list) -> None:
        self.zone_count += 1
        layers = [clean(layer, 40) for layer in (child(node, "layers") or [])[1:] if isinstance(layer, str)]
        if not layers and self.layer_of(node):
            layers = [self.layer_of(node)]
        net_name = child(node, "net_name")
        net = clean(net_name[1]) if net_name and len(net_name) > 1 else clean(self.net_of(node, "a zone"))
        if net:
            self.names.add(net)
        outline = child(node, "polygon")
        points = _pts(child(outline, "pts"), "zone outline") if outline else []
        name_node = child(node, "name")
        entry: dict[str, Any] = {
            "name": clean(name_node[1]) if name_node and len(name_node) > 1 else "",
            "net": net,
            "layers": layers,
            "keepout": child(node, "keepout") is not None,
            "filled": child(node, "filled_polygon") is not None,
            "outline_points": len(points),
            "outline_area_mm2": polygon_area(points),
        }
        if len(self.zones) < self.max_list:
            self.zones.append(entry)

    def footprint(self, node: list) -> None:
        self.footprint_count += 1
        name = clean(node[1]) if len(node) > 1 and isinstance(node[1], str) else ""
        x, y = 0.0, 0.0
        angle = 0.0
        at = child(node, "at")
        if at is not None:
            x, y = point(at, "footprint position")
            if len(at) > 3:
                angle = num(at[3], "footprint angle", MAX_ANGLE_DEG)
        layer = self.layer_of(node)
        side = "back" if layer.startswith("B.") else "front"
        self.sides[side] += 1
        reference = value = ""
        for prop in children(node, "property"):
            if len(prop) >= 3 and prop[1] == "Reference":
                reference = prop[2]
            elif len(prop) >= 3 and prop[1] == "Value":
                value = prop[2]
        for text in children(node, "fp_text"):
            if len(text) >= 3 and text[1] == "reference" and not reference:
                reference = text[2]
            elif len(text) >= 3 and text[1] == "value" and not value:
                value = text[2]
        pads = 0
        for pad in children(node, "pad"):
            pads += 1
            kind = pad[2] if len(pad) > 2 and isinstance(pad[2], str) else "other"
            self.pad_types[kind] += 1
            drill = child(pad, "drill")
            if drill is not None:
                for token in drill[1:]:
                    if isinstance(token, str) and token != "oval":
                        try:
                            self.pad_drills[round(num(token, "pad drill"), 6)] += 1
                        except Skip:
                            pass
                        break
            self.stat(self.net_of(pad, "a pad"))["pads"] += 1
        for kind in ("fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly", "fp_curve"):
            for shape in children(node, kind):
                if self.layer_of(shape) == "Edge.Cuts":
                    self.edge_shape(shape, "gr_" + kind[3:], (x, y, angle))
        if len(self.footprints) < self.max_list:
            self.footprints.append(
                {
                    "reference": clean(reference),
                    "value": clean(value),
                    "footprint": name,
                    "x_mm": x,
                    "y_mm": y,
                    "angle_deg": angle,
                    "side": side,
                    "pads": pads,
                }
            )

    def edge_shape(self, node: list, kind: str, frame: tuple[float, float, float] | None = None) -> None:
        """Add one shape on Edge.Cuts to the outline box; ``frame`` is a footprint's ``(x, y, angle)`` for its own shapes."""

        def place(p: tuple[float, float]) -> tuple[float, float]:
            if frame is None:
                return p
            rx, ry = rotate(p[0], p[1], frame[2])
            return frame[0] + rx, frame[1] + ry

        pts: list[tuple[float, float]]
        if kind in ("gr_line", "gr_rect"):
            pts = [point(child(node, "start"), kind + " start"), point(child(node, "end"), kind + " end")]
            if kind == "gr_rect" and frame is not None and frame[2]:  # a rotated rectangle: all four corners
                (x1, y1), (x2, y2) = pts
                pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        elif kind == "gr_circle":
            centre, rim = point(child(node, "center"), "circle centre"), point(child(node, "end"), "circle end")
            r = math.hypot(rim[0] - centre[0], rim[1] - centre[1])
            pts = [(centre[0] - r, centre[1] - r), (centre[0] + r, centre[1] + r)]
            if frame is not None and frame[2]:
                pts = [(centre[0] + r * math.cos(t * math.pi / 2), centre[1] + r * math.sin(t * math.pi / 2)) for t in range(4)]
        elif kind == "gr_arc":
            a = [point(child(node, n), "arc " + n) for n in ("start", "mid", "end")]
            if frame is None or not frame[2]:
                box = arc_bbox(*[place(p) for p in a])
                self.outline.items += 1
                self.outline.add_box(box)
                return
            pts = arc_points(*a)  # a rotated arc: sampled every degree
        elif kind == "gr_poly":
            pts = _pts(child(node, "pts"), "polygon")
        elif kind == "gr_curve":
            c = _pts(child(node, "pts"), "curve")
            if len(c) != 4:
                raise Skip("a curve needs 4 points")
            pts = bezier_points(*c)
        else:
            raise Skip(f"unknown shape {kind}")
        self.outline.items += 1
        for p in pts:
            self.outline.add(*place(p))

    def feed(self, node: list) -> None:
        name = head(node)
        self.elements += 1
        if self.elements > MAX_ELEMENTS:
            raise ToolFailure(
                "input.too_large",
                f"the board holds more than {MAX_ELEMENTS} elements; this reader's limit protects the server's memory and time",
                {"elements": self.elements, "limit": MAX_ELEMENTS},
            )
        try:
            if name in ("version", "generator", "generator_version", "general", "layers", "net"):
                self.header_item(node)
            elif name == "segment":
                self.segment(node)
            elif name == "arc":
                self.arc_track(node)
            elif name == "via":
                self.via(node)
            elif name == "zone":
                self.zone(node)
            elif name in ("footprint", "module"):
                self.footprint(node)
            elif name in ("gr_line", "gr_rect", "gr_circle", "gr_arc", "gr_poly", "gr_curve"):
                if self.layer_of(node) == "Edge.Cuts":
                    self.edge_shape(node, name)
                else:
                    self.ignored[name] += 1
            else:
                self.ignored[name or "(unnamed)"] += 1
        except Skip as exc:
            self.issues.add(f"{name or 'element'} #{self.elements}: {exc}")


def _widths(counter: Counter) -> dict[str, Any]:
    if not counter:
        return {"min": None, "max": None, "distinct": []}
    return {"min": min(counter), "max": max(counter), "distinct": [{"mm": k, "count": counter[k]} for k in sorted(counter)][:50]}


def read_board(text: str, *, max_list: int = 200) -> dict[str, Any]:
    """Everything this reader knows about the board in ``text``.  Raises ``ToolFailure`` for a file that is not a KiCad board
    (``input.invalid``), is too deep or too large (``input.invalid`` / ``input.too_large``), or holds a number beyond the limit
    (``input.out_of_range``)."""
    reader = Reader(max_list)
    try:
        items = sexp.top_level(text)
        root = next(items)
        if head(root) != "kicad_pcb":
            raise ToolFailure(
                "input.invalid",
                f"not a KiCad board: the file's first list is {clean(head(root), 40)!r}, not 'kicad_pcb'"
                + (" (this looks like a schematic: use pcb_erc)" if head(root) == "kicad_sch" else ""),
                {"first_list": clean(head(root), 40)},
            )
        for node in items:
            reader.feed(node)
    except sexp.SexpError as exc:
        family = "input.too_large" if exc.kind == "too_large" else "input.invalid"
        raise ToolFailure(family, f"not a readable KiCad board: {exc.message}", {"reason": exc.kind, "line": exc.line, "column": exc.column, **exc.details}) from exc
    return summarize(reader)


def summarize(r: Reader) -> dict[str, Any]:
    copper = [layer["name"] for layer in r.layers if layer["type"] in COPPER_TYPES]
    track_total = sum(v["length_mm"] for v in r.tracks.values())
    nets = sorted(n for n in r.names if n)
    net_rows = [
        {"name": clean(n), **{k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.net_stats.get(n, {}).items()}} for n in nets[: r.max_list]
    ]
    via_total = sum(r.vias.values())
    return {
        "file": {"format_version": r.header.get("version", ""), "generator": r.header.get("generator", ""), "generator_version": r.header.get("generator_version", "")},
        "layers": {"copper_count": len(copper), "copper": copper, "all": [layer["name"] for layer in r.layers]},
        "board": {"thickness_mm": r.thickness},
        "outline": {
            "extents_mm": r.outline.result(),
            "edge_shapes": r.outline.items,
            "measured_on": "the centre line of the shapes on Edge.Cuts, stroke width not added",
        },
        "nets": {"count": len(nets), "listed": len(net_rows), "list": net_rows},
        "footprints": {
            "count": r.footprint_count,
            "front": r.sides["front"],
            "back": r.sides["back"],
            "listed": len(r.footprints),
            "list": r.footprints,
        },
        "pads": {"count": sum(r.pad_types.values()), "by_type": dict(sorted(r.pad_types.items())), "drills": _widths(r.pad_drills)},
        "tracks": {
            "count": sum(r.track_kinds.values()),
            "segments": r.track_kinds["segment"],
            "arcs": r.track_kinds["arc"],
            "total_length_mm": track_total,
            "by_layer": {k: {"count": int(v["count"]), "length_mm": v["length_mm"]} for k, v in sorted(r.tracks.items())},
            "width_mm": _widths(r.track_widths),
        },
        "vias": {
            "count": via_total,
            "through": r.vias["through"],
            "blind": r.vias["blind"],
            "micro": r.vias["micro"],
            "drill_mm": _widths(r.via_drills),
            "size_mm": _widths(r.via_sizes),
        },
        "zones": {"count": r.zone_count, "listed": len(r.zones), "list": r.zones},
        "parse_issues": {"count": r.issues.count, "list": r.issues.items},
        "ignored_elements": dict(sorted(r.ignored.items())),
    }


def iter_finite(value: Any) -> Iterator[float]:
    """Every float inside nested dicts and lists (for the final check that no inf or NaN leaves the module)."""
    if isinstance(value, float):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_finite(item)


__all__ = ["MAX_ABS_MM", "MAX_ELEMENTS", "arc_bbox", "arc_length", "bezier_points", "clean", "iter_finite", "polygon_area", "read_board", "rotate"]
