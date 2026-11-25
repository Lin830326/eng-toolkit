---
name: eng-thermal-check
description: Use FIRST for "fan or heatsink for a transistor, regulator or chip", junction temperature, warm-up curve of a hot part, insulated pipe or wall loss, fins, radiation, exchanger duty (thermal_* tools). Catalogue Rth trap, Tj limit, pad vs paste, outside-toolkit list.
---

# Thermal check workflow (eng-toolkit)

Load `eng-toolkit` too. Unreviewed estimates: steady or lumped, constant properties, scatter 10-25 %.

## 1. Pin down the problem (ask BEFORE any verdict)

Heat (W); ambient (air at the part, not room air); every resistance in the path (Rjc, interface pad or paste, heatsink); **the junction limit and who sets it**: a fan decision needs it, so ask "what Tj do you design to?" first (the datasheet Tj max is not the user's reliability rule); orientation, enclosure, fan data; heatsink geometry (fin gap, height, flow length) and mass; for a catalogue Rth, **the temperature rise it is quoted at** (footnote, often 50 to 80 K). Do not write "no fan needed" before the limit, Rjc, the interface and the catalogue conditions are known.

## 2. Choose the tool

| Question | Tool |
|---|---|
| Layers in series (wall, pipe plus insulation), heat loss | `thermal_conduction` (`geometry` plane / cylinder / sphere) |
| h of a surface from Re/Ra (pipe, plate, cylinder, sphere, natural plate) | `thermal_convection` (`correlation`) |
| h of a finned heatsink, natural convection | `thermal_convection` `natural_channel` (`fin_gap`, `fin_height`) |
| Straight or pin fin, array of fins with a given h | `thermal_fin` |
| Radiation between plates, cylinders, spheres, body in enclosure | `thermal_radiation` |
| Exchanger duty, outlet temperatures, UA, area, NTU | `thermal_exchanger` |
| Lumped or one-term transient, time to a temperature | `thermal_transient` |
| Warm-up CURVE of a lumped body with a heat source | `thermal_transient` `model lumped`, `heat_input`, `times` (or `t_end` + `points`): one call gives `temperature_vs_time.csv` |
| Pump head, flow, pressure drop of the water side | skill `eng-flow-network` |

## 3. Build the inputs

- Fluid properties are inputs (no tables): air at 60 degC `conductivity 0.0285 W/(m*K)`, `density 1.05 kg/m^3`, `dynamic_viscosity 1.95e-5 Pa*s`, `specific_heat 1007 J/(kg*K)`; water at 35 degC 0.62, 994, 0.72e-3, 4178. Use the film temperature.
- Absolute temperatures `"80 degC"`; differences in K. `thermal_radiation` emissivities are plain numbers; a fin facing its neighbours radiates far less than its full area.
- `thermal_conduction` layers: `'3 mm @ 50 W/(m*K); 30 mm @ 0.04 W/(m*K)'`; cylinder needs `inner_radius`, `length`; `h_outer` gives film and critical radius. `thermal_exchanger`: both mass flows and cp; `duty` sizes, `ua` rates.
- `natural_channel`: `length` is the distance the air flows along (upright sink: base height, NOT fin height), `fin_gap` the clear gap; it returns `h` per wetted surface, `optimum_gap`, `h_at_optimum_gap`. A plate correlation given `fin_gap` warns that it overstates h.

## 4. Recipe: power part on a heatsink (example inputs, not datasheet values: 7 W, 35 degC air, Rjc 2.2 K/W, pad 1.0 K/W, catalogue 4.0 K/W at a 70 K rise, fins 75 mm long, 20 mm high, 6.6 mm gap, 85 g, Tj rule 88 degC)

1. Series chain `Tj = Ta + P (Rjc + Rcs + Rsa)`; Rjc and the pad come from the datasheet or the user.
2. **Catalogue Rsa holds only at its quoted rise.** The real rise `P Rsa` is lower, and natural-convection Rth grows as the rise falls. Scale with the channel model, using only the RATIO: `Rsa = 4.0 x h(quoted surface temperature) / h(real surface temperature)`. `thermal_convection correlation natural_channel length "75 mm" fin_gap "6.6 mm" fin_height "20 mm" ambient_temperature "35 degC"` gives h = 7.383 W/m2K at the quoted 105 degC and 5.695 at 71.4 degC. Iterate (surface = 35 + 7 Rsa): 68.6, 72.7, 70.7, 71.6 ... converges at 71.4 degC, Rsa = 5.19 K/W (my arithmetic).
3. Naive `35 + 7 (2.2 + 1.0 + 4.0) = 85.4 degC` passes an 88 degC rule; corrected `35 + 7 (2.2 + 1.0 + 5.19) = 93.7 degC` fails: **a fan (or a bigger sink) is needed**. The isolated-plate model (`natural_vertical_plate`: 7.211 and 6.005 W/m2K, Rsa 4.80, Tj 91.0 degC) overstates h for a 6.6 mm gap, as its warning says.
4. **Never conclude "no fan" from the isolated-plate h.** A fan decision needs the vendor Rth-versus-airflow curve: ask for it; a duct correlation is not a fan size.
5. Cross-check: `thermal_fin` with 7 rectangular fins (20 x 1.5 x 75 mm, `tip "convective"`, `base_unfinned_area "0.002963 m^2"`, h 5.69, base 71.4 degC) gives `array_heat_rate` 5.19 W = 7.0 K/W for convection only; the catalogue figure also holds radiation. Report both.
6. **Insulating pad or paste.** A silicone pad is usually the electrical insulation: removing it is not an option unless the sink is isolated. Offer a thinner or better isolator and ask the isolation voltage; paste alone is for a non-isolated case.

## 5. Warm-up curve of the junction

`thermal_transient model lumped heat_input "7 W"`, initial and ambient `"35 degC"`, aluminium (`conductivity "200 W/(m*K)"`, `density "2700 kg/m^3"`, `specific_heat "900 J/(kg*K)"`), `volume "3.148e-5 m^3"`, `surface_area "0.05 m^2"`, `h "3.854 W/(m^2*K)"` (= 1/(Rsa A), my arithmetic) and `times "0 s; 100 s; 200 s; 400 s; 800 s; 1200 s; 2400 s"` returns the SINK: `final_temperature` 71.33 degC, `time_constant` 397 s (= Rsa m cp) and `temperature_vs_time.csv` (`time_s,temperature_degC`: 35.0, 43.1, 49.4, 58.1, 66.5, 69.6, 71.2). `t_end` with `points` gives a dense table.

There is **no two-lump (junction plus sink) model**. The die and pad hold almost no heat: the junction follows the sink at a constant offset and **jumps by `P (Rjc + Rcs)` at once**: `Tj(t) = Tsink(t) + 7 x 3.2 = Tsink(t) + 22.4 K` (add it by hand, "my arithmetic").

- **Check before plotting:** Tj at t = 0+ equals `Ta + P (Rjc + Rcs)` = 57.4 degC; the final Tj equals step 3 (71.33 + 22.4 = 93.7 degC). A curve that starts at ambient is wrong.
- Plot with `plot_xy` and inline series (sink from the CSV, junction = sink + 22.4), `hlines="88,Tj rule"`, labels with units, the case in the title; give `data.png_path` (skill `eng-plot-results`).

## 6. Other jobs

- Insulated pipe (25 mm inner radius, 3 mm steel, 30 mm insulation, 80 to 20 degC, `h_outer "10 W/(m^2*K)"`): 18.9 W per m; read the critical-radius warning.
- Exchanger sizing (counterflow, 70 degC 0.8 kg/s against 20 degC 1.1 kg/s, cp 4180, `duty "40 kW"`): UA 1009 W/K, outlets 58.0 and 28.7 degC; an unreachable duty is refused (a result).

## 7. Verify (label "independent hand check")

Series sum of resistances; `Q = m cp dT`; `tau = rho V cp / (h A)`. State the margin when Tj is within 10 degC of the limit.

## 8. Outside the toolkit

Fan curves and airflow, enclosure natural convection, PCB spreading, thermal vias, a two-lump junction model, temperature-dependent heat (Rds(on) rises with Tj), phase change, 2D/3D conduction.

## 9. Report

As in `eng-toolkit`: Tj, Rsa used, margin to the user's limit, fan yes/no; inputs (limit source, quoted rise, pad); checks (Tj at t = 0+); limits.
