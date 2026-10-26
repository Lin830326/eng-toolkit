---
name: eng-3d-view
description: Use to build a 3D part from a text spec, or to turn a part or an FEM result into a PNG picture plus a coloured GLB (faces by kind, DFM findings, named face groups, stress or displacement map) with geometry_make_part, geometry_view, fem_static and fem_view. Load eng-toolkit first.
---

# 3D view workflow (eng-toolkit)

Load `eng-toolkit` first. A picture presents numbers, it is not a result: the colours are the toolkit's coding and everything is unreviewed. Most people cannot open a GLB, so `geometry_view` and `fem_view` write a **PNG picture** next to the GLB by default (`preview="png"`). The PNG is what you hand over; never draw a picture yourself with matplotlib or a script.

## 1. Pin down the problem

What must the user SEE: the shape, the surface kind of each face, where manufacturability rules complain, a named face group, or where a loaded part is stressed or moves? For FEM ask for E, nu, supports, loads and the field.

## 2. Choose the tool

| Goal | Tool |
|---|---|
| Part from a description (volume, area, mass, optional GLB) | `geometry_make_part` (`formats="glb"`, `density`); one-colour GLB, no picture: run `geometry_view` on its STEP |
| Existing STEP/IGES/BREP as PNG and coloured GLB | `geometry_view`: `color_by` `face_kind`, `dfm`, `highlight` or `none` |
| GLB/STL/STEP conversion with a loss report | `geometry_convert` (an `.stl` goes only to `stl`/`glb`) |
| Stress or displacement map (PNG with bar, GLB) | `fem_mesh` (optional) -> `fem_static` -> `fem_view` |

Colouring: `face_kind` (a hole is a cylinder); `dfm` after a `geometry_check_dfm` review with the same `process`, `tool_axis`, `limits` (near-tangent kinks are not coloured; `tool_radius` is a RADIUS, half the cutter diameter; heuristic findings are warn); `highlight` for faces named by the user or a message (ids `F<n>` as in `geometry_measure`, valid for that file only): `highlight="F3,F7"` is ONE colour, and **named groups get one colour each in one picture**: `highlight="bends: F5,F12; holes: F3-F4; ribs: F14-F19"` (up to 8 groups, a face in one group only; colours in `data.highlight_groups` and the legend). Never recolour a GLB with a script.

`preview`: `png` (default; `geometry_view`: isometric view plus small front, top and right views, legend, size in mm; `fem_view`: one large view), `iso` (`geometry_view`: one view), `none` (GLB only).

## 3. Geometry inputs

- Build parts with gravity along -z (vertical = z; sketch a bracket's leg on a plane that contains z): every viewer shows Z up, so a part built along y lies on its side.
- `geometry_make_part(spec=<JSON string>, name, formats, density, mesh_deflection)`: `{"params":{...},"features":[...]}`, lengths as unit strings, first feature a body, later `hole`, `fillet` (`"edges":"|z & convex"`), `pattern`. Check the volume by a closed form. Results: mm^3, mm^2, kg.
- `geometry_view(model_path, color_by, ...)`: a closed solid only. `mesh_deflection` (`0.001` to `100 mm`, default `0.01 mm`) sets triangles and time (coarser to view).

## 4. Geometry refusals are answers

- Over 600,000 triangles: `input.out_of_range` at `/mesh_deflection` with `details.suggested_mesh_deflection_mm`. Open shell: `geometry.not_a_solid`. `.stl`: `input.unsupported_format`. Over 2500 faces with `dfm`: `input.too_large`. Unknown face id: `geometry.unknown_entity`. `geometry.time_budget_exceeded`: raise `max_seconds` once (max 600).

## 5. FEM chain

`geometry_make_part` STEP (one solid) -> `fem_mesh(model_path, element_size)` (optional) -> `fem_static(model_path, element_size, youngs_modulus, poisson_ratio, supports, loads)` -> `fem_view(result_dir=<data.job_dir>, field=...)`. Needs Gmsh and CalculiX.

- Selectors are planes (curved faces cannot be selected): supports `x = 0 mm` or `x = 0 mm : ux; point(0 mm, 5 mm, 0 mm) : uy`; loads `force (0 N, 0 N, -1 kN) on x = 100 mm` or `pressure 5 MPa on normal +z at z = 10 mm` (positive pushes in). A downward load has negative `Fz`.
- Refusals: `fem.not_constrained` (names the free motions), `fem.empty_selection`, `fem.overlapping_selection`, `input.too_large` (over 60,000 elements; the message gives a size).
- A fully fixed face stops the Poisson contraction (warning): for tension constrain `ux` plus point supports.
- **Convergence**: two or three sizes, each HALF the previous (4 -> 2 -> 1 mm), with the location of the maximum (`data.max_von_mises_at_mm`). Cantilever 100x10x10 mm, 1 kN: tip uz -1.894 ... -1.906 mm from 10 to 1.25 mm (beam theory 1.9048), max von Mises 591, 600, 594, 724 MPa. A maximum that moves or sits on a clamped face is a **singularity**: say it grows with refinement and quote the stress at the feature you care about. `max_displacement` is a magnitude; the deflection is `values.max_displacement_ux/uy/uz` (signed range in `data.max_displacement_components_mm`), no CSV needed.

## 6. Reading the colour map (`fem_view`)

- `field`: `von_mises` and `displacement` are magnitudes (sequential map); `ux uy uz s_xx ... principal_min` are signed (diverging, **light grey = 0**, blue negative, red positive). Signed ranges are always symmetric. `range="0 MPa, 250 MPa"` clips: warnings count saturated nodes.
- `data.range` has `low`, `high`, unit, `symmetric_about_zero`, `widened`. `values.field_min/field_max` are over surface nodes (a warning says when an interior value is larger). `deformation_scale="auto"` draws the largest displacement at 5 % of the diagonal: state `deformation_factor` when above 1.

## 7. Hand it over (the PNG first)

1. **Give the PNG path first** (`artifacts[].path`; `data.preview.file` names it: `<name>.png`, default `view.png`; `fem_view.png`). It can be pasted into a mail or chat as it is. Look at it before describing it. If `data.preview.file` is null, say so and give the GLB.
2. **Describe it in words**: shape and size (`data.preview.model_size_mm`); which colour means what and how many faces (`data.preview.groups`); where they are (face ids and what they are); for FEM the field, unit, range, the extremes with node and place, and that light grey is zero. The large view looks from +x, -y, +z (Z up); a face it hides is in the second panel or named in a warning.
3. **Mention the GLB** once: same colours, face ids in its extras; open with `cad_show` (absolute path) if it exists, else any glTF viewer.
4. **Say what a colour means**: a `dfm` face was *flagged by a rule of thumb*, not a verdict, and grey is not a checked face; `face_kind` is the CAD kernel's classification; a stress colour is a nodal value of a linear model, not a failure prediction.
5. For a mail with only the confirmed problems, make a second picture with `color_by="highlight"` and say which colour is which.

## 8. Report

PNG path first, then GLB, numbers with units, the legend in words. Inputs: spec or file, mesh sizes, material, supports, loads. Checks: closed-form volume, beam-theory deflection, convergence table, equilibrium residual, warnings. Limits: colours are a coding; DFM rules are rules of thumb; FEM is linear static with nodal surface values; no yield, safety factor or fatigue judgement; nothing is approved.
