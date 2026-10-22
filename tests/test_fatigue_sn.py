"""Hand calculations and independent fits for S-N and mean-stress criteria."""

import math
from statistics import NormalDist

import numpy as np
import pytest

from eng_toolkit.domains.fatigue.sn import sn
from tests.fatigue_testing import SN, call, close, ok, table, v


def test_two_point_basquin_and_runout():
    r = ok(sn, **{**SN, "points": SN["points"]+";800 MPa,10000000,runout"}, cycles="1000 cycle", alternating_stress="200 MPa")
    # b=log10(200/400)/log10(1e6/1e3); A=400/1000^b=800 MPa.
    close(v(r,"basquin_a"),800)
    close(v(r,"basquin_b"),math.log10(.5)/3)
    close(v(r,"stress_at_life"),400)
    close(v(r,"life"),1e6)
    close(r.data["fit"]["r_squared"],1)
    # Default E739 fit reports its residual scatter in log10 life; two points leave it undefined.
    assert r.data["fit"]["standard_error_log10_life"] is None
    assert len(r.data["excluded_runouts"]) == 1
    assert len(table(r)) == 61


def test_generated_power_law_and_scatter():
    n = np.geomspace(100,1e8,19)
    points = ";".join(f"{1200*x**-.12:.14g} MPa,{x:.14g}" for x in n)
    r = ok(sn, mode="fit", points=points)
    # Every log10 stress equals log10(1200)-.12*log10 N.
    close(v(r,"basquin_a"),1200)
    close(v(r,"basquin_b"),-.12)
    close(r.data["fit"]["life_scatter_factor_2se"],1)


@pytest.mark.parametrize("method,expected",[
    ("none",100), ("goodman",125), ("gerber",100/.96),
    ("soderberg",100/.75), ("morrow",100/.9), ("swt",math.sqrt(20000)),
    ("walker",100**.25*200**.75),
])
def test_equivalent_stresses(method,expected):
    # Sa=100, Sm=100, Su=500, Sy=400, sigma_f'=1000 MPa; direct textbook equations.
    r=ok(sn,mode="correction",alternating_stress="100 MPa",mean_stress="100 MPa",ultimate_strength="500 MPa",
         yield_strength="400 MPa",fatigue_strength_coefficient="1000 MPa",mean_method=method,walker_gamma=.25)
    close(v(r,"equivalent_amplitude"),expected)


@pytest.mark.parametrize("method,expected",[("goodman",1/(.5+.2)),("gerber",2/(.5+math.sqrt(.25+.16))),
                                            ("soderberg",1/(.5+.25)),("morrow",1/(.5+.1))])
def test_proportional_load_safety(method,expected):
    # Sa/Se=.5, Sm/Su=.2; Gerber solves .5*n+.04*n^2=1.
    r=ok(sn,mode="correction",alternating_stress="100 MPa",mean_stress="100 MPa",ultimate_strength="500 MPa",
         yield_strength="400 MPa",fatigue_strength_coefficient="1000 MPa",endurance_limit="200 MPa",mean_method=method)
    close(v(r,"safety_factor"),expected)


def test_compressive_mean_has_no_benefit_and_endurance_no_infinity():
    r=ok(sn,mode="curve",basquin_a="800 MPa",basquin_b=math.log10(.5)/3,endurance_limit="200 MPa",
         alternating_stress="100 MPa",mean_stress="-50 MPa",ultimate_strength="500 MPa",mean_method="goodman")
    close(v(r,"equivalent_amplitude"),100)
    close(v(r,"knee_cycles"),1e6)
    assert r.data["life_cycles"] is None


@pytest.mark.parametrize("percent,ke,z",[(50,1,0),(90,.897,1.2815515655446004),(95,.868,1.6448536269514722),
                                       (99,.814,2.3263478740408408),(99.9,.753,3.0902323061678132),(99.99,.702,3.719016485455709)])
def test_reliability_table_against_normal_quantiles(percent,ke,z):
    # Published z values imply ke=1-.08*z, rounded Shigley values within .0006.
    close(NormalDist().inv_cdf(percent/100),z,rel=1e-12)
    close(ke,1-.08*z,abs=.0006)
    r=ok(sn,mode="estimate",ultimate_strength="600 MPa",size_factor=1,reliability=percent)
    close(v(r,"endurance_limit"),300*ke)


def test_marin_surface_size_and_high_strength_cap():
    r=ok(sn,mode="estimate",ultimate_strength="1600 MPa",diameter="20 mm",surface_finish="machined",temperature_factor=.9,reliability=99)
    # Se'=700 MPa; ka=4.51*1600^-.265, kb=1.24*20^-.107, kc=1, kd=.9, ke=.814.
    close(v(r,"endurance_limit"),700*4.51*1600**-.265*1.24*20**-.107*.9*.814)


@pytest.mark.parametrize("method,expected",[("peterson",.5),("neuber",.5)])
def test_notch_measured_material_length(method,expected):
    r=ok(sn,mode="notch",notch_method=method,kt=3,notch_radius="1 mm",notch_material_length="1 mm")
    # r=a gives q=1/2 for both methods; Kf=1+.5*(3-1)=2.
    close(v(r,"notch_sensitivity"),expected)
    close(v(r,"fatigue_notch_factor"),2)


def test_peterson_steel_fit_and_imperial_equivalence():
    r=ok(sn,mode="notch",notch_method="peterson",ultimate_strength="690 MPa",kt=2,notch_radius="0.1 inch")
    # a=.0254*(2070/690)^1.8 mm and r=2.54 mm.
    close(v(r,"notch_sensitivity"),1/(1+.0254*3**1.8/2.54))
    a=ok(sn,mode="curve",basquin_a="100 ksi",alternating_stress="10 ksi")
    close(v(a,"life"),1e10)


def test_random_fits_against_scipy():
    stats=pytest.importorskip("scipy.stats")
    rng=np.random.default_rng(982)
    for _ in range(8):
        x=np.linspace(3,7,12)
        y=3-.1*x+rng.normal(0,.02,12)
        text=";".join(f"{10**s:.15g} MPa,{10**n:.15g}" for n,s in zip(x,y))
        # s_on_n regresses log S on log N; the default E739 fit regresses log N on log S and inverts.
        r=ok(sn,mode="fit",points=text,fit_method="s_on_n")
        expected=stats.linregress(x,y)
        close(v(r,"basquin_b"),expected.slope)
        close(r.data["fit"]["r_squared"],expected.rvalue**2)
        e739=ok(sn,mode="fit",points=text)
        inverse=stats.linregress(y,x)
        close(v(e739,"basquin_b"),1/inverse.slope)
        close(v(e739,"basquin_a"),10**(-inverse.intercept/inverse.slope))
        close(e739.data["fit"]["r_squared"],inverse.rvalue**2)


@pytest.mark.parametrize("args",[
    dict(mode="fit",points="100 MPa,1000;200 MPa,10000"),
    dict(mode="fit",points="100 MPa,1000,runout;200 MPa,10000,runout"),
    dict(mode="fit",points="100 MPa,1000;200 MPa,1000"),
    dict(mode="correction",alternating_stress="100 MPa",mean_stress="500 MPa",ultimate_strength="500 MPa",mean_method="goodman"),
    dict(mode="correction",alternating_stress="300 MPa",yield_strength="200 MPa"),
    dict(mode="notch",notch_method="peterson",notch_radius="1 mm",ultimate_strength="200 MPa"),
    dict(mode="estimate",ultimate_strength="600 MPa",diameter="1 mm"),
    dict(mode="curve",basquin_a="800 MPa",endurance_limit="200 MPa",knee_cycles="100 cycle"),
    dict(mode="estimate",ultimate_strength="600 MPa",size_factor=1,cycles="10 cycle"),
])
def test_sn_refusals(args):
    assert not call(sn,**args).ok
