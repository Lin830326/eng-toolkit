"""Second independent review: exact gateway repros and independent oracles."""

from decimal import Decimal, localcontext
from fractions import Fraction
import math

from fastmcp import Client
import numpy as np
import pytest

from eng_toolkit.domains.optimize._expr import Expression
from eng_toolkit.errors import ToolFailure
from eng_toolkit.gateway.server import gateway
from tests.test_optimize_gateway import mcp


async def invoke(name, **arguments):
    async with Client(gateway) as client:
        return await mcp(client, "optimize_" + name, arguments)


def refused(result, family, param):
    assert not result.ok, result.data
    assert result.error.family == family
    assert result.error.details["param"] == param


async def test_1_tiny_bounded_minimum():
    result = await invoke("minimize", expression="(x/1e-18-0.5)^2",
                          initial="x=0", bounds="[[0,1e-18]]")
    # Completing the square gives x = width/2, f = 0.
    expected = float(Fraction(1, 10**18) / 2)
    assert result.ok, result.error
    assert result.data["point"]["x"] == pytest.approx(expected, rel=1e-7, abs=0)
    assert result.data["objective"] == pytest.approx(0, abs=1e-14)


async def test_2_bounded_lp_small_pivot():
    result = await invoke("linprog", c="[-1,0]", A_ub="[[1e-8,10000]]", b_ub="[1]")
    # y >= 0 implies x <= 1/(1e-8), attained with y = 0.
    expected = float(1 / Fraction("1e-8"))
    assert result.ok, result.error
    assert result.data["x"] == pytest.approx([expected, 0], rel=1e-10, abs=1e-12)
    assert result.data["objective"] == pytest.approx(-expected)


async def test_3_lp_small_improving_cost():
    result = await invoke("linprog", c="[-1e-8,-1.09e-8]",
                          A_eq="[[1,1]]", b_eq="[10000]")
    # Substitute x = 10000-y: the coefficient of y is strictly negative.
    assert Fraction("-1.09e-8") - Fraction("-1e-8") < 0
    assert result.ok, result.error
    assert result.data["x"] == pytest.approx([0, 10000], abs=1e-8)
    assert result.data["objective"] == pytest.approx(float(Fraction("-1.09e-8") * 10000))


async def test_4_destructive_underflow():
    with localcontext() as context:
        context.prec = 80
        tiny = Decimal("1e-100")
        factor = tiny**4 / tiny**3 / tiny
        assert factor == 1
        assert factor * (Decimal(1)**2 + 1) == 2
    result = await invoke("root", expression="(1e-100)^4/(1e-100)^3/(1e-100)*(x^2+1)",
                          initial="x=1", method="newton")
    refused(result, "input.out_of_range", "/expression/0")


async def test_5_system_forward_accuracy():
    # Subtract the equations exactly: 1e-8*y = 1e-8, then x = 2-y.
    y = (Fraction("2.00000001") - 2) / (Fraction("1.00000001") - 1)
    assert (2-y, y) == (1, 1)
    result = await invoke("root", expression="x+y-2;x+1.00000001*y-2.00000001",
                          initial="x=1.5;y=.5", method="newton")
    refused(result, "optimize.not_converged", "/expression")


async def test_6_root_multistart_bracket():
    result = await invoke("root", expression="x^2-1", initial="x=-2",
                          bracket="[0,2]", multistart=2)
    assert result.ok, result.error
    assert [row["solution"]["x"] for row in result.data["roots"]] == pytest.approx([1])


async def test_7_fit_stationary_maximum():
    # S(a) = 3*a^4 - 4*a^2 + 3; S''(0)=-8, minima at a^2=2/3.
    a2 = Fraction(2, 3)
    assert 3*a2*a2 - 4*a2 + 3 == Fraction(5, 3)
    result = await invoke("fit", model="a*x+a^2", initial="a=0",
                          data='{"x":[-1,0,1],"y":[1,1,1]}')
    refused(result, "optimize.not_converged", "/model")


async def test_8_relative_weight_scale():
    result = await invoke("fit", model="a*x", initial="a=0",
                          data='{"x":[1,2,3],"y":[1,2,3]}', sigma="[1e10,1e10,1e10]")
    # S(a) is a positive multiple of 14*(a-1)^2.
    assert result.ok, result.error
    assert result.data["parameters"]["a"] == pytest.approx(1, abs=1e-8)
    assert result.data["rms"] == pytest.approx(0, abs=1e-8)
    assert result.data["covariance"][0][0] == pytest.approx(0, abs=1e-16)


async def test_9_unresolved_second_order():
    # E[X^2]=1 and E[X^4]=3 for standard normal X, so Var(X^2)=2.
    assert Fraction(3) - Fraction(1)**2 == 2
    assert math.sqrt(2) > 0
    result = await invoke("sensitivity", expression="1e12+x^2", nominal="x=0",
                          standard_uncertainties="x=1", second_order=True)
    refused(result, "optimize.not_converged", "/nominal/0")


@pytest.mark.parametrize("width", ["1e-12", "1e-30", "1e-80"])
async def test_tiny_interval_scaling_family(width):
    result = await invoke("minimize", expression=f"(x/{width}-0.3)^2",
                          initial="x=0", bounds=f"[[0,{width}]]")
    assert result.ok, result.error
    assert result.data["point"]["x"] == pytest.approx(float(Decimal(width)*Decimal(".3")), rel=1e-7, abs=0)


async def test_interval_without_representable_stencil_refuses():
    result = await invoke("minimize", expression="((x-1)/2.220446049250313e-16-0.5)^2",
                          initial="x=1", bounds="[[1,1.0000000000000002]]")
    refused(result, "optimize.not_converged", "/expression")


@pytest.mark.parametrize("formula", [
    "(1e-100)^4", "1e-100*1e-100*1e-100*1e-100",
    "1e-100/1e100/1e100/1e100", "exp(-800)",
    "(1e-100)^3*1e-8", "exp(-710)",
])
@pytest.mark.parametrize("binding", [1., np.array([1., 2.])])
def test_scalar_and_array_underflow(formula, binding):
    with pytest.raises(ToolFailure) as error:
        Expression(f"({formula})*x", ["x"]).evaluate({"x": binding})
    assert error.value.family == "input.out_of_range"
    assert error.value.details["param"] == "/expression"


@pytest.mark.parametrize("binding", [1e-100, np.array([1e-100, 2e-100])])
def test_underflow_in_variable_arithmetic(binding):
    with pytest.raises(ToolFailure) as error:
        Expression("x*x*x*x", ["x"]).evaluate({"x": binding})
    assert error.value.family == "input.out_of_range"
    assert error.value.details["param"] == "/expression"


@pytest.mark.parametrize("formula,expected", [
    ("x-x", [0., 0.]), ("0*x", [0., 0.]),
    ("floor(x/10)", [0., 0.]), ("(1e-100)^3*x", [1e-300, 2e-300]),
])
def test_exact_zeros_and_normal_small_values(formula, expected):
    value = Expression(formula, ["x"]).evaluate({"x": np.array([1., 2.])})
    assert value == pytest.approx(expected, rel=1e-14, abs=0)


async def test_resolvable_system_correction():
    result = await invoke("root", expression="x+y-2;x+1.00001*y-2.00001",
                          initial="x=1.5;y=.5", method="newton")
    assert result.ok, result.error
    assert list(result.data["solution"].values()) == pytest.approx([1, 1], abs=1e-8)
    certificate = result.data["roots"][0]["certificate"]
    assert certificate["forward_error_scaled_inf"] <= certificate["tolerance"]


@pytest.mark.parametrize("method", ["newton", "secant", "bisection"])
async def test_multistart_respects_bracket_for_all_methods(method):
    result = await invoke("root", expression="x^2-1", initial="x=-2",
                          bracket="[0,2]", multistart=8, method=method)
    assert result.ok, result.error
    assert [row["solution"]["x"] for row in result.data["roots"]] == pytest.approx([1])


async def test_fit_coupled_saddle():
    # S = 4*(a^2+b^2) + 4*((a+b)^2-1)^2 has negative curvature along a=b at zero.
    result = await invoke("fit", model="a*x+b*z+(a+b)^2", initial="a=0;b=0", independent="x,z",
                          data='{"x":[-1,1,-1,1],"z":[-1,-1,1,1],"y":[1,1,1,1]}')
    refused(result, "optimize.not_converged", "/model")


async def test_fit_resolves_a_minimum_away_from_the_maximum():
    result = await invoke("fit", model="a*x+a^2", initial="a=.5",
                          data='{"x":[-1,0,1],"y":[1,1,1]}')
    assert result.ok, result.error
    assert result.data["parameters"]["a"] == pytest.approx(math.sqrt(2/3), abs=1e-7)
    assert result.data["chi_square"] == pytest.approx(float(Fraction(5,3)), abs=1e-10)


@pytest.mark.parametrize("extra", [{"bounds": "[[0,2]]"}, {}])
async def test_small_scale_fit_maximum_and_weak_bound(extra):
    result = await invoke("fit", model="1e-10*(a*x+a^2)", initial="a=0",
                          data='{"x":[-1,0,1],"y":[1e-10,1e-10,1e-10]}', **extra)
    refused(result, "optimize.not_converged", "/model")


async def test_unbounded_ray_has_exact_constraint_certificate():
    result = await invoke("linprog", c="[-1,-1]", A_eq="[[3,-1]]", b_eq="[0]")
    refused(result, "optimize.unbounded", "/inputs")
    proof = result.error.details["proof"]
    direction = [Fraction(v) for v in proof["ray_exact"]]
    assert 3*direction[0] - direction[1] == 0
    assert min(direction) >= 0
    assert -sum(direction) < 0


@pytest.mark.parametrize("factor", [1e-10, 1., 1e10])
@pytest.mark.parametrize("absolute", [False, True])
async def test_unequal_weight_scaling_and_covariance(factor, absolute):
    result = await invoke("fit", model="a*x", initial="a=0", absolute_sigma=absolute,
                          data='{"x":[1,2,3],"y":[1,2,4]}', sigma=str([factor, 2*factor, 3*factor]))
    # Weighted normal equation: a = (10/3)/3. Weighted SSE = 2/27.
    assert result.ok, result.error
    assert result.data["parameters"]["a"] == pytest.approx(float(Fraction(10, 9)), abs=1e-8)
    covariance = factor**2/3 if absolute else float(Fraction(1, 81))
    assert result.data["covariance"][0][0] == pytest.approx(covariance, rel=1e-7, abs=0)
    assert result.data["chi_square"] == pytest.approx(float(Fraction(2, 27))/factor**2)


async def test_resolved_mixed_second_derivatives():
    # H=[[2,3],[3,4]], independent unit normals: shift=3, variance=tr(H^2)/2=19.
    result = await invoke("sensitivity", expression="x^2+3*x*y+2*y^2", nominal="x=0;y=0",
                          standard_uncertainties="x=1;y=1", second_order=True)
    assert result.ok, result.error
    assert np.array(result.data["hessian"]) == pytest.approx(np.array([[2.,3.],[3.,4.]]), abs=1e-7)
    assert result.data["gum"]["mean"] == pytest.approx(3)
    assert result.data["gum"]["second_order_variance_correction"] == pytest.approx(19)
