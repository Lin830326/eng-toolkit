"""Regressions for what an adversarial review of drawing_read found (each test names the finding): wrong or dropped results,
active-content holes in the PDF scan, unbounded output, and quadratic or late-failing work."""

import json
import os
import time
from pathlib import Path

import pytest
import raw_dxf as R
import raw_pdf as RP

pytest.importorskip("ezdxf", reason="ezdxf is not installed; run: uv sync --extra drawing")
pytest.importorskip("pdfminer", reason="pdfminer.six is not installed; run: uv sync --extra drawing")
from eng_toolkit.domains.drawing import dxf_read, pdf_read, reading  # noqa: E402
from eng_toolkit.domains.drawing import titleblock as T  # noqa: E402
from eng_toolkit.domains.drawing.tools import read as read_tool  # noqa: E402
from eng_toolkit.errors import ToolFailure  # noqa: E402

LIMITS = reading.Limits().as_dict()
LINEAR_100 = {10: (50, 10), 13: (0, 0), 14: (100, 0)}


def read(tmp_path, text, *, name="r.dxf", encoding="utf-8", limits=None):
    path = R.write(tmp_path / name, text, encoding)
    return dxf_read.read(path, limits or LIMITS)


def call(path, **kw):
    fn = getattr(read_tool, "fn", read_tool)
    return fn(path=str(path), **kw)


# --------------------------------------------------------------------------- dropped or wrong results


def test_a_dimension_whose_geometry_block_is_missing_is_still_a_dimension(tmp_path):
    ents = (R.dimension(0, pts=LINEAR_100, rotation=0, block="*D9"),)
    r = read(tmp_path, R.document(R.V2000, insunits=4, entities=ents))
    assert len(r["dimensions"]) == 1 and r["dimensions"][0]["value"] == pytest.approx(100.0)
    assert not any("could not be read" in w for w in r["warnings"])


def test_an_insert_of_a_missing_block_still_gives_its_attributes_to_the_title_block(tmp_path):
    attribs = [("TITLE", "FLANGE", (0, 0)), ("DWG_NO", "FL-1", (0, 1)), ("REV", "B", (0, 2))]
    r = read(tmp_path, R.document(R.V2000, insunits=4, entities=(R.insert("NOBLOCK", (0, 0), attribs=attribs),)))
    assert r["title_block"]["fields"]["part_number"]["value"] == "FL-1"
    assert not any("could not be read" in w for w in r["warnings"])


def test_a_dimlfac_that_converts_inches_to_millimetres_is_called_out(tmp_path):
    pts = {10: (1, 1), 13: (0, 0), 14: (2, 0)}
    styles = (R.dimstyle_row("STANDARD", **{"144": 25.4}),)
    r = read(tmp_path, R.document(R.V2000, insunits=1, dimstyles=styles, entities=(R.dimension(0, pts=pts, rotation=0),)))
    d = r["dimensions"][0]
    assert d["value"] == pytest.approx(50.8)
    assert any("unit conversion" in w and "value_mm" in w for w in r["warnings"])


def test_an_arc_with_a_flipped_extrusion_is_mirrored_in_the_extents(tmp_path):
    arc = R.entity("ARC", 8, "0", 10, 10, 20, 0, 30, 0, 40, 5, 50, 0, 51, 90, 210, 0, 220, 0, 230, -1, version=R.V12)
    r = read(tmp_path, R.document(R.V12, entities=(arc,)))
    box = r["extents"]["model"]["geometry"]
    assert box["min"][0] == pytest.approx(-15.0, abs=1e-6) and box["max"][0] == pytest.approx(-10.0, abs=1e-6)


def test_an_oblique_extrusion_is_a_warning_not_a_silent_wrong_extent(tmp_path):
    circle = R.entity("CIRCLE", 8, "0", 10, 0, 20, 0, 30, 0, 40, 5, 210, 1, 220, 0, 230, 0, version=R.V12)
    r = read(tmp_path, R.document(R.V12, entities=(circle,)))
    assert any("extrusion" in w for w in r["warnings"])


def test_polyface_face_records_do_not_pull_the_extents_to_the_origin(tmp_path):
    ents = [R.entity("POLYLINE", 8, "0", 66, 1, 70, 64, 71, 3, 72, 1, version=R.V12)]
    for x, y in ((100, 100), (110, 100), (110, 110)):
        ents.append(R.entity("VERTEX", 8, "0", 10, x, 20, y, 30, 0, 70, 192, version=R.V12))
    ents.append(R.entity("VERTEX", 8, "0", 10, 0, 20, 0, 30, 0, 70, 128, 71, 1, 72, 2, 73, 3, version=R.V12))
    ents.append(R.entity("SEQEND", 8, "0", version=R.V12))
    r = read(tmp_path, R.document(R.V12, entities=tuple(ents)))
    assert r["extents"]["model"]["geometry"]["min"] == pytest.approx([100.0, 100.0])


def test_non_finite_coordinates_are_ignored_with_a_warning_and_the_result_is_strict_json(tmp_path):
    bad = R.entity("LINE", 8, "A", 10, "nan", 20, 0, 30, 0, 11, 5, 21, 0, 31, 0, version=R.V12)
    inf = R.entity("LINE", 8, "A", 10, 0, 20, 0, 30, 0, 11, "1e999", 21, 0, 31, 0, version=R.V12)
    r = read(tmp_path, R.document(R.V12, entities=(bad, inf, R.line((0, 0), (10, 0), "A", R.V12))))
    json.dumps(r, allow_nan=False)
    assert r["extents"]["model"]["geometry"]["max"][0] == pytest.approx(10.0)
    assert any("non-finite" in w for w in r["warnings"])


def test_a_nan_dimlfac_is_not_applied(tmp_path):
    styles = (R.dimstyle_row("STANDARD", **{"144": "nan"}),)
    r = read(tmp_path, R.document(R.V2000, insunits=4, dimstyles=styles, entities=(R.dimension(0, pts=LINEAR_100, rotation=0),)))
    d = r["dimensions"][0]
    json.dumps(r, allow_nan=False)
    assert d["dimlfac"] == 1.0 and d["value"] == pytest.approx(100.0)


@pytest.mark.parametrize("raw", ["4.9", "1e999", "-1e999"])
def test_an_insunits_that_is_not_an_integer_is_not_a_unit(tmp_path, raw):
    r = read(tmp_path, R.document(R.V2000, header_extra=R.tags(9, "$INSUNITS", 70, raw), entities=(R.line((0, 0), (1, 0)),)))
    assert r["units"]["mm_per_unit"] is None and r["units"]["name"] == "unitless"


def test_a_nan_in_an_integer_tag_is_a_structured_corrupt_error(tmp_path):
    with pytest.raises(ToolFailure) as caught:
        read(tmp_path, R.document(R.V2000, header_extra=R.tags(9, "$INSUNITS", 70, "nan"), entities=(R.line((0, 0), (1, 0)),)))
    assert caught.value.family == "drawing.corrupt"


@pytest.mark.parametrize(("code", "mm"), [(21, 304.8006096), (22, 25.4000508), (23, 914.4018288), (24, 1609347.2187)])
def test_the_us_survey_units_are_units(tmp_path, code, mm):
    r = read(tmp_path, R.document(R.V2000, insunits=code, entities=(R.line((0, 0), (1, 0)),)))
    assert r["units"]["mm_per_unit"] == pytest.approx(mm) and "survey" in r["units"]["name"]


def test_a_byte_order_mark_does_not_hide_the_units(tmp_path):
    text = "﻿" + R.document(R.V2018, insunits=1, entities=(R.line((0, 0), (1, 0), "0", R.V2018),))
    r = read(tmp_path, text)
    assert r["units"]["name"] == "inch"


def test_a_next_line_character_inside_a_header_string_does_not_shift_the_header(tmp_path):
    header = R.tags(9, "$PROJECTNAME", 1, "a\u0085b")
    r = read(tmp_path, R.document(R.V2018, insunits=1, header_extra=header, entities=(R.line((0, 0), (1, 0), "0", R.V2018),)))
    assert r["units"]["name"] == "inch"


def test_layer_names_that_differ_only_in_case_are_one_layer(tmp_path):
    ents = (R.line((0, 0), (1, 0), "Walls"), R.line((0, 0), (2, 0), "WALLS"), R.line((0, 0), (3, 0), "walls"))
    r = read(tmp_path, R.document(R.V2000, insunits=4, layers=(R.layer_row("Walls"),), entities=ents))
    rows = [l for l in r["layers"] if l["name"].lower() == "walls"]
    assert len(rows) == 1 and rows[0]["entities"] == 3 and rows[0]["length"] == pytest.approx(6.0)


def test_text_that_is_a_lone_surrogate_does_not_fail_the_call(tmp_path):
    path = R.write(tmp_path / "s.dxf", R.document(R.V2000, insunits=4, entities=(R.text(r"\U+D800 PART", (0, 0)), R.line((0, 0), (1, 0)))))
    result = call(path)
    assert result.ok, result.error
    full = json.loads(Path(next(a.path for a in result.artifacts if a.path.endswith("drawing_read.json"))).read_text(encoding="utf-8"))
    assert any("PART" in t["text"] for t in full["texts"])


# --------------------------------------------------------------------------- unbounded output


def test_a_huge_text_is_cut_in_the_artifact_and_further_in_the_answer(tmp_path):
    big = "X" * 3_000_000
    path = R.write(tmp_path / "t.dxf", R.document(R.V2000, insunits=4, entities=(R.text(big, (0, 0)),)))
    result = call(path)
    assert result.ok
    assert max(len(t["text"]) for t in result.data["texts"]["items"]) <= 540  # 500 characters and the "... [N more characters]" marker
    full = json.loads(Path(next(a.path for a in result.artifacts if a.path.endswith("drawing_read.json"))).read_text(encoding="utf-8"))
    assert max(len(t["text"]) for t in full["texts"]) <= 4100
    assert len(json.dumps(result.model_dump(mode="json"))) < 40_000


def test_a_huge_dimdec_is_clamped(tmp_path):
    styles = (R.dimstyle_row("STANDARD", **{"271": 50_000_000}),)
    r = read(tmp_path, R.document(R.V2000, insunits=4, dimstyles=styles, entities=(R.dimension(0, pts=LINEAR_100, rotation=0),)))
    assert len(r["dimensions"][0]["text"]) < 40


def test_an_error_message_that_quotes_the_file_is_short(tmp_path):
    path = tmp_path / "g.dxf"
    path.write_bytes(b"x" * 5_000_000)
    result = call(path)
    assert result.ok is False and len(result.error.message) < 1500 and len(json.dumps(result.error.details)) < 3000


def test_one_insert_with_thousands_of_attributes_does_not_flood_the_answer(tmp_path):
    attribs = [(f"T{i}", "v" * 20, (0, i)) for i in range(4000)]
    path = R.write(tmp_path / "a.dxf", R.document(R.V2000, insunits=4, blocks=(R.block("B"),), entities=(R.insert("B", (0, 0), attribs=attribs),)))
    result = call(path, max_items=10)
    assert result.ok
    listed = result.data["blocks"]["with_attributes"]
    assert listed and len(listed[0]["attribs"]) <= 10
    assert len(json.dumps(result.data)) < 20_000


def test_a_long_pdf_text_line_is_cut_in_the_answer(tmp_path):
    content = b"BT /F1 12 Tf 10 400 Td (" + b"A" * 300_000 + b") Tj ET"
    path = tmp_path / "l.pdf"
    path.write_bytes(RP.document([(content, RP.A4, 0)]))
    result = call(path)
    assert result.ok and max(len(l["text"]) for p in result.data["pages"]["items"] for l in p["text"]["lines"]) <= 540
    assert len(result.data["summary"]) < 3000


# --------------------------------------------------------------------------- cost


def test_the_title_block_search_stays_cheap_on_thousands_of_labels():
    texts = [T.TextRec("DATE", i * 3.0, 100.0, 2.5, "T") for i in range(15_000)] + [T.TextRec(f"v{i}", i * 3.0, 90.0, 2.5, "T") for i in range(15_000)]
    started = time.time()
    T.from_label_pairs(texts)
    assert time.time() - started < 8.0


def test_a_file_with_far_too_many_records_is_refused_before_it_is_loaded(tmp_path):
    ents = tuple(R.entity("POINT", 8, "0", 10, i, 20, 0, 30, 0, version=R.V12) for i in range(3000))
    path = R.write(tmp_path / "p.dxf", R.document(R.V12, entities=ents))
    with pytest.raises(ToolFailure) as caught:
        dxf_read.read(path, reading.Limits(max_entities=100).as_dict())
    assert caught.value.family == "drawing.too_large" and caught.value.details["records"] >= 3000


def test_a_file_over_the_size_limit_is_refused_before_it_is_hashed(tmp_path, monkeypatch):
    monkeypatch.setenv("ENG_TOOLKIT_READ_MAX_MB", "0.0001")
    path = R.write(tmp_path / "b.dxf", R.document(R.V2000, insunits=4, entities=tuple(R.line((i, 0), (i, 1)) for i in range(50))))
    result = call(path)
    assert result.ok is False and result.error.family == "drawing.too_large" and result.provenance.input_files == []


def test_a_dimension_block_text_is_built_once_per_block(tmp_path):
    texts = [R.mtext(f"{i}", (i, 0)) for i in range(300)]
    block = R.block("*D1", *texts, flags=1)
    ents = tuple(R.dimension(0, pts=LINEAR_100, rotation=0, block="*D1") for _ in range(300))
    started = time.time()
    r = read(tmp_path, R.document(R.V2000, insunits=4, blocks=(block,), entities=ents))
    assert len(r["dimensions"]) == 300 and time.time() - started < 10.0


# --------------------------------------------------------------------------- PDF


def test_a_cropbox_smaller_than_the_mediabox_is_reported_with_a_warning(tmp_path):
    payload = RP.document([(b"0 0 m 10 10 l S\n", RP.A4, 0)]).replace(b"/MediaBox [0 0 595.2756 841.8898]", b"/MediaBox [0 0 595.2756 841.8898] /CropBox [100 100 300 200]")
    path = tmp_path / "c.pdf"
    path.write_bytes(payload)
    r = pdf_read.read(str(path), LIMITS)
    page = r["pages"][0]
    assert page["cropbox_mm"] == pytest.approx([200 * 25.4 / 72, 100 * 25.4 / 72], abs=0.01)
    assert any("CropBox" in w for w in r["warnings"])


def test_a_malformed_mediabox_is_a_warning_not_a_silent_letter_page(tmp_path):
    payload = RP.document([(b"0 0 m 10 10 l S\n", RP.A4, 0)]).replace(b"/MediaBox [0 0 595.2756 841.8898]", b"/MediaBox [0 0 100]")
    path = tmp_path / "m.pdf"
    path.write_bytes(payload)
    r = pdf_read.read(str(path), LIMITS)
    assert any("MediaBox" in w and "default" in w for w in r["warnings"])


def test_a_page_whose_content_could_not_be_parsed_does_not_claim_a_text_layer_question_was_answered(tmp_path):
    payload = RP.document([(b"BT /F1 12 Tf 72 700 Td (HELLO) Tj ET\n0 0 m 100 100 l S\n", RP.A4, 0)]).replace(b"/Length 55", b"/Length 3")
    path = tmp_path / "s.pdf"
    path.write_bytes(payload)
    r = pdf_read.read(str(path), LIMITS)
    if r["text"]["chars"] == 0 and r["vector"]["paths"] == 0:
        assert any("empty or its content could not be parsed" in w for w in r["warnings"])


@pytest.mark.parametrize(
    ("raw", "kind"),
    [(b"<< /S /Action /JS (x) >>", "JavaScript"), (b"<< /F << /EmbeddedFile 4 0 R >> >>", "EmbeddedFile"), (b"/Names << /EmbeddedFiles 5 0 R >>", "EmbeddedFile"), (b"/AA << /O 3 0 R >>", "AdditionalActions"), (b"/AA 7 0 R", "AdditionalActions")],
)
def test_the_raw_scan_finds_each_name_it_lists(raw, kind):
    assert pdf_read._scan_raw(raw)[kind] >= 1


def test_the_raw_scan_is_not_fooled_by_binary_data():
    blob = os.urandom(5_000_000)
    assert pdf_read._scan_raw(blob).get("AdditionalActions", 0) == 0
    assert pdf_read._scan_raw(b"\x00/AAB\x01/AAPL\xff").get("AdditionalActions", 0) == 0


def test_the_object_walk_finds_an_action_past_the_thousandth_array_item_and_deep_in_nesting():
    from pdfminer.psparser import PSLiteral

    action = {"S": PSLiteral("URI"), "URI": b"http://x"}
    kinds: set[str] = set()
    pdf_read._look([{} for _ in range(2000)] + [action], kinds)
    assert "URI" in kinds
    deep: object = action
    for _ in range(30):
        deep = {"K": deep}
    kinds = set()
    pdf_read._look(deep, kinds)
    assert "URI" in kinds


def test_many_link_annotations_are_counted_once(tmp_path):
    n = 300
    page = RP.page_objects(b"0 0 m 10 10 l S\n")[0].replace(b"/Contents", b"/Annots [" + b" ".join(f"{5 + i} 0 R".encode() for i in range(n)) + b"] /Contents")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", page, RP.stream(b"0 0 m 10 10 l S\n")]
    objects += [b"<< /Type /Annot /Subtype /Link /Rect [0 0 1 1] /A << /S /URI /URI (http://x/" + str(i).encode() + b") >> >>" for i in range(n)]
    path = tmp_path / "links.pdf"
    path.write_bytes(RP.assemble(objects))
    assert pdf_read.read(str(path), LIMITS)["pdf"]["active_content"]["URI"] == n
