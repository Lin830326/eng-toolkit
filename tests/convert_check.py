"""Independent readers of the files drawing_convert writes: PDF content streams, SVG paths and PNG pixels, standard library only.

The converter writes these files itself (no PDF, SVG or PNG library), so the tests read them back with code that shares nothing with
the writers: a tokeniser for the PDF operators the converter uses (m l c S f* w d), an SVG path parser that measures lengths
numerically, and a PNG decoder for the 8-bit greyscale files with filter type 0.
"""

from __future__ import annotations

import math
import re
import struct
import xml.etree.ElementTree as ET
import zlib

PT_PER_MM = 72.0 / 25.4


# --------------------------------------------------------------------------- PDF


def pdf_media_box(data: bytes) -> tuple[float, float]:
    found = re.search(rb"/MediaBox \[0 0 ([0-9.]+) ([0-9.]+)\]", data)
    assert found, "no MediaBox"
    return float(found.group(1)), float(found.group(2))


def pdf_content(data: bytes) -> str:
    start = data.index(b"stream\n") + len(b"stream\n")
    end = data.index(b"\nendstream")
    return data[start:end].decode("ascii")


def pdf_ops(data: bytes) -> list[tuple[str, list[float | list[float]]]]:
    """(operator, operands) in file order; a dash array is one operand, a list."""
    tokens = pdf_content(data).split()
    out: list[tuple[str, list]] = []
    operands: list = []
    array: list[float] | None = None
    for token in tokens:
        if token.startswith("["):
            array = []
            token = token[1:]
            if token.endswith("]"):
                token = token[:-1]
                if token:
                    array.append(float(token))
                operands.append(array)
                array = None
                continue
        elif array is not None and token.endswith("]"):
            token = token[:-1]
            if token:
                array.append(float(token))
            operands.append(array)
            array = None
            continue
        if array is not None:
            if token:
                array.append(float(token))
            continue
        if re.fullmatch(r"-?[0-9.]+", token):
            operands.append(float(token))
        else:
            out.append((token, operands))
            operands = []
    return out


def pdf_strokes(data: bytes) -> list[dict]:
    """Every stroked path: its points (segments and cubic Bezier control points) in points, line width in points, dash array in points."""
    width, dash = 1.0, []
    strokes, current, here = [], [], None
    for op, args in pdf_ops(data):
        if op == "w":
            width = args[0]
        elif op == "d":
            dash = args[0]
        elif op == "m":
            here = (args[0], args[1])
            current.append(("m", here))
        elif op == "l":
            here = (args[0], args[1])
            current.append(("l", here))
        elif op == "c":
            current.append(("c", (args[0], args[1]), (args[2], args[3]), (args[4], args[5])))
            here = (args[4], args[5])
        elif op == "S":
            strokes.append({"path": current, "width": width, "dash": dash})
            current = []
        elif op in ("f*", "f"):
            current = []
    return strokes


def bezier_length(p0, p1, p2, p3, steps: int = 400) -> float:
    total, last = 0.0, p0
    for i in range(1, steps + 1):
        t = i / steps
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t**2, t**3
        point = (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])
        total += math.dist(last, point)
        last = point
    return total


def stroke_length(stroke: dict) -> float:
    """Length of a stroked path in points, curves measured by 400 steps each."""
    total, here, start = 0.0, None, None
    for item in stroke["path"]:
        kind = item[0]
        if kind == "m":
            here = start = item[1]
        elif kind == "l":
            total += math.dist(here, item[1])
            here = item[1]
        elif kind == "c":
            total += bezier_length(here, item[1], item[2], item[3])
            here = item[3]
    return total


def pdf_length_pt(data: bytes) -> float:
    return sum(stroke_length(s) for s in pdf_strokes(data))


# --------------------------------------------------------------------------- SVG

_SVG = "{http://www.w3.org/2000/svg}"


def svg_root(text: str | bytes):
    return ET.fromstring(text)


def svg_size_mm(text: str | bytes) -> tuple[float, float]:
    root = svg_root(text)
    return float(root.get("width").removesuffix("mm")), float(root.get("height").removesuffix("mm"))


def svg_paths(text: str | bytes) -> list[dict]:
    root = svg_root(text)
    out = []
    for element in root.iter(_SVG + "path"):
        out.append(
            {
                "d": element.get("d"),
                "stroke_width": float(element.get("stroke-width")) if element.get("stroke-width") else None,
                "dash": [float(v) for v in element.get("stroke-dasharray").split()] if element.get("stroke-dasharray") else [],
                "filled": element.get("fill") == "#000",
            }
        )
    return out


def path_points(d: str) -> list[list[tuple[float, float]]]:
    """Subpaths of an SVG path made of M, L, C, Q and Z, each as the list of points the curve passes (curves sampled 200 times)."""
    tokens = re.findall(r"[MLCQZ]|-?[0-9.]+", d)
    paths: list[list[tuple[float, float]]] = []
    i, here = 0, (0.0, 0.0)
    while i < len(tokens):
        cmd = tokens[i]
        i += 1
        if cmd == "M":
            here = (float(tokens[i]), float(tokens[i + 1]))
            i += 2
            paths.append([here])
        elif cmd == "L":
            here = (float(tokens[i]), float(tokens[i + 1]))
            i += 2
            paths[-1].append(here)
        elif cmd == "C":
            p = [(float(tokens[i + k]), float(tokens[i + k + 1])) for k in (0, 2, 4)]
            i += 6
            for s in range(1, 201):
                t = s / 200
                a, b, c, e = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t**2, t**3
                paths[-1].append((a * here[0] + b * p[0][0] + c * p[1][0] + e * p[2][0], a * here[1] + b * p[0][1] + c * p[1][1] + e * p[2][1]))
            here = p[2]
        elif cmd == "Q":
            p = [(float(tokens[i + k]), float(tokens[i + k + 1])) for k in (0, 2)]
            i += 4
            for s in range(1, 201):
                t = s / 200
                paths[-1].append(((1 - t) ** 2 * here[0] + 2 * (1 - t) * t * p[0][0] + t**2 * p[1][0], (1 - t) ** 2 * here[1] + 2 * (1 - t) * t * p[0][1] + t**2 * p[1][1]))
            here = p[1]
        elif cmd == "Z":
            paths[-1].append(paths[-1][0])
    return paths


def svg_length_mm(text: str | bytes) -> float:
    """Length of all stroked (not filled) path data in millimetres."""
    total = 0.0
    for path in svg_paths(text):
        if path["filled"]:
            continue
        for sub in path_points(path["d"]):
            total += sum(math.dist(a, b) for a, b in zip(sub, sub[1:]))
    return total


# --------------------------------------------------------------------------- PNG


def png_decode(data: bytes) -> tuple[int, int, list[bytes]]:
    """(width, height, rows) of an 8-bit greyscale PNG whose rows all use filter type 0."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, size = 8, b"", None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        assert struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])[0] == zlib.crc32(kind + body) & 0xFFFFFFFF
        if kind == b"IHDR":
            w, h, depth, colour = struct.unpack(">IIBB", body[:10])
            assert (depth, colour) == (8, 0)
            size = (w, h)
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    w, h = size
    rows = []
    for y in range(h):
        row = raw[y * (w + 1) : (y + 1) * (w + 1)]
        assert row[0] == 0
        rows.append(row[1:])
    return w, h, rows


def dark_pixels(rows: list[bytes], threshold: int = 128) -> list[tuple[int, int]]:
    return [(x, y) for y, row in enumerate(rows) for x, v in enumerate(row) if v < threshold]
