"""Lame boundary values, sizing, constitutive displacement and ODE reference."""

import math
import random

import pytest

from eng_toolkit.domains.machine.pressure_vessel import pressure_vessel
from tests.machine_testing import call, close, ok, v

BASE = dict(geometry="cylinder",inner_radius="50 mm",thickness="50 mm",internal_pressure="30 MPa")


@pytest.mark.parametrize("ends,axial",[("closed",10),("open",0),("constrained",6)])
def test_thick_cylinder_hand_values(ends,axial):
    r = ok(pressure_vessel, **BASE, ends=ends, youngs_modulus="200 GPa", yield_strength="200 MPa")
    # a=50,b=100, pi=30,po=0: A=30*2500/7500=10; B=30*2500*10000/7500=100000 MPa mm2.
    # Bore sr=10-100000/2500=-30; st=10+40=50. Closed axial=10, open=0, constrained=.6*10=6.
    close(v(r,"radial_stress"),-30)
    close(v(r,"hoop_stress"),50)
    close(v(r,"axial_stress"),axial)
    vm = math.sqrt((80**2+(50-axial)**2+(axial+30)**2)/2)
    close(v(r,"inner_von_mises"),vm)
    close(v(r,"inner_tresca"),80)
    close(v(r,"yield_safety_factor"),200/vm)
    # u=a/E*(st-nu*(sr+sz))=50/200000*(50-.3*(-30+axial)).
    close(v(r,"inner_displacement"),50/200000*(50-.3*(-30+axial)))


def test_sphere_thick_thin_and_required_wall():
    r = ok(pressure_vessel,geometry="sphere",inner_radius="50 mm",outer_radius="100 mm",internal_pressure="7 MPa",youngs_modulus="100 GPa",poisson_ratio=.25,allowable_stress="5 MPa")
    # a^3=125000,b^3=1000000: A=7/7=1; B/a^3=8. sr=-7, st=1+8/2=5 MPa.
    close(v(r,"radial_stress"),-7)
    close(v(r,"hoop_stress"),5)
    close(v(r,"axial_stress"),5)
    close(v(r,"inner_von_mises"),12)
    close(v(r,"inner_displacement"),50/100000*(.75*5+.25*7))
    close(v(r,"required_thickness_lame"),50)  # ((5+7)/(5-3.5))^(1/3)=2.
    close(v(r,"required_thickness_thin"),35)  # 7*50/(2*5)=35.
    thin = ok(pressure_vessel,geometry="sphere",inner_radius="50 mm",thickness="1 mm",internal_pressure="2 MPa",model="thin")
    close(v(thin,"hoop_stress"),50)  # p*r/(2t)=2*50/2.


def test_sizing_inversion_external_pressure_and_units():
    sizing = ok(pressure_vessel,geometry="cylinder",inner_radius="50 mm",internal_pressure="30 MPa",allowable_stress="50 MPa")
    # b/a=sqrt((S+p)/(S-p))=sqrt(80/20)=2, t=50 mm; thin=30*50/50=30 mm.
    close(v(sizing,"required_thickness_lame"),50)
    close(v(sizing,"required_thickness_thin"),30)
    thin_sizing = ok(pressure_vessel,geometry="cylinder",inner_radius="50 mm",internal_pressure="30 MPa",allowable_stress="50 MPa",model="thin")
    # Sizing-only calls still need the membrane validity warning: r/t=50/30<10.
    close(v(thin_sizing,"required_thickness_thin"),30)
    assert any("below 10" in w for w in thin_sizing.warnings)
    ext = ok(pressure_vessel,**{**BASE,"internal_pressure":"0 MPa"},external_pressure="30 MPa",allowable_stress="80 MPa")
    # A=-40, B=-100000; bore sr=0, st=-80; external sizing b/a=sqrt(-80/-20)=2.
    close(v(ext,"hoop_stress"),-80)
    close(v(ext,"required_thickness_lame"),50)
    assert any("buckling" in w for w in ext.warnings)
    ext_sizing = ok(pressure_vessel,geometry="cylinder",inner_radius="50 mm",internal_pressure="0 MPa",external_pressure="30 MPa",allowable_stress="80 MPa")
    assert any("buckling" in w for w in ext_sizing.warnings)
    inch = ok(pressure_vessel,geometry="cylinder",inner_radius="1 inch",thickness="1 inch",internal_pressure="3000 psi",unit_system="imperial")
    # Geometric ratio 2:1 gives bore hoop=(5/3)*3000=5000 psi.
    close(v(inch,"hoop_stress","psi"),5000)


def test_thin_limit_warning_and_boundary_equilibrium():
    thick = ok(pressure_vessel,geometry="cylinder",inner_radius="100 mm",thickness="0.01 mm",internal_pressure="1 MPa")
    # Thin hoop=100/.01=10000; exact bore hoop differs by ~0.5 MPa, relative <1e-4.
    close(v(thick,"hoop_stress"),10000,rel=1e-4)
    thin = ok(pressure_vessel,**BASE,model="thin")
    close(v(thin,"hoop_stress"),30)  # 30*50/50=30; Lame=50, relative error=-.4.
    close(thin.data["thin_hoop_relative_error_against_lame"],-.4)
    assert any("below 10" in w for w in thin.warnings)
    outer = ok(pressure_vessel,**BASE,external_pressure="5 MPa",radius="100 mm")
    close(v(outer,"radial_stress"),-5)  # radial boundary traction at b is -po.
    # At r=75, central finite difference verifies dsr/dr=(st-sr)/r.
    rr = ok(pressure_vessel,**BASE,radius="75 mm")
    left = ok(pressure_vessel,**BASE,radius="74.999 mm")
    right = ok(pressure_vessel,**BASE,radius="75.001 mm")
    close((v(right,"radial_stress")-v(left,"radial_stress"))/.002,
          (v(rr,"hoop_stress")-v(rr,"radial_stress"))/75,rel=1e-8)


def integrate_plane_stress(a,b,pi,po,e,nu):
    # Independent open-end constitutive BVP: state=[u,sr]; st=E*u/r+nu*sr.
    # u'=(sr-nu*st)/E; sr'=(st-sr)/r. Shoot on u(a) to hit sr(b)=-po.
    def march(u0):
        state = (u0,-pi)
        h = (b-a)/1000
        def deriv(x,s):
            u,sr=s
            st=e*u/x+nu*sr
            return ((sr-nu*st)/e,(st-sr)/x)
        def plus(s,k,scale):
            return tuple(x+scale*y for x,y in zip(s,k))
        for i in range(1000):
            x=a+i*h
            k1=deriv(x,state)
            k2=deriv(x+h/2,plus(state,k1,h/2))
            k3=deriv(x+h/2,plus(state,k2,h/2))
            k4=deriv(x+h,plus(state,k3,h))
            state=tuple(s+h*(v1+2*v2+2*v3+v4)/6 for s,v1,v2,v3,v4 in zip(state,k1,k2,k3,k4))
        return state[1]
    s0,s1=march(0),march(1)
    return (-po-s0)/(s1-s0)


def test_random_lame_displacement_against_numeric_equilibrium():
    rng=random.Random(713)
    for _ in range(6):
        a=rng.uniform(20,80)
        b=a*rng.uniform(1.1,2.5)
        pi,po=rng.uniform(10,50),rng.uniform(0,8)
        e,nu=200000,.3
        r=ok(pressure_vessel,geometry="cylinder",inner_radius=f"{a} mm",outer_radius=f"{b} mm",internal_pressure=f"{pi} MPa",external_pressure=f"{po} MPa",youngs_modulus="200 GPa",ends="open")
        close(v(r,"inner_displacement"),integrate_plane_stress(a,b,pi,po,e,nu),rel=1e-8)


@pytest.mark.parametrize("changes",[dict(outer_radius="100 mm"),dict(thickness="",outer_radius="40 mm"),dict(radius="101 mm"),dict(allowable_stress="20 MPa"),dict(geometry="sphere",ends="open")])
def test_vessel_refusals(changes):
    assert not call(pressure_vessel,**{**BASE,**changes}).ok


def test_joint_efficiency_scales_the_allowable_and_corrosion_is_added_to_the_wall():
    # UAT M3: air receiver, inner radius 600 mm, 1.1 MPa, f = 170.8 MPa, joint coefficient 0.85 (it was folded into the allowable by hand), corrosion 1 mm.
    # S E = 170.8 * 0.85 = 145.18 MPa; thin t = 1.1 * 600 / 145.18 = 4.5461 mm; Lame b/a = sqrt((S+p)/(S-p)): t = 600 (sqrt(146.28/144.08) - 1) = 4.5630 mm.
    r = ok(pressure_vessel,geometry="cylinder",inner_radius="600 mm",internal_pressure="1.1 MPa",allowable_stress="170.8 MPa",joint_efficiency=.85,corrosion_allowance="1 mm")
    thin = 1.1*600/(170.8*.85)
    lame = 600*(math.sqrt((145.18+1.1)/(145.18-1.1))-1)
    close(v(r,"required_thickness_thin"),thin)
    close(v(r,"required_thickness_lame"),lame)
    close(v(r,"nominal_thickness_thin"),thin+1)
    close(v(r,"nominal_thickness_lame"),lame+1)
    assert 4.54 < v(r,"required_thickness_thin") < 4.55 and 4.56 < v(r,"required_thickness_lame") < 4.57
    plain = ok(pressure_vessel,geometry="cylinder",inner_radius="600 mm",internal_pressure="1.1 MPa",allowable_stress="145.18 MPa")
    close(v(plain,"required_thickness_lame"),lame,rel=1e-12)  # E = 0.85 on 170.8 is the same as the folded allowable
    assert "nominal_thickness_lame" not in plain.values
    with_wall = ok(pressure_vessel,geometry="cylinder",inner_radius="600 mm",thickness="6 mm",internal_pressure="1.1 MPa",allowable_stress="170.8 MPa",joint_efficiency=.85,corrosion_allowance="1 mm")
    close(v(with_wall,"thickness_after_corrosion"),5)
    close(v(with_wall,"thickness_margin_after_corrosion"),5-lame)
    close(v(with_wall,"hoop_stress"),v(ok(pressure_vessel,geometry="cylinder",inner_radius="600 mm",thickness="6 mm",internal_pressure="1.1 MPa"),"hoop_stress"))  # stresses are not scaled by E


def test_joint_efficiency_and_corrosion_need_an_allowable_and_stay_in_range():
    for changes in ({"joint_efficiency":.8},{"corrosion_allowance":"1 mm"}):
        r = call(pressure_vessel,geometry="cylinder",inner_radius="600 mm",thickness="6 mm",internal_pressure="1.1 MPa",**changes)
        assert r.ok is False and r.error.details["param"] == "/allowable_stress" and r.error.family == "input.missing"
    for efficiency in (.49,1.01,0,-1):
        r = call(pressure_vessel,geometry="cylinder",inner_radius="600 mm",internal_pressure="1.1 MPa",allowable_stress="170 MPa",joint_efficiency=efficiency)
        assert r.ok is False and r.error.details["param"] == "/joint_efficiency"
    r = call(pressure_vessel,geometry="cylinder",inner_radius="600 mm",internal_pressure="1.1 MPa",allowable_stress="170 MPa",corrosion_allowance="-1 mm")
    assert r.ok is False and r.error.details["param"] == "/corrosion_allowance"


def test_the_sphere_result_warns_that_it_is_not_a_dished_end():
    # UAT M3-4: the sphere gave 2.27 mm for a 2:1 ellipsoidal end (the cylinder gives 4.55 mm) and nothing said it was the wrong shape.
    sphere = ok(pressure_vessel,geometry="sphere",inner_radius="600 mm",internal_pressure="1.1 MPa",allowable_stress="145.18 MPa",model="thin")
    close(v(sphere,"required_thickness_thin"),1.1*600/(2*145.18))
    cylinder = ok(pressure_vessel,geometry="cylinder",inner_radius="600 mm",internal_pressure="1.1 MPa",allowable_stress="145.18 MPa",model="thin")
    close(v(cylinder,"required_thickness_thin"),2*v(sphere,"required_thickness_thin"))
    warning = next(w for w in sphere.warnings if "hemispherical" in w)
    assert "ellipsoidal" in warning and "torispherical" in warning and "NOT" in warning
    assert not any("hemispherical" in w for w in cylinder.warnings)
    for r in (sphere,cylinder):
        assert any("Not modelled" in w and "nozzle" in w and "heads" in w for w in r.warnings)
