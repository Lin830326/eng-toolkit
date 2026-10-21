# Machine elements

The auto-discovered `machine` domain exposes six tools. They use Python arithmetic and the existing Pint dependency. They read no files, invoke no external programs or LLMs, and execute no caller code. All results carry `review_status: "unreviewed"`. The gateway supplies the `machine_` prefix.

Physical inputs are strings with units. Optional quantities use `""`. Dimensionless coefficients and tooth counts are JSON numbers. Results use `QuantityOut` in metric units of mm, N, N mm, MPa, W, m/s and radians, or imperial units of inch, lbf, lbf ft, ksi, hp, ft/s and degrees. Rotational outputs use rad/s or rpm. Input rpm includes revolutions; Hz counts revolutions per second. Error `details.param` is a JSON pointer, such as `/outer_diameter` or `/segments_text/0/2`.

The shared domain helper rejects nonfinite values and physical magnitudes above these working-unit limits: length 1e7 mm, force 1e12 N, moment 1e15 N mm, pressure/modulus 1e7 MPa, power 1e12 W, linear speed 1e5 m/s, mass per length 1e6 kg/m, inverse length 1e6/mm, angle 2 pi rad. Nonzero magnitudes below 1e-9 in those units are refused. Dimensions that must be positive reject zero and negative values. Signed forces, moments and coordinates allow zero. Shaft wall/diameter and vessel wall/radius must be at least 1e-6. Derived nonfinite results or results above 1e100 are refused. Errors use `input.invalid`, `input.missing`, `input.out_of_range` or the existing `units.*` families. No NaN or Infinity is emitted, including in error details.

Each computed result includes formulas, assumptions and validity in `data`. An unbounded safety factor at zero demand is omitted from `values` and recorded as null with a reason in `data`. A safety factor is a strength/stress ratio unless its fatigue equation specifies a proportional load multiplier. None of these tools establishes design-code compliance.

## `machine_shaft`

Inputs describe a solid shaft or concentric hollow shaft. `bending_moment`, `torque` and `axial_force` default to zero; at least one static or explicit fatigue load must be nonzero. `inner_diameter` defaults to zero. Let d and di be the outer and inner diameters.

The elementary section formulas are

```
sigma_b = 32 M d / [pi (d^4 - di^4)]
sigma_z = 4 F / [pi (d^2 - di^2)]
tau = Kts 16 T d / [pi (d^4 - di^4)]
sigma = Kt sigma_b + sigma_z
von Mises = sqrt(sigma^2 + 3 tau^2)
maximum shear = sqrt(sigma^2/4 + tau^2)
Tresca equivalent = 2 maximum shear
n_static = Sy / von Mises
```

M is signed bending at the chosen outer fibre, and F is positive in tension. The bending and axial stresses add algebraically there. `worst_fibre_von_mises` also checks the opposite bending fibre, with a corresponding safety factor. Kt applies to bending and Kts to torsion; the axial stress is nominal. Stress concentration factors range from 1 to 100.

Fatigue needs `ultimate_strength` and at least one nonzero explicit `alternating_bending`, `mean_bending`, `alternating_torque` or `mean_torque`. Blank parts mean zero. Static M and T are a separate load case and do not silently become mean fatigue loads. Alternating parts must be nonnegative. There is no axial fatigue input, so a nonzero axial force in a fatigue call is refused. Compressive mean parts conservatively enter the nonnegative von Mises mean equivalent; no beneficial mean compression correction is claimed.

For steel, the Marin estimate is `Se = ka kb kc kd ke Se_prime`, with `Se_prime=min(0.5 Sut,700 MPa)`. Direct ka, kc and ke must be positive and at most 1. Direct kb and kd may be as high as 2, since small-size and temperature corrections can exceed 1. Zero for ka, kb, kc or ke selects an automatic value. kd defaults to 1 and is caller-supplied when temperature requires a correction; no temperature-to-kd correlation is implemented. Corrected endurance above Sut triggers a warning. With no finish, ka defaults to 1. Give ka or a finish, never both. Empirical surface constants use Sut in MPa:

| Finish | a | b in ka = a Sut^b |
|---|---:|---:|
| ground | 1.58 | -0.085 |
| machined | 4.51 | -0.265 |
| hot_rolled | 57.7 | -0.718 |
| as_forged | 272 | -0.995 |

Computed ka is capped at 1. These are empirical regression constants, not exact material properties. See the [Jönköping University fatigue notes](https://mechanics.ju.se/SolidMechanics/Fatigue.html) and Shigley's *Mechanical Engineering Design*, chapter on fatigue. The tool warns outside Sut 400 to 1400 MPa, while still applying the specified 700 MPa endurance cap.

The automatic size correction uses d in mm:

```
kb = 1.24 d^-0.107     2.79 <= d <= 51
kb = 1.51 d^-0.157     51 < d <= 254
```

Outside that range the caller must provide kb. The hollow-shaft outer-diameter approximation is flagged. `load_type` chooses kc of 1 for combined or bending, .85 for axial, and .59 for torsion. Combined bending/torsion normally uses kc=1 with the equivalent-stress treatment. The axial kc option only selects a correction factor; it does not introduce an axial load history. Direct kc overrides the selection. Reliability percentages 50, 90, 95, 99, 99.9 and 99.99 select ke of 1, .897, .868, .814, .753 and .702. Give ke or a nondefault reliability, never both.

Let a and m denote the alternating and mean equivalent stresses:

```
a = sqrt((Kf sigma_a)^2 + 3 (Kfs tau_a)^2)
m = sqrt((Kf sigma_m)^2 + 3 (Kfs tau_m)^2)
```

Kf and Kfs apply to both mean and alternating parts as requested by this model. The safety factors multiply both a and m along a proportional-loading ray:

| Criterion | Failure line | Closed-form n |
|---|---|---|
| Goodman | n a/Se + n m/Sut = 1 | 1 / (a/Se + m/Sut) |
| Soderberg | n a/Se + n m/Sy = 1 | 1 / (a/Se + m/Sy) |
| Gerber | n a/Se + (n m/Sut)^2 = 1 | 2 / (A + sqrt(A^2 + 4B^2)), A=a/Se, B=m/Sut |
| ASME elliptic | (n a/Se)^2 + (n m/Sy)^2 = 1 | 1 / hypot(a/Se,m/Sy) |

Gerber follows from `B^2 n^2 + A n - 1=0`; rationalizing the positive quadratic root avoids cancellation and covers zero mean stress. The elliptic equation factors n squared directly. These are load multipliers, rather than simply evaluating the failure expression at the current stresses and taking its reciprocal. Soderberg and elliptic results require Sy. Tests independently find intersections by bisection, including pure mean and pure alternating cases. `ASME elliptic` names a fatigue criterion; it makes no pressure-vessel code claim.

When both length L and shear modulus G are supplied, `theta=T L/(G J)`, `J=pi(d^4-di^4)/32`, using the static torque. The separate `static_deflections` field is a semicolon list with units, such as `"1 mm; 3 mm"`, with at most 50 values. A single mass gives Rayleigh `omega=sqrt(g/delta)`. Several deflections use Dunkerley `1/omega^2=sum(delta_i/g)`, with g=9806.65 mm/s². Each delta must be the deflection from that mass acting alone on the same shaft/support system. The multiple-mass result is an estimate, not an exact eigenfrequency. The tool cannot derive deflections from unsupported mass/support geometries, and does not model distributed shaft mass, gyroscopic effects or bearing stiffness.

## `machine_gear`

This tool models external, unshifted, full-depth involute spur gears. Give module m or diametral pitch Pd, exactly one, with `m=1/Pd` in consistent units. Tooth counts N1,N2 and face width F are required. The input gear is gear 1; speed is its rotational speed. Supply torque or power and speed for loading. Torque and power together are refused. Geometry alone is supported.

```
di = m Ni; C = (d1+d2)/2; dbi = di cos(phi)
addendum = m; dedendum = 1.25m; outside diameter = di+2m
speed ratio = N2/N1; v = omega d1/2; Wt = 2T/d1
Wr = Wt tan(phi); power = Wt v
contact ratio = [sqrt(Ro1^2-Rb1^2) + sqrt(Ro2^2-Rb2^2)
                 - C sin(phi)] / [pi m cos(phi)]
```

Ro is outside **radius**, Rb is base **radius**, and C is full centre distance. Pressure angle defaults to 20 deg and is bounded to 5..45 deg. A rack cutter has the theoretical unshifted undercut threshold `Nmin=2/sin(phi)^2`; the output retains the real threshold, so the integer minimum is its ceiling. Pair interference separately checks that `sqrt(Ro2^2-Rb2^2)-R2 sin(phi) <= R1 sin(phi)` and the converse. The tool warns on undercut, interference or contact ratio below 1.

Basic Lewis bending uses `sigma = load_factor Wt/(F m Y)`. The caller may supply each Y or use piecewise linear interpolation of a small selection of Shigley table 14-2 values for 20 degree full-depth teeth. These dimensionless geometric facts use the module convention, not the circular-pitch convention. [The publisher's chapter 14 lecture slides, table 14-2](https://www.philadelphia.edu.jo/academics/mgogazeh/uploads/DESIGN2%20CH14.pdf) identify the table and geometry. The selected knots are:

```
N: 12   14   16   18   20   24   30   38   43   50   60  100  150  300
Y: .245 .277 .296 .309 .322 .337 .359 .384 .397 .409 .422 .447 .460 .472
rack limit: .485
```

The rack limit is documented, but a finite gear cannot select an infinite tooth count. No extrapolation beyond 12..300 is made; supply Y instead. Other pressure angles require both Y values explicitly. This table is an empirical/geometric approximation to a tooth beam, not a stress standard.

Hertz pitch-point contact uses two elastic cylinders with involute curvature radii `rho_i=di sin(phi)/2`:

```
sigma_H = sqrt[load_factor Wt/(F cos(phi)) * (1/rho1+1/rho2)
               / (pi ((1-nu1^2)/E1+(1-nu2^2)/E2))]
```

Both elastic moduli are required together. Poisson ratios default to .3 and lie in 0..0.4999. Given bending/contact allowables, safety is allowable/stress. `load_factor` is a positive caller-selected multiplier up to 100, applied to design Wt for Lewis and Hertz only. Nominal Wt and power remain the transmitted values. This is a basic Lewis/Hertz check, **not an AGMA or ISO 6336 rating**. It has no automatic dynamic, overload, size, mounting, reliability or lubrication factors, no pitting life, and no helical or bevel gears. AGMA allowable stress numbers should not be mixed with these basic stresses without an appropriate basis.

## `machine_weld`

`segments_text` accepts 1..50 segments, each of the form `(0 mm,0 mm)-(100 mm,0 mm)`, separated by semicolons. Every coordinate needs a unit, including zero. A bounded regular-expression parser recognizes only this grammar, then sends coordinates to Pint. Function calls, nested parentheses, bare coordinates, zero-length lines and positive-length collinear overlaps are refused. Shared endpoints and crossing lines have zero measure and do not double-count area. Text is limited to 20000 characters.

The leg h gives throat `a=0.707 h`. This specified decimal approximation is used consistently rather than replacing it with sqrt(1/2). All integration is over the actual straight segments. For each segment of length l with midpoint coordinates xc,yc measured from the group centroid and end differences dx,dy:

```
L = sum(l); Aw = a L
centroid = sum(l * segment_midpoint)/L
Ix = sum(l (yc^2 + dy^2/12))
Iy = sum(l (xc^2 + dx^2/12))
Ixy = sum(l (xc yc + dx dy/12))
Ju = Ix + Iy
```

Ix,Iy,Ixy have units length cubed, per unit throat. The coordinate second-moment tensor Iu is `[[Iy,Ixy],[Ixy,Ix]]`; `data.I_u` maps its entries to quantity names. Multiply these line integrals by throat for physical area moments of inertia. The centroid calculation subtracts a reference coordinate before integration to reduce cancellation under translations.

For two vertical lines of length d separated by b, `Ju=d(3b^2+d^2)/6`. For a rectangular box of width b and height d, `Ju=(b+d)^3/6`. The tests verify both identities and translation invariance.

Force components Fx,Fy lie in the plane and Fz is normal. `load_x/y/z` describe the force application point; load_z defaults to zero. With position relative to the centroid, `M=position cross force`. At any weld point x,y relative to the centroid:

```
tau_x = Fx/Aw - Mz y/(a Ju)
tau_y = Fy/Aw + Mz x/(a Ju)
sigma_z = Fz/Aw + cx x + cy y
a [[Iy,Ixy],[Ixy,Ix]] [cx,cy] = [-My,Mx]
resultant = sqrt(tau_x^2 + tau_y^2 + sigma_z^2)
```

The complete tensor handles unsymmetric patterns. A rank-one pattern uses its supported bending direction; moments outside that direction are refused. The chosen point requires both point_x and point_y. Without them the tool checks every segment endpoint. Traction is affine along a segment, so its Euclidean norm is convex and its maximum lies at an endpoint. `worst_resultant` and `group_safety_factor` always describe the pattern endpoints, even when a separate chosen point is requested.

Allowable weld shear is compared with the total traction norm as a conservative screening convention. The normal and shear components are also returned. This is not a weld-code interaction equation. The model assumes uniform throat and rigid attached parts; no weld fatigue, residual stress, local notch stress, plastic redistribution, overlap fillet geometry or directional weld-strength enhancement is evaluated. A leg larger than one tenth of total line length triggers a line-model warning.

## `machine_pressure_vessel`

Use geometry `cylinder` or `sphere`, model `thin` or `thick`, inner radius a, internal pressure pi and external pressure po. Supply wall thickness or outer radius b, not both. For sizing alone, omit both and supply `allowable_stress`. Pressure is nonnegative; signed pressure is represented through which side it acts on. Tension is positive.

The thin membrane formulas use `dp=pi-po`, hoop `dp a/t` for a cylinder, and tangential stress `dp a/(2t)` for a sphere. Cylinder axial stress is half the hoop stress for closed ends, zero for open ends, or nu times hoop stress for zero axial strain. Thin radial stress is zero. At a/t below 10 the tool warns and reports the relative bore-hoop error `(thin-Lame)/Lame`, or null if the reference is zero.

Lamé's cylinder solution is

```
A = (pi a^2 - po b^2)/(b^2-a^2)
B = (pi-po) a^2 b^2/(b^2-a^2)
sigma_r(r) = A-B/r^2; sigma_theta(r) = A+B/r^2
sigma_z = A (closed), 0 (open), 2 nu A (constrained)
```

Constrained means zero axial strain. For a sphere:

```
A = (pi a^3 - po b^3)/(b^3-a^3)
B = (pi-po) a^3 b^3/(b^3-a^3)
sigma_r(r) = A-B/r^3
sigma_theta(r) = sigma_phi(r) = A+B/(2r^3)
```

The sphere requires closed geometry. `axial_stress` means the second tangential principal stress for a sphere. The optional evaluation radius must lie inside the wall. Boundary tractions are exactly `sigma_r(a)=-pi`, `sigma_r(b)=-po`. From the three bore principal stresses, von Mises is `sqrt(((sr-st)^2+(st-sz)^2+(sz-sr)^2)/2)` and Tresca equivalent is their maximum minus minimum. Yield safety factors are Sy divided by each equivalent. The bore result is reported explicitly; no claim is made that the bore always controls every possible open/constrained external-pressure load case.

The inner displacement follows Hooke's law from the exact Lamé stresses, including when membrane stresses were selected for reporting: cylinder `u(a)=a/E [st-nu(sr+sz)]`, sphere `u(a)=a/E [(1-nu)st-nu sr]`. Thus displacement is an exact elastic solution rather than a thin approximation. Poisson ratio lies in 0..0.4999. A displacement/radius above .01 or an equivalent stress above yield triggers a warning. [IIT Kharagpur's spherical elasticity exercise](https://facweb.iitkgp.ac.in/~jeevanjyoti/teaching/mechsolids/2026/ts/ts9.pdf) gives the spherical equilibrium/constitutive route.

Wall sizing constrains **absolute bore hoop stress**, not the von Mises yield stress. For allowable S and `H=sign(dp) S`, rearrangement of the bore equations gives

```
thin cylinder t = abs(dp) a/S
thin sphere t = abs(dp) a/(2S)
thick cylinder t = a [sqrt((H+pi)/(H-pi+2po)) - 1]
thick sphere t = a [((H+pi)/(H-pi/2+3po/2))^(1/3) - 1]
```

The ratio must exceed 1 for a finite positive wall. Otherwise the allowable is below the asymptotic demand and the tool refuses sizing. Equal pressures give zero required thickness by the hoop-only criterion; this says nothing about stability or hydrostatic compression strength. The actual-wall input must remain positive. The thick inversion is exact for the stated criterion and elastic geometry. The thin inversion is an approximation.

This is **closed-form elastic stress only, not an ASME design-code calculation**. There is no external-pressure buckling, nozzle/end stress, corrosion allowance, plastic collapse, thermal loading or fatigue check. Every nonzero external pressure triggers the buckling omission warning.

## `machine_belt`

Use small/large pulley diameters d,D and centre distance C. The pulleys must not overlap, `C>(D+d)/2`. Configuration is open or crossed; belt type is flat or v. Crossed V belts are refused. The included groove angle beta defaults to 40 deg and is bounded to 5..120 deg. A nondefault groove angle on a flat belt is refused.

```
open approximate L = 2C + pi(D+d)/2 + (D-d)^2/(4C)
open small-pulley wrap theta = pi - 2 asin((D-d)/(2C))
crossed approximate L = 2C + pi(D+d)/2 + (D+d)^2/(4C)
crossed wrap theta = pi + 2 asin((D+d)/(2C))
```

The length polynomials are long-centre approximations. The exact geometric length is also returned: `2 sqrt(C^2-k^2)+pi(R+r)+2k asin(k/C)`, with k=R-r for open or R+r for crossed. The tool reports relative approximation error, warns for C<D+d and warns for wrap below 120 deg.

Euler's capstan equation at impending slip is `T1/T2=exp(mu_eff theta)`, where mu_eff=mu for flat belts and `mu/sin(beta/2)` for V belts. T1 and T2 in this interface are **effective tractive tensions**, excluding centrifugal tension. With mass per length m and belt velocity v, `Tc=m v^2`, and the total strand tensions are `T1+Tc`, `T2+Tc`. Equivalently the total-tension capstan equation subtracts Tc from numerator and denominator. See [IIT Kharagpur's belt-drive derivation](https://archive.nptel.ac.in/content/storage2/courses/112105125/pdf/mod13les1.pdf). Friction is positive, at most 10, and mu_eff theta must be between 1e-9 and 50 to bound the exponential.

Give velocity or small-pulley rotational speed, with `v=omega d/2`. For power/tension evaluation provide either effective tight_tension or power. The tool assumes the limiting capstan ratio, so the inverse is the minimum-tension limiting solution, rather than an arbitrary installed preload:

```
P = (T1-T2) v
T2 = (P/v)/(ratio-1); T1 = T2 + P/v
initial tension = (T1+T2)/2 + Tc
```

Initial tension uses the equal-strand elongation approximation applied to total tensions. It is not a detailed elastic installation model. The effective shaft-load components are `(T1+T2) cos(alpha)` and `(T1-T2) sin(alpha)`, alpha=asin(k/C). Their norm includes the actual strand directions. Effective tensions account for moving-belt momentum; total tensile strand forces are also reported separately.

With a maximum **total** belt tension Tmax and positive mass per length, `v_opt=sqrt(Tmax/(3m))`. At fixed limiting ratio the power is `(Tmax-m v^2)(1-1/ratio)v`; differentiating gives the stated optimum. `maximum_power` evaluates this expression there. Actual total tight tension above Tmax triggers a warning. This distinction avoids confusing effective T1 with the total tension limit in the maximum-power formula.

Geometry and capstan relations are exact under the ideal flexible-belt assumptions; the length polynomial, installation preload and V wedge friction are idealizations. There is no slip, creep, sag, thickness, bending fatigue, groove-bottoming, centrifugal expansion or service-life rating. Both pulleys are assumed to have the same friction coefficient.

## `machine_key`

For joint_type `key`, supply torque T, shaft diameter d, key width b, key height h and engaged length L. Half of h bears in the hub:

```
tau = 2T/(d b L)
sigma_bearing = 4T/(d h L)
n_shear = Sshear/tau; n_bearing = Sbearing/sigma_bearing
Lmin_shear = 2T/(d b Sshear)
Lmin_bearing = 4T/(d h Sbearing)
```

Explicit shear and bearing allowables take precedence. If only yield strength Sy is given, `Sshear=.577 Sy`, the specified rounded von Mises conversion, and `Sbearing=Sy`. The minimum lengths correspond to unit safety against those supplied allowables. Width and height must be smaller than d. L/d outside .5..1.5 warns about the uniform-load assumption.

For `straight_spline` or `involute_spline`, provide tooth_count n, engaged L, mean radius rm, effective radial contact tooth_height h and allowable_pressure p. Both use a projected single-flank bearing-area model:

```
A_one = L h
Tcapacity = n p A_one rm f
actual bearing pressure = T/(n A_one rm f)
safety factor = Tcapacity/T
```

`load_sharing` f defaults to .75, a documented engineering assumption about uneven tooth contact, not a universal empirical constant or a standard-mandated factor. It must be in (0,1]. Effective tooth height is caller-supplied and must be less than 2rm; an involute spline's effective contact height is not automatically its full physical tooth height. Spline geometry and capacity can be returned without an applied torque. Key-specific parameters in a spline call and spline-specific parameters in a key call are refused.

These are static uniform-load estimates. There is no standard-size key selection, keyway fatigue/stress concentration, shaft weakening, fretting, hub splitting, spline tooth bending or involute flank pressure distribution. The spline formula is not a DIN/ISO/AGMA spline rating.

## Verification

`tests/test_machine_*.py` and `tests/machine_testing.py` check the actual tool result contract, direct calls and the real gateway client. Expected examples have hand arithmetic and explicit tolerances. The default comparison tolerance is 1e-10 relative and absolute; the independent RK4 pressure-vessel boundary-value solution uses 1e-8 relative tolerance. Tests include:

* Solid and hollow shafts, signed fibres, TL/GJ twist, every surface finish, endurance cap and deflection-based critical speed. Gerber and elliptic roots are checked by bisection of their failure equations.
* Gear power equals Wt v and T omega. An independent Hertz contact-width calculation recovers peak contact stress. Factored bending scales linearly and contact stress scales with the square root of load.
* Weld parallel-line and box identities, arbitrary-pattern force/moment equilibrium by exact two-point Gauss quadrature, translations, supported and unsupported singular patterns, chosen points and endpoint maxima.
* Cylinder/sphere boundary stresses, end conditions, wall-sizing inversion, displacement, thin-wall convergence, differential radial equilibrium and randomized shooting integration of the open-end elastic constitutive ODE.
* Belt ratio, power, V wedge effect, crossed geometry, tension conventions and a numerical check around the maximum-power speed.
* Key limiting lengths reconstruct unit safety; spline pressure times effective area times radius reconstructs torque.
* Unit conversions using inch, psi, lbf inch, kN m, rpm and hp. Gateway checks list six compact descriptions and basic parameter schemas, verify strict finite JSON, and reject zero/negative dimensions, incompatible units, bare quantities, huge magnitudes, NaN text, malicious/oversized segment lists, missing conditional inputs and conflicting inputs.

The tests verify formulas and interface behavior. They do not validate material allowables, manufacturing conditions, fatigue life in service, engineering sign-off or compliance with a design code.

## Review notes

An independent formula review (2026-10-21) led to these behaviours, each covered by `tests/test_machine_review_fixes.py`: angular speeds in deg/s, turn/s and rpm all convert directly to rad/s (Hz keeps the cycles-per-second meaning); reliability factors are 90 % .897, 95 % .868, 99 % .814, 99.9 % .753, 99.99 % .702; `load_type="torsion"` uses kc=1 because torsion enters as a von Mises equivalent stress (a warning says so); sizing with equal internal and external pressure is refused when the allowable is below p; `model="thin"` is refused when external pressure is present and the membrane stress is under ten times the neglected radial pressure; the weld segment parser is linear time; the weld bending traction is solved in the principal frame so a very thin pattern keeps its weak-axis moment.
