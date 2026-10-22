"""Projected BFGS, safeguarded method of multipliers and bounded global searches."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import math
import time
from typing import Annotated

import numpy as np
from pydantic import Field

from eng_toolkit.errors import ToolFailure
from ._common import (Budget, GRAMMAR, Seed, TimeLimit, Tolerance, bounds_array, choice,
                      count, csv_text, invalid, named, number, records, result, run)
from ._expr import Expression, TrialError
from ._numerics import jacobian
from ._server import server

# Scaled coordinates beyond this magnitude are treated as a diverging (unbounded) search.
DIVERGENCE = 1e10
BUDGET_REASONS = ("optimize.time_budget_exceeded", "optimize.evaluation_budget_exceeded")


@dataclass
class LocalResult:
    point: np.ndarray
    value: float
    iterations: int
    converged: bool
    status: str = ""
    inverse: object = None  # BFGS inverse Hessian, reused to warm-start the next stage


def projected_gradient(x, g, lo, hi):
    return x - np.clip(x - g, lo, hi)


def _record(history, value, residual):
    # The convergence log only holds finite, reportable magnitudes.
    if all(math.isfinite(v) and abs(v) <= 1e100 for v in (value, residual)):
        history.append([len(history), value, residual])


def _default_unit(point, value):
    return max(1., abs(value))


def bfgs(function, start, lo, hi, tolerance, iterations, budget, history, unit=_default_unit, warm=None):
    """Inverse BFGS with projected directions, Richardson differences, Armijo backtracking.

    Stationarity is ||projected gradient||_inf <= tolerance*unit(point, value), so the
    test is relative to the objective magnitude at the current point, not at the start.
    Steps are capped at 100*max(1,||z||_inf) scaled units; an iterate beyond 1e10
    scaled units stops the search as diverging.
    """
    x = np.clip(np.array(start, dtype=float), lo, hi)
    f = float(function(x))
    n = len(x)
    inverse = None if warm is None or warm.shape != (len(x), len(x)) else warm.copy()
    flat_steps = 0
    g = jacobian(function, x, lo, hi).reshape(n)
    for iteration in range(iterations + 1):
        budget.check()
        residual = float(np.linalg.norm(projected_gradient(x, g, lo, hi), ord=np.inf))
        _record(history, f, residual)
        if residual <= tolerance * unit(x, f):
            return LocalResult(x, f, iteration, True, "converged", inverse)
        if iteration == iterations:
            break
        # Variables held at a bound by the gradient are fixed; the free block of the
        # inverse Hessian gives the direction for the rest (Bertsekas projected quasi-Newton).
        held = ((x <= lo + 1e-12) & (g > 0)) | ((x >= hi - 1e-12) & (g < 0))
        free = ~held
        direction = np.zeros(n)
        if inverse is None:
            direction[free] = -g[free]
        else:
            direction[free] = -inverse[np.ix_(free, free)] @ g[free]
        direction[(x <= lo + 1e-12) & (direction < 0)] = 0
        direction[(x >= hi - 1e-12) & (direction > 0)] = 0
        # Relative descent test: an absolute floor would reject every quasi-Newton
        # direction once the gradient is small.
        if not float(g @ direction) < -1e-12 * np.linalg.norm(g) * np.linalg.norm(direction):
            direction = -projected_gradient(x, g, lo, hi)
            inverse = None
        size = max(1., float(np.linalg.norm(x, ord=np.inf)))
        radius = size if inverse is None else 100 * size
        length = float(np.linalg.norm(direction, ord=np.inf))
        if length > radius:
            direction *= radius / length
        accepted = False
        step = 1.
        candidate_gradient = None
        for _ in range(60):
            trial = np.clip(x + step * direction, lo, hi)
            displacement = trial - x
            if np.linalg.norm(displacement, ord=np.inf) <= 1e-15 * size:
                break
            try:
                candidate = float(function(trial))
                if candidate < f and candidate <= f + 1e-4 * float(g @ displacement):
                    accepted = True
                    break
                # An objective near a constant offset may round to the same
                # value on both sides of its minimum. Require derivative
                # improvement instead of claiming convergence from equal values.
                if candidate <= f + 32 * np.finfo(float).eps * max(1., abs(f)):
                    probe = jacobian(function, trial, lo, hi).reshape(n)
                    probe_residual = np.linalg.norm(projected_gradient(trial, probe, lo, hi), ord=np.inf)
                    if probe_residual < .8 * residual or probe_residual <= tolerance * unit(trial, candidate):
                        candidate_gradient = probe
                        accepted = True
                        break
            except TrialError:
                pass
            step *= .5
        # Steps accepted only on equal objective values are at the rounding floor; a few
        # in a row mean finite-difference noise, not progress.
        if accepted:
            flat = f - candidate <= 32 * np.finfo(float).eps * abs(f)
            flat_steps = flat_steps + 1 if flat else 0
        if not accepted or flat_steps > 4:
            return LocalResult(x, f, iteration, False, "stalled", inverse)
        if np.linalg.norm(trial, ord=np.inf) > DIVERGENCE:
            return LocalResult(trial, candidate, iteration + 1, False, "diverged")
        new_g = jacobian(function, trial, lo, hi).reshape(n) if candidate_gradient is None else candidate_gradient
        # Secant pair of the reduced problem: gradient changes of variables held on a
        # bound would otherwise corrupt the free block of the inverse Hessian.
        s, y = displacement, np.where(held, 0., new_g - g)
        curvature = float(s @ y)
        if curvature > 1e-12 * np.linalg.norm(s) * np.linalg.norm(y):
            if inverse is None:
                # Shanno-Phua initial scaling keeps BFGS invariant to objective scale.
                inverse = np.eye(n) * curvature / float(y @ y)
            rho = 1. / curvature
            transform = np.eye(n) - rho * np.outer(s, y)
            inverse = transform @ inverse @ transform.T + rho * np.outer(s, s)
        else:
            inverse = None
        x, f, g = trial, candidate, new_g
    return LocalResult(x, f, iterations, False, "iteration_limit", inverse)


def nelder_mead(function, start, lo, hi, tolerance, iterations, budget, history, unit=_default_unit, warm=None):
    """Nelder-Mead with two shrinking-simplex restarts; domain failures rank last."""
    n, total = len(start), 0
    best = np.array(start, dtype=float)
    def score(x):
        try:
            return float(function(x))
        except TrialError:
            return float("inf")
    for restart in range(3):
        simplex = np.tile(best, (n + 1, 1))
        for i in range(n):
            delta = .08 / (restart + 1) * max(1., abs(best[i]))
            simplex[i + 1, i] = np.clip(best[i] + delta, lo[i], hi[i])
            if simplex[i + 1, i] == best[i]:
                simplex[i + 1, i] = np.clip(best[i] - delta, lo[i], hi[i])
        scores = np.array([score(x) for x in simplex])
        for _ in range(max(1, iterations // 3)):
            total += 1
            budget.check()
            order = np.argsort(scores, kind="stable")
            simplex, scores = simplex[order], scores[order]
            diameter = float(np.max(np.abs(simplex - simplex[0])))
            if np.isfinite(scores[0]):
                _record(history, scores[0], diameter)
            if (np.all(np.isfinite(scores)) and diameter <= tolerance
                    and np.ptp(scores) <= tolerance * unit(simplex[0], scores[0])):
                return LocalResult(simplex[0], scores[0], total, True, "converged")
            if np.linalg.norm(simplex[0], ord=np.inf) > DIVERGENCE:
                return LocalResult(simplex[0], scores[0], total, False, "diverged")
            centroid = np.mean(simplex[:-1], axis=0)
            reflected = np.clip(2 * centroid - simplex[-1], lo, hi)
            fr = score(reflected)
            if fr < scores[0]:
                expanded = np.clip(centroid + 2 * (reflected - centroid), lo, hi)
                fe = score(expanded)
                simplex[-1], scores[-1] = (expanded, fe) if fe < fr else (reflected, fr)
            elif fr < scores[-2]:
                simplex[-1], scores[-1] = reflected, fr
            else:
                outside = fr < scores[-1]
                contracted = np.clip(centroid + .5 * ((reflected if outside else simplex[-1]) - centroid), lo, hi)
                fc = score(contracted)
                if fc < (fr if outside else scores[-1]):
                    simplex[-1], scores[-1] = contracted, fc
                else:
                    simplex[1:] = (simplex[1:] + simplex[0]) / 2
                    scores[1:] = [score(x) for x in simplex[1:]]
        best = simplex[int(np.argmin(scores))]
    value = score(best)
    if not np.isfinite(value):
        raise TrialError("no finite objective trial in Nelder-Mead")
    return LocalResult(best, value, total, False, "iteration_limit")


def differential_evolution(function, start, lo, hi, population, generations, rng, budget, history):
    """DE/rand/1/bin, F=0.8, CR=0.9; bounded candidates and deterministic selection."""
    if not (np.all(np.isfinite(lo)) and np.all(np.isfinite(hi))):
        invalid("bounds", "differential_evolution requires finite lower and upper bounds")
    size = max(5, population)
    points = rng.uniform(lo, hi, (size, len(lo)))
    points[0] = start
    def score(x):
        try:
            return float(function(x))
        except TrialError:
            return float("inf")
    scores = np.array([score(x) for x in points])
    for generation in range(generations):
        budget.check()
        for i in range(size):
            choices = np.delete(np.arange(size), i)
            a, b, c = points[rng.choice(choices, 3, replace=False)]
            mutant = np.clip(a + .8 * (b - c), lo, hi)
            mask = rng.random(len(lo)) < .9
            mask[rng.integers(len(lo))] = True
            trial = np.where(mask, mutant, points[i])
            value = score(trial)
            if value <= scores[i]:
                points[i], scores[i] = trial, value
        finite_scores = scores[np.isfinite(scores)]
        if len(finite_scores):
            _record(history, float(np.min(finite_scores)), float(np.ptp(finite_scores)))
    best = int(np.argmin(scores))
    if not np.isfinite(scores[best]):
        raise TrialError("no finite objective trial in differential evolution")
    return points[best]


@dataclass(frozen=True)
class Constraint:
    expression: Expression
    relation: str
    orientation: float


def parse_constraints(text, names):
    constraints = []
    for i, record in enumerate(records(text, "constraints", 20)):
        found = [(relation, record.find(relation)) for relation in ("<=", ">=", "==") if relation in record]
        if len(found) != 1:
            invalid(f"constraints/{i}", "expected expression <= 0, >= 0 or == 0")
        relation, at = found[0]
        if record[at + 2:].strip() not in ("0", "0.0", "+0", "-0"):
            invalid(f"constraints/{i}", "constraint right-hand side must be zero; subtract it in the expression")
        constraints.append(Constraint(Expression(record[:at], names, f"constraints/{i}"), relation, -1. if relation == ">=" else 1.))
    return constraints


class Tracker:
    """Best evaluated points: feasible (raw violation <= tolerance) by objective, and overall."""

    def __init__(self, tolerance):
        self.tolerance = tolerance
        self.overall = None   # (z, F) best feasible over the whole call
        self.local = None     # (z, F) best feasible within the current local run
        self.any = None       # (key, z, F, violation): feasible first, then lowest objective

    def see(self, z, value, violation):
        if violation <= self.tolerance:
            if self.overall is None or value < self.overall[1]:
                self.overall = (z.copy(), value)
            if self.local is None or value < self.local[1]:
                self.local = (z.copy(), value)
            key = (0, 0., value)
        else:
            key = (1, violation, value)
        if self.any is None or key < self.any[0]:
            self.any = (key, z.copy(), value, violation)


class Slice:
    """Temporarily narrow the shared budget so each multistart run gets a fair share."""

    def __init__(self, budget, seconds, evaluations):
        self.budget, self.seconds, self.evaluations = budget, seconds, evaluations

    def __enter__(self):
        b = self.budget
        self.saved = (b.deadline, b.max_evaluations, b.max_work)
        b.deadline = min(b.deadline, time.monotonic() + self.seconds)
        b.max_evaluations = min(b.max_evaluations, b.evaluations + self.evaluations)
        b.max_work = min(b.max_work, b.work + int(self.evaluations * (b.max_work / max(1, self.saved[1]))))
        return self

    def __exit__(self, kind, exc, trace):
        b = self.budget
        b.deadline, b.max_evaluations, b.max_work = self.saved
        if isinstance(exc, ToolFailure) and exc.details.get("reason") in BUDGET_REASONS:
            # Suppress only when this run's share, not the call's budget, is spent.
            return time.monotonic() <= b.deadline and b.evaluations <= b.max_evaluations and b.work <= b.max_work
        return False


@server.tool(description="Min/max by BFGS/Armijo, projected BFGS, safeguarded augmented Lagrangian, Nelder-Mead, seeded DE or multistart; numerical KKT certificate with second-order probes, no global guarantee. Caps: 16 names, 20 constraints, 200k evaluations/100m node operations. " + GRAMMAR)
def minimize(
    expression: Annotated[str, Field(description="Objective formula, e.g. '(x-1)^2'; required, <=2000 characters; minimize unless maximize is true.")] = "",
    initial: Annotated[str, Field(description="Initial variables as name=number records, e.g. 'x=0;y=1'; semicolon/newline separated, <=16 names.")] = "",
    parameters: Annotated[str, Field(description="Fixed dimensionless name=number records, e.g. 'a=2'; '' omits; total variable and parameter names <=16.")] = "",
    bounds: Annotated[str, Field(description="JSON [lower,upper] pairs in initial-name order, e.g. '[[0,5]]'; null is unbounded; '' omits all bounds.")] = "",
    constraints: Annotated[str, Field(description="Up to 20 expression <= 0, >= 0 or == 0 records, e.g. 'x+y-1<=0'; semicolon/newline separated; '' omits.")] = "",
    method: Annotated[str, Field(description="auto, bfgs, nelder_mead, differential_evolution or multistart; auto uses smoothness, bounds and constraints.")] = "auto",
    maximize: Annotated[bool, Field(description="True maximizes the supplied objective; false minimizes it. Returned objective keeps its original sign.")] = False,
    tolerance: Tolerance = 1e-7,
    max_iterations: Annotated[int, Field(description="Maximum local iterations across multiplier stages, e.g. 1000; integer 1..5000.")] = 1000,
    starts: Annotated[int, Field(description="Seeded starts for multistart, e.g. 16; integer 1..64; other methods require default 16.")] = 16,
    population: Annotated[int, Field(description="DE population size, e.g. 40; integer 5..200; used only by differential_evolution.")] = 40,
    generations: Annotated[int, Field(description="DE generations before local polishing, e.g. 200; integer 1..1000; only differential_evolution uses it.")] = 200,
    seed: Seed = 0,
    time_limit: TimeLimit = "10 s",
):
    inputs = dict(locals())
    def compute(handle):
        budget = Budget(time_limit, 200000)
        started = time.monotonic()
        total_seconds = budget.deadline - started
        tol = number(tolerance, "tolerance", 1e-12, .01)
        limit = count(max_iterations, "max_iterations", 1, 5000)
        nstarts = count(starts, "starts", 1, 64)
        pop = count(population, "population", 5, 200)
        gen = count(generations, "generations", 1, 1000)
        rng = np.random.default_rng(count(seed, "seed", 0, 4294967295))
        selected = choice(method, "method", ("auto", "bfgs", "nelder_mead", "differential_evolution", "multistart"))
        values = named(initial)
        constants = named(parameters, "parameters", required=False)
        if set(values) & set(constants):
            invalid("parameters", "fixed parameter duplicates an initial variable")
        names = tuple(values)
        objective = Expression(expression, (*names, *constants))
        cons = parse_constraints(constraints, (*names, *constants))
        x0 = np.array(list(values.values()))
        lower, upper = bounds_array(bounds, names, x0)
        if np.any(lower == upper):
            invalid("bounds", "fixed variables must be moved to parameters; each search interval needs positive width")
        # Missing bounds are true infinities internally; the 1e100 placeholder never
        # becomes an iterate, a finite-difference neighbour or a logged value.
        lower = np.where(np.abs(lower) >= 1e100, -np.inf, lower)
        upper = np.where(np.abs(upper) >= 1e100, np.inf, upper)
        # Solve in comparable coordinates, without changing the caller's numeric units.
        scale = np.maximum(1., np.abs(x0))
        bounded = np.isfinite(lower) & np.isfinite(upper)
        scale[bounded] = np.maximum(1e-12, (upper[bounded] - lower[bounded]) / 2)
        lo, hi, z0 = lower / scale, upper / scale, x0 / scale
        sign = -1. if maximize else 1.
        m = len(cons)
        eq = np.array([c.relation == "==" for c in cons], dtype=bool)
        tracker = Tracker(tol)
        cache = OrderedDict()

        def bindings(z):
            return {**constants, **dict(zip(names, z * scale))}

        def violation_of(G):
            return float(np.max(np.where(eq, np.abs(G), np.maximum(G, 0.)), initial=0.))

        def evaluate(z):
            """Signed objective F and oriented raw constraints G (G<=0 or G==0)."""
            key = z.tobytes()
            if key in cache:
                return cache[key]
            binding = bindings(z)
            F = sign * float(objective.evaluate(binding, budget))
            G = np.array([c.orientation * float(c.expression.evaluate(binding, budget)) for c in cons])
            tracker.see(z, F, violation_of(G))
            cache[key] = (F, G)
            if len(cache) > 64:
                cache.popitem(last=False)
            return F, G

        def vector(z):
            F, G = evaluate(z)
            return np.r_[F, G]

        def unit(z, value=None):
            return max(1., abs(evaluate(z)[0]))

        def certificate(z):
            J = jacobian(vector, z, lo, hi)
            F, G = evaluate(z)
            g, A = J[0], J[1:]
            x = z * scale
            n = len(z)
            fu = max(1., abs(F))
            # Constraint magnitude at the current point: the largest change of g_i when
            # one variable moves by max(1,|x_j|); never the value at the start.
            magnitude = np.maximum(1., np.max(np.abs(A / scale) * np.maximum(1., np.abs(x)), axis=1, initial=0.)) if m else np.ones(0)
            rows, ids, free, norms = [], [], [], []
            for i in range(m):
                norm = float(np.max(np.abs(A[i])))
                if (eq[i] or G[i] >= -10 * tol * magnitude[i]) and norm > 0:
                    rows.append(A[i] / norm); ids.append(("constraint", i)); free.append(bool(eq[i])); norms.append(norm)
            for i in range(n):
                if z[i] - lo[i] <= 10 * tol:
                    row = np.zeros(n); row[i] = -1
                    rows.append(row); ids.append(("lower", i)); free.append(False); norms.append(1.)
                if hi[i] - z[i] <= 10 * tol:
                    row = np.zeros(n); row[i] = 1
                    rows.append(row); ids.append(("upper", i)); free.append(False); norms.append(1.)
            retained = list(range(len(rows)))
            normalized = np.zeros(len(rows))
            for _ in range(len(rows) + 1):
                if not retained:
                    break
                solved = np.linalg.lstsq(np.array(rows)[retained].T, -g, rcond=1e-12)[0]
                bad = [j for j, v in zip(retained, solved) if not free[j] and v < -tol * fu]
                if not bad:
                    normalized[retained] = solved
                    break
                retained.remove(min(bad, key=lambda j: solved[retained.index(j)]))
            stationarity = g + (np.array(rows).T @ normalized if rows else 0)
            raw = normalized / np.array(norms) if rows else normalized
            lam = np.zeros(m)
            for value, (kind, i) in zip(raw, ids):
                if kind == "constraint":
                    lam[i] = value
            violation = np.where(eq, np.abs(G), np.maximum(G, 0.)) if m else np.zeros(0)
            # Complementarity on the feasible side; a positive G is already a violation.
            complementarity = max((abs(lam[i] * min(G[i], 0.)) / fu for i in range(m) if not eq[i]), default=0.)
            cert = {"stationarity_residual": float(np.linalg.norm(stationarity, ord=np.inf) / fu),
                    "constraint_violation": float(np.max(violation / magnitude, initial=0.)),
                    "raw_constraint_violation": float(np.max(violation, initial=0.)),
                    "complementarity_residual": float(complementarity),
                    "multipliers": raw, "normalized": normalized, "rows": rows, "multiplier_ids": ids,
                    "constraint_multipliers": lam, "objective_unit": fu, "magnitude": magnitude,
                    "jacobian": A, "value": F, "constraint_values": G, "lagrangian_gradient": stationarity * np.ones(n),
                    "stationarity_test": "gradient", "newton_decrement": None}
            cert["passed"] = max(cert["stationarity_residual"], cert["constraint_violation"], cert["complementarity_residual"]) <= tol
            # Feasible and complementary but the gradient test failed (e.g. at the rounding
            # floor of a large |x|): the affine-invariant Newton-decrement test may certify it.
            cert["near"] = (not cert["passed"]
                            and max(cert["constraint_violation"], cert["complementarity_residual"]) <= tol)
            return cert

        def slack(cert):
            return 10 * tol * cert["objective_unit"] + tol * float(np.sum(np.abs(cert["constraint_multipliers"])))

        def feasible_descent(z, cert):
            """Second-order probe on the tangent space of the strongly active set.

            The finite-difference Lagrangian Hessian reduced to that space gives three kinds
            of probe: negative-curvature directions (saddles), flat directions with a nonzero
            reduced gradient (linear or unbounded descent), and the Newton step on the
            positive-curvature part. A probe counts only when an evaluated feasible point
            lowers the objective beyond the certificate slack. Also returns whether the
            reduced Hessian is positive definite and the Newton decrement r^T R^-1 r."""
            F_star, fu = cert["value"], cert["objective_unit"]
            n = len(z)
            strong = [j for j, (kind, i) in enumerate(cert["multiplier_ids"])
                      if (kind == "constraint" and eq[i]) or cert["normalized"][j] > tol * fu]
            active = [cert["multiplier_ids"][j][1] for j in strong if cert["multiplier_ids"][j][0] == "constraint"]
            if strong:
                matrix = np.array([cert["rows"][j] for j in strong])
                _, singular, vt = np.linalg.svd(matrix)
                rank = int(np.sum(singular > 1e-10 * singular[0]))
                basis = vt[rank:].T
            else:
                basis = np.eye(n)
            k = basis.shape[1]
            if k == 0:
                return None, {"pd": True, "decrement": 0.}
            lam = cert["constraint_multipliers"]
            center = F_star + float(lam @ cert["constraint_values"]) if m else F_star
            h = 1e-4 * max(1., float(np.linalg.norm(z, ord=np.inf)))
            def lagrangian(w):
                F, G = evaluate(w)
                return F + (float(lam @ G) if m else 0.)
            def inside(w):
                return bool(np.all(w >= lo) and np.all(w <= hi))
            def curvature(d):
                for a, b in ((1, -1), (1, 2), (-1, -2)):
                    p, q = z + a * h * d, z + b * h * d
                    if inside(p) and inside(q):
                        try:
                            lp, lq = lagrangian(p), lagrangian(q)
                        except TrialError:
                            continue
                        if b == -1:
                            return (lp - 2 * center + lq) / h**2
                        return (lq - 2 * lp + center) / h**2
                return 0.
            reduced = np.zeros((k, k))
            for i in range(k):
                reduced[i, i] = curvature(basis[:, i])
            for i in range(k):
                for j in range(i):
                    both = curvature(basis[:, i] + basis[:, j])
                    reduced[i, j] = reduced[j, i] = (both - reduced[i, i] - reduced[j, j]) / 2
            eigenvalues, eigenvectors = np.linalg.eigh(reduced)
            threshold = 1e-6 * max(1., abs(center), float(np.max(np.abs(eigenvalues), initial=0.)))
            jac = cert["jacobian"][active] if active else None
            best, margin = None, slack(cert)
            size = max(1., float(np.linalg.norm(z, ord=np.inf)))
            reduced_gradient = eigenvectors.T @ (basis.T @ cert["lagrangian_gradient"])
            probes = []  # (direction in z, step multipliers)
            ladder = (1e-3, 1e-2, 1e-1, 1.)
            for index in np.argsort(eigenvalues)[:3]:
                if eigenvalues[index] < -threshold:
                    direction = basis @ eigenvectors[:, index]
                    probes += [(direction * size, ladder), (-direction * size, ladder)]
            flat = np.abs(eigenvalues) <= threshold
            for index in np.flatnonzero(flat & (reduced_gradient != 0))[:3]:
                direction = -np.sign(reduced_gradient[index]) * (basis @ eigenvectors[:, index])
                probes.append((direction * size, ladder + (10., 100.)))
            positive = eigenvalues > threshold
            decrement = float(np.sum(reduced_gradient[positive]**2 / eigenvalues[positive]))
            definite = bool(np.all(positive | (np.abs(reduced_gradient) <= tol * fu)))
            if np.any(positive):
                newton = -basis @ (eigenvectors[:, positive] @ (reduced_gradient[positive] / eigenvalues[positive]))
                probes.append((newton, (1., .5)))
            for direction, steps in probes:
                for alpha in steps:
                    w = np.clip(z + alpha * direction, lo, hi)
                    try:
                        F, G = evaluate(w)
                        for _ in range(3 if active else 0):
                            residual = np.where(eq[active], G[active], np.maximum(G[active], 0.))
                            if violation_of(G) <= tol:
                                break
                            w = np.clip(w - np.linalg.pinv(jac) @ residual, lo, hi)
                            F, G = evaluate(w)
                    except TrialError:
                        continue
                    if violation_of(G) <= tol and F < F_star - margin and (best is None or F < best[1]):
                        best = (w.copy(), F)
            return (None if best is None else best[0]), {"pd": definite, "decrement": decrement}

        history = []
        if selected == "auto":
            selected = "bfgs" if objective.smooth and all(c.expression.smooth for c in cons) else "nelder_mead"
        algorithm = "augmented_lagrangian" if cons else ("projected_bfgs" if bounds and selected == "bfgs" else selected)
        inner = nelder_mead if selected == "nelder_mead" else bfgs
        inner_tol = max(tol * .05, 1e-13)
        total_iterations = 0

        def local_run(start):
            """One safeguarded local solve. Returns (z, cert, status)."""
            nonlocal total_iterations
            tracker.local = None
            z = np.clip(np.array(start, dtype=float), lo, hi)
            F0, G0 = evaluate(z)
            remaining, spurious, cert = limit, 0, None
            # Multipliers are kept in raw constraint units; constraints are normalized by
            # their scaled-gradient size cs (lam_scaled = lam*cs), reset on a 100x change.
            lam = np.zeros(m)
            rho, cs, lam_floor = 1., np.ones(m), 1.
            if cons:
                J0 = jacobian(vector, z, lo, hi)
                cs = np.maximum(1., np.max(np.abs(J0[1:]), axis=1))
                C0 = np.where(eq, G0, np.maximum(G0, 0.)) / cs
                rho = float(np.clip(10 * max(1., abs(F0)) / max(1., .5 * float(C0 @ C0)), 1e-8, 1e8))
                lam_floor = 10 * max(1., abs(F0), float(np.max(np.abs(J0[0]))))
            previous_violation = float("inf")
            stage_start = z.copy()
            warm = False
            tightness = 1.
            hessian = None

            def make_merit(lam, rho, cs):
                def merit(point):
                    F, G = evaluate(point)
                    if not cons:
                        return F
                    C = G / cs
                    scaled = lam * cs
                    with np.errstate(all="ignore"):
                        # ((max(0,l+rC))^2-l^2)/(2r) written without cancellation of l^2 terms.
                        open_ = eq | (scaled + rho * C >= 0)
                        terms = np.where(open_, scaled * C + .5 * rho * C * C, -scaled * scaled / (2 * rho))
                        value = F + float(np.sum(terms))
                    if not math.isfinite(value) or abs(value) > 1e100:
                        raise TrialError("augmented Lagrangian outside the finite range")
                    return value
                return merit

            for outer in range(60):
                budget.check()
                if remaining <= 0:
                    break
                merit = make_merit(lam, rho, cs)
                if warm and tracker.local is not None:
                    # After divergence or a spurious stationary point, restart from the best
                    # feasible point when it is better under the current merit.
                    try:
                        if merit(tracker.local[0]) < merit(z):
                            z = tracker.local[0].copy()
                    except TrialError:
                        pass
                warm = False
                allowance = remaining if not cons else min(remaining, max(40, limit // 8))
                local = inner(merit, z, lo, hi, max(inner_tol * tightness, 1e-15), allowance, budget, history, unit, hessian)
                hessian = local.inverse
                remaining -= max(1, local.iterations)
                total_iterations += local.iterations
                if local.status == "diverged":
                    _, Gd = evaluate(local.point)
                    if violation_of(Gd) <= tol:
                        return local.point, None, "unbounded"
                    # The penalty is too weak: the subproblem is unbounded. Increase it and
                    # restart from the best feasible point, keeping the multipliers.
                    rho *= 10
                    z = tracker.local[0].copy() if tracker.local is not None else stage_start.copy()
                    warm = True
                    hessian = None
                    if rho > 1e14:
                        break
                    continue
                z = local.point
                cert = certificate(z)
                better = None
                if cert["passed"] or cert["near"]:
                    better, curvature = feasible_descent(z, cert)
                    cert["newton_decrement"] = curvature["decrement"]
                    if cert["near"] and curvature["pd"] and curvature["decrement"] <= 2 * tol * cert["objective_unit"]:
                        # Relative-decrease test: the quadratic model on the tangent space
                        # predicts at most tol*max(1,|f|) further improvement.
                        cert["passed"], cert["stationarity_test"] = True, "newton_decrement"
                if cert["passed"]:
                    reference = tracker.local
                    if better is None and (reference is None or cert["value"] <= reference[1] + slack(cert)):
                        return z, cert, "converged"
                    # A stationary point with a known better feasible point (saddle, spurious
                    # merit stationary point or worse local solution): continue from there.
                    spurious += 1
                    if spurious > 8:
                        return z, cert, "worse_than_best_feasible"
                    if better is None or (reference is not None and reference[1] < evaluate(better)[0]):
                        better = reference[0]
                    if np.linalg.norm(better, ord=np.inf) > DIVERGENCE:
                        return better, None, "unbounded"
                    if cons:
                        C = cert["constraint_values"] / cs
                        scaled = lam * cs
                        lam = np.where(eq, scaled + rho * C, np.maximum(0., scaled + rho * C)) / cs
                        rho *= 10
                    z = better.copy()
                    previous_violation = float("inf")
                    warm = True
                    hessian = None
                    continue
                if not cons:
                    if local.status in ("stalled", "iteration_limit") or remaining <= 0:
                        return z, cert, "not_converged"
                    # The inner stopping rule (e.g. a simplex diameter) passed but the
                    # gradient certificate did not: continue with a tighter inner tolerance.
                    tightness *= .01
                    continue
                C = cert["constraint_values"] / cs
                scaled = lam * cs
                update = np.where(eq, scaled + rho * C, np.maximum(0., scaled + rho * C))
                cap = np.maximum(10 * np.abs(scaled), lam_floor)
                lam = np.clip(update, -cap, cap) / cs
                violation = float(np.max(np.where(eq, np.abs(C), np.maximum(C, 0.)), initial=0.))
                if violation > .5 * previous_violation:
                    rho *= 10
                previous_violation = violation
                if rho > 1e14:
                    break
                # Re-normalize a constraint only when its gradient size changed by more than
                # 100x since the scale was set (multipliers are raw, so lam*cs follows).
                current = np.maximum(1., np.max(np.abs(cert["jacobian"]), axis=1))
                cs = np.where((current > 100 * cs) | (current < cs / 100), current, cs)
            if cert is None:
                cert = certificate(z)
            return z, cert, "not_converged"

        def describe(z, F):
            return {"point": dict(zip(names, (z * scale).tolist())), "objective": sign * F}

        def best_details():
            out = {}
            if tracker.overall is not None:
                out["best_feasible"] = describe(*tracker.overall)
            if tracker.any is not None:
                _, z, F, violation = tracker.any
                out["best_trial"] = {**describe(z, F), "raw_constraint_violation": violation,
                                     "feasible": violation <= tol}
            return out

        try:
            try:
                F_start, _ = evaluate(z0)
            except TrialError:
                invalid("expression", "objective or a constraint is undefined at the initial point", "input.out_of_range")
            start_points = [z0]
            if selected in ("multistart", "differential_evolution"):
                if not (np.all(np.isfinite(lower)) and np.all(np.isfinite(upper))):
                    invalid("bounds", "global and multistart searches require finite bounds")
                if selected == "multistart":
                    start_points.extend(rng.uniform(lo, hi, (nstarts - 1, len(names))))
                else:
                    def global_merit(z):
                        F, G = evaluate(z)
                        violation = np.where(eq, G, np.maximum(G, 0.))
                        return F + 1e4 * float(violation @ violation)
                    start_points = [differential_evolution(global_merit, z0, lo, hi, pop, gen, rng, budget, history)]
            candidates, failed, runs, unbounded = [], [], [], None
            sliced = 0
            def attempt(start):
                try:
                    return local_run(start)
                except TrialError:
                    return None
            for index, start in enumerate(start_points):
                outcome = "exhausted"
                if selected == "multistart":
                    # Fair share of what is left, keeping 10% for the final post-condition.
                    left = len(start_points) - index
                    seconds = max(0., (budget.deadline - time.monotonic() - .1 * total_seconds) / left)
                    evaluations = max(0, (budget.max_evaluations * 9 // 10 - budget.evaluations) // left)
                    with Slice(budget, seconds, evaluations):
                        outcome = attempt(start)
                else:
                    outcome = attempt(start)
                if isinstance(outcome, str):
                    sliced += 1
                    runs.append({"start": index, "converged": False, "status": "budget_share_exhausted"})
                    continue
                if outcome is None:
                    runs.append({"start": index, "converged": False, "status": "undefined_trial"})
                    continue
                z, cert, status = outcome
                F, _ = evaluate(z)
                runs.append({"start": index, **describe(z, F), "converged": status == "converged", "status": status})
                if status == "converged":
                    candidates.append((F, z, cert))
                elif status == "unbounded":
                    unbounded = (z, F)
                else:
                    failed.append((F, z, cert, status))
            if unbounded is not None:
                invalid("expression", "optimize.not_converged: objective decreases without bound on the feasible set (search diverged beyond 1e10 scaled units)",
                        "optimize.not_converged", converged=False, reason="optimize.unbounded",
                        last_point=describe(*unbounded), evaluations=budget.evaluations, iterations=total_iterations, **best_details())
            if candidates and tracker.overall is not None:
                F, z, cert = min(candidates, key=lambda item: item[0])
                if F > tracker.overall[1] + slack(cert):
                    # Post-condition: never certify a point worse than an evaluated feasible point.
                    try:
                        zr, cr, status = local_run(tracker.overall[0])
                    except TrialError:
                        status = "undefined_trial"
                    if status == "converged":
                        candidates.append((evaluate(zr)[0], zr, cr))
                    F, z, cert = min(candidates, key=lambda item: item[0])
                    if F > tracker.overall[1] + slack(cert):
                        candidates = []
                        failed.append((F, z, cert, "worse_than_best_feasible"))
            if not candidates:
                details = {}
                if failed:
                    F, z, cert, status = min(failed, key=lambda item: item[0] + 1e6 * item[2]["constraint_violation"])
                    details = {"point": dict(zip(names, z * scale)), "objective": sign * F, "status": status,
                               **{k: cert[k] for k in ("stationarity_residual", "constraint_violation", "raw_constraint_violation", "complementarity_residual")}}
                invalid("expression", "optimize.not_converged: numerical KKT certificate failed or a better feasible point is known", "optimize.not_converged",
                        converged=False, best=details, evaluations=budget.evaluations, iterations=total_iterations,
                        local_runs=runs, **best_details())
            F, z, cert = min(candidates, key=lambda item: item[0])
            distinct = []
            for value, point, _ in sorted(candidates, key=lambda item: item[0]):
                if not any(np.linalg.norm(point - previous[1]) <= max(1e-4, 100 * tol) for previous in distinct):
                    distinct.append((value, point))
            objective_value = sign * F
            G = cert["constraint_values"]
            output_constraints = [{"relation": c.relation, "value": G[i] * c.orientation,
                                   "active": bool(eq[i] or abs(G[i]) <= 10 * tol * cert["magnitude"][i])} for i, c in enumerate(cons)]
            multipliers = []
            for value, (kind, i) in zip(cert["multipliers"], cert["multiplier_ids"]):
                canonical = value if kind == "constraint" else value / scale[i]
                orientation = cons[i].orientation if kind == "constraint" else (-1. if kind == "lower" else 1.)
                multipliers.append({"kind": kind, "index": i, "canonical_multiplier": canonical,
                                    "rhs_sensitivity": -sign * orientation * canonical})
            warnings = []
            if sliced:
                warnings.append(f"{sliced} of {len(start_points)} multistart local runs stopped at their share of the time/evaluation budget; the result is the best certified local optimum among completed runs.")
            artifact = handle.write_text("convergence.csv", csv_text(["iteration", "merit", "residual_or_spread"], history), fmt="csv")
            return result({"point": dict(zip(names, z * scale)), "objective": objective_value, "converged": True,
                           "method": method, "algorithm": algorithm, "inner_algorithm": selected,
                           "constraints": output_constraints, "multipliers": multipliers,
                           "stationarity_residual": cert["stationarity_residual"],
                           "stationarity_test": cert["stationarity_test"], "newton_decrement": cert["newton_decrement"],
                           "constraint_violation": cert["constraint_violation"],
                           "raw_constraint_violation": cert["raw_constraint_violation"],
                           "complementarity_residual": cert["complementarity_residual"],
                           "tolerance": tol, "objective_scale": cert["objective_unit"], "constraint_scales": cert["magnitude"],
                           "variable_scales": scale,
                           "iterations": total_iterations, "evaluations": budget.evaluations, "seed": seed,
                           "local_runs": runs,
                           "local_optima": [{"point": dict(zip(names, p * scale)), "objective": sign * v} for v, p in distinct]},
                          "objective", objective_value, artifacts=[artifact], warnings=warnings,
                          formulas={"BFGS": "inverse-Hessian secant update with Shanno-Phua initial scaling; Armijo c1=1e-4, step halving, step cap 100*max(1,||z||)",
                                    "multipliers": "L=f+(||max(0,lambda+rho*g)||^2-||lambda||^2)/(2*rho); equalities unprojected; lambda clamped to max(10|lambda_prev|, floor)",
                                    "certificate": "max(||grad L||inf/max(1,|f(x)|), max violation_i/max(1,max_j|dg_i/dx_j|max(1,|x_j|)), complementarity) <= tolerance at the returned point",
                                    "second_order": "finite-difference Lagrangian curvature on the active tangent space; negative curvature is probed for a feasible lower point"},
                          assumptions="Finite-difference local smoothness. Numerical KKT conditions are necessary, not a proof of a global minimum. Units are not tracked.",
                          validity="16 total declared names, 20 constraints; 200000 scalar evaluations and 100 million node operations per call. Finite bounds for global search. A point worse than an evaluated feasible point is never certified.")
        except ToolFailure as exc:
            if exc.family == "input.out_of_range" and "budget" in exc.message:
                exc.details.update(best_details())
            raise
    return run("minimize", inputs, compute)
