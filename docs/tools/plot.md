# Charts (SVG, PNG) (`plot`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## Charts of tables (`plot`, SVG or PNG)

`plot_xy`, `plot_panels` and `plot_bar` turn tables — the CSV artifacts of the other
tools (`filter_response.csv`, frame stations, channel profiles, rainflow cycles) or
inline JSON series — into deterministic, self-contained SVG charts (default) or, with `format="png"` / `"both"`, a PNG
(`plot.png`, 1600 px wide, path in `data.png_path`; needs the `png` extra): line, scatter,
step and stem plots with log axes, a right axis, reference lines, a shaded band and
fill to zero; 2-6 stacked panels sharing x (Bode magnitude + phase); grouped or
stacked bars and histograms. Every label is escaped plain text, the SVG has no
scripts or external references, and `plot_data.json` records the ticks and the
data-to-pixel mapping. Large series are min/max decimated with extrema kept. NumPy
only; results are unreviewed. See [layout, caps, escaping and limits](../plot/design.md).
