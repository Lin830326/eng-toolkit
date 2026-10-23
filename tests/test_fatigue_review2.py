"""Second-review regressions through the in-process MCP gateway."""

from decimal import Decimal, localcontext

import pytest
from fastmcp import Client

from eng_toolkit.gateway.server import gateway
from tests.fatigue_testing import table, validate, v


PI = Decimal("3.141592653589793238462643383279502884197169399375105820974944592307816406")
NEAR_TOUGHNESS = "5.6049912166781786"


async def gateway_call(name, **args):
    async with Client(gateway) as client:
        result = await client.call_tool(name, args, raise_on_error=False)
    assert not result.is_error, result.content
    return validate(result.structured_content)


def refusal(result, family, pointer):
    assert not result.ok, result.values
    assert result.error.family == family, result.error
    assert result.error.details["param"] == pointer, result.error


def constant_y_life(toughness, *, law="paris", coefficient="1e-20"):
    """Independent 70-digit antiderivatives, with stress=100 MPa, a0=.001 m, m=3."""
    with localcontext() as ctx:
        ctx.prec = 70
        kc, a0, c = Decimal(toughness), Decimal(".001"), Decimal(coefficient)
        amplitude = 100 * PI.sqrt()
        ac = (kc / amplitude) ** 2
        integral = 2 * (1 / a0.sqrt() - 1 / ac.sqrt())
        if law == "forman":
            integral = kc * integral - amplitude * (ac / a0).ln()
        return float(ac), float(integral / (c * amplitude**3))


def toughness_at(size, geometry="infinite"):
    with localcontext() as ctx:
        ctx.prec = 70
        a = Decimal(size)
        x = a / Decimal(".1")
        y = (Decimal("1.122") - Decimal(".231")*x + Decimal("10.55")*x**2
             - Decimal("21.71")*x**3 + Decimal("30.382")*x**4) if geometry == "edge" else 1
        return str(100 * (PI * a).sqrt() * y)


async def test_torsion_uses_shigley_torsional_neuber_constant():
    result = await gateway_call(
        "fatigue_sn", mode="estimate", load_type="torsion", ultimate_strength="600 MPa",
        yield_strength="550 MPa", size_factor=1, notch_method="neuber", kt=3,
        notch_radius=".1 mm", alternating_stress="100 MPa",
    )
    assert result.ok, result.error
    with localcontext() as ctx:
        ctx.prec = 60
        # Exact lbf and inch definitions give MPa/ksi. Shigley 6-35 uses ksi and sqrt(in).
        mpa_per_ksi = Decimal("4.4482216152605") / Decimal(".0254")**2 / 1000
        su = 600 / mpa_per_ksi
        root_a = Decimal(".190") - Decimal(".00251")*su + Decimal(".0000135")*su**2 - Decimal(".0000000267")*su**3
        sensitivity = 1 / (1 + root_a / (Decimal(".1") / Decimal("25.4")).sqrt())
        kfs = 1 + 2 * sensitivity
        anchor, endurance = Decimal("361.8"), Decimal(177)
        slope = (endurance / anchor).ln() / Decimal(1000).ln()
        life = 1000 * ((100 * kfs / anchor).ln() / slope).exp()
    assert v(result, "notch_sensitivity") == pytest.approx(float(sensitivity), rel=1e-12)
    assert v(result, "fatigue_notch_factor") == pytest.approx(float(kfs), rel=1e-12)
    assert v(result, "life") == pytest.approx(float(life), rel=1e-10)


@pytest.mark.parametrize("method", ["neuber", "peterson"])
async def test_torsional_measured_notch_constant_overrides_estimates(method):
    result = await gateway_call(
        "fatigue_sn", mode="notch", load_type="torsion", notch_method=method,
        kt=3, notch_radius=".1 mm", notch_material_length=".1 mm",
    )
    assert result.ok, result.error
    assert v(result, "notch_sensitivity") == .5
    assert v(result, "fatigue_notch_factor") == 2


async def test_torsional_peterson_requires_measured_constant():
    result = await gateway_call(
        "fatigue_sn", mode="estimate", load_type="torsion", ultimate_strength="600 MPa",
        size_factor=1, notch_method="peterson", kt=3, notch_radius=".1 mm",
    )
    refusal(result, "input.missing", "/notch_material_length")


@pytest.mark.parametrize("law,unit_power", [("paris", 3), ("forman", 2)])
async def test_unresolvable_critical_interval_refuses_life(law, unit_power):
    ac, expected = constant_y_life(NEAR_TOUGHNESS, law=law)
    assert ac == pytest.approx(.0010000000001000001213, rel=1e-16)
    if law == "paris":
        assert expected == pytest.approx(56790.503304017424, rel=1e-12)
    result = await gateway_call(
        "fatigue_fracture", mode="growth", crack_size="1 mm", stress="100 MPa",
        toughness=f"{NEAR_TOUGHNESS} MPa*m^.5", growth_law=law,
        growth_c=f"1e-20 m/(MPa*m^.5)^{unit_power}", table_points=2,
        integration_tolerance=1e-10,
    )
    # The remaining interval is ~1e-13 m; binary64 cannot locate its endpoint to 1e-23 m.
    refusal(result, "numerical.no_convergence", "/integration_tolerance")


@pytest.mark.parametrize("law,unit_power", [("paris", 3), ("forman", 2)])
@pytest.mark.parametrize("size,tolerance", [(".00101", 1e-10), (".00100001", 1e-7), (".002", 1e-10)])
async def test_resolvable_critical_life_matches_decimal(law, unit_power, size, tolerance):
    toughness = toughness_at(size)
    ac, expected = constant_y_life(toughness, law=law)
    result = await gateway_call(
        "fatigue_fracture", mode="growth", crack_size="1 mm", stress="100 MPa",
        toughness=f"{toughness} MPa*m^.5", growth_law=law,
        growth_c=f"1e-20 m/(MPa*m^.5)^{unit_power}", table_points=2,
        integration_tolerance=tolerance,
    )
    assert result.ok, result.error
    assert result.data["growth_stop"] == "Kmax_instability"
    assert v(result, "cycles_to_failure") == pytest.approx(expected, rel=tolerance)
    assert v(result, "final_crack_size") == pytest.approx(ac, rel=1e-14)
    assert float(table(result)[-1]["cycles"]) == pytest.approx(expected, rel=tolerance)


@pytest.mark.parametrize("law,unit_power", [("paris", 3), ("forman", 2)])
@pytest.mark.parametrize("limit", ["", "2 mm"])
async def test_variable_geometry_critical_life_matches_independent_integral(law, unit_power, limit):
    integrate = pytest.importorskip("scipy.integrate")
    toughness = toughness_at(".00101", "edge")
    with localcontext() as ctx:
        ctx.prec = 70
        kc, lo, hi = Decimal(toughness), Decimal(".001"), Decimal(".002")
        # Independent physical-coordinate root using Decimal and the published edge polynomial.
        for _ in range(230):
            mid = (lo + hi) / 2
            if Decimal(toughness_at(mid, "edge")) < kc:
                lo = mid
            else:
                hi = mid
        ac = (lo + hi) / 2

        def inverse_rate(size):
            k = Decimal(toughness_at(Decimal.from_float(size), "edge"))
            return float((kc-k if law == "forman" else 1) / (Decimal("1e-20")*k**3))

        expected = integrate.quad(inverse_rate, .001, float(ac), epsabs=0, epsrel=1e-12)[0]
    result = await gateway_call(
        "fatigue_fracture", mode="growth", geometry="edge", width="100 mm",
        crack_size="1 mm", final_crack=limit, stress="100 MPa",
        toughness=f"{toughness} MPa*m^.5", growth_law=law,
        growth_c=f"1e-20 m/(MPa*m^.5)^{unit_power}", integration_tolerance=1e-10,
    )
    assert result.ok, result.error
    assert result.data["growth_stop"] == "Kmax_instability"
    assert v(result, "cycles_to_failure") == pytest.approx(expected, rel=1e-10)


async def test_variable_geometry_unresolvable_critical_life_refuses():
    toughness = toughness_at(".0010000000001", "edge")
    result = await gateway_call(
        "fatigue_fracture", mode="growth", geometry="edge", width="100 mm",
        crack_size="1 mm", stress="100 MPa", toughness=f"{toughness} MPa*m^.5",
        growth_c="1e-20 m/(MPa*m^.5)^3", integration_tolerance=1e-10,
    )
    refusal(result, "numerical.no_convergence", "/integration_tolerance")


async def test_growth_coefficient_rejects_rounded_exponent():
    result = await gateway_call(
        "fatigue_fracture", mode="growth", crack_size="1 mm", final_crack="10 mm",
        stress="100 MPa", growth_m=3.123456789,
        growth_c="1e-12 m/(MPa*m^0.5)^3.12346",
    )
    refusal(result, "units.dimension_mismatch", "/growth_c")


@pytest.mark.parametrize("law,power", [("paris", "3.123456789"), ("forman", "2.123456789")])
async def test_growth_coefficient_accepts_matching_full_precision_exponent(law, power):
    result = await gateway_call(
        "fatigue_fracture", mode="growth", crack_size="1 mm", final_crack="10 mm",
        stress="100 MPa", growth_m=3.123456789, growth_law=law, toughness="50 MPa*m^.5",
        growth_c=f"1e-12 m/(MPa*m^0.5)^{power}", integration_tolerance=1e-10,
    )
    assert result.ok, result.error
    with localcontext() as ctx:
        ctx.prec = 60
        m = Decimal("3.123456789")
        exponent = 1 - m / 2
        integral = (Decimal(".01")**exponent - Decimal(".001")**exponent) / exponent
        if law == "forman":
            other = exponent + Decimal(".5")
            integral = 50 * integral - 100 * PI.sqrt() * (Decimal(".01")**other - Decimal(".001")**other) / other
        expected = integral / (Decimal("1e-12") * (100 * PI.sqrt())**m)
    assert v(result, "cycles_to_limit") == pytest.approx(float(expected), rel=1e-10)


@pytest.mark.parametrize("history", ["[0,1e101]", "[0,1e-400]", "[0,NaN]", "[0,Infinity]", "[0,-Infinity]"])
async def test_rainflow_preserves_magnitude_error_family(history):
    result = await gateway_call("fatigue_rainflow", history=history, sample_scale="1 MPa")
    refusal(result, "input.out_of_range", "/history")


@pytest.mark.parametrize("history", ["[0,]", "[0,nope]", "[0,1e]"])
async def test_rainflow_malformed_json_remains_invalid(history):
    result = await gateway_call("fatigue_rainflow", history=history, sample_scale="1 MPa")
    refusal(result, "input.invalid", "/history")
