"""A minimal DXF reader for the tests, written from the DXF reference and independent of ezdxf (it imports nothing but the standard library).

It reads group-code pairs and gives the entities of the ENTITIES section as records of LINE, ARC, CIRCLE, ELLIPSE,
LWPOLYLINE, TEXT and DIMENSION with their layer and coordinates, the entities of each block, the header variables, and the
LAYER, LTYPE and DIMSTYLE table entries.  It checks the writer against the file format, not against the writer's own library.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path


def pairs(text: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    return [(int(lines[i].strip()), lines[i + 1]) for i in range(0, len(lines) - 1, 2)]


@dataclass
class Entity:
    type: str
    tags: list[tuple[int, str]] = field(default_factory=list)

    def get(self, code: int, default: str | None = None) -> str | None:
        return next((v for c, v in self.tags if c == code), default)

    def number(self, code: int, default: float | None = None) -> float | None:
        v = self.get(code)
        return default if v is None else float(v)

    def numbers(self, code: int) -> list[float]:
        return [float(v) for c, v in self.tags if c == code]

    @property
    def layer(self) -> str:
        return self.get(8, "0") or "0"

    @property
    def text(self) -> str:
        return "".join(v for c, v in self.tags if c in (3, 1))

    def point(self, x: int, y: int) -> tuple[float, float]:
        return (self.number(x), self.number(y))  # type: ignore[return-value]

    # ---- geometry
    def length(self) -> float:
        """Length of a LINE, ARC, CIRCLE or LWPOLYLINE."""
        if self.type == "LINE":
            return math.dist(self.point(10, 20), self.point(11, 21))
        if self.type == "CIRCLE":
            return 2 * math.pi * self.number(40)  # type: ignore[operator]
        if self.type == "ARC":
            sweep = (self.number(51) - self.number(50)) % 360.0  # type: ignore[operator]
            return math.radians(sweep) * self.number(40)  # type: ignore[operator]
        if self.type == "LWPOLYLINE":
            points = self.vertices()
            total = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
            return total + (math.dist(points[-1], points[0]) if int(self.number(70, 0)) & 1 else 0.0)  # type: ignore[call-overload]
        raise ValueError(f"no length for {self.type}")

    def vertices(self) -> list[tuple[float, float]]:
        xs = [float(v) for c, v in self.tags if c == 10]
        ys = [float(v) for c, v in self.tags if c == 20]
        return list(zip(xs, ys))

    # ---- DIMENSION
    @property
    def dim_kind(self) -> int:
        return int(self.number(70, 0)) & 0x0F  # type: ignore[call-overload]

    def overrides(self) -> dict[int, float]:
        """Dimension style overrides stored in the DSTYLE extended data: DXF variable group code -> value."""
        found: dict[int, float] = {}
        inside = False
        pending: int | None = None
        for code, value in self.tags:
            if code in (1000, 1001):
                inside = value.strip() == "DSTYLE"
            elif inside and code == 1070 and pending is None:
                pending = int(value)
            elif inside and code in (1070, 1040) and pending is not None:
                found[pending] = float(value)
                pending = None
        return found


@dataclass
class Dxf:
    header: dict[str, list[tuple[int, str]]]
    entities: list[Entity]
    blocks: dict[str, list[Entity]]
    layers: dict[str, Entity]
    linetypes: dict[str, Entity]
    dimstyles: dict[str, Entity]
    classes: list[tuple[str, str]]  # (DXF record name, C++ class name) in file order

    def of(self, type_: str, layer: str | None = None) -> list[Entity]:
        return [e for e in self.entities if e.type == type_ and (layer is None or e.layer == layer)]

    def on(self, layer: str) -> list[Entity]:
        return [e for e in self.entities if e.layer == layer]

    def total_length(self, layer: str, views: tuple[float, float, float, float] | None = None) -> float:
        """Summed LINE, ARC, CIRCLE and LWPOLYLINE length on a layer, optionally of the entities inside a box (x0, y0, x1, y1)."""
        return sum(e.length() for e in self.on(layer) if e.type in ("LINE", "ARC", "CIRCLE", "LWPOLYLINE") and (views is None or inside(e, views)))

    def header_value(self, name: str, code: int) -> str | None:
        return next((v for c, v in self.header.get(name, []) if c == code), None)


def anchor(e: Entity) -> tuple[float, float]:
    """A point that decides which view an entity belongs to: its first point."""
    return e.point(10, 20)


def inside(e: Entity, box: tuple[float, float, float, float]) -> bool:
    x, y = anchor(e)
    if e.type == "LWPOLYLINE":
        pts = e.vertices()
        x, y = pts[0]
    return box[0] - 1e-6 <= x <= box[2] + 1e-6 and box[1] - 1e-6 <= y <= box[3] + 1e-6


def split(tags: list[tuple[int, str]]) -> list[Entity]:
    entities: list[Entity] = []
    for code, value in tags:
        if code == 0:
            entities.append(Entity(value.strip()))
        elif entities:
            entities[-1].tags.append((code, value))
    return entities


def read(source: str | Path | bytes) -> Dxf:
    text = source.decode("utf-8") if isinstance(source, bytes) else Path(source).read_text(encoding="utf-8") if isinstance(source, Path) else source
    sections: dict[str, list[tuple[int, str]]] = {}
    current: list[tuple[int, str]] | None = None
    tags = pairs(text)
    i = 0
    while i < len(tags):
        code, value = tags[i]
        if code == 0 and value.strip() == "SECTION":
            name = tags[i + 1][1].strip()
            current = sections.setdefault(name, [])
            i += 2
            continue
        if code == 0 and value.strip() == "ENDSEC":
            current = None
        elif current is not None:
            current.append((code, value))
        i += 1

    header: dict[str, list[tuple[int, str]]] = {}
    name = None
    for code, value in sections.get("HEADER", []):
        if code == 9:
            name = value.strip()
            header[name] = []
        elif name is not None:
            header[name].append((code, value))

    blocks: dict[str, list[Entity]] = {}
    block: str | None = None
    for e in split(sections.get("BLOCKS", [])):
        if e.type == "BLOCK":
            block = e.get(2)
            blocks[block] = []
        elif e.type == "ENDBLK":
            block = None
        elif block is not None:
            blocks[block].append(e)

    tables: dict[str, dict[str, Entity]] = {"LAYER": {}, "LTYPE": {}, "DIMSTYLE": {}}
    for e in split(sections.get("TABLES", [])):
        if e.type in tables:
            tables[e.type][e.get(2) or ""] = e
    classes = [(e.get(1) or "", e.get(2) or "") for e in split(sections.get("CLASSES", [])) if e.type == "CLASS"]
    return Dxf(header, split(sections.get("ENTITIES", [])), blocks, tables["LAYER"], tables["LTYPE"], tables["DIMSTYLE"], classes)
