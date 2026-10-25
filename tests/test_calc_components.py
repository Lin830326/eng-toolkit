"""calc.orifice_flow / pump_power / accumulator_precharge / cylinder_speed checked against hand calculations."""

from eng_toolkit.errors import json_pointer
import json
import math

import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.calc.components import accumulator_precharge, cylinder_speed, orifice_flow, pump_power

PSI = 6894.757293168  # Pa per psi (definition: lbf / in^2)
US_GALLON_M3 = 3.785411784e-3  # exact definition


def _call(tool, **kwargs) -> ToolResult:
    fn = getattr(tool, "fn", tool)
    result = fn(**kwargs)
    assert isinstance(result, ToolResult)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    assert result.review_status == "unreviewed"
    return result


def _fails(tool, family: str, param: str | None = None, **kwargs) -> ToolResult:
    r = _call(tool, **kwargs)
    assert r.ok is False and r.value is None, r.values
    assert r.error.family == family, r.error
    if param is not None:
        assert r.error.details.get("param") == json_pointer(param), r.error
    assert r.warnings == []  # a failed call carries no result-scope warnings
    return r


# --------------------------------------------------------------------------- orifice_flow
# Hand calculation: d = 5 mm, dp = 100 bar, rho = 870 kg/m^3, Cd = 0.62
#   A  = pi/4 * 0.005^2          = 1.963495e-5 m^2
#   sqrt(2 dp / rho) = sqrt(2e7 / 870) = sqrt(22 988.51) = 151.6196 m/s
#   Q  = 0.62 * 1.963495e-5 * 151.6196 = 1.845767e-3 m^3/s = 110.7460 L/min
#   v  = Q / A = 0.62 * 151.6196 = 94.00416 m/s
#   Re = 4 rho Q / (pi d mu) with mu = 0.04 Pa s = 4*870*1.845767e-3 / (pi*0.005*0.04) = 10 222.95
ORIFICE = dict(diameter="5 mm", density="870 kg/m^3", pressure_drop="100 bar")
Q_LMIN = 110.74603886498


def test_orifice_flow_from_pressure_drop():
    r = _call(orifice_flow, **ORIFICE, viscosity="40 cP")
    assert r.ok, r.error
    assert r.value.unit == "liter / minute"
    assert math.isclose(r.value.quantity, Q_LMIN, rel_tol=1e-9)
    assert math.isclose(r.values["orifice_velocity"].quantity, 94.00415740, rel_tol=1e-9)
    assert math.isclose(r.values["orifice_area"].quantity, 19.634954085, rel_tol=1e-9)
    assert math.isclose(r.values["pressure_drop"].quantity, 100.0, rel_tol=1e-12)
    assert math.isclose(r.values["reynolds_orifice"].quantity, 10222.95, rel_tol=1e-6)
    assert r.data["solved_for"] == "flow_rate"
    assert not any("Reynolds" in w for w in r.warnings)  # Re > 1e4: constant Cd is fine
    assert any("cavitation" in w for w in r.warnings)  # standing scope warning


def test_orifice_pressure_drop_from_flow_is_the_inverse():
    # dp = rho/2 * (Q / (Cd A))^2 with Q = 110.74604 L/min -> 100 bar again
    r = _call(orifice_flow, diameter="5 mm", density="870 kg/m^3", flow_rate=f"{Q_LMIN} L/min", pressure_unit="bar")
    assert r.ok, r.error
    assert r.data["solved_for"] == "pressure_drop"
    assert r.value.unit == "bar"
    assert math.isclose(r.value.quantity, 100.0, rel_tol=1e-8)


def test_orifice_flow_unit_conversions_give_the_same_result():
    # the same physical inputs spelled in inch / g/cm^3 / psi must give the same flow
    base = _call(orifice_flow, **ORIFICE)
    other = _call(
        orifice_flow,
        diameter="0.19685039370078738 inch",  # 5 mm / 25.4
        density="0.87 g/cm^3",
        pressure_drop="1450.377377302 psi",  # 1e7 Pa / 6894.757293168
    )
    assert other.ok, other.error
    assert math.isclose(other.value.quantity, base.value.quantity, rel_tol=1e-8)
    gal = _call(orifice_flow, **ORIFICE, flow_unit="gal/min")
    # 110.74604 L/min / 3.785411784 L/gal = 29.25596 gal/min
    assert math.isclose(gal.value.quantity, Q_LMIN / 3.785411784, rel_tol=1e-9)
    assert gal.value.unit == "gallon / minute"


def test_orifice_warns_when_reynolds_is_low():
    # mu = 1 Pa s -> Re = 4*870*1.845767e-3/(pi*0.005*1) = 408.9 < 1e4
    r = _call(orifice_flow, **ORIFICE, viscosity="1 Pa*s")
    assert r.ok
    assert math.isclose(r.values["reynolds_orifice"].quantity, 408.918, rel_tol=1e-5)
    assert any("Reynolds" in w and "overestimated" in w for w in r.warnings)


def test_orifice_warnings_for_gas_unusual_cd_and_no_viscosity():
    r = _call(orifice_flow, diameter="5 mm", density="1.2 kg/m^3", pressure_drop="1 bar", discharge_coeff=0.3)
    assert r.ok
    assert any("gas" in w for w in r.warnings)
    assert any("Cd = 0.3 is unusual" in w for w in r.warnings)
    assert any("viscosity not given" in w for w in r.warnings)


@pytest.mark.parametrize(
    ("override", "family", "param"),
    [
        ({"pressure_drop": "100 bar", "flow_rate": "40 L/min"}, "input.invalid", None),  # both
        ({"pressure_drop": ""}, "input.missing", None),  # neither
        ({"diameter": "5"}, "units.bare_number", "diameter"),
        ({"diameter": "0 mm"}, "input.invalid", "diameter"),
        ({"diameter": "-5 mm"}, "input.invalid", "diameter"),
        ({"diameter": "5 bar"}, "units.dimension_mismatch", "diameter"),
        ({"diameter": "5 widgets"}, "units.parse_error", "diameter"),
        ({"diameter": "1000 m"}, "input.out_of_range", "diameter"),
        ({"diameter": "0.001 mm"}, "input.out_of_range", "diameter"),
        ({"density": "870 kg"}, "units.dimension_mismatch", "density"),
        ({"density": "1e9 kg/m^3"}, "input.out_of_range", "density"),
        ({"pressure_drop": "0 bar"}, "input.invalid", "pressure_drop"),
        ({"pressure_drop": "-3 bar"}, "input.invalid", "pressure_drop"),
        ({"pressure_drop": "1e9 bar"}, "input.out_of_range", "pressure_drop"),
        ({"pressure_drop": "100 mm"}, "units.dimension_mismatch", "pressure_drop"),
        ({"discharge_coeff": 0.0}, "input.invalid", "discharge_coeff"),
        ({"discharge_coeff": 1.5}, "input.invalid", "discharge_coeff"),
        ({"discharge_coeff": "0.6"}, "input.invalid", "discharge_coeff"),
        ({"viscosity": "46 cSt"}, "units.dimension_mismatch", "viscosity"),
        ({"flow_unit": "bar"}, "units.dimension_mismatch", "flow_unit"),
        ({"pressure_unit": "frobnicators"}, "units.parse_error", "pressure_unit"),
    ],
)
def test_orifice_flow_invalid_inputs(override, family, param):
    kwargs = {**ORIFICE, **override}
    _fails(orifice_flow, family, param, **kwargs)


def test_orifice_flow_rejects_flow_so_large_that_the_pressure_drop_is_absurd():
    # 1 m^3/s through a 1 mm hole needs far more than 1000 bar
    _fails(orifice_flow, "input.out_of_range", "pressure_drop (result)", diameter="1 mm", density="870 kg/m^3", flow_rate="1 m^3/s")


# --------------------------------------------------------------------------- pump_power
# Hand calculation: Q = 60 L/min = 1.0e-3 m^3/s, dp = 210 bar = 2.1e7 Pa, eta = 0.85, n = 1500 rpm
#   P_hyd   = Q dp           = 1.0e-3 * 2.1e7 = 21 000 W
#   P_shaft = P_hyd / eta    = 21 000 / 0.85  = 24 705.88 W
#   loss    = 24 705.88 - 21 000 = 3 705.88 W
#   omega   = 1500 * 2 pi / 60 = 157.0796 rad/s ; T = 24 705.88 / 157.0796 = 157.2825 N m
PUMP = dict(flow_rate="60 L/min", pressure_rise="210 bar", efficiency=0.85)


def test_pump_power_matches_hand_calc():
    r = _call(pump_power, **PUMP, speed="1500 rpm")
    assert r.ok, r.error
    assert r.value.unit == "kilowatt"
    assert math.isclose(r.value.quantity, 24.70588235, rel_tol=1e-9)
    assert math.isclose(r.values["hydraulic_power"].quantity, 21.0, rel_tol=1e-12)
    assert math.isclose(r.values["power_loss"].quantity, 3.70588235, rel_tol=1e-9)
    assert math.isclose(r.values["shaft_torque"].quantity, 157.28253, rel_tol=1e-7)
    assert any("motor" in w for w in r.warnings)  # standing scope warning


def test_pump_power_without_speed_has_no_torque():
    r = _call(pump_power, **PUMP)
    assert r.ok and "shaft_torque" not in r.values


@pytest.mark.parametrize("speed", ["25 Hz", "157.07963267949 rad/s", "1500 revolutions_per_minute", "1500 1/min"])
def test_pump_power_speed_spellings_agree(speed):
    # 25 Hz = 25 rev/s = 1500 rev/min; the Hz case must not be turned into 25/(2 pi) rev/s
    r = _call(pump_power, **PUMP, speed=speed)
    assert r.ok, r.error
    assert math.isclose(r.values["shaft_torque"].quantity, 157.28253, rel_tol=1e-6)


def test_pump_power_unit_conversions():
    # 15.8503230745 gal/min = 1e-3 m^3/s ; 3045.7924 psi = 2.1e7 Pa ; output in hp (745.69987 W)
    r = _call(
        pump_power,
        flow_rate=f"{1e-3 / US_GALLON_M3 * 60} gal/min",
        pressure_rise=f"{2.1e7 / PSI} psi",
        efficiency=0.85,
        power_unit="hp",
    )
    assert r.ok, r.error
    assert math.isclose(r.value.quantity, 24705.88235 / 745.69987, rel_tol=1e-6)  # 33.1311 hp
    assert r.value.unit == "horsepower"


@pytest.mark.parametrize(
    ("override", "family", "param"),
    [
        ({"efficiency": 85}, "input.invalid", "efficiency"),
        ({"efficiency": 0.0}, "input.invalid", "efficiency"),
        ({"efficiency": -0.5}, "input.invalid", "efficiency"),
        ({"efficiency": "0.85"}, "input.invalid", "efficiency"),
        ({"efficiency": float("nan")}, "input.invalid", "efficiency"),
        ({"flow_rate": "0 L/min"}, "input.invalid", "flow_rate"),
        ({"flow_rate": "-1 L/min"}, "input.invalid", "flow_rate"),
        ({"flow_rate": "60"}, "units.bare_number", "flow_rate"),
        ({"flow_rate": "60 bar"}, "units.dimension_mismatch", "flow_rate"),
        ({"flow_rate": "1e9 L/min"}, "input.out_of_range", "flow_rate"),
        ({"pressure_rise": "210 L/min"}, "units.dimension_mismatch", "pressure_rise"),
        ({"pressure_rise": "5000000 bar"}, "input.out_of_range", "pressure_rise"),
        ({"pressure_rise": "0 bar"}, "input.invalid", "pressure_rise"),
        ({"speed": "1500"}, "units.bare_number", "speed"),
        ({"speed": "1500 N"}, "units.dimension_mismatch", "speed"),
        ({"speed": "0 rpm"}, "input.invalid", "speed"),
        ({"speed": "1e9 rpm"}, "input.out_of_range", "speed"),
        ({"power_unit": "N"}, "units.dimension_mismatch", "power_unit"),
    ],
)
def test_pump_power_invalid_inputs(override, family, param):
    _fails(pump_power, family, param, **{**PUMP, **override})


def test_pump_power_efficiency_and_pressure_warnings():
    assert any("higher than any hydraulic pump" in w for w in _call(pump_power, **{**PUMP, "efficiency": 0.99}).warnings)
    assert any("very low" in w for w in _call(pump_power, **{**PUMP, "efficiency": 0.2}).warnings)
    assert any("450 bar" in w for w in _call(pump_power, **{**PUMP, "pressure_rise": "600 bar"}).warnings)
    assert not any("pump reaches" in w or "very low" in w for w in _call(pump_power, **PUMP).warnings)


# --------------------------------------------------------------------------- accumulator_precharge
# Gas law p V^n = const (absolute pressures).  V0 = dV / ((p0/p1)^(1/n) - (p0/p2)^(1/n)), V1 = V0 (p0/p1)^(1/n), V2 = V0 (p0/p2)^(1/n)
# Isothermal n = 1, absolute: dV = 5 L, p1 = 100 bar, p2 = 200 bar, p0 = 90 bar
#   V0 = 5 / (90/100 - 90/200) = 5 / (0.9 - 0.45) = 11.1111 L ; V1 = 11.1111*0.9 = 10 L ; V2 = 11.1111*0.45 = 5 L (10 - 5 = 5 L delivered)
ACC = dict(usable_volume="5 L", min_pressure="100 bar", max_pressure="200 bar", precharge_pressure="90 bar", pressures_are_gauge=False)


def test_accumulator_isothermal_absolute():
    r = _call(accumulator_precharge, **ACC, exponent=1.0)
    assert r.ok, r.error
    assert r.value.unit == "liter"
    assert math.isclose(r.value.quantity, 100.0 / 9.0, rel_tol=1e-12)  # 11.1111
    assert math.isclose(r.values["gas_volume_at_min_pressure"].quantity, 10.0, rel_tol=1e-12)
    assert math.isclose(r.values["gas_volume_at_max_pressure"].quantity, 5.0, rel_tol=1e-12)
    assert math.isclose(r.values["pressure_ratio"].quantity, 200.0 / 90.0, rel_tol=1e-12)
    assert math.isclose(r.values["precharge_pressure_absolute"].quantity, 90.0, rel_tol=1e-12)
    assert math.isclose(r.values["precharge_pressure_gauge"].quantity, 90.0 - 1.01325, rel_tol=1e-12)
    assert not any("pre-charge not given" in w for w in r.warnings)


def test_accumulator_adiabatic_n_1_4():
    # (0.9)^(1/1.4) = exp(ln 0.9 / 1.4) = exp(-0.1053605/1.4) = exp(-0.0752575) = 0.927505
    # (0.45)^(1/1.4) = exp(-0.7985077/1.4) = exp(-0.5703626) = 0.565320
    # V0 = 5 / (0.927505 - 0.565320) = 5 / 0.362185 = 13.8051 L
    r = _call(accumulator_precharge, **ACC, exponent=1.4)
    assert r.ok, r.error
    assert math.isclose(r.value.quantity, 13.8051, rel_tol=1e-5)
    assert math.isclose(r.values["gas_volume_at_min_pressure"].quantity, 13.8051 * 0.927505, rel_tol=1e-5)
    # the fluid delivered is exactly the requested 5 L
    delivered = r.values["gas_volume_at_min_pressure"].quantity - r.values["gas_volume_at_max_pressure"].quantity
    assert math.isclose(delivered, 5.0, rel_tol=1e-9)
    assert r.value.quantity > 100.0 / 9.0  # faster discharge needs a larger accumulator than isothermal


def test_accumulator_gauge_pressures_and_default_precharge():
    # gauge 100 / 200 bar, pre-charge defaults to 0.9 * 100 = 90 bar g ; add 1.01325 bar -> absolute 91.01325 / 101.01325 / 201.01325
    # n = 1: p0/p1 = 0.901003, p0/p2 = 0.452772 -> V0 = 5 / 0.448231 = 11.15497 L
    r = _call(accumulator_precharge, usable_volume="5 L", min_pressure="100 bar", max_pressure="200 bar", exponent=1.0)
    assert r.ok, r.error
    assert math.isclose(r.value.quantity, 11.15497, rel_tol=1e-5)
    assert math.isclose(r.values["precharge_pressure_gauge"].quantity, 90.0, rel_tol=1e-12)
    assert any("pre-charge not given" in w for w in r.warnings)


def test_accumulator_unit_conversions():
    # 10 000 kPa = 100 bar, 20 000 kPa = 200 bar, 9 000 kPa = 90 bar, 5 L = 1.32086 US gal
    r = _call(
        accumulator_precharge,
        usable_volume=f"{5e-3 / US_GALLON_M3} gal",
        min_pressure="10000 kPa",
        max_pressure="20000 kPa",
        precharge_pressure="9000 kPa",
        exponent=1.0,
        pressures_are_gauge=False,
        volume_unit="m^3",
    )
    assert r.ok, r.error
    assert r.value.unit == "meter ** 3"
    assert math.isclose(r.value.quantity, 100.0 / 9.0 / 1000.0, rel_tol=1e-9)


def test_accumulator_warnings():
    # precharge 95 bar of 100 bar minimum = 0.95 > 0.9
    r = _call(accumulator_precharge, **{**ACC, "precharge_pressure": "95 bar"})
    assert any("0.95 x the minimum" in w for w in r.warnings)
    # p2/p0 = 200/40 = 5 > 4
    r = _call(accumulator_precharge, **{**ACC, "precharge_pressure": "40 bar"})
    assert any("exceeds about 4" in w for w in r.warnings)


@pytest.mark.parametrize(
    ("override", "family", "param"),
    [
        ({"precharge_pressure": "100 bar"}, "input.invalid", "precharge_pressure"),  # == min
        ({"precharge_pressure": "150 bar"}, "input.invalid", "precharge_pressure"),  # > min
        ({"max_pressure": "100 bar"}, "input.invalid", "max_pressure"),  # == min
        ({"max_pressure": "50 bar"}, "input.invalid", "max_pressure"),
        ({"exponent": 0.9}, "input.invalid", "exponent"),
        ({"exponent": 2.0}, "input.invalid", "exponent"),
        ({"exponent": "1.4"}, "input.invalid", "exponent"),
        ({"usable_volume": "0 L"}, "input.invalid", "usable_volume"),
        ({"usable_volume": "-5 L"}, "input.invalid", "usable_volume"),
        ({"usable_volume": "5"}, "units.bare_number", "usable_volume"),
        ({"usable_volume": "5 bar"}, "units.dimension_mismatch", "usable_volume"),
        ({"usable_volume": "1e9 L"}, "input.out_of_range", "usable_volume"),
        ({"min_pressure": "0 bar"}, "input.invalid", "min_pressure"),
        ({"min_pressure": "100 L"}, "units.dimension_mismatch", "min_pressure"),
        ({"max_pressure": "5000000 bar"}, "input.out_of_range", "max_pressure"),
        ({"precharge_pressure": "-90 bar"}, "input.invalid", "precharge_pressure"),
        ({"volume_unit": "bar"}, "units.dimension_mismatch", "volume_unit"),
    ],
)
def test_accumulator_invalid_inputs(override, family, param):
    _fails(accumulator_precharge, family, param, **{**ACC, **override})


# --------------------------------------------------------------------------- cylinder_speed
# Hand calculation: bore 100 mm, rod 50 mm, Q = 60 L/min = 1.0e-3 m^3/s, stroke 500 mm
#   A_bore = pi/4 0.1^2 = 7.853982e-3 m^2 ; A_ann = pi/4 (0.1^2 - 0.05^2) = 5.890486e-3 m^2
#   v_ext = 1e-3 / 7.853982e-3 = 0.1273240 m/s ; v_ret = 1e-3 / 5.890486e-3 = 0.1697653 m/s
#   t_ext = 0.5 / 0.1273240 = 3.926991 s ; t_ret = 0.5 / 0.1697653 = 2.945243 s
#   return flow while extending = 60 * 5.890486/7.853982 = 45 L/min ; while retracting = 60 * 7.853982/5.890486 = 80 L/min
CYL = dict(bore="100 mm", rod="50 mm", flow_rate="60 L/min")


def test_cylinder_speed_matches_hand_calc():
    r = _call(cylinder_speed, **CYL, stroke="500 mm")
    assert r.ok, r.error
    assert r.value.unit == "millimeter / second"
    assert math.isclose(r.value.quantity, 127.32395, rel_tol=1e-7)
    assert math.isclose(r.values["retract_speed"].quantity, 169.76527, rel_tol=1e-7)
    assert math.isclose(r.values["extend_time"].quantity, 3.926991, rel_tol=1e-6)
    assert math.isclose(r.values["retract_time"].quantity, 2.945243, rel_tol=1e-6)
    assert math.isclose(r.values["return_flow_extend"].quantity, 45.0, rel_tol=1e-12)
    assert math.isclose(r.values["return_flow_retract"].quantity, 80.0, rel_tol=1e-12)
    assert math.isclose(r.data["area_ratio"], 4.0 / 3.0, rel_tol=1e-12)
    assert not any("rod not given" in w for w in r.warnings)


def test_cylinder_speed_imperial():
    # 4 in bore, 2 in rod, 10 gal/min = 10*231/60 = 38.5 in^3/s (1 US gal = 231 in^3 exactly)
    #   A_bore = pi/4*16 = 12.56637 in^2 -> v_ext = 38.5/12.56637 = 3.063733 in/s
    #   A_ann  = pi/4*12 =  9.424778 in^2 -> v_ret = 38.5/9.424778 = 4.084977 in/s
    r = _call(cylinder_speed, bore="4 inch", rod="2 inch", flow_rate="10 gal/min", speed_unit="in/s")
    assert r.ok, r.error
    assert r.value.unit == "inch / second"
    assert math.isclose(r.value.quantity, 3.063733, rel_tol=1e-6)
    assert math.isclose(r.values["retract_speed"].quantity, 4.084977, rel_tol=1e-6)


def test_cylinder_speed_without_rod_warns_and_both_speeds_match():
    r = _call(cylinder_speed, bore="100 mm", flow_rate="60 L/min")
    assert r.ok
    assert math.isclose(r.value.quantity, r.values["retract_speed"].quantity, rel_tol=1e-12)
    assert any("rod not given" in w for w in r.warnings)
    assert "extend_time" not in r.values


def test_cylinder_speed_warnings_fast_and_large_area_ratio():
    # 300 L/min into a 50 mm bore: v = 5e-3 / 1.963495e-3 = 2.546 m/s
    fast = _call(cylinder_speed, bore="50 mm", rod="20 mm", flow_rate="300 L/min")
    assert any("above about 0.5 m/s" in w for w in fast.warnings)
    # rod 80 of 100: area ratio 1 / (1 - 0.64) = 2.78 > 1.6 ; return flow when retracting 60 * 2.7778 = 166.7 L/min
    big = _call(cylinder_speed, bore="100 mm", rod="80 mm", flow_rate="60 L/min")
    assert any("area ratio 2.78" in w and "167 L/min" in w for w in big.warnings)


@pytest.mark.parametrize(
    ("override", "family", "param"),
    [
        ({"rod": "100 mm"}, "input.invalid", "rod"),
        ({"rod": "120 mm"}, "input.invalid", "rod"),
        ({"rod": "0 mm"}, "input.invalid", "rod"),
        ({"rod": "-5 mm"}, "input.invalid", "rod"),
        ({"rod": "50"}, "units.bare_number", "rod"),
        ({"rod": "50 bar"}, "units.dimension_mismatch", "rod"),
        ({"bore": "0 mm"}, "input.invalid", "bore"),
        ({"bore": "0.5 mm"}, "input.out_of_range", "bore"),
        ({"bore": "100000 mm"}, "input.out_of_range", "bore"),
        ({"bore": "100 L"}, "units.dimension_mismatch", "bore"),
        ({"flow_rate": "0 L/min"}, "input.invalid", "flow_rate"),
        ({"flow_rate": "60 mm"}, "units.dimension_mismatch", "flow_rate"),
        ({"flow_rate": "1e12 L/min"}, "input.out_of_range", "flow_rate"),
        ({"stroke": "0 mm"}, "input.invalid", "stroke"),
        ({"stroke": "500 N"}, "units.dimension_mismatch", "stroke"),
        ({"speed_unit": "kg"}, "units.dimension_mismatch", "speed_unit"),
    ],
)
def test_cylinder_speed_invalid_inputs(override, family, param):
    _fails(cylinder_speed, family, param, **{**CYL, "stroke": "500 mm", **override})
