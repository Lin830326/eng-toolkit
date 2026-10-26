# Shared geometry and BREP numerical contract

This update supersedes the bounding-box, integration and freeform-meshing claims in
the earlier [view](../geometry-view/design.md), [conversion](../geometry-convert/design.md),
[DFM](../geometry-dfm/design.md) and [part](../geometry-drawing/design.md) designs.
It records the second independent review against commit `472d99c`.

## Integration

Volume, total surface area and face areas use the tolerance-taking `BRepGProp`
overloads. Each integral is recomputed at progressively tighter tolerances from
`1e-6` through `1e-20`. Success requires successive masses to agree within `1e-8`
relative and centroids within `1e-7 mm`, as well as a finite kernel error estimate
at most `1e-8`. The kernel's error estimate alone is insufficient: a rational
sphere can have a much larger actual error. Exhausting refinement returns
`geometry.integration_not_converged` with the calling tool's input pointer.

These are numerical integrals over exact surfaces. Existing fields named
`area_exact_mm2` retain their names for compatibility and disclose the integration
method. They do not claim symbolic or infinite-precision integration.

For the exact rational sphere of radius 10 mm, the independent references are
`4*pi*10^3/3 = 4188.790204786391 mm^3` and
`4*pi*10^2 = 1256.6370614359173 mm^2`. The original default integration returned
4186.95825179 and 1261.28521567 respectively.

## Conservative bounds and numeric limits

`BRepBndLib.Add` uses triangulation when present, with its deflection and topology
tolerances included in the box gap; otherwise it uses analytic geometry and
control hulls. Floating-point endpoints are rounded outward. `AddOptimal` is no
longer used: it excluded part of the rational sphere.

Bounds and the sizes derived from them are explicitly labelled conservative.
Spline control hulls can be substantially larger than the actual shape, so a
model near the coordinate limit can be refused conservatively. The limit is
1,000,000 mm in each absolute coordinate, allowing 1e-6 mm for kernel tolerance
padding at the boundary. Nonfinite coordinates are refused. Measure and DFM now
perform this check immediately after import, before integration or inventory;
view, conversion and part creation share it.

Density must be positive and no greater than `1e6 kg/m^3` after conversion. Mass
must be finite and at most `1e18 kg`. These are numerical support limits, not
material-property claims. Volume converts to cubic metres before multiplication.
Output length scale must be between `1e-12` and `1e12` output units per millimetre.
Mesh deflection remains `0.001..100 mm`. Arithmetic errors at these boundaries
return `input.out_of_range` at `/density`, `/output_unit` or `/mesh_deflection`.

## Thin material

Planar wall rays start at an offset limited by the candidate gap, and verify that
the start lies in material. Bore end rays start inside the bore air, before the
end plane, so they cannot jump through a blind bottom. Their offset depends on
the bore radius and axial length, not the whole model diagonal.

Coaxial cylinder pairs use their common axial interval and angular intervals,
including wrapped angular seams and reversed axis directions. A sample must lie
on both trimmed faces. A ray from material just outside the inner face must hit
the outer face at the radius difference. Identical endpoints or angular spans
are no longer required. Finding gaps remain exact plane offsets or radius
differences; sampled coverage is not a proof that every wall has been found.

Two cylinders are on one axis when their axes are parallel (|sin| at most 1e-6)
and the perpendicular offset between them is at most `brep.coaxial_tolerance_mm(gap)`,
1e-3 of the radius difference and at least 1e-6 mm; the confirming radial ray may
differ from the radius difference by the same amount. A bend drawn through
3-point arcs whose via points are rounded to 4 decimals (user-acceptance scenario
C4) has its inner and outer cylinders about 7e-5 mm apart; the former absolute
1e-6 mm rejected it, so the bend went unseen. 1e-3 of a 3 mm sheet is 3 um, a
hundredth of a sheet's own thickness tolerance.

Faces that are in no exact plane or cylinder pair (tapered fins, a hole near a
face, cones, free-form faces) are measured by `brep.normal_walls`: up to 12 sample
points per face, mesh-triangle centres spread by farthest-point selection (so a
hole wall is sampled all the way round) and projected onto the face, each cast a
ray from 1e-5 mm inside the material along the inward normal. The ray's length in
the material is the chord; the shortest chord per face pair is kept. Pairs whose
chord is at most twice `min_wall` (and the shortest of all) are confirmed (the
chord's middle is in the material) and refined: when the exact `BRepExtrema`
closest distance between the two faces runs within 1 degree of both face normals
and through the material, it replaces the chord and the wall is exact. A chord is
an upper bound of the local wall; a thin spot between sample points can be missed.

## Thickness queries

`geometry_measure 'thickness P(x, y, z) <axis>'` classifies the start point first
(`BRepClass3d_SolidClassifier`, 1e-6 mm), then reports the material the ray's line
crosses (`brep.wall_segment`):

* outside: between the first two exact hits along the axis (as before);
* inside the material: the wall that contains the point, from the nearest face
  behind it to the nearest face ahead of it. Before this the "first two hits" were
  the far side of the wall and the next face, so the result was the next gap
  (8.864 mm, a fin slot, for a point inside a 3.136 mm fin in scenario C1);
* on a face: the wall that starts there, ahead when the ray enters the material,
  behind when it leaves it.

Each query entry gives `start` (`outside`, `inside`, `surface`), `entry_mm` and
`exit_mm`; a start inside the material, or a wall behind a surface point, adds a
warning that says so.

## Freeform meshing resources

The curvature estimate now also reserves work proportional to spline knot-span
count, degrees, rational weight ratio and conservative surface size divided by
deflection. It rejects the R30 rational sphere at 0.001 mm before meshing, and
covers the measured 53,652 triangles for R10 at 0.005 mm. This is a conservative
preflight estimate, not a proven upper bound on OCCT's algorithm.

Freeform surfaces therefore mesh in a separate Python process. It has a hard
1 GiB allocation ceiling, using a Windows Job Object or POSIX address-space limit,
a 30 s wall-clock ceiling, and a 600,000 triangle cap checked after each face.
The parent also polls any caller deadline and kills and reaps the worker on
expiry. Failure to establish the memory limit refuses the operation. Memory or
worker-time exhaustion returns `geometry.mesh_budget_exceeded` at
`/mesh_deflection`; a caller's deadline keeps its `/max_seconds` refusal.

The worker runs fixed toolkit code and receives a toolkit-written BREP copy.
It inherits the configured Python executable and temporary-directory settings.
It executes no caller-supplied code. Meshed BREP serialization preserves face and
edge identity. The same containment covers view, conversion, part mesh export and
DFM sampling of freeform shapes. Analytic meshing retains its existing preflight,
face-level checks and limitations. Native model import and integration still run
in process; this change is meshing containment, not an input-file sandbox.

## Verification

`tests/test_geometry_review2.py` reproduces every numbered finding through
`Client(gateway)`. References use sphere formulas and construction dimensions:
0.5 mm plate, 19.5 mm blind bore above a 0.5 mm floor, and the 0.5 mm cylindrical
wall from radii 10 and 9.5 over the shared axial interval 5..20 mm. Existing
primitive-box assertions now allow the included topology tolerance instead of
requiring a tolerance-free enclosure.
