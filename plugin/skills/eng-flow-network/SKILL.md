---
name: eng-flow-network
description: Use for liquid flow jobs with the eng-toolkit fluids_* and calc_* tools - pump and system curve, operating point, parallel branches and flow split, pipe losses, orifice, open channel, nozzle, drag, water hammer. Covers DN vs bore and Kv to K. Load eng-toolkit first.
---

# Flow network and pump workflow (eng-toolkit)

Load `eng-toolkit` first. Steady, incompressible (except `fluids_compressible`), Darcy-Weisbach with Colebrook. Unreviewed estimates; fluid properties are inputs.

## 1. Pin down the problem (ask if it changes the answer)

Fluid and temperature (water 35 degC: `994 kg/m^3`, `0.72 mPa*s`, cp 4178); the **real pump curve** (flow, head, efficiency points read from the catalogue; never invent or assume one: ask); pipe material, DN **and schedule** (DN is a nominal size, not the bore); lengths (one way or total), fittings, valves with datasheet **Kv or Cv**, heat exchanger datasheet (dp at a stated flow); static lift (closed loop = none); how the flow divides (branches); required flow (from heat `Q = P / (rho cp dT)`).

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
| Water hammer (Joukowsky, Michaud, MOC) | `fluids_surge` |

## 3. Build the inputs

- **Bore from DN (carbon steel sch 40)**: DN25 26.64 mm, DN32 35.04, DN40 40.90, DN50 52.50, DN65 62.68, DN80 77.92, DN100 102.3. Ask the schedule; never use the nominal as the bore. Commercial steel roughness `0.045 mm`.
- **Loss coefficients are referred to the pipe velocity they sit on.** Typical (say "typical, please confirm"): 90 degree elbow 0.75, tee 1.0 on the branch, entrance 0.5, exit 1.0. Put a branch's own fittings in that branch's K column.
- **Kv to K** (Kv in m3/h per sqrt(bar), valid for water): `K = 200 (3600 A / Kv)^2`, A the pipe flow area in m2 (DN32, Kv 4.0: K = 150.6; Cv: Kv = 0.8646 Cv). **Plate exchanger or any component with "dp at flow Q0"**: `K = 2 dp / (rho v0^2)`, v0 = Q0/A of the pipe the K is attached to (70 kPa at 3.0 m3/h, DN40: K = 350.1); it then scales with Q^2 (one datasheet point, not a curve). This conversion is the only hand arithmetic: say so and show it.
- `fluids_pipe_network` records: nodes `S,0 m,head,0 m;P,0 m,demand,0 L/s`; pipes `id,start,end,L,D,roughness,K` (K a plain number; one K lumps every fitting); pump `pump,S,P,points,0 m^3/h:12.4 m|1 m^3/h:12.1 m|...,no` (the last field is a check valve). At least one fixed-head node is required, so model a closed loop as a reservoir node `S` (expansion vessel) with the pump from `S` and the return pipe back to `S`.
- `fluids_pump_system` points: `p,0 m^3/h,12.4 m,0;p,1 m^3/h,12.1 m,0.22;...` (id,Q,H,eta). Efficiency may be 0 only in the Q = 0 shut-off row; above it eta is 0.01..1 (else `input.out_of_range` at that row). No extrapolation beyond the last point. System: `system_coefficient "1.334e7 s^2/m^5"` with `static_head "0 m"`, or pipe geometry plus `minor_loss_coefficient`.

## 4. Recipe: cooling loop (verified: 15 kW, 5 K, DN40 60 m, 6 elbows, plate exchanger, branches A and B)

1. Required flow `15000 / (4178 x 994 x 5) = 7.2e-4 m3/s = 2.6 m3/h`; per branch 9 kW gives 1.56, 6 kW gives 1.04 m3/h. A pump reaching 2.6 m3/h on its curve proves nothing: the split decides.
2. Network: nodes `S,0 m,head,0 m;P,0 m,demand,0 L/s;J1,0 m,demand,0 L/s;J2,0 m,demand,0 L/s`; pipes `main,P,J1,60 m,40.9 mm,0.045 mm,354.6;bA,J1,J2,15 m,35.04 mm,0.045 mm,154.9;bB,J1,J2,28 m,26.64 mm,0.045 mm,5.75;ret,J2,S,0.1 m,40.9 mm,0.045 mm,0` (354.6 = 6 x 0.75 + 350.1; 154.9 = 4.25 fittings + 150.6 valve; 5.75 = 5 x 0.75 + 2 tees); the 7 catalogue points as the pump; `density "994 kg/m^3"`, `dynamic_viscosity "0.72 mPa*s"`.
3. **`value` is only the mass residual.** Read `data.pumps[0]` (flow 8.77e-4 m3/s = 3.157 m3/h, head 10.26 m) and `data.pipes[]` `flow_m3_s`: A 1.387, B 1.770 m3/h. A needs 1.56, so branch A is short (dT 5.6 K instead of 5; B gets 2.9 K); fix by opening A's valve or throttling B, then rerun with the new K. Pump power: `fluids_pump_system` as above gives 3.157 m3/h, 10.26 m, eta 0.443, shaft 198 W; or `calc_pump_power`.
4. **No tool returns a curve table.** System curve: call `fluids_pipe_network` again with the pump removed, `P,0 m,demand,-4 m^3/h` (negative demand injects flow) and `S` fixed: the head at `P` is the head the system needs at that flow (4 m3/h gives 16.39 m). Repeat at 0, 1, ..., 6 m3/h. Pump points come from the user's catalogue. Chart with `plot_xy` inline `series` (two lines, x in m3/h, y in m), `vlines` at the operating flow and `hlines` at its head, labelled "Q (m3/h)", "H (m)", with `format="png"` and `data.png_path` handed over: see `eng-plot-results`.
5. **Independent hand check**: main-line loss `(f L/D + K) v^2/2g` with the reported v 0.6676 m/s and f 0.02528 = (37.09 + 354.6) x 0.02272 = 8.90 m, equal to `data.pipes` headloss; the straight part alone is `calc_pipe_pressure_drop` (60 m, 40.9 mm, 3.157 m3/h: 8.21 kPa, 0.842 m). Pump head = main loss + branch loss (10.26 = 8.90 + 1.36).

## 5. Other tools (call verified)

- Channel: rectangular 2 m wide, 3 m3/s, slope 0.001, n 0.013: normal depth 0.984 m, Froude 0.49 (subcritical), critical depth 0.612 m. Modes: `analysis`, `uniform`, `profile`.
- Water hammer: DN500, 1000 m, 10 mm steel, 1.5 m/s, closure 1 s: wave speed 1211 m/s, Joukowsky 1.82 MPa (185 m), critical closure 1.65 s. A closure shorter than `critical_closure_time` gets the full Joukowsky rise (hand check `rho a dv`).
- Nozzle: 1 cm2, 6 bar, 20 degC, back pressure 1 bar: choked, 0.1416 kg/s (`gamma`, `gas_constant` are inputs: air by default).
- Drag: 50 mm cylinder, 0.05 m2, 10 m/s air: Cd 1.01, 3.03 N.

## 6. Outside the toolkit

Control-valve characteristics (you must have Kv at the actual opening: turns to Kv needs the vendor's table), NPSH beyond the simple margin, two-phase or non-Newtonian flow, transient surge in a network (surge is one reservoir-pipe-valve), heat transfer of the exchanger (see `eng-thermal-check`), pump selection from a catalogue.

## 7. Report

As in `eng-toolkit`: operating flow, head, efficiency, power; flow per branch against required; inputs (curve source, bore, K sources, fluid state); checks (hand check, `mass_balance_residual`, pump inside its curve); limits (K values typical, Kv scaling quadratic, unreviewed).
