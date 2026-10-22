"""Bounded numerical work, input grammar, and finite edge-case outcomes."""

import inspect
import math

import pytest

from eng_toolkit.domains.fatigue._common import q, root
from eng_toolkit.domains.fatigue.fracture import Integrator, fracture
from eng_toolkit.domains.fatigue.material import material
from eng_toolkit.domains.fatigue.rainflow import rainflow
from eng_toolkit.domains.fatigue.sn import sn
from eng_toolkit.domains.fatigue.strain_life import strain_life
from eng_toolkit.units import UnitsError
from tests.fatigue_testing import FM, MT, RF, SL, SN, call, close


def test_root_requires_bracket_and_convergence():
    with pytest.raises(UnitsError,match="not bracketed"):
        root(lambda x:x*x+1,0,1,"test")
    with pytest.raises(UnitsError,match="did not converge") as exc:
        root(lambda x:x*x-2,0,2,"test",max_iter=1)
    assert exc.value.family=="numerical.no_convergence"
    assert exc.value.details["param"]=="/test"


def test_integrator_work_cap_refuses():
    integrator=Integrator(lambda x:math.exp(x),1e-7)
    integrator.evaluations=50000
    with pytest.raises(UnitsError,match="50000") as exc:
        integrator.integrate(0,1)
    assert exc.value.family=="numerical.no_convergence"


@pytest.mark.parametrize("text",["1e-300 yPa","1e-400 MPa","1e100 YPa"])
def test_conversion_underflow_overflow(text):
    with pytest.raises(UnitsError) as exc:
        q(text,"stress","stress")
    assert exc.value.family=="input.out_of_range"


@pytest.mark.parametrize("text",["1 __import__('os')","1 m^(2+2)","1 (m)^2^2","1 (((((m)))))","1 MPa/0"])
def test_units_are_bounded_grammar_not_expressions(text):
    with pytest.raises(UnitsError):
        q(text,"stress","stress")


@pytest.mark.parametrize("tool,args",[(sn,SN),(rainflow,RF),(strain_life,{**SL,"strain_amplitude":".005 dimensionless"}),(fracture,FM),(material,MT)])
def test_all_quantity_boundaries_never_engine_failure(tool,args):
    # Each string parameter is malformed independently. Modes may ignore defaults; bad inputs may be refused,
    # but every result must serialize as finite JSON with a structured contract, including optional branches.
    for name,p in inspect.signature(tool).parameters.items():
        if isinstance(p.default,str) and name!="mode":
            call(tool,**{**args,name:"NaN MPa"})
