---
name: eng-3d-view
description: Use to build a 3D part from a text spec, or to turn a part or a finite element result into a coloured GLB the user can look at (faces by kind, DFM findings, highlighted faces, or a stress or displacement colour map), with the eng-toolkit geometry_make_part, geometry_view, geometry_convert and fem_mesh, fem_static, fem_view tools. Covers colour choice, reading the legend, mesh deflection and the triangle cap, the FEM chain and its caveats, showing the file, and saying what the picture does not prove. Load eng-toolkit first.
---

# 3D view workflow (eng-toolkit)

Load the `eng-toolkit` skill first for the unit rules and report format. A picture presents numbers, it is not a result: the colours are the toolkit's coding and everything is unreviewed.

## 1. Pin down the problem

What must the user SEE: the shape, the surface kind of each face, where manufacturability rules complain, one named face, or where a loaded part is stressed or moves? For FEM ask for E, nu, supports, loads (and their faces) and the field (stress or displacement).

## 2. Choose the tool

| Goal | Tool |
|---|---|
| Part from a description (volume, area, mass, optional GLB) | `geometry_make_part` (`formats="glb"`, `density`); its GLB is single-colour |
| Existing STEP/IGES/BREP as a coloured GLB | `geometry_view`: `color_by` `face_kind`, `dfm`, `highlight` or `none` |
| Plain GLB/STL/STEP conversion with a loss report | `geometry_convert` (an `.stl` can only go to `stl`/`glb`) |
| Stress or displacement map | `fem_mesh` (optional) -> `fem_static` -> `fem_view` |

Colouring: `face_kind` to confirm what the geometry is (a hole is a cylinder); `dfm` after a `geometry_check_dfm` style review (same `process`, `tool_axis`, `limits`); `highlight` with `highlight="F3,F7"` for faces a message or the user named (ids `F<n>` as in `geometry_measure`, valid only for that file's bytes).

## 3. Geometry inputs

- `geometry_make_part(spec=<JSON string>, name, formats, density, mesh_deflection)`: `{"params":{...},"features":[...]}`, every length a unit string, first feature a body (`box`, `cylinder`, ...), later `hole`, `fillet` (`"edges":"|z & convex"`), `pattern`. Check the volume against a closed form (box minus hole `pi d^2 t/4`). Results: mm^3, mm^2, kg; file paths in `artifacts[].path`.
- `geometry_view(model_path, color_by, ...)`: a closed solid only. `dfm` also takes `process` (`cnc_milling`/`sheet_metal`), `tool_axis`, `limits` (JSON with units); otherwise ignored (warning).
- `mesh_deflection` (chord error, `0.001 mm` to `100 mm`, default `0.01 mm`) sets triangles, file size and time: for viewing, a coarser value is enough.

## 4. Geometry refusals are answers

- Over **600,000 triangles** (estimated before meshing): `input.out_of_range` at `/mesh_deflection`; the message gives the estimate, the cap and a coarser `details.suggested_mesh_deflection_mm`. Example: a 120 mm sphere at `0.001 mm`: 622,581 estimated, suggests `0.0012 mm`. Use the suggestion or a larger value.
- Not a closed solid (open shell, loose faces, surface-only file): `geometry.not_a_solid`. `geometry_measure` shows what is open.
- `.stl`: `input.unsupported_format` (a mesh has no faces to colour). Coordinates beyond 1,000,000 mm (1 km): refused. More than 2500 faces with `dfm`: `input.too_large`. Unknown face id: `geometry.unknown_entity`. `geometry.time_budget_exceeded`: raise `max_seconds` once (max 600).

## 5. FEM chain

Order: geometry file (`geometry_make_part` STEP, one solid) -> `fem_mesh(model_path, element_size)` (optional: quality, mesh vs exact volume) -> `fem_static(model_path, element_size, youngs_modulus, poisson_ratio, supports, loads)` -> `fem_view(result_dir=<fem_static data.job_dir>, field=...)`. Needs Gmsh and CalculiX (`toolkit_doctor`).

- Selectors are planes: supports `x = 0 mm` or `x = 0 mm : ux; point(0 mm, 5 mm, 0 mm) : uy`; loads `force (0 N, 0 N, -1 kN) on x = 100 mm` (spread by area) or `pressure 5 MPa on normal +z at z = 10 mm` (positive pushes in). Curved faces cannot be selected. A downward load has negative `Fz`.
- Refusals to explain: `fem.not_constrained` (names the free motions; add supports), `fem.empty_selection`, `fem.overlapping_selection` (support face also loaded), `input.too_large` (over 60,000 elements or `element_size` under diagonal/1000; the message gives a size that fits), `units.bare_number`.
- A fully fixed face stops the Poisson contraction (warning): stress at its corner is mesh-dependent. For tension cases constrain `ux` on the face plus point supports.
- **Convergence**: run two or three sizes (halve `element_size`) and compare. Cantilever 100x10x10 mm, 1 kN, E 210 GPa, clamped: tip uz -1.894, -1.901, -1.904, -1.906 mm at 10, 5, 2.5, 1.25 mm against beam theory 1.9048; but max von Mises 591, 600, 594, 724 MPa does not converge (clamped-corner singularity). `max_displacement` is a magnitude (1.9098 there), not the deflection: read the component from the `uz` field range.

## 6. Reading the colour map (`fem_view`)

- `field`: `von_mises` and `displacement` are magnitudes (sequential map, dark blue to pale yellow); `ux uy uz s_xx ... principal_min` are signed (diverging blue-grey-red, **light grey = 0**, blue negative, red positive). A diverging map forced on `von_mises` wastes half the scale.
- `data.range`: `low`, `high`, unit, `symmetric_about_zero`, `widened`, `clamped_below/above`. Signed ranges are always made symmetric, also an explicit `range` (`-0.1 mm, 3 mm` becomes -3..3 mm). `range="0 MPa, 250 MPa"` clips: warnings give how many nodes saturate (they take the end colour). A range in the wrong dimension is `units.dimension_mismatch`.
- `values.field_min/field_max` are over surface nodes; a warning says when an interior value is larger. `data.fem_static_comparison` ties the maximum to `fem_static`. `legend.svg` is the colour bar.
- `deformation_scale="auto"` draws the largest displacement at 5 % of the diagonal (0 = undeformed, up to 10000): state `deformation_factor` when above 1, the shape is a caricature.

## 7. Show it

Paths come from `artifacts[].path` (`view.glb`, `fem_view.glb`, `legend.svg`). Open the GLB with the `cad_show` tool (absolute path) when it exists, else any glTF viewer (browser viewer, Windows 3D Viewer, Blender; not tested by the toolkit). GLB: metres, +Y up (model `(x, y, z) mm` -> `(x, z, -y)/1000`).

## 8. Report

Result: file paths, the numbers with units, the legend in words (colour -> meaning, light grey = 0). Inputs: spec or file, mesh sizes, material, supports, loads. Checks: closed-form volume, beam-theory deflection, convergence table, equilibrium residual, warnings. Limits: colours are a coding, a neutral DFM face is not a checked face, DFM rules are rules of thumb; FEM is linear static with nodal surface values (interior not shown, colours between nodes interpolated, peaks at point loads, supports and sharp corners are not failure predictions); no yield, safety factor or fatigue judgement; nothing is approved.
