"""Design point sets and independently calculated least-squares diagnostics."""
import numpy as np
import pytest

from eng_toolkit.domains.optimize.doe import doe
from tests.optimize_testing import call, ok, table


FACTORS = "x,-1,1;y,-1,1;z,-1,1"


def test_multilevel_full_factorial_points_and_natural_means():
    r = ok(doe,factors="x,0,10;y,2,8",levels="[3,4]",center_points=0)
    points = np.array(r.data["natural"])
    assert points.shape==(12,2)
    assert sorted(set(points[:,0]))==pytest.approx([0,5,10])
    assert sorted(set(points[:,1]))==pytest.approx([2,4,6,8])
    assert points.mean(axis=0)==pytest.approx([5,5])
    assert r.data["metadata"]["levels"]==[3,4]


@pytest.mark.parametrize("kwargs",[
    {"levels":"[3]"}, {"levels":"[1,3]"}, {"levels":"[3.5,4]"},
    {"levels":"[3,4]","design":"lhs"},
    {"factors":FACTORS+";w,0,1","levels":"[10,10,10,10]"},
])
def test_multilevel_refusals(kwargs):
    assert not call(doe,**{"factors":"x,0,1;y,0,1",**kwargs}).ok


def test_factorial_orthogonality_and_artifact():
    r = ok(doe,factors=FACTORS,center_points=0)
    x = np.asarray(r.data["coded"])
    assert x.shape == (8,3)
    assert x.sum(axis=0) == pytest.approx([0,0,0])
    assert x.T@x == pytest.approx(8*np.eye(3))
    assert len(table(r)) == 8
    assert table(r)[0]["coded_x"] == "-1.0"


@pytest.mark.parametrize("design,k,runs,resolution",[("fractional_iii",3,4,3),("fractional_iv",4,8,4),("fractional_v",5,16,5)])
def test_standard_fraction_resolution_and_orthogonality(design,k,runs,resolution):
    factors = ";".join(f"x{i},-1,1" for i in range(k))
    r = ok(doe,factors=factors,design=design,center_points=0)
    x = np.asarray(r.data["coded"])
    assert len(x)==runs
    assert x.T@x == pytest.approx(runs*np.eye(k))
    assert r.data["metadata"]["resolution"] >= resolution
    assert r.data["metadata"]["defining_words"][0] == "I"


def test_custom_fraction_aliases():
    r = ok(doe,factors=FACTORS+";w,-1,1",design="fractional_iv",generators="D=ABC",center_points=0)
    assert r.data["metadata"]["defining_words"] == ["I","ABCD"]
    assert "CD" in r.data["metadata"]["alias_sets"]["AB"]


def test_box_behnken_three_factor_point_set():
    r = ok(doe,factors=FACTORS,design="box_behnken",center_points=3)
    points = np.array(r.data["coded"])
    assert len(points)==15
    assert np.sum(np.abs(points[:12]),axis=1) == pytest.approx(np.full(12,2))
    assert len(set(map(tuple,points[:12])))==12
    assert not np.any(points[12:])


@pytest.mark.parametrize("design",["ccd_face","ccd_circumscribed","ccd_inscribed"])
def test_ccd_sets_and_rotatability(design):
    r = ok(doe,factors=FACTORS,design=design,center_points=2)
    x = np.array(r.data["coded"])
    assert x.shape==(16,3)
    alpha = r.data["metadata"]["alpha"]
    if design == "ccd_face":
        assert alpha==1
        assert np.max(np.abs(x))==1
    else:
        assert alpha==pytest.approx(8**.25)
        assert np.sum(x[:,0]**4) == pytest.approx(3*np.sum(x[:,0]**2*x[:,1]**2))
        assert r.data["metadata"]["rotatable"]
        if design == "ccd_inscribed":
            assert np.max(np.abs(x))==1


@pytest.mark.parametrize("design",["lhs","sobol","halton"])
def test_space_filling_reproducible_and_natural_coding(design):
    args = dict(factors='{"x":[10,20],"y":[0,100]}',design=design,samples=32,seed=83)
    a,b = ok(doe,**args),ok(doe,**args)
    x = np.array(a.data["coded"])
    assert a.data["coded"]==b.data["coded"]
    assert np.all(np.abs(x)<=1)
    natural = np.array(a.data["natural"])
    assert natural[:,0] == pytest.approx(15+5*x[:,0])
    if design == "lhs":
        assert sorted(np.floor((x[:,0]+1)*16).astype(int))==list(range(32))
    if design == "sobol":
        # Each coordinate of a digitally shifted 32-point net occupies every dyadic stratum once.
        for j in range(2):
            assert sorted(np.floor((x[:,j]+1)*16).astype(int))==list(range(32))


def test_exact_quadratic_stationary_and_box_extrema():
    r = ok(doe,factors="x,-2,2;y,-2,2",design="ccd_face",center_points=3,model="5+(x-.5)^2+2*(y+.25)^2")
    fit = r.data["fit"]
    assert fit["SSE"]<1e-22
    assert fit["stationary"]["nature"]=="minimum"
    assert fit["stationary"]["natural"]==pytest.approx([.5,-.25])
    assert fit["box_minimum"]["value"]==pytest.approx(5)
    assert fit["box_maximum"]["natural"]==pytest.approx([-2,2])
    assert fit["R_squared"]==pytest.approx(1)
    assert fit["ANOVA"]["F"] is None


def test_hand_anova_press_and_replicate_lack_of_fit():
    # Rows are -1,+1,0,0,0; y=1+2*x plus independent residuals -1,-1,0,0,2.
    r = ok(doe,factors="x,-1,1",center_points=3,responses="[-2,2,1,1,3]",fit="linear")
    fit = r.data["fit"]
    x = np.array([-1,1,0,0,0.])
    y = np.array([-2,2,1,1,3.])
    matrix = np.column_stack([np.ones(5),x])
    beta = np.linalg.solve(matrix.T@matrix,matrix.T@y)
    residual = y-matrix@beta
    sse = residual@residual
    sst = np.sum((y-y.mean())**2)
    f = (sst-sse)/(sse/3)
    assert fit["SSE"]==pytest.approx(sse)
    assert fit["ANOVA"]["F"]==pytest.approx(f)
    assert fit["lack_of_fit"]["pure_error_SS"]==pytest.approx(8/3)
    assert fit["lack_of_fit"]["pure_error_df"]==2
    deleted_errors = []
    for i in range(5):
        keep = np.arange(5)!=i
        coeff = np.linalg.lstsq(matrix[keep],y[keep],rcond=None)[0]
        deleted_errors.append(y[i]-matrix[i]@coeff)
    assert fit["PRESS"]==pytest.approx(np.sum(np.square(deleted_errors)))
    scipy = pytest.importorskip("scipy.stats")
    assert fit["ANOVA"]["p_value"]==pytest.approx(scipy.f.sf(f,1,3),rel=1e-9)
    se = fit["coefficients"][0]["standard_error"]
    ci = fit["coefficients"][0]["confidence_interval_95"]
    assert ci[1]-fit["coefficients"][0]["coefficient"]==pytest.approx(scipy.t.ppf(.975,3)*se,rel=1e-9)


def test_response_file_provenance(tmp_path):
    file = tmp_path/"response.csv"
    file.write_text("response\n-1\n3\n1\n",encoding="utf-8")
    r = ok(doe,factors="x,-1,1",responses_file=str(file),fit="linear")
    assert r.provenance.input_files[0].sha256
    assert r.data["fit"]["main_effects"]["x"]==pytest.approx(4)


@pytest.mark.parametrize("kwargs",[
    {}, {"factors":"x,1,1"}, {"design":"box_behnken","factors":"x,-1,1"},
    {"design":"fractional_v"}, {"generators":"D=ABC"},
    {"model":"x+y+z"}, {"responses":"[1,2]"},
    {"design":"lhs","samples":4096,"lhs_candidates":32},
    {"factors":";".join(f"x{i},0,1" for i in range(7))},
])
def test_refusals(kwargs):
    base = {"factors":FACTORS} if kwargs else {}
    assert not call(doe,**{**base,**kwargs}).ok
