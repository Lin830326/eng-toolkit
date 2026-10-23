"""Balanced three-phase induction motor with a stator-referred T circuit."""

import math
from typing import Annotated

from pydantic import Field

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.electric._common import (
    Result, bisect, choice, count, invalid, number, q, run,
)
from eng_toolkit.domains.electric._server import server


def _thevenin(voltage, r1, x1, xm, rc):
    ym = complex(0 if rc is None else 1 / rc, -1 / xm)
    zm = 1 / ym
    z1 = complex(r1, x1)
    return voltage * zm / (z1 + zm), z1 * zm / (z1 + zm), ym


def _point(s, voltage, r1, x1, r2, x2, ym, omega_sync, fixed_loss, delta):
    """Use rotor admittance so synchronous speed has a finite circuit limit."""
    yr = s / complex(r2, s * x2)
    node = voltage / (1 + complex(r1, x1) * (ym + yr))
    i1 = node * (ym + yr)
    i2 = node * yr
    stator = 3 * abs(i1) ** 2 * r1
    core = 3 * abs(node) ** 2 * ym.real
    rotor = 3 * abs(i2) ** 2 * r2
    airgap = 3 * abs(node) ** 2 * s * r2 / (r2 * r2 + (s * x2) ** 2)
    # The input-current real part can lose all precision against reactive
    # current. These nonnegative circuit powers retain the real input power.
    pin = math.fsum((stator, core, airgap))
    converted = airgap * (1 - s)
    shaft = converted - fixed_loss
    speed = omega_sync * (1 - s)
    apparent = 3 * voltage * abs(i1)
    return {
        "slip": s, "speed": speed, "electromagnetic_torque": airgap / omega_sync,
        "shaft_torque": shaft / speed if speed > 0 else None,
        "line_current": abs(i1) * (math.sqrt(3) if delta else 1),
        "phase_current": abs(i1), "rotor_current": abs(i2),
        "power_factor": pin / apparent,
        "efficiency": shaft / pin if 0 < s < 1 and shaft > 0 and pin > 0 else None,
        "input_power": pin, "apparent_power": apparent,
        "reactive_power": -3 * voltage * i1.imag,
        "stator_copper_loss": stator, "core_loss": core, "rotor_copper_loss": rotor,
        "airgap_power": airgap, "converted_power": converted, "shaft_power": shaft,
        "power_balance_residual": pin - stator - core - rotor - fixed_loss - shaft,
    }


@server.tool(description="Chapman/Fitzgerald three-phase induction motor: exact T circuit, Thevenin torque, stable shaft-load root, V/f and CSV. Linear unsaturated steady state; assumed nameplate fit is nonunique.")
def induction_motor(
    mode: Annotated[str, Field(description="Use equivalent for supplied per-phase parameters, or nameplate for an assumed one-point circuit fit.")] = "equivalent",
    line_voltage: Annotated[str, Field(description="RMS line-to-line supply, e.g. '460 V'; base voltage when base_frequency is supplied.")] = "",
    frequency: Annotated[str, Field(description="Operating electrical frequency, e.g. '60 Hz'; supported range 0.1 to 10000 Hz.")] = "60 Hz",
    poles: Annotated[int, Field(description="Even magnetic pole count from 2 through 200.")] = 4,
    connection: Annotated[str, Field(description="Winding connection, wye or delta; circuit impedances are per winding phase.")] = "wye",
    r1: Annotated[str, Field(description="Stator resistance per phase, e.g. '0.641 ohm'; required in equivalent mode.")] = "",
    x1: Annotated[str, Field(description="Stator leakage reactance per phase at base frequency, e.g. '1.106 ohm'.")] = "",
    xm: Annotated[str, Field(description="Magnetizing reactance per phase at base frequency, e.g. '26.3 ohm'.")] = "",
    r2: Annotated[str, Field(description="Stator-referred rotor resistance per phase before its multiplier, e.g. '0.332 ohm'.")] = "",
    x2: Annotated[str, Field(description="Stator-referred rotor leakage reactance at base frequency, e.g. '0.464 ohm'.")] = "",
    rc: Annotated[str, Field(description="Optional shunt core-loss resistance per phase, e.g. '240 ohm'; omission means zero core loss.")] = "",
    mechanical_loss: Annotated[str, Field(description="Constant friction and windage loss, e.g. '300 W'; default assumes zero loss.")] = "0 W",
    stray_loss: Annotated[str, Field(description="Constant stray load loss, e.g. '120 W'; default assumes zero loss.")] = "0 W",
    slip: Annotated[float, Field(description="Operating slip in (0,1); zero selects speed, load torque, nameplate slip or default 0.05.")] = 0,
    speed: Annotated[str, Field(description="Operating speed or nameplate speed at base frequency, e.g. '1755 rpm' or '183.78 rad/s'.")] = "",
    load_torque: Annotated[str, Field(description="Positive shaft load, e.g. '70 N*m'; solves the stable motoring branch including fixed losses.")] = "",
    line_current: Annotated[str, Field(description="Required nameplate RMS line current, e.g. '20 A'; unused in equivalent mode.")] = "",
    power_factor: Annotated[float, Field(description="Required nameplate lagging power factor in (0,1); zero means unspecified.")] = 0,
    output_power: Annotated[str, Field(description="Required nameplate shaft output power, e.g. '7.5 kW' or '10 hp'.")] = "",
    magnetizing_current_fraction: Annotated[float, Field(description="Nameplate fit assumes Vphase/Xm equals this fraction of nameplate phase current, from 0.01 to 0.9.")] = 0.3,
    base_frequency: Annotated[str, Field(description="Base electrical frequency, e.g. '60 Hz'; V/f voltage scales below base and is capped above base.")] = "",
    rotor_resistance_multiplier: Annotated[float, Field(description="Multiplier on stator-referred rotor resistance, from 0.01 to 100.")] = 1,
    curve_points: Annotated[int, Field(description="Number of evenly spaced slip samples in the CSV curve, including 0 and 1; from 3 to 201.")] = 101,
) -> ToolResult:
    inputs = dict(locals())

    def compute(handle):
        kind = choice(mode, "mode", ("equivalent", "nameplate"))
        winding = choice(connection, "connection", ("wye", "delta"))
        n = count(poles, "poles", 2, 200)
        if n % 2:
            invalid("poles", "pole count must be even")
        points = count(curve_points, "curve_points", 3, 201)
        f = q(frequency, "frequency", "Hz", lo=0.1, hi=10000)
        fbase = q(base_frequency, "base_frequency", "Hz", optional=True, lo=0.1, hi=10000)
        fbase = f if fbase is None else fbase
        vbase_line = q(line_voltage, "line_voltage", "V", lo=1e-3, hi=1e7)
        delta = winding == "delta"
        vbase_phase = vbase_line if delta else vbase_line / math.sqrt(3)
        ratio = f / fbase
        voltage = vbase_phase * min(ratio, 1)
        vline = vbase_line * min(ratio, 1)
        omega_sync = 4 * math.pi * f / n
        omega_base = 4 * math.pi * fbase / n
        mech = q(mechanical_loss, "mechanical_loss", "W", zero=True, power="real")
        stray = q(stray_loss, "stray_loss", "W", zero=True, power="real")
        losses = mech + stray
        s_input = number(slip, "slip", 0, 1)
        if s_input and not 1e-8 <= s_input <= 1 - 1e-8:
            invalid("slip", "operating slip must be from 1e-8 through 1-1e-8", "input.out_of_range")
        speed_value = q(speed, "speed", "rad/s", optional=True, lo=1e-8, hi=1e7)
        load = q(load_torque, "load_torque", "N*m", optional=True)
        pf = number(power_factor, "power_factor", 0, 1)
        fraction = number(magnetizing_current_fraction, "magnetizing_current_fraction", 0.01, 0.9)
        multiplier = number(rotor_resistance_multiplier, "rotor_resistance_multiplier", 0.01, 100)
        resistance = q(rc, "rc", "ohm", optional=True, lo=1e-8, hi=1e8)
        current = q(line_current, "line_current", "A", optional=True, lo=1e-8, hi=1e8)
        rated_power = q(output_power, "output_power", "W", optional=True, power="real")
        circuit = {key: q(raw, key, "ohm", optional=kind == "nameplate", lo=1e-8, hi=1e8)
                   for key, raw in (("r1", r1), ("x1", x1), ("xm", xm), ("r2", r2), ("x2", x2))}
        if load is not None and (s_input or (kind == "equivalent" and speed_value is not None)):
            invalid("load_torque", "choose one operating condition: slip, speed or load torque")
        if kind == "equivalent" and s_input and speed_value is not None:
            invalid("speed", "choose operating speed or slip")

        r = Result({
            "synchronous_speed": "ns=120*f/poles rpm; omega_s=4*pi*f/poles; s=(omega_s-omega)/omega_s",
            "thevenin": "Zm=1/(1/Rc+1/(j*Xm)); Vth=Vphase*Zm/(R1+j*X1+Zm); Zth=(R1+j*X1)*Zm/(R1+j*X1+Zm)",
            "torque": "Te=3*abs(Vth)^2*(R2/s)/(omega_s*((Rth+R2/s)^2+(Xth+X2)^2))",
            "breakdown": "sm=R2/sqrt(Rth^2+(Xth+X2)^2); Tmax=3*abs(Vth)^2/[2*omega_s*(Rth+sqrt(Rth^2+(Xth+X2)^2))]",
            "losses": "Pgap=3*abs(I2)^2*R2/s; Pcu2=s*Pgap; Pconv=(1-s)*Pgap; Pshaft=Pconv-Pmechanical-Pstray",
            "shaft_load": "Tshaft=Te-(Pmechanical+Pstray)/(omega_s*(1-s)); stable solution lies before peak Tshaft",
            "kloss": "Tapprox=2*Tmax/(s/sm+sm/s); relative error=(Tapprox-Te)/Te for Te>0",
            "vf": "V=Vbase*min(f/fbase,1); X=Xbase*f/fbase; ideal torque envelope=min(1,fbase/f)",
        },
            "Balanced sinusoidal three-phase steady state; per-phase parameters are stator referred. "
            "Linear magnetizing branch, constant resistances and fixed mechanical/stray losses. "
            "Omitted Rc means zero core loss. Shaft-load stability assumes constant applied load torque.",
            "Positive motoring only; operating slip 1e-8..1-1e-8. Curve endpoints use circuit limits; "
            "efficiency is null for endpoints or nonpositive shaft output. No saturation, harmonics, "
            "skin effect, temperature change, thermal rating or transient starting model. Roots use at most 100 iterations; CSV at most 201 points.")

        if kind == "nameplate":
            if any(value is not None for value in circuit.values()) or resistance is not None:
                invalid("mode", "nameplate fit requires omitted circuit parameters and Rc")
            if current is None:
                invalid("line_current", "nameplate fit requires line current", "input.missing")
            if rated_power is None:
                invalid("output_power", "nameplate fit requires shaft output power", "input.missing")
            if speed_value is None:
                invalid("speed", "nameplate fit requires rated speed", "input.missing")
            if not 0 < pf < 1:
                invalid("power_factor", "nameplate fit requires a lagging power factor strictly between zero and one")
            sn = 1 - speed_value / omega_base
            if not 1e-8 <= sn <= 1 - 1e-8:
                invalid("speed", "nameplate speed must be positive and below base synchronous speed")
            iphase = current / math.sqrt(3) if delta else current
            pin = math.sqrt(3) * vbase_line * current * pf
            pag = (rated_power + losses) / (1 - sn)
            stator_loss = pin - pag
            if stator_loss <= 0:
                invalid("output_power", "nameplate power and losses leave no positive stator copper loss", "electric.no_solution")
            r1v = stator_loss / (3 * iphase * iphase)
            zmagnitude = vbase_phase / iphase
            zin = zmagnitude * complex(pf, math.sqrt(1 - pf * pf))
            xmv = zmagnitude / fraction

            def inferred_rotor(x):
                return 1 / (1 / (zin - complex(r1v, x)) + complex(0, 1 / xmv))

            def equal_leakage(x):
                return inferred_rotor(x).imag - x

            if equal_leakage(0) <= 0:
                invalid("magnetizing_current_fraction", "chosen magnetizing fraction cannot produce positive equal leakage reactances", "electric.no_solution")
            xv = bisect(equal_leakage, 0, zin.imag, "magnetizing_current_fraction")
            r2v = sn * inferred_rotor(xv).real
            circuit = {"r1": r1v, "x1": xv, "xm": xmv, "r2": r2v, "x2": xv}
            if any(not 1e-8 <= value <= 1e8 for value in circuit.values()):
                invalid("mode", "inferred circuit falls outside supported 1e-8..1e8 ohm range", "input.out_of_range")
            _, _, ym_base = _thevenin(vbase_phase, r1v, xv, xmv, None)
            matched = _point(sn, vbase_phase, r1v, xv, r2v, xv, ym_base, omega_base, losses, delta)
            r.data["nameplate_fit"] = {
                "nonunique": True, "slip": sn, "input_power_W": pin,
                "output_power_W": rated_power, "efficiency": rated_power / pin,
                "shaft_torque_Nm": rated_power / speed_value,
                "magnetizing_current_fraction": fraction,
                "assumptions": "Rc=infinity, X1=X2, Xm=Vphase/(fraction*Iphase). Infer R1 from the power budget and solve equal leakage from measured input impedance.",
                "line_current_residual_A": matched["line_current"] - current,
                "power_factor_residual": matched["power_factor"] - pf,
                "output_power_residual_W": matched["shaft_power"] - rated_power,
            }
            r.warnings.append("Nameplate fit is nonunique. Assumed magnetizing current, equal leakage and zero core loss determine this estimated circuit; starting and breakdown predictions require measured circuit tests for validation.")
            selected_slip = s_input or sn
        else:
            if current is not None or rated_power is not None or pf or fraction != 0.3:
                invalid("mode", "nameplate current, power, power factor and fit assumptions require nameplate mode")
            selected_slip = s_input or (1 - speed_value / omega_sync if speed_value is not None else 0.05)

        r1v = circuit["r1"]
        x1v, xmv, x2v = (circuit[key] * ratio for key in ("x1", "xm", "x2"))
        r2v = circuit["r2"] * multiplier
        vth, zth, ym = _thevenin(voltage, r1v, x1v, xmv, resistance)
        reactance = zth.imag + x2v
        magnitude = math.hypot(zth.real, reactance)
        sm = r2v / magnitude
        peak = 3 * abs(vth) ** 2 / (2 * omega_sync * (zth.real + magnitude))
        constant = 3 * abs(vth) ** 2 / omega_sync

        def torque(s):
            return constant * r2v * s / ((r2v + zth.real * s) ** 2 + (reactance * s) ** 2)

        def shaft_torque(s):
            return torque(s) - losses / (omega_sync * (1 - s)) if losses else torque(s)

        accessible_slip = min(sm, 1)
        if losses:
            def slope(s):
                denominator = (r2v + zth.real * s) ** 2 + (reactance * s) ** 2
                return constant * r2v * (r2v * r2v - magnitude * magnitude * s * s) / denominator ** 2 - losses / (omega_sync * (1 - s) ** 2)

            upper = min(accessible_slip, 1 - 1e-12)
            if slope(0) <= 0:
                shaft_peak_slip = 0
            elif slope(upper) >= 0:
                shaft_peak_slip = upper
            else:
                shaft_peak_slip = bisect(slope, 0, upper, "mechanical_loss")
            shaft_peak = shaft_torque(shaft_peak_slip)
        else:
            shaft_peak_slip = accessible_slip
            shaft_peak = torque(shaft_peak_slip)
        if load is not None:
            if shaft_peak <= 0 or load > shaft_peak:
                invalid("load_torque", "shaft load exceeds the available stable motoring maximum", "electric.no_solution")
            if load >= shaft_peak:
                selected_slip = shaft_peak_slip
            else:
                selected_slip = bisect(lambda s: shaft_torque(s) - load, 0, shaft_peak_slip, "load_torque")
        if not 1e-8 <= selected_slip <= 1 - 1e-8:
            invalid("load_torque" if load is not None else "speed" if speed_value is not None else "slip",
                    "operating point must have slip in 1e-8..1-1e-8", "electric.no_solution")
        operating = _point(selected_slip, voltage, r1v, x1v, r2v, x2v, ym, omega_sync, losses, delta)
        if operating["shaft_power"] <= 0:
            invalid("load_torque" if load is not None else "speed" if speed_value is not None else "slip",
                    "fixed losses exceed converted power at this operating point", "electric.no_solution")
        if selected_slip > shaft_peak_slip:
            r.warnings.append("The supplied operating point is on the unstable branch for a constant shaft torque load.")

        for name, value, unit in (
            ("synchronous_speed", omega_sync, "rad/s"), ("synchronous_speed_rpm", 120 * f / n, "rpm"),
            ("synchronous_frequency", f, "Hz"), ("applied_line_voltage", vline, "V"),
            ("thevenin_voltage", abs(vth), "V"), ("thevenin_voltage_real", vth.real, "V"),
            ("thevenin_voltage_imag", vth.imag, "V"), ("thevenin_resistance", zth.real, "ohm"),
            ("thevenin_reactance", zth.imag, "ohm"), ("breakdown_slip", sm, "dimensionless"),
            ("breakdown_torque", peak, "N*m"), ("motoring_peak_slip", accessible_slip, "dimensionless"),
            ("motoring_peak_torque", torque(accessible_slip), "N*m"),
            ("maximum_shaft_torque", shaft_peak, "N*m"), ("maximum_shaft_torque_slip", shaft_peak_slip, "dimensionless"),
            ("mechanical_loss", mech, "W"), ("stray_loss", stray, "W"),
            ("ideal_vf_torque_factor", min(1, 1 / ratio), "dimensionless"),
            ("ideal_vf_power_multiplier", min(ratio, 1), "dimensionless"),
        ):
            r.add(name, value, unit)
        for key, value in circuit.items():
            r.add("base_" + key, value, "ohm")
        r.add("effective_r2", r2v, "ohm")
        units = {"speed": "rad/s", "electromagnetic_torque": "N*m", "shaft_torque": "N*m",
                 "line_current": "A", "phase_current": "A", "rotor_current": "A",
                 "apparent_power": "VA", "reactive_power": "var"}
        for key, value in operating.items():
            r.add(key, value, units.get(key, "dimensionless" if key in ("slip", "power_factor", "efficiency") else "W"))
        start = _point(1, voltage, r1v, x1v, r2v, x2v, ym, omega_sync, losses, delta)
        r.add("starting_torque", start["electromagnetic_torque"], "N*m")
        r.add("starting_line_current", start["line_current"], "A")
        r.data["starting"] = {"efficiency": None, "shaft_torque": None, "note": "Starting torque is electromagnetic torque; fixed power loss cannot define shaft torque at zero speed."}
        r.data["operating_stable"] = selected_slip <= shaft_peak_slip
        r.data["breakdown_in_motoring_range"] = sm <= 1
        r.data["vf"] = {"base_frequency_Hz": fbase, "frequency_ratio": ratio,
                           "region": "constant_torque" if ratio <= 1 else "field_weakening",
                           "assumptions": "Ideal constant-flux torque envelope below base, constant-power envelope above base. Exact circuit includes stator drop; fixed losses and Rc do not scale. No low-speed boost or thermal/current limit is modeled."}
        r.data["curve_efficiency_note"] = "Null CSV fields mean undefined efficiency at slip 0 or 1, or nonpositive net shaft output. Torque column is electromagnetic torque."
        rows, errors = [], []
        for index in range(points):
            s = index / (points - 1)
            p = _point(s, voltage, r1v, x1v, r2v, x2v, ym, omega_sync, losses, delta)
            kloss = 2 * peak * s * sm / (s * s + sm * sm)
            error = (kloss - p["electromagnetic_torque"]) / p["electromagnetic_torque"] if s else None
            if error is not None:
                errors.append(abs(error))
            rows.append([s, p["speed"] * 30 / math.pi, p["electromagnetic_torque"], p["line_current"],
                         p["power_factor"], p["efficiency"], kloss, error])
        selected_kloss = 2 * peak * selected_slip * sm / (selected_slip ** 2 + sm * sm)
        r.add("kloss_torque", selected_kloss, "N*m")
        r.add("kloss_relative_error", (selected_kloss - operating["electromagnetic_torque"]) / operating["electromagnetic_torque"])
        r.add("kloss_curve_max_absolute_relative_error", max(errors))
        r.csv(handle, "induction_motor_curve.csv", ["slip", "speed_rpm", "electromagnetic_torque_Nm",
              "line_current_A", "power_factor", "efficiency", "kloss_torque_Nm", "kloss_relative_error"], rows)
        return r.done("shaft_torque")

    return run("induction_motor", inputs, compute)
