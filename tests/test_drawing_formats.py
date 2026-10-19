"""drawing_make_views: the SVG (our own writer) and the PNG (our PDF rasterised by PDFium).

Checks that do not use the writers: the SVG is parsed with the standard library's XML parser and its stroke lengths are
compared with the ones pdfminer.six reads from the PDF of the same drawing; the PNG is decoded by Pillow and its pixels are
looked at where the geometry says there is ink (the frame, 20 mm from the left edge, 0.7 mm wide: pixel 118 at 150 dpi).
"""

import io
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.drawing import raster
from eng_toolkit.domains.drawing.tools import make_views
from eng_toolkit.domains.drawing.tools import read as read_tool

pytest.importorskip("ezdxf", reason="ezdxf is not installed; run: uv run --extra drawing")
pytest.importorskip("pdfminer", reason="pdfminer.six is not installed; run: uv sync --extra drawing")
pytest.importorskip("pypdfium2", reason="pypdfium2 is not installed; run: uv sync --extra png")
PIL = pytest.importorskip("PIL.Image", reason="Pillow is a dev dependency (it comes with pdfplumber)")

SVG = "{http://www.w3.org/2000/svg}"
MM_PER_PT = 25.4 / 72.0


@pytest.fixture(scope="module")
def probe_model(tmp_path_factory):
    pytest.importorskip("OCP", reason="OCP is not installed; run: uv sync --extra cad")
    import cad_fixtures as cad

    return cad.write_step(cad.probe_part(), tmp_path_factory.mktemp("formats") / "probe.step")


def run(model, **kwargs) -> ToolResult:
    result = getattr(make_views, "fn", make_views)(model_path=str(model), **kwargs)
    assert isinstance(result, ToolResult)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    return result


def artifact(result: ToolResult, name: str) -> Path:
    return next(Path(a.path) for a in result.artifacts if Path(a.path).name == name)


@pytest.fixture(scope="module")
def all_formats(probe_model):
    r = run(probe_model, title="PROBE", formats="dxf,pdf,svg,png")
    assert r.ok, r.error
    return r


# --------------------------------------------------------------------------- SVG


def test_the_svg_is_written_and_named_in_the_result(all_formats):
    r = all_formats
    assert r.data["formats"] == ["dxf", "pdf", "svg", "png"]
    assert [a.format for a in r.artifacts if a.format in ("dxf", "pdf", "svg", "png")] == ["dxf", "pdf", "svg", "png"]
    assert artifact(r, "drawing.svg").read_bytes().startswith(b'<?xml version="1.0" encoding="UTF-8"?>\n<svg ')


def test_the_svg_is_well_formed_a4_in_millimetres_and_has_nothing_active_in_it(all_formats):
    root = ET.fromstring(artifact(all_formats, "drawing.svg").read_bytes())
    assert root.tag == f"{SVG}svg"
    assert (root.get("width"), root.get("height"), root.get("viewBox")) == ("297mm", "210mm", "0 0 297 210")
    tags = {el.tag.removeprefix(SVG) for el in root.iter()}
    assert tags == {"svg", "rect", "g", "path"}  # no script, style, image, foreignObject or link
    assert all(not k.startswith("on") and "href" not in k for el in root.iter() for k in el.attrib)
    (group,) = root.findall(f"{SVG}g")
    assert group.get("transform") == "translate(0 210) scale(1 -1)"  # y points up in the drawing


def path_lengths(root) -> dict[tuple[float, bool], float]:
    """Stroke length per (weight in mm, dashed) of every stroked <path>, from the path data; curves in 32 chords as the PDF reader does."""
    out: dict[tuple[float, bool], float] = {}
    for el in root.iter(f"{SVG}path"):
        if el.get("stroke-width") is None:
            continue
        total, here, start = 0.0, None, None
        tokens = re.findall(r"[MLCQZ]|-?\d+(?:\.\d+)?", el.get("d"))
        i = 0
        while i < len(tokens):
            c = tokens[i]
            i += 1
            if c in "ML":
                point = (float(tokens[i]), float(tokens[i + 1]))
                i += 2
                if c == "L":
                    total += math.dist(here, point)
                else:
                    start = point
                here = point
            elif c == "C":
                p1, p2, p3 = (float(tokens[i]), float(tokens[i + 1])), (float(tokens[i + 2]), float(tokens[i + 3])), (float(tokens[i + 4]), float(tokens[i + 5]))
                i += 6
                prev = here
                for k in range(1, 33):
                    t = k / 32
                    q = tuple((1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t**2 * c_ + t**3 * d for a, b, c_, d in zip(here, p1, p2, p3))
                    total += math.dist(prev, q)
                    prev = q
                here = p3
            elif c == "Q":
                p1, p2 = (float(tokens[i]), float(tokens[i + 1])), (float(tokens[i + 2]), float(tokens[i + 3]))
                i += 4
                total += math.dist(here, p1) + math.dist(p1, p2)  # coarse: quadratic pieces are glyph outlines, which are filled, not stroked
                here = p2
        key = (round(float(el.get("stroke-width")), 3), el.get("stroke-dasharray") is not None)
        out[key] = out.get(key, 0.0) + total
    return out


def test_the_svg_strokes_have_the_lengths_pdfminer_reads_from_the_pdf(all_formats):
    svg = path_lengths(ET.fromstring(artifact(all_formats, "drawing.svg").read_bytes()))
    pdf = getattr(read_tool, "fn", read_tool)(path=str(artifact(all_formats, "drawing.pdf")))
    assert pdf.ok, pdf.error
    from_pdf: dict[tuple[float, bool], float] = {}
    full = json.loads(Path(pdf.data["full_json"]).read_text(encoding="utf-8"))
    for style in full["pages"][0]["vector"]["by_style"]:
        key = (round(style["width_pt"] * MM_PER_PT, 3), style["dashed"])
        from_pdf[key] = from_pdf.get(key, 0.0) + style["length_mm"]
    assert set(svg) == set(from_pdf) and len(svg) >= 4  # frame 0.7, title 0.35, outline 0.5, hidden 0.25 dashed, dimensions 0.25
    for key, length in from_pdf.items():
        assert svg[key] == pytest.approx(length, rel=2e-3), key


def test_the_svg_has_the_hidden_lines_dashed_and_text_as_filled_outlines(all_formats):
    root = ET.fromstring(artifact(all_formats, "drawing.svg").read_bytes())
    dashed = [el for el in root.iter(f"{SVG}path") if el.get("stroke-dasharray")]
    assert dashed
    assert {el.get("stroke-dasharray") for el in dashed} == {"3 2"}  # HIDDEN_MM: 3 mm dash, 2 mm gap
    assert {el.get("stroke-width") for el in dashed} == {"0.25"}
    filled = [el for el in root.iter(f"{SVG}path") if el.get("fill") == "#000"]
    assert len(filled) > 20  # the glyphs of the title block, the callout and the dimensions


def test_the_svg_alone_needs_no_pdfium(probe_model, monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdfium2", None)
    r = run(probe_model, formats="svg")
    assert r.ok and r.data["formats"] == ["dxf", "svg"] and "pypdfium2" not in r.provenance.engine_versions


# --------------------------------------------------------------------------- PNG


def test_the_png_is_an_8_bit_grey_a4_page_at_150_dpi(all_formats):
    data = artifact(all_formats, "drawing.png").read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    image = PIL.open(io.BytesIO(data))
    assert (image.mode, image.format) == ("L", "PNG")
    assert image.size == (1754, 1241)  # 297 mm and 210 mm at 150 / 25.4 pixels per mm, as PDFium rounds a 841.89 x 595.28 pt page
    assert all_formats.data["png"] == {"width_px": 1754, "height_px": 1241, "dpi": 150.0, "greyscale": True, "from": "drawing.pdf rendered by PDFium"}
    assert image.info == {}  # no text, time or gamma chunk


def test_the_png_has_ink_where_the_frame_is_and_white_where_it_is_not(all_formats):
    image = PIL.open(io.BytesIO(artifact(all_formats, "drawing.png").read_bytes()))
    px = 150.0 / 25.4  # pixels per millimetre
    middle = image.height // 2

    def at(x_mm: float, y_mm_from_top: float) -> int:
        return image.getpixel((round(x_mm * px), round(y_mm_from_top * px)))

    assert at(20.0, 105.0) < 100  # the frame's left edge at x = 20 mm, 0.7 mm wide
    assert at(10.0, 105.0) > 250  # outside the frame: the 10 mm of white paper
    assert at(150.0, 10.0) < 100  # the frame's top edge, 10 mm below the top of the sheet (y = 200 mm from the bottom)
    assert at(287.0, 105.0) < 100  # the right edge, x = 287 mm
    pixels = image.tobytes()
    assert min(pixels) == 0 and sum(pixels) / len(pixels) > 240  # black ink on mostly white paper
    assert image.getpixel((5, middle)) == 255


def test_the_png_shows_the_hole_circle_where_the_hole_is(all_formats):
    """The top view's origin is in data.views; the hole is at (25, 25) mm from its corner, radius 5: ink on the circle, paper at its centre."""
    image = PIL.open(io.BytesIO(artifact(all_formats, "drawing.png").read_bytes()))
    px = 150.0 / 25.4
    top = next(v for v in all_formats.data["views"] if v["name"] == "top")
    cx, cy = top["origin_mm"][0] + 25.0, top["origin_mm"][1] + 25.0  # sheet mm, y up
    row = lambda y_mm: round((210.0 - y_mm) * px)  # noqa: E731
    assert image.getpixel((round((cx + 5.0) * px), row(cy))) < 100  # on the circle's right end
    assert image.getpixel((round(cx * px), row(cy + 5.0))) < 100  # and its top
    assert image.getpixel((round((cx + 2.0) * px), row(cy + 2.0))) > 250  # inside: paper (the extension lines meet at the centre only)


def test_png_alone_writes_no_pdf_file_and_names_pdfium_in_the_provenance(probe_model):
    import importlib.metadata as md

    r = run(probe_model, formats="png")
    assert r.ok and r.data["formats"] == ["dxf", "png"]
    assert not any(Path(a.path).name == "drawing.pdf" for a in r.artifacts) and artifact(r, "drawing.png").exists()
    assert r.provenance.engine_versions["pypdfium2"] == md.version("pypdfium2")


def test_without_pypdfium2_png_is_engine_unavailable_with_the_extra_to_install(probe_model, monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdfium2", None)
    r = run(probe_model, formats="png")
    assert r.ok is False and r.error.family == "engine.unavailable"
    assert "uv sync --extra png" in r.error.message and r.error.details["install"] == "uv sync --extra png"


def test_the_warnings_say_which_pictures_have_no_text_and_what_the_png_guarantees(all_formats):
    text = " ".join(all_formats.warnings)
    assert "The PDF, SVG, PNG have no text layer" in text and "pixels in the PNG" in text
    assert "1754 x 1241 pixel greyscale raster (150.0 dpi)" in text and "another machine" in text


def test_a_png_is_the_same_bytes_every_time_and_when_four_threads_render_at_once(probe_model):
    first = artifact(run(probe_model, formats="png"), "drawing.png").read_bytes()
    with ThreadPoolExecutor(max_workers=4) as pool:
        rest = list(pool.map(lambda _: artifact(run(probe_model, formats="png"), "drawing.png").read_bytes(), range(4)))
    assert all(png == first for png in rest)


# --------------------------------------------------------------------------- the raster module on its own


def test_the_resolution_is_150_dpi_and_at_most_4000_pixels_on_the_long_side():
    assert raster.pixels_per_mm(297.0, 210.0) == pytest.approx(150.0 / 25.4)
    assert raster.pixels_per_mm(420.0, 297.0) == pytest.approx(150.0 / 25.4)  # A3: 2480 px
    a0 = raster.pixels_per_mm(1189.0, 841.0)
    assert a0 == pytest.approx(4000.0 / 1189.0) and a0 * 1189.0 <= 4000.0 + 1e-9 and a0 * 25.4 == pytest.approx(85.4, abs=0.1)


def test_encode_png_round_trips_through_an_independent_decoder_and_holds_only_the_required_chunks():
    width, height = 7, 5
    gray = bytes((x * 31 + y * 17) % 256 for y in range(height) for x in range(width))
    png = raster.encode_png(gray, width, height)
    image = PIL.open(io.BytesIO(png))
    assert image.size == (width, height) and image.mode == "L" and image.tobytes() == gray
    chunks, at = [], 8
    while at < len(png):
        length = int.from_bytes(png[at : at + 4], "big")
        kind = png[at + 4 : at + 8]
        assert int.from_bytes(png[at + 8 + length : at + 12 + length], "big") == zlib.crc32(kind + png[at + 8 : at + 8 + length])
        chunks.append(kind)
        at += 12 + length
    assert chunks == [b"IHDR", b"IDAT", b"IEND"]
    assert raster.encode_png(gray, width, height) == png


def test_encode_png_refuses_pixels_that_do_not_fit_the_size():
    with pytest.raises(ValueError):
        raster.encode_png(b"\x00" * 5, 2, 3)
