---
name: eng-motor-drive-sizing
description: Use to size a motor and gearbox for a motion profile (peak and RMS torque, inertia, speed), or for induction, PMSM/BLDC/DC motor points, buck/boost converters, transformers, three-phase power and faults with the eng-toolkit electric_* tools. Load eng-toolkit first.
---

# Motor, drive and power workflow (eng-toolkit)

Load `eng-toolkit` first. All six tools are **steady-state, linear, unreviewed estimates**.

## 1. Pin down the problem (ask if it changes the answer)

- **Motion sizing**: the whole cycle (move, **dwell/idle time, return stroke**), load mass or inertia, pulley radius or lead, **pulley, belt and gearbox inertia**, friction, gravity, external force, ratio, efficiency, the candidate's datasheet (never from memory).
- **Induction**: line voltage, frequency, poles, wye or delta, per-phase R1 X1 Xm R2 X2, losses, ONE operating point. **PMSM/BLDC/DC**: bus voltage, R, Ld, Lq, flux, pole pairs, current limit, speed or torque.
- **Converter**: Vin range (size L and ripple at maximum Vin), Vout, Iout, frequency, ripple targets; for efficiency: both Rds(on), Qg, rise/fall, dead time, inductor DCR, capacitor ESR. **Power**: kW, kVA or kvar; line-line V; lead or lag.

## 2. Choose the tool

| Question | Tool |
|---|---|
| Motor + gearbox for a move: peak/RMS torque, speed, candidate margins | `electric_drive_sizing` |
| Induction torque-speed, slip, load point, V/f | `electric_induction_motor` |
| DC, BLDC, PMSM dq, MTPA, voltage-limited envelope | `electric_pm_dc_motor` |
| Buck, boost, buck-boost: duty, L, C, CCM/DCM, stresses, losses | `electric_converter` |
| Transformer regulation, efficiency | `electric_transformer` |
| Three-phase P/Q/S, pf, feeder drop, sequence components, faults | `electric_power_flow` |

## 3. Build the inputs

- **kVA is not kW.** W/kW/hp real, VA/kVA apparent, var reactive; the wrong family is refused: ask, never relabel. Transformer `rating` is VA; motor `output_power` is W.
- **Speed** is a string, `"1755 rpm"` or `"183.8 rad/s"`; results are in rad/s (rpm = rad/s x 9.549, my arithmetic; report rpm and say so). PMSM speed is mechanical (electrical = `pole_pairs` x mechanical). Induction `poles` = poles, PMSM `pole_pairs` = pairs.
- **drive_sizing**: `gear_ratio` = motor revolutions per output revolution (10:1 reducer = 10; `direct`: 1); `efficiency` in (0,1] (1/eta motoring). `rotary|direct`: `load_inertia`, rad/s, N*m. `belt|rack`: `load_mass`, `radius`. `screw`: `load_mass`, `lead`; linear uses m/s and N.
  - `profile` rows `duration,start_speed,end_speed,load_effort` joined by `;`, e.g. `1 s,0 rad/s,4 rad/s,0 N*m;1 s,4 rad/s,0 rad/s,0 N*m`. Units on every field; adjacent speeds match. Positive effort resists travel. **`load_effort` is external force only** (friction, process); inertia comes from `load_mass`/`load_inertia`, never also as m*a. The `distance` + `move_time` shortcut covers the move only, so its RMS is too high (30 % in one case): add `cycle_time` (the period, at least `move_time`) and `dwell_load` (effort held during the rest; gravity always acts) and the RMS covers the repeating cycle. With a `profile`, write every phase (dwell `10 s,0 m/s,0 m/s,<holding force>`, return stroke) as rows.
  - **Inertia**: `belt|rack` take only `load_mass`: add pulley and belt inertia as equivalent mass J/r^2 (my arithmetic). Gearbox input inertia goes into `rotor_inertia`. Blank `rotor_inertia` = zero: peak torque understated. `candidates` rows `name,inertia,rated_torque,max_torque,rated_speed,max_speed` (speeds as `"3000 rpm"` or rad/s) use their own rotor. `duty` rows `duration,shaft_torque` with `rated_torque` check RMS separately. Vertical axis: `gravity_angle="90 deg"`.
- **Induction**: impedances per winding phase. Exactly one of `slip`, `speed`, `load_torque` (none: slip 0.05 is silently used). Off base frequency set `frequency` and `base_frequency`, else no V/f scaling.
- **PMSM**: `current_limit` (peak phase) and `speed` are required; give `torque` OR `d_current`+`q_current`, else you get the envelope torque at that speed. Phase-peak values; SVPWM limit `Vdc/sqrt(3)`. DC/BLDC: `torque` or `speed`, not both.
- **Converter**: `output_voltage` is a positive magnitude. Give `inductance` or `target_current_ripple`; for an output-ripple target give `target_output_ripple` (at maximum Vin) plus `capacitor_esr` and report `minimum_output_capacitance`, not only the capacitor on hand. In DCM the ripple target means peak current. `synchronous=true` replaces the diode by a low-side FET: give `low_side_resistance` (its Rds(on)) and `dead_time` (loss `Vf Io 2 t f` with `diode_drop` = body diode); `gate_charge` is both FETs. With no loss input it warns "Ideal" (`data.losses_given` false, efficiency 1.0): never quote it.
- **Power flow**: P and Q are three-phase totals; fault impedances `R,X` in ohm; unbalanced faults need negative (ground: also zero) sequence.

## 4. Run, then verify

1. Read `warnings`, `data.assumptions`, status strings.
2. Hand check one number: drive accel torque `(J_load alpha + T_ext)/(k eta) + J_rotor k alpha` (k = ratio; braking multiplies by eta/k), RMS `sqrt(sum(T^2 t)/sum t)` over the whole cycle; induction `n_s = 120 f/poles`; PMSM `Te = 1.5 p psi iq`; buck `D = Vo/Vin`, `L = (Vin-Vo) D/(f dI)`, `C = dI/(8 f dV)`; `S = sqrt(3) V I`; fault `I = (Vll/sqrt(3))/|Z1|`.
3. Curves as a PNG (`format="png"`, give `data.png_path`): `plot_xy` on the CSV artifact (`induction_motor_curve.csv`: `x_column="speed_rpm"`, `y_columns="electromagnetic_torque_Nm"`; `pmsm_envelope.csv`). Drive torque-speed picture: inline series from `drive_profile.csv` (rpm = rad/s x 9.549, one point per segment start and end), `hlines="<rated>,continuous;<peak>,peak"`, `vlines="<rated rpm>,rated;<max rpm>,max"`.

## 5. Interpret (refusals are results)

- `electric.no_solution`: load above the stable maximum torque, speed beyond synchronous. `electric.limit_exceeded`: torque beyond the envelope (rerun without `torque` for the maximum). `electric.model_limit`: ripple above 10 % of DC voltage: raise C or lower ESR.
- **Candidates** (`data.candidates`): read each signed margin, not only `status`. Pass or fail depends on RMS torque vs rated (continuous) torque, peak torque vs maximum torque and peak speed vs the MAXIMUM speed. A peak speed above `rated_speed` but within the maximum passes: `status` says "above rated speed (1.06 x)", `rated_speed_margin` is negative and a warning asks for the datasheet torque-speed curve (the continuous torque derates there; the tool does not model it). Ask for that curve; the speed check stays open until then. Never enlarge the motor for that line alone.
- **Rated power is a continuous rating**: judge a servo by RMS torque at RMS speed and the peak torque curve, not peak power vs rated power.

## 6. Report

As in `eng-toolkit`: peak and RMS torque, peak speed (rpm and rad/s), `load_to_rotor_inertia_ratio`, always against the datasheet limits; recompute after every input correction. Limits: unreviewed steady state; no thermal dynamics, saturation, inverter losses, elasticity, harmonics or protection; not a rating approval.
