"""Regression cases from the independent formula review: each failed before the fix."""

import math
import time

import pytest

from eng_toolkit.domains.machine.belt import belt
from eng_toolkit.domains.machine.gear import gear
from eng_toolkit.domains.machine.pressure_vessel import pressure_vessel
from eng_toolkit.domains.machine.shaft import shaft
from eng_toolkit.domains.machine.weld import weld
from tests.machine_testing import call, close, ok, v


@pytest.mark.parametrize("speed", ["3600 deg/s", "10 turn/s", "600 rpm", "62.83185307179586 rad/s"])
def test_angular_speed_units_agree(speed):
    # 3600 deg/s = 10 turn/s = 600 rpm = 20 pi rad/s; pitch radius 20 mm gives v = 20 pi * 0.02 m/s.
    r = ok(gear, teeth_1=20, teeth_2=20, module="2 mm", face_width="20 mm", torque="1 N*m", speed=speed)
    close(v(r, "pitch_line_velocity"), 20*math.pi*0.02, rel=1e-9)
    b = ok(belt, small_diameter="100 mm", large_diameter="100 mm", centre_distance="500 mm", tight_tension="300 N", speed=speed)
    close(v(b, "belt_velocity"), 20*math.pi*0.05, rel=1e-9)


def test_hertz_frequency_still_means_cycles():
    # 10 Hz = 10 revolutions per second = 20 pi rad/s.
    r = ok(gear, teeth_1=20, teeth_2=20, module="2 mm", face_width="20 mm", torque="1 N*m", speed="10 Hz")
    close(v(r, "pitch_line_velocity"), 20*math.pi*0.02, rel=1e-9)


@pytest.mark.parametrize("pct,ke", [(90, .897), (95, .868), (99, .814), (99.9, .753), (99.99, .702)])
def test_reliability_factors_match_published_table(pct, ke):
    # Se = 0.5*600 * 1 * 1 * ke with ka = kb = 1.
    r = ok(shaft, outer_diameter="20 mm", alternating_bending="100 N*m", ultimate_strength="600 MPa", ka=1, kb=1, reliability=pct)
    close(v(r, "endurance_limit"), 300*ke)


def test_torsion_fatigue_uses_one_basis():
    # tau_a = 16*100e3/(pi*20^3) = 63.661977 MPa; equivalent sigma = sqrt(3) tau_a; Goodman n = Se/sigma = 300/(sqrt(3) tau_a).
    r = ok(shaft, outer_diameter="20 mm", alternating_torque="100 N*m", ultimate_strength="600 MPa", ka=1, kb=1, load_type="torsion")
    close(v(r, "goodman_safety_factor"), 300/(math.sqrt(3)*16*100e3/(math.pi*20**3)), rel=1e-9)


def test_equal_pressure_sizing_is_refused_when_allowable_is_below_p():
    for shape in ("cylinder", "sphere"):
        r = call(pressure_vessel, geometry=shape, inner_radius="100 mm", internal_pressure="10 MPa", external_pressure="10 MPa", allowable_stress="5 MPa")
        assert not r.ok and r.error.details["param"] == "/allowable_stress"
    r = ok(pressure_vessel, geometry="cylinder", inner_radius="100 mm", internal_pressure="10 MPa", external_pressure="10 MPa", allowable_stress="15 MPa")
    close(v(r, "required_thickness_lame"), 0)


def test_thin_model_refused_when_external_pressure_dominates():
    r = call(pressure_vessel, geometry="cylinder", inner_radius="100 mm", thickness="1 mm", internal_pressure="100 MPa",
             external_pressure="100 MPa", ends="open", model="thin", yield_strength="50 MPa")
    assert not r.ok and r.error.details["param"] == "/model"
    thick = ok(pressure_vessel, geometry="cylinder", inner_radius="100 mm", thickness="1 mm", internal_pressure="100 MPa",
               external_pressure="100 MPa", ends="open", model="thick", yield_strength="50 MPa")
    close(v(thick, "inner_von_mises"), 100)  # (sr, st, sz) = (-100, -100, 0)
    close(v(thick, "yield_safety_factor"), .5)


def test_weld_parser_is_linear_time():
    text = "(" + " "*3000 + "," + " "*3000 + "!"
    t0 = time.perf_counter()
    r = call(weld, segments_text=text, leg_size="5 mm", force_y="1000 N")
    assert not r.ok and time.perf_counter()-t0 < 1.0


def test_weld_thin_pattern_keeps_weak_axis_moment():
    # Two lines 2e-8 mm apart: Ix = 2*L*(1e-8)^2 = 2e-14, Iy = 2*L^3/12 = 166666.67 mm3, throat 0.707 mm.
    # Load Fz=100 N at (50, 4e-8): Mx = 4e-6 N mm about the weak axis, My = -50*100 = -5000 N mm about the strong one.
    segs = "(-50 mm,-0.00000001 mm)-(50 mm,-0.00000001 mm);(-50 mm,0.00000001 mm)-(50 mm,0.00000001 mm)"
    r = ok(weld, segments_text=segs, leg_size="1 mm", force_z="100 N", load_x="50 mm", load_y="0.00000004 mm", allowable_shear="100 MPa")
    throat = .707
    area = throat*200
    # sigma_z = Fz/A + My*x/(throat*Iy) + Mx*y/(throat*Ix) at the end point x=50, y=1e-8; the weak axis term is 4e-6*1e-8/(0.707*2e-14).
    sz = 100/area + 5000*50/(throat*166666.6666667) + 4e-6*1e-8/(throat*2e-14)
    close(v(r, "resultant"), sz, rel=1e-5)
    close(sz, 5.657708628, rel=1e-6)


def test_weld_collinear_pattern_still_refuses_unsupported_moment():
    r = call(weld, segments_text="(0 mm,0 mm)-(100 mm,0 mm)", leg_size="5 mm", force_z="100 N", load_x="50 mm", load_y="30 mm")
    assert not r.ok
    r = ok(weld, segments_text="(0 mm,0 mm)-(100 mm,0 mm)", leg_size="5 mm", force_z="100 N", load_x="20 mm", load_y="0 mm")
    assert r.ok
