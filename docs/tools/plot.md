# Charts (SVG, PNG) (`plot`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## Charts of tables (`plot`, SVG or PNG)

`plot_xy`, `plot_panels` and `plot_bar` turn tables — the CSV artifacts of the other
tools (`filter_response.csv`, frame stations, channel profiles, rainflow cycles) or
inline JSON series — into deterministic, self-contained SVG charts and a PNG (`plot.png`, 1600 px wide, path in
`data.png_path`). `format` is `both` (default), `svg` or `png`; the PNG needs the `png` extra, and without it the tool
returns the SVG with a warning. Chart types: line, scatter,
step and stem plots with log axes, a right axis, reference lines, a shaded band and
fill to zero; 2-6 stacked panels sharing x (Bode magnitude + phase); grouped or
stacked bars and histograms. Every label is escaped plain text, the SVG has no
scripts or external references, and `plot_data.json` records the ticks and the
data-to-pixel mapping. Large series are min/max decimated with extrema kept. NumPy
only; results are unreviewed. See [layout, caps, escaping and limits](../plot/design.md).

The PNG also comes back as an image block in the reply (`inline_image`, default true) when it is at most 600 kB; a larger
PNG is not attached and a warning says so. `geometry_view` and `fem_view` do the same.

`unwrap_phase=true` (`plot_xy`, `plot_panels`; default false) unwraps every series whose name contains `phase`, in data
order, so a Bode phase that passes -180° is drawn without the jump to +180°. A warning names each series with its range
before and after.

Tick labels: a linear axis in seconds whose largest value is below 1 s gets labels in `ms`, `us`, `ns` or `ps`; the data
and the axis range stay in seconds, and a warning says the labels are scaled. A log axis that would mix plain and
exponent labels uses `k`, `M`, `G` instead (`10k, 100k, 1M`).

`plot_bar` takes `hlines` (`"value,label;..."`, at most 10): dashed limit lines across the bars, for example an allowable
value. With a fixed `y_range`, a line outside the range is not drawn and a warning says so.
