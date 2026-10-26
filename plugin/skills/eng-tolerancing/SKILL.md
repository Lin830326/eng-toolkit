---
name: eng-tolerancing
description: Use for ISO 286 fits, bearing seat tolerances, 1D tolerance stack-ups with worst case, RSS and Monte Carlo, true position, form error and scan-to-model deviation with the metrology_* tools. Covers fit choice, bearing ring tolerances, stack-up setup and reading nominal versus mean.
---

# Tolerancing workflow (eng-toolkit)

Load `eng-toolkit` first. The tools compute tabulated limits and sums; whether a fit suits the application is a judgement you state, not a result. All unreviewed.

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

- `metrology_fit_limits nominal="25 mm" fit="H7/k5"` gives clearance -0.011 to +0.019 mm (transition; hole +21/0, shaft +11/+2 um). `nominal="52 mm" fit="K7/h6"` gives -0.021 to +0.028 mm.
- `metrology_fit_search nominal="25 mm" min_clearance="-0.025 mm" max_clearance="-0.001 mm"` returns no preferred fit; `data.nearest` lists misses (try `fit_set="common"` or the other `basis`).
- `metrology_form_error feature="flatness" points="0,0,0.010; 50,0,-0.004; 0,50,0.006; 50,50,-0.002; 25,25,0.003" unit="mm" tolerance="0.02 mm"` gives 0.003 mm (five points under-estimate the surface: say so).
- `metrology_position_tolerance nominal="20,25; 80,25" measured="20.04,25.03; 79.97,25.05" tolerance="0.2 mm" unit="mm"`: errors 2 sqrt(dx^2 + dy^2), worst 0.117 mm (hand check), pass. Coordinates must already be in the datum frame.

## 4. Bearing seats: the table fit is not the bearing

A rolling bearing ring is **not** an ISO H hole or h shaft: the ring tolerance is `0/-x` below nominal (normal class, ISO 492: 6205 bore 25 mm `0/-10 um`, outer 52 mm `0/-13 um`; confirm in the catalogue, never recall). `fit_limits` gives only the shaft or housing side. Build the real seat with `metrology_stackup_1d` (clearance = hole - shaft, interference negative):

`chain=[{"name":"bearing bore 6205","nominal":"25 mm","plus":"0 mm","minus":"0.010 mm","direction":"+"},{"name":"shaft k5 (+2/+11 um)","nominal":"25.0065 mm","tol":"0.0045 mm","direction":"-"}] output_unit="um"` gives -21 to -2 um (interference 2 to 21 um), whereas the table `H7/k5` says -11 to +19 um. Housing side the same way with the outer ring `0/-13` against the housing hole (K7 gives about -21 to +22 um).

Rules to state, labelled as catalogue practice to confirm in the bearing maker's tables: rotating inner ring under a fixed radial load gets an interference fit (k5, k6, m5 by load size), the stationary outer ring a tighter or looser housing fit by load direction and housing material. **A light-alloy housing expands more than the steel ring and loosens the outer ring at temperature: choose a tighter housing fit than in steel, not looser** (thermal hand check `(alpha_Al - alpha_steel) D dT`, about 30 um for 52 mm and 50 K). Never advise a looser fit because of expansion without computing the clearance at temperature.

## 5. Stack-up setup (verified)

`metrology_stackup_1d chain=[{"name":"shoulder to groove","nominal":"21.5 mm","tol":"0.1 mm","direction":"+"},{"name":"spacer","nominal":"5.0 mm","plus":"0 mm","minus":"0.1 mm","direction":"-"},{"name":"bearing width","nominal":"15 mm","plus":"0 mm","minus":"0.12 mm","direction":"-"},{"name":"circlip","nominal":"1.2 mm","plus":"0 mm","minus":"0.06 mm","direction":"-"}] lower_limit="0.1 mm" upper_limit="0.5 mm" monte_carlo=true output_unit="mm"`

Result: worst case 0.20 to 0.68 mm (hand: 21.6 - 4.9 - 14.88 - 1.14 = 0.68), RSS 0.31 to 0.57, 8.4 % above 0.5 mm, `within_spec` false. **`result_nominal` (0.30) is the sum of the drawing nominals; with asymmetric tolerances the centre is `result_mean` (0.44): quote the mean.** Every length carries a unit, `"tol"` is symmetric, `plus`/`minus` are magnitudes. Linear chains only: no thermal growth, form or tilt. Before declaring interference, check that a shim or spacer in the chain is not an adjustable one that you may choose at assembly.

## 6. Report

Result (limits with units and signs; clearance positive, interference negative), inputs (which tolerance source for every link, including the bearing catalogue), hand check, distribution assumption for Monte Carlo (normal, 3 sigma), what is judgement versus calculation, limits (no thermal or form effects unless computed by hand, not a drawing check).
