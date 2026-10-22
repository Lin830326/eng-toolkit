import math
import numpy as np
import pytest

from eng_toolkit.domains.optimize.root import root
from eng_toolkit.domains.optimize._numerics import jacobian
from optimize_testing import call,ok


@pytest.mark.parametrize("method",["auto","newton","secant","bisection"])
def test_polynomial(method):
    # x^2=2 and positive bracket/start select +sqrt(2), checked to 1e-7.
    r=ok(root,expression="x^2-2",initial="x=1",method=method)
    assert r.data["solution"]["x"]==pytest.approx(math.sqrt(2),abs=1e-7)


def test_kepler_colebrook_and_scan():
    k=ok(root,expression="E-0.7*sin(E)-1",initial="E=1")
    value=k.data["solution"]["E"]
    assert value-.7*math.sin(value)==pytest.approx(1,abs=1e-7)
    c=ok(root,expression="1/sqrt(f)+2*log10(0.0002/3.7+2.51/(100000*sqrt(f)))",initial="f=0.02",bracket="[0.005,0.1]")
    # Independent bisection of the Colebrook residual. It decreases on this interval.
    low,high=.005,.1
    for _ in range(70):
        midpoint=(low+high)/2
        residual=1/math.sqrt(midpoint)+2*math.log10(.0002/3.7+2.51/(100000*math.sqrt(midpoint)))
        if residual>0:
            low=midpoint
        else:
            high=midpoint
    assert c.data["solution"]["f"]==pytest.approx((low+high)/2,abs=1e-8)
    r=ok(root,expression="(x-1)*(x+2)*(x-3)",initial="x=0",bracket="[-4,4]",scan=100)
    assert sorted(item["solution"]["x"] for item in r.data["roots"])==pytest.approx([-2,1,3],abs=1e-7)


@pytest.mark.parametrize("expression,initial,answer",[
    ("x^2+y^2-5;x-y-1","x=2.2;y=1.1",[2,1]),
    ("x+y+z-6;x-y;x+z-4","x=1;y=1;z=1",[2,2,2]),
])
def test_square_system(expression,initial,answer):
    # First: y=x-1 ->2*x^2-2*x-4=0, positive solution(2,1).
    # Second: y=x,z=4-x ->x+4=6 ->(2,2,2).
    r=ok(root,expression=expression,initial=initial,method="newton")
    assert list(r.data["solution"].values())==pytest.approx(answer,abs=2e-7)


def test_singular_and_pole_refused():
    # Start off the solution line: (1,1) is itself an exact root, returned with a warning.
    assert not call(root,expression="x+y-2;2*x+2*y-4",initial="x=1;y=0").ok
    assert not call(root,expression="1/x",initial="x=0.1",bracket="[-1,1]",method="bisection").ok


def test_jacobian_bound_and_domain():
    from eng_toolkit.domains.optimize._expr import TrialError
    def f(x):
        if x[0]<0:
            raise TrialError("negative")
        return np.array([x[0]**2+3*x[1],x[1]**3])
    assert jacobian(f,[0,2],lo=[0,-10],hi=[10,10])==pytest.approx(np.array([[0,3],[0,12]]),abs=1e-5)


def test_seeded_system_multistart_and_budget(monkeypatch):
    kwargs=dict(expression="x^2+y^2-5;x-y-1",initial="x=2;y=1",bounds="[[-4,4],[-4,4]]",multistart=12,seed=9)
    first=ok(root,**kwargs)
    second=ok(root,**kwargs)
    assert first.data["roots"]==second.data["roots"]
    assert len(first.data["roots"])==2
    import importlib
    module=importlib.import_module("eng_toolkit.domains.optimize.root")
    from eng_toolkit.domains.optimize._common import Budget
    monkeypatch.setattr(module,"Budget",lambda duration:Budget(duration,max_evaluations=5))
    r=call(root,**kwargs)
    assert not r.ok
    assert "budget" in r.error.message


@pytest.mark.parametrize("factor",["1e-20","1e20"])
def test_equation_scaling_preserves_root(factor):
    r=ok(root,expression=f"{factor}*(x+y-3);{factor}*(x-y-1)",initial="x=0;y=0",method="newton")
    assert list(r.data["solution"].values())==pytest.approx([2,1],abs=1e-7)
    certificate=r.data["roots"][0]["certificate"]
    assert certificate["scaled_residual_inf"]<=certificate["tolerance"]
    assert certificate["jacobian_condition_scaled"]<10
