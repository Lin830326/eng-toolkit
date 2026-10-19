# geometry_check_dfm (slice S7)

`geometry_check_dfm` loads a solid (STEP, IGES, BREP; the same `JobHandle.input_file` open-once path as `geometry_measure`) and runs manufacturability checks for one process. It reports **findings**, never a verdict of "manufacturable". It calls no model and executes nothing from the file.

Supported processes: `cnc_milling` and `sheet_metal`. Any other value is refused with `geometry.unsupported_process`; the message says whether the name is recognised-but-unsupported (`turning`, `injection_molding`, `additive`, `casting`, `cnc_3axis`, `cnc_5axis`, `laser_cutting`) or unknown. Nothing is guessed for them.

## Deviations from `docs/geometry-drawing/design.md`

| design.md said | This slice does | Why |
|---|---|---|
| processes `cnc_3axis`, `injection_molding`, `additive` | `cnc_milling` and `sheet_metal` only | owner's task for S7: do two processes well, refuse the rest in words |
| "default limit table stays empty until each value has a source" | a small table of **documented defaults** (below), reported in every result with `source: default` or `caller`, and a warning when any default was used | owner's task for S7 asks for sane documented defaults. The defaults are this toolkit's rules of thumb. They are not from a standard and the tool never says a part conforms to one. The open question 4 in design.md (which sources are acceptable) is still open |
| measurements always | measurements are in `data.measurements`; findings only where a limit is violated (plus `info` facts) | keeps the finding list short enough to read |
| geometry code only under `domains/geometry/` | one new private module `eng_toolkit/brep/_dfm.py` plus its exports in `eng_toolkit/brep/__init__.py` (20 added lines) | `tests/test_engine_ownership.py` forbids OCP outside `eng_toolkit/brep`, and edge adjacency and face pairing need OCP. The rules themselves are in `domains/geometry/dfm.py` and contain no OCP |

## Files

| file | contents |
|---|---|
| `eng_toolkit/brep/_dfm.py` | measuring only: `edge_facts`, `cylinder_facts`, `facing_pairs`, `face_access` |
| `eng_toolkit/domains/geometry/dfm.py` | limits, rules, `Finding`, what was not performed |
| `eng_toolkit/domains/geometry/tools.py` | the tool wrapper `check_dfm` (contract shaping, artifact `dfm_findings.json`) |
| `tests/dfm_fixtures.py`, `tests/test_geometry_check_dfm.py` | fixtures built with OCP from the dimensions the tests expect; 51 tests |

## Facts measured by `brep/_dfm.py`

* **Edge facts** (exact). For every edge shared by two different faces: the two face ids, line/circle/other, the edge midpoint and tangent, the length, and **convex / concave / smooth** with the material angle (90 for a box corner, 270 for a pocket corner, 180 for a tangent join). Convexity comes from the outward normals of both faces at the edge midpoint and the direction in which the first face's boundary runs along the edge: the second face's inward direction is `n2 x t`, and the edge is convex when that points behind the first face's tangent plane. Seams (one face on both sides), degenerate edges and edges of three or more faces are left out.
* **Cylinder facts** (exact). Radius, axis, axial ends, angular span, a point on the face half way round and along, whether the face is concave or convex.
* **Facing pairs** (gap exact, existence checked by rays). Two faces with material between them:
  * *planes*: opposite outward normals, each on the other's material side (`(origin_b - origin_a) . n_a < 0`; the other sign is air, a slot, and is not a wall). The gap is the exact plane offset. Candidate pairs are pruned by axis-aligned bounding boxes. A pair is kept only if a sample point of one face (centre of one of its 12 largest mesh triangles) lies, projected along the normal, inside the other face (`BRepClass_FaceClassifier`) and a ray from just inside the material at that point meets the second face first, at `gap`, with nothing in between.
  * *cylinders*: a concave and a convex cylinder face on one axis, same axial extent and span; gap = difference of radii; a radial ray from the axis must meet exactly those two surfaces first. This covers tube walls and sheet-metal bends.
* **Face access** (heuristic). For each planar face, one sample point (centre of the largest mesh triangle) moved 1e-5 of the part size out along the normal; each of the six axis directions with non-negative dot product with the normal counts as open if the ray from that point leaves the part without meeting any face.

## Rules

Limits are in `LIMIT_DEFAULTS` (`domains/geometry/dfm.py`). Lengths are strings with units in `limits`; ratios and counts are plain numbers. Comparisons: a measured value **equal** to the limit passes (to 1e-9 relative).

| rule | process | severity | check | what is measured | default limit |
|---|---|---|---|---|---|
| `cnc.internal_corner_radius` | cnc_milling | error | exact | concave straight edges parallel to `tool_axis` (radius 0); concave cylinder faces with their axis parallel to `tool_axis`, not part of a hole and not a full circle (their radius) | `tool_radius` 3 mm |
| `cnc.min_wall` | cnc_milling | error | exact gap, completeness limited (below) | facing pairs closer than `min_wall`, searched up to 5 x `min_wall` | `min_wall` 0.8 mm |
| `cnc.hole_depth_ratio` | cnc_milling | error | exact | detected round holes: cylindrical depth / diameter (drill-point cone and counterbore not counted) | `max_hole_depth_ratio` 10 |
| `cnc.min_hole_diameter` | cnc_milling | error | exact | detected round holes: diameter | `min_hole_diameter` 1 mm |
| `cnc.tool_access` | cnc_milling | warn | **heuristic** | number of the six axis directions that reach a planar face's sample point | `min_access_directions` 1 |
| `sheet.thickness` | sheet_metal | info (error if none) | exact gap; the choice of "the" thickness is heuristic | facing pairs up to `max_thickness`, grouped by gap to 1e-6 mm; the group covering the largest face area is the sheet | `max_thickness` 10 mm |
| `sheet.thickness_uniform` | sheet_metal | error | **heuristic** | faces that are neither in a pair of the sheet thickness nor a cut edge (a face touching two covered faces); lists them and any other pair thicknesses | none |
| `sheet.min_bend_radius` | sheet_metal | error | exact for a cylinder pair of the sheet thickness; **heuristic** for a sharp inside corner between two sheet faces (radius 0) | inner radius of the bend | `min_bend_radius_ratio` 1.0 x thickness |
| `sheet.hole_to_edge` | sheet_metal | error | exact | `BRepExtrema` distance from a hole's wall to the nearest edge or cutout face (a face of the sheet's edge, excluding hole walls), holes whose axis is parallel to a sheet face normal | `min_hole_edge_ratio` 1.5 x thickness |

Severity policy: `error` = an exact rule is violated, `warn` = a heuristic rule is violated, `info` = a fact with no limit. Exception: `sheet.thickness_uniform` and the "no sheet found" case are heuristic and reported as `error` because the rest of the sheet analysis depends on them; each carries `check: heuristic`.

Every finding carries: `rule`, `severity`, `check` (`exact`/`heuristic`), `message`, `measured` and `limit` (value and unit; `ratio` and `count` are unitless), `passes_when`, `face_ids`, `edge_ids`, `location_mm`, `basis` (why it is exact or heuristic) and `details`. `data.summary` counts by severity and by rule. `data.findings` lists up to `max_findings` (default 100); `dfm_findings.json` holds all of them.

## Refusals

* Not a solid (shell, loose faces, zero solids or zero volume): `geometry.not_a_solid`. A manufacturability check needs material between faces.
* Empty or unreadable file: `input.invalid` (same reader as `geometry_measure`). `.stl`: `input.unsupported_format`.
* More than 2500 faces: `input.too_large`.
* Unknown limit key, bare number for a length, negative or wrong-dimension value, bad JSON, a sheet-metal limit given to cnc_milling: `input.invalid` / `units.*`.
* Several solids: not refused. They are checked together as one body and a warning says so; a wall between two separate solids is not recognised.

## Verification

Fixtures are built with OCP from primitives whose dimensions are the numbers expected (`tests/dfm_fixtures.py`). Every value was derived by hand before the test was run. Tolerance is 1e-6 mm.

| case | construction | expected | result |
|---|---|---|---|
| thin wall | 60 x 40 x 20 block, pocket x 1..31 | wall 1.0 at x = 0.5, point inside the wall | equal |
| floor | same, z 0..5 | 5.0 | equal |
| cavity | 40 x 30 x 20 block, 36 x 26 x 16 cavity | six walls of 2.0, six unreachable faces | equal |
| tube | R10 outer, R7 inner | 3.0 at radius 8.5 | equal |
| air gap | 3 mm slot through a 10 mm plate | no wall of 3; smallest wall 10 | equal |
| sharp corners | 30 x 20 pocket | four corners, radius 0, at (10,10), (40,10), (40,30), (10,30), z 12.5 | equal |
| R0.5 corners | fillets on the pocket tool | 0.5, point (10 + d, 10 + d) with d = 0.5 (1 - cos 45 deg) = 0.1464466 | equal |
| R3 / R2.5 | pocket fillets | R3 passes against 3 mm; R2.5 fails with 2.5 | equal |
| hole | diameter 2, depth 30 | ratio 15 at (10, 10, 40); limit 15 passes, 14.99 fails | equal |
| clean part | R4 pocket, diameter 6 hole, thick walls | 0 errors, 0 warnings | equal |
| flat sheet | 2 mm plate, diameter 5 hole at x = 6 | thickness 2.0; hole to edge 6 - 2.5 = 3.5 | equal |
| bend | 2 mm sheet, inner radius 3, 90 degrees | thickness 2.0, inner radius 3.0 (outer 5.0, 90 deg) at (40 + 4 cos 45, ., 5 - 4 sin 45); 3 = 1.5 x 2 passes | equal |
| sharp L | no bend radius | radius 0 at (38, ., 2), heuristic | equal |
| stepped plate | 2 mm with a 3 mm boss | not uniform, other thickness 3.0 | equal |
| inch file | STEP written in inches, wall 1.27 mm = 0.05 in | 1.27 mm; 0.05 inch with `output_unit=inch` | equal |

Also tested: BREP input, two solids (warning, same wall), a planar shell (refused), an empty file, `.stl`, unknown and unsupported processes (`turning`, `injection_molding`, `additive`, `cnc_3axis`, `witchcraft`), ten bad `limits`, `max_findings` truncation, the face-count limit, a real MCP client through the gateway, and the edge facts (a box pocket has 8 concave edges at 270 deg and 8 tangent edges at 180 deg once filleted).

## Timing

Measured on the development machine (Windows 11), one call each, nothing else heavy running. "Inventory" is the existing `brep.inventory` (hole detection), which `geometry_measure` also runs and which dominates for perforated plates.

| model | faces | whole call | of which inventory |
|---|---|---|---|
| 12 x 12 hole grid | 150 | 2.2 s | |
| comb of 125 fins (planar faces) | 506 | 7.3 s | 0.2 s |
| 31 x 31 hole grid, `cnc_milling` | 967 | 60.7 s | 32.4 s |
| 31 x 31 hole grid, `sheet_metal` | 967 | 58.1 s | 25.7 s |
| comb of 250 fins, `cnc_milling` | 1006 | 22.4 s | 0.3 s |
| comb of 250 fins, `sheet_metal` | 1006 | 22.0 s | 0.3 s |
| comb of 500 fins, `cnc_milling` | 2006 | 166.8 s | 0.8 s |

Time grows faster than linearly (the ray tests and face classifications scale with the number of faces and with the number of wires on the big faces). There is no cancellation: a call that is slow stays slow. The hard cap is 2500 faces (`MAX_FACES`); times above 2000 faces are not measured. A model with more than 30 s of work should be mapped to `toolkit.job_status` when that exists (design.md, open risks).

## What is not done

The tool lists these in `data.not_performed` for the process.

cnc_milling: corner radius at the bottom of a pocket and pocket depth against tool diameter; undercuts, T-slots and tool length (only the one-sample reach test exists, `cnc.tool_access`); walls between faces that are not parallel planes or coaxial cylinders (cones, tapered or free-form walls); walls thicker than 5 x `min_wall` (not searched); threads, counterbores and countersinks (a counterbore is listed as a separate blind hole), drill-point cones; tolerance, surface finish, set-ups, material, tool deflection, cost.

sheet_metal: flat pattern and unfolding, bend allowance, K-factor, springback; hole-to-bend and bend-to-bend distance, bend relief, minimum flange length; minimum hole diameter and slot width against thickness; hems, jogs, lances, countersinks and other formed features (they appear as off-thickness faces); material, grain, tooling.

Known limits of what *is* done:

* The wall search samples at most 12 points per face per direction. A wall whose overlap region contains none of those points is missed (false negative); a reported wall is always real at its sample point.
* A wall found between parallel planes is a wall *at one place*. The finding's `location_mm` is that place; the same pair may be thicker or thinner elsewhere only if the faces are not parallel, and then they would not have been paired.
* Hole detection is `brep.inventory`'s: concave full cylinders open at one or both ends. Pockets that are round and open are holes by that definition.
* `cnc.internal_corner_radius` judges only edges and cylinders parallel to `tool_axis`. A pocket cut from the side is a different `tool_axis` call.
* `cnc.tool_access` counts reachability of one point per planar face with infinitely thin rays; partial shadowing, tool diameter and length are ignored. With the default `min_access_directions` of 1 it only reports faces no axis direction reaches.
* The sheet thickness is the gap covering the largest face area. A part that is half 2 mm and half 3 mm is not a sheet; the tool says so (`sheet.thickness_uniform`), it does not choose.
* Several solids are one body. Face ids are numbered over the whole file.
* Inch and other units: STEP and IGES carry their unit and OpenCASCADE converts to millimetres (tested with an inch STEP); BREP numbers are taken as millimetres, as in `geometry_measure`.
* The tool was not exercised against a real vendor STEP assembly or a sheet-metal CAD export (bends exported as B-splines or cones would not be recognised as cylinder pairs).
