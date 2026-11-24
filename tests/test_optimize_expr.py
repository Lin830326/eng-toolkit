"""Language semantics, bounded resource use, and execution-syntax refusals."""

from pathlib import Path
import time

import numpy as np
import pytest

from eng_toolkit.domains.optimize._expr import Expression, TrialError
from eng_toolkit.errors import ToolFailure
from timing import budget


@pytest.mark.parametrize("text,expected", [
    ("-2^2", -4), ("2^3^2", 512), ("2^-1", .5), ("1-2-3", -4),
    ("8/2/2", 2), ("(-2)^2", 4), ("2**3**2", 512), (".5+1e-3", .501),
    ("2*-3^2", -18), ("-2^-2", -.25), ("pi+e", np.pi + np.e),
])
def test_precedence(text, expected):
    # Exact arithmetic: powers associate right; unary minus applies after a left power.
    assert Expression(text).evaluate({}) == pytest.approx(expected, abs=1e-14)


@pytest.mark.parametrize("name,numpy_function,domain", [
    ("sin", np.sin, (-3, 3)), ("cos", np.cos, (-3, 3)), ("tan", np.tan, (-1, 1)),
    ("asin", np.arcsin, (-1, 1)), ("acos", np.arccos, (-1, 1)), ("atan", np.arctan, (-3, 3)),
    ("sinh", np.sinh, (-3, 3)), ("cosh", np.cosh, (-3, 3)), ("tanh", np.tanh, (-3, 3)),
    ("exp", np.exp, (-3, 3)), ("log", np.log, (.1, 3)), ("log10", np.log10, (.1, 3)),
    ("sqrt", np.sqrt, (0, 3)), ("abs", np.abs, (-3, 3)), ("floor", np.floor, (-3, 3)),
    ("ceil", np.ceil, (-3, 3)), ("sign", np.sign, (-3, 3)),
])
def test_unary_functions(name, numpy_function, domain):
    x = np.random.default_rng(123).uniform(*domain, 101)
    np.testing.assert_allclose(Expression(name + "(x)", ["x"]).evaluate({"x": x}), numpy_function(x), rtol=1e-14, atol=1e-14)


@pytest.mark.parametrize("name,function", [("min", np.minimum), ("max", np.maximum), ("atan2", np.arctan2), ("pow", np.power)])
def test_binary_functions(name, function):
    rng = np.random.default_rng(5)
    x, y = rng.uniform(.1, 3, (2, 100))
    np.testing.assert_allclose(Expression(name + "(x,y)", ["x", "y"]).evaluate({"x": x, "y": y}), function(x, y), rtol=1e-14)


@pytest.mark.parametrize("text", ["", "sin()", "min(1)", "max(1,2,3)", "sin(1,2)", "atan2(1)",
                                    "x", "2 3", "1e", "sin", "1,2", "(1", "1)", "pi(1)", "NaN"])
def test_parse_refusals(text):
    with pytest.raises(ToolFailure) as raised:
        Expression(text)
    assert raised.value.family == "input.invalid"
    assert raised.value.details["param"] == "/expression"
    assert "character" in raised.value.message


@pytest.mark.parametrize("text", ["__import__('os').system('x')", "().__class__", "1;2", "a.b", "a[0]",
                                    "lambda", "exec", "ｘ+1", "x\x00", "'string'", "x=1", "[x for x in y]"])
def test_no_python_syntax(text):
    with pytest.raises(ToolFailure) as raised:
        Expression(text, ["a", "x", "y"])
    assert raised.value.family == "input.invalid"


@pytest.mark.parametrize("text", ["(" * 6000, "1+" * 3000, "a^" * 3000, "-" * 6000,
                                    "(" * 100 + "1" + ")" * 100, "-" * 100 + "1", "1+" * 300 + "1", "a^" * 100 + "a"])
def test_hostile_limits(text):
    start = time.perf_counter()
    with pytest.raises(ToolFailure) as raised:
        Expression(text, ["a"])
    assert raised.value.family == "input.out_of_range"
    assert time.perf_counter() - start < budget(1)


@pytest.mark.parametrize("text", ["10^10^10", "pow(10,100000)", "(-1)^.5", "sqrt(-1)", "log(0)", "1/0", "exp(1000)"])
def test_bad_trial_arithmetic(text):
    with pytest.raises(TrialError):
        Expression(text).evaluate({})


@pytest.mark.parametrize("text", ["1e308", "1e309", "1e-400", "1e-200"])
def test_literal_range(text):
    with pytest.raises(ToolFailure) as raised:
        Expression(text)
    assert raised.value.family == "input.out_of_range"


def test_negative_integer_powers_and_names():
    assert Expression("(-2)^3").evaluate({}) == -8
    with pytest.raises(ToolFailure):
        Expression("a", ["a"] * 2)
    with pytest.raises(ToolFailure):
        Expression("a", ["v" + str(i) for i in range(17)])


def test_source_does_not_delegate_expression_execution():
    package = Path(__file__).parents[1] / "eng_toolkit" / "domains" / "optimize"
    forbidden = ("eval(", "exec(", "compile(", "__import__", "importlib", "subprocess", "pickle", "ast.parse", "sympy", "numexpr", "open(")
    for path in package.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not [item for item in forbidden if item in source], path
