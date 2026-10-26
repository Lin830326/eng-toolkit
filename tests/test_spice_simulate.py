"""spice_simulate against real ngspice, every expected number derived by hand below.

Tolerances (what each depends on):

* operating points and AC points of linear circuits are exact linear algebra: the only error is the gmin (1e-12 S) ngspice puts across every
  junction/node and rounding, so 1e-6 relative is generous;
* transient values depend on ngspice's integration error: with the largest internal step limited to tau/100 it was 2e-6 V on a 1 V step; the tests allow 1e-5 V;
* the diode depends on ngspice's value of kT/q (the 6th digit differs from CODATA 2018) and on gmin: 5e-5 V;
* all interpolation in these tests is linear between samples at least 100 per time constant, i.e. below 1e-6.
"""

import hashlib
import math

import pytest

from spice_fixtures import DIVIDER, artifact_path, call, interp, needs_ngspice, read_csv

pytestmark = needs_ngspice

RC = "rc low-pass\nV1 in 0 {src}\nR1 in out 1k\nC1 out 0 1u\n"  # tau = R C = 1e3 * 1e-6 = 1e-3 s
TAU = 1e-3
FC = 1 / (2 * math.pi * TAU)  # = 1 / 6.283185307e-3 = 159.15494309 Hz


# --------------------------------------------------------------------------- operating point


async def test_resistive_divider_operating_point_and_the_source_current_sign():
    # v(mid) = 5 V * 1k / (1k + 1k) = 2.5 V.  The source drives 5 V / 2 kOhm = 2.5 mA out of its + terminal; SPICE counts a current as positive
    # when it flows into the + terminal, so i(v1) = -2.5 mA.
    r = await call("spice_simulate", {"analysis": "op", "netlist": DIVIDER, "outputs": "v(mid),i(v1)"})
    assert r.ok, r.error
    assert r.values["v(mid)"].unit == "volt" and r.values["i(v1)"].unit == "ampere"
    assert r.values["v(mid)"].quantity == pytest.approx(2.5, rel=1e-6)
    assert r.values["i(v1)"].quantity == pytest.approx(-2.5e-3, rel=1e-6)
    assert r.value == r.values["v(mid)"]
    assert "positive when the current flows into the + terminal" in r.data["sign_convention"]
    assert r.review_status == "unreviewed" and r.provenance.engine == "eng_toolkit.spice"
    assert r.data["engine"]["version_banner"].startswith("ngspice-")


async def test_default_outputs_are_all_node_voltages():
    r = await call("spice_simulate", {"analysis": "op", "netlist": DIVIDER})
    assert r.ok and set(r.values) == {"v(in)", "v(mid)"}
    assert r.values["v(in)"].quantity == pytest.approx(5.0, rel=1e-9)


async def test_inductor_current_and_ground_alias_gnd():
    # DC: the inductor is a short, so i(l1) = 5 V / 1 kOhm = 5 mA (positive: from node x through the inductor to ground), v(x) = 0.
    net = "rl\nV1 in gnd 5\nR1 in x 1k\nL1 x gnd 1m\n"
    r = await call("spice_simulate", {"analysis": "op", "netlist": net, "outputs": "i(l1),v(x)"})
    assert r.ok, r.error
    assert r.values["i(l1)"].quantity == pytest.approx(5e-3, rel=1e-6)
    assert abs(r.values["v(x)"].quantity) < 1e-9


async def test_param_expression_and_subcircuit_and_behavioural_source():
    # .param rv = 2k: v = 5 * 1k / (2k + 1k) = 1.6666667 V
    net = "param\n.param rv=2k\nV1 in 0 5\nR1 in mid {rv}\nR2 mid 0 1k\n"
    r = await call("spice_simulate", {"analysis": "op", "netlist": net, "outputs": "v(mid)"})
    assert r.ok and r.values["v(mid)"].quantity == pytest.approx(5 / 3, rel=1e-6)
    # the divider as a subcircuit: 5 * 1k / 2k = 2.5 V
    sub = "sub\n.subckt div top mid\nR1 top mid 1k\nR2 mid 0 1k\n.ends\nV1 in 0 5\nX1 in out div\n"
    r = await call("spice_simulate", {"analysis": "op", "netlist": sub, "outputs": "v(out)"})
    assert r.ok and r.values["v(out)"].quantity == pytest.approx(2.5, rel=1e-6)
    assert r.data["netlist"]["expanded_elements"] == 4  # V1, X1, R1, R2
    # B source: v(out) = 2 * v(in) = 2 * 3 V = 6 V
    r = await call("spice_simulate", {"analysis": "op", "netlist": "gain\nV1 in 0 3\nB1 out 0 V=2*v(in)\nR1 out 0 1k\n", "outputs": "v(out)"})
    assert r.ok and r.values["v(out)"].quantity == pytest.approx(6.0, rel=1e-6)


def _diode_drop(supply=5.0, r=1000.0, i_s=1e-14, n=1.0, temp_k=300.15):
    """Newton on f(v) = (supply - v)/r - Is (exp(v / (n Vt)) - 1) = 0, Vt = k T / q with k = 1.380649e-23, q = 1.602176634e-19 (T = 27 degC)."""
    vt = 1.380649e-23 * temp_k / 1.602176634e-19  # = 0.025865 V
    v = 0.7
    for _ in range(100):
        f = (supply - v) / r - i_s * (math.exp(v / (n * vt)) - 1)
        df = -1 / r - i_s / (n * vt) * math.exp(v / (n * vt))
        step = f / df
        v -= step
        if abs(step) < 1e-15:
            break
    return v


async def test_diode_circuit_matches_the_shockley_equation_solved_by_newton():
    # 5 V -> 1 kOhm -> diode (Is = 1e-14 A, n = 1) -> ground.  The test solves (5 - Vd)/1k = Is (exp(Vd / Vt) - 1) itself: Vd = 0.69289 V, I = 4.3071 mA.
    net = "diode\nV1 in 0 5\nR1 in a 1k\nD1 a 0 dm\n.model dm d is=1e-14 n=1\n"
    r = await call("spice_simulate", {"analysis": "op", "netlist": net, "outputs": "v(a),i(v1)"})
    assert r.ok, r.error
    vd = _diode_drop()
    assert 0.69 < vd < 0.70
    assert r.values["v(a)"].quantity == pytest.approx(vd, abs=5e-5)
    assert r.values["i(v1)"].quantity == pytest.approx(-(5 - vd) / 1000, rel=1e-3)


# --------------------------------------------------------------------------- DC sweep


async def test_dc_sweep_of_a_divider_has_the_right_table_and_summary():
    # 1k over 3k: v(out) = Vs * 3000 / (1000 + 3000) = 0.75 Vs; 0 .. 5 V in 0.5 V steps is 11 points; i(v1) = -Vs / 4000.
    net = "dc\nV1 in 0 0\nR1 in out 1k\nR2 out 0 3k\n"
    r = await call(
        "spice_simulate",
        {"analysis": "dc", "netlist": net, "outputs": "v(out),i(v1)", "sweep_source": "v1", "start": "0 V", "stop": "5 V", "step": "0.5 V"},
    )
    assert r.ok, r.error
    assert r.data["x"]["samples"] == 11 and r.data["analysis"]["points"] == 11
    assert r.values["v(out).final"].quantity == pytest.approx(3.75, rel=1e-6)
    assert r.values["v(out).max"].quantity == pytest.approx(3.75, rel=1e-6)
    assert abs(r.values["v(out).min"].quantity) < 1e-9
    assert r.values["i(v1).final"].quantity == pytest.approx(-5 / 4000, rel=1e-6)
    header, rows = read_csv(artifact_path(r, "result.csv"))
    assert header == ["sweep_v1", "v(out)", "i(v1)"]
    for row in rows:
        assert row[1] == pytest.approx(0.75 * row[0], abs=1e-9)
    assert [round(row[0], 9) for row in rows] == [round(0.5 * k, 9) for k in range(11)]


async def test_dc_sweep_downwards_and_of_a_current_source():
    # I1 pushes 1 mA .. 3 mA (swept downwards) into 2 kOhm: v = I R = 2 V .. 6 V; the source is I1 so the unit of the sweep is ampere.
    net = "isweep\nI1 0 a 1m\nR1 a 0 2k\n"
    r = await call("spice_simulate", {"analysis": "dc", "netlist": net, "outputs": "v(a)", "sweep_source": "I1", "start": "3 mA", "stop": "1 mA", "step": "1 mA"})
    assert r.ok, r.error
    assert r.values["v(a).max"].quantity == pytest.approx(6.0, rel=1e-6) and r.values["v(a).final"].quantity == pytest.approx(2.0, rel=1e-6)


# --------------------------------------------------------------------------- AC


async def test_rc_low_pass_gain_and_phase_at_the_corner_and_a_decade_either_side():
    # |H| = 1 / sqrt(1 + (w R C)^2).  At f = fc = 1/(2 pi R C): w R C = 1, |H| = 1/sqrt(2) = 0.70710678 = -3.0103 dB, phase = -atan(1) = -45 degrees.
    # At 10 fc: 1/sqrt(1 + 100) = 0.099503719;  at fc/10: 1/sqrt(1 + 0.01) = 0.995037190.
    net = RC.format(src="DC 0 AC 1")
    for factor, gain, db, phase in (
        (1.0, 1 / math.sqrt(2), -3.010299957, -45.0),
        (10.0, 0.099503719, 20 * math.log10(0.099503719), -math.degrees(math.atan(10))),
        (0.1, 0.995037190, 20 * math.log10(0.995037190), -math.degrees(math.atan(0.1))),
    ):
        f = f"{FC * factor!r} Hz"
        r = await call("spice_simulate", {"analysis": "ac", "netlist": net, "outputs": "v(out)", "sweep": "lin", "points": 1, "start": f, "stop": f})
        assert r.ok, r.error
        assert r.values["v(out).final"].quantity == pytest.approx(gain, rel=1e-8)
        header, rows = read_csv(artifact_path(r, "result.csv"))
        assert header == ["frequency", "v(out).re", "v(out).im", "v(out).mag", "v(out).db", "v(out).phase_deg"]
        assert rows[0][header.index("v(out).db")] == pytest.approx(db, abs=1e-6)
        assert rows[0][header.index("v(out).phase_deg")] == pytest.approx(phase, abs=1e-6)


async def test_series_rlc_resonance_peak_and_half_power_points():
    # R = 10 Ohm, L = 1 mH, C = 1 uF.  w0 = 1/sqrt(L C) = 1/sqrt(1e-9) = 31622.7766 rad/s, f0 = w0/2pi = 5032.9212 Hz.
    # Q = (1/R) sqrt(L/C) = 0.1 * sqrt(1000) = 3.16227766.  The capacitor voltage at f0 is Q times the drive and lags it by 90 degrees (|Zc| = sqrt(L/C) = 31.62 Ohm, I = 1/R = 0.1 A).
    # Across R (band-pass): |H(f0)| = 1, and the half-power points are f0 (sqrt(1 + 1/(4 Q^2)) -+ 1/(2 Q)), a distance f0/Q = R/(2 pi L) = 1591.549 Hz apart.
    f0 = 1 / (2 * math.pi * math.sqrt(1e-3 * 1e-6))
    q = 0.1 * math.sqrt(1e-3 / 1e-6)
    assert f0 == pytest.approx(5032.921210, rel=1e-9) and q == pytest.approx(3.16227766, rel=1e-9)
    series_c = "rlc\nV1 in 0 DC 0 AC 1\nR1 in a 10\nL1 a out 1m\nC1 out 0 1u\n"
    at_f0 = f"{f0!r} Hz"
    r = await call("spice_simulate", {"analysis": "ac", "netlist": series_c, "outputs": "v(out)", "sweep": "lin", "points": 1, "start": at_f0, "stop": at_f0})
    assert r.ok and r.values["v(out).final"].quantity == pytest.approx(q, rel=1e-8)
    header, rows = read_csv(artifact_path(r, "result.csv"))
    assert rows[0][header.index("v(out).phase_deg")] == pytest.approx(-90.0, abs=1e-6)

    # the peak of |V_C|: Q / sqrt(1 - 1/(4 Q^2)) = 3.1622777 / sqrt(0.975) = 3.202604, at f0 sqrt(1 - 1/(2 Q^2)) = 0.974679 f0 = 4905.5 Hz
    peak = q / math.sqrt(1 - 1 / (4 * q * q))
    at = f0 * math.sqrt(1 - 1 / (2 * q * q))
    r = await call(
        "spice_simulate",
        {"analysis": "ac", "netlist": series_c, "outputs": "v(out)", "sweep": "dec", "points": 400, "start": f"{f0 / 10!r} Hz", "stop": f"{f0 * 10!r} Hz"},
    )
    assert r.ok, r.error
    assert r.values["v(out).max"].quantity == pytest.approx(peak, rel=5e-4)  # sampling the peak 400 times per decade: error ~ (0.29 %)^2 * Q-dependent curvature
    assert r.data["summary"]["v(out)"]["magnitude_peak_frequency_hz"] == pytest.approx(at, rel=1e-2)

    bandpass = "rlc\nV1 in 0 DC 0 AC 1\nL1 in a 1m\nC1 a b 1u\nR1 b 0 10\n"
    r = await call("spice_simulate", {"analysis": "ac", "netlist": bandpass, "outputs": "v(b)", "sweep": "lin", "points": 1, "start": at_f0, "stop": at_f0})
    assert r.ok and r.values["v(b).final"].quantity == pytest.approx(1.0, rel=1e-8)
    for sign in (-1, 1):
        f_half = f0 * (math.sqrt(1 + 1 / (4 * q * q)) + sign / (2 * q))
        r = await call("spice_simulate", {"analysis": "ac", "netlist": bandpass, "outputs": "v(b)", "sweep": "lin", "points": 1, "start": f"{f_half!r} Hz", "stop": f"{f_half!r} Hz"})
        assert r.values["v(b).final"].quantity == pytest.approx(1 / math.sqrt(2), rel=1e-8)
    f_lo = f0 * (math.sqrt(1 + 1 / (4 * q * q)) - 1 / (2 * q))
    f_hi = f0 * (math.sqrt(1 + 1 / (4 * q * q)) + 1 / (2 * q))
    assert f_hi - f_lo == pytest.approx(10 / (2 * math.pi * 1e-3), rel=1e-9)


# --------------------------------------------------------------------------- transient


async def test_rc_step_response_at_one_and_three_time_constants():
    # v(t) = V (1 - e^(-t/RC)); a 1 V step with a 1 ns edge (the 1 ns shifts the curve by 0.5 ns = 5e-7 of tau, far below the tolerance).
    # t = RC:  1 - e^-1 = 1 - 0.367879441 = 0.632120559;   t = 3 RC:  1 - e^-3 = 1 - 0.049787068 = 0.950212932
    net = RC.format(src="PULSE(0 1 0 1n 1n 1 2)")
    r = await call("spice_simulate", {"analysis": "tran", "netlist": net, "outputs": "v(out)", "step": "10 us", "stop": "5 ms"})
    assert r.ok, r.error
    header, rows = read_csv(artifact_path(r, "result.csv"))
    assert header == ["time", "v(out)"]
    assert interp(header, rows, "v(out)", 1 * TAU) == pytest.approx(0.6321205588, abs=1e-5)
    assert interp(header, rows, "v(out)", 3 * TAU) == pytest.approx(0.9502129316, abs=1e-5)
    # final value at 5 tau: 1 - e^-5 = 1 - 0.006737947 = 0.993262053
    assert r.values["v(out).final"].quantity == pytest.approx(0.9932620530, abs=1e-5)
    assert r.values["v(out).max"].quantity == pytest.approx(0.9932620530, abs=1e-5) and abs(r.values["v(out).min"].quantity) < 1e-6
    assert r.data["x"]["samples"] == len(rows) and r.data["x"]["last"] == pytest.approx(5e-3)


async def test_initial_condition_discharge_with_uic():
    # C = 1 uF starts at 2 V and discharges through 1 kOhm: v(t) = 2 e^(-t/RC); at t = RC: 2 / e = 0.735758882
    net = "discharge\nR1 out 0 1k\nC1 out 0 1u\n.ic v(out)=2\n"
    r = await call("spice_simulate", {"analysis": "tran", "netlist": net, "outputs": "v(out)", "step": "10 us", "stop": "3 ms", "use_initial_conditions": True})
    assert r.ok, r.error
    header, rows = read_csv(artifact_path(r, "result.csv"))
    assert interp(header, rows, "v(out)", 1e-3) == pytest.approx(2 / math.e, abs=1e-5)


async def test_the_same_netlist_twice_gives_identical_numbers():
    net = RC.format(src="PULSE(0 1 0 1n 1n 1 2)")
    args = {"analysis": "tran", "netlist": net, "outputs": "v(out)", "step": "20 us", "stop": "2 ms"}
    a, b = await call("spice_simulate", args), await call("spice_simulate", args)
    assert a.ok and b.ok
    assert a.values == b.values
    assert hashlib.sha256(artifact_path(a, "result.csv").read_bytes()).digest() == hashlib.sha256(artifact_path(b, "result.csv").read_bytes()).digest()
    assert a.provenance.inputs_hash == b.provenance.inputs_hash
    op = {"analysis": "op", "netlist": DIVIDER}
    assert (await call("spice_simulate", op)).values == (await call("spice_simulate", op)).values


# --------------------------------------------------------------------------- inputs and files


async def test_netlist_from_a_file_gives_the_same_result_and_is_recorded(tmp_path):
    path = tmp_path / "divider.cir"
    path.write_text(DIVIDER, encoding="utf-8")
    inline = await call("spice_simulate", {"analysis": "op", "netlist": DIVIDER, "outputs": "v(mid)"})
    from_file = await call("spice_simulate", {"analysis": "op", "netlist_file": str(path), "outputs": "v(mid)"})
    assert from_file.ok and from_file.values == inline.values
    assert len(from_file.provenance.input_files) == 1
    assert from_file.provenance.input_files[0].sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


async def test_the_circuit_ngspice_received_is_the_normalised_netlist(tmp_path):
    net = "My Title\nV1 IN 0 DC 5   ; supply\n* a comment .include x\nR1\tIN MID\n+ 1K\nR2 MID 0 1k\n"
    r = await call("spice_simulate", {"analysis": "op", "netlist": net, "outputs": "v(mid)"})
    assert r.ok, r.error
    sent = artifact_path(r, "netlist.cir").read_text(encoding="utf-8")
    assert "v1 in 0 dc 5\nr1 in mid 1k\nr2 mid 0 1k\n" in sent
    assert "my title" not in sent.lower().split("\n", 1)[1] and "include" not in sent.split("\n", 1)[1] and "supply" not in sent
    deck = artifact_path(r, "deck.cir").read_text(encoding="utf-8")
    assert deck.startswith("eng-toolkit deck\nv1 in 0 dc 5\n")
    assert r.values["v(mid)"].quantity == pytest.approx(2.5, rel=1e-6)


@pytest.mark.parametrize(
    "args, family, needle",
    [
        ({"analysis": "op"}, "input.missing", "netlist"),
        ({"analysis": "op", "netlist": DIVIDER, "netlist_file": "x.cir"}, "input.invalid", "exactly one"),
        ({"analysis": "noise", "netlist": DIVIDER}, "input.invalid", "analysis"),
        ({"analysis": "op", "netlist": DIVIDER, "outputs": "v(zz)"}, "input.invalid", "node"),
        ({"analysis": "op", "netlist": DIVIDER, "outputs": "i(r1)"}, "input.invalid", "voltage source"),
        ({"analysis": "op", "netlist": DIVIDER, "outputs": "v(0)"}, "input.invalid", "reference"),
        ({"analysis": "op", "netlist": DIVIDER, "outputs": "@r1[i]"}, "input.invalid", "not 'v(node)'"),
        ({"analysis": "dc", "netlist": DIVIDER, "start": "0 V", "stop": "1 V", "step": "0.1 V"}, "input.invalid", "sweep_source"),
        ({"analysis": "dc", "netlist": DIVIDER, "sweep_source": "r1", "start": "0 V", "stop": "1 V", "step": "0.1 V"}, "input.invalid", "sweep_source"),
        ({"analysis": "dc", "netlist": DIVIDER, "sweep_source": "v1", "start": "0 V", "stop": "1 V", "step": "0.1"}, "units.bare_number", "step"),
        ({"analysis": "dc", "netlist": DIVIDER, "sweep_source": "v1", "start": "0 V", "stop": "1 V", "step": "0.1 A"}, "units.dimension_mismatch", "step"),
        ({"analysis": "dc", "netlist": DIVIDER, "sweep_source": "v1", "start": "0 V", "stop": "1 V", "step": "1 nV"}, "input.out_of_range", "points"),
        ({"analysis": "ac", "netlist": DIVIDER, "start": "10 Hz", "stop": "1 Hz", "points": 10}, "input.invalid", "above"),
        ({"analysis": "ac", "netlist": DIVIDER, "start": "1 Hz", "stop": "1 kHz", "points": 0}, "input.invalid", "points"),
        ({"analysis": "ac", "netlist": DIVIDER, "start": "1 rad/s", "stop": "1 kHz", "points": 10}, "units.parse_error", "hertz"),
        ({"analysis": "ac", "netlist": DIVIDER, "start": "1 Hz", "stop": "1e308 Hz", "points": 10}, "input.out_of_range", "stop"),
        ({"analysis": "ac", "netlist": DIVIDER, "start": "1 Hz", "stop": "1 THz", "points": 501}, "input.out_of_range", "points"),
        ({"analysis": "ac", "netlist": DIVIDER, "start": "1 Hz", "stop": "1 kHz", "points": 60000, "sweep": "lin"}, "input.out_of_range", "points"),
        ({"analysis": "tran", "netlist": DIVIDER, "step": "1 us", "stop": "1e12 s"}, "input.out_of_range", "stop"),
        ({"analysis": "tran", "netlist": DIVIDER, "step": "1 us", "stop": "1e6 s"}, "input.out_of_range", "points"),
        ({"analysis": "tran", "netlist": DIVIDER, "step": "1 ps", "stop": "1 s"}, "input.out_of_range", "points"),
        ({"analysis": "tran", "netlist": DIVIDER, "step": "1 us", "stop": "1 V"}, "units.dimension_mismatch", "stop"),
        ({"analysis": "tran", "netlist": DIVIDER, "step": "1 us", "start": "2 ms", "stop": "1 ms"}, "input.invalid", "later than"),
        ({"analysis": "tran", "netlist": DIVIDER, "step": "1 ms", "stop": "1 us"}, "input.invalid", "step"),
    ],
)
async def test_bad_parameters_are_refused_before_ngspice_starts(args, family, needle):
    r = await call("spice_simulate", args)
    assert not r.ok and r.error.family == family, r.error
    assert needle in r.error.message
    assert r.review_status == "unreviewed" and not r.values


async def test_a_transient_asking_for_a_trillion_points_is_refused_with_the_count_and_the_limit():
    r = await call("spice_simulate", {"analysis": "tran", "netlist": DIVIDER, "step": "1 us", "stop": "1e6 s"})
    assert not r.ok and r.error.family == "input.out_of_range"
    assert r.error.details["points"] == 1e12 + 1 or r.error.details["points"] >= 1e12
    assert r.error.details["limit"] == 100_000


async def test_a_netlist_with_a_problem_is_not_simulated():
    r = await call("spice_simulate", {"analysis": "op", "netlist": "t\nV1 a b 5\nR1 a b 1k\n"})  # no ground
    assert not r.ok and r.error.family == "input.netlist_invalid"
    assert r.error.details["problems"][0]["code"] == "no_ground"


async def test_gateway_lists_the_three_tools_with_basic_parameter_types():
    from fastmcp import Client

    from eng_toolkit.gateway.server import gateway

    async with Client(gateway) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert {"spice_simulate", "spice_check_netlist", "spice_measure"} <= set(tools)
    for name in ("spice_simulate", "spice_check_netlist", "spice_measure"):
        for pname, prop in tools[name].input_schema.get("properties", {}).items():
            assert prop.get("type") in {"string", "number", "integer", "boolean"}, (name, pname, prop)
            assert prop.get("description"), (name, pname)



async def test_a_relative_workspace_path_works_for_ngspice(tmp_path, monkeypatch):
    # UAT item 1: "ngspice exited with code 1" when ENG_TOOLKIT_WS was relative
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENG_TOOLKIT_WS", "relative-ws")
    r = await call("spice_simulate", {"netlist": DIVIDER, "analysis": "op"})
    assert r.ok, r.error
