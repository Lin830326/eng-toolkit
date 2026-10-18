# metrology_check_scan: design rationale

Produced under the pstack `poteto-mode` Feature playbook (architect step run by hand, engine facts probed in a scratch environment).

## Problem

An inspector or an agent has a point cloud from a scanner and the STEP model of the part, and wants to know how far the part is from the model and where. The design document names this tool `metrology.check_scan`, with Open3D as the main engine and OCCT to read the STEP.

## Usage (caller's view)

```text
metrology_check_scan(scan_path="C:/scans/bracket.ply", scan_unit="mm",
                     model_path="C:/cad/bracket.step", tolerance="0.1 mm",
                     registration="icp")
  -> rms 0.032 mm, max 0.21 mm, 4.1 % of points out of tolerance,
     2 regions (centroid, bounding box, worst deviation), ICP transform and fit quality,
     deviations.csv with every out-of-tolerance point
```

Sign convention: deviation is the signed distance from the scan point to the model surface, positive outside the material, negative inside. If the model is not exactly one closed solid the sign is undefined (open shell) or ambiguous (several solids, which may overlap), and the tool reports unsigned distances and says so.

## Facts from the probe (scratch environment, Windows, Python 3.12)

* STEP to mesh: `OCP` (the `cadquery-ocp-novtk` wheel) reads the STEP (default unit mm), `BRepMesh_IncrementalMesh` tessellates, and the triangles feed Open3D. A 100 x 50 x 20 box round-trips with the exact volume.
* Distance, first probe: `open3d.t.geometry.RaycastingScene.compute_signed_distance` gave +0.049999 mm for points 0.05 mm outside the top face and -0.1 mm for points 0.1 mm inside. 1000 queries took 3 ms.
* Distance, as built: that call is float32 and was wrong on long or thin triangles (a 3 m plate with every scan point exactly on the face reported rms 0.035 mm, max 0.309 mm and 48 false regions; a 30 mm cylinder at deflection 0.001 mm was off by about 2 micrometres; a 100 mm box 10 m from the origin by 9e-5 mm). The tool now moves model and points to the mesh centre before any float32 cast, uses Open3D only to name the closest triangle (`compute_closest_points`, `primitive_ids`), recomputes the unsigned distance in float64 against that triangle and every triangle sharing a vertex with it, and takes the sign from a three-ray vote (`compute_occupancy(nsamples=3)`). The same inputs now agree with a brute-force float64 distance over every triangle to about 2e-15 mm, and a point exactly on the 3 m plate reads 0 (a barycentric projection instead of the plane projection left up to 7e-10 mm on a 3 m triangle).
* Open3D 0.20 is 231 MB and its import takes several seconds on a cold start. `cadquery-ocp-novtk` is 108 MB. Open3D also pulls dash, plotly, flask, nbformat and scikit-learn. All of these are permissive.
* `build123d` would give a nicer STEP API but adds `ocpsvg` and `bd_materials`, which declare no licence at all. Using OCP directly avoids them.
* Windows path length matters: OCP failed to load from a very deep virtual environment path (DLL load error "file name too long"), and worked from a short one.

## Decisions

1. **Engines.** Open3D for registration and distance, OCP for reading and tessellating STEP, as the design document says. The wrapper is Apache-2.0 but the wheel bundles OCCT, which is LGPL-2.1 with an exception. Under the licence policy that is `lgpl-dynamic`: unmodified, dynamically linked, not vendored in our repository. We declare that tier honestly in `licenses.KNOWN` and record the bundled licence, instead of letting the Apache metadata make it look permissive. The doctor accepts that declared-versus-detected gap only for `lgpl-dynamic` declared against a permissive wrapper; an installed engine declared `gpl-isolated` or `forbidden` always fails the doctor, whatever it bundles. The OCP wheel also bundles other dual-licensed libraries (reported by review: FreeImage, LibRaw, FreeType, zstd, each with a non-GPL option); redistribution should verify them.
2. **Optional extra, not a core dependency.** About 340 MB and a slow import should not be paid by every user who only wants fits and calculations. They go in the optional dependency extra `scan` and nowhere else (not in the `dev` group), so a plain `uv sync` or `uv run eng-toolkit` does not install them; development uses `uv sync --all-groups --extra scan`, and the scan tests skip when the engines are missing. The tool is always registered. If the engines are missing it returns a structured error `engine.unavailable` with the install command. `toolkit_doctor` lists optional engines that are not installed as informational, not as a failure. The Windows install script installs the extra, so the one-click install still has every engine.
3. **Reuse the OCP mesh, no second STEP reader.** The model is meshed once with a linear deflection the caller can set (default 0.01 mm). The deflection is an error bound on curved surfaces, so it is reported in the result and the warnings say deviations below it are not meaningful.
4. **Registration is opt-in and local.** `registration="none"` (default) trusts that the scan is already in the model frame. `"icp"` refines with point-to-plane ICP from the identity, with a maximum correspondence distance the caller can set. Global registration (RANSAC with FPFH, TEASER++) for unaligned scans is out of scope for this step. The transform, fitness and RMSE are returned so the agent can judge it. Fitness is the share of scan points whose distance to the model surface after registration is within the search distance (Open3D's own fitness is measured against 100k sampled model points and read 0.136 on a perfectly aligned 1 x 1 m plate). The transform is estimated from at most 200000 points (seeded subset) and applied to all. Best fit describes form, not location: it can absorb a real defect (a 0.2 mm plateau on 28 percent of a face dropped from 22 percent to 3 percent out of tolerance), so every ICP result warns. The normal matrix of the registered inlier points (rows `[r x n, n]`, `r` relative to the centroid in units of the RMS radius) names directions it leaves free when its smallest eigenvalue is below 1e-3 of its largest; one plane leaves in-plane translation and the rotation about the normal free, three non-parallel faces leave nothing. ICP results can differ in the 1e-5 mm range between identical runs (multithreaded float summation).
5. **The scan has no unit, so the caller must give one.** `scan_unit` is a required string such as "mm", "m" or "inch", parsed as a unit only: any numeric part ("2 mm", "0 mm", "mm*2") is `input.invalid`, because a magnitude would silently rescale the scan. STEP files carry their own unit and are read in millimetres. Several solids in one STEP, or a root entity that yields no faces, are reported (`data.mesh.solids`, `data.mesh.step_roots`).
6. **Coarse tool, few primitive parameters.** One tool with string, boolean and integer parameters. Large clouds are downsampled to `max_points` with a fixed seed, so results are reproducible. Out-of-tolerance points are grouped into regions with DBSCAN over their distinct positions (coordinates equal to 1e-6 mm count once), with eps five times the median nearest-neighbour spacing of the whole deduplicated scan, so repeated points neither shrink eps nor inflate a region. A uniformly sampled patch was split into 6 to 41 regions at a factor of 3 and stayed one region at 5, which is why the factor is 5. The spacing comes from the whole scan, so a scan whose points sit in near-duplicate layers (0.08 mm apart on a 4 mm grid) gets an eps too small to join the grid; the agent gets "two regions, here and here" instead of a point list. The full list goes to a CSV artifact.

## Alternatives considered

1. **trimesh plus scipy instead of Open3D.** 2 MB plus scipy, and trimesh closest-point is a Python loop over an R-tree, slow beyond about 1e5 points. It would also need our own ICP. Kept in mind as the fallback if Open3D ever becomes impractical, but the design document and the speed favour Open3D.
2. **Make the engines core dependencies.** Simplest to describe, but every install gets 340 MB and a slow import, and every `uv sync` in CI downloads them. Rejected for the optional extra.
3. **build123d for STEP.** Less code, more dependencies including two with no declared licence. Rejected.
4. **Return the full per-point deviation list in the result.** Heavy for a model that reads it. Rejected for statistics, regions and a CSV artifact.

## Tradeoffs accepted

* The tool is registered even when it cannot run, in exchange for a clear error and a stable tool list.
* Deviations are measured to the tessellated mesh, not to the exact B-Rep surface. The error is bounded by the mesh deflection, which is reported.
* The search for the closest triangle is still float32. Distances are exact float64 to the chosen triangle and its neighbours (below 1e-9 mm in the tests), but a degenerate mesh can defeat the search.
* A truncated PLY (ASCII or binary, either endianness) is rejected by comparing the header with the file; a truncated PCD or other format is only caught when it produces non-finite or beyond-1e7 mm coordinates.
* ICP from the identity only converges for scans that are already close to the model, and its deviations do not locate the part.

## Process notes

* `how`: the existing gateway, licence registry, doctor and tool code were read earlier in the session. The engine facts above were probed for this feature.
* `architect`: the arena fan-out was not run. The design is settled by the facts above and the alternatives listed.
* Implementation is delegated to a `poteto-agent` subagent. The design is contested enough (licence tier, optional dependency), so a skeptical review of the diff follows before shipping.
