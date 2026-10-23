"""Averaged nonsynchronous converters with explicit triangular-waveform limits."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Annotated

from pydantic import Field

from eng_toolkit.domains.electric._common import Result, bisect, choice, invalid, q, run
from eng_toolkit.domains.electric._server import server

_DUTY_MIN = 1e-8
_DUTY_MAX = 1 - _DUTY_MIN


@dataclass(frozen=True)
class _Cycle:
    mode: str
    duty: float
    diode: float
    minimum: float
    peak: float
    inductance: float
    ripple: float

    @property
    def idle(self):
        return max(0.0, 1 - self.duty - self.diode)


def _ccm_duty(topology, current, on_voltage, off_voltage, on_resistance, off_resistance):
    def balance(duty):
        mean = current if topology == "buck" else current / (1 - duty)
        return (duty * on_voltage - (1 - duty) * off_voltage
                - mean * (duty * on_resistance + (1 - duty) * off_resistance))

    high = _DUTY_MAX
    if topology != "buck":
        # Multiplying the balance by 1-D gives a concave quadratic. Its vertex
        # brackets the lower-current root without crossing the lossy second root.
        high = min(high, (on_voltage + 2 * off_voltage
                         - current * (on_resistance - off_resistance))
                   / (2 * (on_voltage + off_voltage)))
    if high <= _DUTY_MIN:
        invalid("output_current", "no averaged steady state at this load", "electric.no_solution")
    duty = bisect(balance, _DUTY_MIN, high, "output_current")
    mean = current if topology == "buck" else current / (1 - duty)
    slope_voltage = on_voltage - on_resistance * mean
    if slope_voltage <= 0:
        invalid("output_current", "the on interval cannot increase inductor current", "electric.no_solution")
    return duty, mean, slope_voltage


def _dcm_point(duty, inductance, frequency, on_voltage, off_voltage, on_resistance, off_resistance):
    peak = on_voltage * duty / (inductance * frequency + on_resistance * duty / 2)
    diode = inductance * frequency * peak / (off_voltage + off_resistance * peak / 2)
    return peak, diode


def _boundary(topology, inductance, frequency, on_voltage, off_voltage, on_resistance, off_resistance):
    def point(duty):
        return _dcm_point(duty, inductance, frequency, on_voltage, off_voltage, on_resistance, off_resistance)

    duty = bisect(lambda d: d + point(d)[1] - 1, _DUTY_MIN, _DUTY_MAX, "inductance")
    peak, diode = point(duty)
    critical_current = peak * (1 if topology == "buck" else diode) / 2
    return duty, critical_current


def _size_inductance(topology, mode, current, frequency, ripple,
                     on_voltage, off_voltage, on_resistance, off_resistance):
    duty, mean, slope = _ccm_duty(topology, current, on_voltage, off_voltage, on_resistance, off_resistance)
    if ripple <= 2 * mean * (1 + 1e-10) and mode != "dcm":
        return slope * duty / (frequency * ripple), "ccm"
    if mode == "ccm":
        invalid("target_current_ripple", "the requested ripple crosses zero current in CCM")
    on = on_voltage - on_resistance * ripple / 2
    off = off_voltage + off_resistance * ripple / 2
    if on <= 0:
        invalid("target_current_ripple", "resistive drop prevents the requested peak current")
    ratio = on / off
    duty = 2 * current / (ripple * (1 + ratio if topology == "buck" else ratio))
    diode = duty * ratio
    if duty + diode > 1 + 1e-9 or not _DUTY_MIN <= duty <= _DUTY_MAX:
        invalid("target_current_ripple", "requested ripple is incompatible with DCM intervals")
    return on * duty / (frequency * ripple), "dcm"


def _cycle(topology, mode, current, frequency, inductance,
           on_voltage, off_voltage, on_resistance, off_resistance):
    boundary_duty, critical_current = _boundary(
        topology, inductance, frequency, on_voltage, off_voltage, on_resistance, off_resistance)
    discontinuous = current < critical_current * (1 - 1e-9)
    if mode == "ccm" and discontinuous:
        invalid("mode", "this inductance and load require DCM")
    if mode == "dcm" and current > critical_current * (1 + 1e-9):
        invalid("mode", "this inductance and load require CCM")
    if discontinuous or mode == "dcm":
        def point(duty):
            return _dcm_point(duty, inductance, frequency, on_voltage, off_voltage, on_resistance, off_resistance)

        def load(duty):
            peak, diode = point(duty)
            return peak * (duty + diode if topology == "buck" else diode) / 2

        duty = bisect(lambda d: load(d) - current, _DUTY_MIN, boundary_duty, "output_current")
        peak, diode = point(duty)
        cycle = _Cycle("dcm", duty, diode, 0.0, peak, inductance, peak)
    else:
        duty, mean, slope = _ccm_duty(topology, current, on_voltage, off_voltage, on_resistance, off_resistance)
        ripple = slope * duty / (inductance * frequency)
        cycle = _Cycle("ccm", duty, 1 - duty, max(0.0, mean - ripple / 2), mean + ripple / 2, inductance, ripple)
    if cycle.peak > 1e9:
        invalid("output_current", "derived peak current exceeds 1e9 A", "input.out_of_range")
    return cycle, critical_current


def _waveforms(topology, cycle, current):
    low, high = cycle.minimum, cycle.peak
    segments = [(cycle.duty, low, high), (cycle.diode, high, low), (cycle.idle, 0.0, 0.0)]
    output = []
    source = []
    for index, (duration, start, end) in enumerate(segments):
        delivers = topology == "buck" or index == 1
        draws = topology == "boost" or index == 0
        output.append((duration, (start if delivers else 0) - current, (end if delivers else 0) - current))
        source.append((duration, start if draws else 0, end if draws else 0))
    return segments, output, source


def _moments(segments):
    average = sum(t * (a + b) / 2 for t, a, b in segments)
    square = sum(t * (a * a + a * b + b * b) / 3 for t, a, b in segments)
    return average, square


def _charge_ripple(segments, frequency):
    charge = 0.0
    extrema = [charge]
    currents = []
    for fraction, start, end in segments:
        if fraction <= 0:
            continue
        duration = fraction / frequency
        currents.extend((start, end))
        if start * end < 0:
            zero_time = -start * duration / (end - start)
            extrema.append(charge + start * zero_time / 2)
        charge += (start + end) * duration / 2
        extrema.append(charge)
    return max(extrema) - min(extrema), max(currents) - min(currents)


def _control(result, topology, duty, voltage, current, inductance, capacitance):
    resistance = voltage / current
    complement = 1 - duty
    scale = 1 if topology == "buck" else complement * complement
    denominator = [inductance * capacitance / scale, inductance / (resistance * scale), 1.0]
    # Vin is supplied separately through the operating-point data.
    vin = result.data["input_voltage_v"]
    gain = vin if topology == "buck" else vin / scale
    if topology == "buck_boost":
        gain = -gain
    numerator = [gain]
    zero = None
    if topology != "buck":
        zero = resistance * scale / (inductance * (duty if topology == "buck_boost" else 1))
        numerator = [-gain / zero, gain]
        result.add("control_rhp_zero_angular_frequency", zero, "rad/s")
    omega = math.sqrt(scale / (inductance * capacitance))
    quality = resistance * math.sqrt(capacitance * scale / inductance)
    real = -omega / (2 * quality)
    discriminant = real * real - omega * omega
    if discriminant >= 0:
        fast_pole = real - math.sqrt(discriminant)
        poles = [[omega * omega / fast_pole, 0.0], [fast_pole, 0.0]]
    else:
        poles = [[real, math.sqrt(-discriminant)], [real, -math.sqrt(-discriminant)]]
    result.add("control_dc_gain", gain, "V")
    result.add("control_natural_angular_frequency", omega, "rad/s")
    result.add("control_quality_factor", quality)
    result.data["control_to_output_ideal_ccm"] = {
        "coefficient_order": "descending powers of s; s in rad/s; output is signed voltage",
        "numerator": numerator, "denominator": denominator, "poles_rad_per_s": poles,
        "rhp_zero_rad_per_s": zero,
        "validity": "Ideal CCM voltage-mode averaged plant at the calculated duty and load resistance; parasitics and DCM dynamics excluded.",
    }


@server.tool(description="State-space averaged buck/boost/inverting buck-boost duty, CCM/DCM triangular ripple, stresses and losses; small-ripple model.")
def converter(
    topology: Annotated[str, Field(description="Nonsynchronous power stage: buck, boost, or buck_boost (inverting output).")] = "buck",
    mode: Annotated[str, Field(description="Conduction requirement: auto selects CCM/DCM; ccm or dcm rejects incompatible load and inductance.")] = "auto",
    input_voltage: Annotated[str, Field(description="Positive DC source voltage with units, e.g. '24 V'; supported 1e-6..1e6 V.")] = "",
    output_voltage: Annotated[str, Field(description="Positive output-voltage magnitude, e.g. '12 V'; buck_boost reports a negative signed output.")] = "",
    output_current: Annotated[str, Field(description="Positive DC load-current magnitude with units, e.g. '5 A'; no-load operation is excluded.")] = "",
    switching_frequency: Annotated[str, Field(description="Fixed switching frequency with Hz units, e.g. '20 kHz'; supported 1e-3..1e9 Hz.")] = "",
    inductance: Annotated[str, Field(description="Inductor value with units, e.g. '150 uH'; omit only when target_current_ripple sizes it.")] = "",
    target_current_ripple: Annotated[str, Field(description="Optional desired inductor peak-to-peak ripple in A; in DCM this is the peak current, e.g. '2 A'.")] = "",
    output_capacitance: Annotated[str, Field(description="Optional output capacitance with units, e.g. '470 uF'; needed for ripple and control-pole outputs.")] = "",
    target_output_ripple: Annotated[str, Field(description="Optional total output peak-to-peak ripple target, e.g. '50 mV'; sizes capacitance after the ESR allowance.")] = "",
    capacitor_esr: Annotated[str, Field(description="Output-capacitor series resistance with units, e.g. '20 mohm'; zero is ideal.")] = "0 ohm",
    diode_drop: Annotated[str, Field(description="Constant forward diode drop during its conduction interval, e.g. '0.6 V'.")] = "0 V",
    switch_resistance: Annotated[str, Field(description="On-state MOSFET resistance Rds(on), e.g. '10 mohm'; the off-state is open.")] = "0 ohm",
    inductor_resistance: Annotated[str, Field(description="Inductor winding DC resistance, e.g. '30 mohm'; core and AC copper losses are excluded.")] = "0 ohm",
    rise_time: Annotated[str, Field(description="Switch turn-on voltage-current overlap duration, e.g. '20 ns'; triangular overlap estimate.")] = "0 s",
    fall_time: Annotated[str, Field(description="Switch turn-off voltage-current overlap duration, e.g. '30 ns'; must fit the switching period.")] = "0 s",
    gate_charge: Annotated[str, Field(description="Effective gate charge per switching cycle with coulomb units, e.g. '30 nC'.")] = "0 C",
    gate_voltage: Annotated[str, Field(description="Gate-drive voltage paired with gate_charge, e.g. '10 V'; both must be positive or both zero.")] = "0 V",
    reverse_recovery_charge: Annotated[str, Field(description="Diode hard-commutation reverse-recovery charge, e.g. '15 nC'; assumed zero recovery loss in DCM.")] = "0 C",
):
    inputs = locals()

    def compute(handle):
        choice(topology, "topology", ("buck", "boost", "buck_boost"))
        choice(mode, "mode", ("auto", "ccm", "dcm"))
        vin = q(input_voltage, "input_voltage", "V", lo=1e-6, hi=1e6)
        vo = q(output_voltage, "output_voltage", "V", lo=1e-6, hi=1e6)
        io = q(output_current, "output_current", "A", lo=1e-9, hi=1e6)
        frequency = q(switching_frequency, "switching_frequency", "Hz", lo=1e-3, hi=1e9)
        induct = q(inductance, "inductance", "H", optional=True, lo=1e-12, hi=1e3)
        ripple_target = q(target_current_ripple, "target_current_ripple", "A", optional=True, lo=1e-9, hi=1e9)
        capacitance = q(output_capacitance, "output_capacitance", "F", optional=True, lo=1e-12, hi=1e4)
        voltage_target = q(target_output_ripple, "target_output_ripple", "V", optional=True, lo=1e-9, hi=1e6)
        esr = q(capacitor_esr, "capacitor_esr", "ohm", zero=True, hi=1e6)
        vd = q(diode_drop, "diode_drop", "V", zero=True, hi=1e4)
        rs = q(switch_resistance, "switch_resistance", "ohm", zero=True, hi=1e6)
        rl = q(inductor_resistance, "inductor_resistance", "ohm", zero=True, hi=1e6)
        tr = q(rise_time, "rise_time", "s", zero=True, hi=1e3)
        tf = q(fall_time, "fall_time", "s", zero=True, hi=1e3)
        qg = q(gate_charge, "gate_charge", "C", zero=True, hi=1)
        vg = q(gate_voltage, "gate_voltage", "V", zero=True, hi=1e3)
        qrr = q(reverse_recovery_charge, "reverse_recovery_charge", "C", zero=True, hi=1)
        if topology == "buck" and vo >= vin:
            invalid("output_voltage", "buck output magnitude must be below input voltage")
        if topology == "boost" and vo <= vin:
            invalid("output_voltage", "boost output magnitude must exceed input voltage")
        if (qg == 0) != (vg == 0):
            invalid("gate_charge", "gate charge and gate voltage must both be zero or both positive")
        if (tr + tf) * frequency > 0.1:
            invalid("rise_time", "overlap durations together must not exceed 10% of the switching period")
        on_voltage = vin - vo if topology == "buck" else vin
        off_voltage = vo + vd - (vin if topology == "boost" else 0)
        design_inductance = None
        design_mode = None
        if ripple_target is not None:
            design_inductance, design_mode = _size_inductance(
                topology, mode, io, frequency, ripple_target, on_voltage, off_voltage, rs + rl, rl)
            if not 1e-12 <= design_inductance <= 1e3:
                invalid("target_current_ripple", "sized inductance lies outside 1e-12..1e3 H", "input.out_of_range")
        if induct is None:
            if design_inductance is None:
                invalid("inductance", "supply inductance or target_current_ripple", "input.missing")
            induct = design_inductance
        cycle, critical_current = _cycle(topology, mode, io, frequency, induct,
                                         on_voltage, off_voltage, rs + rl, rl)
        if (tr + tf) * frequency > min(cycle.duty, 1 - cycle.duty):
            invalid("rise_time", "overlap durations do not fit the on and off intervals")
        ccm_duty, ccm_mean, ccm_slope = _ccm_duty(topology, io, on_voltage, off_voltage, rs + rl, rl)
        critical_inductance = ccm_slope * ccm_duty / (2 * ccm_mean * frequency)
        segments, capacitor_wave, source_wave = _waveforms(topology, cycle, io)
        il_average, il_square = _moments(segments)
        source_average, _ = _moments(source_wave)
        _, capacitor_square = _moments(capacitor_wave)
        local_square = (cycle.minimum ** 2 + cycle.minimum * cycle.peak + cycle.peak ** 2) / 3
        local_mean = (cycle.minimum + cycle.peak) / 2
        # Center the ramp before taking its second moment. Retain its ripple
        # separately because subtracting peak and minimum can also lose it.
        source_fraction = min(1.0, cycle.duty + cycle.diode) if topology == "boost" else cycle.duty
        source_variance = source_fraction * (cycle.ripple ** 2 / 12
                                             + (1 - source_fraction) * local_mean ** 2)
        switch_square, diode_square = cycle.duty * local_square, cycle.diode * local_square
        switch_loss, inductor_loss = rs * switch_square, rl * il_square
        diode_loss = vd * cycle.diode * local_mean
        mean_copper = (rs * cycle.duty + rl * (cycle.duty + cycle.diode)) * local_mean ** 2
        ripple_copper = max(0.0, switch_loss + inductor_loss - mean_copper)
        output_power = vo * io
        if ripple_copper > 0.05 * output_power:
            invalid("inductor_resistance", "ripple copper correction exceeds 5% of output power; use a switched-circuit model", "electric.model_limit")
        charge_ripple, _ = _charge_ripple(capacitor_wave, frequency)
        capacitor_current_pp = cycle.ripple if topology == "buck" else cycle.peak
        esr_ripple = capacitor_current_pp * esr
        if esr_ripple > 0.1 * vo:
            invalid("capacitor_esr", "ESR ripple alone exceeds 10% of DC voltage; stiff-output approximation is unsupported", "electric.model_limit")
        minimum_capacitance = None
        if voltage_target is not None:
            if voltage_target <= esr_ripple:
                invalid("target_output_ripple", "the ESR ripple alone reaches or exceeds the target")
            minimum_capacitance = charge_ripple / (voltage_target - esr_ripple)
            if not 1e-12 <= minimum_capacitance <= 1e4:
                invalid("target_output_ripple", "sized capacitance lies outside 1e-12..1e4 F", "input.out_of_range")
            if capacitance is None:
                capacitance = minimum_capacitance
        stress = vin + vd if topology == "buck" else vo + vd + (vin if topology == "buck_boost" else 0)
        diode_stress = vin if topology == "buck" else vo + (vin if topology == "buck_boost" else 0)
        overlap_loss = 0.5 * stress * (cycle.minimum * tr + cycle.peak * tf) * frequency
        gate_loss = qg * vg * frequency
        recovery_loss = qrr * stress * frequency if cycle.mode == "ccm" and cycle.minimum > 0 else 0.0
        esr_loss = esr * capacitor_square
        conduction_loss = switch_loss + inductor_loss + diode_loss
        total_loss = conduction_loss + esr_loss + overlap_loss + gate_loss + recovery_loss
        input_power = output_power + total_loss
        result = Result(
            {"ccm": "D*V_on-(1-D)*V_off=I_L*(D*R_on+(1-D)*R_off)",
             "dcm": "Ipk=V_on*D/(L*f+R_on*D/2); delta=L*f*Ipk/(V_off+R_off*Ipk/2)",
             "load": "CCM Io=IL for buck, (1-D)*IL otherwise; DCM Io=Ipk*(D+delta)/2 for buck, delta*Ipk/2 otherwise",
             "ripple": "Vpp=range(integral(i_C dt))/C + ESR*range(i_C)",
             "loss": "Pcond=Rds*Isw_rms^2+DCR*IL_rms^2+Vd*Id_avg; Pin_est=Vo*Io+all reported losses"},
            "Fixed-frequency nonsynchronous stage; stiff input/output DC voltages, constant load current per cycle, linear current ramps and interval-mean resistive drops.",
            "Textbook averaged estimate, not a switched transient simulation. Ripple copper correction <=5% of output power; no core, AC winding, Coss, dead-time or thermal model.",
        )
        result.data.update({"topology": topology, "conduction_mode": cycle.mode, "input_voltage_v": vin,
                            "target_ripple_mode": design_mode,
                            "power_balance": "Averaged waveform power balances interval-mean conduction loss. Pin estimate adds RMS ripple copper, ESR and switching losses.",
                            "ripple_bound": "Capacitive and ESR peak-to-peak contributions are added conservatively; their extrema need not coincide."})
        values = {
            "duty_cycle": (cycle.duty, "dimensionless"), "diode_interval": (cycle.diode, "dimensionless"),
            "idle_interval": (cycle.idle, "dimensionless"), "inductance": (induct, "H"),
            "critical_inductance": (critical_inductance, "H"), "critical_output_current": (critical_current, "A"),
            "output_voltage_signed": (-vo if topology == "buck_boost" else vo, "V"),
            "inductor_ripple_peak_to_peak": (cycle.ripple, "A"),
            "inductor_current_average": (il_average, "A"), "inductor_current_minimum": (cycle.minimum, "A"),
            "inductor_current_peak": (cycle.peak, "A"), "inductor_current_rms": (math.sqrt(il_square), "A"),
            "switch_current_peak": (cycle.peak, "A"), "switch_current_rms": (math.sqrt(switch_square), "A"),
            "diode_current_peak": (cycle.peak, "A"), "diode_current_rms": (math.sqrt(diode_square), "A"),
            "diode_current_average": (cycle.diode * local_mean, "A"),
            "switch_voltage_stress": (stress, "V"), "diode_voltage_stress": (diode_stress, "V"),
            "input_capacitor_current_rms": (math.sqrt(source_variance), "A"),
            "output_capacitor_current_rms": (math.sqrt(capacitor_square), "A"),
            "output_ripple_esr_peak_to_peak": (esr_ripple, "V"),
            "switch_conduction_loss": (switch_loss, "W"), "diode_conduction_loss": (diode_loss, "W"),
            "inductor_copper_loss": (inductor_loss, "W"), "ripple_copper_correction": (ripple_copper, "W"),
            "capacitor_esr_loss": (esr_loss, "W"), "switch_overlap_loss": (overlap_loss, "W"),
            "gate_drive_loss": (gate_loss, "W"), "reverse_recovery_loss": (recovery_loss, "W"),
            "total_loss": (total_loss, "W"), "output_power": (output_power, "W"),
            "input_power_estimate": (input_power, "W"), "input_current_estimate": (input_power / vin, "A"),
            "waveform_input_current": (source_average, "A"), "efficiency": (output_power / input_power, "dimensionless"),
            "averaged_power_balance_residual": (vin * source_average - output_power - mean_copper - diode_loss, "W"),
            "rms_waveform_power_balance_residual": (vin * source_average - output_power - conduction_loss, "W"),
            "estimated_power_balance_residual": (input_power - output_power - total_loss, "W"),
        }
        for name, (value, unit) in values.items():
            result.add(name, value, unit)
        if design_inductance is not None:
            result.add("inductance_for_target_ripple", design_inductance, "H")
        if minimum_capacitance is not None:
            result.add("minimum_output_capacitance", minimum_capacitance, "F")
        if capacitance is not None:
            capacitive_ripple = charge_ripple / capacitance
            result.add("output_capacitance", capacitance, "F")
            result.add("output_ripple_capacitive_peak_to_peak", capacitive_ripple, "V")
            result.add("output_ripple_total_peak_to_peak", capacitive_ripple + esr_ripple, "V")
            if capacitive_ripple + esr_ripple > 0.1 * vo:
                invalid("output_capacitance", "predicted output ripple exceeds 10% of DC voltage; stiff-output approximation is unsupported", "electric.model_limit")
            if voltage_target is not None and capacitive_ripple + esr_ripple > voltage_target * (1 + 1e-9):
                result.warnings.append("The supplied output capacitance does not meet the requested total ripple target.")
            if cycle.mode == "ccm":
                _control(result, topology, cycle.duty, vo, io, induct, capacitance)
        if cycle.mode == "dcm":
            result.data["control_to_output_ideal_ccm"] = None
            result.data["control_omission_reason"] = "The CCM two-pole plant is invalid in DCM; no DCM dynamic model is calculated."
            if qrr:
                result.warnings.append("DCM diode current reaches zero before turn-on; the hard-commutation recovery-loss estimate is zero.")
        elif capacitance is None:
            result.data["control_to_output_ideal_ccm"] = None
            result.data["control_omission_reason"] = "Supply output_capacitance or target_output_ripple for ideal CCM control coefficients."
        if capacitance is None:
            result.warnings.append("Output capacitance was not supplied; the ESR ripple limit was checked, but total voltage ripple and the stiff-output assumption remain unverified.")
        result.csv(handle, "converter_intervals.csv", ["interval", "fraction", "inductor_start_A", "inductor_end_A"],
                   [[name, duration, start, end] for name, (duration, start, end) in zip(("switch_on", "diode_on", "idle"), segments)])
        return result.done("duty_cycle")

    return run("converter", inputs, compute)
