"""drawing_make_views end to end on CAD files built with OCP, checked against hand calculations and independent readers.

Probe part: the 100 x 50 x 20 mm block with a vertical diameter-10 hole through it at (25, 25) and R2 fillets on the
four outer vertical edges.  Front, top and right views in third angle, auto sheet and scale (A4, 1:1; the layout
arithmetic is in tests/test_drawing_sheet.py):

    front   u = X 0..100, v = Z 0..20: visible outline is the rectangle, 240 mm; the hole is hidden: x = 20 and x = 30, 20 mm each
    top     u = X 0..100, v = Y 0..50: 4 LINEs 2 * (100 - 4) + 2 * (50 - 4) = 284, 4 ARCs R2 4 * (pi / 2 * 2) = 4 pi,
            1 CIRCLE R5 10 pi: 284 + 14 pi = 327.9823 mm; everything hidden lies under a visible line
    right   u = Y 0..50, v = Z 0..20: hidden lines y = 20 and y = 30: 40 mm
    dimensions 100 (X) and 20 (Z) on the front view, 50 (Y) on the top view; the PDF page is A4: 841.89 x 595.28 pt
"""

import importlib.metadata
import json
import math
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import dxf_reader as R
import pytest

from eng_toolkit import brep
from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.drawing.tools import make_views
from eng_toolkit.domains.drawing.tools import read as read_tool
from eng_toolkit.domains.geometry.tools import measure

NEED_OCP = "OCP is not installed; run: uv sync --extra drawing"
ezdxf = pytest.importorskip("ezdxf", reason="ezdxf is not installed; run: uv sync --extra drawing")
pdfplumber = pytest.importorskip("pdfplumber", reason="pdfplumber is a dev dependency; run: uv sync --all-groups --extra drawing")
PT_PER_MM = 72.0 / 25.4
TOP_VISIBLE = 284 + 14 * math.pi


@pytest.fixture(scope="module")
def ocp():
    return pytest.importorskip("OCP", reason=NEED_OCP)


@pytest.fixture(scope="module")
def models(ocp, tmp_path_factory):
    import cad_fixtures as cad

    root = tmp_path_factory.mktemp("models")
    probe = cad.probe_part()
    return {
        "probe": cad.write_step(probe, root / "probe.step"),
        "probe_iges": cad.write_iges(probe, root / "probe.iges"),
        "probe_brep": cad.write_brep(probe, root / "probe.brep"),
        "probe_iges_faces": cad.write_iges_faces(probe, root / "probe_faces.iges"),
        "cube": cad.write_step(cad.box(length=5.0, width=5.0, height=5.0), root / "cube.step"),
        "plate": cad.write_step(cad.box(length=1500.0, width=50.0, height=10.0), root / "plate.step"),
        "block": cad.write_step(cad.box(length=500.0, width=100.0, height=20.0), root / "block.step"),
        "giant": cad.write_step(cad.box(length=300000.0, width=50.0, height=10.0), root / "giant.step"),
    }


def run(model_path, **kwargs) -> ToolResult:
    fn = getattr(make_views, "fn", make_views)
    result = fn(model_path=str(model_path), **kwargs)
    assert isinstance(result, ToolResult)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    return result


def failed(result: ToolResult, family: str) -> ToolResult:
    assert result.ok is False, "expected a failure"
    assert result.error.family == family, result.error
    assert result.warnings == []
    assert result.value is None and result.values == {}
    return result


def artifact(result: ToolResult, name: str) -> Path:
    return next(Path(a.path) for a in result.artifacts if Path(a.path).name == name)


def dxf_of(result: ToolResult) -> R.Dxf:
    return R.read(artifact(result, "drawing.dxf"))


def view_box(result: ToolResult, name: str, pad: float = 1.0):
    v = next(v for v in result.data["views"] if v["name"] == name)
    (ox, oy), (w, h) = v["origin_mm"], v["extent_mm"]
    return ox - pad, oy - pad, ox + w + pad, oy + h + pad


def view(result: ToolResult, name: str) -> dict:
    return next(v for v in result.data["views"] if v["name"] == name)


@pytest.fixture(scope="module")
def probe(models):
    return run(models["probe"], title="PROBE")


@pytest.fixture(scope="module")
def half(models):
    return run(models["probe"], scale="1:2", dimensions="overall")


@pytest.fixture(scope="module")
def overall(models):
    """The probe part with the overall dimensions only: the layout of tests/test_drawing_sheet.py without hole callouts."""
    return run(models["probe"], dimensions="overall")


# --------------------------------------------------------------------------- the result


def test_defaults_choose_a4_at_one_to_one_and_report_the_numbers(probe):
    r = probe
    assert r.ok, r.error
    assert r.provenance.engine == "eng_toolkit.drawing.make_views"
    assert r.value == r.values["scale"] and r.value.unit == "dimensionless" and r.value.quantity == 1.0
    assert r.values["view_count"].quantity == 3 and r.values["dimension_count"].quantity == 7  # 3 overall + 4 ordinates
    assert r.values["callout_count"].quantity == 1
    assert (r.values["sheet_width"].quantity, r.values["sheet_height"].quantity) == (297.0, 210.0)
    assert r.values["sheet_width"].unit == "millimeter"
    assert r.data["sheet"] == {"name": "A4", "size_mm": [297.0, 210.0], "scale_text": "1:1", "projection": "third_angle"}
    assert r.data["formats"] == ["dxf", "pdf"]
    assert "hole callouts and hole ordinates" in r.data["coverage"] and "centre lines" in r.data["coverage"]
    assert r.data["determinism"]


def test_the_views_are_reported_with_their_numbers(overall):
    probe = overall
    assert [v["name"] for v in probe.data["views"]] == ["front", "top", "right"]
    front, top, right = (view(probe, n) for n in ("front", "top", "right"))
    assert front["extent_mm"] == pytest.approx([100.0, 20.0], abs=1e-3)
    assert top["extent_mm"] == pytest.approx([100.0, 50.0], abs=1e-3)
    assert right["extent_mm"] == pytest.approx([50.0, 20.0], abs=1e-3)
    for v in (front, top, right):
        assert v["method"] == "exact" and v["verified"] is True
        assert v["visible_primitives"] > 0 and isinstance(v["dropped"], dict)
    assert (front["hidden_primitives"], top["hidden_primitives"], right["hidden_primitives"]) == (2, 0, 2)
    assert front["origin_mm"] == pytest.approx([75.0, 86.5], abs=1e-3)  # the layout arithmetic of tests/test_drawing_sheet.py


def test_the_artifacts_are_the_dxf_and_the_pdf(probe):
    assert [a.format for a in probe.artifacts if a.format in ("dxf", "pdf")] == ["dxf", "pdf"]
    assert artifact(probe, "drawing.dxf").read_bytes().startswith(b"  0\nSECTION")
    assert artifact(probe, "drawing.pdf").read_bytes().startswith(b"%PDF-1.4")
    manifest = json.loads((artifact(probe, "drawing.dxf").parent / "manifest.json").read_text(encoding="utf-8"))
    assert {"drawing.dxf", "drawing.pdf"} <= set(manifest["files"])


def test_the_standing_warnings_say_what_the_drawing_is_not(probe):
    text = " ".join(probe.warnings)
    assert "bounding box" in text and "unreviewed" in text and "not a complete manufacturing drawing" in text
    assert "no text layer" in text


def test_provenance_lists_the_model_and_the_engines(probe, models):
    (ref,) = probe.provenance.input_files
    path = Path(models["probe"])
    assert ref.path == str(path.resolve()) and ref.bytes == path.stat().st_size
    import hashlib

    assert ref.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert {"cadquery-ocp-novtk", "ezdxf", "numpy"} <= set(probe.provenance.engine_versions)
    assert probe.provenance.engine_versions["ezdxf"] == importlib.metadata.version("ezdxf")
    assert probe.provenance.engine_versions["cadquery-ocp-novtk"] == importlib.metadata.version("cadquery-ocp-novtk")


# --------------------------------------------------------------------------- DXF geometry, read with the independent reader


def test_front_outline_is_exactly_100_by_20_at_its_origin(probe):
    f = dxf_of(probe)
    ox, oy = view(probe, "front")["origin_mm"]
    box = view_box(probe, "front")
    lines = [e for e in f.of("LINE", "OUTLINE") if R.inside(e, box)]
    assert len(lines) == 4
    corners = {tuple(round(c, 3) for c in p) for e in lines for p in (e.point(10, 20), e.point(11, 21))}
    assert corners == {(round(ox, 3), round(oy, 3)), (round(ox + 100, 3), round(oy, 3)), (round(ox + 100, 3), round(oy + 20, 3)), (round(ox, 3), round(oy + 20, 3))}
    assert f.total_length("OUTLINE", box) == pytest.approx(240.0, abs=1e-3)


def test_top_outline_total_length_is_284_plus_14_pi(probe):
    f = dxf_of(probe)
    box = view_box(probe, "top")
    inside_top = [e for e in f.on("OUTLINE") if R.inside(e, box)]
    kinds = {t: sum(e.type == t for e in inside_top) for t in ("LINE", "ARC", "CIRCLE")}
    assert kinds == {"LINE": 4, "ARC": 4, "CIRCLE": 1}
    assert f.total_length("OUTLINE", box) == pytest.approx(TOP_VISIBLE, abs=1e-3)
    assert f.total_length("OUTLINE", box) == pytest.approx(327.9823, abs=1e-3)
    ox, oy = view(probe, "top")["origin_mm"]
    (circle,) = [e for e in inside_top if e.type == "CIRCLE"]
    assert circle.point(10, 20) == pytest.approx((ox + 25.0, oy + 25.0), abs=1e-3) and circle.number(40) == pytest.approx(5.0, abs=1e-6)
    centres = sorted(tuple(round(c - o, 3) for c, o in zip(e.point(10, 20), (ox, oy))) for e in inside_top if e.type == "ARC")
    assert centres == [(2.0, 2.0), (2.0, 48.0), (98.0, 2.0), (98.0, 48.0)]
    assert all(e.number(40) == pytest.approx(2.0, abs=1e-6) for e in inside_top if e.type == "ARC")


def test_hidden_lines_of_the_hole_are_40_mm_in_the_front_and_right_views(probe):
    f = dxf_of(probe)
    assert f.total_length("HIDDEN", view_box(probe, "front")) == pytest.approx(40.0, abs=1e-4)
    assert f.total_length("HIDDEN", view_box(probe, "right")) == pytest.approx(40.0, abs=1e-4)
    assert f.total_length("HIDDEN", view_box(probe, "top")) == 0.0
    ox, oy = view(probe, "front")["origin_mm"]
    xs = sorted(round(e.point(10, 20)[0] - ox, 3) for e in f.of("LINE", "HIDDEN") if R.inside(e, view_box(probe, "front")))
    assert xs == [20.0, 30.0]
    assert f.layers["HIDDEN"].get(6) == "HIDDEN_MM"


def test_at_one_to_two_every_drawn_length_halves(half):
    assert half.values["scale"].quantity == 0.5 and half.data["sheet"]["scale_text"] == "1:2"
    f = dxf_of(half)
    assert view(half, "front")["extent_mm"] == pytest.approx([50.0, 10.0], abs=1e-3)
    assert view(half, "top")["extent_mm"] == pytest.approx([50.0, 25.0], abs=1e-3)
    assert view(half, "right")["extent_mm"] == pytest.approx([25.0, 10.0], abs=1e-3)
    assert f.total_length("HIDDEN", view_box(half, "front")) == pytest.approx(20.0, abs=1e-4)
    assert f.total_length("HIDDEN", view_box(half, "right")) == pytest.approx(20.0, abs=1e-4)
    assert f.total_length("OUTLINE", view_box(half, "top")) == pytest.approx(TOP_VISIBLE / 2, abs=1e-3)
    ox, oy = view(half, "top")["origin_mm"]
    (circle,) = [e for e in f.of("CIRCLE", "OUTLINE") if R.inside(e, view_box(half, "top"))]
    assert circle.point(10, 20) == pytest.approx((ox + 12.5, oy + 12.5), abs=1e-3) and circle.number(40) == pytest.approx(2.5, abs=1e-6)


def test_hidden_lines_can_be_left_out(models):
    r = run(models["probe"], hidden_lines=False)
    assert r.ok and all(v["hidden_primitives"] == 0 for v in r.data["views"])
    assert dxf_of(r).on("HIDDEN") == []


# --------------------------------------------------------------------------- dimensions


def independent_measure(e: R.Entity) -> float:
    a, b = e.point(13, 23), e.point(14, 24)
    angle = math.radians(e.number(50, 0.0))
    return abs((b[0] - a[0]) * math.cos(angle) + (b[1] - a[1]) * math.sin(angle)) * e.overrides().get(144, 1.0)


def sizes_from_geometry_measure(model) -> list[float]:
    r = getattr(measure, "fn", measure)(model_path=str(model))
    return sorted(r.values[k].quantity for k in ("size_x", "size_y", "size_z"))


@pytest.mark.parametrize("scale", ["auto", "1:2", "2:1"])
def test_dimensions_read_the_sizes_geometry_measure_reports(models, scale):
    r = run(models["probe"], scale=scale, dimensions="overall")
    assert r.ok, r.error
    expected = sizes_from_geometry_measure(models["probe"])
    assert expected == pytest.approx([20.0, 50.0, 100.0], abs=1e-6)
    reported = sorted(d["value_mm"] for d in r.data["dimensions"])
    assert reported == pytest.approx(expected, abs=1e-3)
    assert sorted(d["text"] for d in r.data["dimensions"]) == ["100", "20", "50"]

    f = dxf_of(r)
    entities = f.of("DIMENSION", "DIM")
    assert len(entities) == 3 == r.values["dimension_count"].quantity
    assert sorted(independent_measure(e) for e in entities) == pytest.approx(expected, abs=1e-3)

    doc = ezdxf.readfile(artifact(r, "drawing.dxf"))
    shown = []
    for e in doc.modelspace().query("DIMENSION"):
        lfac = e.override().get("dimlfac", doc.dimstyles.get(e.dxf.dimstyle).dxf.dimlfac)
        shown.append(e.get_measurement() * lfac)
    assert sorted(shown) == pytest.approx(expected, abs=1e-3)
    block_text = sorted(b.text for e in f.of("DIMENSION") for b in f.blocks[e.get(2)] if b.type in ("MTEXT", "TEXT"))
    assert block_text == ["100", "20", "50"]


def test_dimensions_are_once_per_axis_on_the_right_views(probe):
    got = {d["source"]: d for d in probe.data["dimensions"]}
    assert set(got) == {"bbox:x", "bbox:y", "bbox:z"}
    assert (got["bbox:x"]["view"], got["bbox:z"]["view"], got["bbox:y"]["view"]) == ("front", "front", "top")
    assert all(d["kind"] == "linear" for d in got.values())


def test_no_dimensions_on_request(models):
    r = run(models["probe"], dimensions="none")
    assert r.ok and r.values["dimension_count"].quantity == 0 and r.data["dimensions"] == []
    assert dxf_of(r).of("DIMENSION") == []


def test_a_single_front_view_is_dimensioned_in_x_and_z_only(models):
    r = run(models["probe"], views="front")
    assert sorted(d["text"] for d in r.data["dimensions"]) == ["100", "20"]
    assert {d["source"] for d in r.data["dimensions"]} == {"bbox:x", "bbox:z"}


# --------------------------------------------------------------------------- PDF, read with pdfplumber


def test_the_pdf_page_is_a4_and_has_no_characters(probe):
    path = artifact(probe, "drawing.pdf")
    data = path.read_bytes()
    media = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", data)
    assert (float(media.group(1)), float(media.group(2))) == (841.89, 595.28)
    with pdfplumber.open(path) as pdf:
        assert len(pdf.pages) == 1
        page = pdf.pages[0]
        assert (page.width, page.height) == pytest.approx((841.89, 595.28), abs=0.01)
        assert len(page.chars) == 0


def test_the_front_outline_is_283_465_pt_wide_at_its_place(probe):
    ox, oy = view(probe, "front")["origin_mm"]
    with pdfplumber.open(artifact(probe, "drawing.pdf")) as pdf:
        page = pdf.pages[0]
        long = [line for line in page.lines if line["x1"] - line["x0"] > 35 * PT_PER_MM]
        outline = [line for line in long if abs(line["linewidth"] - 0.5 * PT_PER_MM) < 0.005 and abs(line["x1"] - line["x0"] - 100 * PT_PER_MM) < 0.05]
        assert len(outline) == 2
        assert all(abs(line["bottom"] - line["top"]) < 0.01 for line in outline)
        bottom = max(outline, key=lambda line: line["top"])
        top = min(outline, key=lambda line: line["top"])
        assert bottom["x0"] == pytest.approx(ox * PT_PER_MM, abs=0.05)
        assert page.height - bottom["bottom"] == pytest.approx(oy * PT_PER_MM, abs=0.05)
        assert bottom["top"] - top["top"] == pytest.approx(20 * PT_PER_MM, abs=0.05)
        # lines wider than 35 mm, by hand: frame 2 + title block 5 (outline top and bottom, three dividers) + front 2 + top 2 (96 mm)
        # + right 2 (50 mm) = 13, and the 100 mm dimension line makes 14; text outlines and arrowheads are shorter
        assert 13 <= len(long) <= 20, len(long)


def test_the_pdf_has_the_same_hidden_dashes_as_the_linetype(probe):
    data = artifact(probe, "drawing.pdf").read_bytes()
    assert f"[{3 * PT_PER_MM:.3f} {2 * PT_PER_MM:.3f}] 0 d".encode() in data


# --------------------------------------------------------------------------- layout through the tool


def test_third_angle_puts_top_above_front_and_right_beside_it(probe):
    front, top, right = (view(probe, n)["origin_mm"] for n in ("front", "top", "right"))
    assert top[1] > front[1] + 20 and right[0] > front[0] + 100
    assert top[0] == pytest.approx(front[0], abs=1e-6) and right[1] == pytest.approx(front[1], abs=1e-6)


def test_first_angle_mirrors_it(models):
    r = run(models["probe"], projection="first_angle", views="front,top,right,left,bottom")
    front, top, right, left, bottom = (view(r, n)["origin_mm"] for n in ("front", "top", "right", "left", "bottom"))
    assert top[1] < front[1] and bottom[1] > front[1] and right[0] < front[0] and left[0] > front[0]
    assert r.data["sheet"]["projection"] == "first_angle"
    assert "FIRST ANGLE" in {e.text for e in dxf_of(r).of("TEXT")}


@pytest.mark.parametrize("names", ["iso", "front", "back,left", "bottom,top,front", "front,back,top,bottom,left,right,iso"])
def test_any_subset_of_views_works(models, names):
    r = run(models["probe"], views=names)
    assert r.ok, r.error
    assert [v["name"] for v in r.data["views"]] == names.split(",")
    assert dxf_of(r).of("LINE", "OUTLINE")


def test_a_five_millimetre_cube_is_enlarged(models):
    r = run(models["cube"])
    assert r.ok and r.values["scale"].quantity == 10.0 and r.data["sheet"]["scale_text"] == "10:1" and r.data["sheet"]["name"] == "A4"
    assert sorted(d["text"] for d in r.data["dimensions"]) == ["5", "5", "5"]


def test_a_1500_mm_plate_goes_on_a1_at_one_to_two(models):
    r = run(models["plate"])
    assert r.ok, r.error
    assert (r.data["sheet"]["name"], r.data["sheet"]["scale_text"]) == ("A1", "1:2")
    assert (r.values["sheet_width"].quantity, r.values["sheet_height"].quantity) == (841.0, 594.0)
    assert sorted(d["text"] for d in r.data["dimensions"]) == ["10", "1500", "50"]


def test_a_fixed_sheet_searches_the_scale_and_a_fixed_scale_the_sheet(models):
    r = run(models["plate"], sheet="A4")
    assert (r.data["sheet"]["name"], r.data["sheet"]["scale_text"]) == ("A4", "1:10")
    r = run(models["block"], scale="1:1")
    assert (r.data["sheet"]["name"], r.data["sheet"]["scale_text"]) == ("A1", "1:1")


def test_nothing_fitting_is_a_layout_failure_that_names_what_would(models):
    r = failed(run(models["block"], sheet="A4", scale="1:1"), "drawing.layout_failed")
    assert r.error.details["fits_at_scale"] == "1:5" and r.error.details["fits_on_sheet"] == "A1"
    r = failed(run(models["giant"]), "drawing.layout_failed")
    assert r.error.details["fits_at_scale"] is None
    failed(run(models["plate"], scale="1:1"), "drawing.layout_failed")


# --------------------------------------------------------------------------- title block and text


def test_the_title_block_carries_what_was_given(models):
    r = run(
        models["probe"],
        title="BRACKET",
        part_number="BR-001",
        material="AL 6061",
        revision="C",
        general_tolerance="ISO 2768-m",
        notes="deburr;break edges 0.5",
    )
    texts = {e.text for e in dxf_of(r).of("TEXT")}
    assert {"BRACKET", "BR-001", "AL 6061", "C", "ISO 2768-m", "1:1", "A4", "THIRD ANGLE", "1. deburr", "2. break edges 0.5"} <= texts
    assert "UNREVIEWED - generated by eng-toolkit, not engineering approved" in texts


@pytest.mark.parametrize("space", ["　", " ", " ", " "])
def test_space_characters_other_than_the_ascii_space_are_accepted_as_one_space(models, space):
    """CJK text uses U+3000 as a separator; U+00A0 comes from pasted text. Both are written, and said to be written, as U+0020."""
    r = run(models["probe"], title=f"支架{space}本體", notes=f"去毛邊{space}兩面;second{space}note", material=f"AL{space}6061")
    assert r.ok, r.error
    texts = {e.text for e in dxf_of(r).of("TEXT")}
    assert {"支架 本體", "AL 6061", "1. 去毛邊 兩面", "2. second note"} <= texts
    assert not any(c in "".join(texts) for c in "　   ")
    (warning,) = [w for w in r.warnings if "U+0020" in w]
    assert warning.startswith("title, material, notes:")


def test_an_ordinary_title_gets_no_space_warning(probe):
    assert not any("U+0020" in w for w in probe.warnings)


@pytest.mark.parametrize("bad", ["a b", "a b", "a​b", "ab", "ab", "ab", "a﻿b", "ab"])
def test_line_breaks_and_invisible_controls_are_still_rejected(models, bad):
    r = failed(run(models["probe"], title=bad), "input.invalid")
    assert r.error.details["param"] == "title"


LITERAL = "%%c10 %%d %%p 50% %%%u %%065 100%"


def read_texts(result) -> list[str]:
    path = artifact(result, "drawing.dxf")
    out = getattr(read_tool, "fn", read_tool)(path=str(path), max_items=500)
    assert out.ok, out.error
    return [t["text"] for t in out.data["texts"]["items"]]


def test_percent_codes_in_user_text_are_escaped_so_they_stay_literal(models):
    """%%c %%d %%p (and %%u, %%nnn) are CAD control codes; the user's text must come back as typed, not as a diameter sign."""
    r = run(models["probe"], title="%%c10 %%d %%p", notes=f"{LITERAL};5% off")
    assert r.ok, r.error
    raw = {e.text for e in dxf_of(r).of("TEXT")}
    assert "%%%%%%c10 %%%%%%d %%%%%%p" in raw  # every % that touches another % is written as %%%
    assert "2. 5% off" in raw  # a lone % needs no escape
    back = read_texts(r)
    assert "%%c10 %%d %%p" in back and f"1. {LITERAL}" in back and "2. 5% off" in back
    assert not any(c in text for text in back for c in "°±")  # no code was turned into a symbol (the Ø of the hole callout is not from a code)


def test_a_real_code_is_still_a_code_for_the_reader_so_the_round_trip_tells_them_apart(models, tmp_path):
    """The reader turns a raw %%c into a diameter sign, and our escaped literal %%c into the three characters."""
    import raw_dxf as RD

    raw = tmp_path / "codes.dxf"
    raw.write_text(RD.document(RD.V2000, entities=(RD.text("%%c10", (0, 0), 2.5, "0"), RD.text("%%%%%%c10", (0, 10), 2.5, "0"))), encoding="utf-8")
    out = getattr(read_tool, "fn", read_tool)(path=str(raw), max_items=50)
    assert sorted(t["text"] for t in out.data["texts"]["items"]) == ["%%c10", "Ø10"]


def test_an_escaped_percent_text_is_drawn_literally_in_the_pdf(models):
    plain = {t: artifact(run(models["probe"], title=t), "drawing.pdf").read_bytes() for t in ("%%c10", "Ø10", "%%x10")}
    assert plain["%%c10"] != plain["Ø10"]  # not the diameter sign any more
    assert len(plain["%%c10"]) > len(plain["Ø10"]) + 1000  # three more glyphs: %, %, c instead of one


def test_a_chinese_title_does_not_crash_and_the_dxf_text_round_trips(models):
    title = "支架 BRACKET Ø10 ±0.1"
    r = run(models["probe"], title=title, notes="去毛邊;未註公差依 ISO 2768-m")
    assert r.ok, r.error
    texts = {e.text for e in dxf_of(r).of("TEXT")}
    assert title in texts and "1. 去毛邊" in texts
    doc = ezdxf.readfile(artifact(r, "drawing.dxf"))
    assert title in {e.dxf.text for e in doc.modelspace().query("TEXT")}
    with pdfplumber.open(artifact(r, "drawing.pdf")) as pdf:
        assert len(pdf.pages[0].chars) == 0
    assert not any("No installed font" in w for w in r.warnings)


def test_a_view_that_is_not_exact_gets_a_specific_warning(models, monkeypatch):
    import dataclasses

    real = brep.project
    methods = {"front": "polygonal", "top": "exact_unverified"}
    monkeypatch.setattr(brep, "project", lambda solid, name, **kw: dataclasses.replace(real(solid, name, **kw), method=methods.get(name, "exact")))
    r = run(models["probe"])
    assert r.ok
    assert any("front view" in w and "polygonal" in w for w in r.warnings)
    assert any("top view" in w and "cross-checked" in w for w in r.warnings)
    assert not any("right view" in w for w in r.warnings)


# --------------------------------------------------------------------------- formats


def test_the_pdf_can_be_left_out_but_the_dxf_is_always_written(models):
    r = run(models["probe"], formats="dxf")
    assert [a.format for a in r.artifacts if a.format in ("dxf", "pdf")] == ["dxf"]
    assert not any("text layer" in w for w in r.warnings)
    r = run(models["probe"], formats="pdf")
    assert r.data["formats"] == ["dxf", "pdf"]
    assert artifact(r, "drawing.dxf").exists() and artifact(r, "drawing.pdf").exists()


# --------------------------------------------------------------------------- determinism

DETERMINISM_SCRIPT = """
import json, sys
from eng_toolkit.domains.drawing.tools import make_views
fn = getattr(make_views, "fn", make_views)
r = fn(model_path=sys.argv[1], title="支架 BRACKET", part_number="BR-001", notes="deburr;去毛邊", formats="dxf,pdf,svg,png")
assert r.ok, r.error
files = {a.format: a.sha256 for a in r.artifacts if a.format in ("dxf", "pdf", "svg", "png")}
open(sys.argv[2], "w", encoding="utf-8").write(json.dumps({"files": files, "inputs_hash": r.provenance.inputs_hash}))
"""


def run_in_process(model, seed, tmp_path):
    out = tmp_path / f"out_{seed}.json"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONHASHSEED"} | {"ENG_TOOLKIT_WS": str(tmp_path / f"ws_{seed}")}
    if seed is not None:
        env["PYTHONHASHSEED"] = str(seed)
    done = subprocess.run([sys.executable, "-c", DETERMINISM_SCRIPT, str(model), str(out)], env=env, capture_output=True, check=False)
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    assert done.stdout == b""
    return json.loads(out.read_text(encoding="utf-8"))


def test_the_dxf_the_pdf_the_svg_and_the_png_are_the_same_bytes_under_every_hash_seed(models, tmp_path):
    seeds = [*range(1, 13), None]
    with ThreadPoolExecutor(max_workers=6) as pool:
        runs = list(pool.map(lambda seed: run_in_process(models["probe"], seed, tmp_path), seeds))
    assert len({json.dumps(r["files"], sort_keys=True) for r in runs}) == 1, {s: r["files"] for s, r in zip(seeds, runs)}
    assert set(runs[0]["files"]) == {"dxf", "pdf", "svg", "png"}
    assert len({r["inputs_hash"] for r in runs}) == 1


def test_two_calls_in_one_process_give_the_same_bytes_and_inputs_hash(models):
    a, b = run(models["probe"], title="支架"), run(models["probe"], title="支架")
    assert artifact(a, "drawing.dxf").read_bytes() == artifact(b, "drawing.dxf").read_bytes()
    assert artifact(a, "drawing.pdf").read_bytes() == artifact(b, "drawing.pdf").read_bytes()
    assert a.provenance.inputs_hash == b.provenance.inputs_hash
    assert artifact(a, "drawing.dxf") != artifact(b, "drawing.dxf")


@pytest.mark.parametrize("key", ["probe_iges", "probe_brep"])
def test_iges_and_brep_models_work_too(models, key):
    r = run(models[key])
    assert r.ok, r.error
    assert sorted(d["text"] for d in r.data["dimensions"]) == ["100", "20", "50"]


def test_a_surface_only_iges_draws_the_same_hidden_lines_as_the_solid(models, probe):
    """Loose IGES faces store the hole as a surface of revolution; its seam must not show as a dashed line in the hole centre."""
    r = run(models["probe_iges_faces"])
    assert r.ok, r.error
    f = dxf_of(r)
    assert f.total_length("HIDDEN", view_box(r, "front")) == pytest.approx(40.0, abs=1e-4)
    assert f.total_length("HIDDEN", view_box(r, "right")) == pytest.approx(40.0, abs=1e-4)
    assert [view(r, n)["hidden_primitives"] for n in ("front", "top", "right")] == [2, 0, 2]
    assert view(r, "right")["dropped"]["seam"] == 1  # the same count as the solid file of the same part
    assert view(probe, "right")["dropped"]["seam"] == 1


def test_a_model_without_a_closed_solid_says_so(models, probe):
    r = run(models["probe_iges_faces"])
    text = " ".join(r.warnings)
    assert "no closed solid" in text and "IGES" in text
    assert "no closed solid" not in " ".join(probe.warnings)


# --------------------------------------------------------------------------- errors


def test_a_missing_model(tmp_path):
    failed(run(tmp_path / "nope.step"), "input.file_not_found")


def test_an_unsupported_extension(tmp_path):
    path = tmp_path / "model.txt"
    path.write_text("hello")
    failed(run(path), "input.unsupported_format")


def test_a_text_file_named_step(tmp_path, ocp):
    path = tmp_path / "fake.step"
    path.write_text("this is not a step file\n")
    failed(run(path), "input.invalid")


@pytest.mark.parametrize(
    ("kwargs", "param"),
    [
        ({"sheet": "A5"}, "sheet"),
        ({"sheet": "letter"}, "sheet"),
        ({"scale": "1:3"}, "scale"),
        ({"scale": "0.5"}, "scale"),
        ({"formats": "gif"}, "formats"),
        ({"formats": "dxf,pdf,jpg"}, "formats"),
        ({"title": "x" * 81}, "title"),
        ({"part_number": "x" * 41}, "part_number"),
        ({"material": "x" * 41}, "material"),
        ({"revision": "x" * 9}, "revision"),
        ({"general_tolerance": "x" * 81}, "general_tolerance"),
        ({"title": "a\nb"}, "title"),
        ({"material": "a\tb"}, "material"),
        ({"notes": "a\x00b"}, "notes"),
        ({"notes": ";".join(["note"] * 7)}, "notes"),
        ({"notes": "n" * 101}, "notes"),
        ({"projection": "second_angle"}, "projection"),
        ({"dimensions": "everything"}, "dimensions"),
        ({"views": "front,front"}, "views"),
        ({"views": "front,side"}, "views"),
        ({"views": ""}, "views"),
    ],
)
def test_bad_arguments_are_input_invalid_and_name_the_parameter(models, kwargs, param):
    r = failed(run(models["probe"], **kwargs), "input.invalid")
    assert r.error.details["param"] == param


def test_a_missing_ezdxf_gives_an_install_hint(models, monkeypatch):
    monkeypatch.setitem(sys.modules, "ezdxf", None)
    r = failed(run(models["probe"]), "engine.unavailable")
    assert "uv sync --extra drawing" in r.error.message and r.error.details["install"] == "uv sync --extra drawing"


def test_a_missing_ocp_gives_an_install_hint(models, monkeypatch):
    monkeypatch.setitem(sys.modules, "OCP", None)
    r = failed(run(models["probe"]), "engine.unavailable")
    assert "uv sync --extra cad" in r.error.message


# --------------------------------------------------------------------------- process hygiene


def test_nothing_is_written_to_stdout_on_success_or_failure(models, tmp_path, capfd):
    assert run(models["probe"], title="支架").ok
    bad = tmp_path / "bad.step"
    bad.write_text("not a step file\n")
    assert run(bad).ok is False
    assert run(models["probe"], sheet="A9").ok is False
    assert capfd.readouterr().out == ""


def test_a_fresh_process_writes_nothing_to_stdout_not_even_at_exit(models, tmp_path):
    bad = tmp_path / "bad.step"
    bad.write_text("not a step file\n")
    script = (
        "import sys\n"
        "from eng_toolkit.domains.drawing.tools import make_views\n"
        "fn = getattr(make_views, 'fn', make_views)\n"
        "for path in sys.argv[1:]:\n"
        "    fn(model_path=path, title='支架')\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script, str(models["probe"]), str(bad), str(models["probe_brep"])],
        capture_output=True,
        env={**os.environ, "ENG_TOOLKIT_WS": str(tmp_path / "ws")},
        check=False,
    )
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    assert done.stdout == b""


def test_a_non_ascii_path_is_read(models, tmp_path):
    copy = tmp_path / "零件_é.step"
    shutil.copyfile(models["probe"], copy)
    r = run(copy)
    assert r.ok, r.error
    assert sorted(d["text"] for d in r.data["dimensions"]) == ["100", "20", "50"]


def test_the_result_round_trips_through_json(probe):
    again = ToolResult.model_validate(json.loads(probe.model_dump_json()))
    assert again == probe


def test_the_tool_arguments_are_strings_bools_and_ints_only():
    import typing

    hints = typing.get_type_hints(getattr(make_views, "fn", make_views))
    hints.pop("return")
    assert hints and all(kind in (str, bool, int) for kind in hints.values()), hints
