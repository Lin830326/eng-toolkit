"""Round 3 (judge F1-2): pump_system and pipe_network write system_curve.csv and name the operating point.

Closed forms: pump H = 20 m - 200 s/m^2 * Q (points (0, 20 m) and (0.1 m^3/s, 0 m), efficiency 0.6 -> 0.8 linear), system H = 5 m + 1000 s^2/m^5 Q^2.
The curves cross where 1000 Q^2 + 200 Q - 15 = 0, Q = (-200 + sqrt(200^2 + 4 * 1000 * 15))/2000 = 0.0581138830 m^3/s, H = 8.3772234 m.
"""

import csv
import math
from pathlib import Path

import pytest

from eng_toolkit.domains.fluids.pipe_network import pipe_network
from eng_toolkit.domains.fluids.pump_system import pump_system
from tests.fluids_testing import G, PUMP, call, close, ok, v

Q_OP = (-200 + math.sqrt(200**2 + 4 * 1000 * 15)) / 2000


def rows_of(result, name="system_curve.csv"):
    path = next(a.path for a in result.artifacts if a.path.endswith(name))
    with Path(path).open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        return reader.fieldnames, [{k: float(x) for k, x in row.items()} for row in reader]


def crossing(rows, a="system_head_m", b="pump_head_m", x="flow_m3_s"):
    """Flow at which the two curves cross, by linear interpolation of their difference between the rows around the sign change."""
    for left, right in zip(rows, rows[1:]):
        dl, dr = left[b] - left[a], right[b] - right[a]
        if dl > 0 >= dr:
            return left[x] + (right[x] - left[x]) * dl / (dl - dr)
    raise AssertionError("the curves do not cross in the table")


# ------------------------------------------------------------------------------------------------- pump_system
def test_single_pump_table_matches_the_closed_forms_and_the_operating_point_is_a_row():
    r = ok(pump_system, **PUMP, curve_points=11)
    header, rows = rows_of(r)
    assert header == ["flow_m3_s", "system_head_m", "pump_head_m", "efficiency", "is_operating_point"]
    assert len(rows) == 12  # 11 evenly spaced rows plus the operating point
    assert rows[0]["flow_m3_s"] == 0 and rows[-1]["flow_m3_s"] == pytest.approx(0.1)  # 0 .. the last pump point
    for row in rows:
        q = row["flow_m3_s"]
        close(row["system_head_m"], 5 + 1000 * q * q, rel=1e-12)
        close(row["pump_head_m"], 20 - 200 * q, rel=1e-12, abs=1e-12)
        if 0 < q < 0.1:
            close(row["efficiency"], 0.6 + 2 * q, rel=1e-9)  # the supplied efficiency, interpolated: 0.6 at Q = 0 -> 0.8 at 0.1 m^3/s
    op = [row for row in rows if row["is_operating_point"] == 1]
    assert len(op) == 1
    close(op[0]["flow_m3_s"], v(r, "operating_flow"), rel=1e-12)
    close(op[0]["pump_head_m"], op[0]["system_head_m"], rel=1e-9)  # the two curves meet in the reported operating point
    close(v(r, "operating_flow"), Q_OP, rel=1e-10)
    point = r.data["operating_point"]
    assert point["flow_m3_s"] == v(r, "operating_flow") and point["head_m"] == v(r, "operating_head")
    close(point["head_m"], 20 - 200 * Q_OP, rel=1e-10)
    assert point["efficiency"] == pytest.approx(v(r, "efficiency")) and point["system_head_m"] == pytest.approx(point["pump_head_m"], rel=1e-9)
    assert r.data["curve_table"]["rows"] == 12 and r.data["curve_table"]["flow_max_m3_s"] == pytest.approx(0.1)


def test_the_crossing_found_in_the_table_alone_is_the_operating_point():
    r = ok(pump_system, **PUMP, curve_points=200)
    _, rows = rows_of(r)
    plain = [row for row in rows if row["is_operating_point"] == 0]  # without the inserted row, from the grid only
    close(crossing(plain), v(r, "operating_flow"), rel=2e-4)  # a 1/200 grid and a parabola against two straight segments
    close(crossing(rows), v(r, "operating_flow"), rel=1e-9)  # with the row, the crossing is exact


def test_no_table_by_default():
    r = ok(pump_system, **PUMP)
    assert not any(a.path.endswith("system_curve.csv") for a in r.artifacts) and "operating_point" not in r.data


def test_pipe_geometry_system_and_speed_scaling_use_the_models_own_curves():
    r = ok(pump_system, pump_points=PUMP["pump_points"], static_head="5 m", pipe_length="20 m", pipe_diameter="0.1 m", roughness="0.05 mm",
           minor_loss_coefficient=2, speed_ratio=1.1, curve_points=21)
    _, rows = rows_of(r)
    op = next(row for row in rows if row["is_operating_point"] == 1)
    close(op["flow_m3_s"], v(r, "operating_flow"), rel=1e-12)
    close(op["system_head_m"], v(r, "operating_head"), rel=1e-9)
    close(op["pump_head_m"], v(r, "operating_head"), rel=1e-9)
    assert rows[-1]["flow_m3_s"] == pytest.approx(0.1 * 1.1)  # the curve scaled by the speed ratio ends at 1.1 times the flow
    close(rows[0]["pump_head_m"], 20 * 1.1**2, rel=1e-12)  # shut-off head scales with the speed squared
    assert rows[0]["system_head_m"] == 5.0


TWO = "a,0 m^3/s,20 m,0.6;a,0.1 m^3/s,0 m,0.8;b,0 m^3/s,20 m,0.6;b,0.1 m^3/s,0 m,0.8"


@pytest.mark.parametrize(
    "combination,heads,flow_end",
    [("series", lambda q: 40 - 400 * q, 0.1), ("parallel", lambda q: 20 * (1 - q / 0.2), 0.2)],
)
def test_two_identical_pumps_in_series_and_parallel(combination, heads, flow_end):
    r = ok(pump_system, **{**PUMP, "pump_points": TWO}, combination=combination, curve_points=9)
    _, rows = rows_of(r)
    assert rows[-1]["flow_m3_s"] == pytest.approx(flow_end)
    for row in rows:
        close(row["pump_head_m"], heads(row["flow_m3_s"]), rel=1e-9, abs=1e-9)
        close(row["system_head_m"], 5 + 1000 * row["flow_m3_s"] ** 2, rel=1e-12)
        if row["flow_m3_s"] > 0 and row["pump_head_m"] > 1e-6:
            # identical pumps share the flow (series: the same Q, parallel: half each): the efficiency is the curve's value at the pump's own flow
            own = row["flow_m3_s"] / (2 if combination == "parallel" else 1)
            close(row["efficiency"], 0.6 + 2 * own, rel=1e-9)
    op = next(row for row in rows if row["is_operating_point"] == 1)
    close(op["flow_m3_s"], v(r, "operating_flow"), rel=1e-12)
    close(op["pump_head_m"], op["system_head_m"], rel=1e-8)
    close(op["efficiency"], v(r, "efficiency"), rel=1e-9)


@pytest.mark.parametrize(
    "bad,family",
    [(-1, "input.out_of_range"), (1, "input.out_of_range"), (501, "input.out_of_range"), (2.5, "input.invalid"), (True, "input.invalid"), (False, "input.invalid"), ("5", "input.invalid")],
)
def test_curve_points_refusals(bad, family):
    r = call(pump_system, **PUMP, curve_points=bad)
    assert not r.ok and r.error.family == family and r.error.details["param"] == "/curve_points"


# ------------------------------------------------------------------------------------------------- pipe_network
# a pump from reservoir r to node a, a pipe a -> t to a reservoir whose head is 3 m: pump H = 20 m - 8000 s/m^2 Q.  The fluid is 0.1 Pa*s oil in a
# 20 mm, 10 m pipe, so the flow is laminar (Re = 4 rho Q/(pi D mu) = 637 000 Q < 1600 up to Q = 2.5e-3 m^3/s): Hagen-Poiseuille h = 128 mu L Q/(pi rho g D^4).
OIL = dict(density="1000 kg/m^3", dynamic_viscosity="0.1 Pa*s")
LAMINAR = dict(
    nodes="r,0 m,head,0 m;a,0 m,demand,0 m^3/s;t,3 m,head,3 m", pipes="p,a,t,10 m,20 mm,0 mm,0",
    pumps="pu,r,a,poly,20 m,8000 s/m^2,0 s^2/m^5,yes", **OIL,
)
C_PIPE = 128 * 0.1 * 10 / (math.pi * 1000 * G * 0.02**4)  # head loss per unit flow, s/m^2


def test_network_system_curve_against_hagen_poiseuille_and_the_operating_point():
    r = ok(pipe_network, **LAMINAR, curve_points=9)
    header, rows = rows_of(r)
    assert header == ["flow_m3_s", "system_head_m", "pump_head_m", "is_operating_point"]  # no efficiency: pipe_network has no efficiency data
    assert rows[0]["flow_m3_s"] == 0 and rows[-1]["flow_m3_s"] == pytest.approx(20 / 8000)  # the flow at which the pump head falls to zero
    for row in rows:
        q = row["flow_m3_s"]
        close(row["system_head_m"], 3 + C_PIPE * q, rel=1e-7, abs=1e-9)
        close(row["pump_head_m"], 20 - 8000 * q, rel=1e-12, abs=1e-9)
    q_op = 17 / (8000 + C_PIPE)  # 20 - 8000 Q = 3 + C Q
    point = r.data["operating_point"]
    close(point["flow_m3_s"], q_op, rel=1e-7)
    close(point["head_m"], 20 - 8000 * q_op, rel=1e-7)
    op = next(row for row in rows if row["is_operating_point"] == 1)
    close(op["flow_m3_s"], point["flow_m3_s"], rel=1e-12)
    close(op["system_head_m"], op["pump_head_m"], rel=1e-7)
    close(point["system_head_m"], point["pump_head_m"], rel=1e-7)
    assert r.data["pumps"][0]["flow_m3_s"] == point["flow_m3_s"]


def test_network_with_pump_points_and_a_second_branch_in_parallel():
    # two identical pipes in parallel halve the friction: C/2 per unit flow
    net = {**LAMINAR, "pipes": "p,a,t,10 m,20 mm,0 mm,0;q,a,t,10 m,20 mm,0 mm,0", "pumps": "pu,r,a,points,0 m^3/s:20 m|0.0025 m^3/s:0 m,yes"}
    r = ok(pipe_network, **net, curve_points=6)
    _, rows = rows_of(r)
    for row in rows:
        close(row["system_head_m"], 3 + C_PIPE / 2 * row["flow_m3_s"], rel=1e-7, abs=1e-9)
        close(row["pump_head_m"], 20 - 8000 * row["flow_m3_s"], rel=1e-9, abs=1e-9)
    op = next(row for row in rows if row["is_operating_point"] == 1)
    close(op["pump_head_m"], op["system_head_m"], rel=1e-7)


def test_a_network_without_exactly_one_pump_gets_a_warning_and_no_table():
    none = ok(pipe_network, nodes="r,0 m,head,20 m;j,0 m,demand,1 L/s", pipes="p,r,j,10 m,50 mm,0 mm,0", curve_points=5)
    assert not any(a.path.endswith("system_curve.csv") for a in none.artifacts) and any("exactly one pump" in w for w in none.warnings)
    two = ok(pipe_network, **{**LAMINAR, "pumps": LAMINAR["pumps"] + ";pw,r,a,poly,20 m,8000 s/m^2,0 s^2/m^5,yes"}, curve_points=5)
    assert any("exactly one pump" in w and "2" in w for w in two.warnings) and not any(a.path.endswith("system_curve.csv") for a in two.artifacts)


@pytest.mark.parametrize("bad", [-1, 1, 201, 3.5, False])
def test_network_curve_points_refusals(bad):
    r = call(pipe_network, **LAMINAR, curve_points=bad)
    assert not r.ok and r.error.details["param"] == "/curve_points"


def test_default_network_result_is_unchanged_by_the_refactor():
    r = ok(pipe_network, nodes="r,0 m,head,20 m;j,0 m,demand,1 L/s", pipes="p,r,j,10 m,50 mm,0 mm,0")
    assert "operating_point" not in r.data and v(r, "iterations") >= 1
