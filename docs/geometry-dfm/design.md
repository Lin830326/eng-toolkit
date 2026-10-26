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
| `eng_toolkit/brep/_dfm.py` | measuring only: `edge_facts`, `cylinder_facts`, `facing_pairs`, `normal_walls`, `face_access`, `face_extents`, `coaxial_tolerance_mm` |
| `eng_toolkit/domains/geometry/dfm.py` | limits, rules, `Finding`, what was not performed |
| `eng_toolkit/domains/geometry/tools.py` | the tool wrapper `check_dfm` (contract shaping, artifact `dfm_findings.json`) |
| `tests/dfm_fixtures.py`, `tests/test_geometry_check_dfm.py`, `tests/test_dfm_ray_index.py`, `tests/test_geometry_dfm_uat_fixes.py` | dimensioned OCP fixtures, rule and contract tests, differential checks for rays, cylinder grouping, sampling, and distance pruning, and the user-acceptance regressions |

## Facts measured by `brep/_dfm.py`

* **Edge facts** (exact). For every edge shared by two different faces: the two face ids, line/circle/other, the edge midpoint and tangent, the length, and **convex / concave / smooth** with the material angle (90 for a box corner, 270 for a pocket corner, 180 for a tangent join). Convexity comes from the outward normals of both faces at the edge midpoint and the direction in which the first face's boundary runs along the edge: the second face's inward direction is `n2 x t`, and the edge is convex when that points behind the first face's tangent plane. Seams (one face on both sides), degenerate edges and edges of three or more faces are left out.
* **Cylinder facts** (exact). Radius, axis, axial ends, angular span, a point on the face half way round and along, whether the face is concave or convex.
* **Facing pairs** (gap exact, existence checked by rays). Two faces with material between them:
  * *planes*: opposite outward normals, each on the other's material side (`(origin_b - origin_a) . n_a < 0`; the other sign is air, a slot, and is not a wall). The gap is the exact plane offset. Candidate pairs are pruned by axis-aligned bounding boxes. A pair is kept only if a sample point of one face (centre of one of its 12 largest mesh triangles) lies, projected along the normal, inside the other face (`BRepClass_FaceClassifier`) and a ray from just inside the material at that point meets the second face first, at `gap`, with nothing in between.
  * *cylinders*: a concave and a convex cylinder face on one axis, same axial extent and span; gap = difference of radii; a radial ray from the axis must meet exactly those two surfaces first. This covers tube walls and sheet-metal bends. "One axis" allows a perpendicular offset of `brep.coaxial_tolerance_mm(gap)` = 1e-3 x gap (at least 1e-6 mm), and the radial ray the same difference (see the user-acceptance fixes below).
* **Normal walls** (`normal_walls`; chord sampled, refined distance exact). Every face that is in no facing pair casts rays from up to 12 spread sample points along its inward normal, up to the search range; the shortest chord per face pair is kept, confirmed in the material and, for chords up to 2 x `min_wall`, replaced by the exact `BRepExtrema` closest distance when that segment runs within 1 degree of both normals through the material. Details in `docs/geometry/design.md`, "Thin material".
* **Face access** (heuristic). For each planar face, one sample point (centre of the largest mesh triangle) moved 1e-5 of the part size out along the normal; each of the six axis directions with non-negative dot product with the normal counts as open if the ray from that point leaves the part without meeting any face. The same point also gives `clearance_mm`, the air along the outward normal to the next face (a slot's width in front of a slot wall).
* **Face extents** (`face_extents`). The range of a face's boundary (vertices and 16 points per curved edge) along a direction: on which side of a line in its plane a face lies.

## Rules

Limits are in `LIMIT_DEFAULTS` (`domains/geometry/dfm.py`). Lengths are strings with units in `limits`; ratios, counts and `near_tangent_deg` (degrees, above 0, at most 90) are plain numbers. Comparisons: a measured value **equal** to the limit passes (to 1e-9 relative; a bend radius to 1e-3 of the thickness). `tool_radius` is the end mill's **radius**, half its diameter: a 3 mm end mill is `"1.5 mm"` (the parameter description, the limit's `meaning` and the tool description say so).

| rule | process | severity | check | what is measured | default limit |
|---|---|---|---|---|---|
| `cnc.internal_corner_radius` | cnc_milling | error | exact | concave straight edges parallel to `tool_axis` (radius 0) whose faces turn by at least `near_tangent_deg`, with `details.uncut_depth_mm` = r (1 / sin(a/2) - 1), a = 360 - material angle; concave cylinder faces with their axis parallel to `tool_axis`, not part of a hole and not a full circle (their radius) | `tool_radius` 3 mm, `near_tangent_deg` 5 |
| `cnc.min_wall` | cnc_milling | error (exact), warn (chord) | exact gap for facing pairs and refined normal walls; heuristic for a chord that could not be refined; completeness limited (below) | facing pairs and normal walls closer than `min_wall`, searched up to 5 x `min_wall` | `min_wall` 0.8 mm |
| `cnc.hole_depth_ratio` | cnc_milling | error | exact | detected round holes: cylindrical depth / diameter (drill-point cone and counterbore not counted) | `max_hole_depth_ratio` 10 |
| `cnc.min_hole_diameter` | cnc_milling | error | exact | detected round holes: diameter | `min_hole_diameter` 1 mm |
| `cnc.tool_access` | cnc_milling | warn | **heuristic** | number of the six axis directions that reach a planar face's sample point | `min_access_directions` 1 |
| `sheet.thickness` | sheet_metal | info (error if none) | exact gap; the choice of "the" thickness is heuristic | facing pairs up to `max_thickness`, grouped by gap to 1e-6 mm; the group covering the largest face area is the sheet | `max_thickness` 10 mm |
| `sheet.thickness_uniform` | sheet_metal | error | **heuristic** | faces that are neither in a pair of the sheet thickness nor a cut edge (a face touching two covered faces); lists the first 50 in `face_ids` (the count in `details.faces_total`) and any other pair thicknesses. The complete list stays on the in-process finding (`Finding.full_face_ids`, for `geometry_view`) and is not in the output | none |
| `sheet.min_bend_radius` | sheet_metal | error (exact), warn (sharp) | exact for a bend, the cylinder pairs of the sheet thickness that share a face or lie on one axis with equal radii merged into one finding; **heuristic** for a sharp bend: a concave straight edge between two sheet faces that both end at it and whose faces across the sheet meet at a convex edge (directly or through one convex face) | inner radius of the bend | `min_bend_radius_ratio` 1.0 x thickness |
| `sheet.joint` | sheet_metal | info | **heuristic** | one finding counting the concave straight edges between sheet faces that are not bends: a rib, gusset or T-joint standing on a sheet (no outer corner, or a face running on past the edge) | none |
| `sheet.hole_to_edge` | sheet_metal | error | exact | `BRepExtrema` distance from a hole's wall to the nearest edge or cutout face (a face of the sheet's edge, excluding hole walls), holes whose axis is parallel to a sheet face normal | `min_hole_edge_ratio` 1.5 x thickness |

Severity policy: `error` = an exact rule is violated, `warn` = a heuristic rule is violated, `info` = a fact with no limit. Exception: `sheet.thickness_uniform` and the "no sheet found" case are heuristic and reported as `error` because the rest of the sheet analysis depends on them; each carries `check: heuristic`. Every other heuristic finding is `warn` or `info`; `data.conventions.severity` names the two exceptions, and `tests/test_geometry_dfm_uat_fixes.py` checks the rule over the fixtures. (Before the user-acceptance fixes the sharp bend was a heuristic `error`, which contradicted the convention.)

Every finding carries: `rule`, `severity`, `check` (`exact`/`heuristic`), `message`, `measured` and `limit` (value and unit; `ratio` and `count` are unitless), `passes_when`, `face_ids`, `edge_ids`, `location_mm`, `basis` (why it is exact or heuristic) and `details`. `data.summary` counts by severity and by rule. `data.findings` lists up to `max_findings` (default 100); `dfm_findings.json` holds all of them.

## Refusals

* Not closed solids only: `geometry.not_a_solid` at `details.param` `/model_path`. A manufacturability check needs material between faces. This covers no solid or zero volume (a shell, loose surfaces) and, since the geometry gaps fix, a solid whose shell is open (free edges: an edge used by one face of its shell; seams and degenerate edges excepted), for example five faces of a box saved as BREP, and faces outside every solid, for example a box with a loose face beside it in one compound. The message names what is wrong (`N of M shells of its solids are open (K free edges)`, `N faces lie outside any solid`) and `error.details` has `solids`, `shells`, `open_shells`, `free_edges`, `faces_outside_solids` and `loose_face_ids`. The check is `dfm.require_closed_solids` on `brep.closure`, the same refusal as `geometry_view`. Before the fix both files were checked (`mass_properties` integrates a volume for them).
* Empty or unreadable file: `input.invalid` (same reader as `geometry_measure`). `.stl`: `input.unsupported_format`.
* More than 2500 faces: `input.too_large`.
* `max_seconds` outside 1 to 600: `input.out_of_range`. The default budget is 120 seconds.
* Wall-clock budget exhausted: `geometry.time_budget_exceeded`, with the phase, elapsed seconds and budget in `error.details`.
* Limits (`dfm.parse_limits`, also the validator of `geometry_view`): each key is validated on its own and refused at its own JSON pointer `/limits/<key>` (RFC 6901 escaping). A value too large for a float (a 400-digit number, which gave `engine.failure` before), infinite (`1e999`, `Infinity`, `NaN`, `1e400 mm`), or above the ceiling, also after the conversion to millimetres (`1e300 m`): `input.out_of_range`. Zero, negative, not a number, or a boolean: `input.invalid`. A bare number for a length: `units.bare_number`; text that is not `<number> <unit>` (`inf mm`): `units.parse_error`; the wrong dimension: `units.dimension_mismatch`. An unknown key, or a sheet-metal limit given to cnc_milling: `input.invalid` at `/limits/<first unknown key>`, with all of them in `details.unknown`. Not a JSON object, not valid JSON, nested too deeply, over 4096 characters: `input.invalid` at `/limits`.
* Every refusal carries `details.param` as a JSON pointer: `/model_path` (the file, the face-count limit, no faces, not a solid), `/process`, `/tool_axis`, `/max_findings`, `/output_unit`, `/max_seconds` (out of range, and an expired budget), `/limits` or `/limits/<key>`. Before the geometry gaps fix these were bare names (`max_seconds`) or missing (the face-count limit, no faces, not a solid). `geometry_measure` (`/model_path`, `/queries`, `/density`, `/inventory_limit`, `/output_unit`) and `geometry_convert` follow the same rule (`tools._with_pointers`, on `view.pointer`).
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

The inch fixture now creates `STEPControl_Writer` before setting `write.step.unit` to `INCH` and asserts that OpenCASCADE accepts the setting. The former value `IN` was rejected silently, so earlier runs of this fixture tested millimetres. A separate test checks that the file contains `CONVERSION_BASED_UNIT('INCH'`. The corrected file gives a 1.27 mm wall and a 0.05 inch result through the shared reader; no loader fix was needed. The geometry conversion inch test now also creates the writer before setting the unit.

Also tested: BREP input, two solids (warning, same wall), a planar shell (refused), an empty file, `.stl`, unknown and unsupported processes (`turning`, `injection_molding`, `additive`, `cnc_3axis`, `witchcraft`), ten bad `limits`, `max_findings` truncation, the face-count limit, a real MCP client through the gateway, and the edge facts (a box pocket has 8 concave edges at 270 deg and 8 tangent edges at 180 deg once filleted).

## Timing

The original measurements below came from the author's Windows 11 development machine, one call each. "Inventory" is `brep.inventory`, including hole detection, which `geometry_measure` also runs. These historical numbers are separate from the before/after measurements below.

| model | faces | whole call | of which inventory |
|---|---|---|---|
| 12 x 12 hole grid | 150 | 2.2 s | |
| comb of 125 fins (planar faces) | 506 | 7.3 s | 0.2 s |
| 31 x 31 hole grid, `cnc_milling` | 967 | 60.7 s | 32.4 s |
| 31 x 31 hole grid, `sheet_metal` | 967 | 58.1 s | 25.7 s |
| comb of 250 fins, `cnc_milling` | 1006 | 22.4 s | 0.3 s |
| comb of 250 fins, `sheet_metal` | 1006 | 22.0 s | 0.3 s |
| comb of 500 fins, `cnc_milling` | 2006 | 166.8 s | 0.8 s |

### Measurements after the speed changes

Measured on 2026-10-20 on a **Windows 11 development machine (CPU not queried)**, with Python 3.12.12, `cadquery-ocp-novtk` 8.0.1.1.0, and NumPy 2.5.4. Calls ran sequentially, without pytest running at the same time. These are single-call measurements, not latency guarantees. Import times and other phases varied between runs.

The fixtures are `dfm_fixtures.hole_grid` with pitch 5 mm and diameter 2 mm, and `dfm_fixtures.comb` with its defaults: pitch 3 mm, fin thickness 1.5 mm, fin height 10 mm, and a 5 mm base. The same persisted STEP bytes were used before and after each change. The script captured original findings before any production edit and refuses to overwrite a baseline. Whole-call times include file import, validation, inventory, rules, and artifact writes. Calls use default limits, `tool_axis=+z`, and `max_findings=500`.

| STEP model | faces | whole call before | whole call after | inventory before | inventory after |
|---|---|---|---|---|---|
| 12 x 12 grid, `cnc_milling` | 150 | 1.13 s | 0.75 s | 0.21 s | 0.07 s |
| 12 x 12 grid, `sheet_metal` | 150 | 1.18 s | 0.78 s | 0.20 s | 0.07 s |
| 31 x 31 grid, `cnc_milling` | 967 | 20.27 s | 8.35 s | 10.86 s | 0.59 s |
| 31 x 31 grid, `sheet_metal` | 967 | 19.81 s | 8.68 s | 7.31 s | 0.61 s |
| 125-fin comb, `cnc_milling` | 506 | 2.68 s | 2.01 s | 0.05 s | 0.05 s |
| 125-fin comb, `sheet_metal` | 506 | 2.06 s | 1.74 s | 0.05 s | 0.05 s |
| 250-fin comb, `cnc_milling` | 1006 | 9.47 s | 5.12 s | 0.10 s | 0.09 s |
| 250-fin comb, `sheet_metal` | 1006 | 5.44 s | 3.89 s | 0.09 s | 0.09 s |
| 500-fin comb, `cnc_milling` | 2006 | 66.85 s | 18.16 s | 0.19 s | 0.20 s |
| 500-fin comb, `sheet_metal` | 2006 | 21.56 s | 13.02 s | 0.20 s | 0.18 s |

The measured 967- and 1006-face cases are below 10 s, and the 2006-face cases are below 30 s. The cap stays at 2500 faces; parts above 2006 faces remain unmeasured.

Phase times in seconds follow. `Pairs` includes mesh construction and triangle sampling. `Distance` is the total time in exact hole-to-edge distance queries. Wrapper work and artifact writes account for the difference between these phases and the whole call.

| STEP model | version | read | mass | inventory | edges | cylinders | pairs | access | distance |
|---|---|---|---|---|---|---|---|---|---|
| grid 31, CNC | before | 3.236 | 0.155 | 10.857 | 1.846 | 0.042 | 2.339 | 1.391 | 0 |
| grid 31, CNC | after | 3.143 | 0.148 | 0.590 | 1.472 | 0.026 | 2.376 | 0.565 | 0 |
| grid 31, sheet | before | 3.137 | 0.145 | 7.312 | 1.471 | 0.027 | 2.970 | 0 | 4.548 |
| grid 31, sheet | after | 3.227 | 0.146 | 0.606 | 1.525 | 0.027 | 2.429 | 0 | 0.605 |
| comb 500, CNC | before | 8.171 | 0.122 | 0.187 | 3.373 | 0.004 | 1.871 | 52.700 | 0 |
| comb 500, CNC | after | 8.047 | 0.125 | 0.202 | 3.376 | 0.004 | 0.904 | 5.436 | 0 |

The same comparison also covered persisted BREP files. Whole-call times before and after were 13.36 and 5.13 s for the 967-face CNC grid, 20.14 and 5.28 s for the sheet grid, 11.02 and 3.30 s for the 1006-face CNC comb, and 67.12 and 8.90 s for the 2006-face CNC comb. STEP import is now a large part of the cost, especially the roughly 8 s import of the largest comb. Import code was outside this change's scope.

### Profile and changes

`cProfile` and nested wall-clock timers ran before editing production code. On the 967-face BREP CNC grid, the instrumented inventory spent 4.73 s in `_hits` and 3.26 s in 461,280 `_Candidate.accepts` calls. Meshing took 2.35 s, and sample extraction took 2.37 s across the two measuring phases. On the 1006-face CNC comb, ray queries took 5.00 s of the 5.19 s access phase; face classifications took only 0.01 s. Intersector loading was about 0.02 s. This profile supported ray pruning and batched array operations rather than replacing the face classifier.

The changes were measured separately, with a full findings comparison after each:

* `_IndexedIntersector` builds conservative analytic face boxes once per measuring phase and tests ray segments against them with NumPy. It creates and reuses an OCCT intersector only for faces whose boxes the ray can meet. Boxes include topology tolerances and rounding slack. The original OCCT intersection tolerance and the global sorting and merging of raw hits remain unchanged.
* Hole grouping filters distant axes with batched component bounds before calling the original `_Candidate.accepts`. Candidate order and the final grouping arithmetic remain unchanged. The filter still scans candidate arrays quadratically, but avoids hundreds of thousands of Python calls on grids.
* Triangle cross products and centroids use batched array operations. Each triangle still uses the original scalar norm, followed by the original stable sort. This preserves equal-area sample order. Meshing settings, all 12 sample points, nudges, search ranges, and geometric tolerances remain unchanged.
* The sheet hole-to-edge search skips exact distance queries when the bounding-box lower bound exceeds the reporting limit by more than 1e-6 mm. This slack exceeds the box-coordinate rounding error. Every distance that can produce a finding still uses `BRepExtrema`; nearest-face order and limit comparisons remain unchanged.

With the same instrumentation after these changes, the CNC grid's inventory ray queries take 0.46 s and sample extraction takes 0.43 s. The distant-axis filter avoids all `_Candidate.accepts` calls on that disjoint grid. Native meshing still takes 2.74 s. The 1006-face CNC comb spends 1.74 s in ray queries, down from 5.00 s in the before profile.

The temporary runner is `scratch_bench/bench_dfm.py`; comparison summaries are produced by `scratch_bench/report.py`. For example, `.venv\Scripts\python.exe scratch_bench\bench_dfm.py final --format step` reruns all STEP cases against the frozen baseline. Scratch files are retained for coordinator review and are not production dependencies.

At each of the four optimization steps, canonical JSON comparisons matched all 20 benchmark calls, both processes for five models in both formats, and all 65 recorded fixture tool calls. Benchmark comparisons include complete `data.findings`, the untruncated findings artifact, and measurements. Fixture comparisons include complete `data.findings` and error families; generated workspace paths in errors are not stable across runs. A separate comparison against the saved original sampler matched every coordinate and sample order on 7,060 planar faces. The 23 additional tests cover exact ray distances, finite endpoints, trimmed holes, located curved faces, enlarged edge tolerances, grouping boundaries, sample ranking, and distance pruning. Existing fixture tests were not changed.

The full suite passed before and after the changes with `.venv\Scripts\python.exe -m pytest -q --basetemp=scratch_bench/pytest_tmp -p no:cacheprovider`. `.venv\Scripts\python.exe -m eng_toolkit doctor` returned `ok: true` and exit 0 both times. Final logs are retained in `scratch_bench/final_pytest.log` and `scratch_bench/final_doctor.json`.

### Time budget

`geometry_check_dfm` accepts integer `max_seconds`, default 120, from 1 to 600 seconds. A monotonic deadline starts at tool entry, before workspace setup and input validation. The wrapper passes a deadline callback to inventory, hole detection, DFM measurements and rules. Other callers of these measuring functions can omit the callback.

Checks at phase boundaries and within face, edge, candidate-pair, ray and rule loops stop work when elapsed time reaches the budget. Failure returns `ok: false`, family `geometry.time_budget_exceeded`, and `details` containing exactly `phase`, `elapsed_s` and `max_seconds`. The message names the phase, seconds used and limit. Phases are `read`, `inventory`, `edges`, `cylinders`, `pairs`, `access`, `rules` and `distance`. Input copying and native import use `read`; solid validation and hole detection use `inventory`; hole-to-edge queries use `distance`.

An OpenCASCADE call already running cannot be interrupted. STEP import, meshing or an exact geometric query can exceed the budget before the next check returns the error. The budget is cooperative and does not guarantee a response within `max_seconds`. MCP transport cancellation remains separate. Results retain `review_status: unreviewed`.

Before editing production code, `scratch_bench/compare_dfm.py capture` froze input bytes, arguments and complete `data.findings` JSON from all 40 successful calls in the existing DFM fixture suite. Replays after the inch-fixture fix matched 40/40 calls. Replays after adding the budget matched 40/40 with the default and 40/40 with 600 seconds. This includes both supported processes and the original persisted file used by the inch test. The corrected inch file has separate declaration and dimensional tests. The runner refuses to overwrite its baseline.

`tests/test_geometry_check_dfm_budget.py` covers a real one-second timeout on a 250-fin comb, bounds, unchanged findings with default and generous budgets, deterministic expiry in every phase, finite error serialization, and the parameter schema and timeout through a real gateway client.

### Remaining limits

STEP import, native meshing, and edge facts still take time. Face access still uses one sample and exact native ray intersections, and its work can grow faster than linearly. These measurements cover constructed fixtures through 2006 faces, not arbitrary vendor files. Rules keep their original exact or heuristic classifications, and outputs remain `review_status: unreviewed`.

## User-acceptance fixes (scenarios C1 and C4)

Each was reproduced with the scenario's own input before the change; `tests/test_geometry_dfm_uat_fixes.py` holds the regression tests (a synthetic heatsink in `dfm_fixtures.heatsink`, the C4 bracket built with `geometry_make_part` from both radius arcs and 4-decimal via arcs, and the real KiCad `Heatsink_125x35x50mm_3xFixationM3.step`, skipped when KiCad is not installed).

| problem | before | after |
|---|---|---|
| C1: tapered fin flanks meet the tip arcs at a 2 degree concave kink (material angle 182.01) | 22 "sharp internal corner, radius 0" errors, half of the 47 errors, colouring most fin faces red | an edge whose faces turn by less than `near_tangent_deg` (default 5) is near-tangent, counted in `measurements.near_tangent_edges_skipped` and in a warning, not reported; the cusp a 1.5 mm tool leaves at 2 degrees is 2e-4 mm. A 270 degree pocket corner is still an error (turn 90), and sharp corners now carry `details.uncut_depth_mm` |
| C1: the 1 mm wall check | `face_pairs_tested` 0 and `smallest_wall_mm` null on the tapered fins, no warning; the 0.94 mm hole-to-bottom ligament not seen | normal walls: three exact `cnc.min_wall` errors of 0.94 mm (hole wall to base bottom), `smallest_wall_mm` 0.94; null only with a warning that says why |
| C1: `thickness` from a point inside a fin | 8.864 mm (the slot after the fin) and 3.2 mm (the hole) with no warning | the wall that contains the point (3.136 mm fin, 30.853 mm from the hole top to the fin tip), `start: inside`, `entry_mm`/`exit_mm`, and a warning |
| C4: bend built from 3-point `via` arcs | inner and outer bend cylinders 7e-5 mm off-axis, rejected by the 1e-6 mm coaxiality test: `bends` 0, no radius finding for an R2 bend in 3 mm (0.67 t) | coaxial within 1e-3 of the gap; gaps within 1e-3 of the thickness are one thickness. Radius and via arcs both give `Bend inner radius 2 mm is 0.667 times the thickness 3 mm, below 1`; R3 passes for both |
| C4: ribs split the bend | the one bend reported three times (its inner face is cut in three by the ribs) | bend pairs that share a face, or lie on one axis with equal radii, are one bend (`bends` 1, `bend_face_pairs` 3) |
| C4: rib-to-plate junctions | 8 "sharp inside corner (radius 0)" heuristic errors | a concave edge is a sharp bend only when both faces end at it and the faces across the sheet meet at an outer corner; the 8 junctions are one `sheet.joint` info finding (`measurements.joints` 8). Ribs flush with the plate's side faces (the first C4 model) are joints too; its real sharp corner stays a sharp bend |
| C4: heuristic findings labelled `error` | sharp bends were heuristic errors, against the stated convention | heuristic = `warn` (or `info`) except the two documented sheet exceptions; the convention text names them |
| C1: `tool_radius` read as a diameter | nothing said it | the parameter and limit descriptions say RADIUS with an example; a warning when `2 x tool_radius` is wider than the narrowest gap in front of a wall parallel to the tool axis (`measurements.narrowest_slot_mm`, one ray per planar face) |

Expected on the KiCad heatsink with `{"min_wall": "1 mm", "tool_radius": "1.5 mm"}`, `tool_axis +z`: 22 `cnc.internal_corner_radius` (all R0.25 root fillets), 3 `cnc.hole_depth_ratio` (10.9375), 3 `cnc.min_wall` (0.94), nothing else. Timing: the 500-fin comb (2006 faces) took the same wall-clock time before and after on the same machine in one session (24.2 s and 24.3 s `cnc_milling`, both under load from other processes): the fin flanks are in exact plane pairs and cast no normal rays; the remaining faces cast 2032.

## What is not done

The tool lists these in `data.not_performed` for the process.

cnc_milling: corner radius at the bottom of a pocket and pocket depth against tool diameter; undercuts, T-slots and tool length (only the one-sample reach test exists, `cnc.tool_access`); a thin spot between the sample points of walls that are not parallel planes or coaxial cylinders (they are measured by normal rays from 12 points per face); walls thicker than 5 x `min_wall` (not searched); threads, counterbores and countersinks (a counterbore is listed as a separate blind hole), drill-point cones; tolerance, surface finish, set-ups, material, tool deflection, cost.

sheet_metal: flat pattern and unfolding, bend allowance, K-factor, springback; hole-to-bend and bend-to-bend distance, bend relief, minimum flange length; minimum hole diameter and slot width against thickness; hems, jogs, lances, countersinks and other formed features (they appear as off-thickness faces); material, grain, tooling.

Known limits of what *is* done:

* The wall search samples at most 12 points per face per direction. A wall whose overlap region contains none of those points is missed (false negative); a reported wall is always real at its sample point.
* A wall found between parallel planes is a wall *at one place*. The finding's `location_mm` is that place; the same pair may be thicker or thinner elsewhere only if the faces are not parallel, and then the normal rays measure it.
* `measurements.smallest_wall_mm` is the smallest of the facing-pair gaps and normal walls found. It is null only when nothing thinner than the search range was found, and then a warning says so with the number of pairs tested and rays cast. Faces the mesher gave no triangle are named in a warning.
* Hole detection is `brep.inventory`'s: concave full cylinders open at one or both ends. Pockets that are round and open are holes by that definition.
* `cnc.internal_corner_radius` judges only edges and cylinders parallel to `tool_axis`. A pocket cut from the side is a different `tool_axis` call.
* `cnc.tool_access` counts reachability of one point per planar face with infinitely thin rays; partial shadowing, tool diameter and length are ignored. With the default `min_access_directions` of 1 it only reports faces no axis direction reaches.
* The sheet thickness is the gap covering the largest face area. A part that is half 2 mm and half 3 mm is not a sheet; the tool says so (`sheet.thickness_uniform`), it does not choose.
* Several solids are one body. Face ids are numbered over the whole file.
* Inch and other units: STEP and IGES carry their unit and OpenCASCADE converts to millimetres (tested with an inch STEP); BREP numbers are taken as millimetres, as in `geometry_measure`.
* The tool was not exercised against a real vendor STEP assembly or a sheet-metal CAD export (bends exported as B-splines or cones would not be recognised as cylinder pairs).
