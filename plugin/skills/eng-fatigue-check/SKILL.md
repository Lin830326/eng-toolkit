---
name: eng-fatigue-check
description: Use FIRST for "will it fatigue", infinite life of a shaft or part, remaining life from a strain or load record (rainflow, Miner), notches, S-N or FAT classes, strain-life, crack growth (fatigue_* tools). Says what is outside the toolkit (preloaded bolts).
---

# Fatigue check workflow (eng-toolkit)

Load `eng-toolkit` too. Unreviewed uniaxial. Outside the toolkit (say so, give the standard method labelled "hand method, not tool-checked"): multiaxial fatigue, weld S-N classes, creep-fatigue, crack closure, environment, **preloaded-bolt fatigue**, Kt charts (you supply Kt), design allowables.

## 1. Pin down the problem

Ask if missing, and never invent: Su (Sy), load type, finish, diameter, reliability, factor of safety, notch (Kt, root radius), life target. For a measured record ask only what is missing (E, unit, gauge position, S-N class); a weld class such as IIW FAT is a stress RANGE at 2e6 cycles with slope m = 3 and, unless the person says otherwise, no cut-off: state that as your assumption. Helical gear: ask the axial force direction; Fa adds a couple Fa*d/2; no tool combines the moments of two planes (by hand, labelled). **Amplitude vs range**: `Sa=(max-min)/2`, `Sm=(max+min)/2`. The tools take amplitudes; the rainflow CSV column `range_MPa` is a **range = 2 x amplitude**; Basquin `A`, `b` are for amplitude (`Sa = A N^b`).

## 2. Choose the tool

| Question | Tool and mode |
|---|---|
| Infinite life or safety of a steel part (Marin factors) | `fatigue_sn` `estimate` (Su 400..2000 MPa, d 2.79..254 mm) |
| Basquin fit / known A, b | `fatigue_sn` `fit` / `curve` |
| Mean-stress effect only; Kf only | `correction` (safety factor against your `endurance_limit`); `notch` |
| Load history, damage | `fatigue_rainflow` (count + Miner) |
| Low-cycle, local strain, notch plasticity | `fatigue_strain_life` (`life`, `cyclic`, `notch`, `block`, `estimate`) |
| K, critical crack, crack growth | `fatigue_fracture` (`evaluate`, `critical`, `growth`) |
| Stress-strain, HB to Su, Larson-Miller | `fatigue_material` |
| Combined bending + torsion shaft | `machine_shaft` |

## 3. Build the inputs

- **Counts** are `"1e5 cycle"`, `"count"` or `"dimensionless"`; `repeats` is a plain number.
- `estimate`: `surface_finish` polished|ground|machined|hot_rolled|as_forged; `reliability` only 50, 90, 95, 99, 99.9, 99.99; axial needs no diameter; `temperature_factor` (kd) is yours.
- **Mean stress** `mean_method`: goodman, gerber need Su; soderberg Sy; morrow `fatigue_strength_coefficient`; swt, walker (`walker_gamma`, 0.5 = SWT) nothing. The methods differ by up to 40 %: name the method.
- **Notch**: `kt`, `notch_radius`, `notch_method` peterson (Su 550..1500) or neuber (345..1500). Kf multiplies the amplitude, Kt the mean.
- `fatigue_rainflow`: `history` flat JSON list `[0,300,-100]`, or `history_file` (CSV of numbers, one column or `time,value`; a first line of column names is skipped and reported as `data.header_skipped`); **`sample_scale` `"1 MPa"` is mandatory**. Damage needs `basquin_a` + `basquin_b` (-1..-1e-5) or `sn_points` `"400 MPa,1000;200 MPa,1e6"`. **A straight S-N line with no cut-off: pass only `basquin_a` and `basquin_b`, nothing else.** A class FAT (range at 2e6 cycles, slope m) converts to amplitude: `A = (FAT/2) x 2e6^(1/m)`, `b = -1/m`; FAT 90, m 3 gives A 5670 MPa, b -0.3333 (a 100 MPa range: 1.458e6 cycles). Default is an open history (residue = half cycles); `closed=true` for a repeating cycle. With `endurance_limit`, cycles below it do no damage unless `below_knee` is `elementary` or `haibach`. Force histories only count.
- `fatigue_strain_life`: `elastic_modulus` always; `fatigue_strength`, `fatigue_ductility`, `b`, `c` (c < b < 0), or `estimate_method` + `ultimate_strength` + `reduction_of_area`; `strain_amplitude` is total.
- `fatigue_fracture`: `stress` is the **maximum** remote stress; growth uses `stress_range` + `load_ratio` R (0..0.95). `width` is the full width; a central crack's `crack_size` is the half-length. Geometries are listed in the parameter. `critical` and Forman need `toughness`. `growth_c` has composite units whose exponent equals `growth_m`: Paris `"1e-12 m/cycle/(MPa*m^0.5)^3"`.

## 4. Run, then verify

Read named `values`: `endurance_limit`, `safety_factor`, `life`, `data.Marin_factors`; rainflow `damage`, `repeats_to_failure`; fracture `stress_intensity`, `critical_crack_size`, `cycles_to_failure` (instability) or `cycles_to_limit` (your `final_crack`). Hand checks:

- Se = ka kb kc kd ke Se', Se' = min(0.5 Su, 700 MPa); machined ka = 4.51 Su^-0.265, kb = 1.24 d^-0.107 (mm), kc 1 / .85 / .59 (bending / axial / torsion). Goodman n = 1/(Sa/Se + Sm/Su).
- Rainflow: damage = sum count/N, N = (Sa/A)^(1/b), Sa = range/2. **Before a life from a measured record, compare its maximum stress with Sy and with the rated load**: a peak above any steel's yield is a spike or a wrong scale, report that data problem first and never quote a life from it. Read the tool's history-range and spike warnings.
- K = Y sigma sqrt(pi a). Constant-Y Paris: `N = (af^(1-m/2) - a0^(1-m/2)) / (C (Y dS sqrt(pi))^m (1-m/2))`.
- Strain-life: `eps_a = (sigma_f'/E)(2N)^b + eps_f'(2N)^c`; `reversals_to_failure` is 2N, `life` is N.

## 5. Interpret

- `life_status: at_or_below_endurance` is not a refusal: the finite life is null. The safety factor scales Sa and Sm together; it is not a static yield check.
- **Refusals are results**: explain, never edit inputs to dodge one (Su or diameter out of range, untabulated reliability, mean >= Su, elastic peak above Sy, life under 1000 cycles in `estimate`, strain > 0.1, net-section stress > 0.8 Sy).
- `growth_status: arrested_at_initial_threshold` means no failure life.
- **Preloaded bolt** (outside the toolkit): `fatigue_sn` correction mode scales mean and alternating together, but in a bolt the preload is a FIXED mean and only the alternating part grows, so that mode is wrong (very conservative). Standard approach, labelled "hand method, not tool-checked": preload stress `si` is the mean; the external swing adds `sa = C P_alt / (2 At)` (`P_alt` the full load swing, `C = kb/(kb+km)`), `sm = si + sa`; Goodman `n = Se (Su - si) / (Su sa + Se (sm - si))` with the Se the person states. A bolt Se from a table already includes the thread Kf: do not apply Kf again, and never question a correct statement of that kind without a reason.
- Paris/Forman coefficients are the user's data. Miner is linear.

## 6. Plot and report

- Charts go out as a PNG (`format="png"`, hand over `data.png_path`; skill `eng-plot-results`). S-N: `plot_xy` on `sn_curve.csv`, `x_column="cycles"`, `y_columns="amplitude_MPa"`, `x_scale="log"`, `y_scale="log"`, `hlines` at the endurance limit. Crack growth: `crack_growth.csv`, `y_columns="crack_m"`. Rainflow: `plot_bar` fails above 200 categories, so use inline series from `data.range_histogram` (`edges`, counts as y; `y_scale="log"` when the first bin is noise).
- Report as in `eng-toolkit`: assumptions (Marin factors, reliability, mean method, curve source, Kt, R), checks, limits (unreviewed, uniaxial, empirical, no allowable).
