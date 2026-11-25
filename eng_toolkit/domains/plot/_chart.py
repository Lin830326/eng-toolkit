"""Layout and drawing of x-y charts (one panel for ``plot_xy``, 2..6 stacked panels sharing x for ``plot_panels``).

Layout constants (px), all stated so a pixel position can be derived by hand:

* outer padding ``PAD`` = 12 on every side; a chart title adds ``TITLE_BAND`` = 28 at the top (font 16, bold, baseline at PAD + 16);
* x axis below the bottom panel: tick length ``TICK_LEN`` = 5, gap ``TICK_GAP`` = 4, tick labels font ``TICK_SIZE`` = 12 (baseline
  at plot bottom + 19); an x label adds ``LABEL_BAND`` = 20 (font 13).  Bottom margin = PAD + 21 (+ 20 with an x label);
* left margin = PAD (+ LABEL_BAND with a y label) + TICK_LEN + TICK_GAP + ceil(widest shown y tick label), where a label's width is
  estimated as 0.6 x 12 px per character (1.0 x 12 for wide characters);
* right margin = with a right axis: PAD + TICK_LEN + TICK_GAP + ceil(widest y2 label) (+ LABEL_BAND with a y2 label); else
  max(PAD, ceil(half the width of the last x tick label) + 2); plus the legend width + 10 when the legend sits outside;
* panels: each panel title adds ``PANEL_TITLE_BAND`` = 20, panels are ``PANEL_GAP`` = 14 apart and equally tall; a plot area
  narrower or lower than ``MIN_PLOT`` = 60 px is refused (``/width`` or ``/height``);
* tick targets: about one x tick per 90 px of width and one y tick per 45 px of panel height (2..12 and 2..10).

The plot area of each panel is clipped, so data outside a given range never draws outside the frame.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field

import numpy as np

from eng_toolkit.domains.plot._axes import Axis, label_widths, make_axis, seconds_axis, thin
from eng_toolkit.domains.plot._common import fail
from eng_toolkit.domains.plot._data import Series, cell_pointer
from eng_toolkit.domains.plot._decimate import decimate, thin_markers
from eng_toolkit.domains.plot._geom import CLIP_GROW, clip_polygon, clip_polyline, inside, length_inside
from eng_toolkit.domains.plot._svg import DASHES, MARKERS, Canvas, fit, marker_path, num, series_style, text_width

PAD = 12
TITLE_SIZE, TITLE_BAND = 16, 28
LABEL_SIZE, LABEL_BAND = 13, 20
TICK_SIZE, TICK_LEN, TICK_GAP = 12, 5, 4
X_TICK_BAND = TICK_LEN + TICK_GAP + TICK_SIZE  # 21
PANEL_TITLE_SIZE, PANEL_TITLE_BAND, PANEL_GAP = 13, 20, 14
LEGEND_SIZE, LEGEND_ROW, LEGEND_PAD, LEGEND_SAMPLE, LEGEND_TEXT_MAX, LEGEND_INSET = 12, 18, 8, 26, 200, 8
LEGEND_TEXT_WIDEST = 420  # the legend text budget grows with the plot width, up to this many px
MIN_PLOT = 60
LINE_WIDTH = 1.75
X_TICK_PX, Y_TICK_PX = 90, 45
LABEL_GAP_X, LABEL_GAP_Y = 8, 2
FILL_OPACITY_POS, FILL_OPACITY_NEG = 0.30, 0.14
BAND_OPACITY = 0.13
KIND_WORD = {"line": "Line", "scatter": "Scatter", "step": "Step", "stem": "Stem"}


@dataclass
class Panel:
    series: list[Series]
    title: str = ""
    y_label: str = ""
    y_scale: str = "linear"
    y_range: tuple[float, float] | None = None
    y_scale_param: str = "y_scale"
    y_range_param: str = "y_range"
    y2_label: str = ""
    hlines: list[tuple[float, str]] = field(default_factory=list)
    band: tuple[float, float, str] | None = None


@dataclass
class Chart:
    tool: str
    width: int
    height: int
    theme: str
    kind: str
    markers: bool
    fill: str
    grid: bool
    legend: str
    title: str
    x_label: str
    x_scale: str
    x_range: tuple[float, float] | None
    panels: list[Panel]
    vlines: list[tuple[float, str]] = field(default_factory=list)
    point: tuple[float, float, str] | None = None  # one marked point (plot_xy 'marker'): x, y on the left axis of the first panel, label


@dataclass
class Rendered:
    svg: str
    data: dict
    warnings: list[str]
    points_in: int
    points_drawn: int


class Texts:
    """Fits caller text into pixel widths and remembers what had to be shortened."""

    def __init__(self):
        self.truncated: list[dict] = []

    def fit(self, text: str, max_px: float, size: float, where: str) -> str:
        shown, cut = fit(text, max(max_px, 3 * size), size)
        if cut:
            self.truncated.append({"where": where, "characters": len(text), "shown": shown})
        return shown


def chart_id(*parts: object, arrays: list[np.ndarray] = ()) -> str:
    """A stable element-id prefix from the content (two charts on one HTML page do not share ids; same input, same id)."""
    h = hashlib.sha256(json.dumps([str(p) for p in parts], ensure_ascii=True).encode())
    for a in arrays:
        h.update(np.ascontiguousarray(a, dtype=float).tobytes())
    return "etk-" + h.hexdigest()[:10]


def _where(s: Series, index: int, which: str) -> tuple[str, str, dict]:
    """Pointer, human text and details for one data value of a series (``which`` = 'x' or 'y')."""
    lines = s.meta.get("lines")
    if lines is not None:
        col = s.x_name if which == "x" else s.label
        return cell_pointer(lines[index], col), f"column {col[:40]!r} line {lines[index]}", {"line": lines[index], "column": col[:120]}
    base = s.x_param if which == "x" else s.y_param
    return f"{base}/{index}", f"/{base}/{index}", {}


def check_positive(series: list[Series], which: str, scale_param: str) -> None:
    for s in series:
        values = s.x if which == "x" else s.y
        bad = values <= 0
        if bad.any():
            i = int(np.argmax(bad))
            pointer, text, details = _where(s, i, which)
            fail(pointer, f"'{scale_param}' is log but {text} holds {values[i]:g}; a log axis needs positive values (nothing is dropped silently: use a linear axis or remove the row)",
                 scale_param=scale_param, **details)


def _extent(values: list[np.ndarray], extra: list[float]) -> tuple[float, float]:
    lo = min([float(np.min(v)) for v in values] + extra)
    hi = max([float(np.max(v)) for v in values] + extra)
    return lo, hi


def _pts(xs: np.ndarray, ys: np.ndarray) -> str:
    return " ".join(f"{num(a)},{num(b)}" for a, b in zip(xs.tolist(), ys.tolist()))


def _dash(attrs: list, dash_index: int) -> list:
    if DASHES[dash_index]:
        attrs.append(("stroke-dasharray", DASHES[dash_index]))
    return attrs


def draw_markers(cv: Canvas, xs: np.ndarray, ys: np.ndarray, colour: int, marker: int) -> None:
    shape = MARKERS[marker]
    paths = []
    filled = True
    for a, b in zip(xs.tolist(), ys.tolist()):
        d, filled = marker_path(shape, a, b)
        paths.append(d)
    if not paths:
        return
    if filled:
        cv.el("path", [("d", "".join(paths))], fill=f"s{colour}")
    else:
        cv.el("path", [("d", "".join(paths)), ("stroke-width", "1.5")], fill="none", stroke=f"s{colour}")


def legend_text_budget(plot_width: float) -> float:
    """Pixels for one legend entry's text: 200, or when the plot is wide enough 45 % of its width less the swatch and padding (at most 420),
    so a name such as 'v(nout).phase_deg (right axis)' is not cut on a 800 px chart."""
    return float(min(LEGEND_TEXT_WIDEST, max(LEGEND_TEXT_MAX, 0.45 * plot_width - (2 * LEGEND_PAD + LEGEND_SAMPLE + 6))))


def legend_box(entries: list[tuple[str, int]], texts: Texts, where: str, budget: float = LEGEND_TEXT_MAX) -> tuple[list[str], float, float]:
    names = [texts.fit(name, budget, LEGEND_SIZE, f"{where} legend entry {i + 1}") for i, (name, _) in enumerate(entries)]
    tw = max(text_width(n, LEGEND_SIZE) for n in names)
    width = 2 * LEGEND_PAD + LEGEND_SAMPLE + 6 + tw
    height = 2 * LEGEND_PAD + len(entries) * LEGEND_ROW - 4
    return names, math.ceil(width), height


def draw_legend(cv: Canvas, x: float, y: float, w: float, h: float, names: list[str], entries: list[tuple[str, int]], kinds: list[str], markers: bool) -> None:
    cv.el("rect", [("x", num(x)), ("y", num(y)), ("width", num(w)), ("height", num(h)), ("fill-opacity", "0.88"), ("rx", "3")], fill="bg", stroke="frame")
    for row, (name, (_, index), kind) in enumerate(zip(names, entries, kinds)):
        colour, dash, marker = series_style(index)
        cy = y + LEGEND_PAD + row * LEGEND_ROW + 7
        sx = x + LEGEND_PAD
        if kind == "bar":
            swatch = [("x", num(sx + 6)), ("y", num(cy - 6)), ("width", "14"), ("height", "12"), ("stroke-width", "1"), ("fill-opacity", "0.85")]
            cv.el("rect", _dash(swatch, dash), fill=f"s{colour}", stroke="fg")
        elif kind != "scatter":
            cv.el("line", _dash([("x1", num(sx)), ("y1", num(cy)), ("x2", num(sx + LEGEND_SAMPLE)), ("y2", num(cy)), ("stroke-width", num(LINE_WIDTH))], dash), stroke=f"s{colour}")
        if markers or kind in ("scatter", "stem"):
            draw_markers(cv, np.array([sx + LEGEND_SAMPLE / 2]), np.array([cy]), colour, marker)
        cv.text(sx + LEGEND_SAMPLE + 6, cy + 4, name, size=LEGEND_SIZE)


def _legend_spot(choice: str, left: float, top: float, right: float, bottom: float, w: float, h: float,
                 segments: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray], marks: tuple[np.ndarray, np.ndarray],
                 avoid: list[tuple[float, float, float, float]] = ()) -> tuple[str, float, float]:
    """The legend corner: ``choice``, or for 'auto' the inside corner covering the least drawing.  Score = length (px) of drawn line
    and stem segments inside the box + 10 per marker centre inside + 1000 per label box it would cover; ties keep the order
    top right, top left, bottom right, bottom left."""
    spots = {
        "top_right": (right - LEGEND_INSET - w, top + LEGEND_INSET),
        "top_left": (left + LEGEND_INSET, top + LEGEND_INSET),
        "bottom_right": (right - LEGEND_INSET - w, bottom - LEGEND_INSET - h),
        "bottom_left": (left + LEGEND_INSET, bottom - LEGEND_INSET - h),
    }
    if choice in spots:
        return (choice, *spots[choice])
    mx, my = marks
    best = None
    for name, (x, y) in spots.items():
        score = length_inside(segments, (x, y, x + w, y + h))
        score += 10.0 * (int(np.count_nonzero((mx >= x) & (mx <= x + w) & (my >= y) & (my <= y + h))) if mx.size else 0)
        score += 1000.0 * sum(1 for a, b, c, d in avoid if a < x + w and c > x and b < y + h and d > y)
        if best is None or score < best[0] - 1e-9:
            best = (score, name, x, y)
    return best[1], best[2], best[3]


def place_label(cv: Canvas, text: str, spots: list[tuple[float, float, str]], frame: tuple[float, float, float, float],
                placed: list[tuple[float, float, float, float]], warnings: list[str], what: str) -> bool:
    """Draw a reference-line label (font 11) at the first candidate (x, baseline y, anchor) whose box lies inside ``frame`` and
    overlaps no label placed before; warn when none fits."""
    width = text_width(text, 11)
    for x, y, anchor in spots:
        x0 = x - width if anchor == "end" else x
        box = (x0, y - 10, x0 + width, y + 2)
        if box[0] >= frame[0] and box[2] <= frame[2] and box[1] >= frame[1] and box[3] <= frame[3] and not any(
            a < box[2] and c > box[0] and b < box[3] and d > box[1] for a, b, c, d in placed
        ):
            cv.text(x, y, text, size=11, anchor=anchor, role="ref")
            placed.append(box)
            return True
    warnings.append(f"the label of {what} could not be placed inside the plot area without overlapping another label; it is left out")
    return False


def series_stats(s: Series) -> dict:
    return {"x_min": float(np.min(s.x)), "x_max": float(np.max(s.x)), "y_min": float(np.min(s.y)), "y_max": float(np.max(s.y)), "y_mean": float(np.mean(s.y))}


def render_xy(chart: Chart) -> Rendered:
    warnings: list[str] = []
    texts = Texts()
    all_series = [s for p in chart.panels for s in p.series]
    order = {id(s): i for i, s in enumerate(all_series)}  # global series index: colour, dash and marker continue across panels
    W, H = chart.width, chart.height

    # ---------------------------------------------------------------- checks that need the whole chart
    if chart.x_scale == "log":
        check_positive(all_series, "x", "x_scale")
        for i, (v, _) in enumerate(chart.vlines):
            if v <= 0:
                fail(f"vlines/{i}", f"vline {v:g} cannot be drawn on a log x axis (it needs a positive value)")
    kind_of = {id(s): s.meta.get("kind", chart.kind) for s in all_series}
    kinds_used = sorted(set(kind_of.values()))
    if chart.fill == "zero" and not set(kinds_used) <= {"line", "step"}:
        fail("fill", "fill to zero goes with kind line or step")
    # markers need room: automatic ranges get a 3 % margin so no marker or stem sits on the frame
    margin = 0.03 if (chart.markers or {"scatter", "stem"} & set(kinds_used)) else 0.0
    for p in chart.panels:
        if p.y_scale == "log":
            check_positive([s for s in p.series if s.axis == "y"], "y", p.y_scale_param)
            if chart.fill == "zero":
                fail("fill", "fill to zero needs a linear y axis (zero is not on a log axis)")
            for i, (v, _) in enumerate(p.hlines):
                if v <= 0:
                    fail(f"hlines/{i}", f"hline {v:g} cannot be drawn on a log y axis (it needs a positive value)")
            if p.band is not None and p.band[0] <= 0:
                fail("band", "the band needs positive limits on a log y axis")

    # ---------------------------------------------------------------- x axis
    x_target = max(2, min(12, round((W - 2 * PAD - 60) / X_TICK_PX)))
    if chart.point is not None:
        px_, py_, _ = chart.point
        if chart.x_scale == "log" and px_ <= 0:
            fail("marker", f"the marked point's x {px_:g} cannot be drawn on a log x axis (it needs a positive value)")
        if chart.panels[0].y_scale == "log" and py_ <= 0:
            fail("marker", f"the marked point's y {py_:g} cannot be drawn on a log y axis (it needs a positive value)")
    xlo, xhi = _extent([s.x for s in all_series], [v for v, _ in chart.vlines] + ([chart.point[0]] if chart.point is not None else []))
    x_axis = make_axis(chart.x_scale, xlo, xhi, x_target, chart.x_range, param="x_range" if chart.x_range else all_series[0].x_param, what="x axis", margin=margin)
    seconds_axis(x_axis, chart.x_label, "x axis")
    warnings += x_axis.notes

    # ---------------------------------------------------------------- vertical layout
    top = PAD + (TITLE_BAND if chart.title else 0)
    bottom_margin = PAD + X_TICK_BAND + (LABEL_BAND if chart.x_label else 0)
    n = len(chart.panels)
    avail = H - top - bottom_margin - (n - 1) * PANEL_GAP - sum(PANEL_TITLE_BAND for p in chart.panels if p.title)
    ph = avail / n
    if ph < MIN_PLOT:
        fail("height", f"a height of {H} px leaves {ph:.0f} px per plot area; at least {MIN_PLOT} px are needed (raise 'height' or drop labels)", "input.out_of_range", needed_px=MIN_PLOT)
    boxes = []
    y = top
    for p in chart.panels:
        if p.title:
            y += PANEL_TITLE_BAND
        boxes.append((y, y + ph))
        y += ph + PANEL_GAP

    # ---------------------------------------------------------------- y axes per panel
    y_target = max(2, min(10, round(ph / Y_TICK_PX)))
    yaxes: list[Axis] = []
    y2axes: list[Axis | None] = []
    for pi, p in enumerate(chart.panels):
        left_series = [s for s in p.series if s.axis == "y"]
        right_series = [s for s in p.series if s.axis == "y2"]
        if not left_series:
            fail("y2_columns", "every series is on the right axis; at least one must stay on the left (y) axis")
        extra = [v for v, _ in p.hlines] + ([p.band[0], p.band[1]] if p.band else []) + ([chart.point[1]] if chart.point is not None and pi == 0 else [])
        if chart.fill == "zero" or (any(kind_of[id(s)] == "stem" for s in left_series) and p.y_scale == "linear"):
            extra.append(0.0)
        lo, hi = _extent([s.y for s in left_series], extra)
        what = f"panel {pi + 1} y axis" if n > 1 else "y axis"
        ax = make_axis(p.y_scale, lo, hi, y_target, p.y_range, param=p.y_range_param if p.y_range else left_series[0].y_param, what=what, margin=margin)
        seconds_axis(ax, p.y_label, what)
        ax.set_pixels(boxes[pi][1], boxes[pi][0])
        ax.shown = thin([ax.px(t) for t in ax.ticks], [TICK_SIZE] * len(ax.ticks), LABEL_GAP_Y)
        warnings += ax.notes
        yaxes.append(ax)
        if right_series:
            zero2 = chart.fill == "zero" or any(kind_of[id(s)] == "stem" for s in right_series)
            lo2, hi2 = _extent([s.y for s in right_series], [0.0] if zero2 else [])
            ax2 = make_axis("linear", lo2, hi2, y_target, None, param=right_series[0].y_param, what="right (y2) axis", margin=margin)
            seconds_axis(ax2, p.y2_label, "right (y2) axis")
            ax2.set_pixels(boxes[pi][1], boxes[pi][0])
            ax2.shown = thin([ax2.px(t) for t in ax2.ticks], [TICK_SIZE] * len(ax2.ticks), LABEL_GAP_Y)
            warnings += ax2.notes
            y2axes.append(ax2)
        else:
            y2axes.append(None)

    # ---------------------------------------------------------------- horizontal layout
    def widest(ax: Axis | None) -> float:
        if ax is None:
            return 0.0
        return max([w for w, s in zip(label_widths(ax.labels, TICK_SIZE), ax.shown) if s] or [0.0])

    max_w = max(widest(a) for a in yaxes)
    max_w2 = max(widest(a) for a in y2axes)
    any_ylabel = any(p.y_label for p in chart.panels)
    any_y2 = any(a is not None for a in y2axes)
    any_y2label = any(p.y2_label for p, a in zip(chart.panels, y2axes) if a is not None)
    left = PAD + (LABEL_BAND if any_ylabel else 0) + TICK_LEN + TICK_GAP + math.ceil(max_w)
    if any_y2:
        right = PAD + TICK_LEN + TICK_GAP + math.ceil(max_w2) + (LABEL_BAND if any_y2label else 0)
    else:
        last_w = text_width(x_axis.labels[-1], TICK_SIZE) if x_axis.labels else 0.0
        right = max(PAD, math.ceil(last_w / 2) + 2)

    # legends: which panels show one, their sizes, inside or outside
    budget = legend_text_budget(W - left - right)
    legends = []
    for pi, p in enumerate(chart.panels):
        entries = [(s.name + (" (right axis)" if s.axis == "y2" else ""), order[id(s)]) for s in p.series]
        show = chart.legend != "off" and not (chart.legend == "auto" and len(entries) == 1)
        if show:
            names, lw, lh = legend_box(entries, texts, f"panel {pi + 1}" if n > 1 else "chart", budget)
            legends.append((entries, names, lw, lh))
        else:
            legends.append(None)
    est_pw = W - left - right
    outside = chart.legend == "right" or any(
        lg is not None and (lg[3] > ph - 2 * LEGEND_INSET or lg[2] > 0.5 * est_pw) for lg in legends
    )
    legend_w = max([lg[2] for lg in legends if lg is not None] or [0])
    if outside and legend_w:
        right += legend_w + 10
        if chart.legend not in ("right",):
            warnings.append("the legend does not fit inside the plot area and was placed to the right of it")
    pw = W - left - right
    if pw < MIN_PLOT:
        fail("width", f"a width of {W} px leaves {pw:.0f} px for the plot area after the labels and legend; at least {MIN_PLOT} px are needed", "input.out_of_range", needed_px=MIN_PLOT)
    x_axis.set_pixels(left, left + pw)
    x_axis.shown = thin([x_axis.px(t) for t in x_axis.ticks], label_widths(x_axis.labels, TICK_SIZE), LABEL_GAP_X)
    plot_right = left + pw

    # ---------------------------------------------------------------- drawing
    pid = chart_id(chart.tool, chart.kind, chart.theme, W, H, chart.title, chart.x_label, chart.x_scale, chart.x_range, chart.fill, chart.markers, chart.grid, chart.legend,
                   chart.vlines, *([("marked point", chart.point)] if chart.point is not None else []), [(p.title, p.y_label, p.y_scale, p.y_range, p.y2_label, p.hlines, p.band, [(s.name, s.axis, kind_of[id(s)]) for s in p.series]) for p in chart.panels],
                   arrays=[a for s in all_series for a in (s.x, s.y)])
    cv = Canvas(W, H, chart.theme, pid)
    cv.el("rect", [("x", "0"), ("y", "0"), ("width", str(W)), ("height", str(H))], fill="bg")
    cv.begin("defs", [])
    filled_axes = []  # per panel: which axes ('y', 'y2') carry a filled series
    for pi, (ptop, pbot) in enumerate(boxes):
        cv.begin("clipPath", [("id", f"{pid}-clip{pi}")])
        cv.el("rect", [("x", num(left)), ("y", num(ptop)), ("width", num(pw)), ("height", num(pbot - ptop))])
        cv.end("clipPath")
        axes_here = sorted({s.axis for s in chart.panels[pi].series}) if chart.fill == "zero" else []
        filled_axes.append(axes_here)
        for axis_name in axes_here:  # the two-tint split of each axis at that axis's own zero
            zax = yaxes[pi] if axis_name == "y" else y2axes[pi]
            tag = "" if axis_name == "y" else "r"
            zero = min(max(zax.px(0.0), ptop), pbot)
            cv.begin("clipPath", [("id", f"{pid}-{tag}pos{pi}")])
            cv.el("rect", [("x", num(left)), ("y", num(ptop)), ("width", num(pw)), ("height", num(zero - ptop))])
            cv.end("clipPath")
            cv.begin("clipPath", [("id", f"{pid}-{tag}neg{pi}")])
            cv.el("rect", [("x", num(left)), ("y", num(zero)), ("width", num(pw)), ("height", num(pbot - zero))])
            cv.end("clipPath")
    cv.end("defs")

    if chart.title:
        cv.text(W / 2, PAD + TITLE_SIZE, texts.fit(chart.title, W - 2 * PAD, TITLE_SIZE, "title"), size=TITLE_SIZE, anchor="middle", weight="600")

    series_out = []
    marked_out = None
    total_in = total_drawn = 0
    decimated_any = False
    for pi, p in enumerate(chart.panels):
        ptop, pbot = boxes[pi]
        ya, y2a = yaxes[pi], y2axes[pi]
        rect = (left - CLIP_GROW, ptop - CLIP_GROW, plot_right + CLIP_GROW, pbot + CLIP_GROW)
        mark_rect = (left - 8, ptop - 8, plot_right + 8, pbot + 8)
        frame_box = (left, ptop, plot_right, pbot)
        cv.begin("g", [("id", f"{pid}-panel{pi}")])
        if p.title:
            cv.text(left, ptop - 6, texts.fit(p.title, pw, PANEL_TITLE_SIZE, f"panel {pi + 1} title"), size=PANEL_TITLE_SIZE, weight="600")
        placed: list[tuple[float, float, float, float]] = []  # boxes of labels drawn inside the plot area
        # band
        if p.band is not None:
            b0, b1 = ya.px(p.band[0]), ya.px(p.band[1])
            yb0, yb1 = max(min(b0, b1), ptop), min(max(b0, b1), pbot)
            if yb1 > yb0:
                cv.el("rect", [("x", num(left)), ("y", num(yb0)), ("width", num(pw)), ("height", num(yb1 - yb0)), ("fill-opacity", str(BAND_OPACITY))], fill="band")
                if p.band[2]:
                    shown = texts.fit(p.band[2], pw - 8, 11, "band label")
                    by = min(yb0 + 13, pbot - 3)
                    cv.text(left + 4, by, shown, size=11, role="muted")
                    placed.append((left + 4, by - 10, left + 4 + text_width(shown, 11), by + 2))
            else:
                warnings.append("the band lies outside the y range and is not visible")
        # grid
        if chart.grid:
            minor = [f"M{num(x_axis.px(t))} {num(ptop)}V{num(pbot)}" for t in x_axis.minor] + [f"M{num(left)} {num(ya.px(t))}H{num(plot_right)}" for t in ya.minor]
            if minor:
                cv.el("path", [("d", "".join(minor)), ("stroke-width", "1")], fill="none", stroke="minor")
            major = [f"M{num(x_axis.px(t))} {num(ptop)}V{num(pbot)}" for t in x_axis.ticks] + [f"M{num(left)} {num(ya.px(t))}H{num(plot_right)}" for t in ya.ticks]
            cv.el("path", [("d", "".join(major)), ("stroke-width", "1")], fill="none", stroke="grid")
        # zero line of every axis that carries a filled series (the right axis's dashed)
        for axis_name in filled_axes[pi]:
            zax = ya if axis_name == "y" else y2a
            if zax.contains(0.0):
                z = zax.px(0.0)
                attrs = [("x1", num(left)), ("y1", num(z)), ("x2", num(plot_right)), ("y2", num(z)), ("stroke-width", "1.25")]
                cv.el("line", attrs + ([("stroke-dasharray", "4 3")] if axis_name == "y2" else []), stroke="fg")
        # series (clipped to the plot area, after exact geometric clipping to the area grown by CLIP_GROW)
        cv.begin("g", [("clip-path", f"url(#{pid}-clip{pi})")])
        seg_parts: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = []
        mark_x, mark_y = [], []
        for s in p.series:
            gi = order[id(s)]
            colour, dash, marker = series_style(gi)
            ax = ya if s.axis == "y" else y2a
            kind = kind_of[id(s)]
            px = x_axis.pxa(s.x)
            py = ax.pxa(s.y)
            line_keep = mark_keep = None
            line_method = mark_method = None
            if kind in ("line", "step"):
                line_keep, line_method = decimate(s.x, s.y, px, left, pw)
                if chart.markers or len(s.y) == 1:
                    mark_keep, mark_method, _ = thin_markers(s.x, s.y, px, py, left, ptop, pw)
            else:
                mark_keep, mark_method, _ = thin_markers(s.x, s.y, px, py, left, ptop, pw, stems=kind == "stem")
            parts = [k for k in (line_keep, mark_keep) if k is not None]
            drawn = np.unique(np.concatenate(parts))
            visible = int(np.count_nonzero((s.x >= x_axis.lo) & (s.x <= x_axis.hi) & (s.y >= ax.lo) & (s.y <= ax.hi)))
            drawn_y = s.y[drawn]
            entry = {
                "name": s.name, "column": s.label, "x_column": s.x_name, "axis": s.axis, "panel": pi + 1, "colour_index": colour, "dash": DASHES[dash] or "solid",
                "marker": MARKERS[marker], "kind": kind, "points_in": int(len(s.y)), "points_drawn": int(len(drawn)),
                "line_vertices": None if line_keep is None else int(len(line_keep)), "markers_drawn": None if mark_keep is None else int(len(mark_keep)),
                "phase_unwrapped": s.meta.get("unwrapped"), "decimated": line_method is not None, "markers_thinned": mark_method is not None,
                "method": "; ".join(m for m in (line_method, mark_method) if m) or "all points drawn",
                "drawn_y_min": float(np.min(drawn_y)), "drawn_y_max": float(np.max(drawn_y)), "points_outside_axes": int(len(s.y)) - visible, **series_stats(s),
            }
            series_out.append(entry)
            total_in += len(s.y)
            total_drawn += len(drawn)
            decimated_any |= line_method is not None
            if mark_method is not None:
                warnings.append(f"marker plot thinned: {len(mark_keep)} of {len(s.y)} markers of {s.name[:40]!r} drawn ({mark_method})")
            if len(s.y) - visible:
                warnings.append(f"{len(s.y) - visible} of {len(s.y)} points of {s.name[:40]!r} lie outside the axis range and are clipped")
            width_attr = [("stroke-width", num(LINE_WIDTH)), ("stroke-linejoin", "round"), ("stroke-linecap", "round")]
            if line_keep is not None:
                lx, ly = px[line_keep], py[line_keep]
                vx, vy = s.x[line_keep], s.y[line_keep]
                if kind == "step" and len(lx) > 1:
                    lx, ly = np.repeat(lx, 2)[1:], np.repeat(ly, 2)[:-1]
                    vx, vy = np.repeat(vx, 2)[1:], np.repeat(vy, 2)[:-1]

                def exact_point(i: int):
                    return x_axis.exact_px(vx[i]), ax.exact_px(vy[i])

                if chart.fill == "zero":
                    tag = "" if s.axis == "y" else "r"
                    z = min(max(ax.px(0.0), rect[1]), rect[3])  # the closing edges are vertical, so clamping the base changes nothing inside
                    def exact_fill_point(i: int):
                        if i == 0 or i == len(lx) + 1:
                            index = 0 if i == 0 else -1
                            return x_axis.exact_px(vx[index]), ax.exact_px(0.0)
                        return exact_point(i - 1)

                    gx, gy = clip_polygon(np.r_[lx[0], lx, lx[-1]], np.r_[z, ly, z], rect, exact_point=exact_fill_point)
                    if len(gx) >= 3:
                        area = "M" + "L".join(f"{num(a)} {num(b)}" for a, b in zip(gx.tolist(), gy.tolist())) + "Z"
                        cv.el("path", [("d", area), ("fill-opacity", str(FILL_OPACITY_POS)), ("clip-path", f"url(#{pid}-{tag}pos{pi})")], fill=f"s{colour}")
                        cv.el("path", [("d", area), ("fill-opacity", str(FILL_OPACITY_NEG)), ("clip-path", f"url(#{pid}-{tag}neg{pi})")], fill=f"s{colour}")
                for rx, ry in clip_polyline(lx, ly, rect, exact_point=exact_point):
                    cv.el("polyline", _dash([("points", _pts(rx, ry)), *width_attr], dash), fill="none", stroke=f"s{colour}")
                    seg_parts.append((rx[:-1], ry[:-1], rx[1:], ry[1:]))
            if mark_keep is not None:
                mx, my = px[mark_keep], py[mark_keep]
                if kind == "stem":
                    base = ax.px(0.0) if ax.scale == "linear" else ax.p0
                    sx_ok = ((mx >= rect[0]) & (mx <= rect[2])
                             & (np.minimum(base, my) <= rect[3]) & (np.maximum(base, my) >= rect[1]))
                    top_y = np.clip(my[sx_ok], rect[1], rect[3])
                    base_y = min(max(base, rect[1]), rect[3])
                    stems = "".join(f"M{num(a)} {num(base_y)}V{num(b)}" for a, b in zip(mx[sx_ok].tolist(), top_y.tolist()))
                    if stems:
                        cv.el("path", _dash([("d", stems), ("stroke-width", "1.25")], dash), fill="none", stroke=f"s{colour}")
                    seg_parts.append((mx[sx_ok], np.full(int(sx_ok.sum()), base_y), mx[sx_ok], top_y))
                vis = inside(mx, my, mark_rect)
                mx, my = mx[vis], my[vis]
                draw_markers(cv, mx, my, colour, marker)
                mark_x.append(mx)
                mark_y.append(my)
        cv.end("g")
        # reference lines (inside the clip), their labels outside it but inside the frame
        for v, _ in p.hlines:
            if ya.contains(v):
                yv = ya.px(v)
                cv.el("line", [("x1", num(left)), ("y1", num(yv)), ("x2", num(plot_right)), ("y2", num(yv)), ("stroke-width", "1.25"), ("stroke-dasharray", "6 4")], stroke="ref")
        for v, _ in chart.vlines:
            if x_axis.contains(v):
                xv = x_axis.px(v)
                cv.el("line", [("x1", num(xv)), ("y1", num(ptop)), ("x2", num(xv)), ("y2", num(pbot)), ("stroke-width", "1.25"), ("stroke-dasharray", "6 4")], stroke="ref")
        for i, (v, label) in enumerate(p.hlines):
            if not ya.contains(v):
                warnings.append(f"hline {v:g} is outside the y range and is not visible")
                continue
            if label:
                yv = ya.px(v)
                shown = texts.fit(label, pw * 0.6, 11, f"hline {i + 1} label")
                spots = [(plot_right - 4, yv - 4, "end"), (plot_right - 4, yv + 13, "end"), (left + 4, yv - 4, "start"), (left + 4, yv + 13, "start")]
                place_label(cv, shown, spots, frame_box, placed, warnings, f"hline {v:g}")
        if pi == 0 and chart.point is not None:
            mx_, my_, mlabel = chart.point
            if x_axis.contains(mx_) and ya.contains(my_):
                mpx, mpy = x_axis.px(mx_), ya.px(my_)
                ring = f"M{num(mpx - 7)} {num(mpy)}a7 7 0 1 0 14 0a7 7 0 1 0 -14 0Z"
                cv.el("path", [("d", ring), ("stroke-width", "2")], fill="none", stroke="fg")
                cv.el("circle", [("cx", num(mpx)), ("cy", num(mpy)), ("r", "2.5")], fill="fg")
                marked_out = {"x": mx_, "y": my_, "label": mlabel, "x_px": mpx, "y_px": mpy}
                if mlabel:
                    shown = texts.fit(mlabel, pw * 0.5, 11, "marker label")
                    spots = [(mpx + 11, mpy - 9, "start"), (mpx + 11, mpy + 17, "start"), (mpx - 11, mpy - 9, "end"), (mpx - 11, mpy + 17, "end")]
                    place_label(cv, shown, spots, frame_box, placed, warnings, "the marked point")
            else:
                warnings.append(f"the marked point ({mx_:g}, {my_:g}) is outside the axis ranges and is not drawn")
        if pi == 0:
            for i, (v, label) in enumerate(chart.vlines):
                if not x_axis.contains(v):
                    warnings.append(f"vline {v:g} is outside the x range and is not visible")
                    continue
                if label:
                    xv = x_axis.px(v)
                    shown = texts.fit(label, max(pw * 0.45, 40), 11, f"vline {i + 1} label")
                    spots = [(xv + 4, ptop + 13, "start"), (xv - 4, ptop + 13, "end"), (xv + 4, pbot - 4, "start"), (xv - 4, pbot - 4, "end")]
                    place_label(cv, shown, spots, frame_box, placed, warnings, f"vline {v:g}")
        # frame, ticks, labels
        cv.el("rect", [("x", num(left)), ("y", num(ptop)), ("width", num(pw)), ("height", num(pbot - ptop)), ("stroke-width", "1")], fill="none", stroke="frame")
        ticks = [f"M{num(left)} {num(ya.px(t))}h{-TICK_LEN}" for t in ya.ticks] + [f"M{num(x_axis.px(t))} {num(pbot)}v{TICK_LEN}" for t in x_axis.ticks]
        if y2a is not None:
            ticks += [f"M{num(plot_right)} {num(y2a.px(t))}h{TICK_LEN}" for t in y2a.ticks]
        cv.el("path", [("d", "".join(ticks)), ("stroke-width", "1")], fill="none", stroke="frame")
        for t, lab, show in zip(ya.ticks, ya.labels, ya.shown):
            if show:
                cv.text(left - TICK_LEN - TICK_GAP, ya.px(t) + 4, lab, size=TICK_SIZE, anchor="end", role="muted")
        if y2a is not None:
            for t, lab, show in zip(y2a.ticks, y2a.labels, y2a.shown):
                if show:
                    cv.text(plot_right + TICK_LEN + TICK_GAP, y2a.px(t) + 4, lab, size=TICK_SIZE, role="muted")
        if pi == n - 1:
            for t, lab, show in zip(x_axis.ticks, x_axis.labels, x_axis.shown):
                if show:
                    cv.text(x_axis.px(t), pbot + TICK_LEN + TICK_GAP + 10, lab, size=TICK_SIZE, anchor="middle", role="muted")
        mid = (ptop + pbot) / 2
        if p.y_label:
            lx = left - TICK_LEN - TICK_GAP - math.ceil(max_w) - 6
            cv.text(lx, mid, texts.fit(p.y_label, pbot - ptop, LABEL_SIZE, f"panel {pi + 1} y label" if n > 1 else "y label"), size=LABEL_SIZE, anchor="middle", rotate=-90)
        if y2a is not None and p.y2_label:
            rx = plot_right + TICK_LEN + TICK_GAP + math.ceil(max_w2) + 6
            cv.text(rx, mid, texts.fit(p.y2_label, pbot - ptop, LABEL_SIZE, "y2 label"), size=LABEL_SIZE, anchor="middle", rotate=90)
        lg = legends[pi]
        if lg is not None:
            entries, names, lw, lh = lg
            if outside:
                spot, lx0, ly0 = "right", W - PAD - legend_w, ptop
            else:
                segs = tuple(np.concatenate([part[j] for part in seg_parts]) if seg_parts else np.zeros(0) for j in range(4))
                marks = (np.concatenate(mark_x) if mark_x else np.zeros(0), np.concatenate(mark_y) if mark_y else np.zeros(0))
                spot, lx0, ly0 = _legend_spot(chart.legend, left, ptop, plot_right, pbot, lw, lh, segs, marks, placed)
            draw_legend(cv, lx0, ly0, lw, lh, names, entries, [kind_of[id(s)] for s in p.series], chart.markers)
            legends[pi] = (entries, names, lw, lh, spot)
        cv.end("g")
    if chart.x_label:
        cv.text(left + pw / 2, H - PAD - 2, texts.fit(chart.x_label, pw, LABEL_SIZE, "x label"), size=LABEL_SIZE, anchor="middle")

    # ---------------------------------------------------------------- disclosure and summaries
    if texts.truncated:
        warnings.append(f"{len(texts.truncated)} text(s) were shortened with an ellipsis to fit: " + "; ".join(f"{t['where']} ({t['characters']} characters)" for t in texts.truncated[:6]))
    if decimated_any:
        warnings.append("line or step series with more than 4 points per horizontal pixel were decimated (min/max envelope per pixel column; "
                        f"the line's extrema are kept): {total_in} points in, {total_drawn} drawn in all")
    hidden_x = len(x_axis.shown) - sum(x_axis.shown)
    names = [s.name for s in all_series]
    listed = ", ".join(repr(nm[:40]) for nm in names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")
    x_name = chart.x_label or all_series[0].x_name
    word = KIND_WORD[kinds_used[0]] if len(kinds_used) == 1 else "Mixed"
    title = chart.title or f"{word} chart of {listed}"[:200]
    axes_text = "; ".join(
        f"{('panel ' + str(i + 1) + ' ') if n > 1 else ''}y {a.scale} from {a.lo:.6g} to {a.hi:.6g}" + (f", right axis from {b.lo:.6g} to {b.hi:.6g}" if b is not None else "")
        for i, (a, b) in enumerate(zip(yaxes, y2axes))
    )
    desc = (
        f"{word} chart{' in ' + str(n) + ' panels sharing the x axis' if n > 1 else ''} of {len(names)} series ({listed}) against {x_name[:60]!r}; "
        f"x {x_axis.scale} from {x_axis.lo:.6g} to {x_axis.hi:.6g}; {axes_text}; {total_drawn} of {total_in} points drawn"
        + ("; fill to zero" if chart.fill == "zero" else "") + (f"; {len(chart.vlines)} vertical and {sum(len(p.hlines) for p in chart.panels)} horizontal reference lines" if chart.vlines or any(p.hlines for p in chart.panels) else "")
        + ("; shaded band" if any(p.band for p in chart.panels) else "") + "."
    )[:1500]
    svg = cv.document(title, desc)
    panels_out = []
    for pi, p in enumerate(chart.panels):
        ptop, pbot = boxes[pi]
        axes = {"x": {**x_axis.describe(), "mapping": x_axis.mapping("x")}, "y": {**yaxes[pi].describe(), "mapping": yaxes[pi].mapping("y")}}
        if y2axes[pi] is not None:
            axes["y2"] = {**y2axes[pi].describe(), "mapping": y2axes[pi].mapping("y2")}
        lg = legends[pi]
        panels_out.append({
            "panel": pi + 1, "title": p.title, "plot_area": {"x0": left, "y0": ptop, "width": pw, "height": pbot - ptop}, "axes": axes,
            "legend": None if lg is None else {"position": lg[4], "entries": lg[1]},
            "reference_lines": {"hlines": [{"value": v, "label": lab} for v, lab in p.hlines], "vlines": [{"value": v, "label": lab} for v, lab in chart.vlines]},
            "band": None if p.band is None else {"lo": p.band[0], "hi": p.band[1], "label": p.band[2]},
        })
    data = {
        "tool": chart.tool, "kind": kinds_used[0] if len(kinds_used) == 1 else kinds_used, "width": W, "height": H, "theme": chart.theme, "fill": chart.fill, "markers": chart.markers,
        "panels": panels_out, "series": series_out, "points_in": total_in, "points_drawn": total_drawn,
        "decimation": {"applied": decimated_any, "threshold_points_per_px": 4,
                       "rule": "lines and steps with more than 4 points per pixel of plot width keep the first, last, minimum and maximum point of each pixel column; "
                               "markers (scatter, stem, markers=true) above the same density keep one marker per occupied 2 x 2 px cell (larger cells beyond 20000 markers) plus the y extremes"},
        **({"marked_point": marked_out} if marked_out is not None else {}),
        "x_tick_labels_hidden_to_avoid_overlap": hidden_x, "truncated_text": texts.truncated, "element_id_prefix": pid,
        "layout": {"pad": PAD, "title_band": TITLE_BAND, "label_band": LABEL_BAND, "tick_len": TICK_LEN, "tick_gap": TICK_GAP, "tick_font": TICK_SIZE, "char_em": 0.6,
                   "panel_title_band": PANEL_TITLE_BAND, "panel_gap": PANEL_GAP, "legend_outside": bool(outside and legend_w)},
    }
    return Rendered(svg, data, warnings, total_in, total_drawn)


__all__ = ["Chart", "Panel", "Rendered", "Texts", "chart_id", "render_xy"]
