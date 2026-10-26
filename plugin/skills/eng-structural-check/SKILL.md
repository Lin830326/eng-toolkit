---
name: eng-structural-check
description: Use for a strength, deflection, buckling or vibration check of a beam, frame, truss, column or plate with the eng-toolkit structures_*, structures3d_* and mech_beam_bending tools, model choice, loads on slopes, supports, hand checks, utilization. Load eng-toolkit first.
---

# Structural check workflow (eng-toolkit)

Load `eng-toolkit` first. Unreviewed textbook estimates: linear elastic, small displacement, no P-delta, no design-code check.

## 1. Pin down the problem (ask if it changes the answer)

Geometry (spans, heights, section dimensions), material (E, nu, yield or allowable stress, density for vibration), supports (fixed / pinned / roller / spring), loads (type, size, direction, where), and the **criterion** the user cares about: allowable stress or safety factor, deflection limit (never assume L/360 or a code limit; a rule of thumb may be mentioned, labelled as such), buckling load factor, or a frequency to avoid. **If the member layout, the load values or the criterion are missing, ask before the first calculation**; never add code load factors the user did not ask for.

## 2. Choose the model

| Problem | Tool |
|---|---|
| Single span, one point load or uniform load, simply supported or cantilever | `mech_beam_bending` (fast, closed form) |
| Anything with several spans, frames, hinges, springs, mixed loads, settlement | `structures_frame` (2D) |
| Pin-jointed bars (loads only at joints) | `structures_truss` (2D or 3D) |
| 3D skeleton, torsion, biaxial bending | `structures3d_static` |
| Cross-section properties from dimensions or an outline | `structures_section` first, then feed `A`, `Ix` into the analysis |
| Stress state at a point, principal stresses, von Mises, safety vs yield | `structures_stress` |
| Slender compression member | `structures_column` (Euler / Johnson / secant); frames: `structures3d_buckling` |
| Natural frequencies | `structures_modal` (2D) or `structures3d_modal`; both need `density` or `mass_per_length` |
| Plate under pressure, plate buckling or frequency | `structures3d_plate`, `structures3d_plate_stability` |
| Beam on soil / elastic foundation | `structures3d_foundation` |
| Shafts, gears, welds, pressure vessels, belts, keys | the `machine_*` tools |

## 3. Build the inputs carefully

- **Section first.** From dimensions, call `structures_section` and read `area`, `Ix`, `Sx_top`/`Sx_bottom`; results are SI. There is **no catalogue** (IPE, HEA, angles): a rolled I built from dimensions has no root radius (IPE 240: I 3671 vs catalogue 3892 cm4, 6 % low). Ask for the catalogue A, I, W, or label it idealised or "from memory, not a tool result". **Single angle or unequal section as a strut: buckling is about the weak principal axis**: draw the outline as a `polygon`, take `principal_I2` (the minimum) and `r_min = sqrt(I2/A)`, never Ix.
- **Records** (examples in the parameter descriptions):
  - `structures_frame`: nodes `id,x,y`; members `id,i,j,E,A,I[,release]` (release `none|start|end|both` = pin); supports `node,fixed|pinned|roller_x|roller_y|spring` (`roller_x` restrains x only: it slides in y; `roller_y` restrains y only; spring `c,spring,1e6 N/m,0 N/m,0 N*m/rad` = kx, ky, krot); nodal loads `node,Fx,Fy,Mz`; member loads `member,uniform,local|global,qx,qy` or `member,point,local|global,x,Px,Py`.
  - `structures_truss`: nodes `id,x,y[,z]` (set `dimension=3` for 3D); members `id,i,j,E,A[,I]`; supports `node,xy` (letters of the fixed directions).
  - `structures3d_*`: nodes `id,x,y,z`; members `id,i,j,E,G,A,Iy,Iz,J`. `Iz` governs displacement along local y, `Iy` along local z. Default local y is global Y (global Z for members parallel to Y): give `orientations` (`e,vector,0,0,1` or `e,roll,30 deg`) whenever the weak/strong axis matters.
- **Signs**: global x right, y up, counterclockwise moments positive, so a downward load has a **negative** `Fy`/`qy`. Tension is positive.
- **Pressure normal to a sloped surface** (wind, snow-on-slope): resolve it at every joint into **both Fx and Fy** (force = p x tributary length along the slope x bay spacing; Fx = F sin(slope), Fy = F cos(slope), signs per side). The horizontal parts cancel in the global sum but not in the member forces: dropping them changed a diagonal force from 7.63 to 6.24 kN and flipped fail to pass.
- Self-weight is never included: add it as a member load.
- Every model needs enough supports, else the tool refuses a mechanism (correct answer: say what restraint is missing).

## 4. Run, then verify

1. Read `warnings`, `data.stability` / `data.conditioning`, and the equilibrium residual (about 1e-9 of the loads or less).
2. **Hand check** one number (label it): cantilever `P L^3/(3 E I)`, simple beam `5 w L^4/(384 E I)` and `w L^2/8`, `P L/(E A)`, `M c / I`. A mismatch above ~1 % means wrong input (units, sign, I about the wrong axis).
3. **Convergence** for modal and buckling: rerun with a larger `elements_per_member` (e.g. 4 -> 10); report the change. Frequencies/factors that move by more than ~1 % are not converged.
4. Refusals are results: `ill-conditioned`, `mechanism`, `no compressive force` (buckling), `out of range`, thin-plate limits (`t/a < 0.1`). Explain and fix the model, never bypass.
5. Internal forces: `end_forces` are member-end forces in local axes; the station CSV holds N, V, M and deflection along the member (for diagrams). **`structures_frame` gives the largest deflection as `values.max_member_deflection`** with member, position and signed value in `data.max_member_deflection_location`: quote it, not `maximum_nodal_displacement` (21.6 vs 19.1 mm at a point-load node; a warning says when they differ).
6. `structures_modal`: every returned frequency is in `values` (`frequency_mode_<k>`; `first_frequency` is only the first), with effective mass fractions in `data.modes` and the shapes in `mode_shapes.csv`. A support needing finite stiffness is a `spring` record, not a rigid roller.
7. A secant (eccentric column) load is a first-yield load: compare it with the user's allowable only if the user says how.

## 5. Evaluate against the criterion

- Bending stress `sigma = M / S`; combine with axial `N / A`; use `structures_stress` for combined states.
- Utilization = demand / allowable; safety factor against yield = `Sy / sigma_vm` (von Mises) or `Sy / (2 tau_max)` (Tresca).
- Deflection ratio = span / max deflection against the user's limit.
- Buckling load factor `lambda`: critical load = `lambda x` applied reference load; `lambda < 1` means buckling before the applied load.
- Frequency: first few modes and the convergence study; resonance margin only against numbers the user supplies.

## 6. Report

Report as in `eng-toolkit`: model, supports, loads, signs; hand check, convergence, warnings; limits (unreviewed, linear small-displacement, no code check, no connection design). Station CSVs are N, N*m, m: for kN*m and mm charts pass rescaled inline series and hand over a PNG (`format="png"`, `data.png_path`; eng-plot-results). Say so when the margin is thin (utilization above ~0.8) or the model idealizes something that matters (rigid joints, self-weight, eccentricity).
