---
name: eng-dfm-review
description: Use to review a 3D CAD part (STEP/IGES/BREP, mm) for CNC milling or sheet-metal manufacturability with geometry_measure and geometry_check_dfm (shop limits, findings (exact vs heuristic), checks not performed). Load eng-toolkit first.
---

# DFM review workflow (eng-toolkit)

Load the `eng-toolkit` skill first for the unit rules and report format. The DFM rules are the toolkit's own **rules of thumb, not a standard**: say so in the review.

## 1. Scope and inputs

- The file must be a **closed solid** in `.step .stp .iges .igs .brep` (read as millimetres). Meshes (`.stl`) and surface-only files are refused. If the user has another CAD format, ask for a STEP export; `geometry_convert` converts between supported formats.
- The process must be `cnc_milling` or `sheet_metal`. Turning, injection molding, casting and additive are **not supported**: say that plainly and offer the general guidance you can give without the tool, clearly marked as not tool-checked.
- **Ask for the shop's limits** (or its capability sheet) before judging; a limit you pick yourself is YOUR assumption, never "the tool's rule" or "the shop's rule". Findings depend on them: for milling `tool_radius`, `min_wall`, `min_hole_diameter`, `max_hole_depth_ratio`, spindle axis `tool_axis`; for sheet metal `max_thickness`, `min_bend_radius_ratio`, `min_hole_edge_ratio`. **`tool_radius` is the RADIUS of the smallest end mill, half its diameter**: a 6 mm cutter is `"3 mm"`, a 3 mm cutter `"1.5 mm"` (ask which the shop means). Pass limits as JSON with units in strings, e.g. `{"min_wall": "1.5 mm", "tool_radius": "3 mm"}`.

## 2. Measure first

Call `geometry_measure` with the file path and no queries: volume, surface area, bounding box, face inventory by kind, hole list (diameter, depth, through/blind). Use it to confirm the part is what the user thinks it is (size, hole count) and to choose the spindle axis. Pass `density` (e.g. `"2700 kg/m^3"` aluminium, `"7850 kg/m^3"` steel) when the user wants mass. Face ids `F<n>` and hole ids `H<n>` belong to that file; use them in follow-up queries such as `thickness P(10 mm, 0 mm, 5 mm) +z`, `diameter H2`, `distance F3 H1`.

## 3. Run the checks

`geometry_check_dfm` with `process`, `tool_axis` (milling: the direction the spindle approaches from; try the main setup direction, and if the part needs several setups, run once per axis and combine), `limits`, and `max_findings`. If it returns `geometry.time_budget_exceeded`, increase `max_seconds` (up to 600) once; if it still expires, report that the check could not be completed for this part rather than reporting a partial list as complete.

## 4. Read the findings correctly

- `limits[].source` says `default` (toolkit rule of thumb) or `caller` (what you passed, so your assumption): report which. A position "from the bend" needs a datum (outer face or bend line) before you compare it with an edge rule.
- Each finding has rule id, **severity** (`error` an exact rule is violated, must be fixed or justified; `warn` a heuristic rule, review it; `info` advisory), measured value and limit, face/edge ids, a point in model coordinates, and **exact vs heuristic**. Heuristic findings are never `error` (two sheet exceptions: `sheet.thickness_uniform`, "no sheet found"). Present heuristic findings as "possible" and exact ones as measured.
- **Read `data.measurements` before saying a rule passed**: `bends: 0` on a part that visibly has bends, `smallest_wall_mm` null (a warning says why), `holes_checked_against_edges: 0` mean that rule was not exercised: say "not checked", not "passed". Always repeat the **checks that were NOT performed**.
- **Milling**: concave edges whose faces turn by less than the `near_tangent_deg` limit (default 5) are near-tangent kinks, not corners: the tool skips them and counts them in `near_tangent_edges_skipped` plus a warning; do not report them. A real sharp corner carries `details.uncut_depth_mm`. The wall check covers tapered and non-parallel faces by normal rays (about 12 per face), so `smallest_wall_mm` is the smallest wall found at those points (a 40 mm taper from 0.5 to 1.5 mm read 0.83 mm: a thin spot between samples can be missed). A warning appears when `2 x tool_radius` is wider than the narrowest slot (`narrowest_slot_mm`; not verified here).
- **Sheet metal**: the cylinder pairs of one bend (ribs split its inner face) are merged into one finding (`bends`, `bend_face_pairs`; merge not re-run here); a sharp bend is a heuristic `warn` (`sharp_bends`); ribs, gussets and T-joints standing on the sheet are one `sheet.joint` info finding (`joints`), not bend errors.
- **Thickness query** (`geometry_measure` `thickness P(...) +z`): from a point inside the material it returns the wall that contains the point (`start: inside`, `entry_mm`/`exit_mm`) with a warning, not the distance to the next face; read `start` before quoting it.
- Verify the important findings with `geometry_measure` queries at the reported point (wall thickness, hole diameter) before telling the user to change the design.
- Group by feature (internal corners, thin walls, deep holes, unreachable faces / extra setups, sheet-metal bends and hole-to-edge), highest severity first.

## 5. Recommend, do not overreach

For each issue propose a concrete change with a number (e.g. "increase the internal corner radius to at least 3 mm or switch to a 2 mm tool and accept longer machining time", "move the hole 4 mm further from the edge"). Mention cost/setup consequences only qualitatively. Do not claim the part is "manufacturable" or "DFM approved": say which rules passed under which limits.

## 6. Optional follow-ups

`geometry_view` with `color_by="dfm"` (same process, tool axis and limits) writes a PNG picture (the one to hand over) and a GLB coloured by the worst finding (red error, orange warning, blue info), face ids as in `geometry_measure`; see `eng-3d-view`. For a picture of only the confirmed problems, use `color_by="highlight"` with named groups, e.g. `highlight="corners: F5,F12; thin wall: F3-F4"` (one colour per group), and say what each colour means; do not write scripts to recolour a GLB. `drawing_make_views` can produce a dimensioned drawing sheet (PDF/DXF/PNG) of the part for the shop; `geometry_make_part` can build a modified candidate from a text spec when the user wants to compare alternatives (then re-run the same checks with the same limits).

## 7. Report

Use the report format from `eng-toolkit`. Result: counts by severity and the top findings with location. Inputs: file, process, axis, limits (default or user-given). Checks: what was measured and cross-verified, checks not performed, time-budget notes. Limits: toolkit rules of thumb, not a standard; geometry only (no material, tolerance, finish, tooling or cost review).
