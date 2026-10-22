"""plot_bar: bar geometry by hand, grouped and stacked bars, error bars, histograms (counts, density, cumulative, edges)."""

import json

import pytest

from eng_toolkit.domains.plot.bar import bar
from tests.plot_testing import elements, fails, ok, plot_data, svg_text, texts


def bars_of(svg: str) -> list[tuple[float, float, float, float]]:
    """The data bars (rects with a fill opacity of 0.85 or a histogram's bg stroke), as (x, y, width, height)."""
    out = []
    for el in elements(svg, "rect"):
        if el.get("fill-opacity") == "0.85" or (el.get("stroke-width") == "0.75"):
            if el.get("width") != "14":  # not a legend swatch
                out.append(tuple(float(el.get(k)) for k in ("x", "y", "width", "height")))
    return out


def test_bar_rectangles_land_where_the_layout_constants_put_them():
    r = ok(bar, series=json.dumps([{"name": "a", "x": ["A", "B"], "y": [2, 4]}]), width=400, height=300, theme="light")
    # top 12, bottom margin 12 + 21 = 33 -> plot height 255, bottom 267.  y: 0 .. 4, target round(255/45) = 6 -> raw 0.67 -> step 1;
    # widest label '4' = 7.2 -> 8 px; left = 12 + 5 + 4 + 8 = 29; right = 12; plot width = 400 - 29 - 12 = 359.
    # band = 359/2 = 179.5; bars fill 80 %: 143.6 wide, offset (179.5 - 143.6)/2 = 17.95.
    # A: x = 29 + 17.95 = 46.95, top = 267 - 2 x 255/4 = 139.5, height 127.5.  B: x = 29 + 179.5 + 17.95 = 226.45, top 12, height 255.
    assert bars_of(svg_text(r)) == [(46.95, 139.5, 143.6, 127.5), (226.45, 12.0, 143.6, 255.0)]
    assert r.value.quantity == 2 and {"A", "B"} <= set(texts(svg_text(r)))
    d = plot_data(r)
    assert d["plot_area"] == {"x0": 29, "y0": 12, "width": 359, "height": 255}
    assert d["axes"]["y"]["ticks"] == [0, 1, 2, 3, 4]


def test_grouped_bars_with_error_bars_and_value_labels():
    series = [{"name": "2025", "x": ["P", "Q"], "y": [3, -2], "err": [0.5, 0.25]}, {"name": "2026", "x": ["P", "Q"], "y": [4, 1]}]
    r = ok(bar, series=json.dumps(series), value_labels=True)
    svg = svg_text(r)
    assert len(bars_of(svg)) == 4 and r.value.quantity == 4
    assert {"3", "-2", "4", "1", "2025", "2026"} <= set(texts(svg))
    err = [p for p in elements(svg, "path") if p.get("stroke-width") == "1.25"]
    assert len(err) == 1 and err[0].get("d").count("V") == 2  # two error bars, each one vertical stroke and two caps
    y = plot_data(r)["axes"]["y"]
    assert y["min"] <= -2.25 and y["max"] >= 3.5  # the axis covers the error bars
    assert plot_data(r)["legend"]["entries"] == ["2025", "2026"]


def test_stacked_bars_stack_positive_up_and_negative_down():
    series = [{"name": "a", "x": ["P"], "y": [3]}, {"name": "b", "x": ["P"], "y": [2]}, {"name": "c", "x": ["P"], "y": [-1]}]
    r = ok(bar, series=json.dumps(series), stacking="stacked", value_labels=True, legend="off")
    d = plot_data(r)
    my = d["axes"]["y"]["mapping"]
    px = lambda v: my["origin_px"] + (v - my["min"]) * my["scale_px_per_unit"]  # noqa: E731
    rects = bars_of(svg_text(r))
    # a: 0 .. 3, b: 3 .. 5, c: 0 .. -1; total label 4
    assert rects[0][1] == pytest.approx(px(3), abs=0.006) and rects[0][1] + rects[0][3] == pytest.approx(px(0), abs=0.01)
    assert rects[1][1] == pytest.approx(px(5), abs=0.006) and rects[1][1] + rects[1][3] == pytest.approx(px(3), abs=0.01)
    assert rects[2][1] == pytest.approx(px(0), abs=0.006) and rects[2][1] + rects[2][3] == pytest.approx(px(-1), abs=0.01)
    assert "4" in texts(svg_text(r))


def test_histogram_counts_density_and_cumulative_by_hand():
    values = json.dumps([{"name": "v", "y": [0, 1, 1, 2, 2, 2, 3]}])
    # 3 equal bins over 0 .. 3: [0,1) = {0}, [1,2) = {1,1}, [2,3] = {2,2,2,3} (the last bin is closed)
    r = ok(bar, mode="histogram", series=values, bins=3)
    h = r.data["histogram"]
    assert h["bin_edges"] == [0, 1, 2, 3] and h["counts"] == [1, 2, 4] and h["heights"] == [1, 2, 4]
    assert r.value.quantity == 3
    d = ok(bar, mode="histogram", series=values, bins=3, histogram="density").data["histogram"]
    assert d["heights"] == pytest.approx([1 / 7, 2 / 7, 4 / 7])  # count / (7 values x width 1): integrates to 1
    c = ok(bar, mode="histogram", series=values, bins=3, histogram="cumulative").data["histogram"]
    assert c["heights"] == [1, 3, 7]
    auto = ok(bar, mode="histogram", series=values).data["histogram"]
    assert len(auto["counts"]) == 4 and "Sturges" in auto["rule"]  # ceil(log2 7 = 2.81) + 1 = 4


def test_histogram_with_explicit_edges_reports_values_outside():
    r = ok(bar, mode="histogram", series=json.dumps([{"name": "v", "y": [0, 1, 1, 2, 2, 2, 3]}]), bin_edges="1,2,3")
    h = r.data["histogram"]
    assert h["counts"] == [2, 4] and h["below_first_edge"] == 1 and h["above_last_edge"] == 0
    assert any("below the first edge" in w for w in r.warnings)
    # the bars sit exactly on the edges: x axis is the edge range 1 .. 3
    x = r.data["axes"]["x"]["mapping"]
    rects = bars_of(svg_text(r))
    assert rects[0][0] == pytest.approx(x["origin_px"], abs=0.006) and rects[1][0] + rects[1][2] == pytest.approx(x["origin_px"] + 2 * x["scale_px_per_unit"], abs=0.01)


def test_histogram_of_a_constant_column_is_padded(tmp_path):
    path = tmp_path / "c.csv"
    path.write_text("v\n5\n5\n5\n", encoding="utf-8")
    r = ok(bar, mode="histogram", data_file=str(path), bins=2)
    assert r.data["histogram"]["bin_edges"] == [4.5, 5.0, 5.5] and r.data["histogram"]["counts"] == [0, 3]


def test_csv_bars_with_text_categories_and_error_columns(tmp_path):
    path = tmp_path / "parts.csv"
    path.write_text("part,q1,q1_err\nPump <A>,3,0.5\nValve & co,4,0.25\n", encoding="utf-8")
    r = ok(bar, data_file=str(path), y_columns="q1", err_columns="q1_err", value_labels=True)
    svg = svg_text(r)
    assert {"Pump <A>", "Valve & co"} <= set(texts(svg)) and "Pump &lt;A&gt;" in svg
    assert r.data["series"][0]["err"] == [0.5, 0.25]


REFUSALS = [
    ({"mode": "pie", "series": "[]"}, "input.invalid", "mode"),
    ({"mode": "histogram", "series": '[{"y": [1, 2]}]', "bins": 201}, "input.out_of_range", "bins"),
    ({"mode": "histogram", "series": '[{"y": [1, 2]}]', "bins": 1}, "input.invalid", "bins"),
    ({"mode": "histogram", "series": '[{"y": [1, 2]}]', "bins": 3, "bin_edges": "0,1,2"}, "input.invalid", "bin_edges"),
    ({"mode": "histogram", "series": '[{"y": [1, 2]}]', "bin_edges": "0,2,1"}, "input.invalid", "bin_edges"),
    ({"mode": "histogram", "series": '[{"y": [1, 2]}]', "bin_edges": ",".join(str(i) for i in range(203))}, "input.out_of_range", "bin_edges"),
    ({"mode": "histogram", "series": '[{"y": [1, 2]}, {"y": [3]}]'}, "input.out_of_range", "series"),
    ({"mode": "histogram", "series": '[{"y": [1, 2]}]', "histogram": "pdf"}, "input.invalid", "histogram"),
    ({"series": json.dumps([{"name": f"s{i}", "x": ["a"], "y": [1]} for i in range(7)])}, "input.out_of_range", "series"),
    ({"series": json.dumps([{"name": "a", "x": ["a", "b"], "y": [1, 2]}, {"name": "b", "x": ["b", "a"], "y": [1, 2]}])}, "input.invalid", "series/1/x"),
    ({"series": json.dumps([{"name": "a", "x": ["a"], "y": [1], "err": [-1]}])}, "input.invalid", "series/0/err/0"),
    ({"series": json.dumps([{"name": "a", "x": ["a"], "y": [1], "err": [1]}, {"name": "b", "x": ["a"], "y": [1]}]), "stacking": "stacked"}, "input.invalid", "series"),
    ({"series": json.dumps([{"name": "a", "x": [str(i) for i in range(201)], "y": [1] * 201}])}, "input.out_of_range", "series/0/x"),
    ({"series": json.dumps([{"name": "a", "x": [True], "y": [1]}])}, "input.invalid", "series/0/x/0"),
    ({"series": json.dumps([{"name": "a", "x": ["a"], "y": [1]}]), "bins": 5}, "input.invalid", "bins"),
    ({"series": json.dumps([{"name": "a", "x": ["a"], "y": [1]}]), "y_range": "2,1"}, "input.invalid", "y_range"),
    ({"series": json.dumps([{"name": "a", "x": ["a"], "y": [1]}]), "title": "t" * 6000}, "input.out_of_range", "title"),
    ({"series": json.dumps([{"name": "a", "x": ["a", "b"], "y": [1e-120, -2e-120]}])}, "input.out_of_range", "series"),  # rescale
    ({"series": json.dumps([{"name": "a", "x": ["a"], "y": [1]}]), "width": 2401}, "input.out_of_range", "width"),
]


@pytest.mark.parametrize("args,family,param", REFUSALS, ids=[f"{i}-{p}" for i, (_, _, p) in enumerate(REFUSALS)])
def test_bar_refusals(args, family, param):
    fails(bar, family, param, **args)
