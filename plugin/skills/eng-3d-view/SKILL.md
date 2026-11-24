---
name: eng-3d-view
description: Use FIRST for "build this part and show me", "colour the problem faces", "stress picture" or "how much does it deflect" of a bracket or STEP part (geometry_make_part, geometry_view, fem_static, fem_view) - PNG plus coloured GLB.
---

# 3D view workflow (eng-toolkit)

Load `eng-toolkit` too. A picture presents numbers, it is not a result: the colours are the toolkit's coding; everything is unreviewed. Most people cannot open a GLB, so `geometry_view` and `fem_view` write a **PNG picture** next to it (`preview="png"`, default), shown in the reply. Give its path too; never draw a picture with a script.

## 1. Pin down the problem

What must the user SEE: the shape, face kinds, where manufacturability rules complain, a named face group, or where a loaded part is stressed or moves? For FEM ask for E, nu, supports, loads, the field.

## 2. Choose the tool

| Goal | Tool |
|---|---|
| Part from a description (volume, area, mass, GLB) | `geometry_make_part` (`formats="glb"`, `density`), then `geometry_view` on its STEP |
| Existing STEP/IGES/BREP as PNG and coloured GLB | `geometry_view`: `color_by` `face_kind`, `dfm`, `highlight` or `none` |
| GLB/STL/STEP conversion with a loss report | `geometry_convert` (`.stl` goes to `stl`/`glb` only) |
| Stress or displacement map (PNG with bar, GLB) | `fem_mesh` (optional) -> `fem_static` -> `fem_view` |

Colouring: `face_kind`; `dfm` after a `geometry_check_dfm` review with the same `process`, `tool_axis`, `limits` (near-tangent kinks are not coloured; `tool_radius` is a RADIUS; heuristic findings are warn); every finding gets a ring and a label `D<n>` (`data.preview.markers`); `highlight` for faces named by the user (ids `F<n>` as in `geometry_measure`, valid for that file only): `highlight="F3,F7"` is ONE colour; **named groups get one colour each in one picture**: `highlight="bends: F5,F12; holes: F3-F4; ribs: F14-F19"` (up to 8 groups, a face in one group only; colours in `data.highlight_groups` and the legend). `focus` adds a close-up panel of faces (`"F3,F7"`), of a group name (`"bends"`) or of a finding (`"D1"`). Never recolour a GLB with a script.

`preview`: `png` (default; `geometry_view`: isometric plus small front, top, right views, legend, size; `fem_view`: one large view), `iso`, `none` (GLB only).

## 3. Geometry inputs

- **Build parts with Z up** (gravity along -z; sketch a bracket's leg on a plane that contains z, `"plane":"xz"`): every viewer shows Z up, so a part built along y lies on its side. Example: an angle bracket (t 5, width 50, height 70, arm 45 mm): `extrude` on `"plane":"xz"` of the L polygon `[[0,0],[A,0],[A,t],[t,t],[t,H],[0,H]]` (unit strings), `distance` = width, then `fillet` `"|y & concave"` 5 mm: volume 27768 mm^3 (closed form 27500 + 268). **Look at the PNG: is the part upright, which end is the support?**
- `geometry_make_part(spec=<JSON string>, ...)`: `{"params":{...},"features":[...]}`, lengths as unit strings, first feature a body, later `hole`, `fillet`, `pattern`. Check the volume by a closed form.
- `geometry_view`: a closed solid only; `mesh_deflection` (default `0.01 mm`) sets triangles and time.

## 4. Geometry refusals are answers

- Over 600,000 triangles: `input.out_of_range` at `/mesh_deflection` (`details.suggested_mesh_deflection_mm`). Open shell: `geometry.not_a_solid`. `.stl`: `input.unsupported_format`. Over 2500 faces with `dfm`: `input.too_large`. `geometry.time_budget_exceeded`: raise `max_seconds` once.

## 5. FEM chain

`geometry_make_part` STEP (one solid) -> `fem_mesh` (optional) -> `fem_static(model_path, element_size, youngs_modulus, poisson_ratio, supports, loads)` -> `fem_view(result_dir=<data.job_dir>, field=...)`. Needs Gmsh and CalculiX.

- Selectors are planes (no curved faces): supports `x = 0 mm` or `x = 0 mm : ux; point(0 mm, 5 mm, 0 mm) : uy`; loads `force (0 N, 0 N, -1 kN) on x = 100 mm` or `pressure 5 MPa on normal +z at z = 10 mm` (positive pushes in). A downward load has negative `Fz`.
- Refusals: `fem.not_constrained` (names the free motions), `fem.empty_selection`, `fem.overlapping_selection`, `input.too_large` (over 60,000 elements; the estimate is 0.87 to 1.26 times the real count and the message names the finest size that passes, 0.919 mm below).
- A fully fixed face stops the Poisson contraction (warned); for tension use `ux` plus point supports.
- **Convergence**: two or three sizes, each HALF the previous (4 -> 2 -> 1 mm), with the location of the maximum (`data.max_von_mises_at_mm`). If the element cap prevents halving, say the peak is not shown to be converged. Cantilever 100x10x10 mm, 1 kN (beam theory 1.905 mm): tip uz 1.894, 1.905, 1.906 mm at 10, 2.5, 1.25 mm; max von Mises 591, 594, 724 MPa, 809 MPa at 0.95 mm (55,294 elements), the last two on the clamped edge. A maximum that moves or sits on a clamped face is a **singularity**: it grows with refinement; quote the stress at the feature you care about. `max_displacement` is a magnitude (`data.displacement_note` says so); the deflection is `values.max_displacement_ux/uy/uz`. Report every other component too (this tip also moves 0.14 mm in x).

## 6. Reading the colour map (`fem_view`)

- `field`: `von_mises` and `displacement` are magnitudes (sequential map); `ux uy uz s_xx ... principal_min` are signed (diverging, **light grey = 0**, blue negative, red positive). Signed ranges are symmetric. `range="0 MPa, 250 MPa"` clips (a warning counts saturated nodes).
- `data.range` has `low`, `high`, unit, `widened`; `values.field_min/field_max` are over surface nodes. `deformation_scale="auto"` draws the largest displacement at 5 % of the diagonal: state `deformation_factor` when above 1.

## 7. Hand it over (the PNG first)

1. **Give the PNG path first** (`artifacts[].path`; `data.preview.file`: `<name>.png`, `view.png`, `fem_view.png`), then the GLB; the PNG already carries the legend and can be pasted as it is. The picture also appears in the reply: look at it before describing it, and still give the path. If `data.preview.file` is null, say so and give the GLB.
2. **Describe it in words**: shape and size (`data.preview.model_size_mm`); which colour means what and how many faces (`data.preview.groups`), with ids; for FEM the field, unit, range, extremes with place, light grey = zero. The large view looks from +x, -y, +z.
3. **Mention the GLB** once: same colours, face ids in its extras; `cad_show` (absolute path) opens it.
4. **Say what a colour means**: a `dfm` face was *flagged by a rule of thumb*, not a verdict, and grey is not a checked face; a stress colour is a nodal value of a linear model, not a failure prediction. For only the confirmed problems make a second picture with `color_by="highlight"`.

## 8. Report

PNG path first, then GLB, numbers with units, the legend in words. Inputs: spec or file, mesh sizes, material, supports, loads. Checks: closed-form volume, beam-theory deflection, convergence table, warnings. Limits: colours are a coding; DFM rules are rules of thumb; FEM is linear static, no yield or fatigue judgement.
