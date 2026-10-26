---
name: eng-machine-design
description: Use to size or check shafts with keys and bearings (helical gear loads), spur gears, bolts, springs, welds, belts, pressure vessels and simple beams with the machine_* and mech_* tools. Says what to ask, what the tools cannot do, and hand checks.
---

# Machine element workflow (eng-toolkit)

Load `eng-toolkit` first. Each tool is one textbook formula set (elastic, static or simple fatigue, unreviewed), **not a standard check** (AGMA, ISO 6336, EN 13445, ASME, VDI 2230). A design verdict needs the person's own criteria, so ask before the first verdict.

## 1. Ask first (a wrong guess flips the verdict)

- **Shaft / bearing / key**: material grade with standard minimum `Su`, `Sy` (not recalled values), surface (machined, ground), **reliability** (e.g. 99 %), **design factor**, **service/application factor** (and whether it multiplies loads), bearing positions and which one locates axially, **direction of the axial force** (decides the sign of the bending couple), gear position, diameters on both sides of every shoulder and fillet radii, keyway type, bearing `C`, `C0`, speed, required life.
- **Vessel / bolts**: design pressure (relief setting, not working pressure), allowable stress and its source, **joint efficiency**, corrosion allowance, plate under-tolerance, **head shape**, cover or load area, bolt class, tightening and joint stiffness fraction, cycling.
- **Drives**: duty cycle including dwell and return stroke.

## 2. Tool choice

| Question | Tool |
|---|---|
| Shaft static and fatigue (Goodman, Soderberg, Gerber, ASME), twist, critical speed | `machine_shaft` |
| Bending moment of the shaft in one plane | `structures_frame` (see 3) |
| Spur gear Lewis bending and Hertz contact | `machine_gear` (spur only) |
| Key shear and crushing, spline capacity | `machine_key` |
| Bearing L10 life | `mech_bearing_life` |
| Bolt preload, torque, proof utilization | `mech_bolt_check` |
| Cylinder or sphere wall thickness | `machine_pressure_vessel` |
| Fillet weld group, belt drive, helical spring, one-span beam | `machine_weld`, `machine_belt`, `mech_spring_compression`, `mech_beam_bending` |
| Servo or gearbox sizing for a gantry | skill `eng-motor-drive-sizing` |

## 3. Shaft with a helical gear: the recipe (verified, 851.8 N*m output torque, 294 mm wheel, 15 deg helix, bearings 300 mm apart, gear 120 mm from A)

1. Forces by hand (label them so): `Wt = 2T/d = 5795 N`, `Wr = Wt tan(an)/cos(b) = 2183 N`, `Wa = Wt tan(b) = 1553 N`; the axial force acts at the pitch radius and adds a **couple `Wa d/2 = 228 N*m`** in the radial plane. No tool computes this (`machine_gear` is spur only).
2. One `structures_frame` per plane (pinned at A, `roller_y` at B, nodes at x = 0, 120, 300 mm): tangential `g,0 N,-5794.6 N,0 N*m` gives max 417.2 N*m; radial `g,0 N,-2183.4 N,228.2 N*m` gives 248.5 and with `-228.2 N*m` 294.1 (the sign is the axial direction: run both when unknown and report the worse). Read each side of the gear from the station CSVs, because the couple makes a jump; the resultant is `sqrt(M_t^2 + M_r^2)` per side (485.6 or 510.5 N*m).
3. `machine_shaft outer_diameter="60 mm" alternating_bending="485.6 N*m" mean_torque="851.8 N*m" ultimate_strength="630 MPa" yield_strength="370 MPa" kf=1.861 kfs=2.608 surface_finish="machined" reliability=99` gives endurance limit 166.4 MPa, Goodman 2.50. `kf` = 1 + q(Kt - 1): the tool applies the Kf you give (default none), so get `Kt`, `q` yourself (`fatigue_sn`, skill `eng-fatigue-check`). The tool checks a diameter, it does not size one: iterate. `bending_stress = 0` in that output only means no *static* load was given.
4. Bearing: `mech_bearing_life` needs `P`, which you build `P = X Fr + Y Fa` with `e, X, Y` from the user's catalogue by `f0 Fa / C0` (no table in the toolkit). A service factor multiplies `P`, never `C`. `C="37.1 kN" P="4740 N" speed="414.1 rpm" bearing_type="ball" required_life="25000 h"` gives L10h 19299 h and needs C 40.4 kN. Only the locating bearing takes `Fa`.
5. Key: `machine_key joint_type="key" length="70 mm" torque="851.8 N*m" shaft_diameter="60 mm" width="18 mm" height="11 mm" yield_strength="530 MPa"` gives minimum lengths 5.16 (shear) and 9.74 mm (crushing) at **no design factor**: multiply by the factor (2 gives 10.3 and 19.5). The fitted length is set by the hub, the standard key size by the diameter (check the key table).

## 4. Vessels, bolts, gears (verified examples)

- `machine_pressure_vessel geometry="cylinder" inner_radius="600 mm" internal_pressure="1.1 MPa" model="thin" allowable_stress="145.2 MPa"` gives 4.545 mm (Lame 4.563). There is no joint efficiency, allowance or dished head: fold `z` into the allowable and add allowances by hand. **`geometry="sphere"` gives 2.27 mm, valid only for a true hemisphere**; a 2:1 ellipsoidal end needs about the shell thickness. If the user quotes the EN 13445 forms, `allowable = f z - P/2` (shell) or `f z - P/4` (2:1 end) reproduces them (4.563 and 4.554 mm): say it is a workaround, not a code check. Nozzle reinforcement, saddles, external pressure: outside the toolkit.
- `mech_bolt_check diameter="12 mm" property_class="8.8" preload="36.67 kN" external_load="5.713 kN" load_share=0.25 nut_factor=0.2 proof_strength="580 MPa"` gives torque 88.0 N*m, utilization 0.78. Without `proof_strength` it estimates 576 MPa. **Bolt fatigue and joint stiffness are not covered**: hand check only, as `n = Se (Sut - si) / (Sut sa + Se (sm - si))` with `sa = C dF/(2 As)`, taking the user's bolt `Se` (it already includes the thread notch: no extra Kf); `fatigue_sn` correction mode assumes proportional loading and does not apply.
- `machine_gear teeth_1=20 teeth_2=60 face_width="30 mm" module="3 mm" torque="50 N*m" youngs_modulus_1="206 GPa" youngs_modulus_2="206 GPa" poisson_1=0.3 poisson_2=0.3` gives Wt 1667 N; contact stress needs both moduli. Lewis and Hertz only.
- `machine_weld segments_text="(0 mm,0 mm)-(100 mm,0 mm); (0 mm,60 mm)-(100 mm,60 mm)" leg_size="6 mm" force_y="-5 kN" load_x="150 mm" load_y="30 mm"`: `load_*` is where the force acts. Hand check throat 0.707 x 6 = 4.24 mm.

## 5. Limits and report

Outside the toolkit: any standard rating, bolt fatigue, joint stiffness, nozzle openings, creep, shock factors, lubrication, thermal growth. If an input is missing and decides pass or fail, ask; do not iterate unverified numbers. Report as in `eng-toolkit`: result, inputs and the factors the user chose, hand check (labelled "independent hand check"), limits.
