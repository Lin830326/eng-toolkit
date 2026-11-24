# Structures domain

Six tools provide closed forms and linear structural analysis using NumPy and Pint.
They register through `DOMAIN` discovery and return the existing `ToolResult`
contract. Every result is `review_status: "unreviewed"`. Successful results include
formulas, assumptions, validity and the standard disclaimer. Refusals have a stable
error family and a JSON pointer in `error.details.param`.

## Input boundary and units

Every physical value is a string with units, including zero. Examples are `200 GPa`,
`1e6 mm^4`, `0 N`, `10 kN m`, `1 kip`, `100 psi`, `0 rad`, and
`0.001 dimensionless`. Coefficients and counts are basic JSON numbers. `unit_system`
selects `metric` (m, N, Pa) or `imperial` (inch, lbf, psi); rotations use rad or deg.
CSV artifacts always identify their SI columns explicitly. Mode-shape coefficients
have the normalization stated in their table metadata.

Each parameter has its own short schema description naming its physical role,
units and an example, with mode requirements and the meaning of an empty string.
Quantity descriptions within a tool must be distinct; gateway tests enforce this.

Records have comma-separated fields and semicolon or newline separators. Do not
include a header, comments, quoting, blank records or a trailing separator. Whitespace
around fields is accepted. IDs are 1..32 ASCII letters, digits, underscores or hyphens,
starting with a letter, digit or underscore. IDs are case-sensitive. Duplicate node,
member, support, thermal or mass definitions are refused; separate load records add.

**Record refusals show the grammar.** Every refusal that points into a record (a wrong field count, an empty field, a unit
error, an unknown member or node id, a bad `uniform`/`point` keyword) names the record kind (the parameter), the
number of the record (and its line when the text has several lines), what was received (clipped to 80 characters, control
characters shown as `?`), the expected fields in order, and an example. Example for the record that cost round 2
(S1) three tries, a point load with the optional moment field a 2D member load does not have:

```text
input.invalid /member_loads/0: expected 6 comma-separated fields but got 7 [member_loads record 1: received
'e,point,global,2 m,0 N,-11772 N,0 N*m'; expected member,uniform,local|global,qx,qy | member,linear,local|global,qx0,qy0,qx1,qy1 |
member,point,local|global,a,Fx,Fy (a is the distance from the start node); e.g. 'e,uniform,local,0 N/m,-1 kN/m' or 'e,point,global,2 m,0 N,-5 kN']
```

The grammar table is `GRAMMAR` in `_common.py` (one per tool: truss, 3D foundation and plate have their own `nodes`, `members`,
`supports`, `loads`, `thermal`, `point_loads` rows, everything else falls back to the frame row). `records` remembers the rows of the
call in a context variable; `invalid` finds the row from the JSON pointer, so every parser (nodes, members, supports, loads,
member loads, shear properties, thermal, releases, orientations, masses, section polygons) gets the note without its own code.
`tests/test_structures_record_errors.py` fires every record kind, checks the note, and parses every example of every grammar
row through the tools so an example cannot rot.

Each text parameter is limited to 60000 characters, each record to 1000, each
quantity to 120 and its unit expression to 64. Unit expressions permit named units,
products, quotients and integer powers -99..99. They do not permit numeric
arithmetic, parentheses or function calls. The parser checks these bounds before
calling `eng_toolkit.units.parse_quantity`. Nothing supplied by a caller is evaluated
as Python. Record splitting and bounded token matching avoid unbounded regex work.

The following are SI bounds on **nonzero absolute magnitudes**. Zero is accepted
only for signed quantities or explicitly nonnegative inputs. Positive dimensions,
moduli and section properties reject zero and negative values. Input numbers above
1e100 are refused before unit conversion; `1e308` therefore returns
`input.out_of_range`. The lower bound is checked after conversion to SI as well as
at literal parsing. A nonzero literal that converts below the minimum or underflows
to exactly zero is refused with `input.out_of_range` at that parameter's JSON pointer.
For example, `1e-300 yPa` cannot become a zero shear stress. Genuine literal zeros,
including `0 yPa`, remain valid wherever zero is allowed.

| Quantity | Minimum | Maximum |
|---|---:|---:|
| Length, coordinates (m) | 1e-9 | 1e4 |
| Area (m²) | 1e-14 | 1e4 |
| Second moment (m⁴) | 1e-24 | 1e8 |
| Stress, E, G (Pa) | 1e-6 | 1e13 |
| Force (N) | 1e-12 | 1e12 |
| Moment (N m) | 1e-12 | 1e15 |
| Line load (N/m) | 1e-12 | 1e12 |
| Translation spring (N/m) | 1e-9 | 1e16 |
| Rotation spring (N m/rad) | 1e-9 | 1e18 |
| Density (kg/m³) | 1e-9 | 1e6 |
| Distributed mass (kg/m) | 1e-12 | 1e9 |
| Nodal mass (kg) | 1e-12 | 1e12 |
| Nodal rotary inertia (kg m²) | 1e-18 | 1e16 |
| Angle (rad) | 1e-14 | 2 pi |
| Strain | 1e-15 | 1 |
| Temperature change (K) | 1e-12 | 1e4 |
| Thermal expansion (1/K) | 1e-15 | .01 |
| Thermal gradient (K/m) | 1e-12 | 1e8 |

Temperature differences use `K`, `delta_degC` or `delta_degF`, not absolute Celsius
or Fahrenheit. Each parsed unit factor is checked for an offset before Pint can
rewrite product units as delta units. This covers aliases such as `celsius`,
`degree_Celsius`, `fahrenheit` and `degree_Fahrenheit`, also in gradients like
`celsius/m` and inverse expansion units. A parsed temperature unit whose zero
converts to a nonzero kelvin value is an offset unit and is refused. Kelvin aliases
and explicit delta units are accepted, including `0 K` and `0 delta_degC/m`.
Geometry also checks member lengths and section topology. Numerical
conditioning can cause a refusal even when individual values satisfy these bounds.
Outputs and CSV cells are checked for finiteness and magnitude <=1e100. No NaN,
infinity or unbounded safety factor is serialized. Zero demand produces a null
factor with a reason.

## `structures_section`

Choose exactly one shape description:

| `shape` | Required dimensions |
|---|---|
| `rectangle` | `width`, `height` |
| `circle` | `diameter` |
| `hollow_circle` | `diameter`, radial wall `thickness` |
| `hollow_rectangle` | `width`, `height`, uniform `thickness` |
| `i`, `h` | `width`, `height`, `web_thickness`, `flange_thickness` |
| `t`, `channel` | Same four dimensions; T flange is at the top, channel web at the left |
| `polygon` | `polygon`; optional `holes` |

Standard polygonal shapes have their bottom at y=0 and x centred on the outer width.
Circles are centred at (0,0). For example:

```text
shape = polygon
polygon = 0 mm,0 mm;100 mm,0 mm;100 mm,200 mm;0 mm,200 mm
holes = 20 mm,20 mm;80 mm,20 mm;80 mm,180 mm;20 mm,180 mm
```

Separate multiple hole rings with `|`. Either winding is accepted. A repeated final
vertex matching the first is accepted. There are at most 200 vertices total and
16 holes. Self intersections, touching or overlapping rings, duplicate/overlapping
edges, zero area, holes outside the outer boundary and nested holes are refused.
Topology checks normalize by outer span and use a 1e-12 tolerance. Tiny features
below that relative resolution are not supported. Net polygon area must exceed
1e-14 m². Separate composite rectangle/circle syntax is not implemented; connected
polygonal composites can be represented by their outer boundary and holes. Detached
pieces and exact curved composite boundaries are outside this interface.

Green's theorem gives signed integrals for each edge `(x,y)` to `(X,Y)` with
`c=xY-Xy`:

```text
A = sum(c)/2
Cx = sum((x+X)c)/(6A), Cy = sum((y+Y)c)/(6A)
Ix = sum((y²+yY+Y²)c)/12
Iy = sum((x²+xX+X²)c)/12
Ixy = sum((2xy+xY+Xy+2XY)c)/24
```

The implementation first translates to a nearby vertex to find the centroid, then
integrates again after translating to the centroid. It never subtracts two large
origin moments to obtain a small centroidal moment. Holes subtract their integrals.
Standard I/T/channel shapes use nonoverlapping rectangles and parallel-axis sums
through the same integration routine. Circles use exact polar integrals.

```text
I1,2 = (Ix+Iy)/2 +/- hypot((Ix-Iy)/2,Ixy)
theta1 = atan2(-2Ixy,Ix-Iy)/2
rx = sqrt(Ix/A), ry = sqrt(Iy/A), J_polar = Ix+Iy
Sx_top/bottom = Ix/(distance to extreme y fibre)
Sy_left/right = Iy/(distance to extreme x fibre)
```

The displayed principal-moment equation is the analytical identity. Numerically,
the solver scales `[[Ix,-Ixy],[-Ixy,Iy]]` by its largest absolute entry and uses
`numpy.linalg.eigvalsh`, then restores the scale. It does not subtract nearly equal
trace and radius terms to obtain I2. For a 1 m by 30 nm rectangle, I2 is
`1*(30e-9)^3/12 = 2.25e-24 m^4`. The principal-angle convention is unchanged.

`theta1` rotates the x axis counterclockwise to the axis with moment I1; an equal
principal pair has no unique principal direction. `Ixy=integral(x*y)dA`, with no
minus sign. The axis moduli are geometric Ix/c and Iy/c; unsymmetrical bending
with nonzero Ixy requires coupled stress recovery.

Torsion methods are deliberately distinct from `J_polar`:

* Circular solid/tube: exact Saint-Venant `J=pi(D^4-d^4)/32`.
* Solid rectangle: Saint-Venant Fourier series, with b the larger side and t the
  smaller, `J=b*t^3/3*[1-192*t/(pi^5*b)*sum_odd(tanh(n*pi*b/(2*t))/n^5)]`.
  The implementation retains 101 odd terms. This is a truncated analytical series,
  not an empirical constant; for a square `J/a^4` is approximately 0.14057701495.
* Hollow rectangle: thin-wall single-cell Bredt `J=4*Am^2/integral(ds/t)`, with
  median dimensions `(b-t,h-t)`. No corner corrections.
* I/H/T/channel: thin open-wall approximation `J=sum(b_i*t_i^3)/3`, counting
  flanges at full width and the clear web height. No junction or warping corrections.

For I/H/T/channel torsion, both web thickness/clear web height and flange
thickness/clear flange outstand must be <=0.25. Clear web height is `h-2*tf`, or
`h-tf` for T; clear flange outstand is `(b-tw)/2`, or `b-tw` for a channel. This
threshold checks individual plates and excludes thick junctions; near 0.25 the sum
b t^3/3 overstates the true J by roughly 15 % (no junction or fillet correction).
Outside it, `torsion_constant` is omitted from values, data records null, and a
warning states that the thin-wall formula is invalid. Area, centroid and moments
remain available. For the 1 m square I section with tw=.999 m and tf=.001 m,
the former 0.331669665 m^4 estimate is therefore not returned.
Hollow-rectangle Bredt torsion retains its warning when t/median span exceeds .1.
General polygon torsion is null with a warning; the polar area moment is never
substituted for Saint-Venant torsion.

Plastic Zx/Zy and shape factors are provided for rectangle, circle, hollow circle,
hollow rectangle and I/H. Their plastic neutral axes coincide with symmetry axes.
`Zx=integral(abs(y-yc))dA`, with the corresponding x integral for Zy. Circle
`Z=4(R^3-r^3)/3`. Shape factor is plastic Z divided by elastic S. T/channel/general
polygon plastic properties are not claimed.

Optional `at_height` is an absolute y coordinate. The tool clips the section above
that height and evaluates `Q=integral(y-yc)dA`. Optional `shear_force` adds `VQ/Ix`.
This is the total longitudinal shear resultant per length across that cut; it does
not distribute flow between multiple webs or solve closed-cell shear circulation.

## `structures_truss`

```text
dimension = 2                         # or 3
nodes = a,0 m,0 m;b,2 m,0 m
members = e,a,b,200 GPa,100 mm^2        # optional sixth field I
supports = a,xy;b,y                    # restrained global directions
loads = b,10 kN,0 N                    # 3D adds Fz; nodes add z
thermal = e,100 K,0.00001 1/K           # member,dT,alpha
```

These example comment lines explain the grammar; comments are not accepted in
record strings. There are at most 200 nodes, 400 members, 200 support records and
400 nodal-load or thermal records. Member IDs and node references must exist.
Coincident endpoints are refused. Global `temperature_change` and
`thermal_expansion` must be supplied together and provide defaults; per-member
`thermal` records override them. Absolute initial strain must not exceed .1.

For length L, unit direction n, `b=[-n,n]`, and initial strain epsilon0:

```text
ke = EA/L * b*b^T
f_thermal = EA*epsilon0*b
extension = b^T*u
N = EA*(extension/L-epsilon0), sigma=N/A
```

Support elimination precedes the dense NumPy solve. Results include signed total
and elastic elongations, N and stress with tension positive, labels, node
displacements/reactions, force equilibrium and moment residuals about the first
input node. The 3D result also includes all three moment components. If I is supplied
for a compression member, `Pcr=pi²EI/(buckling_factor*L)²` is reported, default K=1.
The caller supplies I about the relevant buckling axis. This is not a frame-column
or code capacity check. Elongation/length or elastic strain above .05 generates a
small-displacement warning.

### Loads on a sloped member: pressure normal to the surface

`structures_truss` takes joint loads only; a pressure `p` (wind, snow normal to the roof) on a sloped chord is resolved by hand into
joint forces. There is no `member_pressure` record: a pressure needs a tributary width and a sign convention for the normal, which
the frame `member_loads` already give for beams (`uniform,local`) and which a pin-jointed chord cannot carry between joints
without bending, so the half-and-half joint split is the model. Recipe:

1. For each loaded chord from joint i to j: `dx = xj - xi`, `dy = yj - yi`, `L = hypot(dx, dy)`. The resultant is `R = p * w * L`, with `w` the tributary width (truss spacing) and `p` positive pointing away from the surface.
2. The outward normal of a chord that runs left to right (dx > 0) over a roof is `n = (-dy, dx) / L`. Then `R n = p w (-dy, dx)`: half goes to each end joint, `Fx = p w (-dy) / 2` and `Fy = p w dx / 2`.
3. Add the halves of the chords that meet at a joint; `loads` records for the same joint add. The eave joints (the ends of the first and last chord) carry half a panel each. They sit on supports, but their vertical share goes straight to the support and their horizontal share can load the bottom chord through a roller, so leaving them out changes member forces (round 2, S2: the bottom chord end panel was -14.6 kN instead of -13.5 kN).
4. Compare `data.applied_total_fx` and `data.applied_total_fy` (and `data.loaded_nodes`) with your hand total `sum(p w (-dy))` and `sum(p w dx)` over all loaded chords. A joint left out of `loads` shows as a shortfall there. The reactions cannot show it: they always equal minus what was applied.

Example, a symmetric roof with ridge b at (2 m, 1 m), eaves a (0, 0) and c (4 m, 0), `p w = 3 kN/m` (p = 1 kN/m2, w = 3 m):

```text
chord ab: dx = 2 m, dy = 1 m   half = ( -1.5 kN, 3.0 kN) at a and at b
chord bc: dx = 2 m, dy = -1 m  half = (  1.5 kN, 3.0 kN) at b and at c
loads = a,-1.5 kN,3 kN;b,0 N,6 kN;c,1.5 kN,3 kN        applied_total = (0 kN, 12 kN)
```

`structures_truss` returns `data.applied_total_fx/fy[/fz]` (the sum of the joint loads given, supports included, thermal initial strains
not included), `data.reaction_total_fx/fy[/fz]`, `data.loaded_nodes` (joint ids with a nonzero load) and a `data.load_check` sentence.

## `structures_frame`

The frame and modal tools share `_model.py`. SI model records, transformations,
element stiffness, support interpretation and node/member ownership have one
implementation. A stateful solver/cache object was rejected because it would make
static condensation and dynamic private rotations depend on call order.

```text
nodes = a,0 m,0 m;b,2 m,0 m
members = e,a,b,200 GPa,1000 mm^2,1e6 mm^4
supports = a,fixed
loads = b,0 N,-1 kN,0 N*m
member_loads = e,uniform,local,0 N/m,-1 kN/m
```

Member fields are `id,start,end,E,A,I[,release]`, with release `none`, `start`, `end`
or `both`. Releases concern bending rotations only. Supports accept:

```text
node,fixed[,ux,uy,rotation]
node,pinned[,ux,uy]
node,roller_x[,ux]
node,roller_y[,uy]
node,spring,kx,ky,kr[,rest_x,rest_y,rest_rotation]
```

Spring record, with units (round 1 S4: a finite-stiffness brace; the example is in the parameter description too): `c,spring,5 kN/m,0 N/m,0 N*m/rad` is kx, ky, krot, and a spring that acts in one direction only has `0 N/m` for the others. The `structures3d_*` form `c,spring,ux,1000 kN/m,0 m` (axis, stiffness, rest position) is a different grammar and is refused here with the 2D grammar in the message.

`roller_x` restrains ux only (a reaction in x; the joint slides in y, rotates freely); `roller_y` restrains uy only (a vertical reaction; the joint slides in x: the usual beam roller). Neither restrains rotation. Spring
stiffnesses are nonnegative, in N/m, N/m and N m/rad. All optional prescribed/rest
values must be supplied for the selected support. One support record per node is
accepted; mixed fixed/spring components at a single node are not represented.
`structures_frame` returns `max_member_deflection`, the largest magnitude of the local
transverse displacement along any member (end displacements included, not relative to the
chord), found on the exact displacement curve (dense sampling plus golden-section
refinement), with the member, the position and the signed value in
`data.max_member_deflection_location` and per member `maximum_abs_deflection`. The nodal
maximum `maximum_nodal_displacement` is only the largest joint displacement: a warning
says so when the member maximum exceeds it (a simply supported 2 m beam with a 1 kN load
0.5 m from a support: 4.69e-4 m at the load, 5.82e-4 m in the span). `structures_modal`
puts every returned frequency in `values` as `frequency_mode_<k>` besides
`first_frequency`.
Nodal loads are `node,Fx,Fy,Mz`. Positive Mz and rotation are counterclockwise.

Member loads are:

```text
member,uniform,local|global,qx,qy
member,linear,local|global,qx_start,qy_start,qx_end,qy_end
member,point,local|global,distance_from_start,Px,Py
```

Distributed loads are per actual member length, including global-axis loads, not
per projected length. Point loads may be at endpoints. Point member moments are
not supported; insert a node and apply a nodal moment. Optional
`shear_properties` records are `member,G,effective_shear_area`. This selects
Timoshenko statics for that member. Without it, Euler-Bernoulli applies. Optional
`thermal` is `member,alpha,mean_dT,dT_dy`. Positive gradient means the local +y
fibre is hotter; epsilon0=alpha*dT and kappa0=-alpha*dT_dy. Initial strain or initial
curvature times member length above .1 is refused.

Limits are 100 nodes, 200 members, 100 support records, 400 nodal loads, 400 member
loads and 200 shear/thermal records. `stations` is 2..101, default 21. Each member
gets a CSV with additional load-jump and force-extremum stations.

Local displacement order is `[u_i,v_i,theta_i,u_j,v_j,theta_j]`, x from i to j,
y rotated 90 degrees counterclockwise. The transformation has two identical
3x3 blocks `[[c,s,0],[-s,c,0],[0,0,1]]`. The axial 2x2 stiffness is
`EA/L*[[1,-1],[-1,1]]`. Euler-Bernoulli bending stiffness is:

```text
EI/L^3 * [[12,    6L, -12,    6L],
          [6L,  4L², -6L,   2L²],
          [-12, -6L,  12,   -6L],
          [6L,  2L², -6L,   4L²]]
```

Timoshenko uses phi=12EI/(GAs L²), divides the bending block by (1+phi), and
replaces 4L² by (4+phi)L² and 2L² by (2-phi)L². This is the exact constant-property
static beam matrix, not a low-order shear-locking interpolation.

For released r and retained a local coordinates, static condensation gives
`Kaa-Kar*solve(Krr,Kra)` and `fa-Kar*solve(Krr,fr)`. Recovery solves
`dr=solve(Krr,fr-Kra*da)` before calculating all six end actions `Klocal*d-flocal`.
Released moments therefore vanish under member loads as well as nodal loads.
A global joint rotation with no stiffness and no load is explicitly inactive. A
moment applied to such an unconnected rotation is refused as a mechanism.

Fixed-end loads are derived from compatibility rather than a library of sign tables.
With start end actions ni,vi,mi and `Jp(x)=integral_0^x q(s)(x-s)^p/p! ds`:

```text
N(x) = -ni - J0_x(x)
V(x) =  vi + J0_y(x)
M(x) = -mi + vi*x + J1_y(x)
u(x) = ui + epsilon0*x + [-ni*x-J1_x(x)]/(EA)
theta(x) = theta_i + kappa0*x + [-mi*x+vi*x²/2+J2_y(x)]/(EI)
v(x) = vi_displacement + theta_i*x + kappa0*x²/2
       + [-mi*x²/2+vi*x³/6+J3_y(x)]/(EI)
       - [vi*x+J1_y(x)]/(GAs)
```

The last shear term is omitted for Euler-Bernoulli. Set all end displacements and
rotations to zero and solve the resulting axial equation and 2x2 bending compatibility
system. Negating those end actions gives the equivalent nodal load vector. For EB
uniform qy its bending entries are `[qyL/2,qyL²/12,qyL/2,-qyL²/12]`; uniform qx gives
`[qxL/2,qxL/2]`. For an EB point transverse force at xi=a/L, the entries are P times
`[1-3xi²+2xi³, L(xi-2xi²+xi³), 3xi²-2xi³, L(-xi²+xi³)]`.
For a linear load, Jp follows by integrating its constant and linear terms. A point
force contributes `P*(x-a)^p/p!` on the loaded side. These integrations also recover
exact member displacements for the supported loads, including their quartic/quintic
terms. Cubic interpolation alone would miss them.

End-force output means actions applied to the element, not a single internal-force
sign at both ends. CSV internal N is tension positive; V=dM/dx. Left/right rows
preserve interior point-force jumps. Endpoints use the limit inside the member;
an endpoint load transferred directly into a support does not create fictitious
internal shear outside the member. Reported maximum absolute M/V checks both ends, both
sides of loads and analytical stationary points. Deflection is tabulated; no claim
of a global exact deflection maximum is made.

The assembled system includes spring stiffness and rest-position forces. Prescribed
settlements use `Kff uf=Ff-Kfc uc`. Output reactions combine spring forces with
fixed-support residuals. Equilibrium sums all applied loads and reactions; moments
use the first input node as origin. Node outputs use the selected unit system;
member tables use SI. Translations relative to member length or rotations above .05
generate a small-displacement warning.

## Stability and conditioning

Static free stiffness is equilibrated with `D=diag(1/sqrt(diag(Kff)))`. A nonpositive
diagonal or smallest balanced eigenvalue <=1e-11 times max(1,largest eigenvalue)
is refused. The error points to `/supports` and names the most implicated node and
direction from the smallest eigenvector when available. The solver uses
`numpy.linalg.solve`, then checks a componentwise relative residual <=1e-7. It does
not add artificial stiffness. An ill-conditioned but mathematically stable model
can be refused because double precision cannot reliably resolve it.

## `structures_modal`

`nodes`, `members` and `supports` use exactly the frame grammar. Give `density` or
`mass_per_length`, not both. Optional `member_masses` records `member,mass_per_length`
override this default; without a global default, every member needs an override.
Optional `nodal_masses` accepts `node,translation_mass,rotary_inertia` or
`node,mass_x,mass_y,rotary_inertia`. Nodal masses are nonnegative; every member
requires positive distributed mass.

`elements_per_member` defaults to 4 and accepts 1..20. `modes_count` accepts 1..30;
if fewer dynamic DOFs exist, all available modes are returned. Refined models,
including private hinge rotations, are capped at 900 DOFs. End releases are applied
only at original member ends, never at artificial interior joints.

The consistent axial mass matrix is `mu L/6*[[2,1],[1,2]]`. Bending mass is:

```text
mu*L/420 * [[156,   22L,   54,  -13L],
            [22L,   4L²,  13L, -3L²],
            [54,    13L,  156, -22L],
            [-13L, -3L², -22L,  4L²]]
```

This is Euler-Bernoulli consistent translational inertia. It excludes distributed
cross-section rotary inertia and Timoshenko dynamics. Optional nodal rotary inertia
is retained. Shared geometry and stiffness are identical to frame EB statics.

Modal pin rotations are independent **private** DOFs carrying full consistent mass.
Static condensation of mass would be a Guyan approximation and could erase actual
pinned-beam modes; it is not used. Inactive global rotations with neither stiffness
nor mass are omitted. An active massless DOF is refused. Settlements do not alter
the unstressed linear eigenproblem and generate a warning when supplied.

Element strain energy supplies a factor `K=CᵀWC`. The three rows of C measure
`du=u_j-u_i`, `dtheta=theta_j-theta_i`, and
`dv-L*(theta_i+theta_j)/2`. Their positive weights are `EA/L`, `EI/L` and
`12EI/L^3`. Each positive spring adds a coordinate row with its stiffness as weight.
After constraints and diagonal mass equilibration, Cholesky gives M=LLᵀ.
`numpy.linalg.svd(sqrt(W)*C*L^-T)` supplies angular frequencies and right singular
vectors. This avoids squaring the condition number by forming the standard
stiffness eigenproblem. Transforming back yields mass-normalized modes
`phiᵀMphi=1`. No matrix inverse or runtime SciPy is used. Eigen residuals are
checked against the original K and M with a 1e-7 relative limit and reported.
Sign is fixed by the largest coefficient.

Frequency is sqrt(lambda)/(2pi) Hz; omega is sqrt(lambda) rad/s. Effective modal
mass fraction is `(phiᵀMr)²/[(phiᵀMphi)(rᵀMr)]` for a global unit translation at
unrestrained translational DOFs. The denominator is the corresponding active mass,
not the total mass including constrained DOFs. Fractions for a truncated mode set
need not sum to one. Zero-mode count is the number of active free DOFs minus the
rank of the row-normalized geometric compatibility constraints. Material stiffness,
section inertia and positive spring magnitudes do not enter this rank test.
For frames without releases, each connected component also gets three analytical
rigid motions: x/y translation and `(-y,x,1)` rotation about a local origin.
Their projection onto the fixed and spring restraints must give the same zero
count as the compatibility rank; disagreement is refused. With releases, private
rotations enter the compatibility constraints and may add mechanism modes.
A connected free-free plane frame has three rigid modes; disconnected components
and mechanisms get a warning. Weak positive springs and slender bending modes
remain positive, irrespective of the largest elastic eigenvalue.

Rank tolerances are `8*eps*max(matrix.shape)*largest_singular_value`; a positive
geometric singular value within 100 times that tolerance is considered ambiguous.
If rank is ambiguous, the independent rigid-mode check disagrees, or a positive
frequency is below the strain-factor numerical resolution, the result is an
`input.out_of_range` refusal with a parameter pointer. Unresolved elastic modes
are never converted to zero frequencies.

The mode CSV includes original nodes, generated `@member:index` nodes, and private
rotations. Its entries are normalized coefficients, not physical vibration amplitudes.
The convergence note asks the caller to refine and compare frequencies. For an
eligible single-member beam without springs or nodal masses, data includes analytical
bending frequencies `omega_n=beta_n² sqrt(EI/(mu L⁴))`. Cantilever beta values are
1.8751040687, 4.6940911330, 7.8547574382, numerical roots of `cos(beta)cosh(beta)=-1`;
simply supported beta values are exactly n*pi. Axial modes can interleave bending
modes, so the comparison list is explicitly bending-only.

## `structures_column`

Required quantities are `length`, `youngs_modulus`, `area`, `inertia`. Optional
`yield_strength` selects the Euler/Johnson range; `applied_load` gives capacity/load.
Standard `end_condition` values are `pinned-pinned` (K=1), `fixed-free` (2),
`fixed-fixed` (.5), and `fixed-pinned`. The latter solves `sin(z)-z*cos(z)=0` by
bracket-preserving bisection on (pi,3pi/2), giving z=4.493409457909064 and
K=pi/z=0.6991556596, conventionally quoted as 0.6992. It is not the rounded 0.699
engineering approximation. `custom` requires `effective_length_factor` in (0,10].
Supplying K alongside a standard end condition is a conflict.

```text
r = sqrt(I/A), slenderness = KL/r
Pe = pi²EI/(KL)²
transition = sqrt(2*pi²E/Sy)
Pj/A = Sy - Sy²*slenderness²/(4*pi²E)
```

Johnson's empirical parabolic estimate applies below the transition and is explicitly
labelled inelastic. At and above the transition, Euler stress is <=Sy/2. Without Sy,
the tool states that elastic applicability cannot be checked. No design-code
resistance factors, residual stresses, imperfections or local buckling checks apply.

Supply `eccentricity` and `extreme_fibre` together with Sy for pinned-pinned secant
analysis:

```text
sigma_max = P/A * [1 + (e*c/r²) / cos(KL/(2r)*sqrt(P/(EA)))]
```

Yield load is the bracketed root sigma_max=Sy below Pe. The initial upper bound is
`min(A*Sy, Pe*(1-1e-12))`, since eccentricity can only increase the axial stress.
The solver bisects, using a log-space geometric midpoint when a positive bracket
spans a ratio greater than 100. It stops when relative bracket width is <=1e-14 or
the stress residual is <=1e-12*Sy. Failure to converge within 400 iterations is an
`input.out_of_range` refusal; no unfinished bracket becomes a reported capacity.
Zero eccentricity may have no yield root below buckling, which returns null with a reason.
A root closer than the 1e-12 relative Euler endpoint bracket can be refused as
numerically unresolved. Applied loads at or above Pe are invalid for secant stress.
`estimated_capacity` is the minimum applicable Euler/Johnson/secant estimate. All
outputs are closed-form estimates, not AISC or Eurocode design checks.

## `structures_stress`

`state` is `plane_stress`, `plane_strain`, `three_dimensional`, `rosette_45` or
`rosette_60`. `input_type` is `stress` or `strain`. Physical component inputs are
explicit parameters, not executable expressions. Tension is positive. Positive
tau_xy acts in +y on the +x face. Angles rotate the axes counterclockwise.

Plane states take sigma_x, sigma_y, tau_xy or epsilon_x, epsilon_y, gamma_xy. 3D
requires all six sigma_x/y/z, tau_xy/yz/zx or epsilon_x/y/z, gamma_xy/yz/zx.
Zero components still need units. Engineering shear strain gamma is twice the
off-diagonal tensor strain. Stress and strain inputs cannot be mixed.
`youngs_modulus` is required for strain input and optional for stress input.
`poisson_ratio` is in [-.999,.4999].

Mohr transformation uses mean=(sx+sy)/2, d=(sx-sy)/2, R=hypot(d,txy):

```text
in-plane principal stresses = mean +/- R
principal angle = atan2(2*txy,sx-sy)/2
sx' = mean+d*cos(2theta)+txy*sin(2theta)
sy' = mean-d*cos(2theta)-txy*sin(2theta)
txy' = -d*sin(2theta)+txy*cos(2theta)
```

The symmetric 3D tensor is split into mean hydrostatic stress and a deviator.
`eigvalsh` solves the deviator, then the mean is added back for principal stresses.
Tresca and maximum shear use the deviatoric eigenvalue differences directly, so a
large hydrostatic offset cannot erase a small shear contribution. Adding the mean
back to absolute principal stresses is still subject to floating-point rounding.
Its characteristic cubic is
`s³-I1*s²+I2*s-I3=0`, where I1 is trace, I2 is the sum of principal pair products,
and I3 their product. Full 3D principal stresses are descending; plane stress
includes the zero out-of-plane principal. Both in-plane and absolute 3D maximum
shear are identified. A repeated in-plane principal pair has no unique angle.

Isotropic Hooke law is `epsilon=[(1+nu)sigma-nu*tr(sigma)I]/E`; the inverse is
`sigma=2G epsilon+lambda*tr(epsilon)I`, G=E/[2(1+nu)],
lambda=E nu/[(1+nu)(1-2nu)]. Plane stress derives sigma_z=0; plane strain derives
epsilon_z=0, hence sigma_z=nu(sigma_x+sigma_y). Strain magnitude above .01 warns
that small-strain applicability needs review.

For `three_dimensional`, all three principal strains come from the full symmetric
3x3 strain tensor, with off-diagonal terms gamma/2. A single in-plane principal
angle does not describe 3D principal directions and is omitted in this mode.
Plane states and rosettes retain their two in-plane principal strains and angle;
`epsilon_z` is reported from the applicable plane stress/strain condition.

Rosettes require `input_type=strain`, E, and `strain_a/b/c`. Rectangular 45-degree
rosette angles are 0,45,90 degrees; delta 60-degree angles are 0,60,120:

```text
45: ex=a, ey=c, gamma_xy=2b-a-c
60: ex=a, ey=(2b+2c-a)/3, gamma_xy=2(b-c)/sqrt(3)
```

Rosettes assume a plane-stress surface and report reconstructed components,
in-plane principal strains/angle and stresses. No noise-fitting or arbitrary-angle
rosette solver is implied.

Von Mises is computed directly as
`sqrt(((sx-sy)^2+(sy-sz)^2+(sz-sx)^2)/2 + 3*(txy^2+tyz^2+tzx^2))`.
Tresca is the largest minus smallest deviatoric principal stress. Thus a
hydrostatic 1e10 Pa plus 1e-6 Pa xy shear gives von Mises `sqrt(3)*1e-6 Pa`
and Tresca `2e-6 Pa`. Optional Sy gives strength/equivalent-stress factors. Optional positive
tensile and compressive strengths must appear together. Maximum-principal/Rankine
utilization is max(s1/St,-s3/Sc,0). Brittle Mohr-Coulomb is s1/St-s3/Sc for mixed
signs, and the governing uniaxial ratio otherwise. These are idealized failure
criteria, not fracture-mechanics or material-specific allowables.

## Verification and scope

Tests contain literal arithmetic and explicit tolerances. They cover standard and
polygon sections, circle convergence, hole subtraction, translation/rotation
invariance, truss method of joints, thermal bars, beam reactions and deflections,
fixed-end loads, Timoshenko shear deflection, settlements, springs, portal sway,
90-degree frame rotation, reciprocal stiffness, eigenfrequency refinement,
free-free rigid modes, modal mass orthogonality, column brackets, Mohr eigensolutions,
Hooke law and rosettes. Optional independent SciPy tests use `pytest.importorskip`
and compare generalized `eigh` and `solve`; tools never import SciPy.

`tests/test_structures_review_fixes.py` reproduces all eight independent review
findings. It checks offset-unit aliases and gradients, soft positive modes and
rigid motions, torsion omission, the extreme-aspect minor moment, hydrostatic
deviators, full principal strains, secant convergence and conversion underflow in
all six tools. The weak-spring reference solves the explicit four-DOF bending
determinant with 70-digit Decimal arithmetic; the slender-beam reference solves
the one-element characteristic quadratic. Gateway checks reject duplicate
parameter descriptions and descriptions longer than 140 characters.

Gateway tests list all six descriptions and check the unified contract, unit
conversion, magnitude errors, missing/conflicting inputs, IDs and references,
mechanisms, zero-length members, caps, function-like text and 6000-character hostile
strings with a one-second call bound. Windows test IDs are kept short so fixture
paths do not contain hostile payloads.

This domain does **not** perform design-code checks, nonlinear or second-order
P-delta analysis, member plasticity, 3D frames, plates/shells, damping, transient or
forced dynamic response, automatic self-weight, connection design, torsional
warping, local buckling or load combinations. Frame loads/analysis are new multi-
member capabilities; existing `mech` single-beam calculators remain unchanged.

The implementation was derived from the equations above, without copying external
code. Public method references include TU Delft's
[Euler-Bernoulli stiffness and equivalent-force derivation](https://teachbooks.tudelft.nl/mechanics/slender_structures/EB_K_Feq.html)
and its [dynamic beam formulation](https://teachbooks.tudelft.nl/computational-modelling/structural_linear/Exercises/Workshop_FEM_dyn_beam.html).
Those texts use their own axis conventions; the conventions here are defined above
and verified by equilibrium and hand calculations.
