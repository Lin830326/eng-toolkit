"""Hand-calculated rigid-body motion and profile integration checks."""

import math
import time

import pytest

from eng_toolkit.domains.electric.drive_sizing import drive_sizing
from tests.electric_testing import call, close, ok, table, v
from timing import budget


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
    assert time.perf_counter()-started < budget(1)


def test_peak_speed_above_rated_but_below_maximum_passes_with_a_warning():
    # Coordinator item: 3750 rpm (392.70 rad/s) on a 3000 rpm (314.16 rad/s) rated / 5000 rpm (523.60 rad/s) maximum candidate.
    # Margins: rated 314.159 - 392.699 = -78.54 rad/s, maximum 523.599 - 392.699 = +130.90 rad/s.  A servo may run above its rated speed.
    r = ok(drive_sizing, load_inertia="0.002 kg*m^2", profile="1 s,0 rpm,3750 rpm,0.1 N*m;1 s,3750 rpm,0 rpm,0 N*m",
           candidates="M1,0.00005 kg*m^2,1 N*m,3 N*m,3000 rpm,5000 rpm")
    (row,) = r.data["candidates"]
    close(row["rated_speed_margin_rad_s"], 3000*math.pi/30 - 3750*math.pi/30)
    close(row["maximum_speed_margin_rad_s"], 5000*math.pi/30 - 3750*math.pi/30)
    assert row["rated_speed_margin_rad_s"] < -78 and row["maximum_speed_margin_rad_s"] > 130
    assert row["status"].startswith("pass") and "above rated speed (1.25 x)" in row["status"] and "torque-speed curve" in row["status"]
    assert any("1.25 times its rated speed" in w and "torque-speed curve" in w for w in r.warnings)
    assert "maximum speed" in r.data["candidate_speed_note"]


def test_peak_speed_above_the_maximum_speed_still_fails():
    r = ok(drive_sizing, load_inertia="0.002 kg*m^2", profile="1 s,0 rpm,5500 rpm,0.1 N*m;1 s,5500 rpm,0 rpm,0 N*m",
           candidates="M1,0.00005 kg*m^2,1 N*m,3 N*m,3000 rpm,5000 rpm")
    (row,) = r.data["candidates"]
    assert row["status"].startswith("fail") and row["maximum_speed_margin_rad_s"] < 0
    assert not any("times its rated speed" in w for w in r.warnings)


def test_peak_speed_within_rated_speed_has_the_plain_status_and_no_warning():
    r = ok(drive_sizing, load_inertia="0.002 kg*m^2", profile="1 s,0 rpm,2000 rpm,0.1 N*m;1 s,2000 rpm,0 rpm,0 N*m",
           candidates="M1,0.00005 kg*m^2,1 N*m,3 N*m,3000 rpm,5000 rpm")
    (row,) = r.data["candidates"]
    assert row["status"] == "pass within stated profile and scalar limits"
    assert not any("times its rated speed" in w for w in r.warnings)


def test_cycle_time_adds_a_zero_speed_dwell_and_lowers_the_rms():
    # Triangle move 4 rad in 2 s, J = 2 + 0.5 kg*m^2: torque +/-10 N*m, move RMS 10.  A 4 s cycle adds 2 s of rest at zero torque:
    # RMS = sqrt(10^2 * 2 / 4) = 7.0711 N*m (the move alone overstates it by 41 %).
    move = ok(drive_sizing, load_inertia="2 kg*m^2", rotor_inertia="0.5 kg*m^2", distance="4 rad", move_time="2 s", rated_torque="8 N*m")
    cycle = ok(drive_sizing, load_inertia="2 kg*m^2", rotor_inertia="0.5 kg*m^2", distance="4 rad", move_time="2 s", cycle_time="4 s", rated_torque="8 N*m")
    close(v(move, "rms_torque_with_rotor"), 10)
    close(v(cycle, "rms_torque_with_rotor"), 10/math.sqrt(2))
    close(v(cycle, "peak_torque_with_rotor"), 10)
    close(v(cycle, "profile_duration"), 4)
    assert move.data["profile_thermal_check"].startswith("fail") and cycle.data["profile_thermal_check"].startswith("pass")
    assert any("no dwell" in w and "cycle_time" in w for w in move.warnings)
    assert not any("no dwell" in w for w in cycle.warnings)


def test_dwell_holds_the_stated_load_and_gravity():
    # Dwell of 2 s holding 6 N*m: RMS^2 = (100 * 2 + 36 * 2) / 4 = 68
    r = ok(drive_sizing, load_inertia="2 kg*m^2", rotor_inertia="0.5 kg*m^2", distance="4 rad", move_time="2 s", cycle_time="4 s", dwell_load="6 N*m")
    close(v(r, "rms_torque_with_rotor"), math.sqrt(68))
    g = ok(drive_sizing, model="screw", load_mass="10 kg", lead=f"{2*math.pi} m", gravity_angle="90 deg", distance="1 m", move_time="2 s", cycle_time="4 s")
    assert v(g, "rms_torque_with_rotor") > 0.7*10*9.80665  # gravity is held during the rest


def test_cycle_time_refusals():
    for extra, param in (({"cycle_time": "1 s"}, "/cycle_time"), ({"dwell_load": "1 N*m"}, "/dwell_load")):
        r = call(drive_sizing, load_inertia="2 kg*m^2", distance="4 rad", move_time="2 s", **extra)
        assert r.ok is False and r.error.details["param"] == param, r.error
    r = call(drive_sizing, load_inertia="2 kg*m^2", profile="1 s,0 rad/s,0 rad/s,0 N*m", cycle_time="4 s")
    assert r.ok is False and r.error.details["param"] == "/cycle_time"


# --- strokes_per_cycle: the M4 gantry (82.878 kg equivalent, 1 m in 0.8 s, 0.4 s dwell after each stroke, out and back, period 2.4 s) ---
M4 = dict(model="belt", distance="1 m", move_time="0.8 s", acceleration_time="0.2667 s", load_mass="82.878 kg", radius="0.03183 m",
          gear_ratio=5, efficiency=0.9312, coulomb_friction="15 N", rotor_inertia="0.000133 kg*m^2", rated_torque="2.39 N*m",
          candidates="ECMA-C20807,0.000133 kg*m^2,2.39 N*m,7.16 N*m,3000 rpm,5000 rpm")
_TOP = 1/(0.8-0.2667)
M4_FORWARD = f"0.2667 s,0 m/s,{_TOP} m/s,0 N;0.2666 s,{_TOP} m/s,{_TOP} m/s,0 N;0.2667 s,{_TOP} m/s,0 m/s,0 N;0.4 s,0 m/s,0 m/s,0 N"
M4_BACK = f"0.2667 s,0 m/s,-{_TOP} m/s,0 N;0.2666 s,-{_TOP} m/s,-{_TOP} m/s,0 N;0.2667 s,-{_TOP} m/s,0 m/s,0 N;0.4 s,0 m/s,0 m/s,0 N"
M4_EXPLICIT = {k: x for k, x in M4.items() if k not in ("distance", "move_time", "acceleration_time")}


def _m4_profile_rms(rows):
    return v(ok(drive_sizing, profile=rows, **M4_EXPLICIT), "rms_torque_with_rotor")


def test_strokes_per_cycle_matches_the_explicit_out_and_back_profile():
    # One stroke per 2.4 s reports pass (the original M4 result); the real out-and-back cycle fails the 2.39 N*m rating.
    one = ok(drive_sizing, cycle_time="2.4 s", **M4)
    two = ok(drive_sizing, cycle_time="2.4 s", strokes_per_cycle=2, **M4)
    close(v(two, "rms_torque_with_rotor"), _m4_profile_rms(M4_FORWARD+";"+M4_BACK), rel=1e-3)
    close(v(ok(drive_sizing, cycle_time="1.2 s", **M4), "rms_torque_with_rotor"), _m4_profile_rms(M4_FORWARD), rel=1e-3)
    close(_m4_profile_rms(M4_FORWARD), 2.5935, rel=1e-4)  # the true RMS, 1.09 x the 2.39 N*m rating
    close(v(one, "rms_torque_with_rotor"), 1.8339, rel=1e-4)  # what one stroke per 2.4 s reports: pass with a 23 % margin
    # same period, twice the moving time: the mean square doubles, so the one-stroke shortcut understates RMS by sqrt(2)
    close(v(two, "rms_torque_with_rotor"), math.sqrt(2)*v(one, "rms_torque_with_rotor"))
    close(v(two, "rms_torque_with_rotor"), v(ok(drive_sizing, cycle_time="1.2 s", **M4), "rms_torque_with_rotor"))
    close(v(two, "profile_duration"), 2.4)
    close(v(two, "net_travel"), 0.0)
    assert v(two, "rms_torque_with_rotor") > 2.39 > v(one, "rms_torque_with_rotor")
    assert one.data["candidates"][0]["status"].startswith("pass")
    assert two.data["candidates"][0]["status"].startswith("fail")
    assert two.data["profile_thermal_check"].startswith("fail") and one.data["profile_thermal_check"].startswith("pass")


def test_shortcut_states_the_stroke_assumption_duty_fraction_and_thin_margin():
    one = ok(drive_sizing, cycle_time="2.4 s", **M4)
    assert one.data["cycle"]["strokes_per_cycle"] == 1 and one.data["cycle"]["period_s"] == pytest.approx(2.4)
    close(one.data["cycle"]["active_fraction"], 0.8/2.4)
    close(v(one, "cycle_active_fraction"), 0.8/2.4)
    assert any("1 stroke(s)" in a and "per period of 2.4 s" in a for a in one.data["assumptions"])
    assert any("RMS covers 1 stroke per cycle_time of 2.4 s" in w and "strokes_per_cycle=2" in w for w in one.warnings)
    assert any("margin below 30 %" in w and "stroke assumption" in w and "ECMA-C20807" in w for w in one.warnings)
    two = ok(drive_sizing, cycle_time="2.4 s", strokes_per_cycle=2, **M4)
    close(two.data["cycle"]["active_fraction"], 1.6/2.4)
    assert any("RMS covers 2 strokes per cycle_time of 2.4 s" in w for w in two.warnings)
    assert not any("strokes_per_cycle=2" in w for w in two.warnings)
    # the generated move without a cycle_time also states its assumption
    move = ok(drive_sizing, **M4)
    assert any("RMS covers 1 stroke per move of 0.8 s" in w for w in move.warnings)
    # an explicit profile has no stroke assumption to state
    explicit = ok(drive_sizing, profile=M4_FORWARD, **M4_EXPLICIT)
    assert "cycle" not in explicit.data and not any("stroke" in w for w in explicit.warnings)


def test_strokes_per_cycle_validation_and_odd_counts():
    for bad in (0, 5):
        r = call(drive_sizing, strokes_per_cycle=bad, cycle_time="9 s", **M4)
        assert r.ok is False and r.error.details["param"] == "/strokes_per_cycle" and r.error.family == "input.out_of_range", r.error
    r = call(drive_sizing, strokes_per_cycle=2, cycle_time="1.5 s", **M4)  # 2 x 0.8 s of motion does not fit
    assert r.ok is False and r.error.details["param"] == "/cycle_time"
    r = call(drive_sizing, strokes_per_cycle=2, profile=M4_FORWARD, **M4_EXPLICIT)
    assert r.ok is False and r.error.details["param"] == "/strokes_per_cycle"
    three = ok(drive_sizing, strokes_per_cycle=3, cycle_time="3.6 s", **M4)
    close(v(three, "net_travel"), 1.0)
    close(v(three, "rms_torque_with_rotor"), _m4_profile_rms(M4_FORWARD+";"+M4_BACK+";"+M4_FORWARD), rel=1e-3)
