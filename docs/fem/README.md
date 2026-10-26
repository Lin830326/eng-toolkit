# FEM input and display conventions

`fem_mesh` and `fem_static` accept one solid and produce quadratic tetrahedra.
The mesh readers reject unsupported volume elements, including mixed first-order
tetrahedra and hexahedra. Only recognized point, line and surface elements may be
ignored. Mesh quality checks include all four CalculiX integration points and
the volume quadrature points, corners and edge midpoints. Sampling does not prove
that every point inside a curved element has a positive Jacobian.

`fem_static` sums overlapping force and pressure tractions on each selected facet
before applying its limit of ten times Young's modulus. Loads that cancel to
zero nodal force are refused at `/loads`. Nonzero tiny forces use scaled norms.
Poisson ratios near the singular endpoints retain round-trip precision within
CalculiX's field width. Such values can still produce ill-conditioned solves.
Computed displacements and stresses above 1e9 mm and 1e9 MPa are refused.

Output-unit conversion factors from mm or MPa must lie between 1e-30 and 1e30.
Converted results must be finite, at most 1e30 in magnitude, and preserve nonzero
values. Refusals identify the relevant parameter with a JSON pointer.

`fem_view` stores GLB vertex positions relative to the model bounding-box centre.
The node translation restores global coordinates; it applies to the surface,
mesh edges and undeformed outline. Extras and `view.json` record both the model
origin in millimetres and the translation in glTF metres. This preserves small
parts far from the origin. A shape whose triangles still lose precision in
float32 is refused.

Every diverging colour range is symmetric about zero, so light grey means zero.
For example, `range="-100 MPa, 200 MPa"` with `field="s_xx"` expands to
`-200 MPa, 200 MPa`. The warning, legend and range metadata describe the displayed
range. Sequential colour ranges retain the specified endpoints.

`fem_view` also writes `fem_view.png` (parameter `preview`, default `png`; `none` writes the GLB only): an isometric picture of the
same colours with a colour bar (field, unit, range, min and max), the size of the model in mm and a ring on the largest value (and on
the smallest of a signed field), for people who cannot open a GLB. It is drawn with numpy alone; a picture that cannot be drawn is
a warning and the GLB is still written. Hand the PNG over first; see [design.md](design.md#the-png-preview-preview).

All results remain `review_status: "unreviewed"`. See [design.md](design.md) for
equations, verification cases, solver limits and interpretation of nodal stress.
