"""``plot_bar``: bar charts (grouped or stacked, up to 6 series, optional error bars) and histograms of one column.

Bars: category band = plot width / categories; the bars of a category fill the middle 80 % of its band (grouped: side by side,
stacked: on top of each other, positive values upwards from 0 and negative values downwards, the usual convention).  The y axis
always includes 0.  Histogram: equal-width bins over [min, max] of the data (``bins``; 0 = Sturges, ceil(log2 n) + 1) or the
explicit ``bin_edges``; bins are half-open [a, b) except the last, which is closed (numpy's convention); values outside explicit
edges are counted and reported, never silently dropped.  Heights are counts, density (count / (n in range x bin width), so the
bars integrate to 1) or cumulative counts.
"""

from __future__ import annotations

import math
from typing import Annotated

import numpy as np
from pydantic import Field

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.plot._axes import label_widths, linear_axis, thin
from eng_toolkit.domains.plot._chart import (
    LABEL_BAND, LABEL_GAP_X, LABEL_GAP_Y, LABEL_SIZE, LEGEND_INSET, MIN_PLOT, PAD, TICK_GAP, TICK_LEN, TICK_SIZE, TITLE_BAND, TITLE_SIZE, X_TICK_BAND,
    X_TICK_PX, Y_TICK_PX, Texts, chart_id, draw_legend, legend_box,
)
from eng_toolkit.domains.plot._common import (
    BINS_RANGE,
    VALUE_ABS_MIN,
    MAX_BAR_SERIES,
    MAX_CATEGORIES,
    SIZE_RANGE,
    Computed,
    JobHandle,
    choice,
    column_param,
    fail,
    flag,
    name_list,
    number_token,
    range_param,
    record_text,
    run,
    split_records,
    text_param,
    whole,
)
from eng_toolkit.domains.plot._axes import format_value
from eng_toolkit.domains.plot._data import CsvTable, Series, cell_pointer, check_one_source, csv_series, display, inline_series, numeric_cells
from eng_toolkit.domains.plot._output import finish
from eng_toolkit.domains.plot._server import server
from eng_toolkit.domains.plot._svg import DASHES, Canvas, num, series_style, text_width
from eng_toolkit.domains.plot.xy import THEME_NAMES

LEGENDS = ("auto", "off", "top_right", "top_left", "bottom_right", "bottom_left", "right")
GROUP_FRACTION = 0.8
CATEGORY_LABEL_CHARS = 24
VALUE_SIZE = 11


def _bin_edges(raw: object) -> np.ndarray | None:
    text = raw if isinstance(raw, str) else None
    if raw is None or (text is not None and not text.strip()):
        return None
    if text is None:
        fail("bin_edges", "'bin_edges' must be text 'e0,e1,...'")
    parts = split_records(text.replace(",", ";"), "bin_edges", BINS_RANGE[1] + 1)
    if len(parts) < BINS_RANGE[0] + 1:
        fail("bin_edges", f"'bin_edges' needs at least {BINS_RANGE[0] + 1} edges ({BINS_RANGE[0]} bins), got {len(parts)}")
    edges = np.array([number_token(p, "bin_edges", f"'bin_edges' value {i + 1}") for i, p in enumerate(parts)])
    if not np.all(np.diff(edges) > 0):
        fail("bin_edges", "'bin_edges' must be strictly increasing")
    return edges


def _fmt(v: float) -> str:
    """A bar value label: exact integers below 1e9, plain decimals with 4 significant digits for 1e-4 <= |v| < 1e9, else 1.235e-5."""
    return format_value(v, 4)


@server.tool(
    name="bar",
    description=(
        "Draw a bar chart (categories x up to 6 series, grouped or stacked, optional error bars and value labels) or a histogram "
        "of one column (2..200 bins or explicit edges; counts, density or cumulative) as a deterministic SVG plus plot_data.json "
        "with the bin edges and counts. Data: a CSV with a header row or inline JSON series."
    ),
)
def bar(
    mode: Annotated[str, Field(description="bar (categories and values) or histogram (distribution of one column of numbers).")] = "bar",
    data_file: Annotated[str, Field(description="CSV/TXT path with a header row, e.g. ws/<job>/rainflow_cycles.csv. Give this or 'series'.")] = "",
    series: Annotated[str, Field(description='Inline JSON [{"name":"2025","x":["A","B"],"y":[3,5],"err":[0.2,0.4]}]; bar: <=6 series, same x; histogram: one, y used.')] = "",
    x_column: Annotated[str, Field(description="Bar mode, CSV: the category column (text allowed), e.g. 'part'; blank = the first column.")] = "",
    y_columns: Annotated[str, Field(description="CSV value columns, e.g. 'q1,q2' (bar, <=6) or one column (histogram); blank = numeric columns / first column.")] = "",
    err_columns: Annotated[str, Field(description="Bar mode, CSV: error-bar half widths, one column per y column in the same order, e.g. 'q1_err,q2_err'.")] = "",
    stacking: Annotated[str, Field(description="grouped (bars side by side) or stacked (positives up, negatives down); bar mode with 2+ series.")] = "grouped",
    value_labels: Annotated[bool, Field(description="true writes each bar's value above it (stacked: the total); skipped where a bar is too narrow.")] = False,
    bins: Annotated[int, Field(description="Histogram: number of equal bins over the data range, 2..200; 0 = Sturges rule ceil(log2 n)+1.")] = 0,
    bin_edges: Annotated[str, Field(description="Histogram: explicit increasing edges 'e0,e1,...', e.g. '0,50,100,200' (3..201 values); instead of bins.")] = "",
    histogram: Annotated[str, Field(description="Histogram heights: counts, density (bars integrate to 1) or cumulative counts.")] = "counts",
    title: Annotated[str, Field(description="Chart title, plain text <= 120 characters, e.g. 'Cycle ranges'; blank = none.")] = "",
    x_label: Annotated[str, Field(description="x axis title with unit, plain text <= 120 characters, e.g. 'Stress range (MPa)'.")] = "",
    y_label: Annotated[str, Field(description="y axis title with unit, plain text <= 120 characters, e.g. 'Cycles'.")] = "",
    y_range: Annotated[str, Field(description="Fixed y axis 'min,max', e.g. '0,50'; blank = automatic from 0 with nice ticks.")] = "",
    legend: Annotated[str, Field(description="auto (2+ series), off, top_right, top_left, bottom_right, bottom_left or right (outside the plot).")] = "auto",
    grid: Annotated[bool, Field(description="true draws horizontal grid lines at the y ticks; false draws none.")] = True,
    width: Annotated[int, Field(description="SVG width in px, 200..2400, e.g. 800.")] = 800,
    height: Annotated[int, Field(description="SVG height in px, 200..2400, e.g. 450.")] = 450,
    theme: Annotated[str, Field(description="auto (follows the viewer's light/dark), light, dark, or print (black and grey).")] = "auto",
) -> ToolResult:
    inputs = {
        "mode": mode, "data_file": data_file, "series": record_text(series), "x_column": x_column, "y_columns": y_columns, "err_columns": err_columns,
        "stacking": stacking, "value_labels": value_labels, "bins": bins, "bin_edges": record_text(bin_edges), "histogram": histogram,
        "title": record_text(title), "x_label": record_text(x_label), "y_label": record_text(y_label), "y_range": y_range, "legend": legend,
        "grid": grid, "width": width, "height": height, "theme": theme,
    }

    def compute(handle: JobHandle) -> Computed:
        notes: list[str] = []
        m = choice(mode, "mode", ("bar", "histogram"))
        stack = choice(stacking, "stacking", ("grouped", "stacked"))
        vlabels, chart_grid = flag(value_labels, "value_labels"), flag(grid, "grid")
        hist_mode = choice(histogram, "histogram", ("counts", "density", "cumulative"))
        leg = choice(legend, "legend", LEGENDS)
        th = choice(theme, "theme", THEME_NAMES)
        w, h = whole(width, "width", *SIZE_RANGE), whole(height, "height", *SIZE_RANGE)
        n_bins = whole(bins, "bins", 0, BINS_RANGE[1])
        if n_bins == 1:
            fail("bins", f"'bins' must be 0 (automatic) or {BINS_RANGE[0]}..{BINS_RANGE[1]}")
        edges_in = _bin_edges(bin_edges)
        labels = {name: text_param(raw, name, notes=notes) for name, raw in (("title", title), ("x_label", x_label), ("y_label", y_label))}
        yr = range_param(y_range, "y_range")
        xcol = column_param(x_column, "x_column")
        ycols, ecols = name_list(y_columns, "y_columns"), name_list(err_columns, "err_columns")
        source = check_one_source(data_file, series)
        if m == "histogram":
            if n_bins and edges_in is not None:
                fail("bin_edges", "give either 'bins' or 'bin_edges', not both")
            if ecols:
                fail("err_columns", "error bars go with bar mode, not with a histogram")
            values, vname, vpointer = _histogram_values(handle, source, data_file, series, ycols, notes)
            spec = _histogram(values, n_bins, edges_in, hist_mode, notes, vpointer)
            spec["value_name"] = vname
        else:
            if n_bins or edges_in is not None:
                fail("bins" if n_bins else "bin_edges", "'bins' and 'bin_edges' go with mode histogram")
            spec = _bars(handle, source, data_file, series, xcol, ycols, ecols, notes)
            if stack == "stacked" and any(s.err is not None for s in spec["series"]):
                fail("err_columns" if source == "csv" else "series", "error bars cannot be drawn on stacked bars; use stacking grouped")
        spec["source_param"] = "data_file" if source == "csv" else "series"
        svg, data, drawn, zero_height = _render(spec, m, stack, vlabels, chart_grid, leg, th, w, h, labels, yr, notes)
        return finish(handle, svg, data, notes, {"bars_drawn": drawn, "zero_height_bars": zero_height}, "bars_drawn")

    return run("plot_bar", inputs, compute)


# ----------------------------------------------------------------------------------------------- data


def _histogram_values(handle: JobHandle, source: str, data_file, series, ycols: list[str], notes: list[str]) -> tuple[np.ndarray, str]:
    if source == "csv":
        if len(ycols) > 1:
            fail("y_columns", "a histogram takes exactly one column in 'y_columns'")
        table = CsvTable(handle, data_file)
        name = ycols[0] if ycols else table.header[0]
        idx = table.index(name, "y_columns")
        cols, lines = table.extract([idx])
        return numeric_cells(cols[idx], lines, name), display(name), "data_file"
    if ycols:
        fail("y_columns", "'y_columns' selects CSV columns; give one inline series for a histogram")
    loaded = inline_series(series, notes, max_series=1)
    if loaded[0].x_name != "index":
        notes.append("the histogram uses the 'y' values of the series; its 'x' values are ignored")
    return loaded[0].y, loaded[0].name, "series/0/y"


def _histogram(values: np.ndarray, n_bins: int, edges: np.ndarray | None, hist_mode: str, notes: list[str], pointer: str) -> dict:
    n = len(values)
    lo, hi = float(np.min(values)), float(np.max(values))
    rule = "explicit edges"
    if edges is None and 0.0 < max(abs(lo), abs(hi)) < VALUE_ABS_MIN:
        fail(pointer, f"every value of the histogram column is below {VALUE_ABS_MIN:g} in magnitude (largest {max(abs(lo), abs(hi)):g}): the range is too small to bin; rescale the data",
             "input.out_of_range", limit=VALUE_ABS_MIN)
    if edges is None:
        bins_used = n_bins or min(BINS_RANGE[1], max(BINS_RANGE[0], math.ceil(math.log2(n)) + 1))
        rule = f"{bins_used} equal bins over the data range" + ("" if n_bins else " (Sturges: ceil(log2 n) + 1)")
        if hi - lo <= 1e-9 * max(abs(lo), abs(hi)):
            pad = 0.5 if lo == 0.0 else abs(lo) * 0.1
            notes.append(f"all values are equal ({lo:.6g}); the bins span {lo - pad:.6g} .. {hi + pad:.6g}")
            lo, hi = lo - pad, hi + pad
        edges = np.linspace(lo, hi, bins_used + 1)
    counts = np.histogram(values, bins=edges)[0].astype(float)
    below, above = int(np.count_nonzero(values < edges[0])), int(np.count_nonzero(values > edges[-1]))
    if below or above:
        notes.append(f"{below} value(s) below the first edge and {above} above the last edge are not in any bin (counted in data.histogram)")
    n_in = int(counts.sum())
    widths = np.diff(edges)
    if hist_mode == "density":
        heights = counts / (n_in * widths) if n_in else np.zeros_like(counts)
    elif hist_mode == "cumulative":
        heights = np.cumsum(counts)
    else:
        heights = counts
    return {"edges": edges, "counts": counts, "heights": heights, "below": below, "above": above, "n": n, "n_in_bins": n_in, "rule": rule, "heights_are": hist_mode}


def _bars(handle: JobHandle, source: str, data_file, series, xcol: str, ycols: list[str], ecols: list[str], notes: list[str]) -> dict:
    if source == "csv":
        if len(ycols) > MAX_BAR_SERIES:
            fail("y_columns", f"at most {MAX_BAR_SERIES} bar series", "input.out_of_range", limit=MAX_BAR_SERIES)
        if ecols and len(ecols) != len(ycols):
            fail("err_columns", f"'err_columns' must name one column per y column ({len(ycols)}), got {len(ecols)}" + ("" if ycols else "; name the y columns too"))
        wanted = [(c, "y_columns") for c in ycols] if ycols else None
        loaded, _ = csv_series(handle, data_file, xcol, wanted, notes, text_x=True, err=[(c, "err_columns") for c in ecols] or None)
        if len(loaded) > MAX_BAR_SERIES:
            fail("y_columns", f"{len(loaded)} numeric columns; at most {MAX_BAR_SERIES} bar series: name them in 'y_columns'", "input.out_of_range", limit=MAX_BAR_SERIES)
    else:
        if xcol or ycols or ecols:
            fail("x_column" if xcol else ("y_columns" if ycols else "err_columns"), "column parameters select CSV columns; inline series carry their own categories ('x') and errors ('err')")
        loaded = inline_series(series, notes, text_x=True, allow_err=True, max_series=MAX_BAR_SERIES)
        for i, s in enumerate(loaded):
            if s.labels is None:
                s.labels = [str(j + 1) for j in range(len(s.y))]
            if s.labels != loaded[0].labels:
                fail(f"series/{i}/x", "every bar series must list the same categories in 'x' (same order)")
    cats = loaded[0].labels or []
    if len(cats) > MAX_CATEGORIES:
        fail("data_file" if source == "csv" else "series/0/x", f"{len(cats)} categories; at most {MAX_CATEGORIES}", "input.out_of_range", limit=MAX_CATEGORIES)
    for k, s in enumerate(loaded):
        if s.err is not None and np.any(s.err < 0):
            i = int(np.argmax(s.err < 0))
            where = cell_pointer(s.meta["lines"][i], s.meta.get("err_column", "")) if source == "csv" else f"series/{k}/err/{i}"
            fail(where, f"error-bar half width {s.err[i]:g} is negative")
    return {"categories": cats, "series": loaded}


# ----------------------------------------------------------------------------------------------- drawing


def _render(spec: dict, mode: str, stack: str, vlabels: bool, grid: bool, leg: str, theme: str, W: int, H: int, labels: dict, yr, notes: list[str]):
    texts = Texts()
    hist = mode == "histogram"
    top = PAD + (TITLE_BAND if labels["title"] else 0)
    bottom_margin = PAD + X_TICK_BAND + (LABEL_BAND if labels["x_label"] else 0)
    ph = H - top - bottom_margin
    if ph < MIN_PLOT:
        fail("height", f"a height of {H} px leaves {ph} px for the plot area; at least {MIN_PLOT} px are needed", "input.out_of_range", needed_px=MIN_PLOT)
    bottom = top + ph

    # y extent: 0, the bars (stacked: the stack ends), the error bars
    if hist:
        series_list: list[Series] = []
        heights = [spec["heights"]]
        lo_v, hi_v = 0.0, float(np.max(spec["heights"]))
    else:
        series_list = spec["series"]
        heights = [s.y for s in series_list]
        if stack == "stacked" and len(series_list) > 1:
            pos = np.sum([np.clip(y, 0, None) for y in heights], axis=0)
            neg = np.sum([np.clip(y, None, 0) for y in heights], axis=0)
            lo_v, hi_v = min(0.0, float(np.min(neg))), max(0.0, float(np.max(pos)))
        else:
            lows = [y - (s.err if s.err is not None else 0.0) for s, y in zip(series_list, heights)]
            highs = [y + (s.err if s.err is not None else 0.0) for s, y in zip(series_list, heights)]
            lo_v = min(0.0, min(float(np.min(v)) for v in lows))
            hi_v = max(0.0, max(float(np.max(v)) for v in highs))
    if vlabels and yr is None:  # room for the value labels above (and below) the bars
        span = hi_v - lo_v
        hi_v, lo_v = (hi_v + 0.08 * span if hi_v > 0 else hi_v), (lo_v - 0.08 * span if lo_v < 0 else lo_v)
    y_target = max(2, min(10, round(ph / Y_TICK_PX)))
    ya = linear_axis(lo_v, hi_v, y_target, yr, param="y_range" if yr else spec["source_param"], what="y axis")
    notes += ya.notes
    ya.set_pixels(bottom, top)
    ya.shown = thin([ya.px(t) for t in ya.ticks], [TICK_SIZE] * len(ya.ticks), LABEL_GAP_Y)
    max_w = max([wd for wd, s in zip(label_widths(ya.labels, TICK_SIZE), ya.shown) if s] or [0.0])
    left = PAD + (LABEL_BAND if labels["y_label"] else 0) + TICK_LEN + TICK_GAP + math.ceil(max_w)

    # x: histogram numeric over the edges, bars categorical
    if hist:
        edges = spec["edges"]
        x_target = max(2, min(12, round((W - 2 * PAD - 60) / X_TICK_PX)))
        xa = linear_axis(float(edges[0]), float(edges[-1]), x_target, (float(edges[0]), float(edges[-1])), param="bin_edges", what="x axis")
        last_w = text_width(xa.labels[-1], TICK_SIZE) if xa.ticks and xa.ticks[-1] == xa.hi else 0.0
        right = max(PAD, math.ceil(last_w / 2) + 2)
        cats: list[str] = []
    else:
        xa = None
        cats = spec["categories"]
        right = PAD
    entries = [(s.name, i) for i, s in enumerate(series_list)]
    show_legend = leg != "off" and len(entries) >= 2
    legend = None
    outside = False
    if show_legend:
        names, lw, lh = legend_box(entries, texts, "chart")
        outside = leg == "right" or lh > ph - 2 * LEGEND_INSET or lw > 0.5 * (W - left - right)
        if outside:
            right += lw + 10
            if leg != "right":
                notes.append("the legend does not fit inside the plot area and was placed to the right of it")
        legend = (names, lw, lh)
    pw = W - left - right
    if pw < MIN_PLOT:
        fail("width", f"a width of {W} px leaves {pw:.0f} px for the plot area; at least {MIN_PLOT} px are needed", "input.out_of_range", needed_px=MIN_PLOT)
    plot_right = left + pw
    base = ya.px(0.0) if ya.contains(0.0) else (ya.p0 if ya.lo > 0 else ya.p1)

    rects: list[tuple[float, float, float, float, int, float]] = []  # x, y, w, h, series index, value
    label_jobs: list[tuple[float, float, float, str]] = []  # x centre, value y px, slot width, text
    err_paths: list[str] = []
    if hist:
        xa.set_pixels(left, plot_right)
        xa.shown = thin([xa.px(t) for t in xa.ticks], label_widths(xa.labels, TICK_SIZE), LABEL_GAP_X)
        for i, v in enumerate(spec["heights"].tolist()):
            x0, x1 = xa.px(float(edges[i])), xa.px(float(edges[i + 1]))
            yv = ya.px(v)
            rects.append((x0, min(yv, base), x1 - x0, abs(yv - base), 0, v))
            label_jobs.append(((x0 + x1) / 2, yv, x1 - x0, _fmt(v)))
    else:
        n, m = len(cats), len(series_list)
        band = pw / n
        group = band * GROUP_FRACTION
        bw = group / m if stack == "grouped" else group
        pos_top = np.zeros(n)
        neg_bot = np.zeros(n)
        for j, s in enumerate(series_list):
            for i, v in enumerate(s.y.tolist()):
                x0 = left + i * band + (band - group) / 2 + (j * bw if stack == "grouped" else 0.0)
                if stack == "stacked" and m > 1:
                    start = pos_top[i] if v >= 0 else neg_bot[i]
                    end = start + v
                    if v >= 0:
                        pos_top[i] = end
                    else:
                        neg_bot[i] = end
                    y0, y1 = ya.px(start), ya.px(end)
                else:
                    y0, y1 = base, ya.px(v)
                    tip = v + (float(s.err[i]) if s.err is not None else 0.0) * (1 if v >= 0 else -1)
                    label_jobs.append((x0 + bw / 2, ya.px(tip), bw, _fmt(v)))
                    if s.err is not None and s.err[i] > 0:
                        e = float(s.err[i])
                        cx, cw = x0 + bw / 2, min(bw * 0.5, 10.0) / 2
                        ya_lo, ya_hi = ya.px(v - e), ya.px(v + e)
                        err_paths.append(f"M{num(cx)} {num(ya_lo)}V{num(ya_hi)}M{num(cx - cw)} {num(ya_lo)}H{num(cx + cw)}M{num(cx - cw)} {num(ya_hi)}H{num(cx + cw)}")
                rects.append((x0, min(y0, y1), bw, abs(y1 - y0), j, v))
        if stack == "stacked" and m > 1:
            totals = np.sum([s.y for s in series_list], axis=0)
            for i, t in enumerate(totals.tolist()):
                ytop = ya.px(pos_top[i]) if t >= 0 else ya.px(neg_bot[i])
                label_jobs.append((left + (i + 0.5) * band, ytop, group, _fmt(t)))

    # ---------------------------------------------------------------- SVG
    # the id prefix covers everything that defines the drawing: options, labels, categories, names, values, errors, bin edges
    if hist:
        arrays = [spec["edges"], spec["counts"], spec["heights"]]
        ident = [spec["value_name"], spec["rule"], spec["heights_are"], spec["below"], spec["above"]]
    else:
        arrays = [a for s in series_list for a in (s.y, s.err if s.err is not None else np.zeros(0))]
        ident = [cats, [(s.name, s.err is not None) for s in series_list]]
    pid = chart_id("plot_bar", mode, stack, vlabels, grid, leg, theme, W, H, labels, yr, ident, arrays=arrays)
    cv = Canvas(W, H, theme, pid)
    cv.el("rect", [("x", "0"), ("y", "0"), ("width", str(W)), ("height", str(H))], fill="bg")
    cv.begin("defs", [])
    cv.begin("clipPath", [("id", f"{pid}-clip0")])
    cv.el("rect", [("x", num(left)), ("y", num(top)), ("width", num(pw)), ("height", num(ph))])
    cv.end("clipPath")
    cv.end("defs")
    if labels["title"]:
        cv.text(W / 2, PAD + TITLE_SIZE, texts.fit(labels["title"], W - 2 * PAD, TITLE_SIZE, "title"), size=TITLE_SIZE, anchor="middle", weight="600")
    if grid:
        cv.el("path", [("d", "".join(f"M{num(left)} {num(ya.px(t))}H{num(plot_right)}" for t in ya.ticks)), ("stroke-width", "1")], fill="none", stroke="grid")
    cv.begin("g", [("clip-path", f"url(#{pid}-clip0)")])
    drawn = zero_height = 0
    clipped: list[tuple[int, float, str]] = []  # (bar index, value, side)
    for b, (x0, y0, bw_, bh, j, v) in enumerate(rects):
        if bh <= 0:
            zero_height += 1
            continue
        if y0 < top - 0.01:
            clipped.append((b, v, "top"))
        if y0 + bh > bottom + 0.01:
            clipped.append((b, v, "bottom"))
        cy0, cy1 = max(y0, top - 4.0), min(y0 + bh, bottom + 4.0)  # an axis-aligned rectangle: cutting it to the area is exact
        if cy1 <= cy0:
            continue
        drawn += 1
        colour, dash, _ = series_style(j)
        attrs = [("x", num(x0)), ("y", num(cy0)), ("width", num(bw_)), ("height", num(cy1 - cy0))]
        if hist:
            cv.el("rect", attrs + [("stroke-width", "0.75")], fill=f"s{colour}", stroke="bg")
        else:
            attrs += [("stroke-width", "1")] + ([("stroke-dasharray", DASHES[dash])] if DASHES[dash] else [])
            cv.el("rect", attrs + [("fill-opacity", "0.85")], fill=f"s{colour}", stroke="fg")
    if err_paths:
        cv.el("path", [("d", "".join(err_paths)), ("stroke-width", "1.25")], fill="none", stroke="fg")
    if clipped:  # a small triangle at the frame, pointing the way the bar continues
        marks = []
        for b, _, side in clipped:
            cx = rects[b][0] + rects[b][2] / 2
            if side == "top":
                marks.append(f"M{num(cx - 5)} {num(top + 10)}L{num(cx)} {num(top + 2)}L{num(cx + 5)} {num(top + 10)}Z")
            else:
                marks.append(f"M{num(cx - 5)} {num(bottom - 10)}L{num(cx)} {num(bottom - 2)}L{num(cx + 5)} {num(bottom - 10)}Z")
        cv.el("path", [("d", "".join(marks)), ("stroke-width", "1")], fill="fg", stroke="bg")
        listed = ", ".join(_fmt(v) for _, v, _ in clipped[:10]) + (" ..." if len(clipped) > 10 else "")
        notes.append(f"{len(clipped)} bar(s) extend beyond the y range and are cut at the frame (marked with a triangle; data.bars has clipped: true): values {listed}")
    if ya.contains(0.0):
        cv.el("line", [("x1", num(left)), ("y1", num(base)), ("x2", num(plot_right)), ("y2", num(base)), ("stroke-width", "1.25")], stroke="fg")
    skipped = 0
    if vlabels:
        for cx, yv, slot, text in label_jobs:
            if text_width(text, VALUE_SIZE) > slot + 6:
                skipped += 1
                continue
            above = yv < base or (yv == base and not text.startswith("-"))
            ty = yv - 4 if above else yv + VALUE_SIZE + 2
            cv.text(cx, min(max(ty, top + VALUE_SIZE), bottom - 2), text, size=VALUE_SIZE, anchor="middle")
        if skipped:
            notes.append(f"{skipped} value label(s) were left out because the bar is narrower than the label")
    cv.end("g")
    cv.el("rect", [("x", num(left)), ("y", num(top)), ("width", num(pw)), ("height", num(ph)), ("stroke-width", "1")], fill="none", stroke="frame")
    ticks = [f"M{num(left)} {num(ya.px(t))}h{-TICK_LEN}" for t in ya.ticks]
    if hist:
        ticks += [f"M{num(xa.px(t))} {num(bottom)}v{TICK_LEN}" for t in xa.ticks]
    else:
        ticks += [f"M{num(left + (i + 0.5) * pw / len(cats))} {num(bottom)}v{TICK_LEN}" for i in range(len(cats))]
    cv.el("path", [("d", "".join(ticks)), ("stroke-width", "1")], fill="none", stroke="frame")
    for t, lab, show in zip(ya.ticks, ya.labels, ya.shown):
        if show:
            cv.text(left - TICK_LEN - TICK_GAP, ya.px(t) + 4, lab, size=TICK_SIZE, anchor="end", role="muted")
    cat_shown: list[bool] = []
    cat_text: list[str] = []
    if hist:
        for t, lab, show in zip(xa.ticks, xa.labels, xa.shown):
            if show:
                cv.text(xa.px(t), bottom + TICK_LEN + TICK_GAP + 10, lab, size=TICK_SIZE, anchor="middle", role="muted")
    else:
        bandw = pw / len(cats)
        cat_text = [texts.fit(c, CATEGORY_LABEL_CHARS * 0.6 * TICK_SIZE, TICK_SIZE, f"category {i + 1}") for i, c in enumerate(cats)]
        centres = [left + (i + 0.5) * bandw for i in range(len(cats))]
        cat_shown = thin(centres, label_widths(cat_text, TICK_SIZE), LABEL_GAP_X)
        for c, x, show in zip(cat_text, centres, cat_shown):
            if show:
                cv.text(x, bottom + TICK_LEN + TICK_GAP + 10, c, size=TICK_SIZE, anchor="middle", role="muted")
        if not all(cat_shown):
            notes.append(f"{len(cats) - sum(cat_shown)} of {len(cats)} category labels were left out so that the others do not overlap")
    if labels["y_label"]:
        cv.text(left - TICK_LEN - TICK_GAP - math.ceil(max_w) - 6, (top + bottom) / 2, texts.fit(labels["y_label"], ph, LABEL_SIZE, "y label"), size=LABEL_SIZE, anchor="middle", rotate=-90)
    if labels["x_label"]:
        cv.text(left + pw / 2, H - PAD - 2, texts.fit(labels["x_label"], pw, LABEL_SIZE, "x label"), size=LABEL_SIZE, anchor="middle")
    spot = None
    if legend is not None:
        names, lw, lh = legend
        if outside:
            spot, lx, ly = "right", W - PAD - lw, top
        else:
            spot, lx, ly = _bar_legend_spot(leg, left, top, plot_right, bottom, lw, lh, rects)
        draw_legend(cv, lx, ly, lw, lh, names, entries, ["bar"] * len(entries), False)
    if texts.truncated:
        notes.append(f"{len(texts.truncated)} text(s) were shortened with an ellipsis to fit: " + "; ".join(f"{t['where']} ({t['characters']} characters)" for t in texts.truncated[:6]))

    clipped_set = {b for b, _, _ in clipped}
    word = "Histogram" if hist else ("Stacked bar chart" if stack == "stacked" and len(series_list) > 1 else "Bar chart")
    if hist:
        what = f"of {spec['value_name'][:60]!r}: {len(spec['counts'])} bins from {spec['edges'][0]:.6g} to {spec['edges'][-1]:.6g}, {spec['n_in_bins']} of {spec['n']} values in bins, heights as {spec['heights_are']}"
    else:
        listed = ", ".join(repr(s.name[:40]) for s in series_list)
        what = f"of {len(series_list)} series ({listed}) over {len(cats)} categories"
    desc = f"{word} {what}; y linear from {ya.lo:.6g} to {ya.hi:.6g}; {drawn} bars."[:1500]
    title = labels["title"] or f"{word} {what}"[:200]
    svg = cv.document(title, desc)
    data = {
        "tool": "plot_bar", "mode": mode, "stacking": stack, "width": W, "height": H, "theme": theme, "bars_drawn": drawn, "zero_height_bars": zero_height,
        "clipped_bars": len({b for b, _, _ in clipped}),
        "plot_area": {"x0": left, "y0": top, "width": pw, "height": ph},
        "axes": {"y": {**ya.describe(), "mapping": ya.mapping("y")}, **({"x": {**xa.describe(), "mapping": xa.mapping("x")}} if hist else {})},
        "legend": None if legend is None else {"position": spot, "entries": legend[0]},
        "truncated_text": texts.truncated, "element_id_prefix": pid,
        "bars": [{"x": x0, "y": y0, "width": bw_, "height": bh, "series": j, "value": v, "clipped": b in clipped_set, "zero_height": bh <= 0}
                 for b, (x0, y0, bw_, bh, j, v) in enumerate(rects)][:2000],
    }
    if hist:
        data["histogram"] = {
            "value_column": spec["value_name"], "bin_edges": spec["edges"], "counts": spec["counts"], "heights": spec["heights"], "heights_are": spec["heights_are"],
            "rule": spec["rule"], "values": spec["n"], "values_in_bins": spec["n_in_bins"], "below_first_edge": spec["below"], "above_last_edge": spec["above"],
            "bins_closed": "half-open [a, b) except the last bin, which is closed [a, b]",
        }
    else:
        data["categories"] = cats
        data["category_labels_shown"] = [c for c, s in zip(cat_text, cat_shown) if s]
        data["category_layout"] = {"band_px": pw / len(cats), "group_fraction": GROUP_FRACTION, "bar_px": (pw / len(cats)) * GROUP_FRACTION / (len(series_list) if stack == "grouped" else 1)}
        data["series"] = [{"name": s.name, "column": s.label, "values": s.y, "err": s.err, "colour_index": series_style(j)[0]} for j, s in enumerate(series_list)]
    return svg, data, drawn, zero_height


def _bar_legend_spot(choice_: str, left: float, top: float, right: float, bottom: float, w: float, h: float, rects) -> tuple[str, float, float]:
    spots = {
        "top_right": (right - LEGEND_INSET - w, top + LEGEND_INSET),
        "top_left": (left + LEGEND_INSET, top + LEGEND_INSET),
        "bottom_right": (right - LEGEND_INSET - w, bottom - LEGEND_INSET - h),
        "bottom_left": (left + LEGEND_INSET, bottom - LEGEND_INSET - h),
    }
    if choice_ in spots:
        return (choice_, *spots[choice_])
    best = None
    for name, (x, y) in spots.items():  # smallest bar area under the box; ties keep this order
        area = sum(max(0.0, min(x + w, rx + rw) - max(x, rx)) * max(0.0, min(y + h, ry + rh) - max(y, ry)) for rx, ry, rw, rh, _, _ in rects)
        if best is None or area < best[0] - 1e-9:
            best = (area, name, x, y)
    return best[1], best[2], best[3]


__all__ = ["bar"]
