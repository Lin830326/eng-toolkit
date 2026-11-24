---
name: eng-tolerancing
description: Use FIRST for "which fit for this bearing or shaft", H7/h6 clearance, bearing seat, tolerance stack-up or end float, true position, flatness (metrology_* tools). Fit choice, bearing ring tolerances, stack-up setup, nominal vs mean.
---

# Tolerancing workflow (eng-toolkit)

Load `eng-toolkit` too. The tools compute tabulated limits and sums; whether a fit suits the application is a judgement you state, not a result. All unreviewed.

## 1. Pin down the problem (one short list)

Nominal size and what mates with what; which part rotates and where the load sits; materials and operating temperature (aluminium or plastic housing); the function limit (clearance window, interference range, end float); for a chain, **every part in the load path in order, and where each spacer, shim or gasket sits and whether it is selected at assembly**; distributions (tolerance = +-3 sigma if the user has no capability data); for a bearing, its tolerance class and the catalogue ring tolerances.

## 2. Tool choice

| Question | Tool |
|---|---|
| Limits and clearance of a named fit (`H7/g6`) at 3 to 500 mm | `metrology_fit_limits` |
| Which standard fit gives a clearance window | `metrology_fit_search` (interference is a negative clearance) |
| Closing dimension of a chain: worst case, RSS, probability out of spec | `metrology_stackup_1d` |
| True position of holes with MMC or LMC bonus | `metrology_position_tolerance` |
| Flatness, straightness, roundness, cylindricity from points | `metrology_form_error` |
| Scan versus STEP deviation | `metrology_check_scan` (needs the `scan` extra: if `engine.unavailable`, say what to install) |
| Bar chart of fits (min and max clearance) | `plot_bar`, `format="png"` (bars do not float: two bars per fit) |

## 3. Verified calls

- `metrology_fit_limits nominal="35 mm" fit="H7/k5"` gives clearance -0.013 to +0.023 mm (transition; hole +25/0, shaft +13/+2 um). `nominal="72 mm" fit="K7/h6"` gives -0.021 to +0.028 mm. The `fit` needs hole and shaft (`H7/g6`): a single class is refused.
- `metrology_fit_search nominal="35 mm" min_clearance="-0.030 mm" max_clearance="0 mm"` returns no preferred fit (`candidate_count` 0); `data.nearest` lists misses (try `fit_set="common"` or the other `basis`).
- `metrology_form_error feature="flatness" points="0,0,0.010; 50,0,-0.004; 0,50,0.006; 50,50,-0.002; 25,25,0.003" unit="mm" tolerance="0.02 mm"` gives 0.003 mm (five points under-estimate the surface: say so).
- `metrology_position_tolerance nominal="20,25; 80,25" measured="20.04,25.03; 79.97,25.05" tolerance="0.2 mm" unit="mm"`: errors 2 sqrt(dx^2 + dy^2), worst 0.117 mm (hand check), pass. Coordinates must already be in the datum frame.

## 4. Bearing seats: the table fit is not the bearing

A rolling bearing ring is **not** an ISO H hole or h shaft: the ring tolerance is `0/-x` below nominal (normal class, ISO 492; for example a 35 mm bore `0/-12 um`, a 72 mm outer `0/-13 um`: confirm in the catalogue, never recall). `fit_limits` gives only the shaft or housing side. Build the real seat with `metrology_stackup_1d` (clearance = hole - shaft, interference negative):

`chain=[{"name":"bearing bore","nominal":"35 mm","plus":"0 mm","minus":"0.012 mm","direction":"+"},{"name":"shaft k5 (+2/+13 um)","nominal":"35.0075 mm","tol":"0.0055 mm","direction":"-"}] output_unit="um"` gives -25 to -2 um (interference 2 to 25 um), whereas the table `H7/k5` says -13 to +23 um. Housing side the same way: housing K7 (+9/-21 um) against the outer ring `0/-13` gives -21 to +22 um.

Rules to state, labelled as catalogue practice to confirm in the bearing maker's tables: rotating inner ring under a fixed radial load gets an interference fit (k5, k6, m5 by load size), the stationary outer ring a tighter or looser housing fit by load direction and housing material. **A light-alloy housing expands more than the steel ring and loosens the outer ring when warm: choose a tighter housing fit than in steel, not looser.** Hand check `(alpha_Al - alpha_steel) D dT` (about 40 um for 72 mm and 50 K, with 23.6 and 12 um/m/K). **Ask the operating temperature of housing and ring; never assume a temperature difference** and never quote a loosened clearance from an assumed one.

## 5. Stack-up setup (verified)

`metrology_stackup_1d chain=[{"name":"shoulder to groove","nominal":"24.9 mm","tol":"0.1 mm","direction":"+"},{"name":"spacer","nominal":"6.0 mm","plus":"0 mm","minus":"0.05 mm","direction":"-"},{"name":"bearing width","nominal":"17 mm","plus":"0 mm","minus":"0.12 mm","direction":"-"},{"name":"circlip","nominal":"1.5 mm","plus":"0 mm","minus":"0.06 mm","direction":"-"}] lower_limit="0.2 mm" upper_limit="0.6 mm" monte_carlo=true output_unit="mm"`

Result: worst case 0.30 to 0.73 mm (hand: 24.8 - 24.5 = 0.30, 25.0 - 24.27 = 0.73), 1.9 % above 0.6 mm in the Monte Carlo, `within_spec` false. **`result_nominal` (0.40) is the sum of the drawing nominals; with asymmetric tolerances the centre is `result_mean` (0.515): quote the mean.** Every length carries a unit, `"tol"` is symmetric, `plus`/`minus` are magnitudes. Linear chains only: no thermal growth, form or tilt. Before declaring interference, check that a shim or spacer in the chain is not an adjustable one that you may choose at assembly.

## 6. Report

Result (limits with units and signs; clearance positive, interference negative), inputs (which tolerance source for every link, including the bearing catalogue), hand check, distribution assumption for Monte Carlo (normal, 3 sigma), what is judgement versus calculation, limits (no thermal or form effects unless computed by hand, not a drawing check).
