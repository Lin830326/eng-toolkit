"""The DXF reader on hand-written DXF text (tests/raw_dxf.py), with every expected number worked out from the coordinates written there.

No file in this module was written by ezdxf: the reader's engine cannot agree with itself about a mistake.
"""

import math
import time

import pytest
import raw_dxf as R

from eng_toolkit.errors import ToolFailure

pytest.importorskip("ezdxf", reason="ezdxf is not installed; run: uv sync --extra drawing")
from eng_toolkit.domains.drawing import dxf_read  # noqa: E402
from eng_toolkit.domains.drawing import reading  # noqa: E402


def read(tmp_path, text, *, encoding="utf-8", name="t.dxf", limits=None):
    path = R.write(tmp_path / name, text, encoding)
    return dxf_read.read(path, limits or reading.Limits().as_dict())


def simple(version=R.V2000, **kw):
    return R.document(version, entities=(R.line((0, 0), (10, 0), "A", version),), **kw)


def by_name(items, name):
    return next(i for i in items if i["name"] == name)


# --------------------------------------------------------------------------- version, encoding, units


@pytest.mark.parametrize(
    ("code", "release"),
    [(R.V12, "R12"), (R.V2000, "R2000"), (R.V2004, "R2004"), (R.V2007, "R2007"), (R.V2018, "R2018")],
)
def test_the_file_version_is_reported_as_the_file_says_it(tmp_path, code, release):
    r = read(tmp_path, simple(code, insunits=4 if code != R.V12 else None))
    assert r["dxf"]["version_code"] == code and r["dxf"]["release"] == release
    assert r["entities"]["total"] == 1


def test_an_inch_drawing_says_inch_and_gives_the_millimetre_values(tmp_path):
    text = R.document(R.V2000, insunits=1, entities=(R.line((0, 0), (10, 0), "A"), R.line((0, 0), (0, 4), "A")))
    r = read(tmp_path, text)
    assert r["units"]["name"] == "inch" and r["units"]["mm_per_unit"] == 25.4 and r["units"]["source"] == "$INSUNITS"
    ext = r["extents"]["model"]
    assert ext["size"] == pytest.approx([10.0, 4.0])
    assert ext["size_mm"] == pytest.approx([254.0, 101.6])
    layer = by_name(r["layers"], "A")
    assert layer["length"] == pytest.approx(14.0) and layer["length_mm"] == pytest.approx(355.6)


@pytest.mark.parametrize(
    ("code", "name", "mm"),
    [(4, "millimeter", 1.0), (5, "centimeter", 10.0), (6, "meter", 1000.0), (2, "foot", 304.8), (13, "micrometer", 0.001), (14, "decimeter", 100.0), (10, "yard", 914.4)],
)
def test_every_insunits_value_has_its_name_and_factor(tmp_path, code, name, mm):
    r = read(tmp_path, simple(insunits=code))
    assert (r["units"]["name"], r["units"]["mm_per_unit"]) == (name, pytest.approx(mm))


def test_no_units_in_the_file_means_no_millimetre_values_and_a_warning(tmp_path):
    r = read(tmp_path, simple(R.V12))  # R12 has no $INSUNITS
    u = r["units"]
    assert u["insunits"] is None and u["mm_per_unit"] is None and u["name"] == "unitless"
    assert r["extents"]["model"]["size_mm"] is None
    assert by_name(r["layers"], "A")["length_mm"] is None
    assert any("unit" in w.lower() and "not assum" in w.lower() for w in r["warnings"])


def test_a_header_that_does_not_mention_units_is_unitless_whatever_the_library_would_default_to(tmp_path):
    # ezdxf fills a missing $INSUNITS with a default (meters for a new document); the reader must report what the FILE says
    for version in (R.V2000, R.V2018):
        r = read(tmp_path, R.document(version, entities=(R.line((0, 0), (10, 0), "A", version),)), name=f"{version}.dxf")
        assert r["units"]["insunits"] is None and r["units"]["mm_per_unit"] is None and r["units"]["name"] == "unitless", version
        assert r["extents"]["model"]["size_mm"] is None


def test_a_file_with_no_header_section_at_all_is_unitless(tmp_path):
    text = R.section("ENTITIES", R.line((0, 0), (10, 0), "A", R.V12)) + R.tags(0, "EOF")
    r = read(tmp_path, text)
    assert r["units"]["insunits"] is None and r["units"]["mm_per_unit"] is None
    assert any("Not assuming millimetres or inches" in w for w in r["warnings"])


def test_a_header_dimlfac_that_the_file_does_not_contain_is_not_reported_as_the_files(tmp_path):
    text = R.section("ENTITIES", R.dimension(0, style="NOSUCH", pts=LINEAR_100, rotation=0, version=R.V12)) + R.tags(0, "EOF")
    d = read(tmp_path, text)["dimensions"][0]
    assert d["dimlfac"] == 1.0 and d["dimlfac_source"] == "default"


def test_insunits_zero_is_unitless_and_the_measurement_variable_is_only_a_hint(tmp_path):
    r = read(tmp_path, simple(insunits=0, measurement=1))
    assert r["units"]["mm_per_unit"] is None and r["units"]["measurement_hint"] == "metric"
    r = read(tmp_path, simple(insunits=0, measurement=0), name="b.dxf")
    assert r["units"]["measurement_hint"] == "english" and r["units"]["mm_per_unit"] is None


def test_an_unknown_insunits_code_is_not_a_unit(tmp_path):
    r = read(tmp_path, simple(insunits=99))
    assert r["units"]["mm_per_unit"] is None and any("99" in w for w in r["warnings"])


# --------------------------------------------------------------------------- text, CJK and old encodings


def texts_of(r):
    return [t["text"] for t in r["texts"]]


def test_utf8_cjk_text_and_layer_names_survive_in_r2018(tmp_path):
    text = R.document(R.V2018, insunits=4, layers=(R.layer_row("標題", version=R.V2018),), entities=(R.text("法蘭 FL-001 零件", (1, 2), 3.5, "標題", R.V2018),))
    r = read(tmp_path, text)
    assert "法蘭 FL-001 零件" in texts_of(r)
    assert by_name(r["layers"], "標題")["entities"] == 1
    assert r["dxf"]["encoding"] == "utf-8"


@pytest.mark.parametrize(
    ("codepage", "codec", "word"),
    [("ANSI_950", "cp950", "法蘭"), ("ANSI_936", "gbk", "法兰"), ("ANSI_932", "cp932", "フランジ"), ("ANSI_949", "cp949", "플랜지")],
)
def test_old_files_in_a_double_byte_code_page_are_decoded_by_their_dwgcodepage(tmp_path, codepage, codec, word):
    text = R.document(R.V2000, insunits=4, codepage=codepage, entities=(R.text(word, (0, 0), 2.5, "0"),))
    r = read(tmp_path, text, encoding=codec)
    assert word in texts_of(r)
    assert r["dxf"]["encoding"] == codec and codepage in r["dxf"]["encoding_source"]


def test_r12_unicode_escapes_become_characters(tmp_path):
    r = read(tmp_path, R.document(R.V12, entities=(R.text(r"\U+6CD5\U+862D A", (0, 0), 2.5, "0", R.V12),)))
    assert "法蘭 A" in texts_of(r)


def test_percent_codes_become_symbols(tmp_path):
    r = read(tmp_path, R.document(R.V2000, entities=(R.text("%%c10 %%p0.1 90%%d", (0, 0), 2.5, "0"),)))
    assert "Ø10 ±0.1 90°" in texts_of(r)


def test_mtext_formatting_is_removed(tmp_path):
    r = read(tmp_path, R.document(R.V2000, entities=(R.mtext(r"{\fArial|b1;Bold}\Pline2", (0, 0)),)))
    assert "Bold line2" in texts_of(r)
    assert next(t for t in r["texts"] if t["text"] == "Bold line2")["kind"] == "MTEXT"


# --------------------------------------------------------------------------- layers, entities, lengths


def test_entity_counts_and_lengths_per_layer_are_worked_out_from_the_coordinates(tmp_path):
    ents = (
        R.line((0, 0), (10, 0), "A"),  # 10
        R.arc((0, 0), 5, 0, 90, "A"),  # 5 * pi / 2
        R.circle((20, 20), 2, "A"),  # 4 pi
        R.lwpolyline([(0, 0), (4, 0), (4, 4), (0, 4)], "A", closed=True),  # 16
        R.lwpolyline([(0, 0), (2, 0)], "A", bulges=[1.0, 0]),  # a semicircle of diameter 2: pi
        R.text("X", (0, 0), 2.5, "B"),
    )
    r = read(tmp_path, R.document(R.V2000, insunits=4, layers=(R.layer_row("A"), R.layer_row("B")), entities=ents))
    a = by_name(r["layers"], "A")
    assert a["entities"] == 5
    assert a["length"] == pytest.approx(10 + 5 * math.pi / 2 + 4 * math.pi + 16 + math.pi)
    assert by_name(r["layers"], "B")["entities"] == 1 and by_name(r["layers"], "B")["length"] == 0.0
    assert r["entities"]["total"] == 6
    assert r["entities"]["by_type"] == {"LINE": 1, "ARC": 1, "CIRCLE": 1, "LWPOLYLINE": 2, "TEXT": 1}


def test_layer_flags_color_and_linetype_are_read_and_undeclared_layers_are_marked(tmp_path):
    rows = (R.layer_row("OUT", 1, "CONTINUOUS"), R.layer_row("OLD", 3, "CONTINUOUS", flags=1 | 4), R.layer_row("HID", -5, "CONTINUOUS"))
    ents = (R.line((0, 0), (1, 0), "OUT"), R.line((0, 0), (1, 0), "GHOST"))
    r = read(tmp_path, R.document(R.V2000, layers=rows, entities=ents))
    out, old, hid = by_name(r["layers"], "OUT"), by_name(r["layers"], "OLD"), by_name(r["layers"], "HID")
    assert (out["color"], out["linetype"], out["on"], out["frozen"], out["locked"], out["declared"]) == (1, "CONTINUOUS", True, False, False, True)
    assert old["frozen"] is True and old["locked"] is True
    assert hid["on"] is False
    ghost = by_name(r["layers"], "GHOST")
    assert ghost["declared"] is False and ghost["entities"] == 1


def test_extents_are_those_of_the_geometry_and_text_is_a_point(tmp_path):
    ents = (R.line((-5, 2), (10, 2)), R.circle((0, 0), 3), R.text("far", (100, 50)))
    r = read(tmp_path, R.document(R.V2000, insunits=4, entities=ents))
    e = r["extents"]["model"]
    assert e["min"] == pytest.approx([-5.0, -3.0]) and e["max"] == pytest.approx([100.0, 50.0])


def test_paper_space_entities_are_counted_apart_from_model_space(tmp_path):
    paper = R.line((0, 0), (5, 0), "P").replace("0\nLINE\n", "0\nLINE\n", 1)
    paper = paper.replace("100\nAcDbEntity\n", "100\nAcDbEntity\n67\n1\n", 1)
    r = read(tmp_path, R.document(R.V2000, insunits=4, entities=(R.line((0, 0), (10, 0), "M"), paper)))
    assert r["entities"]["model"] == 1 and r["entities"]["paper"] == 1
    assert {layout["space"] for layout in r["layouts"]} >= {"model", "paper"}
    assert r["extents"]["paper"]["size"] == pytest.approx([5.0, 0.0])


# --------------------------------------------------------------------------- dimensions


def style_rows(**values):
    return (R.dimstyle_row("STANDARD", **values),)


LINEAR_100 = {10: (50, 10), 13: (0, 0), 14: (100, 0)}


def dims_of(r):
    return r["dimensions"]


def test_dimlfac_two_makes_a_hundred_millimetre_measurement_read_as_two_hundred(tmp_path):
    ents = (R.dimension(0, pts=LINEAR_100, rotation=0),)
    r = read(tmp_path, R.document(R.V2000, insunits=4, dimstyles=style_rows(**{"144": 2.0, "271": 2, "78": 8}), entities=ents))
    d = dims_of(r)[0]
    assert d["kind"] == "linear" and d["measured"] == pytest.approx(100.0) and d["dimlfac"] == 2.0
    assert d["dimlfac_source"] == "dimension style"
    assert d["value"] == pytest.approx(200.0) and d["value_mm"] == pytest.approx(200.0)
    assert d["text"] == "200" and d["text_source"] == "computed"


def test_the_entitys_own_dstyle_override_beats_the_dimension_style(tmp_path):
    ents = (R.dimension(0, pts=LINEAR_100, rotation=0, xdata_dstyle={144: 0.5}),)
    r = read(tmp_path, R.document(R.V2000, insunits=4, dimstyles=style_rows(**{"144": 2.0, "271": 2, "78": 8}), entities=ents))
    d = dims_of(r)[0]
    assert d["dimlfac"] == 0.5 and d["dimlfac_source"] == "entity DSTYLE override"
    assert d["value"] == pytest.approx(50.0) and d["text"] == "50"


def test_a_header_dimlfac_applies_when_the_dimension_style_does_not_exist(tmp_path):
    ents = (R.dimension(0, style="NOSUCH", pts=LINEAR_100, rotation=0),)
    r = read(tmp_path, R.document(R.V2000, insunits=4, header_extra=R.tags(9, "$DIMLFAC", 40, 2.0), entities=ents))
    d = dims_of(r)[0]
    assert d["dimlfac"] == 2.0 and "header" in d["dimlfac_source"] and d["value"] == pytest.approx(200.0)


def test_a_dimension_without_any_dimlfac_is_scaled_by_one(tmp_path):
    r = read(tmp_path, R.document(R.V2000, insunits=4, entities=(R.dimension(0, style="NOSUCH", pts=LINEAR_100, rotation=0),)))
    d = dims_of(r)[0]
    assert d["dimlfac"] == 1.0 and d["dimlfac_source"] == "default" and d["value"] == pytest.approx(100.0)


def test_a_text_override_replaces_the_value_at_the_placeholder(tmp_path):
    ents = (R.dimension(0, pts=LINEAR_100, rotation=0, override="<>H7"),)
    r = read(tmp_path, R.document(R.V2000, insunits=4, dimstyles=style_rows(**{"144": 2.0, "271": 2, "78": 8}), entities=ents))
    d = dims_of(r)[0]
    assert d["text"] == "200H7" and d["text_source"] == "override" and d["text_override"] == "<>H7"
    assert d["value"] == pytest.approx(200.0)


def test_a_plain_override_is_the_text_and_a_single_space_hides_it(tmp_path):
    ents = (R.dimension(0, pts=LINEAR_100, rotation=0, override="TYP"), R.dimension(0, pts=LINEAR_100, rotation=0, override=" "))
    d1, d2 = dims_of(read(tmp_path, R.document(R.V2000, insunits=4, entities=ents)))
    assert d1["text"] == "TYP" and d2["text"] == "" and d2["hidden"] is True


def test_diameter_radius_aligned_and_ordinate_dimensions(tmp_path):
    ents = (
        R.dimension(3, pts={10: (10, 0), 15: (-10, 0), 11: (0, 0)}),
        R.dimension(4, pts={10: (0, 0), 15: (7, 0)}),
        R.dimension(1, pts={10: (0, 5), 13: (0, 0), 14: (3, 4)}),
        R.dimension(64 + 6, pts={10: (5, 5), 13: (25, 45), 14: (25, 60)}),
        R.dimension(6, pts={10: (5, 5), 13: (25, 45), 14: (30, 45)}),
    )
    r = read(tmp_path, R.document(R.V2000, insunits=4, dimstyles=style_rows(**{"271": 2, "78": 8}), entities=ents))
    got = [(d["kind"], d["measured"], d["text"]) for d in dims_of(r)]
    assert got == [("diameter", pytest.approx(20.0), "Ø20"), ("radius", pytest.approx(7.0), "R7"), ("aligned", pytest.approx(5.0), "5"), ("ordinate", pytest.approx(20.0), "20"), ("ordinate", pytest.approx(40.0), "40")]


def test_an_angular_dimension_is_in_degrees_and_dimlfac_does_not_scale_it(tmp_path):
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    pts = {13: (0, 0), 14: (10, 0), 15: (0, 0), 10: (10 * c, 10 * s), 16: (6 * math.cos(math.radians(15)), 6 * math.sin(math.radians(15)))}
    r = read(tmp_path, R.document(R.V2000, insunits=4, dimstyles=style_rows(**{"144": 2.0, "179": 0}), entities=(R.dimension(2, pts=pts),)))
    d = dims_of(r)[0]
    assert d["kind"] == "angular" and d["measured_unit"] == "angle" and d["measured"] == pytest.approx(30.0)
    assert d["value"] == pytest.approx(30.0) and d["text"] == "30°" and d["value_mm"] is None


def test_the_text_a_viewer_draws_is_the_dimension_blocks_text_and_a_disagreement_is_a_warning(tmp_path):
    shown = R.block("*D1", R.mtext("200", (50, 12)), version=R.V2000, flags=1)
    stale = R.block("*D2", R.mtext("100", (50, 12)), version=R.V2000, flags=1)
    ents = (R.dimension(0, pts=LINEAR_100, rotation=0, block="*D1"), R.dimension(0, pts=LINEAR_100, rotation=0, block="*D2"))
    r = read(tmp_path, R.document(R.V2000, insunits=4, dimstyles=style_rows(**{"144": 2.0, "271": 2, "78": 8}), blocks=(shown, stale), entities=ents))
    d1, d2 = dims_of(r)
    assert d1["text"] == "200" and d1["text_source"] == "block" and d1["block_text"] == "200" and d1["computed_text"] == "200"
    assert d2["text"] == "100" and d2["block_text"] == "100" and d2["computed_text"] == "200"
    assert sum("differs" in w for w in r["warnings"]) == 1


def test_a_negative_dimlfac_in_model_space_is_not_applied_and_says_so(tmp_path):
    r = read(tmp_path, R.document(R.V2000, insunits=4, dimstyles=style_rows(**{"144": -2.0}), entities=(R.dimension(0, pts=LINEAR_100, rotation=0),)))
    d = dims_of(r)[0]
    assert d["dimlfac"] == 1.0 and d["value"] == pytest.approx(100.0)
    assert any("negative" in w for w in r["warnings"])


def test_an_inch_dimension_reports_inches_and_millimetres(tmp_path):
    pts = {10: (2, 1), 13: (0, 0), 14: (4, 0)}
    r = read(tmp_path, R.document(R.V2000, insunits=1, dimstyles=style_rows(**{"271": 3, "78": 8}), entities=(R.dimension(0, pts=pts, rotation=0),)))
    d = dims_of(r)[0]
    assert d["measured"] == pytest.approx(4.0) and d["value"] == pytest.approx(4.0) and d["value_mm"] == pytest.approx(101.6)
    assert d["text"] == "4"


def test_dimlfac_is_applied_to_the_millimetre_value_of_a_dimension_in_an_inch_drawing(tmp_path):
    pts = {10: (2, 1), 13: (0, 0), 14: (4, 0)}
    r = read(tmp_path, R.document(R.V2000, insunits=1, dimstyles=style_rows(**{"144": 2.0}), entities=(R.dimension(0, pts=pts, rotation=0),)))
    d = dims_of(r)[0]
    assert d["value"] == pytest.approx(8.0) and d["value_mm"] == pytest.approx(203.2)


def test_dimensions_in_r12_are_read_too(tmp_path):
    ents = (R.dimension(0, pts=LINEAR_100, rotation=0, version=R.V12),)
    r = read(tmp_path, R.document(R.V12, header_extra=R.tags(9, "$DIMLFAC", 40, 2.0), entities=ents))
    d = dims_of(r)[0]
    assert d["value"] == pytest.approx(200.0)
    assert d["value_mm"] is None  # R12 has no units


# --------------------------------------------------------------------------- blocks, attributes, title block


def title_attribs():
    return [("TITLE", "FLANGE", (1, 2)), ("DWG_NO", "FL-001", (1, 4)), ("REV", "B", (1, 6)), ("MATERIAL", "SS304", (1, 8)), ("SCALE", "1:2", (1, 10))]


def test_an_attribute_title_block_is_read_as_exact_fields(tmp_path):
    defs = R.block("TITLEBLOCK", *[R.attdef(tag, "", pos) for tag, _, pos in title_attribs()])
    r = read(tmp_path, R.document(R.V2000, insunits=4, blocks=(defs,), entities=(R.insert("TITLEBLOCK", (200, 10), attribs=title_attribs()),)))
    tb = r["title_block"]
    assert tb["method"] == "attributes" and tb["block"] == "TITLEBLOCK"
    got = {k: (v["value"], v["inferred"], v["confidence"]) for k, v in tb["fields"].items()}
    assert got == {"title": ("FLANGE", False, 1.0), "part_number": ("FL-001", False, 1.0), "revision": ("B", False, 1.0), "material": ("SS304", False, 1.0), "scale": ("1:2", False, 1.0)}
    listed = r["blocks"]["with_attributes"][0]
    assert listed["block"] == "TITLEBLOCK" and listed["attribs"]["DWG_NO"] == "FL-001"
    assert "FLANGE" in texts_of(r)  # attribute values are texts of the drawing too
    assert next(t for t in r["texts"] if t["text"] == "FLANGE")["kind"] == "ATTRIB"


def test_a_label_and_value_title_block_is_read_as_inferred_fields(tmp_path):
    ents = (
        R.text("TITLE", (101.5, 47.4), 1.8, "TITLE"),
        R.text("BRACKET", (101.5, 37.5), 5.0, "TITLE"),
        R.text("PART NO.", (221.5, 47.4), 1.8, "TITLE"),
        R.text("BR-01", (221.5, 37.5), 3.5, "TITLE"),
        R.text("SCALE", (181.5, 33.4), 1.8, "TITLE"),
        R.text("1:2", (181.5, 27.5), 3.5, "TITLE"),
        R.text("UNREVIEWED - generated by eng-toolkit, not engineering approved", (101.5, 6.0), 3.0, "TITLE"),
    )
    r = read(tmp_path, R.document(R.V2000, insunits=4, entities=ents))
    tb = r["title_block"]
    assert tb["method"] == "label_pairs"
    assert {k: v["value"] for k, v in tb["fields"].items()} == {"title": "BRACKET", "part_number": "BR-01", "scale": "1:2"}
    assert all(v["inferred"] and v["confidence"] < 1.0 for v in tb["fields"].values())
    assert tb["status"].startswith("UNREVIEWED")


def test_chinese_attribute_tags_and_labels(tmp_path):
    defs = R.block("圖框", R.attdef("圖號", "", (0, 0)), R.attdef("品名", "", (0, 2)), version=R.V2018)
    ents = (R.insert("圖框", (0, 0), attribs=[("圖號", "A-100", (1, 1)), ("品名", "法蘭", (1, 3))], version=R.V2018),)
    r = read(tmp_path, R.document(R.V2018, insunits=4, blocks=(defs,), entities=ents))
    f = r["title_block"]["fields"]
    assert (f["part_number"]["value"], f["title"]["value"]) == ("A-100", "法蘭")


def test_no_title_block_is_said_plainly(tmp_path):
    r = read(tmp_path, simple())
    assert r["title_block"]["method"] == "none" and r["title_block"]["fields"] == {}


def test_inserts_blocks_and_their_nesting_are_counted(tmp_path):
    blocks = (R.block("INNER", R.line((0, 0), (1, 0))), R.block("OUTER", R.insert("INNER", (0, 0)), R.insert("INNER", (5, 0))))
    r = read(tmp_path, R.document(R.V2000, blocks=blocks, entities=(R.insert("OUTER", (0, 0)),)))
    assert r["blocks"]["inserts"] == 1 and r["blocks"]["definitions"] >= 2 and r["blocks"]["max_nesting"] == 2


# --------------------------------------------------------------------------- views


def rect(x0, y0, x1, y1, layer="OUTLINE"):
    return [R.line((x0, y0), (x1, y0), layer), R.line((x1, y0), (x1, y1), layer), R.line((x1, y1), (x0, y1), layer), R.line((x0, y1), (x0, y0), layer)]


def test_separated_groups_of_geometry_are_counted_as_views_and_the_sheet_furniture_is_not(tmp_path):
    ents = tuple(rect(20, 100, 120, 120) + rect(20, 30, 120, 80) + rect(150, 30, 170, 80) + rect(10, 10, 287, 200, "FRAME") + [R.line((20, 90), (120, 90), "DIM"), R.text("N", (0, 0), 2, "TEXT")])
    r = read(tmp_path, R.document(R.V2000, insunits=4, entities=ents))
    v = r["views"]
    assert v["count"] == 3 and v["inferred"] is True and 0 < v["confidence"] < 1
    boxes = sorted(item["bbox"] for item in v["items"])
    assert boxes[0] == pytest.approx([20, 30, 120, 80]) or boxes[0] == pytest.approx([20, 100, 120, 120])


# --------------------------------------------------------------------------- hostile and damaged files


def valid_text():
    ents = tuple(R.line((i, 0), (i, 5), "A") for i in range(50))
    return R.document(R.V2000, insunits=4, layers=(R.layer_row("A"),), entities=ents)


def test_a_truncated_file_is_recovered_with_a_warning_or_refused_with_a_structured_error(tmp_path):
    text = valid_text()
    path = R.write(tmp_path / "cut.dxf", text[: len(text) // 2])
    try:
        r = dxf_read.read(path, reading.Limits().as_dict())
    except ToolFailure as exc:
        assert exc.family == "drawing.corrupt"
    else:
        assert r["dxf"]["recovered"] is True
        assert 0 < r["entities"]["total"] < 50
        assert any("damaged" in w or "recover" in w for w in r["warnings"])


@pytest.mark.parametrize("payload", [b"", b"\x00\x01\x02garbage" * 50, b"not a dxf file at all\njust text\n", "0\nSECTION\n2\nENTITIES\n0\nLINE\n8\n".encode()])
def test_garbage_is_a_structured_error_not_a_traceback(tmp_path, payload):
    path = tmp_path / "bad.dxf"
    path.write_bytes(payload)
    with pytest.raises(ToolFailure) as caught:
        dxf_read.read(str(path), reading.Limits().as_dict())
    assert caught.value.family == "drawing.corrupt"


def test_a_dwg_is_refused_with_the_converter_hint(tmp_path):
    path = tmp_path / "x.dxf"
    path.write_bytes(b"AC1018\x00\x00\x00\x00\x00\x06\x01" + b"\x00" * 100)
    with pytest.raises(ToolFailure) as caught:
        dxf_read.read(str(path), reading.Limits().as_dict())
    assert caught.value.family == "input.unsupported_format" and "dwg2dxf" in caught.value.message


def test_a_pdf_named_dxf_is_refused(tmp_path):
    path = tmp_path / "x.dxf"
    path.write_bytes(b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\n")
    with pytest.raises(ToolFailure) as caught:
        dxf_read.read(str(path), reading.Limits().as_dict())
    assert caught.value.family == "input.invalid" and "PDF" in caught.value.message


def test_a_file_over_the_size_limit_is_refused_before_it_is_parsed(tmp_path):
    path = R.write(tmp_path / "big.dxf", valid_text())
    with pytest.raises(ToolFailure) as caught:
        dxf_read.read(path, reading.Limits(max_file_bytes=1000).as_dict())
    assert caught.value.family == "drawing.too_large" and caught.value.details["limit_bytes"] == 1000


def test_a_file_with_more_entities_than_the_limit_is_refused(tmp_path):
    path = R.write(tmp_path / "many.dxf", valid_text())
    with pytest.raises(ToolFailure) as caught:
        dxf_read.read(path, reading.Limits(max_entities=10).as_dict())
    assert caught.value.family == "drawing.too_large" and caught.value.details["entities"] >= 50


def test_blocks_that_insert_each_other_are_reported_and_do_not_loop(tmp_path):
    blocks = (R.block("A", R.line((0, 0), (10, 0)), R.insert("B", (0, 5))), R.block("B", R.line((0, 0), (0, 10)), R.insert("A", (5, 0))))
    r = read(tmp_path, R.document(R.V2000, insunits=4, blocks=blocks, entities=(R.insert("A", (100, 100)),)))
    assert r["blocks"]["cycles"] and set(r["blocks"]["cycles"][0]) >= {"A", "B"}
    assert any("refers back to itself" in w for w in r["warnings"])
    assert r["extents"]["model"]["max"][0] >= 110.0  # the part that can be drawn is still measured


def test_a_block_that_inserts_itself(tmp_path):
    blocks = (R.block("SELF", R.line((0, 0), (10, 0)), R.insert("SELF", (1, 1))),)
    r = read(tmp_path, R.document(R.V2000, blocks=blocks, entities=(R.insert("SELF", (0, 0)),)))
    assert r["blocks"]["cycles"] == [["SELF", "SELF"]]


def test_two_hundred_nested_blocks_hit_the_depth_limit_without_recursion_errors(tmp_path):
    n = 200
    blocks = tuple(R.block(f"L{i}", R.insert(f"L{i + 1}", (1, 0))) for i in range(n)) + (R.block(f"L{n}", R.line((0, 0), (1, 0))),)
    started = time.time()
    r = read(tmp_path, R.document(R.V2000, blocks=blocks, entities=(R.insert("L0", (0, 0)),)))
    assert time.time() - started < 10
    assert r["blocks"]["max_nesting"] > 16
    assert any("nesting" in w and "deeper than 16" in w for w in r["warnings"])


def test_a_billion_laughs_of_blocks_is_measured_in_linear_time(tmp_path):
    n = 12  # ten copies of the next block, twelve deep: ten to the twelfth visits if every one were followed
    blocks = tuple(R.block(f"B{i}", *[R.insert(f"B{i + 1}", (j, 0)) for j in range(10)]) for i in range(n)) + (R.block(f"B{n}", R.line((0, 0), (1, 0))),)
    started = time.time()
    r = read(tmp_path, R.document(R.V2000, insunits=4, blocks=blocks, entities=(R.insert("B0", (0, 0)),)))
    assert time.time() - started < 10
    assert r["extents"]["model"]["max"][0] == pytest.approx(9 * n + 1)


def test_a_huge_insert_array_is_counted_not_expanded_and_its_corners_are_in_the_extents(tmp_path):
    blocks = (R.block("U", R.line((0, 0), (1, 0))),)
    r = read(tmp_path, R.document(R.V2000, insunits=4, blocks=blocks, entities=(R.insert("U", (0, 0), columns=3, col_spacing=10.0),)))
    assert r["extents"]["model"]["max"][0] == pytest.approx(21.0)
    started = time.time()
    r = read(tmp_path, R.document(R.V2000, insunits=4, blocks=blocks, entities=(R.insert("U", (0, 0), columns=1_000_000_000, rows=1_000_000_000, col_spacing=10.0, row_spacing=10.0),)), name="b.dxf")
    assert time.time() - started < 10
    assert r["extents"]["model"]["max"][0] > 1e9


def test_an_external_reference_is_listed_and_never_followed(tmp_path):
    blocks = (R.block("OTHER", flags=4, xref_path="C:/secret/other.dwg"),)
    r = read(tmp_path, R.document(R.V2000, blocks=blocks, entities=(R.insert("OTHER", (0, 0)),)))
    assert r["blocks"]["xrefs"] == [{"name": "OTHER", "path": "C:/secret/other.dwg"}]
    assert any("external reference" in w and "not loaded" in w for w in r["warnings"])


# --------------------------------------------------------------------------- a smoke test on files another writer made (ezdxf), not an oracle


def test_every_entity_type_ezdxf_can_write_is_read_without_a_skipped_entity_and_its_dimensions_agree_with_their_blocks(tmp_path):
    import ezdxf
    from ezdxf import units

    doc = ezdxf.new("R2018", setup=True, units=units.MM)
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 0))
    msp.add_circle((50, 50), 20)
    msp.add_arc((0, 0), 30, 10, 100)
    msp.add_ellipse((0, 0), major_axis=(10, 0), ratio=0.5)
    msp.add_spline([(0, 0), (10, 10), (20, 0), (30, 10)])
    msp.add_lwpolyline([(0, 0, 0, 0, 0.5), (10, 0, 0, 0, 0), (10, 10, 0, 0, 0)], format="xyseb")
    msp.add_polyline2d([(0, 0), (5, 5), (10, 0)])
    msp.add_polyline3d([(0, 0, 0), (5, 5, 5), (10, 0, 5)])
    msp.add_point((1, 1))
    msp.add_solid([(0, 0), (1, 0), (0, 1), (1, 1)])
    msp.add_3dface([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    msp.add_mtext(r"{\fArial|b1;Heading}\Pbody").set_location((5, 5))
    msp.add_text("%%c25 THRU").set_placement((10, 10))
    hatch = msp.add_hatch()
    hatch.paths.add_polyline_path([(0, 0), (10, 0), (10, 10), (0, 10)])
    msp.add_leader([(0, 0), (5, 5), (10, 5)])
    msp.add_linear_dim(base=(0, -5), p1=(0, 0), p2=(100, 0), dimstyle="EZDXF").render()
    msp.add_aligned_dim(p1=(0, 0), p2=(30, 40), distance=3, dimstyle="EZDXF").render()
    msp.add_diameter_dim(center=(50, 50), radius=20, angle=45, dimstyle="EZDXF").render()
    msp.add_radius_dim(center=(0, 0), radius=30, angle=30, dimstyle="EZDXF").render()
    msp.add_angular_dim_cra(center=(0, 0), radius=15, start_angle=0, end_angle=60, distance=3, dimstyle="EZDXF").render()
    layout = doc.new_layout("Sheet1")
    layout.add_viewport(center=(100, 100), size=(80, 60), view_center_point=(0, 0), view_height=50)
    layout.add_line((0, 0), (10, 10))
    path = tmp_path / "ezdxf-made.dxf"
    doc.saveas(path)

    r = dxf_read.read(str(path), reading.Limits().as_dict())
    assert not any("could not be read" in w for w in r["warnings"]), r["warnings"]
    assert r["entities"]["by_type"]["DIMENSION"] == 5
    assert not any("differs" in w for w in r["warnings"]), r["warnings"]  # the computed text equals the text ezdxf drew into each dimension's block
    # ezdxf's own EZDXF dimension style has DIMLFAC 100 (it is made for metres drawn in centimetres): the reader applies it,
    # and the numbers it computes are the ones ezdxf drew into the blocks (checked by the missing "differs" warning above)
    dims = r["dimensions"]
    assert all(d["dimlfac"] == 100.0 and d["dimlfac_source"] == "dimension style" for d in dims if d["measured_unit"] == "length")
    assert sorted(d["value"] for d in dims if d["kind"] == "linear") == pytest.approx([5000.0, 10000.0])  # 50 and 100 mm measured
    diameter = next(d for d in dims if d["kind"] == "diameter")
    radius = next(d for d in dims if d["kind"] == "radius")
    angle = next(d for d in dims if d["measured_unit"] == "angle")
    assert (diameter["measured"], diameter["text"]) == (pytest.approx(40.0), "Ø4000")
    assert (radius["measured"], radius["text"]) == (pytest.approx(30.0), "R3000")
    assert (angle["value"], angle["dimlfac"], angle["text"]) == (pytest.approx(60.0), 100.0, "60°")  # DIMLFAC never scales an angle
    assert any(t["text"] == "Ø25 THRU" for t in r["texts"])
    assert {layout["space"] for layout in r["layouts"]} == {"model", "paper"}


def test_an_image_reference_is_listed_with_its_path_and_never_opened(tmp_path):
    import ezdxf

    doc = ezdxf.new("R2018", units=4)
    definition = doc.add_image_def(filename="C:/private/secret.png", size_in_pixel=(100, 100))
    doc.modelspace().add_image(definition, insert=(0, 0), size_in_units=(10, 10), rotation=0)
    path = tmp_path / "img.dxf"
    doc.saveas(path)
    r = dxf_read.read(str(path), reading.Limits().as_dict())
    assert r["images"] == [{"layer": "0", "path": "C:/private/secret.png"}]
    assert any("listed, not opened" in w for w in r["warnings"])


def test_a_multileader_callout_is_a_text_of_the_drawing(tmp_path):
    import ezdxf
    from ezdxf.render.mleader import ConnectionSide

    doc = ezdxf.new("R2018", units=4, setup=True)
    builder = doc.modelspace().add_multileader_mtext("Standard")
    builder.set_content("4X %%c6.6 THRU", style="OpenSans")
    builder.add_leader_line(ConnectionSide.left, [ezdxf.math.Vec2(-20, 20)])
    builder.build(insert=ezdxf.math.Vec2(40, 40))
    path = tmp_path / "callout.dxf"
    doc.saveas(path)
    r = dxf_read.read(str(path), reading.Limits().as_dict())
    callout = next(t for t in r["texts"] if t["kind"] == "MULTILEADER")
    assert "4X" in callout["text"] and "6.6" in callout["text"] and "THRU" in callout["text"]


def test_a_feature_control_frame_written_by_hand_is_a_text_with_its_symbols_left_as_font_letters(tmp_path):
    fcf = R.entity("TOLERANCE", 8, "GDT", 3, "Standard", 10, 5, 20, 5, 30, 0, 1, r"{\Fgdt;j}%%v{\Fgdt;n}0.1%%v A", subclass="AcDbFcf")
    r = read(tmp_path, R.document(R.V2000, insunits=4, entities=(fcf,)))
    frame = next(t for t in r["texts"] if t["kind"] == "TOLERANCE")
    assert "0.1" in frame["text"] and frame["layer"] == "GDT" and frame["at"] == [5.0, 5.0]
