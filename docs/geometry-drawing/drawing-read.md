# drawing_read: design record (slice S5)

Written with pstack's `architect` skill, scaled down: Phase A was done by reading the code and design documents named in the slice brief and by measuring the engines on this machine (below); Phase B is "design it twice" per decision, with the alternatives and why they lost, written by one model. The multi-model arena (two opus xhigh runners and a cross-judge, as for the geometry and drawing design) was **not** run; an independent adversarial review of the code by a second agent was (see "Verification"). Cursor-only steps of the skill do not exist in Claude Code and were skipped (no Automations, Bugbot or cloud agent was needed); `poteto-mode` needs `bun`, which this machine does not have, so it was not used.

## Problem

An agent is handed a drawing somebody else made (DXF or PDF) and has to answer plain questions about it: which part, which units, how big, how many views, what the dimensions say. Three things make this harder than "parse the file":

1. **Units must never be assumed.** An inch drawing read as millimetres is a 25.4x error that nothing downstream notices.
2. **Dimension text is not the geometry.** A `DIMENSION` stores the measured points; what the viewer shows is `measurement x DIMLFAC`, rounded, with an optional text override. Slice 4 writes every dimension with a `DIMLFAC = 1/scale` override in the entity's own `DSTYLE` extended data, so a reader that looks only at the dimension style table reads 50 where the sheet says 100.
3. **Both file types are untrusted input read inside the MCP server.** A truncated file, a block that inserts itself, a compressed stream that expands a thousand-fold, a PDF with JavaScript must each give a structured error or a warning, and must never hang or kill the server.

## Usage (caller's view)

```text
drawing_read(path="C:/drawings/flange.dxf")
  -> ok, value entity_count = 214
     data.summary      "DXF R2018 (AC1032), ... Units: inch (from $INSUNITS); 1 drawing unit = 25.4 mm. ... Title block (...): ..."
     data.units        {insunits 1, name inch, mm_per_unit 25.4, source "$INSUNITS"}      # never silently mm
     data.title_block  {fields: {part_number: {value, source, inferred, confidence}, ...}}
     data.dimensions   {total, shown, items: [{kind linear, measured 50, dimlfac 2, value 100, text "100", ...}]}
     data.full_json    path of drawing_read.json (everything, up to the artifact limits)
drawing_read(path="C:/drawings/sheet.pdf")
  -> pages with size in mm, text lines with positions, vector statistics, warnings (no text layer, JavaScript present)
drawing_read(path=..., detail="full", max_items=200)   # more fields per item, more items inline (hard cap 500 per list)
```

The result keeps the toolkit contract: `value`/`values` carry dimensionless counts and lengths in millimetres, `data` the structured content, `provenance.input_files` the file with its sha256, `review_status = unreviewed`.

## Shape

```text
eng_toolkit/
  isolated.py                    run_isolated(target, args, timeout, memory): child process, hard time and memory limit, structured failure
  runner.py                      JobHandle.input_file also enforces the optional ENG_TOOLKIT_READ_DIRS allow-list; read_dirs()
  gateway/server.py              toolkit_doctor reports data.file_reads
  domains/drawing/
    __init__.py                  DOMAIN and server are built on first use, so importing a reader does not import FastMCP (1.9 s)
    reading.py                   pure: Limits, the INSUNITS table, units_info (never assumes)
    dimensions.py                pure: measured value per dimension type, DIMLFAC resolution, text formatting
    titleblock.py                pure: attribute and label-pair vocabulary (English + Chinese), pairing with confidence
    plane.py                     pure: arc and bulge points, convex hull, affine maps, clustering
    shaping.py                   pure: the summary paragraph and the bounded `data` from a reader's full result
    dxf_read.py                  the ezdxf adapter for reading (second owner of ezdxf, beside dxf.py the writer)
    pdf_read.py                  the pdfminer.six adapter (the one owner of pdfminer)
    tools.py                     the `read` tool: validates strings, runs the adapter isolated, shapes the contract
```

Data flows: `tools.read` checks the arguments, then `job.input_file` (extension, allow-list, existence, sha256 recorded), then runs `dxf_read.read(path, limits)` or `pdf_read.read(path, limits)` **in a child process**. The child returns one plain dict (the full content, JSON). The parent writes it as `drawing_read.json`, builds the bounded `data` from it (`shaping.shape`) and wraps both in `ToolResult`. Pure modules take plain numbers and strings, so the hard parts (dimension arithmetic, title-block pairing, clustering) are tested without any engine.

## Decisions

### 1. Output structure and size control

Chosen: **summary inline, everything in a JSON artifact.** `data` holds a one-paragraph `summary` string, the facts a question is likely to need (units, extents, layers, entity counts, title block, dimensions, the first texts, blocks with attributes, warnings), each list capped by `max_items` (default 25, at most 500) and carrying `total` and `shown`. `drawing_read.json` has the lists in full up to hard caps (20 000 items per list). `detail="full"` adds fields to the items; it never inlines an unbounded list. Measured size of the default `data`: 5.6 kB for a slice-4 drawing, 2.4 kB for a 200 000-entity file; a test fails above 14 kB for a drawing with 120 layers and 300 texts, and above 9 kB for a slice-4 drawing.

Alternatives: (a) return everything inline: a 5 000-entity drawing is hundreds of kilobytes in the model's context, and the model cannot tell what matters. Rejected. (b) several tools (`drawing_read_dimensions`, `..._texts`): the project wants few coarse tools. Rejected. (c) paging parameters (`offset`, `limit` per list): more parameters for a small model to get wrong; the artifact already holds the rest. Rejected for v1.

The one-paragraph `summary` exists because the small-model check (below) reads it first; the structured fields are for programs and for follow-up questions.

### 2. DXF engine: ezdxf, in the `drawing` extra, behind a second owner module

Chosen: **ezdxf (MIT)** for loading, including its recovery mode for damaged files, code-page decoding, the entity classes and the extended-data accessors. It is already in the `drawing` extra (owner decision 5 keeps it out of the core). The ownership rule in `tests/test_engine_ownership.py` said one module (`dxf.py`, the writer); it now names two owner modules of the same package (`dxf.py` writes, `dxf_read.py` reads) and a third rule for pdfminer. A reader added to the 365-line writer would have hidden two unrelated jobs in one file; an engine import anywhere else is still a test failure.

Alternative: a hand-written tag parser (no engine). Rejected for production: it would re-implement recovery, six code pages, DXF Unicode escapes, binary DXF and every entity's group codes. It is used where it belongs: `tests/dxf_reader.py`, the stdlib reader slice 4 already has, and the raw DXF text built by `tests/raw_dxf.py` are the independent oracle. The reader's tests do not generate their material with ezdxf and read it back with ezdxf.

What ezdxf does not give us and we do ourselves: dimension values (ezdxf's `get_measurement()` excludes DIMLFAC; group 42 is optional and unreliable), the DIMLFAC resolution order, extents that survive cyclic block references, the title block, and **the units**: ezdxf fills header variables a file does not have with defaults (a file with no HEADER section reports `$INSUNITS` 6, meters, which is a silent assumption), so `$INSUNITS`, `$MEASUREMENT`, `$ACADVER` and the dimension variables are read from the file's own HEADER text (`dxf_read._raw_header`). This was found by the large-file check, not by design.

### 3. PDF engine: pdfminer.six alone

Candidates, with the licences read from the installed wheels' metadata on this machine:

| Candidate | Licence | Brings | Verdict |
| --- | --- | --- | --- |
| pdfminer.six 20260107 | MIT | charset-normalizer (MIT), cryptography (Apache-2.0 OR BSD-3-Clause; for encrypted files) | **chosen** |
| pdfplumber 0.11 | MIT | pdfminer.six, Pillow (MIT-CMU/HPND), pypdfium2 | rejected: a convenience layer; pulls Pillow and a native PDF engine we do not call |
| pypdfium2 5.14 | BSD-3-Clause OR Apache-2.0 (wheel also bundles other notices) | native PDFium | rejected for production: native code parsing untrusted input would take the server down on a crash, and it exposes no path segments |
| PyMuPDF | AGPL | | forbidden, guarded in `licenses.KNOWN` |

pdfminer.six is pure Python (a malformed file raises an exception rather than faulting), gives characters with fonts, sizes and boxes, and gives paths with their segments, line width and dash pattern (`LTCurve.original_path`, `.linewidth`, `.dashing_style`), which is what vector statistics need. It is added to the `drawing` extra (`pdfminer.six>=20260107,<20270101`, the version `pdfplumber` pins) and to `licenses.KNOWN` (`extra="drawing"`); `tests/test_extras_registry.py` and `toolkit_doctor` keep pyproject and the registry in step (doctor exit 0, 90 distributions all `permissive`, no `unknown`). `pdfplumber` stays in the `dev` group, where it is a test oracle, together with `pypdfium2` (which comes with it) as a second, native oracle for text.

Slice 4's own PDFs have **no text layer** (text is glyph outlines). The reader says so: `text.has_text_layer = false`, the filled-path count is reported as the likely glyph outlines, and the warning tells the caller to read the DXF the PDF came from. It never pretends to have read text.

### 4. Untrusted input: a child process with hard limits, not in-process checks

Chosen: both readers run in **a child process** (`isolated.run_isolated`: `python -P -m eng_toolkit.isolated`, request on stdin as JSON, answer in a file as JSON, never a pickle). The parent waits at most `timeout_s` (default 30) and kills the child and its children if it is late (`taskkill /T` on Windows, because a virtual environment's `python.exe` is a launcher that starts the real interpreter and killing only the launcher left the real one running: found by the first timeout test); a child that dies is `engine.worker_died`, one that runs out of memory `engine.resource_limit`; on Windows the child puts itself into a Job Object with a process memory limit (default 2048 MB) and a one-process limit, on Linux it sets `RLIMIT_AS`; elsewhere only the time limit applies and `data.limits.memory_cap_applied` and a warning say so. The pages and entities are also capped inside the readers, so a normal-sized hostile file fails fast and cleanly, and the child is the backstop for what the counters cannot see.

Why not in-process: ezdxf's loader and pdfminer's parser cannot be interrupted, so an in-process "time limit" is only checked between operations and a pathological stream defeats it; a Flate stream that expands a thousand-fold is allocated before any counter sees it. Measured on this machine: ezdxf loads 17.5 MB / 200 000 entities in 3.9 s; the whole tool takes 6.1 s on that file; a 2.5 GB Flate bomb of 2.4 MB is stopped by a 400 MB cap. The limits (50 MB file, 500 000 DXF entities, 200 PDF pages, 30 s, 2048 MB) leave room for large honest files.

Cost: a child start is about 1.2 s (Python and the engine; importing the whole domain package for FastMCP used to add 1.6 s, which `drawing/__init__.py` now avoids by building `DOMAIN` and `server` on first use). Owner decision 7 ("no worker process per call in v1") is about OpenCASCADE and is untouched; this is a separate, narrow use that the design's own risk list asks for ("untrusted file parsed in the server process").

What the readers do with specific hostile content (all tested):

| Input | Result |
| --- | --- |
| truncated DXF | the last half-written record is dropped, the open sections closed, the rest read; a warning says everything after the cut is missing. ezdxf's own recovery mode returned 0 entities for every truncation tried (30% to 90% of the file), so this step comes first and the library's recovery second |
| binary garbage, empty file, text that is neither DXF nor PDF | `drawing.corrupt` (never an `engine.failure` traceback) |
| a DWG, or a DWG named `.dxf` | `input.unsupported_format` naming `dwg2dxf` (S9 handles DWG) |
| a PDF named `.dxf`, a DXF named `.pdf` | `input.invalid` naming the real format |
| nested blocks to any depth, a block that inserts itself, A inserts B inserts A | block extents are convex hulls computed once per block, with a depth limit (16) and a path check; a warning names the cycle; the rest is still measured; 200 nested blocks and ten-copies-twelve-deep (10^12 visits if followed) both finish in seconds |
| a huge `INSERT` array (10^9 columns and rows) | counted, never expanded: only the four corner instances enter the extents |
| XREF blocks and IMAGE paths | listed (`blocks.xrefs`, `images`) with a warning that nothing was loaded or followed |
| a PDF with `/JavaScript`, `/JS`, `/URI`, `/Launch`, `/OpenAction`, `/EmbeddedFile` | named in `pdf.active_content` and a warning; never executed or followed. Found by walking the parsed objects (so a JavaScript action inside a compressed object stream is found; the test file does not contain the word in its bytes) and by scanning the raw bytes with `#xx` hex-escaped names decoded. Link targets are not copied into the warning |
| an encrypted PDF that needs a password | `drawing.encrypted` (the empty user password is tried because pdfminer does) |
| a Flate bomb | the child's memory limit ends it: `engine.resource_limit` or `engine.worker_died` |
| a page tree that lists itself, a form XObject that draws itself | read without looping, or `drawing.corrupt` |

### 5. Reading arbitrary paths over HTTP

Chosen: **documented, and an optional allow-list**. `ENG_TOOLKIT_READ_DIRS` (directories separated by `os.pathsep`; unset = unrestricted, which is right for the default stdio transport; set but empty = the workspace only, since an empty list must not fail open where the server runs as the user who asked) restricts every tool that reads a file, because the check lives in `JobHandle.input_file`, the one place all of them go through. The workspace root is always allowed so a tool can read back what another tool wrote. Paths are resolved (symlinks, `..`, case on Windows) before the comparison. A refused path is `input.path_not_allowed`; the check runs before the existence check so the error cannot be used to probe which files exist, and the message names the variable and the number of directories, not the paths. `toolkit_doctor` reports `data.file_reads`. The README says to set it whenever `--transport http` is used.

Alternatives: (a) a `--read-dir` command-line flag only: an environment variable reaches the gateway the same way for stdio and HTTP and is what the workspace root already uses. (b) always restrict to the workspace plus the current directory: breaks the common case of reading a drawing in `Documents`. (c) do nothing but document: the design's risk list asked for a gate or documentation; the gate is a few lines in a function that already exists.

The check and the open used to be two steps (time of check to time of use): the coordinator session reproduced 5 leaks of an outside file in 78 reads while a directory inside the allowed one was swapped for a junction to the outside, because the parent checked the path and the child process then opened it again. `JobHandle.input_file` now opens the file **once** (`eng_toolkit/safefile.py`), asks the system for the real path of the *open file* (`GetFinalPathNameByHandleW`; `/proc/self/fd` on Linux; `F_GETPATH` on macOS; elsewhere `realpath` verified by `samestat`), compares that with the allow-list, and copies the bytes from the open file into `ws/<job-id>/input/<n>-<name>`, hashing them as they are copied. Every tool that goes through `input_file` (drawing_read, drawing_make_views, geometry_measure, metrology_check_scan) reads that copy; nowhere is the caller's path opened a second time. `provenance.input_files` keeps the real path of the original and the sha256 of the copied bytes, so the hash is the hash of what was processed. The lexical check before the open stays, only to keep the error order (a path outside the list is refused whether or not it exists). Measured with a thread that exchanges a real directory and a junction to the outside as fast as it can (about 1900 swaps in 4 s): the old logic leaked 411 of 4269 reads, the new one 0 (`tests/test_input_file_race.py`, plus the coordinator's `junction_test.py`: 61 reads, 0 leaks). Cost: every input file exists twice in the workspace. Not covered: the allowed directories themselves being swapped by an attacker, and an empty `ENG_TOOLKIT_READ_DIRS` meaning "unrestricted"; it is a guard against an agent asking for the wrong file, not a full sandbox.

### 6. Units and dimension values

Units: `$INSUNITS` (24-entry table) gives `name` and `mm_per_unit`. `INSUNITS = 0` (unitless) or a missing variable (R12 has none; a file may have no header at all): `mm_per_unit` is `null`, every `*_mm` field is `null`, the summary says `UNKNOWN`, a warning says "not assuming millimetres or inches", and `$MEASUREMENT` is reported as a hint (metric or English) that is never used to convert. Inch drawings report `name: "inch"`, `mm_per_unit: 25.4` and each length in both units.

Dimensions: the measured value comes from the definition points, per type (linear/rotated, aligned, diameter, radius, two-line angle, three-point angle, ordinate), not from group 42. The two-line angle is the sector between the two lines that holds the dimension-arc point (0 to 180 degrees); the three-point angle runs counterclockwise from the first leg to the second (as ezdxf computes it). The `DIMLFAC` is resolved as the entity's own `DSTYLE` extended data (what slice 4 writes), else the dimension style table entry, else the file's header `$DIMLFAC` (when the style entry does not set it), else 1; a negative value applies, as its absolute value, in paper space only. DIMLFAC scales linear, radius, diameter and ordinate values, never angles. The shown text is: the text in the dimension's anonymous block (what a viewer draws), else the text override with `<>` replaced by the formatted value (a single space hides the text), else the value formatted from DIMDEC, DIMZIN, DIMRND, DIMPOST, DIMDSEP (`Ø` before a diameter, `R` before a radius, `°` after an angle); fractional and architectural unit formats are not computed. Each dimension reports `measured`, `dimlfac`, `dimlfac_source`, `value` (= what is shown, numerically), `value_mm`, `text`, `text_source` and, when the block text and the computed text disagree, the file gets a warning.

A cross-check that needs no oracle of ours: ezdxf renders its own dimensions into blocks. Reading a file ezdxf wrote with its built-in `EZDXF` style (DIMLFAC 100) gives computed texts `10000`, `Ø4000`, `R3000`, `60°` identical to the texts ezdxf drew (no "differs" warning).

### 7. Title block

Two sources, both reported with how sure we are. **Attributes** (`INSERT` with `ATTRIB`): the tag is looked up in a fixed vocabulary (English and Chinese synonyms); a hit is `inferred: false, confidence: 1.0`; the INSERT with the most recognised tags is the title block. **Label pairs** (what slice 4 writes: a label text and a value text on the `TITLE` layer, value below the label): the value is the nearest text that is not itself a label, below the label and overlapping it horizontally, or to its right on the same line; matching is one-to-one by distance, so an empty cell does not take the value of the cell below it; the field is `inferred: true`, confidence 0.85 on the same layer (minus 0.25 on another, minus 0.15 when far). `LABEL: value` in one text is 0.9. Labels or tags outside the vocabulary are listed unmatched, never guessed. The line `UNREVIEWED - generated by eng-toolkit ...` is reported as `status`, and a drawing with the slice-4 layers, dimension style and status line is flagged `recognised_as`. Views are counted by clustering the geometry that is not on sheet-furniture layers (inferred, confidence 0.7).

## Verification

**Independent of the reader's engines.**

* DXF test material is raw text assembled by `tests/raw_dxf.py` (group-code pairs as strings); every expected number is worked out from the coordinates written there (inch extents 254 x 101.6 mm, lengths 10 + 5pi/2 + 4pi + 16 + pi, DIMLFAC 2 on 100 gives 200, and so on). R12, R2000, R2004, R2007 and R2018 files; Big5 (cp950), GBK, Shift-JIS and cp949 files decoded by their `$DWGCODEPAGE`; `\U+XXXX` escapes; `%%c %%d %%p`; MTEXT formatting.
* PDF test material is raw bytes assembled by `tests/raw_pdf.py` (known strings at known coordinates, an exact xref table), including a JavaScript action inside a compressed object stream, a 2.5 GB Flate bomb, a self-listing page tree and a self-drawing form. Cross-checks with engines the reader does not use: `pypdfium2` (native PDFium) finds the same strings; `pdfplumber` finds the same words and the same single rectangle.
* The stdlib DXF reader of slice 4 (`tests/dxf_reader.py`, imports nothing but the standard library) gives the layer lengths the round-trip test compares with (`OUTLINE`, `HIDDEN`, `FRAME`, `TITLE`).
* Round trip on slice 4 (`drawing_make_views` of the probe part, 1:1, 1:2, a single view, first angle): dimensions 100, 50, 20 (at 1:2: drawn 50, 25, 10 with `DIMLFAC` 2 from the entity override), title block fields, scale, projection, 3 views (1 for a single view), layer lengths, hidden length 80 mm, and the PDF: no text layer, page 297 x 210 mm, dashed length equal to the `HIDDEN` layer.
* Dimension arithmetic is a pure module with hand-calculated unit tests (45 cases).

**Bugs these checks found while building** (each has a regression test): a timeout left the real interpreter running because only the venv launcher was killed; `Blocks.max_nesting` counted the model-space block as one level; ezdxf's recovery mode returns zero entities for a truncated DXF; clustering copied the member list on every merge (72 s on 200 000 lines, 5.5 s after the fix: the file was found by timing a 17.5 MB file through the tool); ezdxf reports `$INSUNITS` 6 for a file with no HEADER section (a silent meter assumption); PDF text lines came back in pdfminer's box order, not reading order (a Haiku question about "the first line" failed); the `TOLERANCE` entity's text lives in `content`, not `string`. The adversarial review then found the ones listed under "Review findings".

**Adversarial review.** A second agent that had not seen the code was given the files and the requirements and asked to write new hostile files and break the readers; its findings are listed in "Review findings" below.

**Headless run.** `claude -p ... --mcp-config` with this worktree as the server: the model called `drawing_read` on the inch drawing and returned the summary. See the report of the slice for the pasted output.

### Small-model check (Claude Haiku)

Ten questions about five drawings whose answers are known by construction (`make_samples.py` and `haiku_check.py` were scratch files, reproduced in the method below). Two modes: **tool** (Haiku has `drawing_read` through MCP and calls it itself) and **summary** (Haiku gets only `data.summary`, no tools). Scored by fixed rules (substring or number within tolerance); the strict yes/no rule for Q10 takes the first word.

| # | File | Question | Known answer |
| --- | --- | --- | --- |
| Q1 | bracket_a.dxf (slice 4) | part number | BR-2041 |
| Q2 | same | material | AL6061-T6 |
| Q3 | same | number of views | 3 |
| Q4 | same | overall dimensions | 100, 50, 20 |
| Q5 | same | projection method | third angle |
| Q6 | flange_inch.dxf (hand-made, inch, attribute title block) | unit of the drawing | inch |
| Q7 | same | length of the linear dimension in mm (drawn 2.25 in, DIMLFAC 2) | 114.3 |
| Q8 | same | part number (attribute DWG_NO) | FL-7788 |
| Q9 | zhongwen_cp950.dxf (Big5, label/value title block) | part name (品名) | 法蘭 |
| Q10 | bracket_a.pdf | can text be read from it? | no |
| extra | bracket_half.dxf (1:2) | scale; drawn vs real longest dimension | 1:2; drawn 50, real 100 |
| extra | legacy_r12.dxf (no units) | longest line in mm | unknown |
| extra | hand_made.pdf | first line of text | HELLO FL-001 |

Results (one run each unless said):

| Run | Tool mode | Summary mode |
| --- | --- | --- |
| first run, first build | 10 / 10 (extra X1 wrong: lines were not in reading order) | 10 / 10 (X1 wrong) |
| after sorting PDF lines top to bottom | 10 / 10, extras 4 / 4 | 10 / 10 with a lenient Q10 rule; **9 / 10** with the strict rule (one answer began "Yes, but no text can be read"); extras 4 / 4 including "unknown" for the unit-less file |
| Q10 only, summary sentence "NO TEXT LAYER, so no text was read" | | 2 of 3 earlier answers began with "No"; with the sentence "Text: NONE ..." 4 / 6 |
| Q10 only, summary sentence "Readable text: NO. This PDF has no text layer ..." | | **7 / 8** (the miss was "Yes. Wait, correction: no.") |
| final code (after the review fixes), 2 runs, strict rule | **20 / 20**, extras 8 / 8 | **20 / 20**, extras 8 / 8 |

What changed because of the check: the PDF lines are sorted top to bottom, then left to right; the PDF summary sentence for a file without text now answers the question the way a person asks it ("Readable text: NO"); the summary says `UNKNOWN` loudly for unit-less files and the model answered "unknown" for the unit-less line instead of 250 mm (2 / 2 times it was asked, once per mode); grammar of counts ("1 group", "inches"). The output was never too long: the default `data` is 5.6 kB, the summary about 700 characters. The check is one model, a few drawings and a handful of runs: a usability signal, not a benchmark, and it does not show that a model reads a long or messy real drawing correctly.

## Review findings

A second agent, which had not seen the code, was given the files and the requirements and asked to write new hostile inputs and break the readers (61 tool calls, 14 reproduced problems; it found no crash of the server and no hang past the time cap). Every finding below has a regression test in `tests/test_drawing_read_review.py` or `tests/test_isolated.py`.

| Finding | Severity | Resolution |
| --- | --- | --- |
| a missing block (a dimension's `*D1`, an inserted block) made the dimension or the INSERT, with its attributes and so the attribute title block, be dropped | dropped content | a missing block is an empty block; the dimension and the attributes are read |
| a DIMLFAC that converts units (25.4 on an inch drawing) gives a wrong `value_mm` | wrong number | cannot be told from the file which unit the shown value is in, so a DIMLFAC within 1e-6 of 25.4 or 1/25.4 on a drawing with a unit gets a warning naming `value_mm` |
| extents ignored an OCS extrusion (a mirrored arc) | wrong number | extrusion (0,0,-1) mirrors x exactly; any other plane is counted and warned about |
| polyface face records (vertex flag 128) pulled the extents to the origin | wrong number | face records are skipped |
| NaN or infinity in coordinates, lengths or DIMLFAC poisoned results and made the artifact invalid JSON | misleading | such entities are ignored with one warning; a non-finite DIMLFAC is not applied; the child writes strict JSON (`allow_nan=False`), anything else is `engine.failure` |
| `$INSUNITS` 4.9 read as millimetre (truncated); 1e999 raised OverflowError; US survey units 21 to 24 "not defined"; a BOM or U+0085 in the header hid the unit | misleading | integer or nothing; the four US survey units are in the table; the header is split only at CR and LF and the BOM is dropped |
| layer names differing only in case counted as separate layers | minor | one layer (the name first seen) |
| a lone surrogate (`\U+D800`) made the answer file unwritable and the call fail | crash-like | the child writes ASCII JSON and replaces lone surrogates with U+FFFD (pairs are kept) |
| a PDF with a short `/Length` reported "no text layer" for a page whose content was dropped; a CropBox was ignored; a malformed MediaBox silently became Letter | misleading | new warnings: "empty or its content could not be parsed", CropBox (`cropbox_mm`), MediaBox "default" |
| the raw scan for active content had holes: `/JS` was overwritten by `/JavaScript` in a dict, `EmbeddedFiles?` never matched, the object walk stopped at 1000 array items and 8 levels, `/AA` fired on binary image data, 100k links counted twice | misleading | a list of patterns with the right names, stream bodies skipped (linear), `/AA` only before a value, depth 40 and 100 000 items, parsed counts win over raw counts |
| no cap on string length: a 10 MB text, a 3 MB layer name, 100 000 attributes on one INSERT, a 50-million-digit DIMDEC and a 20 MB garbage file's error message each gave an answer of megabytes | output size | strings are cut at 4000 characters in the full result and 500 in the answer (2000 with `detail="full"`), at most 200 attributes per reference, DIMDEC at 16, error messages at 500 characters, the summary at 3000 |
| title-block pairing is labels x candidates (15 000 + 15 000 texts: 13 s then the memory limit) | cost | at most 200 labels and 5000 other texts are considered, with a warning |
| the entity cap was checked after the load (2 M points: 25 s) | cost | the records of the file are counted first (a regex over the bytes); far more than the cap is refused before ezdxf parses |
| dimension block text rebuilt for every dimension (3000 dimensions x 3000 texts: 12.6 s) | cost | cached per block |
| `input_file` hashed a 3 GB file before the size cap refused it | cost | `input_file(..., max_bytes=...)` refuses first, after the allow-list and existence checks |
| a directory flipped to a junction between the allow-list check and the child process's open leaked the outside file (found by the coordinator, 5 of 78 reads) | TOCTOU | see decision 5: the file is opened once, its real path is checked on the open file and the bytes are copied |
| the second cut of a long string counted only its own part ("3524 more" for 999 500; "28 more" for 996 000) | misleading | `reading.cut_text` reads an earlier note back, so every "[N more characters]" is relative to the original string (`tests/test_drawing_read_truncation.py`) |
| `f"{2.5:.0f}"` is "2" (half-even), AutoCAD shows "3" | minor | not changed; computed text can differ in exact-half cases, the block text (what a viewer draws) wins when present |

Checked and solid, from the same review: 15 of 15 forced timeouts with no leftover processes; the memory cap on a 1.5 GB flate bomb and a 3.4 M-vertex polyline; the allow-list against `..` in both slash styles, a junction, an 8.3 short name, `\\localhost\C$`, `\\?\`, a trailing space and a sibling directory with a shared prefix; block cycles, billion-laughs blocks, xref blocks, truncated and empty files, a PDF xref `/Prev` loop, a cyclic page tree, a form that draws itself; the two-line and three-point angles, ordinate and linear projection, DIMLFAC precedence, the unit table.

## Known gaps

* DWG is not read (S9). No OCR; PDF images are counted, not read. GD&T is not interpreted: a `TOLERANCE` frame's text comes back with its symbols as font letters.
* Dimensions inside block definitions (a dimension in a block that is inserted) are not reported, only those in model and paper space. `MULTILEADER` and `TOLERANCE` texts are listed; `HATCH` boundaries, viewport contents and `ARC_DIMENSION` are not read.
* Extents are of geometry points (arcs sampled every 15 degrees plus axis extremes, text at its insertion point, no clipping, no hatches, images or viewports). Layer lengths count LINE, ARC, CIRCLE, ELLIPSE, LWPOLYLINE and 2D POLYLINE only; splines contribute 0.
* The title block and the view count are guesses from positions, marked as such; an unusual layout can be paired wrongly, and a drawing whose views touch counts as one.
* Text in a pre-R2007 DXF without `$DWGCODEPAGE` is decoded as cp1252 (the library default), so CJK text in such a file shows as mojibake; the reported `encoding_source` says "no $DWGCODEPAGE".
* The memory cap exists on Windows and Linux; on macOS only the time limit applies, and the result says so.
* The allow-list is not a full sandbox: the allowed directories themselves are trusted, and `ENG_TOOLKIT_READ_DIRS` set to an empty string means "the workspace only" (changed from "unrestricted", which failed open; only an unset variable is unrestricted).
* Found by the coordinator, not changed: PDF font sizes are not multiplied by the text matrix (a scaled text shows its unscaled size); the default PDF line width is reported as 0; the extents include dimension lines and text positions, so they are larger than the part.

## How to verify independently

```bash
uv sync --inexact --no-install-project --extra cad --extra drawing          # not the whole project
uv run --no-sync python -m pytest tests/test_drawing_read.py tests/test_drawing_read_dxf.py tests/test_drawing_read_pdf.py tests/test_drawing_dimensions.py tests/test_drawing_titleblock.py tests/test_drawing_plane.py tests/test_isolated.py tests/test_extras_registry.py tests/test_engine_ownership.py -q
uv run --no-sync python -m pytest -q                                         # whole suite, about 3.5 minutes
uv run --no-sync python -m eng_toolkit doctor                                # exit 0
```

Then try your own files: any DXF from another CAD program, an inch drawing (check `units.name` and `value_mm`), a drawing with `DIMLFAC` set in the style table and one set per entity, a unit-less R12 file (expect `UNKNOWN`), truncated copies of a real file, a PDF from a CAD export (text layer) and one from `drawing_make_views` (none). With `ENG_TOOLKIT_READ_DIRS` set to one directory, a path outside it must give `input.path_not_allowed`.

## Tradeoffs accepted

* A 1.2 s start-up per call in exchange for a hard time and memory limit on untrusted parsers.
* A second copy of every input file in the workspace in exchange for tools that cannot be fooled by a path that changes after the check.
* Text in a drawing is not interpreted beyond title-block pairing: no OCR, no GD&T, no scans.
* The label-pair heuristic can pair a label with the wrong neighbour in an unusual title block, in exchange for naming every such field `inferred` with a confidence.
* A generic extents walk that ignores clipping and text extent, and says so in `data.extents_note` (with `detail="full"`).
* PDF vector length is the length of the straight segments plus Bezier curves flattened into 32 chords, and only stroked paths count.
* The gateway now lists twelve tools (the test cap went from 11 to 12): a reader is a tool of its own, and the design's own plan has seven for these two domains.

## Alternatives considered

* **DWG too.** Out of scope for this slice (S9, `dwg2dxf` as a separate process).
* **PDF text via pypdfium2.** Native crash risk on untrusted input; no path API. Kept as a test oracle only.
* **One `drawing_read` per format.** The model should not have to know which; the extension and a sniff of the first bytes decide.
* **In-process limits instead of a child process.** Rejected above: a parser cannot be interrupted from inside.

## Open questions and risks

* Should `ENG_TOOLKIT_READ_DIRS` default to something when `--transport http` is chosen? Today it is unset (unrestricted) and only documented and reported by the doctor, because the project owner decides the HTTP posture.
* Windows Job Objects can fail to nest inside a parent job on old Windows; the child then runs without the memory cap and says so in `data.limits.memory_cap_applied`.
* Many tool calls in parallel each start a child with up to 2048 MB: there is no semaphore (the design's risk list names one for OCCT and HLR too).
