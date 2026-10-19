# geometry_convert (slice S8a)

`geometry_convert` converts a 3D model file and says what the conversion lost. It is slice S8a of [the geometry and drawing design](../geometry-drawing/design.md) (S8b, `drawing_convert`, is a separate tool in the drawing domain).

## Formats

| Input | Output | How |
| --- | --- | --- |
| `.step` `.stp` `.iges` `.igs` `.brep` | `step` | read by `brep.read_solid`, written by `brep.write_step` (XCAF writer, fixed header, AP214) |
| same | `brep` | `brep.write_brep` (OpenCASCADE text, millimetre numbers, no unit tag) |
| same | `stl` | `brep.triangulate` at `mesh_deflection`, then `brep.write_mesh` (binary STL, millimetres, +Z up) |
| same | `glb` | same mesh, written as GLB: metres, +Y up |
| `.stl` (binary or ASCII) | `stl`, `glb` | the triangles are read by a NumPy parser in `convert.py` and copied; no OpenCASCADE involved |
| `.stl` | `step`, `brep` | **refused**, `geometry.needs_brep`: a mesh has no exact surfaces, and a solid built from triangles (one planar face per triangle) would be a different object that no CAD system treats as the part |

Nothing in `brep/` was added or edited: the existing reader, writers and `weld`/`mesh_topology` cover it. The STL reader lives in the domain because it needs no OCP, so the ownership test is unaffected.

The tool takes four string parameters: `input_path`, `output_format`, `mesh_deflection` (default `0.01 mm`, 0.001 mm to 100 mm), `name` (output stem and STEP product name; empty = the input's stem with characters outside `[A-Za-z0-9_-]` replaced by `_`). It adds one tool to the geometry domain (now 4 of 6).

## Reading and writing rules

* The input goes through `JobHandle.input_file` (opened once, hashed, copied into the job directory; 100 MB cap). The output is written with `job.write_bytes` into the job directory `ws/<job>/`, which is new for every call, so the input can never be overwritten even when output and input have the same stem and format (tested).
* Every number that describes a mesh output (`volume_out`, `surface_area_out`, `triangle_count`, the GLB accessor min/max) is **read back from the bytes of the file that is returned**, with the tool's own STL and GLB parsers. A B-rep output is read back with `read_solid` and its volume must match within 1e-6 or the call fails with `geometry.readback_mismatch`, as `geometry_make_part` does.
* The tests use a second, independent set of readers (`tests/convert_readers.py`: plain `struct` and `json`) so the writer and the tool's read-back are not the only witnesses.

## Contract

Primary value `volume_out` (`millimeter ** 3`). Values: `volume_in`, `volume_out`, `volume_relative_change` = (out − in) / in (dimensionless, signed), `surface_area_in`, `surface_area_out`, and for mesh outputs `triangle_count`.

When there is no volume (a shell or loose faces in a B-rep file, an STL that is not a closed, consistently oriented surface) the volume values are omitted, `surface_area_out` is the primary value, and a warning says why. An empty file, a file with no readable faces, and an inside-out STL (closed but negative volume) are refused.

`data` holds `input` and `output` descriptions (kind, solids/faces or triangles, closure, `declared_length_unit` for STEP, file name and sha256), `losses`, and for B-rep to mesh `output.deflection_mm`, `angular_deflection_rad` (0.5, fixed in `brep.triangulate`), `volume_change_bound_mm3` and `float32_coordinate_error_bound_mm`. The converted file is an artifact with its sha256.

## The loss report (`data.losses`)

A list of `{kind, detail}` entries, only those that apply:

| kind | when | says |
| --- | --- | --- |
| `exact_surfaces_to_triangles` | B-rep to STL/GLB | surfaces become flat triangles within the requested chord error and 0.5 rad; convex curved faces lose volume, concave ones gain it |
| `face_structure` | B-rep to STL/GLB | faces merge into one triangle soup/mesh; face ids and holes are gone |
| `coordinate_precision` | any mesh output | coordinates are 32-bit floats, with the error bound for this model's size (largest coordinate × 2⁻²⁴) |
| `stl_format` | STL out | millimetres by convention, normals recomputed from the winding, no names or colours |
| `glb_axes_and_unit` | GLB out | the conversion `(x, y, z) → (x, z, −y) / 1000` and its meaning, stated and applied |
| `glb_content` | GLB out | one mesh, one node, welded vertices, positions only |
| `units` | B-rep in | the unit the STEP file declares (`inch`, `metre`, ...) and that it was converted to millimetres; BREP has no unit; IGES is converted by OpenCASCADE |
| `names_colours_structure` | B-rep in | names, colours, layers, PMI and the assembly tree are not read, so they are not in the output |
| `face_ids` | B-rep in | `geometry_measure` ids of the input do not apply to the output |
| `input_units`, `mesh_deflection_ignored` | STL in | the STL's unit is assumed to be millimetres; the triangles are copied, not re-meshed |
| `degenerate_triangles` | welding dropped some | how many |

## Hand-derived acceptance numbers (all in `tests/test_geometry_convert.py`)

* Box 100 × 50 × 20 mm: volume 100·50·20 = 100000 mm³, area 2(5000 + 2000 + 1000) = 16000 mm², 12 triangles. The STL volume read by the independent reader is 100000 (all coordinates are exact in float32). Through GLB it is 100000 ± 7.5e-9 relative, because 0.1, 0.05 and 0.02 are not exact in binary: that error is real and is reported as `volume_relative_change`.
* Cylinder Ø20 × 30 (the design's example): V = π·10²·30 = 9424.778 mm³. The tessellation at 0.01 mm has 396 triangles; with 2n wall triangles and n−2 on each cap, n = (396 + 4)/4 = 100 rim nodes. An inscribed 100-gon has area ½·100·10²·sin(2π/100) = 313.9526 mm², so the mesh volume is 9418.578 mm³ (measured 9418.57795 through float32 STL), 6.2 mm³ below the true value. The bound: the lost region is within the chord error of the boundary, so it is at most perimeter × deflection × height = 2π·10·0.01·30 = **18.85 mm³** (0.6283185 × 30 = 18.84956). The sagitta of the polygon, 10(1 − cos(π/100)) = 0.00493 mm, is below 0.01 mm. The test repeats this at 0.5, 0.05, 0.01 and 0.002 mm with the bound recomputed each time.
* GLB: the model box [0,100]×[0,50]×[0,20] mm maps to X [0, 0.1], Y [0, 0.02], Z [−0.05, 0] m: accessor max `[0.1, 0.02, 0]`, min `[0, 0, −0.05]`. A 100 mm edge along model +Z reads 0.1 on glTF +Y (tested with a 20 × 30 × 100 box: max Y 0.1, max X 0.02, min Z −0.03). The mapping is a proper rotation (determinant +1), so outward triangles stay outward: the GLB volume read by the independent reader is +100000 mm³.
* Inches: a 25.4 mm cube written by OpenCASCADE with `write.step.unit = INCH` has edge 1 in its CARTESIAN_POINTs; converted, volume 25.4³ = 16387.064 mm³ and the output file declares `.MILLI.,.METRE.` with coordinates 25.4.
* Round trip STEP → BREP → STEP → BREP of the probe part (box with a Ø10 through hole and four R2 fillets): V = 100000 − 25π·20 − 4(4 − π)·20 = 98360.531085 mm³, relative error under 1e-9 at every step.

## Magnitudes

Lengths must stay within ±1e6 mm (1 km) of the origin. STL coordinates beyond that, a B-rep whose bounding box is beyond that or not finite, and any mesh volume or area that is not finite give `input.out_of_range`; so do `mesh_deflection` values outside 0.001 to 100 mm, including ones that overflow only when converted to millimetres (`1e308 m`), and a deflection so fine for the model's size (diagonal / deflection > 5e6) that tessellating would take too long. A STEP file that places geometry at 1e150 mm is not read as such: OpenCASCADE drops the faces it cannot convert, so the call fails with `input.invalid` ("no faces could be read") rather than `out_of_range`; at 1e20 mm the geometry survives and `out_of_range` is returned. In every case the result is `ok=false` with no value; a test asserts that the serialised result contains no `NaN` or `Infinity`, directly and through a `fastmcp.Client` on the gateway.

## Decisions and alternatives

* **STL reading by hand, not `StlAPI_Reader`.** OpenCASCADE's reader returns a shape carrying a `Poly_Triangulation` that is not a B-rep, which would need new code in `brep/` and gives no way to count or validate triangles the way this tool reports them. Parsing is about 40 lines of NumPy, works without the `cad` extra, and rejects non-finite or huge coordinates before anything uses them. Binary versus ASCII is decided by file size (84 + 50 n), because many binary files start with `solid`.
* **No STL to STEP.** A mesh is not a solid; faceted STEP would have thousands of planar faces and no cylinders, so a downstream `geometry_measure` would report nonsense holes. Refused with a clear error; the design table already said this.
* **Shells convert.** The brief allowed it only if stated: a B-rep shell converts to every format with the warning "no closed solid" and `surface_area_out` as primary value.
* **Read back, don't trust.** `volume_out` of a mesh is the volume of what is in the file (including float32 rounding), not of what was meant to be written.
* **Units are fixed millimetres in the result.** The design table has no `output_unit` for this tool; the unit string on every value says `millimeter ** 3` etc.

## What was not done or not verified

* **Colours, names, layers, PMI, assemblies** are dropped, not converted (they are not read: `brep.read_solid` uses the plain STEP reader, not XCAF). Stated in the loss report.
* **IGES units** are converted by OpenCASCADE but not independently reported (only STEP's declared unit is parsed, from the file text, best effort).
* **The tessellation guarantee** is OpenCASCADE's, not ours: the tool checks after the fact and warns when the mesh volume differs from the exact volume by more than area × deflection (a looser, general bound than the cylinder's perimeter-based one), but it does not measure the true maximum chord error.
* **Open or non-manifold meshes** are written as they are; only closure is reported. STL welding uses a 1e-6 mm grid, and nodes closer than that are merged.
* **Time and memory** of tessellating a very large B-rep are bounded only by the size-per-deflection ratio, a 100 MB input cap and a 2 000 000 triangle cap checked after meshing; a model with a huge number of freeform faces can still take long inside OpenCASCADE.
* **Crash isolation:** an untrusted STEP is parsed in the server process, as for the other geometry tools (design open risk, not addressed here).
* **GLB has no normals**: a viewer computes flat or smooth shading itself.
* Not tried with a real `claude -p --mcp-config` session or other CAD systems' readers; checks are with OpenCASCADE and the hand-written parsers in the tests.
