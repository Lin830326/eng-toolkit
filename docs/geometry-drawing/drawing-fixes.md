# drawing: seven fixes after independent verification (branch feat/drawing-fixes)

Each fix has its own commit and its own regression test, written before the change and run to see it fail for the intended reason. Written with pstack's `architect` skill, scaled down as for [drawing-read.md](drawing-read.md): Phase A is reading the code and measuring OpenCASCADE and pdfminer on this machine, Phase B is "design it twice" per decision below, by one model; the multi-model arena was **not** run (these are point fixes, not a new shape). Implementation followed pstack's `tdd` skill. `poteto-mode` needs `bun`, which this machine does not have, so it was not used. Skipped in Claude Code: Cursor-only steps (Automations, Bugbot, cloud agents); none was needed.

## 1. IGES surface-only model: a seam line through the hole, and no "no closed solid" warning

**Measured.** A cylinder stored as a surface of revolution (IGES mode 0, "loose faces") projects with one extra 20 mm hidden line in the hole centre, and the fillet tangent edges are not recognised either (STEP: `tangent` 4 per view; IGES: 1). In the HLR compounds the seam is in `HCompound` (a sharp hidden edge) twice, once per direction, where the STEP cylinder has it once in `RgNLineHCompound`. Cause: the loose faces are not sewn, so the two halves of the seam are two unconnected edges, and even after sewing OCCT leaves the seam's regularity at C0 on one of its two face occurrences, so HLR does not call it a seam.

**Decision.** In `brep/_project.py`, a shape with no solid is sewn (`BRepBuilderAPI_Sewing`, 0.001 mm), `BRepLib.EncodeRegularity` runs with a 1e-4 rad tolerance (the default 1e-10 finds no tangent edge in IGES surfaces: measured 1 vs 4; 1e-6 already finds all four), and every edge that is closed on a face (a seam) is given continuity CN. Result: the right view's `dropped.seam` is 1 and the hidden lines are those of the STEP file of the same part (tests compare 40 mm and primitive counts 2, 0, 2).

* Alternative A: drop edges whose 3D curve coincides with a closed edge after projecting. Rejected: a second geometric matching pass that duplicates what OCCT already knows once the flag is set.
* Alternative B: recognise the surface as a cylinder (`ShapeAnalysis_CanonicalRecognition`) and rebuild the face. Rejected: more code, and it changes the geometry the drawing is drawn from.
* Alternative C: sew in `brep.read_solid` for every consumer. Rejected: `geometry_measure` has measured behaviour on surface-only IGES (no volume, a warning) that sewing would not change but also would not need; the fix stays where the problem shows.
* If sewing changes the face count, the unsewn shape is projected (sewing is a convenience).

**Warning.** `drawing_make_views` now says when the file holds no closed solid (the wording follows `geometry_measure`), for IGES with the IGES-specific hint, and says the faces were sewn and hidden lines depend on the surfaces being complete and closed.

**Not done.** A surface model that sewing cannot close still gets hidden lines from whatever faces exist; the warning says so.

## 2. U+3000 and U+00A0 in text

**Decision: fold every Unicode space separator (category Zs) to U+0020 and say so in a warning.** Reasons: many CAD fonts (Arial among them) have no glyph for U+3000 or U+00A0 and the viewer then draws a box or nothing; our own PDF backend cannot know the advance of a glyph the font lacks; U+0020 looks the same in every viewer and in `drawing_read` (which already joins whitespace). Alternative: keep U+3000 and draw it at the CJK font's advance. Rejected: depends on which CJK font the machine has, so the DXF and PDF would look different between machines for a character that carries no meaning beyond a gap. A full-width space becomes one narrower gap; the warning names the fields that changed.

Still rejected (and tested): tab, newline, other control characters (including U+0085, U+000B), U+2028 and U+2029, zero-width characters (U+200B, U+FEFF), DEL.

## 3. `%%c`, `%%d`, `%%p` in user text

**Measured.** ezdxf's PDF renderer decodes the codes too: a title `%%c10` gives a PDF byte-identical to the title `Ø10`. So the literal had to be escaped for the DXF, which both our PDF and every viewer read.

**Decision.** Every run of two or more `%` is written with each `%` as `%%%` (`%%c` becomes `%%%%%%c`); a lone `%` is left alone. In AutoCAD's rule `%%` starts a code (`c d p u o k`, `%%nnn`) and `%%%` is a literal percent, parsed left to right, so `%%%` `%%%` `c` displays as `%%c`. Escaping whole runs removes every ambiguity (`%%%u`, `x%%%%y`) and keeps ordinary text such as `50%` unchanged in the file. The function lives beside its inverse (`dimensions.escape_percent` / `unescape_percent`); a property test checks `unescape(escape(s)) == s` for edge cases. Alternative: escape every `%`. Rejected: `50%` would become `50%%%` in the raw file for no gain.

**Round trip that tells them apart.** `drawing_read` already turned a raw `%%c10` into `Ø10`; it turns our escaped literal into the typed `%%c10`. Tests: write a title with `%%c10 %%d %%p`, read the DXF back with `drawing_read`, expect the typed text and no `Ø°±`; a hand-written DXF holding both raw `%%c10` and `%%%%%%c10` reads back as `Ø10` and `%%c10`; the PDF of `%%c10` differs from the PDF of `Ø10`.

## 4. PDF font size

**The example in the brief is not valid content.** `BT /F1 1 Tf 12 0 0 12 72 600 Td ... ET` gives `Td` six operands; `Td` takes two (the six-number operator is `Tm`), so the scale is never applied by pdfminer or by a viewer. With `Tm`, with `cm`, and with both together (`Tf 3`, `Tm 2`, `cm 2`) the reader already reports 12 (tests now lock all three).

**The defect in the same place.** For turned text pdfminer's `size` is the height of the axis-aligned glyph box, which is the advance of the glyph, not the font size: a 12 pt line turned 90 degrees reported 8.66, 8.0, 7.33 per letter. `pdf_read._font_size` derives the size from the glyph box and the matrix: with advance `adv` and font size `fs` the box is `|a|adv + |c|fs` wide and `|b|adv + |d|fs` high, so `fs` follows from the dominant side, and the drawn size is `fs` times `hypot(c, d)`. Tests: 0, 30, 90 and 270 degrees, non-uniform scale (6), horizontal scaling `Tz` (unchanged).

## 5. PDF default line width

pdfminer starts a page at `linewidth = 0`; the specification's initial value is 1 (PDF 32000-1 table 52). A small subclass of the page interpreter sets 1 in `init_state` (every page and form). An explicit `0 w` stays 0 (a hairline is meaningful). A width set on page 1 does not leak to page 2 (tested). Not done: the width is not multiplied by the CTM scale (the number is the operand of `w`, as before).

## 6. Extents: geometry and with_annotations

`extents.<space>` is now `{geometry, with_annotations, complete, method}`; `geometry` and `with_annotations` each hold `min`, `max`, `size`, `size_mm`. `geometry` leaves out TEXT, MTEXT, DIMENSION, MULTILEADER, TOLERANCE, LEADER and attributes, also inside blocks (a second block walk with `geometry_only`, because a block's hull is computed once per block and cannot be split afterwards; it reports nothing of its own and its counters are reset so warnings do not double). `with_annotations` is the old box. The summary and `values.extent_width/height` use `geometry`; annotation-only content has `geometry: null` and no extent values. The old top-level `min/max/size/size_mm` are removed: a key that keeps its name and changes meaning is worse than a key that goes away. Consumers of `extents.model.size_mm` must read `extents.model.geometry.size_mm`.

## 7. `ENG_TOOLKIT_READ_DIRS` set but empty

Unset stays unrestricted. Set but empty (empty string, blanks, only separators) is a list with no directory: only the workspace is readable, so blanking the list can never open more files. `read_dirs()` returns `[]` instead of `None`; the two checks in `JobHandle.input_file` already test `is not None`. `toolkit_doctor` reports `restricted: true`, `directories: 0` and a note; README and [drawing-read.md](drawing-read.md) are updated. Tests: the three empty spellings, the shared file check, workspace read-back, the doctor.

## How to verify independently

```bash
uv sync --inexact --no-install-project --extra cad --extra drawing
uv run --no-sync python -m pytest -q
uv run --no-sync python -m eng_toolkit doctor
```

Hand-written inputs: `tests/raw_pdf.py`, `tests/raw_dxf.py`; the IGES file is written by OCCT in mode 0 (`tests/cad_fixtures.write_iges_faces`). Every expected number in the new tests is worked out in the test (12 = 3 x 2 x 2; 100 x 60 and 100 x 85; seam count 1).
