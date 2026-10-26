---
name: eng-3d-view
description: Use to build a 3D part from a text spec, or to turn a part or an FEM result into a PNG picture plus a coloured GLB (faces by kind, DFM findings, named face groups, stress or displacement map) with geometry_make_part, geometry_view, geometry_convert, fem_mesh, fem_static and fem_view. Load eng-toolkit first.
---

# 3D view workflow (eng-toolkit)

Load the `eng-toolkit` skill first for the unit rules and report format. A picture presents numbers, it is not a result: the colours are the toolkit's coding and everything is unreviewed. Most people cannot open a GLB, so `geometry_view` and `fem_view` write a **PNG picture** next to the GLB by default (`preview="png"`). The PNG is what you hand over; never try to draw a picture yourself with matplotlib or a script.

## 1. Pin down the problem

What must the user SEE: the shape, the surface kind of each face, where manufacturability rules complain, one named face, or where a loaded part is stressed or moves? For FEM ask for E, nu, supports, loads (and their faces) and the field (stress or displacement).

## 2. Choose the tool

| Goal | Tool |
|---|---|
| Part from a description (volume, area, mass, optional GLB) | `geometry_make_part` (`formats="glb"`, `density`); its GLB is single-colour and it makes no picture: run `geometry_view` on its STEP for colours and the PNG |
| Existing STEP/IGES/BREP as a picture (PNG, default) and a coloured GLB | `geometry_view`: `color_by` `face_kind`, `dfm`, `highlight` or `none` |
| Plain GLB/STL/STEP conversion with a loss report | `geometry_convert` (an `.stl` can only go to `stl`/`glb`) |
| Stress or displacement map (PNG with colour bar, and GLB) | `fem_mesh` (optional) -> `fem_static` -> `fem_view` |

Colouring: `face_kind` to confirm what the geometry is (a hole is a cylinder); `dfm` after a `geometry_check_dfm` style review (same `process`, `tool_axis`, `limits`); `highlight` for faces a message or the user named (ids `F<n>` as in `geometry_measure`, valid only for that file's bytes): `highlight="F3,F7"` is one colour, and **named groups get one colour each in one picture**, e.g. `highlight="bends: F5,F12; holes: F3-F4; ribs: F14-F19"` (up to 8 groups, ranges `F14-F19`, a face in one group only; the colours are in `data.highlight_groups` and in the legend). Never recolour a GLB with a script.

`preview`: `png` (default: isometric view plus small front, top and right views, legend, size in mm), `iso` (one view), `none` (GLB only, when the user wants only the viewer file).

## 3. Geometry inputs

- Build parts with gravity along -z (vertical = z, sketch on plane `xz`): the GLB maps model z to +Y up, so the part then stands upright.
- `geometry_make_part(spec=<JSON string>, name, formats, density, mesh_deflection)`: `{"params":{...},"features":[...]}`, every length a unit string, first feature a body (`box`, `cylinder`, ...), later `hole`, `fillet` (`"edges":"|z & convex"`), `pattern`. Check the volume against a closed form (box minus hole `pi d^2 t/4`). Results: mm^3, mm^2, kg; file paths in `artifacts[].path`.
- Build parts upright: gravity along -z, vertical = z (sketch a bracket's leg on a plane that contains z), because the picture and every viewer show Z up. A part built with its leg along y lies on its side.
- `geometry_view(model_path, color_by, ...)`: a closed solid only. `dfm` also takes `process` (`cnc_milling`/`sheet_metal`), `tool_axis`, `limits` (JSON with units); otherwise ignored (warning).
- `mesh_deflection` (chord error, `0.001 mm` to `100 mm`, default `0.01 mm`) sets triangles, file size and time: for viewing, a coarser value is enough.

## 4. Geometry refusals are answers

- Over **600,000 triangles** (estimated before meshing): `input.out_of_range` at `/mesh_deflection`, with a coarser `details.suggested_mesh_deflection_mm`: use it or a larger value.
- Not a closed solid (open shell, loose faces, surface-only file): `geometry.not_a_solid`. `geometry_measure` shows what is open.
- `.stl`: `input.unsupported_format` (no faces to colour). Beyond 1 km: refused. More than 2500 faces with `dfm`: `input.too_large`. Unknown face id: `geometry.unknown_entity`. `geometry.time_budget_exceeded`: raise `max_seconds` once (max 600).

## 5. FEM chain

Order: geometry file (`geometry_make_part` STEP, one solid) -> `fem_mesh(model_path, element_size)` (optional: quality, mesh vs exact volume) -> `fem_static(model_path, element_size, youngs_modulus, poisson_ratio, supports, loads)` -> `fem_view(result_dir=<fem_static data.job_dir>, field=...)`. Needs Gmsh and CalculiX (`toolkit_doctor`).

- Selectors are planes: supports `x = 0 mm` or `x = 0 mm : ux; point(0 mm, 5 mm, 0 mm) : uy`; loads `force (0 N, 0 N, -1 kN) on x = 100 mm` (spread by area) or `pressure 5 MPa on normal +z at z = 10 mm` (positive pushes in). Curved faces cannot be selected. A downward load has negative `Fz`.
- Refusals to explain: `fem.not_constrained` (names the free motions; add supports), `fem.empty_selection`, `fem.overlapping_selection` (support face also loaded), `input.too_large` (over 60,000 elements or `element_size` under diagonal/1000; the message gives a size that fits), `units.bare_number`.
- A fully fixed face stops the Poisson contraction (warning): stress at its corner is mesh-dependent; for tension cases constrain `ux` on the face plus point supports.
- **Convergence**: run two or three sizes, each HALF the previous (4 -> 2 -> 1 mm, not 4 -> 3 -> 2.5), and give the location of the maximum for each (`data.max_von_mises_at_mm`). Cantilever 100x10x10 mm, 1 kN, clamped: tip uz -1.894, -1.901, -1.904, -1.906 mm at 10, 5, 2.5, 1.25 mm against beam theory 1.9048, but max von Mises 591, 600, 594, 724 MPa does not converge. A maximum that moves or sits on a clamped face is a **singularity**: say it grows with refinement and quote the stress at the feature you care about (a fillet, a hole). `max_displacement` is a magnitude, not the deflection: read the component (`data.max_displacement_components_mm` or the `uz` range).

## 6. Reading the colour map (`fem_view`)

- `field`: `von_mises` and `displacement` are magnitudes (sequential map, dark blue to pale yellow); `ux uy uz s_xx ... principal_min` are signed (diverging blue-grey-red, **light grey = 0**, blue negative, red positive). A diverging map forced on `von_mises` wastes half the scale.
- `data.range`: `low`, `high`, unit, `symmetric_about_zero`, `widened`, `clamped_below/above`. Signed ranges are always made symmetric, also an explicit `range` (`-0.1 mm, 3 mm` becomes -3..3 mm). `range="0 MPa, 250 MPa"` clips: warnings give how many nodes saturate (they take the end colour). A range in the wrong dimension is `units.dimension_mismatch`.
- `values.field_min/field_max` are over surface nodes; a warning says when an interior value is larger. `data.fem_static_comparison` ties the maximum to `fem_static`. `legend.svg` is the colour bar; `fem_view.png` shows the same bar with unit, range, min and max, a ring on the largest value (and on the smallest of a signed field), and a second view when the largest value sits on a face that points away.
- `deformation_scale="auto"` draws the largest displacement at 5 % of the diagonal (0 = undeformed, up to 10000): state `deformation_factor` when above 1, the shape is a caricature.

## 7. Hand it over (the PNG first)

The people you work for usually cannot open a GLB, and you cannot show them anything but a file path. So:

1. **Give the PNG path first**, from `artifacts[].path` (`<name>.png` for `geometry_view`, `fem_view.png` for `fem_view`; `data.preview.file` names it). Say it can be pasted into a mail or a chat as it is. If you can read images yourself (the `Read` tool), look at it before you describe it. Do not write a script, call internal modules or ask for permissions to make a picture: the tool already made it. If `data.preview.file` is null (a warning says why), say so and give the GLB.
2. **Describe the picture in words**, from `data` and the legend, so that the person understands it before opening it: the shape and its size (`data.preview.model_size_mm`, the picture's `Size X ... mm` line); which colour stands for what and how many faces each has (`data.preview.groups`, the legend); where the coloured faces are (name the face ids and what they are: the bore, the fillets, the fin faces); and for FEM the field, the unit, the range of the colour bar, the minimum and maximum with their node and place, and that **light grey is zero** for a signed field. The large view looks from the front right above (+x, -y, +z, Z up); the small ones are front (from -y), top and right (from +x). A marker or a face the large view hides is in the second panel or named in a warning.
3. **Mention the GLB** once, for people who have a 3D viewer: the same colours, orbit and zoom, the face ids and the legend in its extras. Open it with the `cad_show` tool (absolute path) when that tool exists, else any glTF viewer (a browser viewer, Windows 3D Viewer, Blender; not tested by the toolkit). GLB: metres, +Y up (model `(x, y, z) mm` -> `(x, z, -y)/1000`). `view.json` and `legend.svg` are for tools, not for the person.
4. **Say what a colour means**: a face coloured by `dfm` was *flagged by a rule of thumb* of this toolkit, it is **not a verdict** that the part cannot be made or is wrong, and a grey face is not a checked face; `face_kind` colours say how the CAD kernel classifies a surface, not that anything is wrong; a stress colour is a nodal value of a linear model, not a failure prediction.
5. If the person wants only some faces in colour for a mail (the confirmed problems, not every rule hit), make a second picture with `color_by="highlight"` and only those faces (one group each if there are several kinds), and say which colour is which.

## 8. Report

Result: the PNG path first, then the GLB path, the numbers with units, the legend in words (colour -> meaning, light grey = 0). Inputs: spec or file, mesh sizes, material, supports, loads. Checks: closed-form volume, beam-theory deflection, convergence table, equilibrium residual, warnings. Limits: colours are a coding, a neutral DFM face is not a checked face, DFM rules are rules of thumb; FEM is linear static with nodal surface values (interior not shown, colours between nodes interpolated, peaks at point loads, supports and sharp corners are not failure predictions); no yield, safety factor or fatigue judgement; nothing is approved.
