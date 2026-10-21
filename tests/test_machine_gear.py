"""Hand geometry/load numbers and independent Hertz contact construction."""

import math

import pytest

from eng_toolkit.domains.machine.gear import gear
from tests.machine_testing import call, close, ok, v

BASE = dict(teeth_1=20, teeth_2=40, module="2 mm", face_width="20 mm", torque="100 N*m", speed="600 rpm")


def test_geometry_lewis_hertz_and_power():
    r = ok(gear, **BASE, lewis_factor_1=.3, lewis_factor_2=.4,
           youngs_modulus_1="200 GPa", youngs_modulus_2="200 GPa", poisson_1=0, poisson_2=0,
           allowable_bending="250 MPa", allowable_contact="1000 MPa")
    # d1=2*20=40, d2=80, C=60 mm; Wt=2*100000/40=5000 N.
    # Lewis pinion=5000/(20*2*.3)=416.6667 MPa; gear=312.5 MPa.
    close(v(r, "pitch_diameter_1"), 40)
    close(v(r, "centre_distance"), 60)
    close(v(r, "outside_diameter_2"), 84)
    close(v(r, "dedendum"), 2.5)
    close(v(r, "tangential_force"), 5000)
    close(v(r, "bending_stress_1"), 1250/3)
    close(v(r, "bending_stress_2"), 312.5)
    close(v(r, "bending_safety_factor_1"), .6)
    # v=pi*.04*600/60=.4pi m/s; P=5000*.4pi=2000pi W, also T*omega.
    close(v(r, "pitch_line_velocity"), .4*math.pi)
    close(v(r, "transmitted_power"), 2000*math.pi)
    phi = math.pi/9
    close(v(r, "radial_force"), 5000*math.tan(phi))
    # Contact as two cylinders: R'=1/(1/(20sin20)+1/(40sin20)), E'=100000 MPa.
    # Hertz halfwidth b=sqrt(4*Fn*R'/(pi*F*E')); p0=2Fn/(pi*b*F).
    radius = 1/(1/(20*math.sin(phi))+1/(40*math.sin(phi)))
    fn = 5000/math.cos(phi)
    halfwidth = math.sqrt(4*fn*radius/(math.pi*20*100000))
    hertz = 2*fn/(math.pi*halfwidth*20)
    close(v(r, "contact_stress"), hertz)
    close(v(r, "contact_safety_factor"), 1000/hertz)
    # Ro are radii 22,42; Rb are radii 20cos20,40cos20. Base pitch=2pi*cos20.
    expected = (math.sqrt(22**2-(20*math.cos(phi))**2)+math.sqrt(42**2-(40*math.cos(phi))**2)-60*math.sin(phi))/(2*math.pi*math.cos(phi))
    close(v(r, "contact_ratio"), expected)


def test_imperial_pitch_power_and_factor_scaling():
    r = ok(gear, teeth_1=20, teeth_2=40, face_width="1 inch", diametral_pitch="10 / inch", power="1 hp", speed="600 rpm", unit_system="imperial")
    # d=N/Pd=20/10=2 inch; C=(2+4)/2=3 inch. Mechanical hp=745.69987158227 W.
    close(v(r, "pitch_diameter_1"), 2)
    close(v(r, "centre_distance"), 3)
    close(v(r, "transmitted_power"), 1)
    close(v(r, "transmitted_power", "W"), 745.69987158227)
    base = ok(gear, **BASE, youngs_modulus_1="200 GPa", youngs_modulus_2="200 GPa")
    scaled = ok(gear, **BASE, load_factor=4, youngs_modulus_1="200 GPa", youngs_modulus_2="200 GPa")
    # Lewis linear in Wt, Hertz proportional to sqrt(Wt), power independent of design factor.
    close(v(scaled, "bending_stress_1"), 4*v(base, "bending_stress_1"))
    close(v(scaled, "contact_stress"), 2*v(base, "contact_stress"))
    close(v(scaled, "transmitted_power"), v(base, "transmitted_power"))


def test_table_interpolation_and_interference_warnings():
    r = ok(gear, **{**BASE, "teeth_1": 13, "teeth_2": 300})
    # Linear interpolation at 13 between (12,.245) and (14,.277) = .261.
    close(v(r, "lewis_factor_1"), .261)
    close(v(r, "lewis_factor_2"), .472)
    close(v(r, "minimum_teeth_rack"), 2/math.sin(math.pi/9)**2)
    assert r.data["interference_1"] and r.data["undercut_1"]
    assert any("undercut" in w for w in r.warnings)
    assert any("interference" in w for w in r.warnings)


@pytest.mark.parametrize("changes", [dict(diametral_pitch="10 / inch"), dict(module=""),
    dict(torque="", power="100 W", speed=""), dict(pressure_angle="25 deg"),
    dict(youngs_modulus_1="200 GPa"), dict(teeth_1=11), dict(pressure_angle="1 deg")])
def test_gear_refusals(changes):
    assert not call(gear, **{**BASE, **changes}).ok
