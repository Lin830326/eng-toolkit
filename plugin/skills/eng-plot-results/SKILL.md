---
name: eng-plot-results
description: Use to turn results of other eng-toolkit tools (their CSV artifacts) or numbers the user gives into a chart picture (PNG for slides and reports, or SVG) with plot_xy, plot_panels or plot_bar, for example Bode, S-N, spectrum, station diagrams, histograms, bars. Load eng-toolkit first.
---

# Plot results workflow (eng-toolkit)

Load `eng-toolkit` first. The plot tools **draw the numbers they are given and check nothing about them**; you cannot see the picture, so verify it through the numbers the tool reports. Unreviewed.

## 1. Pin down the picture

Which quantity against which (with units), where the numbers come from (a tool run here, a file, values typed by the user), linear or log per axis, the range that matters, reference lines (cutoff, yield, allowable), and where the picture goes: someone who wants to paste it into slides, a report or a chat needs a PNG (`format="png"`, section 6); `theme="print"` for reports. Not covered: contour, heat map, 3D, polar, pie, horizontal bars, date axes (use seconds), fitting or smoothing, PDF output, second x axis, log axis on bars.

## 2. Choose the tool

| Picture | Tool and settings |
|---|---|
| Curves against x | `plot_xy` |
| Bode gain + phase | `plot_panels`, `panels="Gain\|magnitude_db\|Gain (dB);Phase\|phase_deg\|Phase (deg)"`, `x_scale="log"` |
| Two units, same x | `plot_xy` with `y2_columns` (right axis linear) |
| Shear/moment diagram | `plot_xy`, `fill="zero"`, one member file at a time |
| S-N curve | `plot_xy`, `x_scale="log"`, `y_scale="log"` |
| Spectrum lines / stepped signal | `kind="stem"` (narrow with `x_range`) / `kind="step"` |
| Test points over a curve; pump or system curve with its operating point | `plot_xy` inline series, `kind="line,line,scatter"` (one kind per series), or `marker="1200,45.5,duty point"` which rings one point |
| Distribution of one column | `plot_bar`, `mode="histogram"` |
| Compare categories | `plot_bar` (grouped/stacked, up to 6 series, `err`) |

`plot_panels` needs 2..6 panels sharing one x.

## 3. Build the inputs

- Give **exactly one** of `data_file` or `series`. `data_file` = a path from the producing tool's `artifacts[]` (`format: "csv"`): `.csv`/`.txt`, header row, comma separated (`.xlsx` refused). Columns are picked by header name; a wrong name is refused with the real names.
- Tables: `dsp_filter_design` `filter_response.csv` (`frequency_hz,magnitude_db,phase_deg`); `control_frequency` `bode.csv`; `control_response` `response.csv` (`time_s,input,output`); `fatigue_sn` `sn_curve.csv` (`cycles,amplitude_MPa`); `fatigue_rainflow` `rainflow_cycles.csv`; `structures_frame` `member_<n>_stations.csv` (`x_m,side,N_N,V_N,M_N_m,u_local_m,...`); `fluids_open_channel` `channel_profile.csv`; `dsp_spectrum` `spectrum.csv` (`frequency_hz,amplitude,...`). `thermal_transient` has no table: call it per time and pass inline `series`.
- `x_column` blank = first column. **`y_columns` blank = every other numeric column**: always name them. A text column (`side`) is refused.
- Inline: `series='[{"name":"gain","x":[1,10],"y":[0,-3]}]'`, plain numbers, equal lengths; bars take text categories in `x` and `"err"`. Use inline series also for numbers from several tool results.
- **Units**: station CSVs are SI (N, N*m, m) and cannot be rescaled. For kN*m and mm pass inline series multiplied by 1e-3 / 1e3 (my arithmetic) and say so in the axis label ("Moment (kN*m)").
- A rise time (10-90 %) or a band is marked with `vlines` at t10 and t90 or a `band`, not one line at the target value.
- Labels with unit: `x_label="Frequency (Hz)"`. `hlines="250,yield"`, `vlines="1000,cutoff"`, `band="-3,0,passband"`, `x_range`/`y_range="min,max"` and `marker` take plain numbers in axis units (`"-3 dB"` and `"0,10 s"` are refused; the unit goes in the label). `width`/`height` 200..2400 px (a PNG is always 1600 px wide).

## 4. Run, then verify

1. `ok` true; read `warnings` (clipping, padding, decimation).
2. `values.points_drawn` vs `points_in` (bars: `bars_drawn`, `zero_height_bars`); a line above 4 points per pixel is decimated, extremes kept.
3. `data.series[i]`: `y_min`, `y_max`, `points_outside_axes`. Compare extremes with the source tool (max moment vs `w L^2/8`).
4. `data.panels[k].axes.x` and `.y`: `scale`, `min`, `max`. Confirm log axes are log (a Bode on a linear x is a silent mistake). Automatic ranges are not padded across zero: data that start at 0 start the axis at 0. Fixed ranges count clipped points.
5. Histogram: `data.histogram` has `bin_edges`, `counts`, `values`, `below_first_edge`, `above_last_edge`; `sum(counts)` + outside = `values`. Quote edges from `bin_edges`, not ticks.

## 5. Refusals and surprises

- **Log axis with zero or negative data is refused**, naming line and column. The filter table starts at 0 Hz, so a log Bode of it fails: use a linear axis plus `x_range`, inline series without the 0 Hz row, or `control_frequency` data. dB columns are negative: keep y linear.
- The -300 dB floor stretches an automatic gain axis: set `y_range="-80,5"`.
- A histogram counts **table rows, not cycles**: the rainflow table merges equal cycles into one row and ignores `weighted_count`. For equivalent cycles use bar mode with `y_columns="weighted_count"`. `plot_bar` fails above 200 categories on a real record: use `data.range_histogram` of `fatigue_rainflow` as inline series.
- Density with `bin_edges` that hold no data is refused; `bins` is 0 (Sturges) or 2..200, never with `bin_edges`. Bars: same categories in every series; no error bars when stacked.
- `kind` lists need one per series; `fill="zero"` needs a linear y axis.

## 6. Hand over the picture and report

Most people cannot open an SVG from a path and the chat shows no picture, so **use `format="png"`** whenever the person wants to see, paste or send the chart (`"both"` also keeps the vector `plot.svg`). The default `svg` is only for a client that renders SVG. The PNG path is `data.png_path` (also `artifacts[]`, file `plot.png`), size `data.png_width` / `data.png_height`.

1. **Give the full `data.png_path`**, copied exactly, and say it is a PNG they can paste into slides, a report or an email.
2. **Say in words what the picture shows**, from the tool's numbers (`data.series[i]`, `data.panels[k].axes`, `data.histogram`, warnings): both axes with units and ranges, each curve's start, end and peak, reference lines, the marked point, anything clipped.
3. **Never** hand over only an SVG path or `svg_inline`, and never write chart code, hand-made SVG or HTML.

If `data.png_path` is null with an `engine.unavailable` warning, the png extra is missing: say so, quote the install command in the warning, give the SVG path and the description. A PNG uses the light colours for `theme="auto"`. Text outside Latin, Greek and common symbols draws as `?` (counted in a warning); CJK text needs a CJK font on the machine. 
Report: chart and PNG path; source tool and columns; scales, ranges, labels; checks (points drawn, ranges, extremes vs the source tool, warnings); limits (picture only, data not checked, unreviewed).
