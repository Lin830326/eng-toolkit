---
name: eng-plot-results
description: Use to turn results of other eng-toolkit tools (their CSV artifacts such as filter response, Bode data, S-N curve, rainflow table, beam stations, channel profile, step response, spectrum) or numbers the user gives into an SVG chart with plot_xy, plot_panels or plot_bar. Covers choosing the chart, artifact paths and column names, labels with units, log axes, ranges, reference lines, histograms, checking the drawn data against the source tool, and handing over the SVG. Load eng-toolkit first.
---

# Plot results workflow (eng-toolkit)

Load the `eng-toolkit` skill first for unit rules and report format. The plot tools **draw the numbers they are given and check nothing about them**; you cannot see the picture, so verify it through the numbers the tool reports. Results are unreviewed.

## 1. Pin down the picture

Which quantity against which (with units), where the numbers come from (a tool run here, a file, values typed by the user), linear or log per axis, the range that matters (0..2 kHz, gain down to -80 dB), reference lines (cutoff, yield, allowable), and the use (`theme="print"` for reports). Not covered: contour, heat map, 3D, polar, pie, horizontal bars, date axes (use seconds), curve fitting or smoothing, PNG/PDF, second x axis, log axis on bars.

## 2. Choose the tool

| Picture | Tool and settings |
|---|---|
| Curves against x (response, profile) | `plot_xy` |
| Bode gain + phase | `plot_panels`, `panels="Gain\|magnitude_db\|Gain (dB);Phase\|phase_deg\|Phase (deg)"`, `x_scale="log"` |
| Two units, same x, one frame | `plot_xy` with `y2_columns` (right axis always linear) |
| Shear/moment diagram | `plot_xy`, `fill="zero"`, one member file at a time |
| S-N curve | `plot_xy`, `x_scale="log"`, `y_scale="log"` |
| Spectrum lines / piecewise-constant signal | `kind="stem"` (narrow with `x_range`) / `kind="step"` |
| Test points over a curve | inline series, `kind="line,scatter"` (one kind per series) |
| Distribution of one column | `plot_bar`, `mode="histogram"` |
| Compare categories | `plot_bar` (grouped/stacked, up to 6 series, `err`) |

`plot_panels` needs 2..6 panels sharing one x (one panel: `plot_xy`). Never mix dB and degrees on one axis.

## 3. Build the inputs

- Give **exactly one** of `data_file` or `series`. `data_file` = the path in the producing tool's `artifacts[]` (`format: "csv"`): `.csv`/`.txt`, header row, comma separated (a semicolon export reads as one column; `.xlsx` refused). Columns are picked by header name; a wrong name is refused with the real names.
- Tables: `dsp_filter_design` `filter_response.csv` (`frequency_hz,magnitude_db,phase_deg`; 512 rows, 0 Hz to Nyquist, floor -300 dB); `control_frequency` `bode.csv` (`frequency_rad_s,...`, log grid); `control_response` `response.csv` (`time_s,input,output`); `fatigue_sn` `sn_curve.csv` (`cycles,amplitude_MPa`; 61 rows, 1e3..1e9); `fatigue_rainflow` `rainflow_cycles.csv` (`range_MPa,mean_MPa,full_cycles,half_cycles,weighted_count`); `structures_frame` `member_<n>_stations.csv` (`x_m,side,N_N,V_N,M_N_m,u_local_m,...`, one per member); `fluids_open_channel` `channel_profile.csv` (`x_m,bed_m,depth_m,water_surface_m,...`); `dsp_spectrum` `spectrum.csv` (`frequency_hz,amplitude,...`). `thermal_transient` returns single numbers, no table: call it for several times and pass the results as inline `series`.
- `x_column` blank = first column. **`y_columns` blank = every other numeric column** (six unit-mixed series on a station table): always name them. Naming a text column (`side`) is refused.
- Inline: `series='[{"name":"gain","x":[1,10],"y":[0,-3]}]'`, plain numbers (no units, nan, `1_000`), equal lengths, `x` optional; bars take text categories in `x` and `"err"`.
- Labels with unit: `x_label="Frequency (Hz)"`, `y_label="Stress amplitude (MPa)"` (at most 120 characters). `hlines="250,yield"`, `vlines="1000,cutoff"`, `band="-3,0,passband"` use plain numbers in axis units (`"-3 dB"` refused). `x_range`/`y_range` = `"min,max"`, min < max. `width`/`height` 200..2400 px.

## 4. Run, then verify

1. `ok` true; read `warnings` (clipping, padding, decimation).
2. `values.points_drawn` vs `points_in` (bars: `bars_drawn`, `zero_height_bars`); for a CSV, points per series = row count (above 4 points per pixel the line is decimated, extremes kept).
3. `data.series[i]`: `y_min`, `y_max`, `points_outside_axes`. Compare extremes with the source tool's numbers (max moment vs `w L^2/8`); a sampled step response peaks slightly below the tool's analytic peak.
4. Axes: `data.panels[k].axes.x` and `.y` (also in `plot_xy`) give `scale`, `min`, `max`. Confirm log axes are log (a Bode on a linear x is a silent mistake) and the range is the intended one; fixed ranges count clipped points.
5. Histogram: `data.histogram` has `bin_edges`, `counts`, `values`, `below_first_edge`, `above_last_edge`; `sum(counts)` + outside = `values`. Bins are half-open, the last closed. Quote edges from `bin_edges`, not the axis ticks.

## 5. Refusals and surprises

- **Log axis with zero or negative data is refused** (never dropped), naming line and column (`/data_file/2/frequency_hz`). The filter table starts at 0 Hz, so a log Bode of it fails and `x_range` does not help: use a linear axis plus `x_range`, inline series without the 0 Hz row, or `control_frequency` data. dB columns are negative: keep y linear.
- The -300 dB floor stretches an automatic gain axis: set `y_range="-80,5"` (clipped points are counted).
- A histogram counts **table rows, not cycles**: the rainflow table merges equal full and half cycles into one row and ignores `weighted_count`. For equivalent cycles use bar mode with `y_columns="weighted_count"`.
- Density with `bin_edges` that hold no data is refused (`/bin_edges`); counts draw zero bars with a warning. `bins` is 0 (Sturges) or 2..200, never with `bin_edges`.
- Bars: same categories in every series; no error bars when stacked; blank `y_columns` also draws error columns as bars.
- `kind` lists need one per series; `fill="zero"` needs a linear y axis. Constant data is padded with a warning.

## 6. Hand over and report

`plot.svg` and `plot_data.json` are in the job folder (`artifacts[]`, `format: "svg"`). `data.svg_inline` holds the SVG text when it is at most 150 kB (else `svg_inline_omitted`: give the path). Give the `plot.svg` path; where the client renders SVG/HTML, show `data.svg_inline` unedited. Describe the chart by its numbers and settings, never by visual impressions.

Report: chart and file path; source tool and columns; scales, ranges, labels; checks (points drawn, ranges, extremes vs the source tool, warnings); limits (picture only, data not checked, unreviewed).
