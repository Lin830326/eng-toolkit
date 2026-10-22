"""Independent closed forms for uncertainty and global sensitivity."""
import math

import pytest

from eng_toolkit.domains.optimize.sensitivity import sensitivity
from tests.optimize_testing import call, ok


def test_product_derivatives_and_correlated_gum():
    # g=(y,x)=(3,2); variance=9*.1^2+4*.2^2+2*3*2*.5*.1*.2=.37.
    r = ok(sensitivity,expression="x*y",nominal="x=2;y=3",standard_uncertainties="x=.1;y=.2",correlation="[[1,0.5],[0.5,1]]")
    assert [s["derivative"] for s in r.data["sensitivities"]] == pytest.approx([3,2])
    assert r.value.quantity == pytest.approx(math.sqrt(.37))
    assert [s["elasticity"] for s in r.data["sensitivities"]] == pytest.approx([1,1])
    assert sum(s["uncertainty_contribution"] for s in r.data["sensitivities"]) == pytest.approx(.37)
    assert [row["name"] for row in r.data["ranking"]]==["y","x"]


def test_second_order_gaussian_square_exact_moments():
    # E[X^2]=mu^2+s^2=4.09; Var(X^2)=4*mu^2*s^2+2*s^4.
    r = ok(sensitivity,expression="x^2",nominal="x=2",standard_uncertainties="x=.3",second_order=True)
    assert r.data["gum"]["mean"] == pytest.approx(4.09,abs=1e-8)
    assert r.value.quantity**2 == pytest.approx(16*.09+2*.09**2,abs=1e-8)


def test_monte_carlo_normal_matches_linear_gum_and_is_reproducible():
    # Mean=2*1+2=4; variance=4*.2^2+.3^2=.25; coverage=4 +/-1.96*.5.
    args = dict(expression="2*x+y",nominal="x=1;y=2",standard_uncertainties="x=.2;y=.3",method="monte_carlo",samples=20000,seed=17)
    a,b = ok(sensitivity,**args),ok(sensitivity,**args)
    mc = a.data["monte_carlo"]
    assert mc == b.data["monte_carlo"]
    assert mc["mean"] == pytest.approx(4,abs=5*mc["mean_standard_error"])
    assert mc["standard_deviation"] == pytest.approx(.5,rel=.03)
    assert mc["coverage_interval_95"] == pytest.approx([3.02,4.98],abs=.04)


@pytest.mark.parametrize("distribution,mean,variance",[
    ('["uniform",0,2]',1,1/3),
    ('["triangular",0,1,2]',1,1/6),
    ('["lognormal",0,0.2]',math.exp(.02),(math.exp(.04)-1)*math.exp(.04)),
])
def test_distribution_moments(distribution,mean,variance):
    r = ok(sensitivity,expression="x",nominal="x=1",method="monte_carlo",distributions='{"x":'+distribution+'}',samples=30000,seed=73)
    mc = r.data["monte_carlo"]
    assert mc["mean"] == pytest.approx(mean,abs=5*math.sqrt(variance/30000))
    assert mc["standard_deviation"]**2 == pytest.approx(variance,rel=.04)


def test_sobol_additive_and_product_known_indices():
    # Uniform variance=1/12. Additive fractions=1/(1+4),4/(1+4).
    # Product variance=1/9-1/16=7/144; first-order variance=1/48 ->3/7; total=4/7.
    kwargs = dict(nominal="x=.5;y=.5",method="sobol",samples=20000,distributions="x,uniform,0,1;y,uniform,0,1",seed=29)
    additive = ok(sensitivity,expression="x+2*y",**kwargs).data["sobol"]["indices"]
    assert [r["first_order"] for r in additive] == pytest.approx([.2,.8],abs=.025)
    assert [r["total_order"] for r in additive] == pytest.approx([.2,.8],abs=.025)
    product = ok(sensitivity,expression="x*y",**kwargs).data["sobol"]["indices"]
    assert [r["first_order"] for r in product] == pytest.approx([3/7,3/7],abs=.03)
    assert [r["total_order"] for r in product] == pytest.approx([4/7,4/7],abs=.03)


def test_singular_psd_covariance_is_valid():
    r = ok(sensitivity,expression="x-y",nominal="x=1;y=1",standard_uncertainties="x=.1;y=.1",correlation="[[1,1],[1,1]]")
    assert r.value.quantity < 1e-8
    assert r.data["sensitivities"][0]["elasticity"] is None


@pytest.mark.parametrize("kwargs",[
    {}, {"correlation":"[[1,2],[2,1]]"}, {"standard_uncertainties":"x=-1"},
    {"distributions":'{"x":["normal",0,-1],"y":["normal",0,1]}',"method":"monte_carlo"},
    {"method":"sobol","correlation":"[[1,.2],[.2,1]]"},
    {"expression":"sqrt(-x)"}, {"expression":"__import__('os')"},
    {"method":"sobol","samples":100000}, {"samples":True,"method":"monte_carlo"},
])
def test_refusals_are_structured(kwargs):
    base = dict(expression="x+y",nominal="x=1;y=1") if kwargs else {}
    r = call(sensitivity,**{**base,**kwargs})
    assert not r.ok


def test_correlated_normal_monte_carlo():
    r = ok(sensitivity,expression="x+y",nominal="x=0;y=0",standard_uncertainties="x=1;y=1",correlation="[[1,0.5],[0.5,1]]",method="monte_carlo",samples=20000)
    assert r.data["monte_carlo"]["standard_deviation"] == pytest.approx(math.sqrt(3),rel=.03)


def test_monte_carlo_disagreement_is_visible():
    r = ok(sensitivity,expression="x*x",nominal="x=0",standard_uncertainties="x=1",method="monte_carlo",samples=10000)
    assert any("disagree" in w for w in r.warnings)


def test_full_monte_carlo_row_cap_has_derivative_allowance():
    r = ok(sensitivity,expression="x",nominal="x=0",standard_uncertainties="x=1",method="monte_carlo",samples=200000)
    assert r.data["monte_carlo"]["samples"]==200000
    assert 200000 < r.data["evaluations"] <= 202048
    assert len(r.data["monte_carlo"]["coverage_interval_endpoint_standard_errors_approx"])==2


def test_monte_carlo_above_row_cap_refuses():
    assert not call(sensitivity,expression="x",nominal="x=0",method="monte_carlo",samples=200001).ok
