# plot domain: design notes

Scope: a new `plot` domain with three tools that turn tables into deterministic, self-contained SVG charts: `plot_xy`,
`plot_panels`, `plot_bar`. Branch `feat/plot-domain`. Files: `eng_toolkit/domains/plot/`, `tests/test_plot_*.py` +
`tests/plot_testing.py`, this folder, one README section. Nothing in `pyproject.toml`, `uv.lock`, `tests/test_gateway.py`, the shared
layer or another domain.

Why: the other domains return numbers and CSV tables (shear/moment stations, S-N points, Bode responses, filter responses, channel
profiles, rainflow cycles). `plot` draws them so an assistant can show the chart (the SVG text is inline in `data.svg_inline` when it is
at most 150 kB, so a chat client can render it as an artifact) and an engineer can paste it into a report.

Ground rules: clean-room; **numpy only at run time** (`DOMAIN.engines = ("numpy",)`; no matplotlib, no plotting or SVG library — the
SVG is written by `_svg.py`); tools never call an LLM; **nothing the caller sends is executed or written unescaped** (numbers go through
one anchored regular expression and `float`, JSON through `json.loads`, every caller string through control-character replacement and
XML escaping); every result says `review_status: "unreviewed"`; no NaN or infinity can reach a result or the SVG (`clean()` refuses
them; coordinates are clamped); every refusal has a family and a `details.param` JSON pointer.

## Tools

| Tool | What it draws | Primary value |
|---|---|---|
| `plot_xy` | line / scatter / step / stem chart of up to 20 series; per-series kinds (`kind="line,scatter"`); log axes; fixed ranges; a right axis (`y2_columns`, always linear); horizontal and vertical reference lines; one shaded band; fill to zero in two tints; legend; grid; themes | points drawn |
| `plot_panels` | 2..6 vertically stacked panels sharing one x axis (`panels` = `title\|y_columns\|y_label\|y_scale` records joined by `;`), each with its own y axis; x tick labels under the bottom panel only | points drawn |
| `plot_bar` | bar chart (categories x up to 6 series, grouped or stacked, error bars, value labels) or histogram of one column (2..200 bins or explicit edges; counts, density or cumulative) | bars drawn |

Every tool writes `plot.svg` and `plot_data.json` (series statistics, axis ranges, every tick and label actually used, the
data-to-pixel mapping, decimation, truncated texts, warnings) and returns the same summary in `data` (plus `svg_inline`, or
`svg_inline_omitted` with the reason when the SVG is larger than 150 000 bytes).

## Data input

Exactly one of:

* `data_file`: a `.csv` or `.txt` path, read through `JobHandle.input_file` (one open, the read-directory allow-list
  `ENG_TOOLKIT_READ_DIRS` checked on the open file, a copy hashed into `provenance.input_files`; the workspace of other tools is always
  readable, so `ws/<job>/filter_response.csv` works under a restricted allow-list). The file is **streamed**: read in binary chunks,
  decoded incrementally (UTF-8, BOM skipped), split into records only at `\n`, `\r\n` and `\r` (a U+2028, form feed or other
  separator inside a cell stays in the cell and is then cleaned), parsed by the `csv` module; the caps (rows, columns, blank lines) are
  enforced while reading and only the cells of the selected columns are kept, so memory follows the retained data, not the file size.
  The first non-blank line is the header; comma-separated (tab-separated when the header has a tab; whitespace-separated when it has
  several space-separated names and no quote; otherwise a one-column CSV), standard CSV quoting, at most 10 000 blank lines.
  Columns are selected by header name (`x_column`, blank = first column; `y_columns` comma-separated, blank = every other column).
  With a blank `y_columns` a column is skipped as a text column only when **no** cell of it is a number (the `side` column of a
  frame station table; the warning names the skipped columns and says why); a column with some numeric and some bad cells is
  refused at the first bad cell. Only the selected columns must be numeric.
* `series`: inline JSON `[{"name": "a", "x": [...], "y": [...]}]` (a single object is accepted too); `x` may be omitted (0, 1, 2, ...);
  `plot_bar` also takes `"err"` and text categories in `"x"`.

A number is a plain ASCII decimal (`-1.5e3`, `.5`, `2.`; spaces and tabs around it are allowed, other whitespace such as a form feed
is not). A bad cell is refused at the JSON pointer `/data_file/<line>/<column>` (RFC 6901 escaping of `/` and `~`, with
`details.line`, `details.column` and `details.reason` = "not a number" or "out of range"): text, an empty cell, `nan`, `inf`,
`1_000`, `0x10`, fullwidth digits (`input.invalid`); magnitudes above 1e100, including `1e308` and `1e999` (`input.out_of_range`). A
ragged row is refused at `/data_file/<line>`. Duplicate keys in an inline series object are refused at `/series/<i>/<key>`.
The JSON parse hook stores duplicate metadata outside the caller's objects, so no caller key can impersonate that metadata.
Numeric bar categories have the same magnitude and finiteness checks as values, before conversion to text; a refusal points at
`/series/<i>/x/<j>`. Text categories remain text. The regular expression has non-overlapping alternatives and tokens are capped at 64
characters before matching, so a 6000-character token is refused in microseconds.

## Caps (all enforced; exceeding one is `input.out_of_range` unless noted)

| What | Cap |
|---|---|
| series per chart | 20 (`plot_bar`: 6 bar series, 1 histogram column) |
| points per series / CSV data rows | 200 000 |
| CSV file | 200 MB, 256 columns, header cells of 200 characters, 10 000 blank lines |
| inline JSON | 200 000 000 characters |
| title, axis labels, series names, reference-line labels, panel titles | 120 characters (a longer label is refused; one that does not fit its pixel width is shortened with an ellipsis and listed in `truncated_text` and the warnings) |
| column-name parameters | 200 characters each, 4000 for a list |
| record parameters (`hlines`, `vlines`, `band`, `panels`, `bin_edges`) | 4000 characters; 10 reference lines of each kind; 1 band; 2..6 panels (fewer than 2 is `input.invalid`); 3..201 edges |
| width, height | 200 .. 2400 px; a plot area below 60 px after labels and legend is refused (`/width` or `/height`) |
| histogram bins | 2 .. 200 (0 = Sturges, `ceil(log2 n) + 1`) |
| bar categories | 200 |
| data magnitude | 1e100; an axis whose values are all non-zero but below 1e-100 in magnitude is refused (rescale) |
| path | 1000 characters |

Worst case measured (Windows 11, this machine): 20 columns x 200 000 rows of CSV (80 MB) in 4 s (scatter 5 s), 20 x 200 000 inline
points as scatter in 3.4 s; a 199 MB file of blank lines, of `1,2` rows or of `,` rows is refused in 0.5 s with a peak working set of
about 150 MB (most of it the interpreter; it was 3.3 GB and up to 8.5 s before the file was streamed); refusals of 6000-character strings in every text parameter take well under a millisecond each (tested below one second
through the MCP client).

## Layout constants (px)

All positions follow from these, so a test can derive a pixel by hand (see `tests/test_plot_xy.py::test_a_known_point_maps_to_the_documented_pixel`):

* outer padding `PAD` = 12; a title adds 28 (font 16, bold, baseline at 28);
* x axis: tick length 5, gap 4, tick labels font 12 with baseline at plot bottom + 19; an x label adds 20 (font 13, baseline at height − 14);
  bottom margin = 12 + 21 (+ 20);
* left margin = 12 (+ 20 with a y label) + 5 + 4 + ceil(widest shown y tick label); right margin = with a right axis 12 + 5 + 4 +
  ceil(widest y2 label) (+ 20 with a y2 label), else max(12, ceil(half the last x tick label) + 2); plus the legend width + 10 when the
  legend sits outside;
* text width estimate: 0.6 x font size per character (1.0 for east-asian wide characters) — generous for digits, so labels never collide;
* panels: a panel title adds 20 (font 13), panels are 14 apart and equally tall;
* tick targets: about one x tick per 90 px and one y tick per 45 px (2..12 and 2..10);
* legend: font 12, rows of 18, padding 8, sample 26, text at most 200 px (ellipsis beyond); `auto` shows it for 2+ series, in the inside
  corner that covers the least drawing — score = length in px of the drawn (clipped) line and stem segments inside the box + 10 per
  marker centre inside + 1000 per reference or band label it would cover; ties: top right, top left, bottom right, bottom left —
  and outside to the right when it is taller than the plot or wider than half of it;
* reference-line labels (font 11) are drawn outside the data clip group but inside the frame: an hline label right-aligned above its
  line, else below it, else at the left above or below; a vline label right of the line at the top, else left of it, else at the
  bottom; the first spot inside the frame that overlaps no earlier label is used, and a label that fits nowhere is left out with a warning;
* bars: each category band is plot width / categories, its bars fill the middle 80 %.

Mapping, also in `plot_data.json` per axis: `px = origin_px + (v - min) * scale_px_per_unit` (linear) or
`px = origin_px + (log10 v - log10 min) * scale_px_per_unit` (log, scale in px per decade); for y axes `origin_px` is the bottom of the
plot area and the scale is negative. Coordinates are written with at most two decimals, trailing zeros trimmed, `-0` as `0`.
**Clipping is geometric**: before formatting, polylines are clipped to the plot area grown by 4 px with Liang-Barsky. A line that
leaves and re-enters becomes several polylines. Ordinary segments reconstruct intersections from the nearer endpoint. When a pixel
coordinate exceeds 1e8 in magnitude, the clipper uses exact rational arithmetic, including the data-to-pixel mapping from the original
data and axis bounds. This preserves offsets that float conversion would lose before clipping. The fallback is exact for the parsed
binary64 data, with log coordinates taken after the log transform. Coordinates round only after clipping, to 0.005 px.
Fill polygons use Sutherland-Hodgman with the same rational fallback. Stems clip their vertical extent before testing marker visibility;
linear stems always start at the transformed zero, including when zero lies above a negative fixed range. Stems wholly outside the frame
are omitted. Markers whose centre is more than 8 px outside are not written. The SVG clip path trims line width at the frame.
Bars are axis-aligned rectangles and are cut at the plot area grown by 4 px.

## Axes and ticks

* Linear, automatic: step = the smallest 1, 2, 5 or 10 x 10^e at least span/target; the axis runs from the step multiple at or below the
  minimum to the one at or above the maximum (nice padded ticks that cover the data; rounding noise of at most 1e-9 step beyond the end
  tick widens the axis by that noise instead of adding a tick, so a 1e-15 dB rounding error at DC does not stretch a -300..0 dB axis).
  Ticks are built as `k*m*10^e` from integers and parsed from text, so labels are exact decimals. Labels use the step's decimals, or
  compact scientific notation (`1.5e6`) for magnitudes of 1e6 and above or more than five decimals. Every label set is then
  **verified**: the labels must all differ and each must read back as its tick to within 1e-6 of the tick spacing; otherwise the
  ticks are relabelled with as many significant digits as needed (2, 3, ... 17). So no label repeats and none names another value.
* Equal minimum and maximum (or closer than 1e-9 relative): padded by 10 % of the value (to -1..1 for zero) and said so in the
  warnings — never a division by zero, never a refusal.
* Log: whole decades around the data, equally spaced (the mapping is linear in log10); every `stride`-th decade labelled (stride 1, 2,
  5, 10, ... so that at most `target` labels appear); 2x and 5x labelled when the axis spans at most two decades; minor grid at 2..9 x
  10^n when every decade is labelled and there are at most 15. Labels are plain for 1e-4 .. 1e5 and `1e6`, `2e-7` otherwise. A fixed
  log range inside one decade with fewer than two 1..9 multiples (`2,2.5`, `30,31`, `2e-6,2.5e-6`) gets linear nice ticks labelled by
  their spacing (`2.0, 2.1, ...`, `30.0, 30.1, ...`, `2e-6, 2.05e-6, ..., 2.5e-6`), verified as above. Zero or
  negative data on a log axis is **refused** with a pointer to the series value or to the CSV column and line — never dropped.
  Automatic decade or marker expansion must produce positive, distinct finite bounds. If expansion underflows to zero, the request
  returns `input.out_of_range` at the relevant data pointer, before tick generation or mapping. Representable subnormal bounds,
  including fixed ranges, remain usable; tick generation excludes zero and labels subnormal ticks without dividing by an underflowed decade.
* Markers need room: when markers are drawn (scatter, stem, `markers=true`), automatic ranges get a 3 % margin (on a log axis out to
  the next 1, 2 or 5 x 10^n instead of a whole decade).
* Fixed ranges (`x_range`, `y_range`, `"min,max"`): kept exactly; ticks are the step multiples inside; points outside are clipped by
  the clip path and counted (`points_outside_axes`, warning).
* Tick labels never overlap: every k-th label is kept, the smallest k for which estimated label boxes are at least 8 px (x) or 2 px
  (y) apart; tick marks and grid lines stay at every tick.

## Decimation and marker thinning

**Lines and steps.** A line or step series with more than 4 points per pixel of plot width is reduced. When x never decreases, points are grouped by the pixel column
they fall in (everything left or right of the plot area is one group each); of each group the first, last, minimum-y and maximum-y
points are kept, in their original order. When x is not sorted (a hysteresis loop), groups are runs of `ceil(n / plot width)`
consecutive points and the minimum and maximum x are kept as well. The global extremes are extremes of some group, so the plotted
minimum and maximum are **exactly** the data's (tested with a single spike in 200 000 points). Nothing is interpolated or averaged.
**Markers** (scatter, stem, and the markers of `markers=true`) are never envelope-decimated: that would delete every marker between a
column's minimum and maximum (a dense band at y = 0 vanished completely). Above the same density, one marker is drawn per occupied
2 x 2 px cell (the first point of the cell in input order; the cell is doubled while more than 20 000 cells are occupied), plus the
points with the smallest and largest y; for stems also the per-column extremes, so the drawn stems reach every point. Every point that
is not drawn lies within `cell x sqrt(2)` px (2.8 px for 2 x 2 cells) of a drawn marker centre, i.e. under a marker of radius 3.5.
A line with `markers=true` uses the envelope for the line and the cells for the markers.
`data.series[i]` reports `points_in`, `points_drawn` (distinct points drawn as vertex or marker), `line_vertices`, `markers_drawn`,
`decimated`, `markers_thinned`, `method`, `drawn_y_min`, `drawn_y_max`; warnings say which series were decimated or thinned and how.

## SVG content, escaping and themes

* Elements: only `svg g path polyline line rect circle text title desc style clipPath defs`. No `script`, `foreignObject`, `image`,
  `use`, `a`, no `href`/`xlink:href`, no `on*` attributes, no external references (`url(#id)` refers to a clip path in the same file),
  no web fonts (a generic stack `system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif`).
* The root has `viewBox`, `width`, `height`, `role="img"`, `aria-labelledby` pointing at `<title>` (the chart title, or "Line chart of
  ...") and `<desc>` (a text summary: kind, series names, axis scales and ranges, points drawn, fills, reference lines and bands).
* Caller text — labels, series names, CSV headers, CSV and inline categories, everything that reaches the SVG, `data` or
  `plot_data.json` — goes through one cleaning function: whitespace controls and U+2028/U+2029 become spaces; other C0/C1 control
  characters, lone surrogates, U+FFFE/U+FFFF and bidi overrides become U+FFFD; one warning per call gives the number of distinct texts
  that were changed. Data, warnings and refusal messages are scrubbed again before they are written, the SVG escaper repeats the
  replacement whatever the source of a string, and `plot_data.json` is encoded with an error handler, so no caller string can make the
  UTF-8 encoding fail (a lone surrogate in a series name was an `engine.failure` before). Then `& < > " '` are escaped in text and
  attribute values alike. `<script>alert(1)</script>` appears in
  the file only as `&lt;script&gt;...`.
* Determinism: identical input gives identical bytes — fixed number formatting, a fixed element order, no timestamps or random ids.
  Element ids are prefixed with `etk-` + the first 10 hex digits of a sha256 of everything that defines the drawing (every option and
  label, series names, axes and kinds, all x and y values; for bars also categories and error values; for histograms the bin edges,
  counts, heights and the value column), so two different charts inlined in one HTML page do not share ids, and the same input gets
  the same id. CSS rules are scoped to that id.
* Palette: an Okabe-Ito based 8-colour categorical palette (`#0072B2 #D55E00 #009E73 #CC79A7 #E69F00 #56B4E9 #882255 #555555`), a lighter
  variant for dark backgrounds, greys for print. Each series also has its own dash pattern (8) and marker shape (8); the first 64
  (colour, dash, marker) combinations are distinct and 20 series already differ in colour + dash, so charts survive greyscale. Bars
  carry a dashed outline per series as the second channel.
* Themes: `auto` writes every colour as a presentation attribute (light values, so any renderer shows something sensible) plus a class;
  the `<style>` defines CSS custom properties for light and, under `@media (prefers-color-scheme: dark)`, for dark, and maps the
  classes to `var(--etk-..., fallback)`. `light`, `dark` and `print` (black and grey, dash patterns as the main channel) write fixed
  colours and no media query.
* Fill to zero (`fill="zero"`, line and step only, linear y): the area between each line and y = 0 is drawn twice, clipped above and
  below the zero line **of the axis the series belongs to** (a right-axis series is split at the right axis's zero), with fill
  opacities 0.30 and 0.14 (two tints); a zero line is drawn for every axis that carries a filled series (solid for the left axis,
  dashed `4 3` for the right one).
* Step = post (the value holds until the next x). Stem = a vertical line from 0 (or the bottom of a log axis) to each point plus a marker.

## Bars and histograms

* Grouped bars side by side, stacked bars with positive values upwards from 0 and negative values downwards; the y axis always
  includes 0; error bars (`err`, half widths, must be >= 0) with caps on grouped bars only (stacked + error bars is refused);
  value labels above the bar or error bar (stacked: the total), left out (and counted) where the bar is narrower than the label; with
  value labels and an automatic range 8 % headroom is added. Value labels use the same formatter family as the ticks: exact integers
  below 1e9 (`12345`, `123456`), plain decimals with 4 significant digits for 1e-4 <= |v| < 1e9 (`0.0001235`, `-3.142`), otherwise
  `1.5e9` / `2.5e-5` (never `e+04`).
* A bar that extends beyond a fixed `y_range` is cut at the frame, marked with a small triangle pointing the way it continues,
  listed with `clipped: true` in `data.bars`, counted in `data.clipped_bars`, and a warning gives the count and the values.
* `bars_drawn` (the primary value) counts the rectangles actually drawn; zero-height bars are reported separately
  (`zero_height_bars`, `zero_height: true`).
* A histogram whose values are all non-zero but below 1e-100 is refused at the value column (`/data_file` or `/series/0/y`): the
  range is too small to bin.
* Histogram: `numpy.histogram` over equal-width bins of [min, max] or the given edges. Bins are half-open except the last, which is
  closed. Inline integers and decimal tokens, CSV values, and explicit edges retain their exact values for comparisons. Automatic
  edges use the decimal spelling of the generated numbers, widened to include the exact data extrema. Only drawing coordinates use
  binary64. Edges that collapse to the same drawing coordinate value, or nonzero edges that underflow to zero, return
  `input.out_of_range` at `/bin_edges` before computing rational widths.
  Values outside explicit edges are counted in `below_first_edge` / `above_last_edge` and a warning, never silently dropped.
  Density = count / (values in bins x bin width), using rational edge differences before converting the width to binary64.
  If no values fall in the bins, density is undefined and returns `input.invalid` at `/bin_edges`. Counts and cumulative counts
  remain zero in that case. `data.histogram` holds numeric `bin_edges`, exact decimal strings in `bin_edges_exact`, raw counts,
  heights, the rule used and the counts outside. Integral edges remain integers in the metadata, including above 2^53.
* Category labels are shortened to about 24 characters with an ellipsis and thinned so they do not overlap.

## Verification

* `tests/test_plot_units.py`: nice steps by hand (10/8 -> 2, 100/10 -> 10, ...), tick coverage and exact decimals (`0.1 .. 0.3` gives
  0.10, 0.15, ..., 0.30), padding of equal values, fixed ranges (-80..5 with target 8 -> step 20), rounding noise above a tick, tiny-data
  refusal, log decades 100 px apart, stride 5 over 24 decades, thinning, escaping, text fitting, control and bidi characters, number
  formatting, the strict number pattern and its speed on 6000-character tokens, style uniqueness for 20 series, decimation keeping
  the extrema of a random walk and of an unsorted spiral exactly.
* `tests/test_plot_xy.py`: the hand-derived pixel of a known point (`(5, 50)` -> `(415.5, 239.5)` from the layout constants), the
  SVG structure, fill-to-zero clip paths at the zero line, equal decade spacing on log-log axes, log refusals with pointers,
  decimation of 200 000 points keeping a single spike, byte-identical repeated output, hostile text (`<script>`, `"><svg onload=...>`,
  `&`, CDATA, comments, unicode/CJK), control characters, 6000-character labels refused fast, ellipsis truncation, reference lines,
  band and right axis, auto/dark/print themes, all kinds, clipping and counting outside a fixed range, padding of equal values, the
  inline-size limit, 29 parameter refusals.
* `tests/test_plot_csv.py`: named columns next to a text column, default column selection, ten kinds of bad cells with line numbers,
  missing columns and file-level refusals (empty, header only, NUL, wrong extension, missing file, Latin-1, too many columns, long
  header cell), the row cap, BOM/tab/whitespace/quoted files, log refusal pointing at a CSV column and line, panels from CSV.
* `tests/test_plot_panels.py`, `tests/test_plot_bar.py`: panel geometry by hand, shared x mapping, x labels only under the bottom
  panel; bar rectangles by hand (`(46.95, 139.5, 143.6, 127.5)`), grouped bars with error bars and value labels, stacking of positive
  and negative values, histogram counts/density/cumulative by hand, explicit edges with values outside, constant columns, CSV
  categories with markup characters; refusals.
* `tests/test_plot_gateway.py` (through `fastmcp.Client` and the gateway): registry, schemas (basic types; every parameter
  description 20..140 characters and **no two parameters of one tool share a description**), CSV artifacts of real tools plotted
  under a restricted read allow-list (`dsp_filter_design` -> Bode panels; `structures_frame` -> shear and moment with fill, checked
  against wL/2 = 20 kN and wL²/8 = 20 kN m; `fluids_open_channel` profile; `fatigue_rainflow` cycles histogram), a path outside the
  allowed directories and a `..` escape, 22 hostile requests each under one second with the expected family and pointer, non-finite
  numbers, strict JSON of every response, and a source grep (no eval/exec/compile/import tricks, subprocess, pickle, `open(`,
  plotting libraries, `<script`, `<foreignObject`, `xlink:`, `href=`).
* Every SVG produced in the tests is parsed with `xml.etree.ElementTree` and checked against the element and attribute whitelist.
* Visual check (by hand, not in the test suite): the demo SVGs (Bode panels from `dsp_filter_design`, shear and moment from
  `structures_frame`, an S-N curve with scatter points and a band, a histogram, grouped and stacked bars, 200 000 decimated points,
  hostile labels) were rendered to PNG with headless Chrome and inspected. The repository's PNG facility (`pypdfium2`) renders PDF,
  not SVG, so the suite asserts structure and geometry instead of pixels.

* `tests/test_plot_review_fixes.py`: one or more regression tests per review finding (S1, C1-C9, R1-R5, K1, K2), see below.
* `tests/test_plot_review2.py`: all eight findings from the review at `472d99c`, reproduced through `Client(gateway)` with real
  artifact writes. Geometry is checked against independent rational edge intersections, histogram boundaries against Python
  integers and `Decimal`, density against a rational integral. Coverage includes reversed and asymmetric extreme lines, fill
  polygons, both panels, stem visibility, inline and CSV precision, forged duplicate metadata, log underflow and representable
  subnormals, numeric categories, and empty density ranges.

## Changes after the independent review (before -> after)

| Item | Before | After |
|---|---|---|
| S1 lone surrogate in an inline series name | `engine.failure` (UnicodeEncodeError writing `plot_data.json`) | cleaned to U+FFFD everywhere, one warning; artifacts encoded with an error handler. A surrogate in a top-level string parameter still fails in the shared layer while `inputs.json` is written (outside this domain) |
| C1 fill on a right-axis series | split at the left axis's zero (467 px), wrong tint, no right zero line | split at the right axis's zero (125.75 px), dashed right zero line |
| C2 scatter of 30 000 points, 80 % at y = 0 | 0 of 1289 markers at y = 0 ("extrema kept") | 264 of 792 markers at y = 0; every point within 2.8 px of a drawn marker; "marker plot thinned" warning |
| C3 narrow fixed log ranges | `2,2,2,2,2,2`; ten `2e-6` and 2.5e-6 labelled `3e-6` | `2.0, 2.1, ..., 2.5`; `2e-6, 2.05e-6, ..., 2.5e-6`; labels verified distinct and exact |
| C4 segment to a far point | leaves the top at 642.5 px (true 601.0) | 601.0 (exact Liang-Barsky clipping) |
| C5 bar 5 with `y_range="0,2"` | silently drawn as 2 | cut at the frame, triangle marker, `clipped: true`, warning with the value |
| C6 hline at the top of the axis | label clipped away invisibly | label below the line, outside the clip group, inside the frame; warning if no spot fits |
| C7 value labels | `1.234e+04`, `1.235e+05`, `0.0001235` | `12345`, `123456`, `0.0001235` |
| C8 element ids | same prefix for histograms of [1..4] and [1000..4000], for bars with err 0.1 and 5 | different prefixes (hash of all drawing content) |
| C9 auto legend | top right, crossed by a diagonal between its vertices | scored by segment length: top left, nothing under it |
| R1 default columns, one bad cell | column silently dropped ("not entirely numeric") | refused at `/data_file/<line>/<column>` with the reason; only columns without any number are skipped |
| R2 199 MB file of blank lines | accepted after 8.5 s, 3.3 GB peak | refused in 0.5 s (blank-line cap) at about 150 MB |
| R3 U+2028 / form feed in a quoted cell | line numbers off by two, categories altered | records split only at real line ends; line 4 reported; `A B`, `C` U+FFFD `D` |
| R4 histogram of 1e-200 values | pointer `/bin_edges` | pointer `/series/0/y` (or `/data_file`) |
| R5 duplicate JSON keys | last one silently wins | refused at `/series/<i>/<key>` |
| K1 controls in CSV headers and categories | replaced silently; raw text in `column`, `value_column` | cleaned everywhere, counted in one warning |
| K2 `bars_drawn` with a zero bar | 2 for one rectangle | 1, plus `zero_height_bars` = 1 |

Not changed: a non-integer or boolean `width` (800.5, true) is rejected by the MCP schema layer before the tool runs, so that
refusal has no family or pointer (shared framework behaviour).

## Second independent review

All eight findings reproduced through the gateway before the fixes. No finding was disputed.

| Finding | Reproduction and corrected result |
|---|---|
| 1 | A line from (-1e20, -1e20) to (1e20, 1e20) in fixed -1..1 axes used to collapse to (0,0). Rational mapping and clipping now preserve the visible crossing from (50,467) to (787,12), and both shared-x panel crossings. |
| 2 | A stem at (0.5,10) with 0..1 axes had no path. The visible segment now runs at x=415 from y=467 to y=12. |
| 3 | A stem at y=-2.5 in a -3..-2 range started at the bottom. It now enters from the top at y=12 and ends at y=239.5. |
| 4 | Values 9007199254740992, 9007199254740993, 9007199254740994 with edges 0, 9007199254740993, 10000000000000000 gave [0,3]. Exact comparisons now give [1,2] for both inline and CSV data. |
| 5 | A forged `\u0000duplicate-key` key with null, list or integer value caused a traceback. It now receives an unknown-key `input.invalid` refusal with a pointer. Actual duplicate keys still point at the repeated key. |
| 6 | Automatic log expansion for [5e-324,1], or marker expansion for [1e-320,1e100], reached log10(0). Both now return `input.out_of_range` at the axis data pointer. |
| 7 | Numeric categories 1e999, -1e999 and 1e101 were printed as labels. They now return `input.out_of_range` at `/series/0/x/0`. |
| 8 | Density for values [5,6] and edges [0,1,2] returned zero heights with integral zero. It now returns `input.invalid` at `/bin_edges` because the normalization count is zero. |

## Not done (this version)

No 3D, maps, polar, contour or heatmap plots; no interactivity (tooltips, zoom, hover); no PNG or PDF output; no date/time axes
(x is a number; convert times to seconds); no second x axis; no per-panel y ranges in `plot_panels`; no log right axis; no
horizontal bars; no hatch patterns (they would need `<pattern>`); no curve fitting, smoothing or interpolation of the data; no
automatic unit handling (labels are plain text and the caller writes the unit); no text measurement from real font metrics (widths
are estimated generously, so a label may get slightly more room than it needs, never less).
