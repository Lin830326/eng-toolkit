---
name: eng-drawing
description: Use FIRST for a dimensioned engineering drawing (A4 to A0 sheet, PDF, PNG, DXF) of a STEP, IGES or BREP part with views, overall sizes, hole callouts and a hole list, and to read or convert a DXF or PDF drawing, with drawing_make_views, drawing_read and drawing_convert.
---

# Engineering drawing workflow (eng-toolkit)

Load `eng-toolkit` first for unit rules and the report format. For a drawing of a STEP part use `drawing_make_views` before any other drawing skill or a script: it runs in the toolkit, hidden lines are removed exactly, and no `uvx` or shell is needed. The sheet is **not a complete manufacturing drawing**: no tolerances, centre lines, counterbores or countersinks, title block says UNREVIEWED.

## 1. Pin down the problem (one short list)

Sheet (A3 landscape?), views (top/front/right, iso if room), projection (default third angle; first angle for ISO-E users), what must be readable (overall size, hole positions, which holes), file type (PDF for print, PNG to look at or send, DXF to edit), title, part number, material, general tolerance text. "Holes" usually means the mounting holes: say which groups the model has before drawing.

## 2. Tool choice

| Need | Tool |
|---|---|
| Sheet with views, dimensions, hole callouts | `drawing_make_views` |
| Hole list with diameters and positions | `data.callouts[].entry_points_mm` of that result, or `geometry_measure` (`data.holes`) |
| What is in a DXF or PDF (texts, dimensions, layers, title block, units) | `drawing_read` |
| DXF to PDF, SVG, PNG, or another DXF version | `drawing_convert` |
| Build the part first, with fit and thread text in the callouts | `geometry_make_part`, then pass its `features.json` as `features_path` |
| A picture of the 3D part (coloured faces, DFM findings; not a drawing) | `geometry_view`: it writes a PNG (iso plus front, top, right views, legend, size in mm) next to a GLB, see `eng-3d-view` |

## 3. Inputs that work (verified)

Raspberry Pi Pico board (KiCad's `RaspberryPi_Pico.step`, in `<KiCad>/share/kicad/3dmodels/Module.3dshapes/`), four views on A3, PDF and PNG:

`drawing_make_views model_path="<path>/RaspberryPi_Pico.step" views="top,front,right,iso" sheet="A3" formats="pdf,png" title="Raspberry Pi Pico"`

gives scale 2:1, 4 views, 11 dimensions (21 x 52.3 x 3.7), callouts `4× Ø2.1 THRU` and `43× Ø1 THRU`. Output files `drawing.dxf`, `drawing.pdf`, `drawing.png` are in the job folder (`artifacts`).

- `sheet` auto or A4..A0 (landscape); `scale` auto or an ISO scale from 1:100 to 10:1; `dimensions` auto, overall or none; `notes` up to 6 separated by `;`.
- `features_path` makes a callout such as `2x D10 H7 THRU`; it only applies when it belongs to this exact STEP file (hash), else a warning and no fit text.
- Formats: DXF is always written and holds the text. PDF, SVG and PNG draw text as outlines (no text layer), so `drawing_read` of a PDF returns vectors only: read the DXF instead. PNG needs the `png` extra (otherwise say so).

## 4. Run, then verify

1. Read `warnings` and `data.layout_warnings` before showing the sheet. Look at the PNG (Read it) and check readability.
2. **Known layout limits**:
   - **No y ordinates** when the holes lie at more than 16 distinct y values (the Pico has 23): the sheet then cannot show the 47 mm hole spacing. Give hole centres from `data.callouts[].entry_points_mm` as a table in your answer (mounting holes: x = +-5.7, y = +-23.5, so spacing 11.4 x 47 mm) and say the sheet does not show them.
   - **Overlapping ordinate texts** (warning "x ordinates 0 and 1.61 ... overlap"): raise the scale or the sheet. A3 allows 2:1; `scale=5:1` on A3 is refused with `drawing.layout_failed` and the message names the fit (A2 at 5:1 has no overlap). Never drop to a smaller sheet silently.
   - The part may fill only part of the sheet; the numbers on the sheet use a decimal comma in dimensions and a point in callouts (`52,3` and `4× Ø2.1`): tell the user if that matters.
3. **Hand check** the overall size against `geometry_measure` size_x/size_y/size_z, and the hole count against `data.holes`. Castellated half-holes on a board edge are not holes in the callout. A bounding box includes protruding parts (a connector can add 1 to 2 mm to the board length): say so when the size differs from a datasheet.
4. If `ok=false` with `drawing.layout_failed`, use the sheet or scale the message names.

## 5. Coverage limits (outside the toolkit: say so)

No section or detail views, no tolerances or GD&T frames, no surface finish, thread symbols, centre lines, counterbore or countersink callouts, bills of material, 3D PDF or weldments. DWG needs the user's LibreDWG. A drawing is a picture of this model: model errors are not found. For tolerances use `eng-tolerancing`.

## 6. Report

Result: file paths (PDF, PNG, DXF), sheet, scale, views. Hole list as a table (id group, diameter, count, centres). Checks: overall size against the model, warnings, what is not readable on the sheet and where the number is instead. Limits: unreviewed, not a manufacturing drawing, items above. Reply in the user's language.
