"""Hand key stresses, limiting lengths and projected spline bearing capacity."""

import pytest

from eng_toolkit.domains.machine.key import key
from tests.machine_testing import call, close, ok, v

BASE=dict(joint_type="key",length="50 mm",shaft_diameter="40 mm",width="10 mm",height="8 mm",torque="100 N*m")


def test_parallel_key_stress_and_minimum_length():
    r=ok(key,**BASE,shear_strength="50 MPa",bearing_strength="100 MPa")
    # tau=2*100000/(40*10*50)=10 MPa; bearing=4*100000/(40*8*50)=25 MPa.
    # Safety=50/10=5 and 100/25=4; minimum lengths 50/5=10 and 50/4=12.5 mm.
    close(v(r,"shear_stress"),10)
    close(v(r,"bearing_stress"),25)
    close(v(r,"shear_safety_factor"),5)
    close(v(r,"bearing_safety_factor"),4)
    close(v(r,"minimum_length_shear"),10)
    close(v(r,"minimum_length_bearing"),12.5)
    shear_limit=ok(key,**{**BASE,"length":"10 mm"},shear_strength="50 MPa")
    close(v(shear_limit,"shear_safety_factor"),1)
    bearing_limit=ok(key,**{**BASE,"length":"12.5 mm"},bearing_strength="100 MPa")
    close(v(bearing_limit,"bearing_safety_factor"),1)


def test_yield_default_and_imperial_units():
    r=ok(key,**BASE,yield_strength="100 MPa")
    # Sshear=.577*100=57.7 MPa; safety=57.7/10=5.77; bearing safety=100/25=4.
    close(v(r,"shear_safety_factor"),5.77)
    close(v(r,"bearing_safety_factor"),4)
    r=ok(key,joint_type="key",length="2 inch",shaft_diameter="2 inch",width="0.5 inch",height="0.5 inch",torque="1000 lbf*inch",shear_strength="1000 psi",unit_system="imperial")
    # tau=2*1000/(2*.5*2)=1000 psi; bearing twice this (b=h).
    close(v(r,"shear_stress","psi"),1000)
    close(v(r,"bearing_stress","psi"),2000)
    close(v(r,"shear_safety_factor"),1)


@pytest.mark.parametrize("kind",["straight_spline","involute_spline"])
def test_spline_capacity_and_torque_balance(kind):
    r=ok(key,joint_type=kind,length="20 mm",tooth_count=10,mean_radius="25 mm",tooth_height="3 mm",allowable_pressure="100 MPa",torque="750 N*m")
    # Capacity=10*100*(20*3)*25*.75=1125000 Nmm=1125 Nm; pressure=100*(750/1125).
    close(v(r,"torque_capacity","N*m"),1125)
    close(v(r,"effective_bearing_area"),450)
    close(v(r,"bearing_pressure"),200/3)
    close(v(r,"safety_factor"),1.5)
    # Actual contact force=pressure*effective area, multiplied by 25 mm gives input torque.
    close(v(r,"bearing_pressure")*v(r,"effective_bearing_area")*25,750000)


def test_key_validity_warning():
    r=ok(key,**{**BASE,"length":"100 mm"})
    assert any("L/d" in w for w in r.warnings)


@pytest.mark.parametrize("changes",[dict(torque=""),dict(width="40 mm"),dict(mean_radius="20 mm"),dict(load_sharing=1),dict(joint_type="involute_spline")])
def test_key_refusals(changes):
    assert not call(key,**{**BASE,**changes}).ok
