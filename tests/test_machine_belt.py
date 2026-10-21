"""Capstan limiting transmission, power balance and geometry."""

import math

import pytest

from eng_toolkit.domains.machine.belt import belt
from tests.machine_testing import call, close, ok, v

BASE=dict(small_diameter="100 mm",large_diameter="100 mm",centre_distance="500 mm",velocity="10 m/s",friction=math.log(3)/math.pi)


def test_flat_inverse_and_forward_with_centrifugal_tension():
    r=ok(belt,**BASE,power="2 kW",mass_per_length="0.5 kg/m",maximum_tension="1350 N")
    # Equal pulleys: wrap=pi, ratio=exp(log3)=3. P/v=200 N=T1-T2; T2=100,T1=300.
    # Tc=.5*10^2=50 N; initial=(300+100)/2+50=250 N; effective shaft load=400 N.
    close(v(r,"wrap_angle"),math.pi)
    close(v(r,"tension_ratio"),3)
    close(v(r,"tight_tension"),300)
    close(v(r,"slack_tension"),100)
    close(v(r,"centrifugal_tension"),50)
    close(v(r,"initial_tension"),250)
    close(v(r,"total_tight_tension"),350)
    close(v(r,"shaft_load"),400)
    close(v(r,"belt_length"),1000+100*math.pi)
    # Optimum v=sqrt(1350/(3*.5))=30 m/s, Pmax=(2/3)*1350*(2/3)*30=18000 W.
    close(v(r,"maximum_power_speed"),30)
    close(v(r,"maximum_power"),18000)
    fwd=ok(belt,**BASE,tight_tension="300 N",mass_per_length="0.5 kg/m")
    close(v(fwd,"transmitted_power"),2000)
    close(v(fwd,"tight_tension")/v(fwd,"slack_tension"),3)
    close((v(fwd,"tight_tension")-v(fwd,"slack_tension"))*v(fwd,"belt_velocity"),2000)
    # Max-power derivative independently by symmetric perturbation with total tension fixed.
    def p(speed):
        return (1350-.5*speed**2)*(1-1/3)*speed
    assert p(30)>p(29.99) and p(30)>p(30.01)


def test_v_belt_wedge_and_crossed_exact_geometry():
    r=ok(belt,**{**BASE,"friction":math.log(3)/(2*math.pi)},belt_type="v",groove_angle="60 deg",tight_tension="300 N")
    # sin(beta/2)=sin30=.5, so effective mu doubles and ratio remains 3.
    close(v(r,"tension_ratio"),3)
    cross=ok(belt,**BASE,configuration="crossed",tight_tension="300 N")
    # k=(D+d)/2=100; alpha=asin(.2); crossed wrap=pi+2asin(.2).
    close(v(cross,"wrap_angle"),math.pi+2*math.asin(.2))
    close(v(cross,"belt_length"),1000+100*math.pi+20)
    close(v(cross,"exact_belt_length"),2*math.sqrt(500**2-100**2)+100*(math.pi+2*math.asin(.2)))
    # Norm of the two effective tension vectors, angle between them=2alpha.
    t1,t2=300,v(cross,"slack_tension")
    close(v(cross,"shaft_load"),math.sqrt(t1*t1+t2*t2+2*t1*t2*math.cos(2*math.asin(.2))))


def test_inch_rpm_hp_conversion_and_wrap_warning():
    r=ok(belt,small_diameter="4 inch",large_diameter="4 inch",centre_distance="20 inch",speed="600 rpm",power="1 hp",unit_system="imperial")
    # v=pi*(4*.0254)*(600/60)=1.016pi m/s; L=40+4pi inch.
    close(v(r,"belt_velocity","m/s"),1.016*math.pi)
    close(v(r,"belt_length"),40+4*math.pi)
    close(v(r,"transmitted_power"),1)
    short=ok(belt,small_diameter="10 mm",large_diameter="200 mm",centre_distance="110 mm")
    assert any("below 120" in w for w in short.warnings)
    assert any("Centre distance" in w for w in short.warnings)


@pytest.mark.parametrize("changes",[dict(centre_distance="90 mm"),dict(large_diameter="90 mm"),dict(speed="600 rpm"),dict(power="1 W",tight_tension="100 N"),dict(velocity="",power="100 W"),dict(maximum_tension="1 kN"),dict(belt_type="v",configuration="crossed"),dict(friction=100)])
def test_belt_refusals(changes):
    assert not call(belt,**{**BASE,**changes}).ok
