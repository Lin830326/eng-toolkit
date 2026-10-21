"""Section references use direct area integrals or textbook arithmetic."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.structures.section import section
from tests.structures_testing import call, close, ok, v


def polygon_text(points):
    return ";".join(f"{0 if abs(x)<1e-12 else x:.15g} m,{0 if abs(y)<1e-12 else y:.15g} m" for x,y in points)


def test_rectangle_properties_and_shear():
    result = ok(section,width="100 mm",height="200 mm",at_height="100 mm",shear_force="10 kN")
    # A=100*200; Ix=100*200^3/12; Iy=200*100^3/12; Q=(100*100)*50.
    close(v(result,"area","mm^2"),20000)
    close(v(result,"Ix","mm^4"),100*200**3/12)
    close(v(result,"Iy","mm^4"),200*100**3/12)
    close(v(result,"first_moment_Q","mm^3"),500000)
    close(v(result,"shear_flow","N/mm"),10000*500000/(100*200**3/12))
    # Z=b*h^2/4=1e6; S=b*h^2/6; Z/S=1.5.
    close(v(result,"plastic_Zx","mm^3"),1e6)
    close(v(result,"shape_factor_x"),1.5)
    close(v(result,"centroid_y","mm"),100)


@pytest.mark.parametrize("hollow",[False,True])
def test_circle_exact(hollow):
    result = ok(section,shape="hollow_circle" if hollow else "circle",diameter="100 mm",**({"thickness":"10 mm"} if hollow else {}),at_height="0 mm")
    inner = 40 if hollow else 0
    # Integral in polar coordinates: A=pi(R²-r²), Ix=pi(R⁴-r⁴)/4, Z=4(R³-r³)/3.
    close(v(result,"area","mm^2"),math.pi*(50**2-inner**2))
    close(v(result,"Ix","mm^4"),math.pi*(50**4-inner**4)/4)
    close(v(result,"torsion_constant","mm^4"),math.pi*(50**4-inner**4)/2)
    close(v(result,"plastic_Zx","mm^3"),4*(50**3-inner**3)/3)
    close(v(result,"first_moment_Q","mm^3"),2*(50**3-inner**3)/3)


def test_hollow_rectangle_and_bredt():
    r = ok(section,shape="hollow_rectangle",width="100 mm",height="200 mm",thickness="5 mm")
    # Subtract 90x190 void; median area 95*195, perimeter 2(95+195), wall 5.
    close(v(r,"area","mm^2"),100*200-90*190)
    close(v(r,"Ix","mm^4"),(100*200**3-90*190**3)/12)
    close(v(r,"plastic_Zx","mm^3"),(100*200**2-90*190**2)/4)
    close(v(r,"torsion_constant","mm^4"),4*(95*195)**2/(2*(95+195)/5))


@pytest.mark.parametrize("shape",["i","h","t","channel"])
def test_open_shapes(shape):
    r = ok(section,shape=shape,width="100 mm",height="200 mm",web_thickness="10 mm",flange_thickness="30 mm")
    n = 1 if shape == "t" else 2
    web_h = 200-n*30
    # Nonoverlapping rectangles: flange n*100*30 and web 10*web_h; flange outstand/thickness ratio exceeds .25.
    close(v(r,"area","mm^2"),n*100*30+10*web_h)
    assert "torsion_constant" not in r.values
    assert r.data["torsion_constant"] is None
    if shape in ("i","h"):
        # Outer-minus-side-void formula Ix=[100*200³-90*140³]/12.
        close(v(r,"Ix","mm^4"),(100*200**3-90*140**3)/12)
        close(v(r,"plastic_Zx","mm^3"),(100*200**2-90*140**2)/4)
    assert any("thin-wall formula is not valid" in warning for warning in r.warnings)


def test_imperial_and_square_torsion():
    r = ok(section,width="2 inch",height="2 inch",unit_system="imperial")
    # A=4 in², I=2*2³/12=4/3 in⁴; square Saint-Venant constant J/a⁴=0.14057701495.
    close(v(r,"area"),4)
    close(v(r,"Ix"),4/3)
    close(v(r,"torsion_constant"),.14057701495*16,rel=2e-9)
    assert not r.data["principal_angle_unique"]


@pytest.mark.parametrize("seed",range(10))
def test_random_rectangle_polygon_translation_rotation(seed):
    rng = np.random.default_rng(seed)
    b,h = rng.uniform(.1,2,2)
    angle = rng.uniform(-math.pi,math.pi)
    c,s = math.cos(angle),math.sin(angle)
    base = np.array([[-b/2,-h/2],[b/2,-h/2],[b/2,h/2],[-b/2,h/2]])
    shift = np.array([1000.,-2000.])
    p = base@np.array([[c,s],[-s,c]])+shift
    result = ok(section,shape="polygon",polygon=polygon_text(p if seed%2 else p[::-1]))
    # Rotation of central integrals: Ix'=Ix*c²+Iy*s², Ixy'=(Iy-Ix)*s*c.
    ix,iy = b*h**3/12,h*b**3/12
    close(v(result,"Ix"),ix*c*c+iy*s*s,rel=2e-8)
    close(v(result,"Ixy"),(iy-ix)*s*c,rel=2e-7,abs=2e-9)
    close(v(result,"principal_I1"),max(ix,iy),rel=2e-8)
    close(v(result,"centroid_x"),1000,abs=1e-8)
    close(v(result,"centroid_y"),-2000,abs=1e-8)


def test_polygon_hole_winding_and_precision():
    outer = np.array([[0,0],[4,0],[4,6],[0,6.]])
    hole = np.array([[1,1],[3,1],[3,5],[1,5.]])
    r = ok(section,shape="polygon",polygon=polygon_text(outer+1000),holes=polygon_text((hole+1000)[::-1]),at_height="1003 m")
    # A=4*6-2*4=16; Ix=(4*6³-2*4³)/12; Q=4*3*1.5-2*2*1=14.
    close(v(r,"area"),16)
    close(v(r,"Ix"),(4*6**3-2*4**3)/12)
    close(v(r,"first_moment_Q"),14)
    assert r.data["torsion_constant"] is None


def test_regular_polygon_converges_to_circle():
    angles = np.linspace(0,2*math.pi,200,endpoint=False)
    r = ok(section,shape="polygon",polygon=polygon_text(np.column_stack((np.cos(angles),np.sin(angles)))))
    # Exact circle integral A=pi, Ix=pi/4; inscribed 200-gon error is O((2pi/200)^2).
    close(v(r,"area"),math.pi,rel=2e-4)
    close(v(r,"Ix"),math.pi/4,rel=4e-4)


@pytest.mark.parametrize("polygon,holes",[
    ("0 m,0 m;1 m,1 m;0 m,1 m;1 m,0 m",""),
    ("0 m,0 m;1 m,0 m;2 m,0 m",""),
    ("0 m,0 m;4 m,0 m;4 m,4 m;0 m,4 m","5 m,5 m;6 m,5 m;5 m,6 m"),
    ("0 m,0 m;4 m,0 m;4 m,4 m;0 m,4 m","0 m,0 m;1 m,0 m;1 m,1 m"),
    ("0 m,0 m;4 m,0 m;4 m,4 m;0 m,4 m","1 m,1 m;3 m,1 m;3 m,3 m;1 m,3 m|2 m,2 m;3.5 m,2 m;2 m,3.5 m"),
])
def test_invalid_polygons(polygon,holes):
    assert not call(section,shape="polygon",polygon=polygon,holes=holes).ok
