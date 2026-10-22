# Space frames, plates and elastic foundations

`structures3d` registers six tools through domain discovery. Runtime calculations use NumPy and Pint. The domain has no private imports from another domain and adds no dependencies.

| Tool | Method | Main output |
|---|---|---|
| `structures3d_static` | Direct stiffness, spatial Euler-Bernoulli or Timoshenko beams | Joint displacements, reactions, local actions and station CSVs |
| `structures3d_modal` | Consistent mass, Cholesky reduction, symmetric eigensolution | Frequencies, mass participation and mode CSV |
| `structures3d_buckling` | Linear reference solution and axial-force geometric stiffness | Positive proportional load factors and mode CSV |
| `structures3d_plate` | Navier double series or Levy separated series | Deflection, moments, coefficients, eligible stresses and grid CSV |
| `structures3d_plate_stability` | Plate eigenfunctions and scoped elastic coefficients | Critical stresses or SSSS natural frequencies |
| `structures3d_foundation` | Hermite finite elements on bilateral Winkler springs | Deflection, slope, moment, shear and soil reaction CSV |

## Contract and numerical boundaries

Every call returns the standard `ok`, `value`, `values`, `data`, `artifacts`, `provenance`, `warnings`, `error`, and `review_status` contract. `review_status` is always `unreviewed`. Successful results contain formulas, assumptions, validity, and the standing structural-estimate warning. Refusals identify an error family and a JSON pointer in `error.details.param`.

Physical inputs are strings with explicit units, including zero. Bare quantities, function calls and arithmetic expressions are rejected. Unit syntax permits named products, quotients and integer powers. Pint performs dimensional checks and conversion. Magnitude limits apply to the literal and the SI value, including nonzero values that underflow to zero during conversion. The thermal parser inspects parsed unit definitions, so aliases for offset Celsius and Fahrenheit are also refused for differences.

All record lists use commas within a record and semicolons or newlines between records. Empty and trailing records are errors. Each parameter permits at most 60,000 characters, each record 1,000 characters, each quantity token 120 characters and each unit expression 64 characters. IDs contain 1..32 ASCII letters, digits, underscores or hyphens. IDs must be unique within their node/member list. Loads may repeat and add. Properties, supports and release definitions may not conflict or repeat.

`unit_system` is `metric` or `imperial`. CSV columns always identify SI units. Results and CSV data pass finite-value checks. No caller text is evaluated as Python. Numerical overflow, failed factorization, unresolved constraint rank or excessive residual becomes a structured refusal.

Selected nonzero SI input bounds are below. Signed values are allowed only where stated by the parameter.

| Quantity | Magnitude range |
|---|---|
| Coordinate, length, settlement | 1e-9..1e4 m; coordinates and settlements also permit zero |
| Area | 1e-14..1e4 m² |
| Area moment or Saint-Venant J | 1e-24..1e8 m⁴ |
| Modulus or pressure | 1e-6..1e13 Pa |
| Force | 1e-12..1e12 N |
| Moment | 1e-12..1e15 N m |
| Distributed force | 1e-12..1e12 N/m |
| Mass density | 1e-9..1e6 kg/m³ |
| Foundation stiffness | 1e-9..1e16 N/m² |
| Beam EI | 1e-12..1e25 N m² |

## Shared frame records and local axes

The original model is capped at 200 nodes and 400 members. Every node must belong to a member. A further dense-work bound allows 1,200 total DOFs, including refined nodes and private released rotations. The bound is checked before matrix allocation. Modal and buckling subdivision counts are 1..20, default 4. A model within the original node/member caps can exceed the refined-DOF cap.

| Parameter | Grammar and example |
|---|---|
| `nodes` | `id,x,y,z`: `a,0 m,0 m,0 m;b,2 m,0 m,0 m` |
| `members` | `id,i,j,E,G,A,Iy,Iz,J`: `e,a,b,200 GPa,80 GPa,1000 mm^2,1e6 mm^4,2e6 mm^4,1e6 mm^4` |
| `orientations` | `e,vector,0,1,0` or `e,roll,30 deg`; at most 400 |
| `supports` | `a,fixed`, `a,pinned`, `b,roller_y`, or `b,ux+uz+rx`; at most 1,200 records |
| `supports`, spring | `b,spring,uy,100 kN/m,0 mm` or `b,spring,rz,10 kN*m/rad,0 deg`; rest position optional |
| `supports`, settlement | `b,settlement,uy,-2 mm` or `b,settlement,rz,0.001 rad` |
| `loads` | `node,Fx,Fy,Fz,Mx,My,Mz`: `b,0 N,1 kN,0 N,0 N*m,0 N*m,0 N*m`; at most 800 |
| `member_loads`, uniform | `e,uniform,local,0 N/m,-1 kN/m,0 N/m`; `global` also accepted |
| `member_loads`, point | `e,point,global,1 m,0 N,-1 kN,0 N`; position measured from member i; at most 800 load records |
| `releases` | `e,start,ry+rz` or `e,end,rx`; local rotational releases only, at most 800 records |
| `shear_properties` | `e,Asy,Asz`: `e,800 mm^2,600 mm^2`; at most 400, static only |
| `thermal` | `e,alpha,dT`: `e,12e-6 1/K,50 K`; at most 400, static only |

Optional record parameters default to `""`, meaning omitted. A member may instead append one roll angle or three dimensionless reference-vector components to its nine required fields. Supplying that orientation again in `orientations` is an error. Reference components must be finite, with nonzero magnitudes 1e-12..1e6. Zero-length members and collinear or nearly collinear reference vectors are refused.

The local axis convention is defined without a picture:

1. `ex` is the unit vector from node i to node j.
2. With a supplied reference `v`, `ey = normalize(v - ex*(ex dot v))`.
3. `ez = ex cross ey`, giving a right-handed frame.
4. The default reference is global Y when `abs(ex dot Y)<0.9`, otherwise global Z. A member parallel to global Y therefore has local y along global Z. A member parallel to global Z has local y along global Y.
5. A positive roll rotates `ey` toward `ez`: `ey_new=cos(roll)*ey+sin(roll)*ez`. The corresponding `ez_new` preserves handedness.

The rows of the reported rotation matrix are `ex,ey,ez`. Translations, rotations, forces and moments transform by the same matrix. Rigidly rotating a model requires rotating its explicit reference vectors too. A fixed global default is intentionally not rotation-covariant.

Local DOFs are `ux,uy,uz,rx,ry,rz` at i, then the same at j. Positive rotations and moments follow the right-hand rule. `Iy` is the area moment about local y, governing displacement along local z. `Iz` governs displacement along local y. `J` is the Saint-Venant torsion constant; the polar area moment is `Iy+Iz` and is distinct from J.

`fixed` restrains all six DOFs. `pinned` restrains all three translations. `roller_x`, `roller_y`, and `roller_z` restrain only their named global translation. Explicit combinations use distinct names from `ux,uy,uz,rx,ry,rz`. Spring directions and settlements are global. Point member loads may occur at either endpoint. Distributed member moments are outside the record grammar; nodal moments provide concentrated end moments.

## Static frame analysis

The local 12-DOF matrix combines `EA/L` axial stiffness, `GJ/L` torsional stiffness and two bending blocks. Each Euler-Bernoulli bending block, ordered as transverse translation and slope at both ends, is

```text
EI/L³ * [[ 12,  6L, -12,  6L],
         [ 6L, 4L², -6L, 2L²],
         [-12, -6L,  12, -6L],
         [ 6L, 2L², -6L, 4L²]]
```

These coefficients follow integration of the cubic Hermite curvature products. For the local z plane, the slope coordinate is `-ry`. Timoshenko bending uses `phi=12EI/(G As L²)`, divides the block by `1+phi`, and replaces 4 and 2 by `4+phi` and `2-phi`. `Asy` and `Asz` are already effective shear areas, including the caller's shear correction.

Uniform and point fixed-end loads are obtained by integrating local equilibrium and compatibility. This also gives exact constant-section static displacement recovery with shear flexibility. Uniform thermal strain is `epsilon0=alpha*dT`, with equivalent axial actions `[-EA epsilon0,+EA epsilon0]`. Differences accept K, delta_degC and delta_degF, and reject every offset-temperature alias. Absolute thermal strain above 0.05 is refused.

An element extraction matrix maps joint coordinates and private local released rotations into its twelve local coordinates. All operators use `A.T @ local_operator @ A`. Unused joint-rotation directions are removed in an orthogonal basis, including when members are inclined. Supported global rotation axes retain their coordinates. Output maps rotations back to global axes, with zero rotation in unused directions as a reporting convention. Applied moment or nodal inertia in an unused direction is refused. The static solve eliminates private end coordinates by a Schur complement and recovers them afterward. Dynamic analysis retains them. A double torsional release introduces an unrestrained member spin: static and buckling refuse it, while modal analysis reports its zero-frequency mode. A truss special case uses both bending releases and restrained unused joint rotations; the tests include a spatial three-bar tripod.

Stability is decided from six material-independent member compatibility rows: axial extension, twist, two end-rotation differences and two transverse chord changes. Positive spring constraints are included. Translations receive a geometric length scale before row-normalized SVD. An unresolved singular-value guard band gives `input.out_of_range`. Static kinematic nullity gives an explicit mechanism refusal. A small stiffness eigenvalue is never turned into a zero displacement.

All three frame solvers equilibrate the free stiffness with `S=diag(Kff)^(-1/2)`. Before factorization, `numpy.linalg.cond` estimates the 2-norm condition number of `S Kff S`, within the 1,200-DOF cap. Modal analysis excludes the known rigid fields before estimating the elastic condition. If `cond*float64_epsilon > 1e-8`, the solver refuses with `input.out_of_range` at `/members`, naming the shortest member, its element length, the condition number and estimated relative error bound. This conservative threshold targets at least six reliable digits in the algebraic solve; it does not bound discretization or modelling errors. Accepted results report the estimate and threshold in `data.conditioning`. Static solves, including the buckling reference state, apply two iterative refinement corrections using `math.fsum` residuals, so platforms where `longdouble` equals float64 still use compensated summation. A small backward residual alone cannot establish accuracy.

The solver includes support settlements and spring rest forces. End forces are actions on the element. Station axial force is positive in tension. With i-end actions denoted `Fxi,Fyi,Fzi,Mxi,Myi,Mzi`, internal recovery uses `N=-Fxi-integral(qx)`, `Vy=Fyi+integral(qy)`, `Vz=Fzi+integral(qz)` and `T=-Mxi`. At x=0, `My=-Myi` and `Mz=-Mzi`; along a span, `dMy/dx=-Vz` and `dMz/dx=Vy`. Point forces contribute jumps to the force integrals. Global force and moment residuals include all applied loads and ground reactions, with moments about `(0,0,0)`.

The station count is 2..101, default 21, per member. Point-force sides and exact force/moment stationary points are added. Displacement and rotation maxima are explicitly sampled, not claimed as exact extrema. Rotations or relative displacement-to-length above 0.05 produce a small-displacement warning.

Matrix coefficients and the Timoshenko flexibility follow [Gavin, Structural Element Stiffness, Mass, and Damping Matrices, sections 3.6 and 4.4](https://people.duke.edu/~hpgavin/StructuralDynamics/StructuralElements.pdf). The implementation derives the coefficients from the stated beam fields; it does not include external solver code.

## Frame modes and buckling

Modal inputs reuse frame geometry, orientations, supports and releases. `density` supplies kg/m³ or `mass_per_length` supplies kg/m; the two defaults conflict. `member_masses` overrides individual members with `member,mass_per_length`. Every member requires positive distributed mass. `nodal_masses` accepts `node,m,J` for equal masses and rotary inertias in three axes, or `node,mx,my,mz,Jx,Jy,Jz`. Lumped values are nonnegative and use kg and kg m². There are at most 400 member mass and 200 nodal mass records.

Axial and torsional consistent mass use the linear shape integral `mu L/6 * [[2,1],[1,2]]`. Torsion replaces `mu` by `rho(Iy+Iz)`. Each bending block uses `mu L/420` times the Hermite mass matrix with coefficients 156, 22L, 54, -13L, 4L² and -3L². The default omits cross-section rotary inertia in bending. `include_rotary_inertia=true` adds the Rayleigh terms `rho Iz integral(v'²)` and `rho Iy integral(w'²)` by exact three-point quadrature. The result identifies its mass model. Additional nodal rotary inertia is supported; inertia on an unrestrained joint coordinate disconnected by releases is refused. The source is [Gavin, sections 3.6 and 4](https://people.duke.edu/~hpgavin/StructuralDynamics/StructuralElements.pdf#page=17).

Each element has six analytic rigid fields: three translations and three rotations. The assembly joins those fields through retained end coordinates and intersects them with supports and positive springs. Released rotations remain private, so the rigid clusters on opposite sides of a release can rotate independently. A fixed joint with all three member-start rotations released therefore leaves three rotational zero modes. The reported zero-mode count equals the compatibility nullity, including piecewise-rigid hinge mechanisms and released member spins. A numerical disagreement between the two independently constructed spaces is refused. A connected frame without releases has the usual six free-body modes; partial restraints remove the appropriate number.

After diagonal scaling, `M=L L.T` gives a symmetric mass-whitened stiffness. Analytic rigid fields are transformed into those coordinates and removed by an orthogonal projection. `numpy.linalg.eigh` solves the elastic complement. The zero modes are analytic; positive eigenvalues are not clipped. Unresolvable positive frequencies cause a refusal. Mode vectors satisfy `phi.T M phi=1` in SI, and their CSV values are modal coefficients, not physical amplitudes.

Effective modal mass fractions use `(phi.T M r)²/[(phi.T M phi)(r.T M r)]`. Three fields r are global translations. Three are rigid rotations about the global origin, including translational motion `axis cross position` and private release rotations. These influence fields retain their restrained components. For a mass-normalized mode, `Gamma=phi_f.T (Mff rf+Mfs rs)` and the denominator is the total `r.T M r` over all coordinates. The one-element axial cantilever has fraction 0.75, from `M=(mu L/6)[[2,1],[1,2]]`, `phi=(0,1)` and `r=(1,1)`. Cumulative fractions appear per mode and in `data.cumulative_effective_mass_fractions`. Even a complete finite-mesh modal basis can sum to less than one: the support carries the remaining fraction. A truncated list also omits higher-mode participation. Refinement reduces the support share for a continuum cantilever; free-free rigid modes carry all translation mass. The output defaults to six modes and permits 1..30. Settlements and spring rest positions do not change this unstressed eigenproblem.

A single uniform cantilever with the default mass model receives continuous reference frequencies in `data`: bending `beta_n²/(2pi)*sqrt(EI/(rho A L⁴))`, axial `(2n-1)/(4L)*sqrt(E/rho)`, and torsion `(2n-1)/(4L)*sqrt(GJ/(rho Ip))`. The first bending roots are 1.8751040687, 4.6940911330 and 7.8547574382. They solve `cos(beta)*cosh(beta)=-1`; see the [Georgia Tech cantilever example](https://aeresources.gatech.edu/strdyn/Webpage/BeamBending/Examples/BeamBending_p2.php). The axial and torsional roots follow the fixed-free sine eigenfunction. The incompatible Euler-Bernoulli comparison is omitted when Rayleigh bending inertia is enabled.

Buckling first solves the stable reference frame, then integrates `Kg=integral N(x)*(bv.T bv+bw.T bw+((Iy+Iz)/A)*bt.T bt) dx`, where `bt` differentiates the linear twist field. N is tension-positive, so tension stiffens and compression destabilizes. Three-point Gauss integration is exact between point-force jumps for the implemented constant or linearly varying axial force. Its constant-force bending block is `N/(30L)` times

```text
[[ 36,  3L, -36,  3L],
 [ 3L, 4L², -3L, -L²],
 [-36, -3L,  36, -3L],
 [ 3L, -L², -3L, 4L²]]
```

This is the standard Hermite axial-force geometric matrix in [Gavin, section 3.6](https://people.duke.edu/~hpgavin/StructuralDynamics/StructuralElements.pdf#page=17). Axial stress also contributes the torsion block `N*(Iy+Iz)/(A*L)*[[1,-1],[-1,1]]` on the two twist DOFs. For nonconstant N, integration uses the element-average axial force for this linear-twist block. It follows directly from the second variation `1/2 integral N*(Iy+Iz)/A*(theta_x')² dx`. The model assumes the shear centre equals the centroid and Saint-Venant torsion with zero warping stiffness. It predicts pure torsional bifurcation at `Pcr=GJA/(Iy+Iz)`. Cruciform and other thin-walled open sections may need warping stiffness, which remains outside scope. Geometric terms from bending moments and shear, lateral-torsional coupling and warping buckling are not included. These limitations are reported without adding section-based refusals.

Cholesky reduction with the elastic K solves `(K+lambda Kg)phi=0`. Negative eigenvalues gamma of the transformed Kg give positive factors `lambda=-1/gamma`. The smallest positive factors are returned, at most 30. A reference state with no compression is refused. Compression that cannot be distinguished from local force-recovery roundoff is also refused; a rotated purely transverse load cannot create a fictitious factor. Shape vectors satisfy `phi.T K phi=1` in SI.

Recognized single straight columns receive Euler comparison data with `Pcr=pi² EI/(Keff L)²`: `Keff=1` pinned-pinned, 2 fixed-free and 0.5 fixed-fixed. These factors appear in [MIT Mechanics and Materials II, column laboratory, pages 5 and 6](https://ocw.mit.edu/courses/2-002-mechanics-and-materials-ii-spring-2004/9aebe9fc6669d928aa716a5033cc9c9f_lab_1_s04.pdf#page=6). Comparison data identify the selected mesh and relative error. Tests show convergence over three meshes for each end condition. General frame eigenvalues require caller comparison of subdivisions.

Both spectral tools currently refuse Timoshenko shear properties. Their mass and geometric operators use Euler-Bernoulli interpolation. Buckling also refuses thermal strain, prescribed settlements and nonzero spring rest positions; its factor scales the supplied mechanical loads. These are explicit scope limits.

## Rectangular plate bending

Required inputs are `length=a`, `width=b`, `thickness=t`, and `youngs_modulus=E`. Defaults are `poisson_ratio=0.3`, `pressure="0 Pa"`, `point_loads=""`, `method="navier"`, `edges="SSSS"`, `terms=41`, `grid_points=21` and `convergence_tolerance=0.005`.

Edge letters follow x=0, y=0, x=a, y=b cyclically. S is simply supported, C clamped and F free. SCSC clamps both y edges; SFSF frees both y edges. Navier requires SSSS and accepts pressure plus up to ten interior point records `x,y,P`, such as `0.4 m,0.6 m,100 N`. Levy accepts uniform pressure only, with SSSS, SCSC or SFSF edges.

`D=Et³/[12(1-nu²)]`. Navier uses `qmn=16q/(pi² mn)` for odd m,n under uniform pressure, and `4P sin(m pi xp/a)sin(n pi yp/b)/(ab)` for each point. Deflection coefficients divide by `D*((m pi/a)²+(n pi/b)²)²`. Derivatives give `Mx=-D(wxx+nu wyy)`, `My=-D(wyy+nu wxx)` and `Mxy=-D(1-nu)wxy`.

Levy solves `W''''-2 alpha² W''+alpha⁴ W=qm/D` for each x mode. A symmetric decaying-exponential basis avoids overflowing hyperbolic functions. The y conditions impose W=My=0 at simple edges, W=W'=0 at clamped edges, and My=Vy=0 at free edges. Vy is the effective Kirchhoff edge shear. These methods and coefficients follow [Kelly, Solid Mechanics Part II, section 6.5](https://pkel015.connect.amazon.auckland.ac.nz/SolidMechanicsBooks/Part_II/06_PlateTheory/06_PlateTheory_Complete.pdf).

Uniform-only results include `alpha=max_sampled|w| D/(|q|a⁴)` and `beta_x=max_sampled|Mx|/(|q|a²)`, with analogous beta_y. A single point-only result uses `alpha=max_sampled|w| D/(|P|a²)`. Zero and superposed cases omit ambiguous coefficients. Centre deflection is separately reported with its sign.

Point deflection is finite, but point-source bending moments have a logarithmic singularity. Their grid cells are blank and all global point-load stress maxima are omitted. Uniform-load surface stresses use `6M/t²`, including twisting shear and von Mises stress. When sampled deflection exceeds t/2, stress is omitted with a warning. The remaining deflection is explicitly a linear-theory estimate.

Bounds are `t/min(a,b)<0.1`, `0.05<=a/b<=20`, `-0.999<=nu<=0.499`, an initial 5..121 Fourier indices and an odd 5..51 grid count. The solver compares the full and half-order series for deflection and nonsingular moments, and compares sampled deflection peaks on two grids. Failed series checks increase the largest index to `min(2*n+1,2049)`. Failed grid checks increase the uniform count to `min(2*g-1,51)` while preserving requested sample coordinates and point sources, for at most 112 samples per axis. Source coordinates also appear in the comparison grid. `data.convergence` reports requested and used terms, the cap, used grid count and relative changes. A successful result always has `passed=true`; unresolved convergence at either cap gives `input.out_of_range` at `/terms`. No finite moment at a point source is certified: those cells remain blank, and only moments away from sources enter the convergence test. Near-source moments can reach the cap and trigger refusal. The sampled maximum is not certified as the continuum maximum.

## Plate stability and vibration

The same plate geometry and material inputs support `analysis=uniaxial|biaxial|shear|bending|vibration`. Compression is positive. `stress_x` is reference compression, shear demand or peak in-plane bending stress. `stress_y` is allowed only for biaxial compression. Density is required only for vibration. Optional yield strength provides an elastic-scope warning, not a code strength.

| Analysis | Formula and enforced scope |
|---|---|
| Uniaxial SSSS | `sigma_cr=k pi² E/(12(1-nu²))*(t/b)²`; exact `k=min_m(m/r+r/m)²`, r=a/b, n=1 |
| Uniaxial SSSC | Tabulated long-plate simple/clamped k=5.42, two decimal places, versus exact limit about 5.4097; r>=10 |
| Uniaxial SCSC | Tabulated long-plate clamped/clamped k=6.97, two decimal places, versus exact limit about 6.9709; r>=10 |
| Uniaxial SSSF | Finite simple/free characteristic equation with the supplied nu; 0.05<=r<=100 |
| Shear | SSSS approximation `k=5.34+4/r²`; r>=1 |
| Biaxial | Exact SSSS modal multiplier `D(alpha²+beta²)²/[t(sigma_x alpha²+sigma_y beta²)]` |
| In-plane bending | Long SSSS limit k=23.9; r>=10; linear longitudinal stress from equal compression to tension |
| Vibration | Unloaded SSSS exact `fmn=(pi/2)*sqrt(D/(rho t))*[(m/a)²+(n/b)²]` |

The aspect ratio is bounded by 0.05..100. SSSS uniaxial integer minimization needs only floor and ceiling of r. Biaxial analysis searches 2..200 indices per axis and uses an excluded-mode lower bound to certify the minimum. An uncertified search is refused. Vibration returns 1..30 modes. Clamped or mixed-edge vibration is deliberately unsupported; no unverified Leissa constants are returned.

For SSSF, set `alpha=m*pi*b/a`, `p=k*pi²` and `eta=y/b`. Separating `w=sin(m*pi*x/a)*W(eta)` gives `W''''-2 alpha² W''+(alpha⁴-p alpha²)W=0`. The simple edge imposes `W(0)=W''(0)=0`. The free edge imposes `W''(1)-nu alpha² W(1)=0` and `W'''(1)-(2-nu)alpha² W'(1)=0`. Let `h=alpha*sqrt(p)`, `u²=alpha²+h`, `v²=alpha²-h`, and `c=(1-nu)alpha²`. The Timoshenko-Gere characteristic determinant reduces to `((c-h)/(c+h))²*tanh(v)/v-tanh(u)/u=0`. For imaginary v, use the real sine/cosine form without tangent poles. Hyperbolic columns are scaled to avoid overflow. Each root is bracketed between `(1-nu²)alpha²+(1-nu)pi²/2` and `alpha²+6(1-nu)`, then bisected to a relative bracket of 2e-13, with an 80-iteration limit. The upper bound is the trial field W=eta. The lower bound follows by completing the bending-energy square and applying `integral W'² >= (pi²/4) integral W²`. Integer m values are checked until this lower bound exceeds the best eigenvalue. Failure to bracket or converge is refused.

The long-wavelength limit is `k=6(1-nu)/pi²`, about 0.425549 at nu=0.3 and 0.304571478 at nu=0.499. Thus 0.425 is only a rounded steel-like value. For a/b=100 and nu=0.499 the finite solution gives k=0.304660841 and stress 6.673071417 MPa for E=200 GPa, t=10 mm, b=1 m. Strongly auxetic materials can have a finite-wavelength minimum with m>1 even on a long plate, so the implementation searches m instead of assuming m=1. The other edge coefficients have no hidden nu dependence: W=0 at simple or clamped unloaded edges removes nu from the boundary eigenproblem. Their stresses still depend on nu through D.

The governing plate equation and the role of loaded/unloaded edge conditions are described in [Wierzbicki, MIT Structural Mechanics, lecture 11, sections 11.1 to 11.3](https://ocw.mit.edu/courses/2-080j-structural-mechanics-fall-2013/345c462fe40421ea0f795bd35d2e4af1_MIT2_080JF13_Lecture11.pdf). The SSSF determinant above is derived from those boundary conditions. The regression reference uses independent 70-digit Taylor fundamental solutions of the ODE, rather than the production hyperbolic determinant.

The long-plate r>=10 rule applies to the tabulated clamped-edge and in-plane bending branches. It is an implementation restriction, not a claim that finite-end effects vanish there. `data.coefficient_method` identifies tabulated approximations, precision and source. The 5.42/6.97 values retain two table decimals, with reference limits 5.4097/6.9709 disclosed; 5.42 is not a correctly rounded exact limit. The shear fit uses 5.34 to two decimals and the coefficient 4; the bending limit 23.9 has one decimal. These precisions describe tabulated constants, not certified finite-length error bounds. No effective-width method, postbuckling resistance or design-code checks are implemented.

The edge coefficients are tabulated in [AISC's local buckling report, Attachment BT-3, Table 2](https://www.aisc.org/media/a0jdm5pv/localbuckling-limits-report_aisc_adhoctg.pdf). This table was checked through indexed source text; direct PDF access returned HTTP 403. The shear expression is [AISI S100-16, G2.3-4](https://cfsei.memberclicks.net/assets/docs/publications/aisi-standards/aisi%20s100-16%20%20s100-16-c_e_s.pdf), and its Commentary 1.1.2 identifies the pure-bending k=23.9. Exact SSSS stability and vibration follow [Kelly, sections 6.6 and 6.7](https://pkel015.connect.amazon.auckland.ac.nz/SolidMechanicsBooks/Part_II/06_PlateTheory/06_PlateTheory_Complete.pdf). Citing these elastic coefficients does not implement either design standard.

## Winkler foundation beam

Required inputs are `length`, `flexural_rigidity` in N m² and `foundation_stiffness` in N/m². The latter is spring stiffness per beam length, including tributary width. Both ends independently accept `free`, `pinned` or `fixed`, with zero prescribed movements. `uniform_load` is signed N/m. Up to 100 point records `x,force,moment` add, for example `3 m,20 kN,0 N*m;7 m,0 N,5 kN*m`.

Positive loads and deflections share a transverse direction. Positive concentrated moment does positive work against positive slope. With `M=-EI w''` and `V=M'`, point jumps are `V_right-V_left=-P` and `M_right-M_left=C`. Soil reaction is `-k w`.

The Galerkin matrix integrates `EI N''T N'' + k NT N` exactly with cubic Hermite functions. The foundation block has the same 1/420 polynomial coefficients as consistent beam mass. Every point-load location becomes a node. The requested 1..200 count is the initial count; the final count appears in `data.elements` and the requested count in `data.requested_elements`. Distinct locations and ends must be separated by at least `1e-9*length`.

Deflection and slope use the Hermite field. Moment and shear integrate equilibrium with its soil loading, starting from end tractions. Thus jumps and integrated soil forces satisfy global equilibrium. The continuum constitutive relation converges with refinement. Free/free uniform loading uses the exact particular solution `w=q/k`, avoiding cancellation in its identically zero bending field. Reported extrema are exact roots of FE recovery polynomials, not exact continuum extrema.

A second mesh uses `min(2n,200)` elements, or a coarser mesh of at least 100 when the initial n=200. The solver retains the finer solution and doubles again until all five fields converge at 201 common stations and point-force sides. A clamped-clamped single element has no free DOFs and is automatically replaced by two elements before the first solve. The internal solver refuses any mesh with no free DOFs at `/elements`. The default convergence tolerance is 0.01. A successful result always has `data.convergence.converged=true`; failed comparison at the 200-element cap gives `input.out_of_range` at `/elements`. A small load-based scale floor handles zero fields. Two compensated-residual refinement steps reduce solve roundoff on refined meshes. CSV output includes 2..2001 requested stations, element ends and FE extrema. Ill-conditioned stiffness or excessive residual is refused.

Hetenyi's decay parameter is `beta=(k/(4EI))^(1/4)` and the characteristic length is `1/beta`. The classification is short for beta L<pi/4, medium through pi and long above pi. The finite-boundary solution is always used. For a single point force with both ends at least six characteristic lengths away, a second CSV compares deflection against

```text
w=P beta/(2k) exp(-beta |x-a|)[cos(beta |x-a|)+sin(beta |x-a|)]
M=P/(4 beta) exp(-beta |x-a|)[cos(beta |x-a|)-sin(beta |x-a|)]
```

Comparison stations include the force and offsets of one, two and three characteristic lengths. Differences include finite-boundary effects and mesh error. Other load cases explicitly identify why the infinite comparison is unavailable. The tool and tests use the bilateral beam equation in [Saravanan, NPTEL Advanced Solid Mechanics, chapter 11](https://archive.nptel.ac.in/content/storage2/courses/105106049/lecnotes/mainch11.html), and the infinite point solution in [Estaire, Crespo-Chacon and Santana, ECCM-ECFD 2018, section 2.1](https://congress.cimne.com/eccm_ecfd2018/admin/files/filePaper/p264.pdf). The moment formula also follows twice differentiating the stated deflection.

`foundation_model` accepts only `linear`. Springs carry tension and remain attached. Uplift, compression-only contact, nonlinear soil, shear deformation and axial-force effects are unsupported.

## Verification and exclusions

The test suite covers the actual public contract and gateway discovery, independent assemblies and textbook limits. Examples use E=200 GPa, G=80 GPa, L=2 m, A=1000 mm², Iy=1e6 mm⁴, Iz=2e6 mm⁴ and J=1e6 mm⁴ unless stated otherwise.

| Hand or series check | Expected value |
|---|---:|
| 1 kN axial cantilever load, PL/EA | 0.01 mm |
| 1 kN along local y, PL³/(3EIz) | 6.6666667 mm |
| 1 kN along local z, PL³/(3EIy) | 13.3333333 mm |
| 1 kN m torsion, TL/(GJ) | 0.025 rad |
| L-shaped arms 2 m and 1 m, I=1e6 mm⁴, J=2e6 mm⁴, tip 10/20/30 N | -0.0832333, 0.1667667, 0.825 mm |
| Pinned 2 m column, EIy=200000 N m² | 493480.2201 N |
| 1 m square, t=10 mm, nu=0.3, D | 18315.018315 N m |
| Same square, uniform pressure alpha and centre beta | 0.00406235 and 0.0478864 |
| Same square, central point alpha | approximately 0.0116008 |
| Square plate uniaxial k and shear k | 4 and 9.34 |
| Same square, rho=7850 kg/m³, f11 | approximately 47.98646 Hz |

Default direct-arithmetic comparisons use 1e-10 relative and absolute tolerances. Finite-mesh and truncated-series tests state wider tolerances next to the benchmark. Independent routes include Gauss integration, scalar high-term Navier sums, NumPy direct solves, and SciPy generalized eigenvalues or continuum matrix exponentials. SciPy is imported only in tests through `pytest.importorskip`.

The suite checks rigid rotation, reciprocity, symmetry, equilibrium, mesh convergence, partial-support rigid counts, releases, tension stiffening, unit conversion, thermal aliases, caps and hostile 6,000-character inputs. A gateway test requires six compact tool descriptions and rejects duplicate parameter descriptions. No result may contain NaN or Infinity.

Verification commands, using the already provisioned environment:

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_structures3d_*.py
.venv/Scripts/python.exe -m eng_toolkit doctor
```

The suite has no nonlinear or experimental validation. The domain does not implement P-delta response, shells, solids, plasticity, material damage, nonlinear contact, dynamic response beyond undamped modes, load combinations, design-code checks or design approval. Geometric buckling is the stated axial-force bifurcation approximation. Plate and foundation convergence comparisons estimate discretization sensitivity and are not rigorous error bounds.

Verification on 2026-10-22 after review fixes: all 272 structures3d tests passed, including the real gateway client and available SciPy references. `eng_toolkit doctor` exited 0 with no failures and discovered the new domain. PowerShell supplied the wildcard-matched test paths as an argument array. The whole repository suite was not run, as requested.

`tests/test_structures3d_review_fixes.py` adds 30 regression cases. It covers each exact review reproduction, conditioning metadata, Poisson ratios and finite SSSF aspect ratios, a finite-wavelength auxetic minimum, coefficient provenance, constant and varying axial-force torsion, released rigid clusters against SciPy stiffness null spaces, cumulative modal mass, independent high-order Navier sums, the closed-form clamped foundation solution, and refusal at the series/mesh caps. The short-column reference integrates Hermite polynomials with `Fraction` and bisects a 70-digit Decimal determinant, giving 497.1822751056. The static reference is `1.00001³/(3*200e9*2e-6)=8.333583335833342e-7 m`. Both short-segment calls now refuse: the scaled stiffness condition is about 3.61e16, far outside the accuracy bound.

The exact five-term plate reproduction now uses 383 terms, returning centre deflection 0.000221804455272772 m and centre Mx=47.8863781527 N per unit width; the independent index-1601 sum gives 47.8863796534 N. The review's 47.8863800174 N is another finite-sum approximation within 1e-8 relative of that reference. The clamped foundation reproduction refines from one requested element to 16, returning 0.00644005461853 m versus the continuum centre deflection 0.00644002119 m. These convergence tolerances concern discretization and remain separate from matrix-conditioning checks.
