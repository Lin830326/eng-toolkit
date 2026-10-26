---
name: eng-fatigue-check
description: Use for a fatigue or fracture question with the eng-toolkit fatigue_* tools, infinite-life check (Marin factors, mean stress), S-N life, rainflow counting with Miner damage, notches, strain-life, crack growth. Says what is outside the toolkit (preloaded bolts). Load eng-toolkit first.
---

# Fatigue check workflow (eng-toolkit)

Load `eng-toolkit` first. Unreviewed uniaxial estimates. Outside the toolkit (say so, give the standard method labelled "hand method, not tool-checked"): multiaxial fatigue, weld S-N classes, creep-fatigue, crack closure, environment, **preloaded-bolt fatigue**, Kt charts (you supply Kt), design allowables.

## 1. Pin down the problem

Ask if missing, and never invent: Su (Sy), load type, finish, diameter, reliability, factor of safety, notch (Kt, root radius), life target. Helical gear: ask the axial force direction; Fa adds a couple Fa*d/2, and no tool combines the moments of two planes (by hand, labelled). **Amplitude vs range**: `Sa=(max-min)/2`, `Sm=(max+min)/2`. The tools take amplitudes; the rainflow CSV column `range_MPa` is a **range = 2 x amplitude**; Basquin `A`, `b` are for amplitude (`Sa = A N^b`).

## 2. Choose the tool

| Question | Tool and mode |
|---|---|
| Infinite life or safety of a steel part (Marin factors) | `fatigue_sn` `estimate` (Su 400..2000 MPa, d 2.79..254 mm) |
| Basquin fit / known A, b | `fatigue_sn` `fit` / `curve` |
| Mean-stress effect only; Kf only | `correction` (safety factor against the `endurance_limit` you give; drop `cycles`/`load_type`, or use `estimate`); `notch` |
| Load history, damage | `fatigue_rainflow` (count + Miner) |
| Low-cycle, local strain, notch plasticity | `fatigue_strain_life` (`life`, `cyclic`, `notch`, `block`, `estimate`) |
| K, critical crack, crack growth | `fatigue_fracture` (`evaluate`, `critical`, `growth`) |
| Stress-strain, HB to Su, Larson-Miller | `fatigue_material` |
| Combined bending + torsion shaft | `machine_shaft` |

## 3. Build the inputs

- **Counts** are `"1e5 cycle"`, `"cycles"`, `"count"` or `"dimensionless"`; angles and `kilocycle` are refused. `repeats` is a plain number.
- `estimate`: `surface_finish` polished|ground|machined|hot_rolled|as_forged; `reliability` only 50, 90, 95, 99, 99.9, 99.99 (ke 1 to .702); axial needs no diameter; `temperature_factor` (kd) is yours. `yield_strength` adds a screen `Kt(Sa+|Sm|) <= Sy`.
- **Mean stress** `mean_method`: goodman, gerber need Su; soderberg Sy; morrow `fatigue_strength_coefficient`; swt, walker (`walker_gamma`, 0.5 = SWT) nothing. Equivalent amplitude for Sa=Sm=100, Su 600, Sy 450, sigma_f' 900, gamma .6: none 100, gerber 102.9, morrow 112.5, goodman 120, soderberg 128.6, walker 132.0, swt 141.4. Name the method.
- **Notch**: `kt`, `notch_radius`, `notch_method` peterson (Su 550..1500) or neuber (345..1500); `notch_material_length` is required for Peterson in torsion. Kf multiplies the amplitude, Kt the mean.
- `fatigue_rainflow`: `history` flat JSON list `[0,300,-100]`, or `history_file` (CSV of numbers, one column or `time,value`; an optional first line of column names is skipped and reported as `data.header_skipped`; any other text is refused with its line number); **`sample_scale` `"1 MPa"` is mandatory**. Damage needs `basquin_a` + `basquin_b` (-1..-1e-5) or `sn_points` `"400 MPa,1000;200 MPa,1e6"`. Default is an open history (residue = half cycles); `closed=true` for a repeating cycle. With `endurance_limit`, cycles below it do no damage unless `below_knee` is `elementary` or `haibach`. Force histories only count.
- `fatigue_strain_life`: `elastic_modulus` always; `fatigue_strength`, `fatigue_ductility`, `b`, `c` (c < b < 0), or `estimate_method` + `ultimate_strength` + `reduction_of_area`. `strain_amplitude` is total, <= 0.1.
- `fatigue_fracture`: `stress` is the **maximum** remote stress; growth uses `stress_range` + `load_ratio` R (0..0.95). `width` is the full width; a central crack's `crack_size` is the half-length. Geometries: infinite, center_secant, center_tada, edge_small, edge, double_edge, surface, senb, ct. `critical` and Forman need `toughness`. `growth_c` has composite units whose exponent equals `growth_m`: Paris `"1e-12 m/cycle/(MPa*m^0.5)^3"`, Forman `...^2`.

## 4. Run, then verify

Read named `values` entries: `endurance_limit`, `equivalent_amplitude`, `safety_factor`, `life`, `stress_at_life`, `data.Marin_factors`; rainflow `full_cycles`, `half_cycles`, `damage_per_block`, `damage`, `repeats_to_failure`; fracture `stress_intensity`, `critical_crack_size`, `cycles_to_failure` (instability) or `cycles_to_limit` (your `final_crack`). Hand checks:

- Se = ka kb kc kd ke Se', Se' = min(0.5 Su, 700 MPa); machined ka = 4.51 Su^-0.265 (MPa), kb = 1.24 d^-0.107 (mm, to 51), kc 1 / .85 / .59 (bending / axial / torsion). Goodman n = 1/(Sa/Se + Sm/Su).
- Rainflow: damage = sum count/N, N = (Sa/A)^(1/b), Sa = range/2.
- K = Y sigma sqrt(pi a): 100 MPa, 10 mm, infinite plate gives 17.72 MPa sqrt(m). Constant-Y Paris: `N = (af^(1-m/2) - a0^(1-m/2)) / (C (Y dS sqrt(pi))^m (1-m/2))`.
- Strain-life: `eps_a = (sigma_f'/E)(2N)^b + eps_f'(2N)^c`; `reversals_to_failure` is 2N, `life` is N.

## 5. Interpret

- `life_status: at_or_below_endurance` is not a refusal: stress is under the plateau, so the finite life is null. The safety factor scales Sa and Sm together; it is not a static yield check.
- **Refusals are results**: explain, never edit inputs to dodge one (Su or diameter out of range, untabulated reliability, mean >= Su, elastic peak above Sy, life under 1000 cycles in `estimate`, strain > 0.1, net-section stress > 0.8 Sy, a/W outside the geometry limits).
- Strain-life `notch` does not warn about general yielding. `growth_status: arrested_at_initial_threshold` means no failure life.
- **Preloaded bolt** (outside the toolkit; `fatigue_sn` correction scales mean and amplitude together, which is wrong here). Hand method: the preload stress `si` is a constant mean; the external load adds only `sa = C (Pmax-Pmin)/(2 At)`, `C = kb/(kb+km)` (joint stiffness), `sm = si + sa`; Goodman with preload `n = Se (Su - si)/(Su sa + Se (sm - si))`. A bolt Se from a table already includes the thread Kf: do not apply Kf again. Label all of it "hand method, not tool-checked".
- Paris/Forman coefficients are the user's calibrated data. Miner is linear, no sequence effects.

## 6. Plot and report

- Charts go out as a PNG (`format="png"`, hand over `data.png_path`; skill `eng-plot-results`). S-N: `plot_xy` on `sn_curve.csv`, `x_column="cycles"`, `y_columns="amplitude_MPa"`, `x_scale="log"`, `y_scale="log"`, `hlines="180,endurance limit"`, labelled axes. Crack growth: `crack_growth.csv`, `x_column="cycles"`, `y_columns="crack_m"`. Rainflow: `plot_bar` fails above 200 categories on a real record, so use inline series from `data.range_histogram` (labels from `edges`, counts as y; `y_scale="log"` or `range_bins` 10-20 when the first bin is sensor noise).
- Report as in `eng-toolkit`: assumptions (Marin factors, reliability, mean method, curve source, Kt, R), checks, limits (unreviewed, uniaxial, empirical, no allowable).
