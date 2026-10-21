"""Column and stress arithmetic; default 1e-10 tolerance unless noted."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.structures.column import column
from eng_toolkit.domains.structures.stress import stress
from tests.structures_testing import COLUMN, STRESS, call, close, ok, v


def test_column_euler_johnson_and_safety():
    r = ok(column,**COLUMN,applied_load="10 kN")
    # EI=200e9*1e-6=200000 N m²; Euler=pi²*200000/2²; lambda²=4/(.001)=4000.
    close(v(r,"euler_load"),math.pi**2*200000/4)
    # Johnson A*[Sy-Sy²*lambda²/(4pi²E)] with A=.001.
    johnson = .001*(250e6-250e6**2*4000/(4*math.pi**2*200e9))
    close(v(r,"johnson_load"),johnson)
    close(v(r,"safety_factor"),johnson/10000)
    assert "inelastic" in r.data["elastic_check"]
    elastic = ok(column,**{**COLUMN,"length":"10 m"})
    assert elastic.data["selected_method"] == "Euler"


@pytest.mark.parametrize("condition,k",[("pinned-pinned",1),("fixed-free",2),("fixed-fixed",.5),("fixed-pinned",.699155659642841)])
def test_column_effective_lengths(condition,k):
    r = ok(column,**COLUMN,end_condition=condition)
    # Euler load changes by 1/K²; fixed-pinned root z=4.493409457909064, K=pi/z.
    close(v(r,"effective_length_factor"),k,rel=2e-12)
    close(v(r,"euler_load"),math.pi**2*200000/(2*k)**2)


def test_secant_yield_constructed_root():
    # E=10000 Pa,A=1m²,I=1m⁴,L=pi m: Pe=10000N.
    # At P=2500N, angle=pi/4; e=c=r=1, stress=2500(1+sqrt(2)).
    sy = 2500*(1+math.sqrt(2))
    r = ok(column,length=f"{math.pi} m",youngs_modulus="10000 Pa",area="1 m^2",inertia="1 m^4",yield_strength=f"{sy} Pa",eccentricity="1 m",extreme_fibre="1 m",applied_load="2500 N")
    close(v(r,"secant_yield_load"),2500,rel=2e-12)
    close(v(r,"maximum_stress"),sy)
    zero = ok(column,length=f"{math.pi} m",youngs_modulus="10000 Pa",area="1 m^2",inertia="1 m^4",yield_strength="20000 Pa",eccentricity="0 m",extreme_fibre="1 m")
    assert zero.data["secant_yield_load"] is None


def test_column_imperial():
    r = ok(column,length="100 inch",youngs_modulus="30000000 psi",area="2 inch^2",inertia="3 inch^4",unit_system="imperial")
    # In consistent inch/lbf units: Pe=pi²*30e6*3/100².
    close(v(r,"euler_load"),math.pi**2*30e6*3/100**2)


def test_mohr_textbook_and_brittle():
    r = ok(stress,**STRESS,angle="45 deg",yield_strength="200 MPa",tensile_strength="100 MPa",compressive_strength="300 MPa")
    # Mean=50, radius=sqrt(30²+40²)=50; in-plane principals 100,0 MPa.
    close(v(r,"principal_stress_1","MPa"),100)
    close(v(r,"principal_stress_3","MPa"),0)
    close(v(r,"von_mises","MPa"),100)
    close(v(r,"transformed_sigma_x","MPa"),90)
    close(v(r,"transformed_tau_xy","MPa"),-30)
    close(v(r,"principal_angle","deg"),math.degrees(math.atan2(80,60)/2))
    close(v(r,"von_mises_safety_factor"),2)
    close(v(r,"mohr_coulomb_safety_factor"),1)


def test_3d_invariants_and_mixed_brittle():
    r = ok(stress,state="three_dimensional",sigma_x="100 MPa",sigma_y="50 MPa",sigma_z="-100 MPa",tau_xy="0 MPa",tau_yz="0 MPa",tau_zx="0 MPa",tensile_strength="200 MPa",compressive_strength="400 MPa")
    # Diagonal principal tensor: I1=50, I2=5000-5000-10000=-10000, I3=-500000 MPa powers.
    close(v(r,"I1","MPa"),50)
    close(v(r,"I2","MPa^2"),-10000)
    close(v(r,"I3","MPa^3"),-500000)
    close(v(r,"tresca","MPa"),200)
    close(v(r,"mohr_coulomb_safety_factor"),1/(100/200+100/400))


def test_hooke_plane_stress_strain_and_imperial():
    r = ok(stress,sigma_x="100 MPa",sigma_y="0 MPa",tau_xy="0 MPa",youngs_modulus="200 GPa",poisson_ratio=.25)
    # ex=100/200000=.0005; ey=ez=-nu*ex=-.000125.
    close(v(r,"epsilon_x"),.0005)
    close(v(r,"epsilon_y"),-.000125)
    back = ok(stress,input_type="strain",epsilon_x="0.0005 dimensionless",epsilon_y="-0.000125 dimensionless",gamma_xy="0 dimensionless",youngs_modulus="200 GPa",poisson_ratio=.25)
    close(v(back,"sigma_x","MPa"),100)
    constrained = ok(stress,state="plane_strain",sigma_x="100 MPa",sigma_y="0 MPa",tau_xy="0 MPa",youngs_modulus="200 GPa",poisson_ratio=.25)
    close(v(constrained,"sigma_z","MPa"),25)
    close(v(constrained,"epsilon_z"),0)
    imperial = ok(stress,sigma_x="10 ksi",sigma_y="0 psi",tau_xy="0 psi",unit_system="imperial")
    close(v(imperial,"von_mises"),10000)


@pytest.mark.parametrize("state,b,c",[("rosette_45",.0008,-.0002),("rosette_60",.0001+.0002*math.sqrt(3),.0001-.0002*math.sqrt(3))])
def test_rosettes(state,b,c):
    # ex=.001,ey=-.0002,gxy=.0008. e(theta)=mean+diff*cos2theta+gamma/2*sin2theta.
    r = ok(stress,state=state,input_type="strain",strain_a=".001 dimensionless",strain_b=f"{b} dimensionless",strain_c=f"{c} dimensionless",youngs_modulus="100 GPa",poisson_ratio=.2)
    close(v(r,"epsilon_x"),.001)
    close(v(r,"epsilon_y"),-.0002)
    close(v(r,"gamma_xy"),.0008)
    # sx=E/(1-nu²)*(ex+nu*ey)=100MPa, sy=0, tau=E/[2(1+nu)]*gamma=100/3MPa.
    close(v(r,"sigma_x","MPa"),100)
    close(v(r,"tau_xy","MPa"),100/3)


@pytest.mark.parametrize("seed",range(12))
def test_random_mohr_against_eigh(seed):
    rng = np.random.default_rng(seed)
    sx,sy,tau = rng.uniform(-200,200,3)
    r = ok(stress,sigma_x=f"{sx} MPa",sigma_y=f"{sy} MPa",tau_xy=f"{tau} MPa")
    # Independent route: symmetric 2x2 eigensolver rather than Mohr mean/radius.
    expected = np.linalg.eigh(np.array([[sx,tau],[tau,sy]]))[0]
    close(v(r,"in_plane_principal_1","MPa"),expected[1])
    close(v(r,"in_plane_principal_2","MPa"),expected[0])


@pytest.mark.parametrize("seed",range(5))
def test_random_column_secant_independent_brent(seed):
    scipy = pytest.importorskip("scipy.optimize")
    rng = np.random.default_rng(seed)
    length,sy,ecc = rng.uniform(1,4),rng.uniform(150e6,400e6),rng.uniform(.001,.02)
    r = ok(column,**{**COLUMN,"length":f"{length} m","yield_strength":f"{sy} Pa"},eccentricity=f"{ecc} m",extreme_fibre=".05 m")
    # r²=I/A=.001; Pe=pi²*200000/L². Independent Brent bracket below Pe.
    pe = math.pi**2*200000/length**2
    def equation(p):
        return p/.001*(1+ecc*.05/.001/math.cos(math.pi/2*math.sqrt(p/pe)))-sy
    expected = scipy.brentq(equation,0,pe*(1-1e-10))
    close(v(r,"secant_yield_load"),expected,rel=1e-10)


def test_zero_stress_no_infinite_factors():
    r = ok(stress,sigma_x="0 Pa",sigma_y="0 Pa",tau_xy="0 Pa",yield_strength="200 MPa",tensile_strength="100 MPa",compressive_strength="200 MPa")
    close(v(r,"von_mises"),0)
    assert r.data["von_mises_safety_factor"] is None
    assert r.data["mohr_coulomb_safety_factor"] is None
    assert not r.data["principal_angle_unique"]


@pytest.mark.parametrize("tool,args",[(column,{**COLUMN,"effective_length_factor":1}), (column,{**COLUMN,"eccentricity":"1 mm"}), (stress,{**STRESS,"epsilon_x":"0 dimensionless"}), (stress,{**STRESS,"sigma_z":"1 Pa"}), (stress,{**STRESS,"tensile_strength":"1 Pa"})])
def test_closed_form_conflicts(tool,args):
    assert not call(tool,**args).ok
