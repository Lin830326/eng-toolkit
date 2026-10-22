"""Handbook normalization and independent integration/root references."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.fatigue.fracture import fracture
from tests.fatigue_testing import FM, call, close, ok, table, v


def test_infinite_plate_irwin_size_and_critical():
    r=ok(fracture,**FM,toughness="50 MPa sqrt(m)",yield_strength="500 MPa",thickness="50 mm")
    # K=100*sqrt(pi*.01)=17.72453851 MPa sqrt(m); Irwin radii (K/500)^2/(2pi), /(6pi); zone sizes are twice those.
    expected=100*math.sqrt(math.pi*.01)
    close(v(r,"stress_intensity"),expected)
    close(v(r,"ry_plane_stress"),.0002)
    close(v(r,"ry_plane_strain"),.0002/3)
    close(v(r,"rp_plane_stress"),.0004)
    close(v(r,"rp_plane_strain"),.0004/3)
    close(v(r,"plane_strain_minimum_dimension"),.025)
    assert not r.data["plane_strain_size_screen_passed"]
    r=ok(fracture,mode="critical",stress="100 MPa",toughness="50 MPa*m^0.5",yield_strength="500 MPa")
    close(v(r,"critical_crack_size"),(50/100)**2/math.pi,rel=2e-9)


@pytest.mark.parametrize("geometry,expected",[
    ("center_secant",1/math.sqrt(math.cos(math.pi*.1))),
    ("center_tada",(1-.025*.2**2+.06*.2**4)/math.sqrt(math.cos(math.pi*.1))),
    ("edge",1.122-.231*.1+10.55*.1**2-21.71*.1**3+30.382*.1**4),
    ("double_edge",(1.122-.561*.2-.205*.2**2+.471*.2**3-.190*.2**4)/math.sqrt(.8)),
])
def test_plate_handbook_factors(geometry,expected):
    # a/W=.1; central/double-edge correction argument is 2a/W=.2.
    r=ok(fracture,**FM,geometry=geometry,width="100 mm")
    close(v(r,"geometry_factor"),expected)


@pytest.mark.parametrize("geometry,expected",[("ct",9.65907863100824),("senb",2.6625)])
def test_astm_specimen_factors_at_half_width(geometry,expected):
    # CT: 2.5*(.886+2.32-3.33+1.84-.35)/.5^1.5=3.415/.5^1.5=9.659078631.
    # SENB: 3*sqrt(.5)*(1.99-.25*(2.15-3.93*.5+2.7*.25))/(2*2*.5^1.5)=2.6625.
    args=dict(geometry=geometry,crack_size="50 mm",width="100 mm",thickness="10 mm",load="1 kN")
    if geometry=="senb":
        args["span"]="400 mm"
    r=ok(fracture,**args)
    close(v(r,"geometry_factor"),expected)
    base=1000/(.01*math.sqrt(.1))/1e6
    close(v(r,"stress_intensity"),base*expected*(4 if geometry=="senb" else 1))


@pytest.mark.parametrize("aspect,depth",[(1,.2),(1,.6),(.5,.2),(.5,.6),(.2,.8)])
def test_newman_raju_hand_evaluated_components(aspect,depth):
    a=.002
    t=a/depth
    width=1.
    c=a/aspect
    # Published 1981 tension equation at phi=pi/2 and phi=0, including Q and full-width W convention.
    m1=1.13-.09*aspect
    m2=-.54+.89/(.2+aspect)
    m3=.5-1/(.65+aspect)+14*(1-aspect)**24
    f=(m1+m2*depth**2+m3*depth**4)/math.sqrt(math.cos(math.pi*c/width*math.sqrt(depth)))
    deepest=f/math.sqrt(1+1.464*aspect**1.65)
    surface=deepest*math.sqrt(aspect)*(1.1+.35*depth**2)
    r=ok(fracture,geometry="surface",crack_size=f"{a} m",surface_half_length=f"{c} m",width="1 m",thickness=f"{t} m",stress="100 MPa")
    close(v(r,"K_deepest"),100*math.sqrt(math.pi*a)*deepest)
    close(v(r,"K_surface"),100*math.sqrt(math.pi*a)*surface)


@pytest.mark.parametrize("depth,surface_f,deepest_f",[(.2,1.174,1.049),(.4,1.229,1.062),(.6,1.355,1.107),(.8,1.464,1.112)])
def test_newman_raju_published_finite_element_table(depth,surface_f,deepest_f):
    # NASA TP-1578 Table I (printed p.20, PDF p.24), a/c=1, c/b<=.2.
    # Table F=K/[S*sqrt(pi*a/Q)], Q=2.464. Empirical equation is checked to its stated 5% accuracy,
    # not machine precision against a finite-element dataset. W=20 mm gives c/b=.2 for a=c=2 mm.
    r=ok(fracture,geometry="surface",crack_size="2 mm",surface_half_length="2 mm",width="20 mm",
         thickness=f"{.002/depth:.15g} m",stress="100 MPa")
    scale=100*math.sqrt(math.pi*.002/2.464)
    close(v(r,"K_surface")/scale,surface_f,rel=.05)
    close(v(r,"K_deepest")/scale,deepest_f,rel=.05)


@pytest.mark.parametrize("m",[2,3,4,.5])
def test_paris_closed_form(m):
    a0,af,c,ds=.001,.01,1e-10,50
    r=ok(fracture,mode="growth",crack_size="1 mm",final_crack="10 mm",stress_range="50 MPa",
         growth_c=f"{c} m/cycle/(MPa*m^0.5)^{m}",growth_m=m)
    # Integral a^(-m/2) da / [C*(Delta_sigma*sqrt(pi))^m]; logarithmic limit for m=2.
    exponent=1-m/2
    expected=(math.log(af/a0) if m==2 else (af**exponent-a0**exponent)/exponent)/(c*(ds*math.sqrt(math.pi))**m)
    close(v(r,"cycles_to_limit"),expected,rel=1e-7)
    rows=table(r)
    assert len(rows)==31
    close(float(rows[-1]["cycles"]),expected,rel=1e-7)


def test_imperial_composite_coefficient_conversion():
    # 1 ksi sqrt(in)=6.894757293 MPa*sqrt(.0254 m). C_SI=C_in*.0254/Kconversion^m.
    kconversion=6.894757293168361*math.sqrt(.0254)
    c_imperial=1e-8
    c_si=c_imperial*.0254/kconversion**3
    a=ok(fracture,mode="growth",crack_size=".1 inch",final_crack="1 inch",stress_range="10 ksi",
         growth_c="1e-8 inch/cycle/(ksi sqrt(inch))^3")
    b=ok(fracture,mode="growth",crack_size="2.54 mm",final_crack="25.4 mm",stress_range=f"{10*6.894757293168361} MPa",
         growth_c=f"{c_si} m/cycle/(MPa*m^0.5)^3")
    close(v(a,"cycles_to_limit"),v(b,"cycles_to_limit"))
    c=ok(fracture,crack_size="1 inch",stress="10 ksi")
    close(v(c,"stress_intensity","ksi*inch^0.5"),10*math.sqrt(math.pi))


def test_variable_geometry_quad_and_log_rk4():
    integrate=pytest.importorskip("scipy.integrate")
    c,ds,w,a0,af=1e-10,40,.1,.001,.02
    def independent_rate(a):
        x=a/w
        y=1.122-.231*x+10.55*x*x-21.71*x**3+30.382*x**4
        return c*(y*ds*math.sqrt(math.pi*a))**3
    expected,error=integrate.quad(lambda a:1/independent_rate(a),a0,af,epsabs=1e-6,epsrel=1e-10)
    # Independent RK4 for dN/du=a/(da/dN), u=ln(a), 2000 log-spaced steps.
    grid=np.linspace(math.log(a0),math.log(af),2001)
    n=0.
    for left,right in zip(grid,grid[1:]):
        h=right-left
        f=lambda u:math.exp(u)/independent_rate(math.exp(u))
        n+=h*(f(left)+4*f(left+h/2)+f(right))/6
    close(n,expected,rel=1e-9)
    r=ok(fracture,mode="growth",geometry="edge",width="100 mm",crack_size="1 mm",final_crack="20 mm",stress_range="40 MPa",
         growth_c="1e-10 m/cycle/(MPa*m^0.5)^3")
    close(v(r,"cycles_to_limit"),expected,rel=2e-7)


def test_forman_quad_threshold_and_instability():
    integrate=pytest.importorskip("scipy.integrate")
    r=ok(fracture,mode="growth",crack_size="1 mm",final_crack="10 mm",stress="100 MPa",load_ratio=.2,toughness="50 MPa*m^0.5",
         growth_law="forman",growth_c="1e-9 m/cycle/(MPa*m^0.5)^2")
    # da/dN=C*(80sqrt(pi*a))^3/[.8*50-80sqrt(pi*a)].
    expected=integrate.quad(lambda a:(40-80*math.sqrt(math.pi*a))/(1e-9*(80*math.sqrt(math.pi*a))**3),.001,.01)[0]
    close(v(r,"cycles_to_limit"),expected,rel=1e-7)
    arrested=ok(fracture,mode="growth",crack_size="1 mm",final_crack="10 mm",stress_range="10 MPa",threshold="5 MPa*m^0.5",
                growth_c="1e-10 m/cycle/(MPa*m^0.5)^3")
    assert arrested.data["cycles_to_failure"] is None
    close(v(arrested,"final_crack_size"),.001)
    critical=ok(fracture,mode="growth",crack_size="1 mm",stress="100 MPa",toughness="20 MPa*m^0.5",
                growth_law="forman",growth_c="1e-9 m/cycle/(MPa*m^0.5)^2")
    close(v(critical,"final_crack_size"),.04/math.pi,rel=2e-9)
    assert "cycles_to_failure" in critical.values


@pytest.mark.parametrize("args",[
    dict(geometry="edge",width="10 mm",crack_size="11 mm"),
    dict(geometry="edge",width="100 mm",crack_size="61 mm"),
    dict(geometry="surface",width="100 mm",thickness="5 mm",surface_half_length="2 mm",crack_size="3 mm"),
    dict(geometry="surface",width="100 mm",thickness="5 mm",surface_half_length="5 mm",crack_size="4.5 mm"),
    dict(yield_strength="100 MPa"),dict(mode="critical",toughness="1e6 MPa*m^0.5"),
    dict(mode="growth",final_crack="20 mm",growth_c="1e-10 m/cycle",growth_m=3),
    dict(mode="growth",final_crack="20 mm",growth_c="1e-10 m/cycle/(MPa*m^0.5)^3",toughness="1 MPa*m^0.5"),
])
def test_fracture_refusals(args):
    assert not call(fracture,**{**FM,**args}).ok
