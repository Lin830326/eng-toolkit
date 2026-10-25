"""The thermal domain through a real MCP client against the gateway: registry, schemas, unit handling and hostile inputs."""

from eng_toolkit.errors import json_pointer
import json
import math
import re

import pytest
from fastmcp import Client

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains import all_domains
from eng_toolkit.gateway.server import gateway
from tests.thermal_testing import assert_finite

THERMAL = {"thermal_conduction", "thermal_exchanger", "thermal_fin", "thermal_transient", "thermal_radiation", "thermal_convection"}

WALL = dict(geometry="plane", layers="200 mm @ 0.72 W/(m*K); 100 mm @ 0.04 W/(m*K)", temperature_inner="20 degC", temperature_outer="-5 degC", h_inner="8 W/(m^2*K)", h_outer="25 W/(m^2*K)", contact_resistances="0.001 m^2*K/W", area="10 m^2")
PIPE = dict(geometry="cylinder", layers="5 mm @ 50 W/(m*K); 25 mm @ 0.04 W/(m*K)", inner_radius="25 mm", length="10 m", temperature_inner="150 degC", temperature_outer="20 degC", h_inner="1000 W/(m^2*K)", h_outer="10 W/(m^2*K)")
EXCH_RATING = dict(arrangement="shell_tube", hot_inlet_temperature="150 degC", cold_inlet_temperature="20 degC", hot_mass_flow="2 kg/s", hot_cp="2100 J/(kg*K)", cold_mass_flow="1.5 kg/s", cold_cp="4180 J/(kg*K)", ua="5000 W/K")
EXCH_SIZING = {**{k: v for k, v in EXCH_RATING.items() if k != "ua"}, "arrangement": "crossflow_unmixed", "duty": "200 kW", "overall_u": "500 W/(m^2*K)"}
FIN = dict(fin_type="pin", diameter="5 mm", length="50 mm", conductivity="200 W/(m*K)", h="25 W/(m^2*K)", base_temperature="100 degC", ambient_temperature="25 degC", tip="convective", fin_count=3, base_unfinned_area="0.002 m^2")
FIN_PRESCRIBED = {**FIN, "fin_type": "rectangular", "thickness": "3 mm", "width": "100 mm", "diameter": "", "tip": "prescribed_temperature", "tip_temperature": "60 degC"}
TRANSIENT_SPHERE = dict(model="sphere", initial_temperature="200 degC", ambient_temperature="20 degC", conductivity="10 W/(m*K)", density="1000 kg/m^3", specific_heat="1000 J/(kg*K)", h="100 W/(m^2*K)", size="0.1 m", time="1000 s", position=0.5)
TRANSIENT_CONTACT = dict(model="semi_infinite", initial_temperature="90 degC", ambient_temperature="30 degC", conductivity="4 W/(m*K)", density="1000 kg/m^3", specific_heat="1000 J/(kg*K)", time="100 s", depth="4 cm", surface_condition="contact", other_temperature="30 degC", other_conductivity="1 W/(m*K)", other_density="1000 kg/m^3", other_specific_heat="1000 J/(kg*K)", body_thickness="1 m")
TRANSIENT_LUMPED = dict(model="lumped", initial_temperature="500 degC", ambient_temperature="20 degC", conductivity="15 W/(m*K)", density="7800 kg/m^3", specific_heat="500 J/(kg*K)", h="200 W/(m^2*K)", volume="1e-4 m^3", surface_area="0.03 m^2", target_temperature="100 degC")
RADIATION = dict(configuration="parallel_plates", temperature_1="600 K", temperature_2="300 K", emissivity_1=0.8, emissivity_2=0.6, area="2 m^2", shields=1, shield_emissivity=0.5)
RADIATION_CYL = dict(configuration="concentric_cylinders", temperature_1="500 K", temperature_2="300 K", emissivity_1=0.5, emissivity_2=0.8, inner_radius="50 mm", outer_radius="100 mm", length="2 m")
CONV_FORCED = dict(correlation="gnielinski", length="50 mm", conductivity="0.6 W/(m*K)", density="1000 kg/m^3", dynamic_viscosity="1e-3 Pa*s", specific_heat="3000 J/(kg*K)", velocity="1 m/s", pipe_length="2 m", viscosity_surface="1e-3 Pa*s")
CONV_NATURAL = dict(correlation="natural_vertical_plate", length="0.5 m", conductivity="0.026 W/(m*K)", density="1.2 kg/m^3", dynamic_viscosity="1.8e-5 Pa*s", prandtl=0.71, surface_temperature="80 degC", ambient_temperature="20 degC", expansion_coefficient="0.003 1/K")

CASES = [
    ("thermal_conduction", WALL), ("thermal_conduction", PIPE), ("thermal_exchanger", EXCH_RATING), ("thermal_exchanger", EXCH_SIZING),
    ("thermal_fin", FIN), ("thermal_fin", FIN_PRESCRIBED), ("thermal_transient", TRANSIENT_SPHERE), ("thermal_transient", TRANSIENT_CONTACT),
    ("thermal_transient", TRANSIENT_LUMPED), ("thermal_radiation", RADIATION), ("thermal_radiation", RADIATION_CYL),
    ("thermal_convection", CONV_FORCED), ("thermal_convection", CONV_NATURAL),
]


def _no_constants(name: str):
    raise AssertionError(f"the response contains the JSON constant {name}")


async def _call(client: Client, tool: str, args: dict):
    result = await client.call_tool(tool, args, raise_on_error=False)
    assert not result.is_error, (tool, args, result.content)
    for block in result.content:  # the raw JSON text must be strict JSON: no NaN / Infinity
        json.loads(block.text, parse_constant=_no_constants)
    payload = result.structured_content
    assert payload is not None
    parsed = ToolResult.model_validate(payload)
    assert_finite(payload)
    assert parsed.review_status == "unreviewed"
    assert parsed.provenance.engine == "eng_toolkit.thermal"
    return parsed


def test_registry_has_at_most_six_thermal_tools():
    domains = {d.name: d for d in all_domains()}
    assert "thermal" in domains
    assert set(domains["thermal"].tools) == {"conduction", "exchanger", "fin", "transient", "radiation", "convection"}
    assert len(domains["thermal"].tools) <= 6
    assert domains["thermal"].engines == ("pint",)


@pytest.mark.asyncio
async def test_tools_are_listed_with_basic_parameter_types_and_descriptions():
    async with Client(gateway) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert THERMAL <= set(tools)
    for name in THERMAL:
        assert tools[name].description and len(tools[name].description) < 1200
        for pname, prop in tools[name].input_schema.get("properties", {}).items():
            assert prop.get("type") in {"string", "number", "integer", "boolean"}, (name, pname, prop)
            assert prop.get("description"), (name, pname)


@pytest.mark.asyncio
async def test_toolkit_lists_the_thermal_domain():
    async with Client(gateway) as client:
        r = await client.call_tool("toolkit_list_tools", {})
    domains = {d["name"]: d for d in ToolResult.model_validate(r.structured_content).data["domains"]}
    assert set(domains["thermal"]["tools"]) == THERMAL


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, args", CASES, ids=[f"{t}-{i}" for i, (t, _) in enumerate(CASES)])
async def test_every_tool_works_over_mcp_and_returns_the_contract(tool, args):
    async with Client(gateway) as client:
        r = await _call(client, tool, args)
    assert r.ok, r.error
    assert r.value is not None and r.values
    assert r.provenance.engine_versions.get("pint")
    assert r.artifacts and r.artifacts[0].sha256
    assert r.warnings  # every successful result carries its scope limits


@pytest.mark.asyncio
async def test_structured_errors_over_mcp():
    async with Client(gateway) as client:
        bare = await _call(client, "thermal_conduction", {**WALL, "temperature_inner": "20"})
        delta = await _call(client, "thermal_conduction", {**WALL, "temperature_inner": "20 delta_degC"})
        wrong = await _call(client, "thermal_radiation", {**RADIATION, "area": "2 kg"})
        zero = await _call(client, "thermal_fin", {**FIN, "length": "0 mm"})
        absurd = await _call(client, "thermal_exchanger", {**EXCH_RATING, "ua": "1e308 W/K"})
        mismatch = await _call(client, "thermal_conduction", {**WALL, "contact_resistances": "1 m^2*K/W; 2 m^2*K/W"})
    assert (bare.ok, bare.error.family, bare.error.details["param"]) == (False, "units.bare_number", "/temperature_inner")
    assert (delta.error.family, delta.error.details["param"]) == ("input.invalid", "/temperature_inner")
    assert wrong.error.family == "units.dimension_mismatch" and wrong.error.details["param"] == "/area"
    assert zero.error.family == "input.invalid" and zero.error.details["param"] == "/length"
    assert absurd.error.family == "input.out_of_range" and absurd.error.details["param"] == "/ua"
    assert mismatch.error.family == "input.invalid" and mismatch.error.details["param"] == "/contact_resistances"
    for r in (bare, delta, wrong, zero, absurd, mismatch):
        assert r.value is None and r.warnings == [] and r.review_status == "unreviewed"


@pytest.mark.asyncio
async def test_degc_rate_and_json_quantities_over_mcp():
    async with Client(gateway) as client:
        k = await _call(client, "thermal_conduction", {**WALL, "layers": "100 mm @ 0.04 W/(m*K)", "contact_resistances": ""})
        c = await _call(client, "thermal_conduction", {**WALL, "layers": "100 mm @ 0.04 W/(m*degC)", "contact_resistances": ""})
        j = await _call(client, "thermal_conduction", {**WALL, "layers": '[{"thickness": {"value": 100, "unit": "mm"}, "conductivity": {"value": 0.04, "unit": "W/(m*degC)"}}]', "contact_resistances": ""})
        temp_json = await _call(client, "thermal_radiation", {**RADIATION, "temperature_1": '{"value": 600, "unit": "K"}', "shields": 0})
        temp_k = await _call(client, "thermal_radiation", {**RADIATION, "shields": 0})
    assert k.ok and c.ok and j.ok
    assert math.isclose(c.value.quantity, k.value.quantity, rel_tol=1e-12) and math.isclose(j.value.quantity, k.value.quantity, rel_tol=1e-12)
    assert math.isclose(temp_json.value.quantity, temp_k.value.quantity, rel_tol=1e-12)


# ---------------------------------------------------------------------------------------------------- hostile inputs
HOSTILE_TEXT = [
    "", "0", "nan", "inf mm", "1e999 m", "-5 m", "5 parsec", "{}", '{"value": 1e308, "unit": "m"}', "💥 5 m", "20 delta_degC", "-300 degC",
]
HOSTILE_FLOAT = [0.0, -1.0, 1e308, 1e-308]
HOSTILE_INT = [0, -1, 10**9]
_NUMBER_UNIT = re.compile(r"^\s*([-+0-9.eE]+)\s+(.+)$")


def _right_unit_absurd(value: str) -> list[str]:
    """Absurd magnitudes with the unit the parameter really has: '5 mm' -> '1e308 mm', '-1e308 mm', ..."""
    m = _NUMBER_UNIT.match(value)
    if not m:
        return []
    unit = m.group(2)
    return [f"{n} {unit}" for n in ("1e308", "-1e308", "1e-308", "0")]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, base", CASES, ids=[f"{t}-{i}" for i, (t, _) in enumerate(CASES)])
async def test_hostile_values_are_refused_cleanly_or_give_a_finite_result(tool, base):
    calls = 0
    async with Client(gateway) as client:
        for param, original in base.items():
            if isinstance(original, bool):
                candidates = [True, False]
            elif isinstance(original, int):
                candidates = HOSTILE_INT
            elif isinstance(original, float):
                candidates = HOSTILE_FLOAT
            else:
                candidates = HOSTILE_TEXT + _right_unit_absurd(original)
            for value in candidates:
                result = await _call(client, tool, {**base, param: value})
                calls += 1
                if result.ok:
                    assert result.value is not None and math.isfinite(result.value.quantity)
                    for q in result.values.values():
                        assert math.isfinite(q.quantity), (tool, param, value)
                else:
                    assert result.error.family in {"input.invalid", "input.out_of_range", "input.missing", "units.bare_number", "units.parse_error", "units.dimension_mismatch"}, (param, value, result.error)
                    assert result.value is None and not result.values
    assert calls > 50


@pytest.mark.asyncio
async def test_absurd_magnitudes_with_the_right_unit_are_out_of_range_like_calc_and_mech():
    async with Client(gateway) as client:
        for tool, args, param in (
            ("thermal_conduction", {**WALL, "area": "1e308 m^2"}, "area"),
            ("thermal_conduction", {**WALL, "layers": "1e308 mm @ 0.72 W/(m*K)", "contact_resistances": ""}, "layers[1].thickness"),
            ("thermal_exchanger", {**EXCH_RATING, "hot_mass_flow": "1e308 kg/s"}, "hot_mass_flow"),
            ("thermal_fin", {**FIN, "h": "1e308 W/(m^2*K)"}, "h"),
            ("thermal_transient", {**TRANSIENT_SPHERE, "time": "1e308 s"}, "time"),
            ("thermal_radiation", {**RADIATION, "temperature_1": "1e308 K"}, "temperature_1"),
            ("thermal_convection", {**CONV_FORCED, "velocity": "1e308 m/s"}, "velocity"),
        ):
            r = await _call(client, tool, args)
            assert not r.ok and r.error.family == "input.out_of_range" and r.error.details["param"] == json_pointer(param), (tool, r.error)
