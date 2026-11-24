"""optimize_fit with parameters of very different size (UAT round 2, F5-8).

The Steinhart-Hart form T(R) = 1/(a + b ln R + c ln^3 R) has a ~ 1e-3, b ~ 2e-4 and
c ~ 1e-7. The old finite-difference step cbrt(eps)*max(1,|x|) was 6e-6 for c, which moved
c ln^3 R (ln^3 R up to 1124) by 7e-3, more than the denominator itself (2.7e-3), so the
Jacobian crossed the model's pole: the fit stopped with 'LM trust damping could not improve
the residual' (multistart 1) or ran out of time (multistart 8). The solver now works in
z = x/|initial|, with column-equilibrated damping, rank and curvature tests.
"""

import json
import time

import numpy as np
import pytest

import eng_toolkit.domains.optimize.fit as fit_module
from eng_toolkit.domains.optimize.fit import fit
from optimize_testing import call, ok

# TDK B57861S0103F040, R/T characteristic no. 8016, 0..100 C (uat/scenarios/F5_ntc_calibration_heatsink_opt.json).
CELSIUS = list(range(0, 101, 5))
OHM = [32650, 25390, 19900, 15710, 12490, 10000, 8057, 6531, 5327, 4369, 3603, 2986, 2488, 2083,
       1752, 1481, 1258, 1072, 917.7, 788.5, 680]
DATA = json.dumps({"x": OHM, "y": CELSIUS})
MODEL = "1/(a+b*log(x)+c*log(x)^3)-273.15"
# uat/truth/F5_ntc_calibration_heatsink_opt.py: scipy curve_fit of 1/T = a + b lnR + c lnR^3 with
# sigma = 0.01/T^2 (temperature-equivalent weights), covariance scaled by chi-square, t(0.975, 18).
TRUTH = [0.0011244781164535048, 0.00023488082161352393, 8.479715081723547e-08]
TRUTH_HALF_WIDTH = [1.3676107946629102e-06, 2.5276721632598695e-07, 1.2198482159092689e-09]


def steinhart_hart_oracle():
    """Least squares in temperature with the exact Jacobian (Gauss-Newton, scaled columns)."""
    log_r = np.log(np.array(OHM, dtype=float))
    basis = np.column_stack([np.ones_like(log_r), log_r, log_r**3])
    point = np.array(TRUTH)
    for _ in range(30):
        g = basis@point
        residual = 1/g - 273.15 - np.array(CELSIUS, dtype=float)
        jac = -basis/g[:, None]**2
        norms = np.linalg.norm(jac, axis=0)
        point = point + np.linalg.lstsq(jac/norms, -residual, rcond=None)[0]/norms
    g = basis@point
    residual = 1/g - 273.15 - np.array(CELSIUS, dtype=float)
    jac = -basis/g[:, None]**2
    covariance = np.linalg.inv(jac.T@jac)*(residual@residual)/18
    return point, covariance


def half_widths(result):
    return [(hi-lo)/2 for lo, hi in result.data["confidence_intervals_95"].values()]


def test_steinhart_hart_temperature_form_reproduces_the_truth_fast():
    # The transcript's first Steinhart-Hart call with multistart 1, as the judge re-ran it.
    started = time.perf_counter()
    r = ok(fit, model=MODEL, initial="a=0.001;b=0.00025;c=0.0000001", data=DATA)
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0
    assert r.data["parameter_scales"] == {"a": 0.001, "b": 0.00025, "c": 1e-7}
    # Six significant digits. The truth weights 1/T by T^2 instead of fitting T itself;
    # the two objectives differ at second order (1e-6 relative in c).
    assert list(r.data["parameters"].values()) == pytest.approx(TRUTH, rel=2e-6)
    assert half_widths(r) == pytest.approx(TRUTH_HALF_WIDTH, rel=1e-4)
    assert r.data["max_absolute_residual"] == pytest.approx(0.019277, abs=2e-5)
    assert r.data["max_residual_coordinates"]["x"] == 1752  # 70 C
    assert r.data["rms"] == pytest.approx(0.0075609, abs=1e-6)
    certificate = r.data["certificate"]
    assert certificate["passed"] and certificate["projected_gradient_scaled_inf"] <= 1e-8
    assert certificate["objective_curvature"]["minimum_eigenvalue"] > 0


def test_steinhart_hart_matches_an_exact_jacobian_oracle():
    point, covariance = steinhart_hart_oracle()
    r = ok(fit, model=MODEL, initial="a=0.001;b=0.00025;c=0.0000001", data=DATA, tolerance=1e-10)
    assert list(r.data["parameters"].values()) == pytest.approx(point, rel=1e-8)
    assert np.array(r.data["covariance"]) == pytest.approx(covariance, rel=1e-6)


def test_steinhart_hart_truth_formulation_is_reproduced_exactly():
    # Same problem as the truth script: 1/T on ln R with sigma = 0.01/T^2.
    kelvin = np.array(CELSIUS, dtype=float) + 273.15
    r = ok(fit, model="a+b*log(x)+c*log(x)^3", initial="a=0.001;b=0.0002;c=0.0000001",
           data=json.dumps({"x": OHM, "y": (1/kelvin).tolist()}), sigma=json.dumps((0.01/kelvin**2).tolist()))
    assert list(r.data["parameters"].values()) == pytest.approx(TRUTH, rel=1e-8)
    assert half_widths(r) == pytest.approx(TRUTH_HALF_WIDTH, rel=1e-5)


def test_the_truth_constants_match_scipy():
    optimize = pytest.importorskip("scipy.optimize")
    stats = pytest.importorskip("scipy.stats")
    kelvin = np.array(CELSIUS, dtype=float) + 273.15
    log_r = np.log(np.array(OHM, dtype=float))
    popt, pcov = optimize.curve_fit(lambda x, a, b, c: a + b*x + c*x**3, log_r, 1/kelvin, p0=[1e-3, 2e-4, 1e-7],
                                    sigma=0.01/kelvin**2, absolute_sigma=False)
    assert popt == pytest.approx(TRUTH, rel=1e-9)
    assert stats.t.ppf(.975, 18)*np.sqrt(np.diag(pcov)) == pytest.approx(TRUTH_HALF_WIDTH, rel=1e-6)


@pytest.mark.parametrize("arguments", [
    {"initial": "a=0.001;b=0.00025;c=0.0000001", "multistart": 8},
    {"initial": "a=0.0011259;b=0.00023461;c=0.00000008603", "multistart": 1, "time_limit": "30 s"},
])
def test_the_other_transcript_calls_converge(arguments):
    r = ok(fit, model=MODEL, data=DATA, **arguments)
    assert list(r.data["parameters"].values()) == pytest.approx(TRUTH, rel=2e-6)
    assert r.data["multistart"]["starts"] == arguments["multistart"]


def test_certificate_and_covariance_are_scale_invariant():
    # The hand-rescaled model of the transcript (p1 = 1000 a, p2 = 1000 b, p3 = 1e7 c) and the
    # raw model from the same start give the same fit, certificate and condition.
    raw = ok(fit, model=MODEL, initial="a=0.00112587;b=0.00023461;c=0.00000008603", data=DATA)
    scaled = ok(fit, model="1000/(p1+p2*log(x)+p3*0.0001*log(x)^3)-273.15", initial="p1=1.12587;p2=0.23461;p3=0.8603", data=DATA)
    factor = np.array([1e-3, 1e-3, 1e-7])
    assert np.array(list(scaled.data["parameters"].values()))*factor == pytest.approx(list(raw.data["parameters"].values()), rel=1e-7)
    assert np.array(scaled.data["covariance"])*np.outer(factor, factor) == pytest.approx(np.array(raw.data["covariance"]), rel=1e-5)
    assert scaled.data["iterations"] == raw.data["iterations"]
    assert scaled.data["jacobian_condition"] == pytest.approx(raw.data["jacobian_condition"], rel=1e-6)
    a, b = raw.data["certificate"], scaled.data["certificate"]
    # The last step's rounding differs between the two forms; the metric agrees to a few %.
    assert b["projected_gradient_scaled_inf"] == pytest.approx(a["projected_gradient_scaled_inf"], rel=.05)
    assert b["residual_scale"] == pytest.approx(a["residual_scale"], rel=1e-9)
    assert b["objective_curvature"]["minimum_eigenvalue"] == pytest.approx(a["objective_curvature"]["minimum_eigenvalue"], rel=1e-4)
    assert np.array(b["gradient_scale"])/factor == pytest.approx(a["gradient_scale"], rel=1e-6)


# The certificate floor was one unit of y (max(1,||r||)): small observations were certified
# too early. Found while reproducing the truth's 1/T form (y ~ 3e-3).

@pytest.mark.parametrize("scale", [1e-10, 1e-6, 1.])
def test_small_observations_are_not_certified_at_the_start(scale):
    r = ok(fit, model="a*x", initial="a=0", data=json.dumps({"x": [1, 2, 3], "y": [scale, 2*scale, 3*scale]}))
    # Before: a = 0 after 0 iterations for 1e-10, and a = 9.99e-7 for 1e-6.
    assert r.data["parameters"]["a"] == pytest.approx(scale, rel=1e-10)


def test_unweighted_inverse_temperature_form_is_the_exact_least_squares_solution():
    kelvin = np.array(CELSIUS, dtype=float) + 273.15
    log_r = np.log(np.array(OHM, dtype=float))
    exact = np.linalg.lstsq(np.column_stack([np.ones_like(log_r), log_r, log_r**3]), 1/kelvin, rcond=None)[0]
    r = ok(fit, model="a+b*log(x)+c*log(x)^3", initial="a=0.001;b=0.0002;c=0.0000001",
           data=json.dumps({"x": OHM, "y": (1/kelvin).tolist()}))
    # Before: a = 1.12794e-3 (exact 1.12497e-3), certified with a residual-gradient cosine of 0.025.
    assert list(r.data["parameters"].values()) == pytest.approx(exact, rel=1e-8)


def test_certificate_is_invariant_to_the_scale_of_y():
    x = np.linspace(0, 4, 35)
    y = 2*np.exp(-.7*x) + .01*np.sin(5*x)
    results = [ok(fit, model="a*exp(-b*x)", initial=f"a={1.8*k!r};b=0.5", data=json.dumps({"x": x.tolist(), "y": (k*y).tolist()}))
               for k in (1e-12, 1., 1e12)]
    for k, r in zip((1e-12, 1., 1e12), results):
        assert r.data["parameters"]["a"]/k == pytest.approx(results[1].data["parameters"]["a"], rel=1e-9)
        assert r.data["parameters"]["b"] == pytest.approx(results[1].data["parameters"]["b"], rel=1e-9)
        assert r.data["iterations"] == results[1].data["iterations"]
        assert r.data["certificate"]["projected_gradient_scaled_inf"] <= 1e-8


# Synthetic: parameters from 1.5e-8 to 1.2e3, the smallest inside an exponential, so its
# finite-difference step matters: the old 6e-6 step changed c*x^3 by 750 at x = 500, and
# exp(-750) refused as an intermediate underflow.
X = np.linspace(0, 500, 61)
SYNTHETIC = "a*exp(-c*x^3)+d*(1-exp(-x/b))"
TRUE = {"a": 1.2e3, "b": 150., "c": 1.5e-8, "d": 40.}
BOUNDS = "[[0,10000],[1,10000],[0,0.000001],[0,1000]]"


def synthetic(x, a, b, c, d):
    return a*np.exp(-c*x**3) + d*(1-np.exp(-x/b))


def synthetic_jacobian(point, sigma):
    a, b, c, d = point
    e, f = np.exp(-c*X**3), np.exp(-X/b)
    return np.column_stack([e, -d*f*X/b**2, -a*X**3*e, 1-f])/sigma[:, None]


def synthetic_oracle(y, sigma):
    point = np.array(list(TRUE.values()))
    for _ in range(40):
        jac = synthetic_jacobian(point, sigma)
        residual = (synthetic(X, *point)-y)/sigma
        norms = np.linalg.norm(jac, axis=0)
        point = point + np.linalg.lstsq(jac/norms, -residual, rcond=None)[0]/norms
    jac = synthetic_jacobian(point, sigma)
    return point, np.linalg.inv(jac.T@jac)


def test_synthetic_parameters_spanning_eleven_decades_noiseless():
    y = synthetic(X, *TRUE.values())
    r = ok(fit, model=SYNTHETIC, initial="a=1000;b=100;c=0.00000001;d=30", bounds=BOUNDS,
           data=json.dumps({"x": X.tolist(), "y": y.tolist()}), tolerance=1e-10)
    assert r.data["parameters"] == pytest.approx(TRUE, rel=1e-8)
    assert r.data["parameter_scales"] == {"a": 1000., "b": 100., "c": 1e-8, "d": 30.}
    assert r.data["jacobian_condition"] < 1e6
    assert not any("rank deficient" in w or "bound" in w for w in r.warnings)


@pytest.mark.parametrize("seed", range(4))
def test_synthetic_noisy_against_exact_jacobian_oracle(seed):
    rng = np.random.default_rng(seed)
    sigma = 0.5 + 0.01*X
    y = synthetic(X, *TRUE.values()) + rng.normal(size=X.size)*sigma
    point, covariance = synthetic_oracle(y, sigma)
    # Bounds keep b > 0: from this start an unbounded LM (SciPy's too) can follow b -> -inf.
    r = ok(fit, model=SYNTHETIC, initial="a=1000;b=100;c=0.00000001;d=30", absolute_sigma=True, bounds=BOUNDS,
           data=json.dumps({"x": X.tolist(), "y": y.tolist()}), sigma=json.dumps(sigma.tolist()), tolerance=1e-10)
    assert list(r.data["parameters"].values()) == pytest.approx(point, rel=1e-7)
    assert np.array(r.data["covariance"]) == pytest.approx(covariance, rel=1e-5)


# Budget stops: a start that uses up its share no longer takes the other starts' time, and a
# clock refusal names /time_limit only when the caller chose the limit.

def expire_shares(monkeypatch, starts_to_expire):
    original = fit_module._Share.__enter__
    entered = []
    def enter(self):
        original(self)
        if len(entered) in starts_to_expire:
            self.budget.deadline = time.monotonic() - 1
        entered.append(True)
        return self
    monkeypatch.setattr(fit_module._Share, "__enter__", enter)


@pytest.mark.parametrize("time_limit,param", [(None, "/model"), ("20 s", "/time_limit")])
def test_no_start_converges_in_its_share(monkeypatch, time_limit, param):
    expire_shares(monkeypatch, range(64))
    extra = {} if time_limit is None else {"time_limit": time_limit}
    r = call(fit, model=MODEL, initial="a=0.001;b=0.00025;c=0.0000001", data=DATA, multistart=2, **extra)
    assert not r.ok
    assert r.error.family == "optimize.not_converged"
    assert r.error.details["param"] == param
    assert r.error.details["reason"] == "optimize.time_budget_exceeded"
    assert "2 of 2 starts stopped" in r.error.message


def test_a_stopped_start_does_not_discard_a_converged_one(monkeypatch):
    expire_shares(monkeypatch, {1, 2})
    r = ok(fit, model=MODEL, initial="a=0.001;b=0.00025;c=0.0000001", data=DATA, multistart=3)
    assert r.data["multistart"] == {"starts": 3, "converged": 1, "stopped_at_budget_share": 2}
    assert any("2 of 3 starts stopped" in w for w in r.warnings)
    assert list(r.data["parameters"].values()) == pytest.approx(TRUTH, rel=2e-6)


def test_evaluation_shares_stop_starts_without_spending_the_call(monkeypatch):
    from eng_toolkit.domains.optimize._common import Budget
    monkeypatch.setattr(fit_module, "Budget", lambda duration: Budget(duration, max_evaluations=10))
    r = call(fit, model="a*x", initial="a=1", data='{"x":[1,2,3,4],"y":[2,4,6,8]}', multistart=5)
    assert not r.ok
    assert r.error.family == "optimize.not_converged"
    assert r.error.details["param"] == "/model"
    assert r.error.details["reason"] == "optimize.evaluation_budget_exceeded"
    assert "5 of 5 starts stopped" in r.error.message


@pytest.mark.parametrize("time_limit,param", [(None, "/model"), ("20 s", "/time_limit")])
def test_clock_refusal_names_time_limit_only_when_given(monkeypatch, time_limit, param):
    from eng_toolkit.domains.optimize import _common
    original = _common.Budget.check
    def expired(self):
        self.deadline = -1
        original(self)
    monkeypatch.setattr(_common.Budget, "check", expired)
    extra = {} if time_limit is None else {"time_limit": time_limit}
    r = call(fit, model=MODEL, initial="a=0.001;b=0.00025;c=0.0000001", data=DATA, **extra)
    assert not r.ok
    assert r.error.family == "input.out_of_range"
    assert r.error.details["reason"] == "optimize.time_budget_exceeded"
    assert r.error.details["param"] == param
