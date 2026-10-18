"""The DXF and PDF writers (``drawing/dxf.py``) on hand-made drawings, read back with independent readers.

The DXF is read with tests/dxf_reader.py (standard library only), the PDF with pdfplumber and with regular expressions;
ezdxf is used only where the test is about ezdxf's own view of the file.  No OpenCASCADE needed.

Rich front view (tests/drawing_fixtures.py), 1:1: OUTLINE lines 4 x (100, 20, 100, 20) = 240, circle R5 = 31.4159,
quarter arc R3 = 4.7124, polyline 2 * hypot(5, 4) = 12.8062 -> 288.9345 mm (the ellipse has no closed-form length
here); HIDDEN 2 x 20 = 40.  At 1:2 every drawn length halves.
"""

import math
import re
import sys

import dxf_reader as R
import pytest
from drawing_fixtures import projection, rich_front

from eng_toolkit.domains.drawing import conventions as C
from eng_toolkit.domains.drawing import sheet as S
from eng_toolkit.errors import EngineUnavailable

ezdxf = pytest.importorskip("ezdxf", reason="ezdxf is not installed; run: uv sync --extra drawing")
pdfplumber = pytest.importorskip("pdfplumber", reason="pdfplumber is a dev dependency; run: uv sync --all-groups --extra drawing")
from eng_toolkit.domains.drawing import dxf  # noqa: E402

PT_PER_MM = 72.0 / 25.4
OUTLINE_TOTAL = 240 + 2 * math.pi * 5 + math.pi / 2 * 3 + 2 * math.hypot(5, 4)


def rich(scale="1:1", **kwargs) -> S.Drawing:
    return S.compose([rich_front()], method="third_angle", scale=S.parse_scale(scale), dimensions="none", **kwargs)


def three_views(scale="1:1", **kwargs) -> S.Drawing:
    return S.compose([projection(v) for v in ("front", "top", "right")], method="third_angle", scale=S.parse_scale(scale), **kwargs)


def read(drawing: S.Drawing) -> R.Dxf:
    return R.read(dxf.write_dxf(drawing))


def box_of(drawing: S.Drawing, name: str, pad: float = 1.0):
    (x0, y0), (x1, y1) = next(v for v in drawing.views if v.name == name).box
    return x0 - pad, y0 - pad, x1 + pad, y1 + pad


# --------------------------------------------------------------------------- file level


def test_the_file_is_dxf_r2018_in_millimetres():
    f = read(rich())
    assert f.header_value("$ACADVER", 1) == "AC1032"
    assert f.header_value("$INSUNITS", 70) == "4"


def test_ezdxf_reads_the_file_back_and_the_audit_finds_nothing(tmp_path):
    path = tmp_path / "d.dxf"
    path.write_bytes(dxf.write_dxf(three_views(title=S.TitleBlock(title="BRACKET", notes=("deburr",)))))
    doc = ezdxf.readfile(path)
    assert doc.dxfversion == "AC1032"
    audit = doc.audit()
    assert not audit.has_errors and not audit.has_fixes
    assert len(doc.modelspace().query("DIMENSION")) == 3


def test_the_layers_carry_the_conventions():
    f = read(rich())
    assert set(f.layers) >= {layer.name for layer in C.LAYERS}
    for layer in C.LAYERS:
        record = f.layers[layer.name]
        assert int(record.number(370)) == round(layer.weight_mm * 100), layer.name
        assert record.get(6) == layer.linetype, layer.name
    assert [layer.weight_mm for layer in (C.OUTLINE, C.HIDDEN, C.DIM, C.TEXT, C.FRAME)] == [0.5, 0.25, 0.25, 0.25, 0.7]


def test_the_metric_linetypes_are_defined():
    f = read(rich())
    hidden = f.linetypes["HIDDEN_MM"]
    assert hidden.number(40) == 5.0 and hidden.numbers(49) == [3.0, -2.0]
    center = f.linetypes["CENTER_MM"]
    assert center.number(40) == 28.0 and center.numbers(49) == [20.0, -3.0, 2.0, -3.0]


def test_the_dimension_style_has_the_values_that_make_it_readable_on_a_millimetre_sheet():
    style = read(rich()).dimstyles["ENG_ISO"]
    expected = {140: 3.5, 41: 3.0, 42: 1.0, 44: 1.5, 147: 1.0, 77: 1.0, 78: 8.0}  # DIMTXT DIMASZ DIMEXO DIMEXE DIMGAP DIMTAD DIMZIN
    assert {code: style.number(code) for code in expected} == expected


def test_the_classes_section_is_in_a_fixed_order():
    classes = read(three_views()).classes
    assert classes and classes == sorted(classes)


# --------------------------------------------------------------------------- geometry


def test_every_primitive_kind_becomes_its_dxf_entity():
    f = read(rich())
    kinds = {t: [e for e in f.of(t, "OUTLINE")] for t in ("LINE", "ARC", "CIRCLE", "ELLIPSE", "LWPOLYLINE")}
    assert {t: len(v) for t, v in kinds.items()} == {"LINE": 4, "ARC": 1, "CIRCLE": 1, "ELLIPSE": 1, "LWPOLYLINE": 1}
    assert len(f.of("LINE", "HIDDEN")) == 2 and not f.of("ARC", "HIDDEN")


@pytest.mark.parametrize(("scale", "factor"), [("1:1", 1.0), ("1:2", 0.5), ("2:1", 2.0)])
def test_drawn_lengths_follow_the_scale(scale, factor):
    f = read(rich(scale))
    assert f.total_length("OUTLINE") == pytest.approx(OUTLINE_TOTAL * factor, abs=1e-3)
    assert f.total_length("HIDDEN") == pytest.approx(40.0 * factor, abs=1e-3)


def test_each_coordinate_is_origin_plus_scale_times_the_model_coordinate():
    d = rich("1:2")
    (view,) = d.views
    ox, oy = view.origin
    f = read(d)
    ends = sorted(tuple(round(c, 4) for c in (*e.point(10, 20), *e.point(11, 21))) for e in f.of("LINE", "OUTLINE"))
    expected = sorted(
        tuple(round(c, 4) for c in (ox + 0.5 * a[0], oy + 0.5 * a[1], ox + 0.5 * b[0], oy + 0.5 * b[1]))
        for a, b in (((0, 0), (100, 0)), ((100, 0), (100, 20)), ((0, 20), (100, 20)), ((0, 0), (0, 20)))
    )
    assert ends == expected
    (circle,) = f.of("CIRCLE")
    assert circle.point(10, 20) == pytest.approx((ox + 25.0, oy + 5.0), abs=1e-4) and circle.number(40) == pytest.approx(2.5, abs=1e-6)
    (arc,) = f.of("ARC")
    assert arc.point(10, 20) == pytest.approx((ox + 10.0, oy + 5.0), abs=1e-4)
    assert (arc.number(40), arc.number(50), arc.number(51)) == pytest.approx((1.5, 0.0, 90.0), abs=1e-6)
    (ellipse,) = f.of("ELLIPSE")
    assert ellipse.point(10, 20) == pytest.approx((ox + 35.0, oy + 5.0), abs=1e-4)
    assert ellipse.point(11, 21) == pytest.approx((4.0, 0.0), abs=1e-6)  # relative to the centre
    assert (ellipse.number(40), ellipse.number(41), ellipse.number(42)) == pytest.approx((0.5, 0.0, 2 * math.pi), abs=1e-6)
    (poly,) = f.of("LWPOLYLINE")
    assert poly.vertices() == pytest.approx([(ox + 40.0, oy + 1.0), (ox + 42.5, oy + 3.0), (ox + 45.0, oy + 1.0)], abs=1e-4)


# --------------------------------------------------------------------------- dimensions


def independent_measure(e: R.Entity) -> float:
    """Distance between the two measured points along the dimension line direction, times DIMLFAC."""
    a, b = e.point(13, 23), e.point(14, 24)
    angle = math.radians(e.number(50, 0.0))
    along = abs((b[0] - a[0]) * math.cos(angle) + (b[1] - a[1]) * math.sin(angle))
    return along * e.overrides().get(144, 1.0)


@pytest.mark.parametrize("scale", ["1:1", "1:2", "2:1"])
def test_dimensions_are_real_entities_that_read_the_true_size_at_any_scale(scale, tmp_path):
    d = three_views(scale)
    f = read(d)
    dims = f.of("DIMENSION", "DIM")
    assert len(dims) == 3 and not f.of("DIMENSION", "0")
    assert all(e.dim_kind == 0 for e in dims)  # linear
    assert sorted(round(independent_measure(e), 4) for e in dims) == [20.0, 50.0, 100.0]
    assert all(e.overrides().get(144, 1.0) == pytest.approx(d.dimlfac) for e in dims)
    path = tmp_path / "d.dxf"
    path.write_bytes(dxf.write_dxf(d))
    doc = ezdxf.readfile(path)
    shown = []
    for e in doc.modelspace().query("DIMENSION"):
        lfac = e.override().get("dimlfac", doc.dimstyles.get(e.dxf.dimstyle).dxf.dimlfac)
        shown.append(round(e.get_measurement() * lfac, 4))
        assert e.dxf.dimstyle == "ENG_ISO"
    assert sorted(shown) == [20.0, 50.0, 100.0]


def test_the_dimension_text_in_the_block_reads_the_true_size():
    d = three_views("1:2")
    f = read(d)
    texts = []
    for e in f.of("DIMENSION"):
        block = f.blocks[e.get(2)]
        texts += [b.text for b in block if b.type in ("MTEXT", "TEXT")]
    assert sorted(texts) == ["100", "20", "50"]


def test_a_horizontal_dimension_lies_below_its_view_and_a_vertical_one_to_its_left():
    d = three_views()
    front = next(v for v in d.views if v.name == "front")
    (x0, y0), _ = front.box
    horizontal = next(e for e in read(d).of("DIMENSION") if (e.number(50, 0.0) or 0.0) % 180 == 0 and abs(independent_measure(e) - 100) < 1e-6)
    assert horizontal.point(10, 20)[1] == pytest.approx(y0 - 8.0, abs=1e-6)
    assert horizontal.point(13, 23) == pytest.approx((x0, y0), abs=1e-6)


# --------------------------------------------------------------------------- sheet furniture and text


def test_frame_title_block_and_status_line_are_drawn():
    d = three_views(title=S.TitleBlock(title="BRACKET", part_number="BR-001", material="AL 6061", revision="B", general_tolerance="ISO 2768-m", notes=("deburr", "break edges")))
    f = read(d)
    assert len(f.of("LINE", "FRAME")) == 4
    assert len(f.of("LINE", "TITLE")) >= 4
    texts = {e.text for e in f.of("TEXT")}
    assert {"BRACKET", "BR-001", "AL 6061", "B", "ISO 2768-m", "1:1", "A4", "THIRD ANGLE", C.STATUS_LINE, "1. deburr", "2. break edges"} <= texts
    assert {e.layer for e in f.of("TEXT")} == {"TITLE", "TEXT"}


def test_non_ascii_text_round_trips():
    title = "支架 é Ø10 ±0.1"
    f = read(three_views(title=S.TitleBlock(title=title, notes=("去毛邊",))))
    texts = {e.text for e in f.of("TEXT")}
    assert title in texts and "1. 去毛邊" in texts


def test_chinese_text_gets_a_style_with_a_font_that_has_the_glyphs():
    f = read(three_views(title=S.TitleBlock(title="支架")))
    styles = {e.text: e.get(7) for e in f.of("TEXT")}
    assert styles["支架"] == "ENG_ISO_CJK" and styles["A4"] == "ENG_ISO"


def test_latin_only_text_needs_no_second_style():
    f = read(three_views(title=S.TitleBlock(title="BRACKET")))
    assert {e.get(7) for e in f.of("TEXT")} == {"ENG_ISO"}
    assert dxf.text_warnings(three_views(title=S.TitleBlock(title="BRACKET Ø10 ±0.1 é"))) == []


def test_glyphs_no_installed_font_has_are_reported_by_character(monkeypatch):
    d = three_views(title=S.TitleBlock(title="支架 BRACKET"))
    assert dxf.text_warnings(d) == []
    monkeypatch.setattr(C, "FALLBACK_FONTS", ())
    (warning,) = dxf.text_warnings(d)
    assert "支" in warning and "架" in warning and "BRACKET" not in warning and "U+652F" in warning
    dxf.write_dxf(d)  # still writes the text, whatever the PDF shows for it


# --------------------------------------------------------------------------- determinism and process hygiene


def test_two_writes_give_the_same_bytes():
    d = three_views(title=S.TitleBlock(title="支架"))
    assert dxf.write_dxf(d) == dxf.write_dxf(d)


def test_the_global_ezdxf_option_is_restored():
    before = ezdxf.options.write_fixed_meta_data_for_testing
    dxf.write_dxf(rich())
    dxf.render_pdf(rich())
    assert ezdxf.options.write_fixed_meta_data_for_testing == before


def test_a_missing_engine_gives_an_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "ezdxf", None)
    with pytest.raises(EngineUnavailable) as caught:
        dxf.write_dxf(rich())
    assert caught.value.family == "engine.unavailable"
    assert caught.value.details["install"] == "uv sync --extra drawing"


def test_writing_prints_nothing(capfd):
    dxf.write_dxf(three_views(title=S.TitleBlock(title="支架")))
    dxf.render_pdf(three_views(title=S.TitleBlock(title="支架")))
    captured = capfd.readouterr()
    assert captured.out == "" and captured.err == ""


# --------------------------------------------------------------------------- PDF

SHEET_POINTS = {"A4": (841.89, 595.28), "A3": (1190.55, 841.89), "A2": (1683.78, 1190.55), "A1": (2383.94, 1683.78), "A0": (3370.39, 2383.94)}


def pdf_of(drawing: S.Drawing) -> bytes:
    return dxf.render_pdf(drawing)


def open_pdf(tmp_path, data: bytes):
    path = tmp_path / "d.pdf"
    path.write_bytes(data)
    return pdfplumber.open(path)


@pytest.mark.parametrize("sheet", list(S.SHEETS))
def test_the_page_is_the_sheet_in_points(sheet, tmp_path):
    data = pdf_of(S.compose([projection("front", (5.0, 5.0, 5.0))], method="third_angle", sheet=sheet, scale=(1, 1)))
    assert data.startswith(b"%PDF-1.4")
    media = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", data)
    assert (float(media.group(1)), float(media.group(2))) == pytest.approx(SHEET_POINTS[sheet], abs=0.006)
    with open_pdf(tmp_path, data) as pdf:
        assert len(pdf.pages) == 1
        assert (pdf.pages[0].width, pdf.pages[0].height) == pytest.approx(SHEET_POINTS[sheet], abs=0.01)
    assert (float(media.group(1)), float(media.group(2))) == pytest.approx((S.SHEETS[sheet][0] * PT_PER_MM, S.SHEETS[sheet][1] * PT_PER_MM), abs=0.006)


def test_the_pdf_has_no_text_layer_dates_producer_or_compression(tmp_path):
    data = pdf_of(three_views(title=S.TitleBlock(title="BRACKET")))
    assert not re.search(rb"/(Filter|Producer|Creator|CreationDate|ModDate|Info|Font)\b", data)
    with open_pdf(tmp_path, data) as pdf:
        page = pdf.pages[0]
        assert page.chars == []
        assert len(page.lines) + len(page.curves) > 100  # the text is there, as outlines


def test_line_weights_follow_the_layers(tmp_path):
    d = rich()
    with open_pdf(tmp_path, pdf_of(d)) as pdf:
        page = pdf.pages[0]
        ox, oy = d.views[0].origin
        outline = [line for line in page.lines if abs(line["x1"] - line["x0"] - 100 * PT_PER_MM) < 0.05 and abs(line["bottom"] - line["top"]) < 0.01]
        frame = [line for line in page.lines if abs(line["x1"] - line["x0"] - 267 * PT_PER_MM) < 0.05]
        assert len(outline) == 2 and len(frame) == 2
        assert all(line["linewidth"] == pytest.approx(0.5 * PT_PER_MM, abs=0.005) for line in outline)
        assert all(line["linewidth"] == pytest.approx(0.7 * PT_PER_MM, abs=0.005) for line in frame)
        bottom = min(outline, key=lambda line: -line["top"])
        assert bottom["x0"] == pytest.approx(ox * PT_PER_MM, abs=0.05)
        assert page.height - bottom["bottom"] == pytest.approx(oy * PT_PER_MM, abs=0.05)


def test_hidden_lines_are_dashed_with_the_dash_array_of_the_linetype(tmp_path):
    data = pdf_of(rich())
    dash = " ".join(f"{v * PT_PER_MM:.3f}" for v in (3.0, 2.0))
    assert re.search(rb"\[" + dash.encode() + rb"\] 0 d", data)
    with open_pdf(tmp_path, data) as pdf:
        hidden = [line for line in pdf.pages[0].lines if abs(line["linewidth"] - 0.25 * PT_PER_MM) < 0.005 and abs(line["bottom"] - line["top"] - 20 * PT_PER_MM) < 0.05]
        assert len(hidden) == 2
        assert all(line["dash"][0] == pytest.approx([3.0 * PT_PER_MM, 2.0 * PT_PER_MM], abs=0.01) for line in hidden)


def test_the_text_is_drawn_as_filled_outlines():
    data = pdf_of(three_views(title=S.TitleBlock(title="BRACKET")))
    assert data.count(b"f*") > 20


def test_two_renders_give_the_same_bytes():
    d = three_views(title=S.TitleBlock(title="支架"))
    assert pdf_of(d) == pdf_of(d)


def test_a_chinese_title_is_drawn_with_real_glyphs_not_boxes(monkeypatch):
    d = three_views(title=S.TitleBlock(title="支架"))
    drawn = pdf_of(d)
    assert drawn.count(b"f*") > pdf_of(three_views(title=S.TitleBlock(title=""))).count(b"f*")
    monkeypatch.setattr(C, "FALLBACK_FONTS", ())
    assert pdf_of(d) != drawn  # without a CJK font the characters are the font's missing-glyph box


def test_the_pdf_content_mirrors_the_dxf_geometry(tmp_path):
    d = rich("1:2")
    with open_pdf(tmp_path, pdf_of(d)) as pdf:
        page = pdf.pages[0]
        long = [line for line in page.lines if abs(line["linewidth"] - 0.5 * PT_PER_MM) < 0.005]
        assert sorted(round((line["x1"] - line["x0"]) / PT_PER_MM, 2) for line in long if abs(line["bottom"] - line["top"]) < 0.01) == [50.0, 50.0]
