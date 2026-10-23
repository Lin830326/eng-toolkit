# Geometry tool behaviour

All results remain `review_status: "unreviewed"`.

* Measure, view, convert and make-part report convergence-checked BREP integrals.
  An integral that does not converge is refused.
* Bounding boxes and derived sizes are conservative enclosures, including model
  tolerances and spline control hulls. They are not exact part dimensions.
* All geometry tools refuse nonfinite coordinates or models extending beyond
  1 km from the origin. Bounds near that limit can cause conservative refusals.
* Density is limited to `1e6 kg/m^3`, mass to `1e18 kg`, and mesh deflection to
  `0.001..100 mm`. Invalid arithmetic names the affected parameter.
* DFM detects thin planar walls in long parts, blind-hole floors and cylindrical
  walls whose faces overlap only in part. A neutral face still does not mean a
  complete manufacturability check.
* Fine spline meshes can be refused before meshing. Freeform meshing also has a
  separate 1 GiB, 30 s worker budget; use a coarser deflection after a budget refusal.

See [design and verification details](design.md).
