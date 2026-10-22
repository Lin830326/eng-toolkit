"""Regression tests for the findings of the independent review (one test or more per item, named after it)."""

import json
import math
import re
import time
import tracemalloc

import numpy as np
import pytest

from eng_toolkit.domains.plot._axes import format_value, log_axis
from eng_toolkit.domains.plot.bar import bar
from eng_toolkit.domains.plot.xy import xy
from tests.plot_testing import SVG_NS, artifact, elements, fails, ok, plot_data, polylines, root, svg_text, texts


def js(*series) -> str:
    return json.dumps([{"name": n, "x": list(x), "y": list(y)} for n, x, y in series])


def write(tmp_path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return str(path)


def px(mapping: dict, v: float) -> float:
    t, t0 = (math.log10(v), math.log10(mapping["min"])) if mapping["transform"] == "log10" else (v, mapping["min"])
    return mapping["origin_px"] + (t - t0) * mapping["scale_px_per_unit"]


# ----------------------------------------------------------------------------------------------- S1, K1


def test_S1_a_lone_surrogate_in_a_series_name_is_cleaned_everywhere():
    # the JSON text is ASCII ("\udfff" is an escape), json.loads turns it into a lone surrogate
    series = '[{"name":"a\\udfffb","x":[1,2],"y":[1,2]},{"name":"c","x":[1,2],"y":[2,1]}]'
    r = ok(xy, series=series)
    assert r.data["series"][0]["column"] == "a�b" and r.data["series"][0]["name"] == "a�b"
    artifact(r, "plot_data.json").read_bytes().decode("utf-8")  # strict UTF-8: no surrogate escaped into the file
    assert "a�b" in texts(svg_text(r))
    assert any("1 text value(s)" in w for w in r.warnings)
    b = ok(bar, series='[{"name":"s","x":["a\\ud800b","c"],"y":[1,2]}]')
    assert b.data["categories"] == ["a�b", "c"]


def test_K1_controls_in_csv_headers_and_categories_are_cleaned_and_counted(tmp_path):
    path = write(tmp_path, "h.csv", 'cat,"v‮",w\n"A\x02",1,2\n"B",3,4\n')
    r = ok(bar, data_file=path, y_columns="v‮,w")
    assert r.data["categories"] == ["A�", "B"] and r.data["series"][0]["column"] == "v�"
    assert any(w.startswith("2 text value(s)") for w in r.warnings)
    h = ok(bar, mode="histogram", data_file=write(tmp_path, "hh.csv", '"<h>\x03"\n1\n2\n3\n'))
    assert h.data["histogram"]["value_column"] == "<h>�"


# ----------------------------------------------------------------------------------------------- C1


def test_C1_fill_on_the_right_axis_splits_at_the_right_axis_zero():
    xs = [0, 1, 2, 3]
    r = ok(xy, series=js(("a", xs, [10, 20, 15, 12]), ("b", xs, [100, -300, 50, -20])), y2_columns="b", fill="zero", theme="light")
    svg = svg_text(r)
    # right axis: data -300 .. 100 plus 0; target round(455/45) = 10, raw 40 -> step 50; ticks -300 .. 100;
    # zero at 467 - (0 + 300) x 455/400 = 125.75 px.  The left axis (0 .. 20 here) has its zero at the bottom, 467 px.
    y2 = plot_data(r)["panels"][0]["axes"]["y2"]["mapping"]
    assert px(y2, 0.0) == pytest.approx(125.75)
    clips = {c.get("id").split("-")[-1]: c[0] for c in elements(svg, "clipPath")}
    assert float(clips["rpos0"].get("height")) == pytest.approx(125.75 - 12) and float(clips["rneg0"].get("y")) == pytest.approx(125.75)
    fills = [p for p in elements(svg, "path") if p.get("clip-path") and "pos0" in p.get("clip-path") or (p.get("clip-path") or "").endswith("neg0)")]
    right = [p.get("clip-path") for p in fills if "#0072B2" not in p.get("fill")]
    assert right and all("-rpos0" in c or "-rneg0" in c for c in right)  # series b (second colour) uses the right-axis split
    zero_lines = [ln for ln in elements(svg, "line") if ln.get("y1") == ln.get("y2")]
    assert any(ln.get("y1") == "125.75" and ln.get("stroke-dasharray") == "4 3" for ln in zero_lines)
    assert any(ln.get("y1") == "467" and not ln.get("stroke-dasharray") for ln in zero_lines)


# ----------------------------------------------------------------------------------------------- C2


def _marker_centres(svg: str) -> np.ndarray:
    out = []
    for p in elements(svg, "path"):
        d = p.get("d", "")
        for m in re.finditer(r"M(-?[\d.]+) (-?[\d.]+)a3\.5", d):  # circle markers: start at (x - 3.5, y)
            out.append((float(m.group(1)) + 3.5, float(m.group(2))))
    return np.array(out)


def test_C2_scatter_thinning_keeps_density_not_only_the_envelope():
    n = 30_000
    x = np.linspace(0, 1, n)
    y = np.zeros(n)
    y[::10] = -1
    y[5::10] = 1
    r = ok(xy, series=json.dumps([{"name": "a", "x": x.tolist(), "y": y.tolist()}]), kind="scatter", theme="light")
    s = r.data["series"][0]
    assert s["markers_thinned"] and not s["decimated"] and "marker thinning" in s["method"]
    assert any("marker plot thinned" in w for w in r.warnings)
    assert not any("extrema kept" in w for w in r.warnings)
    assert s["drawn_y_min"] == -1.0 and s["drawn_y_max"] == 1.0
    centres = _marker_centres(svg_text(r))
    assert len(centres) == s["markers_drawn"]
    ym = plot_data(r)["panels"][0]["axes"]["y"]["mapping"]
    zero = px(ym, 0.0)
    assert np.count_nonzero(np.abs(centres[:, 1] - zero) < 0.1) > 100  # the dense y = 0 band is drawn (it had 0 markers)
    # every input point lies within 2 x sqrt(2) px of a drawn marker centre
    xm = plot_data(r)["panels"][0]["axes"]["x"]["mapping"]
    pts = np.column_stack([[px(xm, v) for v in x[::7]], [px(ym, v) for v in y[::7]]])
    nearest = np.min(np.hypot(pts[:, None, 0] - centres[None, :, 0], pts[:, None, 1] - centres[None, :, 1]), axis=1)
    assert nearest.max() <= 2 * math.sqrt(2) + 0.02


def test_C2_lines_still_use_the_envelope_and_stems_reach_every_point():
    x = np.linspace(0, 1, 20_000)
    y = np.sin(40 * x)
    r = ok(xy, series=json.dumps([{"name": "s", "x": x.tolist(), "y": y.tolist()}]), kind="stem")
    s = r.data["series"][0]
    assert s["markers_thinned"] and "stems reach every point" in s["method"]
    assert s["drawn_y_min"] == y.min() and s["drawn_y_max"] == y.max()
    line = ok(xy, series=json.dumps([{"name": "s", "x": x.tolist(), "y": y.tolist()}]), markers=True)
    ls = line.data["series"][0]
    assert ls["decimated"] and ls["markers_thinned"] and ls["line_vertices"] < 4 * 760


# ----------------------------------------------------------------------------------------------- C3


@pytest.mark.parametrize("rng", ["2,2.5", "2e-6,2.5e-6", "30,31", "0.2,0.25"])
def test_C3_narrow_log_ranges_never_repeat_or_mislabel_a_tick(rng):
    lo, hi = (float(v) for v in rng.split(","))
    ax = log_axis(lo, hi, 8, (lo, hi), param="x_range", what="x")
    assert len(ax.ticks) >= 2 and len(set(ax.labels)) == len(ax.labels)
    spacing = min(b - a for a, b in zip(ax.ticks, ax.ticks[1:]))
    for t, lab in zip(ax.ticks, ax.labels):
        assert abs(float(lab) - t) <= 1e-6 * spacing, (t, lab)
    r = ok(xy, series=js(("a", [lo, hi], [lo, hi])), x_scale="log", y_scale="log", x_range=rng, y_range=rng)
    labs = plot_data(r)["panels"][0]["axes"]["y"]["tick_labels"]
    assert len(set(labs)) == len(labs)


def test_C3_the_2_5e_minus_6_tick_is_not_labelled_3e_minus_6():
    ax = log_axis(2e-6, 2.5e-6, 8, (2e-6, 2.5e-6), param="y_range", what="y")
    assert ax.labels[-1] == "2.5e-6" and "3e-6" not in ax.labels


# ----------------------------------------------------------------------------------------------- C4


def test_C4_far_points_are_clipped_geometrically_not_clamped():
    r = ok(xy, series=js(("s", [0.5, 1e5], [0.5, 2e5])), x_range="0,1", y_range="0,1")
    d = plot_data(r)["panels"][0]
    xm, ym, top = d["axes"]["x"]["mapping"], d["axes"]["y"]["mapping"], d["plot_area"]["y0"]
    (x0, y0), (x1, y1) = (px(xm, 0.5), px(ym, 0.5)), (px(xm, 1e5), px(ym, 2e5))
    true_x_at_top = x0 + (top - y0) * (x1 - x0) / (y1 - y0)
    (a, b), (c, e) = polylines(svg_text(r))[0]
    assert (a, b) == (pytest.approx(x0, abs=0.006), pytest.approx(y0, abs=0.006))
    # the visible run ends on the clip rectangle (plot area grown by 4 px) on the exact line
    assert e == pytest.approx(top - 4, abs=0.006)
    drawn_x_at_top = a + (top - b) * (c - a) / (e - b)
    assert drawn_x_at_top == pytest.approx(true_x_at_top, abs=0.02)  # was 41 px off with per-coordinate clamping


def test_C4_a_polyline_leaving_and_reentering_is_split_into_runs():
    r = ok(xy, series=js(("s", [0, 1, 2, 3, 4], [0, 50, 0, 50, 0])), y_range="0,10")
    runs = polylines(svg_text(r))
    assert len(runs) == 3 and all(len(run) >= 2 for run in runs)  # up out, down in, up out, down in
    area = plot_data(r)["panels"][0]["plot_area"]
    for run in runs:
        for x, y in run:
            assert area["y0"] - 4.006 <= y <= area["y0"] + area["height"] + 4.006


# ----------------------------------------------------------------------------------------------- C5, K2, C7, C8


def test_C5_bars_beyond_a_fixed_range_are_warned_and_marked():
    r = ok(bar, series=json.dumps([{"name": "a", "x": ["p", "q"], "y": [5, 1]}]), y_range="0,2")
    d = r.data
    assert d["bars"][0]["clipped"] is True and d["bars"][1]["clipped"] is False and d["clipped_bars"] == 1
    assert any("beyond the y range" in w and "values 5" in w for w in r.warnings)
    marks = [p for p in elements(svg_text(r), "path") if p.get("d", "").endswith("Z") and p.get("d", "").count("L") == 2]
    assert marks  # the triangle at the frame
    rect = [el for el in elements(svg_text(r), "rect") if el.get("fill-opacity") == "0.85"][0]
    assert float(rect.get("y")) >= d["plot_area"]["y0"] - 4.006  # cut at the frame, not drawn far above


def test_K2_zero_height_bars_are_not_counted_as_drawn():
    r = ok(bar, series=json.dumps([{"name": "a", "x": ["p", "q"], "y": [0, 1]}]))
    assert r.value.quantity == 1 and r.values["zero_height_bars"].quantity == 1 and r.data["bars"][0]["zero_height"] is True
    assert svg_text(r).count('fill-opacity="0.85"') == 1


def test_C7_value_labels_keep_their_digits():
    r = ok(bar, series=json.dumps([{"name": "a", "x": ["p", "q", "r", "s"], "y": [12345, 123456, 0.000123456, 1.5e9]}]), value_labels=True, width=1200)
    assert {"12345", "123456", "0.0001235", "1.5e9"} <= set(texts(svg_text(r)))
    assert [format_value(v) for v in (12345.0, 0.5, 2.5e-5, -3.14159, 1e9, 999999999.0, 1234.5678)] == ["12345", "0.5", "2.5e-5", "-3.142", "1e9", "999999999", "1235"]


def test_C8_different_charts_get_different_element_ids():
    a = ok(bar, mode="histogram", series=json.dumps({"name": "v", "y": [1, 2, 3, 4]}), bins=2)
    b = ok(bar, mode="histogram", series=json.dumps({"name": "v", "y": [1000, 2000, 3000, 4000]}), bins=2)
    c = ok(bar, series=json.dumps([{"name": "v", "x": ["a", "b"], "y": [1, 2], "err": [0.1, 0.1]}]))
    e = ok(bar, series=json.dumps([{"name": "v", "x": ["a", "b"], "y": [1, 2], "err": [5, 5]}]))
    prefixes = {r.data["element_id_prefix"] for r in (a, b, c, e)}
    assert len(prefixes) == 4
    again = ok(bar, mode="histogram", series=json.dumps({"name": "v", "y": [1, 2, 3, 4]}), bins=2)
    assert again.data["element_id_prefix"] == a.data["element_id_prefix"]


# ----------------------------------------------------------------------------------------------- C6, C9


def _label(svg: str, text: str):
    return next(t for t in elements(svg, "text") if t.text == text)


def _inside_clip_group(svg: str, text: str) -> bool:
    for g in root(svg).iter(f"{SVG_NS}g"):
        if g.get("clip-path") and any(t.text == text for t in g.iter(f"{SVG_NS}text")):
            return True
    return False


def test_C6_reference_labels_stay_inside_the_frame_and_outside_the_clip():
    r = ok(xy, series=js(("s", [0, 1, 2, 3], [10, 120, 200, 90])), hlines="250,yield", vlines="3,end")
    svg = svg_text(r)
    area = plot_data(r)["panels"][0]["plot_area"]
    y_label = _label(svg, "yield")
    # the line is at the top of the 0 .. 250 axis: above it does not fit, so the label goes below it
    assert float(y_label.get("y")) - 10 >= area["y0"] and not _inside_clip_group(svg, "yield")
    v_label = _label(svg, "end")  # the vline is at the right edge: the label goes to its left
    assert v_label.get("text-anchor") == "end" and float(v_label.get("x")) <= area["x0"] + area["width"]
    assert not any("could not be placed" in w for w in r.warnings)


def test_C6_a_label_that_cannot_be_placed_is_warned():
    lines = ";".join(f"{v},label number {v}" for v in (5.0, 5.01, 5.02, 5.03, 5.04))
    r = ok(xy, series=js(("s", [0, 1], [0, 10])), hlines=lines, height=200)
    assert any("could not be placed" in w for w in r.warnings)


def test_C9_the_automatic_legend_avoids_lines_not_only_vertices():
    r = ok(xy, series=js(("diag", [0, 10], [0, 10]), ("flat", [0, 10], [5, 5])))
    legend = plot_data(r)["panels"][0]["legend"]
    assert legend["position"] != "top_right"  # the diagonal crosses the top right corner between its two vertices
    box = [el for el in elements(svg_text(r), "rect") if el.get("fill-opacity") == "0.88"][0]
    x0, y0 = float(box.get("x")), float(box.get("y"))
    x1, y1 = x0 + float(box.get("width")), y0 + float(box.get("height"))
    for run in polylines(svg_text(r)):
        for (a, b), (c, d) in zip(run, run[1:]):
            for t in np.linspace(0, 1, 200):
                xx, yy = a + t * (c - a), b + t * (d - b)
                assert not (x0 < xx < x1 and y0 < yy < y1)


# ----------------------------------------------------------------------------------------------- R1 .. R5


def test_R1_a_numeric_column_with_one_bad_cell_is_refused_not_skipped(tmp_path):
    r = fails(xy, "input.out_of_range", "data_file/2/z", data_file=write(tmp_path, "a.csv", "x,y,z\n1,2,1e101\n2,3,4\n"))
    assert r.error.details["reason"] == "out of range"
    r = fails(xy, "input.invalid", "data_file/3/y", data_file=write(tmp_path, "b.csv", "x,y\n1,2\n2,abc\n"))
    assert r.error.details["reason"] == "not a number" and "line 3" in r.error.message
    ok_ = ok(xy, data_file=write(tmp_path, "c.csv", "x,label,y\n1,left,2\n2,right,3\n"))
    assert any("no numeric cell" in w and "'label'" in w for w in ok_.warnings)
    assert [s["column"] for s in ok_.data["series"]] == ["y"]


def test_R1_pointer_tokens_escape_slash_and_tilde(tmp_path):
    fails(xy, "input.invalid", "data_file/3/a~1b~0c", data_file=write(tmp_path, "p.csv", "x,a/b~c\n1,2\n2,zz\n"), y_columns="a/b~c")


def test_R2_blank_line_floods_and_row_floods_are_refused_while_streaming(tmp_path):
    fails(xy, "input.out_of_range", "data_file", data_file=write(tmp_path, "blank.csv", "x,y\n1,2\n2,3\n" + "\n" * 20_000))
    path = tmp_path / "rows.csv"
    path.write_bytes(b"x,y\n" + b"1,2\n" * 2_000_000)  # 8 MB, ten times the row cap
    tracemalloc.start()
    start = time.perf_counter()
    fails(xy, "input.out_of_range", "data_file", data_file=str(path))
    elapsed = time.perf_counter() - start
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    # only the kept cells of 200000 rows are held (the whole file decoded and split would be well over 100 MB of objects)
    assert peak < 60e6, peak
    assert elapsed < 10


def test_R3_records_split_only_at_real_line_ends(tmp_path):
    path = tmp_path / "ls.csv"
    path.write_bytes('cat,v\n"A B",1\n"C\x0cD",2\nE,x\n'.encode("utf-8"))
    r = fails(bar, "input.invalid", "data_file/4/v", data_file=str(path), y_columns="v")
    assert r.error.details["line"] == 4
    path.write_bytes('cat,v\n"A B",1\n"C\x1dD",2\nE,3\n'.encode("utf-8"))
    r = ok(bar, data_file=str(path), y_columns="v")
    assert r.data["categories"] == ["A B", "C�D", "E"]
    fails(xy, "input.invalid", "data_file/3/y", data_file=write(tmp_path, "ff.csv", "x,y\n1,2\n2,3\x0c\n3,4\n"))
    ok(xy, data_file=write(tmp_path, "cr.csv", "x,y\r1,2\r2,3\r"))


def test_R4_a_histogram_of_tiny_values_points_at_the_data():
    fails(bar, "input.out_of_range", "series/0/y", mode="histogram", series=json.dumps({"name": "v", "y": [1e-200, 2e-200, 3e-200]}))


def test_R5_duplicate_json_keys_are_refused():
    fails(xy, "input.invalid", "series/0/y", series='[{"name": "a", "y": [1, 2], "y": [3, 4]}]')
    fails(xy, "input.invalid", "series/1/name", series='[{"name": "a", "y": [1]}, {"name": "b", "name": "c", "y": [2]}]')
