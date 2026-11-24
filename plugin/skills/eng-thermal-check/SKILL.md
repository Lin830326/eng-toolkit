---
name: eng-thermal-check
description: Use FIRST for "fan or heatsink for a transistor, regulator or chip", junction temperature, warm-up curve of a hot part, insulated pipe or wall loss, fins, radiation, exchanger duty (thermal_* tools). Catalogue Rth trap, Tj limit, pad vs paste, outside-toolkit list.
---

# Thermal check workflow (eng-toolkit)

Load `eng-toolkit` too (units, errors, report format). Unreviewed textbook estimates: steady or lumped, constant properties, scatter 10-25 %.

## 1. Pin down the problem (ask BEFORE any verdict)

Heat (W), constant or not; ambient (air at the part, not room air); every resistance in the path (Rjc, interface pad or paste, heatsink); **the junction limit and who sets it**: a fan decision needs it, so ask "what Tj do you design to?" first (the datasheet Tj max is not the user's reliability rule); orientation, enclosure, fan data; heatsink geometry and mass; for a catalogue Rth, **the temperature rise it is quoted at** (footnote, often 50 to 80 K) and the length. Do not write "no fan needed" before the limit, Rjc, the interface and the catalogue conditions are known.

## 2. Choose the tool

| Question | Tool |
|---|---|
| Layers in series (wall, pipe plus insulation), heat loss | `thermal_conduction` (`geometry` plane / cylinder / sphere) |
| h of a surface from Re/Ra (pipe flow, plate, cylinder, sphere, natural plate) | `thermal_convection` (`correlation`) |
| Straight or pin fin, array of fins with a given h | `thermal_fin` |
| Radiation between plates, cylinders, spheres, body in enclosure | `thermal_radiation` |
| Exchanger duty, outlet temperatures, UA, area, NTU | `thermal_exchanger` |
| Lumped or one-term transient, time to a temperature | `thermal_transient` |
| Warm-up CURVE of a lumped body with a heat source | `thermal_transient` if its description lists a heat input and a time list; else `control_response`, then `plot_xy` as PNG |
| Pump head, flow, pressure drop of the water side | skill `eng-flow-network` |

## 3. Build the inputs

- Fluid properties are inputs (no tables): air at 60 degC `conductivity 0.0285 W/(m*K)`, `density 1.05 kg/m^3`, `dynamic_viscosity 1.95e-5 Pa*s`, `specific_heat 1007 J/(kg*K)`; water at 35 degC `0.62`, `994`, `0.72e-3`, `4178`. Use the film temperature.
- Absolute temperatures `"80 degC"`; differences in K. `thermal_radiation` emissivities are plain numbers; a fin radiating mostly to its neighbours has far less than its full area.
- `thermal_conduction` layers: `'3 mm @ 50 W/(m*K); 30 mm @ 0.04 W/(m*K)'`; cylinder needs `inner_radius`, `length`; `h_outer` gives film and critical radius. `thermal_exchanger`: both mass flows and cp (flow = P / (cp dT)); sizing with `duty`, rating with `ua`.

## 4. Recipe: power part on a heatsink (example inputs, not datasheet values: 7 W, 35 degC air, Rjc 2.2 K/W, pad 1.0 K/W, catalogue 4.0 K/W at a 70 K rise, 75 mm fins, 85 g, Tj rule 88 degC)

1. Series chain `Tj = Ta + P (Rjc + Rcs + Rsa)`. Rjc and the pad come from the datasheet or the user, never typical values.
2. **Catalogue Rsa holds only at its quoted rise.** The real rise is `P Rsa`, lower, and natural-convection Rth grows as the rise falls. Scale with the tool: `thermal_convection correlation natural_vertical_plate` (`length "75 mm"` = fin length, air above) at the surface temperature of the quoted rise (105 degC) and at the real one (68.4 degC), `ambient_temperature "35 degC"`: h = 7.265 and 6.044 W/m2K, ratio 0.832, so `Rsa = 4.0 / 0.832 = 4.8 K/W` (iterate once: rise 7 x 4.8 = 33.7 K, converged). Use only the RATIO.
3. Naive `35 + 7 (2.2 + 1.0 + 4.0) = 85.4 degC` passes an 88 degC rule; corrected `35 + 7 (2.2 + 1.0 + 4.8) = 91.1 degC` fails: **a fan (or a bigger sink) is needed**.
4. **Never conclude "no fan" from the isolated-plate h**: the tool has no channel correlation for fins in a row (fins closer than about 10 mm choke each other; here 6.6 mm). If `thermal_convection` lists a natural channel model, use it; else say "outside the toolkit, closely spaced fins" and derate the catalogue value as in step 2. A fan decision needs the vendor Rth-versus-airflow curve: ask for it; a duct correlation is not a fan size.
5. Cross-check: `thermal_fin` (`fin_type "rectangular"`, `length "20 mm"`, `thickness "1.5 mm"`, `width "75 mm"`, `tip "convective"`, `fin_count 7`, `base_unfinned_area "0.002963 m^2"`, h 6.0, base 68.4 degC) gives `array_heat_rate` 5.0 W (6.7 K/W, convection only). Same order as 4.8 K/W; radiation of an anodised sink fills the gap.
6. **Insulating pad or paste.** A silicone pad is usually the electrical insulation: removing it is not a thermal option unless the sink is isolated. Offer a thinner or higher-conductivity isolator and ask the isolation voltage; paste alone is for a non-isolated case.

## 5. Warm-up curve of the junction

The die and pad hold almost no heat, so the junction follows the sink at a constant offset and **jumps by `P (Rjc + Rcs)` at once**:

`Tj(t) = Tsink(t) + P (Rjc + Rcs)`, `Tsink(t) = Ta + P Rsa (1 - exp(-t/tau))`, `tau = Rsa m cp` (4.8 x 0.085 x 900 = 368 s for 85 g aluminium).

- With `control_response`, rise above ambient in K: sink `numerator "[33.67]" denominator "[368, 1]"`; junction `numerator "[8243, 56.07]" denominator "[368, 1]"` (a = tau P (Rjc + Rcs), b = P (Rjc + Rcs + Rsa)), `t_final "2400 s" samples 241`; `response.csv` has `time_s,output` (ignore "dimensionless").
- **Check before plotting:** Tj at t = 0+ equals `Ta + P (Rjc + Rcs)` (here 22.4 K above ambient, first CSV row); the final value equals the steady Tj of step 3. A curve that starts at ambient is wrong.
- Plot both curves (`plot_xy`, `format="png"`, `data.png_path`) as rise above 35 degC (K) with `hlines` at rule minus 35, or as degC from inline series (add 35, my arithmetic).
- Lumped check: `thermal_transient` (`model lumped`, `initial_temperature "35 degC"`, `ambient_temperature "68.67 degC"` = final sink temperature, `volume "3.148e-5 m^3"`, `surface_area "0.05 m^2"`, `h "4.158 W/(m^2*K)"`, `density 2700`, `specific_heat 900`, `conductivity 200`, `time "368 s"`) returns 56.29 degC = 63.2 % of the rise.

## 6. Other jobs

- Insulated pipe (25 mm inner radius, 3 mm steel, 30 mm insulation, 80 to 20 degC, `h_outer "10 W/(m^2*K)"`): 18.9 W per m; read the critical-radius warning.
- Exchanger sizing, counterflow, hot 70 degC 0.8 kg/s, cold 20 degC 1.1 kg/s, both cp 4180, `duty "40 kW"`: UA 1009 W/K, LMTD 39.65 K, outlets 58.0 and 28.7 degC. Duties the arrangement cannot reach are refused (a result).

## 7. Verify (label it "independent hand check")

Series sum of resistances; `Q = m cp dT`; `tau = rho V cp / (h A)`; `energy_balance` agrees to 1e-9. A Tj within 10 degC of the limit needs a margin statement.

## 8. Outside the toolkit

Fin arrays with narrow gaps (unless a channel model exists), enclosure natural convection, fans and airflow, PCB spreading, thermal vias, temperature-dependent heat (Rds(on) rises with Tj), phase change, 2D/3D conduction. Say so.

## 9. Report

As in `eng-toolkit`: Tj, Rsa used, margin to the user's limit, fan yes/no; inputs (limit source, quoted rise, pad); checks (hand check, Tj at t = 0+); limits.
