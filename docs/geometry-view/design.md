# geometry_view

`geometry_view` writes a solid 3D CAD file (`.step` `.stp` `.iges` `.igs` `.brep`, read in millimetres) as a **coloured GLB**
for web, AR and CAD viewers (the text-to-cad viewer among them), with colours that carry engineering meaning, and a
`view.json` table that links every face to its colour and to the DFM findings that name it. It is the fifth tool of the
`geometry` domain (5 of 6).

`geometry_convert` already writes a plain, single-colour GLB. `geometry_view` keeps its conventions (metres, +Y up, the
`(x, y, z) mm -> (x, z, -y) / 1000 m` conversion, read-back of every reported number from the returned bytes, a loss report)
and adds per-face colours and face identity.

## Parameters

| Parameter | Default | Meaning |
| --- | --- | --- |
| `model_path` | (required) | the solid model; `.stl` is refused (`input.unsupported_format`), as is anything that is not closed solids only (see "Closed solids only") |
| `color_by` | `face_kind` | `none`, `face_kind`, `dfm` or `highlight`; anything else is `input.invalid` at `/color_by` |
| `highlight` | `""` | `color_by="highlight"` only: face ids `F<n>` separated by commas or blanks (`F3,F7`); required there, refused with any other `color_by` |
| `process`, `tool_axis`, `limits` | `cnc_milling`, `+z`, `""` | `color_by="dfm"` only, the same values and JSON semantics as `geometry_check_dfm`; with another `color_by` they are validated and a warning says they were not used |
| `max_seconds` | 120 | wall-clock budget, 1 to 600 s, `dfm.Deadline` as in `geometry_check_dfm`, checked between steps (read, the triangle estimate, inventory, rules, before every face of the meshing, every edge, write); the faces are meshed one at a time, so the budget is honoured within one face's meshing time, but a running OpenCASCADE call (one face) cannot be interrupted. It applies to every `color_by`, not only `dfm`, because reading, the inventory and the meshing are OpenCASCADE calls too |
| `mesh_deflection` | `0.01 mm` | chord error, 0.001 mm to 100 mm (`geometry_convert`'s parser and range), angular limit 0.5 rad |
| `name` | `view` | stem of the GLB and name of its node: `[A-Za-z0-9_-]{1,64}`; empty = the input file's stem (`geometry_convert`'s rule) |
| `show_edges` | `true` | add the B-rep edges as one `GL_LINES` primitive |

Every refusal carries `details.param` as a JSON pointer (`/model_path`, `/highlight`, `/limits/min_wall`, ...): the tool
wraps its compute and rewrites the parameter names that the shared parsers report (`limits.min_wall` becomes
`/limits/min_wall`); a refusal about the file (not a solid, budget aside) points at `/model_path`, a time-budget refusal
at `/max_seconds`.

## Colours

`baseColorFactor` is linear RGB, so the sRGB colours below are converted with the sRGB transfer function; the legend keeps
both (`color` is sRGB hex, `base_color_factor` is what the material holds). Every material has `metallicFactor` 0.1,
`roughnessFactor` 0.8 and `doubleSided` false.

| `color_by` | Group | sRGB | Meaning |
| --- | --- | --- | --- |
| `none` | `neutral` | `#B4B8BE` | every face, no coding |
| `face_kind` | `plane` | `#56B4E9` | planar face |
| | `cylinder` | `#E69F00` | cylindrical face |
| | `cone` | `#009E73` | conical face |
| | `sphere` | `#CC79A7` | spherical face |
| | `torus` | `#F0E442` | toroidal face |
| | `bspline` | `#0072B2` | B-spline or Bezier surface |
| | `other` | `#7F7F7F` | any other surface (revolution, extrusion, offset) |
| `dfm` | `dfm_error` | `#D62728` | worst finding on the face has severity `error` |
| | `dfm_warn` | `#FF7F0E` | worst finding has severity `warn` |
| | `dfm_info` | `#1F77B4` | worst finding has severity `info` |
| | `neutral` | `#B4B8BE` | no finding names the face |
| `highlight` | `highlight` | `#E6007E` | a face listed in `highlight` |
| | `neutral` | `#B4B8BE` | not listed |
| (all, `show_edges`) | `edges` | `#1A1A1A` | the B-rep edges as lines |

The face-kind colours are the Okabe-Ito palette (distinguishable with the common colour-vision deficiencies). `bspline` is
its own group because `geometry_measure` reports it as a kind; the six kinds named in the brief plus this one cover every
kind `geometry_measure` can report.

### DFM colouring

`color_by="dfm"` calls `dfm.parse_limits`, `dfm.run` and `dfm.Deadline` of `geometry_check_dfm` unchanged: no rule,
limit or severity lives in `view.py`. The rules run on the freshly read shape, before the view's own meshing, so they see
exactly what `geometry_check_dfm` sees (the access test samples a mesh of its own); the view's meshing then removes that
triangulation (`BRepTools.Clean`) and meshes at `mesh_deflection`, so the GLB is the same whatever `color_by` is.
Findings are ordered as `geometry_check_dfm` orders them (severity, then rule) and numbered `D1`, `D2`, ...: `D<n>` is
the n-th finding of `geometry_check_dfm` with the same inputs. Each face takes the colour of the worst severity among the
findings whose `face_ids` contain it. Colours follow **severity only**: severity and exactness are independent in
`geometry_check_dfm` (`sheet.thickness_uniform`, a sharp-corner `sheet.min_bend_radius` and a missing `sheet.thickness`
are errors from heuristic checks), so the legend says "error / warn / info finding" and each finding's `check`
(`exact` or `heuristic`) is in `data.findings_*` and in `view.json`.

Findings with no face are listed in `data.findings_not_coloured` and in `view.json` with `"coloured": false`, with a
warning. This happens: `cad_fixtures.probe_part()` checked as `sheet_metal` gives `sheet.thickness` as an error with no
face and no edge (no uniform sheet thickness found) (tested).

A finding may concern more faces than its `face_ids` list: `sheet.thickness_uniform` lists the first 50 and gives the
full count in `extra.faces_total` (the full list is not kept anywhere in `dfm.py`, and `geometry_check_dfm`'s own output
is unchanged). The view colours the 50 it is given and discloses the rest: every finding row carries `faces_named`,
`faces_total` and `faces_not_coloured`, `data.findings_partly_coloured` lists the findings with uncoloured faces, and a
warning names each one. Example (tested): a 200 x 200 x 2 mm plate with an 8 x 8 grid of 3 x 3 x 1 mm bosses as sheet
metal: 320 faces off the sheet thickness, 50 red, 270 not coloured.

`highlighted_face_count` is the number of faces singled out by the colouring: the faces named by at least one finding
(`dfm`), the listed faces (`highlight`), 0 for `none` and `face_kind`.

## The GLB

One scene, one node (named `name`), one mesh. The mesh holds one `TRIANGLES` primitive per colour group that has at
least one face, in legend order, each with its own material, `POSITION` and `NORMAL` (float32, VEC3) and `UINT32`
indices, then, with `show_edges`, one `LINES` primitive (positions and indices only). Every accessor has `min` and `max`;
every buffer view starts on a 4-byte boundary and is exactly as long as its accessor; the JSON chunk is padded with
spaces and the binary chunk with zeros. The bytes depend only on the input bytes and parameters (no time stamp, sorted
JSON keys): the same call twice gives the same file.

Vertices are not shared between faces: each face keeps its own nodes, so per-face triangle ranges are exact and the
shading has a crease at every edge. Vertex normals are the area-weighted mean of the triangle normals of the face around
the vertex (smooth inside a curved face, sharp between faces), converted to glTF axes as `(x, z, -y)` and normalised.

### Extras schema

Primitive `extras` (triangles):

```json
{"group": "cylinder", "color": "#E69F00",
 "faces": [{"id": "F3", "first_triangle": 0, "triangle_count": 48}, {"id": "F4", "first_triangle": 48, "triangle_count": 48}]}
```

Triangle `first_triangle .. first_triangle + triangle_count - 1` of the primitive (index triples `3k .. 3k + 2` of its
indices accessor) belong to the face; the ranges of a primitive follow each other and cover it.

Primitive `extras` (lines):

```json
{"group": "edges", "color": "#1A1A1A", "edges": [{"id": "E1", "first_point": 0, "point_count": 2}]}
```

Points `first_point .. first_point + point_count - 1` of the line primitive's `POSITION` accessor are the polyline of edge
`E<n>`; the indices join consecutive points of one polyline (`point_count - 1` segments). The accessor's vertex count is
the total number of polyline points.

Node `extras`:

```json
{"tool": "geometry_view", "tool_version": "...", "color_by": "dfm",
 "legend": [{"group": "dfm_error", "color": "#D62728", "base_color_factor": [0.672443, 0.020289, 0.021219, 1.0], "meaning": "...", "faces": 4}, "..."],
 "units": {"model_unit": "millimetre", "unit": "metre", "up_axis": "+Y", "conversion": "model (x, y, z) mm -> glTF (x, z, -y) / 1000 m"},
 "model_bbox_mm": {"min": [0, 0, 0], "max": [60, 40, 20]},
 "face_ids": "F<n> as in geometry_measure ...", "face_ids_valid_for": "<sha256 of the input>", "mesh_deflection_mm": 0.01}
```

The legend lists every group of the chosen `color_by` (also those with 0 faces) plus `edges`; the bounding box is the
exact B-rep box in model coordinates (millimetres, +Z up), so a viewer can show model coordinates without inverting the
axis conversion.

### view.json

`{tool, tool_version, glb, glb_sha256, face_ids_valid_for, color_by, legend, units, model_bbox_mm, faces, findings}`.
`faces` has one row per face in id order: `id`, `kind`, `group`, `color`, `finding_ids` (`D<n>`), `triangle_count`,
`area_mm2` (exact, from the inventory) and `area_mesh_mm2` (sum of the face's triangles, read back from the GLB bytes).
`findings` lists every DFM finding with its id, rule, severity, check, message, measured value and limit (mm or ratio),
face and edge ids, location, `coloured`, `faces_named`, `faces_total` and `faces_not_coloured`.

## Face-id correspondence

Faces are numbered by the 1-based `TopExp_Explorer` order, the numbering of `brep.inventory` and so of
`geometry_measure` and `geometry_check_dfm`; edges by the 1-based index of `TopExp.MapShapesAndAncestors`, the DFM edge
ids. Both are valid only for the same file bytes and OpenCASCADE version (`face_ids_valid_for` is the input's sha256).
The new helper `brep.tessellate_faces` (module `brep/_view.py`, additive) walks the faces in that same order and records
each face's vertex and triangle range, then takes each edge's polyline from the tessellation of one of its faces
(`BRep_Tool.PolygonOnTriangulation`), so the lines lie exactly on the triangle edges. Seam edges (a periodic face closing
on itself, such as the line along a cylinder) and degenerate edges (a sphere's pole) are not drawn and are counted in
`data.edges`; drawn + seams + degenerate = the edge count of `geometry_measure`.

The test suite checks the correspondence through the gateway: for the probe part it calls `geometry_measure` and
`geometry_view` on the same file and compares, for every face id, the kind (from the colour group) and the area (summed
from the GLB triangles by an independent reader) within the tessellation bound.

## Read-back

After writing, the tool parses its own GLB from the artifact's bytes and reports from that: `triangle_count`, triangles
per group, triangles and mesh area per face, edge points and edges, and the union of the triangle primitives' `POSITION`
`min`/`max` (`data.output.glb.accessor_min_m/max_m`). A difference from what was meant to be written is
`geometry.readback_mismatch`. The tests read the file a second time with `tests/view_readers.py` (plain `struct`/`json`).

## Contract

Primary value `triangle_count`; values `face_count`, `highlighted_face_count`, `finding_count` (all dimensionless).
`data` holds the legend, the groups (faces, triangles, the first 200 face ids each), the highlighted faces, the coloured
and not-coloured findings (200 each at most; `view.json` holds all), `findings_partly_coloured`, the edge counts, input facts, output facts (file,
sha256, deflection, exact and mesh area, float32 error bound, accessor bounds) and `losses`:

* `units`: the STEP's declared unit (inch etc.) was converted to millimetres on reading; BREP has no unit; IGES converted.
* `names_colours_structure`: names, the CAD system's colours and materials, layers, PMI and the assembly tree are not read;
  the GLB is one flat mesh and its colours are this tool's coding.
* `exact_surfaces_to_triangles`: chord error up to `mesh_deflection`, angle 0.5 rad; face identity kept.
* `normals`: averaged triangle normals per face, not exact surface normals.
* `coordinate_precision`: float32.
* `glb_axes_and_unit`, `glb_content`, `face_ids`, `edges` (seams and degenerate edges not drawn).

## Caps and refusals

| Case | Family | Pointer |
| --- | --- | --- |
| estimated triangles (before meshing) above 600,000 | `input.out_of_range`; states the estimate, the cap, the memory (about 4 GB) and a coarser `mesh_deflection` that fits (`details.suggested_mesh_deflection_mm`) | `/mesh_deflection` |
| the running count after any face above 600,000 (second line of defence) | `input.too_large`, same message with "reached" | `/mesh_deflection` |
| model diagonal / deflection above 5e6 (meshing time guard of `geometry_convert`) | `input.out_of_range` | `/mesh_deflection` |
| more than 2500 faces with `dfm` (`dfm.MAX_FACES`), more than 20000 otherwise | `input.too_large` | `/model_path` |
| no closed solid with positive volume | `geometry.not_a_solid` (the refusal of `geometry_check_dfm`) | `/model_path` |
| a solid with an open shell, or faces outside every solid | `geometry.not_a_solid` with `open_shells`, `free_edges`, `faces_outside_solids` | `/model_path` |
| a limit value that overflows (a 400-digit number) | `input.out_of_range` (or the parser's own family) | `/limits/<key>` |
| `.stl` | `input.unsupported_format` | `/model_path` |
| coordinates beyond 1 km or not finite | `input.out_of_range` | `/model_path` |
| face id above the face count | `geometry.unknown_entity` (lists the unknown ids and the face count) | `/highlight` |
| malformed id, ids with another `color_by`, no ids with `highlight` | `input.invalid` / `input.missing` | `/highlight` |
| `process`, `tool_axis`, `color_by`, `mesh_deflection`, `name` over 64 characters; `highlight`, `limits` over 4096; `model_path` over 1024 | `input.invalid` (the long text is not echoed) | the parameter |
| budget expired | `geometry.time_budget_exceeded` | `/max_seconds` |

Inputs over 100 MB are refused by `JobHandle.input_file` as in `geometry_convert`.

Measured on this machine: a plate with 400 holes (406 faces) at 0.01 mm gives 52,812 triangles and a 2.4 MB GLB in
about 2.7 s (`face_kind`) and 3.9 s (`dfm`).

## Memory and time

OpenCASCADE's mesher (`BRepMesh_IncrementalMesh`) holds about 6.6 kB per triangle while it works (the review measured a
sphere of radius 60 mm at 0.001 mm: 603,802 triangles, 4.1 GB, 26 s). Three guards bound it:

1. **Estimate before meshing** (`brep.estimate_triangles`): per face, one triangle per boundary segment (each edge cut into
   chords spanning `min(0.5 rad, 2 acos(1 - d k))` of its curvature `k`; a straight edge is one segment), plus the
   interior: a plane is its boundary polygon (n - 2); a cylinder is one strip, two triangles per circumferential step; a
   sphere or torus is `6.6 x sum(area / (h1 h2))` over a 5 x 5 grid of UV samples, `h = step / k` in each principal
   direction; a cone, B-spline, surface of revolution or other surface is meshed isotropically, `1.2` (cone), `2.5`
   (revolution) or `5.0` (B-spline and others) `x sum(area / h^2)` with the larger curvature. It costs about 0.5 ms per curved face (12 ms for a 406-face plate). Above 600,000 the call is
   refused before any meshing, with a deflection that should fit (the deflection scaled by 1.2 x estimate / cap; triangle
   counts fall at least as fast as 1 / deflection).
2. **Cap of 600,000 triangles** (about 4 GB), lowered from the 2,000,000 that `geometry_convert` keeps for STL input.
3. **Face-by-face meshing**: each face is meshed by its own serial `BRepMesh_IncrementalMesh` call (edges shared by two
   faces are discretised once and reused, so the faces meet without cracks; the triangle counts were identical to meshing
   the whole shape on every calibration model, and the welded result stays closed). The time budget is checked before every
   face and the triangle count after every face, so both stop within one face's meshing time. One face is one OpenCASCADE
   call: a single huge face (a fine sphere) cannot be interrupted, which is why the estimate is the main guard. The
   price is OpenCASCADE's parallel meshing: on the 2,506-face plate (50 x 50 holes) at 0.01 mm, meshing takes 22 s face
   by face against 12 s for the whole shape in parallel (serial whole-shape: 22 s; the slowest single face, the plate's
   top with 2,500 holes, 10.8 s). The whole call there (STEP read 16 s, meshing, edges) took 48 s, 330,012 triangles,
   with a peak working set of 2.4 GB for the process (0.65 GB before the call); the review measured 4.5 GB before.

Calibration (actual triangles from `tessellate_faces` / estimate; 4 deflections, 1, 0.1, 0.01 and 0.001 mm):

| Model | 1 mm | 0.1 mm | 0.01 mm | 0.001 mm | triangles at 0.001 mm |
| --- | --- | --- | --- | --- | --- |
| sphere r 1 / r 10 / r 60 | 0.88 / 0.88 / 0.96 | 0.88 / 0.92 / 0.95 | 0.92 / 0.97 / 0.96 | 0.97 / 0.97 / 0.97 | 10,108 / 100,520 / 603,802 |
| cylinder r 5 h 100 / r 50 h 10 / r 0.5 h 200 | 1.32 / 1.06 / 1.32 | 1.06 / 0.94 / 1.32 | 0.94 / 0.94 / 1.06 | 0.94 / 0.94 / 0.94 | 888 / 2,808 / 280 |
| torus 50/5 | 1.25 | 0.92 | 0.81 | 0.81 | 328,702 |
| torus 20/15 | 1.70 | 1.05 | 1.06 | 1.07 | 452,760 |
| cone r 20 to 0 h 30 / r 20 to 5 h 30 | 0.60 / 1.23 | 1.08 / 1.05 | 0.75 / 0.77 | 0.53 / 0.50 | 15,379 / 14,562 |
| loft circle to ellipse (B-spline) | 0.17 | 0.92 | 1.03 | 1.06 | 189,442 |
| loft of three sections (B-spline) | 0.34 | 1.02 | 1.12 | 1.21 | 298,926 |
| ellipse revolved (surface of revolution) | 0.36 | 0.86 | 0.96 | 1.00 | 392,170 |
| box with every edge R 5 | 0.95 | 0.93 | 0.92 | 0.97 | 53,316 |
| probe part, clean part, bent sheet | 1.18, 1.17, 1.06 | 1.08, 1.17, 1.06 | 0.96, 0.91, 0.91 | 0.95, 0.93, 0.93 | 1,484, 1,520, 420 |
| box, 20-fin comb | 1.00, 1.00 | 1.00, 1.00 | 1.00, 1.00 | 1.00, 1.00 | 12, 332 |
| plate with 100 holes, plate with 64 bosses | 1.35, 1.14 | 1.35, 1.14 | 0.94, 1.14 | 0.94, 1.14 | 40,412, 1,036 |

The estimate never under-estimates by more than 1.70 x (a coarse torus, 1,352 triangles); at the deflections where the
cap matters (0.01 mm and finer) the worst under-estimate is 1.21 x (the three-section loft). It over-estimates up to
about 6 x on coarse free-form faces with few triangles and 2 x on a fine cone, which can only refuse early near the
cap. The surface-of-revolution factor (2.5) rests on one example; other kinds (extrusions, offsets, Bezier) use the
B-spline factor without a calibration model of their own. The meshing times behind the table: the r 60 sphere at 0.001 mm took 26.6 s and the 20/15
torus 56 s, both single faces (the estimate refuses the sphere; the torus, at 425,000 estimated, is below the cap and is
meshed, which is the price of a 600,000 cap with one-face granularity).

`geometry_convert` uses the same estimate before its B-rep tessellation (STEP/IGES/BREP to STL or GLB) with the same
600,000 cap (`MAX_TESSELLATION_TRIANGLES`, `input.out_of_range` before meshing, `input.too_large` after); it still meshes
the whole shape in one call, so its time is bounded only by the estimate. Its 2,000,000 cap for STL input (pure NumPy,
about 100 bytes per triangle) is unchanged.

## Closed solids only

`mass_properties` reports a volume for any shape holding a solid with positive volume, which also accepts a solid
whose shell is open (a box with five faces, saved as BREP, has a "volume") and a compound of a solid with loose faces
beside it. `brep.closure` checks every shell of every solid for free edges (an edge used by one face of the shell; seams
and degenerate edges excepted) and counts the faces that lie in no solid. Either refuses the file with
`geometry.not_a_solid`. `geometry_measure` and `geometry_check_dfm` do not run this check and share the gap (out of scope
here).

## Review fixes (before / after)

| # | Issue | Before | After |
| --- | --- | --- | --- |
| 1 | memory and time before meshing | sphere r 60 mm at 0.001 mm: 603,802 triangles, 4.1 GB, 29.8 s with `max_seconds=5`; r 300 mm: over 23 GB and 10 min | both refused before meshing in well under a second (`input.out_of_range` at `/mesh_deflection`, estimate and a fitting deflection); cap 600,000; budget and count checked per face; `geometry_convert` refuses the same sphere before meshing |
| 2 | DFM legend said error = exact, warn = heuristic | `sheet.thickness_uniform` (error, heuristic) shown as "exact rule violated" | legend says severity only; `check` per finding in `data` and `view.json` |
| 3 | faces beyond a finding's 50 listed ids | 50 red, 270 silently neutral | same colours, plus `faces_named`/`faces_total`/`faces_not_coloured`, `data.findings_partly_coloured` and a warning |
| 4 | overflowing limit blamed on `/model_path` | `{"min_wall": "<400 nines> mm"}` -> `/model_path` | `/limits/min_wall` (each key parsed on its own first) |
| 5 | doc claimed no rule gives a faceless finding | false (probe part as sheet metal) | doc corrected, case tested |
| 6 | open shells and loose faces accepted | a box plus a loose face, and an open 5-face shell as a solid, were coloured | refused with `geometry.not_a_solid`; a colour group with no triangles writes no primitive (no empty accessor) |

## Not done

* No assemblies, product names, CAD colours, layers or materials from the STEP: the input is read as one flat shape.
* No textures, UV coordinates, vertex colours, animation, morph targets, scene hierarchy or instancing.
* No FEM or other field results (stress, temperature) as colours; only the four codings above.
* No mesh input (`.stl`): a mesh has no faces to identify.
* No `KHR_materials_unlit` for the edge lines: they use a dark standard material, which viewers light like any other.
* The GLB was checked by the toolkit's own read-back and an independent `struct`/`json` reader, not by the Khronos glTF
  validator (not a dependency) and not by opening it in a viewer in the test suite.
