"""thermal_transient lumped mode with a heat input and the T(t) table, checked against the closed form."""

import csv
import math
from pathlib import Path

import pytest

from eng_toolkit.domains.thermal.transient import transient
from tests.thermal_testing import fails, ok, v

# 200 g aluminium heatsink: C = rho V cp = 2700 * 7.4074e-5 * 900 = 180 J/K ; h As = 4 W/K -> R = 0.25 K/W, tau = 45 s
SINK = dict(
    model="lumped", initial_temperature="45 degC", ambient_temperature="45 degC",
    conductivity="201 W/(m*K)", density="2700 kg/m^3", specific_heat="900 J/(kg*K)",
    h="10 W/(m^2*K)", volume="7.407407407407407e-5 m^3", surface_area="0.4 m^2",
)
C_J_PER_K = 2700.0 * 7.407407407407407e-5 * 900.0
R = 1.0 / (10.0 * 0.4)
TAU = C_J_PER_K * R


def _rows(result):
    art = [a for a in result.artifacts if a.path.endswith("temperature_vs_time.csv")]
    assert len(art) == 1 and art[0].format == "csv"
    with open(Path(art[0].path), newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))


def test_warm_up_table_matches_the_closed_form():
    r = ok(transient, **SINK, heat_input="8 W", times="0 s; 30 s; 90 s; 180 s; 600 s")
    assert math.isclose(v(r, "time_constant"), TAU, rel_tol=1e-12)
    assert math.isclose(TAU, 45.0, rel_tol=1e-9)
    assert math.isclose(v(r, "final_temperature"), 45.0 + 8.0 * R, rel_tol=1e-12)  # 47 degC
    rows = _rows(r)
    assert rows[0] == ["time_s", "temperature_degC"]
    assert [float(x[0]) for x in rows[1:]] == [0.0, 30.0, 90.0, 180.0, 600.0]
    for t, temp in ((float(a), float(b)) for a, b in rows[1:]):
        assert math.isclose(temp, 45.0 + 8.0 * R * (1.0 - math.exp(-t / TAU)), rel_tol=1e-12, abs_tol=1e-12)
    assert math.isclose(v(r, "temperature_at_t_end"), float(rows[-1][1]), rel_tol=1e-12)
    assert math.isclose(v(r, "thermal_resistance"), R, rel_tol=1e-12)
    assert math.isclose(v(r, "heat_input"), 8.0, rel_tol=1e-12)
    assert r.value.unit == "kelvin" or r.value.unit == "degree_Celsius"  # primary is final_temperature
    assert not any("time constants" in w for w in r.warnings)  # 600 s is 13 tau


def test_t_end_and_points_give_an_even_grid_in_seconds_even_for_minutes_input():
    r = ok(transient, **SINK, heat_input="8 W", t_end="10 min", points=11)
    rows = _rows(r)[1:]
    assert len(rows) == 11
    assert [float(x[0]) for x in rows] == pytest.approx([60.0 * i for i in range(11)])  # time column is seconds
    assert float(rows[0][1]) == 45.0
    assert math.isclose(float(rows[-1][1]), 45.0 + 8.0 * R * (1.0 - math.exp(-600.0 / TAU)), rel_tol=1e-12)
    assert r.data["table"]["rows"] == 11


def test_initial_offset_cool_down_and_single_time_with_heat():
    # starts at 60 degC with 8 W in 45 degC air: T(t) = 47 + 13 exp(-t/tau)
    r = ok(transient, **{**SINK, "initial_temperature": "60 degC"}, heat_input="8 W", time="45 s")
    assert math.isclose(v(r, "temperature"), 47.0 + 13.0 / math.e, rel_tol=1e-12)
    assert math.isclose(v(r, "final_temperature"), 47.0, rel_tol=1e-12)
    assert math.isclose(v(r, "energy_stored"), C_J_PER_K * (47.0 + 13.0 / math.e - 60.0), rel_tol=1e-9)
    # heat balance: stored rate + loss = input: C dT/dt = Q - hA (T - Tinf) at this point
    dTdt = -13.0 / TAU / math.e
    assert math.isclose(C_J_PER_K * dTdt, 8.0 - v(r, "heat_rate_to_ambient"), rel_tol=1e-9)


def test_time_to_reach_a_target_with_heat_input():
    r = ok(transient, **SINK, heat_input="8 W", target_temperature="46 degC")
    # 45 + 2 (1 - exp(-t/tau)) = 46 -> t = tau ln 2
    assert math.isclose(v(r, "time"), TAU * math.log(2.0), rel_tol=1e-12)
    fails(transient, "input.invalid", "target_temperature", **SINK, heat_input="8 W", target_temperature="48 degC")  # above the 47 degC steady state


def test_zero_heat_input_equals_the_old_cool_down_and_keeps_old_keys():
    base = {**SINK, "initial_temperature": "100 degC"}
    plain = ok(transient, **base, time="30 s")
    zero = ok(transient, **base, heat_input="0 W", time="30 s")
    assert v(plain, "temperature") == v(zero, "temperature")
    assert "energy_transferred" in zero.values and "heat_rate_now" in zero.values


def test_fahrenheit_system_header_and_short_table_warning():
    r = ok(transient, **SINK, heat_input="8 W", t_end="30 s", points=4, unit_system="imperial")
    rows = _rows(r)
    assert rows[0] == ["time_s", "temperature_degF"]
    assert math.isclose(float(rows[1][1]), 113.0, rel_tol=1e-9)  # 45 degC
    assert any("time constants" in w for w in r.warnings)


def test_biot_warning_still_applies_in_table_mode():
    r = ok(transient, **{**SINK, "h": "2000 W/(m^2*K)", "conductivity": "1 W/(m*K)"}, heat_input="8 W", t_end="10 s")
    assert any("Biot" in w for w in r.warnings)


@pytest.mark.parametrize(
    "extra,family,param",
    [
        ({"times": "10 s; 5 s"}, "input.invalid", "times"),
        ({"times": "0 s; 10 s", "t_end": "10 s"}, "input.invalid", "times|t_end"),
        ({"times": "0 s; 10 s", "time": "10 s"}, "input.invalid", "time|target_temperature"),
        ({"t_end": "100 s", "points": 1}, "input.invalid", "points"),
        ({"t_end": "100 s", "points": 5000}, "input.invalid", "points"),
        ({"t_end": "0 s"}, "input.invalid", "t_end"),
        ({"times": "10"}, "units.bare_number", "times"),
        ({"times": "10 m"}, "units.dimension_mismatch", "times"),
        ({"t_end": "1e308 s"}, "input.out_of_range", "t_end"),
        ({"heat_input": "-8 W", "time": "10 s"}, "input.invalid", "heat_input"),
        ({"heat_input": "8 m", "time": "10 s"}, "units.dimension_mismatch", "heat_input"),
        ({"heat_input": "1e300 W", "time": "10 s"}, "input.out_of_range", "heat_input"),
    ],
)
def test_table_refusals(extra, family, param):
    fails(transient, family, param, **SINK, **extra)


def test_table_and_heat_are_lumped_only():
    wall = dict(model="plane_wall", initial_temperature="200 degC", ambient_temperature="20 degC", conductivity="15 W/(m*K)",
                density="7800 kg/m^3", specific_heat="500 J/(kg*K)", h="100 W/(m^2*K)", size="10 mm")
    fails(transient, "input.invalid", "times", **wall, times="0 s; 10 s")
    fails(transient, "input.invalid", "t_end", **wall, t_end="10 s")
    fails(transient, "input.invalid", "heat_input", **wall, time="10 s", heat_input="5 W")
