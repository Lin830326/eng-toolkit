"""spice_measure: the formulas on tables built from closed-form curves (no ngspice), then on tables ngspice produced (skipped when it is absent).

Every expected value is derived in the comment above it.  The tolerances are the interpolation error of the sample spacing used, stated per test.
"""

import math
from pathlib import Path

import pytest

from spice_fixtures import artifact_path, call, needs_ngspice

TAU = 1e-3
LN9 = math.log(9.0)  # 2.1972246


def write_csv(path: Path, header: list[str], columns: list[list[float]]) -> str:
    rows = [",".join(header)] + [",".join(repr(v) for v in row) for row in zip(*columns)]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return str(path)


def time_grid(span: float, n: int) -> list[float]:
    return [span * k / n for k in range(n + 1)]


def first_order(tmp_path, *, rising=True, span=20 * TAU, n=4000, name="v(out)"):
    ts = time_grid(span, n)
    ys = [1 - math.exp(-t / TAU) if rising else math.exp(-t / TAU) for t in ts]
    return write_csv(tmp_path / "step.csv", ["time", name], [ts, ys])


async def measure(csv_file, kind, **extra):
    return await call("spice_measure", {"csv_file": csv_file, "measurement": kind, "column": extra.pop("column", "v(out)"), **extra})


# --------------------------------------------------------------------------- formulas on closed-form curves


async def test_rise_time_of_a_first_order_step_is_tau_ln9(tmp_path):
    # y = 1 - e^(-t/tau): y = 0.1 at t = -tau ln 0.9 = 0.105361 tau, y = 0.9 at t = -tau ln 0.1 = 2.302585 tau; the difference is tau ln 9 = 2.197225 tau.
    # 4000 samples over 20 tau (5 us spacing): linear interpolation error well below 1e-4 of the result; the steady state (last 5 % = 19..20 tau) is 1 - 5.6e-9.
    path = first_order(tmp_path)
    r = await measure(path, "rise_time", final_fraction=0.05)
    assert r.ok, r.error
    assert r.value.unit == "second" and r.values["rise_time"].quantity == pytest.approx(LN9 * TAU, rel=1e-4)
    assert r.data["t_10_percent_s"] == pytest.approx(-TAU * math.log(0.9), rel=2e-3)
    assert "10 %" in r.data["formula"] and r.data["steady_state"] == pytest.approx(1.0, abs=1e-6)
    assert r.review_status == "unreviewed" and r.provenance.input_files[0].bytes > 0


async def test_fall_time_of_a_decay_is_also_tau_ln9(tmp_path):
    # y = e^(-t/tau) falls from 0.9 (t = 0.105361 tau) to 0.1 (t = 2.302585 tau): tau ln 9 = 2.197225 tau
    path = first_order(tmp_path, rising=False)
    r = await measure(path, "fall_time", final_fraction=0.05)
    assert r.ok and r.value.quantity == pytest.approx(LN9 * TAU, rel=1e-4)
    wrong = await measure(path, "rise_time", final_fraction=0.05)
    assert not wrong.ok and wrong.error.family == "measure.not_found" and ("falling" in wrong.error.message or "down" in wrong.error.message)


async def test_settling_time_to_two_percent_is_tau_ln50(tmp_path):
    # |y - 1| = e^(-t/tau) <= 0.02 when t >= tau ln 50 = 3.912023 tau
    path = first_order(tmp_path)
    r = await measure(path, "settling_time", final_fraction=0.05, tolerance=0.02)
    assert r.ok and r.value.quantity == pytest.approx(TAU * math.log(50), rel=1e-4)
    r = await measure(path, "settling_time", final_fraction=0.05, tolerance=0.1)  # ln 10 = 2.302585 tau
    assert r.ok and r.value.quantity == pytest.approx(TAU * math.log(10), rel=1e-4)


async def test_overshoot_of_an_underdamped_second_order_step_matches_the_closed_form(tmp_path):
    # zeta = 0.2, wn = 2 pi 1000 rad/s: overshoot = exp(-pi zeta / sqrt(1 - zeta^2)) = exp(-0.641275) = 0.526626 -> 52.6626 %
    zeta, wn = 0.2, 2 * math.pi * 1000
    wd = wn * math.sqrt(1 - zeta**2)
    phi = math.acos(zeta)
    ts = time_grid(0.05, 50_000)
    ys = [1 - math.exp(-zeta * wn * t) / math.sqrt(1 - zeta**2) * math.sin(wd * t + phi) for t in ts]
    path = write_csv(tmp_path / "second.csv", ["time", "v(out)"], [ts, ys])
    r = await measure(path, "overshoot", final_fraction=0.1)
    expected = 100 * math.exp(-math.pi * zeta / math.sqrt(1 - zeta**2))
    assert expected == pytest.approx(52.6626, abs=1e-3)
    assert r.ok and r.value.unit == "percent" and r.value.quantity == pytest.approx(expected, abs=0.01)
    assert r.data["peak"] == pytest.approx(1 + expected / 100, abs=1e-4)


async def test_overshoot_of_a_first_order_step_is_zero(tmp_path):
    r = await measure(first_order(tmp_path), "overshoot", final_fraction=0.05)
    assert r.ok and r.value.quantity == pytest.approx(0.0, abs=1e-4)  # 1.5e-7 percent: the steady-state mean of the last 5 % is a hair below the true final value


async def test_steady_state_is_the_time_weighted_mean_of_the_last_fraction_on_a_nonuniform_grid(tmp_path):
    # y = 3 + t on t = u^2 (u = 0..1, so the spacing is far from uniform).  Mean of (3 + t) over t in [0.9, 1.0] = 3 + 0.95 = 3.95; trapezoids are exact for a line.
    ts = [(k / 400) ** 2 for k in range(401)]
    path = write_csv(tmp_path / "ramp.csv", ["time", "v(out)"], [ts, [3 + t for t in ts]])
    r = await measure(path, "steady_state", final_fraction=0.1)
    assert r.ok and r.value.unit == "volt" and r.value.quantity == pytest.approx(3.95, rel=1e-12)
    assert r.values["steady_state"].quantity == r.value.quantity


async def test_value_at_interpolates_linearly_with_units(tmp_path):
    # y = 2 t + 1 on the same nonuniform grid: y(0.37 s) = 1.74 exactly
    ts = [(k / 400) ** 2 for k in range(401)]
    path = write_csv(tmp_path / "line.csv", ["time", "i(v1)"], [ts, [2 * t + 1 for t in ts]])
    for at in ("0.37 s", "370 ms", "370000 us"):
        r = await measure(path, "value_at", column="i(v1)", at=at)
        assert r.ok, r.error
        assert r.value.quantity == pytest.approx(1.74, rel=1e-12) and r.value.unit == "ampere"
    out = await measure(path, "value_at", column="i(v1)", at="2 s")
    assert not out.ok and out.error.family == "measure.not_found"
    for bad, family in (("", "input.missing"), ("0.37", "units.bare_number"), ("5 V", "units.dimension_mismatch")):
        r = await measure(path, "value_at", column="i(v1)", at=bad)
        assert not r.ok and r.error.family == family, (bad, r.error)


async def test_bandwidth_of_an_rc_low_pass_is_1_over_2_pi_rc(tmp_path):
    # |H| = 1/sqrt(1 + (f/fc)^2), fc = 1/(2 pi R C) = 159.15494 Hz for RC = 1 ms; -3.0103 dB below the first sample (|H| = 0.99998 at 1 Hz, so the
    # target is a hair below -3.0103 dB; the difference is 1.7e-4 dB).  20 points per decade, interpolation linear in dB against log10 f: error ~2e-4 relative.
    fc = 1 / (2 * math.pi * TAU)
    fs = [10 ** (k / 20) for k in range(0, 101)]  # 1 Hz .. 1e5 Hz
    mags = [1 / math.sqrt(1 + (f / fc) ** 2) for f in fs]
    dbs = [20 * math.log10(m) for m in mags]
    path = write_csv(tmp_path / "ac.csv", ["frequency", "v(out).mag", "v(out).db", "v(out).phase_deg"], [fs, mags, dbs, [-math.degrees(math.atan(f / fc)) for f in fs]])
    for column in ("v(out)", "v(out).mag", "v(out).db"):
        r = await measure(path, "bandwidth_3db", column=column)
        assert r.ok, r.error
        assert r.value.unit == "hertz" and r.value.quantity == pytest.approx(fc, rel=5e-4)
    assert r.data["target_gain_db"] == pytest.approx(r.data["reference_gain_db"] - 3.0102999566, abs=1e-9)


# --------------------------------------------------------------------------- refusals


async def test_measurements_that_the_table_cannot_support_say_so(tmp_path):
    ts = time_grid(1.0, 100)
    still = write_csv(tmp_path / "flat.csv", ["time", "v(out)"], [ts, [2.0] * 101])
    r = await measure(still, "rise_time")
    assert not r.ok and r.error.family == "measure.not_found" and "no step" in r.error.message
    ramp = write_csv(tmp_path / "ramp.csv", ["time", "v(out)"], [ts, [t for t in ts]])  # still rising at the end: last 10 % averages to 0.95, the data never settle
    r = await measure(ramp, "settling_time", tolerance=0.02)
    assert not r.ok and r.error.family == "measure.not_found" and "not settled" in r.error.message
    lowpass = write_csv(tmp_path / "ac.csv", ["frequency", "v(out).db"], [[1.0, 10.0, 100.0], [0.0, -0.01, -0.02]])
    r = await measure(lowpass, "bandwidth_3db")
    assert not r.ok and r.error.family == "measure.not_found" and "never falls" in r.error.message
    r = await measure(lowpass, "rise_time", column="v(out).db")
    assert not r.ok and r.error.family == "input.invalid" and "'time'" in r.error.message
    r = await measure(still, "bandwidth_3db")
    assert not r.ok and r.error.family == "input.invalid" and "frequency" in r.error.message


@pytest.mark.parametrize(
    "content, family, needle",
    [
        ("", "input.invalid", "empty"),
        ("time\n1\n2\n3\n", "input.invalid", "header"),
        ("time,v(out)\n0,1\n1,2\n", "input.invalid", "3 data rows"),
        ("time,v(out)\n0,1\n1,nan\n2,3\n", "input.invalid", "not finite"),
        ("time,v(out)\n0,1\n1,inf\n2,3\n", "input.invalid", "not finite"),
        ("time,v(out)\n0,1\n1,abc\n2,3\n", "input.invalid", "not a number"),
        ("time,v(out)\n0,1\n1\n2,3\n", "input.invalid", "fields"),
        ("time,v(out)\n0,1\n2,2\n1,3\n", "input.invalid", "increasing"),
        ("time,v(out)\n0,1\n1,2\n1,3\n", "input.invalid", "increasing"),
    ],
)
async def test_bad_tables_are_refused(tmp_path, content, family, needle):
    path = tmp_path / "bad.csv"
    path.write_text(content, encoding="utf-8")
    r = await measure(str(path), "steady_state")
    assert not r.ok and r.error.family == family and needle in r.error.message, r.error


async def test_bad_parameters_and_files(tmp_path):
    path = first_order(tmp_path)
    for args, needle in (
        ({"measurement": "frobnicate"}, "measurement"),
        ({"measurement": "steady_state", "column": "v(zz)"}, "not in the file"),
        ({"measurement": "steady_state", "final_fraction": 0.9}, "final_fraction"),
        ({"measurement": "steady_state", "final_fraction": 0.0}, "final_fraction"),
        ({"measurement": "settling_time", "tolerance": 1.5}, "tolerance"),
        ({"measurement": "settling_time", "tolerance": 1.0}, "tolerance"),
    ):
        r = await call("spice_measure", {"csv_file": path, **{"column": "v(out)", **args}})
        assert not r.ok and r.error.family == "input.invalid" and needle in r.error.message, (args, r.error)
    missing = await call("spice_measure", {"csv_file": str(tmp_path / "nope.csv"), "measurement": "steady_state"})
    assert not missing.ok and missing.error.family == "input.file_not_found"
    wrong = tmp_path / "t.txt"
    wrong.write_text("time,v\n0,1\n", encoding="utf-8")
    r = await call("spice_measure", {"csv_file": str(wrong), "measurement": "steady_state"})
    assert not r.ok and r.error.family == "input.unsupported_format"
    one_column = tmp_path / "one.csv"
    write_csv(one_column, ["time", "v(out)"], [[0.0, 1.0, 2.0], [1.0, 1.0, 1.0]])
    r = await call("spice_measure", {"csv_file": str(one_column), "measurement": "steady_state"})  # a single data column needs no 'column'
    assert r.ok and r.value.quantity == pytest.approx(1.0)


# --------------------------------------------------------------------------- on tables ngspice made


@needs_ngspice
async def test_rc_step_from_ngspice_rise_and_settling_time():
    # tau = 1 ms: 10-90 % rise = tau ln 9 = 2.197225 ms, 2 % settling = tau ln 50 = 3.912023 ms.  Depends on ngspice's integration error (about 2e-6 V on a 1 V step
    # at this step) and on the 10 us output spacing: 1e-3 relative on the rise time (2.2 us) is generous.
    net = "rc\nV1 in 0 PULSE(0 1 0 1n 1n 1 2)\nR1 in out 1k\nC1 out 0 1u\n"
    sim = await call("spice_simulate", {"analysis": "tran", "netlist": net, "outputs": "v(out)", "step": "10 us", "stop": "20 ms"})
    assert sim.ok, sim.error
    table = str(artifact_path(sim, "result.csv"))
    rise = await call("spice_measure", {"csv_file": table, "measurement": "rise_time", "column": "v(out)", "final_fraction": 0.05})
    assert rise.ok and rise.value.quantity == pytest.approx(LN9 * TAU, rel=1e-3)
    settle = await call("spice_measure", {"csv_file": table, "measurement": "settling_time", "column": "v(out)", "final_fraction": 0.05})
    assert settle.ok and settle.value.quantity == pytest.approx(TAU * math.log(50), rel=2e-3)
    final = await call("spice_measure", {"csv_file": table, "measurement": "steady_state", "column": "v(out)", "final_fraction": 0.05})
    assert final.value.quantity == pytest.approx(1.0, abs=1e-5)


@needs_ngspice
async def test_series_rlc_overshoot_from_ngspice_matches_the_closed_form():
    # R = 10 Ohm, L = 1 mH, C = 1 uF: zeta = (R/2) sqrt(C/L) = 5 * 0.0316228 = 0.158114; overshoot = exp(-pi zeta / sqrt(1 - zeta^2)) = exp(-0.503054) = 0.604679 -> 60.468 %.
    # Depends on ngspice's integration error and on the 2 us spacing around the peak (a few 1e-3 of a percent point): 0.1 percentage points allowed.
    net = "rlc\nV1 in 0 PULSE(0 1 0 1n 1n 1 2)\nR1 in a 10\nL1 a out 1m\nC1 out 0 1u\n"
    sim = await call("spice_simulate", {"analysis": "tran", "netlist": net, "outputs": "v(out)", "step": "2 us", "stop": "5 ms"})
    assert sim.ok, sim.error
    zeta = 5 * math.sqrt(1e-6 / 1e-3)
    expected = 100 * math.exp(-math.pi * zeta / math.sqrt(1 - zeta**2))
    assert expected == pytest.approx(60.468, abs=2e-3)
    r = await call("spice_measure", {"csv_file": str(artifact_path(sim, "result.csv")), "measurement": "overshoot", "column": "v(out)", "final_fraction": 0.1})
    assert r.ok and r.value.quantity == pytest.approx(expected, abs=0.1)


@needs_ngspice
async def test_rc_bandwidth_from_an_ngspice_ac_sweep():
    # fc = 1/(2 pi R C) = 159.15494 Hz; 50 points per decade: linear-in-dB interpolation error below 1e-4 relative; 5e-4 allowed.
    net = "rc\nV1 in 0 DC 0 AC 1\nR1 in out 1k\nC1 out 0 1u\n"
    sim = await call("spice_simulate", {"analysis": "ac", "netlist": net, "outputs": "v(out)", "start": "1 Hz", "stop": "100 kHz", "points": 50})
    assert sim.ok, sim.error
    r = await call("spice_measure", {"csv_file": str(artifact_path(sim, "result.csv")), "measurement": "bandwidth_3db", "column": "v(out)"})
    assert r.ok and r.value.quantity == pytest.approx(1 / (2 * math.pi * TAU), rel=5e-4)


async def test_a_signal_that_is_still_moving_at_the_end_is_flagged(tmp_path):
    # 3 tau of an RC step: the mean of the last 10 % (2.7..3 tau) is 1 - (e^-2.7 - e^-3) / 0.3 = 1 - (0.0672055 - 0.0497871) / 0.3 = 0.941939, the last sample
    # 1 - e^-3 = 0.950213, so they are 0.008274 apart = 0.878 % of the step 0.941939 (> 0.1 %, flagged);
    # a record of 20 tau ends 1e-9 away from its mean (not flagged).
    short = first_order(tmp_path, span=3 * TAU, n=600)
    r = await measure(short, "overshoot")
    assert r.ok and any("still moving" in w for w in r.warnings)
    assert r.data["last_sample_vs_steady_state_percent_of_step"] == pytest.approx(0.8784, abs=0.002)
    settled = await measure(first_order(tmp_path), "overshoot", final_fraction=0.05)
    assert settled.ok and not any("still moving" in w for w in settled.warnings)
