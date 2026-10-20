# thermal domain: design notes

Scope: a new `thermal` domain with six pure-formula heat-transfer tools. Branch `feat/thermal-domain`, cut from main at `d596a45`.
Files touched: `eng_toolkit/domains/thermal/`, `tests/test_thermal_*.py` + `tests/thermal_testing.py`, this folder, one README
section. Nothing in `pyproject.toml`, `uv.lock`, `tests/test_gateway.py`, the shared layer or another domain.

Models after `calc` and `mech`: textbook closed forms, every quantity a string with a unit through `units.parse_quantity`, the
unified return contract, `review_status: "unreviewed"`, no LLM call, nothing the caller sends is executed, no new dependency
(pure `math`; `pint` only for units; `DOMAIN.engines = ("pint",)`, no `engines.py`, no external program).

## Ground rules followed

* Formulas are named by their author in the tool description or docstring (Dittus-Boelter, Gnielinski/Petukhov, Churchill-Bernstein,
  Whitaker, Churchill-Chu, Bowman, Kays & London, Mason series, Carslaw & Jaeger). No text, figure or table of a book is copied; the
  only "data" are the textbook constants in the formulas themselves.
* **No property database.** Conductivity, density, specific heat, viscosity (or Prandtl number), expansion coefficient are always
  inputs, evaluated by the caller at the temperature the correlation asks for. The tools do not know water, air or any metal.
* Error families are the existing ones: `units.bare_number | dimension_mismatch | parse_error` from `parse_quantity`; `input.invalid`
  (zero, negative, unknown choice, an impossible duty, a temperature at or below absolute zero, a difference unit where an absolute
  temperature is required, an emissivity outside (0, 1], mismatched list lengths, an over/under-specified exclusive group);
  `input.missing`; `input.out_of_range` (parses and is positive, but absurd, see the table). A failed call carries no warnings.
* Warnings (success only) say when a correlation is used outside its stated range; the number is still returned, except where the
  formula is undefined there (Gnielinski at Re <= 1000, the mixed flat-plate formula where it goes negative), which is refused.
* No NaN/infinity can reach a result: every output quantity passes `_common.out` / `plain` and every number in `data` passes
  `check_data`; an overflow raises `input.out_of_range` naming the result. Tested through `fastmcp.Client` with strict JSON parsing
  (`parse_constant` raising on `NaN`/`Infinity`).

## Temperatures (the one thing that is easy to get wrong)

* Every temperature input is an **absolute** temperature: `80 degC`, `353.15 K`, `176 degF`. It is converted to kelvin by pint
  (offset conversion), so `20 degC` is 293.15 K and `68 degF` is 293.15 K. Tests run the same case in degC, K and degF.
* `delta_degC`, `delta_degF` (any unit starting `delta_`) in an absolute-temperature parameter is `input.invalid`: `20 delta_degC`
  would otherwise be read as 20 K. At or below 0 K is `input.invalid`; above 10 000 K or below 1 K is `input.out_of_range`.
* **No tool takes a temperature difference as input.** Wherever a difference matters (fin `theta_b`, natural-convection `dT`,
  exchanger `dT_max`) the tool takes two absolute temperatures and subtracts them. Differences are therefore always in kelvin.
* A temperature that appears *inside a rate* (`W/(m*degC)`, `J/(kg*degF)`, `1/degC`, `m^2*degC/W`) is a per-degree unit, never an
  offset temperature. pint's default (`autoconvert_offset_to_baseunit`) silently turns `0.04 W/m/degC` into `0.04/274.15 W/m/K`
  (checked: it returns 1.459e-4 W/(m K)), a 270-fold error. `_common.rate_quantity` detects this with a second, strict registry
  (which raises `OffsetUnitCalculusError`), rewrites `degC`/`degF` to `delta_degC`/`delta_degF`, and adds a **warning** saying how
  the unit was read. Tested: `0.04 W/(m*degC)` equals `0.04 W/(m*K)`; `0.04 W/(m*degF)` equals `0.072 W/(m*K)`.
* A bare `K` cannot be told from a difference: `10 K` is accepted as an absolute 10 K (kelvin differences and absolute kelvin share
  the unit). For outputs that are differences (LMTD, approach) the unit is `kelvin` (metric) or `delta_degree_Fahrenheit`.
* Outputs: `metric` gives degC / K / W / m / W/(m2 K); `imperial` gives degF / delta_degF / Btu/h / ft / Btu/(h ft2 degF).

## Input ranges (hard `input.out_of_range`, same convention as calc/mech)

| quantity | range | | quantity | range |
|---|---|---|---|---|
| layer thickness, radius | 1 um .. 1 km | | thermal conductivity | 1e-3 .. 1e4 W/(m K) (fluids 1e-3 .. 1e3) |
| plane area | 1e-10 .. 1e9 m2 | | film coefficient h, U | 1e-2 .. 1e7 W/(m2 K) |
| cylinder length | 1 um .. 1000 km | | areal contact resistance (>= 0) | 0 .. 10 m2 K/W |
| absolute temperature | 1 .. 10 000 K | | mass flow | 1e-6 .. 1e6 kg/s |
| specific heat | 10 .. 1e5 J/(kg K) | | UA | 1e-6 .. 1e12 W/K; duty 1e-3 W .. 1e13 W |
| fin length | 0.1 mm .. 10 m; thickness / diameter 10 um .. 0.1 m; width 0.1 mm .. 10 m | | time | 0 .. 1e11 s |
| density | 1e-4 (1e-3 for solids) .. 3e4 kg/m3 | | viscosity | 1e-7 .. 1e4 Pa s |
| velocity | 1e-6 .. 1e4 m/s | | Prandtl number | 1e-4 .. 1e5 |
| expansion coefficient | 1e-7 .. 1 1/K | | radiation shields | 0 .. 50 |

Dimensionless parameters (emissivity, `position`, Prandtl as a plain number) outside their definition are `input.invalid`
(emissivity 80 for 0.8). Lists are limited to 30 layers.

## The six tools

### `thermal_conduction` (steady 1-D, resistances in series)

Plane `R = t/(kA)`, cylinder `R = ln(r_o/r_i)/(2 pi k L)` (evaluated with `log1p(t/r_i)`), sphere `R = (1/r_i - 1/r_o)/(4 pi k)`;
film `1/(hA)`, areal contact resistance `R''c/A` at the interface (cylinder/sphere: at that interface radius). `q = (T_in - T_out)/R_total`,
`UA = 1/R_total`, `U` per unit area (plane), `U_outer`, `U_inner` (cylinder/sphere), temperature at the inner surface, at each interface
(two values, "inner side"/"outer side", if there is a contact resistance) and at the outer surface; heat flows from the inner to the
outer side when positive. `critical_radius = k/h_outer` (cylinder), `2k/h_outer` (sphere), using the outermost layer's k, with a warning
when the outer radius is below it ("more insulation increases the loss").

Decisions: layers are a text list `'200 mm @ 0.72 W/(m*K); 100 mm @ ...'` or a JSON array of `{thickness, conductivity}`; for a cylinder
or sphere the radii are built from `inner_radius` plus the thicknesses (not given as a list of radii, which small models mis-order);
`contact_resistances` is a separate list of exactly `n-1` areal resistances (a wrong count is `input.invalid`). Films are optional
(blank = surface at the fluid temperature). `data.energy_balance` recomputes the heat rate from the temperature drop across every element.

### `thermal_exchanger` (effectiveness-NTU, cross-checked with LMTD)

Arrangements: `parallel`, `counterflow`, `crossflow_unmixed`, `crossflow_hot_mixed`, `crossflow_cold_mixed`, `shell_tube` (one shell pass,
2, 4, ... tube passes). Relations (NTU = UA/Cmin, Cr = Cmin/Cmax, `e` = effectiveness):

* parallel `(1 - exp(-NTU(1+Cr)))/(1+Cr)`; counterflow `(1 - exp(-NTU(1-Cr)))/(1 - Cr exp(-NTU(1-Cr)))`, `NTU/(1+NTU)` at `Cr = 1`
  (written with `expm1` so it is smooth through `Cr -> 1`);
* crossflow, Cmax mixed `(1 - exp(-Cr(1-exp(-NTU))))/Cr`; Cmin mixed `1 - exp(-(1-exp(-Cr NTU))/Cr)`; which one applies is decided from the
  capacity rates (the stream you call mixed is Cmax or Cmin);
* crossflow, both unmixed: the **exact** Mason / Kays-London series `e = (1/(Cr NTU)) sum_n P(n,NTU) P(n,Cr NTU)` (regularized Poisson
  tails), not the common power-law approximation, which is within ~1-2 % of it (test). Limited to NTU <= 100;
* shell and tube `2/(1 + Cr + sqrt(1+Cr^2) coth(NTU sqrt(1+Cr^2)/2))`; `Cr = 0`: `1 - exp(-NTU)` for all.

Rating (`ua`, or `overall_u` with `area`) gives duty and both outlets. Sizing (`duty`, optionally `overall_u`) inverts the relation in closed
form (bisection only for crossflow unmixed) and gives UA and area. Exactly one of the two groups must be given.

LMTD route (always reported): `lmtd`, `lmtd_correction_factor` F, `data.lmtd_route.ua_from_lmtd` and its relative difference to the NTU
route. F = 1 for parallel and counterflow; for the shell-and-tube it is Bowman's closed form computed from the outlet temperatures (an
independent route); for crossflow it is derived from the NTU result and flagged "not an independent check" (no closed form exists).
Agreement to 1e-9 is tested for parallel, counterflow and shell-and-tube over a grid of capacity ratios and UA. When an end temperature
difference is below 1e-6 of the inlet span (NTU huge) the log-mean is ill-conditioned and the cross-check is skipped with a warning.

Refused: hot inlet not above cold inlet; duty >= Cmin (Th,in - Tc,in) (an outlet beyond the other inlet); a duty above what the arrangement
can reach (e.g. parallel flow cannot exceed `1/(1+Cr)`), with the limit in `error.details`; both/neither of rating and sizing data.
Warned: temperature cross (cold outlet above hot outlet; harmless in counterflow, reduces F in shell-and-tube/crossflow), F < 0.75,
NTU > 20.

### `thermal_fin` (uniform cross-section, 1-D)

Rectangular (`P = 2(w+t)`, `Ac = w t`) and round pin (`P = pi D`, `Ac = pi D^2/4`); `m = sqrt(hP/(kAc))`, `M = sqrt(hPkAc) theta_b`.
Infinite `q = M`; adiabatic `q = M tanh(mL)`; convective `q = M (tanh mL + h/(mk))/(1 + (h/(mk)) tanh mL)`; prescribed tip
`q = M (coth mL - (theta_L/theta_b) csch mL)` (written without `cosh`/`sinh` so it cannot overflow). Efficiency `q/(h A_f theta_b)` with `A_f = P L`
(+ `Ac` for the convective tip); adiabatic equals `tanh(mL)/(mL)`; effectiveness `q/(h Ac theta_b)`. For the convective tip the corrected-length
approximation `L_c = L + Ac/P` is reported next to the exact value (relative difference 1e-6 for the test pin). Array: `A_t = N A_f + A_b`,
`eta_o = 1 - (N A_f/A_t)(1 - eta_f)`, `q = eta_o h A_t theta_b`, cross-checked against `N q_f + h A_b theta_b`.
Warned: fin Biot `h (Ac/P)/k > 0.1`, an "infinite" fin with `mL < 2.65`, effectiveness below 2, a prescribed tip temperature outside ambient..base.
Not covered: tapered, annular, pin-fin arrays with shared tips, base contact resistance, variable h.

### `thermal_transient`

* `lumped`: `tau = rho V cp/(h As)`, `theta/theta_i = exp(-t/tau)`, Biot with `Lc = V/As`; **warns when Bi >= 0.1** (the result is still returned);
  `time` or `target_temperature` (returns the time). `surface_area` below the surface of a sphere of the same volume is refused (impossible body).
* `plane_wall` / `cylinder` / `sphere`: one-term series with the first eigenvalue found by bisection (the eigenconditions `z tan z = Bi`,
  `z J1/J0 = Bi`, `1 - z cot z = Bi`; Bessel functions by power series, small-argument series where `sin z - z cos z` would cancel), `C1`,
  Fourier number (**warns below 0.2**), temperature at `position` (0 centre .. 1 surface), the centre temperature, the energy fraction and the
  absolute energy (J/m2 of wall face for the full thickness, J/m of cylinder, J for the sphere). Target-temperature mode inverts the one-term
  expression and refuses a target the series cannot reach.
* `semi_infinite`: imposed surface temperature `erf`, convective surface `erfc(eta) - exp(hx/k + h^2 alpha t/k^2) erfc(eta + h sqrt(alpha t)/k)`
  (evaluated as `erfc(eta) - exp(-eta^2) erfcx(eta + beta)` so it cannot overflow; `erfcx` has a continued-asymptotic branch above 20),
  and the contact temperature of two semi-infinite bodies `Ts = (e_A T_A + e_B T_B)/(e_A + e_B)`. `body_thickness` (optional) triggers a
  warning when the penetration depth `4 sqrt(alpha t)` exceeds it. For `contact`, `ambient_temperature` is required by the signature but unused.

### `thermal_radiation`

Two infinite parallel plates `q = A sigma (T1^4 - T2^4)/(1/e1 + 1/e2 - 1)`; with N shields (emissivity `es` on both faces) the denominator gains
`N (2/es - 1)` (all emissivities equal: divided by `N+1`); long concentric cylinders and concentric spheres
`q = sigma A1 (T1^4 - T2^4)/(1/e1 + (A1/A2)(1/e2 - 1))`; small convex body in a large enclosure `q = e1 sigma A1 (T1^4 - T2^4)`.
Returns `heat_rate` (positive from surface 1 to 2), effective emissivity, `h_r = sigma e_eff (T1+T2)(T1^2+T2^2)` (finite at `T1 = T2`),
shield temperatures. `data.assumptions` states: diffuse-grey opaque surfaces, uniform temperature and radiosity, non-participating medium,
configuration-specific view factors. Shields are supported for parallel plates only. sigma = 5.670374419e-8 W/(m2 K4).

### `thermal_convection`

Re/Pr/Ra, Nu, `h = Nu k/L`. Correlations: internal laminar `Nu = 3.66` / `4.36`; Dittus-Boelter `0.023 Re^0.8 Pr^n` (n = 0.4 heated, 0.3 cooled);
Gnielinski with the Petukhov friction factor; flat plate laminar `0.664 Re^(1/2) Pr^(1/3)` and mixed `(0.037 Re^(4/5) - 871) Pr^(1/3)`;
Churchill-Bernstein (cylinder); Whitaker (sphere, `mu/mu_s` optional); Churchill-Chu vertical plate (full range and the laminar form) and horizontal cylinder.
Natural convection: `Ra = g beta |Ts - Tinf| L^3 rho^2 Pr/mu^2`; if `beta` is blank an ideal gas `1/T_film` is assumed and said so in a warning.
Prandtl is given directly or as `cp mu/k` (the latter wins if both differ by more than 5 %, with a warning). Warnings: Dittus-Boelter Re < 1e4, Pr outside
0.6..160, L/D < 10 (if `pipe_length` is given); Gnielinski Re outside 3000..5e6 or Pr outside 0.5..2000; laminar constants for Re > 2300 or
inside the thermal entry length; flat-plate Re/Pr ranges; Churchill-Bernstein Re Pr <= 0.2; Whitaker Re 3.5..7.6e4, Pr 0.71..380, mu/mu_s 1..3.2;
Churchill-Chu Ra beyond 1e12 (1e9 for the laminar plate form).

## How the tests verify it

Every expected number is a hand calculation with the arithmetic in a comment (and recomputed with plain Python `math`, not the tool code):
three-layer wall with films (exact `Fraction` arithmetic: R'' = 2.982778, q = 83.81449 W for 10 m2, interface temperatures), pipe wall with insulation
against `ln(r2/r1)/(2 pi k L)`, critical radius by scanning the outer radius (the heat rate peaks at `r = k/h`), sphere shell; all six NTU relations
at `Cr = 0`, `Cr = 1`, `Cr = 0.5`, inverse relations by round trip, the unmixed crossflow series against an independent N x N cell
marching model (Richardson-extrapolated, 1e-4), Bowman F against hand values and against the NTU route to 1e-9, fin quantities (pin with
m = 10 exactly), eigenvalues against the classical tables (sphere Bi = 1: `z = pi/2`, `C1 = 4/pi`), `exp(-1)` at `t = tau`, lumped vs one-term at
Bi = 1e-4, `erf(1)`, `erfc` at `beta = 1`, contact temperature 70 degC from effusivities 2000/1000, radiation shields `(N+1)`, Gnielinski /
Dittus-Boelter / Churchill-Chu hand values. Refusals and hostile inputs (zero, negative, `1e308`, `nan`, `inf`, `1e999`, wrong dimension,
difference units, mismatched lists, JSON strings, emoji) run through `fastmcp.Client` for every parameter of 13 representative calls, with strict-JSON
parsing and a finite-number check; a separate randomized sweep (not in the suite) of over 10 000 calls found no `engine.failure` and no non-finite number after one fix (the LMTD
route at the arrangement's limit, see above).

## Not done (deliberately, and not verified)

* No property database (water, air, steel, ...), no property variation with temperature, no film-temperature iteration.
* No two-phase flow: no boiling, condensation, evaporator/condenser sizing (a condenser is only reachable as a very large capacity rate, `Cr -> 0`).
* No heat pipes, thermosyphons, thermoelectrics, heat sinks beyond the uniform fin and a simple array; no tapered/annular fins; no base spreading resistance.
* No view-factor integration (only the listed enclosure configurations), no wavelength-dependent or directional emissivity, no gas radiation.
* No multi-dimensional transient conduction, no finite differences or finite elements, no internal heat generation, no shape factors for 2-D steady conduction.
* No mixed (forced + natural) convection, tube banks, rough pipes, non-Newtonian flow, liquid metals, entry-length developing correlations beyond the warning.
* No pressure drop or fouling in the exchanger tool; no multi-shell-pass F; no overall coefficient calculation (U is an input).
* Unverified: the coefficients of the empirical correlations were written from the textbook formulas and checked by recomputing the formula
  by hand, not against an independent software implementation (no `fluids`/`scipy` comparison was run); an independent reviewer should recompute
  them. The crossflow-unmixed series is verified numerically, not against a published chart. No real LLM-client session was run against these tools.
