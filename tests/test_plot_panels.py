"""plot_panels: stacked panels share one x mapping, each has its own y axis; panel geometry by hand; refusals."""

import json

import pytest

from eng_toolkit.domains.plot.panels import panels
from tests.plot_testing import elements, fails, ok, plot_data, polylines, svg_text, texts

SERIES = json.dumps([
    {"name": "mag", "x": [1, 10, 100, 1000], "y": [0, -1, -20, -40]},
    {"name": "phase", "x": [1, 10, 100, 1000], "y": [-1, -10, -80, -90]},
    {"name": "spare", "x": [1, 2], "y": [1, 2]},
])


def test_two_panels_geometry_by_hand():
    r = ok(panels, series=SERIES, panels="Gain|mag|dB;Phase|phase|deg", width=800, height=600, x_scale="log", theme="light")
    d = plot_data(r)
    # top 12 (no title); bottom margin 33; two panel titles of 20 and one gap of 14:
    # each plot is (600 - 12 - 33 - 14 - 2 x 20)/2 = 250.5 px; panel 1 from 12 + 20 = 32 to 282.5, panel 2 from 282.5 + 14 + 20 = 316.5 to 567 (= 600 - 33)
    a1, a2 = d["panels"][0]["plot_area"], d["panels"][1]["plot_area"]
    assert (a1["y0"], a1["height"]) == (32, 250.5) and (a2["y0"], a2["height"]) == (316.5, 250.5)
    assert a1["x0"] == a2["x0"] and a1["width"] == a2["width"]
    # shared log x: 1 .. 1000, three decades equally spaced in both panels
    lines = polylines(svg_text(r))
    for pts in lines:
        dx = [b[0] - a[0] for a, b in zip(pts, pts[1:])]
        assert max(dx) - min(dx) < 0.02
    assert [p[0] for p in lines[0]] == [p[0] for p in lines[1]]
    assert all(282.5 >= p[1] >= 32 for p in lines[0]) and all(567 >= p[1] >= 316.5 for p in lines[1])
    assert {"Gain", "Phase", "dB", "deg"} <= set(texts(svg_text(r)))
    assert any("not drawn" in w and "spare" in w for w in r.warnings)
    assert len(elements(svg_text(r), "clipPath")) == 2


def test_x_tick_labels_only_under_the_bottom_panel():
    r = ok(panels, series=SERIES, panels="|mag;|phase", x_label="f (Hz)")
    svg = svg_text(r)
    bottom = plot_data(r)["panels"][1]["plot_area"]
    xs = [t for t in elements(svg, "text") if t.get("text-anchor") == "middle" and t.text in ("0", "200", "400", "600", "800", "1000")]
    assert xs and all(float(t.get("y")) > bottom["y0"] + bottom["height"] for t in xs)


def test_panel_with_log_y_refuses_negative_data_with_the_panel_pointer():
    fails(panels, "input.invalid", "series/0/y/0", series=SERIES, panels="A|mag||log;B|phase")


REFUSALS = [
    ({"series": SERIES, "panels": "only|mag"}, "input.invalid", "panels"),
    ({"series": SERIES, "panels": ""}, "input.missing", "panels"),
    ({"series": SERIES, "panels": ";".join(["p|mag"] * 7)}, "input.out_of_range", "panels"),
    ({"series": SERIES, "panels": "a|mag;b|nope"}, "input.invalid", "panels/1"),
    ({"series": SERIES, "panels": "a|mag;b|phase|deg|cubic"}, "input.invalid", "panels/1"),
    ({"series": SERIES, "panels": "a|mag;b|phase|deg|log|x"}, "input.invalid", "panels/1"),
    ({"series": SERIES, "panels": "a|;b|phase"}, "input.invalid", "panels/0"),
    ({"series": SERIES, "panels": "a|mag,mag;b|phase"}, "input.invalid", "panels/0"),
    ({"series": SERIES, "panels": "t" * 6000 + "|mag;b|phase"}, "input.out_of_range", "panels"),
    ({"series": SERIES, "panels": "a|mag;b|phase;c|mag;d|phase;e|mag;f|phase", "height": 300}, "input.out_of_range", "height"),
    ({"series": SERIES, "panels": "a|mag;b|phase", "x_column": "f"}, "input.invalid", "x_column"),
    ({"series": SERIES, "panels": "a|mag;b|phase", "kind": "bar"}, "input.invalid", "kind"),
]


@pytest.mark.parametrize("args,family,param", REFUSALS, ids=[f"{i}-{p}" for i, (_, _, p) in enumerate(REFUSALS)])
def test_panel_refusals(args, family, param):
    fails(panels, family, param, **args)


def test_six_panels_fit_and_twenty_series_cap():
    many = json.dumps([{"name": f"s{i}", "y": [i, i + 1]} for i in range(21)])
    fails(panels, "input.out_of_range", "panels", series=many, panels=";".join(f"p{k}|" + ",".join(f"s{i}" for i in range(k * 4, k * 4 + 4)) for k in range(5)) + ";p5|s20")
    six = ok(panels, series=SERIES, panels=";".join(["a|mag", "b|phase"] * 3), height=900)
    assert len(plot_data(six)["panels"]) == 6
