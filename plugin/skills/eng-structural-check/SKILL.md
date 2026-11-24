---
name: eng-structural-check
description: Use FIRST for "is this beam, truss, frame, column, pipe or plate strong enough", deflection, buckling, natural frequency, member forces under snow or wind (structures_*, structures3d_*, mech_beam_bending). Model choice, loads on slopes, supports, hand checks.
---

# Structural check workflow (eng-toolkit)

Load `eng-toolkit` too. Unreviewed textbook estimates: linear elastic, small displacement, no P-delta, no design-code check.

## 1. Pin down the problem (ask if it changes the answer)

Geometry (spans, heights, section dimensions), material (E, nu, yield or allowable stress, density for vibration), supports (fixed / pinned / roller / spring), loads (type, size, direction, where), and the **criterion** the user cares about: allowable stress or safety factor, deflection limit (never assume L/360 or a code limit), buckling load factor, or a frequency to avoid. **If the member layout, the load values or the criterion are missing, ask before the first calculation** (only what cannot be inferred; restate the inferred model and run it); never add code load factors the user did not ask for. **For a compression bar ask which safety factor against Euler the person applies before printing a utilisation** (`N / (Pcr / SF)`); if unknown, give `N / Pcr` and the factor, with no pass or fail word. The same for any allowable.

## 2. Choose the model

| Problem | Tool |
|---|---|
| Single span, one point or uniform load, simply supported or cantilever | `mech_beam_bending` |
| Several spans, frames, hinges, springs, mixed loads, settlement | `structures_frame` (2D) |
| Pin-jointed bars (loads only at joints) | `structures_truss` (2D or 3D) |
| 3D skeleton, torsion, biaxial bending | `structures3d_static` |
| Section properties from dimensions or an outline | `structures_section` first |
| Stress state, principal stresses, von Mises | `structures_stress` |
| Slender compression member | `structures_column` (Euler / Johnson / secant); frames: `structures3d_buckling` |
| Natural frequencies | `structures_modal` (2D) or `structures3d_modal`; both need `density` or `mass_per_length` |
| Plate pressure, buckling, frequency | `structures3d_plate`, `structures3d_plate_stability` |
| Beam on elastic foundation | `structures3d_foundation` |
| Shafts, gears, welds, vessels, belts, keys | `machine_*` tools |

## 3. Build the inputs carefully

- **Section first.** From dimensions, call `structures_section` and read `area`, `Ix`, `Sx_top`/`Sx_bottom`; results are SI. There is **no catalogue** (IPE, HEA, angles): a rolled I built from dimensions has no root radius (160 x 160 x 8 x 13 mm: Ix 2414 vs about 2492 cm4 in the catalogue, from memory). Ask for the catalogue A, I, W, or label it idealised. **Single angle or unequal section as a strut: buckling is about the weak principal axis**: draw the outline as a `polygon`, take `principal_I2` (the minimum) and `r_min = sqrt(I2/A)`, never Ix.
- **Records**:
  - `structures_frame`: nodes `id,x,y`; members `id,i,j,E,A,I[,release]` (release `none|start|end|both` = pin); supports `node,fixed|pinned|roller_x|roller_y|spring` (`roller_x` restrains x only: it slides in y; `roller_y` restrains y only; spring `c,spring,1e6 N/m,0 N/m,0 N*m/rad` = kx, ky, krot); nodal loads `node,Fx,Fy,Mz`; member loads `member,uniform,local|global,qx,qy` or `member,point,local|global,x,Px,Py`.
  - `structures_truss`: nodes `id,x,y[,z]` (`dimension=3` for 3D); members `id,i,j,E,A[,I]`; supports `node,xy` (letters of the fixed directions).
  - `structures3d_*`: nodes `id,x,y,z`; members `id,i,j,E,G,A,Iy,Iz,J`. `Iz` governs displacement along local y, `Iy` along local z: give `orientations` (`e,vector,0,0,1` or `e,roll,30 deg`) whenever the weak/strong axis matters.
- **Signs**: global x right, y up, counterclockwise moments positive, so a downward load has a **negative** `Fy`/`qy`. Tension is positive.
- **Pressure normal to a sloped surface** (wind, snow-on-slope): resolve it at every joint into **both Fx and Fy** (F = p x tributary length x bay spacing; Fx = F sin(slope), Fy = F cos(slope), signs per side). The horizontal parts cancel in the global sum but not in the member forces: dropping them changes diagonal forces enough to flip fail to pass. **Load the eave and support joints too (half a panel)**: the vertical part goes into the support, the horizontal part at a roller joint loads the bottom chord. Compare the sum of applied loads with the reactions and say how many totals you checked.
- Self-weight is never included: add it as a member load unless the person says it is included.
- Name members by their end coordinates, never by internal node ids. A rigid lateral support is `pinned` (or `roller_x`), not clamped.
- Too few supports: the tool refuses a mechanism; say what restraint is missing.

## 4. Run, then verify

1. Read `warnings`, `data.stability` / `data.conditioning`, and the equilibrium residual (about 1e-9 of the loads).
2. **Hand check** one number (label it): `P L^3/(3 E I)`, `5 w L^4/(384 E I)`, `w L^2/8`, `P L/(E A)`, `M c / I`. A mismatch above ~1 % means wrong input (units, sign, wrong axis).
3. **Convergence** for modal and buckling: rerun with larger `elements_per_member` (4 -> 10); a change above ~1 % means not converged.
4. Refusals are results (`ill-conditioned`, `mechanism`, `no compressive force`, `out of range`, `t/a < 0.1`): explain and fix the model, never bypass.
5. `end_forces` are member-end forces in local axes; the station CSV holds N, V, M and deflection along the member. **`structures_frame` gives the largest deflection as `values.max_member_deflection`** with member, position and signed value in `data.max_member_deflection_location`: quote it, not `maximum_nodal_displacement` (15.8 vs 13.3 mm for 15 kN at 1.5 m on 5 m; a warning says so).
6. `structures_modal`: every returned frequency is in `values` (`frequency_mode_<k>`; `first_frequency` is only the first), with effective mass fractions in `data.modes` and the shapes in `mode_shapes.csv`.
7. A secant (eccentric column) load is a first-yield load: answer in the person's criterion first and add the secant check as one extra line.

## 5. Evaluate against the criterion

Bending stress `sigma = M / S` (plus axial `N / A`; `structures_stress` for combined states). Utilization = demand / allowable; safety factor against yield = `Sy / sigma_vm`. Deflection ratio = span / max deflection against the user's limit. Buckling load factor `lambda < 1` means buckling before the applied load. Resonance margin only against frequencies the user supplies.

## 6. Report

Report as in `eng-toolkit`: model, supports, loads, signs; hand check, convergence, warnings; limits (unreviewed, linear, no code check, no connections). Station CSVs are N, N*m, m: for kN*m and mm charts pass rescaled inline series and hand over a PNG (`format="png"`, `data.png_path`; eng-plot-results); draw a deflection limit as `hlines`. If the person cannot see the picture, give the station table to paste into a spreadsheet; never draw a chart in text. Say so when the margin is thin (utilization above ~0.8) or the model idealizes something that matters.
