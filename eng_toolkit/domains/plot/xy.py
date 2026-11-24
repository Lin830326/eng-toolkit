"""``plot_xy``: a line, scatter, step or stem chart of up to 20 series as a self-contained SVG."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from eng_toolkit.contract import ToolResult
from eng_toolkit.inline_image import INLINE_IMAGE_DESCRIPTION
from eng_toolkit.domains.plot._chart import Chart, Panel, render_xy
from eng_toolkit.domains.plot._common import (
    MAX_SERIES,
    SIZE_RANGE,
    Computed,
    JobHandle,
    band_param,
    choice,
    column_param,
    fail,
    flag,
    name_list,
    range_param,
    record_text,
    point_param,
    ref_lines,
    run,
    text_param,
    whole,
)
from eng_toolkit.domains.plot._data import check_one_source, csv_series, inline_series
from eng_toolkit.domains.plot._data import unwrap_phase as unwrap_series
from eng_toolkit.domains.plot._output import DEFAULT_FORMAT, FORMAT_TEXT, FORMATS, finish, wants_png
from eng_toolkit.domains.plot._server import server

KINDS = ("line", "scatter", "step", "stem")
SCALES = ("linear", "log")
LEGENDS = ("auto", "off", "top_right", "top_left", "bottom_right", "bottom_left", "right")
THEME_NAMES = ("auto", "light", "dark", "print")


def parse_kinds(raw: object) -> list[str]:
    """One kind, or a comma-separated kind per series."""
    if isinstance(raw, str) and "," in raw:
        if len(raw) > 400:
            fail("kind", f"'kind' is {len(raw)} characters long; at most 400", "input.out_of_range")
        parts = raw.split(",")
        if len(parts) > MAX_SERIES:
            fail("kind", f"'kind' lists {len(parts)} kinds; at most {MAX_SERIES}", "input.out_of_range")
        return [choice(p, "kind", KINDS) for p in parts]
    return [choice(raw, "kind", KINDS)]


@server.tool(
    name="xy",
    description=(
        "Draw a line, scatter, step or stem chart of up to 20 series as a deterministic SVG (plot.svg; inline in data.svg_inline when "
        "<= 150 kB) and, by default, plot.png 1600 px wide (data.png_path; needs the png extra). The PNG also comes back as an image "
        "in the reply (inline_image): hand over the PNG, not the SVG path. Also plot_data.json. Data: a CSV with a header row (e.g. "
        "another tool's CSV artifact) or inline JSON series. Log axes, fixed ranges, a right axis, reference lines, a band, one marked "
        "point, fill to zero, themes, unwrap_phase for a Bode phase. Dense series are min/max decimated."
    ),
)
def xy(
    data_file: Annotated[str, Field(description="CSV/TXT path with a header row, e.g. a tool's ws/<job>/filter_response.csv artifact. Give this or 'series'.")] = "",
    series: Annotated[str, Field(description='Inline JSON instead of a file: [{"name":"a","x":[0,1],"y":[2,3]}]; <=20 series, <=200000 points each; x optional.')] = "",
    x_column: Annotated[str, Field(description="CSV header name of the x column, e.g. 'frequency_hz'; blank = the first column. CSV mode only.")] = "",
    y_columns: Annotated[str, Field(description="Comma-separated CSV columns for the left axis, e.g. 'magnitude_db'; blank = every other numeric column.")] = "",
    y2_columns: Annotated[str, Field(description="Columns (CSV) or series names (inline) drawn against a right-hand axis, e.g. 'phase_deg'; blank = none.")] = "",
    kind: Annotated[str, Field(description="line, scatter, step (value holds until the next x) or stem; or one per series in order, e.g. 'line,scatter'.")] = "line",
    markers: Annotated[bool, Field(description="true puts a marker on every drawn point of line/step series (scatter and stem always have markers).")] = False,
    fill: Annotated[str, Field(description="none, or zero = shade between each line and y = 0, positive and negative in two tints (shear/moment diagrams).")] = "none",
    title: Annotated[str, Field(description="Chart title, plain text of at most 120 characters, e.g. 'Beam shear force'; blank = none.")] = "",
    x_label: Annotated[str, Field(description="x axis title including its unit, plain text <= 120 characters, e.g. 'Frequency (Hz)'.")] = "",
    y_label: Annotated[str, Field(description="Left axis title including its unit, e.g. 'Magnitude (dB)'; plain text <= 120 characters.")] = "",
    y2_label: Annotated[str, Field(description="Right axis title including its unit, e.g. 'Phase (deg)'; used together with y2_columns.")] = "",
    x_scale: Annotated[str, Field(description="linear or log; a log x axis refuses zero or negative x values (nothing is dropped silently).")] = "linear",
    y_scale: Annotated[str, Field(description="linear or log for the left axis; log refuses zero or negative y values.")] = "linear",
    x_range: Annotated[str, Field(description="Fixed x axis 'min,max': plain numbers in the axis unit, no unit text, e.g. '0,4000' (not '0,10 s'); blank = automatic.")] = "",
    y_range: Annotated[str, Field(description="Fixed left-axis 'min,max': plain numbers in the axis unit, no unit text, e.g. '-80,5'; blank = automatic; outside points are clipped.")] = "",
    hlines: Annotated[str, Field(description="Horizontal reference lines 'value,label;...' in left-axis units, e.g. '250,yield;-250,'; at most 10.")] = "",
    vlines: Annotated[str, Field(description="Vertical reference lines 'value,label;...' in x units, e.g. '1000,cutoff'; at most 10.")] = "",
    band: Annotated[str, Field(description="One shaded horizontal band 'lo,hi,label' in left-axis units, e.g. '-100,100,allowable'; blank = none.")] = "",
    marker: Annotated[str, Field(description="Optional: ring one point 'x,y,label' in x and left-axis units, e.g. '1200,45.5,duty point' (operating point); blank = none.")] = "",
    legend: Annotated[str, Field(description="auto (2+ series, emptiest corner), off, top_right, top_left, bottom_right, bottom_left or right (outside).")] = "auto",
    grid: Annotated[bool, Field(description="true draws grid lines at the major ticks (and log minor ticks); false draws none.")] = True,
    width: Annotated[int, Field(description="SVG width in px, 200..2400, e.g. 800.")] = 800,
    height: Annotated[int, Field(description="SVG height in px, 200..2400, e.g. 500.")] = 500,
    theme: Annotated[str, Field(description="auto (follows the viewer's light/dark setting), light, dark, or print (black and grey with dash patterns).")] = "auto",
    unwrap_phase: Annotated[bool, Field(description="true unwraps every series whose name contains 'phase' (degrees; 'rad' in the name: radians): no +-180 jumps in a Bode phase.")] = False,
    format: Annotated[str, Field(description=f"Picture file: {FORMAT_TEXT}.")] = DEFAULT_FORMAT,
    inline_image: Annotated[bool, Field(description=INLINE_IMAGE_DESCRIPTION)] = True,
) -> ToolResult:
    inputs = {
        "data_file": data_file, "series": record_text(series), "x_column": x_column, "y_columns": y_columns, "y2_columns": y2_columns, "kind": kind,
        "markers": markers, "fill": fill, "title": record_text(title), "x_label": record_text(x_label), "y_label": record_text(y_label),
        "y2_label": record_text(y2_label), "x_scale": x_scale, "y_scale": y_scale, "x_range": x_range, "y_range": y_range, "hlines": record_text(hlines),
        "vlines": record_text(vlines), "band": record_text(band), "marker": record_text(marker), "legend": legend, "grid": grid, "width": width, "height": height,
        "theme": theme, "unwrap_phase": unwrap_phase, "format": format,
    }

    def compute(handle: JobHandle) -> Computed:
        notes: list[str] = []
        kinds = parse_kinds(kind)
        fill_mode = choice(fill, "fill", ("none", "zero"))
        chart_markers = flag(markers, "markers")
        chart_grid = flag(grid, "grid")
        leg = choice(legend, "legend", LEGENDS)
        th = choice(theme, "theme", THEME_NAMES)
        unwrap = flag(unwrap_phase, "unwrap_phase")
        xs, ys = choice(x_scale, "x_scale", SCALES), choice(y_scale, "y_scale", SCALES)
        w, h = whole(width, "width", *SIZE_RANGE), whole(height, "height", *SIZE_RANGE)
        labels = {name: text_param(raw, name, notes=notes) for name, raw in (("title", title), ("x_label", x_label), ("y_label", y_label), ("y2_label", y2_label))}
        xr, yr = range_param(x_range, "x_range"), range_param(y_range, "y_range")
        h_lines, v_lines = ref_lines(hlines, "hlines", notes), ref_lines(vlines, "vlines", notes)
        bd = band_param(band, "band", notes)
        pt = point_param(marker, "marker", notes)
        fmt = choice(format, "format", FORMATS)
        xcol = column_param(x_column, "x_column")
        ycols, y2cols = name_list(y_columns, "y_columns"), name_list(y2_columns, "y2_columns")
        if set(ycols) & set(y2cols):
            fail("y2_columns", "a column cannot be on both axes")
        source = check_one_source(data_file, series)
        if source == "csv":
            wanted = [(c, "y_columns") for c in ycols] if ycols else None
            loaded, _ = csv_series(handle, data_file, xcol, wanted, notes, extra=[(c, "y2_columns") for c in y2cols])
        else:
            if xcol:
                fail("x_column", "'x_column' selects a CSV column; inline 'series' carry their own x")
            if ycols:
                fail("y_columns", "'y_columns' selects CSV columns; every inline series is plotted (use y2_columns to move some to the right axis)")
            loaded = inline_series(series, notes)
            known = {s.column for s in loaded}
            for c in y2cols:
                if c not in known:
                    fail("y2_columns", f"'y2_columns' names {c[:40]!r}, which is not the name of an inline series")
        if unwrap:
            unwrap_series(loaded, notes)
        for s in loaded:
            s.axis = "y2" if s.column in y2cols else "y"
        if len(kinds) > 1:
            if len(kinds) != len(loaded):
                fail("kind", f"'kind' lists {len(kinds)} kinds for {len(loaded)} series; give one kind, or one per series in order")
            for s, kk in zip(loaded, kinds):
                s.meta["kind"] = kk
        if y2cols and ys == "log":
            notes.append("the right axis is always linear")
        chart = Chart(
            tool="plot_xy", width=w, height=h, theme=th, kind=kinds[0], markers=chart_markers, fill=fill_mode, grid=chart_grid, legend=leg,
            title=labels["title"], x_label=labels["x_label"], x_scale=xs, x_range=xr, vlines=v_lines, point=pt,
            panels=[Panel(series=loaded, y_label=labels["y_label"], y_scale=ys, y_range=yr, y2_label=labels["y2_label"], hlines=h_lines, band=bd)],
        )
        out = render_xy(chart)
        return finish(handle, out.svg, out.data, notes + out.warnings,
                      {"points_drawn": out.points_drawn, "points_in": out.points_in, "series": len(loaded)}, "points_drawn", fmt)

    return run("plot_xy", inputs, compute, engines=("pypdfium2",) if wants_png(format) else ())


__all__ = ["xy"]
