"""Material reductions, offset temperatures and fit-span refusals."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.fatigue.material import material
from tests.fatigue_testing import MT, call, close, ok, v


def test_engineering_true_roundtrip():
    r=ok(material,**MT)
    # sigma_true=300*1.1=330 MPa; eps_true=ln(1.1).
    close(v(r,"stress"),330)
    close(v(r,"strain"),math.log(1.1))
    back=ok(material,direction="true_to_engineering",stress="330 MPa",strain=f"{math.log(1.1)} dimensionless",uniform_strain=".2 dimensionless")
    close(v(back,"stress"),300)
    close(v(back,"strain"),.1)


@pytest.mark.parametrize("model",["ramberg_osgood","hollomon"])
def test_power_law_fit(model):
    # K=1000 MPa,n=.2; plastic strain 1e-4..1e-1 spans three decades, sigma=1000*eps_pl^.2.
    plastic=np.geomspace(1e-4,.1,20)
    stress=1000*plastic**.2
    strain=plastic+stress/200000 if model=="ramberg_osgood" else plastic
    points=";".join(f"{s:.14g} MPa,{e:.14g} dimensionless" for s,e in zip(stress,strain))
    r=ok(material,mode="fit",points=points,fit_model=model,elastic_modulus="200 GPa" if model=="ramberg_osgood" else "")
    close(v(r,"strength_coefficient"),1000)
    close(v(r,"hardening_exponent"),.2)
    close(r.data["fit"]["r_squared"],1)


def test_noisy_fit_against_independent_curve_fit():
    optimize=pytest.importorskip("scipy.optimize")
    rng=np.random.default_rng(105)
    x=np.linspace(-4,-1,21)
    y=3+.2*x+rng.normal(0,.01,len(x))
    expected,_=optimize.curve_fit(lambda x,intercept,slope:intercept+slope*x,x,y)
    points=";".join(f"{10**s:.15g} MPa,{10**e:.15g} dimensionless" for s,e in zip(y,x))
    r=ok(material,mode="fit",fit_model="hollomon",points=points)
    close(v(r,"strength_coefficient"),10**expected[0],rel=1e-7)
    close(v(r,"hardening_exponent"),expected[1],rel=1e-7)


YIELD_POINTS="0 MPa,0 dimensionless;100 MPa,0.0005 dimensionless;200 MPa,0.001 dimensionless;300 MPa,0.003 dimensionless;320 MPa,0.004 dimensionless;350 MPa,0.01 dimensionless"


def test_offset_yield_and_areas():
    r=ok(material,mode="yield",points=YIELD_POINTS,table_reaches_fracture=True)
    # E=200000 MPa; segment 300+20000*(eps-.003)=200000*(eps-.002)
    # eps=.00355555555556, Sy=311.111111111 MPa.
    sy=2800/9
    close(v(r,"elastic_modulus"),200000)
    close(v(r,"offset_yield"),sy)
    close(v(r,"offset_yield_strain"),.002+sy/200000)
    close(v(r,"resilience"),sy**2/(2*200000))
    # Trapezoids: .025+.075+.5+.31+2.01=2.92 MJ/m^3.
    close(v(r,"toughness"),2.92)
    assert r.data["elastic_fit"]["points"]==3


def test_hardness_inverse():
    r=ok(material,mode="hardness",hardness_hb=200)
    close(v(r,"ultimate_strength"),690)  # 3.45*200
    back=ok(material,mode="hardness",ultimate_strength="690 MPa")
    close(v(back,"brinell_hardness"),200)


@pytest.mark.parametrize("temperature",["500 degC","500 celsius","500 degree_Celsius","932 degF","932 degree_Fahrenheit","773.15 K"])
def test_larson_miller_absolute_temperature_aliases(temperature):
    r=ok(material,mode="larson_miller",temperature=temperature,exposure_time="1000 hour")
    # P=(500+273.15)*(20+log10(1000))=17782.45 K.
    close(v(r,"temperature_kelvin"),773.15)
    close(v(r,"larson_miller_parameter"),17782.45)
    inverse=ok(material,mode="larson_miller",temperature=temperature,larson_parameter="17782.45 K")
    close(v(inverse,"exposure_time"),1000)


def test_typical_properties_are_sourced_and_not_auto_applied():
    r=ok(material,mode="typical")
    close(v(r,"material_count"),8)
    assert r.data["label"]=="typical, not guaranteed, not for design"
    for row in r.data["properties"]:
        assert row["source"] and row["condition"] and row["physical_source"] and row["basis"]
        assert row["E_GPa"][0]>0
    iron=next(row for row in r.data["properties"] if row["material"]=="grey_iron")
    assert iron["yield_MPa"] is None and iron["elongation_percent"] is None
    # Primary manufacturer reference: TIMET annealed 895 MPa minimum, ST-aged 1137 MPa reference.
    titanium=next(row for row in r.data["properties"] if row["material"]=="Ti-6Al-4V")
    assert titanium["ultimate_MPa"]==[895,1137]


@pytest.mark.parametrize("args",[
    dict(strain="0.3 dimensionless"),
    dict(mode="fit",fit_model="hollomon",points="100 MPa,0.01 dimensionless;120 MPa,0.02 dimensionless"),
    dict(mode="fit",elastic_modulus="200 GPa",points="100 MPa,0.0001 dimensionless;200 MPa,0.0002 dimensionless"),
    dict(mode="yield",points="0 MPa,0 dimensionless;100 MPa,0.0005 dimensionless;200 MPa,0.001 dimensionless;300 MPa,0.0015 dimensionless"),
    dict(mode="hardness",hardness_hb=600),
    dict(mode="larson_miller",temperature="-274 celsius",exposure_time="1 hour"),
    dict(mode="larson_miller",temperature="300 delta_degC",exposure_time="1 hour"),
    dict(mode="larson_miller",temperature="300 K",exposure_time="1 hour",larson_parameter="10000 K"),
    dict(mode="larson_miller",temperature="1 K",larson_parameter="1e20 K"),
])
def test_material_refusals(args):
    given={**MT,**args} if "mode" not in args else args
    assert not call(material,**given).ok
