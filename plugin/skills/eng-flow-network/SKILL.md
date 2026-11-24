---
name: eng-flow-network
description: Use FIRST for "is this pump big enough", pump curve against system curve, operating point, flow split between branches, pipe pressure drop, valve Kv, open channel, water hammer (fluids_*, calc_* tools). DN vs bore, Kv to K.
---

# Flow network and pump workflow (eng-toolkit)

Load `eng-toolkit` too. Steady, incompressible (except `fluids_compressible`), Darcy-Weisbach with Colebrook. Unreviewed estimates; fluid properties are inputs.

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
- **Kv to K** (Kv in m3/h per sqrt(bar), valid for water): `K = 200 (3600 A / Kv)^2`, A the pipe flow area in m2 (DN32, Kv 2.5: K = 385.6; Cv: Kv = 0.8646 Cv). **Plate exchanger or any component with "dp at flow Q0"**: `K = 2 dp / (rho v0^2)`, v0 = Q0/A of the pipe the K is attached to (45 kPa at 5.0 m3/h, DN50: K = 220.0); it then scales with Q^2 (one datasheet point, not a curve). This conversion is the only hand arithmetic: say so and show it.
- `fluids_pipe_network` records: nodes `S,0 m,head,0 m;P,0 m,demand,0 L/s`; pipes `id,start,end,L,D,roughness,K` (K a plain number; one K lumps every fitting); pump `pump,S,P,points,0 m^3/h:12.4 m|1 m^3/h:12.1 m|...,no` (the last field is a check valve). At least one fixed-head node is required, so model a closed loop as a reservoir node `S` (expansion vessel) with the pump from `S` and the return pipe back to `S`.
- `fluids_pump_system` points: `p,0 m^3/h,12.4 m,0;p,1 m^3/h,12.1 m,0.22;...` (id,Q,H,eta). Efficiency may be 0 only in the Q = 0 shut-off row; above it eta is 0.01..1 (else `input.out_of_range` at that row). No extrapolation beyond the last point. System: `system_coefficient "4.5e6 s^2/m^5"` with `static_head "0 m"`, or pipe geometry plus `minor_loss_coefficient`.

## 4. Recipe: cooling loop (example inputs: 22 kW, 6 K, DN50 40 m main with 4 elbows and a plate exchanger, branches A and B; pump curve points are invented for the example)

1. Required flow `22000 / (4178 x 994 x 6) = 8.83e-4 m3/s = 3.18 m3/h`; per branch 14 kW gives 2.02, 8 kW gives 1.16 m3/h. A pump reaching 3.18 m3/h on its curve proves nothing: the split decides.
2. Network: nodes `S,0 m,head,0 m;P,0 m,demand,0 L/s;J1,0 m,demand,0 L/s;J2,0 m,demand,0 L/s`; pipes `main,P,J1,40 m,52.5 mm,0.045 mm,223.0;bA,J1,J2,12 m,35.04 mm,0.045 mm,388.1;bB,J1,J2,20 m,26.64 mm,0.045 mm,4.25;ret,J2,S,0.1 m,52.5 mm,0.045 mm,0` (223.0 = 4 x 0.75 + 220.0 exchanger; 388.1 = 2 x 0.75 + 1.0 tee + 385.6 valve at its preset Kv 2.5; 4.25 = 3 x 0.75 + 2 tees); the pump points `0 m^3/h:16 m|1 m^3/h:15.7 m|...|7 m^3/h:6 m`; `density "994 kg/m^3"`, `dynamic_viscosity "0.72 mPa*s"`.
3. **`value` is only the mass residual.** Read `data.pumps[0]` (flow 1.490e-3 m3/s = 5.364 m3/h, head 10.00 m) and `data.pipes[]` `flow_m3_s`: A 1.576, B 3.788 m3/h. A needs 2.02, so branch A is short (dT 7.7 K instead of 6; B gets 1.8 K). **A balancing valve with a preset: the datasheet Kv is the current setting, so the first fix is to open the preset** (Kv 6.3 gives A 2.86, B 2.89 m3/h), then rerun with the new K. Pump power: `fluids_pump_system` with the points as `p,0 m^3/h,16 m,0;p,1 m^3/h,15.7 m,0.20;...` and `system_coefficient "4.505e6 s^2/m^5"` (H/Q^2 from this result, my arithmetic) gives 5.364 m3/h, 10.00 m, eta 0.475, shaft 305 W; or `calc_pump_power`.
4. **No tool returns a curve table.** System curve: call `fluids_pipe_network` again with the pump removed, `P,0 m,demand,-4 m^3/h` (negative demand injects flow) and `S` fixed: the head at `P` is the head the system needs at that flow (2, 4, 6 m3/h give 1.44, 5.61, 12.48 m). Chart with `plot_xy` inline `series` (two lines, x in m3/h, y in m), `vlines` at the operating flow and `hlines` at its head, labelled "Q (m3/h)", "H (m)", `format="png"`, `data.png_path` handed over; the title says which case (as installed, or with the valve opened): see `eng-plot-results`.
5. **Independent hand check**: main-line loss `(f L/D + K) v^2/2g` with the reported v 0.6883 m/s and f 0.02363 = (18.0 + 223.0) x 0.02416 = 5.82 m, equal to `data.pipes` headloss; the straight part alone is `calc_pipe_pressure_drop` (40 m, 52.5 mm, 5.364 m3/h: 4.24 kPa, 0.435 m). Pump head = main loss + branch loss (10.00 = 5.82 + 4.18).

## 5. Other tools (call verified)

- Channel: rectangular 2 m wide, 3 m3/s, slope 0.001, n 0.013: normal depth 0.984 m, Froude 0.49 (subcritical), critical depth 0.612 m. Modes: `analysis`, `uniform`, `profile`.
- Water hammer: DN500, 1000 m, 10 mm steel, 1.5 m/s, closure 1 s: wave speed 1211 m/s, Joukowsky 1.82 MPa (185 m), critical closure 1.65 s. A closure shorter than `critical_closure_time` gets the full Joukowsky rise (hand check `rho a dv`).
- Nozzle: 1 cm2, 6 bar, 20 degC, back pressure 1 bar: choked, 0.1416 kg/s (air by default). Drag: `fluids_external_flow`.

## 6. Outside the toolkit

Control-valve characteristics (Kv at the actual opening needs the vendor's table), NPSH beyond the simple margin, two-phase or non-Newtonian flow, surge in a network (one reservoir-pipe-valve only), exchanger heat transfer (`eng-thermal-check`), pump selection.

## 7. Report

As in `eng-toolkit`: operating flow, head, efficiency, power; flow per branch against required, **for the case as installed first; every sentence meant for a third party carries the change it depends on**; inputs (curve source, bore, K sources, fluid state); checks (hand check, `mass_balance_residual`, pump inside its curve); limits (K values typical, Kv scaling quadratic, unreviewed).
