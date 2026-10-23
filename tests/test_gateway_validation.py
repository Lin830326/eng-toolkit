"""Second-review shared regressions, exercised through the in-process gateway."""

import json
from fractions import Fraction
from pathlib import Path

import pytest
from fastmcp import Client

from eng_toolkit.contract import ToolResult, inputs_hash
from eng_toolkit.gateway.server import DOMAINS, gateway
from eng_toolkit.units import Q_, UnitsError, parse_quantity, ureg
from eng_toolkit.workspace import Workspace


async def call(client, tool, arguments):
    reply = await client.call_tool(tool, arguments, raise_on_error=False)
    assert not reply.is_error, reply.content
    assert reply.structured_content is not None, reply.content
    result = ToolResult.model_validate(reply.structured_content)
    assert result.review_status == "unreviewed"
    json.dumps(result.model_dump(mode="json"), allow_nan=False)
    return result


def refused(result, family, pointer):
    assert not result.ok
    assert result.error.family == family
    assert result.error.details["param"] == pointer
    assert result.value is None and not result.warnings
    assert "traceback" not in result.model_dump_json().lower()


# Electric finding 6 and fatigue finding 7, with the exact reported arguments.
@pytest.mark.parametrize("tool,arguments,pointer,family", [
    *[("electric_" + tool, {param: 24}, "/" + param, "units.bare_number") for tool, param in (
        ("transformer", "rating"), ("induction_motor", "line_voltage"),
        ("pm_dc_motor", "voltage"), ("power_flow", "line_voltage"),
        ("drive_sizing", "load_inertia"), ("converter", "input_voltage"))],
    ("fatigue_sn", {"mode": "curve", "basquin_a": 1000}, "/basquin_a", "units.bare_number"),
    ("fatigue_rainflow", {"range_bins": 1.5}, "/range_bins", "input.invalid"),
    ("fatigue_strain_life", {"elastic_modulus": 200000}, "/elastic_modulus", "units.bare_number"),
    ("fatigue_fracture", {"crack_size": 1}, "/crack_size", "units.bare_number"),
    ("fatigue_material", {"hardness_hb": None}, "/hardness_hb", "input.invalid"),
    ("calc_cylinder_force", {}, "/bore", "input.missing"),
    ("toolkit_list_tools", {"unknown": 1}, "/unknown", "input.invalid"),
    ("fatigue_sn", {"mode": 123}, "/mode", "input.invalid"),
    ("fatigue_sn", {"points": 123}, "/points", "input.invalid"),
    ("electric_drive_sizing", {"profile": 123}, "/profile", "input.invalid"),
    ("electric_power_flow", {"fault_impedance": 123}, "/fault_impedance", "input.invalid"),
    ("calc_pipe_pressure_drop", {"roughness": 123}, "/roughness", "units.bare_number"),
    ("thermal_convection", {"expansion_coefficient": 123}, "/expansion_coefficient", "units.bare_number"),
    ("structures_truss", {"thermal_expansion": 123}, "/thermal_expansion", "units.bare_number"),
    ("geometry_measure", {"model_path": 123}, "/model_path", "input.invalid"),
    ("fatigue_sn", {"basquin_a": True}, "/basquin_a", "input.invalid"),
    ("fatigue_sn", {"unknown~/field": 1}, "/unknown~0~1field", "input.invalid"),
])
async def test_reported_argument_validation(tool, arguments, pointer, family):
    async with Client(gateway) as client:
        refused(await call(client, tool, arguments), family, pointer)


@pytest.mark.parametrize("domain", DOMAINS, ids=lambda domain: domain.name)
async def test_wrong_type_reaches_contract_in_every_domain(domain):
    async with Client(gateway) as client:
        tool = next(t for t in await client.list_tools() if t.name.startswith(domain.name + "_"))
        param = next(iter(tool.input_schema["properties"]))
        # Every published tool parameter is scalar, so an object is a type error.
        result = await call(client, tool.name, {param: {"nested": "wrong"}})
    refused(result, "input.invalid", "/" + param)
    assert tool.input_schema["properties"][param]["type"] in result.error.message


async def test_unknown_arguments_are_structured_for_every_registered_tool():
    async with Client(gateway) as client:
        for tool in await client.list_tools():
            result = await call(client, tool.name, {"unknown_argument": "wrong"})
            refused(result, "input.invalid", "/unknown_argument")


async def test_validation_does_not_echo_huge_or_surrogate_inputs():
    async with Client(gateway) as client:
        result = await call(client, "fatigue_rainflow", {"range_bins": "hostile\udfff" * 10000})
        refused(result, "input.invalid", "/range_bins")
        assert "integer" in result.error.message
        assert len(result.model_dump_json()) < 1500
        result = await call(client, "toolkit_list_tools", {"unknown" * 10000: "bad"})
        refused(result, "input.invalid", "/")
        assert len(result.model_dump_json()) < 1500


# Fatigue finding 5: aliases must be rejected before cancellation or delta substitution.
OFFSET_ALIASES = [name for name, definition in ureg._units.items()
                  if not definition.is_multiplicative
                  and ureg.get_dimensionality(definition.name) == ureg.get_dimensionality("K")]


@pytest.mark.parametrize("unit", ["degF", "degC", "celsius", "fahrenheit", "degree_Celsius", "degree_Fahrenheit"])
@pytest.mark.parametrize("compound", ["MPa*{unit}/K", "MPa*K/{unit}", "MPa*({unit}/K)^2", "MPa*{unit}/{unit}", "MPa*{unit}^0"])
async def test_compound_offset_gateway(unit, compound):
    async with Client(gateway) as client:
        result = await call(client, "fatigue_sn", {"mode": "curve", "basquin_a": "1 " + compound.format(unit=unit)})
    refused(result, "units.dimension_mismatch", "/basquin_a")


@pytest.mark.parametrize("unit", OFFSET_ALIASES + ["degCs", "degFs", "mdegC"])
@pytest.mark.parametrize("form", ["mapping", "string", "json"])
def test_all_registry_offset_aliases_and_quantity_forms(unit, form):
    raw = {"value": 1, "unit": "MPa*" + unit + "/K"}
    if form == "string":
        raw = "1 " + raw["unit"]
    elif form == "json":
        raw = json.dumps(raw)
    with pytest.raises(UnitsError) as caught:
        parse_quantity(raw, name="coefficient")
    assert caught.value.family == "units.dimension_mismatch"
    assert caught.value.details["param"] == "/coefficient"


async def test_reported_strain_life_offset_and_correct_delta_reference():
    args = dict(mode="cyclic", elastic_modulus="200 GPa*degF/K", stress_amplitude="100 MPa",
                cyclic_strength="1000 MPa", cyclic_exponent=.2)
    async with Client(gateway) as client:
        refused(await call(client, "fatigue_strain_life", args), "units.dimension_mismatch", "/elastic_modulus")
        for unit, factor in [("delta_degC", Fraction(1)), ("K", Fraction(1)), ("degR", Fraction(5, 9)), ("delta_degF", Fraction(5, 9))]:
            result = await call(client, "fatigue_strain_life", {**args, "elastic_modulus": f"200 GPa*{unit}/K"})
            assert result.ok, result.error
            # Ramberg-Osgood: sigma/E + (sigma/K')^(1/n'), computed with exact rationals.
            expected = Fraction(100, 200000) / factor + Fraction(100, 1000) ** 5
            assert result.values["strain_amplitude"].quantity == pytest.approx(float(expected), rel=1e-12)


@pytest.mark.parametrize("name", ["pipes/0/3", "/pipes/0/3"])
def test_compound_offset_keeps_internal_parameter_paths(name):
    with pytest.raises(UnitsError) as caught:
        parse_quantity({"value": 10, "unit": "m*degC/K"}, name=name)
    assert caught.value.family == "units.dimension_mismatch"
    assert caught.value.details["param"] == "/pipes/0/3"


async def test_compound_offset_record_path_through_gateway():
    async with Client(gateway) as client:
        result = await call(client, "fluids_pipe_network", {
            "nodes": "r,0 m,head,20 m;j,0 m,demand,1 L/s",
            "pipes": "p,r,j,10 m*degC/K,50 mm,0 mm,0",
        })
    refused(result, "units.dimension_mismatch", "/pipes/0/3")


@pytest.mark.parametrize("unit,value,kelvin", [("degC", 20, 293.15), ("celsius", 20, 293.15), ("degF", 68, 293.15), ("fahrenheit", 68, 293.15)])
def test_plain_absolute_temperatures_keep_the_offset(unit, value, kelvin):
    for raw in (f"{value} {unit}", {"value": value, "unit": unit}, Q_(value, unit)):
        assert parse_quantity(raw, name="temperature", dimension="temperature").to("K").magnitude == pytest.approx(kelvin)


async def test_absolute_temperature_still_works_through_gateway():
    async with Client(gateway) as client:
        result = await call(client, "fatigue_material", {"mode": "larson_miller", "temperature": "500 degC", "exposure_time": "1000 h"})
    assert result.ok, result.error
    # P = (500 + 273.15) * (20 + log10(1000)).
    assert result.values["larson_miller_parameter"].quantity == pytest.approx(773.15 * 23)


def strict_json(path):
    def invalid_constant(token):
        raise AssertionError(f"Nonstandard JSON number {token} in {path}")
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=invalid_constant)


# Fatigue finding 6, including the ignored growth_m input in evaluate mode.
@pytest.mark.parametrize("tool,args,param", [
    ("fatigue_material", {"mode": "hardness"}, "hardness_hb"),
    ("fatigue_fracture", {"mode": "evaluate", "crack_size": "1 mm", "stress": "100 MPa"}, "growth_m"),
    ("fatigue_sn", {"mode": "curve", "basquin_a": "1000 MPa"}, "basquin_b"),
    ("fatigue_rainflow", {"history": "[0,100,-100,0]", "sample_scale": "1 MPa"}, "repeats"),
    ("fatigue_strain_life", {"mode": "cyclic", "elastic_modulus": "200 GPa", "stress_amplitude": "100 MPa", "cyclic_strength": "1000 MPa"}, "cyclic_exponent"),
    ("electric_induction_motor", {}, "slip"),
])
@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
async def test_nonfinite_inputs_never_write_nonstandard_json(tool, args, param, token):
    async with Client(gateway) as client:
        result = await call(client, tool, {**args, param: token})
    job = Path(result.data["job_dir"])
    saved = strict_json(job / "inputs.json")
    assert saved[param] == token
    assert result.provenance.inputs_hash == inputs_hash(saved)
    for path in job.rglob("*.json"):
        strict_json(path)


def test_workspace_recursively_preserves_nonfinite_values_as_strings():
    job = Workspace().new_job("nested_inputs")
    artifact = job.write_json("nested.json", {"values": [float("nan"), {"limits": (float("inf"), float("-inf"))}]})
    assert strict_json(Path(artifact.path)) == {"values": ["NaN", {"limits": ["Infinity", "-Infinity"]}]}
    job.finalize({"data": {"value": float("nan")}})
    assert strict_json(job.dir / "result.json") == {"data": {"value": "NaN"}}
    strict_json(job.dir / "manifest.json")
