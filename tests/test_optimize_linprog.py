"""Independent LP oracle and degeneracy coverage."""
import numpy as np
import pytest
from eng_toolkit.domains.optimize._common import Budget
from eng_toolkit.domains.optimize.linprog import solve_lp, _integer


def solve(c,A,b,E=None,e=None,bounds=None):
    c=np.asarray(c,float); n=len(c)
    return solve_lp(c,np.asarray(A,float).reshape(-1,n),np.asarray(b,float),
                    np.zeros((0,n)) if E is None else np.asarray(E,float).reshape(-1,n),
                    np.zeros(0) if e is None else np.asarray(e,float),
                    [(0,None)]*n if bounds is None else bounds,Budget("10 s"))


@pytest.mark.parametrize("seed",range(180))
def test_random_feasible_against_scipy(seed):
    scipy=pytest.importorskip("scipy.optimize")
    rng=np.random.default_rng(seed); n=2+seed%7; m=2+seed%9
    A=rng.normal(size=(m,n)); point=rng.uniform(.2,2,n)
    b=A@point+rng.uniform(.1,2,m); c=rng.normal(size=n)
    E=rng.normal(size=(seed%3,n)); e=E@point
    bounds=[(0,4)]*n
    ref=scipy.linprog(c,A_ub=A,b_ub=b,A_eq=E if len(E) else None,b_eq=e if len(E) else None,bounds=bounds,method="highs")
    out=solve(c,A,b,E,e,bounds)
    assert ref.success
    assert out["status"]=="optimal"
    assert out["objective"]==pytest.approx(ref.fun,abs=2e-6)
    assert np.all(np.isfinite(out["x"]))


@pytest.mark.parametrize("seed",range(60))
def test_random_infeasible_against_scipy(seed):
    scipy=pytest.importorskip("scipy.optimize")
    rng=np.random.default_rng(seed); a=rng.uniform(.1,4,3)
    A=np.array([a,-a]); b=np.array([1.,-2.])
    assert scipy.linprog([1,2,3],A_ub=A,b_ub=b).status==2
    out=solve([1,2,3],A,b)
    assert out["status"]=="infeasible"
    assert out["proof"]["phase1_residual"]>0


@pytest.mark.parametrize("seed",range(60))
def test_random_unbounded_against_scipy(seed):
    scipy=pytest.importorskip("scipy.optimize")
    rng=np.random.default_rng(seed); c=-rng.uniform(.1,4,3)
    A=-np.eye(3); b=-rng.uniform(0,2,3)
    assert scipy.linprog(c,A_ub=A,b_ub=b).status==3
    out=solve(c,A,b)
    assert out["status"]=="unbounded"
    ray=np.array(out["proof"]["ray"])
    assert c@ray<0 and np.max(A@ray)<=1e-7


def test_beale_cycling():
    out=solve([-10,57,9,24],[[.5,-5.5,-2.5,9],[.5,-1.5,-.5,1],[1,0,0,0]],[0,0,1])
    assert out["objective"]==pytest.approx(-1)


def test_free_fixed_and_redundant_equalities():
    out=solve([1,2],[],[],[[1,1],[2,2]],[1,2],[(None,None),(2,2)])
    assert out["x"]==pytest.approx([-1,2])
    assert out["objective"]==pytest.approx(3)


def test_diet():
    out=solve([2,3],[[-3,-1],[-1,-2]],[-6,-4])
    assert out["x"]==pytest.approx([1.6,1.2])
    assert out["objective"]==pytest.approx(6.8)


def test_transportation():
    out=solve([2,3,4,1],[],[],[[1,1,0,0],[0,0,1,1],[1,0,1,0],[0,1,0,1]],[3,2,2,3])
    assert out["objective"]==pytest.approx(9)


def test_integer_knapsack():
    out=_integer(np.array([-6.,-5.,-4.]),np.array([[4.,3.,2.]]),np.array([5.]),
                 np.zeros((0,3)),np.zeros(0),[(0,1)]*3,[0,1,2],Budget("10 s"),100)
    assert out["objective"]==pytest.approx(-9)
    assert out["gap"]==0


def test_public_contract_and_dual_signs():
    from eng_toolkit.domains.optimize.linprog import linprog
    from optimize_testing import ok
    out=ok(linprog,c="[3,2]",A_ub="[[1,1],[1,0]]",b_ub="[4,2]",maximize=True)
    assert out.data["objective"]==pytest.approx(10)
    assert out.data["duals_ub"]==pytest.approx([2,1])
    assert out.data["rhs_ranges"] is not None


@pytest.mark.parametrize("kwargs",[
    {"c":"[1e20]"},{"c":"[1e-20]"},{"c":"[NaN]"},
    {"c":"[]"},{"A_ub":"[[1,2]]","b_ub":"[1]"},
    {"bounds":"[[2,1]]"},{"integer":"[0,0]"},
])
def test_invalid_inputs_are_structured(kwargs):
    from eng_toolkit.domains.optimize.linprog import linprog
    from optimize_testing import call
    out=call(linprog,**kwargs)
    assert not out.ok


def test_budget_incomplete_has_bound_and_gap():
    from eng_toolkit.domains.optimize.linprog import linprog
    from optimize_testing import call
    out=call(linprog,c="[-6,-5,-4]",A_ub="[[4,3,2]]",b_ub="[4]",
             bounds="[[0,1],[0,1],[0,1]]",integer="[0,1,2]",node_limit=1)
    assert not out.ok
    assert out.error.family=="optimize.not_converged"
    assert out.error.details["bound"] is not None
    assert "gap" in out.error.details


@pytest.mark.parametrize("c,b,family",[("[1]","[-1]","optimize.infeasible"),("[-1]","[0]","optimize.unbounded")])
def test_public_refusal_proofs(c,b,family):
    from eng_toolkit.domains.optimize.linprog import linprog
    from optimize_testing import call
    out=call(linprog,c=c,A_ub="[[-1]]" if family.endswith("unbounded") else "[[1]]",b_ub=b)
    assert out.error.family==family
    assert out.error.details["proof"]


def test_tiny_inconsistent_constraints_never_report_optimal():
    out=solve([1],[[1],[-1]],[0,-1e-8])
    assert out["status"]=="infeasible"
    assert out["proof"]["certificate_margin"]>0


@pytest.mark.parametrize("separation",[1e-12,1e-15])
def test_sub_tolerance_inconsistency_is_proved_or_refused(separation):
    from eng_toolkit.errors import ToolFailure
    try:
        out=solve([1],[[1],[-1]],[0,-separation])
    except ToolFailure as exc:
        assert exc.family=="optimize.not_converged"
    else:
        assert out["status"]=="infeasible"


def test_integer_interval_contains_no_integer():
    out=_integer(np.array([1.]),np.zeros((0,1)),np.zeros(0),np.zeros((0,1)),np.zeros(0),
                 [(.99999995,.99999996)],[0],Budget(),100)
    assert out["status"]=="infeasible"
    assert out["proof"]["empty_integer_bound"]==0


def test_near_integer_candidate_does_not_satisfy_original_row():
    from eng_toolkit.errors import ToolFailure
    with pytest.raises(ToolFailure) as exc:
        _integer(np.array([1.]),np.array([[-1.]]),np.array([-1e-8]),np.zeros((0,1)),np.zeros(0),
                 [(0,1)],[0],Budget(),100)
    assert exc.value.family=="optimize.not_converged"
