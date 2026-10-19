"""The one owner of ezdxf (optional extra ``drawing``): a :class:`Drawing` becomes a DXF file and a PDF page.

Both come from one ezdxf document built from the :class:`~eng_toolkit.domains.drawing.sheet.Drawing`, so the PDF shows
what the DXF holds.  The same drawing gives the same bytes, in this process and in any other.

* The DXF is R2018 in millimetres.  ezdxf's ``write_fixed_meta_data_for_testing`` option removes the dates and GUIDs;
  it is set for the time of one build and restored.  That alone is not enough: ezdxf adds the CLASS records of the
  entity types in use by iterating a ``set`` of names, so their order depends on ``PYTHONHASHSEED`` (measured: the
  records LAYOUT and ACDBPLACEHOLDER swap places).  The order of CLASS records has no meaning, so the classes are
  registered and sorted before saving (docs/geometry-drawing/drawing-determinism.md).
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
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager

from eng_toolkit.brep import Arc2, Ellipse2, Line2, Poly2
from eng_toolkit.domains.drawing import conventions as C
from eng_toolkit.domains.drawing.dimensions import escape_percent
from eng_toolkit.domains.drawing.sheet import Drawing, PlacedView, SheetText
from eng_toolkit.errors import EngineUnavailable
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


def _num(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _pdf_backend_class():
    """The backend class, made on first use because it derives from a class of ezdxf, which may not be installed."""
    from ezdxf.addons.drawing.backend import BackendInterface
    from ezdxf.path import Command

    class _PdfBackend(BackendInterface):
        """Replays ezdxf's recorded drawing as PDF operators in sheet millimetres converted to points."""

        def __init__(self, width_mm: float, height_mm: float) -> None:
            self.width_mm, self.height_mm = width_mm, height_mm
            self.ops: list[str] = ["0 0 0 RG 0 0 0 rg 1 j"]
            self._weight: float | None = None
            self._dash: tuple[float, ...] | None = None

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
            layer = C.LAYER_BY_NAME.get(properties.layer)
            dash = C.dash_array_mm(layer.linetype) if layer else ()
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


def render_pdf(drawing: Drawing) -> bytes:
    """The PDF page of a drawing (see the module docstring).  ``engine.unavailable`` without ezdxf."""
    ezdxf = require_engine()
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration, LinePolicy, LineweightPolicy
    from ezdxf.addons.drawing.recorder import Recorder

    config = Configuration(
        line_policy=LinePolicy.SOLID,  # dashes are PDF dashes, set from the layer's linetype
        lineweight_policy=LineweightPolicy.ABSOLUTE,
        color_policy=ColorPolicy.BLACK,
        background_policy=BackgroundPolicy.WHITE,
    )
    with _fixed_metadata(ezdxf):
        doc = _build(ezdxf, drawing)
        recorder = Recorder()
        Frontend(RenderContext(doc), recorder, config=config).draw_layout(doc.modelspace())
    backend = _pdf_backend_class()(*drawing.size_mm)
    recorder.player().replay(backend)
    return backend.document()


__all__ = ["DXF_VERSION", "render_pdf", "require_engine", "text_warnings", "write_dxf"]
