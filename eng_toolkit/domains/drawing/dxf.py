"""The one owner of ezdxf (optional extra ``drawing``): a :class:`Drawing` becomes a DXF file and a PDF page.

Both come from one ezdxf document built from the :class:`~eng_toolkit.domains.drawing.sheet.Drawing`, so the PDF shows
what the DXF holds.  The same drawing gives the same bytes, in this process and in any other.

* The DXF is R2018 in millimetres.  ezdxf's ``write_fixed_meta_data_for_testing`` option removes the dates and GUIDs;
  it is set for the time of one build and restored.  That alone is not enough: ezdxf adds the CLASS records of the
  entity types in use by iterating a ``set`` of names, so their order depends on ``PYTHONHASHSEED`` (measured: the
  records LAYOUT and ACDBPLACEHOLDER swap places).  The order of CLASS records has no meaning, so the classes are
  registered and sorted before saving (docs/geometry-drawing/drawing-determinism.md).
* The SVG is written by :class:`_SvgBackend`, also ours and without dependencies: the same recorded drawing as the PDF,
  as ``<path>`` elements in sheet millimetres (the page is ``297mm`` wide with a ``viewBox`` of 297 units), black ink on
  white, consecutive strokes of one weight and dash merged into one element, numbers with at most three decimals.
* The PDF is written by :class:`_PdfBackend`, ours, because ezdxf's own PDF path needs PyMuPDF (AGPL).  ezdxf's drawing
  frontend turns the document into lines, paths and filled paths (text arrives as filled glyph outlines, so the PDF
  has no text layer and needs no font); the backend writes them as PDF 1.4 operators.  The page is the sheet in
  points (72 / 25.4 per mm), ink is black, a line's weight is the weight of its layer, dashes are the dash array of
  the layer's linetype.  No dates, no producer, no compression and a fixed object order.
"""

from __future__ import annotations

import io
import logging
import math
import os
import re
import threading
import time
from collections import Counter, OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from eng_toolkit.brep import Arc2, Ellipse2, Line2, Poly2
from eng_toolkit.domains.drawing import conventions as C
from eng_toolkit.domains.drawing import convert as V
from eng_toolkit.domains.drawing import dxf_read as DR
from eng_toolkit.domains.drawing import reading as Rd
from eng_toolkit.domains.drawing.dimensions import escape_percent
from eng_toolkit.domains.drawing.sheet import Drawing, PlacedView, SheetText
from eng_toolkit.errors import EngineUnavailable, ToolFailure
from eng_toolkit.licenses import install_hint

MODULE = DIST = "ezdxf"
DXF_VERSION = "R2018"
PT_PER_MM = 72.0 / 25.4
TWO_PI = 2.0 * math.pi
_LOCK = threading.Lock()  # ezdxf.options is process-wide and FastMCP runs sync tools on a thread pool


def require_engine():
    """Import ezdxf, or raise ``engine.unavailable`` with the install command; keeps ezdxf's logger off stderr."""
    try:
        import ezdxf
    except (ImportError, OSError) as exc:
        raise EngineUnavailable(MODULE, DIST, exc, install=install_hint(DIST)) from exc
    logger = logging.getLogger("ezdxf")
    if not any(isinstance(h, logging.NullHandler) for h in logger.handlers):
        logger.addHandler(logging.NullHandler())  # stderr is not ours to write to
    return ezdxf


@contextmanager
def _fixed_metadata(ezdxf) -> Iterator[None]:
    with _LOCK:
        previous = ezdxf.options.write_fixed_meta_data_for_testing
        ezdxf.options.write_fixed_meta_data_for_testing = True
        try:
            yield
        finally:
            ezdxf.options.write_fixed_meta_data_for_testing = previous


# --------------------------------------------------------------------------- text and fonts


def _glyphs(font_name: str) -> frozenset[int] | None:
    """Code points the font behind ``font_name`` has a glyph for, or ``None`` when that cannot be told."""
    from ezdxf.fonts import fonts

    try:
        cmap = fonts.make_font(font_name, 1.0).glyph_cache.cmap  # type: ignore[attr-defined]
        return frozenset(cmap)
    except Exception:  # noqa: BLE001 - a font that cannot be read counts as unknown, not as an error
        return None


def _installed(font_name: str) -> bool:
    from ezdxf.fonts import fonts

    return bool(fonts.font_manager.has_font(font_name))


def _text_plan(texts: tuple[SheetText, ...]) -> tuple[str | None, dict[str, str], list[str]]:
    """The fallback font, the text style of every text, and the warnings about characters no font has.

    A text whose characters the main font lacks goes to ``ENG_ISO_CJK`` with the first installed fallback font that
    has most of them; characters that font lacks too are listed.
    """
    wanted = {c for t in texts for c in t.text if not c.isspace()}
    main = _glyphs(C.TEXT_FONT)
    if main is None or all(ord(c) in main for c in wanted):
        return None, {}, []
    missing = {c for c in wanted if ord(c) not in main}
    best: tuple[int, str, frozenset[int]] | None = None
    for name in C.FALLBACK_FONTS:
        cmap = _glyphs(name) if _installed(name) else None
        if cmap is not None and (best is None or sum(ord(c) in cmap for c in missing) > best[0]):
            best = (sum(ord(c) in cmap for c in missing), name, cmap)
    fallback = best[1] if best else None
    cmap = best[2] if best else frozenset()
    styles = {t.text: C.CJK_TEXT_STYLE for t in texts if any(ord(c) not in main for c in t.text if not c.isspace())} if fallback else {}
    lost = sorted(c for c in missing if ord(c) not in cmap)
    warnings = []
    if lost:
        listed = ", ".join(f"{c} (U+{ord(c):04X})" for c in lost)
        warnings.append(
            f"No installed font has a glyph for {listed}: the DXF keeps the text, but the PDF shows the font's "
            "missing-glyph box. Install a font that has them (for a CJK font see conventions.FALLBACK_FONTS)"
        )
    return fallback, styles, warnings


def text_warnings(drawing: Drawing) -> list[str]:
    """Warnings about characters of the drawing's texts that no installed font can draw."""
    require_engine()
    return _text_plan(drawing.texts)[2]


# --------------------------------------------------------------------------- the document


def _angle_deg(rad: float) -> float:
    return round(math.degrees(rad) % 360.0, 9)


def _add_prim(msp, view: PlacedView, prim, layer: str) -> None:
    attribs = {"layer": layer}
    s = view.scale
    if isinstance(prim, Line2):
        msp.add_line(view.to_sheet(prim.a), view.to_sheet(prim.b), dxfattribs=attribs)
    elif isinstance(prim, Arc2):
        center, radius = view.to_sheet(prim.center), round(prim.radius * s, 6)
        if prim.end_rad - prim.start_rad >= TWO_PI - 1e-9:
            msp.add_circle(center, radius, dxfattribs=attribs)
        else:
            msp.add_arc(center, radius, _angle_deg(prim.start_rad), _angle_deg(prim.end_rad), dxfattribs=attribs)
    elif isinstance(prim, Ellipse2):
        full = prim.end_rad - prim.start_rad >= TWO_PI - 1e-9
        msp.add_ellipse(
            view.to_sheet(prim.center),
            major_axis=(round(prim.major_axis[0] * s, 6), round(prim.major_axis[1] * s, 6), 0.0),
            ratio=prim.ratio,
            start_param=0.0 if full else prim.start_rad % TWO_PI,
            end_param=TWO_PI if full else prim.end_rad % TWO_PI,
            dxfattribs=attribs,
        )
    elif isinstance(prim, Poly2):
        msp.add_lwpolyline([view.to_sheet(p) for p in prim.points], dxfattribs=attribs)
    else:  # pragma: no cover - Prim2 is a closed union
        raise TypeError(f"cannot draw {type(prim).__name__}")


def _build(ezdxf, drawing: Drawing):
    """The ezdxf document of a drawing; call with the metadata option set."""
    from ezdxf import units

    doc = ezdxf.new(DXF_VERSION, setup=False, units=units.MM)
    doc.header["$MEASUREMENT"] = 1
    doc.header["$LWDISPLAY"] = 1
    for name, pattern in C.LINETYPES.items():
        doc.linetypes.add(name, pattern=list(pattern), description=name)
    fallback, styles, _ = _text_plan(drawing.texts)
    doc.styles.add(C.TEXT_STYLE, font=C.TEXT_FONT)
    if fallback:
        doc.styles.add(C.CJK_TEXT_STYLE, font=fallback)
    doc.dimstyles.new(C.DIMSTYLE, dxfattribs=dict(C.DIMSTYLE_VALUES))
    for layer in C.LAYERS:
        doc.layers.add(layer.name, color=7, lineweight=round(layer.weight_mm * 100), linetype=layer.linetype)
    msp = doc.modelspace()

    for line in drawing.lines:
        msp.add_line(line.a, line.b, dxfattribs={"layer": line.layer})
    for view in drawing.views:
        for layer, prims in ((C.OUTLINE.name, view.projection.visible), (C.HIDDEN.name, view.projection.hidden)):
            for prim in prims:
                _add_prim(msp, view, prim, layer)
    for dim in drawing.dims:
        entity = msp.add_linear_dim(
            base=dim.base,
            p1=dim.p1,
            p2=dim.p2,
            angle=0 if dim.orientation == "horizontal" else 90,
            dimstyle=C.DIMSTYLE,
            override={"dimlfac": drawing.dimlfac},
            dxfattribs={"layer": C.DIM.name},
        )
        entity.render()
    for o in drawing.ordinates:
        add = msp.add_ordinate_x_dim if o.axis == "x" else msp.add_ordinate_y_dim
        entity = add(
            o.feature,
            (round(o.end[0] - o.feature[0], 6), round(o.end[1] - o.feature[1], 6)),
            origin=o.origin,
            dimstyle=C.DIMSTYLE,
            override={"dimlfac": drawing.dimlfac},
            dxfattribs={"layer": C.DIM.name},
        )
        entity.render()
    for callout in drawing.callouts:
        msp.add_leader(list(callout.leader), dimstyle=C.DIMSTYLE, dxfattribs={"layer": C.DIM.name})
    for text in drawing.texts:
        attribs = {"layer": text.layer, "style": styles.get(text.text, C.TEXT_STYLE)}
        if text.width_factor != 1.0:
            attribs["width"] = text.width_factor
        msp.add_text(escape_percent(text.text), height=text.height, dxfattribs=attribs).set_placement(text.at)

    # Sorting the CLASS records is what makes the file independent of PYTHONHASHSEED: see the module docstring.
    doc.classes.add_required_classes(doc.dxfversion)
    doc.classes.classes = OrderedDict(sorted(doc.classes.classes.items()))
    return doc


def write_dxf(drawing: Drawing) -> bytes:
    """The DXF file of a drawing, UTF-8, newline-terminated lines.  ``engine.unavailable`` without ezdxf."""
    ezdxf = require_engine()
    with _fixed_metadata(ezdxf):
        doc = _build(ezdxf, drawing)
        stream = io.StringIO()
        doc.write(stream)
    return stream.getvalue().encode("utf-8")


# --------------------------------------------------------------------------- PDF


DashOf = Callable[[Any], tuple[float, ...]]  # backend properties -> dash array in sheet millimetres, () for a continuous line


def _layer_dash(properties) -> tuple[float, ...]:
    """The dash array of a drawing this toolkit made: the linetype of the layer (``conventions.LAYERS``)."""
    layer = C.LAYER_BY_NAME.get(properties.layer)
    return C.dash_array_mm(layer.linetype) if layer else ()


def _num(value: float) -> str:
    if not math.isfinite(value):
        raise ToolFailure("input.out_of_range", "a coordinate of the drawing is not a finite number", {})
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _pdf_backend_class():
    """The backend class, made on first use because it derives from a class of ezdxf, which may not be installed."""
    from ezdxf.addons.drawing.backend import BackendInterface
    from ezdxf.path import Command

    class _PdfBackend(BackendInterface):
        """Replays ezdxf's recorded drawing as PDF operators in sheet millimetres converted to points."""

        def __init__(self, width_mm: float, height_mm: float, dash_of: DashOf | None = None) -> None:
            self.width_mm, self.height_mm = width_mm, height_mm
            self.ops: list[str] = ["0 0 0 RG 0 0 0 rg 1 j"]
            self._weight: float | None = None
            self._dash: tuple[float, ...] | None = None
            self._dash_of = dash_of or _layer_dash

        # ---- no-ops of the interface
        def configure(self, config) -> None:
            pass

        def enter_entity(self, entity, properties) -> None:
            pass

        def exit_entity(self, entity) -> None:
            pass

        def set_background(self, color) -> None:
            pass

        def draw_image(self, image_data, properties) -> None:
            pass

        def draw_point(self, pos, properties) -> None:
            pass

        def clear(self) -> None:
            self.ops = self.ops[:1]

        def finalize(self) -> None:
            pass

        # ---- drawing
        def _pt(self, p) -> str:
            return f"{_num(p.x * PT_PER_MM)} {_num(p.y * PT_PER_MM)}"

        def _stroke_state(self, properties) -> None:
            weight = round(properties.lineweight, 4)
            dash = self._dash_of(properties)
            if weight != self._weight:
                self.ops.append(f"{_num(weight * PT_PER_MM)} w")
                self._weight = weight
            if dash != self._dash:
                array = " ".join(_num(v * PT_PER_MM) for v in dash)
                self.ops.append(f"[{array}] 0 d {0 if dash else 2} J")  # dashes keep butt caps; solid lines close their corners
                self._dash = dash

        def draw_line(self, start, end, properties) -> None:
            self._stroke_state(properties)
            self.ops.append(f"{self._pt(start)} m {self._pt(end)} l S")

        def draw_solid_lines(self, lines, properties) -> None:
            self._stroke_state(properties)
            for a, b in lines:
                self.ops.append(f"{self._pt(a)} m {self._pt(b)} l S")

        def _path(self, path) -> str:
            out = [f"{self._pt(path.start)} m"]
            here = path.start
            for cmd in path.commands():
                if cmd.type == Command.MOVE_TO:
                    out.append(f"{self._pt(cmd.end)} m")
                elif cmd.type == Command.LINE_TO:
                    out.append(f"{self._pt(cmd.end)} l")
                elif cmd.type == Command.CURVE3_TO:  # a quadratic Bezier as a cubic one
                    c1 = here + (cmd.ctrl - here) * (2 / 3)
                    c2 = cmd.end + (cmd.ctrl - cmd.end) * (2 / 3)
                    out.append(f"{self._pt(c1)} {self._pt(c2)} {self._pt(cmd.end)} c")
                else:
                    out.append(f"{self._pt(cmd.ctrl1)} {self._pt(cmd.ctrl2)} {self._pt(cmd.end)} c")
                here = cmd.end
            return " ".join(out)

        def draw_path(self, path, properties) -> None:
            if len(path):
                self._stroke_state(properties)
                self.ops.append(self._path(path) + " S")

        def draw_filled_paths(self, paths, properties) -> None:
            body = " h ".join(self._path(p) for p in paths if len(p))
            if body:
                self.ops.append(body + " h f*")

        def draw_filled_polygon(self, points, properties) -> None:
            vertices = list(points.vertices())
            if len(vertices) >= 3:
                self.ops.append(f"{self._pt(vertices[0])} m " + " ".join(f"{self._pt(p)} l" for p in vertices[1:]) + " h f*")

        def document(self) -> bytes:
            return _pdf_document(self.width_mm, self.height_mm, "\n".join(self.ops))

    return _PdfBackend


def _pdf_document(width_mm: float, height_mm: float, content: str) -> bytes:
    """A one-page PDF 1.4: catalog, pages, page, uncompressed content stream; no information dictionary."""
    stream = content.encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width_mm * PT_PER_MM:.2f} {height_mm * PT_PER_MM:.2f}] /Contents 4 0 R /Resources << >> >>".encode("ascii"),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii")
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    return bytes(out)


# --------------------------------------------------------------------------- SVG


def _svg_backend_class():
    """The SVG backend class, made on first use for the same reason as the PDF one."""
    from ezdxf.addons.drawing.backend import BackendInterface
    from ezdxf.path import Command

    class _SvgBackend(BackendInterface):
        """Replays ezdxf's recorded drawing as SVG paths in sheet millimetres; y points up, so one group flips it."""

        def __init__(self, width_mm: float, height_mm: float, dash_of: DashOf | None = None) -> None:
            self.width_mm, self.height_mm = width_mm, height_mm
            self.elements: list[str] = []
            self._style: tuple[float, tuple[float, ...]] | None = None
            self._run: list[str] = []
            self._dash_of = dash_of or _layer_dash

        # ---- no-ops of the interface
        def configure(self, config) -> None:
            pass

        def enter_entity(self, entity, properties) -> None:
            pass

        def exit_entity(self, entity) -> None:
            pass

        def set_background(self, color) -> None:
            pass

        def draw_image(self, image_data, properties) -> None:
            pass

        def draw_point(self, pos, properties) -> None:
            pass

        def clear(self) -> None:
            self.elements, self._run, self._style = [], [], None

        def finalize(self) -> None:
            self._flush()

        # ---- drawing
        @staticmethod
        def _pt(p) -> str:
            return f"{_num(p.x)} {_num(p.y)}"

        def _flush(self) -> None:
            if self._run and self._style is not None:
                weight, dash = self._style
                attrs = f'stroke-width="{_num(weight)}"'
                attrs += f' stroke-dasharray="{" ".join(_num(v) for v in dash)}" stroke-linecap="butt"' if dash else ' stroke-linecap="square"'
                self.elements.append(f'<path d="{" ".join(self._run)}" {attrs}/>')
            self._run = []

        def _stroke_state(self, properties) -> None:
            style = (round(properties.lineweight, 4), self._dash_of(properties))
            if style != self._style:
                self._flush()
                self._style = style

        def draw_line(self, start, end, properties) -> None:
            self._stroke_state(properties)
            self._run.append(f"M{self._pt(start)} L{self._pt(end)}")

        def draw_solid_lines(self, lines, properties) -> None:
            self._stroke_state(properties)
            for a, b in lines:
                self._run.append(f"M{self._pt(a)} L{self._pt(b)}")

        def _path(self, path) -> str:
            out = [f"M{self._pt(path.start)}"]
            here = path.start
            for cmd in path.commands():
                if cmd.type == Command.MOVE_TO:
                    out.append(f"M{self._pt(cmd.end)}")
                elif cmd.type == Command.LINE_TO:
                    out.append(f"L{self._pt(cmd.end)}")
                elif cmd.type == Command.CURVE3_TO:
                    out.append(f"Q{self._pt(cmd.ctrl)} {self._pt(cmd.end)}")
                else:
                    out.append(f"C{self._pt(cmd.ctrl1)} {self._pt(cmd.ctrl2)} {self._pt(cmd.end)}")
                here = cmd.end
            return " ".join(out)

        def draw_path(self, path, properties) -> None:
            if len(path):
                self._stroke_state(properties)
                self._run.append(self._path(path))

        def draw_filled_paths(self, paths, properties) -> None:
            body = " Z ".join(self._path(p) for p in paths if len(p))
            if body:
                self._flush()
                self.elements.append(f'<path d="{body} Z" fill="#000" stroke="none" fill-rule="evenodd"/>')

        def draw_filled_polygon(self, points, properties) -> None:
            vertices = list(points.vertices())
            if len(vertices) >= 3:
                self._flush()
                self.elements.append(f'<path d="M{self._pt(vertices[0])} ' + " ".join(f"L{self._pt(p)}" for p in vertices[1:]) + ' Z" fill="#000" stroke="none" fill-rule="evenodd"/>')

        def document(self) -> bytes:
            self._flush()
            w, h = _num(self.width_mm), _num(self.height_mm)
            head = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" viewBox="0 0 {w} {h}">\n'
                f'<rect width="{w}" height="{h}" fill="#fff"/>\n'
                f'<g transform="translate(0 {h}) scale(1 -1)" fill="none" stroke="#000" stroke-linejoin="round">\n'
            )
            return (head + "\n".join(self.elements) + "\n</g>\n</svg>\n").encode("utf-8")

    return _SvgBackend


def _replay(drawing: Drawing, backend) -> None:
    """Draw the drawing's DXF document into ``backend`` with ezdxf's frontend (black ink, absolute line weights, solid lines)."""
    ezdxf = require_engine()
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration, LinePolicy, LineweightPolicy
    from ezdxf.addons.drawing.recorder import Recorder

    config = Configuration(
        line_policy=LinePolicy.SOLID,  # dashes are set from the layer's linetype by the backend
        lineweight_policy=LineweightPolicy.ABSOLUTE,
        color_policy=ColorPolicy.BLACK,
        background_policy=BackgroundPolicy.WHITE,
    )
    with _fixed_metadata(ezdxf):
        doc = _build(ezdxf, drawing)
        recorder = Recorder()
        Frontend(RenderContext(doc), recorder, config=config).draw_layout(doc.modelspace())
    recorder.player().replay(backend)


def render_svg(drawing: Drawing) -> bytes:
    """The SVG file of a drawing (see the module docstring).  ``engine.unavailable`` without ezdxf."""
    backend = _svg_backend_class()(*drawing.size_mm)
    _replay(drawing, backend)
    return backend.document()


def render_pdf(drawing: Drawing) -> bytes:
    """The PDF page of a drawing (see the module docstring).  ``engine.unavailable`` without ezdxf."""
    backend = _pdf_backend_class()(*drawing.size_mm)
    _replay(drawing, backend)
    return backend.document()


# --------------------------------------------------------------------------- convert (drawing_convert)

CONVERT_TARGET = "eng_toolkit.domains.drawing.dxf:convert_file"
TEXT_TYPES = ("TEXT", "MTEXT", "ATTRIB", "ATTDEF")
MAX_HATCH_SEGMENTS = 200_000  # per hatch; a denser pattern is drawn as its boundary
MAX_NAMES = 60  # longest list of names (styles, fonts, linetypes, handles) in the answer
DWG_MESSAGE = (
    "DWG files are not read by this version: converting a DWG needs the dwg2dxf tool (LibreDWG), which is planned as slice S9 "
    "and is not available yet. Convert the file to DXF with a CAD program or dwg2dxf and convert the DXF"
)


def dwg_unavailable(name: str = "") -> ToolFailure:
    return ToolFailure("engine.unavailable", DWG_MESSAGE, {"tool": "dwg2dxf", "format": "dwg", "slice": "S9", **({"path": name} if name else {})})


class _Clock:
    """The time budget of one conversion, checked between entities, so that a slow file ends with a clear message."""

    def __init__(self, budget_s: float) -> None:
        self.budget_s = budget_s
        self.deadline = time.monotonic() + budget_s

    def check(self) -> None:
        if time.monotonic() > self.deadline:
            raise ToolFailure("engine.timeout", f"the drawing took longer than {self.budget_s:g} s to convert and was stopped", {"timeout_s": self.budget_s})


def _txt(value: Any, limit: int = 200) -> str:
    return Rd.cut_text(str(value), limit)


# ---- what is in the file, container by container (model space, paper space, every block)


@dataclass
class _Scan:
    count: int = 0
    vertices: int = 0
    chars: int = 0
    edges: list[tuple[Any, str, int]] = field(default_factory=list)  # (INSERT or DIMENSION, key of the block it draws, copies)
    oversize: list[Any] = field(default_factory=list)  # texts longer than the limit


def _vertex_count(e) -> int:
    kind = e.dxftype()
    try:
        if kind == "LWPOLYLINE":
            return len(e)
        if kind == "POLYLINE":
            return len(e.vertices)
        if kind == "SPLINE":
            return len(e.control_points) + len(e.fit_points)
        if kind == "HATCH":
            return sum(len(p.vertices) if hasattr(p, "vertices") else len(p.edges) for p in e.paths)
        if kind in ("LEADER", "MESH"):
            return len(e.vertices)
    except Exception:  # noqa: BLE001 - a damaged entity counts as one
        pass
    return 0


def _text_length(e) -> int:
    try:
        return len((e.text if e.dxftype() == "MTEXT" else e.dxf.get("text", "")) or "")
    except Exception:  # noqa: BLE001
        return 0


def _scan_container(container, max_text: int) -> _Scan:
    scan = _Scan()
    for e in container:
        kind = e.dxftype()
        scan.count += 1
        scan.vertices += _vertex_count(e)
        if kind in TEXT_TYPES:
            n = _text_length(e)
            if n > max_text:
                scan.oversize.append(e)
            else:
                scan.chars += n
        elif kind == "INSERT":
            try:
                copies = max(int(e.dxf.get("column_count", 1)), 1) * max(int(e.dxf.get("row_count", 1)), 1)
                scan.edges.append((e, str(e.dxf.get("name", "")).lower(), copies))
                for a in e.attribs:
                    scan.chars += min(_text_length(a), max_text)
            except Exception:  # noqa: BLE001
                scan.edges.append((e, "", 1))
        elif kind == "DIMENSION":
            geometry = str(e.dxf.get("geometry", "") or "").lower()
            if geometry:
                scan.edges.append((e, geometry, 1))
    return scan


@dataclass
class _Tot:
    entities: int
    vertices: int
    chars: int
    height: int  # the longest chain of block references below


def _analyse(scans: dict[str, _Scan], roots: list[str], max_nesting: int, cut) -> dict[str, _Tot]:
    """Totals of each container reached from ``roots``, block references expanded; ``cut(entity, key, reason)`` is called for every block
    reference that must go: to a block that does not exist, back to a block still being walked (a cycle), or deeper than ``max_nesting``.

    Iterative: a chain of a hundred thousand blocks must not overflow the stack.
    """
    done: dict[str, _Tot] = {}
    on_path: set[str] = set()
    for root in roots:
        if root in done:
            continue
        # frame: [key, iterator over edge numbers, kept (target, copies) list, edge waiting for its target, references cut here]
        stack: list[list[Any]] = [[root, iter(range(len(scans[root].edges))), [], None, 0]]
        on_path.add(root)
        while stack:
            frame = stack[-1]
            key, edges = frame[0], scans[frame[0]].edges
            if frame[3] is not None:  # the target of that edge has just been completed
                entity, target, copies = edges[frame[3]]
                frame[3] = None
                if done[target].height + 1 > max_nesting:
                    cut(entity, key, f"block references nested deeper than {max_nesting}")
                    frame[4] += 1
                else:
                    frame[2].append((target, copies))
            descend = None
            for i in frame[1]:
                entity, target, copies = edges[i]
                if target not in scans:
                    cut(entity, key, "reference to a block that does not exist")
                    frame[4] += 1
                elif target in on_path:
                    cut(entity, key, "block that refers back to itself")
                    frame[4] += 1
                elif target in done:
                    if done[target].height + 1 > max_nesting:
                        cut(entity, key, f"block references nested deeper than {max_nesting}")
                        frame[4] += 1
                    else:
                        frame[2].append((target, copies))
                else:
                    frame[3] = i
                    descend = target
                    break
            if descend is not None:
                on_path.add(descend)
                stack.append([descend, iter(range(len(scans[descend].edges))), [], None, 0])
                continue
            scan = scans[key]
            total = _Tot(scan.count - len(scan.oversize) - frame[4], scan.vertices, scan.chars, 0)
            for target, copies in frame[2]:
                t = done[target]
                total.entities += copies * t.entities
                total.vertices += copies * t.vertices
                total.chars += copies * t.chars
                total.height = max(total.height, t.height + 1)
            done[key] = total
            on_path.discard(key)
            stack.pop()
    return done


# ---- numbers in the file


def _numbers(e):
    """Every number an entity holds: its own attributes and the vertices, control points and attributes it owns."""
    for value in e.dxf.all_existing_dxf_attribs().values():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            yield value
        elif isinstance(value, (tuple, list)) or hasattr(value, "xyz"):
            for part in value:
                if isinstance(part, (int, float)) and not isinstance(part, bool):
                    yield part
    kind = e.dxftype()
    if kind == "LWPOLYLINE":
        for row in e.lwpoints:
            yield from (float(x) for x in row)
    elif kind == "POLYLINE":
        for vertex in e.vertices:
            yield from _numbers(vertex)
    elif kind == "SPLINE":
        for points in (e.control_points, e.fit_points):
            for point in points:
                yield from (float(x) for x in point)
        yield from (float(x) for x in e.knots)
        yield from (float(x) for x in e.weights)
    elif kind == "HATCH":
        for path in e.paths:
            for vertex in getattr(path, "vertices", ()):
                yield from (float(x) for x in vertex)
            for edge in getattr(path, "edges", ()):
                for name in ("start", "end", "center", "major_axis"):
                    point = getattr(edge, name, None)
                    if point is not None:
                        yield from (float(x) for x in point)
                for name in ("radius", "ratio", "start_angle", "end_angle"):
                    if hasattr(edge, name):
                        yield float(getattr(edge, name))
                for name in ("control_points", "fit_points", "knot_values", "weights"):
                    for item in getattr(edge, name, ()) or ():
                        yield from (float(x) for x in item) if hasattr(item, "__iter__") else (float(item),)
    elif kind in ("LEADER", "MESH"):
        for vertex in e.vertices:
            yield from (float(x) for x in vertex)
    elif kind == "INSERT":
        for attrib in e.attribs:
            yield from _numbers(attrib)


def _check_numbers(containers: dict[str, Any], keys, limit_units: float, mm_per_unit: float, clock: _Clock) -> list[tuple[Any, Any, str]]:
    """Entities to remove: ``(container, entity, reason)``.  Raises ``input.out_of_range`` for a number beyond ``limit_units``."""
    remove: list[tuple[Any, Any, str]] = []
    for key in keys:
        clock.check()
        for e in containers[key]:
            try:
                nonfinite = False
                for value in _numbers(e):
                    if isinstance(value, float) and not math.isfinite(value):
                        nonfinite = True
                        break
                    if abs(value) > limit_units:
                        raise ToolFailure(
                            "input.out_of_range",
                            f"{e.dxftype()} {e.dxf.get('handle', '?')} holds the number {value:.6g}, over the limit of {limit_units:.6g} drawing units "
                            f"({limit_units * mm_per_unit:.6g} mm) the converter allows; a drawing with coordinates this large cannot be placed on a page",
                            {"entity": e.dxftype(), "handle": _txt(e.dxf.get("handle", "")), "value": float(value), "limit_units": limit_units, "mm_per_unit": mm_per_unit},
                        )
                if nonfinite:
                    remove.append((containers[key], e, "non-finite coordinate (NaN or infinity)"))
            except ToolFailure:
                raise
            except Exception as exc:  # noqa: BLE001 - an entity whose data cannot even be read
                remove.append((containers[key], e, f"unreadable data ({type(exc).__name__})"))
    return remove


# ---- the file

HEADER_UNITS_NOTE = "$INSUNITS"


def _units(doc, raw_header: dict[str, str] | None) -> tuple[dict[str, Any], float, list[str]]:
    """(units row, millimetres per drawing unit, warnings).  Unlike drawing_read this assumes millimetres when the file states none."""
    if raw_header is None:
        insunits, measurement = doc.header.get("$INSUNITS"), doc.header.get("$MEASUREMENT")
    else:
        insunits, measurement = DR._number(raw_header, "$INSUNITS"), DR._number(raw_header, "$MEASUREMENT")
    info, _ = Rd.units_info(insunits, measurement)
    warnings: list[str] = []
    mm = info["mm_per_unit"]
    if mm is None:
        why = "has no $INSUNITS" if insunits is None else "has $INSUNITS 0 (unitless)" if insunits == 0 else f"has $INSUNITS {insunits}, which is not a unit code"
        warnings.append(f"The drawing {why}: 1 drawing unit is taken as 1 mm. If it is not, the page is wrong by that factor; ask whoever made the drawing")
        info = {**info, "assumed": "millimeter", "mm_per_unit_used": 1.0}
        mm = 1.0
    else:
        info = {**info, "assumed": None, "mm_per_unit_used": mm}
    return info, mm, warnings


def _inventory(doc) -> dict[str, Counter]:
    """Entities by type in model space, in paper space layouts and in block definitions (the layouts' own blocks are not definitions)."""
    out = {"model": Counter(), "paper": Counter(), "blocks": Counter()}
    for block in doc.blocks:
        name = block.name.lower()
        space = "model" if name.startswith("*model_space") else "paper" if name.startswith("*paper_space") else "blocks"
        for e in block:
            out[space][e.dxftype()] += 1
    return out


ENTITY_START = re.compile(rb"(?:^|[\r\n])[ \t]*0[ \t]*[\r\n]+[ \t]*[A-Za-z*]")  # group code 0 followed by a name: a record, not a value 0


def _check_records(path: str, limits: V.ConvertLimits) -> None:
    """Refuse a text DXF with far more records than ``max_entities`` allows, before the library builds an object for each.

    A record starts with the group code 0 and a name (SECTION, LINE, ...); a line holding only 0 as a value (a coordinate) is followed by a group code, a number.
    """
    with open(path, "rb") as handle:
        head = handle.read(18)
        if head.startswith(b"AutoCAD Binary DXF"):
            return
        handle.seek(0)
        records = len(ENTITY_START.findall(handle.read()))
    if records > limits.max_entities * 1.2 + 1000:
        raise V.too_large("records in the file", records, int(limits.max_entities * 1.2 + 1000))


def _convert_open(path: str, limits: V.ConvertLimits, clock: _Clock):
    """The document, its units, and the notes about how it was read.  Refuses what is not a DXF drawing."""
    ezdxf = require_engine()
    with open(path, "rb") as handle:
        head = handle.read(8)
    if re.match(rb"AC10\d\d", head) and b"\n" not in head[:6]:
        raise dwg_unavailable()
    DR._check_file(path, Rd.Limits(max_file_bytes=limits.max_file_bytes, max_entities=10**12))  # size, PDF and DWG; the records are counted below
    _check_records(path, limits)
    doc, repair, audit = DR._load(ezdxf, path)
    notes: list[str] = []
    if repair == "truncated":
        notes.append("The file is damaged: it ends in the middle of its data. It was read up to the last complete record and the open sections were closed, so everything after the cut is missing from the output.")
    elif repair == "recovered":
        notes.append("The file is damaged and was read in recovery mode, so some content may be missing or repaired" + (": " + "; ".join(_txt(a, 200) for a in audit) if audit else ""))
    if repair is not None and not any(len(layout) for layout in doc.layouts):
        raise ToolFailure("drawing.corrupt", "the file is damaged and no entities could be recovered from it", {"repair": repair})
    total = len(doc.entitydb)
    if total > limits.max_entities:
        raise V.too_large("entities in the file", total, limits.max_entities)
    clock.check()
    return ezdxf, doc, repair, notes


@dataclass
class _Tally:
    """What the rendering did, counted while it did it."""

    drawn: Counter = field(default_factory=Counter)
    skipped: Counter = field(default_factory=Counter)  # (type, reason) -> count
    styles: Counter = field(default_factory=Counter)
    coloured: int = 0
    formatted_mtext: int = 0
    pattern_hatches: int = 0
    hatch_timeouts: int = 0
    dense_hatches: int = 0
    dash_names: dict[tuple[float, ...], str] = field(default_factory=dict)
    fine: set[str] = field(default_factory=set)


def _frontend_class():
    from ezdxf.addons.drawing import Frontend
    from ezdxf.addons.drawing.config import HatchPolicy
    from ezdxf.addons.drawing.properties import Filling
    from ezdxf.math import Vec2

    class _CountingFrontend(Frontend):
        """The ezdxf frontend that counts what it draws and skips by type, and stops at the time budget."""

        def __init__(self, ctx, out, config, tally: _Tally, clock: _Clock) -> None:
            super().__init__(ctx, out, config)
            self.tally, self.clock = tally, clock
            self._stack: list[list[Any]] = []

        def draw_entity(self, entity, properties) -> None:
            self.clock.check()
            kind = entity.dxftype()
            self._stack.append([entity, False])
            try:
                super().draw_entity(entity, properties)
            finally:
                _, skipped = self._stack.pop()
            if skipped:
                return
            self.tally.drawn[kind] += 1
            if kind in TEXT_TYPES:
                self.tally.styles[_txt(entity.dxf.get("style", "Standard"), 80)] += 1
                if kind == "MTEXT" and "\\" in (entity.text or ""):
                    self.tally.formatted_mtext += 1

        def skip_entity(self, entity, msg) -> None:
            if self._stack and self._stack[-1][0] is entity:
                self._stack[-1][1] = True
            self.tally.skipped[(entity.dxftype(), _txt(msg, 60))] += 1

        def draw_hatch_entity(self, entity, properties, *, loops=None) -> None:
            pattern = properties.filling is not None and properties.filling.type == Filling.PATTERN
            if pattern and self._too_many_pattern_lines(entity, loops):
                self.tally.dense_hatches += 1
                saved = self.config
                self.config = saved.with_changes(hatch_policy=HatchPolicy.SHOW_OUTLINE)  # the boundary only, not a million pattern lines
                try:
                    super().draw_hatch_entity(entity, properties, loops=loops)
                finally:
                    self.config = saved
                return
            started = time.perf_counter()
            super().draw_hatch_entity(entity, properties, loops=loops)
            if pattern:
                self.tally.pattern_hatches += 1
                if time.perf_counter() - started >= self.config.hatching_timeout:
                    self.tally.hatch_timeouts += 1

        def _too_many_pattern_lines(self, entity, loops) -> bool:
            """True when the pattern of a hatch would need more than ``MAX_HATCH_SEGMENTS`` line segments: the boundary's diagonal over the
            distance between the pattern's parallel lines, times the dashes along each line."""
            from ezdxf.path import bbox as path_bbox
            from ezdxf.render import hatching

            try:
                paths = loops if loops is not None else hatching.hatch_boundary_paths(entity, filter_text_boxes=True)
                box = path_bbox(paths, fast=True)
                if not box.has_data:
                    return False
                diagonal = float(box.size.magnitude)
                total = 0.0
                for line in entity.pattern.lines:
                    direction = Vec2.from_deg_angle(line.angle)
                    spacing = abs(-line.offset.x * direction.y + line.offset.y * direction.x)
                    period = sum(abs(v) for v in line.dash_length_items)
                    dashes = len(line.dash_length_items) / 2.0 * diagonal / period if period > 0 else 1.0
                    total += diagonal / max(spacing, 1e-12) * max(dashes, 1.0)
                    if total > MAX_HATCH_SEGMENTS:
                        return True
                return False
            except Exception:  # noqa: BLE001 - a hatch whose geometry cannot even be measured is left to the library and its time limit
                return False

    return _CountingFrontend


def _dash_source(tally: _Tally, table: list[tuple[float, ...]]):
    """The property override that tells the backend, through the pen number, which dash array an entity has (0 = continuous)."""

    def override(entity, properties) -> None:
        if properties.color.lower()[:7] != "#000000":
            tally.coloured += 1
        pattern = properties.linetype_pattern
        if pattern:
            units = tuple(abs(float(v)) * float(properties.linetype_scale) for v in pattern)
            if units not in tally.dash_names:
                tally.dash_names[units] = _txt(properties.linetype_name, 60)
                table.append(units)
            properties.pen = table.index(units) + 1
        else:
            properties.pen = 0

    return override


MIN_DOT_MM, MIN_DASH_MM, MIN_PERIOD_MM = 0.15, 0.05, 0.3


def _dash_of(table: list[tuple[float, ...]], tally: _Tally, f: float):
    """Backend properties -> dash array in millimetres on the sheet; a pattern too fine to print at this scale is drawn solid."""
    cache: dict[int, tuple[float, ...]] = {}

    def dash_of(properties) -> tuple[float, ...]:
        pen = properties.pen
        if pen <= 0 or pen > len(table):
            return ()
        if pen not in cache:
            units = table[pen - 1]
            mm = tuple(v * f for v in units)
            fine = sum(mm) < MIN_PERIOD_MM or any(0 < v < MIN_DASH_MM for v in mm)
            if fine or len(mm) < 2 or not all(math.isfinite(v) for v in mm):
                tally.fine.add(tally.dash_names.get(units, "?"))
                cache[pen] = ()
            else:
                cache[pen] = tuple(MIN_DOT_MM if (i % 2 == 0 and v == 0.0) else v for i, v in enumerate(mm))
        return cache[pen]

    return dash_of


def _font_rows(doc, tally: _Tally) -> list[dict[str, Any]]:
    from ezdxf.fonts import fonts

    rows = []
    for style, count in sorted(tally.styles.items())[:MAX_NAMES]:
        try:
            font = str(doc.styles.get(style).dxf.get("font", "") or "")
        except Exception:  # noqa: BLE001
            font = ""
        installed = bool(font) and not font.lower().endswith((".shx", ".shp")) and bool(fonts.font_manager.has_font(font))
        rows.append({"style": style, "font": _txt(font, 80), "entities": count, "replaced": not installed, "outlines_from": font if installed else "the default font"})
    return rows


def _render(doc, kind: str, request: dict[str, Any], limits: V.ConvertLimits, mm_per_unit: float, clock: _Clock, outdir: str) -> dict[str, Any]:
    from ezdxf.addons.drawing import RenderContext
    from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration, HatchPolicy, ImagePolicy, LinePolicy, LineweightPolicy
    from ezdxf.addons.drawing.recorder import Recorder
    from ezdxf.math import Matrix44

    sheet = request["sheet"]
    ratio = tuple(request["ratio"]) if request.get("ratio") else None
    config = Configuration(
        line_policy=LinePolicy.SOLID,  # dashes are set by the backend from the pen number, never as geometry
        lineweight_policy=LineweightPolicy.ABSOLUTE,
        color_policy=ColorPolicy.BLACK,
        background_policy=BackgroundPolicy.WHITE,
        hatch_policy=HatchPolicy.NORMAL,
        image_policy=ImagePolicy.IGNORE,  # an image is a file on disk named in the drawing: never opened
        hatching_timeout=3.0,
    )
    tally = _Tally()
    table: list[tuple[float, ...]] = []
    recorder = Recorder()
    frontend = _frontend_class()(RenderContext(doc), recorder, config, tally, clock)
    frontend.push_property_override_function(_dash_source(tally, table))
    try:
        frontend.draw_layout(doc.modelspace())
    except ToolFailure:
        raise
    except RecursionError as exc:
        raise ToolFailure("drawing.too_large", "the block references nest too deeply to draw", {}) from exc
    except ArithmeticError as exc:
        raise ToolFailure("input.out_of_range", f"a number in the drawing overflowed while it was drawn ({type(exc).__name__}: {exc})", {}) from exc
    clock.check()

    player = recorder.player()
    box = player.bbox()
    if not box.has_data:
        raise ToolFailure("drawing.empty", "nothing in model space can be drawn: no entity with a shape, or every layer is off or frozen", {"drawn": dict(tally.drawn)})
    (x0, y0), (x1, y1) = tuple(box.extmin)[:2], tuple(box.extmax)[:2]
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
        raise ToolFailure("input.out_of_range", "the extents of the drawing are not finite numbers", {})
    limit_mm = limits.max_coordinate_mm
    for v in (x0, y0, x1, y1):
        if abs(v) * mm_per_unit > limit_mm:
            raise ToolFailure(
                "input.out_of_range",
                f"the drawing reaches {abs(v) * mm_per_unit:.6g} mm from its origin, over the {limit_mm:g} mm the converter allows; a stray entity or a wrong unit is the usual cause",
                {"extent_mm": abs(v) * mm_per_unit, "limit_mm": limit_mm},
            )
    width_mm, height_mm = (x1 - x0) * mm_per_unit, (y1 - y0) * mm_per_unit
    page = V.choose_page(width_mm, height_mm, sheet, ratio, limits)
    f = V.factor(mm_per_unit, page)
    # scale about the origin, then move the middle of the extents to the middle of the page
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    player.transform(Matrix44.chain(Matrix44.scale(f, f, 1.0), Matrix44.translate(page.width_mm / 2.0 - cx * f, page.height_mm / 2.0 - cy * f, 0.0)))
    dash_of = _dash_of(table, tally, f)
    fmt = kind
    files: list[dict[str, Any]] = []
    png_info = None
    if fmt == "svg":
        backend = _svg_backend_class()(page.width_mm, page.height_mm, dash_of)
        player.replay(backend)
        payload = backend.document()
        files.append({"name": "converted.svg", "format": "svg", "bytes": payload})
    else:
        backend = _pdf_backend_class()(page.width_mm, page.height_mm, dash_of)
        player.replay(backend)
        payload = backend.document()
        if fmt == "pdf":
            files.append({"name": "converted.pdf", "format": "pdf", "bytes": payload})
        else:
            from eng_toolkit.domains.drawing import raster

            png, w_px, h_px, dpi = raster.render_png(payload, page.width_mm, page.height_mm)
            files.append({"name": "converted.png", "format": "png", "bytes": png})
            png_info = {"width_px": w_px, "height_px": h_px, "dpi": dpi, "greyscale": True, "from": "converted PDF rendered by PDFium"}
    for item in files:
        if len(item["bytes"]) > limits.max_output_bytes:
            raise V.too_large("size of the output in bytes", len(item["bytes"]), limits.max_output_bytes)
        with open(os.path.join(outdir, item["name"]), "wb") as handle:
            handle.write(item["bytes"])
        item["bytes"] = len(item["bytes"])
    return {
        "files": files,
        "page": {
            "sheet": page.sheet,
            "orientation": page.orientation,
            "width_mm": round(page.width_mm, 6),
            "height_mm": round(page.height_mm, 6),
            "margin_mm": V.MARGIN_MM,
            "scale_text": V.scale_text(page.ratio),
            "scale": page.scale,
            "mm_per_drawing_unit": f,
            "pt_per_drawing_unit": f * PT_PER_MM,
        },
        "extents": {
            "min_units": [x0, y0],
            "max_units": [x1, y1],
            "size_mm": [width_mm, height_mm],
            "on_page_mm": [width_mm * page.scale, height_mm * page.scale],
        },
        "tally": tally,
        "png": png_info,
    }


def _render_losses(doc, kind: str, tally: _Tally, inventory: dict[str, Counter]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items: list[dict[str, Any]] = []
    texts = sum(tally.drawn[t] for t in TEXT_TYPES)
    if texts:
        items.append(V.loss("text_as_outlines", f"Text is drawn as filled glyph outlines in the {kind.upper()}: it has no text layer and cannot be searched, selected or copied", texts))
    fonts_rows = _font_rows(doc, tally)
    replaced = [r for r in fonts_rows if r["replaced"]]
    if replaced:
        items.append(
            V.loss(
                "fonts_replaced",
                "Text styles whose font file is not installed here (or is an SHX font) are drawn with the default font: " + ", ".join(f"{r['style']} ({r['font'] or 'no font'})" for r in replaced[:10]),
                sum(r["entities"] for r in replaced),
                styles=[r["style"] for r in replaced],
            )
        )
    if tally.coloured:
        items.append(V.loss("colour", "All ink is black: colours are not carried (line weights and dashes are)", tally.coloured))
    if tally.formatted_mtext:
        items.append(V.loss("mtext_formatting", "MTEXT with inline formatting codes is drawn as far as ezdxf supports them (fonts, height, bold and italic yes; stacked fractions, columns and tab stops simplified)", tally.formatted_mtext))
    if tally.pattern_hatches:
        items.append(V.loss("hatch_pattern", "Hatch patterns are drawn as lines by ezdxf's pattern renderer and solid hatches as black fills", tally.pattern_hatches))
    if tally.dense_hatches:
        items.append(V.loss("hatch_outline_only", f"A hatch whose pattern would need more than {MAX_HATCH_SEGMENTS} line segments is drawn as its boundary only", tally.dense_hatches))
    if tally.hatch_timeouts:
        items.append(V.loss("hatch_incomplete", "A pattern hatch took longer than 3 s and may be incomplete", tally.hatch_timeouts))
    if tally.fine:
        items.append(V.loss("linetype_solid", "A linetype whose dashes are finer than 0.05 mm (or whose period is under 0.3 mm) at this scale is drawn solid: " + ", ".join(sorted(tally.fine)[:10]), len(tally.fine)))
    skipped: dict[str, dict[str, int]] = {}
    for (kind_, reason), n in tally.skipped.items():
        skipped.setdefault(kind_, {})[reason] = skipped.setdefault(kind_, {}).get(reason, 0) + n
    for kind_, reasons in sorted(skipped.items()):
        for reason, n in sorted(reasons.items()):
            why = {
                "unsupported": "the renderer has no drawing for this entity type (ACIS solids, proxy objects, OLE frames and similar)",
                "invisible": "its layer is off or frozen, or the entity is hidden",
            }.get(reason, reason)
            items.append(V.loss("entity_skipped", f"{kind_}: {why}", n, type=kind_, reason=reason))
    images = inventory["model"].get("IMAGE", 0)
    if images:
        items.append(V.loss("images_not_drawn", "Image entities are not drawn: the file they name is never opened", images))
    if sum(inventory["paper"].values()):
        items.append(V.loss("paper_space", "Only model space is converted; paper space (layouts, viewports, title blocks) is not drawn", sum(inventory["paper"].values())))
    summary = {
        "model_space_by_type": dict(sorted(inventory["model"].items())),
        "drawn_by_type": dict(sorted(tally.drawn.items())),
        "skipped_by_type": {k: {"count": sum(v.values()), "reasons": v} for k, v in sorted(skipped.items())},
        "drawn_total": sum(tally.drawn.values()),
        "skipped_total": sum(tally.skipped.values()),
        "fonts": fonts_rows,
    }
    return items, summary


# ---- DXF to DXF


def _space_of(block) -> str:
    name = block.name.lower()
    return "model" if name.startswith("*model_space") else "paper" if name.startswith("*paper_space") else "blocks"


def _lwpolylines_to_polylines(doc) -> dict[str, int]:
    """R12 has no LWPOLYLINE: the same vertices, bulges and widths as a 2D POLYLINE on the same layer.  Returns the count per space."""
    converted: Counter = Counter()
    for block in doc.blocks:
        for e in [e for e in block if e.dxftype() == "LWPOLYLINE"]:
            attribs = {k: v for k, v in e.dxf.all_existing_dxf_attribs().items() if k in ("layer", "color", "linetype", "ltscale", "invisible", "paperspace")}
            rows = [tuple(float(x) for x in row) for row in e.lwpoints]
            poly = block.add_polyline2d(rows, format="xyseb", close=bool(e.closed), dxfattribs=attribs)
            if e.dxf.hasattr("elevation"):
                poly.dxf.elevation = (0.0, 0.0, float(e.dxf.elevation))
            if e.dxf.hasattr("extrusion"):
                poly.dxf.extrusion = e.dxf.extrusion
            block.delete_entity(e)
            converted[_space_of(block)] += 1
    return dict(converted)


def _old_release_findings(doc, version: str, had_units: bool) -> list[dict[str, Any]]:
    """The properties an older release cannot hold, found by looking at what the document uses."""
    notes = dict(V.OLD_RELEASE_NOTES.get(version, ()))
    if not notes:
        return []
    used = {"lineweight": 0, "true_colour": 0, "transparency": 0, "unicode": 0}
    encoding = "cp1252"
    try:
        encoding = str(doc.encoding)
    except Exception:  # noqa: BLE001
        pass
    for layer in doc.layers:
        if int(layer.dxf.get("lineweight", -3)) >= 0:
            used["lineweight"] += 1
        if layer.dxf.hasattr("true_color"):
            used["true_colour"] += 1
    for block in doc.blocks:
        for e in block:
            d = e.dxf
            if d.hasattr("lineweight") and int(d.lineweight) >= 0:
                used["lineweight"] += 1
            if d.hasattr("true_color"):
                used["true_colour"] += 1
            if d.hasattr("transparency"):
                used["transparency"] += 1
            if e.dxftype() in TEXT_TYPES:
                try:
                    (e.text if e.dxftype() == "MTEXT" else d.get("text", "")).encode(encoding)
                except (UnicodeEncodeError, LookupError, AttributeError):
                    used["unicode"] += 1
    used["units"] = 1 if had_units else 0
    return [V.loss(code, notes[code], used[code]) for code in notes if used.get(code)]


def _rewrite(ezdxf, doc, version: str, had_units: bool, outdir: str, limits: V.ConvertLimits, clock: _Clock) -> dict[str, Any]:
    target = V.DXF_VERSIONS[version]
    source_version = str(doc.dxfversion)
    before = _inventory(doc)
    layouts_before = len(list(doc.layouts))
    findings = _old_release_findings(doc, version, had_units)
    converted: dict[str, int] = _lwpolylines_to_polylines(doc) if version == "R12" else {}
    doc.dxfversion = target
    out_path = os.path.join(outdir, "converted.dxf")
    with _fixed_metadata(ezdxf):
        doc.classes.add_required_classes(doc.dxfversion)
        doc.classes.classes = OrderedDict(sorted(doc.classes.classes.items()))
        doc.saveas(out_path)
    clock.check()
    size = os.path.getsize(out_path)
    if size > limits.max_output_bytes:
        raise V.too_large("size of the output in bytes", size, limits.max_output_bytes)
    try:
        back = ezdxf.readfile(out_path)
    except Exception as exc:  # noqa: BLE001
        raise ToolFailure("engine.failure", f"the converted DXF could not be read back: {type(exc).__name__}: {exc}", {}) from exc
    after = _inventory(back)
    dropped: dict[str, dict[str, int]] = {}
    for space in ("model", "paper", "blocks"):
        expected = Counter(before[space])
        if converted.get(space):
            expected["LWPOLYLINE"] -= converted[space]
            expected["POLYLINE"] += converted[space]
        for kind_, want in expected.items():
            if want > 0 and after[space].get(kind_, 0) < want:
                dropped.setdefault(space, {})[kind_] = want - after[space].get(kind_, 0)
    items = list(findings)
    if converted:
        items.append(V.loss("entity_converted", f"LWPOLYLINE cannot be stored in {version}; each became a 2D POLYLINE with the same vertices, bulges and widths", sum(converted.values()), type="LWPOLYLINE", to="POLYLINE"))
    for space, row in dropped.items():
        for kind_, n in sorted(row.items()):
            items.append(V.loss("entity_dropped", f"{kind_} ({space.replace('blocks', 'block definitions')}) is not stored in {version}: the writer left it out", n, type=kind_, space=space))
    layouts_after = len(list(back.layouts))
    if layouts_after < layouts_before:
        items.append(V.loss("layouts", f"{version} keeps model space and one paper space: {layouts_before - layouts_after} layouts are not stored", layouts_before - layouts_after))
    return {
        "files": [{"name": "converted.dxf", "format": "dxf", "bytes": size}],
        "dxf": {"from": source_version, "to": target, "release": version, "written_by": f"ezdxf {ezdxf.__version__}"},
        "entities_before": {k: dict(sorted(v.items())) for k, v in before.items()},
        "entities_after": {k: dict(sorted(v.items())) for k, v in after.items()},
        "dropped": dropped,
        "converted": converted,
        "items": items,
    }


def convert_file(path: str, request: dict[str, Any], outdir: str) -> dict[str, Any]:
    """Convert one DXF file; runs in a child process (:mod:`eng_toolkit.isolated`) because the file is untrusted.

    ``request``: ``format`` (pdf, svg, png or dxf), ``sheet``, ``ratio`` ([n, d] or null for auto), ``dxf_version``, ``limits``
    (:class:`~eng_toolkit.domains.drawing.convert.ConvertLimits` as a dict) and ``budget_s``.  The output goes into ``outdir`` as
    ``converted.<format>``; the return value is plain JSON: the files, the units, the page, the entity counts and the loss report.
    """
    limits = V.ConvertLimits(**request["limits"])
    clock = _Clock(float(request["budget_s"]))
    fmt = request["format"]
    ezdxf, doc, repair, warnings = _convert_open(path, limits, clock)
    raw = DR._raw_header(path)
    units, mm_per_unit, unit_warnings = _units(doc, raw)
    had_units = (DR._number(raw, "$INSUNITS") if raw is not None else doc.header.get("$INSUNITS")) is not None
    if fmt != "dxf":
        warnings += unit_warnings

    containers: dict[str, Any] = {b.name.lower(): b for b in doc.blocks}
    msp_key = next(k for k in containers if k.startswith("*model_space"))
    containers[msp_key] = doc.modelspace()
    inventory = _inventory(doc)
    if not any(sum(c.values()) for c in inventory.values()):
        raise ToolFailure("drawing.empty", "the file has no entities: nothing to convert", {})
    scans = {key: _scan_container(container, limits.max_text_chars) for key, container in containers.items()}
    removals: list[tuple[Any, Any, str]] = []
    totals = _analyse(scans, [msp_key] if fmt != "dxf" else list(containers), limits.max_nesting, lambda entity, key, reason: removals.append((containers[key], entity, reason)))
    for key in totals:
        removals += [(containers[key], e, f"text longer than {limits.max_text_chars} characters") for e in scans[key].oversize]
    if fmt != "dxf":
        t = totals[msp_key]
        work = V.weigh(t.entities, t.vertices, t.chars)
        if work > limits.max_work:
            raise V.too_large("work to draw model space (entities + vertices + 5 per text character, block references expanded)", work, limits.max_work)
    clock.check()
    limit_units = max(limits.max_coordinate_mm / mm_per_unit, 1e9)
    removals += _check_numbers(containers, totals.keys(), limit_units, mm_per_unit, clock)
    removed: Counter = Counter()
    seen: set[int] = set()
    for container, entity, reason in removals:
        if id(entity) in seen:
            continue
        seen.add(id(entity))
        container.delete_entity(entity)
        removed[(entity.dxftype(), reason)] += 1

    xrefs = sum(1 for b in doc.blocks if not b.name.startswith("*") and int(b.block.dxf.get("flags", 0)) & 12)
    items: list[dict[str, Any]] = []
    for (kind_, reason), n in sorted(removed.items()):
        items.append(V.loss("entity_removed", f"{kind_} removed before conversion: {reason}", n, type=kind_, reason=reason))
    if xrefs:
        items.append(V.loss("xref_not_loaded", "External references are not loaded: what they draw is missing (nothing outside the file is opened)", xrefs))

    out: dict[str, Any] = {
        "format": fmt,
        "units": units,
        "repair": repair,
        "limits": limits.as_dict(),
        "dxf_release_in": DR.RELEASES.get(str((raw or {}).get("$ACADVER") or doc.header.get("$ACADVER", "")), "unknown"),
        "work": {"entities": totals[msp_key].entities, "vertices": totals[msp_key].vertices, "text_chars": totals[msp_key].chars, "max_nesting": totals[msp_key].height},
    }
    if fmt == "dxf":
        done = _rewrite(ezdxf, doc, request["dxf_version"], had_units, outdir, limits, clock)
        items = done.pop("items") + items
        out.update(done)
        out["entities"] = {"model_space_by_type": done["entities_after"]["model"], "written_total": sum(done["entities_after"]["model"].values())}
    else:
        done = _render(doc, fmt, request, limits, mm_per_unit, clock, outdir)
        tally: _Tally = done.pop("tally")
        more, summary = _render_losses(doc, fmt, tally, inventory)
        items += more
        out.update(done)
        out["entities"] = summary
    out["losses"] = {"items": items, "lossless": not items}
    out["warnings"] = [_txt(w, 1000) for w in dict.fromkeys(warnings)][:30]
    return DR._bound(out)


__all__ = ["CONVERT_TARGET", "DXF_VERSION", "convert_file", "dwg_unavailable", "render_pdf", "render_svg", "require_engine", "text_warnings", "write_dxf"]
