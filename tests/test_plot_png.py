"""The PNG of the plot tools (format="png"|"both"), the SVG-to-PDF converter behind it, and the findings fixed with it:
zero-anchored automatic axes, value labels that fit their bars, unit text in a range, the marked point."""

import importlib.util
import json
import struct
import sys
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest

from eng_toolkit.domains.plot import _png, _svgpdf
from eng_toolkit.domains.plot.bar import bar
from eng_toolkit.domains.plot.panels import panels
from eng_toolkit.domains.plot.xy import xy
from tests.plot_testing import artifact, call, elements, fails, ok, plot_data, svg_text, texts

needs_pdfium = pytest.mark.skipif(importlib.util.find_spec("pypdfium2") is None, reason="pypdfium2 is not installed; run: uv sync --extra png")

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
LINE = json.dumps([{"name": "a", "x": [0, 5, 10], "y": [0, 50, 100]}])


def decode_png(path: Path) -> np.ndarray:
    """The (height, width, 3) pixels of one of our PNGs; checks the signature, the CRCs and the chunk layout on the way."""
    raw = Path(path).read_bytes()
    assert raw[:8] == PNG_SIGNATURE
    pos, chunks = 8, []
    while pos < len(raw):
        n = struct.unpack(">I", raw[pos:pos + 4])[0]
        kind, body = raw[pos + 4:pos + 8], raw[pos + 8:pos + 8 + n]
        assert zlib.crc32(kind + body) & 0xFFFFFFFF == struct.unpack(">I", raw[pos + 8 + n:pos + 12 + n])[0]
        chunks.append((kind, body))
        pos += 12 + n
    assert [k for k, _ in chunks] == [b"IHDR", b"IDAT", b"IEND"]
    width, height, depth, colour = struct.unpack(">IIBB", chunks[0][1][:10])
    assert (depth, colour) == (8, 2)
    data = np.frombuffer(zlib.decompress(chunks[1][1]), dtype=np.uint8).reshape(height, 1 + 3 * width)
    rows = data[:, 1:].copy()
    for y in range(height):
        assert data[y, 0] in (0, 2)
        if data[y, 0] == 2 and y:
            rows[y] = rows[y] + rows[y - 1]
    return rows.reshape(height, width, 3)


def count_near(pixels: np.ndarray, rgb: tuple[int, int, int], tol: int = 12) -> int:
    return int(np.count_nonzero(np.all(np.abs(pixels.astype(int) - np.array(rgb)) <= tol, axis=2)))


# ----------------------------------------------------------------------------------------------- the PNG itself


@needs_pdfium
def test_format_png_writes_a_real_png_of_the_documented_size_and_lists_it():
    r = call(xy, series=LINE, format="png", x_label="Time (s)", y_label="Power (W)", title="Duty")
    assert r.ok, r.error
    png = artifact(r, "plot.png")
    assert png.read_bytes()[:8] == PNG_SIGNATURE
    pixels = decode_png(png)
    assert pixels.shape == (1000, 1600, 3)  # 800 x 500 SVG at 2x
    assert r.data["png_path"] == str(png) and r.data["png_width"] == 1600 and r.data["png_height"] == 1000
    assert next(a for a in r.artifacts if a.path == str(png)).format == "png"
    assert not any(a.path.endswith("plot.svg") for a in r.artifacts)  # png only: no SVG written
    assert r.data["svg_inline"] is None and "format is png" in r.data["svg_inline_omitted"] and r.data["format"] == "png"
    assert r.data["files"] == {"png": "plot.png", "data": "plot_data.json"}
    saved = plot_data(r)
    assert saved["png"]["width_px"] == 1600 and saved["png"]["bytes"] == png.stat().st_size and saved["format"] == "png"
    assert r.provenance.engine_versions["pypdfium2"]


@needs_pdfium
def test_the_png_is_a_picture_not_a_blank_page():
    pixels = decode_png(artifact(call(xy, series=LINE, theme="light", title="Duty", format="png"), "plot.png"))
    assert tuple(pixels[0, 0]) == (255, 255, 255)
    assert count_near(pixels, (0x00, 0x72, 0xB2)) > 1500  # the series line, 1.75 px x 2 over about 1500 px
    assert np.count_nonzero(np.all(pixels[10:60, 600:1000] < 120, axis=2)) > 150  # the title text sits at the top centre
    blended = np.count_nonzero((pixels.min(axis=2) > 30) & (pixels.max(axis=2) < 240))
    assert blended > 500  # anti-aliased edges of the line and the text exist


@needs_pdfium
def test_the_theme_colours_reach_the_png():
    dark = decode_png(artifact(call(xy, series=LINE, theme="dark", format="png"), "plot.png"))
    assert tuple(dark[0, 0]) == (0x15, 0x18, 0x1D)
    auto = decode_png(artifact(call(xy, series=LINE, theme="auto", format="png"), "plot.png"))
    assert tuple(auto[0, 0]) == (255, 255, 255)  # a bitmap has no dark-mode switch: auto is the light theme
    assert count_near(decode_png(artifact(call(xy, series=LINE, theme="print", format="png"), "plot.png")), (0, 0, 0), 20) > 1500


@needs_pdfium
def test_the_same_chart_gives_the_same_bytes_in_any_call_and_from_four_threads():
    def one(_):
        return artifact(call(xy, series=LINE, markers=True, format="png"), "plot.png").read_bytes()

    first = one(0)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rest = list(pool.map(one, range(4)))
    assert all(png == first for png in rest)
    other = artifact(call(xy, series=json.dumps([{"name": "a", "x": [0, 5, 10], "y": [0, 50, 101]}]), markers=True, format="png"), "plot.png").read_bytes()
    assert other != first


def _names(r):
    return [a.path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for a in r.artifacts]


@needs_pdfium
def test_both_writes_the_svg_and_the_png_and_is_the_default():
    both = call(xy, series=LINE, format="both")
    assert both.ok and artifact(both, "plot.svg").exists() and artifact(both, "plot.png").exists()
    assert both.data["svg_inline"].startswith("<svg") and both.data["files"] == {"svg": "plot.svg", "png": "plot.png", "data": "plot_data.json"}
    default = ok(xy, series=LINE)
    assert _names(default) == ["inputs.json", "plot.svg", "plot.png", "plot_data.json"]
    assert default.data["format"] == "both" and default.data["png_path"].endswith("plot.png") and "pypdfium2" in default.provenance.engine_versions
    assert svg_text(both) == svg_text(default)  # the SVG is the same bytes whatever else is asked for
    assert artifact(both, "plot.png").read_bytes() == artifact(default, "plot.png").read_bytes()


@needs_pdfium
def test_every_plot_tool_defaults_to_both_and_format_svg_still_means_svg_only():
    bar_series = json.dumps([{"name": "a", "x": ["p", "q"], "y": [1, 2]}])
    two = json.dumps([{"name": "a", "x": [1, 2], "y": [1, 2]}, {"name": "b", "x": [1, 2], "y": [2, 1]}])
    for tool, kwargs in ((xy, {"series": LINE}), (panels, {"panels": "A|a;B|b", "series": two}), (bar, {"series": bar_series})):
        default = ok(tool, **kwargs)
        assert default.data["format"] == "both" and _names(default) == ["inputs.json", "plot.svg", "plot.png", "plot_data.json"], tool
        svg_only = ok(tool, format="svg", **kwargs)
        assert svg_only.data["format"] == "svg" and _names(svg_only) == ["inputs.json", "plot.svg", "plot_data.json"], tool
        assert "png_path" not in svg_only.data and "pypdfium2" not in svg_only.provenance.engine_versions
        assert svg_text(svg_only) == svg_text(default)


@needs_pdfium
def test_plot_data_json_is_reproducible_with_the_png_and_holds_no_job_path():
    one, two = ok(xy, series=LINE), ok(xy, series=LINE)
    assert artifact(one, "plot_data.json").read_bytes() == artifact(two, "plot_data.json").read_bytes()
    assert "path" not in plot_data(one)["png"] and one.data["png"]["path"] == one.data["png_path"]


@needs_pdfium
def test_panels_and_bar_take_the_format_too_and_the_size_follows_the_svg_aspect():
    bode = call(panels, panels="Gain|mag|dB;Phase|ph|deg", series=json.dumps([{"name": "mag", "x": [10, 100, 1000], "y": [0, -3, -20]},
                                                                             {"name": "ph", "x": [10, 100, 1000], "y": [0, -45, -85]}]),
                x_scale="log", width=900, height=700, format="png")
    assert bode.ok, bode.error
    assert decode_png(artifact(bode, "plot.png")).shape == (1245, 1600, 3)  # 1600 / 900 x 700 = 1244.4, rounded up
    hist = call(bar, mode="histogram", series=json.dumps([{"name": "v", "y": [1, 2, 2, 3, 3, 3, 4]}]), bins=4, format="both")
    assert hist.ok and decode_png(artifact(hist, "plot.png")).shape == (900, 1600, 3) and artifact(hist, "plot.svg").exists()


def test_the_size_rule():
    import math

    assert _png.scale_for(800, 500) == pytest.approx(2.0) and _png.scale_for(200, 200) == pytest.approx(8.0)  # 1600 px wide
    assert _png.scale_for(1600, 500) == pytest.approx(1.0) and _png.scale_for(2400, 2400) == pytest.approx(1.0)
    assert _png.scale_for(200, 2400) == pytest.approx(4000 / 2400)  # the long side is at most 4000 px
    for width in range(200, 2401):  # PDFium's ceil(width x scale) is exactly 1600 (or the SVG's own width), never one more
        assert math.ceil(width * _png.scale_for(width, 200)) == max(1600, width), width


@needs_pdfium
def test_a_wide_svg_keeps_its_own_width():
    r = call(xy, series=LINE, width=2400, height=300, format="png")
    assert r.ok and (r.data["png_width"], r.data["png_height"]) == (2400, 300)


# ----------------------------------------------------------------------------------------------- the extra is not installed


@pytest.mark.parametrize("fmt", ["png", "both"])
def test_without_pypdfium2_the_svg_comes_back_with_a_warning_that_names_the_install_command(monkeypatch, fmt):
    monkeypatch.setitem(sys.modules, "pypdfium2", None)
    for tool, kwargs in ((xy, {"series": LINE}), (panels, {"panels": "A|a;B|b", "series": json.dumps([{"name": "a", "x": [1, 2], "y": [1, 2]}, {"name": "b", "x": [1, 2], "y": [2, 1]}])}),
                         (bar, {"series": json.dumps([{"name": "a", "x": ["p", "q"], "y": [1, 2]}])})):
        r = call(tool, format=fmt, **kwargs)
        assert r.ok and r.review_status == "unreviewed"
        assert artifact(r, "plot.svg").exists() and not any(a.path.endswith("plot.png") for a in r.artifacts)
        assert r.data["png_path"] is None and r.data["format"] == "svg" and r.data["svg_inline"].startswith("<svg")
        error = r.data["png"]["error"]
        assert r.data["png"]["written"] is False and error["code"] == "engine.unavailable" and error["install"] == "uv sync --extra png" and error["module"] == "pypdfium2"
        text = " ".join(r.warnings)
        assert "NO PNG WAS WRITTEN" in text and "uv sync --extra png" in text
        assert plot_data(r)["png"]["error"]["install"] == "uv sync --extra png"


def test_format_svg_never_touches_pypdfium2(monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdfium2", None)
    r = ok(xy, series=LINE, format="svg")
    assert "png" not in r.data and "NO PNG" not in " ".join(r.warnings)


def test_the_default_without_pypdfium2_still_returns_the_svg_and_says_the_png_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdfium2", None)
    r = ok(xy, series=LINE)
    assert artifact(r, "plot.svg").exists() and r.data["png_path"] is None and r.data["format"] == "svg"
    assert "NO PNG WAS WRITTEN" in " ".join(r.warnings) and "uv sync --extra png" in " ".join(r.warnings)


def test_an_unknown_format_is_refused_at_the_format_pointer():
    for tool, kwargs in ((xy, {"series": LINE}), (bar, {"series": json.dumps([{"name": "a", "x": ["p"], "y": [1]}])})):
        r = fails(tool, "input.invalid", "format", format="pdf", **kwargs)
        assert "svg" in r.error.message and "png" in r.error.message and "both" in r.error.message


# ----------------------------------------------------------------------------------------------- the converter


def test_the_pdf_is_a_valid_deterministic_one_page_document():
    svg = svg_text(ok(xy, series=LINE, title="Duty (θ ≤ 5 Ω)", markers=True))
    first = _svgpdf.svg_to_pdf(svg)
    assert first.pdf == _svgpdf.svg_to_pdf(svg).pdf and first.pdf.startswith(b"%PDF-1.4") and first.pdf.rstrip().endswith(b"%%EOF")
    assert (first.width, first.height, first.substituted, first.cjk) == (800, 500, 0, False)
    assert b"/Helvetica" in first.pdf and b"/Symbol" in first.pdf and b"/MSung-Light" not in first.pdf
    assert b"/Date" not in first.pdf and b"/ID" not in first.pdf and b"/Producer" not in first.pdf  # nothing that varies by run


def test_text_is_split_into_fonts_and_what_has_no_glyph_is_counted():
    runs, bad = _svgpdf.text_runs("T (°C) θ ≤ 5 Ω −3 µm", False)
    assert bad == 0 and [r[0] for r in runs] == ["F1", "F3", "F1", "F3", "F1", "F3", "F1", "F3", "F1"]
    assert b"\xb0" in runs[0][1] and runs[1][1] == b"\x71"  # the degree sign is WinAnsi 0xB0, theta is Symbol 0x71
    runs, bad = _svgpdf.text_runs("ok \U0001F642 �", False)
    assert bad == 2 and b"?" in runs[0][1]
    runs, bad = _svgpdf.text_runs("溫度", True)
    assert bad == 0 and runs == [("F4", "溫度".encode("utf-16-be"), 2000.0)]


def test_helvetica_widths_are_the_adobe_ones():
    assert _svgpdf.char_width("A", False) == 667 and _svgpdf.char_width("i", False) == 222 and _svgpdf.char_width("m", False) == 833
    assert _svgpdf.char_width("A", True) == 722 and _svgpdf.char_width("-", False) == 333 and _svgpdf.char_width(" ", True) == 278
    assert sum(_svgpdf.char_width(c, False) for c in "Hello") == 722 + 556 + 222 + 222 + 556


def test_a_path_with_relative_arcs_becomes_four_curves_that_end_where_the_svg_arc_ends():
    ops = _svgpdf.path_ops("M10 20a3.5 3.5 0 1 0 7 0a3.5 3.5 0 1 0 -7 0Z").splitlines()
    assert ops[0] == "10 20 m" and ops[-1] == "h" and sum(op.endswith(" c") for op in ops) == 4
    assert ops[2].endswith("17 20 c")  # the first half circle (two curves) ends at (17, 20)
    assert _svgpdf.path_ops("M0 0H10V10h-10Z").splitlines() == ["0 0 m", "10 0 l", "10 10 l", "0 10 l", "h"]
    quarter = _svgpdf.arc_curves(0, 0, 10, 10, 0, False, True, 10, 10)
    assert len(quarter) == 1 and quarter[0][4:] == (10, 10) and quarter[0][0] == pytest.approx(5.5228, abs=1e-3)


def test_what_the_converter_does_not_know_raises_instead_of_vanishing():
    head = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    for body in ('<image x="0" y="0"/>', '<path d="M0 0Q5 5 10 10"/>', '<text x="1" y="1" transform="scale(2)">a</text>', '<rect x="0" y="0" width="1" height="1" fill="red"/>',
                 '<rect x="0" y="0" width="1" height="1" clip-path="url(#nope)"/>'):
        with pytest.raises(ValueError):
            _svgpdf.svg_to_pdf(head + body + "</svg>")
    assert _svgpdf.svg_to_pdf(head + "</svg>").width == 100


def test_every_chart_of_every_plot_option_converts(monkeypatch):
    from eng_toolkit.domains.plot import _svg

    seen = []
    original = _svg.Canvas.document

    def converting(self, title, desc):
        svg = original(self, title, desc)
        seen.append(_svgpdf.svg_to_pdf(svg).width)
        return svg

    monkeypatch.setattr(_svg.Canvas, "document", converting)
    x = list(range(1, 41))
    series = json.dumps([{"name": "a", "x": x, "y": [v * v for v in x]}, {"name": "b", "x": x, "y": [-v for v in x]}])
    for kind in ("line", "scatter", "step", "stem"):
        ok(xy, series=series, kind=kind, y2_columns="b", fill="none", hlines="100,limit", vlines="20,mid", band="-10,10,ok", marker="10,100,duty", x_scale="log")
    ok(xy, series=series, fill="zero", markers=True, theme="print", legend="right", title="t", x_label="x", y_label="y")
    ok(panels, panels="A|a;B|b", series=series, kind="stem", x_scale="log", title="t")
    ok(bar, series=json.dumps([{"name": "a", "x": ["p", "q"], "y": [1, -2], "err": [0.1, 0.2]}]), value_labels=True)
    ok(bar, series=json.dumps([{"name": "a", "x": ["p", "q"], "y": [1, 2]}, {"name": "b", "x": ["p", "q"], "y": [1, -2]}]), stacking="stacked", y_range="0,1")
    ok(bar, mode="histogram", series=json.dumps([{"name": "v", "y": [1, 2, 2, 3]}]), bins=3, histogram="density")
    assert len(seen) == 9


@needs_pdfium
def test_hostile_text_and_wide_characters_do_not_break_the_picture():
    title = "<script>alert(1)</script> & \"quotes\" \U0001F642 溫度 \x00‮"
    r = call(xy, series=LINE, title=title, x_label="θ (°)", y_label="ω (rad/s) Ω", format="png")
    assert r.ok, r.error
    assert decode_png(artifact(r, "plot.png")).shape == (1000, 1600, 3)
    assert any("drawn as '?'" in w for w in r.warnings) and any("CJK" in w for w in r.warnings)


@needs_pdfium
def test_a_character_with_no_glyph_is_counted_in_a_warning_and_the_svg_keeps_it():
    r = call(xy, series=LINE, title="smile \U0001F642", format="both")
    assert "\U0001F642" in svg_text(r)
    assert any("1 character(s) have no glyph" in w for w in r.warnings)


# ----------------------------------------------------------------------------------------------- findings fixed with the picture


@pytest.mark.parametrize(("name", "kind", "xs", "extra"), [
    ("0..100 data", "line", list(range(0, 101, 10)), {"markers": True}),
    ("0..300 data", "scatter", list(range(0, 301, 30)), {}),
    ("a time axis from 0", "scatter", [0, 600, 1200, 1800, 2400, 3000, 3600], {}),
])
def test_all_non_negative_data_do_not_get_a_negative_axis(name, kind, xs, extra):
    r = ok(xy, series=json.dumps([{"name": "a", "x": xs, "y": [1.0 + 2 * v for v in xs]}]), kind=kind, **extra)
    axes = plot_data(r)["panels"][0]["axes"]
    assert axes["x"]["min"] == 0 and axes["x"]["ticks"][0] == 0 and axes["y"]["min"] >= 0, name
    assert axes["x"]["max"] >= xs[-1] and axes["y"]["max"] >= 1.0 + 2 * xs[-1]


def test_the_zero_anchor_also_holds_for_stems_for_non_positive_data_and_for_the_right_axis():
    stems = ok(xy, series=json.dumps([{"name": "s", "x": [1, 2, 3], "y": [1, 0.5, 0.25]}]), kind="stem")
    assert plot_data(stems)["panels"][0]["axes"]["y"]["min"] == 0
    neg = ok(xy, series=json.dumps([{"name": "n", "x": [1, 2, 3], "y": [-0.1, -5, -3]}]), kind="scatter")
    ay = plot_data(neg)["panels"][0]["axes"]["y"]
    assert ay["max"] == 0 and ay["min"] <= -5
    right = ok(xy, series=json.dumps([{"name": "a", "x": [0, 1, 2], "y": [1, 2, 3]}, {"name": "b", "x": [0, 1, 2], "y": [10, 20, 30]}]), y2_columns="b", kind="scatter")
    assert plot_data(right)["panels"][0]["axes"]["y2"]["min"] >= 0


def test_data_that_cross_zero_and_data_that_stay_positive_keep_their_margin():
    cross = ok(xy, series=json.dumps([{"name": "a", "x": [-5, 5], "y": [-5, 5]}]), kind="scatter")
    assert plot_data(cross)["panels"][0]["axes"]["x"]["min"] < -5
    high = ok(xy, series=json.dumps([{"name": "a", "x": [1000, 1100], "y": [20, 30]}]), kind="scatter")
    assert plot_data(high)["panels"][0]["axes"]["x"]["min"] > 0 and plot_data(high)["panels"][0]["axes"]["x"]["min"] <= 1000


S2_CATEGORIES = [f"C{i}" for i in range(1, 14)]
S2_UP = [-25.445, 23.625, 10.085, -6.3, 0.0, 3.9, -12.8, 17.333, 8.2, -3.1, 14.9, -19.66, 5.5]
S2_DOWN = [-6.3, 3.9, 0.0, -25.445, 11.1, 2.2, 9.9, -7.7, 20.5, 1.1, -4.4, 6.6, -15.15]


def test_thirteen_categories_in_two_series_keep_every_value_label_with_fewer_digits():
    r = ok(bar, series=json.dumps([{"name": "up", "x": S2_CATEGORIES, "y": S2_UP}, {"name": "down", "x": S2_CATEGORIES, "y": S2_DOWN}]), value_labels=True)
    svg = svg_text(r)
    assert not any("left out" in w for w in r.warnings)
    labels = [t.text for t in elements(svg, "text") if t.get("font-size") == "11" and t.get("fill") == "#1F2328"]  # value labels: 11 px, foreground colour
    assert len(labels) == 26  # one per bar, none dropped
    assert {"-25", "24", "-6.3", "3.9", "17", "8.2", "-3.1", "-20", "5.5", "-13"} <= set(labels)  # -25.445, 23.625, ... at two significant digits
    assert any("2 significant digits" in w for w in r.warnings)


def test_value_labels_keep_four_digits_where_they_fit_and_go_vertical_where_nothing_else_fits():
    wide = ok(bar, series=json.dumps([{"name": "a", "x": ["p", "q"], "y": [-25.4456, 3.14159]}]), value_labels=True)
    assert {"-25.45", "3.142"} <= set(texts(svg_text(wide))) and not any("significant digits" in w for w in wide.warnings)
    cats = [f"c{i}" for i in range(40)]
    narrow = ok(bar, series=json.dumps([{"name": "a", "x": cats, "y": [10.5 + i for i in range(40)]}]), value_labels=True, width=400, height=500, y_range="0,100")
    rotated = [t for t in elements(svg_text(narrow), "text") if (t.get("transform") or "").startswith("rotate(-90") and (t.text or "")[:1].isdigit() and t.get("font-size") == "11"]
    assert len(rotated) == 40 and all(t.get("text-anchor") is None for t in rotated)  # anchor start is the SVG default
    assert any("written vertically" in w for w in narrow.warnings)
    below = ok(bar, series=json.dumps([{"name": "a", "x": cats, "y": [-(10.5 + i) for i in range(40)]}]), value_labels=True, width=400, height=500, y_range="-100,0")
    ends = [t for t in elements(svg_text(below), "text") if (t.get("transform") or "").startswith("rotate(-90") and (t.text or "").startswith("-")]
    assert len(ends) == 40 and all(t.get("text-anchor") == "end" for t in ends)


@pytest.mark.parametrize("tool, key, kwargs", [
    (xy, "x_range", {"series": LINE}), (xy, "y_range", {"series": LINE}), (panels, "x_range", {"panels": "A|a;B|b", "series": json.dumps([{"name": "a", "x": [1, 2], "y": [1, 2]}, {"name": "b", "x": [1, 2], "y": [2, 1]}])}),
    (bar, "y_range", {"series": json.dumps([{"name": "a", "x": ["p"], "y": [1]}])}),
])
def test_a_range_with_unit_text_is_refused_with_the_way_to_write_it(tool, key, kwargs):
    r = fails(tool, "input.invalid", key, **{key: "0,10 s"}, **kwargs)
    assert "plain" in r.error.message and "unit" in r.error.message and "axis label" in r.error.message and "'0,3600'" in r.error.message
    assert ok(tool, **{key: "0,3600"}, **kwargs).ok
    assert "plain" in fails(tool, "input.invalid", key, **{key: "0,1 kHz"}, **kwargs).error.message


def test_the_parameter_descriptions_say_that_ranges_are_plain_numbers():
    import asyncio

    from fastmcp import Client

    from eng_toolkit.gateway import gateway

    async def read():
        async with Client(gateway) as client:
            return {t.name: t for t in await client.list_tools()}

    tools = asyncio.run(read())
    for name, key in (("plot_xy", "x_range"), ("plot_xy", "y_range"), ("plot_panels", "x_range"), ("plot_bar", "y_range")):
        text = tools[name].input_schema["properties"][key]["description"]
        assert "plain numbers" in text and "no unit text" in text and "e.g. '" in text
    assert "format" in tools["plot_xy"].input_schema["properties"] and "png" in tools["plot_bar"].input_schema["properties"]["format"]["description"]
    assert "marker" in tools["plot_xy"].input_schema["properties"]


# ----------------------------------------------------------------------------------------------- the marked point


def test_marker_rings_one_point_and_labels_it():
    r = ok(xy, series=json.dumps([{"name": "head", "x": [0, 10, 20, 30], "y": [30, 28, 22, 10]}]), marker="12,26.5,duty point", theme="light")
    svg = svg_text(r)
    rings = [e for e in elements(svg, "path") if e.get("stroke-width") == "2" and e.get("fill") == "none"]
    dots = [e for e in elements(svg, "circle") if e.get("r") == "2.5"]
    assert len(rings) == 1 and len(dots) == 1 and "duty point" in texts(svg)
    m = plot_data(r)["marked_point"]
    d = plot_data(r)["panels"][0]
    assert (m["x"], m["y"], m["label"]) == (12, 26.5, "duty point")
    ax, ay = d["axes"]["x"]["mapping"], d["axes"]["y"]["mapping"]
    assert m["x_px"] == pytest.approx(ax["origin_px"] + (12 - ax["min"]) * ax["scale_px_per_unit"])
    assert m["y_px"] == pytest.approx(ay["origin_px"] + (26.5 - ay["min"]) * ay["scale_px_per_unit"])
    assert float(dots[0].get("cx")) == pytest.approx(m["x_px"], abs=0.01)
    assert "marked_point" not in plot_data(ok(xy, series=LINE))


def test_marker_without_a_label_widens_the_automatic_range_and_outside_a_fixed_range_it_is_not_drawn():
    inside = ok(xy, series=LINE, marker="40,10")
    assert plot_data(inside)["panels"][0]["axes"]["x"]["max"] >= 40 and plot_data(inside)["marked_point"]["label"] == ""
    outside = ok(xy, series=LINE, marker="40,10,far", x_range="0,10")
    assert "marked_point" not in plot_data(outside) and any("marked point (40, 10) is outside" in w for w in outside.warnings)
    assert not elements(svg_text(outside), "circle")


def test_marker_refusals_name_the_marker_pointer():
    fails(xy, "input.invalid", "marker", series=LINE, marker="1")
    fails(xy, "input.invalid", "marker", series=LINE, marker="a,b,c")
    fails(xy, "input.invalid", "marker", series=json.dumps([{"name": "a", "x": [1, 10], "y": [1, 10]}]), marker="0,5", x_scale="log")
    fails(xy, "input.invalid", "marker", series=json.dumps([{"name": "a", "x": [1, 10], "y": [1, 10]}]), marker="5,-1", y_scale="log")
    fails(xy, "input.out_of_range", "marker", series=LINE, marker="1e300,5")
    assert ok(xy, series=LINE, marker="5,50,a, b with comma").ok  # the label may hold commas
