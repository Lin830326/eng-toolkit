"""An independent reader of the coloured GLB that geometry_view writes: plain ``struct``, ``json`` and ``math``, nothing from
eng_toolkit, so the numbers in the file are checked by code that shares nothing with the writer or its read-back.

``read_view_glb`` checks the container while it reads (GLB header, chunk types and lengths, 4-byte alignment, every
buffer view inside the binary chunk and as long as its accessor needs, accessor min/max equal to the data, indices in
range) and returns the document with every accessor decoded.  Pure: no engine needed.
"""

import json
import math
import struct
from pathlib import Path

SIZES = {5125: ("I", 4), 5126: ("f", 4)}
COMPONENTS = {"SCALAR": 1, "VEC3": 3}


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def triangle_area(a, b, c) -> float:
    n = _cross(_sub(b, a), _sub(c, a))
    return 0.5 * math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)


def gltf_to_model_mm(p):
    """Inverse of the documented (x, y, z) mm -> (x, z, -y) / 1000 m."""
    return (1000.0 * p[0], -1000.0 * p[2], 1000.0 * p[1])


def read_view_glb(path: Path) -> dict:
    data = Path(path).read_bytes()
    magic, version, length = struct.unpack("<4sII", data[:12])
    assert magic == b"glTF" and version == 2 and length == len(data), "GLB header"
    json_length, json_type = struct.unpack("<I4s", data[12:20])
    assert json_type == b"JSON" and json_length % 4 == 0, "JSON chunk"
    text = data[20 : 20 + json_length]
    assert text.rstrip(b" ") == text.rstrip(), "the JSON chunk is padded with spaces only"
    document = json.loads(text)
    start = 20 + json_length
    bin_length, bin_type = struct.unpack("<I4s", data[start : start + 8])
    assert bin_type == b"BIN\x00" and bin_length % 4 == 0 and start + 8 + bin_length == len(data), "BIN chunk"
    blob = data[start + 8 :]
    assert document["buffers"] == [{"byteLength": bin_length}], "one buffer, the BIN chunk"

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
        rows = [tuple(flat[i : i + width]) for i in range(0, len(flat), width)]
        if rows:
            lows = [min(r[k] for r in rows) for k in range(width)]
            highs = [max(r[k] for r in rows) for k in range(width)]
            assert accessor["min"] == lows and accessor["max"] == highs, "accessor min/max are those of the data"
        decoded.append(rows)

    primitives = []
    for primitive in document["meshes"][0]["primitives"]:
        positions = decoded[primitive["attributes"]["POSITION"]]
        indices = [r[0] for r in decoded[primitive["indices"]]]
        assert all(0 <= i < len(positions) for i in indices), "indices in range"
        entry = {
            "mode": primitive["mode"],
            "material": document["materials"][primitive["material"]],
            "extras": primitive["extras"],
            "positions": positions,
            "indices": indices,
            "normals": decoded[primitive["attributes"]["NORMAL"]] if "NORMAL" in primitive["attributes"] else None,
        }
        if primitive["mode"] == 4:
            assert len(indices) % 3 == 0
            entry["triangles"] = [tuple(indices[i : i + 3]) for i in range(0, len(indices), 3)]
        else:
            assert primitive["mode"] == 1 and len(indices) % 2 == 0
        primitives.append(entry)
    return {"document": document, "primitives": primitives, "node": document["nodes"][0], "bytes": data}


def face_areas_mm2(parsed: dict) -> dict[str, float]:
    """Sum of the triangle areas of every face id listed in the triangle primitives' extras, in mm^2."""
    out = {}
    for p in parsed["primitives"]:
        if p["mode"] != 4:
            continue
        model = [gltf_to_model_mm(v) for v in p["positions"]]
        for face in p["extras"]["faces"]:
            first, count = face["first_triangle"], face["triangle_count"]
            out[face["id"]] = math.fsum(triangle_area(*(model[i] for i in t)) for t in p["triangles"][first : first + count])
    return out


def groups_of_faces(parsed: dict) -> dict[str, str]:
    """face id -> the group (colour) of the primitive that holds it."""
    return {face["id"]: p["extras"]["group"] for p in parsed["primitives"] if p["mode"] == 4 for face in p["extras"]["faces"]}
