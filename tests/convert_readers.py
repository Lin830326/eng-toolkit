"""Independent readers and builders for the geometry_convert tests: plain ``struct`` and ``json``, nothing from eng_toolkit.

They exist so that the numbers in a converted file are checked by code that shares nothing with the writer or with the
tool's own read-back.  Pure: no engine needed.
"""

import json
import math
import struct
from pathlib import Path

Tri = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]


def box_triangles(x: float, y: float, z: float) -> list[Tri]:
    """The 12 triangles of the box [0,x] x [0,y] x [0,z], every one counter-clockwise seen from outside (checked by hand: see the cross products)."""
    quads = [
        ((0, 0, 0), (0, y, 0), (x, y, 0), (x, 0, 0)),  # bottom, normal -z: (0,y,0) x (x,y,0) = (0, 0, -xy)
        ((0, 0, z), (x, 0, z), (x, y, z), (0, y, z)),  # top, +z
        ((0, 0, 0), (x, 0, 0), (x, 0, z), (0, 0, z)),  # y = 0, -y
        ((0, y, 0), (0, y, z), (x, y, z), (x, y, 0)),  # y = y, +y
        ((0, 0, 0), (0, 0, z), (0, y, z), (0, y, 0)),  # x = 0, -x
        ((x, 0, 0), (x, y, 0), (x, y, z), (x, 0, z)),  # x = x, +x
    ]
    out: list[Tri] = []
    for a, b, c, d in quads:
        out.append((a, b, c))
        out.append((a, c, d))
    return [tuple(tuple(float(v) for v in p) for p in t) for t in out]  # type: ignore[misc]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def signed_volume(triangles) -> float:
    """Sum over the triangles of dot(a, cross(b, c)) / 6, tetrahedra from the origin."""
    return math.fsum(_dot(a, _cross(b, c)) for a, b, c in triangles) / 6.0


def total_area(triangles) -> float:
    parts = []
    for a, b, c in triangles:
        ab = tuple(b[i] - a[i] for i in range(3))
        ac = tuple(c[i] - a[i] for i in range(3))
        n = _cross(ab, ac)
        parts.append(0.5 * math.sqrt(_dot(n, n)))
    return math.fsum(parts)


def binary_stl(triangles, header: bytes = b"hand written".ljust(80, b" ")) -> bytes:
    body = b"".join(struct.pack("<12fH", 0, 0, 0, *(c for p in t for c in p), 0) for t in triangles)
    return header + struct.pack("<I", len(triangles)) + body


def ascii_stl(triangles, name: str = "part") -> str:
    lines = [f"solid {name}"]
    for a, b, c in triangles:
        lines += ["  facet normal 0 0 0", "    outer loop"]
        lines += [f"      vertex {p[0]!r} {p[1]!r} {p[2]!r}" for p in (a, b, c)]
        lines += ["    endloop", "  endfacet"]
    lines.append(f"endsolid {name}")
    return "\n".join(lines) + "\n"


def read_binary_stl(path: Path) -> list[Tri]:
    data = Path(path).read_bytes()
    (count,) = struct.unpack("<I", data[80:84])
    assert len(data) == 84 + 50 * count, "a binary STL is 84 + 50 n bytes"
    triangles = []
    for i in range(count):
        values = struct.unpack("<12f", data[84 + 50 * i : 84 + 50 * i + 48])
        triangles.append((values[3:6], values[6:9], values[9:12]))
    return triangles


def read_glb(path: Path) -> dict:
    """The JSON document, the accessor min/max, the vertex list (glTF axes, metres) and the triangle index triples of a one-mesh GLB."""
    data = Path(path).read_bytes()
    magic, version, length = struct.unpack("<4sII", data[:12])
    assert magic == b"glTF" and version == 2 and length == len(data)
    json_length, json_type = struct.unpack("<I4s", data[12:20])
    assert json_type == b"JSON" and json_length % 4 == 0
    document = json.loads(data[20 : 20 + json_length])
    start = 20 + json_length
    bin_length, bin_type = struct.unpack("<I4s", data[start : start + 8])
    assert bin_type == b"BIN\x00" and bin_length % 4 == 0 and start + 8 + bin_length == len(data)
    blob = data[start + 8 :]
    primitive = document["meshes"][0]["primitives"][0]
    assert primitive["mode"] == 4  # triangles

    def slab(accessor_index: int):
        accessor = document["accessors"][accessor_index]
        view = document["bufferViews"][accessor["bufferView"]]
        return accessor, view["byteOffset"] + accessor.get("byteOffset", 0)

    accessor, offset = slab(primitive["indices"])
    assert accessor["componentType"] == 5125 and accessor["type"] == "SCALAR"
    indices = list(struct.unpack(f"<{accessor['count']}I", blob[offset : offset + 4 * accessor["count"]]))
    accessor, offset = slab(primitive["attributes"]["POSITION"])
    assert accessor["componentType"] == 5126 and accessor["type"] == "VEC3"
    flat = struct.unpack(f"<{3 * accessor['count']}f", blob[offset : offset + 12 * accessor["count"]])
    vertices = [flat[i : i + 3] for i in range(0, len(flat), 3)]
    return {
        "document": document,
        "min": accessor["min"],
        "max": accessor["max"],
        "vertices": vertices,
        "triangles": [tuple(indices[i : i + 3]) for i in range(0, len(indices), 3)],
    }


def glb_volume_mm3(parsed: dict) -> float:
    """Volume of a GLB's mesh in mm^3: the volume in m^3 times 1e9."""
    triangles = [tuple(parsed["vertices"][i] for i in t) for t in parsed["triangles"]]
    return signed_volume(triangles) * 1e9
