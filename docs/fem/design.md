# fem: meshing with Gmsh and linear statics with CalculiX

Domain `fem` (`eng_toolkit/domains/fem/`): two tools, `fem_mesh` and `fem_static`. A solid CAD file goes in; a second-order tetrahedral mesh, or a linear static result (displacement, stress, reactions), comes out. Gmsh and CalculiX are programs the user installed; the toolkit starts them as separate processes through `eng_toolkit.external` (`docs/external/design.md`) and never imports, links or ships them. Tests: `tests/test_fem_selectors.py` (selector language, 61), `tests/test_fem_internals.py` (mesh arithmetic, rigid-body detection, deck, readers, 22), `tests/test_fem_real.py` (the real programs, 29, skipped when OCP, Gmsh or CalculiX is missing), `tests/test_fem_gateway.py` (through `fastmcp.Client`, 57; 14 of them need the programs).

## Pipeline

| Step | Who | What |
|---|---|---|
| 1 | `brep.read_solid` (the loader of `geometry_measure`) | STEP/IGES/BREP -> solid in millimetres. One closed solid with positive volume, or `input.invalid` / `input.unsupported` |
| 2 | `mesh.check_before_meshing` | element count estimate and size limits, **before** any program starts |
| 3 | `brep.write_brep`, then Gmsh | `gmsh part.brep -3 -order 2 -clmax <size> -format msh2 -nopopup -o part.msh` in `<job>/gmsh/`; no `.geo` is written, the only generated text is the size (`repr` of a validated float) |
| 4 | `mesh.read_msh` | strict reader of the ASCII file: nodes and 10-node tetrahedra (Gmsh type 11); Gmsh's midside nodes 9 and 10 are swapped to CalculiX's order |
| 5 | `mesh.check_and_measure` | quality, Jacobians, exact volume of the quadratic mesh; connected pieces; boundary facets with outward normals and curved areas |
| 6 | `selectors` + `tools.static` | supports and loads parsed to numbers; faces chosen on the boundary facets; loads turned into consistent nodal forces |
| 7 | `solver.free_rigid_motions` | rigid-body check before the solver |
| 8 | `solver.build_deck`, CalculiX | `ccx -i job` in `<job>/calculix/` on the deck the toolkit wrote |
| 9 | `solver.read_dat`, `read_frd_stress` | displacements and reactions from `.dat`, nodal stresses from `.frd` |
| 10 | `tools.static` | equilibrium check (fails the call), von Mises, extrema, `results.csv`, artifacts |

Files a program wrote are copied to the job directory's top level (`mesh.msh`, `model.inp`, `result.frd`, `result.dat`) so they are in `manifest.json` with their sha256; the program directories stay as written.

## Tool contracts

`fem_mesh(model_path, element_size, output_unit="mm")` -> primary `element_count`; `values`: `element_count`, `node_count`, `mesh_volume`, `exact_volume` (cubic `output_unit`), `volume_relative_difference`, `min_quality`, `mean_quality` (dimensionless); `data`: `quality` (min, mean, median, slivers, worst element, min Jacobian ratio), `estimated_elements`, `limits`, bounding box, versions, `conventions`; artifact `mesh.msh` (msh2 ASCII).

`fem_static(model_path, element_size, youngs_modulus, poisson_ratio, supports, loads, output_length_unit="mm", output_stress_unit="MPa")` -> primary `max_displacement`; `values`: `max_displacement`, `max_von_mises`, `applied_force_x/y/z`, `reaction_force_x/y/z` (newton), `equilibrium_residual` (dimensionless, relative), `element_count`, `node_count`; `data`: `max_displacement_at_mm`, `max_displacement_vector_mm`, `max_displacement_components_mm` (largest |ux|, |uy|, |uz| over nodes), `max_von_mises_at_mm`, `equilibrium` (applied, reaction, residual, both relative residuals and limits), `supports` and `loads` (what each clause selected: facets, nodes, area, resultant, snapped node and distance for points), `material`, `mesh`, `solver` (versions, times, files), `conventions`; artifacts `model.inp`, `result.frd`, `result.dat`, `results.csv` (node, x, y, z, ux, uy, uz, |u|, six stresses, von Mises), `mesh.msh`. Every result says `review_status: unreviewed`.

`max_displacement` is the largest displacement **magnitude** over nodes. In bending that is a tip corner, which also moves along the axis by the end rotation, so it is larger than the deflection; read the deflection from `results.csv` or `max_displacement_components_mm`.

## The selector language (`selectors.py`)

Text is held to the characters `A-Za-z0-9 . , ; : = ( ) + - * / ^`, then matched by anchored patterns, then each quantity passes `units.parse_quantity` and a magnitude bound. Nothing is evaluated.

* Face: `x = 0 mm`, `normal +z at z = 20 mm`. All boundary facets whose six nodes lie within `1e-6 x diagonal` of the plane (floor `1e-9 mm`); with `normal`, the outward normal's component along the axis must have the sign (> 0.5). Planes only: a curved face is selected by none, which is an error naming the selector (`fem.empty_selection`).
* Support: `<face> [: fixed | ux uy uz]`, or `point(x, y, z) [: dofs]` (nearest mesh node of the whole mesh; farther than one element size is `fem.point_not_on_model`; any move longer than `1e-6 x diagonal` is a warning).
* Load: `force (Fx, Fy, Fz) on <face>`: the total force spread by area (uniform traction) as consistent nodal forces of the quadratic facets (corner nodes of a straight triangle get nothing, midside nodes a third of the area each; curved facets integrated with a degree-5 rule on the curved triangle, so the shares add up to the total exactly). `pressure p on <face>`: `-p x (outward area vector)` per node; positive presses into the material.
* At most 20 clauses and 2000 characters per parameter.

A support facet that is also a loaded facet is refused (`fem.overlapping_selection`): the load would go into the support and say nothing. A point support on a node of a loaded facet is refused the same way. A support face and a loaded face that only share an edge are fine (the hydrostatic block test has rollers and pressures meeting at all twelve edges).

## Rigid-body motion

The six rigid motions (translations; rotations about axes through the centroid, lengths scaled by half the diagonal) are tabulated at every constrained degree of freedom. The body is held exactly when that matrix has rank 6. The 6x6 Gram matrix is decomposed; an eigenvector with eigenvalue below `1e-12` of the largest (singular value ratio `1e-6`) is a free motion, and is named by its components above 0.25 ("translation along y", "rotation about x"). It is a necessary and sufficient test for a single connected body in linear elasticity, and it runs before the solver. CalculiX's own singularity message is a second net (`fem.not_constrained`). Several solids are refused up front, so "connected" holds.

## What is exact, what is discretisation error, what is neither

**Exact (to the digits the programs print):**

* The B-rep volume (OpenCASCADE) and the unit handling: a STEP in inches is read in millimetres and gives the same physics (tested).
* The loads: the nodal forces are computed in double precision and sum to the requested force to rounding (`applied_force_*` equals the request to `1e-12`).
* The equilibrium check: reactions are CalculiX's nodal forces RF at the constrained nodes. **RF is the internal force K u** (summed over all nodes it is zero), so at a constrained node the reaction is RF minus the load applied there; the toolkit does that. The total is the one CalculiX summed itself before printing seven digits, so residuals are about 1e-14 relative when nothing is wrong (measured, bar and cantilever) and 2.3e-8 at worst from the seven printed digits (hydrostatic block). The call fails above `1e-6` of the sum of the nodal applied forces' magnitudes, or above `1e-5` for the moment residual about the centroid (taken from the per-node values, which carry seven digits each; measured 1e-10 to 1e-8).
* A uniform stress state in a field the elements can reproduce. Quadratic tetrahedra reproduce any displacement quadratic in position, so the uniaxial bar and the hydrostatic block are solved exactly; the differences left are the digits CalculiX prints.

**Discretisation error (shrinks with the element size, never zero):**

* Bending, shear lag, anything with a cubic or higher displacement: the cantilever.
* Curved boundaries: the mesh surface is made of quadratic patches with midside nodes on the true surface; a cylinder of radius 10 and height 30 has a meshed volume within `3e-5` of `pi r^2 h` at all sizes tried (8, 4, 2 mm; not monotone, because Gmsh splits the circle differently at each size). The region between nodes of a thin part is poorly represented.
* Peak stresses: nodal values grow without bound near a point load, a point support, a sharp re-entrant corner, and at the corner of a fully fixed face (below).

**Neither exact nor discretisation, a property of the output:**

* `max_von_mises` is the largest of the *nodal* stresses CalculiX writes to `.frd` (extrapolated from the four integration points of each tetrahedron and averaged between elements), printed with six digits. A uniform 100 MPa field comes out as 99.999 (1e-5 low), although the integration-point stresses in `.dat` are 100.0000: an artefact of the extrapolation, tolerance 1e-4 in the tests. The location is the node, not the true peak.
* The tool reports no safety factor, yield check or fatigue life.

## The numbers, derived by hand in the tests, with tolerances

Steel, E = 210000 MPa, nu = 0.3, G = 80769.23 MPa. Every expected value below is derived in the test's docstring with the arithmetic.

| Case | Hand value | Measured | Tolerance and why |
|---|---|---|---|
| Bar 10 x 10 x 100, 10 kN, minimal supports: tip ux | F L / (A E) = 10000 * 100 / (100 * 210000) = 0.047619048 mm | 0.047619 (h = 5, 2.5) | 1e-4: the field is representable, the error is the printed digits |
| same: von Mises | F / A = 100 MPa | 99.999 | 1e-4 (extrapolation artefact above) |
| same: largest magnitude | sqrt(0.047619048^2 + 2 (0.3 * 4.7619e-4 * 5)^2) = 0.047629763 mm | 0.04762976 | 1e-4 |
| same: Poisson contraction at the corners | 0.3 * 4.7619048e-4 * 5 = 7.1428571e-4 mm | 7.14286e-4 | 1e-4 |
| same: reaction | -10000 N | -10000.0000 | 1e-6 (seven printed digits) |
| Cantilever 100 x 10 x 10, P = 1 kN, free-section root (`x = 0 mm : ux uz` + `uy` at two points): tip deflection | Timoshenko 1.904762 + 0.014571 = 1.919333 mm | 1.9128 (h 2.5), 1.9135 (h 1.25) | 0.5 % of Timoshenko (observed 0.35 % and 0.30 % below); lies between Euler-Bernoulli (1.904762) and Timoshenko, so the shear term is visible |
| same: sigma_xx at mid-span top | M c / I = 50000 * 5 / 833.333 = 300 MPa | 299.9 to 300.1 | 1 % |
| Cantilever, textbook clamp (`x = 0 mm`): tip deflection | 1.919333 mm | 1.9042 (h 2.5), 1.9057 (h 1.25) | 1.5 % (observed 0.78 % and 0.71 % below); the clamp stops the root section contracting sideways and cancels the shear term (0.05 % above Euler-Bernoulli) |
| same: sigma_xx at mid-span top and bottom | +-300 MPa | 299.9, -299.9 | 3 % |
| Refinement, free-section cantilever, h = 10, 5, 2.5, 1.25 | tip converges to about 1.914 | 1.9074, 1.9110, 1.9128, 1.9135 mm | differences to the finest: 6.1e-3, 2.5e-3, 7.5e-4 mm: strictly decreasing, and the first is more than 3 times the last |
| Hydrostatic block 10 mm, rollers, p = 100 MPa on three faces | eps = -100 * 0.4 / 210000 = -1.9047619e-4, u = 1.9047619e-3 mm; von Mises 0 | 1.90476e-3 (corner 3.299145e-3); von Mises 1.4e-10 MPa | 1e-4 and `< 1e-3 MPa` |
| Same bar in inches (STEP unit inch, 3.937 x 0.3937 x 0.3937), loads in lbf, selectors in inch, results in inch and psi | the mm values converted | same | 1e-4 |
| `fem_mesh` volume, bar | 10000 mm3 | 10000 (1e-16) | flat faces: exact |
| `fem_mesh` volume, cylinder r 10, h 30 | pi * 100 * 30 = 9424.7780 mm3 | within 2.6e-5 | 1e-4 |

The fully clamped root is not a bug and not the textbook beam: beam theory lets the root section contract; a 3D clamp does not. The tool warns when a face is fully fixed. Its maximum von Mises is singular at the root corner and grows with refinement (590, 600, 594, 687, 724 MPa at h = 10 ... 1.25), so no test compares it with beam theory. With a single point carrying the whole shear reaction the stress is worse (1489 MPa) and the deflection wrong (1.939): point supports are for removing rigid motion where the exact reaction there is zero, not for carrying load.

**Minimal constraints.** A bar under tension is solved exactly only if the supports remove rigid motion and nothing else. The tests fix `ux` on the face (the section may contract and shear freely), `uy` at (0, 5, 0) and (0, 5, 10) (the exact `u_y = -nu eps (y - 5)` is zero on y = 5) and `uz` at (0, 0, 5) (the exact `u_z` is zero on z = 5). `uy` at two points also removes rotation about x. Fully fixing the face instead raises the largest von Mises above 100 MPa by more than 1 % and forbids the root contraction (tested).

## Limits and refusals

| Limit | Value | Error |
|---|---|---|
| Element estimate `6 sqrt(2) V / h^3` before meshing | 60000 | `input.too_large`, the estimate and the size that would pass |
| Elements and nodes after meshing | 60000 and 120000 | `input.too_large` (thin walls need more elements than the estimate; a 100 x 100 x 0.5 mm plate at 2 mm is estimated at 5300 and meshed as 17900 elements) |
| Element size, lower | 1/1000 of the bounding-box diagonal | `input.too_large` |
| Element size, upper | the diagonal | `input.out_of_range` |
| Part size | 1e-3 mm to 1e6 mm | `input.out_of_range` |
| Young's modulus | 1e-3 to 1e7 MPa, > 0 | `input.invalid` / `input.out_of_range` |
| Poisson's ratio | -1 < nu < 0.5 (a warning from 0.45) | `input.invalid` |
| Force, pressure, coordinates | 1e12 N, 1e7 MPa, 1e7 mm (the force *vector* too) | `input.out_of_range` |
| Load as a stress on its face | at most 10 x E | `input.out_of_range` |
| Gmsh | 120 s, 4096 MB | `engine.timeout`, `engine.resource_limit` |
| CalculiX | 240 s, 6144 MB, 150 MB of files | same |
| Solids in the file | exactly one | `input.unsupported` |
| Non-solid, zero-volume solid | | `input.invalid` ("no closed solid with positive volume") |
| NaN and infinity | in text: parse errors; in JSON numbers: `input.invalid`; in results: never (checked on the Gmsh and CalculiX output, and through `fastmcp.Client`) | |

Measured: 33482 elements (the bar at 1.13 mm) take 2.3 s to mesh and about 22 s to solve; 3563 elements about 1 s. The largest run tried is that one. A mesh at the 60000 cap was not solved (memory and time there are the limits of Windows' per-process cap, not tested).

## Security

* **The caller cannot produce a Gmsh script.** There is no `.geo`; Gmsh gets a fixed argument list and a BREP the toolkit wrote. `Merge`, `Include`, `Exec`, `SystemCall` cannot occur. A test spies on `external.run` and asserts the argument list (`part.brep -3 -order 2 -clmax <float> ...`), that no `*.geo` exists in the job, and that a hostile file name (`bar & calc $(x).step`) reaches no program (the copy under `input/` is never an argument).
* **The caller cannot produce a CalculiX deck.** Every line of `job.inp` is one of a closed set of keywords with constant names (`NALL`, `EALL`, `NSUP`, `MAT`) and integers or `%.12e` floats. `*INCLUDE` and `*INPUT` are never written. A test checks every line of a generated deck against the closed vocabulary, and that text planted in the STEP file (a product name `*INCLUDE, INPUT=evil.inp`) never reaches the deck.
* **Text from the selectors** is held to a character whitelist and parsed to numbers; the tests refuse `;`-chained commands, pipes, `&`, backticks, `$()`, quotes, braces, backslashes, newlines, NUL, and the Gmsh and CalculiX keywords.
* Both programs run only through `eng_toolkit.external` (fixed argument list, job subdirectory as working directory, scrubbed environment, time/memory/output limits). The layer is not a sandbox (see its design note): a program that parses a file in C runs with the server's rights.
* The user-supplied CAD file is parsed by OpenCASCADE in-process (the same loader as `geometry_measure`, with its size and read-directory rules via `JobHandle.input_file`).

## Not done

* No nonlinear analysis (material, geometry, contact), no dynamics, no modal or buckling, no thermal or thermo-mechanical, no body forces (gravity, centrifugal), no prescribed displacements other than zero, no orthotropic or temperature-dependent materials, no beams, shells or mixed models, no assemblies (several solids are refused), no section forces, no results at integration points, no mesh refinement controls (one global size), no curved-face selectors (cylinders, cones), no node or edge selectors beyond the nearest-node point.
* No plate-with-hole or thick-walled-cylinder acceptance case (optional in the brief). The hydrostatic block tests the pressure load and the roller supports against a closed form.
* Not run: Linux and macOS (the layer's paths there are untested, as its note says); Gmsh and CalculiX versions other than 4.15.2 and 2.23; a solve at the element cap.
* Gmsh's own quality measure (SICN) is not reported: the toolkit computes the mean ratio of the corner nodes, which is a different number (1 for a regular tetrahedron, 0 for a flat one).
* The estimate `6 sqrt(2) V / h^3` over-estimates compact parts (bar: 679 estimated, 434 made at 5 mm; 25100 against 14700 at 1.5 mm) and under-estimates thin ones, so the after-meshing count is the real limit.
* Nodal stresses are the extrapolated and averaged values CalculiX writes; stresses at integration points exist in `.dat` only if a future version asks for them.

## Found on the way (for other sessions)

* `tests/dfm_fixtures.write_step_inches` writes a millimetre STEP: OCCT only accepts `write.step.unit` after a writer exists and under the name `INCH` (`IN` is refused, returning False), so its `inch` file in `tests/test_geometry_check_dfm.py` is millimetres and the inch test there passes without testing inches. `tests/test_fem_real.py` has a corrected writer.
* `JobHandle` has no public accessor for the job directory; the fem tools use `JobHandle._job.dir` in one helper (`tools._job_dir`) because a program needs a directory to run in.
* CalculiX prints seven digits to `.dat` and six to `.frd`; the equilibrium check depends on `.dat`.
