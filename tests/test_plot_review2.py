"""Second independent review: exact gateway reproductions and numerical oracles."""

import json
import re
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
from fastmcp import Client

from eng_toolkit.contract import ToolResult
from eng_toolkit.gateway.server import gateway
from tests.plot_testing import assert_finite, assert_safe_svg, elements, polylines, svg_text


async def call(tool, **args):
    async with Client(gateway) as client:
        response = await client.call_tool(tool, args)
    result = ToolResult.model_validate(response.structured_content)
    assert result.review_status == "unreviewed"
    assert_finite(response.structured_content)
    json.dumps(response.structured_content, allow_nan=False)
    if result.ok:
        assert_safe_svg(svg_text(result))
        metadata = Path(next(a.path for a in result.artifacts if a.path.endswith("plot_data.json")))
        assert_finite(json.loads(metadata.read_text(encoding="utf-8")))
    return result


def refusal(result, family, pointer):
    assert not result.ok
    assert result.error.family == family, result.error
    assert result.error.details["param"] == pointer
    assert not any("traceback" in k.lower() for k in result.error.details)


def exact_crossings(start, end, rect):
    """Independent oracle: intersect with each edge, then sort the valid crossings."""
    a, b = [tuple(map(Fraction, point)) for point in (start, end)]
    xmin, ymin, xmax, ymax = map(Fraction, rect)
    hits = []
    for axis, edge in ((0, xmin), (0, xmax), (1, ymin), (1, ymax)):
        if b[axis] == a[axis]:
            continue
        t = (edge - a[axis]) / (b[axis] - a[axis])
        point = tuple(a[k] + t * (b[k] - a[k]) for k in (0, 1))
        if 0 <= t <= 1 and xmin <= point[0] <= xmax and ymin <= point[1] <= ymax:
            hits.append((t, point))
    for t, point in ((0, a), (1, b)):
        if xmin <= point[0] <= xmax and ymin <= point[1] <= ymax:
            hits.append((t, point))
    hits.sort()
    return [tuple(map(float, hits[i][1])) for i in (0, -1)]


def exact_pixel(value, axis, p1):
    m = axis["mapping"]
    p0, p1 = Fraction(m["origin_px"]), Fraction(p1)
    return p0 + (Fraction(value) - Fraction(m["min"])) * (p1 - p0) / (Fraction(m["max"]) - Fraction(m["min"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("magnitude", ["1e20", "1e16", "1e100"])
@pytest.mark.parametrize("reverse", [False, True])
async def test_1_extreme_line_matches_exact_rational_intersections(magnitude, reverse):
    values = f"[-{magnitude},{magnitude}]" if not reverse else f"[{magnitude},-{magnitude}]"
    result = await call("plot_xy", series=f'{{"x":{values},"y":{values}}}', x_range="-1,1", y_range="-1,1", legend="off")
    assert result.ok, result.error
    # From the documented layout: x = 50 + (v+1)*737/2; y = 467 - (v+1)*455/2.
    points = [(Fraction(50) + (Fraction(v) + 1) * 737 / 2,
               Fraction(467) - (Fraction(v) + 1) * 455 / 2) for v in json.loads(values)]
    expected = exact_crossings(*points, (46, 8, 791, 471))
    runs = polylines(svg_text(result))
    assert len(runs) == 1
    for actual, wanted in zip(runs[0], expected):
        assert actual == pytest.approx(wanted, abs=0.0051)
    visible = exact_crossings(*runs[0], (50, 12, 787, 467))
    assert visible[0] == pytest.approx((50, 467) if not reverse else (787, 12), abs=0.01)
    assert visible[1] == pytest.approx((787, 12) if not reverse else (50, 467), abs=0.01)


@pytest.mark.asyncio
async def test_1_panels_keep_extreme_horizontal_crossings():
    result = await call("plot_panels", series='{"name":"a","x":[-1e20,1e20],"y":[0,1]}', panels="A|a;B|a", x_range="-1,1")
    assert result.ok, result.error
    runs = polylines(svg_text(result))
    assert len(runs) == 2
    for panel, run in zip(result.data["panels"], runs):
        area, axes = panel["plot_area"], panel["axes"]
        right, bottom = area["x0"] + area["width"], area["y0"] + area["height"]
        points = [(exact_pixel(x, axes["x"], right), exact_pixel(y, axes["y"], area["y0"]))
                  for x, y in ((-1e20, 0), (1e20, 1))]
        expected = exact_crossings(*points, (area["x0"] - 4, area["y0"] - 4, right + 4, bottom + 4))
        for actual, wanted in zip(run, expected):
            assert actual == pytest.approx(wanted, abs=0.0051)


@pytest.mark.asyncio
@pytest.mark.parametrize("points", [
    [(-1e20, -2e20), (1e20, 2e20)],
    [(-1e20, 1e20), (1e20, -1e20)],
    [(0.25, 0.5), (1e99, 2e99)],
    [(1e99, 2e99), (0.25, 0.5)],
])
async def test_1_asymmetric_extreme_segments_match_rational_oracle(points):
    result = await call("plot_xy", series=json.dumps({"x": [p[0] for p in points], "y": [p[1] for p in points]}),
                        x_range="-1,1", y_range="-1,1", legend="off")
    assert result.ok, result.error
    pixels = [(Fraction(50) + (Fraction(x) + 1) * 737 / 2,
               Fraction(467) - (Fraction(y) + 1) * 455 / 2) for x, y in points]
    expected = exact_crossings(*pixels, (46, 8, 791, 471))
    [run] = polylines(svg_text(result))
    for actual, wanted in zip(run, expected):
        assert actual == pytest.approx(wanted, abs=0.0051)


@pytest.mark.asyncio
async def test_1_fill_uses_the_same_exact_crossings_as_the_line():
    result = await call("plot_xy", series='{"x":[-1e20,1e20],"y":[-1e20,1e20]}',
                        x_range="-1,1", y_range="-1,1", legend="off", fill="zero")
    assert result.ok, result.error
    fills = [p for p in elements(svg_text(result), "path") if p.get("fill-opacity")]
    assert len(fills) == 2
    expected = sorted([(46, 239.5), (791, 239.5),
                       (46, float(Fraction(467) + Fraction(4 * 455, 737))),
                       (791, float(Fraction(12) - Fraction(4 * 455, 737)))])
    for path in fills:
        vertices = sorted(tuple(map(float, pair)) for pair in re.findall(r"[ML](-?[\d.]+) (-?[\d.]+)", path.get("d")))
        assert len(vertices) == len(expected)
        for actual, wanted in zip(vertices, expected):
            assert actual == pytest.approx(wanted, abs=0.0051)


def stem_segments(result):
    return [tuple(map(float, match)) for path in elements(svg_text(result), "path")
            if path.get("stroke-width") == "1.25"
            for match in re.findall(r"M(-?[\d.]+) (-?[\d.]+)V(-?[\d.]+)", path.get("d", ""))]


@pytest.mark.asyncio
async def test_2_stem_is_visible_when_marker_is_outside():
    result = await call("plot_xy", series='{"x":[0.5],"y":[10]}', kind="stem", x_range="0,1", y_range="0,1", legend="off")
    assert result.ok, result.error
    segments = stem_segments(result)
    assert len(segments) == 1
    x, base, tip = segments[0]
    assert (x, base, max(12, tip)) == (415, 467, 12)


@pytest.mark.asyncio
async def test_3_negative_range_stem_enters_from_zero_above_frame():
    result = await call("plot_xy", series='{"x":[0.5],"y":[-2.5]}', kind="stem", x_range="0,1", y_range="-3,-2", legend="off")
    assert result.ok, result.error
    [(x, base, tip)] = stem_segments(result)
    assert (x, max(12, base), tip) == (418.5, 12, 239.5)


@pytest.mark.asyncio
@pytest.mark.parametrize("value,yrange", [(10, "-3,-2"), (-10, "2,3")])
async def test_2_3_stems_wholly_outside_frame_do_not_draw(value, yrange):
    result = await call("plot_xy", series=json.dumps({"x": [0.5], "y": [value]}), kind="stem", x_range="0,1", y_range=yrange, legend="off")
    assert result.ok, result.error
    assert stem_segments(result) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["inline", "csv"])
async def test_4_histogram_integer_boundaries_are_exact(source, tmp_path):
    values = [9007199254740992, 9007199254740993, 9007199254740994]
    edges = [0, 9007199254740993, 10000000000000000]
    expected = [sum(a <= v < b or (i == 1 and v == b) for v in values) for i, (a, b) in enumerate(zip(edges, edges[1:]))]
    assert expected == [1, 2]  # Python integer comparisons, independent of binary64 / NumPy.
    if source == "csv":
        path = tmp_path / "integers.csv"
        path.write_text("y\n" + "\n".join(map(str, values)), encoding="utf-8")
        args = {"data_file": str(path)}
    else:
        args = {"series": '{"y":[9007199254740992,9007199254740993,9007199254740994]}'}
    result = await call("plot_bar", mode="histogram", bin_edges="0,9007199254740993,10000000000000000", **args)
    assert result.ok, result.error
    hist = result.data["histogram"]
    assert hist["counts"] == expected
    assert hist["bin_edges"] == edges


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["inline", "csv"])
@pytest.mark.parametrize("values,edges", [
    (["9007199254740992.0", "9007199254740993.0", "9007199254740994.0"], ["0", "9007199254740993", "10000000000000000"]),
    (["-9007199254740994", "-9007199254740993", "-9007199254740992"], ["-10000000000000000", "-9007199254740993", "0"]),
    (["0.1", "0.100000000000000001", "0.100000000000000002", "1"], ["0", "0.100000000000000001", "1"]),
])
async def test_4_histogram_matches_decimal_comparisons(source, values, edges, tmp_path):
    exact_values, exact_edges = list(map(Decimal, values)), list(map(Decimal, edges))
    expected = [sum(a <= v < b or (i == len(edges) - 2 and v == b) for v in exact_values)
                for i, (a, b) in enumerate(zip(exact_edges, exact_edges[1:]))]
    if source == "csv":
        path = tmp_path / "decimals.csv"
        path.write_text("y\n" + "\n".join(values), encoding="utf-8")
        args = {"data_file": str(path)}
    else:
        args = {"series": '{"y":[' + ",".join(values) + "]}"}
    result = await call("plot_bar", mode="histogram", bin_edges=",".join(edges), **args)
    assert result.ok, result.error
    hist = result.data["histogram"]
    assert hist["counts"] == expected
    assert list(map(Decimal, hist["bin_edges_exact"])) == exact_edges


@pytest.mark.asyncio
async def test_4_automatic_decimal_edges_agree_with_reported_boundaries():
    result = await call("plot_bar", mode="histogram", series='{"y":[0.1,0.2,0.3]}', bins=2)
    assert result.ok, result.error
    assert result.data["histogram"]["counts"] == [1, 2]
    assert result.data["histogram"]["bin_edges"] == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_4_indistinguishable_bin_edges_are_refused():
    result = await call("plot_bar", mode="histogram", series='{"y":[1]}', bin_edges="0,9007199254740992,9007199254740993")
    refusal(result, "input.out_of_range", "/bin_edges")


@pytest.mark.asyncio
async def test_4_nonzero_bin_edge_that_underflows_is_refused():
    result = await call("plot_bar", mode="histogram", series='{"y":[0]}', bin_edges="-1,1e-10000,1", histogram="density")
    refusal(result, "input.out_of_range", "/bin_edges")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["plot_xy", "plot_panels", "plot_bar"])
@pytest.mark.parametrize("value", ["null", "[]", "1"])
async def test_5_caller_cannot_forge_duplicate_metadata(tool, value):
    args = {"panels": "A|series 1;B|series 1"} if tool == "plot_panels" else {}
    result = await call(tool, series='{"\\u0000duplicate-key":' + value + ',"y":[1,2]}', **args)
    refusal(result, "input.invalid", "/series/0/�duplicate-key")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["plot_xy", "plot_panels", "plot_bar"])
async def test_5_real_duplicate_still_refused_with_forged_metadata_present(tool):
    args = {"panels": "A|series 1;B|series 1"} if tool == "plot_panels" else {}
    result = await call(tool, series='{"\\u0000duplicate-key":null,"y":[1],"y":[2]}', **args)
    refusal(result, "input.invalid", "/series/0/y")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["plot_xy", "plot_panels"])
@pytest.mark.parametrize("xs,markers", [("[5e-324,1]", False), ("[1e-320,1e100]", True)])
async def test_6_log_expansion_underflow_is_a_structured_refusal(tool, xs, markers):
    args = {"panels": "A|series 1;B|series 1"} if tool == "plot_panels" else {}
    result = await call(tool, series=f'{{"x":{xs},"y":[1,2]}}', x_scale="log", markers=markers, **args)
    refusal(result, "input.out_of_range", "/series/0/x")


@pytest.mark.asyncio
@pytest.mark.parametrize("fixed", ["", "5e-324,1", "5e-324,1e-323"])
async def test_6_representable_log_bounds_remain_usable(fixed):
    xs = [1e-320, 1] if not fixed else [float(v) for v in fixed.split(",")]
    result = await call("plot_xy", series=json.dumps({"x": xs, "y": [1, 2]}), x_scale="log", x_range=fixed)
    assert result.ok, result.error
    axis = result.data["panels"][0]["axes"]["x"]
    assert 0 < axis["min"] < axis["max"]
    assert all(v > 0 for v in axis["ticks"] + axis["minor_ticks"])


@pytest.mark.asyncio
async def test_6_log_y_expansion_points_at_y_values():
    result = await call("plot_xy", series='{"x":[1,2],"y":[5e-324,1]}', y_scale="log")
    refusal(result, "input.out_of_range", "/series/0/y")


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["1e999", "-1e999", "1e101", "-1e101"])
async def test_7_numeric_categories_obey_magnitude_cap(label):
    result = await call("plot_bar", series=f'{{"x":[{label}],"y":[1]}}')
    refusal(result, "input.out_of_range", "/series/0/x/0")


@pytest.mark.asyncio
async def test_8_density_without_any_values_in_bins_is_undefined():
    # Both bins contain zero observations, so n_in = 0 and count/(n_in*width) is undefined.
    result = await call("plot_bar", mode="histogram", series='{"y":[5,6]}', bin_edges="0,1,2", histogram="density")
    refusal(result, "input.invalid", "/bin_edges")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["counts", "cumulative"])
async def test_8_empty_counts_and_cumulative_remain_defined(mode):
    result = await call("plot_bar", mode="histogram", series='{"y":[5,6]}', bin_edges="0,1,2", histogram=mode)
    assert result.ok, result.error
    assert result.data["histogram"]["heights"] == [0, 0]


@pytest.mark.asyncio
async def test_8_density_with_exact_edges_integrates_to_one():
    result = await call("plot_bar", mode="histogram", series='{"y":[9007199254740992,9007199254740993,9007199254740994]}',
                        bin_edges="0,9007199254740993,10000000000000000", histogram="density")
    assert result.ok, result.error
    hist = result.data["histogram"]
    edges = list(map(Fraction, hist["bin_edges_exact"]))
    integral = sum(Fraction(height) * (b - a) for height, a, b in zip(hist["heights"], edges, edges[1:]))
    assert float(integral) == pytest.approx(1, abs=1e-15)
