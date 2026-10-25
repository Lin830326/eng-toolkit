---
name: eng-motor-drive-sizing
description: Use to size a motor and gearbox for a motion profile (torque, inertia, RMS duty, regeneration), to get an induction motor's torque-speed curve or load point, a PMSM/BLDC/DC motor's currents and field weakening, a buck/boost converter's L, C and ripple, a transformer's regulation, or three-phase power and fault currents with the eng-toolkit electric_* tools. Covers record formats, unit pitfalls (kVA vs kW, rad/s vs rpm), hand checks and refusals. Load eng-toolkit first.
---

# Motor, drive and power workflow (eng-toolkit)

Load `eng-toolkit` first for unit rules and report format. All six tools are **steady-state, linear, unreviewed estimates**: no thermal S1/S3 dynamics, saturation, harmonics, transients, protection or code check.

## 1. Pin down the problem (ask if it changes the answer)

- **Motion sizing**: move (distance and time, or speed profile), load inertia or mass, friction, gravity, external force, transmission (ratio, efficiency, radius or lead), candidate ratings.
- **Induction motor**: line voltage, frequency, poles, wye or delta, per-phase R1 X1 Xm R2 X2, losses, ONE operating point (slip, speed or load torque).
- **PMSM / BLDC / DC**: bus voltage, R, Ld, Lq, flux, pole pairs, current limit, speed, torque.
- **Converter**: Vin, Vout, Iout, frequency, ripple targets, parasitics. **Power**: kW, kVA or kvar; line-line V; lagging or leading.

## 2. Choose the tool

| Question | Tool |
|---|---|
| Motor + gearbox for a move: peak/RMS torque, speed, regeneration, candidate margins | `electric_drive_sizing` |
| Induction torque-speed, slip, load point, V/f | `electric_induction_motor` |
| DC, trapezoidal BLDC, PMSM dq, MTPA, voltage-limited envelope | `electric_pm_dc_motor` |
| Buck, boost, inverting buck-boost: duty, L, C, CCM/DCM, stresses, losses | `electric_converter` |
| Transformer tests or circuit, regulation, efficiency | `electric_transformer` |
| Three-phase P/Q/S, pf correction, feeder drop, per-unit, sequence components, faults | `electric_power_flow` |

## 3. Build the inputs

- **kVA is not kW.** Real W/kW/hp, apparent VA/kVA, reactive var/kvar; the wrong family is refused (`units.dimension_mismatch`): ask which the user means, never relabel. Transformer `rating` and `base_power` are VA; motor `output_power` and losses are W.
- **Speed** is a string, `"1755 rpm"` or `"183.8 rad/s"`; results are in rad/s. Frequency is Hz only. `Hz` is refused in back-EMF/torque constants (use `V*s/rad`, `V/krpm`). PMSM speed is mechanical, electrical = `pole_pairs` x mechanical. Induction `poles` counts poles, PMSM `pole_pairs` pairs (8 poles = 4 pairs).
- **drive_sizing**: `gear_ratio` = motor revolutions per output revolution (10:1 reducer = 10; `direct` needs 1); `efficiency` in (0,1], applied as 1/eta motoring and eta braking. `rotary|direct`: `load_inertia`, rad/s, N*m. `belt|rack`: `load_mass`, `radius`. `screw`: `load_mass`, `lead`. Linear uses m/s and N.
  - `profile` rows `duration,start_speed,end_speed,load_effort` joined by `;`, e.g. `1 s,0 rad/s,4 rad/s,0 N*m;1 s,4 rad/s,0 rad/s,0 N*m`. Every field has a unit (also `0 rad/s`); adjacent speeds match. Positive effort resists travel, negative assists. Alternative: `distance` + `move_time` [+ `acceleration_time` <= half; blank = triangle] with `load_torque`/`load_force`, never together with `profile`.
  - Blank `rotor_inertia` means zero: peak torque and regeneration are understated. Pass it, or `candidates` rows `name,inertia,rated_torque,max_torque,rated_speed,max_speed` (each recomputed with its own rotor). `duty` rows `duration,shaft_torque` with `rated_torque` give a separate RMS check. Vertical linear axis: `gravity_angle="90 deg"`.
- **Temperature** is only a rise: `"40 K"` or `delta_degC`; `degC` is refused. `thermal_resistance` and `allowed_temperature_rise` come as a pair.
- **Induction**: impedances per winding phase. Give exactly one of `slip`, `speed`, `load_torque` (`slip=0` means not given; with none, slip 0.05 is silently used). Off base frequency set `frequency` and `base_frequency` (`line_voltage` is the base voltage); without it voltage and reactances are not scaled and no V/f scaling happens. `mode="nameplate"` is a non-unique fit: its starting and breakdown torque are rough.
- **PMSM**: `current_limit` (peak phase) and `speed` are required (also with `speed_points`); give `torque` OR `d_current`+`q_current`; with neither you get the envelope torque at that speed. Values are phase-peak; linear SVPWM limit `Vdc/sqrt(3)`. DC/BLDC: `torque` or `speed`, not both.
- **Converter**: `output_voltage` is a positive magnitude (buck below Vin, boost above). Give `inductance` or `target_current_ripple`; optionally `output_capacitance` or `target_output_ripple` plus `capacitor_esr`. In DCM the ripple target means peak current.
- **Power flow**: P and Q are three-phase totals; current angle references phase-A voltage; fault impedances are `R,X` records in ohm; unbalanced faults need negative (ground faults also zero) sequence.

## 4. Run, then verify

1. Read `warnings`, `data.assumptions`, status strings.
2. Hand check one number: drive accel torque `(J_load alpha + T_ext)/(k eta) + J_rotor k alpha` (k = ratio; braking multiplies by eta/k instead), RMS `sqrt(sum(T^2 t)/sum t)`; induction `n_s = 120 f/poles`, `T_shaft omega = P_shaft`; PMSM `Te = 1.5 p psi iq` (Ld=Lq), `|v| <= Vdc/sqrt(3)`; buck `D = Vo/Vin`, `L = (Vin-Vo) D/(f dI)`, `C = dI/(8 f dV)`; boost `D = 1-Vin/Vo`, `L = Vin D/(f dI)`, `C = Io D/(f dV)`; `S = sqrt(3) V I`, `P = S pf`; bolted fault `I = (Vll/sqrt(3))/|Z1|`.
3. To show a curve, call `plot_xy` with `data_file` = the CSV artifact (`induction_motor_curve.csv`: `x_column="speed_rpm"`, `y_columns="electromagnetic_torque_Nm"`; `pmsm_envelope.csv`).

## 5. Interpret (refusals are results)

- `electric.no_solution`: load above the stable maximum shaft torque (`maximum_shaft_torque`, below `breakdown_torque`), speed beyond synchronous, boost that cannot deliver with its losses. `electric.limit_exceeded`: torque beyond the voltage/current envelope (rerun without `torque` for the achievable maximum), current over limit. `electric.model_limit`: ripple or ESR ripple above 10 % of DC voltage, the stiff-output model fails: say so, raise C or lower ESR. `input.invalid`: wrong mode or direction.
- Candidates: signed margins and `pass within stated limits` or `fail` inside a successful call (`data.candidates`): compare RMS with rated torque, peak with maximum torque, peak speed with rated speed. RMS screening assumes continuous cooling.

## 6. Report

Report as in `eng-toolkit`. Result: key numbers with units. Assumptions: model, ratio/efficiency convention, losses, supply. Checks: hand check, warnings, refusals explained. Limits: unreviewed steady-state estimate; no thermal dynamics, saturation, inverter losses, elasticity, harmonics, protection or code check; not a rating approval.
