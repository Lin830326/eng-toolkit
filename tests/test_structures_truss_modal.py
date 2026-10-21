"""Truss joints and modal analytical/generalized-eigenproblem references."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.structures.truss import truss
from eng_toolkit.domains.structures.modal import modal
from eng_toolkit.domains.structures._model import assemble_modal, mass_matrix, modes, parse_frame
from tests.structures_testing import FRAME, TRUSS, call, close, ok, quantity, v


def test_truss_bar_and_thermal():
    r = ok(truss,**TRUSS)
    # u=PL/(EA)=10000*2/(200e9*.0001)=.001m; sigma=100MPa.
    close(v(r,"maximum_displacement"),.001)
    close(quantity(r.data["members"][0]["axial_force"]),10000)
    close(quantity(r.data["members"][0]["stress"],"MPa"),100)
    thermal = ok(truss,**{**TRUSS,"supports":"a,xy;b,xy","loads":""},thermal="e,100 K,.00001 1/K")
    # Fully restrained bar N=-EA alpha DT=-200e9*.0001*.001=-20000N.
    close(quantity(thermal.data["members"][0]["axial_force"]),-20000)
    free = ok(truss,**{**TRUSS,"loads":""},temperature_change="100 K",thermal_expansion=".00001 1/K")
    close(v(free,"maximum_displacement"),.002)


@pytest.mark.parametrize("seed",range(8))
def test_triangle_method_of_joints(seed):
    p = float(np.random.default_rng(seed).uniform(10,10000))
    r = ok(truss,nodes="a,0 m,0 m;b,2 m,0 m;c,1 m,1 m",members="ab,a,b,200 GPa,100 mm^2;ac,a,c,200 GPa,100 mm^2;bc,b,c,200 GPa,100 mm^2",supports="a,xy;b,y",loads=f"c,0 N,{-p} N")
    # Each 45deg diagonal balances P/2 vertically -> -P/sqrt(2); chord=P/2.
    actual = {m["id"]:quantity(m["axial_force"]) for m in r.data["members"]}
    close(actual["ac"],-p/math.sqrt(2))
    close(actual["bc"],-p/math.sqrt(2))
    close(actual["ab"],p/2)
    for out in r.data["equilibrium_residual"].values():
        close(quantity(out),0,abs=1e-8)


def test_3d_truss_and_imperial_euler():
    r = ok(truss,dimension=3,nodes="a,0 m,0 m,0 m;b,1 m,1 m,1 m",members="e,a,b,200 GPa,100 mm^2",supports="a,xyz;b,yz",loads="b,1000 N,0 N,0 N")
    # kxx=(EA/L)*nx²=20e6/(3sqrt(3)); ux=1000*3sqrt(3)/20e6.
    close(v(r,"maximum_displacement"),1000*3*math.sqrt(3)/20e6)
    imp = ok(truss,nodes="a,0 inch,0 inch;b,100 inch,0 inch",members="e,a,b,30000000 psi,2 inch^2,3 inch^4",supports="a,xy;b,y",loads="b,-1 kip,0 kip",unit_system="imperial")
    # u=1000*100/(30e6*2)=1/600in; Euler=pi²*30e6*3/100² lbf.
    close(v(imp,"maximum_displacement"),1/600)
    close(quantity(imp.data["members"][0]["euler_critical_load"]),math.pi**2*30e6*3/100**2)


def test_modal_cantilever_refinement_and_beam_reference():
    results = [ok(modal,**FRAME,density="7800 kg/m^3",elements_per_member=n,modes_count=3) for n in (1,4,12)]
    # mu=7800*.001=7.8kg/m; f1=1.8751040687²/(2pi)*sqrt(200000/(7.8*2⁴)).
    expected = 1.875104068711961**2/(2*math.pi)*math.sqrt(200000/(7.8*16))
    errors = [abs(v(r,"first_frequency")-expected) for r in results]
    assert errors[2] < errors[1] < errors[0]
    close(v(results[-1],"first_frequency"),expected,rel=5e-7)
    close(results[-1].data["closed_form_comparison"]["bending_frequencies_hz"][0],expected)


@pytest.mark.parametrize("release,support",[("none","a,pinned;b,roller_y"),("both","a,fixed;b,fixed")])
def test_modal_simply_supported_and_dynamic_releases(release,support):
    r = ok(modal,**{**FRAME,"members":FRAME["members"]+","+release,"supports":support},mass_per_length="10 kg/m",elements_per_member=12,modes_count=3)
    # f1=pi/(2L²)*sqrt(EI/mu)=pi/8*sqrt(20000); fn=n²f1 before axial interleaving.
    expected = math.pi/8*math.sqrt(20000)
    close(v(r,"first_frequency"),expected,rel=4e-6)
    close(quantity(r.data["modes"][1]["frequency"]),4*expected,rel=6e-5)
    if release == "both":
        assert len(r.data["private_release_rotations"]) == 2
        coarse = ok(modal,**{**FRAME,"members":FRAME["members"]+",both","supports":"a,fixed;b,fixed"},mass_per_length="10 kg/m",elements_per_member=1,modes_count=2)
        # One-element pinned beam keeps two private rotations; first eigenvalue=120 EI/(mu L⁴).
        close(v(coarse,"first_frequency"),math.sqrt(120*200000/(10*16))/(2*math.pi))


def test_free_free_rigid_modes_and_effective_mass():
    r = ok(modal,**{**FRAME,"supports":""},density="7800 kg/m^3",modes_count=12)
    assert r.data["zero_mode_count"] == 3
    for mode in r.data["modes"][:3]:
        close(quantity(mode["frequency"]),0)
    # Complete rigid eigenspace carries all global translational mass.
    for axis in ("x","y"):
        close(sum(mode["effective_mass_fraction_"+axis] for mode in r.data["modes"][:3]),1,rel=1e-9)


def test_modal_scipy_and_mass_orthogonality():
    scipy = pytest.importorskip("scipy.linalg")
    model = parse_frame(**FRAME)
    ids,xy,k,mass,_,strain = assemble_modal(model,4,[7.8],np.zeros(6))
    free = [j for j in range(len(k)) if j not in model.fixed]
    expected = scipy.eigh(k[np.ix_(free,free)],mass[np.ix_(free,free)],eigvals_only=True)
    eig,phi,zeros,residuals = modes(k,mass,model.fixed,len(free),strain)
    # Independent generalized symmetric solver; mass-normalized phi^T M phi=identity.
    np.testing.assert_allclose(eig,expected,rtol=1e-9,atol=1e-6)
    np.testing.assert_allclose(phi.T@mass@phi,np.eye(len(free)),rtol=1e-10,atol=1e-10)
    assert max(residuals) < 1e-12
    assert zeros == 0


@pytest.mark.parametrize("seed",range(5))
def test_random_modal_against_closed_beam_and_mass_quadrature(seed):
    rng = np.random.default_rng(seed)
    length,ei,mu = rng.uniform(1,5),rng.uniform(1e5,1e6),rng.uniform(1,50)
    r = ok(modal,nodes=f"a,0 m,0 m;b,{length} m,0 m",members=f"e,a,b,200 GPa,1000 mm^2,{ei/200e9} m^4",supports="a,fixed",mass_per_length=f"{mu} kg/m",elements_per_member=10,modes_count=1)
    # omega1=beta1² sqrt(EI/(mu L⁴)); 10-element frequency tolerance 2e-6.
    expected = 1.875104068711961**2/(2*math.pi)*math.sqrt(ei/(mu*length**4))
    close(v(r,"first_frequency"),expected,rel=2e-6)
    # Independent route: integrate products of cubic Hermite shapes with 4-point
    # Gauss quadrature, exact through degree 7, while shape products have degree 6.
    points,weights = np.polynomial.legendre.leggauss(4)
    integrated = np.zeros((6,6))
    for p,w in zip(points,weights):
        x = (p+1)/2
        axial = np.array([1-x,0,0,x,0,0])
        bend = np.array([0,1-3*x*x+2*x**3,length*(x-2*x*x+x**3),0,3*x*x-2*x**3,length*(-x*x+x**3)])
        integrated += mu*length*w/2*(np.outer(axial,axial)+np.outer(bend,bend))
    np.testing.assert_allclose(mass_matrix(mu,length),integrated,rtol=1e-12,atol=1e-12)


def test_modal_refined_dof_cap():
    # 16 members * 19 internal nodes +17 originals gives 963 joint DOFs >900.
    nodes = ";".join(f"n{i},{i} m,0 m" for i in range(17))
    members = ";".join(f"e{i},n{i},n{i+1},200 GPa,1000 mm^2,1e6 mm^4" for i in range(16))
    r = call(modal,nodes=nodes,members=members,supports="n0,fixed",density="7800 kg/m^3",elements_per_member=20)
    assert not r.ok and r.error.details["param"] == "/elements_per_member"


def test_modal_nodal_mass_spring_and_imperial():
    base = ok(modal,**FRAME,mass_per_length="10 kg/m")
    loaded = ok(modal,**FRAME,mass_per_length="10 kg/m",nodal_masses="b,10 kg,0 kg*m^2")
    assert v(loaded,"first_frequency") < v(base,"first_frequency")
    metric = ok(modal,nodes="a,0 m,0 m;b,2.54 m,0 m",members="e,a,b,206842718795.05084 Pa,0.00129032 m^2,4.162314256e-6 m^4",supports="a,fixed",mass_per_length="10 kg/m")
    imperial = ok(modal,nodes="a,0 inch,0 inch;b,100 inch,0 inch",members="e,a,b,30000000 psi,2 inch^2,10 inch^4",supports="a,fixed",mass_per_length="10 kg/m",unit_system="imperial")
    close(v(imperial,"first_frequency"),v(metric,"first_frequency"),rel=1e-9)


@pytest.mark.parametrize("args",[{**FRAME},{**FRAME,"density":"7800 kg/m^3","mass_per_length":"1 kg/m"},{**FRAME,"density":"7800 kg/m^3","elements_per_member":21},{**FRAME,"density":"7800 kg/m^3","nodal_masses":"missing,1 kg,0 kg*m^2"}])
def test_modal_refusals(args):
    assert not call(modal,**args).ok
