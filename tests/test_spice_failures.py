"""What happens when ngspice is missing, fails, lies or writes rubbish: a fake program stands in (see tests/fake_exe.py).  No real ngspice needed."""

import pytest

import exe_fixtures as F
from eng_toolkit.domains.spice import engine
from eng_toolkit.external_catalog import NGSPICE
from spice_fixtures import DIVIDER, call

OP = {"analysis": "op", "netlist": DIVIDER, "outputs": "v(in),v(mid)"}
GOOD_TABLE = " in v(in) v(mid)\n 5.0e+00 5.0e+00 2.5e+00 \n"


@pytest.fixture
def make(tmp_path, monkeypatch):
    def install(**config) -> F.Fake:
        return F.install_fake(tmp_path, monkeypatch, env_var=NGSPICE.env_var, name="ngspice_con", version_args=["-v"], version_text="ngspice-41 fake", **config)

    return install


async def test_a_good_table_is_read_by_position_and_the_log_warnings_are_passed_on(make):
    make(files={"table.csv": GOOD_TABLE, "ngspice.log": "Note: hello\nWarning: singular matrix:  check node b\nWarning: singular matrix:  check node b\n"})
    r = await call("spice_simulate", OP)
    assert r.ok, r.error
    assert r.values["v(in)"].quantity == 5.0 and r.values["v(mid)"].quantity == 2.5  # column 0 is ngspice's scale vector and is skipped
    assert r.warnings.count("Warning: singular matrix: check node b") == 1  # de-duplicated
    assert {a.path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for a in r.artifacts} >= {"deck.cir", "netlist.cir", "ngspice.log"}


async def test_ngspice_not_installed_is_engine_unavailable_and_the_check_still_works(tmp_path, monkeypatch):
    F.no_tool(monkeypatch, tmp_path, NGSPICE)
    monkeypatch.setattr("eng_toolkit.external._expand", lambda pattern: [])  # also hide the well-known locations
    r = await call("spice_simulate", OP)
    assert not r.ok and r.error.family == "engine.unavailable"
    assert "ngspice.sourceforge.io" in r.error.message and "ENG_TOOLKIT_NGSPICE" in r.error.message
    checked = await call("spice_check_netlist", {"netlist": DIVIDER})
    assert checked.ok and checked.data["runnable"] is True


async def test_a_nonzero_exit_is_a_failure_with_the_programs_words(make):
    make(exit=3, stderr="segmentation trouble")
    r = await call("spice_simulate", OP)
    assert not r.ok and r.error.family == "engine.failure"
    assert "code 3" in r.error.message and r.error.details["returncode"] == 3 and not r.values


async def test_an_error_line_in_the_log_is_a_failure_even_with_exit_zero_and_a_table(make):
    make(files={"table.csv": GOOD_TABLE, "ngspice.log": "Note: x\nError: no such vector zz\n  in term: v(zz)\n"})
    r = await call("spice_simulate", OP)
    assert not r.ok and r.error.family == "engine.failure"
    assert "no such vector zz" in r.error.message and not r.values


async def test_timestep_too_small_is_a_failure(make):
    make(files={"table.csv": GOOD_TABLE, "ngspice.log": "doAnalysis: Timestep too small; time = 1e-9, timestep = 1e-21: trouble with node out\n"})
    r = await call("spice_simulate", OP)
    assert not r.ok and r.error.family == "engine.failure" and "Timestep too small" in r.error.message


async def test_no_table_is_a_failure(make):
    make()
    r = await call("spice_simulate", OP)
    assert not r.ok and r.error.family == "engine.failure" and r.error.details["reason_code"] == "no_table"


@pytest.mark.parametrize(
    "table, code",
    [
        (" in v(in) v(mid)\n 5 5 nan\n", "non_finite"),
        (" in v(in) v(mid)\n 5 inf 2\n", "non_finite"),
        (" in v(in) v(mid)\n 5 -Infinity 2\n", "non_finite"),
        (" in v(in) v(mid)\n 5 5\n", "table_shape"),
        (" in v(in) v(mid)\n 5 5 2 9\n", "table_shape"),
        (" in v(in) v(mid)\n 5 5 abc\n", "table_value"),
        (" in v(in) v(mid)\n", "empty_table"),
        ("", "empty_table"),
    ],
)
async def test_a_bad_table_is_a_failure_and_no_nan_or_inf_reaches_the_client(make, table, code):
    make(files={"table.csv": table})
    r = await call("spice_simulate", OP)  # call() also refuses a payload with NaN or Infinity
    assert not r.ok and r.error.family == "engine.failure" and r.error.details["reason_code"] == code, r.error
    assert not r.values


async def test_a_table_with_too_many_rows_is_a_resource_limit(make, monkeypatch):
    monkeypatch.setattr(engine, "MAX_TABLE_ROWS", 3)
    make(files={"table.csv": " time v(in) v(mid)\n" + "".join(f" {k} 1 2\n" for k in range(5))})
    r = await call("spice_simulate", {"analysis": "tran", "netlist": DIVIDER, "outputs": "v(in),v(mid)", "step": "1 ms", "stop": "10 ms"})
    assert not r.ok and r.error.family == "engine.resource_limit"


async def test_a_run_over_the_time_limit_is_killed_and_reported(make, monkeypatch):
    monkeypatch.setattr(engine, "TIMEOUT_S", 1.0)
    fake = make(mode="sleep", sleep_s=30)
    r = await call("spice_simulate", OP)
    assert not r.ok and r.error.family == "engine.timeout"
    assert len(fake.runs()) == 1  # started once, never retried


async def test_huge_but_finite_ac_values_stay_json_safe(make):
    make(files={"table.csv": " frequency v(in) v(in)\n 1.0 1e300 1e300\n 2.0 1e300 1e300\n"})
    r = await call("spice_simulate", {"analysis": "ac", "netlist": DIVIDER, "outputs": "v(in)", "start": "1 Hz", "stop": "2 Hz", "points": 2, "sweep": "lin"})
    assert r.ok, r.error  # hypot(1e300, 1e300) = 1.4e300 is finite; call() already refused NaN and Infinity in the payload
    assert r.values["v(in).max"].quantity == pytest.approx(1.4142135623730951e300)


async def test_a_warning_in_the_middle_of_a_log_line_is_found(make):
    make(files={"table.csv": GOOD_TABLE, "ngspice.log": "Trying gmin =   1.0000E-03 Warning: singular matrix:  check node b\nNote: done\n"})
    r = await call("spice_simulate", OP)
    assert r.ok and "Warning: singular matrix: check node b" in r.warnings
