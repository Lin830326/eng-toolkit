# drawing_convert: DXF to PDF, SVG, PNG or another DXF release

Slice S8b of `docs/geometry-drawing/design.md`. One tool, `drawing_convert`, in the `drawing` domain (now 3 tools of the budget of 6). Code: `eng_toolkit/domains/drawing/convert.py` (pure: strings, page, scale, limits, loss rows), `dxf.py` (the ezdxf side: `convert_file`, runs in a child process; the two backends gained a `dash_of` argument), `tools.py` (the tool). Tests: `tests/test_drawing_convert_layout.py` (pure, 50 tests), `test_drawing_convert.py` (end to end, 63 tests), `test_drawing_convert_hostile.py` (hostile files, 33 tests), with independent PDF, SVG and PNG readers in `tests/convert_check.py` and the DXF reader `tests/dxf_reader.py`.

## Contract

```
drawing_convert(input_path, output_format, sheet="auto", scale="auto", dxf_version="R2018")
```

All strings. `output_format`: `pdf`, `svg`, `png`, `dxf`. The output is `converted.<format>` in the job directory with a sha256; the input is opened once through `JobHandle.input_file` (read-directory allow-list, size cap, copy, hash) and never written.

Values: `entity_count_drawn` (primary), `entity_count_skipped`, `mm_per_drawing_unit`, and for pdf/svg/png `page_width`, `page_height` (mm) and `scale` (0.5 = 1:2). `data`: `units`, `page`, `extents`, `entities`, `losses`, `limits`, `work`, `png`, and for dxf `dxf`, `entities_before`, `entities_after`, `dropped`, `converted`.

Errors: `input.invalid` (format, version, scale, sheet; a PDF named .dxf), `input.unsupported_format`, `input.file_not_found`, `input.path_not_allowed`, `drawing.corrupt` (empty, garbage, nothing recoverable), `drawing.empty` (no entity, or every layer off or frozen), `drawing.too_large`, `input.out_of_range` (absurd numbers, a page over 5080 mm), `drawing.layout_failed` (a given sheet and scale that do not fit), `engine.unavailable` (ezdxf, pypdfium2, and DWG), `engine.timeout`, `engine.resource_limit`.

## True scale

`factor = mm_per_unit(from $INSUNITS) x scale_ratio` is millimetres on the page per drawing unit. A 100 mm line at 1:1 is 100 mm on the page: 100 / 25.4 x 72 = **283.465 pt** in the PDF (test: the stroke runs from 28.346 to 311.811 pt on a page of 340.16 x 56.69 pt, and `drawing_read`'s PDF reader measures 100.00 mm). Other hand numbers in the tests: 10 inch = 254 mm = 720 pt; at 1:2 141.732 pt, at 2:1 566.929 pt, at 1:100 2.835 pt; on A3 the same line is centred at (160..260 mm, 148.5 mm); SVG root `width="297mm"` for A4.

* **Units.** From the file's own header text (`dxf_read._raw_header`), because the library fills `$INSUNITS` 6 (metres) into a file that has none. Codes 1 to 24 are known. A file with no `$INSUNITS`, with 0, or with a code that is not a unit is taken as **millimetres, with a warning** and `data.units.assumed = "millimeter"` (unlike `drawing_read`, which never assumes: a converter has to put something on the page).
* **Sheet.** `auto`: the drawn extents plus 10 mm on every side, so the page is custom sized. `A4` to `A0` (ISO 5457): landscape, portrait only if only that fits; the drawing is centred; margins 10 mm.
* **Scale.** An ISO 5455 ratio (`50:1` ... `2:1`, `1:1`, `1:2` ... `1:10000`) or `auto`. `auto` on an automatic sheet is true size unless the page would pass A0 (usable 1169 x 821 mm), then the largest reduction that makes it fit; on a named sheet it is the largest of `1:1` and the reductions at which the extents fit inside the sheet minus the margins. It never enlarges. A sheet and scale that do not fit together are **refused** (`drawing.layout_failed`, with the sizes), never clipped; a page over 5080 mm (200 inch, the PDF 1.4 limit) is `input.out_of_range`.
* **Extents** are what is drawn (the recorded primitives including text outlines and curves), not `$EXTMIN`, so a stray header value cannot size the page.
* **Line weights** stay in millimetres on the page (the layer or entity lineweight; the default is 0.25 mm = 0.709 pt); they do not scale with the drawing. **Dashes** come from the linetype pattern times the linetype scale, in drawing units, times `factor`; a pattern whose period is under 0.3 mm or whose dash is under 0.05 mm at that scale is drawn solid and listed.

## Formats

* **pdf**: the toolkit's own writer (PDF 1.4, one page, no compression, no dates), the same backend as `drawing_make_views`. **svg**: the same recording in millimetres, own writer. **png**: that PDF rasterised by PDFium at 150 dpi or less (4000 px long side), greyscale, in the child process. PDFium only ever parses the PDF this call just wrote.
* **dxf**: the document is loaded (strict, then salvage of a cut-off file, then ezdxf's recovery), the version is changed (`doc.dxfversion`), the file is saved with fixed metadata (no dates or GUIDs; CLASS records sorted), and read back with ezdxf to check it and to count what was lost. R12, R2000, R2004, R2007, R2010, R2013, R2018. R13 and R14 cannot be written by the library and are refused by name. `sheet` and `scale` are ignored (warning).
* **dwg**: converted to DXF first by the user's LibreDWG `dwg2dxf` (slice S9, `docs/drawing-dwg/design.md`); without the program `engine.unavailable` with how to get it. Recognised by the `.dwg` extension and by a DWG header inside a `.dxf`.

## Losses (`data.losses.items`)

Each row has a stable `code`, a sentence and a `count`. `lossless` is true only when the list is empty.

| code | when |
| --- | --- |
| `text_as_outlines` | pdf/svg/png: text is filled glyph outlines, no text layer (counts TEXT, MTEXT, ATTRIB) |
| `fonts_replaced` | a text style whose font is an SHX font or not installed here is drawn with the default font (`data.entities.fonts` lists style, font, entities) |
| `colour` | n entities resolved to a colour other than black; all ink is black |
| `mtext_formatting` | MTEXT with inline codes (`\f`, `\S`, ...): ezdxf applies what it supports |
| `hatch_pattern`, `hatch_outline_only`, `hatch_incomplete` | pattern hatches are lines; a hatch needing over 200 000 segments is drawn as its boundary; a hatch that used its 3 s |
| `linetype_solid` | patterns too fine for the scale |
| `entity_skipped` | per type and reason: `unsupported` (3DSOLID, BODY, REGION, proxy, OLE frame ...) or `invisible` (layer off or frozen) |
| `images_not_drawn`, `paper_space`, `xref_not_loaded` | IMAGE (the file named is never opened), paper space (model space only), external references |
| `entity_removed` | removed before conversion: non-finite coordinates, a block reference to a missing block, a loop, nesting over 16, a text over 20 000 characters |
| dxf: `entity_dropped` | types the chosen release does not store, **counted by writing the file and reading it back** (R12 drops ELLIPSE, SPLINE, MTEXT, HATCH, ...) |
| dxf: `entity_converted` | R12 only: each LWPOLYLINE becomes a 2D POLYLINE with the same vertices, bulges, widths |
| dxf: `lineweight`, `true_colour`, `transparency`, `units`, `unicode`, `layouts` | properties the release lacks, found by looking at what the document uses: R12 has no lineweight, true colour, transparency or `$INSUNITS`; R2000 no true colour or transparency; R12 to R2004 text is in a code page and what it cannot encode becomes `\U+XXXX` |

`data.entities` (pdf/svg/png): `model_space_by_type` (the file), `drawn_by_type` and `skipped_by_type` with reasons (instances, block references expanded: an array of 6 copies of a LINE counts 6 LINEs and 1 INSERT), `drawn_total`, `skipped_total`.

## Limits and hostile files

The file is untrusted. It is converted by `eng_toolkit.isolated` in a child process (`python -P`, memory cap by Job Object on Windows or `RLIMIT_AS`, time limit; where no cap exists `memory_cap_applied` is false and a warning says so). Defaults (`convert.ConvertLimits`, all in the tool description): time 60 s and 2048 MB (`ENG_TOOLKIT_CONVERT_TIMEOUT_S`, `_MEMORY_MB`), file 50 MB (`_MAX_MB`), 200 000 entities in the file, **1 000 000 units of work** (entities + vertices + 5 per text character, after block references are expanded, counted without drawing them), block nesting 16, one text 20 000 characters, a hatch 200 000 segments, any coordinate or factor 1e12 mm (floor 1e9 drawing units), page 5080 mm, output 200 MB. Measured on the development machine (Windows 11, ezdxf 1.4.5): 190 000 LINEs to PDF 11.5 s, 190 000 circles to PNG 19.3 s, which is why the caps are where they are.

| hostile input | result |
| --- | --- |
| block containing itself, two blocks containing each other | the repeated INSERT is removed and listed, the rest is drawn (ezdxf alone raises `RecursionError`) |
| 20 nested blocks | a reference that would make a chain over 16 is removed and listed |
| INSERT array of 100 000 x 100 000; arrays of arrays (1e12 lines); 10 blocks each holding 10 of the next, 8 deep | `drawing.too_large` with the computed work (10 000 000 001; > 1e12; 21 111 111), in about 1.4 s, nothing drawn |
| 1 000 000 LINEs | `drawing.too_large` by counting records (a record is a 0 group followed by a name; a coordinate `0` is not one) in 2.5 s, nothing loaded |
| one TEXT of 1 000 000 characters; 100 texts of 15 000 | the first is removed and listed; the second is `drawing.too_large` (7 500 100 units) |
| NaN or infinite coordinate, radius | the entity is removed and listed; no NaN or Infinity can reach the result (every backend number is checked; the gateway-client tests parse with `allow_nan=False`) |
| 1e308, 1e200, 1e13, a text height 1e30, an INSERT scale 1e300 | `input.out_of_range` naming the entity and the limit |
| coordinates small in file units but huge in mm (1e5 astronomical units, 1e11 km, INSERT scale 1e11 of a 1e5 line) | `input.out_of_range` from the drawn extents |
| hatch with a pattern of lines 7e-5 mm apart | boundary only, listed; a 1e6 mm hatch is refused by the page limit instead of exhausting memory |
| polyline with 1 100 000 vertices | `drawing.too_large` |
| a file that takes longer than the limit | `engine.timeout`, "took longer than N s to convert" (the child checks the clock between entities and the parent kills it at the limit) |

`ezdxf` loads image and xref names without opening them; the renderer's image policy is `IGNORE`, so a path in the drawing is never opened. Text in the drawing never appears in the answer except as counts, style names (cut to 80 characters, at most 60 listed) and font file names.

## Decisions

* **One recording, three backends.** ezdxf's `Frontend` draws into a `Recorder`; `Player.transform` scales and centres the records; the PDF and SVG backends of `drawing_make_views` replay them. The pieces ezdxf's own PDF/SVG backends would add (PyMuPDF, AGPL) are avoided as before. Linetype dashes are not made into geometry (a 1e9 mm line with a fine pattern would be billions of segments): the line policy is `SOLID` and a property override puts a dash index in the backend property `pen`, which the backend turns into a PDF/SVG dash array.
* **Cycle and depth cut by graph, not by catching `RecursionError`.** `_analyse` walks the block graph iteratively (a chain of 100 000 blocks cannot overflow the stack), cuts the INSERT that closes a loop, and cuts a reference that would make a chain over 16 (the rule is on the chain below the reference, so a deep tail is kept and the reference that makes the chain too long is cut). It runs for every output format, so a DXF-to-DXF conversion does not pass a loop on.
* **Non-finite is removed, absurd is refused.** A NaN entity cannot be drawn and removing it loses nothing real; a finite 1e200 is most likely a unit disaster, and dropping it silently would hide that.
* **Record counting.** `drawing_read`'s pre-check counts every line equal to `0`, including coordinate values, which would refuse a 60 000-entity file full of zero coordinates at this tool's cap; the converter counts group-0 lines followed by a name. (`drawing_read` itself is unchanged.)
* **Fixed metadata for DXF output**: the DXF has no dates or GUIDs and a sorted CLASS section, so the same input gives the same bytes in any process. The original timestamps and GUIDs are not carried (not reported as a loss).

## Not done, not verified

* Not built: DWG output; colour; a text layer (searchable PDF text); paper space and layouts (only model space); viewports; a dpi parameter (PNG is fixed at 150 dpi or the 4000 px long side); a title block or frame on the converted page (the converter adds nothing to the drawing); conversion of ELLIPSE, SPLINE and MTEXT to something R12 can hold (they are dropped and listed); complex linetypes with text or shapes (only the dashes are used).
* Not verified: that AutoCAD, BricsCAD or LibreCAD open the DXF written for each release (checked: written by ezdxf, read back by ezdxf, `ezdxf` audit finds no errors for R12 in an experiment, and by the independent reader `tests/dxf_reader.py` for R2000 and R12); PDF in a viewer other than pdfminer.six and PDFium; the memory cap on macOS (not applied, the time limit applies); the figures in the limits table on another machine; fonts: the outlines depend on the fonts installed (the PDF is byte-stable on one machine, not across machines), and the PNG additionally on the PDFium build.
* `drawing_read` still counts value-`0` lines as records (see Decisions): a file with about 100 000 entities and many zero coordinates can be refused there although it is under its 500 000-entity cap. Not changed here; a follow-up.
* The loss list for DXF-to-DXF does not see what ezdxf drops inside objects (extension dictionaries, extended data it does not keep, custom classes): only entity types, the listed properties and layouts are counted.
