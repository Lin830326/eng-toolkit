"""The control domain through a real MCP client against the gateway: registry, schemas, strict JSON and hostile inputs."""

import json
import math

import pytest
from fastmcp import Client

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains import all_domains
from eng_toolkit.gateway.server import gateway
from tests.control_testing import assert_finite

CONTROL = {"control_analyze", "control_response", "control_frequency", "control_feedback", "control_pid_tune", "control_discretize"}

GOOD = {
    "control_analyze": {"numerator": "[1]", "denominator": "s^2 + 3 s + 2"},
    "control_response": {"numerator": "16", "denominator": "[1, 4, 16]"},
    "control_frequency": {"numerator": "[1]", "denominator": "s (s+1)(s+2)"},
    "control_feedback": {"plant_numerator": "2", "plant_denominator": "s (s+1)(s+2)"},
    "control_pid_tune": {"gain": 2.0, "time_constant": "10 s", "delay": "2 s", "rule": "ziegler_nichols", "controller": "PID"},
    "control_discretize": {"numerator": "3", "denominator": "[1, 2]", "sample_time": "0.1 s", "method": "tustin"},
}


def _no_constants(name: str):
    raise AssertionError(f"the response contains the JSON constant {name}")


async def _call(client: Client, tool: str, args: dict, *, expect_ok: bool | None = True):
    result = await client.call_tool(tool, args, raise_on_error=False)
    if result.is_error:
        assert expect_ok is not True, (tool, args, result.content)
        return None  # refused by the schema layer before the tool ran: acceptable for a hostile call
    for block in result.content:  # the raw text must be strict JSON: no NaN / Infinity
        json.loads(block.text, parse_constant=_no_constants)
    parsed = ToolResult.model_validate(result.structured_content)
    assert_finite(result.structured_content)
    assert parsed.review_status == "unreviewed" and parsed.provenance.engine == "eng_toolkit.control"
    if expect_ok is True:
        assert parsed.ok, parsed.error
    return parsed


def test_registry_has_at_most_six_control_tools_and_only_numpy_as_engine():
    domains = {d.name: d for d in all_domains()}
    assert "control" in domains
    assert set(domains["control"].tools) == {"analyze", "response", "frequency", "feedback", "pid_tune", "discretize"}
    assert len(domains["control"].tools) <= 6
    assert domains["control"].engines == ("numpy",)


@pytest.mark.asyncio
async def test_tools_are_listed_with_basic_types_compact_descriptions_and_no_unions():
    async with Client(gateway) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert CONTROL <= set(tools)
    for name in CONTROL:
        assert 40 < len(tools[name].description) < 1100, name
        for pname, prop in tools[name].input_schema.get("properties", {}).items():
            assert prop.get("type") in {"string", "number", "integer", "boolean"}, (name, pname, prop)
            assert "anyOf" not in prop and "oneOf" not in prop
            assert prop.get("description"), (name, pname)
    # a model must be able to see how to write the polynomial
    assert "descending" in tools["control_analyze"].description.lower()


@pytest.mark.asyncio
async def test_toolkit_lists_the_control_domain():
    async with Client(gateway) as client:
        r = await client.call_tool("toolkit_list_tools", {})
    text = json.dumps(r.structured_content)
    assert all(name in text for name in CONTROL)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", sorted(GOOD))
async def test_every_tool_answers_through_the_client_with_strict_json(tool):
    async with Client(gateway) as client:
        parsed = await _call(client, tool, GOOD[tool])
    assert parsed.ok and parsed.value is not None and parsed.values
    assert parsed.warnings and "Classical linear time-invariant" in parsed.warnings[0]


@pytest.mark.asyncio
async def test_csv_artifacts_are_written_and_hashed():
    async with Client(gateway) as client:
        r = await _call(client, "control_response", GOOD["control_response"])
        f = await _call(client, "control_frequency", GOOD["control_frequency"])
    for res, name, header in ((r, "response.csv", "time_s,input,output"), (f, "bode.csv", "frequency_rad_s,magnitude_db,phase_deg")):
        art = next(a for a in res.artifacts if a.path.endswith(name))
        assert art.format == "csv" and len(art.sha256) == 64
        with open(art.path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        assert lines[0] == header and len(lines) > 100 and all(math.isfinite(float(x)) for x in lines[-1].split(","))


HOSTILE = [
    # (tool, arguments, expected error family)
    ("control_analyze", {"numerator": "1", "denominator": "[0, 1, 2]"}, "input.invalid"),  # zero leading coefficient
    ("control_analyze", {"numerator": "1", "denominator": "[]"}, "input.invalid"),
    ("control_analyze", {"numerator": "[]", "denominator": "[1, 1]"}, "input.invalid"),
    ("control_analyze", {"numerator": "1", "denominator": "[1, 'a']"}, "input.invalid"),
    ("control_analyze", {"numerator": "1", "denominator": "abc"}, "input.invalid"),
    ("control_analyze", {"numerator": "1", "denominator": "[1, NaN]"}, "input.invalid"),
    ("control_analyze", {"numerator": "1", "denominator": "[1, Infinity]"}, "input.invalid"),
    ("control_analyze", {"numerator": "1", "denominator": "[1, -Infinity]"}, "input.invalid"),
    ("control_analyze", {"numerator": "1", "denominator": "[1, 1e999]"}, "input.invalid"),
    ("control_analyze", {"numerator": "1", "denominator": "[1, 1e308]"}, "input.out_of_range"),
    ("control_analyze", {"numerator": "1e308", "denominator": "[1, 1]"}, "input.out_of_range"),
    ("control_analyze", {"numerator": "1", "denominator": "[1e-300, 1]"}, "input.out_of_range"),
    ("control_analyze", {"numerator": "1", "denominator": "[" + ",".join(["1"] * 42) + "]"}, "input.out_of_range"),  # order 41
    ("control_analyze", {"numerator": "1", "denominator": "s^41 + 1"}, "input.out_of_range"),
    ("control_analyze", {"numerator": "1", "denominator": "(s+1)^21 (s+2)^20"}, "input.out_of_range"),
    ("control_analyze", {"numerator": "1", "denominator": "s" * 100_000}, "input.out_of_range"),
    ("control_analyze", {"numerator": "1", "denominator": "[" + "1," * 50_000 + "1]"}, "input.out_of_range"),
    ("control_analyze", {"numerator": "1", "denominator": "__import__('os').system('echo pwned')"}, "input.invalid"),
    ("control_analyze", {"numerator": "1", "denominator": "s + abs(s)"}, "input.invalid"),
    ("control_analyze", {"numerator": "1", "denominator": "sin(s) + 1"}, "input.invalid"),
    ("control_analyze", {"numerator": "1", "denominator": "s.__class__"}, "input.invalid"),
    ("control_analyze", {"numerator": "1", "denominator": "s__"}, "input.invalid"),
    ("control_analyze", {"numerator": "1", "denominator": "(lambda: 1)()"}, "input.invalid"),
    ("control_analyze", {"numerator": "1", "denominator": "open('/etc/passwd')"}, "input.invalid"),
    ("control_analyze", {"numerator": "1", "denominator": "s; import os"}, "input.invalid"),
    ("control_analyze", {"numerator": "1", "denominator": "s ** 2 ** 3"}, "input.invalid"),
    ("control_analyze", {"numerator": "1", "denominator": "(" * 5000 + "s" + ")" * 5000}, "input.out_of_range"),
    ("control_analyze", {"numerator": "1", "denominator": "[" * 2400 + "]" * 2400}, "input.invalid"),
    ("control_analyze", {"numerator": "1", "denominator": "[1, 1]", "sample_time": "0.1 s"}, "input.invalid"),  # sample time with a continuous model
    ("control_analyze", {"numerator": "1", "denominator": "[1, 1]", "domain": "discrete"}, "input.missing"),
    ("control_analyze", {"numerator": "1", "denominator": "[1, 1]", "domain": "discrete", "sample_time": "-0.1 s"}, "input.invalid"),
    ("control_analyze", {"numerator": "s + 1", "denominator": "z + 1", "domain": "discrete", "sample_time": "1 s"}, "input.invalid"),
    ("control_response", {"numerator": "[1, 0, 0]", "denominator": "[1, 1]"}, "input.invalid"),  # improper
    ("control_response", {"numerator": "1", "denominator": "[1, 1]", "samples": 1_000_000_000}, "input.out_of_range"),  # 1e9 samples
    ("control_response", {"numerator": "1", "denominator": "[1, 1]", "samples": 100_001}, "input.out_of_range"),
    ("control_response", {"numerator": "1", "denominator": "[1, 1]", "t_final": "1" + "0" * 400 + " s"}, "input.out_of_range"),
    ("control_response", {"numerator": "1", "denominator": "[1, 1]", "t_final": "x" * 100_000}, "input.out_of_range"),
    ("control_response", {"numerator": "1", "denominator": "[1, 1]", "t_final": "-1 s"}, "input.invalid"),
    ("control_response", {"numerator": "1", "denominator": "[1, 1]", "input_type": "chirp"}, "input.invalid"),
    ("control_response", {"state_a": "[[1e308]]", "state_b": "[1]", "state_c": "[1]"}, "input.out_of_range"),
    ("control_response", {"state_a": "[[0, 1], [-1, -1]]", "state_b": "[0, 1, 2]", "state_c": "[1, 0]"}, "input.invalid"),
    ("control_response", {"state_a": "[[NaN]]", "state_b": "[1]", "state_c": "[1]"}, "input.invalid"),
    ("control_response", {"state_a": "{'a': 1}", "state_b": "[1]", "state_c": "[1]"}, "input.invalid"),
    ("control_frequency", {"numerator": "[1, 0, 0]", "denominator": "[1, 1]"}, "input.invalid"),
    ("control_frequency", {"numerator": "1", "denominator": "[1, 1]", "points": 1_000_000_000}, "input.out_of_range"),
    ("control_frequency", {"numerator": "1", "denominator": "[1, 1]", "frequency_max": 1e300}, "input.out_of_range"),
    ("control_frequency", {"numerator": "1", "denominator": "[1, 1]", "frequency_min": 5.0, "frequency_max": 1.0}, "input.invalid"),
    ("control_feedback", {"plant_numerator": "1", "plant_denominator": "[1, 1]", "controller_numerator": "[1, 0, 0]"}, "input.invalid"),
    ("control_feedback", {"plant_numerator": "1", "plant_denominator": "[1]", "sensor_numerator": "-1"}, "input.invalid"),
    ("control_feedback", {"plant_numerator": "1", "plant_denominator": "(s+1)^20", "controller_denominator": "(s+1)^21"}, "input.out_of_range"),
    ("control_feedback", {"plant_numerator": "1", "plant_denominator": "[1, 1]", "sensor_denominator": "[0, 0]"}, "input.invalid"),
    ("control_pid_tune", {"gain": 0.0, "time_constant": "10 s", "delay": "2 s"}, "input.invalid"),
    ("control_pid_tune", {"gain": 1e300, "time_constant": "10 s", "delay": "2 s"}, "input.out_of_range"),
    ("control_pid_tune", {"gain": 2.0, "time_constant": "10", "delay": "2 s"}, "units.bare_number"),
    ("control_pid_tune", {"gain": 2.0, "time_constant": "10 s", "delay": "0 s"}, "input.invalid"),
    ("control_pid_tune", {"gain": 2.0, "time_constant": "1e300 s", "delay": "1 s"}, "input.out_of_range"),
    ("control_pid_tune", {"gain": 2.0, "time_constant": "10 s", "delay": "2 s", "pade_order": 100}, "input.out_of_range"),
    ("control_pid_tune", {"gain": 2.0, "time_constant": "10 s", "delay": "2 s", "rule": "imc", "controller": "P"}, "input.invalid"),
    ("control_discretize", {"numerator": "1", "denominator": "[1, 1]", "sample_time": "-0.1 s"}, "input.invalid"),
    ("control_discretize", {"numerator": "1", "denominator": "[1, 1]", "sample_time": "0 s"}, "input.invalid"),
    ("control_discretize", {"numerator": "1", "denominator": "[1, 1]", "sample_time": "1e300 s"}, "input.out_of_range"),
    ("control_discretize", {"numerator": "[1, 0, 0]", "denominator": "[1, 1]", "sample_time": "0.1 s"}, "input.invalid"),
    ("control_discretize", {"numerator": "1", "denominator": "[1, 1]", "sample_time": "0.1 s", "method": "tustin", "prewarp_frequency": 1e300}, "input.out_of_range"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, args, family", HOSTILE, ids=[f"{t}-{i}" for i, (t, _, _) in enumerate(HOSTILE)])
async def test_hostile_inputs_are_refused_cleanly(tool, args, family):
    async with Client(gateway) as client:
        parsed = await _call(client, tool, args, expect_ok=False)
    assert parsed is not None, "the call was rejected by the schema layer; it should reach the tool and be refused there"
    assert parsed.ok is False and parsed.value is None and parsed.error is not None
    assert parsed.error.family == family, (tool, parsed.error)
    assert len(parsed.error.message) < 1500  # a huge input is never echoed back
    assert parsed.warnings == [] and parsed.provenance.engine == "eng_toolkit.control"


@pytest.mark.asyncio
async def test_non_finite_numbers_in_number_parameters_never_succeed():
    async with Client(gateway) as client:
        for bad in (float("nan"), float("inf"), -float("inf")):
            for tool, key in (("control_frequency", "frequency_min"), ("control_pid_tune", "gain"), ("control_response", "settling_tolerance"), ("control_discretize", "prewarp_frequency")):
                args = {**GOOD[tool], key: bad}
                if tool == "control_pid_tune":
                    args["gain"] = bad
                try:
                    result = await client.call_tool(tool, args, raise_on_error=False)
                except Exception:  # the transport itself may refuse a non-JSON number
                    continue
                if result.is_error:
                    continue
                parsed = ToolResult.model_validate(result.structured_content)
                assert parsed.ok is False, (tool, key, bad)
                assert_finite(result.structured_content)


@pytest.mark.asyncio
async def test_an_unstable_system_is_reported_as_unbounded_not_failed():
    async with Client(gateway) as client:
        r = await _call(client, "control_response", {"numerator": "1", "denominator": "[1, -50]", "t_final": "100 s"})
        a = await _call(client, "control_analyze", {"numerator": "1", "denominator": "[1, 0, -1]"})
        s = await _call(client, "control_response", {"numerator": "1", "denominator": "[1, -1]"})
    assert r.data["metrics"]["settling_time"] == "unbounded"
    assert a.data["stability"]["verdict"] == "unstable"
    assert s.data["metrics"]["overshoot_percent"] == "unbounded" and "settling_time" not in s.values


@pytest.mark.asyncio
async def test_extreme_but_legal_inputs_stay_finite():
    cases = [
        ("control_response", {"numerator": "1e12", "denominator": "[1e12, 1e-12]"}),  # time constant 1e24 s
        ("control_response", {"numerator": "1e-12", "denominator": "[1, 1e12]"}),
        ("control_frequency", {"numerator": "1e12", "denominator": "[1e-12, 1e12, 1e-12]"}),
        ("control_frequency", {"numerator": "1", "denominator": "s^40 + 1"}),
        ("control_analyze", {"numerator": "1e12", "denominator": "[1e-12, 0, 1e12]"}),
        ("control_feedback", {"plant_numerator": "1e12", "plant_denominator": "[1, 1e12]"}),
        ("control_discretize", {"numerator": "1e12", "denominator": "[1, 1e12]", "sample_time": "1e-9 s"}),
        ("control_discretize", {"numerator": "1", "denominator": "[1, 1e-12]", "sample_time": "1e9 s"}),
        ("control_pid_tune", {"gain": 1e6, "time_constant": "1e7 s", "delay": "1e7 s"}),
        ("control_pid_tune", {"gain": 1e-6, "time_constant": "1e-6 s", "delay": "1e-6 s", "rule": "ziegler_nichols", "controller": "PID", "pade_order": 10}),
    ]
    async with Client(gateway) as client:
        for tool, args in cases:
            parsed = await _call(client, tool, args, expect_ok=None)  # either a clean result or a clean refusal; never NaN/Infinity
            assert parsed is not None, (tool, args)
            if not parsed.ok:
                assert parsed.error.family in {"input.out_of_range", "input.invalid"}, (tool, args, parsed.error)


@pytest.mark.asyncio
async def test_order_forty_runs_through_every_tool_that_accepts_it():
    den = "[" + ",".join(["1"] * 41) + "]"
    async with Client(gateway) as client:
        a = await _call(client, "control_analyze", {"numerator": "1", "denominator": den}, expect_ok=None)
        f = await _call(client, "control_frequency", {"numerator": "1", "denominator": den}, expect_ok=None)
        r = await _call(client, "control_response", {"numerator": "1", "denominator": den, "samples": 300}, expect_ok=None)
        d = await _call(client, "control_discretize", {"numerator": "1", "denominator": den, "sample_time": "0.1 s"}, expect_ok=None)
    assert a.ok and a.values["order"].quantity == 40
    for res in (f, r, d):
        assert res.ok or res.error.family in {"input.out_of_range", "input.invalid"}
