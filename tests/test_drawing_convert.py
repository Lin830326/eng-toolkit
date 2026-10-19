"""drawing_convert end to end on hand-written DXF text (tests/raw_dxf.py), read back with independent PDF, SVG, PNG and DXF readers.

No input here was written by ezdxf and no output is read by ezdxf: the numbers come from the coordinates in the input and from the
unit arithmetic, 1 mm = 72 / 25.4 pt = 2.834646 pt:

    100 mm line at 1:1 on an automatic sheet: page 120 x 20 mm = 340.16 x 56.69 pt, the line from 28.346 to 311.811 pt = 283.465 pt
    10 inch line: 254 mm = 720 pt
"""

import hashlib
import json
import math
import typing
from pathlib import Path

import convert_check as CC
import dxf_reader as DR
import pytest
import raw_dxf as R

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.drawing import tools as drawing_tools

pytest.importorskip("ezdxf", reason="ezdxf is not installed; run: uv sync --extra drawing")
pytest.importorskip("pypdfium2", reason="pypdfium2 is not installed; run: uv sync --extra png")
PT = CC.PT_PER_MM


def _strict(value):
    raise ValueError(f"{value} in a result")


def convert(path, fmt, **kwargs) -> ToolResult:
    fn = getattr(drawing_tools.convert, "fn", drawing_tools.convert)
    result = fn(input_path=str(path), output_format=fmt, **kwargs)
    assert isinstance(result, ToolResult)
    json.loads(result.model_dump_json(), parse_constant=_strict)  # no NaN or Infinity, which MCP clients reject
    return result


def failed(result: ToolResult, family: str) -> ToolResult:
    assert result.ok is False, f"expected {family}, got ok"
    assert result.error.family == family, result.error
    assert result.warnings == [] and result.value is None and result.values == {}
    return result


def artifact(result: ToolResult, name: str) -> Path:
    return next(Path(a.path) for a in result.artifacts if Path(a.path).name == name)


def losses(result: ToolResult, code: str) -> list[dict]:
    return [i for i in result.data["losses"]["items"] if i["code"] == code]


def drawing(tmp_path, *entities, version=R.V2000, insunits=4, name="d.dxf", **kw):
    return R.write(tmp_path / name, R.document(version, insunits=insunits if version != R.V12 else None, entities=entities, **kw))


def pdf_of(result):
    return artifact(result, "converted.pdf").read_bytes()


def with_table(text: str, table: str) -> str:
    """Add a table (text of one TABLE ... ENDTAB) at the end of the TABLES section."""
    start = text.index("TABLES\n")
    end = text.index("0\nENDSEC\n", start)
    return text[:end] + table + text[end:]


def with_ltype(text: str, record: str) -> str:
    return text.replace("0\nENDTAB\n", record + "0\nENDTAB\n", 1)  # the first table is LTYPE


def point(stroke_point, i=0):
    return stroke_point[1] if len(stroke_point) == 2 else stroke_point[i]


# --------------------------------------------------------------------------- true scale in the PDF


def test_a_100_mm_line_at_1_to_1_is_283_465_points_in_the_pdf(tmp_path):
    r = convert(drawing(tmp_path, R.line((0, 0), (100, 0))), "pdf")
    assert r.ok, r.error
    pdf = pdf_of(r)
    assert CC.pdf_media_box(pdf) == (340.16, 56.69)  # 120 x 20 mm: the extents plus 10 mm on every side
    (stroke,) = CC.pdf_strokes(pdf)
    (m, l) = stroke["path"]
    assert m[1] == pytest.approx((28.346, 28.346), abs=0.001) and l[1] == pytest.approx((311.811, 28.346), abs=0.001)
    assert CC.stroke_length(stroke) == pytest.approx(100 / 25.4 * 72, abs=0.01) == pytest.approx(283.465, abs=0.01)
    assert stroke["width"] == pytest.approx(0.25 * PT, abs=0.001)  # the default line weight, 0.25 mm
    assert r.values["page_width"].quantity == 120.0 and r.values["page_height"].quantity == 20.0
    assert r.values["scale"].quantity == 1.0 and r.data["page"]["scale_text"] == "1:1"
    assert r.data["page"]["pt_per_drawing_unit"] == pytest.approx(2.834646, abs=1e-6)


def test_the_pdf_is_read_back_by_the_pdf_reader_at_the_same_size(tmp_path):
    from eng_toolkit.domains.drawing import pdf_read, reading

    r = convert(drawing(tmp_path, R.line((0, 0), (100, 0))), "pdf")
    full = pdf_read.read(str(artifact(r, "converted.pdf")), reading.Limits().as_dict())
    assert full["pages"][0]["width_mm"] == pytest.approx(120.0, abs=0.01)
    assert full["pages"][0]["height_mm"] == pytest.approx(20.0, abs=0.01)
    assert full["vector"]["straight_length_mm"] == pytest.approx(100.0, abs=0.01)


def test_an_inch_drawing_is_scaled_by_25_4(tmp_path):
    r = convert(drawing(tmp_path, R.line((0, 0), (10, 0)), insunits=1), "pdf")
    assert r.ok, r.error
    assert CC.pdf_length_pt(pdf_of(r)) == pytest.approx(720.0, abs=0.01)  # 10 inch = 254 mm = 720 pt
    assert r.data["units"]["name"] == "inch" and r.data["units"]["mm_per_unit"] == 25.4 and r.data["units"]["assumed"] is None
    assert r.values["mm_per_drawing_unit"].quantity == 25.4
    assert r.values["page_width"].quantity == pytest.approx(274.0)  # 254 + 20


@pytest.mark.parametrize(
    ("scale", "pt"),
    [("1:1", 283.4646), ("1:2", 141.7323), ("1:10", 28.3465), ("2:1", 566.9291), ("5:1", 1417.3228), ("1:100", 2.8346)],
)
def test_a_given_scale_shortens_or_lengthens_the_line_by_its_ratio(tmp_path, scale, pt):
    r = convert(drawing(tmp_path, R.line((0, 0), (100, 0))), "pdf", scale=scale)
    assert r.ok, r.error
    assert CC.pdf_length_pt(pdf_of(r)) == pytest.approx(pt, abs=0.01)
    ratio = int(scale.split(":")[0]) / int(scale.split(":")[1])
    assert r.values["scale"].quantity == pytest.approx(ratio)
    assert r.values["page_width"].quantity == pytest.approx(100 * ratio + 20)


def test_a_named_sheet_centres_the_drawing_and_keeps_true_size(tmp_path):
    r = convert(drawing(tmp_path, R.line((0, 0), (100, 0))), "pdf", sheet="A3")
    pdf = pdf_of(r)
    assert CC.pdf_media_box(pdf) == (1190.55, 841.89)  # 420 x 297 mm
    (stroke,) = CC.pdf_strokes(pdf)
    (_, a), (_, b) = stroke["path"]
    assert a == pytest.approx((160 * PT, 148.5 * PT), abs=0.001)  # (420 - 100) / 2 and 297 / 2 mm
    assert b == pytest.approx((260 * PT, 148.5 * PT), abs=0.001)
    assert (r.data["page"]["sheet"], r.data["page"]["orientation"], r.data["page"]["scale_text"]) == ("A3", "landscape", "1:1")


def test_a_drawing_larger_than_the_sheet_takes_the_largest_scale_that_fits(tmp_path):
    r = convert(drawing(tmp_path, R.line((0, 0), (500, 0)), R.line((0, 0), (0, 250))), "pdf", sheet="A3")
    assert r.data["page"]["scale_text"] == "1:2"  # 500 x 250 mm does not fit 400 x 277; 250 x 125 does
    assert CC.pdf_length_pt(pdf_of(r)) == pytest.approx((250 + 125) * PT, abs=0.01)


def test_the_extents_are_what_is_drawn_not_the_origin(tmp_path):
    r = convert(drawing(tmp_path, R.line((1000, 2000), (1100, 2000))), "pdf")  # far from the origin: the page is still 120 x 20
    assert CC.pdf_media_box(pdf_of(r)) == (340.16, 56.69)
    assert CC.pdf_length_pt(pdf_of(r)) == pytest.approx(283.465, abs=0.01)


# --------------------------------------------------------------------------- shapes


def test_a_circle_is_a_closed_curve_of_length_two_pi_r_in_the_svg_and_the_pdf(tmp_path):
    r = convert(drawing(tmp_path, R.circle((50, 50), 10)), "svg")
    svg = artifact(r, "converted.svg").read_text(encoding="utf-8")
    assert CC.svg_size_mm(svg) == (40.0, 40.0)  # 20 mm across plus 10 mm each side
    assert CC.svg_length_mm(svg) == pytest.approx(2 * math.pi * 10, rel=1e-3)  # 62.832
    xs = [p[0] for sub in CC.path_points(CC.svg_paths(svg)[0]["d"]) for p in sub]
    ys = [p[1] for sub in CC.path_points(CC.svg_paths(svg)[0]["d"]) for p in sub]
    assert (min(xs), max(xs), min(ys), max(ys)) == pytest.approx((10, 30, 10, 30), abs=0.01)  # the circle centred on the page
    p = convert(drawing(tmp_path, R.circle((50, 50), 10)), "pdf")
    assert CC.pdf_length_pt(pdf_of(p)) == pytest.approx(2 * math.pi * 10 * PT, rel=1e-3)


def test_an_arc_has_its_sweep_and_a_wrapping_arc_goes_the_long_way(tmp_path):
    quarter = convert(drawing(tmp_path, R.arc((0, 0), 30, 0, 90)), "svg")
    assert CC.svg_length_mm(artifact(quarter, "converted.svg").read_text(encoding="utf-8")) == pytest.approx(math.pi / 2 * 30, rel=1e-3)  # 47.124
    assert quarter.values["page_width"].quantity == pytest.approx(50.0, abs=0.01)  # 30 x 30 plus the margins
    wrap = convert(drawing(tmp_path, R.arc((0, 0), 30, 270, 180), name="w.dxf"), "svg")  # counter-clockwise from 270 to 180 degrees: 270 degrees
    assert CC.svg_length_mm(artifact(wrap, "converted.svg").read_text(encoding="utf-8")) == pytest.approx(1.5 * math.pi * 30, rel=1e-3)  # 141.372


def test_a_closed_polyline_with_a_bulge_has_the_length_of_its_segments_and_arcs(tmp_path):
    # a 40 x 20 rectangle whose right edge bulges into a half circle (bulge 1 over a 20 mm chord): 40 + 20 + 40 + pi * 10
    poly = R.lwpolyline([(0, 0), (40, 0), (40, 20), (0, 20)], closed=True, bulges=[0, 1, 0, 0])
    r = convert(drawing(tmp_path, poly), "svg")
    assert CC.svg_length_mm(artifact(r, "converted.svg").read_text(encoding="utf-8")) == pytest.approx(40 + 20 + 40 + math.pi * 10, rel=1e-3)


def test_an_insert_is_scaled_and_rotated_about_its_insertion_point(tmp_path):
    # a 10 mm line along the block's x axis, inserted at (50, 20) with x scale 2 and rotated 90 degrees: (50, 20) to (50, 40)
    text = R.document(R.V2000, insunits=4, blocks=(R.block("B", R.line((0, 0), (10, 0))),), entities=(R.insert("B", (50, 20), scale=(2, 1, 1), rotation=90),))
    r = convert(R.write(tmp_path / "i.dxf", text), "pdf")
    assert r.ok, r.error
    pdf = pdf_of(r)
    assert CC.pdf_media_box(pdf) == (56.69, 113.39)  # 20 x 40 mm: the line is 0 wide and 20 long, plus the margins
    (stroke,) = CC.pdf_strokes(pdf)
    (_, a), (_, b) = stroke["path"]
    assert a == pytest.approx((10 * PT, 10 * PT), abs=0.002) and b == pytest.approx((10 * PT, 30 * PT), abs=0.002)
    assert r.data["entities"]["drawn_by_type"] == {"INSERT": 1, "LINE": 1}


def test_an_insert_array_draws_every_copy(tmp_path):
    # 3 columns 10 apart and 2 rows 5 apart of a 4 mm line: six lines, 24 mm in all, extents 24 x 5 mm
    text = R.document(R.V2000, insunits=4, blocks=(R.block("B", R.line((0, 0), (4, 0))),), entities=(R.insert("B", (0, 0), columns=3, rows=2, col_spacing=10, row_spacing=5),))
    r = convert(R.write(tmp_path / "m.dxf", text), "pdf")
    pdf = pdf_of(r)
    assert len(CC.pdf_strokes(pdf)) == 6
    assert CC.pdf_length_pt(pdf) == pytest.approx(24 * PT, abs=0.01)
    assert r.values["page_width"].quantity == pytest.approx(44.0) and r.values["page_height"].quantity == pytest.approx(25.0)
    assert r.data["entities"]["drawn_by_type"] == {"INSERT": 1, "LINE": 6}


def test_layer_line_weights_and_linetypes_reach_the_stroke(tmp_path):
    heavy = R.tags(0, "LAYER", 5, "30", 100, "AcDbSymbolTableRecord", 100, "AcDbLayerTableRecord", 2, "HEAVY", 70, 0, 62, 7, 6, "DASHED", 370, 50)
    dashed = R.tags(0, "LTYPE", 5, "31", 100, "AcDbSymbolTableRecord", 100, "AcDbLinetypeTableRecord", 2, "DASHED", 70, 0, 3, "dashed", 72, 65, 73, 2, 40, 18.0, 49, 12.0, 74, 0, 49, -6.0, 74, 0)
    text = R.document(R.V2000, insunits=4, layers=(heavy,), entities=(R.line((0, 0), (100, 0), "HEAVY"),))
    r = convert(R.write(tmp_path / "w.dxf", with_ltype(text, dashed)), "pdf")
    assert r.ok, r.error
    (stroke,) = CC.pdf_strokes(pdf_of(r))
    assert stroke["width"] == pytest.approx(0.5 * PT, abs=0.001)  # lineweight 50 = 0.50 mm
    assert stroke["dash"] == pytest.approx([12 * PT, 6 * PT], abs=0.001)  # 12 mm on, 6 mm off at the linetype scale 1


def test_a_linetype_too_fine_for_the_scale_is_drawn_solid_and_listed(tmp_path):
    layer = R.tags(0, "LAYER", 5, "30", 100, "AcDbSymbolTableRecord", 100, "AcDbLayerTableRecord", 2, "FINE", 70, 0, 62, 7, 6, "TINY")
    tiny = R.tags(0, "LTYPE", 5, "31", 100, "AcDbSymbolTableRecord", 100, "AcDbLinetypeTableRecord", 2, "TINY", 70, 0, 3, "tiny", 72, 65, 73, 2, 40, 0.03, 49, 0.02, 74, 0, 49, -0.01, 74, 0)
    text = R.document(R.V2000, insunits=4, layers=(layer,), entities=(R.line((0, 0), (100, 0), "FINE"),))
    r = convert(R.write(tmp_path / "f.dxf", with_ltype(text, tiny)), "pdf")
    assert CC.pdf_strokes(pdf_of(r))[0]["dash"] == []
    (row,) = losses(r, "linetype_solid")
    assert "TINY" in row["message"] and row["count"] == 1


def test_svg_dash_and_width_follow_the_same_rules(tmp_path):
    heavy = R.tags(0, "LAYER", 5, "30", 100, "AcDbSymbolTableRecord", 100, "AcDbLayerTableRecord", 2, "HEAVY", 70, 0, 62, 7, 6, "DASHED", 370, 35)
    dashed = R.tags(0, "LTYPE", 5, "31", 100, "AcDbSymbolTableRecord", 100, "AcDbLinetypeTableRecord", 2, "DASHED", 70, 0, 3, "dashed", 72, 65, 73, 2, 40, 18.0, 49, 12.0, 74, 0, 49, -6.0, 74, 0)
    text = R.document(R.V2000, insunits=4, layers=(heavy,), entities=(R.line((0, 0), (100, 0), "HEAVY"),))
    r = convert(R.write(tmp_path / "w.dxf", with_ltype(text, dashed)), "svg")
    (path,) = CC.svg_paths(artifact(r, "converted.svg").read_text(encoding="utf-8"))
    assert path["stroke_width"] == 0.35 and path["dash"] == [12.0, 6.0]


def test_svg_page_is_the_sheet_in_millimetres(tmp_path):
    r = convert(drawing(tmp_path, R.line((0, 0), (100, 0))), "svg", sheet="A4")
    svg = artifact(r, "converted.svg").read_text(encoding="utf-8")
    assert CC.svg_size_mm(svg) == (297.0, 210.0)
    root = CC.svg_root(svg)
    assert root.get("width") == "297mm" and root.get("viewBox") == "0 0 297 210"
    assert CC.svg_length_mm(svg) == pytest.approx(100.0, abs=0.01)


def test_the_png_has_the_page_at_150_dpi_and_the_line_where_the_geometry_says(tmp_path):
    r = convert(drawing(tmp_path, R.line((0, 0), (100, 0))), "png")
    assert r.ok, r.error
    w, h, rows = CC.png_decode(artifact(r, "converted.png").read_bytes())
    per_mm = 150 / 25.4
    assert w == pytest.approx(120 * per_mm, abs=1.5) and h == pytest.approx(20 * per_mm, abs=1.5)  # 709 x 118
    dark = CC.dark_pixels(rows)
    assert dark, "no ink"
    xs, ys = [p[0] for p in dark], [p[1] for p in dark]
    assert min(xs) == pytest.approx(10 * per_mm, abs=2) and max(xs) == pytest.approx(110 * per_mm, abs=2)  # the line runs from 10 mm to 110 mm
    assert min(ys) == pytest.approx(h - 10 * per_mm, abs=3) and max(ys) == pytest.approx(h - 10 * per_mm, abs=3)  # 10 mm above the bottom edge
    assert r.data["png"]["dpi"] == pytest.approx(150.0, abs=0.01)


# --------------------------------------------------------------------------- units


def test_a_drawing_with_no_unit_is_taken_as_millimetres_and_says_so(tmp_path):
    for insunits in (0, None):
        path = R.write(tmp_path / f"u{insunits}.dxf", R.document(R.V2000, insunits=insunits, entities=(R.line((0, 0), (100, 0)),)))
        r = convert(path, "pdf")
        assert CC.pdf_length_pt(pdf_of(r)) == pytest.approx(283.465, abs=0.01)
        assert r.data["units"]["assumed"] == "millimeter" and r.data["units"]["mm_per_unit_used"] == 1.0
        assert any("taken as 1 mm" in w for w in r.warnings)


def test_an_r12_drawing_has_no_unit_either(tmp_path):
    r = convert(drawing(tmp_path, R.line((0, 0), (100, 0), version=R.V12), version=R.V12), "pdf")
    assert r.ok, r.error
    assert r.data["units"]["assumed"] == "millimeter" and any("taken as 1 mm" in w for w in r.warnings)
    assert r.data["dxf_release_in"] == "R12"


@pytest.mark.parametrize(("code", "mm"), [(4, 1.0), (5, 10.0), (6, 1000.0), (13, 0.001), (1, 25.4), (2, 304.8)])
def test_each_insunits_value_gives_its_millimetre_factor(tmp_path, code, mm):
    r = convert(drawing(tmp_path, R.line((0, 0), (1, 0)), insunits=code), "pdf")
    assert r.ok, r.error
    assert r.data["units"]["mm_per_unit"] == pytest.approx(mm)
    assert CC.pdf_length_pt(pdf_of(r)) == pytest.approx(mm * PT, abs=0.01)


# --------------------------------------------------------------------------- DXF to DXF


def read_dxf(result):
    return DR.read(artifact(result, "converted.dxf"))


def rich(version=R.V2018, **kw):
    return R.document(
        version,
        insunits=4,
        entities=(
            R.line((0, 0), (100, 0), "A", version),
            R.circle((50, 50), 10, "A", version),
            R.arc((0, 0), 30, 30, 120, "B", version),
            R.lwpolyline([(0, 0), (10, 0), (10, 10)], "B", closed=True),
            R.text("HELLO", (5, 5), 2.5, "A", version),
        ),
        layers=(R.layer_row("A", 1, version=version), R.layer_row("B", 3, version=version)),
        **kw,
    )


def test_a_dxf_written_to_r2000_holds_the_same_entities(tmp_path):
    r = convert(R.write(tmp_path / "rich.dxf", rich()), "dxf", dxf_version="R2000")
    assert r.ok, r.error
    d = read_dxf(r)
    assert d.header_value("$ACADVER", 1) == "AC1015" and d.header_value("$INSUNITS", 70) == "4"
    assert sorted(e.type for e in d.entities) == ["ARC", "CIRCLE", "LINE", "LWPOLYLINE", "TEXT"]
    (line,) = d.of("LINE")
    assert line.point(10, 20) == (0.0, 0.0) and line.point(11, 21) == (100.0, 0.0) and line.layer == "A"
    (circle,) = d.of("CIRCLE")
    assert circle.point(10, 20) == (50.0, 50.0) and circle.number(40) == 10.0
    (arc,) = d.of("ARC")
    assert (arc.number(40), arc.number(50), arc.number(51), arc.layer) == (30.0, 30.0, 120.0, "B")
    (poly,) = d.of("LWPOLYLINE")
    assert poly.vertices() == [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)] and int(poly.number(70)) & 1 == 1
    assert d.of("TEXT")[0].text == "HELLO"
    assert {"A", "B"} <= set(d.layers) and d.layers["A"].number(62) == 1.0 and d.layers["B"].number(62) == 3.0
    assert r.data["dxf"]["to"] == "AC1015" and r.data["dxf"]["from"] == "AC1032"
    assert r.data["losses"] == {"items": [], "lossless": True}
    assert r.values["entity_count_drawn"].quantity == 5 and r.values["entity_count_skipped"].quantity == 0


@pytest.mark.parametrize(("version", "code"), [("R2000", "AC1015"), ("R2004", "AC1018"), ("R2007", "AC1021"), ("R2010", "AC1024"), ("R2013", "AC1027"), ("R2018", "AC1032")])
def test_every_release_after_r12_is_written_and_read_back(tmp_path, version, code):
    r = convert(R.write(tmp_path / "rich.dxf", rich()), "dxf", dxf_version=version)
    assert r.ok, r.error
    d = read_dxf(r)
    assert d.header_value("$ACADVER", 1) == code
    assert sorted(e.type for e in d.entities) == ["ARC", "CIRCLE", "LINE", "LWPOLYLINE", "TEXT"]
    assert r.data["losses"]["lossless"] is True


def test_r12_loses_what_it_cannot_carry_and_the_loss_report_names_it(tmp_path):
    ellipse = R.entity("ELLIPSE", 8, "A", 10, 0, 20, 0, 30, 0, 11, 10, 21, 0, 31, 0, 40, 0.5, 41, 0, 42, 6.283185307179586, subclass="AcDbEllipse")
    heavy = R.tags(0, "LAYER", 5, "30", 100, "AcDbSymbolTableRecord", 100, "AcDbLayerTableRecord", 2, "A", 70, 0, 62, 7, 6, "CONTINUOUS", 370, 50)
    text = R.document(
        R.V2018,
        insunits=4,
        layers=(heavy,),
        entities=(R.line((0, 0), (100, 0), "A"), R.lwpolyline([(0, 0), (10, 0), (10, 10)], "A", closed=True), ellipse, R.mtext("two\\Plines", (0, 0), 2.5, "A")),
    )
    r = convert(R.write(tmp_path / "x.dxf", text), "dxf", dxf_version="R12")
    assert r.ok, r.error
    d = read_dxf(r)
    types = [e.type for e in d.entities]
    assert d.header_value("$ACADVER", 1) == "AC1009" and "$INSUNITS" not in d.header
    assert "LWPOLYLINE" not in types and "ELLIPSE" not in types and "MTEXT" not in types
    assert types.count("LINE") == 1 and types.count("POLYLINE") == 1 and types.count("VERTEX") == 3
    vertices = [e.point(10, 20) for e in d.entities if e.type == "VERTEX"]
    assert vertices == [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    polyline = next(e for e in d.entities if e.type == "POLYLINE")
    assert int(polyline.number(70)) & 1 == 1  # still closed
    # the loss report: counted by writing the file and reading it back, and the properties R12 has no place for
    assert losses(r, "entity_converted")[0]["count"] == 1 and losses(r, "entity_converted")[0]["type"] == "LWPOLYLINE"
    dropped = {i["type"]: i["count"] for i in losses(r, "entity_dropped")}
    assert dropped == {"ELLIPSE": 1, "MTEXT": 1}
    assert losses(r, "lineweight")[0]["count"] == 1 and "lineweight" in losses(r, "lineweight")[0]["message"].lower()
    assert losses(r, "units")[0]["count"] == 1
    assert r.data["entities_before"]["model"] == {"ELLIPSE": 1, "LINE": 1, "LWPOLYLINE": 1, "MTEXT": 1}
    assert r.data["entities_after"]["model"] == {"LINE": 1, "POLYLINE": 1}
    assert r.values["entity_count_skipped"].quantity == 2 and r.values["entity_count_drawn"].quantity == 2


def test_r12_text_outside_its_code_page_is_listed(tmp_path):
    text = R.document(R.V2018, insunits=4, entities=(R.text("caf\u00e9", (0, 0)), R.text("\u4e2d\u6587", (0, 5))))
    r = convert(R.write(tmp_path / "u.dxf", text), "dxf", dxf_version="R12")
    assert r.ok, r.error
    assert losses(r, "unicode")[0]["count"] == 1  # the Chinese text; "cafe" with an acute e is in code page 1252


def test_true_colour_and_transparency_are_listed_for_r2000(tmp_path):
    coloured = R.entity("LINE", 8, "0", 420, 16711680, 440, 33554432 + 128, 10, 0, 20, 0, 30, 0, 11, 5, 21, 0, 31, 0, subclass="AcDbLine")
    r = convert(R.write(tmp_path / "c.dxf", R.document(R.V2018, insunits=4, entities=(coloured,))), "dxf", dxf_version="R2000")
    assert r.ok, r.error
    assert losses(r, "true_colour")[0]["count"] == 1 and losses(r, "transparency")[0]["count"] == 1


def test_an_r12_drawing_can_be_written_to_a_newer_release(tmp_path):
    text = R.document(R.V12, layers=(R.layer_row("A", version=R.V12),), entities=(R.line((0, 0), (7, 0), "A", R.V12), R.text("TXT", (1, 1), 2.5, "A", R.V12)))
    r = convert(R.write(tmp_path / "old.dxf", text), "dxf", dxf_version="R2018")
    assert r.ok, r.error
    d = read_dxf(r)
    assert d.header_value("$ACADVER", 1) == "AC1032"
    assert sorted(e.type for e in d.entities) == ["LINE", "TEXT"] and d.of("LINE")[0].point(11, 21) == (7.0, 0.0)
    assert r.data["dxf"]["from"] == "AC1009" and r.data["losses"]["lossless"] is True


def test_sheet_and_scale_are_ignored_for_dxf_output_and_dxf_version_for_pdf(tmp_path):
    path = R.write(tmp_path / "rich.dxf", rich())
    r = convert(path, "dxf", sheet="A3", scale="1:2")
    assert r.ok and any("ignored" in w and "sheet" in w for w in r.warnings)
    p = convert(path, "pdf", dxf_version="R12")
    assert p.ok and any("dxf_version R12 is ignored" in w for w in p.warnings)


# --------------------------------------------------------------------------- the loss report of a picture


def test_text_is_outlines_and_the_report_says_the_pdf_has_no_text_layer(tmp_path):
    r = convert(drawing(tmp_path, R.text("ABC", (0, 0), 5), R.mtext("DEF", (0, 10), 5), R.line((0, 0), (50, 0))), "pdf")
    assert r.ok, r.error
    assert losses(r, "text_as_outlines")[0]["count"] == 2 and "no text layer" in losses(r, "text_as_outlines")[0]["message"]
    assert r.data["entities"]["drawn_by_type"] == {"LINE": 1, "MTEXT": 1, "TEXT": 1}
    from eng_toolkit.domains.drawing import pdf_read, reading

    full = pdf_read.read(str(artifact(r, "converted.pdf")), reading.Limits().as_dict())
    assert full["text"]["chars"] == 0  # nothing a PDF reader can extract


def test_an_shx_font_is_listed_as_replaced(tmp_path):
    style = R.tags(0, "STYLE", 5, "40", 100, "AcDbSymbolTableRecord", 100, "AcDbTextStyleTableRecord", 2, "SHXSTYLE", 70, 0, 40, 0, 41, 1, 50, 0, 71, 0, 42, 2.5, 3, "simplex.shx", 4, "")
    text = with_table(R.document(R.V2000, insunits=4, entities=(R.entity("TEXT", 8, "0", 10, 0, 20, 0, 30, 0, 40, 5, 1, "X", 7, "SHXSTYLE", subclass="AcDbText"),)), R.table("STYLE", style))
    r = convert(R.write(tmp_path / "s.dxf", text), "pdf")
    assert r.ok, r.error
    row = next(f for f in r.data["entities"]["fonts"] if f["style"] == "SHXSTYLE")
    assert row["font"] == "simplex.shx" and row["replaced"] is True and row["entities"] == 1
    assert losses(r, "fonts_replaced")[0]["styles"] == ["SHXSTYLE"]


def test_colours_are_dropped_and_counted(tmp_path):
    red = R.entity("LINE", 8, "0", 62, 1, 10, 0, 20, 0, 30, 0, 11, 5, 21, 0, 31, 0, subclass="AcDbLine")
    r = convert(drawing(tmp_path, red, R.line((0, 5), (5, 5))), "pdf")
    assert losses(r, "colour")[0]["count"] == 1 and "black" in losses(r, "colour")[0]["message"]


def test_entities_the_renderer_does_not_draw_are_skipped_and_listed_by_type(tmp_path):
    solid = R.entity("3DSOLID", 8, "0", 100, "AcDbModelerGeometry", 70, 1, 1, "ACIS DATA", subclass="")
    frozen = R.layer_row("FRZ", flags=1)
    hidden = R.line((0, 5), (5, 5), "FRZ")
    paper = R.entity("LINE", 8, "0", 67, 1, 10, 0, 20, 0, 30, 0, 11, 5, 21, 0, 31, 0, subclass="AcDbLine")
    text = R.document(R.V2000, insunits=4, layers=(frozen,), entities=(R.line((0, 0), (10, 0)), solid, hidden, paper))
    r = convert(R.write(tmp_path / "s.dxf", text), "pdf")
    assert r.ok, r.error
    skipped = r.data["entities"]["skipped_by_type"]
    assert skipped["3DSOLID"] == {"count": 1, "reasons": {"unsupported": 1}}
    assert skipped["LINE"] == {"count": 1, "reasons": {"invisible": 1}}  # the one on the frozen layer
    assert r.data["entities"]["drawn_by_type"] == {"LINE": 1}
    assert r.values["entity_count_drawn"].quantity == 1 and r.values["entity_count_skipped"].quantity == 2
    assert {i["type"] for i in losses(r, "entity_skipped")} == {"3DSOLID", "LINE"}
    assert losses(r, "paper_space")[0]["count"] == 1
    assert CC.pdf_length_pt(pdf_of(r)) == pytest.approx(10 * PT, abs=0.01)  # only the model-space line on a visible layer


def test_an_external_reference_is_listed_and_not_loaded(tmp_path):
    text = R.document(R.V2000, insunits=4, blocks=(R.block("XR", flags=4, xref_path="C:/secret/other.dwg"),), entities=(R.line((0, 0), (10, 0)),))
    r = convert(R.write(tmp_path / "x.dxf", text), "pdf")
    assert r.ok and losses(r, "xref_not_loaded")[0]["count"] == 1
    assert "secret" not in json.dumps(r.data)  # the path is not echoed and nothing is opened


# --------------------------------------------------------------------------- never overwritten, recorded, reproducible


def test_the_input_is_untouched_and_the_output_is_in_the_job_directory_with_a_hash(tmp_path):
    path = drawing(tmp_path, R.line((0, 0), (100, 0)))
    before = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    mtime = Path(path).stat().st_mtime_ns
    r = convert(path, "pdf")
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == before and Path(path).stat().st_mtime_ns == mtime
    out = next(a for a in r.artifacts if a.path.endswith("converted.pdf"))
    assert Path(out.path).parent == Path(r.data["job_dir"]) and out.format == "pdf"
    assert out.sha256 == hashlib.sha256(Path(out.path).read_bytes()).hexdigest()
    assert r.provenance.input_files[0].sha256 == before and r.provenance.input_files[0].bytes == Path(path).stat().st_size
    assert r.provenance.engine_versions.get("ezdxf")
    assert r.review_status == "unreviewed"
    assert Path(path).name not in {Path(a.path).name for a in r.artifacts}


@pytest.mark.parametrize("fmt", ["pdf", "svg", "png", "dxf"])
def test_the_same_drawing_gives_the_same_bytes_every_time(tmp_path, fmt):
    path = R.write(tmp_path / "rich.dxf", rich())
    a, b = convert(path, fmt), convert(path, fmt)
    name = f"converted.{fmt}"
    assert artifact(a, name).read_bytes() == artifact(b, name).read_bytes()


def test_a_dxf_that_is_cut_off_is_converted_as_far_as_it_goes(tmp_path):
    text = rich(R.V2000)
    cut = text[: text.index("AcDbCircle")]  # the LINE is complete, the CIRCLE after it is cut off in its fourth record
    r = convert(R.write(tmp_path / "cut.dxf", cut), "pdf")
    assert r.ok, r.error
    assert r.data["repair"] is not None and any("damaged" in w for w in r.warnings)
    assert r.values["entity_count_drawn"].quantity >= 1


# --------------------------------------------------------------------------- refusals


def test_a_dwg_is_refused_with_the_name_of_the_tool_it_needs(tmp_path):
    p = tmp_path / "a.dwg"
    p.write_bytes(b"AC1018\x00\x00\x00\x00\x00\x00\x00\x00")
    r = failed(convert(p, "pdf"), "engine.unavailable")
    assert "dwg2dxf" in r.error.message and "S9" in r.error.message
    assert r.error.details["tool"] == "dwg2dxf" and r.provenance.input_files == []  # nothing was opened


def test_a_dwg_that_is_named_dxf_is_recognised_by_its_content(tmp_path):
    p = tmp_path / "a.dxf"
    p.write_bytes(b"AC1018\x00\x00\x00\x00\x00\x00\x00\x00" + bytes(200))
    r = failed(convert(p, "pdf"), "engine.unavailable")
    assert "dwg2dxf" in r.error.message


def test_a_file_that_is_not_a_drawing_is_refused(tmp_path):
    empty = tmp_path / "empty.dxf"
    empty.write_bytes(b"")
    failed(convert(empty, "pdf"), "drawing.corrupt")
    garbage = tmp_path / "garbage.dxf"
    garbage.write_text("this is not a drawing\nat all\n", encoding="utf-8")
    failed(convert(garbage, "pdf"), "drawing.corrupt")
    binary = tmp_path / "binary.dxf"
    binary.write_bytes(bytes(range(256)) * 40)
    failed(convert(binary, "pdf"), "drawing.corrupt")
    pdf = tmp_path / "pdf.dxf"
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj\n")
    failed(convert(pdf, "pdf"), "input.invalid")


def test_a_drawing_with_nothing_in_it_is_refused(tmp_path):
    failed(convert(R.write(tmp_path / "e.dxf", R.document(R.V2000, insunits=4)), "pdf"), "drawing.empty")
    failed(convert(R.write(tmp_path / "e2.dxf", R.document(R.V2000, insunits=4)), "dxf"), "drawing.empty")
    frozen = R.document(R.V2000, insunits=4, layers=(R.layer_row("FRZ", flags=1),), entities=(R.line((0, 0), (5, 0), "FRZ"),))
    r = failed(convert(R.write(tmp_path / "f.dxf", frozen), "pdf"), "drawing.empty")
    assert "frozen" in r.error.message


def test_wrong_files_and_wrong_arguments_have_their_own_errors(tmp_path):
    good = drawing(tmp_path, R.line((0, 0), (100, 0)))
    failed(convert(tmp_path / "missing.dxf", "pdf"), "input.file_not_found")
    other = tmp_path / "a.txt"
    other.write_text("x", encoding="utf-8")
    failed(convert(other, "pdf"), "input.unsupported_format")
    for fmt in ("docx", "", "pdf,svg", "dwg"):
        assert failed(convert(good, fmt), "input.invalid").error.details["param"] == "output_format"
    assert failed(convert(good, "pdf", scale="1:3"), "input.invalid").error.details["param"] == "scale"
    assert failed(convert(good, "pdf", sheet="A5"), "input.invalid").error.details["param"] == "sheet"
    assert failed(convert(good, "dxf", dxf_version="R14"), "input.invalid").error.details["param"] == "dxf_version"


def test_a_sheet_and_scale_that_do_not_fit_are_refused_and_a_page_over_the_limit_too(tmp_path):
    wide = drawing(tmp_path, R.line((0, 0), (1000, 0)))
    r = failed(convert(wide, "pdf", sheet="A4", scale="1:1"), "drawing.layout_failed")
    assert "1000" in r.error.message
    assert convert(wide, "pdf", sheet="A4").data["page"]["scale_text"] == "1:5"
    long = R.write(tmp_path / "long.dxf", R.document(R.V2000, insunits=4, entities=(R.line((0, 0), (10000, 0)),)))
    r = failed(convert(long, "pdf", scale="1:1"), "input.out_of_range")
    assert "5080" in r.error.message and "smaller scale" in r.error.message
    assert convert(long, "pdf").data["page"]["scale_text"] == "1:10"  # auto reduces until the page passes A0 (10000 / 10 = 1000 mm)


def test_the_read_directory_allow_list_and_the_size_cap_apply(tmp_path, monkeypatch):
    path = drawing(tmp_path, R.line((0, 0), (100, 0)))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(elsewhere))
    assert failed(convert(path, "pdf"), "input.path_not_allowed").provenance.input_files == []
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(tmp_path))
    assert convert(path, "pdf").ok
    monkeypatch.setenv("ENG_TOOLKIT_CONVERT_MAX_MB", "0.0002")  # 200 bytes; the file is a few hundred
    r = failed(convert(path, "pdf"), "drawing.too_large")
    assert r.provenance.input_files == []


# --------------------------------------------------------------------------- the tool's surface


def test_the_arguments_are_strings_only_and_the_description_states_the_contract():
    fn = getattr(drawing_tools.convert, "fn", drawing_tools.convert)
    hints = typing.get_type_hints(fn, include_extras=True)
    hints.pop("return", None)
    assert list(hints) == ["input_path", "output_format", "sheet", "scale", "dxf_version"]
    assert all(typing.get_origin(v) is typing.Annotated and typing.get_args(v)[0] is str for v in hints.values())
    text = drawing_tools.CONVERT_DESCRIPTION
    for needle in ("283.465", "dwg2dxf", "S9", "data.losses", "true scale", "ISO 5455", "ISO 5457", "R2018", "untrusted"):
        assert needle in text, needle
    assert "make_views" in drawing_tools.TOOL_NAMES and drawing_tools.TOOL_NAMES == ("make_views", "read", "convert")


# --------------------------------------------------------------------------- the toolkit's own drawings


def test_a_drawing_made_by_make_views_keeps_the_ink_of_its_own_pdf(tmp_path):
    from drawing_fixtures import projection

    from eng_toolkit.domains.drawing import dxf as dxf_module
    from eng_toolkit.domains.drawing import sheet as S

    made = S.compose([projection(v) for v in ("front", "top", "right")], method="third_angle", scale=(1, 1))
    path = tmp_path / "made.dxf"
    path.write_bytes(dxf_module.write_dxf(made))
    own = dxf_module.render_pdf(made)
    r = convert(path, "pdf", sheet="A4", scale="1:1")
    assert r.ok, r.error
    pdf = pdf_of(r)
    assert CC.pdf_media_box(pdf) == CC.pdf_media_box(own) == (841.89, 595.28)  # A4 landscape, 297 x 210 mm
    assert CC.pdf_length_pt(pdf) == pytest.approx(CC.pdf_length_pt(own), rel=2e-3)  # the same lines at the same size
    assert len(CC.pdf_strokes(pdf)) == pytest.approx(len(CC.pdf_strokes(own)), abs=2)
    assert r.data["page"]["scale_text"] == "1:1" and r.data["units"]["name"] == "millimeter"
    assert losses(r, "text_as_outlines") and not losses(r, "entity_skipped")


def test_a_layer_colour_with_nothing_drawn_in_it_is_not_a_loss(tmp_path):
    text = R.document(R.V2000, insunits=4, layers=(R.layer_row("RED", 1),), entities=(R.line((0, 0), (10, 0)),))
    r = convert(R.write(tmp_path / "c.dxf", text), "pdf")
    assert r.ok and not losses(r, "colour")
