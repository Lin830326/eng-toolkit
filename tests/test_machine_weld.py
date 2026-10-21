"""Exact line-pattern identities, equilibrium and endpoint extrema."""

import math

import pytest

from eng_toolkit.domains.machine.weld import weld
from tests.machine_testing import call, close, ok, v

TWO = "(0 mm,0 mm)-(0 mm,100 mm); (60 mm,0 mm)-(60 mm,100 mm)"
BOX = TWO+"; (0 mm,0 mm)-(60 mm,0 mm); (0 mm,100 mm)-(60 mm,100 mm)"


def test_two_parallel_lines_and_box_closed_forms():
    r = ok(weld, segments_text=TWO, leg_size="10 mm", force_y="1000 N", load_x="30 mm", load_y="50 mm")
    # Two vertical segments each length d=100, separated b=60. L=200, centroid=(30,50).
    # Ix=2*d^3/12=100^3/6; Iy=2*d*(b/2)^2=180000 mm3.
    # Ju=d*(3b^2+d^2)/6=346666.6667 mm3, Aw=.707*10*200=1414 mm2.
    close(v(r, "polar_unit"), 100*(3*60**2+100**2)/6)
    close(v(r, "ix_unit"), 100**3/6)
    close(v(r, "iy_unit"), 180000)
    close(v(r, "throat_area"), 1414)
    close(v(r, "centroid_x"), 30)
    close(v(r, "centroid_y"), 50)
    close(v(r, "resultant"), 1000/1414)
    box = ok(weld, segments_text=BOX, leg_size="10 mm", force_y="1000 N")
    # Rectangular box Ju=(b+d)^3/6=160^3/6, valid for any aspect ratio.
    close(v(box, "polar_unit"), 160**3/6)


def test_torsional_vector_sum_and_translation():
    r = ok(weld, segments_text=TWO, leg_size="10 mm", force_y="1000 N", load_x="130 mm", load_y="50 mm", allowable_shear="100 MPa")
    # Mz=1000*(130-30)=100000 Nmm; at (60,0) x'=30,y'=-50.
    # Direct sy=1000/1414, torsion sx=Mz*50/(7.07*Ju), sy=Mz*30/(7.07*Ju).
    ju = 100*(3*60**2+100**2)/6
    sx, sy = 100000*50/(7.07*ju), 1000/1414+100000*30/(7.07*ju)
    expected = math.hypot(sx, sy)
    close(v(r, "worst_resultant"), expected)
    close(v(r, "group_safety_factor"), 100/expected)
    shifted = "(500 mm,-200 mm)-(500 mm,-100 mm); (560 mm,-200 mm)-(560 mm,-100 mm)"
    s = ok(weld, segments_text=shifted, leg_size="10 mm", force_y="1000 N", load_x="630 mm", load_y="-150 mm")
    for key in ("polar_unit", "ix_unit", "iy_unit", "ixy_unit", "resultant", "moment_z"):
        close(v(s, key), v(r, key))
    close(v(s, "centroid_x")-v(r, "centroid_x"), 500)
    close(v(s, "centroid_y")-v(r, "centroid_y"), -200)


def test_out_of_plane_bending_and_general_product_inertia():
    r = ok(weld, segments_text=TWO, leg_size="10 mm", force_z="1000 N", load_x="60 mm", load_y="50 mm", point_x="60 mm", point_y="50 mm")
    # Fz direct=1000/1414; My=-30000 Nmm; sigma_b=30000*30/(7.07*180000).
    close(v(r, "normal_stress"), 1000/1414+30000*30/(7.07*180000))
    # For an asymmetric L, verify the computed affine normal traction integrates to Fz,Mx,My.
    lines = "(0 mm,0 mm)-(100 mm,0 mm); (0 mm,0 mm)-(0 mm,60 mm)"
    r = ok(weld, segments_text=lines, leg_size="5 mm", force_z="1000 N", load_x="50 mm", load_y="40 mm")
    # Centroid x=(100*50)/160=31.25, y=(60*30)/160=11.25 mm.
    close(v(r, "centroid_x"), 31.25)
    close(v(r, "centroid_y"), 11.25)
    cx, cy = 31.25, 11.25
    # 2-point Gauss quadrature exactly integrates traction and first moments (quadratic).
    force = mx = my = 0.0
    for x1,y1,x2,y2 in ((0,0,100,0),(0,0,0,60)):
        ll = math.hypot(x2-x1,y2-y1)
        for s in ((1-1/math.sqrt(3))/2,(1+1/math.sqrt(3))/2):
            x,y = x1+s*(x2-x1), y1+s*(y2-y1)
            p = ok(weld, segments_text=lines, leg_size="5 mm", force_z="1000 N", load_x="50 mm", load_y="40 mm", point_x=f"{x} mm", point_y=f"{y} mm")
            df = v(p,"normal_stress")*.707*5*ll/2
            force += df
            mx += (y-cy)*df
            my -= (x-cx)*df
    close(force,1000)
    close(mx,1000*(40-cy))
    close(my,-1000*(50-cx))


def test_inch_lbf_conversion_and_singular_pattern():
    r = ok(weld, segments_text="(0 inch,0 inch)-(2 inch,0 inch)", leg_size="0.25 inch", force_y="100 lbf", load_x="1 inch", unit_system="imperial")
    # Area=.707*.25*2=.3535 in2, direct shear=100/.3535 psi.
    close(v(r,"resultant","psi"),100/.3535)
    supported = ok(weld, segments_text="(0 mm,0 mm)-(100 mm,0 mm)", leg_size="5 mm", force_z="1 kN", load_x="60 mm")
    assert supported.ok  # one line can resist moment about y, but not about its own x axis.
    refused = call(weld, segments_text="(0 mm,0 mm)-(100 mm,0 mm)", leg_size="5 mm", force_z="1 kN", load_y="10 mm")
    assert not refused.ok


@pytest.mark.parametrize("pattern", ["(0,0)-(100,0)", "(0 mm,0 mm)-(0 mm,0 mm)", "__import__('os').system('x')", TWO+"; (0 mm,0 mm)-(0 mm,100 mm)", ";".join([TWO]*500)])
def test_pattern_refusals(pattern):
    assert not call(weld, segments_text=pattern, leg_size="5 mm", force_y="1 kN").ok
