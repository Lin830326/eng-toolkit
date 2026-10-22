"""Local derivatives, covariance propagation, and bounded distribution sampling."""
from __future__ import annotations

import math
from typing import Annotated

import numpy as np
from pydantic import Field

from eng_toolkit.contract import ToolResult
from ._common import Budget, GRAMMAR, Seed, TimeLimit, array, choice, count, invalid, json_value, named, number, records, result, run
from ._expr import Expression, TrialError
from ._server import server


def _marginals(text, names, x, uncertainties):
    if not isinstance(text, str):
        invalid("distributions", "expected distribution records or JSON text")
    if not text:
        return [("normal", [float(v), float(s)]) for v, s in zip(x, uncertainties)]
    if text.lstrip().startswith("{"):
        raw = json_value(text, "distributions")
        if not isinstance(raw, dict) or set(raw) != set(names):
            invalid("distributions", "provide exactly one distribution for each nominal variable")
        entries = [raw[n] for n in names]
    else:
        raw = {}
        for i, row in enumerate(records(text, "distributions")):
            pieces = [p.strip() for p in row.split(",")]
            if len(pieces) < 3 or pieces[0] in raw:
                invalid(f"distributions/{i}", "use name,normal,mean,std; name,uniform,lower,upper; name,triangular,lower,mode,upper; or name,lognormal,log_mean,log_std")
            raw[pieces[0]] = [pieces[1], *[named("v=" + p, "distributions")["v"] for p in pieces[2:]]]
        if set(raw) != set(names):
            invalid("distributions", "provide exactly one distribution for each nominal variable")
        entries = [raw[n] for n in names]
    out = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, list) or not entry:
            invalid(f"distributions/{names[i]}", "expected [family, parameter, ...]")
        family = choice(entry[0], "distributions", ("normal", "uniform", "triangular", "lognormal"))
        params = [number(p, "distributions") for p in entry[1:]]
        if len(params) != (3 if family == "triangular" else 2):
            invalid("distributions", "distribution parameter count is incorrect")
        if family in ("normal", "lognormal") and params[1] < 0:
            invalid("distributions", "standard deviation must be nonnegative")
        if family == "uniform" and params[0] >= params[1]:
            invalid("distributions", "uniform lower must be smaller than upper")
        if family == "triangular" and (not params[0] <= params[1] <= params[2] or params[0] == params[2]):
            invalid("distributions", "triangular requires lower <= mode <= upper and lower < upper")
        out.append((family, params))
    return out


def _draw(rng, marginals, n):
    cols = []
    for family, p in marginals:
        if family == "normal":
            col = rng.normal(*p, n)
        elif family == "uniform":
            col = rng.uniform(*p, n)
        elif family == "triangular":
            col = rng.triangular(*p, n)
        else:
            col = rng.lognormal(*p, n)
        cols.append(col)
    return np.column_stack(cols)


DERIVATIVE_TOLERANCE = 1e-6


def _steps(x, u):
    """Initial central-difference steps: 1e-3 of |x| (or of u, or 1, at x=0), kept
    within 0.1 u of the nominal so the stencil stays inside the uncertainty region,
    but never below 1e-6 |x| where rounding would dominate."""
    typical = np.where(x != 0, np.abs(x), np.where(u > 0, u, 1.))
    h = 1e-3 * typical
    return np.where(u > 0, np.minimum(h, np.maximum(.1 * u, 1e-6 * typical)), h), typical


def _derivatives(f, x, u, second_order):
    """Central differences at h, h/2, h/4 with two Richardson levels per variable.

    The derivative is the finer Richardson value; its error estimate is the difference
    between the two Richardson values plus a rounding term. Steps shrink tenfold (at
    most four times) when a trial leaves the model's domain or the estimate exceeds
    DERIVATIVE_TOLERANCE relative to max(|g|, |f|/typical). Unresolved derivatives raise
    TrialError naming the variable; they are never returned silently."""
    n = len(x)
    h, typical = _steps(x, u)
    center = f(x)
    gradient, checks = np.zeros(n), []
    hessian = np.zeros((n, n))
    eps = np.finfo(float).eps
    for i in range(n):
        step, last = float(h[i]), None
        for attempt in range(5):
            delta = np.zeros(n)
            delta[i] = step
            try:
                values = {k: f(x + k * delta) for k in (1., -1., .5, -.5, .25, -.25)}
            except TrialError as exc:
                last = f"variable {i}: finite-difference stencil leaves the model domain ({exc})"
                step /= 10
                continue
            d1 = (values[1.] - values[-1.]) / (2 * step)
            d2 = (values[.5] - values[-.5]) / step
            d4 = (values[.25] - values[-.25]) / (step / 2)
            r1, r2 = (4 * d2 - d1) / 3, (4 * d4 - d2) / 3
            rounding = 8 * eps * max(abs(v) for v in (center, *values.values())) / step
            error = abs(r2 - r1) + rounding
            scale = max(abs(r2), abs(center) / typical[i])
            if error <= DERIVATIVE_TOLERANCE * scale or scale == 0:
                break
            last = f"variable {i}: Richardson error estimate {error:.3g} exceeds {DERIVATIVE_TOLERANCE:g} relative to {scale:.3g}"
            step /= 10
        else:
            raise TrialError("local derivative not resolved: " + (last or "no valid step"), f"nominal/{i}")
        gradient[i] = r2
        checks.append({"step": step, "coarse": r1, "fine": r2, "richardson": r2,
                       "absolute_step_difference": abs(r2 - r1), "error_estimate": error,
                       "relative_error_estimate": error / scale if scale else 0., "stable": True})
        if second_order:
            s1 = (values[1.] - 2 * center + values[-1.]) / step**2
            s2 = (values[.5] - 2 * center + values[-.5]) / (step / 2)**2
            hessian[i, i] = (4 * s2 - s1) / 3
        h[i] = step
    if second_order:
        for i in range(n):
            for j in range(i):
                a, b = np.zeros(n), np.zeros(n)
                a[i], b[j] = h[i], h[j]
                hessian[i, j] = hessian[j, i] = (f(x+a+b) - f(x+a-b) - f(x-a+b) + f(x-a-b)) / (4*h[i]*h[j])
    return center, gradient, hessian, checks


@server.tool(description="Local Richardson sensitivity, correlated GUM uncertainty, distribution Monte Carlo or independent-input Saltelli/Jansen Sobol indices. " + GRAMMAR)
def sensitivity(
    expression: Annotated[str, Field(description="Scalar arithmetic model, e.g. 'x*y'; '' means missing.")] = "",
    nominal: Annotated[str, Field(description="At most 16 nominal records, e.g. 'x=2;y=3'. Names define expression variables.")] = "",
    standard_uncertainties: Annotated[str, Field(description="Nonnegative standard uncertainties as name=value records; omitted names default to zero, e.g. 'x=0.1;y=0.2'.")] = "",
    correlation: Annotated[str, Field(description="JSON symmetric positive-semidefinite correlation matrix in nominal name order; blank means independent.")] = "",
    distributions: Annotated[str, Field(description='Normal/uniform/triangular/lognormal marginals, e.g. x,normal,2,0.1 or {"x":["normal",2,0.1]}; \'\' uses nominal normals.')] = "",
    method: Annotated[str, Field(description="gum, monte_carlo, or sobol, e.g. 'gum'; Sobol requires independent marginals and reports unclipped sampling estimates.")] = "gum",
    samples: Annotated[int, Field(description="Monte Carlo rows 100..200000; Sobol base rows 100..200000, total evaluations also capped at 200000.")] = 10000,
    second_order: Annotated[bool, Field(description="Include Hessian second-order Gaussian mean/variance corrections; these are local approximations.")] = False,
    seed: Seed = 0,
    time_limit: TimeLimit = "10 s",
) -> ToolResult:
    inputs = dict(expression=expression, nominal=nominal, standard_uncertainties=standard_uncertainties, correlation=correlation,
                  distributions=distributions, method=method, samples=samples, second_order=second_order, seed=seed, time_limit=time_limit)

    def compute(job):
        # Reserve a small, fixed allowance for the local derivative diagnostics.
        # Sampling itself remains capped separately below.
        budget = Budget(time_limit, max_evaluations=202048, max_work=100000000)
        values = named(nominal, "nominal")
        names, x = list(values), np.array(list(values.values()))
        uncertain = named(standard_uncertainties, "standard_uncertainties", required=False)
        if set(uncertain) - set(names):
            invalid("standard_uncertainties", "uncertainty names must occur in nominal")
        u = np.array([number(uncertain.get(n, 0), "standard_uncertainties", 0) for n in names])
        corr = np.eye(len(names)) if not correlation else array(correlation, "correlation", ndim=2, cap=256)
        if corr.shape != (len(names), len(names)) or not np.allclose(corr, corr.T, atol=1e-12, rtol=0) or not np.allclose(np.diag(corr), 1, atol=1e-12, rtol=0) or np.any(np.abs(corr) > 1 + 1e-12):
            invalid("correlation", "expected symmetric correlation matrix with unit diagonal and entries in [-1,1]")
        eigenvalues, eigenvectors = np.linalg.eigh(corr)
        if eigenvalues[0] < -1e-10:
            invalid("correlation", "correlation matrix must be positive semidefinite")
        if not isinstance(second_order, bool):
            invalid("second_order", "expected boolean")
        seed_value = count(seed, "seed", 0, 4294967295)
        method_value = choice(method, "method", ("gum", "monte_carlo", "sobol"))
        exp = Expression(expression, names)
        def evaluate(points):
            points = np.asarray(points)
            bindings = dict(zip(names, points if points.ndim == 1 else points.T))
            output = exp.evaluate(bindings, budget)
            return float(output) if points.ndim == 1 else np.broadcast_to(output, len(points)).copy()
        covariance = corr * np.outer(u, u)
        warnings = []
        # GUM and second-order corrections need derivatives; Monte Carlo and Sobol only
        # report them as optional local diagnostics and never depend on them.
        needs_derivatives = method_value == "gum" or second_order
        try:
            center, g, hessian, checks = _derivatives(evaluate, x, u, second_order)
            derivative_failure = None
        except TrialError as exc:
            if needs_derivatives:
                if exc.param.startswith("nominal/"):
                    invalid(exc.param, str(exc), "optimize.not_converged")
                raise
            center = g = hessian = checks = None
            derivative_failure = str(exc)
        data = {"method": method_value, "names": names, "covariance": covariance, "seed": seed_value}
        standard = None
        if derivative_failure is None:
            variance = max(0., float(g @ covariance @ g))
            shift = float(np.trace(hessian @ covariance) / 2) if second_order else 0.
            correction = float(np.trace(hessian @ covariance @ hessian @ covariance) / 2) if second_order else 0.
            standard = math.sqrt(max(0., variance + correction))
            if not exp.smooth:
                warnings.append("Local derivatives may be unreliable at a nonsmooth point of abs/min/max/floor/ceil/sign.")
            rows = []
            for i, name in enumerate(names):
                rows.append({"name": name, "derivative": g[i], "absolute_sensitivity": abs(g[i]),
                             "elasticity": None if center == 0 else g[i] * x[i] / center,
                             "standardized_sensitivity": None if standard == 0 else g[i] * u[i] / standard,
                             "uncertainty_contribution": float(g[i] * (covariance @ g)[i]), "step_check": checks[i]})
            data.update({"nominal_value": center, "sensitivities": rows,
                         "gum": {"mean": center+shift, "standard_uncertainty": standard, "first_order_variance": variance,
                                 "second_order_mean_shift": shift, "second_order_variance_correction": correction,
                                 "second_order_assumption": "Gaussian inputs; local Taylor approximation" if second_order else None}})
            data["ranking"] = [
                {"rank": rank+1, "name": names[i], "absolute_standard_uncertainty_effect": abs(float(g[i]*u[i]))}
                for rank,i in enumerate(sorted(range(len(names)),key=lambda i: -abs(g[i]*u[i])))
            ]
            if second_order:
                data["hessian"] = hessian
        else:
            data.update({"nominal_value": None, "sensitivities": None, "gum": None, "ranking": None,
                         "local_derivatives_unavailable": derivative_failure})
            warnings.append("Local derivatives and the GUM comparison are unavailable: " + derivative_failure[:200])
        if method_value != "gum":
            n = count(samples, "samples", 100, 200000)
            marginals = _marginals(distributions, names, x, u)
            rng = np.random.default_rng(seed_value)
            if method_value == "monte_carlo":
                points = _draw(rng, marginals, n)
                if correlation and not np.allclose(corr, np.eye(len(names))):
                    if any(f != "normal" for f, _ in marginals):
                        invalid("correlation", "correlated Monte Carlo supports normal marginals only; nonnormal copulas are unspecified")
                    z = rng.standard_normal((n, len(names))) @ (eigenvectors * np.sqrt(np.maximum(eigenvalues, 0))).T
                    points = z * np.array([p[1] for _, p in marginals]) + np.array([p[0] for _, p in marginals])
                y = evaluate(points)
                mc_mean, mc_std = float(np.mean(y)), float(np.std(y, ddof=1))
                # Equal-size independent batches estimate percentile Monte Carlo error
                # without assuming a normal output distribution or extra model calls.
                batches = np.array_split(y, 10)
                batch_quantiles = np.array([np.quantile(batch,[.025,.975]) for batch in batches])
                interval_se = np.std(batch_quantiles,axis=0,ddof=1)/math.sqrt(10)
                data["monte_carlo"] = {"samples": n, "mean": mc_mean, "standard_deviation": mc_std,
                    "quantiles": dict(zip(("0.025", "0.5", "0.975"), np.quantile(y, [.025, .5, .975]).tolist())),
                    "coverage_interval_95": np.quantile(y, [.025,.975]), "mean_standard_error": mc_std/math.sqrt(n),
                    "coverage_interval_endpoint_standard_errors_approx": interval_se,
                    "coverage_interval_standard_error_method": "Standard deviation of the 2.5%/97.5% sample quantiles in ten independent batches, divided by sqrt(10). Approximate and unreliable for small batches or atoms.",
                    "mean_confidence_interval_95": [mc_mean-1.96*mc_std/math.sqrt(n), mc_mean+1.96*mc_std/math.sqrt(n)],
                    "standard_deviation_standard_error_approx": mc_std/math.sqrt(2*(n-1)),
                    "standard_deviation_standard_error_assumption": "Normal output approximation; this standard-error formula is not calibrated for skewed or heavy-tailed responses."}
                if standard is None:
                    standard = mc_std
                elif abs(mc_mean-data["gum"]["mean"]) > 3*mc_std/math.sqrt(n) + .05*max(standard, mc_std) or abs(mc_std-standard) > .1*max(mc_std, standard, 1e-100):
                    warnings.append("Monte Carlo and local GUM estimates disagree; nonlinear response, marginal choices or sampling error may explain the difference.")
            else:
                if not np.allclose(corr, np.eye(len(names)), atol=1e-12):
                    invalid("correlation", "Sobol decomposition requires independent inputs")
                if n*(len(names)+2) + budget.evaluations > 200000:
                    invalid("samples", "Sobol base rows times (variables+2) exceed the shared 200000 evaluation cap", "input.out_of_range")
                a, b = _draw(rng, marginals, n), _draw(rng, marginals, n)
                ya, yb = evaluate(a), evaluate(b)
                var_y = float(np.var(np.concatenate([ya, yb]), ddof=1))
                if var_y <= 0:
                    invalid("expression", "Sobol indices are undefined for a constant sampled response")
                indices = []
                for i, name in enumerate(names):
                    mixed = a.copy()
                    mixed[:,i] = b[:,i]
                    ym = evaluate(mixed)
                    first_terms = (yb-np.mean(yb))*(ym-ya)/var_y
                    total_terms = (ya-ym)**2/(2*var_y)
                    indices.append({"name": name, "first_order": float(np.mean(first_terms)), "total_order": float(np.mean(total_terms)),
                        "first_order_standard_error_approx": float(np.std(first_terms,ddof=1)/math.sqrt(n)),
                        "total_order_standard_error_approx": float(np.std(total_terms,ddof=1)/math.sqrt(n))})
                if standard is None:
                    standard = math.sqrt(var_y)
                data["sobol"] = {"base_samples": n, "indices": indices, "variance": var_y,
                                 "estimator": "Saltelli first order, Jansen total order; Monte Carlo standard errors conditional on estimated variance"}
                warnings.append("Sobol indices and their standard errors are finite-sample estimates; estimates are not clipped to [0,1].")
        budget.check()
        data["evaluations"] = budget.evaluations
        return result(data, "standard_uncertainty", standard, formulas={"GUM": "u_y^2 = g^T C g", "second_order": "For multivariate normal inputs: mean shift=tr(HC)/2; variance correction=tr(HCHC)/2"},
                      assumptions="Plain numeric variables in caller-selected consistent units. Distribution parameters explicitly describe marginals.",
                      validity="Local Richardson derivatives (steps scaled with |x| and u) refuse when the error estimate exceeds 1e-6 relative; GUM is a Taylor approximation; Monte Carlo coverage is empirical. Without GUM, the headline value is the sampled standard deviation.", warnings=warnings)
    return run("sensitivity", inputs, compute)
