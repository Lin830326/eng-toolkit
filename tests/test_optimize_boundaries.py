"""Boundary guards independent of numerical algorithms."""

import numpy as np
import pytest

from eng_toolkit.errors import ToolFailure
from eng_toolkit.domains.optimize._common import Budget, array, named
from eng_toolkit.domains.optimize._expr import Expression


@pytest.mark.parametrize("value", ["pi","e","(1)","1+2","--1","1,2","True","NaN","Inf"])
def test_records_require_literal_values(value):
    with pytest.raises(ToolFailure):
        named("x="+value)


@pytest.mark.parametrize("value", ["[true]","[null]","[NaN]","[1e308]","[1e-400]","[[1],[2,3]]"])
def test_json_number_and_shape_boundaries(value):
    with pytest.raises(ToolFailure):
        array(value,"values")


def test_budget_counts_scalar_samples_and_nodes():
    budget=Budget("10 s",max_evaluations=20,max_work=100)
    exp=Expression("x*x+1",["x"])
    exp.evaluate({"x":np.ones(10)},budget)
    assert budget.evaluations==10
    assert budget.work==50  # two name nodes, multiply, literal, addition.
    with pytest.raises(ToolFailure) as raised:
        exp.evaluate({"x":np.ones(11)},budget)
    assert raised.value.details["reason"]=="optimize.evaluation_budget_exceeded"


def test_deadline_is_rechecked_during_array_work(monkeypatch):
    from eng_toolkit.domains.optimize import _common
    times=iter([0.,0.,0.,2.,2.,2.])
    monkeypatch.setattr(_common.time,"monotonic",lambda:next(times,2.))
    budget=Budget("1 s")
    with pytest.raises(ToolFailure) as raised:
        Expression("sin(sin(x))",["x"]).evaluate({"x":np.ones(200000)},budget)
    assert raised.value.details["reason"]=="optimize.time_budget_exceeded"


def test_trial_error_retains_expression_pointer():
    from eng_toolkit.domains.optimize._expr import TrialError
    with pytest.raises(TrialError) as raised:
        Expression("sqrt(-1)",[],"constraints/3").evaluate({})
    assert raised.value.param=="constraints/3"
