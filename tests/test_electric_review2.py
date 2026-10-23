"""Gateway regressions for the second electric review, findings 1 through 5."""

import math
from decimal import Decimal, localcontext

import pytest
from fastmcp import Client

from eng_toolkit.gateway.server import gateway
from tests.electric_testing import table, v, validate


async def _call(name, **args):
    async with Client(gateway) as client:
        reply = await client.call_tool("electric_" + name, args, raise_on_error=False)
    assert not reply.is_error, reply.content
    assert reply.structured_content is not None
    return validate(reply.structured_content)


async def test_esr_ripple_limit_without_capacitance():
    result = await _call(
        "converter", input_voltage="24 V", output_voltage="12 V",
        output_current="5 A", switching_frequency="20 kHz", inductance="150 uH",
        capacitor_esr="2 ohm",
    )
    # D=1/2, delta_I=(24-12)/2/(150e-6*20000)=2 A; ESR ripple=4 V > 1.2 V.
    assert not result.ok
    assert result.error.family == "electric.model_limit"
    assert result.error.details["param"] == "/capacitor_esr"


@pytest.mark.parametrize("capacitance,target", [("470 uF", ""), ("", "5 V")])
async def test_esr_ripple_limit_also_applies_when_evaluating_or_sizing_capacitance(capacitance, target):
    result = await _call(
        "converter", input_voltage="24 V", output_voltage="12 V", output_current="5 A",
        switching_frequency="20 kHz", inductance="150 uH", capacitor_esr="2 ohm",
        output_capacitance=capacitance, target_output_ripple=target,
    )
    assert not result.ok
    assert result.error.family == "electric.model_limit"
    assert result.error.details["param"] == "/capacitor_esr"


async def test_esr_below_limit_without_capacitance_keeps_total_ripple_unverified():
    result = await _call(
        "converter", input_voltage="24 V", output_voltage="12 V", output_current="5 A",
        switching_frequency="20 kHz", inductance="150 uH", capacitor_esr="0.5 ohm",
    )
    assert result.ok, result.error
    assert v(result, "output_ripple_esr_peak_to_peak") == pytest.approx(1, rel=1e-9)
    assert "output_ripple_total_peak_to_peak" not in result.values
    assert any("unverified" in warning for warning in result.warnings)


async def test_esr_limit_retains_ripple_below_dc_current_resolution():
    result = await _call(
        "converter", input_voltage="2e-6 V", output_voltage="1e-6 V", output_current="1e6 A",
        switching_frequency="1e9 Hz", inductance="1e-3 H", capacitor_esr="1e6 ohm",
    )
    # delta_I=1e-6*0.5/(1e-3*1e9)=5e-13 A, below the ulp of 1e6 A.
    # Its ESR contribution is still 5e-7 V, five times the 1e-7 V limit.
    assert not result.ok
    assert result.error.family == "electric.model_limit"
    assert result.error.details["param"] == "/capacitor_esr"


def _induction_reference(slip_text=".9", core_resistance=None):
    # Independently invert the parallel branch, then the complete input
    # impedance at 80 digits. Three-phase power is Vline^2 * Re(Yin).
    with localcontext() as context:
        context.prec = 80
        r1, x1, xm, r2, x2, slip = map(Decimal, ("1e-8", "1e8", "1e8", "1e-8", "1e-8", slip_text))
        rotor_g, rotor_b = Decimal(0), Decimal(0)
        if slip:
            rotor_r = r2 / slip
            rotor_norm = rotor_r**2 + x2**2
            rotor_g, rotor_b = rotor_r / rotor_norm, -x2 / rotor_norm
        g = rotor_g + (1/Decimal(core_resistance) if core_resistance else 0)
        b = -1 / xm + rotor_b
        branch_norm = g**2 + b**2
        real = r1 + g / branch_norm
        imag = x1 - b / branch_norm
        input_norm = real**2 + imag**2
        pin = 10000 * real / input_norm
        node_norm = (1 + r1*g - x1*b)**2 + (r1*b + x1*g)**2
        shaft = 10000 * rotor_g / node_norm * (1-slip)
        return float(pin), float(shaft/pin), float(real/input_norm.sqrt())


async def test_induction_input_power_avoids_reactive_cancellation():
    result = await _call(
        "induction_motor", line_voltage="100 V", r1="1e-8 ohm", x1="1e8 ohm",
        xm="1e8 ohm", r2="1e-8 ohm", x2="1e-8 ohm", slip=0.9, curve_points=3,
    )
    assert result.ok, result.error
    pin, efficiency, pf = _induction_reference()
    assert pin == pytest.approx(2.111111111111111e-20, rel=1e-14, abs=0)
    assert efficiency == pytest.approx(1/19, rel=1e-14, abs=0)
    assert v(result, "input_power") == pytest.approx(pin, rel=1e-12, abs=0)
    assert v(result, "efficiency") == pytest.approx(efficiency, rel=1e-12, abs=0)
    assert v(result, "power_factor") == pytest.approx(pf, rel=1e-12, abs=0)


@pytest.mark.parametrize("core_resistance", [None, "1e-8", "1e8"])
async def test_induction_loss_sum_and_curve_match_high_precision_impedance(core_resistance):
    result = await _call(
        "induction_motor", line_voltage="100 V", r1="1e-8 ohm", x1="1e8 ohm",
        xm="1e8 ohm", r2="1e-8 ohm", x2="1e-8 ohm", slip=0.9, curve_points=3,
        rc="" if core_resistance is None else core_resistance + " ohm",
    )
    assert result.ok, result.error
    pin, efficiency, pf = _induction_reference(core_resistance=core_resistance)
    for name, expected in (("input_power", pin), ("efficiency", efficiency), ("power_factor", pf)):
        assert v(result, name) == pytest.approx(expected, rel=1e-12, abs=0)
    assert v(result, "input_power") >= v(result, "stator_copper_loss")
    for row in table(result):
        _, efficiency, pf = _induction_reference(row["slip"], core_resistance)
        assert float(row["power_factor"]) == pytest.approx(pf, rel=1e-12, abs=0)
        if Decimal(row["slip"]) == Decimal(".5"):
            assert float(row["efficiency"]) == pytest.approx(efficiency, rel=1e-12, abs=0)
        else:
            assert row["efficiency"] == ""


async def test_pmsm_standstill_resolves_small_requested_torque():
    result = await _call(
        "pm_dc_motor", mode="pmsm", voltage="100 V", resistance="1e-12 ohm",
        current_limit="1e9 A", d_inductance="1 mH", q_inductance="1 mH",
        flux_linkage="1 Wb", speed="0 rad/s", torque="1e-12 N*m", curve_points=2,
    )
    assert result.ok, result.error
    # Equal inductances: no reluctance torque, id=0 and iq=T/(1.5*p*psi).
    assert v(result, "d_current") == 0
    assert v(result, "q_current") == pytest.approx(1e-12/1.5, rel=1e-10, abs=0)
    assert v(result, "electromagnetic_torque") == pytest.approx(1e-12, rel=1e-10, abs=0)


@pytest.mark.parametrize("ld,lq,flux,torque", [
    (".001", ".002", "1", "1e-12"), (".002", ".001", "1", "1e-12"),
    (".001", "1000", "1e-12", "1e-12"), ("1000", ".001", "1e-12", "1e-12"),
    (".001", ".002", ".1", "15"), (".002", ".001", ".1", "15"),
])
async def test_salient_mtpa_request_scaled_bracket(ld, lq, flux, torque):
    # Minimize id^2+[tau/(psi+delta*id)]^2 at fixed torque. The unique
    # stationary point satisfies u*(psi+abs(delta)*u)^3=abs(delta)*tau^2,
    # where u=abs(id). Solve this independently at 80 decimal digits.
    with localcontext() as context:
        context.prec = 80
        psi, tau = Decimal(flux), Decimal(torque)/Decimal("1.5")
        delta = Decimal(ld)-Decimal(lq)
        low, high = Decimal(0), min(tau/psi, (tau/abs(delta)).sqrt())
        for _ in range(256):
            mid = (low+high)/2
            if mid*(psi+abs(delta)*mid)**3 < abs(delta)*tau**2:
                low = mid
            else:
                high = mid
        expected_d = float(high.copy_sign(delta))
        expected_q = float(tau/(psi+abs(delta)*high))
    result = await _call(
        "pm_dc_motor", mode="pmsm", voltage="100 V", resistance="1e-12 ohm",
        current_limit="1e9 A", d_inductance=ld+" H", q_inductance=lq+" H",
        flux_linkage=flux+" Wb", speed="0 rad/s", torque=torque+" N*m",
        speed_points="0 rad/s;1e-9 rad/s",
    )
    assert result.ok, result.error
    assert v(result, "d_current") == pytest.approx(expected_d, rel=1e-10, abs=0)
    assert v(result, "q_current") == pytest.approx(expected_q, rel=1e-10, abs=0)
    assert v(result, "electromagnetic_torque") == pytest.approx(float(torque), rel=1e-10, abs=0)


@pytest.mark.parametrize("inductance,current,frequency", [(10, 100, "1 MHz"), (100, 100, "1 MHz"), (1000, 1e6, "1e9 Hz")])
async def test_boost_input_capacitor_small_ripple(inductance, current, frequency):
    result = await _call(
        "converter", topology="boost", input_voltage="12 V", output_voltage="24 V",
        output_current=f"{current:g} A", switching_frequency=frequency, inductance=f"{inductance} H",
    )
    assert result.ok, result.error
    # D=1/2; integrate the centered triangular ramp: variance=delta_I^2/12.
    ripple = 12*0.5/(inductance*(1e6 if frequency == "1 MHz" else 1e9))
    assert v(result, "input_capacitor_current_rms") == pytest.approx(ripple/math.sqrt(12), rel=1e-9, abs=0)
    assert v(result, "inductor_ripple_peak_to_peak") == pytest.approx(ripple, rel=1e-9, abs=0)


async def test_drive_candidate_rejects_fifty_times_all_limits():
    result = await _call(
        "drive_sizing", load_inertia="1 kg*m^2",
        profile="1 s,5e-11 rad/s,5e-11 rad/s,5e-11 N*m",
        candidates="tiny,0 kg*m^2,1e-12 N*m,1e-12 N*m,1e-12 rad/s,1e-12 rad/s",
    )
    assert result.ok, result.error
    candidate = result.data["candidates"][0]
    # Constant speed and torque: RMS=peak=5e-11; each margin=1e-12-5e-11.
    for name in ("rated_torque_margin_N_m", "maximum_torque_margin_N_m",
                 "rated_speed_margin_rad_s", "maximum_speed_margin_rad_s"):
        assert candidate[name] == pytest.approx(-4.9e-11, rel=1e-12, abs=0)
    assert candidate["status"].startswith("fail:"), candidate["status"]
    assert table(result, 1)[0]["status"] == candidate["status"]
    assert table(result, 2)[0]["profile_status"] == candidate["status"]


@pytest.mark.parametrize("rating", [1e-12, 1, 1e6])
@pytest.mark.parametrize("fraction,passed", [(0.5, True), (1, True), (1+1e-7, False), (50, False)])
async def test_drive_candidate_margin_scales_with_rating(rating, fraction, passed):
    demand = rating*fraction
    result = await _call(
        "drive_sizing", load_inertia="1 kg*m^2",
        profile=f"1 s,{demand:.16g} rad/s,{demand:.16g} rad/s,{demand:.16g} N*m",
        candidates=f"motor,0 kg*m^2,{rating:g} N*m,{rating:g} N*m,{rating:g} rad/s,{rating:g} rad/s",
    )
    assert result.ok, result.error
    status = result.data["candidates"][0]["status"]
    assert status.startswith("pass" if passed else "fail:"), status
    assert table(result, 1)[0]["status"] == status
    assert table(result, 2)[0]["profile_status"] == status
