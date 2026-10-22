"""Closed-form stationary points, constrained designs, and seeded global searches."""

import json
import math

import numpy as np
import pytest

from eng_toolkit.domains.optimize.minimize import minimize
from tests.optimize_testing import call, ok, table


def test_rosenbrock_and_history():
    r = ok(minimize, expression="(1-x)^2+100*(y-x^2)^2", initial="x=-1.2;y=1")
    # Both squares vanish only at x=1,y=x^2=1; f=0, tolerance 2e-5 in coordinates.
    assert list(r.data["point"].values()) == pytest.approx([1, 1], abs=2e-5)
    assert r.data["objective"] < 1e-10
    assert r.data["stationarity_residual"] <= r.data["tolerance"]
    assert table(r)


def test_box_optimum_and_maximize():
    r = ok(minimize, expression="(x-3)^2+(y+2)^2", initial="x=0;y=0", bounds="[[0,2],[-1,1]]")
    # Nearest box point to (3,-2) is (2,-1), f=1+1=2.
    assert list(r.data["point"].values()) == pytest.approx([2, -1], abs=1e-7)
    assert r.data["objective"] == pytest.approx(2, abs=1e-7)
    r = ok(minimize, expression="4-(x-2)^2", initial="x=0", maximize=True)
    assert r.data["point"]["x"] == pytest.approx(2, abs=1e-6)
    assert r.data["objective"] == pytest.approx(4, abs=1e-8)


@pytest.mark.parametrize("relation", ["x+y-1>=0", "1-x-y<=0", "x+y-1==0"])
def test_known_kkt_quadratic(relation):
    r = ok(minimize, expression="x^2+y^2", initial="x=0.2;y=0.3", constraints=relation, time_limit="30 s")
    # 2x=2y=lambda and x+y=1 -> x=y=1/2, f=1/2, canonical lambda=1.
    assert list(r.data["point"].values()) == pytest.approx([.5, .5], abs=2e-6)
    assert r.data["objective"] == pytest.approx(.5, abs=2e-6)
    multiplier = r.data["multipliers"][0]
    assert abs(multiplier["canonical_multiplier"]) == pytest.approx(1, abs=1e-5)
    assert r.data["constraints"][0]["active"]


def test_linear_equality_closed_form():
    r = ok(minimize, expression="(x-2)^2+2*(y-1)^2", initial="x=0;y=0", constraints="x+2*y-2==0")
    # 2(x-2)+lambda=0;4(y-1)+2lambda=0; x+2y=2 -> lambda=4/3,x=4/3,y=1/3.
    assert list(r.data["point"].values()) == pytest.approx([4/3, 1/3], abs=2e-5)


def test_himmelblau_four_minima():
    r = ok(minimize, expression="(x^2+y-11)^2+(x+y^2-7)^2", initial="x=0;y=0", bounds="[[-5,5],[-5,5]]", method="multistart", starts=32, seed=12, time_limit="60 s")
    # Solving the two zero-square equations gives four real intersections.
    expected = [[3, 2], [-2.805118, 3.131312], [-3.779310, -3.283186], [3.584428, -1.848126]]
    points = [list(item["point"].values()) for item in r.data["local_optima"]]
    assert len(points) == 4
    for point in expected:
        assert min(np.linalg.norm(np.array(p)-point) for p in points) < 2e-5


@pytest.mark.parametrize("method", ["differential_evolution", "multistart"])
def test_rastrigin_global_candidates(method):
    r = ok(minimize, expression="20+x^2+y^2-10*cos(2*pi*x)-10*cos(2*pi*y)", initial="x=0.1;y=0.1", bounds="[[-5.12,5.12],[-5.12,5.12]]", method=method, starts=24, seed=4, generations=120, time_limit="60 s")
    # x^2+y^2>=0 and each 1-cos>=0; at the origin all four terms are zero.
    assert r.data["objective"] == pytest.approx(0, abs=1e-7)
    assert r.data["seed"] == 4


def test_nelder_mead_restarts():
    r = ok(minimize, expression="(x-1)^2+3*(y+2)^2", initial="x=4;y=4", method="nelder_mead", max_iterations=1800)
    assert list(r.data["point"].values()) == pytest.approx([1, -2], abs=2e-5)


def test_spring_benchmark():
    r = ok(minimize, expression="(n+2)*d^2*D", initial="d=0.06;D=0.4;n=10", bounds="[[0.05,2],[0.25,1.3],[2,15]]",
           constraints="1-D^3*n/(71785*d^4)<=0;(4*D^2-d*D)/(12566*(D*d^3-d^4))+1/(5108*d^2)-1<=0;1-140.45*d/(D^2*n)<=0;(D+d)/1.5-1<=0", tolerance=1e-6, max_iterations=5000, time_limit="60 s")
    # Published continuous spring point: (0.05169,0.35675,11.287); (n+2)d^2D=0.0126652.
    assert r.data["objective"] == pytest.approx(.01266523, rel=5e-4)


def test_continuous_pressure_vessel():
    r = ok(minimize, expression="0.6224*t*r*l+1.7781*h*r^2+3.1661*t^2*l+19.84*t^2*r", initial="t=1;h=0.5;r=45;l=180",
           bounds="[[0.5,2],[0.2,2],[10,100],[10,200]]", constraints="0.0193*r-t<=0;0.00954*r-h<=0;1296000-pi*r^2*l-4*pi*r^3/3<=0", tolerance=2e-6, max_iterations=5000, time_limit="60 s")
    # Active l=200, t=.0193r,h=.00954r. Independently bisect volume pi*r^2*(200+4r/3)=1296000.
    a, b = 10., 100.
    for _ in range(60):
        mid = (a+b)/2
        if math.pi*mid**2*(200+4*mid/3) < 1296000:
            a = mid
        else:
            b = mid
    radius = (a+b)/2
    t, h, l = .0193*radius, .00954*radius, 200
    expected = .6224*t*radius*l+1.7781*h*radius**2+3.1661*t**2*l+19.84*t**2*radius
    assert r.data["objective"] == pytest.approx(expected, rel=5e-4)


def test_cantilever_beam():
    r = ok(minimize, expression="0.0624*(a+b+c+d+z)", initial="a=6;b=5;c=4;d=3;z=2",
           bounds="[[0.1,10],[0.1,10],[0.1,10],[0.1,10],[0.1,10]]",
           constraints="61/a^3+37/b^3+19/c^3+7/d^3+1/z^3-1<=0", tolerance=1e-6, max_iterations=3000,time_limit="60 s")
    # Lagrange condition x_i^4=3*lambda*c_i/.0624 -> x_i=k*c_i^(1/4), k^3=sum c_i^(1/4).
    roots = np.array([61,37,19,7,1])**.25
    expected = .0624 * roots.sum()**(4/3)
    assert r.data["objective"] == pytest.approx(expected, rel=1e-4)


@pytest.mark.parametrize("kwargs", [
    {}, {"expression":"x^2","initial":"x=0","bounds":"[[2,1]]"},
    {"expression":"sqrt(-1)","initial":"x=0"}, {"expression":"x^2","initial":"x=1e308"},
    {"expression":"x","initial":"x=0","max_iterations":1},
    {"expression":"(x-2)^2","initial":"x=0","constraints":"x<=0;x-1>=0","max_iterations":30},
    {"expression":"x^2","initial":"x=0","parameters":"x=1"},
])
def test_minimize_refusals(kwargs):
    assert not call(minimize, **kwargs).ok


@pytest.mark.parametrize("seed", range(12))
def test_random_positive_quadratic_against_scipy(seed):
    scipy = pytest.importorskip("scipy.optimize")
    rng = np.random.default_rng(seed)
    a = rng.uniform(.2, 3, 3)
    b = rng.uniform(-2, 2, 3)
    expression = "+".join(f"{a[i]}*(x{i}-({b[i]}))^2" for i in range(3))
    r = ok(minimize, expression=expression, initial="x0=0;x1=0;x2=0", bounds=json.dumps([[-1,1]]*3))
    oracle = scipy.minimize(lambda x: np.sum(a*(x-b)**2), np.zeros(3), bounds=[(-1,1)]*3)
    assert r.data["objective"] == pytest.approx(oracle.fun, abs=1e-7)
