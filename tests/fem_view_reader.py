"""An independent reader of the GLB that fem_view writes: plain ``struct``, ``json`` and ``math``, nothing from eng_toolkit, so the
numbers in the file are checked by code that shares nothing with the writer or its read-back.

``read_fem_glb`` checks the container while it reads (GLB header, chunk types and lengths, 4-byte alignment, every buffer view
inside the binary chunk and exactly as long as its accessor, accessor min/max equal to the data, indices in range, no NaN or
infinity anywhere, COLOR_0 within [0, 1], extras valid JSON) and returns every primitive with its attributes decoded.

Also here, for the same reason: an independent count of the boundary facets of a Gmsh msh2 file, the colour maps re-derived from
their published formulas, and the inversion of a colour back to a value.
"""

import json
import math
import struct
from pathlib import Path

SIZES = {5125: ("I", 4), 5126: ("f", 4)}
COMPONENTS = {"SCALAR": 1, "VEC3": 3}


def read_fem_glb(path) -> dict:
    data = Path(path).read_bytes()
    magic, version, length = struct.unpack("<4sII", data[:12])
    assert magic == b"glTF" and version == 2 and length == len(data), "GLB header"
    json_length, json_type = struct.unpack("<I4s", data[12:20])
    assert json_type == b"JSON" and json_length % 4 == 0, "JSON chunk"
    text = data[20 : 20 + json_length]
    assert text.rstrip(b" ") == text.rstrip(), "the JSON chunk is padded with spaces only"

    def no_constant(token):
        raise AssertionError(f"{token} in the GLB JSON")

    document = json.loads(text, parse_constant=no_constant)
    start = 20 + json_length
    bin_length, bin_type = struct.unpack("<I4s", data[start : start + 8])
    assert bin_type == b"BIN\x00" and bin_length % 4 == 0 and start + 8 + bin_length == len(data), "BIN chunk"
    blob = data[start + 8 :]
    assert document["buffers"] == [{"byteLength": bin_length}], "one buffer, the BIN chunk"
    assert document["asset"]["version"] == "2.0"

    decoded = []
    for accessor in document["accessors"]:
        view = document["bufferViews"][accessor["bufferView"]]
        offset = view["byteOffset"] + accessor.get("byteOffset", 0)
        letter, size = SIZES[accessor["componentType"]]
        width = COMPONENTS[accessor["type"]]
        need = accessor["count"] * width * size
        assert view["byteOffset"] % 4 == 0 and offset % 4 == 0, "buffer views start on a 4-byte boundary"
        assert view["byteLength"] == need and offset + need <= len(blob), "a buffer view holds exactly its accessor"
        flat = struct.unpack(f"<{accessor['count'] * width}{letter}", blob[offset : offset + need])
        assert all(math.isfinite(v) for v in flat), "no NaN or infinity in the binary data"
        rows = [tuple(flat[i : i + width]) for i in range(0, len(flat), width)]
        assert rows, "no empty accessor"
        lows = [min(r[k] for r in rows) for k in range(width)]
        highs = [max(r[k] for r in rows) for k in range(width)]
        assert accessor["min"] == lows and accessor["max"] == highs, "accessor min/max are those of the data"
        decoded.append(rows)

    primitives = []
    for primitive in document["meshes"][0]["primitives"]:
        attributes = {name: decoded[index] for name, index in primitive["attributes"].items()}
        positions = attributes["POSITION"]
        indices = [r[0] for r in decoded[primitive["indices"]]]
        assert all(0 <= i < len(positions) for i in indices), "indices in range"
        for rows in attributes.values():
            assert len(rows) == len(positions), "every attribute has one entry per vertex"
        if "COLOR_0" in attributes:
            assert all(0.0 <= c <= 1.0 for row in attributes["COLOR_0"] for c in row), "COLOR_0 within [0, 1]"
        json.dumps(primitive.get("extras", {}), allow_nan=False)
        entry = {"mode": primitive["mode"], "material": document["materials"][primitive["material"]], "extras": primitive["extras"], "attributes": attributes, "indices": indices}
        if primitive["mode"] == 4:
            assert len(indices) % 3 == 0
            entry["triangles"] = [tuple(indices[i : i + 3]) for i in range(0, len(indices), 3)]
        else:
            assert primitive["mode"] == 1 and len(indices) % 2 == 0
        primitives.append(entry)
    node = document["nodes"][0]
    json.dumps(node["extras"], allow_nan=False)
    return {"document": document, "primitives": primitives, "node": node, "bytes": data}


def gltf_to_model_mm(p):
    """Inverse of the documented (x, y, z) mm -> (x, z, -y) / 1000 m."""
    return (1000.0 * p[0], -1000.0 * p[2], 1000.0 * p[1])


def signed_volume(points, triangles) -> float:
    total = 0.0
    for a, b, c in triangles:
        pa, pb, pc = points[a], points[b], points[c]
        cross = (pb[1] * pc[2] - pb[2] * pc[1], pb[2] * pc[0] - pb[0] * pc[2], pb[0] * pc[1] - pb[1] * pc[0])
        total += pa[0] * cross[0] + pa[1] * cross[1] + pa[2] * cross[2]
    return total / 6.0


def boundary_facet_count(msh_path) -> int:
    """Faces (corner triples) of the 10-node tetrahedra (type 11) of an msh2 file that belong to exactly one element."""
    lines = Path(msh_path).read_text().splitlines()
    j = lines.index("$Elements")
    seen: dict[frozenset, int] = {}
    for row in lines[j + 2 : j + 2 + int(lines[j + 1])]:
        parts = row.split()
        if int(parts[1]) != 11:
            continue
        corners = [int(v) for v in parts[3 + int(parts[2]) :][:4]]
        for skip in range(4):
            face = frozenset(c for k, c in enumerate(corners) if k != skip)
            seen[face] = seen.get(face, 0) + 1
    return sum(1 for n in seen.values() if n == 1)


# --------------------------------------------------------------------------- colour maps from their formulas


def cubehelix(lam, start=3.0, rotations=-0.65, hue=1.3, gamma=1.0):
    """Green (2011), Bull. Astr. Soc. India 39, 289, eq. 2: the documented parameters of fem_view's sequential map."""
    phi = 2 * math.pi * (start / 3 + rotations * lam)
    lg = lam**gamma
    a = hue * lg * (1 - lg) / 2
    r = lg + a * (-0.14861 * math.cos(phi) + 1.78277 * math.sin(phi))
    g = lg + a * (-0.29227 * math.cos(phi) - 0.90649 * math.sin(phi))
    b = lg + a * (1.97294 * math.cos(phi))
    return tuple(min(1.0, max(0.0, v)) for v in (r, g, b))


def sequential_table(points=65):
    return [cubehelix(0.10 + 0.78 * k / (points - 1)) for k in range(points)]


def linear_to_srgb(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def invert(rgb_linear, table):
    """Position t in [0, 1] whose colour (linear interpolation of the sRGB table) is closest to the given linear RGB, and the
    distance to it (sRGB units)."""
    target = [linear_to_srgb(c) for c in rgb_linear]
    best = (float("inf"), 0.0)
    n = len(table) - 1
    for k in range(n):
        a, b = table[k], table[k + 1]
        d = [b[i] - a[i] for i in range(3)]
        dd = sum(x * x for x in d)
        s = 0.0 if dd == 0 else max(0.0, min(1.0, sum((target[i] - a[i]) * d[i] for i in range(3)) / dd))
        gap = math.sqrt(sum((a[i] + s * d[i] - target[i]) ** 2 for i in range(3)))
        if gap < best[0]:
            best = (gap, (k + s) / n)
    return best[1], best[0]
