"""``plot_panels``: 2..6 vertically stacked panels sharing one x axis (Bode magnitude + phase, waveform + spectrum, S-N + residual)."""

from __future__ import annotations

import dataclasses
from typing import Annotated

from pydantic import Field

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.plot._chart import Chart, Panel, render_xy
from eng_toolkit.domains.plot._common import (
    MAX_SERIES,
    PANELS_RANGE,
    SIZE_RANGE,
    Computed,
    JobHandle,
    choice,
    column_param,
    fail,
    flag,
    range_param,
    record_text,
    run,
    split_records,
    text_param,
    whole,
)
from eng_toolkit.domains.plot._data import check_one_source, csv_series, inline_series
from eng_toolkit.domains.plot._output import FORMAT_TEXT, FORMATS, finish, wants_png
from eng_toolkit.domains.plot._server import server
from eng_toolkit.domains.plot.xy import KINDS, LEGENDS, SCALES, THEME_NAMES


def parse_panels(raw: object, notes: list[str]) -> list[tuple[str, list[str], str, str]]:
    records = split_records(raw, "panels", PANELS_RANGE[1])
    if len(records) < PANELS_RANGE[0]:
        fail("panels", f"'panels' needs {PANELS_RANGE[0]}..{PANELS_RANGE[1]} records 'title|y_columns|y_label|y_scale' separated by ';', got {len(records)} (use plot_xy for one panel)",
             "input.missing" if not records else "input.invalid")
    out = []
    for i, rec in enumerate(records):
        p = f"panels/{i}"
        fields = rec.split("|")
        if not 2 <= len(fields) <= 4:
            fail(p, f"panel {i + 1}: expected 'title|y_columns|y_label|y_scale' (2 to 4 fields separated by '|'), got {len(fields)} field(s)")
        title = text_param(fields[0], p, notes=notes)
        cols = [c.strip() for c in fields[1].split(",") if c.strip()]
        if not cols:
            fail(p, f"panel {i + 1}: name at least one column in the second field")
        if len(set(cols)) != len(cols):
            fail(p, f"panel {i + 1} names a column twice")
        y_label = text_param(fields[2] if len(fields) > 2 else "", p, notes=notes)
        scale = choice(fields[3] if len(fields) > 3 and fields[3].strip() else "linear", p, SCALES)
        out.append((title, cols, y_label, scale))
    total = sum(len(c) for _, c, _, _ in out)
    if total > MAX_SERIES:
        fail("panels", f"the panels name {total} series in all; at most {MAX_SERIES}", "input.out_of_range", limit=MAX_SERIES)
    return out


@server.tool(
    name="panels",
    description=(
        "Draw 2-6 vertically stacked panels that share one x axis (Bode magnitude + phase, waveform + spectrum, S-N curve + "
        "residual) as one deterministic SVG plus plot_data.json; format=png (or both) also writes plot.png, a 1600 px wide picture "
        "to paste into slides and reports (data.png_path; needs the png extra). 'panels' lists 'title|y_columns|y_label|y_scale' "
        "records; each panel gets its own y axis. Data: a CSV with a header row or inline JSON series; the same kinds, themes and decimation as plot_xy."
    ),
)
def panels(
    panels: Annotated[str, Field(description="2..6 records 'title|y_columns|y_label|y_scale' joined by ';', e.g. 'Gain|magnitude_db|dB|linear;Phase|phase_deg|deg'.")] = "",
    data_file: Annotated[str, Field(description="CSV/TXT path with a header row, e.g. ws/<job>/filter_response.csv from dsp_filter_design. Give this or 'series'.")] = "",
    series: Annotated[str, Field(description='Inline JSON series [{"name":"mag","x":[...],"y":[...]}]; panels pick them by name; <=20 series, <=200000 points.')] = "",
    x_column: Annotated[str, Field(description="CSV header name of the shared x column, e.g. 'frequency_hz'; blank = the first column. CSV mode only.")] = "",
    kind: Annotated[str, Field(description="line, scatter, step or stem for every panel; default line.")] = "line",
    markers: Annotated[bool, Field(description="true adds a marker on every drawn point of line/step series in all panels.")] = False,
    x_scale: Annotated[str, Field(description="linear or log for the shared x axis; log refuses zero or negative x (e.g. a 0 Hz row).")] = "linear",
    x_range: Annotated[str, Field(description="Fixed shared x range 'min,max': plain numbers in the axis unit, no unit text, e.g. '10,4000' (not '10 Hz,4 kHz'); blank = automatic.")] = "",
    x_label: Annotated[str, Field(description="Title of the shared x axis with its unit, e.g. 'Frequency (Hz)'; plain text <= 120 characters.")] = "",
    title: Annotated[str, Field(description="Title above all panels, plain text <= 120 characters, e.g. 'Butterworth low pass'; blank = none.")] = "",
    legend: Annotated[str, Field(description="auto (panels with 2+ series), off, top_right, top_left, bottom_right, bottom_left or right (outside).")] = "auto",
    grid: Annotated[bool, Field(description="true draws grid lines at the major ticks of every panel; false draws none.")] = True,
    width: Annotated[int, Field(description="SVG width in px, 200..2400, e.g. 900.")] = 800,
    height: Annotated[int, Field(description="SVG height in px, 200..2400, e.g. 700; split equally between the panels.")] = 700,
    theme: Annotated[str, Field(description="auto (follows light/dark), light, dark, or print (black and grey with dash patterns).")] = "auto",
    format: Annotated[str, Field(description=f"Picture file: {FORMAT_TEXT}.")] = "svg",
) -> ToolResult:
    inputs = {
        "panels": record_text(panels), "data_file": data_file, "series": record_text(series), "x_column": x_column, "kind": kind, "markers": markers,
        "x_scale": x_scale, "x_range": x_range, "x_label": record_text(x_label), "title": record_text(title), "legend": legend, "grid": grid,
        "width": width, "height": height, "theme": theme, "format": format,
    }

    def compute(handle: JobHandle) -> Computed:
        notes: list[str] = []
        k = choice(kind, "kind", KINDS)
        chart_markers, chart_grid = flag(markers, "markers"), flag(grid, "grid")
        leg = choice(legend, "legend", LEGENDS)
        th = choice(theme, "theme", THEME_NAMES)
        fmt = choice(format, "format", FORMATS)
        xs = choice(x_scale, "x_scale", SCALES)
        w, h = whole(width, "width", *SIZE_RANGE), whole(height, "height", *SIZE_RANGE)
        chart_title = text_param(title, "title", notes=notes)
        xlab = text_param(x_label, "x_label", notes=notes)
        xr = range_param(x_range, "x_range")
        xcol = column_param(x_column, "x_column")
        specs = parse_panels(panels, notes)
        source = check_one_source(data_file, series)
        by_name = {}
        if source == "csv":
            wanted, seen = [], set()
            for i, (_, cols, _, _) in enumerate(specs):
                for c in cols:
                    if c not in seen:
                        seen.add(c)
                        wanted.append((c, f"panels/{i}"))
            loaded, _ = csv_series(handle, data_file, xcol, wanted, notes)
            by_name = {s.column: s for s in loaded}
        else:
            if xcol:
                fail("x_column", "'x_column' selects a CSV column; inline 'series' carry their own x")
            loaded = inline_series(series, notes)
            by_name = {s.column: s for s in loaded}
            used = {c for _, cols, _, _ in specs for c in cols}
            for i, (_, cols, _, _) in enumerate(specs):
                for c in cols:
                    if c not in by_name:
                        fail(f"panels/{i}", f"panel {i + 1} names {c[:40]!r}, which is not the name of an inline series")
            unused = [s.column for s in loaded if s.column not in used]
            if unused:
                notes.append(f"inline series not named by any panel are not drawn: {', '.join(repr(u[:30]) for u in unused[:6])}")
        chart_panels = [
            Panel(series=[dataclasses.replace(by_name[c]) for c in cols], title=t, y_label=yl, y_scale=sc, y_scale_param=f"panels/{i}")
            for i, (t, cols, yl, sc) in enumerate(specs)
        ]
        chart = Chart(
            tool="plot_panels", width=w, height=h, theme=th, kind=k, markers=chart_markers, fill="none", grid=chart_grid, legend=leg,
            title=chart_title, x_label=xlab, x_scale=xs, x_range=xr, panels=chart_panels,
        )
        out = render_xy(chart)
        return finish(handle, out.svg, out.data, notes + out.warnings,
                      {"points_drawn": out.points_drawn, "points_in": out.points_in, "panels": len(chart_panels)}, "points_drawn", fmt)

    return run("plot_panels", inputs, compute, engines=("pypdfium2",) if wants_png(format) else ())


__all__ = ["panels"]
