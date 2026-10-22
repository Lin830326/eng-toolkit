import numpy as np
import pytest

from eng_toolkit.domains.optimize._stats import beta_regularized, f_survival, t_quantile


@pytest.mark.parametrize("df,expected", [(1,12.706),(2,4.303),(5,2.571),(10,2.228),(30,2.042)])
def test_student_nist_published_975_table(df,expected):
    # Two-sided 95% tail is (1+.95)/2=.975. NIST eda3672 rounds to .001.
    assert t_quantile(.975,df) == pytest.approx(expected,abs=.0005)


def test_beta_and_f_closed_forms():
    # B(1,1)=1, integral_0^x 1 dt=x. For F(2,2), survival=1/(1+f).
    assert beta_regularized(.3,1,1)==pytest.approx(.3,abs=1e-14)
    assert f_survival(3,2,2)==pytest.approx(1/4,abs=1e-14)


@pytest.mark.parametrize("df",[1,2,3,7,30,100,10000])
@pytest.mark.parametrize("prob",[1e-5,.001,.025,.25,.5,.975,.999,1-1e-5])
def test_student_oracle(df,prob):
    stats = pytest.importorskip("scipy.stats")
    assert t_quantile(prob,df) == pytest.approx(stats.t.ppf(prob,df),rel=2e-9,abs=2e-10)


def test_beta_and_f_oracles():
    special = pytest.importorskip("scipy.special")
    stats = pytest.importorskip("scipy.stats")
    for a in [.1,.5,1,5,50,1000]:
        for b in [.1,.5,2,30]:
            for x in [1e-10,.01,.3,.8,1-1e-10]:
                assert beta_regularized(x,a,b)==pytest.approx(special.betainc(a,b,x),abs=2e-11)
    for f in [0,.01,1,3,100,1e8]:
        for d1,d2 in [(1,1),(2,20),(50,100),(10,3)]:
            assert f_survival(f,d1,d2)==pytest.approx(stats.f.sf(f,d1,d2),abs=2e-12)


@pytest.mark.parametrize("args",[(0,0,1),(-1,1,1),(2,1,1)])
def test_invalid_beta(args):
    with pytest.raises(ValueError):
        beta_regularized(*args)
