# Numerical optimization and experiments

The `optimize` domain supplies six tools using NumPy and the standard library. Pint parses the physical wall-clock budget. No new dependency or external process is required. SciPy is used only as an optional test oracle, behind `pytest.importorskip`.

Every result is `unreviewed`. Successful results include formulas, assumptions, validity, provenance and the standing warning. Numerical success means the reported finite-precision certificate passed. It does not establish physical validity or a global nonlinear optimum.

## Public calls and numeric conventions

Tools accept basic JSON scalar parameters. Matrices and data tables are JSON **text**, for example `bounds="[[0,5],[0,2]]"`. Named values are semicolon/newline-separated records, for example `initial="x=0;y=1"`. Records have no trailing separator. Blank optional fields mean omitted as described by each parameter.

Expressions use plain numbers in the units selected by the caller. The interpreter does not track units, convert variables, infer dimensions, or validate dimensional consistency. Returned variable values use the same numeric convention as the inputs. The unified contract labels scalar headline values `dimensionless` because no physical dimension has been declared.

Examples:

```python
optimize_minimize(expression="(x-1)^2+4*(y+2)^2",
                  initial="x=0;y=0", bounds="[[-5,5],[-5,5]]")
optimize_root(expression="x^2-2", initial="x=1", bracket="[0,2]")
optimize_fit(model="a*exp(-b*x)", initial="a=2;b=0.5",
             data='{"x":[0,1,2,3],"y":[2,1.2130613194,0.7357588823,0.4462603203]}')
optimize_sensitivity(expression="F/A", nominal="F=100;A=10",
                     standard_uncertainties="F=2;A=0.1")
optimize_doe(factors="temperature,10,30;speed,100,200",
             design="full_factorial", levels="[3,4]", center_points=0)
optimize_linprog(c="[-1,-1]", A_ub="[[1,1]]", b_ub="[3]")
```

`time_limit` requires a numeric literal and one named time unit, such as `10 s` or `500 ms`. Conversion goes through `units.parse_quantity`, then the converted value must be between 0.001 and 60 seconds. Bare values, expressions, non-time units, conversion underflow, NaN and infinity are refused.

## Expression language

`_expr.py` is a handwritten tokenizer, Pratt parser, immutable tree and interpreter. It never delegates caller formulas to Python or to another expression engine. Operators and function names select functions from a fixed table. Bindings are declared before parsing. No caller value is used for attribute lookup or indexing.

```text
expression := sum
sum        := product { ("+" | "-") product }
product    := unary { ("*" | "/") unary }
unary      := ("+" | "-") unary | power
power      := primary [ ("^" | "**") unary ]
primary    := number | declared_name | "pi" | "e"
            | "(" expression ")" | function "(" arguments ")"
arguments  := expression [ "," expression ]
number     := (digits ["." [digits]] | "." digits)
              [("e" | "E") ["+" | "-"] digits]
name       := ASCII letter or underscore, followed by ASCII letters/digits/underscores
```

Powers associate right. Exponentiation binds more tightly than unary minus on its left, so `-2^2=-4`, `2^3^2=512`, and `2^-1=0.5`. Addition, subtraction, multiplication and division associate left.

Unary functions are `sin cos tan asin acos atan sinh cosh tanh exp log log10 sqrt abs floor ceil sign`. Binary functions are `atan2 min max pow`; `atan2(y,x)` follows NumPy's argument order. Constants are `pi` and `e`. Angles inside formulas are numerical radians. `log` is natural logarithm. Variable and fixed-parameter names cannot shadow functions or constants.

There are no strings, attributes, subscripts, assignments, comprehensions, lambdas, implicit multiplication, Unicode identifiers, or comparisons inside expressions. Commas occur only inside function calls. Semicolons are record separators at the tool boundary, not expression syntax. Unknown names, wrong arity, empty expressions, and trailing tokens produce `input.invalid` with a zero-based character position.

Limits are enforced before or during interpretation:

| Resource | Limit |
| --- | --- |
| Expression text | 2,000 characters per expression |
| Identifier | 32 ASCII characters |
| Declared names | 16 total, including independent columns and fixed parameters |
| Tree | 500 nodes, depth 64 |
| Parser recursion | Explicitly bounded to 64 expression frames |
| Token list | 1,500 tokens |
| Array binding | Scalar or one-dimensional array; at most 200,000 entries; shared length |
| Numeric literals | Zero or absolute magnitude `1e-100..1e100` |
| Intermediate/output magnitude | Finite, absolute magnitude at most `1e100` |
| Power exponent | Absolute magnitude at most 10,000 |
| Record text | 42,000 characters, plus tool-specific record caps |
| JSON text | 4,000,000 characters, nesting at most 8 |

Numeric record values must be signed numeric literals. `x=pi`, `x=(1)` and `x=1+2` are refused. JSON numeric conversion detects underflowing nonzero literals as well as overflow. A negative base with a fractional exponent is an invalid trial. Integer powers of negative bases are allowed. Nonfinite or excessive intermediate values produce `TrialError`, which search algorithms may reject as an infeasible trial. The final public result never contains NaN or infinity.

Scalar and array expression operations refuse destructive underflow as `input.out_of_range` at the expression's parameter pointer. This includes a nonzero intermediate rounded to zero and any subnormal intermediate, even when later operations would restore a representable magnitude. Constants use the same NumPy arithmetic guards as array operands. Exact zeros from cancellation, multiplication by zero, or functions such as `floor` remain valid.

Parsing is linear in text/tree size with fixed recursion limits. Hostile 6,000-character strings are rejected by the length check. Array evaluation uses the same tree and numeric guards as scalar evaluation; the deadline is checked during tree traversal and after the final operation.

## Budgets and determinism

Each request owns one `Budget`. Nested solvers, finite differences, constraint evaluations, population searches, multistart scans and certification share it. One array evaluation charges each scalar sample, not just the Python call. Expression work is charged as `samples * tree_nodes` before execution, with a 100,000,000 operation cap. The ordinary evaluation cap is 200,000. Sensitivity allows 202,048 evaluations to accommodate local derivatives around a full 200,000-row Monte Carlo sample; Sobol sampling retains a separate 200,000 total sample-evaluation cap.

`fit` switches its budget to per-call accounting (`Budget.count_calls`): one evaluation per vectorised model call over all data rows, at most 200,000 calls, with separate caps of 200,000,000 row evaluations and 2,000,000,000 node-row operations, plus the time limit. Counting every row against the 200,000-evaluation cap made the advertised 20,000-row limit unreachable (a two-parameter linear fit failed at 4,000 rows). Results report `evaluations` (model calls) and `row_evaluations`.

Multistart shares the budget fairly: each local run gets an equal share of the remaining time and evaluations, with 10% held back for the final post-condition. A run that spends its share is recorded as `budget_share_exhausted` in `local_runs`; the call still returns the best certified local optimum among completed runs, with a warning naming how many runs stopped. Only the call's own budget produces a budget refusal. A budget refusal carries `best_trial`: the best evaluated point (feasible first, then lowest objective; otherwise lowest violation), never merely the last one, plus `best_feasible` when one exists.

The monotonic clock is checked in solvers, linear-program pivots, branch nodes, expression traversal and bounded design work. A clock limit returns family `input.out_of_range`, pointer `/time_limit`, and reason `optimize.time_budget_exceeded`. An evaluation/work limit also uses `input.out_of_range` and reason `optimize.evaluation_budget_exceeded`. Fixed loop caps remain in place even if the clock is mocked or changes unexpectedly.

All random draws use one `numpy.random.default_rng(seed)` per call. Seeds are integers `0..4294967295`, default zero, and are echoed. LP is deterministic and does not accept a seed. Timing failures can depend on machine load; completed stochastic calculations are reproducible for a fixed NumPy implementation and seed.

## Minimize

`minimize` accepts an objective, required initial variable records, optional fixed `parameters`, bounds, and up to 20 constraints. Constraints have the form `expression <= 0`, `expression >= 0` or `expression == 0`; subtract a nonzero right-hand side within the expression. Bounds follow initial-name order, with `null` for an omitted endpoint. Fixed search variables must be supplied as fixed parameters instead. `maximize=True` keeps the returned objective in its original sign.

`auto` chooses BFGS for formulas without the explicitly nonsmooth functions `abs/min/max/floor/ceil/sign`; otherwise it chooses Nelder-Mead. Bounds activate projected directions. Constraints activate an augmented Lagrangian outer solve with the selected local method. This syntactic smoothness test cannot establish differentiability everywhere in the domain.

The local smooth method uses inverse BFGS with Shanno-Phua initial scaling, central Richardson finite differences, and Armijo backtracking with `c1=1e-4` and step halving, at most 60 trials per step. Variables held on a bound by the gradient are fixed; the direction uses the free block of the inverse Hessian, and the secant pair excludes held variables (a Bertsekas-style projected quasi-Newton step). The descent test is relative (`g.d < -1e-12*|g||d|`); an absolute floor would reject every quasi-Newton direction once the gradient is small. Inadequate curvature resets the inverse Hessian. Steps are capped at `100*max(1,||z||inf)` scaled units (`max(1,||z||inf)` after a reset). An iterate beyond `1e10` scaled units stops the search as diverging. When objective differences are at floating-point resolution, a step may be accepted only if the numerical projected gradient improves; equal objective values alone never certify convergence, and five rounding-level steps in a row end the local search as stalled. This is projected BFGS, not a full L-BFGS-B implementation and not a strong-Wolfe search.

Missing bounds are true infinities internally. The `1e100` JSON placeholder never becomes an iterate, a finite-difference neighbour or a logged value; `convergence.csv` only records finite values up to `1e100`.

For canonical inequalities `g<=0` the safeguarded multiplier method minimizes
`f + sum((max(0,l+r*c)^2-l^2)/(2*r))` with `c=g/s`. It is evaluated in the algebraically equal form `l*c+r*c^2/2`, or `-l^2/(2r)` on the inactive side, so large multipliers do not cancel to rounding noise. Equalities use the unprojected shifted multiplier. The safeguards:

- Constraint normalization `s` is `max(1,||grad g||inf)` in scaled coordinates. It is reset only when the gradient size changes by more than 100x, and multipliers are kept in raw constraint units across resets.
- The penalty starts at `10*max(1,|f0|)/max(1,||c0||^2/2)`, clipped to `1e-8..1e8`. It grows tenfold when the violation fails to halve and refuses above `1e14`.
- Each multiplier update is clamped to `max(10*|previous|, 10*max(1,|f0|,||grad f0||inf))`.
- If a subproblem diverges while infeasible (the penalty is too weak for a nonconvex objective), the penalty grows tenfold. The stage then restarts from the best feasible point seen, keeping the multipliers.
- A diverging feasible search is refused as unbounded.
- The BFGS inverse Hessian warm-starts the next stage.

Local iterations are capped at 1..5,000 per start; each stage receives at most `max(40, max_iterations/8)`.

A point passes the first-order certificate only at the returned point, using scales that do not depend on the start:

- Stationarity is `||grad L||inf/max(1,|f(x)|)` in scaled coordinates.
- Violation of constraint i is divided by `max(1, max_j |dg_i/dx_j|*max(1,|x_j|))`, the constraint magnitude at the current point.
- Complementarity is `|lambda_i*min(g_i,0)|/max(1,|f(x)|)`.

Before M3 was fixed, `(x-1e6)^2` from `x=0` returned x=999977 because the objective was divided by its starting value `1e12`. Feasible, complementary points whose gradient test fails at the rounding floor (for example `|x|~1e6` with a start-derived variable unit of `1e6`) may instead pass the affine-invariant Newton-decrement test. It requires a positive-definite reduced Hessian and `r^T R^-1 r <= 2*tol*max(1,|f|)`; the result reports `stationarity_test` and `newton_decrement`. Final active constraint and bound gradients determine numerical multipliers by sign-constrained least squares. At a bound, the one-sided derivative uses the second-order three-point formula, so bound multipliers are accurate to finite-difference truncation, not first order.

Certified points are then checked twice before they can be returned:

1. Second-order probe. A finite-difference Lagrangian Hessian is reduced to the tangent space of strongly active constraints. Three kinds of direction are probed: negative-curvature directions (saddles such as the origin of `max xy`), flat directions with a nonzero reduced gradient (linear or unbounded descent), and the Newton step. Nonlinear active constraints are restored with up to three Gauss-Newton steps. A probe counts only when an evaluated point is feasible (raw violation `<= tol`) and lowers the objective by more than the slack `10*tol*max(1,|f|) + tol*sum|lambda|`.
2. Post-condition. Every evaluation is tracked, and the point may not be worse than the best feasible point evaluated in the local run, the start included, beyond that slack.

A failed check continues the method of multipliers from the better point with a tenfold penalty, up to eight times. After that the run is reported as `worse_than_best_feasible`, or as unbounded once the point passes `1e10` scaled units. The selected optimum is also compared with the best feasible point of the whole call. If it is worse, one local solve restarts from that point. If the result is still worse, the call refuses with `optimize.not_converged`; the details carry `best_feasible`, `best_trial`, the failed candidate and `local_runs`.

Unbounded problems refuse as `optimize.not_converged` with `reason: optimize.unbounded`, never `input.out_of_range`.

Nelder-Mead uses reflection, expansion, contraction and shrink, with up to two shrinking-simplex restarts. When its simplex test passes but the gradient certificate does not, the next pass uses a 100x tighter inner tolerance. DE is `DE/rand/1/bin`, mutation factor 0.8, crossover probability 0.9, population 5..200 and generations 1..1,000, followed by local polishing. Multistart uses 1..64 seeded starts and reports distinct certified local optima and a `local_runs` list with each run's converged flag and status. Global and multistart modes require finite bounds. DE's search penalty selects promising points; the final multiplier solve still must satisfy the certificate.

Coordinates are scaled by initial magnitudes or finite bound half-widths and reported as `variable_scales`. `objective_scale` and `constraint_scales` report the current-point units used by the certificate. Raw feasibility is reported separately. Right-hand-side sensitivities undo constraint and orientation scaling. Nonconvergence returns `optimize.not_converged`, `converged:false`, and the best candidate and diagnostics in error details.

Finite bound half-widths have no absolute scale floor. Shared finite-difference steps are also capped at a quarter of the bound width, leaving space for a three-point one-sided stencil. Tiny intervals therefore retain local derivative information rather than using a secant across the whole interval. If no proposed neighbor is distinguishable from the current coordinate in floating-point arithmetic, differentiation refuses instead of reporting a zero derivative.

`convergence.csv` records the local merit and residual/spread. KKT conditions with a probed second-order check are still local conditions; they do not prove global optimality, particularly for nonsmooth or nonconvex models.

## Linear programming and integer variables

`linprog` accepts JSON coefficient lists `c`, `A_ub`, `b_ub`, `A_eq`, `b_eq`, and bound pairs. The default bounds are nonnegative; `null` endpoints allow free variables. Up to 200 variables and 200 original constraints are allowed. Nonzero coefficients must lie in `1e-8..1e8`, with combined dynamic range at most `1e12`; bounds have magnitude at most `1e8`. Rescale problems outside these limits.

The algorithm is dense two-phase simplex with Bland's entering/leaving rule and a 20,000-pivot cap per phase. Bounds become nonnegative standard-form variables. Row scaling improves arithmetic. After phase I, the basic values are recomputed from the original normalized rows with one step of iterative refinement; the refined values are kept only when they reduce the basis residual. Then the artificial sum is classified:

- A positive residual with a verified Farkas certificate is infeasible.
- A residual within the forward-error bound `64*m*eps*cond(B)*max(|b|,|B||x_B|)`, capped at `1e-9*m*max(1,|b|)`, is roundoff of a feasible basis. Its artificials are zeroed, and phase II plus the original-coordinate check proceed.
- A larger positive residual without a proof refuses as numerically ambiguous.

The old exact-zero test refused feasible problems whose artificial sum was `-6.7e-16`, 147 of 3,000 small-integer LPs with equality rows in the regression family. Original-coordinate primal feasibility is checked using floating-point accumulation bounds. An unbounded result requires a feasible point and verified improving ray. Infeasible and unbounded problems return error families `optimize.infeasible` and `optimize.unbounded`, with status and proof data in details.

Each pivot rebuilds the tableau from its basis with iterative refinement. The ratio test uses every positive coefficient resolved beyond its solve-error bound, including small coefficients created by row scaling. Redundant-row removal selects independent original equations supporting the retained basis. Reduced-cost entry tests use accumulation and solve-error bounds proportional to the objective coefficients, without an absolute threshold. Final dual checks refine the multiplier solve and propagate its residual and rounding error into reduced costs.

Unbounded directions are reconstructed from the unscaled basis with standard-library `Fraction` arithmetic, treating the converted input coefficients as exact binary rationals. Every original homogeneous constraint, variable-bound sign and objective improvement must pass exactly. The proof includes `ray_exact` as rational strings alongside the floating-point `ray` for display. A small negative component cannot certify an unbounded direction for a nonnegative variable. Exact elimination shares the request budget. Unresolved certificates refuse as `optimize.not_converged`.

Optimal results include the primal point, objective, slacks, equality residuals, dual/shadow prices and reduced costs. RHS ranges vary one row at a time with the basis fixed. They are omitted for redundant rows. They do not describe simultaneous perturbations.

`integer` is a JSON list of up to 30 zero-based indices. Integer bounds are tightened by ceiling/floor before branch and bound. Rounded candidates are rechecked against original constraints and bounds. Best-bound search shares the clock and permits 1..10,000 nodes. Reaching a cap returns an error with incumbent, bound and absolute gap when available, never an unproved optimal status. Only the node cap (`/node_limit`) and the clock (`/time_limit`) are reported as an incomplete search. A node LP or candidate refusal keeps its own message and pointer, prefixed "integer search stopped:", with nodes, incumbent, bound and gap added. An example is an unbounded relaxation at `/integer`. An unbounded continuous relaxation does not itself prove integer unboundedness. LP dual diagnostics at an integer incumbent are not integer shadow prices.

## Root finding

`root` accepts one scalar residual or a semicolon/newline-separated square system with at most 12 variables/equations. A scalar bracket must change sign or contain an exact endpoint root. `auto` can search up to 60 expanding intervals, then use damped Newton if no bracket is found. Supplied brackets constrain starts, local solves, finite differences and returned roots, including multistart. An initial point outside the bracket is clipped into it. `scan` divides a bracket into 2..2,000 intervals and reports roots at samples or sign changes; tangent roots may be missed. Up to 64 seeded starts search systems or scalar problems, without a completeness guarantee.

Bisection checks the residual, not merely the bracket width, so a discontinuity cannot pass solely because its interval shrank. Secant is damped. Square systems use row/column-equilibrated Newton, Richardson finite differences, residual-norm backtracking, and a Levenberg-Marquardt fallback. Singular or numerically unresolved system Jacobians refuse. Local solves are capped at 2,000 iterations.

Final certification checks both `abs(F)/max(1,abs(F(initial)))` and a Jacobian-local residual scale `abs(J) @ (abs(x) + 1e-15*width/tol)` against the tolerance. These residual checks alone do not establish forward accuracy for a system. Newton stopping also requires each correction to be below `tol*abs(x) + 1e-15*width`. Final certification propagates an equation-rounding estimate through the equilibrated inverse Jacobian and adds it to the correction. The resulting `forward_error_scaled_inf` must pass the tolerance. Nearly dependent systems refuse when this accuracy cannot be resolved.

The width is the bracket, a finite bound interval, or `max(1,abs(initial))`. With the old `max(1,abs(x))` scale, small roots were only absolutely accurate: `(x-1e-6)(x+1)` returned `9.98e-7`, and `x^2-1e-10` returned `9.9987e-6`. Each root reports `estimated_relative_error`, one Newton correction relative to `max(abs(x), absolute_floor)`, and `absolute_floor`. An exactly zero residual, such as `x^2` at `x=0`, can still return with a warning when the Jacobian is singular there. A nonzero residual at a singular Jacobian refuses. Raw and equilibrated Jacobian condition estimates are reported. These are local finite-difference estimates, not interval-arithmetic proofs. Numerical nonconvergence uses `optimize.not_converged`.

## Nonlinear fitting and statistical support

`fit` takes a model, initial values for at most 12 parameters, and data as JSON columns/rows or a CSV path. Independent columns are named by `independent`, default `x`; the dependent column defaults to `y`. Parameter plus independent names total at most 16. There must be more observations than parameters, with 2..20,000 rows. Optional `sigma` contains one positive standard deviation per row. CSV files go through `JobHandle.input_file` and are read only from its checked copy, capped at 4 MB. CSV cells use the same strict decimal-literal grammar as expressions and the DOE response file (`decimal` in `_common.py`), not Python `float()`. Digit separators (`1_000`), non-ASCII digits (fullwidth `６`), `nan`/`inf`, hexadecimal and embedded spaces refuse with the pointer `/data_file/<row>/<column>`. The budget is counted per vectorised model call, so 20,000-row fits of typical models finish well inside the default 10 s.

The solver is bounded Levenberg-Marquardt with diagonal damping, accepted residual reduction, and Richardson finite differences. Up to 64 seeded starts select the best certified weighted fit. Damping updates and iterations are capped. The final scaled projected-gradient certificate must pass the requested tolerance; a small step alone does not establish convergence. Nonconvergence is an error.

The solver divides sigmas by their minimum before optimization, so multiplying every sigma by a common positive factor leaves stopping, parameters and relative-sigma covariance unchanged. Reported chi-square and absolute-sigma covariance restore the supplied sigma scale. Before accepting a stationary fit, a finite-difference Hessian of the full squared-error objective checks curvature over parameters not held by a resolved bound multiplier. This includes residual curvature omitted by `J^T J`. Negative or unresolved curvature refuses as `optimize.not_converged` at `/model`; no covariance is returned for that candidate. The eigenvalue threshold scales with the Hessian norm, without an absolute floor. The certificate reports the smallest checked eigenvalue. This is a local curvature check, not a global minimum guarantee.

For the weighted residual Jacobian `J`, covariance is `(J^T J)^-1` when `absolute_sigma=True`, or that matrix multiplied by `chi_square/(n-p)` otherwise. SVD computes covariance without explicitly forming an inverse of the normal matrix. A singular-value ratio at or below `1e-10` refuses covariance; condition above `1e6` warns about parameter identifiability. Active bounds warn that symmetric intervals may be unreliable. Outputs include standard errors, correlations, local Student-t 95% intervals, RMS, R-squared/adjusted R-squared, the largest absolute residual and its row/coordinates, and Durbin-Watson in supplied row order. `fit.csv` contains inputs, observations, fitted values and residuals.

The Student quantile uses a regularized incomplete beta function, a bounded modified-Lentz continued fraction with convergence checks, bracketing and bisection. The same incomplete beta implementation computes F survival probabilities for DOE. The numerical functions are checked against NIST table values and SciPy. Confidence intervals are approximate local nonlinear-model intervals, not exact finite-sample guarantees. Undefined statistics are `null` rather than nonfinite values.

## Sensitivity and uncertainty

`sensitivity` accepts a model, nominal records, optional standard uncertainties and a symmetric positive-semidefinite correlation matrix.

Local sensitivities use central differences at `h`, `h/2` and `h/4` with two Richardson levels. The derivative is the finer Richardson value. Its error estimate is the difference between the two Richardson values plus a rounding term `8*eps*max|f|/h`, reported per variable as `error_estimate` and `relative_error_estimate`. The step scales with the variable:

- `h = 1e-3*|x|` (or `1e-3*u`, or `1e-3`, at `x=0`).
- With `u>0`, `h` is limited to `max(0.1*u, 1e-6*|x|)`, so the stencil stays inside the uncertainty region without dropping into rounding noise.
- The step shrinks tenfold, at most four times, when a trial leaves the model's domain or the error estimate exceeds `1e-6` relative to `max(|g|, |f|/typical)`.
- A derivative that stays unresolved refuses with `optimize.not_converged` at `/nominal/<i>`, so it is never returned silently.

The old fixed step of `1e-3` for `|x|<1` returned `-1.264e7` for `d(1/x)/dx` at `x=0.0006`; the exact value is `-2.778e6`. It also refused `sqrt(x)` at `4e-4`.

Derivatives are required only by GUM and second-order corrections. For Monte Carlo and Sobol they are optional diagnostics. If they fail, `sensitivities`, `gum` and `ranking` are null, `local_derivatives_unavailable` gives the reason with a warning, and the headline value is the sampled standard deviation. Sampling never depends on derivative success. Nonsmooth expressions still warn. Results include derivatives, magnitudes, elasticities `x_i*g_i/y`, standardized sensitivities, uncertainty contributions, and ranking by `abs(g_i*u_i)`. Undefined elasticities are null.

First-order GUM propagation is `u_y^2=g^T C g`, including correlations. Optional second-order corrections are `tr(HC)/2` for the mean and `tr(HCHC)/2` for variance; these assume multivariate normal inputs and a local Taylor model.

Each diagonal and mixed Hessian entry uses central differences at three step sizes and two Richardson estimates. Its error includes their difference and an output-rounding term proportional to `eps*max(abs(f))/(h_i*h_j)`. The acceptance scale uses derivatives and variable scales, excluding constant output offsets. Up to five attempts double a rounding-dominated step or halve a truncation-dominated or invalid step. An unresolved second derivative refuses at `/nominal/<i>` with `optimize.not_converged`; it cannot silently produce zero second-order uncertainty. Per-row Hessian error estimates accompany the sensitivity step checks.

Monte Carlo supports normal `(mean,std)`, uniform `(lower,upper)`, triangular `(lower,mode,upper)`, and lognormal `(underlying_log_mean,underlying_log_std)` marginals. Specify records such as `x,normal,2,0.1` or JSON `{"x":["normal",2,0.1]}`. Correlated simulation supports normal marginals only. Model evaluations are vectorized and support 100..200,000 samples. A model that is undefined on any sampled input refuses instead of silently discarding samples.

Simulation returns mean, standard deviation, empirical 2.5/50/97.5 percentiles, a 95% coverage interval, and the mean standard error. Interval endpoint Monte Carlo errors use ten independent batches and are approximate, especially for small batches or distributions with atoms. Standard-deviation error uses a normal-output approximation, explicitly labelled. A discrepancy warning triggers when means differ by more than three Monte Carlo mean errors plus 5% of the larger standard uncertainty, or standard deviations differ by more than 10%.

Sobol sensitivity requires independent inputs and positive sampled output variance. It uses Saltelli first-order and Jansen total-effect estimators from two independent sample matrices and one mixed matrix per input. Total model samples, including local derivatives, cannot exceed 200,000. Estimates and approximate sampling errors are reported without clipping negative or greater-than-one estimates. They are Monte Carlo estimates, not exact indices.

## Design of experiments and response surfaces

`doe` accepts 1..6 factor ranges as JSON or records such as `x,0,10;y,1,3`. Both coded and natural values appear in `design.csv` and data. Designs include:

- Full factorial with 2..10 equally spaced levels per factor, provided by `levels="[3,4]"`; at most 4,096 total runs including centers.
- Positive regular two-level fractions with requested resolution III/IV/V. Default construction chooses the smallest regular fraction satisfying the resolution; explicit generators use factor letters, such as `D=ABC`. Output includes complete defining words and all-order aliases for main and two-factor terms. Impossible requested resolutions refuse.
- Box-Behnken with 3..6 factors, pairwise edge-midpoint construction, and requested center replicates. For three factors the noncenter set has 12 points. Higher-dimensional construction uses every factor pair, not a reduced catalog design.
- Central composite face-centered, circumscribed and inscribed designs. Rotatable alpha is `(2^k)^(1/4)`. Face-centered alpha is one. Circumscribed star points can extend beyond factor ranges, as stated in the result; inscribed designs rescale all points to stay inside them.
- Seeded Latin hypercube sampling with 1..32 maximin candidates, shifted Halton, and digitally shifted Sobol sequences. Space-filling counts are 2..4,096. Maximin LHS with multiple candidates is capped at 1,024 rows. Sobol balance requires a power-of-two count and otherwise warns.

Sobol direction-number construction uses six fixed primitive-polynomial configurations `(degree, coefficient mask, initial numerators)`: `(0,0,())`, `(1,0,(1,))`, `(2,1,(1,3))`, `(3,1,(1,3,1))`, `(3,2,(1,1,1))`, `(4,1,(1,1,3,3))`. The implementation uses a 32-bit Gray-code recurrence and a seeded digital shift. These are explicit sequence choices, not a claim to reproduce every Sobol library's initialization table. Direction numbers are not universal constants; sequence implementations can choose different values, as discussed in [Harase's comparison](https://arxiv.org/abs/1809.03122).

Responses may be a JSON list, a one-column numeric CSV/TXT read through the job input policy, or an expression evaluated in natural coordinates. Only one source may be supplied. Linear, interaction or quadratic regression uses coded variables. The SVD rank/conditioning check refuses unidentifiable designs. Reported diagnostics include coefficients, standard errors, Student intervals, SSE/SST, R-squared/adjusted/predicted R-squared, PRESS, overall ANOVA, and replicate-based lack-of-fit when its degrees of freedom and pure error permit it.

Saturated fits return estimable coefficients but null inferential statistics. Perfect fits use null F statistics with reasons rather than infinity. A quadratic's Hessian eigenvalues classify its unique stationary point; a singular Hessian has no unique reported stationary point. Enumerating stationary candidates on all box faces gives the fitted quadratic's box extrema. Main and two-factor effects are twice their coded coefficients. These fitted extrema describe the response surface, not a validated physical optimum.

## Verification and references

Tests exercise the actual FastMCP gateway and strict JSON serialization, tool counts/descriptions, unique parameter descriptions, hostile syntax, invalid bounds and names, resource limits, no-root and singular cases, and the absence of delegated code execution. Numerical suites include:

- Rosenbrock `(1,1), f=0`; all four Himmelblau intersections; Rastrigin's nonnegative terms at the origin; box optima and linear constraints with hand-derived KKT points.
- The continuous spring objective near `0.01266523`, the continuous pressure-vessel problem near `5885.33`, and the cantilever objective derived from `x_i proportional to c_i^(1/4)`. The pressure-vessel test explicitly uses continuous thicknesses, not the discrete manufacturing grid. [Yang, Huyck, Karamanoglu and Khan](https://arxiv.org/abs/1403.7793) analyze the benchmark's global-optimality issues.
- Hundreds of random LPs compared with `scipy.optimize.linprog`, plus Beale cycling, diet, transportation, free/fixed variables, infeasibility proofs, rays, integer knapsack, and close-contradiction regressions.
- Polynomial roots, Kepler, independent Colebrook bisection, square nonlinear systems, equation rescaling and singular Jacobian refusal.
- Noiseless exponential, power, logistic and sinusoidal fits; seeded noisy parameter/covariance comparisons to `curve_fit` for both sigma conventions; degenerate-parameter refusal.
- Analytic GUM derivatives/moments, Monte Carlo convergence, independent-input Sobol indices, exact run counts, orthogonality, CCD rotatability, response-surface coefficients and hand-calculated ANOVA/PRESS.
- Review regressions (`tests/test_optimize_review_fixes.py`):
  - The `max xy, x+y<=10` saddle case, in box and half-open bounds.
  - 40 bilinear maximizations with the analytic optimum `a*c^2/4`.
  - 200 random strictly convex QPs and concave quadratic maximizations with linear rows, against an exact KKT active-set oracle cross-checked with SLSQP. There must be no wrong success.
  - Unbounded refusals.
  - `(x-1e6)^2` from three starts, and the current-point constraint scale.
  - Rosenbrock scaled by `1e-3..1e6` and translated by up to `1e6`.
  - Best-trial and multistart budget sharing, and second-order bound multipliers.
  - The two LP repros, and 3,000 seeded small-integer equality LPs against HiGHS with fewer than 0.1% refusals.
  - Small-root relative accuracy, and the exact root at the start.
  - 20,000-row fits, and strict CSV cells.
  - The reciprocal, log and sqrt near-zero sensitivity cases, plus a power-law family `x^p` at scales `1e-6..1e6` against `p*x^(p-1)`.
- Second review regressions (`tests/test_optimize_review2.py`) reproduce all nine findings through the gateway. Fraction and Decimal oracles cover tiny bounded minima, small LP pivots and reduced costs, destructive expression underflow, nearly dependent root systems, bracketed multistart, stationary fit maxima and saddles, uniform sigma scaling, and unresolved Hessian cancellation. Related cases check normal small values, legitimate zeros, absolute covariance scaling and resolved mixed Hessians.

Design conventions follow the [NIST central composite](https://www.itl.nist.gov/div898/handbook/pri/section3/pri3361.htm) and [Box-Behnken](https://www.itl.nist.gov/div898/handbook/pri/section3/pri3362.htm) descriptions. Student checks use the [NIST critical-value table](https://www.itl.nist.gov/div898/handbook/eda/section3/eda3672.htm). Statistical tails and randomized comparisons use SciPy only in tests. Algorithms here are independent implementations; no source from another company project is used.

## Scope limits

There is no symbolic or automatic differentiation, automatic unit algebra, expression execution through Python, stochastic global-optimality guarantee, proof that root scans find every root, nonlinear mixed-integer solver, multi-objective Pareto solver, MIP beyond 30 integer variables, arbitrary nonnormal correlated copula, robust/heteroscedastic response-surface inference, or design-code approval. Ill-scaled or unresolved problems refuse rather than manufacture zeros, frequencies, covariance, convergence or finite substitutes for infinity.

## Module ownership

The six tool modules own their algorithms and certificates. `_expr.py` owns language invariants; `_common.py` owns bounded inputs, one request budget and the result contract; `_numerics.py` owns finite differences; `_stats.py` owns distribution math. This follows the existing domain registration pattern and requires no shared-layer change. A design comparison favored these procedural modules over six `Problem.solve` wrappers; both designs contributed the invariant that solving and certification share one budget.
