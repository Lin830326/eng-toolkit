---
name: eng-structural-check
description: Use for a strength, deflection, buckling or vibration check of a beam, frame, truss, column, plate or machine element with the eng-toolkit structures tools (structures_*, structures3d_*, mech_beam_bending). Covers choosing the model, building the inputs, cross-checking by hand, and reporting utilization against the user's allowable. Load eng-toolkit first.
---

# Structural check workflow (eng-toolkit)

Load the `eng-toolkit` skill first for the unit rules, error handling and report format. All results are unreviewed textbook estimates: linear elastic, small displacement, no P-delta, no design-code check.

## 1. Pin down the problem (ask if it changes the answer)

Geometry (spans, heights, section dimensions), material (E, nu, yield or allowable stress, density for vibration), supports (fixed / pinned / roller / spring), loads (type, size, direction, where), and the **criterion** the user cares about: allowable stress or safety factor, deflection limit (never assume L/360 or any code limit unless the user gives one; you may mention a common rule of thumb clearly labelled as such), buckling load factor, or a frequency to avoid.

## 2. Choose the model

| Problem | Tool |
|---|---|
| Single span, one point load or uniform load, simply supported or cantilever | `mech_beam_bending` (fast, closed form) |
| Anything with several spans, frames, hinges, springs, mixed loads, settlement | `structures_frame` (2D) |
| Pin-jointed bars (loads only at joints) | `structures_truss` (2D or 3D) |
| 3D skeleton, torsion, biaxial bending | `structures3d_static` |
| Cross-section properties from dimensions or a drawing outline | `structures_section` first, then feed `A`, `Ix` into the analysis |
| Stress state at a point, principal stresses, von Mises, safety vs yield | `structures_stress` |
| Slender compression member | `structures_column` (Euler / Johnson / secant); frames: `structures3d_buckling` |
| Natural frequencies | `structures_modal` (2D) or `structures3d_modal`; both need `density` or `mass_per_length` |
| Plate under pressure, plate buckling or frequency | `structures3d_plate`, `structures3d_plate_stability` |
| Beam on soil / elastic foundation | `structures3d_foundation` |
| Shafts, gears, welds, pressure vessels, belts, keys | the `machine_*` tools |

## 3. Build the inputs carefully

- **Section first.** If only dimensions are given, call `structures_section` and read `area`, `Ix` (about the horizontal centroidal axis), `Sx_top`/`Sx_bottom`. Results come back in SI base units (m, m^2, m^4, Pa, N) unless `unit_system="imperial"`.
- **Records** (see the parameter descriptions for exact examples):
  - `structures_frame`: nodes `id,x,y`; members `id,i,j,E,A,I[,release]` (release `none|start|end|both` = pin); supports `node,fixed|pinned|roller_x|roller_y`; nodal loads `node,Fx,Fy,Mz`; member loads `member,uniform,local|global,qx,qy` or `member,point,local|global,x,Px,Py`.
  - `structures_truss`: nodes `id,x,y[,z]` (set `dimension=3` for 3D); members `id,i,j,E,A[,I]`; supports `node,xy` (letters of the fixed directions).
  - `structures3d_*`: nodes `id,x,y,z`; members `id,i,j,E,G,A,Iy,Iz,J`. `Iz` governs displacement along local y, `Iy` along local z. Default local y is global Y (global Z for members parallel to Y): give `orientations` (`e,vector,0,0,1` or `e,roll,30 deg`) whenever the weak/strong axis matters.
- **Signs**: global x right, y up, counterclockwise moments positive, so a downward load has a **negative** `Fy`/`qy`. Tension is positive in axial forces and stresses.
- Self-weight is never included: add it as a member load if it matters.
- Do not model a free body: every model needs enough supports, otherwise the tool refuses a mechanism (that is the correct answer: tell the user what restraint is missing).

## 4. Run, then verify

1. Read `warnings`, `data.stability` / `data.conditioning`, and the equilibrium residual (should be ~1e-9 of the loads or smaller).
2. **Hand check** at least one number with a closed form: cantilever `P L^3/(3 E I)`, simple beam `5 w L^4/(384 E I)` and `w L^2/8`, axial `P L/(E A)`, bending stress `M c / I`. A mismatch above ~1 % means wrong input (units, sign, I about the wrong axis) until proven otherwise.
3. **Convergence** for modal and buckling: rerun with a larger `elements_per_member` (e.g. 4 -> 10); report the change. Frequencies/factors that move by more than ~1 % are not converged.
4. Treat refusals as results: `ill-conditioned` (tiny members, huge stiffness ratios), `mechanism`, `no compressive force` (buckling), `detached`/`out of range`, thin-plate limits (`t/a < 0.1`). Explain and fix the model, never bypass.
5. Internal forces: `end_forces` are forces on the member ends in local axes; the station CSV (artifact) holds internal N, V, M and deflection along the member. Read the CSV for the location of the maximum.

## 5. Evaluate against the criterion

- Bending stress `sigma = M / S`; combine with axial `N / A`; use `structures_stress` for combined states.
- Utilization = demand / allowable; safety factor against yield = `Sy / sigma_vm` (von Mises) or `Sy / (2 tau_max)` (Tresca).
- Deflection ratio = span / max deflection, compared with the user's limit.
- Buckling load factor `lambda`: critical load = `lambda x` applied reference load; `lambda < 1` means buckling before the applied load.
- Frequency: report the first few modes and the convergence study; resonance margin only against numbers the user supplies.

## 6. Report

Use the report format from `eng-toolkit`: result, inputs/assumptions (model, supports, loads, sign conventions), checks (hand check, convergence, warnings), limits (unreviewed, linear small-displacement, no code check, no connection design). If the margin is thin (utilization above ~0.8) or the model idealizes something that matters (rigid joints, ignored self-weight, eccentric loads, local buckling, fatigue), say so explicitly.
