---
name: eng-motor-drive-sizing
description: Use FIRST for "is this servo/motor and gearbox big enough", peak and RMS torque of a move, inertia ratio, induction or PMSM/DC motor points, buck/boost converter sizing and efficiency, transformers, three-phase faults (electric_* tools).
---

# Motor, drive and power workflow (eng-toolkit)

Load `eng-toolkit` too. All six tools are steady-state, linear, unreviewed.

## 1. Pin down the problem (ask if it changes the answer)

- **Motion sizing**: the whole cycle (move, **dwell/idle time, return stroke**), load mass or inertia, pulley radius or lead, **pulley, belt and gearbox inertia**, friction, gravity, external force, ratio, efficiency, the candidate's datasheet (never from memory); never fill a missing efficiency, friction or inertia.
- **Induction**: line voltage, frequency, poles, wye or delta, per-phase R1 X1 Xm R2 X2, losses, ONE operating point. **PMSM/BLDC/DC**: bus voltage, R, Ld, Lq, flux, pole pairs, current limit, speed or torque.
- **Converter**: Vin range (L and ripple at maximum Vin), Vout, Iout, frequency, ripple targets; efficiency needs both Rds(on), Qg, rise/fall, dead time, inductor DCR and saturation current, capacitor ESR (ask for the person's parts or label "assumed"). **Power**: kW, kVA or kvar; line-line V; lead or lag.

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

- **kVA is not kW.** W/kW/hp real, VA/kVA apparent, var reactive; a wrong family is refused: ask, never relabel.
- **Speed** is a string, `"1755 rpm"` or `"183.8 rad/s"`; results are in rad/s (rpm = rad/s x 9.549, my arithmetic). PMSM speed is mechanical. `poles` (induction) = poles, `pole_pairs` (PMSM) = pairs.
- **drive_sizing**: `gear_ratio` = motor revolutions per output revolution (a 10:1 reducer = 10); `efficiency` in (0,1]. `rotary|direct`: `load_inertia`, rad/s, N*m. `belt|rack`: `load_mass`, `radius`. `screw`: `load_mass`, `lead`; linear uses m/s and N.
  - `profile` rows `duration,start_speed,end_speed,load_effort` joined by `;` (the parameter shows an example). Units on every field; adjacent speeds match. Positive effort resists travel. **`load_effort` is external force only** (friction, process); inertia comes from `load_mass`/`load_inertia`, never also as m*a. The `distance` + `move_time` shortcut models ONE stroke per `cycle_time` by default: add `cycle_time` (the whole period, at least `strokes_per_cycle x move_time`) and `dwell_load` (effort held during the rest; gravity always acts). **An out-and-back axis needs `strokes_per_cycle=2`** (1..4; the return mirrors speed and load; one stroke understates the RMS by up to sqrt 2 and warns). A return with another load is written as `profile` rows (move, dwell row `10 s,0 m/s,0 m/s,<holding force>`, return, dwell). Check `data.cycle` (strokes, period, active fraction) against the person's strokes, dwell and period.
  - **Inertia**: `belt|rack` take only `load_mass`: add pulley and belt inertia as equivalent mass J/r^2 (my arithmetic). Gearbox input inertia goes into `rotor_inertia`. Blank `rotor_inertia` = zero: peak torque understated. `candidates` rows `name,inertia,rated_torque,max_torque,rated_speed,max_speed` use their own rotor; `duty` rows `duration,shaft_torque` with `rated_torque` check RMS separately. Vertical axis: `gravity_angle="90 deg"`.
- **Induction**: impedances per winding phase. Exactly one of `slip`, `speed`, `load_torque` (none: slip 0.05 is used). Off base frequency set `frequency` and `base_frequency`.
- **PMSM**: `current_limit` (peak phase) and `speed` are required; give `torque` OR `d_current`+`q_current`, else you get the envelope torque at that speed. Phase-peak values. DC/BLDC: `torque` or `speed`, not both.
- **Converter**: `output_voltage` is a positive magnitude. Give `inductance` or `target_current_ripple`; for an output-ripple target give `target_output_ripple` (at maximum Vin) plus `capacitor_esr`, and **always report `minimum_output_capacitance` next to the capacitor the person has**. `synchronous=true` replaces the diode by a low-side FET: give `low_side_resistance` (its Rds(on)) and `dead_time` (loss `Vf Io 2 t f`, `diode_drop` = body diode); `gate_charge` is the SUM of both FETs. With no loss input it warns "Ideal" (`data.losses_given` false, efficiency 1.0): never quote it.
- **Power flow**: P and Q are three-phase totals; fault impedances `R,X` in ohm; unbalanced faults need negative (ground: also zero) sequence.

## 4. Run, then verify

1. Read `warnings`, `data.assumptions`, status strings.
2. Hand check: accel torque `(J_load alpha + T_ext)/(k eta) + J_rotor k alpha` (k = ratio; braking multiplies by eta/k), RMS `sqrt(sum(T^2 t)/sum t)` over the whole cycle; `n_s = 120 f/poles`; `Te = 1.5 p psi iq`; buck `D = Vo/Vin`, `L = (Vin-Vo) D/(f dI)`, `C = dI/(8 f dV)`; `S = sqrt(3) V I`.
3. Curves: `plot_xy` writes the PNG by default, give `data.png_path`; use the CSV artifact (`induction_motor_curve.csv`: `x_column="speed_rpm"`, `y_columns="electromagnetic_torque_Nm"`). Drive torque-speed picture: inline series from `drive_profile.csv` (rpm = rad/s x 9.549), `hlines="<rated>,continuous;<peak>,peak"`, `vlines="<rated rpm>,rated;<max rpm>,max"`.

## 5. Interpret (refusals are results)

- `electric.no_solution`: load above the stable maximum torque, speed beyond synchronous. `electric.limit_exceeded`: torque beyond the envelope (rerun without `torque`). `electric.model_limit`: ripple above 10 % of DC voltage.
- **Candidates** (`data.candidates`): read each signed margin, not only `status`. Pass or fail depends on RMS torque vs rated (continuous) torque, peak torque vs maximum torque and peak speed vs the MAXIMUM speed. A peak speed above `rated_speed` but within the maximum passes: `status` says "above rated speed" with a ratio, `rated_speed_margin_rad_s` is negative and a warning asks for the datasheet torque-speed curve. Ask for it; the speed check stays open until then; never enlarge the motor for that line alone. A pass with under 30 % RMS margin warns: confirm stroke count and cycle first.
- **Rated power is a continuous rating**: judge a servo by RMS torque at RMS speed and the peak torque curve, not peak power vs rated power; never call peak power a hard limit.

## 6. Report

As in `eng-toolkit`: the asked case first, then alternatives (labelled); peak and RMS torque, peak speed (rpm and rad/s), `load_to_rotor_inertia_ratio`, against the datasheet limits; recompute after every input correction. Limits: steady state; no thermal dynamics, saturation, inverter losses, elasticity, harmonics; not a rating approval.
