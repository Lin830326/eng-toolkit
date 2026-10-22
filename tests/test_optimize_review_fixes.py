"""Regression tests for the independent review findings M1-M4, L1, R1-R2, F1-F2 and S1.

Every expected number is derived here in closed form or, behind importorskip, from SciPy.
"""

import json
import math
import time

import numpy as np
import pytest

from eng_toolkit.domains.optimize._common import Budget
from eng_toolkit.domains.optimize.fit import fit
from eng_toolkit.domains.optimize.linprog import linprog, solve_lp
from eng_toolkit.domains.optimize.minimize import minimize
from eng_toolkit.domains.optimize.root import root
from eng_toolkit.domains.optimize.sensitivity import sensitivity
from tests.optimize_testing import call, ok, table

BILINEAR = dict(expression="x*y", initial="x=1;y=1", constraints="x+y-10<=0", maximize=True)


# M1: a maximization with a feasible start must never certify a worse point (the saddle).

def test_m1_bilinear_maximum_in_box():
    # Maximize xy on x+y<=10: xy<=((x+y)/2)^2<=25, equality at x=y=5. The start (1,1)
    # is feasible with objective 1; the saddle (0,0) with objective 0 must not be returned.
    r = ok(minimize, bounds="[[0,100],[0,100]]", **BILINEAR)
    assert list(r.data["point"].values()) == pytest.approx([5, 5], abs=1e-6)
    assert r.data["objective"] == pytest.approx(25, abs=1e-6)
    assert r.data["objective"] >= 1  # never worse than the feasible start
    # KKT: grad(xy)=(5,5)=lambda*(1,1), so the inequality multiplier is 5.
    assert abs(r.data["multipliers"][0]["canonical_multiplier"]) == pytest.approx(5, rel=1e-5)


def test_m1_saddle_is_not_certified():
    # x^2-y^2 has a saddle at the origin and is unbounded below along y: refuse.
    r = call(minimize, expression="x^2-y^2", initial="x=1;y=0")
    assert not r.ok
    assert r.error.family == "optimize.not_converged"


@pytest.mark.parametrize("seed", range(40))
def test_m1_bilinear_family_analytic(seed):
    # max a*x*y s.t. x+y<=c, x,y>=0 (finite or open upper bounds): optimum a*c^2/4 at x=y=c/2.
    rng = np.random.default_rng(seed)
    a, c = float(rng.uniform(.5, 3)), float(rng.uniform(2, 20))
    upper = None if seed % 2 else float(rng.uniform(c, 5 * c))
    x0, y0 = map(float, rng.uniform(0, c / 4, 2))
    r = call(minimize, expression=f"{a!r}*x*y", initial=f"x={x0!r};y={y0!r}", constraints=f"x+y-{c!r}<=0",
             bounds=json.dumps([[0, upper], [0, upper]]), maximize=True)
    assert r.ok, r.error
    assert r.data["objective"] == pytest.approx(a * c * c / 4, rel=1e-6)


def _quadratic_problem(seed):
    """Random strictly convex QP (or concave maximization) with linear rows and a strictly
    feasible point; coefficients are rounded so the reference sees identical data."""
    rng = np.random.default_rng(seed)
    n = 2 + seed % 3
    B = rng.normal(size=(n, n))
    Q = np.round((B @ B.T + .2 * np.eye(n)) / 2, 6)
    c = np.round(rng.normal(size=n) * 3, 6)
    feasible = rng.uniform(-1, 1, n)
    mi, me = seed % 4, (seed // 4) % 2
    A = np.round(rng.normal(size=(mi, n)), 6)
    b = np.round(A @ feasible + rng.uniform(.1, 1, mi), 6)
    E = np.round(rng.normal(size=(me, n)), 6)
    e = np.round(E @ feasible, 6)
    names = [f"x{i}" for i in range(n)]
    quad = "+".join(f"({float(Q[i, j])!r})*{names[i]}*{names[j]}" for i in range(n) for j in range(n))
    quad += "+" + "+".join(f"({float(c[i])!r})*{names[i]}" for i in range(n))
    maximize = bool(seed % 2)
    rows = ["+".join(f"({float(A[k, i])!r})*{names[i]}" for i in range(n)) + f"-({float(b[k])!r})<=0" for k in range(mi)]
    rows += ["+".join(f"({float(E[k, i])!r})*{names[i]}" for i in range(n)) + f"-({float(e[k])!r})==0" for k in range(me)]
    kind = seed % 3
    bounds = None if kind == 0 else ([[-3, 3]] * n if kind == 1 else [[-3, None]] * n)
    start = feasible if seed % 5 else rng.uniform(-2, 2, n)
    kwargs = dict(expression=f"-({quad})" if maximize else quad, constraints=";".join(rows), maximize=maximize,
                  initial=";".join(f"{name}={float(v)!r}" for name, v in zip(names, start)))
    if bounds is not None:
        kwargs["bounds"] = json.dumps(bounds)
    data = dict(Q=Q, c=c, A=A, b=b, E=E, e=e, bounds=bounds, start=start, feasible=feasible, maximize=maximize)
    return kwargs, data


def _quadratic_reference(scipy, d):
    """Exact optimum of the strictly convex QP by KKT active-set enumeration (a unique
    point satisfies primal feasibility and nonnegative inequality multipliers), cross-
    checked against SLSQP whenever SLSQP reports a feasible converged point."""
    import itertools
    n = len(d["c"])
    rows, rhs = [*d["A"]], [*d["b"]]
    for i, (lo, hi) in enumerate(d["bounds"] or []):
        if lo is not None:
            rows.append(-np.eye(n)[i]); rhs.append(-lo)
        if hi is not None:
            rows.append(np.eye(n)[i]); rhs.append(hi)
    G, h = np.array(rows).reshape(-1, n), np.array(rhs)
    def f(x):
        return x @ d["Q"] @ x + d["c"] @ x
    best = None
    for size in range(0, n - len(d["E"]) + 1):
        for active in itertools.combinations(range(len(h)), size):
            C = np.vstack([d["E"], G[list(active)]]) if len(d["E"]) or active else np.zeros((0, n))
            k = len(C)
            kkt = np.block([[2 * d["Q"], C.T], [C, np.zeros((k, k))]])
            try:
                solution = np.linalg.solve(kkt, np.r_[-d["c"], d["e"], h[list(active)]])
            except np.linalg.LinAlgError:
                continue
            x, lam = solution[:n], solution[n + len(d["E"]):]
            if np.all(G @ x - h <= 1e-9) and np.all(lam >= -1e-9) and (best is None or f(x) < f(best)):
                best = x
    exact = f(best)
    cons = [{"type": "ineq", "fun": lambda x: d["b"] - d["A"] @ x}] if len(d["A"]) else []
    if len(d["E"]):
        cons.append({"type": "eq", "fun": lambda x: d["E"] @ x - d["e"]})
    bounds = None if d["bounds"] is None else [tuple(p) for p in d["bounds"]]
    check = scipy.minimize(f, d["feasible"], method="SLSQP", constraints=cons, bounds=bounds, options={"ftol": 1e-14, "maxiter": 1000})
    if check.success and np.all(G @ check.x - h <= 1e-8) and np.all(abs(d["E"] @ check.x - d["e"]) <= 1e-8):
        assert exact <= check.fun + 1e-7 * max(1, abs(exact))
    return -exact if d["maximize"] else exact


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_m1_quadratic_family_against_scipy():
    # 200 strictly convex QPs / concave maximizations: a unique optimum, so SLSQP (or
    # trust-constr) is an exact oracle. Every success must agree to 1e-6; refusals must
    # be clean, and there must be no wrong success.
    scipy = pytest.importorskip("scipy.optimize")
    wrong, refused = [], []
    for seed in range(200):
        kwargs, data = _quadratic_problem(seed)
        reference = _quadratic_reference(scipy, data)
        r = call(minimize, **kwargs)
        if not r.ok:
            assert r.error.family in ("optimize.not_converged", "input.out_of_range"), r.error
            refused.append(seed)
        elif abs(r.data["objective"] - reference) > 1e-6 * max(1, abs(reference)):
            wrong.append((seed, r.data["objective"], reference))
    assert wrong == []
    assert len(refused) <= 2, refused


# M2: half-open bounds never put the 1e100 placeholder into iterates or the history.

def test_m2_half_open_bounds():
    r = ok(minimize, bounds="[[0,null],[0,null]]", **BILINEAR)
    assert list(r.data["point"].values()) == pytest.approx([5, 5], abs=1e-6)
    assert r.data["objective"] == pytest.approx(25, abs=1e-6)
    rows = table(r)
    assert rows and all(abs(float(v)) <= 1e100 for row in rows for v in row.values())


@pytest.mark.parametrize("kwargs", [
    dict(expression="x+y", initial="x=1;y=1", constraints="x-y<=0", bounds="[[0,null],[0,null]]", maximize=True),
    dict(expression="-x-y", initial="x=0;y=0", tolerance=1e-3),
    dict(expression="-x", initial="x=0", tolerance=1e-5),
])
def test_m2_unbounded_is_not_converged_never_out_of_range(kwargs):
    r = call(minimize, **kwargs)
    assert not r.ok
    assert r.error.family == "optimize.not_converged"
    assert "out_of_range" not in r.error.family


# M3: the convergence test must not depend on how far the start is.

@pytest.mark.parametrize("start", ["0", "2e6", "999990"])
def test_m3_far_minimum(start):
    # (x-1e6)^2 has its only minimum at x=1e6 (scipy BFGS: 999999.9999973 from 0).
    r = ok(minimize, expression="(x-1e6)^2", initial=f"x={start}")
    assert r.data["point"]["x"] == pytest.approx(1e6, abs=1e-6)
    assert r.data["objective"] <= 1e-12


def test_m3_constraint_violation_uses_current_scale():
    # min -x s.t. x^2<=1: x=1. The violation tolerance is relative to the constraint
    # magnitude at x=1 (max(1,|2x|*max(1,|x|))=2), not to g(1000)=999999.
    r = ok(minimize, expression="-x", initial="x=1000", constraints="x^2-1<=0")
    assert r.data["point"]["x"] == pytest.approx(1, abs=2e-7)
    assert r.data["raw_constraint_violation"] <= 2e-7
    assert r.data["constraint_violation"] <= r.data["tolerance"]


@pytest.mark.parametrize("s", [1e-3, 1e-1, 1., 1e3, 1e6])
@pytest.mark.parametrize("shift", [0., 1.])
def test_m3_rosenbrock_scale_family(s, shift):
    # u=s*(x+shift), v=s*(y+shift) maps the Rosenbrock minimum (1,1) to s*(1+shift).
    expression = f"(1-(u/{s!r}-{shift!r}))^2+100*((v/{s!r}-{shift!r})-(u/{s!r}-{shift!r})^2)^2"
    r = ok(minimize, expression=expression, initial=f"u={s*(-1.2+shift)!r};v={s*(1+shift)!r}")
    point = r.data["point"]
    assert point["u"] / s - shift == pytest.approx(1, abs=1e-6)
    assert point["v"] / s - shift == pytest.approx(1, abs=1e-6)


@pytest.mark.parametrize("shift", [1e-3, 1e3, 1e6])
def test_m3_rosenbrock_translation_family(shift):
    expression = f"(1-(u-{shift!r}))^2+100*((v-{shift!r})-(u-{shift!r})^2)^2"
    r = ok(minimize, expression=expression, initial=f"u={shift-1.2!r};v={shift+1!r}")
    assert r.data["point"]["u"] - shift == pytest.approx(1, abs=1e-6)
    assert r.data["point"]["v"] - shift == pytest.approx(1, abs=1e-6)


# M4: the best trial, multistart budget sharing and bound multipliers.

RASTRIGIN = "20+x^2+y^2-10*cos(2*pi*x)-10*cos(2*pi*y)"


def test_m4_rastrigin_from_global_minimum_short_limit():
    # Every Rastrigin term is >= 0 and zero at the origin, the start of run 0.
    r = call(minimize, expression=RASTRIGIN, initial="x=0;y=0", bounds="[[-5.12,5.12],[-5.12,5.12]]",
             method="multistart", starts=64, time_limit="1 s")
    if r.ok:
        assert r.data["objective"] == pytest.approx(0, abs=1e-12)
        assert list(r.data["point"].values()) == pytest.approx([0, 0], abs=1e-9)
        assert len(r.data["local_runs"]) == 64
    else:
        best = r.error.details["best_trial"]
        assert best["objective"] == pytest.approx(0, abs=1e-12)
        assert list(best["point"].values()) == pytest.approx([0, 0], abs=1e-9)


def test_m4_best_trial_is_the_best_not_the_last(monkeypatch):
    import importlib
    module = importlib.import_module("eng_toolkit.domains.optimize.minimize")
    monkeypatch.setattr(module, "Budget", lambda duration, evaluations: Budget(duration, max_evaluations=60))
    r = call(minimize, expression="(1-x)^2+100*(y-x^2)^2", initial="x=-1.2;y=1")
    assert not r.ok and "budget" in r.error.message
    best = r.error.details["best_trial"]
    x, y = best["point"]["x"], best["point"]["y"]
    assert best["objective"] == pytest.approx((1 - x)**2 + 100 * (y - x * x)**2, rel=1e-12)
    assert best["objective"] < 24.2  # f(-1.2,1)=4.84+19.36
    assert best["feasible"]


def test_m4_default_multistart_rastrigin_within_time_limit():
    started = time.monotonic()
    r = ok(minimize, expression=RASTRIGIN, initial="x=2.5;y=-3.3", bounds="[[-5.12,5.12],[-5.12,5.12]]", method="multistart")
    assert time.monotonic() - started < 10
    runs = r.data["local_runs"]
    assert len(runs) == 16 and all("converged" in run for run in runs)
    # Local minima of Rastrigin are near integer points with value ~sum of x^2; the best
    # certified one must be one of them and no worse than any other converged run.
    assert r.data["objective"] <= min(run["objective"] for run in runs if run["converged"]) + 1e-12


def test_m4_bound_multipliers_are_second_order_accurate():
    # Nearest box point to (3,-2) in [0,2]x[-1,1] is (2,-1); bound multipliers 2(3-2)=2, 2(-1+2)=2.
    r = ok(minimize, expression="(x-3)^2+(y+2)^2", initial="x=0;y=0", bounds="[[0,2],[-1,1]]")
    assert [m["canonical_multiplier"] for m in r.data["multipliers"]] == pytest.approx([2, 2], rel=1e-8)


# L1: phase I feasibility is scale aware; integer refusals keep their real cause.

def test_l1_phase_one_roundoff_is_feasible():
    # 2x+3y=4 and x+y=2 give y=0, x=2; -4x-y=-8<=-2 and the bounds hold, so the LP is
    # feasible with a single point: objective -1*2-3*0=-2.
    r = ok(linprog, c="[-1,-3]", A_ub="[[-4,-1]]", b_ub="[-2]", A_eq="[[2,3],[-1,-1]]", b_eq="[4,-2]", bounds="[[-3,2],[0,2]]")
    assert r.data["objective"] == pytest.approx(-2, abs=1e-12)
    assert r.data["x"] == pytest.approx([2, 0], abs=1e-12)


def test_l1_integer_repro():
    # scipy.optimize.milp finds -1 at x=(1,0,0,2); check it independently below.
    kwargs = dict(c="[3,-1,1,-2]", A_ub="[[-4,-2,1,-2]]", b_ub="[2]", A_eq="[[-3,3,0,3],[-1,1,-2,3]]", b_eq="[3,5]",
                  bounds="[[-2,1],[0,null],[0,null],[-2,3]]", integer="[0,1,2,3]")
    r = ok(linprog, **kwargs)
    assert r.data["objective"] == pytest.approx(-1, abs=1e-9)
    x = np.array(r.data["x"])
    assert np.allclose(x, np.round(x)) and np.allclose([[-3, 3, 0, 3], [-1, 1, -2, 3]] @ x, [3, 5])
    optimize = pytest.importorskip("scipy.optimize")
    ref = optimize.milp([3, -1, 1, -2], integrality=np.ones(4),
                        constraints=[optimize.LinearConstraint([[-4, -2, 1, -2]], -np.inf, [2]),
                                     optimize.LinearConstraint([[-3, 3, 0, 3], [-1, 1, -2, 3]], [3, 5], [3, 5])],
                        bounds=optimize.Bounds([-2, 0, 0, -2], [1, np.inf, np.inf, 3]))
    assert r.data["objective"] == pytest.approx(ref.fun, abs=1e-9)


def test_l1_integer_refusal_keeps_cause_and_pointer():
    r = call(linprog, c="[-1]", bounds="[[0,null]]", integer="[0]")
    assert not r.ok
    assert r.error.details["param"] == "/integer"
    assert "unbounded relaxation" in r.error.message
    assert r.error.details["nodes"] == 1


def _small_integer_lp(seed):
    rng = np.random.default_rng(seed)
    n, mu, me = int(rng.integers(2, 5)), int(rng.integers(0, 3)), int(rng.integers(1, 3))
    bounds = []
    for _ in range(n):
        lo = int(rng.integers(-3, 1)); hi = lo + int(rng.integers(1, 5)); kind = rng.integers(0, 4)
        bounds.append((lo, hi) if kind < 2 else ((lo, None) if kind == 2 else (None, hi)))
    point = np.array([rng.integers(lo if lo is not None else hi - 3, (hi if hi is not None else lo + 3) + 1)
                      for lo, hi in bounds], float)
    A = rng.integers(-4, 5, (mu, n)).astype(float); b = A @ point + rng.integers(0, 3, mu)
    E = rng.integers(-3, 4, (me, n)).astype(float); e = E @ point
    return rng.integers(-3, 4, n).astype(float), A, b, E, e, bounds


def test_l1_three_thousand_equality_lps_against_scipy():
    # Feasible by construction (an integer point satisfies every row); HiGHS decides
    # optimal or unbounded. Before the fix the exact-zero phase I test refused 147 of these.
    optimize = pytest.importorskip("scipy.optimize")
    from eng_toolkit.errors import ToolFailure
    wrong, refused = [], []
    for seed in range(3000):
        c, A, b, E, e, bounds = _small_integer_lp(seed)
        ref = optimize.linprog(c, A_ub=A if len(A) else None, b_ub=b if len(A) else None, A_eq=E, b_eq=e, bounds=bounds, method="highs")
        status = {0: "optimal", 3: "unbounded"}[ref.status]
        try:
            out = solve_lp(c, A.reshape(-1, len(c)), b, E, e, bounds, Budget("10 s"))
        except ToolFailure as exc:
            refused.append((seed, exc.message))
            continue
        if out["status"] != status or (status == "optimal" and abs(out["objective"] - ref.fun) > 1e-9 * max(1, abs(ref.fun))):
            wrong.append((seed, status, out["status"], out.get("objective"), ref.fun))
    assert wrong == []
    assert len(refused) < 3, refused  # < 0.1 %


# R1/R2: relative accuracy for small roots; an exact root at the start is a root.

@pytest.mark.parametrize("kwargs,expected", [
    (dict(expression="(x-1e-6)*(x+1)", initial="x=0.25", bracket="[0,0.5]"), 1e-6),
    (dict(expression="(x-1e-6)*(x+1)", initial="x=0.25", bracket="[0,0.5]", method="bisection"), 1e-6),
    (dict(expression="x^2-1e-10", initial="x=1"), 1e-5),
    (dict(expression="x^2-1e-10", initial="x=1", method="newton"), 1e-5),
    (dict(expression="x^2-1e-10", initial="x=1", method="secant"), 1e-5),
])
def test_r1_small_roots_are_relatively_accurate(kwargs, expected):
    r = ok(root, **kwargs)
    value = r.data["solution"]["x"]
    assert value == pytest.approx(expected, rel=1e-7)
    certificate = r.data["roots"][0]["certificate"]
    assert certificate["estimated_relative_error"] <= 1e-7
    assert certificate["absolute_floor"][0] <= 1e-15


def test_r2_exact_root_at_start():
    r = ok(root, expression="x^2", initial="x=0")
    assert r.data["solution"]["x"] == 0
    assert any("singular" in w for w in r.warnings)


def test_r2_singular_start_that_is_not_a_root_still_refuses():
    assert not call(root, expression="x+y-2;2*x+2*y-4", initial="x=1;y=0").ok


# F1/F2: the 20000-row limit is reachable; CSV cells use the strict decimal grammar.

def test_f1_twenty_thousand_rows_linear_and_exponential():
    x = np.linspace(0, 10, 20000)
    y = 3 * x + 2 + .01 * np.sin(7 * x)
    r = ok(fit, model="a*x+b", initial="a=1;b=0", data=json.dumps({"x": x.tolist(), "y": y.tolist()}))
    reference = np.linalg.lstsq(np.column_stack([x, np.ones_like(x)]), y, rcond=None)[0]
    assert list(r.data["parameters"].values()) == pytest.approx(reference, rel=1e-8)
    assert r.data["evaluations"] < 1000  # one evaluation per vectorised model call
    assert r.data["row_evaluations"] == 20000 * r.data["evaluations"]
    x = np.linspace(0, 4, 20000)
    r = ok(fit, model="a*exp(-b*x)", initial="a=1.8;b=0.5", tolerance=1e-10,
           data=json.dumps({"x": x.tolist(), "y": (2 * np.exp(-.7 * x)).tolist()}))
    assert list(r.data["parameters"].values()) == pytest.approx([2, .7], rel=1e-9)


@pytest.mark.parametrize("cell", ["1_000", "６", "nan", "inf", "0x10", "1e", "--1", "1 2"])
def test_f2_csv_cells_are_strict_decimals(tmp_path, monkeypatch, cell):
    source = tmp_path / "observations.csv"
    source.write_text(f"x,y\n0,1\n1,3\n2,{cell}\n3,7\n", encoding="utf-8")
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(tmp_path))
    r = call(fit, model="a*x+b", initial="a=1;b=0", data_file=str(source))
    assert not r.ok
    assert r.error.details["param"] == "/data_file/2/y"


def test_f2_csv_plain_decimals_accepted(tmp_path, monkeypatch):
    source = tmp_path / "observations.csv"
    source.write_text("x,y\n0, 1\n1,3.0\n2,+5e0\n3,7.\n", encoding="utf-8")
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(tmp_path))
    r = ok(fit, model="a*x+b", initial="a=1;b=0", data_file=str(source))
    assert list(r.data["parameters"].values()) == pytest.approx([2, 1], abs=1e-8)


# S1: derivative steps scale with |x| and u; errors are estimated; MC/Sobol stand alone.

def test_s1_reciprocal_near_zero():
    # d(1/x)/dx=-1/x^2=-2.7778e6 at 6e-4; u_y=|g|*u=27.78.
    r = ok(sensitivity, expression="1/x", nominal="x=0.0006", standard_uncertainties="x=0.00001")
    assert r.data["sensitivities"][0]["derivative"] == pytest.approx(-1 / .0006**2, rel=1e-8)
    assert r.value.quantity == pytest.approx(1e-5 / .0006**2, rel=1e-8)
    assert r.data["sensitivities"][0]["step_check"]["relative_error_estimate"] <= 1e-6


def test_s1_monte_carlo_log_near_zero_does_not_need_derivatives():
    # E[log X] for X~U(a,b) is (b ln b - a ln a)/(b-a) - 1.
    a, b = .00039, .00041
    r = ok(sensitivity, expression="log(x)", nominal="x=0.0004", method="monte_carlo",
           distributions=f"x,uniform,{a},{b}", samples=20000, seed=3)
    mc = r.data["monte_carlo"]
    assert mc["mean"] == pytest.approx((b * math.log(b) - a * math.log(a)) / (b - a) - 1, abs=5 * mc["mean_standard_error"])


def test_s1_sqrt_gum_near_zero():
    # d sqrt(x)/dx=1/(2 sqrt(4e-4))=25.
    r = ok(sensitivity, expression="sqrt(x)", nominal="x=4e-4", standard_uncertainties="x=1e-5")
    assert r.data["sensitivities"][0]["derivative"] == pytest.approx(25, rel=1e-8)
    assert r.value.quantity == pytest.approx(25e-5, rel=1e-8)


def test_s1_sampling_survives_unresolvable_derivative():
    # sqrt has an infinite derivative at 0: GUM refuses, Monte Carlo and Sobol proceed.
    r = call(sensitivity, expression="sqrt(x)", nominal="x=0", standard_uncertainties="x=1e-5")
    assert not r.ok and r.error.details["param"] == "/nominal/0"
    r = ok(sensitivity, expression="sqrt(x)", nominal="x=0", method="monte_carlo", distributions="x,uniform,0,1e-4", seed=1)
    # E[sqrt(X)] for U(0,c) is (2/3) sqrt(c).
    assert r.data["monte_carlo"]["mean"] == pytest.approx(2 / 3 * .01, rel=.02)
    assert r.data["sensitivities"] is None and r.data["local_derivatives_unavailable"]
    r = ok(sensitivity, expression="sqrt(x)+y", nominal="x=0;y=0", method="sobol", distributions="x,uniform,0,1e-4;y,uniform,0,1e-3", seed=1)
    assert len(r.data["sobol"]["indices"]) == 2


@pytest.mark.parametrize("power", [-2, -1, -.5, .5, 1.5, 3])
@pytest.mark.parametrize("nominal", [1e-6, 1e-3, 1., 1e3, 1e6])
def test_s1_power_law_family(power, nominal):
    # d(x^p)/dx = p x^(p-1); with u=1e-3 x, u_y=|p x^(p-1)| u.
    u = 1e-3 * nominal
    r = ok(sensitivity, expression=f"x^({power!r})", nominal=f"x={nominal!r}", standard_uncertainties=f"x={u!r}")
    exact = power * nominal ** (power - 1)
    assert r.data["sensitivities"][0]["derivative"] == pytest.approx(exact, rel=1e-8)
    assert r.value.quantity == pytest.approx(abs(exact) * u, rel=1e-8)
