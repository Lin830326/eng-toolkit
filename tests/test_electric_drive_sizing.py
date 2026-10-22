"""Hand-calculated rigid-body motion and profile integration checks."""

import math
import time

import pytest

from eng_toolkit.domains.electric.drive_sizing import drive_sizing
from tests.electric_testing import call, close, ok, table, v


BASE = dict(load_inertia="2 kg*m^2", rotor_inertia="0.5 kg*m^2",
            profile="1 s,0 rad/s,4 rad/s,0 N*m;1 s,4 rad/s,0 rad/s,0 N*m")


def test_triangle_inertia_power_and_regeneration():
    r = ok(drive_sizing, **BASE)
    # alpha=+/-4; load torque=+/-8, rotor torque=+/-2, total=+/-10 N*m.
    # P ramps 0..40 then -40..0, hence Prms=40/sqrt(3); Eregen=20 J.
    for name, expected in {"peak_torque_without_rotor": 8, "rms_torque_without_rotor": 8,
                           "peak_torque_with_rotor": 10, "rms_torque_with_rotor": 10,
                           "peak_power_with_rotor": 40, "rms_power_with_rotor": 40/math.sqrt(3),
                           "regenerative_energy_with_rotor": 20, "regenerative_energy_without_rotor": 16,
                           "net_shaft_energy_with_rotor": 0, "net_travel": 4,
                           "ideal_inertia_matching_ratio": 2}.items():
        close(v(r, name), expected)
    assert len(table(r)) == 2


def test_generated_triangle_matches_explicit_profile():
    r = ok(drive_sizing, load_inertia="2 kg*m^2", rotor_inertia="0.5 kg*m^2", distance="4 rad", move_time="2 s")
    close(v(r, "rms_torque_with_rotor"), 10)
    close(v(r, "peak_motor_speed"), 4)
    close(v(r, "net_travel"), 4)


def test_trapezoid_and_thermal_duty():
    r = ok(drive_sizing, load_inertia="2 kg*m^2", distance="6 rad", move_time="4 s", acceleration_time="1 s",
           duty="1 s,6 N*m;3 s,2 N*m", rated_torque="3 N*m")
    # Top speed=6/(4-1)=2, ramps +/-2: torque +/-4 for 2 of the 4 seconds.
    close(v(r, "peak_motor_speed"), 2)
    close(v(r, "rms_torque_with_rotor"), math.sqrt(8))
    close(v(r, "duty_rms_torque"), math.sqrt((36+3*4)/4))
    assert r.data["profile_thermal_check"].startswith("pass")
    assert r.data["duty_thermal_check"].startswith("fail")


def test_gear_efficiency_motoring_and_regeneration():
    r = ok(drive_sizing, **{**BASE, "rotor_inertia": "0 kg*m^2"}, gear_ratio=2, efficiency=.8)
    # Accel motor torque=8/(2*.8)=5; braking=-8*.8/2=-3.2.
    close(v(r, "peak_torque_with_rotor"), 5)
    close(v(r, "rms_torque_with_rotor"), math.sqrt((25+10.24)/2))
    close(v(r, "regenerative_energy_with_rotor"), 12.8)
    close(v(r, "reflected_load_inertia"), .5)


@pytest.mark.parametrize("model,dimension", [("belt", {"radius": "0.1 m"}), ("rack", {"radius": "0.1 m"}),
                                             ("screw", {"lead": f"{.2*math.pi} m"})])
def test_linear_models_and_gravity(model, dimension):
    r = ok(drive_sizing, model=model, load_mass="10 kg", profile="2 s,1 m/s,1 m/s,0 N",
           gravity_angle="90 deg", **dimension)
    # v/r=10 rad/s; Fg=98.0665 N; T=9.80665 N*m; P=98.0665 W.
    close(v(r, "peak_motor_speed"), 10)
    close(v(r, "rms_torque_with_rotor"), 9.80665)
    close(v(r, "rms_power_with_rotor"), 98.0665)
    close(v(r, "reflected_load_inertia"), .1)


def test_reversal_viscous_and_coulomb_exact_integral():
    r = ok(drive_sizing, load_inertia="1 kg*m^2", profile="2 s,-1 rad/s,1 rad/s,0 N*m",
           coulomb_friction="2 N*m", viscous_friction="3 N*m*s/rad")
    # alpha=1. On first second T=-4+3t, then T=3+3u. Integral T²=(16+4+1)/3+(9+18+36)/3=28.
    close(v(r, "rms_torque_with_rotor"), math.sqrt(14))
    close(v(r, "peak_torque_with_rotor"), 6)
    assert len(table(r)) == 2


def test_available_acceleration_time():
    r = ok(drive_sizing, **BASE, available_torque="20 N*m")
    # Jtotal=2.5; alpha=20/2.5=8; zero-to-4 time=.5 s.
    close(v(r, "conservative_available_acceleration"), 8)
    close(v(r, "conservative_zero_to_peak_time"), .5)


def test_quadratic_power_interior_peak_and_exact_regeneration():
    r = ok(drive_sizing, load_inertia="1 kg*m^2", profile="1 s,0 rad/s,2 rad/s,-4 N*m",
           viscous_friction="1 N*m*s/rad")
    # T=-2+2t; w=2t; P=-4t+4t². Peak absolute power=1 W at t=.5.
    # Integral P²=16*(1/3-1/2+1/5)=8/15; recovered energy=2/3 J.
    close(v(r, "peak_power_with_rotor"), 1)
    close(v(r, "rms_power_with_rotor"), math.sqrt(8/15))
    close(v(r, "regenerative_energy_with_rotor"), 2/3)


def test_candidate_inertia_and_limits_are_recomputed():
    r = ok(drive_sizing, **BASE,
           candidates="small,0.1 kg*m^2,9 N*m,10 N*m,5 rad/s,6 rad/s;large,1 kg*m^2,9 N*m,11 N*m,3 rad/s,6 rad/s")
    candidates = r.data["candidates"]
    # Candidate rotor .1 gives (2+.1)*4=8.4 Nm; rotor 1 gives 12 Nm.
    close(candidates[0]["rms_torque_N_m"], 8.4)
    close(candidates[1]["rms_torque_N_m"], 12)
    assert candidates[0]["status"].startswith("pass")
    assert candidates[1]["status"].startswith("fail")
    assert len(table(r, 1)) == 2
    segment_rows = table(r, 2)
    assert len(segment_rows) == 4
    close(float(segment_rows[0]["segment_peak_torque_margin_N_m"]), 1.6)


def test_direct_rotary_drive():
    r = ok(drive_sizing, **BASE, model="direct")
    close(v(r, "peak_torque_with_rotor"), 10)


@pytest.mark.parametrize("change", [
    {"profile": ""}, {"load_inertia": "0 kg*m^2"}, {"load_inertia": "1e308 kg*m^2"},
    {"load_inertia": "1e-999 kg*m^2"}, {"gear_ratio": 0}, {"efficiency": 0}, {"efficiency": 1.1},
    {"distance": "1 rad"}, {"profile": "0 s,0 rad/s,1 rad/s,0 N*m"},
    {"profile": "1 s,0 rad/s,1 rad/s,0 N*m;1 s,2 rad/s,0 rad/s,0 N*m"},
    {"profile": "1 s,0 rad/s,1 rad/s"}, {"profile": "1 s,0 rad/s,1 rad/s,0 N*m;"},
    {"profile": ";".join(["1 s,0 rad/s,0 rad/s,0 N*m"]*101)},
    {"model": "direct", "gear_ratio": 2}, {"model": "belt", "load_mass": "10 kg", "load_inertia": "", "radius": "0 m"},
    {"gravity_angle": "90 deg"}, {"candidates": "bad,0 kg*m^2,2 N*m,1 N*m,5 rad/s,6 rad/s"},
    {"candidates": "=HYPERLINK,0 kg*m^2,1 N*m,2 N*m,5 rad/s,6 rad/s"},
])
def test_refusals(change):
    assert not call(drive_sizing, **{**BASE, **change}).ok


def test_generated_conflicting_or_invalid_motion():
    assert not call(drive_sizing, load_inertia="1 kg*m^2", distance="1 rad", move_time="2 s", acceleration_time="2 s").ok
    assert not call(drive_sizing, load_inertia="1 kg*m^2", distance="0 rad", move_time="2 s").ok


def test_hostile_profile_is_bounded():
    started = time.perf_counter()
    assert not call(drive_sizing, **{**BASE, "profile": "("*6000}).ok
    assert time.perf_counter()-started < 1
