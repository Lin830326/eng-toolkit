---
name: eng-optimize
description: Use FIRST for "find the lightest or cheapest design", fit a curve or calibration with confidence intervals, solve equations, a linear program, uncertainty of a result, or plan an experiment (optimize_* tools). Unit-free formulas, limits.
---

# Optimization, fitting and uncertainty workflow (eng-toolkit)

Load `eng-toolkit` too. The `optimize_*` tools are plain-number engines: **formulas carry no units**, and a numerical certificate is local, not proof of a global optimum. The physics inside a formula is yours: **a model term the toolkit cannot compute (e.g. spreading/constriction resistance of a small heater on a base plate) is outside the toolkit**: say so, give the standard method labelled "hand method, not tool-checked", and tell the person the optimum may move.

## 1. Pin down the problem (ask if it changes the answer)

Objective and direction; design variables with bounds; every constraint; the data (values, units, are the point uncertainties known?); what is wanted back (optimum, interval, input ranking, design table). Then **choose one consistent unit system** (e.g. N, mm, MPa, kg), convert every number to it (my arithmetic) before writing a formula, and state it (200 GPa = `200000` MPa). Count geometry carefully: n fins across width W leave n - 1 gaps, gap = (W - n t)/(n - 1).

## 2. Choose the tool

| Task | Tool |
|---|---|
| Nonlinear objective, bounds, up to 20 constraints | `optimize_minimize` (`maximize=true` to maximise) |
| Linear objective and linear rows (blends, allocation, integer counts) | `optimize_linprog` |
| Solve `f(x)=0` or a square system | `optimize_root` |
| Fit parameters to data, with intervals | `optimize_fit` |
| Output uncertainty from input uncertainties, ranking of inputs | `optimize_sensitivity` |
| Experiment plan and response surface | `optimize_doe` |
| Data vs fit picture | `plot_xy` with `data_file` = the `fit.csv` artifact (columns `<x>,<y>,fitted,residual`), `y_columns="T,fitted"`, `kind="scatter,line"`, labels with units; the PNG is written by default: give `data.png_path` |

## 3. Build the inputs

- **Formula grammar**: numbers, names, `+ - * / ^` (or `**`), parentheses, `sin cos tan asin acos atan sinh cosh tanh exp log log10 sqrt abs floor ceil sign atan2 min max pow`, constants `pi`, `e`; `log` is natural, angles are radians. No implicit multiplication (`2*x`, not `2x`), no units, no `<=` inside an objective. Variable names must not be `e`, `pi` or a function name.
- **Records** `name=number`, separated by `;` or newline, no trailing separator, no units or arithmetic (`x=0.5`, not `x=pi/2` or `x=3 mm`): refused as `input.invalid`.
- **Bounds / matrices / data** are JSON text: `bounds="[[0,5],[null,2]]"` in the order of `initial`; `null` is unbounded. Fit `data='{"x":[..],"y":[..]}'` or `data_file` = CSV path.
- **Constraints** (`minimize`): `expr<=0`, `>=0` or `==0`, split by `;`, **right side 0** (`x+y-10<=0`).
- Fixed numbers go in `parameters="a=2"`, not in `initial`. `time_limit` needs a unit (`"10 s"`, 0.001 to 60 s).

## 4. Tool specifics and pitfalls

- **minimize**: `auto` uses BFGS (smooth) or Nelder-Mead (`abs min max floor sign`). It is a **local** search: from `x=1.5`, `x^4-3*x^2+x` stops at the local minimum -1.070 while the global one is -3.514 at `x=-1.301`, with `converged: true` and no warning. If the problem may be non-convex use `method="multistart"` with finite bounds (`starts` up to 64) and read `local_optima`.
- **linprog**: `c`, `A_ub x<=b_ub`, `A_eq x=b_eq`, bounds default to **x >= 0** (use `[[null,null],...]` for free variables); write `>=` rows by negating the row and the right side. `integer="[0,2]"` = zero-based integer variables. `shadow_prices` are d(objective)/d(right side) of the row **as written**: for a negated `>=` row the cost rise per unit of requirement is minus the printed dual. `rhs_ranges` vary one row at a time.
- **root**: a `bracket` needs opposite signs at its ends (double roots like `x^2` never change sign). `multistart`/`scan` find more roots: `data.roots` lists all, `solution` is only the first.
- **fit**: each parameter is scaled by its initial value (`data.parameter_scales`), so give initial values of the right order of magnitude, read off the data (last value for an offset, span for an amplitude). A start far off (`Tinf=1;A=1;tau=1` on data near 22, 58 and 140) ends in `optimize.not_converged`; `Tinf=20;A=50;tau=100` converges. Add `bounds`, use `multistart`. With `sigma` unknown leave `absolute_sigma=false` (covariance scaled by reduced chi-square); with known measurement sigmas use `sigma` and `absolute_sigma=true`. The headline `value` is the RMS residual.
- **sensitivity**: `nominal="F=100;A=10"`, `standard_uncertainties="F=2;A=0.1"` (standard, not expanded). Methods: `gum` (first order, `correlation` matrix optional), `monte_carlo` (`distributions="x,uniform,1.8,2.2;y,normal,3,0.1"`), `sobol` (independent inputs only). The headline `value` is always the GUM uncertainty: read Monte Carlo in `data.monte_carlo`, Sobol in `data.sobol.indices`. Use `samples` of 10000 or more (max 200000), a fixed `seed`; Sobol needs `samples*(inputs+2)` <= 200000. For nonlinear models compare GUM with Monte Carlo. **A part tolerance of +-a (uniform) has `u = a/sqrt(3)`: report the expanded uncertainty `U = k u` (k = 2, about 95 %) as an interval, and the worst case `sum(|c_i| a_i)`, and say which one the number is**; a calibration's uncertainty includes the part tolerance, not only the fit interval.
- **doe**: `factors="temp,10,30;speed,100,200"`, `design`: `full_factorial` (`levels="[3,4]"`), `fractional_iii/iv/v`, `box_behnken` (3+ factors), `ccd_face/circumscribed/inscribed`, `lhs`, `sobol`, `halton`. Add `responses` (one per run, in order) or a `model` for a fitted surface; its extrema belong to the fit.

## 5. Run, then verify

1. Check `ok`, `data.converged` (minimize, root, fit) or `data.status` (`optimal`, linprog), `warnings`.
2. **Recompute the headline by hand** from the returned point: the objective, the constraints (`data.constraints[].value`; a 1e-7-level violation is tolerance), the root residual, the fit `residuals`, the LP vertex, or the GUM sum `u^2 = sum((df/dx_i * u_i)^2)`.
3. Compare with an independent route (another start, method or `seed`).
4. Active constraints (`active`, `multipliers[].rhs_sensitivity`): an unexpected active set means a units or sign mistake.
5. Refusals are results: `optimize.not_converged` (read `details.reason`; `optimize.unbounded` = a bound or constraint is missing; contradictory constraints end here too; a rank-deficient fit = parameters not identifiable), `optimize.infeasible` / `optimize.unbounded` from `linprog`, `input.out_of_range`. Fix the model; never invent a value.

## 6. Report

Result in the user's units; the unit system and formulas as passed; checks (hand recomputation, multistart, seed and samples, active constraints); limits: local optimum, local-approximation intervals, Monte Carlo and Sobol sampling error (`mean_standard_error`), terms outside the toolkit, unreviewed.
