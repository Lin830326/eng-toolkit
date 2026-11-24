---
name: eng-flow-network
description: Use FIRST for "is this pump big enough", pump curve against system curve, operating point, flow split between branches, pipe pressure drop, valve Kv, open channel, water hammer (fluids_*, calc_* tools). DN vs bore, Kv to K.
---

# Flow network and pump workflow (eng-toolkit)

Load `eng-toolkit` too. Steady, incompressible (except `fluids_compressible`), Darcy-Weisbach with Colebrook; fluid properties are inputs. Unreviewed.

## 1. Pin down the problem (ask if it changes the answer)

Fluid and temperature (water 35 degC: `994 kg/m^3`, `0.72 mPa*s`, cp 4178); the **real pump curve** (points read from the catalogue; never invent one: ask); pipe material, DN **and schedule** (DN is a nominal size, not the bore); lengths (one way or total), fittings, valves with datasheet **Kv or Cv**, heat exchanger datasheet (dp at a stated flow); static lift (closed loop = none); how the flow divides (branches); required flow (from heat `Q = P / (rho cp dT)`).

## 2. Choose the tool

| Question | Tool |
|---|---|
| Parallel branches, loops, several pumps, flow split | `fluids_pipe_network` |
| One pump on ONE pipe or one equivalent K: operating point, efficiency, shaft power, affinity, NPSH | `fluids_pump_system` |
| Straight-pipe friction drop (no fittings) | `calc_pipe_pressure_drop` |
| Orifice or throttle Q-dp | `calc_orifice_flow` |
| Pump shaft power from Q, dp, efficiency | `calc_pump_power` |
| Channel depth, Froude, jump, profile | `fluids_open_channel` |
| Gas nozzle, shocks, Fanno, Rayleigh | `fluids_compressible` |
| Drag, terminal velocity | `fluids_external_flow` |
| Water hammer | `fluids_surge` |

## 3. Build the inputs

- **Bore from DN (carbon steel sch 40)**: DN25 26.64 mm, DN32 35.04, DN40 40.90, DN50 52.50, DN65 62.68, DN80 77.92, DN100 102.3. Ask the schedule; never use the nominal as the bore. Commercial steel roughness `0.045 mm`.
- **Loss coefficients are referred to the pipe velocity they sit on.** Typical (say "typical, please confirm"): 90 degree elbow 0.75, tee 1.0 on the branch, entrance 0.5, exit 1.0. Put a branch's own fittings in that branch's K column.
- **Kv to K** (Kv in m3/h per sqrt(bar), valid for water): `K = 200 (3600 A / Kv)^2`, A the pipe flow area in m2 (DN32, Kv 2.5: K = 385.6; Kv = 0.8646 Cv). **Plate exchanger or any component with "dp at flow Q0"**: `K = 2 dp / (rho v0^2)`, v0 = Q0/A of the pipe the K is attached to (45 kPa at 5.0 m3/h, DN50: K = 220.0); it then scales with Q^2 (one datasheet point, not a curve). Say it is hand arithmetic and show it.
- `fluids_pipe_network` records (example in the recipe): nodes `id,elevation,head|demand,value`; pipes `id,start,end,L,D,roughness,K` (K a plain number; one K lumps every fitting); pump `id,from,to,points,Q:H|Q:H,no` (the last field is a check valve). At least one fixed-head node is required, so model a closed loop as a reservoir node `S` (expansion vessel) with the pump from `S` and the return pipe back to `S`.
- `fluids_pump_system` points: `id,Q,H,eta` per row. Efficiency may be 0 only in the Q = 0 shut-off row; above it eta is 0.01..1 (else `input.out_of_range` at that row). No extrapolation beyond the last point; `curve_points` adds the curve table. System: `system_coefficient "4.5e6 s^2/m^5"` with `static_head "0 m"`, or pipe geometry plus `minor_loss_coefficient`.

## 4. Recipe: cooling loop (example inputs: 22 kW, 6 K, DN50 40 m main with 4 elbows and a plate exchanger, branches A and B; the pump curve is invented)

1. Required flow `22000 / (4178 x 994 x 6) = 8.83e-4 m3/s = 3.18 m3/h`; per branch 14 kW gives 2.02, 8 kW gives 1.16 m3/h. A pump reaching 3.18 m3/h on its curve proves nothing: the split decides.
2. `fluids_pipe_network`: nodes `S,0 m,head,0 m;P,0 m,demand,0 L/s;J1,0 m,demand,0 L/s;J2,0 m,demand,0 L/s`; pipes `main,P,J1,40 m,52.5 mm,0.045 mm,223.0;bA,J1,J2,12 m,35.04 mm,0.045 mm,388.1;bB,J1,J2,20 m,26.64 mm,0.045 mm,4.25;ret,J2,S,0.1 m,52.5 mm,0.045 mm,0` (223.0 = 4 x 0.75 + 220.0 exchanger; 388.1 = 2 x 0.75 + 1.0 tee + 385.6 valve at its preset Kv 2.5; 4.25 = 3 x 0.75 + 2 tees); pump `pump,S,P,points,0 m^3/h:16 m|1 m^3/h:15.7 m|2 m^3/h:15.1 m|3 m^3/h:14.2 m|4 m^3/h:12.9 m|5 m^3/h:11.0 m|6 m^3/h:8.6 m|7 m^3/h:6 m,no`; `density "994 kg/m^3"`, `dynamic_viscosity "0.72 mPa*s"`, `curve_points 8`.
3. **`value` is only the mass residual.** Read `data.pumps[0]` (flow 1.496e-3 m3/s = 5.385 m3/h, head 10.08 m) and `data.pipes[]` `flow_m3_s`: A 1.582, B 3.803 m3/h. A needs 2.02, so branch A is short (dT 7.7 K instead of 6; B gets 1.8 K). **A balancing valve with a preset: the datasheet Kv is the current setting, so the first fix is to open the preset** (Kv 6.3: K = 2 x 0.75 + 1.0 + 60.7 = 63.2 gives A 2.861, B 2.894 m3/h; pump 5.755 m3/h), then rerun with the new K.
4. **Curves come from `curve_points`** (`fluids_pipe_network`: exactly ONE pump; `fluids_pump_system`: one pump on one pipe). They write `system_curve.csv` (`flow_m3_s,system_head_m,pump_head_m,is_operating_point`; pump_system adds `efficiency`) with the operating point inserted as a row, and `data.operating_point`. Flow is in m3/s: for m3/h scale by 3600 (my arithmetic) into inline `series` (two lines, x in m3/h, y in m), `marker="5.385,10.08,operating point"`, labels "Q (m3/h)", "H (m)"; the title says which case (as installed, or valve opened): `eng-plot-results`.
5. Pump power: `fluids_pump_system` with the same points as `p,0 m^3/h,16 m,0;p,1 m^3/h,15.7 m,0.20;...` (eta 0, .20, .33, .43, .49, .51, .47, .38) and `system_coefficient "4.504e6 s^2/m^5"` (H/Q^2 from step 3, my arithmetic) gives 5.385 m3/h, 10.08 m, eta 0.495, shaft 297 W; or `calc_pump_power`.
6. **Independent hand check**: main-line loss `(f L/D + K) v^2/2g` with the reported v 0.6909 m/s and f 0.02362 = (18.0 + 223.0) x 0.02434 = 5.87 m, equal to `data.pipes` headloss; the straight part alone is `calc_pipe_pressure_drop` (40 m, 52.5 mm, 5.385 m3/h: 4.27 kPa, 0.438 m). Pump head = main loss + branch loss (10.08 = 5.87 + 4.21).

## 5. Other tools

- Channel: rectangular 2 m wide, 3 m3/s, slope 0.001, n 0.013: normal depth 0.984 m, Froude 0.49 (subcritical), critical depth 0.612 m. Modes: `analysis`, `uniform`, `profile`.
- Water hammer: DN500, 1000 m, 10 mm steel, 1.5 m/s, closure 1 s: wave speed 1211 m/s, Joukowsky 1.82 MPa (185 m), critical closure 1.65 s. A closure shorter than `critical_closure_time` gets the full Joukowsky rise (hand check `rho a dv`).

## 6. Outside the toolkit

Control-valve characteristics (Kv at the actual opening needs the vendor's table), NPSH beyond the simple margin, two-phase or non-Newtonian flow, surge in a network (one reservoir-pipe-valve only), exchanger heat transfer (`eng-thermal-check`), pump selection.

## 7. Report

As in `eng-toolkit`: operating flow, head, efficiency, power; flow per branch against required, **for the case as installed first; every sentence meant for a third party carries the change it depends on**; inputs (curve source, bore, K sources, fluid state); checks (hand check, `mass_balance_residual`, pump inside its curve); limits (K values typical, Kv scaling quadratic, unreviewed).
