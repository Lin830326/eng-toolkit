# control domain: design notes

Scope: a new `control` domain with six classical-control tools for single-input single-output (SISO) linear time-invariant systems.
Branch `feat/control-domain`, cut from main at `1f09baf`. Files touched: `eng_toolkit/domains/control/`, `tests/test_control_*.py` +
`tests/control_testing.py`, this folder, one README section. Nothing in `pyproject.toml`, `uv.lock`, `tests/test_gateway.py`, the
shared layer or another domain.

Tools: `control_analyze`, `control_response`, `control_frequency`, `control_feedback`, `control_pid_tune`, `control_discretize`.

Ground rules followed: clean-room (textbook definitions and published tuning formulas only, no code or text from another project);
**numpy and the standard library only at run time** (`DOMAIN.engines = ("numpy",)`; `numpy` is already a core dependency; no new
dependency, no `engines.py`, no external program); scipy and python-control are imported **only in tests**, behind
`pytest.importorskip`, as independent references (a test greps the package to make sure it stays that way); tools never call an LLM;
**nothing the caller sends is executed** (no `eval`, `exec`, `compile`, no import by name; polynomial text goes through the small
parser below); every result says `review_status: "unreviewed"`; no NaN or infinity can reach a result.

## Input formats

* **Polynomials** are coefficient lists in **descending** powers as JSON text, `"[1, 3, 2]"` = `s^2 + 3 s + 2`, or small polynomial text,
  `"s^2 + 3 s + 2"`, `"(s+1)(s+2)^2"`, `"2e-1 s^2 + 3*s + .5"`.
* **Grammar** of the text (own recursive-descent parser in `_poly.py`; the tokens are numbers, the one variable, `+ - * ^ **`, parentheses):

  ```
  expr  := term (('+' | '-') term)*
  term  := ('+' | '-')* power ( '*' power | power )*      juxtaposition only before a variable or '(':  3 s,  (s+1)(s+2)
  power := atom ( ('^' | '**') INTEGER )?                  INTEGER 0..40, unsigned, no decimals
  atom  := NUMBER | VARIABLE | '(' expr ')'
  ```

  `VARIABLE` is `s` for a continuous model and `z` for a discrete one (the other letter is refused with a pointer to `domain`).
  Everything else is `input.invalid` with a pointer to the two accepted forms: names (`sin`, `abs`, `pi`, `e`), calls, attributes,
  `__`, strings, commas, semicolons, `%`, `//`, `@`, unicode look-alikes, `s^2.5`, `s^-1`, `s^2^3`, two numbers in a row (`1 3 2`,
  which would silently be 6), unbalanced or more than 40 deep parentheses. A text longer than 5000 characters is `input.out_of_range`
  and is never echoed back. The parser never calls Python's `eval`/`exec`; it is checked by tests with `__import__('os')...`, lambda,
  `open(...)`, attribute and dunder inputs, and by a source grep.
* **State space** (`control_response` only): `state_a` n x n, `state_b` n x 1 (or a flat list), `state_c` 1 x n (or a flat list),
  `state_d` a number, as nested JSON lists. More than one input or output (MIMO) is refused (`input.invalid`).
* **Continuous or discrete:** the `domain` parameter (`continuous` default, `discrete`) and `sample_time` (a time with a unit). A
  `sample_time` with `domain='continuous'` is `input.invalid`; `domain='discrete'` without one is `input.missing`; zero or negative is
  `input.invalid`; outside 1e-9 .. 1e9 s is `input.out_of_range`.

## Limits (one rule, stated here and in the errors)

| What | Rule | Error |
|---|---|---|
| order of any polynomial, also of a product the tool forms (open loop, characteristic polynomial) | at most 40 | `input.out_of_range` |
| coefficient | finite; zero, or magnitude within 1e-12 .. 1e12 (rescale time or gain otherwise) | NaN, infinity, a non-number: `input.invalid`; outside the magnitude range (1e308, 1e-300, 1e13): `input.out_of_range` |
| denominator leading coefficient | not 0 (`[0, 1, 2]` is refused: drop the zero, the order is the count minus one) | `input.invalid` |
| numerator | not the zero polynomial; leading zeros are dropped | `input.invalid` |
| text / list size | at most 5000 characters | `input.out_of_range` |
| simulated samples | `samples` 11 .. 100 000; a `t_final` that needs more (discrete: `t_final / T`) is refused | `input.out_of_range` (`input.invalid` below 11) |
| time quantities (`t_final`, `delay`, `time_constant`) | 1e-9 .. 1e9 s (pid: 1e-6 .. 1e7 s) | `input.out_of_range`, `units.*` for a missing or wrong unit |
| frequency (`frequency_min/max`) | 1e-6 .. 1e6 rad/s, discrete: up to the Nyquist frequency pi/T | `input.out_of_range` |
| table points | `points` 10 .. 20 000 | `input.out_of_range` |
| Pade order | 1 .. 10 | `input.out_of_range` |
| properness | `control_response`, `control_frequency`, `control_discretize` need numerator order <= denominator order; `control_feedback` needs the **loop** C G H proper (a PID `[Kd, Kp, Ki]/[1, 0]` is fine on a strictly proper plant) | `input.invalid` with advice to add a pole; `control_analyze` accepts an improper G with a warning |

NaN and infinity cannot be typed into a coefficient list (JSON `NaN`, `Infinity` and `1e999` are refused), and a number parameter that
is not finite is `input.invalid`. A result that would be NaN or infinite (a gain of `1e300` times a pole at `1e300`) is refused with
`input.out_of_range` naming the quantity (`qty`/`plain`/`clean` in `_common.py` guard every output). Quantities that are legitimately
unbounded or undefined are **not numbers**: they are the strings `"infinite"`, `"unbounded"`, `"never (...)"`, `"not applicable (...)"`
in `data`, and the key is absent from `values`.

## What each tool computes, and how exact it is

**Exact** here means rational arithmetic on the float coefficients as given (every float is a rational number) or a closed form in
double precision. **Numerical** means floating point with the stated error behaviour. **Approximate** means a model or interpolation.

### `control_analyze`

* poles and zeros: `numpy.roots` (companion-matrix eigenvalues): **numerical**. The residual `|p(r)| / sum |c_k| |r|^k` is checked
  (warns above 1e-8); a root repeated m times is accurate only to about `1e-16^(1/m)`, and the tool warns when two roots are closer
  than 1e-3 (relative). Natural frequency `wn = |p|`, damping `zeta = -Re p / |p|`, time constant `tau = -1/Re p`; discrete poles through
  `s = ln(z)/T` and `tau = -T/ln|z|`.
* **stability: exact.** Continuous: the Routh-Hurwitz array is built in `fractions.Fraction` and shown (`array`, `first_column`); the
  zero-row rule uses the auxiliary polynomial. Routh's array has degenerate cases (a zero first element after a zero row) that need a
  symbolic epsilon, so the **counts** of right-half-plane, imaginary-axis and left-half-plane roots come from a second exact method with
  no special cases: with `p(jw) = P(w) + j Q(w)`, `gcd(P, Q)` carries the axis roots (counted with multiplicity and as distinct, by Sturm
  sequences) and the Cauchy index of `Q/P` gives `N_left - N_right`. The array's own count is compared with it
  (`array_agrees_with_cauchy_count`) and the array is flagged inconclusive (about 1.3 % of random test polynomials, all with symmetric
  roots) when it hits the degenerate case. Discrete: the **Jury table** in rational arithmetic (built only up to order 12: its entries double in bit length every row, 29 s at order 16 and hopeless at 40, found by a test; above that the table is omitted with a note and the verdict rests on the exact count below) (`p(1) > 0`, `(-1)^n p(-1) > 0`,
  `|a0| < an`, the row conditions) with every inequality shown, plus an exact count of roots inside / on / outside the unit circle through
  the bilinear map `z = (1 + w)/(1 - w)` and the same half-plane count. Verdict: `stable`; `marginally stable` (axis roots, all simple);
  `unstable` (a root outside, or a **repeated** root on the boundary, which the exact count detects by multiplicity).
  The exact counts are cross-checked against the numerical roots; a disagreement (tolerance 1e-7) means an ill-conditioned polynomial,
  is reported as a warning, and the exact count is the one used.
* DC gain: from the lowest-order coefficients (exact; `"infinite"` for an integrator, `0` for a zero at the origin); system type =
  net integrators. Relative degree, minimum phase (exact count of zeros in the closed right half plane / on or outside the unit circle).
* dominant pole: the root(s) with the largest real part (continuous) or magnitude (discrete).
* **Warns, never hides:** coefficient range ratio above 1e8, root residual above 1e-8, clustered roots, near pole-zero cancellations
  (distance below 1e-3 relative) with an extra warning if the cancelled pole is unstable or marginal (the transfer function then looks
  stable but the plant is not internally stable), order above 15 (companion-matrix methods lose digits).

### `control_response`

* A transfer function is realised in controllable canonical form; a state-space model is used as given.
* Continuous time is **exact at the sample instants**: the unit step and the unit ramp are generated by an augmented system whose state
  contains the input (`u' = 0, u(0) = 1`; `u' = c, c' = 0`), so `expm(M T)` propagates the true input rather than a hold approximation;
  the impulse is the initial state `B` (the Dirac through `D` cannot be sampled: warned). Discrete time is the plain recursion. Tested
  against closed forms to about **1e-9** (`1e-12` for the first-order and discrete cases) and against `scipy.signal` to 1e-7.
* `expm` is own code: scaling and squaring with a (6,6) Pade approximant (Golub and Van Loan 11.3.1), compared with
  `scipy.linalg.expm` to 1e-11 on random matrices up to norm 80. The tool also checks `exp(A h)` against `exp(A h/2)^2` and warns above 1e-9.
  The companion realisation is ill-conditioned for high order or widely spread poles (the *model* the matrix represents is perturbed by
  rounding, which no self-check detects); the warnings about order above 15 and the comparison of the final value with the DC gain are
  the safeguards.
* Time vector: unless `t_final`/`samples` are given, the end time is 10 / (slowest decay rate) (or ten times the slowest non-zero pole
  magnitude for an unstable or marginal system; 10 s if every pole is at the origin), the step resolves the fastest pole at 10 samples per
  radian, with 2001 samples at least and 100 000 at most; when the cap binds, a warning says the fast dynamics are coarsely sampled.
* Metrics (step): final value = DC gain of an asymptotically stable system; rise time 10 % to 90 % of the final value; percent
  overshoot `100 (peak - final)/|final|`; settling time = last instant outside the band `+/- tol |final|` (tol = `settling_tolerance`,
  default 0.02); steady-state error = `1 - final value`. Times are **approximate** (linear interpolation between samples, error of order
  step^2; the peak by a parabola through three samples; tested to 1e-4 s and 2e-3 %). Unstable systems report `"unbounded"` (and the
  response is cut once a state exceeds 1e100, with a warning, so no infinity appears); marginal ones report `"never (...)"`. A final value
  of zero makes overshoot, rise time and the band undefined: said so. Impulse: peak, peak time, settling to `tol x peak`. Ramp: the final
  error `t - y` and whether it converged.
* The table is `response.csv` (`time_s,input,output`), the metrics are in `data.metrics`, a preview in `data.preview`.

### `control_frequency`

* `L(jw)` by Horner evaluation of numerator and denominator at `s = jw` (or `z = exp(jwT)`, `w` up to `pi/T`): **numerical** (about
  1e-14 relative; tested against independent complex arithmetic to 1e-9 dB and degrees and against `scipy.signal.freqs`).
  Magnitude in dB from the logarithms of `|N|` and `|D|` separately (floored at 1e-300), so an exact zero or pole on the grid is finite.
* Phase: numerator and denominator are unwrapped separately and the result is anchored at the low-frequency asymptote (-90 degrees per
  integrator, exact for repeated integrators); the tool warns when the phase moves more than 90 degrees between two table points (a
  lightly damped resonance or a coarse grid): the unwrapping can then be wrong by 360 degrees. If `frequency_min` is placed inside
  the dynamics with many roots below it, the anchor can be off by a multiple of 360 degrees (the margins do not depend on it).
* Margins: gain crossover `|L| = 1` with `PM = 180 + angle L` wrapped to (-180, 180]; phase crossover `Im L = 0, Re L < 0` with
  `GM = 1/|L|`; every crossing is refined by bisection on the direct evaluation (**numerical**, converged to rounding). The search uses a
  6000-point log grid two decades wider than the table; two crossings closer together than one grid cell can be missed, a crossover
  above 1e6 rad/s (or beyond a very high loop gain) is outside the range. The primary margin is the crossing with the smallest |margin|;
  all crossings are listed (at most 10 in the data, with the total). Delay margin `PM/w_gc` (continuous only).
* Bandwidth (first -3 dB point below the DC gain) and resonant peak (golden-section refined) need a finite non-zero DC gain.
* **Closed-loop stability** (`closed_loop_check`): the characteristic polynomial `den + num` is counted **exactly** (Routh/Cauchy or
  Jury). The tool also reports what a margin reader would conclude and flags `margins_agree_with_pole_count: false` when they differ.
  Margins are only a reliable test for an open-loop-stable loop with a single gain and a single phase crossover. Two tested cases:
  `L = 2 (s+3)^2/s^3` is **stable** (closed loop `s^3 + 2 s^2 + 12 s + 18`) although its gain margin is 0.75 (-2.5 dB, below 1: lowering
  the gain destabilises it); `L = 0.5/(s - 1)` has no crossover at all (a margin reader finds nothing wrong) but its closed loop
  `s - 0.5` is **unstable**. The pole count is the authority.

### `control_feedback` (continuous)

* Polynomial algebra (`np.convolve`, exact up to rounding): `L = C G H`, `P = Ld + Ln`, `T = Cn Gn Hd / P`, `S = Ld / P`,
  `U = Cn Gd Hd / P`. No common factor is cancelled (a shared factor is a hidden mode: reported as a near pole-zero cancellation).
  A loop with `1 + L(infinity) = 0` (algebraic loop) or `1 + L = 0` identically is refused.
* Closed-loop poles (numerical) and stability (exact, as above). System type, `Kp, Kv, Ka` and the steady-state errors are read from the
  lowest-order coefficients (**exact**; an infinite constant is the string `"infinite"`). Two errors are given: the *actuating* error
  `e = r - H y` (`1/(1+Kp)`, `1/Kv`, `1/Ka` for `H = 1`) and the *tracking* error `r - y = lim s R (1 - T)`, which differs when the sensor
  gain is not 1. Both are `"not applicable"` for an unstable closed loop.
* Sensitivity peak `Ms = max |S(jw)|` and `max |T|`: 6000-point log grid plus golden-section refinement (tested against a closed form
  to 1e-7 and a 400 000-point scan to 1e-6); guaranteed `GM >= Ms/(Ms-1)`, `PM >= 2 asin(1/(2 Ms))` (lower bounds, not the margins).

### `control_pid_tune`

* Process `K exp(-L s)/(T s + 1)`; the four rule sets with the **formulas named in the output** (`data.formulas`) and in the module
  docstring: Ziegler-Nichols open-loop reaction curve (P, PI, PID), Cohen-Coon (P, PI, PID), IMC / lambda (PI, PID with a first-order
  Pade for the delay in the PID case), SIMC (PI; its "PID" for a first-order-plus-delay process has no derivative term, so `Td = 0` and
  a warning says so). `P` for IMC/SIMC is refused: those rules define none. The gains are evaluated exactly (hand-checked for K = 2,
  T = 10, L = 2 in the tests). `data.intended_for` says what each rule is meant for; warnings fire for `L/T` outside the range a rule
  suits (ZN/CC below 0.1 or above 1) and for lambda below 0.8 L or 0.1 T. `L/T` must be in 0.01 .. 100.
* Forms: standard `Kc (1 + 1/(Ti s) + Td s)` or parallel `Kp + Ki/s + Kd s`; both are always in `data`.
* Closed-loop check (unity feedback, **approximate**): the delay is an `(n, n)` Pade approximation of the stated order (default 3, up
  to 10), the derivative gets the filter `Td s/(1 + Td s/N)`, N = 10 by default; the system is normalised by T so coefficients stay of
  order one. Reported: set-point step overshoot, rise and settling time (2 %), stability by exact pole count, a load-disturbance step at
  the plant input (peak deviation, IAE), and gain/phase/delay margins computed with the **exact delay** `exp(-jwL)` (so the margins do not
  depend on the Pade order). The IMC-PI case is derived by hand in the tests (closed form with a first-order Pade, margins `PM = 90 - 0.5
  rad = 61.35` degrees and `GM = pi`).
* **The numbers are starting points** from textbook rules for a model; no actuator limits, noise, windup or model error are included
  (said in `data.caveats`). A Smith predictor and dead-time-dominant design are not offered.

### `control_discretize`

* Methods and exact formulas (also in `data.formula`): `zoh`, `foh` (noncausal triangle hold as MATLAB's `c2d`), `tustin` (optional
  prewarp frequency, exact match of the response there), `forward_euler`, `backward_euler`, `matched` (pole-zero; `r - 1` zeros at infinity
  go to `z = -1`; gain matched at DC, or at a low frequency when G has a pole or zero at the origin: approximate).
* `zoh`/`foh` run through state space and `expm`, then characteristic polynomials: **numerical**, with digits lost to cancellation at high
  order (tested to order 4 against scipy to 1e-8; a leading numerator coefficient below 1e-10 of the largest is dropped as rounding, with a warning). The
  substitution methods are polynomial algebra (`N(s(z)) (q1 z + q0)^n`): exact up to rounding for moderate order.
* Output: monic discrete polynomials, the difference equation, the pole map (each `s`-pole to its `z`-pole by the method's own mapping, with
  `|z|` and stable/unstable on both sides), the exact Jury verdict of the discrete denominator, DC gain comparison, and the
  frequency-response comparison at 1, 5, 10, 25, 50 % of the Nyquist frequency. Warnings: a stable system turned unstable (forward
  Euler with `|p| T` too large), an unstable one turned stable (backward Euler), `|p| T > 1`.

## What is verified, and how

Every expected number in the tests is derived by hand in a comment (arithmetic shown) with the tolerance stated.

* second order `wn = 4, zeta = 0.5`: overshoot `100 exp(-pi zeta/sqrt(1-zeta^2)) = 16.3034 %` (tolerance 2e-3 %), peak time
  `pi/(wn sqrt(1-zeta^2)) = 0.9069 s` (1e-4 s), table against the closed form to **1e-9**; first order `tau = 2`: `y(tau) = 1 - 1/e`
  (1e-12), rise `tau ln 9`, settling `tau ln 50` (1e-4 s); order 6 against partial fractions (1e-9);
* **Routh against numpy roots**: 200 random polynomials of order 2 to 12 (with roots on the imaginary axis, zero rows, a root at the
  origin), 2000 more for the array-versus-Cauchy comparison, 300 random-coefficient polynomials, 1000 random unit-circle cases for Jury and
  the bilinear count; hand arrays for `(s-1)(s+2)(s+3)`, `s^4 + 5 s^2 + 4`, a repeated axis pair;
* `K/(s(s+1)(s+2))`: `w_pc = sqrt 2`, `GM = 6/K` (1e-9), the phase margin from solving `|L| = 1` in the test (1e-7 degrees);
  conditionally stable and open-loop-unstable loops (above); type 0/1/2 error constants; sensor gain `H != 1`; sensitivity peak of
  `1/(s(s+1))` = 1.467890 at 1.168770 rad/s (closed form, 1e-7);
* ZOH of `K/(s+a)` = `K(1 - e^{-aT})/a / (z - e^{-aT})` (1e-13), Tustin = `K T (z+1)/((2+aT)z + (aT-2))`, forward/backward Euler, FOH by
  hand, prewarp exact at `w0` (1e-13), a ZOH model reproduces the continuous step response at the samples (1e-9);
* PI around a first-order plant: closed-loop `wn`, `zeta` predicted from `Ti T s^2 + Ti (1 + K Kc) s + K Kc` (exact), simulated overshoot compared
  with a partial-fraction closed form (2e-3 %);
* against **scipy.signal / scipy.linalg** (expm, step, impulse, ramp via lsim, `freqs`, `cont2discrete` for zoh, foh, bilinear, gbt alpha 0
  and 1) and **python-control** (`margin`, `step_info`, `sample_system`) behind `importorskip` (python-control was installed outside the
  repository for the verification run; those two tests skip where it is absent);
* hostile inputs through `fastmcp.Client` with strict JSON parsing (`parse_constant` raising on `NaN`/`Infinity`): see the table above.

## Not done (on purpose)

No root-locus plot, no MIMO, no nonlinear systems, no state-feedback design, no Kalman filter, no LQR, no Smith predictor, no
delay in the transfer function (only the PID tool models a dead time), no time-delay margins for discrete models, no Nyquist plot data,
no model identification (the FOPDT parameters are inputs), no anti-windup or saturation, no uncertainty analysis, no
discrete-time `control_feedback`, no pole placement, no automatic gain scheduling. The `control_feedback` error constants are for
continuous models only.

## Known limits worth knowing

* Roots of polynomials above order 15 and of polynomials with widely spread coefficients are inaccurate (companion matrix); the exact
  stability counts do not depend on them, but damping, natural frequencies, margins and the time responses do.
* The state-space realisation of a transfer function is the companion form, which is ill-conditioned for high order; the response
  tool warns above order 15 but cannot detect a perturbed model otherwise.
* The crossing search of the margins is a grid scan with refinement: it cannot find two crossings closer together than one cell.
* The unwrapped phase depends on the grid being fine enough near lightly damped resonances.
* Metric times are interpolated between samples; they are as good as the sample step (the tool warns when the step is coarse).
* A discrete-model response reports times at multiples of `T`; rise and settling times are interpolated between samples although
  a sampled signal has no value in between.
