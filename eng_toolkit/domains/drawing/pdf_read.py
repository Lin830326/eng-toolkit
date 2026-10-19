"""Reads a PDF file into plain data: the pdfminer.six side of ``drawing_read`` (the one module that imports pdfminer).

``read(path, limits)`` runs in a child process (:mod:`eng_toolkit.isolated`) because the file is untrusted.  It reports
the pages and their size in millimetres, the text lines with their positions, and statistics of the vector drawing
(paths by line width and dash, lengths, extents).  A PDF made of glyph outlines, like the ones this toolkit writes, has
no text to read, and the result says so instead of pretending.

Nothing in the file runs or is followed.  pdfminer does not execute JavaScript, open links or launch programs; this
module additionally *reports* them (found by walking the parsed objects, so object streams are covered, and by a scan of
the raw bytes) so that the caller knows the file carries them.
"""

from __future__ import annotations

import math
import os
import re
import statistics
from collections import Counter
from contextlib import contextmanager
from typing import Any, Iterator

from eng_toolkit.domains.drawing import reading as Rd
from eng_toolkit.errors import EngineUnavailable, ToolFailure
from eng_toolkit.licenses import install_hint

MODULE, DIST = "pdfminer", "pdfminer.six"
MM_PER_PT = Rd.MM_PER_PT
MAX_PAGES_COUNTED = 1_000_000
MAX_WARNINGS = 30
BEZIER_STEPS = 32
MAX_LOOK_DEPTH = 40
MAX_LOOK_ITEMS = 100_000
MAX_STRING = 4000
FIGURE_DEPTH = 8
# dictionary keys that mean active content, and values of /S (action type) that do
ACTIVE_KEYS = {
    "JS": "JavaScript",
    "JavaScript": "JavaScript",
    "OpenAction": "OpenAction",
    "AA": "AdditionalActions",
    "URI": "URI",
    "Launch": "Launch",
    "EmbeddedFiles": "EmbeddedFile",
    "EmbeddedFile": "EmbeddedFile",
    "RichMedia": "RichMedia",
    "XFA": "XFA",
}
ACTIVE_ACTIONS = {"JavaScript": "JavaScript", "Launch": "Launch", "URI": "URI", "GoToR": "GoToR", "SubmitForm": "SubmitForm", "ImportData": "ImportData"}
RAW_TOKENS: tuple[tuple[str, str, str], ...] = (
    # (name, what it is, what must follow it: a name, or for the short /AA a value)
    ("JS", "JavaScript", ""),
    ("JavaScript", "JavaScript", ""),
    ("OpenAction", "OpenAction", ""),
    ("AA", "AdditionalActions", r"(?=\s*(?:<<|\d+\s+\d+\s+R))"),
    ("URI", "URI", ""),
    ("Launch", "Launch", ""),
    ("EmbeddedFile", "EmbeddedFile", ""),
    ("EmbeddedFiles", "EmbeddedFile", ""),
    ("GoToR", "GoToR", ""),
    ("SubmitForm", "SubmitForm", ""),
    ("RichMedia", "RichMedia", ""),
    ("XFA", "XFA", ""),
)


def require_engine():
    """Import pdfminer, or raise ``engine.unavailable`` with the install command."""
    try:
        import pdfminer  # noqa: F401
        import pdfminer.high_level  # noqa: F401
    except (ImportError, OSError) as exc:
        raise EngineUnavailable(MODULE, DIST, exc, install=install_hint(DIST)) from exc


def _corrupt(message: str, **details: Any) -> ToolFailure:
    return ToolFailure("drawing.corrupt", message, details)


@contextmanager
def _guard(what: str) -> Iterator[None]:
    """Turn what a parser raises on a bad file into the structured error; password and permission problems keep their own."""
    from pdfminer.pdfdocument import PDFPasswordIncorrect
    from pdfminer.pdfpage import PDFTextExtractionNotAllowed

    try:
        yield
    except ToolFailure:
        raise
    except PDFPasswordIncorrect as exc:
        raise ToolFailure("drawing.encrypted", "the PDF needs a password to open", {}) from exc
    except PDFTextExtractionNotAllowed as exc:
        raise ToolFailure("drawing.restricted", "the PDF forbids extracting its text and content", {}) from exc
    except RecursionError as exc:
        raise _corrupt(f"the PDF is nested too deeply to read ({what})") from exc
    except MemoryError:
        raise
    except (Exception,) as exc:  # noqa: BLE001 - pdfminer raises many kinds on malformed input
        raise _corrupt(f"not a readable PDF ({what}): {type(exc).__name__}: {exc}") from exc


# --------------------------------------------------------------------------- the file


def _check_file(path: str, limits: Rd.Limits) -> tuple[int, bytes]:
    size = os.path.getsize(path)
    if size > limits.max_file_bytes:
        raise ToolFailure(
            "drawing.too_large",
            f"the file is {size} bytes, over the {limits.max_file_bytes} byte limit for a drawing",
            {"bytes": size, "limit_bytes": limits.max_file_bytes},
        )
    with open(path, "rb") as handle:
        data = handle.read()
    if b"%PDF-" not in data[:1024]:
        if re.match(rb"\s*0\s*\r?\n\s*SECTION", data[:64]) or data[:18] == b"AutoCAD Binary DXF":
            raise ToolFailure("input.invalid", "the file is a DXF although its name says .pdf; read it as a .dxf", {"path": path})
        raise _corrupt("not a PDF file: there is no %PDF header", bytes=size)
    return size, data


def _version(data: bytes) -> str | None:
    match = re.search(rb"%PDF-(\d\.\d)", data[:1024])
    return match.group(1).decode("ascii") if match else None


def _name_pattern(word: str) -> str:
    """A regex for /word where any letter may be written as #xx."""
    out = []
    for char in word:
        if char.isalpha():
            out.append(f"(?:{char}|#{ord(char):02x})")
        else:
            out.append(re.escape(char))
    return "".join(out)


_RAW = [
    (kind, re.compile(rb"/(?:" + _name_pattern(token).encode("ascii") + rb")(?![A-Za-z0-9_.\-#])" + follow.encode("ascii")))
    for token, kind, follow in RAW_TOKENS
]
_STREAM_START = re.compile(rb"(?<!end)stream\r?\n")


def _without_streams(data: bytes) -> bytes:
    """The file without the bodies of its streams (images, fonts, compressed data): names do not live there, and random bytes
    look like names.  Linear: an unterminated stream swallows the rest of the file."""
    out: list[bytes] = []
    pos = 0
    for match in _STREAM_START.finditer(data):
        if match.start() < pos:
            continue
        out.append(data[pos : match.end()])
        end = data.find(b"endstream", match.end())
        if end < 0:
            return b"".join(out)
        pos = end
    out.append(data[pos:])
    return b"".join(out)


def _scan_raw(data: bytes) -> Counter[str]:
    """Active-content names found in the bytes outside the streams, whatever the objects say (names may be hex-escaped)."""
    outside = _without_streams(data)
    found: Counter[str] = Counter()
    for kind, pattern in _RAW:
        hits = len(pattern.findall(outside))
        if hits:
            found[kind] = max(found[kind], hits)
    return found


# --------------------------------------------------------------------------- the document


def _open_document(parser):
    from pdfminer.pdfdocument import PDFDocument

    return PDFDocument(parser)


def _look(obj: Any, kinds: set[str], depth: int = 0) -> None:
    from pdfminer.pdftypes import PDFStream
    from pdfminer.psparser import PSLiteral

    if depth > MAX_LOOK_DEPTH:
        return
    if isinstance(obj, PDFStream):
        obj = obj.attrs
    if isinstance(obj, dict):
        for key, value in obj.items():
            name = key if isinstance(key, str) else getattr(key, "name", "")
            if name in ACTIVE_KEYS:
                kinds.add(ACTIVE_KEYS[name])
            if name == "S" and isinstance(value, PSLiteral) and value.name in ACTIVE_ACTIONS:
                kinds.add(ACTIVE_ACTIONS[value.name])
            if name == "Type" and isinstance(value, PSLiteral) and value.name == "EmbeddedFile":
                kinds.add("EmbeddedFile")
            _look(value, kinds, depth + 1)
    elif isinstance(obj, list):
        for item in obj[:MAX_LOOK_ITEMS]:
            _look(item, kinds, depth + 1)


def _scan_objects(doc, limit: int) -> tuple[Counter[str], bool]:
    """Active content found by parsing: every object of every cross-reference section, object streams included."""
    found: Counter[str] = Counter()
    seen = 0
    for xref in doc.xrefs:
        for objid in xref.get_objids():
            seen += 1
            if seen > limit:
                return found, True
            try:
                obj = doc.getobj(objid)
            except Exception:  # noqa: BLE001 - an object that cannot be read is not evidence of anything
                continue
            kinds: set[str] = set()
            _look(obj, kinds)
            for kind in kinds:
                found[kind] += 1
    return found, False


def _text(raw: Any) -> str:
    if isinstance(raw, bytes):
        if raw.startswith((b"\xfe\xff", b"\xff\xfe")):
            return raw.decode("utf-16", errors="replace")[:200]
        return raw.decode("latin-1", errors="replace")[:200]
    return str(raw)[:200]


def _metadata(doc) -> dict[str, str]:
    from pdfminer.pdftypes import resolve1

    out: dict[str, str] = {}
    for info in doc.info or []:
        try:
            info = resolve1(info)
            for key in ("Title", "Author", "Creator", "Producer", "CreationDate", "ModDate"):
                if key in info:
                    out[key] = _text(resolve1(info[key]))
        except Exception:  # noqa: BLE001
            continue
    return out


# --------------------------------------------------------------------------- vector paths


def _flatten_cubic(p0, p1, p2, p3) -> list[tuple[float, float]]:
    pts = []
    for k in range(1, BEZIER_STEPS + 1):
        t = k / BEZIER_STEPS
        u = 1.0 - t
        pts.append(
            (
                u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1],
            )
        )
    return pts


def _path_geometry(original_path) -> tuple[float, float, list[tuple[float, float]]]:
    """(straight length, curve length, points) of one path in points, from pdfminer's path operators m l c v y h."""
    straight = curve = 0.0
    points: list[tuple[float, float]] = []
    start = here = None
    for step in original_path or []:
        op = step[0]
        coords = [float(v) for part in step[1:] for v in (part if isinstance(part, tuple) else (part,))]
        if not all(math.isfinite(c) for c in coords):
            continue  # NaN or infinity in a path: that step is ignored  # ('c', (x1, y1), (x2, y2), (x3, y3))
        if op == "m" and len(coords) >= 2:
            start = here = (coords[0], coords[1])
            points.append(here)
        elif op == "l" and len(coords) >= 2 and here is not None:
            nxt = (coords[0], coords[1])
            straight += math.dist(here, nxt)
            points.append(nxt)
            here = nxt
        elif op in ("c", "v", "y") and here is not None:
            if op == "c" and len(coords) >= 6:
                c1, c2, end = (coords[0], coords[1]), (coords[2], coords[3]), (coords[4], coords[5])
            elif op == "v" and len(coords) >= 4:
                c1, c2, end = here, (coords[0], coords[1]), (coords[2], coords[3])
            elif op == "y" and len(coords) >= 4:
                c1, c2, end = (coords[0], coords[1]), (coords[2], coords[3]), (coords[2], coords[3])
            else:
                continue
            flat = _flatten_cubic(here, c1, c2, end)
            previous = here
            for q in flat:
                curve += math.dist(previous, q)
                previous = q
            points += flat
            here = end
        elif op == "h" and start is not None and here is not None:
            if math.dist(here, start) > 1e-9:
                straight += math.dist(here, start)
            here = start
    return straight, curve, points


class _Vector:
    def __init__(self) -> None:
        self.paths = self.stroked = self.filled = self.rects = 0
        self.straight_pt = self.curve_pt = 0.0
        self.by_style: dict[tuple[float, bool], list[float]] = {}  # (width, dashed) -> [paths, length pt]
        self.box: list[float] | None = None

    def add(self, item, rect: bool) -> None:
        straight, curve, points = _path_geometry(getattr(item, "original_path", None))
        self.paths += 1
        self.rects += 1 if rect else 0
        if getattr(item, "fill", False):
            self.filled += 1
        if getattr(item, "stroke", False):
            self.stroked += 1
            self.straight_pt += straight
            self.curve_pt += curve
            style = getattr(item, "dashing_style", None)
            dashed = bool(style and style[0])
            key = (round(float(getattr(item, "linewidth", 0.0)), 4), dashed)
            entry = self.by_style.setdefault(key, [0, 0.0])
            entry[0] += 1
            entry[1] += straight + curve
        for x, y in points:
            if self.box is None:
                self.box = [x, y, x, y]
            else:
                self.box = [min(self.box[0], x), min(self.box[1], y), max(self.box[2], x), max(self.box[3], y)]

    def as_dict(self, scale: float) -> dict[str, Any]:
        return {
            "paths": self.paths,
            "stroked": self.stroked,
            "filled": self.filled,
            "rects": self.rects,
            "straight_length_mm": round(self.straight_pt * scale, 4),
            "curve_length_mm": round(self.curve_pt * scale, 4),
            "bbox_mm": None if self.box is None else [round(v * scale, 4) for v in self.box],
            "by_style": [
                {"width_pt": width, "dashed": dashed, "paths": int(count), "length_mm": round(length * scale, 4)}
                for (width, dashed), (count, length) in sorted(self.by_style.items())
            ],
            "method": f"straight segments exactly; Bezier curves flattened into {BEZIER_STEPS} chords; only stroked paths count toward lengths",
        }


# --------------------------------------------------------------------------- pages


def _page_content(layout, scale: float, max_lines: int) -> tuple[dict[str, Any], _Vector, int]:
    from pdfminer.layout import LTChar, LTCurve, LTFigure, LTImage, LTRect, LTTextLine

    vector = _Vector()
    lines: list[dict[str, Any]] = []
    line_total = chars = images = 0
    stack: list[tuple[Any, int]] = [(layout, 0)]
    while stack:
        node, depth = stack.pop()
        for item in node:
            if isinstance(item, LTTextLine):
                text = " ".join(item.get_text().split())
                text = Rd.cut_text(text, MAX_STRING)
                members = [c for c in item if isinstance(c, LTChar)]
                chars += len(members)
                if text:
                    line_total += 1
                    if len(lines) < max_lines:
                        x0, y0, x1, y1 = item.bbox
                        size = statistics.median(c.size for c in members) if members else 0.0
                        lines.append({"text": text, "bbox_mm": [round(v * scale, 3) for v in (x0, y0, x1, y1)], "size_pt": round(float(size), 2)})
            elif isinstance(item, LTCurve):
                vector.add(item, isinstance(item, LTRect))
            elif isinstance(item, LTImage):
                images += 1
            elif isinstance(item, LTFigure):
                if depth < FIGURE_DEPTH:
                    stack.append((item, depth + 1))
            elif hasattr(item, "__iter__"):  # text boxes and other containers
                stack.append((item, depth))
            elif isinstance(item, LTChar):
                chars += 1  # a character outside any line
    # reading order: top to bottom (lines within 1 mm of each other height count as one row), then left to right
    lines.sort(key=lambda line: (-round((line["bbox_mm"][1] + line["bbox_mm"][3]) / 2.0), line["bbox_mm"][0]))
    return {"lines": lines, "line_total": line_total, "chars": chars}, vector, images


def read(path: str, limits_dict: dict[str, int] | None = None) -> dict[str, Any]:
    require_engine()
    from pdfminer.layout import LAParams
    from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdfparser import PDFParser
    from pdfminer.converter import PDFPageAggregator

    limits = Rd.Limits.from_dict(limits_dict)
    size, data = _check_file(path, limits)
    warnings: list[str] = []
    raw_active = _scan_raw(data)

    with open(path, "rb") as handle:
        with _guard("opening the file"):
            parser = PDFParser(handle)
            doc = _open_document(parser)
        encrypted = getattr(doc, "encryption", None) is not None
        structure, partial = (Counter(), False)
        with _guard("walking the objects"):
            structure, partial = _scan_objects(doc, limits.max_pdf_objects)
        active = {kind: structure[kind] or raw_active[kind] for kind in sorted(set(structure) | set(raw_active))}
        if partial:
            warnings.append(f"more than {limits.max_pdf_objects} objects: only the first {limits.max_pdf_objects} were examined for active content")

        manager = PDFResourceManager()
        device = PDFPageAggregator(manager, laparams=LAParams())
        interpreter = PDFPageInterpreter(manager, device)
        pages: list[dict[str, Any]] = []
        total_vector = _Vector()
        text_chars = 0
        pages_total = 0
        crop_warned = False
        with _guard("reading the pages"):
            for index, page in enumerate(PDFPage.create_pages(doc)):
                pages_total += 1
                if pages_total > MAX_PAGES_COUNTED:
                    break
                if index >= limits.max_pages:
                    continue
                interpreter.process_page(page)
                layout = device.get_result()
                x0, y0, x1, y1 = page.mediabox
                rotation = int(page.rotate or 0) % 360
                unit = _user_unit(page)
                scale = MM_PER_PT * unit
                width, height = abs(x1 - x0), abs(y1 - y0)
                if not (math.isfinite(width) and math.isfinite(height)):
                    width = height = 0.0
                    warnings.append(f"page {index + 1}: the MediaBox is not finite; its size is shown as 0")
                if not _valid_box(page.attrs.get("MediaBox")):
                    warnings.append(f"page {index + 1}: the MediaBox is missing or malformed, so the page size shown is the reader's default (Letter), not the file's")
                crop = None
                cx0, cy0, cx1, cy1 = page.cropbox
                if all(math.isfinite(v) for v in (cx0, cy0, cx1, cy1)) and (abs(abs(cx1 - cx0) - abs(x1 - x0)) > 0.01 or abs(abs(cy1 - cy0) - abs(y1 - y0)) > 0.01):
                    crop = [abs(cx1 - cx0), abs(cy1 - cy0)]
                    if rotation in (90, 270):
                        crop.reverse()
                    crop_warned = True
                if rotation in (90, 270):
                    width, height = height, width
                content, vector, images = _page_content(layout, scale, limits.max_list_items)
                text_chars += content["chars"]
                for attr in ("paths", "stroked", "filled", "rects"):
                    setattr(total_vector, attr, getattr(total_vector, attr) + getattr(vector, attr))
                total_vector.straight_pt += vector.straight_pt * unit
                total_vector.curve_pt += vector.curve_pt * unit
                for key, (count, length) in vector.by_style.items():
                    entry = total_vector.by_style.setdefault(key, [0, 0.0])
                    entry[0] += count
                    entry[1] += length * unit
                if vector.box:
                    box = [v * unit for v in vector.box]
                    tb = total_vector.box
                    total_vector.box = box if tb is None else [min(tb[0], box[0]), min(tb[1], box[1]), max(tb[2], box[2]), max(tb[3], box[3])]
                pages.append(
                    {
                        "number": index + 1,
                        "width_mm": round(width * scale, 3),
                        "height_mm": round(height * scale, 3),
                        "rotation": rotation,
                        **({"cropbox_mm": [round(v * scale, 3) for v in crop]} if crop else {}),
                        "text": content,
                        "vector": vector.as_dict(scale),
                        "images": images,
                    }
                )

    if crop_warned:
        warnings.append("some pages have a CropBox smaller than their MediaBox: a viewer shows the CropBox (cropbox_mm); width_mm and height_mm and the coordinates are those of the MediaBox")
    if pages and not text_chars and not total_vector.paths and not any(p["images"] for p in pages):
        warnings.append("Each page is empty or its content could not be parsed (for example a damaged stream); nothing was read from the pages.")
    if pages_total > limits.max_pages:
        warnings.append(f"the PDF has {pages_total} pages; only the first {len(pages)} were read ({len(pages)} of {pages_total} pages)")
    if encrypted:
        warnings.append("The PDF is encrypted; it opened with the empty password.")
    if active:
        names = ", ".join(f"{kind} ({count})" for kind, count in active.items())
        warnings.append(f"The PDF contains active content: {names}. It is reported only: it was not executed, not opened and not followed. Treat the file as untrusted.")
    has_text = text_chars > 0
    if not has_text and pages:
        outlines = total_vector.filled
        warnings.append(
            "The PDF has no text layer: no text could be read from it, and none is invented here. "
            + (f"It has {outlines} filled shapes, which may be text drawn as glyph outlines (this toolkit's own PDFs are). " if outlines else "")
            + "Read the DXF the PDF was made from, if there is one."
        )
    if any(p["images"] for p in pages):
        warnings.append("The PDF contains images (a scan?); their content is not read, and there is no OCR here.")

    return {
        "kind": "pdf",
        "file": {"name": os.path.basename(path), "bytes": size},
        "pdf": {
            "version": _version(data),
            "pages_total": pages_total,
            "pages_read": len(pages),
            "encrypted": encrypted,
            "active_content": active,
            "metadata": _metadata(doc),
        },
        "origin": "page coordinates in millimetres from the bottom-left corner of the page, as the page is displayed",
        "pages": pages,
        "text": {"has_text_layer": has_text, "chars": text_chars},
        "vector": total_vector.as_dict(MM_PER_PT),
        "warnings": list(dict.fromkeys(warnings))[:MAX_WARNINGS],
    }


def _valid_box(raw: Any) -> bool:
    """True for a PDF rectangle: an array of four numbers."""
    from pdfminer.pdftypes import resolve1

    try:
        box = resolve1(raw)
        return isinstance(box, (list, tuple)) and len(box) == 4 and all(math.isfinite(float(resolve1(v))) for v in box)
    except (TypeError, ValueError):
        return False


def _user_unit(page) -> float:
    from pdfminer.pdftypes import resolve1

    try:
        value = float(resolve1(page.attrs.get("UserUnit", 1.0)))
    except (TypeError, ValueError):
        return 1.0
    return value if 0.0 < value < 1e6 else 1.0
