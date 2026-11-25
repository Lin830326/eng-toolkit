"""plot_xy: geometry derived by hand from the layout constants, fills, log axes, decimation, determinism, hostile text, refusals."""

import hashlib
import json
import math
import time

import numpy as np
import pytest

from eng_toolkit.domains.plot.xy import xy
from tests.plot_testing import artifact, elements, fails, ok, plot_data, polylines, svg_text, texts
from timing import budget


def js(*series) -> str:
    return json.dumps([{"name": n, "x": list(x), "y": list(y)} for n, x, y in series])


LINE = js(("a", [0, 5, 10], [0, 50, 100]))


def test_a_known_point_maps_to_the_documented_pixel():
    r = ok(xy, series=LINE, theme="light")
    # width 800, height 500, no title or labels, one series (no legend).
    # x: target round((800 - 24 - 60)/90) = 8; span 10 -> raw 1.25 -> step 2; ticks 0..10, labels '0' .. '10'.
    # vertical: top = PAD 12; bottom margin = 12 + 21 = 33; plot height = 500 - 12 - 33 = 455; bottom = 467.
    # y: target round(455/45) = 10; span 100 -> step 10; ticks 0..100; widest label '100' = 3 x 0.6 x 12 = 21.6 -> 22 px.
    # left = 12 + 5 + 4 + 22 = 43; right = max(12, ceil(14.4/2) + 2 = 10) = 12; plot width = 800 - 43 - 12 = 745.
    # (5, 50): x = 43 + 5 x 745/10 = 415.5; y = 467 - 50 x 455/100 = 239.5
    svg = svg_text(r)
    assert polylines(svg) == [[(43.0, 467.0), (415.5, 239.5), (788.0, 12.0)]]
    d = plot_data(r)
    area = d["panels"][0]["plot_area"]
    assert area == {"x0": 43, "y0": 12, "width": 745, "height": 455.0}
    mx, my = d["panels"][0]["axes"]["x"]["mapping"], d["panels"][0]["axes"]["y"]["mapping"]
    assert mx["origin_px"] + (5 - mx["min"]) * mx["scale_px_per_unit"] == pytest.approx(415.5)
    assert my["origin_px"] + (50 - my["min"]) * my["scale_px_per_unit"] == pytest.approx(239.5)
    assert d["panels"][0]["axes"]["x"]["ticks"] == [0, 2, 4, 6, 8, 10]
    assert d["panels"][0]["axes"]["y"]["tick_labels"][-1] == "100"
    assert r.value.quantity == 3 and r.values["points_in"].quantity == 3
    assert r.data["svg_inline"] == svg and r.data["svg_bytes"] == len(svg.encode())


def test_svg_structure_title_desc_viewbox_and_clip():
    r = ok(xy, series=LINE, title="Deflection", x_label="x (m)", y_label="w (mm)")
    svg = svg_text(r)
    tree = elements(svg, "svg")[0]
    assert tree.get("viewBox") == "0 0 800 500" and tree.get("role") == "img"
    assert elements(svg, "title")[0].text == "Deflection"
    assert "Line chart of 1 series ('a')" in elements(svg, "desc")[0].text
    assert len(elements(svg, "clipPath")) == 1
    groups = [g for g in elements(svg, "g") if g.get("clip-path")]
    assert groups and all(g.get("clip-path").startswith("url(#etk-") for g in groups)
    assert {"Deflection", "x (m)", "w (mm)"} <= set(texts(svg))


def test_fill_to_zero_has_two_tints_clipped_at_the_zero_line():
    r = ok(xy, series=js(("V", [0, 1, 2], [10, 0, -10])), fill="zero", theme="light")
    svg = svg_text(r)
    d = plot_data(r)
    my = d["panels"][0]["axes"]["y"]["mapping"]
    zero = my["origin_px"] + (0 - my["min"]) * my["scale_px_per_unit"]
    # y data -10 .. 10 with 0 included: ticks symmetric, so 0 sits mid-height: 12 + 455/2 = 239.5
    assert zero == pytest.approx(239.5)
    fills = [p for p in elements(svg, "path") if p.get("fill-opacity") in ("0.3", "0.14")]
    assert [p.get("fill-opacity") for p in fills] == ["0.3", "0.14"]
    clips = {c.get("id"): c for c in elements(svg, "clipPath")}
    pos = next(c for k, c in clips.items() if k.endswith("pos0"))[0]
    neg = next(c for k, c in clips.items() if k.endswith("neg0"))[0]
    assert float(pos.get("y")) + float(pos.get("height")) == pytest.approx(239.5) and float(neg.get("y")) == pytest.approx(239.5)
    zero_lines = [ln for ln in elements(svg, "line") if ln.get("y1") == ln.get("y2") == "239.5"]
    assert zero_lines


def test_log_axes_put_decades_at_equal_spacing():
    r = ok(xy, series=js(("g", [1, 10, 100, 1000], [1, 10, 100, 1000])), x_scale="log", y_scale="log")
    pts = polylines(svg_text(r))[0]
    dx = [b[0] - a[0] for a, b in zip(pts, pts[1:])]
    dy = [b[1] - a[1] for a, b in zip(pts, pts[1:])]
    assert max(dx) - min(dx) < 0.02 and max(dy) - min(dy) < 0.02 and dx[0] > 0 and dy[0] < 0
    axes = plot_data(r)["panels"][0]["axes"]
    assert axes["x"]["ticks"] == [1, 10, 100, 1000] and axes["x"]["mapping"]["transform"] == "log10"


def test_log_axis_refuses_nonpositive_data_with_a_pointer():
    r = fails(xy, "input.invalid", "series/0/x/1", series=js(("g", [1, 0, 3], [1, 2, 3])), x_scale="log")
    assert "log" in r.error.message
    fails(xy, "input.invalid", "series/1/y/2", series=js(("a", [1, 2, 3], [1, 2, 3]), ("b", [1, 2, 3], [1, 2, -3])), y_scale="log")


def test_decimation_preserves_the_extrema_exactly_and_is_disclosed():
    x = np.linspace(0.0, 1.0, 200_000)
    y = np.sin(2 * np.pi * 50 * x) + 0.1 * np.cos(2 * np.pi * 3000 * x)
    y[123_457] = 3.25  # a single spike must survive
    r = ok(xy, series=json.dumps([{"name": "s", "x": x.tolist(), "y": y.tolist()}]))
    s = plot_data(r)["series"][0]
    assert s["decimated"] and s["points_in"] == 200_000 and s["points_drawn"] < 4 * 760
    assert s["drawn_y_max"] == y.max() == 3.25 and s["drawn_y_min"] == y.min()
    assert any("decimated" in w for w in r.warnings)
    pts = polylines(svg_text(r))[0]
    my = plot_data(r)["panels"][0]["axes"]["y"]["mapping"]
    top = my["origin_px"] + (3.25 - my["min"]) * my["scale_px_per_unit"]
    assert min(p[1] for p in pts) == pytest.approx(top, abs=0.006)  # the spike is drawn at its pixel
    assert len(pts) == s["points_drawn"]


def test_same_input_gives_identical_bytes():
    args = dict(series=js(("a", [0, 1, 2], [3, 1, 2]), ("b", [0, 1, 2], [1, 1, 0])), title="T", hlines="2,limit", band="0.5,1.5,ok")
    one, two = ok(xy, **args), ok(xy, **args)
    a, b = artifact(one, "plot.svg").read_bytes(), artifact(two, "plot.svg").read_bytes()
    assert a == b and one.data["svg_sha256"] == hashlib.sha256(a).hexdigest()
    assert artifact(one, "plot_data.json").read_bytes() == artifact(two, "plot_data.json").read_bytes()
    other = ok(xy, **{**args, "title": "U"})
    assert artifact(other, "plot.svg").read_bytes() != a


HOSTILE_TEXT = [
    "<script>alert(1)</script>",
    '"><svg onload=alert(1)>',
    "A & B; <![CDATA[x]]> <!-- c -->",
    "unicode ünï 漢字 Ω",
]


@pytest.mark.parametrize("text", HOSTILE_TEXT)
def test_hostile_text_is_escaped_and_never_markup(text):
    r = ok(xy, series=json.dumps([{"name": text, "x": [1, 2], "y": [1, 2]}, {"name": "b", "x": [1, 2], "y": [2, 1]}]), title=text, x_label=text, y_label=text,
           hlines=f"1.5,{text.replace(';', ' ')}")
    svg = svg_text(r)
    assert "<script" not in svg and "onload=alert" not in svg.replace("&quot;&gt;&lt;svg onload=alert", "")
    assert texts(svg).count(text) >= 2  # title and x label as plain text nodes (the legend has the series name too)
    assert elements(svg, "title")[0].text == text


def test_control_characters_are_replaced_and_said_so():
    r = ok(xy, series=LINE, title="a\x07b‮c")
    assert elements(svg_text(r), "title")[0].text == "a�b�c"
    assert any("control" in w for w in r.warnings)


def test_long_labels_are_refused_quickly_and_long_fitting_text_is_truncated():
    start = time.perf_counter()
    for param in ("title", "x_label", "y_label", "y2_label"):
        fails(xy, "input.out_of_range", param, series=LINE, **{param: "<" * 6000})
    fails(xy, "input.out_of_range", "series/0/name", series=json.dumps([{"name": "n" * 6000, "y": [1, 2]}]))
    assert time.perf_counter() - start < budget(1.0)
    # a 120-character title fits nowhere in a 200 px wide chart: shortened with an ellipsis and disclosed
    r = ok(xy, series=LINE, title="W" * 120, width=200, height=200)
    assert elements(svg_text(r), "text")[0].text.endswith("…")
    assert plot_data(r)["truncated_text"][0]["where"] == "title" and any("ellipsis" in w for w in r.warnings)


def test_reference_lines_band_and_right_axis():
    r = ok(xy, series=js(("mag", [0, 1, 2], [0, -10, -20]), ("ph", [0, 1, 2], [0, -90, -180])), y2_columns="ph", y_label="dB", y2_label="deg",
           hlines="-3,-3 dB", vlines="1,fc", band="-15,-5,band", theme="print")
    svg = svg_text(r)
    d = plot_data(r)
    assert "y2" in d["panels"][0]["axes"] and d["series"][1]["axis"] == "y2"
    assert {"-3 dB", "fc", "band", "deg", "mag", "ph (right axis)"} <= set(texts(svg))
    dashed = [ln for ln in elements(svg, "line") if ln.get("stroke-dasharray") == "6 4"]
    assert len(dashed) == 2
    my = d["panels"][0]["axes"]["y"]["mapping"]
    assert any(float(ln.get("y1")) == pytest.approx(my["origin_px"] + (-3 - my["min"]) * my["scale_px_per_unit"], abs=0.006) for ln in dashed)
    assert "prefers-color-scheme" not in svg and 'stroke="#000000"' in svg


def test_auto_theme_has_css_variables_a_dark_block_and_fallback_attributes():
    svg = svg_text(ok(xy, series=LINE))
    style = elements(svg, "style")[0].text
    assert "@media (prefers-color-scheme: dark)" in style and "var(--etk-s0,#0072B2)" in style
    line = elements(svg, "polyline")[0]
    assert line.get("stroke") == "#0072B2" and "s0-s" in line.get("class")
    dark = svg_text(ok(xy, series=LINE, theme="dark"))
    assert "prefers-color-scheme" not in dark and 'fill="#15181D"' in dark


@pytest.mark.parametrize("kind", ["scatter", "step", "stem", "line,scatter"])
def test_other_kinds_draw(kind):
    r = ok(xy, series=js(("a", [1, 2, 3], [1, -2, 3]), ("b", [1, 2, 3], [0, 1, 0])), kind=kind)
    svg = svg_text(r)
    if kind == "step":
        # post step: (1,1) (2,1) (2,-2) (3,-2) (3,3) -> 5 vertices for 3 points
        assert [len(p) for p in polylines(svg)] == [5, 5]
    if kind == "stem":
        assert any(p.get("d", "").count("V") == 3 for p in elements(svg, "path"))
    if kind == "line,scatter":
        assert len(polylines(svg)) == 1 and [s["kind"] for s in plot_data(r)["series"]] == ["line", "scatter"]


def test_points_outside_a_given_range_are_clipped_and_counted():
    r = ok(xy, series=js(("a", [0, 1, 2, 3], [0, 100, 1e6, 2])), y_range="0,10")
    s = plot_data(r)["series"][0]
    assert s["points_outside_axes"] == 2 and any("clipped" in w for w in r.warnings)
    assert all(abs(v) <= 1e7 for p in polylines(svg_text(r))[0] for v in p)


def test_equal_values_are_padded_not_refused():
    r = ok(xy, series=js(("c", [0, 1, 2], [5, 5, 5])))
    assert plot_data(r)["panels"][0]["axes"]["y"]["min"] == 4.5 and any("padded" in w for w in r.warnings)
    r = ok(xy, series=js(("c", [3, 3], [5, 6])))
    assert any("x axis: all values are equal" in w for w in r.warnings)


def test_large_svg_is_not_inlined():
    rng = np.random.default_rng(3)
    series = [{"name": f"s{i}", "x": rng.random(3000).tolist(), "y": rng.random(3000).tolist()} for i in range(20)]
    r = ok(xy, series=json.dumps(series), kind="scatter")
    assert r.data["svg_bytes"] > 150_000 and r.data["svg_inline"] is None and "inline" in r.data["svg_inline_omitted"]
    assert r.values["series"].quantity == 20


REFUSALS = [
    ({"series": LINE, "kind": "pie"}, "input.invalid", "kind"),
    ({"series": LINE, "theme": "neon"}, "input.invalid", "theme"),
    ({"series": LINE, "width": 100000}, "input.out_of_range", "width"),
    ({"series": LINE, "height": 199}, "input.out_of_range", "height"),
    ({"series": LINE, "width": 10.5}, "input.invalid", "width"),
    ({"series": LINE, "x_range": "5,5"}, "input.invalid", "x_range"),
    ({"series": LINE, "x_range": "1e308,1e309"}, "input.out_of_range", "x_range"),
    ({"series": LINE, "y_range": "nan,1"}, "input.invalid", "y_range"),
    ({"series": LINE, "hlines": ";".join(["1,a"] * 11)}, "input.out_of_range", "hlines"),
    ({"series": LINE, "band": "3,1,x"}, "input.invalid", "band"),
    ({"series": LINE, "fill": "zero", "kind": "scatter"}, "input.invalid", "fill"),
    ({"series": LINE, "fill": "zero", "y_scale": "log"}, "input.invalid", "series/0/y/0"),
    ({"series": LINE, "y2_columns": "a"}, "input.invalid", "y2_columns"),
    ({"series": LINE, "y2_columns": "zz"}, "input.invalid", "y2_columns"),
    ({"series": LINE, "x_column": "x"}, "input.invalid", "x_column"),
    ({"series": LINE, "kind": "line,scatter"}, "input.invalid", "kind"),
    ({"series": json.dumps([{"name": f"s{i}", "y": [1, 2]} for i in range(21)])}, "input.out_of_range", "series"),
    ({"series": '[{"name": "a", "y": [1, NaN]}]'}, "input.invalid", "series"),
    ({"series": '[{"name": "a", "y": [1, 1e308]}]'}, "input.out_of_range", "series/0/y/1"),
    ({"series": '[{"name": "a", "y": [1, 1e999]}]'}, "input.out_of_range", "series/0/y/1"),
    ({"series": '[{"name": "a", "y": [1, "2"]}]'}, "input.invalid", "series/0/y/1"),
    ({"series": '[{"name": "a", "x": [1], "y": [1, 2]}]'}, "input.invalid", "series/0/x"),
    ({"series": '[{"name": "a", "y": [1], "z": 2}]'}, "input.invalid", "series/0/z"),
    ({"series": "[" * 5000 + "]" * 5000}, "input.invalid", "series"),
    ({"series": "__import__('os').system('x')"}, "input.invalid", "series"),
    ({"series": json.dumps([{"y": [0.0] * 200_001}])}, "input.out_of_range", "series/0/y"),
    ({}, "input.missing", "data_file"),
    ({"series": LINE, "data_file": "x.csv"}, "input.invalid", "series"),
    ({"series": json.dumps([{"name": "a", "y": [1e-120, 2e-120]}])}, "input.out_of_range", "series/0/y"),
]


@pytest.mark.parametrize("args,family,param", REFUSALS, ids=[f"{i}-{p}" for i, (_, _, p) in enumerate(REFUSALS)])
def test_refusals_carry_a_family_and_a_pointer(args, family, param):
    fails(xy, family, param, **args)


def test_a_plot_area_squeezed_below_60_px_is_refused():
    # legend outside on the right: 2 x 8 + 26 + 6 + 200 (text capped at 200 px) = 248 px + 10; width 300 leaves 300 - 43 - 12 - 258 < 60
    names = json.dumps([{"name": "n" * 100, "y": [0, 100]}, {"name": "m", "y": [100, 0]}])
    fails(xy, "input.out_of_range", "width", series=names, legend="right", width=300)
    ok(xy, series=names, legend="right", width=800)


def test_hostile_6000_character_strings_in_every_text_parameter_answer_fast():
    long = '"><script>' + "(" * 6000
    start = time.perf_counter()
    for param in ("x_column", "y_columns", "y2_columns", "kind", "fill", "x_scale", "y_scale", "x_range", "y_range", "hlines", "vlines", "band", "legend", "theme"):
        r = getattr(xy, "fn", xy)(series=LINE, **{param: long})
        assert not r.ok and r.error.details["param"].startswith("/"), param
    assert time.perf_counter() - start < budget(1.0)
    assert math.isfinite(start)
