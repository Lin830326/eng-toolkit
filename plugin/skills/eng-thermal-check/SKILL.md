---
name: eng-thermal-check
description: Use for a heat-transfer check with the eng-toolkit thermal_* tools - transistor or chip on a heatsink (junction temperature, fan or not), insulated walls and pipes, fins, radiation, exchanger duty and UA, warm-up time. Covers datasheet traps and coverage limits. Load eng-toolkit first.
---

# Thermal check workflow (eng-toolkit)

Load `eng-toolkit` first (units, errors, report format). Results are unreviewed textbook estimates: steady or lumped, constant properties, correlation scatter 10-25 %.

## 1. Pin down the problem (ask if it changes the answer)

Heat (W) and whether it is constant; ambient (air at the part, not room air); every resistance in the path (junction-case, pad, heatsink); **the limit and who sets it** (datasheet Tj max is NOT the user's reliability rule: ask "what Tj do you design to?"); orientation, enclosure, fan data; heatsink geometry and mass; for a catalogue Rth, **the temperature rise it is quoted at** (footnote, often 75 K) and the length.

## 2. Choose the tool

| Question | Tool |
|---|---|
| Layers in series (wall, pipe plus insulation), interface temperatures, heat loss | `thermal_conduction` (`geometry` plane / cylinder / sphere) |
| h of a surface from Re/Ra (pipe flow, plate, cylinder, sphere, natural plate) | `thermal_convection` |
| Straight or pin fin, array of fins with a given h | `thermal_fin` |
| Radiation between plates, cylinders, spheres, body in enclosure | `thermal_radiation` |
| Exchanger duty, outlet temperatures, UA, area, NTU | `thermal_exchanger` |
| Lumped or one-term transient, time to a temperature | `thermal_transient` |
| Warm-up CURVE of a lumped body with a heat source | `control_response` (first-order step) then `plot_xy` as PNG |
| Pump head, flow, pressure drop of the water side | `eng-flow-network` skill |

## 3. Build the inputs

- Fluid properties are inputs (no tables): air at 60 degC `k 0.0285 W/(m*K)`, `rho 1.05 kg/m^3`, `mu 1.95e-5 Pa*s`, `cp 1007 J/(kg*K)`; water at 35 degC `0.62`, `994`, `0.72e-3`, `4178`. Use the film temperature.
- Absolute temperatures `"80 degC"`, `"600 K"`; differences in K. `thermal_radiation` emissivities are plain numbers; a fin radiating mostly to its neighbours has far less than its full area.
- `thermal_conduction` layers: `'3 mm @ 50 W/(m*K); 30 mm @ 0.04 W/(m*K)'`; cylinder needs `inner_radius`, `length`; `h_outer` gives film and critical radius.
- `thermal_exchanger`: both mass flows and cp are required (flow = P / (cp dT)); sizing with `duty`, rating with `ua`.
- Returned temperatures are degC. `thermal_transient` lumped: `volume`, `surface_area`, `h` and a Biot check (< 0.1).

## 4. Recipe: power part on a heatsink (verified on 8 W TO-220, 45 degC, catalogue 3.0 K/W at 75 K)

1. Series chain `Tj = Ta + P (Rjc + Rcs + Rsa)`. Take Rjc and the pad from the datasheet or the user (1.15 and 1.4 K/W), not typical values.
2. **Catalogue Rsa is valid only at its quoted rise.** Real rise is `P Rsa`, much lower, and natural-convection Rth grows as the rise falls. Scale with the tool: call `thermal_convection` `natural_vertical_plate` (`length "100 mm"` = fin length, air above) with `surface_temperature "120 degC"` (75 K) then `"75 degC"` (30 K), `ambient_temperature "45 degC"`: h = 6.91 and 5.48 W/m2K, ratio 0.79. `Rsa(30 K) = 3.0 / 0.79 = 3.8 K/W`; iterate once (rise = 8 x 3.8 = 30 K, converged). Use only the RATIO.
3. `Tj = 45 + 8 (1.15 + 1.4 + 3.8) = 95.8 degC` against the user's 90 degC rule: **fan needed** (truth by channel correlation 102 degC). Say the scaling is an engineering estimate.
4. **Never conclude "no fan" from the isolated-plate h** (5.7 W/m2K here): the tool has no channel correlation, and fins with gaps under about 10 mm (here 5.25 mm) choke each other, real h about 3. Say "outside the toolkit, closely spaced fins" and derate the catalogue value as in step 2. Fan sizing needs the vendor Rth-vs-airflow curve; ask for it.
5. `thermal_fin` (`fin_type "rectangular"`, `length "24 mm"`, `thickness "2 mm"`, `width "100 mm"`, `tip "convective"`, `fin_count 9`, `base_unfinned_area "0.0042 m^2"`, h 3.1) gives `array_heat_rate` 4.64 W at a 30 K base rise, convection only: a cross-check of the order of magnitude, not a replacement for the catalogue.
6. **Warm-up curve** (`thermal_transient` has no heat input and returns one time per call): `tau = Rsa x m cp = 3.8 x 0.2 x 900 = 682 s`, final rise `P Rsa = 30.3 K`. `control_response numerator "30.3" denominator "[682, 1]" t_final "3600 s" samples 121` returns `response.csv` (`time_s,output`, output = K above ambient; ignore its "dimensionless" unit); `plot_xy` it (`format="png"`, hand over `data.png_path`) with `hlines` at the final rise and label the axis "rise above 45 degC (K)". Check: `thermal_transient` lumped (`initial_temperature "45 degC"`, `ambient_temperature "75.3 degC"`, `volume "7.407e-5 m^3"`, `surface_area "0.1 m^2"`, `h "2.64 W/(m^2*K)"`, `density 2700`, `specific_heat 900`, `conductivity 200`, `time "682 s"`) returns 64.16 degC = 63.2 % of the rise.

## 5. Other jobs

- Insulated pipe 25 mm inner radius, 3 mm steel, 30 mm insulation, 80 to 20 degC, `h_outer "10 W/(m^2*K)"`: 18.9 W per m (call verified); read the critical-radius warning.
- Exchanger sizing, counterflow, hot 40 degC 0.717 kg/s, cold 25 degC 1.2 kg/s, `duty "15 kW"`: UA 1367 W/K, LMTD 10.97 K; the tool refuses duties beyond what the arrangement can reach (a result).
- `thermal_convection` forced flow: give `velocity`; it warns outside the correlation range.

## 6. Verify (label it "independent hand check")

Series sum of resistances; `Q = m cp dT` for exchangers; `tau = rho V cp / (h A)`; energy balance fields in the result (`energy_balance`) must agree to 1e-9. A Tj that changes the decision (within 10 degC of the limit) needs a margin statement.

## 7. Outside the toolkit

Fin arrays with narrow gaps, chimney and enclosure natural convection, fans and airflow, PCB spreading, thermal vias, temperature-dependent heat sources (MOSFET Rds(on) rises with Tj), phase change, 2D/3D conduction. Say so; do not fake it.

## 8. Report

As in `eng-toolkit`: result (Tj, Rsa used, margin to the user's limit, fan yes/no), inputs (limit source, rise correction), checks (hand check, energy balance), limits (above list, unreviewed).
