"""Hand arithmetic, switched-current integration, and converter refusal contracts."""

import math
import random

import pytest

from eng_toolkit.domains.electric.converter import converter
from tests.electric_testing import call, close, ok, table, v


def _args(topology="buck", **changes):
    settings = dict(topology=topology, input_voltage="24 V", output_voltage="12 V",
                    output_current="5 A", switching_frequency="20 kHz", inductance="150 uH",
                    output_capacitance="470 uF")
    if topology == "boost":
        settings.update(input_voltage="12 V", output_voltage="24 V", output_current="2 A",
                        switching_frequency="50 kHz", inductance="100 uH")
    if topology == "buck_boost":
        settings.update(input_voltage="12 V", output_voltage="18 V", output_current="2 A",
                        switching_frequency="50 kHz", inductance="100 uH")
    return dict(settings, **changes)


def test_buck_hand_design_and_capacitor_ripple():
    result = ok(converter, **_args(inductance="", target_current_ripple="2 A", capacitor_esr="20 mohm"))
    # D=12/24; L=(24-12)*0.5/(20000*2)=150 uH; Icrit=DeltaI/2=1 A.
    close(v(result, "duty_cycle"), 0.5)
    close(v(result, "inductance", "H"), 150e-6)
    close(v(result, "critical_output_current", "A"), 1)
    close(v(result, "critical_inductance", "H"), 30e-6)
    close(v(result, "inductor_current_minimum", "A"), 4)
    close(v(result, "inductor_current_peak", "A"), 6)
    close(v(result, "inductor_current_rms", "A"), math.sqrt(25 + 4 / 12))
    close(v(result, "switch_current_rms", "A"), math.sqrt((25 + 4 / 12) / 2))
    close(v(result, "diode_current_rms", "A"), math.sqrt((25 + 4 / 12) / 2))
    close(v(result, "input_capacitor_current_rms", "A"), math.sqrt(0.5 * (25 + 4 / 12) - 2.5 ** 2))
    close(v(result, "output_capacitor_current_rms", "A"), 2 / math.sqrt(12))
    close(v(result, "output_ripple_capacitive_peak_to_peak", "V"), 2 / (8 * 20000 * 470e-6))
    close(v(result, "output_ripple_esr_peak_to_peak", "V"), 0.04)
    close(v(result, "output_ripple_total_peak_to_peak", "V"), 2 / (8 * 20000 * 470e-6) + 0.04)
    assert result.data["conduction_mode"] == "ccm"
    assert [row["interval"] for row in table(result)] == ["switch_on", "diode_on", "idle"]


@pytest.mark.parametrize("topology,vin,vo,io,frequency,inductance,duty", [
    ("buck", 24, 12, 5, 20000, 150e-6, 0.5),
    ("boost", 12, 24, 2, 50000, 100e-6, 0.5),
    ("buck_boost", 12, 18, 2, 50000, 100e-6, 0.6),
])
def test_ccm_hand_voltage_ratio_and_control(topology, vin, vo, io, frequency, inductance, duty):
    result = ok(converter, **_args(topology))
    close(v(result, "duty_cycle"), duty)
    mean = io if topology == "buck" else io / (1 - duty)
    ripple = (vin - vo if topology == "buck" else vin) * duty / (frequency * inductance)
    close(v(result, "inductor_current_average"), mean)
    close(v(result, "inductor_ripple_peak_to_peak"), ripple)
    close(v(result, "switch_current_rms"), math.sqrt(duty * (mean * mean + ripple * ripple / 12)))
    close(v(result, "diode_current_rms"), math.sqrt((1 - duty) * (mean * mean + ripple * ripple / 12)))
    close(v(result, "efficiency"), 1)
    close(v(result, "averaged_power_balance_residual"), 0, abs=1e-8)
    signed_vo = -vo if topology == "buck_boost" else vo
    close(v(result, "output_voltage_signed"), signed_vo)
    plant = result.data["control_to_output_ideal_ccm"]
    scale = 1 if topology == "buck" else (1 - duty) ** 2
    expected_denominator = [inductance * 470e-6 / scale, inductance / ((vo / io) * scale), 1]
    for actual, expected in zip(plant["denominator"], expected_denominator):
        close(actual, expected)
    # The denominator is LC*s^2 + (L/R)*s + (1-D)^2 for boost and buck-boost.
    close(v(result, "control_natural_angular_frequency"), math.sqrt(scale / (inductance * 470e-6)))
    close(v(result, "control_dc_gain"), (-1 if topology == "buck_boost" else 1) * vin / scale)
    if topology == "buck":
        assert plant["rhp_zero_rad_per_s"] is None
    else:
        # Boost omega_z=R*(1-D)^2/L; inverting buck-boost has the additional D denominator.
        expected_zero = (vo / io) * (1 - duty) ** 2 / inductance
        if topology == "buck_boost":
            expected_zero /= duty
        close(v(result, "control_rhp_zero_angular_frequency"), expected_zero)
        close(plant["numerator"][0], -plant["numerator"][1] / expected_zero)
    for real, imag in plant["poles_rad_per_s"]:
        pole = complex(real, imag)
        a, b, c = plant["denominator"]
        assert abs(a * pole * pole + b * pole + c) < 1e-8


@pytest.mark.parametrize("topology,vin,vo,frequency,inductance", [
    ("buck", 24, 12, 20000, 150e-6), ("boost", 12, 24, 50000, 100e-6),
    ("buck_boost", 12, 18, 50000, 100e-6),
])
def test_dcm_hand_three_intervals_and_ripple_sizing(topology, vin, vo, frequency, inductance):
    current = 0.1
    if topology == "buck":
        duty = math.sqrt(2 * inductance * frequency * current * vo / (vin * (vin - vo)))
        ratio = (vin - vo) / vo
        peak = (vin - vo) * duty / (inductance * frequency)
    else:
        voltage = vo - vin if topology == "boost" else vo
        duty = math.sqrt(2 * inductance * frequency * current * voltage) / vin
        ratio = vin / voltage
        peak = vin * duty / (inductance * frequency)
    result = ok(converter, **_args(topology, mode="dcm", output_current="0.1 A"))
    assert result.data["conduction_mode"] == "dcm"
    close(v(result, "duty_cycle"), duty)
    close(v(result, "diode_interval"), duty * ratio)
    close(v(result, "idle_interval"), 1 - duty * (1 + ratio))
    close(v(result, "inductor_current_peak"), peak)
    close(v(result, "switch_current_rms"), peak * math.sqrt(duty / 3))
    close(v(result, "diode_current_rms"), peak * math.sqrt(duty * ratio / 3))
    close(v(result, "averaged_power_balance_residual"), 0, abs=1e-8)
    assert result.data["control_to_output_ideal_ccm"] is None
    sized = ok(converter, **_args(topology, mode="dcm", output_current="0.1 A", inductance="",
                                 target_current_ripple=f"{peak:.15g} A"))
    close(v(sized, "inductance"), inductance, rel=1e-8)


def _integrate_switched_inductor(topology, vin, vo, inductance, frequency, duty, initial):
    """Integrate v=L di/dt directly, with an ideal diode clamp during the off interval."""
    steps = 12000
    current = initial
    sums = dict(inductor=0.0, inductor2=0.0, source=0.0, source2=0.0, output=0.0,
                capacitor2=0.0, switch2=0.0, diode2=0.0)
    trace = []
    diode_time = 0.0
    peak = initial
    for on, interval in ((True, duty / frequency), (False, (1 - duty) / frequency)):
        step = interval / steps
        for _ in range(steps):
            voltage = ((vin - vo if topology == "buck" else vin) if on
                       else (vin - vo if topology == "boost" else -vo))
            next_current = max(0.0, current + voltage * step / inductance)
            active_time = step
            if not on and next_current == 0 and current > 0:
                active_time = min(step, -current * inductance / voltage)
            elif not on and current == 0:
                active_time = 0.0
            midpoint = (current + next_current) / 2
            mean = midpoint * active_time / step
            square = (current * current + current * next_current + next_current * next_current) / 3 * active_time / step
            source = mean if (on or topology == "boost") else 0.0
            source2 = square if (on or topology == "boost") else 0.0
            delivered = mean if (not on or topology == "buck") else 0.0
            output2 = square if (not on or topology == "buck") else 0.0
            for name, value in (("inductor", mean), ("inductor2", square), ("source", source),
                                ("source2", source2), ("output", delivered),
                                ("switch2", square if on else 0), ("diode2", square if not on else 0)):
                sums[name] += value * step * frequency
            trace.append((step, delivered, output2))
            if not on:
                diode_time += active_time
            current = next_current
            peak = max(peak, current)
    load = sums["output"]
    charge = 0.0
    extrema = [0.0]
    for step, delivered, delivered2 in trace:
        charge += (delivered - load) * step
        extrema.append(charge)
        sums["capacitor2"] += (delivered2 - 2 * load * delivered + load * load) * step * frequency
    return sums, current, peak, diode_time * frequency, max(extrema) - min(extrema)


@pytest.mark.parametrize("topology", ["buck", "boost", "buck_boost"])
@pytest.mark.parametrize("discontinuous", [False, True])
def test_independent_switched_integration(topology, discontinuous):
    args = _args(topology, output_current="0.1 A") if discontinuous else _args(topology)
    result = ok(converter, **args)
    vin = 24 if topology == "buck" else 12
    vo = {"buck": 12, "boost": 24, "buck_boost": 18}[topology]
    frequency = 20000 if topology == "buck" else 50000
    inductance = 150e-6 if topology == "buck" else 100e-6
    initial = v(result, "inductor_current_minimum")
    # Integrate three consecutive switched periods and require a periodic state.
    final = initial
    for _ in range(3):
        sums, final, peak, diode, charge = _integrate_switched_inductor(
            topology, vin, vo, inductance, frequency, v(result, "duty_cycle"), final)
        close(final, initial, abs=1e-8)
    expected_load = 0.1 if discontinuous else (5 if topology == "buck" else 2)
    close(final, initial, abs=1e-8)
    close(sums["output"], expected_load, rel=2e-7)
    close(peak, v(result, "inductor_current_peak"), rel=2e-7)
    close(diode, v(result, "diode_interval"), rel=2e-7)
    close(sums["source"] * vin, expected_load * vo, rel=2e-7)
    for name, value in (("inductor_current_rms", sums["inductor2"]),
                        ("switch_current_rms", sums["switch2"]), ("diode_current_rms", sums["diode2"]),
                        ("output_capacitor_current_rms", sums["capacitor2"]),
                        ("input_capacitor_current_rms", sums["source2"] - sums["source"] ** 2)):
        close(v(result, name), math.sqrt(max(0, value)), rel=2e-7)
    close(v(result, "output_ripple_capacitive_peak_to_peak"), charge / 470e-6, rel=3e-7)


@pytest.mark.parametrize("topology", ["buck", "boost", "buck_boost"])
@pytest.mark.parametrize("discontinuous", [False, True])
def test_nonideal_mean_balance_rms_correction_and_losses(topology, discontinuous):
    result = ok(converter, **_args(topology, output_current="0.1 A" if discontinuous else "2 A",
                                 switch_resistance="50 mohm", inductor_resistance="20 mohm", diode_drop="0.6 V",
                                 capacitor_esr="10 mohm", rise_time="20 ns", fall_time="30 ns",
                                 gate_charge="30 nC", gate_voltage="10 V", reverse_recovery_charge="15 nC"))
    duty, diode = v(result, "duty_cycle"), v(result, "diode_interval")
    minimum, peak = v(result, "inductor_current_minimum"), v(result, "inductor_current_peak")
    mean = (minimum + peak) / 2
    vin, vo = (24, 12) if topology == "buck" else (12, 24 if topology == "boost" else 18)
    frequency = 20000 if topology == "buck" else 50000
    # Independent interval-mean volt-second equation with R_on=.07 and R_off=.02 ohm.
    on = vin - vo if topology == "buck" else vin
    off = vo + 0.6 - (vin if topology == "boost" else 0)
    close(duty * (on - 0.07 * mean), diode * (off + 0.02 * mean), rel=1e-8)
    close(v(result, "averaged_power_balance_residual"), 0, abs=2e-8)
    extra = (0.05 * duty + 0.02 * (duty + diode)) * (peak - minimum) ** 2 / 12
    close(v(result, "ripple_copper_correction"), extra)
    close(v(result, "rms_waveform_power_balance_residual"), -extra, abs=2e-8)
    losses = sum(v(result, name) for name in ("switch_conduction_loss", "diode_conduction_loss", "inductor_copper_loss",
                 "capacitor_esr_loss", "switch_overlap_loss", "gate_drive_loss", "reverse_recovery_loss"))
    close(v(result, "total_loss"), losses)
    close(v(result, "input_power_estimate"), v(result, "output_power") + losses)
    close(v(result, "estimated_power_balance_residual"), 0)
    close(v(result, "gate_drive_loss"), 30e-9 * 10 * frequency)
    stress = vin + 0.6 if topology == "buck" else vo + 0.6 + (vin if topology == "buck_boost" else 0)
    close(v(result, "switch_voltage_stress"), stress)
    close(v(result, "switch_overlap_loss"), 0.5 * stress * (minimum * 20e-9 + peak * 30e-9) * frequency)
    close(v(result, "reverse_recovery_loss"), 0 if discontinuous else 15e-9 * stress * frequency)
    assert 0 < v(result, "efficiency") < 1


def test_nonideal_buck_duty_and_capacitance_size():
    result = ok(converter, **_args(output_current="2 A", switch_resistance="50 mohm",
                                 inductor_resistance="20 mohm", diode_drop="0.6 V"))
    close(v(result, "duty_cycle"), (12 + 0.6 + 2 * 0.02) / (24 + 0.6 - 2 * 0.05))
    sized = ok(converter, **_args(output_capacitance="", target_output_ripple="50 mV", capacitor_esr="10 mohm"))
    # ESR consumes 20 mV; the remaining 30 mV requires C=DeltaI/(8*f*0.03).
    close(v(sized, "minimum_output_capacitance"), 2 / (8 * 20000 * 0.03))
    close(v(sized, "output_ripple_total_peak_to_peak"), 0.05)


@pytest.mark.parametrize("topology", ["buck", "boost", "buck_boost"])
def test_ccm_dcm_boundary_continuity(topology):
    initial = ok(converter, **_args(topology))
    critical = v(initial, "critical_output_current")
    below = ok(converter, **_args(topology, output_current=f"{critical * (1 - 1e-5)} A"))
    above = ok(converter, **_args(topology, output_current=f"{critical * (1 + 1e-5)} A"))
    assert below.data["conduction_mode"] == "dcm"
    assert above.data["conduction_mode"] == "ccm"
    close(v(below, "duty_cycle"), v(above, "duty_cycle"), rel=2e-5)
    close(v(below, "inductor_current_peak"), v(above, "inductor_current_peak"), rel=2e-5)


@pytest.mark.parametrize("topology,current", [("buck", 1), ("boost", 0.3), ("buck_boost", 0.288)])
def test_dcm_allows_the_critical_boundary(topology, current):
    result = ok(converter, **_args(topology, mode="dcm", output_current=f"{current} A"))
    close(v(result, "idle_interval"), 0, abs=1e-9)
    close(v(result, "inductor_current_minimum"), 0)


def test_high_damping_poles_keep_the_small_root():
    result = ok(converter, **_args(output_current="100000 A", inductance="100 H", output_capacitance="10 nF"))
    plant = result.data["control_to_output_ideal_ccm"]
    roots = [complex(real, imag) for real, imag in plant["poles_rad_per_s"]]
    assert all(root.real < 0 for root in roots)
    a, b, c = plant["denominator"]
    close((roots[0] * roots[1]).real, c / a)
    close((roots[0] + roots[1]).real, -b / a)


def test_random_ccm_duties_against_scipy_brentq():
    scipy = pytest.importorskip("scipy.optimize")
    rng = random.Random(31073)
    for topology in ("buck", "boost", "buck_boost"):
        for _ in range(10):
            vin = rng.uniform(20, 60)
            vo = vin * rng.uniform(0.2, 0.8) if topology == "buck" else vin * rng.uniform(1.2, 2)
            current = rng.uniform(1, 5)
            rs, rl, vd = rng.uniform(0, 0.02), rng.uniform(0, 0.02), rng.uniform(0.1, 0.8)
            # Solve the source/output/loss balance directly; this does not use the production volt-second solver.
            def power(duty):
                mean = current if topology == "buck" else current / (1 - duty)
                source = mean if topology == "boost" else duty * mean
                diode = (1 - duty) * mean
                return vin * source - vo * current - rs * duty * mean ** 2 - rl * mean ** 2 - vd * diode
            ideal = vo / vin if topology == "buck" else (1 - vin / vo if topology == "boost" else vo / (vin + vo))
            expected = scipy.brentq(power, ideal, min(0.95, ideal + 0.2), xtol=1e-13)
            result = ok(converter, topology=topology, input_voltage=f"{vin} V", output_voltage=f"{vo} V",
                        output_current=f"{current} A", switching_frequency="100 kHz", inductance="1 mH",
                        output_capacitance="1 mF", switch_resistance=f"{rs} ohm", inductor_resistance=f"{rl} ohm",
                        diode_drop=f"{vd} V")
            close(v(result, "duty_cycle"), expected, rel=2e-9)


@pytest.mark.parametrize("changes", [
    {"topology": "flyback"}, {"mode": "pulse_skip"}, {"input_voltage": ""},
    {"input_voltage": 24}, {"input_voltage": "24"}, {"input_voltage": "24 A"},
    {"input_voltage": "NaN V"}, {"input_voltage": "1e500 V"}, {"input_voltage": "1e-500 V"},
    {"input_voltage": "1e7 V"}, {"input_voltage": "1 V; __import__('os')"},
    {"input_voltage": "1 " + "V" * 200}, {"switching_frequency": "20 rad/s"},
    {"inductance": "0 H"}, {"inductance": "", "target_current_ripple": ""},
    {"output_current": "0 A"}, {"output_voltage": "30 V"}, {"output_voltage": "-12 V"},
    {"mode": "dcm"}, {"mode": "ccm", "output_current": "0.1 A"},
    {"gate_charge": "1 nC"}, {"rise_time": "10 us"}, {"capacitor_esr": "-1 ohm"},
    {"target_output_ripple": "1 mV", "capacitor_esr": "20 mohm"},
    {"output_capacitance": "1 nF"}, {"switch_resistance": "100 ohm"},
    {"inductor_resistance": "1 ohm", "output_current": "0.1 A", "inductance": "1 uH"},
    {"mode": "ccm", "target_current_ripple": "20 A"},
    {"mode": "dcm", "inductance": "", "target_current_ripple": "1 A"},
])
def test_hostile_and_unsupported_inputs_are_structured(changes):
    result = call(converter, **_args(**changes))
    assert not result.ok


def test_empty_call_and_missing_capacitance_are_structured():
    result = call(converter)
    assert not result.ok and result.error.details["param"] == "/input_voltage"
    result = ok(converter, **_args(output_capacitance=""))
    assert result.data["control_to_output_ideal_ccm"] is None
    assert "output_ripple_total_peak_to_peak" not in result.values
