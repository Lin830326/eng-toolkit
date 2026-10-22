"""CMB, Morrow, SWT, notch products and independent scalar roots."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.fatigue.strain_life import strain_life
from tests.fatigue_testing import SL, call, close, ok, table, v


@pytest.mark.parametrize("method",["none","morrow","swt"])
def test_known_reversal_life(method):
    # At 2N=10000: elastic .005*10000^-.1, plastic .5*10000^-.5.
    mean=100
    amplitude=((1000-mean)/200000 if method=="morrow" else .005)*10000**-.1+.5*10000**-.5
    args={**SL,"strain_amplitude":f"{amplitude:.16g} dimensionless","mean_method":method,"mean_stress":"100 MPa"}
    if method=="swt":
        # Choose sigma_max so sigma_max*eps*E equals the two published SWT RHS terms at 10000 reversals.
        maximum=(1000**2*10000**-.2+1000*.5*200000*10000**-.6)/(amplitude*200000)
        args["maximum_stress"]=f"{maximum:.16g} MPa"
    r=ok(strain_life,**args)
    close(v(r,"reversals_to_failure"),10000,rel=5e-9)
    close(v(r,"life"),5000,rel=5e-9)
    # .005*x^-.1=.5*x^-.5 => x^.4=100 => transition=100000 reversals.
    close(v(r,"transition_reversals"),100000)


def test_ramberg_osgood_round_numbers_and_masing():
    # sigma=100 MPa, K'=1000 MPa,n'=.5,E=200000 MPa gives eps=.0005+.01=.0105.
    r=ok(strain_life,mode="cyclic",elastic_modulus="200 GPa",cyclic_strength="1000 MPa",cyclic_exponent=.5,stress_amplitude="100 MPa")
    close(v(r,"strain_amplitude"),.0105)
    # Neuber target 100*.0105=1.05 MPa, so nominal sqrt(1.05*E)/Kt at Kt=2.
    nominal=math.sqrt(1.05*200000)/2
    a=ok(strain_life,mode="notch",elastic_modulus="200 GPa",cyclic_strength="1000 MPa",cyclic_exponent=.5,
         nominal_stress=f"{nominal:.15g} MPa",kt=2)
    b=ok(strain_life,mode="notch",elastic_modulus="200 GPa",cyclic_strength="1000 MPa",cyclic_exponent=.5,
         nominal_stress=f"{2*nominal:.15g} MPa",kt=2,range_input=True)
    close(v(a,"local_stress"),100)
    close(v(a,"strain_amplitude"),.0105)
    close(v(b,"local_stress_range"),200)
    close(v(b,"local_strain_range"),.021)


@pytest.mark.parametrize("rule",["neuber","glinka"])
def test_random_notch_roots_against_scipy(rule):
    optimize=pytest.importorskip("scipy.optimize")
    rng=np.random.default_rng(1961)
    for nominal in rng.uniform(50,250,12):
        e,k,n,kt=200000,1200,.18,2
        target=(kt*nominal)**2/e
        def fn(s):
            return s*s/e+s*(s/k)**(1/n)*(1 if rule=="neuber" else 2/(n+1))-target
        expected=optimize.brentq(fn,1e-9,kt*nominal,xtol=1e-11)
        r=ok(strain_life,mode="notch",elastic_modulus="200 GPa",cyclic_strength="1200 MPa",cyclic_exponent=n,
             nominal_stress=f"{nominal} MPa",kt=kt,notch_rule=rule)
        close(v(r,"local_stress"),expected,rel=5e-9)
        if rule=="neuber":
            close(v(r,"local_stress")*v(r,"strain_amplitude"),target,rel=5e-9)


@pytest.mark.parametrize("estimate",["universal","modified_universal"])
def test_slopes_estimates(estimate):
    r=ok(strain_life,mode="estimate",elastic_modulus="200 GPa",ultimate_strength="600 MPa",reduction_of_area=.5,estimate_method=estimate)
    # Manson original range form 3.5 Su/E*N^-.12+D^.6*N^-.6, D=ln2; divide by2, N=(2N)/2.
    if estimate=="universal":
        close(v(r,"fatigue_strength"),1.75*2**.12*600)
        close(v(r,"fatigue_ductility"),.5*2**.6*math.log(2)**.6)
    else:
        # Muralidharan-Manson amplitude/reversal coefficients.
        close(v(r,"fatigue_strength"),.623*200000*.003**.832)
        close(v(r,"fatigue_ductility"),.0196*math.log(2)**.155*.003**-.53)
    assert any("factor-of-three" in w for w in r.warnings)


def test_block_counts_and_damage():
    r=ok(strain_life,**SL,mode="block",cyclic_strength="1000 MPa",cyclic_exponent=.2,
         reversal_levels="100 MPa;-100 MPa;100 MPa",kt=1)
    rows=table(r)
    # Two equal half cycles add to one full cycle; each contribution is .5/N.
    close(r.data["counted_cycles"],1)
    close(v(r,"damage_per_block"),sum(float(row["count"])/float(row["life_cycles"]) for row in rows))
    close(v(r,"repeats_to_failure"),1/v(r,"damage_per_block"))


def test_life_random_differential():
    optimize=pytest.importorskip("scipy.optimize")
    rng=np.random.default_rng(73)
    for ea in rng.uniform(.002,.02,12):
        expected=optimize.brentq(lambda x:.005*math.exp(-.1*x)+.5*math.exp(-.5*x)-ea,0,math.log(1e18))
        r=ok(strain_life,**SL,strain_amplitude=f"{ea} dimensionless")
        close(math.log(v(r,"reversals_to_failure")),expected,rel=3e-11)


@pytest.mark.parametrize("args",[
    dict(strain_amplitude="1e-14 dimensionless"),dict(strain_amplitude="0.2 dimensionless"),
    dict(strain_amplitude="0.01 dimensionless",mean_method="morrow",mean_stress="1000 MPa"),
    dict(strain_amplitude="0.01 dimensionless",c=-.05),
    dict(strain_amplitude="0.01 dimensionless",cyclic_exponent=.2),
    dict(mode="block",reversal_levels="100 MPa;100 MPa"),
    dict(mode="notch",nominal_stress="100 MPa",stress_amplitude="100 MPa"),
])
def test_strain_refusals(args):
    assert not call(strain_life,**{**SL,**args}).ok
