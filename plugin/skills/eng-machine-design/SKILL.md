---
name: eng-machine-design
description: Use FIRST for "what diameter shaft", key length, bearing life, gear, bolt, weld, spring, belt or pressure-vessel thickness questions (machine_* and mech_* tools). Says what to ask, what the tools cannot do, hand checks.
---

# Machine element workflow (eng-toolkit)

Load `eng-toolkit` too. Each tool is one textbook formula set (elastic, static or simple fatigue), **not a standard check** (AGMA, ISO 6336, EN 13445, ASME, VDI 2230). A design verdict needs the person's own criteria, so ask before the first verdict.

## 1. Ask first (a wrong guess flips the verdict)

Ask ONLY the items of the matching list; never say the person gave an input he did not. If inputs are missing, stop after the questions: no numbers, not even provisional.

- **Shaft / bearing / key**: material grade with standard minimum `Su`, `Sy` (not recalled), surface (machined, ground), **reliability**, **design factor**, **service/application factor** (and whether it multiplies loads), bearing positions and which one locates axially, **direction of the axial force** (decides the sign of the bending couple), gear position, diameters on both sides of every shoulder and fillet radii, keyway type, bearing `C`, `C0`, speed, required life.
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
| Servo or gearbox sizing | skill `eng-motor-drive-sizing` |

## 3. Shaft with a helical gear: the recipe (example inputs: 600 N*m on the wheel shaft, z 58, mn 3, helix 12 deg, bearings 260 mm apart, gear 100 mm from A, 45 mm seat)

1. Forces by hand (label them so). **Pitch diameter `d = z mn / cos(beta)`** = 177.9 mm (not z mn); if the person gave d, use theirs and compare. `Wt = 2T/d = 6746 N`, `Wr = Wt tan(an)/cos(b) = 2510 N`, `Wa = Wt tan(b) = 1434 N`; the axial force acts at the pitch radius and adds a **couple `Wa d/2 = 127.5 N*m`** in the radial plane. `machine_gear` is spur only: with the transverse module `mn/cos(beta)` = 3.067 mm it gives the same d and Wt, but its radial force (2455 N) is the spur value: use the hand Wr and Wa.
2. One `structures_frame` per plane (pinned at A, `roller_y` at B, nodes at x = 0, 100, 260 mm, two members): tangential `g,0 N,-6745.8 N,0 N*m` gives 415.1 N*m; radial `g,0 N,-2510.1 N,127.5 N*m` gives 203.5 (left of the gear) and 76.0 (right); `-127.5 N*m` gives 105.4 and 232.9. **The sign is the axial direction: ask the person and run only that case**; run both only when it is unknown, and report the worse. Read each side of the gear from the station CSVs (the couple jumps); the resultant is `sqrt(M_t^2 + M_r^2)` per side (476.0 N*m worst, the second case, right of the gear).
3. `machine_shaft outer_diameter="45 mm" alternating_bending="476.0 N*m" mean_torque="600 N*m" ultimate_strength="800 MPa" yield_strength="550 MPa" kf=1.9 kfs=1.5 surface_finish="machined" reliability=99` gives endurance limit 206.1 MPa, Goodman 1.668. `kf` = 1 + q(Kt - 1): the tool applies the Kf you give (default none); get `Kt`, `q` yourself (skill `eng-fatigue-check`). The tool checks a diameter, it does not size one: iterate. With only fatigue loads given, the static block uses `|mean| + alternating` and says so in a warning; `kt` and `kts` multiply the static stresses (warned): keep them 1 for a ductile static check; the notch goes in `kf`/`kfs`.
4. Bearing: `mech_bearing_life` needs `P`, which you build `P = X Fr + Y Fa` with `e, X, Y` from the user's catalogue by `f0 Fa / C0` . A service factor multiplies `P`, never `C`. Only the locating bearing takes `Fa`. `dynamic_load_rating="28.1 kN" equivalent_load="4836 N" speed="315 rpm" bearing_type="ball" required_life="20000 h"` gives L10h 10380 h and needs C 35.0 kN.
5. Key: `machine_key joint_type="key" length="56 mm" torque="600 N*m" shaft_diameter="45 mm" width="14 mm" height="9 mm" yield_strength="450 MPa"` gives minimum lengths 7.34 (shear) and 13.17 mm (crushing) at **no design factor**: multiply by the factor (2 gives 14.7 and 26.3). The fitted length is set by the hub, the key size by the diameter (key table).

## 4. Vessels, bolts, gears (verified examples)

- `machine_pressure_vessel geometry="cylinder" inner_radius="400 mm" internal_pressure="2.2 MPa" model="thin" allowable_stress="150 MPa" joint_efficiency=0.7 corrosion_allowance="2 mm"` gives 8.381 mm thin, 8.471 mm Lame and `nominal_thickness_lame` 10.47 mm. `joint_efficiency` (0.5..1) multiplies the allowable and `corrosion_allowance` is added to the nominal wall; both need `allowable_stress`; under-tolerance is yours. **`geometry="sphere"` gives 4.19 mm, valid only for a true hemisphere**; a 2:1 ellipsoidal or torispherical end needs about the shell thickness. There is no dished-end formula: say "outside the toolkit" and give the code form as "hand method, not tool-checked". **Hydrotest**: test stress on the new wall minus under-tolerance against the code's test allowable (hand method). Nozzle reinforcement, saddles, external pressure: outside the toolkit.
- `mech_bolt_check diameter="10 mm" property_class="8.8" preload="25.2 kN" external_load="3.351 kN" load_share=0.3 nut_factor=0.2 proof_strength="580 MPa"` gives torque 50.4 N*m, utilization 0.779. **Bolt fatigue and joint stiffness are not covered**: the preload is a fixed mean and only `sa = C P_alt/(2 At)` alternates, so `fatigue_sn` correction mode (mean and alternating scaled together; the tool warns) does not apply. Hand method, labelled: `n = Se (Sut - si) / (Sut sa + Se (sm - si))` with the person's `Se`. A bolt `Se` the person states already includes the thread notch: no extra Kf.
- `machine_gear` (18/54 teeth, module 2.5 mm, 30 N*m: Wt 1333 N): contact stress needs both moduli and Poisson ratios.
- `machine_weld segments_text="(0 mm,0 mm)-(80 mm,0 mm); (0 mm,50 mm)-(80 mm,50 mm)" leg_size="5 mm" force_y="-4 kN" load_x="120 mm" load_y="25 mm"`: `load_*` is where the force acts; hand check throat 0.707 x 5 = 3.54 mm.

## 5. Limits and report

Outside the toolkit: any standard rating, bolt fatigue, joint stiffness, nozzle openings, creep, shock factors, lubrication, thermal growth. Report as in `eng-toolkit`: result, inputs and the user's factors, hand check (labelled "independent hand check"), limits.
