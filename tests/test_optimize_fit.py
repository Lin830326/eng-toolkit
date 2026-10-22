import json
import numpy as np
import pytest

from eng_toolkit.domains.optimize.fit import fit
from optimize_testing import call,ok,table


@pytest.mark.parametrize("model,initial,true,formula",[
    ("a*exp(-b*x)","a=1.8;b=0.4",[2,.7],lambda x,a,b:a*np.exp(-b*x)),
    ("a*x^b","a=1.8;b=1.2",[2,1.5],lambda x,a,b:a*x**b),
    ("a/(1+exp(-b*(x-c)))","a=2.8;b=0.8;c=1.2",[3,1.2,1.5],lambda x,a,b,c:a/(1+np.exp(-b*(x-c)))),
    ("a*sin(b*x+c)","a=1.8;b=0.9;c=0.1",[2,1.1,.3],lambda x,a,b,c:a*np.sin(b*x+c)),
])
def test_noiseless(model,initial,true,formula):
    x=np.linspace(.1,4,45)
    y=formula(x,*true)
    r=ok(fit,model=model,initial=initial,data=json.dumps({"x":x.tolist(),"y":y.tolist()}),tolerance=1e-10)
    assert list(r.data["parameters"].values())==pytest.approx(true,abs=2e-6)
    assert r.data["rms"]<1e-7
    assert len(table(r))==len(x)
    residual=np.array(r.data["residuals"])
    assert r.data["max_absolute_residual"]==pytest.approx(np.max(np.abs(residual)))
    assert r.data["max_residual_index"]==int(np.argmax(np.abs(residual)))
    assert r.data["jacobian_condition"]>=1


@pytest.mark.parametrize("absolute",[False,True])
@pytest.mark.parametrize("seed",range(12))
def test_noisy_covariance_oracle(absolute,seed):
    optimize=pytest.importorskip("scipy.optimize")
    rng=np.random.default_rng(seed)
    x=np.linspace(0,4,35)
    sigma=.03+.02*x
    y=2*np.exp(-.7*x)+rng.normal(size=len(x))*sigma
    def model(x,a,b):
        return a*np.exp(-b*x)
    ref,cov=optimize.curve_fit(model,x,y,p0=[1.8,.5],sigma=sigma,absolute_sigma=absolute)
    r=ok(fit,model="a*exp(-b*x)",initial="a=1.8;b=0.5",data=json.dumps({"x":x.tolist(),"y":y.tolist()}),sigma=json.dumps(sigma.tolist()),absolute_sigma=absolute,tolerance=1e-10)
    assert list(r.data["parameters"].values())==pytest.approx(ref,rel=1e-5,abs=1e-6)
    assert np.array(r.data["covariance"])==pytest.approx(cov,rel=1e-4,abs=1e-9)
    assert r.data["certificate"]["passed"]
    assert r.data["certificate"]["projected_gradient_scaled_inf"]<=1e-10


def test_multiple_independent_and_rank_refusal():
    x=np.arange(12.)
    z=np.sin(x)
    r=ok(fit,model="a*x+b*z+c",initial="a=1;b=1;c=0",independent="x,z",data=json.dumps({"x":x.tolist(),"z":z.tolist(),"y":(2*x+3*z+4).tolist()}))
    assert list(r.data["parameters"].values())==pytest.approx([2,3,4],abs=1e-6)
    r=call(fit,model="(a+b)*x",initial="a=1;b=1",data=json.dumps({"x":[1,2,3,4],"y":[2,4,6,8]}))
    assert not r.ok
    assert "rank" in r.error.message


def test_bounded_and_constant():
    r=ok(fit,model="a*x",initial="a=1",bounds="[[0,2]]",data='{"x":[1,2,3,4],"y":[3,6,9,12]}')
    assert r.data["parameters"]["a"]==pytest.approx(2)
    assert any("bound" in warning for warning in r.warnings)
    r=ok(fit,model="a",initial="a=1",data='{"x":[1,2,3,4],"y":[2,2,2,2]}')
    assert r.data["r_squared"] is None


def test_csv_copy_and_provenance(tmp_path,monkeypatch):
    source=tmp_path/"observations.csv"
    source.write_text("x,y\n0,1\n1,3\n2,5\n3,7\n",encoding="utf-8")
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS",str(tmp_path))
    r=ok(fit,model="a*x+b",initial="a=1;b=0",data_file=str(source))
    assert list(r.data["parameters"].values())==pytest.approx([2,1],abs=1e-6)
    assert r.provenance.input_files


@pytest.mark.parametrize("kwargs",[
    {"sigma":"[1,0,1,1]"},
    {"bounds":"[[2,1]]"},
    {"initial":""},
    {"data":'{"x":[1,2],"y":[1]}'},
])
def test_fit_boundaries(kwargs):
    args=dict(model="a*x",initial="a=1",data='{"x":[1,2,3,4],"y":[2,4,6,8]}')
    args.update(kwargs)
    assert not call(fit,**args).ok


def test_one_evaluation_budget_across_fit_stages(monkeypatch):
    import importlib
    module=importlib.import_module("eng_toolkit.domains.optimize.fit")
    from eng_toolkit.domains.optimize._common import Budget
    monkeypatch.setattr(module,"Budget",lambda duration:Budget(duration,max_evaluations=10))
    r=call(fit,model="a*x",initial="a=1",data='{"x":[1,2,3,4],"y":[2,4,6,8]}',multistart=5)
    assert not r.ok
    assert "budget" in r.error.message
