"""Hand arithmetic, failure-line roots, conversions and limits for shafts."""

import math

import pytest

from eng_toolkit.domains.machine.shaft import shaft
from tests.machine_testing import call, close, ok, v


def test_static_solid_and_twist():
    r = ok(shaft, outer_diameter="20 mm", bending_moment="100 N*m", torque="50 N*m",
           axial_force="1000 N", yield_strength="300 MPa", length="1 m", shear_modulus="80 GPa")
    # sigma_b=32*100000/(pi*20^3)=400/pi; tau=100/pi; axial=10/pi MPa.
    # sigma=410/pi; vm=sqrt(410^2+3*100^2)/pi; J=pi*20^4/32=5000pi mm4.
    vm = math.sqrt(410**2+3*100**2)/math.pi
    close(v(r, "bending_stress"), 400/math.pi)
    close(v(r, "torsional_shear"), 100/math.pi)
    close(v(r, "von_mises"), vm)
    close(v(r, "static_safety_factor"), 300/vm)
    close(v(r, "maximum_shear"), math.sqrt(205**2+100**2)/math.pi)
    # theta=50000*1000/(80000*5000*pi)=0.125/pi rad.
    close(v(r, "twist"), .125/math.pi)
    # T*theta/2 equals integral(tau(r)^2/(2G) dV). Simpson is exact for the r^3 integrand.
    j = v(r, "polar_inertia")
    def energy_density_ring(radius):
        return (50000*radius/j)**2/(2*80000)*2*math.pi*radius*1000
    integrated_energy = 10/6*(energy_density_ring(0)+4*energy_density_ring(5)+energy_density_ring(10))
    close(integrated_energy, 50000*v(r, "twist")/2)


def test_hollow_signed_fibre_and_conversion():
    metric = ok(shaft, outer_diameter="25.4 mm", inner_diameter="12.7 mm", torque="112.9848290276167 N*m")
    inch = ok(shaft, outer_diameter="1 inch", inner_diameter="0.5 inch", torque="1000 lbf*inch", unit_system="imperial")
    # tau=16*1000/[pi*(1-.5^4)]=5432.488724... psi.
    close(v(inch, "torsional_shear", "psi"), 16000/(math.pi*(1-.5**4)))
    close(v(inch, "von_mises", "MPa"), v(metric, "von_mises"))
    signed = ok(shaft, outer_diameter="20 mm", bending_moment="-100 N*m", axial_force="1000 N")
    # Chosen fibre -400/pi+10/pi; opposite fibre +410/pi controls.
    close(v(signed, "normal_stress"), -390/math.pi)
    close(v(signed, "worst_fibre_von_mises"), 410/math.pi)


def root_on_failure_line(fn):
    # Independent bracket/bisection along the proportional-loading ray, tolerance <1e-12.
    lo, hi = 0.0, 1.0
    while fn(hi) < 1:
        hi *= 2
    for _ in range(80):
        mid = (lo+hi)/2
        if fn(mid) < 1:
            lo = mid
        else:
            hi = mid
    return (lo+hi)/2


@pytest.mark.parametrize("ma,mm,ta,tm", [(100, 0, 0, 50), (0, 100, 0, 0), (100, 0, 0, 0), (20, -30, 40, -50)])
def test_fatigue_closed_forms_against_numeric_failure_line(ma, mm, ta, tm):
    r = ok(shaft, outer_diameter="20 mm", alternating_bending=f"{ma} N*m", mean_bending=f"{mm} N*m",
           alternating_torque=f"{ta} N*m", mean_torque=f"{tm} N*m", ultimate_strength="600 MPa",
           yield_strength="400 MPa", ka=1, kb=1, kc=1, kd=1, ke=1)
    # With d=20: bending per Nm=4/pi MPa, torsion per Nm=2/pi MPa. Se=.5*600=300 MPa.
    sa = math.hypot(4*ma/math.pi, math.sqrt(3)*2*ta/math.pi)
    sm = math.hypot(4*mm/math.pi, math.sqrt(3)*2*tm/math.pi)
    close(v(r, "endurance_limit"), 300)
    close(v(r, "alternating_equivalent"), sa)
    close(v(r, "mean_equivalent"), sm)
    for name, failure in {
        "goodman": lambda n: n*sa/300+n*sm/600,
        "soderberg": lambda n: n*sa/300+n*sm/400,
        "gerber": lambda n: n*sa/300+(n*sm/600)**2,
        "asme_elliptic": lambda n: (n*sa/300)**2+(n*sm/400)**2,
    }.items():
        close(v(r, name+"_safety_factor"), root_on_failure_line(failure))


@pytest.mark.parametrize("finish,a,b", [("ground",1.58,-.085),("machined",4.51,-.265),("hot_rolled",57.7,-.718),("as_forged",272,-.995)])
@pytest.mark.parametrize("d", [20, 100])
def test_empirical_marin_factors(finish, a, b, d):
    r = ok(shaft, outer_diameter=f"{d} mm", alternating_bending="1 kN*m", ultimate_strength="600 MPa",
           surface_finish=finish, reliability=99, kd=.9)
    # Se=300*(a*600^b)*kb*1*.9*.814; kb=1.24*d^-.107 or 1.51*d^-.157.
    kb = 1.24*d**-.107 if d <= 51 else 1.51*d**-.157
    close(v(r, "endurance_limit"), 300*min(1,a*600**b)*kb*.9*.814)


def test_critical_speed_and_endurance_cap():
    r = ok(shaft, outer_diameter="20 mm", torque="1 N*m", static_deflections="1 mm; 3 mm")
    # Dunkerley 1/omega^2=(1+3)/g; omega=sqrt(9806.65/4) rad/s.
    close(v(r, "critical_speed"), math.sqrt(9806.65/4))
    one = ok(shaft, outer_diameter="20 mm", torque="1 N*m", static_deflections="4 mm")
    close(v(one, "critical_speed"), v(r, "critical_speed"))
    cap = ok(shaft, outer_diameter="20 mm", alternating_bending="100 N*m", ultimate_strength="1600 MPa", ka=1, kb=1)
    close(v(cap, "endurance_limit"), 700)  # min(.5*1600,700)=700 MPa.
    assert any("outside typical" in w for w in cap.warnings)


def test_size_and_temperature_factors_can_exceed_one():
    r = ok(shaft, outer_diameter="20 mm", alternating_bending="100 N*m", ultimate_strength="600 MPa",
           ka=1, kb=1.1, kc=1, kd=1.02, ke=1)
    # Small-size and temperature corrections can exceed one: Se=300*1.1*1.02=336.6 MPa.
    close(v(r, "endurance_limit"), 336.6)


@pytest.mark.parametrize("changes", [dict(inner_diameter="21 mm"), dict(length="100 mm"),
    dict(ultimate_strength="600 MPa"), dict(alternating_bending="1 N*m"),
    dict(ultimate_strength="600 MPa", alternating_bending="1 N*m", surface_finish="machined", ka=.8),
    dict(ultimate_strength="600 MPa", alternating_bending="1 N*m", reliability=98)])
def test_shaft_refusals(changes):
    r = call(shaft, **{**dict(outer_diameter="20 mm", torque="10 N*m"), **changes})
    assert not r.ok
