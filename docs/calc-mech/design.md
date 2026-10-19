# calc additions and the mech domain: design notes

Scope: four new tools in `calc` (hydraulic component sizing) and a new `mech` domain (four mechanical-design checks). Branch
`feat/calc-mech`, cut from main at `e5ad2d9`. Nothing outside `eng_toolkit/domains/calc/`, `eng_toolkit/domains/mech/`, their tests,
this folder and the README tool sections was touched (no `pyproject.toml`, `uv.lock`, `test_gateway.py`, other domain).

## Ground rules followed

* Textbook closed forms only, each named in the tool docstring and below; no text, figure or table of any standard is copied. The
  one data table is 20 thread pitches (`metric-coarse-pitch-data.md`).
* Every physical input is a string with a unit through `units.parse_quantity`; the only plain numbers are dimensionless parameters
  (efficiency, Cd, exponent, nut factor, active coils) and the unit-system / choice strings.
* Errors use the existing families: `units.bare_number | dimension_mismatch | parse_error` (from `parse_quantity`),
  `input.invalid` (zero, negative, unknown choice, both/neither of an exclusive pair, a dimensionless parameter outside its
  definition such as efficiency 85), `input.missing`, `input.out_of_range` (parses and is positive, but absurd: 1000 m orifice,
  5 000 000 bar, 1e9 rpm). A failed call carries no warnings (as in `hydraulics.py`).
* Warnings (success only) say when a formula is outside its usual validity; the call still returns the number.
* No LLM call, no execution of anything the caller sends, every result `review_status: "unreviewed"`, no new dependency
  (`pint` only; `mech` declares `engines=("pint",)`, no `engines.py` needed).
* Rotational speed: pint reads `1 Hz` as `1/s` but `rpm` as `2*pi rad/min`, so `Hz` converted to `rpm` would be off by 2*pi. The
  helper `revolutions_per_second` treats a unit that names revolutions or radians through pint and a bare frequency (`Hz`, `1/min`)
  as revolutions. Tested: `25 Hz`, `1500 rpm`, `157.08 rad/s` and `1500 1/min` agree.

## calc (4 new tools, 6 in total)

| tool | formula | hard errors (`input.out_of_range` bounds) | warnings when outside validity |
|---|---|---|---|
| `orifice_flow` | `Q = Cd A sqrt(2 dp / rho)`; or `dp = rho/2 (Q/(Cd A))^2` from the flow. Exactly one of `pressure_drop`/`flow_rate`. | d 0.01 mm..1000 mm; rho 1..25 000 kg/m^3; dp 1 Pa..1000 bar (also checked on the computed dp); Q 1e-12..100 m^3/s; Cd in (0, 1] (`input.invalid`) | orifice Reynolds number `4 rho Q/(pi d mu)` < 1e4 (only if viscosity is given): Cd is not constant, flow overestimated; Cd outside 0.5..0.9; rho < 200 kg/m^3 (looks like gas, incompressible equation invalid); no viscosity given (Re check skipped) |
| `pump_power` | `P_hyd = Q dp`; `P_shaft = P_hyd / eta`; `T = P_shaft / (2 pi n)` | Q 1e-9..10 m^3/s; dp 0.001..1500 bar; speed 1..1e5 rpm; eta in (0, 1] (`input.invalid`, hint "0.85 not 85") | eta > 0.95 (no pump reaches it), eta < 0.3 (likely a percentage), dp > 450 bar |
| `accumulator_precharge` | gas law `p V^n = const` on absolute pressures: `V0 = dV / ((p0/p1)^(1/n) - (p0/p2)^(1/n))`, `V1 = V0 (p0/p1)^(1/n)`, `V2 = V0 (p0/p2)^(1/n)`. Gauge pressures get +101 325 Pa. Pre-charge defaults to 0.9 x p_min. | dV 0.001 L..1e5 L; pressures 0.01..1500 bar; n in [1, 1.67] (`input.invalid`); p_max <= p_min and pre-charge >= p_min are `input.invalid` (the accumulator would deliver nothing) | pre-charge defaulted (rule of thumb, not a standard); pre-charge > 0.9 p_min; p_max/p0 > 4 (typical bladder limit); gauge p_min < 1 bar (probably absolute) |
| `cylinder_speed` | `v = Q / A`, `A_extend = pi/4 d^2`, `A_retract = pi/4 (d^2 - d_rod^2)`; stroke times `s/v`; return flow from the other chamber by continuity | bore 1..2000 mm; rod 0.5 mm..< bore (explicit `0 mm` is `input.invalid`, leave empty for none); Q 1e-9..10 m^3/s; stroke 0.1 mm..100 m | no rod given; speed > 0.5 m/s (cushioning/seal/piston-speed ratings); area ratio > 1.6 (return flow on retract much larger than pump flow) |

Not done (deliberately): compressible/gas orifice flow, cavitation/choking, velocity-of-approach factor and ISO 5167 discharge
correlations (Cd is the caller's), temperature effect on accumulator gas, accumulator selection from a catalogue, pump
volumetric/mechanical split and pump displacement, motor efficiency, cylinder leakage/compressibility/acceleration, any
valve-sizing coefficient (Kv/Cv) conversion.

## mech (4 tools)

| tool | formula | hard errors | warnings when outside validity |
|---|---|---|---|
| `bolt_check` | `A_s = pi/4 (d - 0.9382 P)^2` (mean of pitch and minor diameter of the ISO metric thread); class `a.b`: `Rm = 100 a`, `Rp = 10 a b` MPa (what the designation means); proof stress `Sp` given, else estimated `0.9 Rp`; `F_p = Sp A_s`; `T = K F d` (torque -> preload, or preload -> torque, exactly one given); bolt load `F_i + C P_ext`; utilization `F_b / F_p` | d 1.6..100 mm; pitch 0.2..12 mm and < d/2; torque 1 mN m..100 kN m; preload 1 N..1e8 N; K in [0.05, 1], C in [0, 1] (`input.invalid`); class must be one of 4.6 4.8 5.6 5.8 6.8 8.8 9.8 10.9 12.9; no pitch given for a non-tabulated d is `input.invalid` | utilization > 1 (permanent elongation); preload > 90 % of proof; preload < 25 % of proof; K outside 0.1..0.3; proof stress is the 0.9 Rp estimate; d not in the coarse table (inch-thread caveat); C = 1 assumed |
| `bearing_life` | ISO 281 basic rating life `L10 = (C/P)^p` million rev, p = 3 ball, 10/3 roller; `L10h = 1e6 L10 / (60 n)`; inverse `C_req = P (L10h 60 n / 1e6)^(1/p)` for a `required_life` | C, P 1 N..1e8 N; speed 0.1..1e6 rpm; life 1 h..1e7 h; unknown bearing type `input.invalid` | P/C > 0.5; P/C < 0.05; speed < 10 rpm (static rating); L10h > 1e6 h; the bearing does not reach `required_life` |
| `spring_compression` | `C = D/d`, `k = G d^4 / (8 D^3 Na)`, Wahl `Kw = (4C-1)/(4C-4) + 0.615/C`, `tau = Kw 8 F D / (pi d^3)`; ends: squared&ground `Nt = Na+2, Hs = Nt d`; squared `Nt = Na+2, Hs = (Nt+1) d`; plain&ground `Nt = Na+1, Hs = Nt d`; plain `Nt = Na, Hs = (Nt+1) d`; buckling criterion `L0 < 2.63 D / alpha` (alpha 0.5 and 1.0 -> 5.26 and 2.63 D) | d 0.05..50 mm; D 0.3..500 mm; C in 1.5..50; Na in [0.5, 500]; G 1..1000 GPa; free length not longer than solid height is `input.invalid`; load/deflection beyond the stroke to solid is `input.out_of_range`; both load and deflection is `input.invalid` | C outside 4..12; fewer than 3 active coils; L0/D > 2.63 (needs flat parallel ends) and > 5.26 (can buckle regardless); stress above allowable at working load or at solid |
| `beam_bending` | simply supported: point `R1 = P b/L`, `M = P a b/L`, `y = P s (L^2 - s^2)^1.5 / (9 sqrt3 E I L)` with `s = min(a,b)`; UDL `R = wL/2`, `M = wL^2/8`, `y = 5wL^4/(384EI)`. Cantilever: point `M = P a`, tip `y = P a^2 (3L - a)/(6EI)`; UDL `M = wL^2/2`, `y = wL^4/(8EI)`. `sigma = M c / I`. Sections: rectangle `b h^3/12`, circle `pi d^4/64`, tube `pi (D^4-d^4)/64`, or I and c given | L 1 mm..1 km; loads 1 mN..1e9 N (or N/m); E 1 MPa..1000 GPa; point `position` in [0, L] (negative `input.invalid`, beyond L `input.out_of_range`); a UDL needs a force per length (`10 kN` is a dimension error), a point load a force; inner >= outer diameter `input.invalid`; missing section dimension `input.missing` | max deflection > 5 % of span (small-deflection theory); span/depth < 10 (shear deformation); stress above `allowable_stress`; `position` given with a UDL (ignored) |

All four mech tools take `unit_system` (`metric`: kN, kN m, mm, MPa; `imperial`: lbf, in lbf, in, ksi); inputs accept any unit.

Not done (deliberately): bolt shear/bending/combined stress, VDI 2230 joint stiffness and embedment, fatigue, thread stripping,
UN-thread stress areas; bearing equivalent load `X Fr + Y Fa`, the reliability factor `a1`, `a_ISO` modified life, static
rating, grease life, speed limits; spring fatigue (Goodman/Zimmerli), surge frequency, set removal, extension and torsion
springs, tapered or rectangular wire; beam shear stress, indeterminate beams (fixed-fixed, propped), several loads and
superposition, moving loads, lateral-torsional and local buckling, Timoshenko shear deflection, column buckling, self-weight.

## Decisions

* **One module per concept, one server per domain.** `calc/components.py` registers on the existing `calc` server from
  `hydraulics.py` (its two tools and tests are untouched); `mech/_server.py` holds the `mech` server and each tool module
  registers on it. 4 tools in `mech` and 6 in `calc` stay within the per-domain budget of 6.
* **Plain-number parameters for dimensionless quantities.** An efficiency of `"0.85"` as a string would be rejected by
  `parse_quantity` as a bare number, and `"85 %"` is ambiguous; a JSON number with a hard 0..1 check is the least error-prone for
  small models. A definition-level violation (efficiency 85) is `input.invalid` with a hint, not `input.out_of_range`.
* **Proof stress is not tabulated.** The designation `a.b` fixes the nominal tensile and yield strength, but the proof stress of
  ISO 898-1 is a table. The tool takes `proof_strength` when given and otherwise estimates `0.9 Rp`; it always warns when it
  estimated. From memory the published proof stresses of classes 4.6-12.9 are within roughly 3-7 % of `0.9 Rp` (the estimate is
  slightly low, i.e. conservative for utilization); **I could not check this against the standard in this environment**, so use
  `proof_strength` for a design value.
* **Pump efficiency is the overall one.** Delivered flow x pressure rise / overall efficiency is the textbook shaft power; a
  separate volumetric/mechanical split would require the theoretical flow (displacement x speed), which is a different tool.
* **Accumulator pre-charge default 0.9 x p_min** is a manufacturers' rule of thumb, not a standard; it is applied to the pressures
  in the basis the caller gave (gauge by default), documented in the tool and flagged by a warning when used.
* **Output units:** calc tools take explicit `*_unit` parameters like the existing two; mech tools take one `unit_system`
  switch instead of 3-4 unit parameters each (fewer parameters for small models).

## Verification

* `tests/test_calc_components.py`, `test_mech_bolt.py`, `test_mech_bearing.py`, `test_mech_spring.py`, `test_mech_beam.py`,
  `test_calc_mech_gateway.py`: 262 tests. Every tool has hand-computed expected numbers with the arithmetic in comments,
  unit-conversion cases (inch, psi, bar, kPa, kN, lbf, lbf/ft, gal/min, hp, ft, rad/s, Hz), invalid and edge inputs (zero, negative,
  absurd magnitude, wrong dimension, bare number, unknown unit, unknown choice) each asserting the error family and offending
  parameter, and each validity warning.
* Independent anchors beyond my own arithmetic: bolt stress areas against catalogue values of M8..M24 (within 0.3 %); the beam
  midspan deflection `P L^3 / 48 E I` equals the general formula at `a = b`; the accumulator delivered volume `V1 - V2` equals the
  requested volume (checked for n = 1 and n = 1.4); the orifice flow and pressure drop are exact inverses.
* Through a real MCP client (in-memory transport, `test_calc_mech_gateway.py`) for one tool of each kind, including a JSON-string
  quantity and a structured error. A real `claude -p --mcp-config` call was not run by this session.

## Not verified

* The proof-stress estimate and the 20-entry pitch table against the standards' text (not available here).
* Warning thresholds (Re 1e4 for a constant Cd, P/C 0.5 and 0.05, C index 4..12, p_max/p0 4, speed 0.5 m/s, K 0.1..0.3, preload 90 %
  / 25 %, span/depth 10, deflection 5 %) are common textbook/handbook rules of thumb from memory, not checked against a source.
* The full suite has 10 failures in `tests/test_metrology_check_scan.py` (`details["path"]` is the job's copy, not the caller's
  path); they fail identically on main `e5ad2d9` and are in the metrology domain, not touched here.
