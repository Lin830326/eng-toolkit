# Fluids domain

Six auto-discovered tools use NumPy and Pint. All calculations are local and all
results carry `review_status: "unreviewed"`. No caller text is executed. The domain
does not import the third-party `fluids` package or another domain's private code.
SciPy appears only in differential tests, behind `pytest.importorskip`.

## Inputs, outputs and limits

Physical quantities are strings such as `"50 mm"`, `"10 gpm"`, `"2 bar"`,
`"1 cSt"` or `"20 degree_Celsius"`. Pint checks their dimensions after a strict
literal/unit grammar check. Units can contain named factors, products, quotients
and integer powers. Bare physical numbers, expressions, function calls, JSON
expressions, nonfinite literals and malformed units are refused. `gpm` is a local
abbreviation for US `gallon/minute`; the shared unit registry is unchanged.

Dimensionless coefficients, Mach, gamma, roughness n, slopes and counts use JSON
numbers. Dimensionless fields inside record text use numeric literals. Node and
link IDs use 1..32 ASCII letters, digits, underscores or hyphens. Records are
comma-separated fields; records are separated by semicolons or newlines. Blank
records, including a trailing separator, are refused. Every record list has a
60,000-character cap, each record 1,000 characters, each quantity 120 characters
and its unit expression 64 characters. Parsers split bounded text before matching
bounded tokens; they do not use nested repetition patterns.

All tools accept `density` and exactly one of `dynamic_viscosity` or
`kinematic_viscosity`, with `mu=rho*nu`. To use cSt, explicitly set
`dynamic_viscosity=""`. Defaults are editable nominal inputs, not a fluid-property
table: 1000 kg/m3 and 1 mPa s, or 1.2 kg/m3 and 18.1 microPa s for gas relations.
They are not temperature-dependent water/air correlations. No built-in oils or
property correlations are used. The caller must supply properties appropriate
to the actual fluid, temperature and pressure. Compressible reference density
and viscosity are validated and recorded but do not enter inviscid ratios; nozzle
reservoir density is instead `p0/(R*T0)`. Surge viscosity is recorded through the
initial Reynolds number; the transient model neglects friction.

No temperature differences are requested. Absolute nozzle temperatures accept
plain Pint offset units and aliases, such as `20 degC` or `68 degF`, and must
convert to positive kelvin. Every quantity rejects offset temperature units in
composite expressions, including `mPa*s*degF/K`, aliases and cancelled factors
such as `degC/degC` or `degF^0`. The guard inspects the parsed unit constituents
and separately parses each unit token through Pint before composite conversion
can turn an offset unit into an interval. It does not blacklist unit spellings.
Rankine and explicit delta temperature units are multiplicative and remain valid
where their dimensions match. The temperature unit in `gas_constant` is an
interval, so offset units are refused there even as a single denominator factor.

All output quantities are SI and name their units. Tables in `data` and CSV column
names include units. Every successful result has `formulas`, `assumptions`,
`validity` and the standing review warning. Refusals have an error family and
`details.param` beginning with `/`, including record field pointers such as
`/pipes/2/4`. Numerical overflow and unsupported magnitudes produce
`input.out_of_range`; failed bounded iterations produce `input.nonconvergence`.
Results are checked recursively for finite values before return.

The following nonzero absolute SI input bounds apply after conversion. A nonzero
literal converting to zero is refused. Zero is accepted only where physically
meaningful, such as elevation, roughness, flow demand or closure time.

| Quantity | Minimum | Maximum | SI unit |
|---|---:|---:|---|
| Length/head | 1e-9 | 1e6 | m |
| Area | 1e-18 | 1e8 | m2 |
| Volume flow | 1e-15 | 1e6 | m3/s |
| Pressure | 1e-9 | 1e12 | Pa |
| Elastic/bulk modulus | 1e3 | 1e13 | Pa |
| Density | 1e-6 | 1e5 | kg/m3 |
| Dynamic viscosity | 1e-12 | 1e5 | Pa s |
| Kinematic viscosity | 1e-15 | 1e5 | m2/s |
| Speed | 1e-12 | 1e5 | m/s |
| Time | 1e-9 | 1e9 | s |
| Absolute temperature | 1e-6 | 1e5 | K |
| Specific gas constant | 1e-3 | 1e5 | J/(kg K) |
| Linear pump coefficient a | 1e-12 | 1e15 | s/m2 |
| Quadratic coefficient b or system K | 1e-12 | 1e20 | s2/m5 |
| Angular frequency | 1e-9 | 1e6 | rad/s |
| Turning angle | 1e-30 | pi | rad |

These are implementation bounds, not claims of physical validity. More specific
model limits below also apply. Standard gravity is exactly 9.80665 m/s2, the
[NIST conventional value](https://physics.nist.gov/cgi-bin/cuu/Value?gn).
Unit conversions use Pint; inch, foot and US gallon definitions agree with
[NIST SP 811](https://www.nist.gov/pml/special-publication-811/nist-guide-si-appendix-b-conversion-factors/nist-guide-si-appendix-b9).

Scalar roots use bracket-preserving bisection with geometric midpoints for
positive brackets wider than two decades. They stop at a bracket width of
`1e-14 + 2e-12*max(abs(endpoints))`, with a 160-iteration cap. Flow roots use a
smaller absolute tolerance of 1e-20. A bracket without a root is a refusal.
Unimodal maxima use golden-section search with a 120-iteration cap and a relative
interval tolerance of 2e-13. Network, pump and profile calculations also verify
their physical residuals.

## `fluids_pipe_network`

Example inputs:

```text
nodes = r,0 m,head,20 m;j,0 m,demand,1 L/s
pipes = p,r,j,10 m,50 mm,0 mm,0
```

Node format is `id,elevation,head|demand,value`. A head is the fixed gauge total
head of a reservoir or tank; a demand is positive for withdrawal and negative for
injection. A fixed-head node supplies whatever flow is needed. Caps are 200 nodes,
400 pipes and 40 additional pump links. Pipe format is
`id,start,end,length,inside_diameter,roughness_or_C,minor_K`.
Valve records `pipe_id,open|closed,additional_K` modify a pipe and occur at most
once per pipe, with a 400-record cap. Closed pipes carry zero flow and are removed
from connectivity checks; their hydraulic headloss is undefined and returned as
null. All nodes must be connected and at least one head must be fixed.

The headloss models are:

```text
v = Q/(pi D^2/4), Re = rho |v| D/mu
Darcy-Weisbach: h = (f L/D + K) v |v|/(2g)
Colebrook-White: x = 1/sqrt(f), x + 2 log10(eps/(3.7D) + 2.51x/Re) = 0
Haaland initial x = -1.8 log10[(eps/(3.7D))^1.11 + 6.9/Re]
Hazen-Williams: h = kSI L Q |Q|^0.852/(C^1.852 D^4.871) + K v |v|/(2g)
kSI = 4.727 * 0.3048^(4.871 - 3*1.852)
```

Colebrook Newton iterations require both a relative change below 1e-12 and an
equation residual below 1e-10 within 60 steps. Below Re=2000, `f=64/Re`; at zero
flow headloss is zero and the laminar head derivative is used. For 2000..4000,
`t=(Re-2000)/2000`, `w=t^2(3-2t)` and
`f=(1-w)*64/Re+w*f_Colebrook(Re)`. This transition blend is an implementation
choice, not a published transition correlation, and causes a warning. Darcy is
limited to Re<=1e12 and eps/D<=0.05. These coefficients and the headloss models
follow [EPANET 2.2, sections 3 and 12](https://nepis.epa.gov/Exe/ZyPURL.cgi?Dockey=P10113EM.TXT)
and [Haaland, 1983](https://doi.org/10.1115/1.3240948).
Hazen-Williams preserves the published 4.727 coefficient and 4.871 diameter
exponent in EPANET table 3.1, converting feet/cfs to SI exactly. The resulting
SI coefficient is close to the commonly rounded 10.67.

Hazen-Williams is empirical, restricted here to turbulent water, Re>=4000 except
exactly stagnant links, C=20..200, rho=995..1001 kg/m3 and mu=0.89..1.52 mPa s.
The property envelope is a conservative input screen for water around 5..25 C;
the caller must confirm that it is water. It is not a property identification
algorithm. Hazen-Williams is not generally identical to Darcy-Weisbach.

Pump formats are:

```text
p,r,j,poly,20 m,1000 s/m^2,100000 s^2/m^5,yes
p,r,j,points,0 L/s:20 m|10 L/s:10 m|20 L/s:0 m,yes
```

The final field is `yes|no` for a check valve. Polynomial head is `H0-aQ-bQ^2`,
with positive H0, nonnegative a,b, and at least one nonzero coefficient. Point
curves have 2..20 points, begin at Q=0, and have strictly increasing flow and
strictly decreasing nonnegative head. Point interpolation is linear. All pumps
operate forward at nonnegative delivered head. Adverse head closes a check valve;
without one, reverse/stalled operation is refused. Internal Newton trials may
extend the last table segment, but a final operating point outside the supplied
table is refused.

The solver uses **damped Newton on node heads**, not an implementation claiming
to reproduce EPANET's global gradient algorithm. Link inversion gives `Q(H)` and
`dQ/dDeltaH`; at unknown-head nodes `B Q+d=0`, with
`J=B diag(dQ/dDeltaH) B^T`. A diagonally scaled NumPy solve computes the correction.
Up to 25 halvings find a decreasing mass-residual step. Topology checks require
every variable head to remain connected to a fixed head through active links.
Closure that makes the head indeterminate is refused rather than assigned an
arbitrary pressure. The iteration cap is 100 and can be lowered by the caller.
Success requires max mass residual <=
`1e-9*max(sum(abs(demands)),max(abs(pipe_and_pump_flows)))` m3/s and max head
change <= `1e-8*max(1,max(abs(heads)))` m. The mass tolerance uses the actual
flow scale at each iterate, with no absolute floor. An all-zero network requires
exactly zero residual. The final state is evaluated again before returning.
Pipe-flow inversion also uses relative root tolerances without an absolute flow
floor; Darcy inversion checks its head residual against `1e-10*abs(delta_head)`.
Unresolvable head differences or a stalled mass-residual line search refuse with
`input.out_of_range`. Solved heads and flows above 1e6 in SI units are refused.

Outputs include node gauge pressure `rho*g*(H-z)`, reservoir supply, heads,
signed pipe flows, velocities, Reynolds numbers, friction factors, headlosses,
mass residual and iterations. Reversed flow and velocities exceeding 3 m/s warn;
3 m/s is a conventional screening guideline, not a code limit. Negative gauge
pressures warn. With vapour pressure supplied, cavitation risk is checked using
`p_gauge+p_atm <= p_vapour`, which is the absolute-pressure condition.

## `fluids_pump_system`

`pump_points` contains `pump_id,Q,H,efficiency`, with at most 120 records and eight
pumps. Each pump needs at least two points starting at zero flow; Q strictly
increases and nonnegative H strictly decreases. Efficiencies are in 0.01..1, except that the point at zero flow may be 0 (datasheets list 0 % at
shut-off), and are interpolated independently. This tool chooses the requested **interpolation**
option, not a potentially nonmonotone quadratic least-squares fit. There is no
curve extrapolation. Duplicate IDs group points into one pump in record order.

The system is `Hs=Hstatic+KQ^2`, with nonnegative K, or a pipe whose Darcy loss
is recomputed at each trial flow using the friction method above. Pipe geometry
and system K are mutually exclusive. The operating point is a bracketed
intersection within supplied curve coverage and requires positive flow and head.
The intersection is solved in dimensionless coordinates. Single/series flow is
scaled by the common maximum tabulated flow; parallel head uses the available
head bracket mapped to [0,1]. Head residuals are divided by the maximum of the
combined shutoff head and absolute static head. Bisection has a relative tolerance
of 2e-14 and no absolute floor. Final residuals against the system and all active
pump curves must be <=1e-9 times that head scale, including after affinity scaling.
Unresolvable intersections refuse with `input.out_of_range`; curves that do not
intersect are also refused. Results report solver scales, head tolerance and the
largest head residual. These scales preserve accuracy for nanometre heads and
flows near the lower input limit.

For series pumps, flows agree and heads sum. For parallel pumps, heads agree and
flows sum; check valves keep pumps above their shutoff head inactive. Different
pumps are supported. Parallel curve coverage ends when any active pump would need
extrapolation. Hydraulic power is `rho*g*Q*H`; shaft power is the sum of each
active pump's hydraulic power divided by its interpolated efficiency. Aggregate
efficiency is total hydraulic power divided by total shaft power. Idle parasitic
power is not modeled.

With speed ratio s and impeller trim ratio d, Q scales by sd, H by `(sd)^2`, and
power by `(sd)^3`, assuming unchanged efficiency. Speed ratios are limited to
0.2..3, and diameter ratios to 0.9..1.0 as a conservative implementation limit
for approximate trimming. These are not full geometric-similarity laws for
redesigning pumps. Actual manufacturer curves supersede this approximation.
Affinity exponents and pump-system intersections follow
[USACE EM 1110-3-173, section 5](https://www.publications.usace.army.mil/Portals/76/Publications/EngineerManuals/EM_1110-3-173.pdf)
and the [Oregon State/ODOT pump appendix, section 4.7](https://www.oregon.gov/ODOT/Programs/ResearchDocuments/SPR792EAppendix.pdf).

```text
NPSHa = (p_atm+p_surface_gauge-p_vapour)/(rho*g) + z_surface_above_pump - h_suction
NPSH margin = NPSHa - supplied NPSHr
dimensionless specific speed = omega sqrt(Q)/(g H)^(3/4)
```

NPSH requires vapour pressure and suction elevation; suction loss defaults to
zero. NPSHr is the caller's value at the actual operating point, not affinity
scaled. The available-minus-required margin can warn but is not an automatic
design margin. No suction velocity head is added to a large free-surface vessel.
`rotational_speed` is actual angular speed, e.g. `"1450 rpm"` or `"151.84 rad/s"`.
Specific speed uses aggregate Q,H, so series stages should also be assessed
separately. The definition follows dimensionless pump similarity; NPSH conventions
also appear in [USBR Engineering Monograph 40](https://usbr.gov/tsc/techreferences/hydraulics_lab/pubs/EM/EM40.pdf).

## `fluids_open_channel`

Modes are `analysis` with supplied Q, `uniform` with supplied depth and positive
slope, or `profile` with Q, boundary depth and reach length. Analysis defaults to
normal depth when depth is omitted. Flat/adverse slopes cannot supply normal or
uniform flow, but may be analyzed at a supplied nonuniform depth or as a profile.

For rectangular/trapezoidal sections with bottom width b and horizontal/vertical
side slope z, `A=y(b+zy)`, `P=b+2y sqrt(1+z^2)`, `T=b+2zy`. Rectangles have z=0;
triangles have b=0 and z>0. Circular sections use
`theta=4 asin(sqrt(y/D))`, `A=D^2(theta-sin(theta))/8`,
`P=D theta/2`, `T=2 sqrt(y(D-y))`. A small-angle series avoids cancellation in
segment area. Depth must remain strictly below D; there is no pressurized slot.

```text
R=A/P; Manning Q=A R^(2/3) sqrt(S0)/n
Fr^2=Q^2 T/(g A^3); critical depth solves Fr=1
specific energy E=y+Q^2/(2g A^2)
momentum function M=Q^2/(g A)+integral_0^y A(z) dz
rectangular Belanger: y2/y1=(sqrt(1+8 Fr1^2)-1)/2
rectangular jump loss=(y2-y1)^3/(4 y1 y2)
```

Manning n is the conventional tabulated coefficient, 0.005..0.2, for either input
unit system. Everything is converted to SI first. For Q in ft3/s and geometry in
feet, the equivalent coefficient is `(1/0.3048)^(1/3)=1.4859186`, usually rounded
to 1.486. Multiplying SI geometry by 1.486 would be an error. Manning and the
standard-step energy equation follow
[HEC-RAS Hydraulic Reference Manual, hydraulic calculations](https://www.hec.usace.army.mil/software/hec-ras/documentation/HEC-RAS_6.0_Reference_Manual.pdf).
Belanger's relation and the momentum function follow the hydrostatic momentum
balance, also used in that manual. Alpha and beta are both 1.

Normal and critical depths are bracketed independently. Circular conveyance has
a maximum near y/D=0.938. `normal_branch=lower|upper` selects a root on the
appropriate side of the computed maximum. The upper branch exists only between
full-section and maximum conveyance. Calls above capacity refuse a requested
normal depth; supplied-depth analysis can omit it with a warning. Depth/full ratios
above 0.82 warn about the approaching conveyance maximum, without claiming that
0.82 is its location.

Alternate depth solves the same specific energy on the opposite side of critical
depth. A supercritical depth gets a downstream jump estimate. Rectangles use
Belanger exactly; other shapes solve equal momentum on the subcritical branch.
The hydrostatic moment is `b*y^2/2+z*y^3/3`, or for a circular segment
`(y-D/2)*A + (2/3)[y(D-y)]^(3/2)`. Below y/D=0.01, 24-point Gauss-Legendre
integration of area avoids segment-centroid cancellation. Its order is a numerical
choice, not an empirical coefficient. Impossible free-surface alternate/jump
depths are omitted with a warning. Subcritical inputs do not generate a fictitious
downstream jump.

The standard-step method solves
`E2-E1=S0*dx-(Sf1+Sf2)*dx/2`, with `Sf=[n Q/(A R^(2/3))]^2`.
Subcritical flow integrates upstream from a downstream boundary; supercritical
flow integrates downstream. The boundary bed datum and x are zero; upstream x
is negative. Each requested step scans 100 logarithmic depth points on the chosen
Froude branch, also including the previous and normal depths, brackets roots and
selects the one nearest the previous depth. Every accepted step must stay on the
previous depth's side of both normal and critical depth, preserve the GVF gradient
sign, and change depth in the direction given by
`dy/dx=(S0-Sf)/(1-Fr^2)`. The energy residual must be within 1e-10 of the step's
head-change scale plus 16 ulps of the previous specific energy. Energy differences
use factored area differences to reduce cancellation. A stationary step away
from equilibrium is refused; within 16 ulps of normal depth it is accepted only
if the energy residual also passes.

No root, a branch crossing, an inconsistent direction or excessive residual
causes that step to be bisected. Each requested step allows at most 12 levels
of bisection. There are at most 500 total step attempts, including rejected
attempts, and hence at most 500 accepted steps. These caps retain the previous
bounded scan workload. Exhausting either cap refuses with `input.out_of_range`
at `/profile_steps` or `/profile_length`, explaining that the branch cannot be
preserved. A critical boundary is refused. The solver never inserts a jump.

`requested_profile_steps` records the caller's count; `profile_steps` now reports
the accepted count. Results also report attempts, maximum refinement depth,
minimum/maximum accepted step lengths and `profile_end_depth`. The CSV includes
every accepted subdivision. Refinement enforces physical consistency; it is not
a global truncation-error estimator. Open-section depth brackets extend to 1e6 m;
closed-section brackets stop below the soffit. Profile classes are
M1/M2/M3, S1/S2/S3, H2/H3, A2/A3 and C1/C3, based on normal, critical and local
depth. A CSV reports x, bed, depth, water surface, total head, Fr and class.

## `fluids_compressible`

The gas is calorically perfect, with caller gamma in 1.01..2 and caller R. The
default R=287.05 J/(kg K) is an editable nominal dry-air input, not a gas table.
Supported Mach numbers are 1e-6..50. Ratio relations alone do not establish whether
a real gas retains constant heat capacities at the resulting temperatures.

Let `g=gamma`, `t=1+(g-1)M^2/2`:

| Mode | Equations |
|---|---|
| Isentropic | T0/T=t; p0/p=t^(g/(g-1)); rho0/rho=t^(1/(g-1)); A/A*=[2t/(g+1)]^((g+1)/(2(g-1)))/M |
| Normal shock | M2^2=t/[g M1^2-(g-1)/2]; p2/p1=1+2g(M1^2-1)/(g+1); rho2/rho1=(g+1)M1^2/[2+(g-1)M1^2]; T2/T1=(p2/p1)/(rho2/rho1) |
| Shock total pressure | p02/p01=(rho2/rho1)^(g/(g-1))*[(g+1)/(2g M1^2-(g-1))]^(1/(g-1)) |
| Oblique shock | tan(theta)=2 cot(beta)(M1^2 sin^2(beta)-1)/[M1^2(g+cos(2beta))+2] |
| Prandtl-Meyer | nu(M)=sqrt((g+1)/(g-1))*atan(sqrt((g-1)/(g+1)*(M^2-1)))-atan(sqrt(M^2-1)) |
| Fanno | 4f_F L*/D=(1-M^2)/(g M^2)+(g+1)/(2g)*ln[(g+1)M^2/(2+(g-1)M^2)]; T/T*=(g+1)/(2+(g-1)M^2); p/p*=sqrt(T/T*)/M |
| Rayleigh | p/p*=(g+1)/(1+g M^2); T/T*=M^2(p/p*)^2; T0/T0*=(T/T*)[2+(g-1)M^2]/(g+1); p0/p0*=(p/p*){[2+(g-1)M^2]/(g+1)}^(g/(g-1)) |

Fanno's f_F is the **Fanning** factor. Thus `4 f_F L*/D` also equals
`f_Darcy L*/D`; it is not four times the Darcy parameter. The star denotes a sonic
state on the same model line. Fanno, Rayleigh and isentropic modes are distinct
models, not interchangeable segments of one unspecified process.

Isentropic input is exactly one of Mach, A/A* or p0/p. Area inversion selects
`subsonic|supersonic`; pressure inversion uses `expm1` to retain small changes.
Normal shocks require M>=1. For oblique shocks, M>1 and `branch=weak|strong` are
required. The largest attached deflection follows the closed-form maximum:

```text
sin(beta_max)^2 = [(g+1) M^2 - 4
  + sqrt((g+1) ((g+1) M^4 + 8 (g-1) M^2 + 16))] / (4 g M^2)
```

Near Mach 1, direct subtraction would lose the small cosine and deflection.
The implementation uses `delta=(M-1)(M+1)` and
`w=M^2*cos(beta)^2/delta`, which spans [0,1]. Rationalizing the closed form gives
`w_max=2*((g-1)*M^2+2)/((3*g-1)*M^2+4+sqrt(...))`. In these coordinates,
`M^2*sin(beta)^2-1=delta*(1-w)` without cancellation. Weak and strong roots are
bracketed on opposite sides of `w_max`, with relative tolerance 2e-14 and no
absolute floor. A deflection above the maximum by more than four ulps refuses a
detached shock. The final turning residual must be within the larger of four
ulps of the maximum and 1e-10 of the requested turn; otherwise the call refuses
with `input.out_of_range`. The angle input minimum is 1e-30 rad so near-sonic
attached turns can be represented. Normal
relations use `Mn1=M1 sin(beta)` and `M2=Mn2/sin(beta-theta)`. Expansion solves
`nu(M2)=nu(M1)+turn` and preserves total temperature and pressure. Near sonic
conditions, series avoid cancellation in Prandtl-Meyer and the Fanno parameter.
These are the perfect-gas equations in
[NACA Report 1135](https://www.nasa.gov/wp-content/uploads/2023/03/equations-tables-charts-compressibleflow-report-1135.pdf)
and the Fanno/Rayleigh conventions in
[Virginia Tech's compressible-flow reference](https://devenport.aoe.vt.edu/aoe3114/calch.html).

For nozzle mode all pressures are absolute. Write `r=pb/p0`, with `0<=r<=1`:

```text
r_critical = [2/(g+1)]^(g/(g-1))
choked mdot = Cd A p0 sqrt(g/(R T0)) [2/(g+1)]^((g+1)/(2(g-1)))
unchoked mdot = Cd A p0 sqrt(2g/[R T0(g-1)] * r^(2/g) * [1-r^((g-1)/g)])
```

Choked flow is used at or below the critical ratio; above it the isentropic
unchoked expression applies. Equal pressures give zero flow. The small pressure
difference uses `log_ratio=log1p((pb-p0)/p0)` before evaluating
`exp(2/g*log_ratio)*[-expm1((g-1)/g*log_ratio)]`. Testing `pb==p0` directly
avoids mistaking a rounded pressure ratio for equal pressures. This preserves
the pressure difference of adjacent parsed floats. Cd is supplied in (0,1]. The model is a
converging nozzle/effective orifice estimate; it does not solve shocks in a
converging-diverging nozzle. See
[NASA Glenn mass-flow choking](https://www.grc.nasa.gov/www/k-12/airplane/mflchk.html).

## `fluids_surge`

For a uniform elastic pipe, fluid bulk modulus K, wall modulus E, thickness e and
inside diameter D:

```text
a = sqrt[(K/rho)/(1+K D c1/(E e))]
Joukowsky delta_p = rho a delta_v; delta_H=a delta_v/g
critical closure time = 2L/a; reservoir/closed-valve oscillation period = 4L/a
Michaud slow-closure estimate delta_p = 2 rho L delta_v/tc, only for tc>2L/a
```

Anchoring cases are expansion joints `c1=1`, longitudinally restrained
`c1=1-nu^2`, or upstream anchored with downstream axial motion `c1=1-nu/2`.
Poisson ratio is 0..0.49. The thin-wall condition is enforced as D/e>=20 and the
linear-flow restriction as initial v/a<=0.1. These thresholds are conservative
implementation limits. Formula and anchoring conventions follow Wylie and
Streeter, *Fluid Transients*, 1978, chapter on elastic water hammer; the
[USBR elastic water-column analysis](https://www.usbr.gov/tsc/techreferences/hydraulics_lab/pubs/PAP/PAP-0208.pdf)
provides the characteristic-method basis. Michaud's slow closure is an estimate,
not an exact prediction for an arbitrary valve's travel curve.

When `duration` is supplied, a frictionless method of characteristics simulates a
horizontal reservoir-pipe-valve system. Initially H=Hres and Q=A*v0 everywhere.
There are 2..200 equal reaches, `dx=L/N`, and `dt=dx/a` exactly. A supplied time
step must agree within relative 1e-8; otherwise the call refuses rather than
violating the Courant condition. Duration must span at least one step and rounds
up to a grid time. Caps are 20,000 time steps and two million reach-steps.

With `B=a/(gA)`, the interior characteristics are `C+=Hleft+B Qleft` and
`C-=Hright-B Qright`, giving `H=(C++C-)/2`, `Q=(C+-C-)/(2B)`.
Upstream head is fixed. The downstream valve's effective area fraction is
`tau=max(0,1-t/tc)`, with
`Q=tau Q0 sqrt(H/Hres)` and `H+BQ=C+`. The quadratic in sqrt(H) uses a stable
positive root. Fully closed flow is zero. This means **linear effective valve
area**, not linear discharge. Negative characteristic head while the valve is
open is refused; negative gauge heads elsewhere warn for an absolute-pressure
check. No vapor-cavity or column-separation state is introduced.

`locations` is a list of up to 20 physical positions, mapped to the nearest grid
point, with actual locations reported. Omission selects 0,L/2,L. The CSV contains
head at each location versus time. Peak/minimum heads inspect all grid nodes, not
only sampled locations. Instantaneous closure includes the MOC/Joukowsky ratio
in `data`; Courant-one frictionless steps reproduce its initial peak. Viscous
damping, unsteady friction, viscoelastic walls and network transients are excluded.

## `fluids_external_flow`

`length` means sphere/cylinder diameter or plate streamwise length. Sphere area
is `pi*d^2/4`; a cylinder takes projected area and a plate takes total wetted area.
At zero prescribed velocity drag is exactly zero; undefined coefficients and
boundary-layer thickness are omitted with a warning.

```text
Re = rho v L/mu; F = 0.5 rho v^2 Cd A
Clift-Gauvin sphere: Cd=24/Re*(1+0.15 Re^0.687)+0.42/(1+42500/Re^1.16)
White cylinder: Cd=1+10/Re^(2/3)
Blasius plate: Cf=1.328/sqrt(Re_L), delta=5L/sqrt(Re_L)
Tripped turbulent plate: Cf=0.074/Re_L^0.2, delta=0.37L/Re_L^0.2
Mixed plate: Cf=0.074/Re_L^0.2-1742/Re_L
Stokes sphere: F=3 pi mu d v, v_terminal=g d^2(rho_s-rho)/(18 mu)
```

Clift-Gauvin is restricted to 1e-12<=Re<=2e5, before drag crisis. Its constants
are those reported in the incompressible continuum baseline, equation 3, of
[Loth et al., *Supersonic and Hypersonic Drag Coefficients for a Sphere*, 2021](https://doi.org/10.2514/1.J060153).
White's smooth infinite-cylinder fit is limited here to 1<=Re<=2e5. Its constants
are the published `1+10/Re^(2/3)` approximation from White, *Viscous Fluid Flow*,
also compared in [Elghannay and El Hasadi, equation 2](https://mdp.omu.edu.ly/journals/index.php/mjer/article/download/59/11).
Only the mathematical correlation is implemented; no source code or figures from
these publications are copied. Cylinder lift, finite-end corrections and drag
crisis are not modeled.

Plate coefficients are the Blasius and one-fifth-power engineering correlations
given in Schlichting and Gersten, *Boundary-Layer Theory*, 9th ed., chapters on
flat-plate boundary layers ([publisher](https://doi.org/10.1007/978-3-662-52919-5)).
The supported engineering interval is 1e3..1e7. Laminar mode ends at Re=5e5;
mixed and tripped-turbulent modes start at 5e5. Automatic mode selects laminar
below the transition and mixed above. The mixed correction uses the conventional
rounded coefficient 1742 for a transition at 5e5. Turbulent thickness is an
approximation also used for mixed plates. None of these correlations predicts
transition from surface roughness or free-stream turbulence.

Terminal velocity equates drag to the magnitude of buoyant weight,
`abs(rho_s-rho)*g*pi*d^3/6`, using a bracket in Reynolds number over the sphere
correlation's range. The final relative force residual must be <=1e-9. Positive
velocity denotes settling, negative rising; equal densities give exactly zero.
The Stokes estimate is included only when its own predicted Re<0.1. Stokes drag
is included only when actual Re<0.1; otherwise it is omitted with a warning.
The general correlation tends to Stokes at small Re and is not forcibly replaced
by Stokes, preserving continuity of the terminal-velocity equation.

## Verification and exclusions

`tests/fluids_testing.py` validates the unified contract and finite JSON. Tests
contain independent arithmetic in comments and explicit tolerances. They check
Mach-2 ratios, shocks and choking, independent Colebrook factor bisection,
Manning/critical/Belanger equations, buoyancy-drag balance, unit conversions,
CSV energy balances, and the instantaneous Joukowsky characteristic.

The published two-loop benchmark is Swamee and Sharma, *Design of Water Supply
Pipe Networks*, 2008, example 3.4, pp.56-60
([reference copy](https://library.knu.edu.af/opac/temp/11639.pdf)). The book holds
f=0.02 and reports five-iteration flows 0.223,0.223,0.192,0.192,0.182 m3/s. Those
rounded numbers leave a small imbalance relative to 0.6 m3/s. The test independently
solves the three equivalent parallel resistances and constructs pipes whose
Colebrook factors are 0.02 at that solution. It compares to the exact resistance
solution at 1e-8 relative tolerance and the published rounded flows at 0.002 m3/s
absolute tolerance. It is a resistance-preserving reconstruction, not a claim
that variable Colebrook friction reproduces every original fixed-f geometry.

Seeded random cases compare network flows to SciPy `fsolve` on loop-flow equations,
Hazen-Williams to a separate NumPy loop Newton method, scalar roots to SciPy
`brentq`, and turbulent friction to factor-space bisection. Gateway tests discover
all six tools, require compact distinct parameter descriptions, exercise
malformed/hostile inputs, enforce caps and check JSON-pointer refusals. Timed
hostile-string checks warm MCP dispatch before measuring the parser and runner.

`tests/test_fluids_review_fixes.py` covers the six independent review findings.
Coarse M1, M2 and S2 profiles retain their branches and approach normal depth;
their end depths are compared with SciPy `solve_ivp` integration of the GVF ODE,
behind `pytest.importorskip`. Critical-control reaches and both refinement/work
caps exercise bounded refusal.
All six tools reject offset-viscosity composites, including aliases, while plain
absolute temperatures, Rankine and explicit intervals retain their conversions.
The tiny network case uses Poiseuille's closed form and checks the final mass
residual. Tiny pump curves use a Decimal quadratic reference for single, series,
parallel and affinity-scaled intersections. Near-sonic attached limits use an
80-digit direct closed-form evaluation independent of the implementation's
rationalization. Nearly equal nozzle pressures use an 80-digit reference from
the exact parsed binary floats.

This is not field validation, a design-code check, a pump selection guarantee or
CFD. It does not model two-phase or non-Newtonian fluids, variable gas heat
capacities, real-gas effects, turbomachinery design, pipe-network transients,
pressure-dependent water demand, pressurized channel transitions, sediment,
air entrainment, surface tension, wall-corrected settling or added mass. The only
unsteady model is the documented single-pipe MOC. Empirical inputs and
correlations need engineering review for the actual system.
