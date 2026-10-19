# drawing_make_views part 2 (slice S6): hole callouts, ordinates, features_path, SVG and PNG

Branch `feat/drawing-callouts`, from main at the merge of `feat/drawing-fixes`. Written with pstack's `architect` skill, scaled down as for [drawing-read.md](drawing-read.md) and [drawing-fixes.md](drawing-fixes.md): Phase A is reading the code and measuring ezdxf, OpenCASCADE, pdfminer and PDFium on this machine; Phase B is "design it twice" per decision, by one model; the multi-model arena was **not** run. Implementation followed pstack's `tdd` skill (tests first, run to see them fail for the intended reason). `poteto-mode` needs `bun`, which this machine does not have, so it was not used. Skipped in Claude Code: Cursor-only steps (Automations, Bugbot, cloud agents); none was needed.

The slice's acceptance, from [design.md](design.md): `Ø10 THRU` with ordinates; with a matching `features.json` `Ø10 H7 THRU`; a mismatching hash warns and prints no fit; `4× Ø6.6 THRU`. Each is a test in `tests/test_drawing_callouts_tool.py`.

## Usage (caller's view)

```text
drawing_make_views(model_path=".../bracket.step", title="BRACKET", features_path=".../bracket.features.json", formats="dxf,pdf,svg,png")
  -> top view: callout "Ø10 H7 THRU" to the right, ordinates x 0, 25 below and y 0, 25 to the left; 100, 50, 20 as before
     drawing.dxf (the source), drawing.pdf, drawing.svg, drawing.png
  data.callouts [{text, view, count, diameter_mm, thru, depth_mm, hole_ids, entry_points_mm, fit, thread, from_features_file}]
  data.features {status: bound | hash_mismatch | not given, step_sha256_in_file, model_sha256, declared_holes, matched_holes}
```

## Decisions

### 1. Sizes come from the geometry, intent only adds two words

`brep.inventory` (the hole search of `geometry_measure`) gives diameter, direction, entry point and depth or through. The callout text, the count and the positions are derived from it. `features.json` is read for exactly two strings, the fit and the thread, and only for a hole that the file's declared holes match. Alternative: draw what the features file says (it has positions and diameters too). Rejected: a file that no longer describes the model (edited STEP, another part) would then draw wrong numbers with the confidence of a drawing; with the geometry as the source the worst a bad file can do is to add no fit.

### 2. Grouping and text (`callouts.py`, pure)

Holes are grouped by diameter (to 0.001 mm), direction, through or depth, fit and thread; through holes ignore the sense of the direction (the same circle from either side), blind holes do not. One callout per group: `Ø10 THRU`, `4× Ø6.6 THRU`, `Ø5 DEEP 8`, `Ø10 H7 THRU`, `Ø5 M6x1-6H THRU`; lengths with two decimals at most. `↧ ⌴ ⌵` are not in Arial (measured in the S4 design work), so depth is the word `DEEP`. Counterbores and countersinks are not recognised by `brep.inventory` (its fields say "not detected yet"), so they are not called out; this is a stated gap, not a silent one.

### 3. Which view, and where the text goes

A group is called out in the first requested view whose viewing direction is along the hole axis; for a blind hole a view that sees the entry is preferred. If no requested view looks along the axis the group is left out and the warning says which view to add. The text goes in a margin to the **right** of the view that the layout reserves (gap 6 mm, text width from `text_em` at 3.5 mm height, 2 mm of air), so it cannot overlap geometry; the first design (text over the part next to the hole) was rejected because nothing prevents it from landing on another hole or a dimension, and avoiding that needs a collision search that this slice does not have. The cost is a wider layout: `_block` takes a right margin per column, and the existing layout tests are unchanged for drawings without holes (`test_the_views_without_holes_are_laid_out_as_before`). A `LEADER` runs from the edge of the group's hole nearest the text to the text, at the hole's height; texts of one view are at least 6 mm apart. The leader points at the hole with the largest u, then the smallest v, so it crosses no other hole of its own group (the first rendering pointed at the first hole by id and ran across the other hole of the same row; found by looking at the PNG). A callout text that would fall on another view is a warning.

### 4. Ordinates

Real `DIMENSION` entities (ezdxf `add_ordinate_x_dim` / `add_ordinate_y_dim`), datum = lower left corner of the view's visible geometry, `DIMLFAC = 1/scale` like every dimension, so 25 mm reads 25 at any scale. One ordinate per distinct coordinate (to 0.001 mm) plus 0 for the datum, anchored at the hole nearest the datum edge so the extension line is the shortest. They take the dimension row nearest the view (below for x, left for y); the overall dimensions move out one row, and the layout counts the extra row. Alternatives: chain dimensions between holes (rejected: ISO 129 chains accumulate and need more rules for the order), and a coordinate table (rejected: not a drawing entity a CAD user can pick or check). Limits, each with a warning: 6 groups per view, 16 coordinates per axis, overlapping ordinate texts are warned and not moved.

### 5. `dimensions` modes

`auto` is now the overall dimensions plus holes (it was "= overall"), `overall` keeps the S4 behaviour exactly, `none` draws no dimension and no callout. `dimension_count` counts `DIMENSION` entities (overall + ordinates); `callout_count` is new.

### 6. `features_path`: hash binding and matching

The file is read through `JobHandle.input_file` (so the read-directory allow-list, the size cap and the provenance record apply; it appears in `provenance.input_files` after the model). `step_sha256` inside must equal the sha256 of the model bytes that were drawn (the same copy that was hashed); otherwise a warning names both digests' first 12 characters and **nothing from the file is used** (`status: hash_mismatch`). A declared hole is matched to a modelled one by diameter (1e-3 mm), direction (parallel; for a blind hole the same sense), through or depth, and the centre line (1e-3 mm from the declared entry's line); each declared hole is used once, nearest entry first. A through hole matches whichever end the model calls its entry. Declared holes with no match get a warning with the count. Parsing reports `input.invalid` with a JSON pointer for every defect (`/holes/0/fit`), and strings that are printed are limited to 24 printable characters without line breaks: the file is data from outside, and a line break would reach the DXF text. `features_path` with `dimensions` other than `auto` is not read and says so.

### 7. SVG: our own backend

`dxf._svg_backend_class` replays the same recorded drawing as the PDF backend: `<path>` elements in sheet millimetres inside one `translate(0 H) scale(1 -1)` group, black on white, consecutive strokes of one weight and dash merged, numbers with at most three decimals, `HIDDEN_MM` as `stroke-dasharray="3 2"`, text as filled glyph outlines (no text layer, like the PDF). No dependency beyond ezdxf. The PDF backend was left unchanged. Alternative: ezdxf's own SVG backend (rejected in the S4 design: `ColorPolicy`/line weights not under our control, and its output carries more structure than we want).

### 8. PNG: PDFium through pypdfium2, in a new `png` extra

Methods compared:

| Method | Dependency and licence | Fidelity | Determinism | Verdict |
| --- | --- | --- | --- | --- |
| pypdfium2 renders our PDF | pypdfium2 (BSD-3-Clause OR Apache-2.0); the wheel bundles PDFium (BSD-3-Clause) and abseil, llvm-libc (Apache-2.0), AGG 2.3, FreeType (FreeType License), ICU (Unicode-3.0), lcms2, fast_float, simdutf (MIT), libjpeg-turbo (IJG, BSD), OpenJPEG (BSD-2-Clause), libpng, zlib; each licence text in the wheel was read; no GPL or AGPL | exactly what the PDF shows | same machine and PDFium build: same bytes; not across machines | **chosen** |
| our own rasteriser (NumPy scanlines) | none | would need anti-aliasing, joins, dashes, even-odd fill: about as much code as the PDF backend again | would be exact across machines | rejected for this slice; the way out if cross-machine bytes ever matter |
| render the DXF with ezdxf + Pillow/matplotlib | matplotlib is heavy; ezdxf's drawing add-on wants PyMuPDF for PDF only, but its raster path uses matplotlib | second code path that can disagree with the PDF | font and version dependent | rejected |

pypdfium2 was already in the `dev` group (it comes with `pdfplumber`). Moving it to runtime adds a native binary, so it is an optional extra, `png = ["eng-toolkit[drawing]", "pypdfium2>=5,<6"]`, registered in `licenses.KNOWN` (`extra="png"`, licence and bundled-library list), with one owner module (`drawing/raster.py`, enforced by `tests/test_engine_ownership.py`) and `scripts/install.ps1` installing it. `uv.lock` was refreshed offline; pypdfium2 5.14.0 was already locked. **It renders only the PDF this call has just written**, never a caller's file, so PDFium (native code) does not parse untrusted input here; `drawing_read` keeps using pdfminer.six for other people's PDFs. PDFium is not thread safe and tools run on a thread pool, so one lock guards every call (tested with four concurrent renders: identical bytes).

The page is rendered as 8-bit grey at 150 dpi, reduced so that the long side is at most 4000 px (A0: 85 dpi), and the PNG is encoded by `raster.encode_png` with the standard library: signature, IHDR, one IDAT (zlib level 9, filter 0), IEND, no time, text or gamma chunk. Pillow decodes it in the tests.

**What determinism is promised for the PNG.** Same machine, same PDFium build and zlib: the same bytes in any process and any `PYTHONHASHSEED` (`test_the_dxf_the_pdf_the_svg_and_the_png_are_the_same_bytes_under_every_hash_seed`, seeds 1 to 12 and unset, with a Chinese title and notes). Across machines: not promised; the result's warning says so. The PDF it comes from is reproducible per [drawing-determinism.md](drawing-determinism.md) (fonts aside). DXF, PDF and SVG keep the cross-process guarantee.

## Verification

* **Hand arithmetic.** `tests/test_drawing_sheet.py`: layout of the probe part with the hole (right margin 6 + 8 characters x 0.62 x 3.5 + 2 = 25.36 mm; column 0 = 16 + 100 + 25.36 + 15, total 206.36; corner (50.32, 74.5); view origins, ordinate end points 8 mm out, callout text at the view's right edge + 6 mm, leader from the R5 circle). `tests/test_drawing_callouts.py` and `test_drawing_features.py`: grouping, text, view choice, ordinates, parsing, binding.
* **Independent readers.** `tests/test_drawing_callouts_tool.py` reads the DXF with `tests/dxf_reader.py` (group codes) and recomputes each ordinate as |feature - datum| x the entity's own DIMLFAC; the SVG is parsed by the standard library's XML parser and its stroke lengths per weight and dash agree with what pdfminer.six reads from the PDF to 0.2 %; the PNG is decoded by Pillow and its pixels are checked where the geometry puts ink (frame at x = 20 mm, the hole circle at its computed place, paper elsewhere). The SVG was also drawn once by Chromium (the Browser pane) and matched the PDF's picture.
* **Round trip with `drawing_read` (S5 reader against the S6 writer).** `drawing_read` of the probe drawing returns the text `Ø10 THRU` and the ordinates `kind: ordinate` with `value_mm` 0, 0, 25, 25 and texts `0 0 25 25`; of the plate at 1:2 it returns `4× Ø6.6 THRU`, `Ø5 DEEP 8` and the ordinate values 0, 10, 50, 90, 0, 10, 25, 40 at true size (drawn 12.5 etc.). With the made part's features file the text is `Ø10 H7 THRU`; with another model's file there is no `H7` anywhere in the DXF.
* **Models without holes, without a view along the axis, surface-only IGES, 72 holes** (the S4 `plate96`: one callout `72× Ø6 THRU`, 20 ordinates, 7.3 s for DXF, PDF and SVG): tests and a manual run.

### What the check found and changed

* A `LEADER` that pointed at the first hole of a group crossed another hole (seen in the PNG): now it points at the hole nearest the text.
* `drawing_read` could not say which axis an ordinate belongs to (a flat list `ordinate 0 mm; ordinate 10 mm; ...`) and only counted the callout texts: the reader now reports `ordinate_axis`, groups ordinates by axis in the summary, and lists the texts that read like hole callouts with the number of holes each names (`4× Ø6.6 THRU (4 holes)`, a callout without a count is one hole). An `UnboundLocalError` for hole texts without any dimension came out of the first version of that change and has its own test.

### Small-model check (Claude Haiku)

Four drawings made by `drawing_make_views` whose answers are known by construction, 17 questions, two modes, two runs each, two rounds (round 1 before the summary change, round 2 after). **Summary** mode gives Haiku only `data.summary`; **data** mode gives the whole `data` object a tool call returns (6 to 7 kB, without `full_json` and `limits`). Haiku ran as a subagent that read one prompt file and was told to use no tool and answer only from it; the MCP server of this session had failed to connect, so the **tool** mode of the S5 check (Haiku calls `drawing_read` itself) was not possible. Scoring is fixed beforehand: numbers within 0.01, sets equal, `H7` contained, `none` for no fit; `unknown` is an abstention (counted apart from wrong answers).

| Drawing | Questions (known answer) |
| --- | --- |
| A: bracket, `Ø10 H7 THRU` at (25, 25), features file bound | diameter 10; number of holes 1; position (25, 25); fit H7; through |
| B: plate, `4× Ø6.6 THRU` and `Ø5 DEEP 8` | number of Ø6.6 holes 4; number of callouts 2; depth of the blind hole 8; x coordinates {10, 50, 90}; y coordinates {10, 25, 40}; Ø6.6 is through: yes; blind hole diameter 5 |
| C: probe drawn with another model's features file (hash mismatch) | fit: none; diameter 10 |
| D: probe at 1:2 | position (25, 25) real size; diameter 10; scale 1:2 |

| Round | Summary mode | Data mode |
| --- | --- | --- |
| 1 (summary without hole counts and axis meaning) | 23 / 34 correct, 11 unknown, 0 wrong | 25 / 34, 9 unknown, 0 wrong |
| 2 (after the change) | **31 / 34**, 3 unknown, 0 wrong | **33 / 34**, 1 unknown, 0 wrong |

Round 1 misses: the number of holes of a callout without a count (summary mode), every hole position, and the x and y coordinates of the plate: the model would not infer that ordinates are hole positions. Round 2 left three unknowns in summary mode (one hole position, and the fit of drawing C twice) and one in data mode (one hole position). Drawing C is the case that matters for safety: **no run, in either round or mode, answered `H7` for the drawing whose features file did not match**; over the two rounds 1 of 4 summary-mode runs and 3 of 4 data-mode runs said `none`, the rest `unknown`. (A reader of the summary alone cannot know that no fit is meant, only that the callout shows none.) The sample is small (one model, four drawings, two runs, one prompt) and the drawings are our own; it is a usability signal, not a benchmark, and it does not show that a model reads a crowded real drawing.

## Known gaps

* No centre marks or centre lines, no counterbore or countersink recognition, no features on inclined faces, no tolerances. Threads are text from `features.json` only.
* A leader can cross geometry (it runs from the hole to the margin at the hole's height); callout texts of more than 6 groups, more than 16 ordinates per axis and overlapping texts are warned, not solved.
* Hole search and callouts are skipped above 3000 faces and find no holes in surface-only IGES (cylinders are surfaces of revolution there); both have a warning.
* The PNG is not byte-stable across machines; it needs the `png` extra and a PDFium that loads on the platform. SVG was checked against the PDF numerically and in one browser, not in every viewer. Text in PDF, SVG and PNG is outlines or pixels, not text.
* `drawing_read` finds hole callouts by wording (a diameter sign followed by a digit, `THRU`, `DEEP`, `M6x1`): a note such as `Ø100 FLANGE` reads as a callout, and the "probably positions these holes" sentence is an inference. It does not pair an x and a y ordinate into one hole position when several holes are present.
* From the coordinator's verification of the previous slice, not changed here: text turned 90 degrees in a PDF comes back as one line per character, in reverse order (the font size is right since the previous slice; the grouping into lines is pdfminer's).

## How to verify independently

```bash
uv sync --inexact --no-install-project --extra cad --extra drawing --extra png
uv run --no-sync python -m pytest -q
uv run --no-sync python -m eng_toolkit doctor
```

Then: make a part with `geometry_make_part` (the example spec has the H7 hole), call `drawing_make_views(model_path=<its .step>, features_path=<its .features.json>, formats="dxf,pdf,svg,png")`, open `drawing.png`, read `drawing.dxf` back with `drawing_read` (texts `Ø10 H7 THRU`, ordinates 0, 25 in x and y), call it again with a different STEP and the same features file (expect the `hash_mismatch` warning and no `H7`), and with `PYTHONHASHSEED` 1 and 2 compare the sha256 of the four files.
