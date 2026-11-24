---
name: eng-dfm-review
description: Use FIRST for "can a shop machine or bend this STEP part", weight and size of a part, thin walls, tool access, bend radius, hole near an edge, "mark the problem faces" (geometry_measure, geometry_check_dfm). Shop limits, exact vs heuristic findings.
---

# DFM review workflow (eng-toolkit)

Load the `eng-toolkit` skill too for the unit rules and report format. The DFM rules are the toolkit's own **rules of thumb, not a standard**: say so.

## 1. Scope and inputs

- The file must be a **closed solid** in `.step .stp .iges .igs .brep` (read as millimetres). Meshes (`.stl`) and surface-only files are refused. For another CAD format ask for a STEP export (`geometry_convert` converts supported formats).
- The process must be `cnc_milling` or `sheet_metal`. Turning, injection molding, casting and additive are **not supported**: say so plainly; general guidance is allowed, marked as not tool-checked.
- **Ask for the shop's limits** (or its capability sheet) before judging; a limit you pick yourself is YOUR assumption, never "the tool's rule" or "the shop's rule". Findings depend on them: for milling `tool_radius`, `min_wall`, `min_hole_diameter`, `max_hole_depth_ratio`, spindle axis `tool_axis`; for sheet metal `max_thickness`, `min_bend_radius_ratio`, `min_hole_edge_ratio`. **`tool_radius` is the RADIUS of the smallest end mill, half its diameter**: a 10 mm cutter is `"5 mm"`, a 4 mm cutter `"2 mm"` (ask which the shop means). Pass limits as JSON with units in strings, e.g. `{"min_wall": "1.5 mm", "tool_radius": "3 mm"}`.

## 2. Measure first

Call `geometry_measure` with the file path and no queries: volume, surface area, bounding box, face inventory by kind, hole list (diameter, depth, through/blind). Use it to confirm the part is what the user thinks it is (size, hole count) and to choose the spindle axis. Pass `density` (e.g. `"2700 kg/m^3"`) for mass. Face ids `F<n>` and hole ids `H<n>` belong to that file; use them in queries such as `thickness P(10 mm, 0 mm, 5 mm) +z`, `diameter H2`, `distance F3 H1`.

## 3. Run the checks

**Ask the milling spindle axis and setup before `geometry_check_dfm`: the verdict depends on it**, and say in the answer which `tool_axis` you used and why (if unknown, run once per axis and show each). Without `tool_axis` the tool uses +z and warns "tool_axis defaulted to +z" (`data.tool_axis_defaulted`): that is a placeholder, not the shop's setup. `geometry_check_dfm` takes `process`, `tool_axis` (the direction the spindle approaches from; several setups: one run per axis, combined), `limits` and `max_findings`. If it returns `geometry.time_budget_exceeded`, raise `max_seconds` (up to 600) once; if it still expires, say the check could not be completed.

## 4. Read the findings correctly

- `limits[].source` says `default` (toolkit rule of thumb) or `caller` (what you passed, so your assumption): report which. A position "from the bend" needs a datum (outer face or bend line) first.
- Each finding has rule id, **severity** (`error` an exact rule is violated, must be fixed or justified; `warn` a heuristic rule, review it; `info` advisory), measured value and limit, face/edge ids, a point in model coordinates, and **exact vs heuristic**. Heuristic findings are never `error` (two sheet exceptions: `sheet.thickness_uniform`, "no sheet found"). Say "possible" for heuristic findings, "measured" for exact ones.
- **Read `data.measurements` before saying a rule passed**: `bends: 0` on a part that visibly has bends, `smallest_wall_mm` null, `holes_checked_against_edges: 0` mean that rule was not exercised: say "not checked", not "passed". **Summary `errors 0 / warnings 0` does not mean the bends were checked**: if `bends` is 0 on a bent part the bend rule did not run (see Sheet metal): fix the model and rerun, never explain it away. Always repeat the **checks that were NOT performed**.
- **Milling**: concave edges whose faces turn by less than the `near_tangent_deg` limit (default 5) are near-tangent kinks, not corners: the tool skips them and counts them in `near_tangent_edges_skipped` plus a warning; do not report them. A real sharp corner has `details.uncut_depth_mm`. The wall check covers tapered and non-parallel faces by normal rays (about 12 per face): `smallest_wall_mm` is the smallest wall found at those points, a thin spot between samples can be missed. A warning appears when `2 x tool_radius` exceeds the narrowest slot (`narrowest_slot_mm`).
- **Sheet metal**: a bend counts only when its inner and outer cylinders share one axis within 0.005 mm (0.05 mm is accepted with a `sheet.bend_tolerance` warn that gives the offset, e.g. 0.028 mm). A looser pair is not a bend: its faces are in `unrecognised_bend_faces`, a `sheet.bend_unrecognised` warn says "bend-like faces not evaluated", no radius was checked there, and `bends` stays short: make the arcs concentric and rerun. The cylinder pairs of one bend (ribs split its inner face) are one finding (`bends`, `bend_face_pairs`); a sharp bend is a heuristic `warn` (`sharp_bends`); ribs, gussets and T-joints standing on the sheet are one `sheet.joint` info finding (`joints`), not bend errors.
- **Thickness query** (`thickness P(...) +z`) from a point inside the material returns the wall that contains the point (`start: inside`, `entry_mm`/`exit_mm`) with a warning, not the distance to the next face: read `start` before quoting.
- Verify important findings with `geometry_measure` at the reported point before advising a change. Group by feature, highest severity first.

## 5. Recommend, do not overreach

For each issue propose a concrete change with a number (e.g. "increase the internal corner radius to at least 3 mm or switch to a 2 mm tool and accept longer machining time", "move the hole 4 mm further from the edge"). Do not claim the part is "manufacturable" or "DFM approved": say which rules passed under which limits.

## 6. Optional follow-ups

`geometry_view` with `color_by="dfm"` (same process, tool axis and limits) writes a PNG (shown in the reply; give its path) and a GLB coloured by the worst finding (red error, orange warning, blue info), with a ring and label `D<n>` at each finding and `focus="D1"` for a close-up panel; see `eng-3d-view`. A colour is a flag from a rule of thumb, not a verdict; grey faces were not shown to be fine. For only the confirmed problems use `color_by="highlight"` with named groups, e.g. `highlight="corners: F5,F12; thin wall: F3-F4"` (one colour per group, optional `focus`), and say what each colour means; never script a GLB recolour. `drawing_make_views` makes a dimensioned sheet for the shop; `geometry_make_part` builds a modified candidate (re-run the same checks with the same limits).

## 7. Report

As in `eng-toolkit`. Result: counts by severity and the top findings with location. Inputs: file, process, axis, limits (default or user-given). Checks: measured and cross-verified, checks not performed, time-budget notes. Limits: rules of thumb, not a standard; geometry only (no material, tolerance, finish, tooling or cost).
