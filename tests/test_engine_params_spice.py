"""spice refusals that come out of the engine or out of a table name the argument to change (details.param, a JSON pointer).

Each case is the exact hostile input, sent through the in-process gateway.  ngspice-backed cases skip when ngspice is absent.
"""

import math

import pytest
from fastmcp import Client

from eng_toolkit.domains.spice import engine
from eng_toolkit.errors import ToolFailure
from eng_toolkit.gateway.server import gateway
from spice_fixtures import needs_ngspice


async def call(tool: str, **arguments):
    async with Client(gateway) as client:
        reply = await client.call_tool(tool, arguments, raise_on_error=False)
    payload = reply.structured_content
    assert payload is not None, reply.content
    return payload


def refusal(payload: dict, family: str) -> dict:
    assert payload["ok"] is False, payload
    assert payload["error"]["family"] == family, payload["error"]
    return payload["error"]["details"]


def write_csv(path, header, columns):
    rows = [",".join(header)] + [",".join(repr(float(v)) for v in row) for row in zip(*columns)]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return str(path)


# --------------------------------------------------------------------------- spice_simulate: ngspice says the circuit is wrong

TWO_SOURCES_ON_ONE_NODE = "two sources fighting\nV1 1 0 5\nV2 1 0 3\n"  # ngspice: "Transient op failed, timestep too small"


@needs_ngspice
async def test_simulate_an_error_ngspice_finds_in_the_circuit_points_at_netlist():
    details = refusal(await call("spice_simulate", analysis="op", netlist=TWO_SOURCES_ON_ONE_NODE), "engine.failure")
    assert details["param"] == "/netlist"
    assert details["reason_code"] == "ngspice_error"  # the existing details stay
    assert details["ngspice"]


@needs_ngspice
async def test_simulate_an_error_ngspice_finds_in_a_netlist_file_points_at_netlist_file(tmp_path):
    path = tmp_path / "fight.cir"
    path.write_text(TWO_SOURCES_ON_ONE_NODE, encoding="utf-8")
    details = refusal(await call("spice_simulate", analysis="op", netlist_file=str(path)), "engine.failure")
    assert details["param"] == "/netlist_file"
    assert details["reason_code"] == "ngspice_error"


async def test_simulate_a_diode_with_an_unknown_model_points_at_netlist():
    details = refusal(await call("spice_simulate", analysis="op", netlist="t\nV1 1 0 5\nD1 1 0 nosuchmodel\n"), "input.netlist_invalid")
    assert details["param"] == "/netlist"


@needs_ngspice
async def test_simulate_the_engine_own_failures_name_no_argument(monkeypatch):
    """No result table is the engine's problem, not something the caller gave: no param."""

    def no_table(*args, **kwargs):
        raise ToolFailure("engine.failure", "ngspice wrote no result table", {"reason_code": "no_table"})

    monkeypatch.setattr(engine, "read_table", no_table)
    details = refusal(await call("spice_simulate", analysis="op", netlist="t\nV1 1 0 5\nR1 1 0 1k\n"), "engine.failure")
    assert "param" not in details


@pytest.mark.parametrize("name", ["/start", "/stop", "/step"])
def test_a_non_finite_number_in_the_deck_names_the_argument_it_came_from(name):
    with pytest.raises(ToolFailure) as caught:
        engine.format_number(math.nan, name)
    assert caught.value.family == "input.invalid"
    assert caught.value.details["param"] == name
    with pytest.raises(ToolFailure) as caught:
        engine.format_number(math.inf)
    assert caught.value.details["param"] == "/netlist"  # a number from the circuit itself


# --------------------------------------------------------------------------- spice_measure


def ac_table(tmp_path, *, flat=False):
    freqs = [10.0 ** (k / 4) for k in range(0, 17)]
    gains = [1.0 + (0.0 if flat else k / 100) for k in range(len(freqs))]  # flat: no 3 dB drop anywhere; else it rises, so there is a step
    return write_csv(tmp_path / "ac.csv", ["frequency", "v(out).mag"], [freqs, gains])


def step_table(tmp_path, ys, name="step.csv"):
    return write_csv(tmp_path / name, ["time", "v(out)"], [[k * 1e-3 for k in range(len(ys))], ys])


async def test_measure_rise_time_on_an_ac_table_points_at_csv_file(tmp_path):
    details = refusal(await call("spice_measure", csv_file=ac_table(tmp_path), measurement="rise_time", column="v(out).mag"), "input.invalid")
    assert details["param"] == "/csv_file"
    assert details["first_column"] == "frequency"


async def test_measure_bandwidth_on_a_time_table_points_at_csv_file(tmp_path):
    path = step_table(tmp_path, [0.0, 0.2, 0.5, 0.8, 0.95, 1.0])
    details = refusal(await call("spice_measure", csv_file=path, measurement="bandwidth_3db", column="v(out)"), "input.invalid")
    assert details["param"] == "/csv_file"
    assert details["first_column"] == "time"


async def test_measure_a_gain_that_never_falls_points_at_csv_file(tmp_path):
    details = refusal(await call("spice_measure", csv_file=ac_table(tmp_path, flat=True), measurement="bandwidth_3db", column="v(out).mag"), "measure.not_found")
    assert details["param"] == "/csv_file"


async def test_measure_a_signal_with_no_step_points_at_csv_file(tmp_path):
    details = refusal(await call("spice_measure", csv_file=step_table(tmp_path, [1.0] * 8), measurement="rise_time", column="v(out)"), "measure.not_found")
    assert details["param"] == "/csv_file"


async def test_measure_a_falling_signal_asked_for_rise_time_points_at_csv_file(tmp_path):
    path = step_table(tmp_path, [1.0, 0.8, 0.5, 0.2, 0.05, 0.0, 0.0, 0.0, 0.0, 0.0])
    details = refusal(await call("spice_measure", csv_file=path, measurement="rise_time", column="v(out)"), "measure.not_found")
    assert details["param"] == "/csv_file"


async def test_measure_a_signal_that_has_not_settled_points_at_csv_file(tmp_path):
    ys = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 5.0]
    details = refusal(await call("spice_measure", csv_file=step_table(tmp_path, ys), measurement="settling_time", column="v(out)", final_fraction=0.05), "measure.not_found")
    assert details["param"] == "/csv_file"


async def test_measure_a_result_that_overflows_points_at_column(tmp_path):
    # 0.5 * (1e308 + 1e308) overflows to infinity inside the trapezoid sum: the measurement is not a finite number.
    path = step_table(tmp_path, [1e308] * 6)
    details = refusal(await call("spice_measure", csv_file=path, measurement="steady_state", column="v(out)"), "engine.failure")
    assert details["param"] == "/column"
