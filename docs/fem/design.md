# fem: meshing with Gmsh and linear statics with CalculiX

Domain `fem` (`eng_toolkit/domains/fem/`): three tools, `fem_mesh`, `fem_static` and `fem_view` (the last one is described in [its own section](#fem_view-a-static-result-as-a-colour-mapped-glb); it runs no program). A solid CAD file goes in; a second-order tetrahedral mesh, or a linear static result (displacement, stress, reactions), comes out. Gmsh and CalculiX are programs the user installed; the toolkit starts them as separate processes through `eng_toolkit.external` (`docs/external/design.md`) and never imports, links or ships them. Tests: `tests/test_fem_selectors.py` (selector language, 61), `tests/test_fem_internals.py` (mesh arithmetic, rigid-body detection, deck, readers, 22), `tests/test_fem_real.py` (the real programs, 29, skipped when OCP, Gmsh or CalculiX is missing), `tests/test_fem_gateway.py` (through `fastmcp.Client`, 57; 14 of them need the programs).

## Pipeline

| Step | Who | What |
|---|---|---|
| 1 | `brep.read_solid` (the loader of `geometry_measure`) | STEP/IGES/BREP -> solid in millimetres. One closed solid with positive volume, or `input.invalid` / `input.unsupported` |
| 2 | `mesh.check_before_meshing` | element count estimate and size limits, **before** any program starts |
| 3 | `brep.write_brep`, then Gmsh | `gmsh part.brep -3 -order 2 -clmax <size> -format msh2 -nopopup -o part.msh` in `<job>/gmsh/`; no `.geo` is written, the only generated text is the size (`repr` of a validated float) |
| 4 | `mesh.read_msh` | strict reader of the ASCII file: nodes and 10-node tetrahedra (Gmsh type 11); Gmsh's midside nodes 9 and 10 are swapped to CalculiX's order |
| 5 | `mesh.check_and_measure` | quality; positive Jacobians at every C3D10 integration point, volume integration point, corner and edge midpoint; exact volume of the quadratic mesh; connected pieces; boundary facets with outward normals and curved areas. These samples do not prove positivity everywhere |
| 6 | `selectors` + `tools.static` | supports and loads parsed to numbers; faces chosen on the boundary facets; loads turned into consistent nodal forces |
| 7 | `solver.free_rigid_motions` | rigid-body check before the solver |
| 8 | `solver.build_deck`, CalculiX | `ccx -i job` in `<job>/calculix/` on the deck the toolkit wrote |
| 9 | `solver.read_dat`, `read_frd_stress` | displacements and reactions from `.dat`, nodal stresses from `.frd` |
| 10 | `tools.static` | equilibrium check (fails the call), von Mises, extrema, `results.csv`, artifacts |

Files a program wrote are copied to the job directory's top level (`mesh.msh`, `model.inp`, `result.frd`, `result.dat`) so they are in `manifest.json` with their sha256; the program directories stay as written.

## Tool contracts

`fem_mesh(model_path, element_size, output_unit="mm")` -> primary `element_count`; `values`: `element_count`, `node_count`, `mesh_volume`, `exact_volume` (cubic `output_unit`), `volume_relative_difference`, `min_quality`, `mean_quality` (dimensionless); `data`: `quality` (min, mean, median, slivers, worst element, min Jacobian ratio), `estimated_elements`, `limits`, bounding box, versions, `conventions`; artifact `mesh.msh` (msh2 ASCII).

`fem_static(model_path, element_size, youngs_modulus, poisson_ratio, supports, loads, output_length_unit="mm", output_stress_unit="MPa")` -> primary `max_displacement`; `values`: `max_displacement` (a magnitude), `max_displacement_ux`, `max_displacement_uy`, `max_displacement_uz` (largest |ux|, |uy|, |uz| over the nodes, in the output length unit), `max_von_mises`, `applied_force_x/y/z`, `reaction_force_x/y/z` (newton), `equilibrium_residual` (dimensionless, relative), `element_count`, `node_count`; `data`: `max_displacement_at_mm`, `max_displacement_vector_mm`, `max_displacement_components_mm` (largest |ux|, |uy|, |uz| over nodes), `max_von_mises_at_mm`, `equilibrium` (applied, reaction, residual, both relative residuals and limits), `supports` and `loads` (what each clause selected: facets, nodes, area, resultant, snapped node and distance for points), `material`, `mesh`, `solver` (versions, times, files), `conventions`; artifacts `model.inp`, `result.frd`, `result.dat`, `results.csv` (node, x, y, z, ux, uy, uz, |u|, six stresses, von Mises), `mesh.msh`. Every result says `review_status: unreviewed`.

`max_displacement` is the largest displacement **magnitude** over nodes. In bending that is a tip corner, which also moves along the axis by the end rotation, so it is larger than the deflection; read the deflection from `max_displacement_ux/uy/uz` (or `max_displacement_components_mm`, or `results.csv`). The tool description says "magnitude; see values.max_displacement_ux/uy/uz" and `data.displacement_note` repeats it for the actual run with the numbers (headline magnitude and the three per-axis maxima in the output length unit) and tells the reader to report sideways motion, the axes other than the loaded one (round 2, S3 r2-3: the L-bracket tip also moves 1.7 mm sideways, which nobody reported). `fem_mesh` and `fem_static` also return `estimated_elements` next to the real `element_count`.

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
| Element estimate `4.0 V / h^3 + 1.7 A / h^2` before meshing (V volume, A surface area; [calibration](#element-estimate-calibration)) | 60000 | `input.too_large`: the message and `details` give the estimate, the calibration ratio (estimate / real 0.87 to 1.26, median 1.01) and `finest_passing_size_mm` |
| Elements and nodes after meshing | 60000 and 120000 | `input.too_large` (thin walls need more elements than the estimate; a 100 x 100 x 0.5 mm plate at 2 mm is estimated at 5300 and meshed as 17900 elements) |
| Element size, lower | 1/1000 of the bounding-box diagonal | `input.too_large` |
| Element size, upper | the diagonal | `input.out_of_range` |
| Part size | 1e-3 mm to 1e6 mm | `input.out_of_range` |
| Young's modulus | 1e-3 to 1e7 MPa, > 0 | `input.invalid` / `input.out_of_range` |
| Poisson's ratio | -1 < nu < 0.5 (a warning from 0.45) | `input.invalid` |
| Force, pressure, coordinates | 1e12 N, 1e7 MPa, 1e7 mm (the force *vector* too) | `input.out_of_range` |
| Combined load as a stress on each facet | at most 10 x E after summing the traction vectors of all overlapping force and pressure clauses | `input.out_of_range`, `/loads` |
| Combined nodal load is exactly zero | refused before CalculiX; tiny nonzero loads use scaled norms | `input.invalid`, `/loads` |
| Solver displacement magnitude and stress components / von Mises | 1e9 mm and 1e9 MPa; nodal and total reactions at most 1e24 N; checked before result artifacts | `input.out_of_range`, `/loads` |
| Mesh/static output conversion | factor from mm or MPa between 1e-30 and 1e30; converted values at most 1e30; nonzero values must remain representable | `input.out_of_range`, `/output_unit`, `/output_length_unit` or `/output_stress_unit` |
| Gmsh | 120 s, 4096 MB | `engine.timeout`, `engine.resource_limit` |
| CalculiX | 240 s, 6144 MB, 150 MB of files | same |
| Solids in the file | exactly one | `input.unsupported` |
| Non-solid, zero-volume solid | | `input.invalid` ("no closed solid with positive volume") |
| NaN and infinity | in text: parse errors; in JSON numbers: `input.invalid`; in results: never (checked on the Gmsh and CalculiX output, and through `fastmcp.Client`) | |

Measured: 33482 elements (the bar at 1.13 mm) take 2.3 s to mesh and about 22 s to solve; 3563 elements about 1 s. The largest run tried is that one. A mesh at the 60000 cap was not solved (memory and time there are the limits of Windows' per-process cap, not tested).

## Element estimate calibration

Round 2 of the user-acceptance run (S3, problem r2-2) found the old estimate `6 sqrt(2) V / h^3` (the number of regular tetrahedra of edge h that fill V) 1.6 times the real count: the S3 L-bracket at 2.2 mm was estimated at 57400 and meshed as 35266, a 2.0 mm run was refused as needing 76000 elements when Gmsh makes 48584, so a mesh-convergence study stopped at 2.17 mm. Measured on the shapes below, Gmsh makes about 4 elements per h^3 of volume plus a denser layer at the boundary (about 1.7 per h^2 of surface), not 8.5 per h^3.

The estimate is now `ELEMENTS_PER_VOLUME * V / h^3 + ELEMENTS_PER_AREA * A / h^2` with 4.0 and 1.7 (`mesh.estimate_elements`, area from `brep.mass_properties`). The coefficients are a least-squares fit of the relative error to real Gmsh 4.15.2 counts (second order, `-clmax h`, the toolkit's own call) on 43 meshes of 9 shapes, using element sizes up to 1.3 times the mean thickness `2 V / A` and counts of 900 or more (below that Gmsh keeps its own floors). Rounded up slightly from the fit (4.01, 1.54) so that the estimate is not low on thick parts. estimate / real over the 43: minimum 0.87, median 1.01, maximum 1.26. Each cell is `h in mm: real count (estimate / real)`:

| shape | V mm3 | A mm2 | measured |
|---|---:|---:|---|
| S3 L-bracket (no plate) | 72066 | 26784 | 6: 2876 (0.90); 4: 5910 (1.24); 3: 15818 (0.99); 2.5: 23757 (1.08); 2.2: 35266 (1.03); 2: 48584 (0.98) |
| block 60x40x30 | 72000 | 10800 | 8: 981 (0.87); 6: 1798 (1.03); 5: 2991 (1.02); 4: 5588 (1.01); 3: 12977 (0.98); 2.5: 21975 (0.97) |
| plate 100x50x6, hole R8 | 28794 | 11699 | 5: 1816 (0.95); 4: 2629 (1.16); 3: 6286 (1.03); 2.5: 9765 (1.08); 2: 20143 (0.96); 1.5: 42259 (1.02) |
| cylinder R15 h60 | 42412 | 7069 | 6: 1065 (1.05); 5: 1810 (1.02); 4: 3390 (1.00); 3: 7806 (0.98); 2.5: 13167 (0.97) |
| bar 100x10x10 | 10000 | 4200 | 4: 944 (1.13); 3: 2364 (0.96); 2.5: 3563 (1.04); 2: 6439 (1.05); 1.5: 14726 (1.02); 1.2: 28916 (0.97) |
| plate 120x80x3 | 28800 | 20400 | 3.5: 6079 (0.91); 3: 8332 (0.97); 2.5: 11759 (1.10) |
| plate 100x60x6 | 36000 | 13920 | 6: 1446 (0.92); 5: 1993 (1.05); 4: 2955 (1.26); 3: 7851 (1.01) |
| cube 20 | 8000 | 2400 | 3: 1575 (1.04); 2.5: 2704 (1.00); 2: 4962 (1.01) |
| slab 100x100x10 | 100000 | 24000 | 8: 1414 (1.00); 6: 2959 (1.01); 5: 4677 (1.03); 4: 7732 (1.14) |

The S3 L-bracket: 2.2 mm is now estimated at 36500 (real 35266) and 2.0 mm at 47400 (real 48584), so a convergence study can go down to about 1.85 mm (the 60000 cap is at 1.84 mm for this part). The largest overshoots in the table (1.14 to 1.26) are at 4 mm on four of the shapes; the cause is not known (unverified: how Gmsh rounds the number of divisions of edges of that length). `tests/test_fem_estimate.py` meshes four shapes at two sizes each and asserts 0.9 <= estimate / real <= 1.3.

The cap (60000 elements, 120000 nodes) is unchanged and is still checked twice: on the estimate before Gmsh starts, and on the real count after it. The refusal message names the estimate, the calibration ratio, the likely real count (estimate divided by 1.26 to 0.87) and the finest size that passes (`mesh.finest_size_within_cap`, rounded up to 4 digits so that the named size passes); `details` carries `estimated_elements`, `finest_passing_size_mm`, `volume_mm3`, `area_mm2` and the `calibration` coefficients.

Refit: run Gmsh on the shapes above, solve `[V/h^3, A/h^2] c = n` in the relative-error sense, update `ELEMENTS_PER_VOLUME`, `ELEMENTS_PER_AREA` and the `CALIBRATION_*` constants in `mesh.py` and this table.

## Security

* **The caller cannot produce a Gmsh script.** There is no `.geo`; Gmsh gets a fixed argument list and a BREP the toolkit wrote. `Merge`, `Include`, `Exec`, `SystemCall` cannot occur. A test spies on `external.run` and asserts the argument list (`part.brep -3 -order 2 -clmax <float> ...`), that no `*.geo` exists in the job, and that a hostile file name (`bar & calc $(x).step`) reaches no program (the copy under `input/` is never an argument).
* **The caller cannot produce a CalculiX deck.** Every line of `job.inp` is one of a closed set of keywords with constant names (`NALL`, `EALL`, `NSUP`, `MAT`) and integers or finite decimal floats. Coordinates, loads and Young's modulus use `%.12e`. Poisson's ratio uses its shortest round-trip decimal if it fits CalculiX's 20-column field, otherwise `%.12e` for tiny ratios with long exponents. The serialized ratio must still lie strictly between -1 and 0.5. This preserves accepted ratios near either singular endpoint but does not establish solver accuracy for ill-conditioned materials. `*INCLUDE` and `*INPUT` are never written. A test checks every line of a generated deck against the closed vocabulary, and that text planted in the STEP file (a product name `*INCLUDE, INPUT=evil.inp`) never reaches the deck.
* **Text from the selectors** is held to a character whitelist and parsed to numbers; the tests refuse `;`-chained commands, pipes, `&`, backticks, `$()`, quotes, braces, backslashes, newlines, NUL, and the Gmsh and CalculiX keywords.
* Both programs run only through `eng_toolkit.external` (fixed argument list, job subdirectory as working directory, scrubbed environment, time/memory/output limits). The layer is not a sandbox (see its design note): a program that parses a file in C runs with the server's rights.
* The user-supplied CAD file is parsed by OpenCASCADE in-process (the same loader as `geometry_measure`, with its size and read-directory rules via `JobHandle.input_file`).

## Not done

* No nonlinear analysis (material, geometry, contact), no dynamics, no modal or buckling, no thermal or thermo-mechanical, no body forces (gravity, centrifugal), no prescribed displacements other than zero, no orthotropic or temperature-dependent materials, no beams, shells or mixed models, no assemblies (several solids are refused), no section forces, no results at integration points, no mesh refinement controls (one global size), no curved-face selectors (cylinders, cones), no node or edge selectors beyond the nearest-node point.
* No plate-with-hole or thick-walled-cylinder acceptance case (optional in the brief). The hydrostatic block tests the pressure load and the roller supports against a closed form.
* Not run: Linux and macOS (the layer's paths there are untested, as its note says); Gmsh and CalculiX versions other than 4.15.2 and 2.23; a solve at the element cap.
* Gmsh's own quality measure (SICN) is not reported: the toolkit computes the mean ratio of the corner nodes, which is a different number (1 for a regular tetrahedron, 0 for a flat one).
* The estimate is calibrated, not derived (next section): about +-25 % on the 43 meshes measured, and it under-estimates parts whose thickness is below the element size (a 120 x 80 x 3 mm plate at 6 mm is estimated at 1500 and meshed as 2238 elements) and parts so small that Gmsh's default size cap of about a tenth of the diagonal makes it refine (a 20 mm cube asked for 5 mm gets 1159 elements, the same as for 10 mm). The count after meshing is the real limit.
* Nodal stresses are the extrapolated and averaged values CalculiX writes; stresses at integration points exist in `.dat` only if a future version asks for them.

## fem_view: a static result as a colour-mapped GLB and a PNG picture

`fem_view` (`eng_toolkit/domains/fem/view.py`) turns the files of one `fem_static` job into `fem_view.glb`, `fem_view.png` (a picture of the same colours that can be pasted into a mail, see "The PNG preview"), `legend.svg` and `view.json`, so a 3D viewer, or a person with no viewer at all, sees where the stress is. It reads files only; Gmsh and CalculiX are not started. Tests: `tests/test_fem_view.py` (62: colour maps, parsers, a synthetic one-tetrahedron job that needs no program, refusals through `fastmcp.Client`, and 9 tests on real `fem_static` runs of the bar, the clamped cantilever and a cylinder, skipped without OCP, Gmsh or CalculiX) and `tests/test_fem_view_review_fixes.py` (25, one block per finding of the review below; 10 of them on real runs: two parts 20 m and 100 m from the origin, the bar, the cylinder, a keyhole part, a plate with a hole), with the independent GLB reader `tests/fem_view_reader.py` (`struct` and `json` only). The picture is tested in `tests/test_fem_view_preview.py` (18: the synthetic job, and the clamped cantilever on the real programs) and `tests/test_preview.py` (17, no engine needed), with the independent PNG reader `tests/png_reader.py` (`struct` and `zlib` only).

### Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `result_dir` | | the `ws/<job>/` directory of a `fem_static` run. Its `manifest.json` must say `"tool": "fem_static"` and list a sha256 for `mesh.msh`, `model.inp`, `result.frd`, `result.dat`, `results.csv` and `result.json`; the three files read (`result.json`, `mesh.msh`, `results.csv`) must match their sha256, or `fem.integrity_mismatch`. A missing or foreign manifest is `fem.not_a_static_result`. Only those fixed file names are opened: nothing named inside the manifest is used as a path |
| `mesh_file`, `results_file` | | instead of `result_dir`: a `mesh.msh` and the `results.csv` of the same run. Without a manifest nothing is hashed against; the CSV's row count must be the node count and its node coordinates must equal the mesh's within `1e-6 x diagonal + 2e-8 x max|coordinate|`, or `fem.integrity_mismatch`. The second term is the `%.9g` rounding of `results.csv` (9 significant digits move a value by at most `5e-9` of it) times a safety factor of 4: a part 20 m from the origin is printed with errors up to 1e-4 mm while its diagonal is 25 mm. In `result_dir` mode the sha256 check has already passed and this comparison is a consistency guard only; it still refuses swapped node rows or another mesh's results (tested) |
| `field` | `von_mises` | `von_mises`, `displacement` (magnitude), `ux` `uy` `uz`, `s_xx` `s_yy` `s_zz` `s_xy` `s_yz` `s_zx`, `principal_max`, `principal_min` (eigenvalues of the nodal tensor, computed here). A field whose columns the CSV lacks is `fem.field_not_available` (`/field`), naming those that exist |
| `colormap` | `auto` | `sequential` for `von_mises` and `displacement`, `diverging` for the signed fields; either can be forced |
| `range` | `auto` | `auto`: min..max over the surface nodes. Or `lo, hi` with units of the field's dimension (`0 MPa, 250 MPa`, `-0.1 mm, 0.1 mm`; any pressure or length unit). Every diverging range, including an explicit range, expands to `-max(abs(lo), abs(hi)) .. +max(abs(lo), abs(hi))`, so light grey #DDDDDD is zero. An explicit expansion is reported in the notes and warnings. Values outside the resulting range take the end colours and are counted |
| `deformation_scale` | `0` | `0` undeformed; `auto` = factor making the largest displacement 5 % of the bounding-box diagonal of the mesh nodes (rounded to 6 digits, and that rounded factor is the one used); or a plain factor 0 to 1e4 |
| `show_undeformed_edges` | true | when deformed, the undeformed feature edges as a grey line primitive |
| `mesh_edges` | false | the surface's element edges (each curved edge as two segments through its midside node) as a line primitive, on the drawn shape |
| `name` | `fem_view` | GLB node name and legend title, 1 to 64 characters; control, format (bidi overrides), surrogate and unassigned characters are refused; everything else is escaped in the SVG and is JSON data in the GLB |
| `output_unit` | `auto` | unit of the values, labels and `_FIELD`: MPa or mm by default, any pressure or length unit matching the field |
| `max_seconds` | 120 | wall-clock budget, 1 to 600, checked between steps (`fem.time_budget_exceeded`, `/max_seconds`) |
| `preview` | `png` | `png` (or `iso`, the same picture): also write `fem_view.png`; `none`: the GLB, `legend.svg` and `view.json` only; anything else is `input.invalid` at `/preview` |
| `inline_image` | `true` | the PNG also comes back as an MCP image block in the reply when it is at most 600 kB (`eng_toolkit/gateway/images.py`, see `docs/plot/design.md`, "PNG output"); `false`: the path only. The structured result is the same either way; a PNG above the limit is not attached and a warning says so |

Values: `triangle_count` (primary), `field_max`, `field_min` (over the surface nodes, read back from the GLB's `_FIELD` as float32 and given to 7 digits), `deformation_factor`. `data`: `statistics` (surface and all-node extremes with node numbers and whether they lie on the surface), `fem_static_comparison` (the all-node maximum of `von_mises` or `displacement` against `fem_static`'s `max_von_mises`/`max_displacement` from `result.json`, relative `1e-6`), `range` (mode, ends, widened, clamped counts), `deformation`, `legend`, `counts`, `closed_surface`, `source` (mode, sha256 of every file read, the `fem_static` job id), `output` (accessor bounds read back, conversion, material), `limits`, `losses`.

### What is drawn

* **Surface:** the boundary facets of the 10-node tetrahedra (`mesh.boundary_facets`, outward). Every six-node facet becomes four flat triangles `(a, ab, ca)`, `(ab, b, bc)`, `(ca, bc, c)`, `(ab, bc, ca)` with the facet's orientation, so curved boundaries pass through the midside nodes and the colour has a vertex at every node. Triangle count = 4 x boundary facets (tested against an independent count from the `.msh`).
* **Feature edges and vertices**, decided per facet pair: a facet edge is a *feature edge* when the normals of its two facets differ by more than 40 degrees (or it does not have exactly two facets). Around each node, the facets joined through non-feature edges form one *sector*; each sector is one vertex with its own averaged normal. A box-edge node therefore has 2 vertices, a box corner 3, a node inside a face 1, a cylinder rim node 2 and a cylinder side-wall node 1, whatever the rest of the part looks like (tested with counts derived by hand on the bar, the cylinder and a keyhole part whose 90-degree edge sits between two tangent joins). A split vertex carries the same nodal value on both sides: the colour is continuous; only the normal changes. The undeformed outline is every feature edge. (Before the review, vertices were shared within transitively connected "smooth patches", so a sharp edge reachable around a smooth path, like the keyhole's, lost its split and its outline.)
* **Normals:** the normal of the curved six-node facet at each of its nodes (from the derivatives of the quadratic shape functions), area-weighted over the facets of one patch, computed on the drawn (possibly deformed) positions.
* **Values:** each vertex carries exactly the value of its node in `results.csv`, which for stresses is the nodal stress CalculiX writes to `.frd`: extrapolated from the integration points of each element and averaged between the elements sharing the node, six printed digits (see "Neither exact nor discretisation" above). `fem_view` does no further averaging. The GLB's maximum von Mises equals `fem_static`'s `max_von_mises` to the seven digits of float32 when the maximum lies on the surface (tested on the bar: 99.999 MPa); when the all-node maximum (or minimum) exceeds the surface's by more than one float32 step, a warning says it lies inside the part and gives the value and node. A tie with a surface node is not reported as interior, and `max_node`/`min_node` then name the surface node (`max_on_surface: true`).
* **Colours:** `t = (value - low) / (high - low)` clamped to [0, 1]; the colour is the linear interpolation of a 65-point sRGB table at `t`; `COLOR_0` is that colour with the sRGB transfer undone (glTF vertex colours are linear), **float32 VEC3**, multiplied by a white base colour (metallic 0, roughness 0.8, single-sided). The viewer interpolates `COLOR_0` linearly over each sub-triangle.
* **Deformation:** subtract the mesh bounding-box centre in float64, add factor x u (mm), then convert axes and store local float32 positions. The node translation restores the original coordinates. The node extras, `view.json`, the legend (`Shape exaggerated: displacements drawn x<factor>`) and a warning say so whenever the factor is above 1.
* **Uniform fields:** an automatic sequential range narrower than 0.1 % of its largest magnitude is widened to that width about its middle (the bar's 99.999 MPa everywhere would otherwise spread the six-digit print noise over the whole map); it is reported as `widened`. An all-zero field gets 0..1 (diverging -1..1).

### Colour maps (own tables, computed from published formulas at import)

| id | Source | Parameters |
|---|---|---|
| `sequential` | cubehelix, D. A. Green, "A colour scheme for the display of astronomical intensity images", Bull. Astr. Soc. India 39, 289 (2011), eq. 2 | start 3.0, rotations -0.65, hue 1.3, gamma 1, lambda 0.10 to 0.88 over 65 points: dark blue, teal, green, pale yellow; perceived intensity rises monotonically (tested on the relative luminance), so it also reads in grey scale. Not viridis's table: a viridis-like ramp from a documented formula |
| `diverging` | K. Moreland, "Diverging Color Maps for Scientific Visualization", ISVC 2009 | his cool-to-warm endpoints sRGB (59, 76, 192) and (180, 4, 38), interpolated in Msh (polar CIELAB, D65 white 0.95047, 1, 1.08883) through a neutral point of M = 88 (grey about (221, 221, 221)), 65 points. Blue below the middle, red above |

The table (sRGB, six decimals) is in the node extras (`colormap_table_srgb`), so a reader can invert a colour back to `t`; the tests derive the sequential table independently from Green's formula and invert every vertex colour of the GLB.

### GLB layout and extras

One scene, one node (`name`), one mesh. Primitive 0 (TRIANGLES, material `field`): `POSITION`, `NORMAL`, `COLOR_0` (float32 linear RGB), `_FIELD` (float32 value in the legend's unit), `_NODE` (float32, the 1-based node number of `results.csv`; exact for these node counts), uint32 indices. `_FIELD` and `_NODE` are application-specific attributes (leading underscore, allowed by glTF 2.0; viewers ignore them). Optional LINES primitives `mesh_edges` (#202020) and `undeformed_outline` (#7A7A7A), positions only. All primitives use the same local origin at the model bounding-box centre. The glTF node `translation` stores that offset in metres; extras and `view.json` record `units.origin_mm` and `units.node_translation_m`. Apply the node transform before comparing positions with the source model. Accessor bounds are local. Metres, +Y up, `(x, y, z) mm -> (x, z, -y) / 1000 m`, every buffer view on a 4-byte boundary, accessor min/max of every accessor, JSON padded with spaces, `allow_nan=False`, ASCII-escaped.

Node extras: `tool`, `tool_version`, `field`, `field_label`, `unit` (pint name), `unit_symbol`, `colormap`, `colormap_source`, `colormap_table_srgb`, `colormap_rule`, `range` {`mode`, `low`, `high`, `unit`, `unit_symbol`, `widened`, `clamped_below`, `clamped_above`, `clamp_rule`}, `legend` (11 samples: `value`, `color` hex, `srgb`, `linear_rgb`), `deformation` {`mode`, `factor`, `exaggerated`, `inverted`, `max_displacement_mm`, `bbox_diagonal_mm`, `drawn_max_displacement_mm`, `rule`}, `notes`, `units` (conversion), `attributes` (what each attribute holds), `source_sha256`, `fem_static_job_id`. Surface primitive extras: `kind`, `facets`, `sub_triangles_per_facet`, `feature_edges`, `meaning`. `view.json` holds the same plus statistics and counts.

Before writing, float32 triangle area vectors must agree with the original float64 vectors to relative 1e-4; collapsed or unrepresentable triangles are refused. After writing, the GLB is parsed from its bytes and the triangle count, every local position, the node translation, every `_FIELD` and `_NODE`, the colour bounds and the line segment counts are compared with what was meant to be written (`fem.readback_mismatch` otherwise). `field_max`/`field_min`, the triangle count, the accessor bounds and the enclosed volume in the answer come from that read-back. Volume uses a common nearby origin, `sum((a-o) dot ((b-o) cross (c-o))) / 6`, to avoid cancellation on translated models.

`legend.svg`: a 340 x 470 hand-written SVG: title, field label and unit, a vertical bar filled by a `linearGradient` whose 65 stops are the table (SVG interpolates in sRGB, as the table does), 11 ticks with labels (enough digits to tell them apart), and the notes (clamped counts, exaggeration, widened range, surface min/max, "nodal values"). Elements `svg title rect text defs linearGradient stop line` only, no script, no `href`, the one reference is the internal `url(#ramp)`; all text escaped (`&`, `<`, `>`, quotes). Deterministic: the same inputs give the same bytes for all three files (tested).

### The PNG preview (`preview`)

The user-acceptance round found that nobody could look at a GLB: a person in a chat client asked four times for "a picture" of a
stress map, and the assistant spent four turns on matplotlib it did not have. `fem_view` therefore also writes `fem_view.png`
(`preview="png"`, the default), drawn by `eng_toolkit/preview.py` (numpy only, the same module and rasteriser as `geometry_view`; see
`docs/geometry-view/design.md`, "The PNG preview", for the algorithm, the font, the encoder, the timing and the memory). The GLB, the
`legend.svg` and the numbers in `data` are the same with or without the picture (tested: same sha256).

* **Content.** 1200 px wide: the title (`name`), the field, its unit and the shape (undeformed, or drawn xN) in a subtitle; the
  isometric view (camera at (+x, -y, +z), Z up) with an X/Y/Z triad; a **colour bar** 330 px tall with the field's name, the unit in
  brackets, six tick labels (digits increased until no two labels are equal) and the colour map exactly as the GLB's
  (tested pixel by pixel against an independent computation of the cubehelix table: within 1/255); under it `Surface min ..., max ...`
  and `Colour scale lo to hi` in the output unit, for a diverging map `Light grey = zero; blue negative, red positive`, and the notes
  of the legend (exaggerated shape, clamped nodes, widened range); the size of the undeformed model in mm; and the caveats: nodal values,
  surface only, peaks at point loads, supports and sharp corners depend on the mesh, unreviewed.
* **Colours.** Each vertex has the colour of the GLB's `COLOR_0` (the sRGB colour of the map), interpolated linearly over the
  sub-triangles as a viewer does, times a gentle Lambert factor `0.8 + 0.2 max(0, n . l)` so that the form shows without moving the
  colour far (a uniform field is one map colour times a factor in [0.8, 1], tested). Thin dark lines draw the feature edges of the drawn
  shape (the edges of a box, the rims of a cylinder); with `mesh_edges` the element edges as well; when deformed with
  `show_undeformed_edges`, the undeformed outline in grey.
* **Markers.** A black ring with `max <value> <unit>` on the surface node that holds the largest value, and for a signed field
  (`ux`, `s_xx`, `principal_*`, ...) a grey ring with `min <value> <unit>` on the smallest. These are the surface extremes (the ones of
  `field_max` and `field_min`; an interior extreme is still only a warning). A marker on a face that points away from the isometric
  camera (a clamped bar's maximum often sits on its underside) is not drawn there: the picture adds a second isometric panel from
  the view that shows most of the hidden markers (`iso_back` from (-x, +y, +z), `iso_under` from (+x, -y, -z) or `iso_under_back`
  from (-x, +y, -z), the first of them on a tie) and draws the rings in it; `data.preview.markers[]` says per marker the node, the
  value, the unit, whether it is `visible` and in which `view`. A marker no view shows is a warning, and its value is still in the
  colour bar notes and in `data`. Tested on the real cantilever: `uz` has its minimum, -1.904 mm (beam theory 1.9048), marked at the
  tip, and the von Mises maximum of 594.5 MPa, at the clamped end of the underside, gets the second panel.
* **Failure is a warning.** If the picture cannot be drawn (memory, its own 60 s, the shared budget running out while drawing) the tool
  still returns the GLB, the legend and `view.json`, adds `The PNG preview was not drawn (...). The GLB is complete` and sets
  `data.preview = {"file": null, "reason": ...}` (tested with injected failures). No Pillow is used (nothing to install, nothing to
  warn about); the tests run with `PIL` and `matplotlib` made unimportable.
* **`data.preview`:** `file`, `sha256`, `format`, `width`, `height`, `views`, `camera`, `supersampling`, `colours`, `colour_scale`
  (`low`, `high`, `unit`, `colormap`: the range the bar shows), `model_size_mm`, `markers`, `rule`.

### What the colours mean, and what they do not

* They are nodal values of a linear, small-displacement, elastic analysis, on the **surface only**. An interior maximum is invisible (a warning names it).
* Between nodes the colour is the GPU's linear interpolation of the vertex colours on four flat triangles, not the element's quadratic field: read numbers at nodes (`_FIELD`), not between them.
* Peaks at point loads, point supports, sharp re-entrant corners and the edges of fully fixed faces are unbounded and mesh-dependent (the clamped cantilever's root corner reaches 594 to 724 MPa depending on the mesh against 300 MPa of beam theory at mid-span): a red spot there is not a failure prediction. No yield, safety factor or fatigue judgement is made.
* A deformed shape with factor > 1 is a caricature for seeing the mode of deformation.
* The closed-surface check: the signed volume of the drawn surface equals the mesh volume where facets are flat (bar and cantilever: 10000 mm^3 to float32 precision). On curved faces the flat sub-triangles cut chords, so the difference is at most the curved area times the largest chord sagitta `l^2 / (8 r)`, and its sign depends on the curvature: a convex face (a cylinder's outside) is cut inward and the drawn surface encloses **less** (cylinder r 10, h 30 at 3 mm: 9398 against 9424.7 mm^3, 0.28 %); a concave face (a hole, a pocket) is cut toward the hole and the drawn surface encloses **more** (plate 40 x 40 x 5 with a diameter-10 hole; the reviewer measured a 100 x 40 x 5 plate: 19609.3 against 19607.3 mm^3, a blind-pocket part: 99779.3 against 99774.0). Both signs and the bound are tested.
* A deformation drawn so large that the shape turns inside out (the signed volume of the drawn surface not positive while the undeformed one is) gives a warning, a legend note and `deformation.inverted: true`; a partial self-intersection that keeps the volume positive is not detected. A factor applied to a field whose displacements are all zero is not called an exaggeration: the note says the displacements are zero and `exaggerated` is false.

### Caps and refusals

| Limit | Value | Error, pointer |
|---|---|---|
| Surface triangles (4 x boundary facets, checked before anything is built) | 1,000,000 | `input.too_large`, `/result_dir` or `/mesh_file`, with the count and "re-run fem_static with a larger element_size". With `fem_static`'s own caps (60000 elements, 120000 nodes, also enforced here) it is not reached by a real job; the test lowers it |
| Files | manifest 1 MB, result.json 16 MB, mesh.msh 16 MB, results.csv 64 MB (through `JobHandle.input_file`: `ENG_TOOLKIT_READ_DIRS` allow-list, opened once, hashed copy). The mesh cap follows from fem_static's own: real meshes take 165 to 200 bytes per tetrahedron (measured on 13 jobs, the thin plate is the largest), so 60000 tetrahedra are at most about 12 MB | `input.too_large`, `input.path_not_allowed`, `input.file_not_found`, `input.unsupported_format` |
| Mesh counts | the `$Nodes` and `$Elements` counts are read first and refused above 120000 nodes and 120000 elements of all kinds (2 x the tetrahedron cap: Gmsh also lists the surface triangles, lines and points, up to 0.7 per tetrahedron measured) **before any row is parsed**; the parser is fem_view's own (`read_mesh_file`, same node order and midside swap as `mesh.read_msh`, tested equal) and checks the deadline every 20000 rows; after parsing, the used nodes and the tetrahedra are held to fem_static's 120000 and 60000 | `input.too_large`, `fem.time_budget_exceeded` |
| Degenerate meshes | a tetrahedron with coplanar or collinear corners (`6V <= 1e-12 x (longest edge)^3`, named by its position among the file's tetrahedra), a face shared by three or more tetrahedra, a mesh with no boundary facet (an element listed twice) are refused before any normal is computed | `input.invalid`, `/mesh_file` (or `/result_dir`) |
| Unexpected errors | any other exception while reading the files or building the surface becomes `input.invalid` at the file's pointer with the exception's type only: no traceback, no path in the result | `input.invalid` |
| Strings | paths 1024, `range` 128, `name` 64, other words 64 characters, checked before anything else. A string over its cap is never echoed (the message says only its length). What is echoed: a path within the cap comes back in full in `input.file_not_found` (message and `details.path`), as for every toolkit tool that reads a file; a short unknown word (`field`, `colormap`, `range` part, `deformation_scale`, `output_unit`) is quoted up to 32 characters followed by `...` | `input.invalid` |
| Range ends, CSV values | 1e9 MPa, 1e9 mm, coordinates 1e7 mm; after unit conversion a value must stay below 1e30 | `input.out_of_range` |
| `deformation_scale` | 0 to 1e4 (plain number, `auto` or `0`; NaN, inf, units, negatives refused); the drawn shape must stay within 1e8 mm | `input.out_of_range` / `input.invalid` |
| NaN, infinity | refused in every text and in the CSV; never written to a result, the GLB or view.json (`allow_nan=False`, read-back finiteness checks) | |

Security: no caller text reaches a program (none is started); the manifest decides nothing about which paths are opened; file names inside it are compared, not followed. The manifest's sha256 check detects files changed or mixed up after `fem_static` wrote them; it is not a signature, and someone who can rewrite both a file and the manifest can forge a job (the tests do exactly that for the synthetic job).

### Not done

The picture is one fixed isometric view (plus a second one only for a hidden marker): no choice of camera, no cut planes, no labels at user-chosen points. No volume rendering, cut planes, iso-surfaces or interior views; no animation of the deformation or of load steps (one static step only); no modal, buckling or thermal results (`fem_static` produces none); no element-averaged versus nodal toggle and no integration-point values (only the nodal values CalculiX wrote); no per-material or per-region averaging control (there is one material); no texture-based colouring (vertex colours only, so colours between nodes are interpolated in linear RGB by the viewer); no custom colour tables; no viewing of a `fem_mesh` job without results. Not tested in a third-party glTF validator (the toolkit's own reader and the tests' independent reader check the structure).

### Review findings and their fixes (`tests/test_fem_view_review_fixes.py`)

| # | Finding | Before | After |
|---|---|---|---|
| 1 | a genuine job far from the origin refused as tampered | box at x = 20000 mm: `fem.integrity_mismatch`, gap 5e-5 mm against tolerance 2.45e-5 mm (at 100000 mm: 5e-4) | tolerance `1e-6 x diagonal + 2e-8 x max|x|` (4.25e-4 mm and 2.0e-3 mm): both accepted; swapped node rows and another mesh's results still refused |
| 2 | sharp edges inside a transitive smooth patch | keyhole: 19 unsplit vertices on the 90-degree edge with normals about (0.70, -0.71, 0), no outline point on it | per facet pair: 38 vertices (2 per node, normals +x and -y), the 19 nodes in the outline; box and cylinder counts match the hand counts |
| 3 | a mesh with no boundary facet | `engine.failure` with an `IndexError` traceback | `input.invalid` at `/mesh_file`, "no boundary facets"; any unexpected exception while reading or building the surface is `input.invalid` with the exception's type only |
| 4 | coplanar or collinear corners | `input.out_of_range` at `/output_unit` ("not finite after conversion") | `input.invalid` at `/mesh_file` naming element 1, before any normal is computed |
| 5 | an interior node tying the surface extreme | "maximum ... is inside the part" on a uniform field, `max_on_surface: false` | no warning, a surface node named; a strictly larger interior value still warned |
| 6 | "falls short" on every curved face | wrong for holes and pockets | sign by curvature (convex less, concave more), both tested against the sagitta bound |
| 7 | the mesh file parsed before the caps | crafted 63 MB msh: 9.3 s and 1.15 GB before `input.too_large`; `max_seconds` not honoured while parsing | 16 MB file cap (refused at once, 0.14 GB peak), the counts checked before parsing, the deadline every 20000 rows |
| a | "blue-white-red", "white is zero" | | "blue-grey-red", light grey #DDDDDD is zero |
| b | "never echoed" | overstated | states what is echoed (paths within the cap, short words up to 32 characters) |
| extras | inside-out shapes; a factor on zero displacements | no warning; "Shape exaggerated x200" | `deformation.inverted` with a warning and a legend note; "Displacements are zero" |

## Second independent review (`tests/test_fem_review2.py`)

All twelve findings at `472d99c` were reproduced through `Client(gateway)` before
the fixes. CAD and external-program fixtures isolate the reported arithmetic;
mesh and solver readers, load assembly, result validation, artifact writing and
the gateway remain real. These fixtures do not establish CalculiX accuracy for
near-singular materials. The separate existing real-program tests cover ordinary
bar, bending, pressure and curved-mesh cases.

| Finding | Correct reference or refusal | Fix |
|---|---|---|
| 1 | 60-digit Decimal differentiation gives det J = -0.02182089431 at the reported C3D10 point | Test all four solver integration points, all volume quadrature points, corners and edge midpoints. Refuse at `/element_size` |
| 2 | The two reported Poisson ratios must remain strictly between -1 and 0.5 in `*ELASTIC` | Preserve endpoint neighbours using shortest decimals within CalculiX's field width and validate the serialized ratio |
| 3 | Two 6 MPa clauses on a 50 mm2 face give 600 N and nominal strain 12 for E = 1 MPa | Sum traction vectors per facet before checking the 10E limit; mixed force/pressure clauses obey the same rule |
| 4 | Opposite 10 N clauses give zero nodal force; a single -1e-200 N clause remains nonzero | Refuse exact cancellation at `/loads`; use hypot norms and dimensionless moment arithmetic for nonzero loads |
| 5 | The element-size limits precede h cubed | Refuse tiny or huge converted sizes at `/element_size` before estimating element count; error details stay finite |
| 6 | 1000/6 mm3 cannot become a successful zero or infinity after conversion | Bound output factors and results; refuse underflow, overflow and absurd converted magnitudes at the output-unit pointer |
| 7 | Uniaxial von Mises equals the absolute axial stress, even for 1e200 mathematically | Use a scaled norm; reject absurd solver components and derived values before CSV or published result artifacts |
| 8 | Ordinary refusals must identify their input with a JSON pointer | Normalize shared unit/file errors at the FEM boundary and identify support/load/mesh failures |
| 9 | T(0.01, 1000000) has ten distinct physical nodes and nonzero triangle areas | Store centred local positions with a node translation shared by surface and line layers; compare float32 and float64 triangle geometry |
| 10 | The old GLB bytes enclose 0.15522042910257974 mm3 by Decimal arithmetic, not 35.7590332 | Integrate about a nearby common origin. The centred GLB now preserves 1/6 mm3 to float32 precision |
| 11 | Light grey represents zero, blue negative and red positive | Expand explicit diverging ranges symmetrically, including -100..200 MPa to -200..200 MPa; update legend, metadata and warnings |
| 12 | A mesh containing an unsupported volume element cannot represent the complete model | Both readers accept only C3D10 volumes and known point/line/surface types 1, 2, 3, 8, 9, 10, 15, 16. Reject every other type, including mixed tetrahedra and hexahedra |

## Found on the way (for other sessions)

* `tests/dfm_fixtures.write_step_inches` writes a millimetre STEP: OCCT only accepts `write.step.unit` after a writer exists and under the name `INCH` (`IN` is refused, returning False), so its `inch` file in `tests/test_geometry_check_dfm.py` is millimetres and the inch test there passes without testing inches. `tests/test_fem_real.py` has a corrected writer.
* `JobHandle` has no public accessor for the job directory; the fem tools use `JobHandle._job.dir` in one helper (`tools._job_dir`) because a program needs a directory to run in.
* CalculiX prints seven digits to `.dat` and six to `.frd`; the equilibrium check depends on `.dat`.
