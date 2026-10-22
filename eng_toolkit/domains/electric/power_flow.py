"""Balanced power, feeder phasors and Fortescue sequence-network calculations."""

from __future__ import annotations

import cmath
import math
from typing import Annotated

from pydantic import Field

from eng_toolkit.domains.electric._common import Result, choice, fields, invalid, number, q, records, run
from eng_toolkit.domains.electric._server import server

_A = cmath.rect(1, 2*math.pi/3)


def _pair(raw, param, unit, *, passive=False):
    rows = records(raw, param, 1, required=True)
    fields(rows[0], (2,), param+"/0")
    z = complex(*(q(t, f"{param}/0/{i}", unit, signed=not passive, zero=True, hi=1e12) for i, t in enumerate(rows[0])))
    return z


def _phasor(z, unit):
    return {"real": z.real, "imag": z.imag, "magnitude": abs(z), "angle_deg": math.degrees(cmath.phase(z)), "unit": unit}


def _to_phase(seq):
    z0, z1, z2 = seq
    return (z0+z1+z2, z0+_A**2*z1+_A*z2, z0+_A*z1+_A**2*z2)


def _to_sequence(phase):
    a, b, c = phase
    return ((a+b+c)/3, (a+_A*b+_A**2*c)/3, (a+_A**2*b+_A*c)/3)


@server.tool(description="Balanced three-phase P/Q/S and capacitor correction; exact/approximate feeder drop; per-unit rebasing; Fortescue components and four sequence-network fault types. Fitzgerald/Kirtley steady-state RMS methods. No harmonics, cable ampacity, protection settings or compliance checks.")
def power_flow(
    mode: Annotated[str, Field(description="Calculation: balanced, feeder, per_unit, components or fault; each mode rejects unrelated quantity inputs.")] = "balanced",
    line_voltage: Annotated[str, Field(description="Receiving or prefault line-line RMS voltage, e.g. '400 V'; balanced, feeder and fault modes.")] = "",
    source: Annotated[str, Field(description="Balanced/feeder data source: power_pf, power_pq or phasors; phasor angles reference phase-A voltage.")] = "power_pf",
    real_power: Annotated[str, Field(description="Total consumed real power, e.g. '12 kW'; power_pf/power_pq modes; W family, '' omits for phasors.")] = "",
    reactive_power: Annotated[str, Field(description="Total signed reactive power, e.g. '9 kvar'; power_pq only, positive inductive; '' omitted otherwise.")] = "",
    line_current: Annotated[str, Field(description="Line RMS current magnitude, e.g. '25 A'; phasors source only, '' omitted for power inputs.")] = "",
    current_angle: Annotated[str, Field(description="Line-A current angle versus phase-A voltage, e.g. '-30 deg'; phasors source only, '' means 0 deg.")] = "",
    power_factor: Annotated[float, Field(description="Displacement power factor magnitude, 0.001..1; used only by power_pf source.")] = .8,
    power_factor_direction: Annotated[str, Field(description="Reactive direction for power_pf source: lagging or leading.")] = "lagging",
    connection: Annotated[str, Field(description="Load connection wye or delta for balanced/feeder winding phase values.")] = "wye",
    target_power_factor: Annotated[float, Field(description="Capacitor correction target 0.001..1, lagging; 0 omits correction. Balanced/feeder modes only.")] = 0.,
    frequency: Annotated[str, Field(description="Supply cyclic frequency, e.g. '50 Hz'; used for capacitor correction only.")] = "50 Hz",
    capacitor_connection: Annotated[str, Field(description="Correction bank connection: delta or wye; capacitance output is per branch.")] = "delta",
    feeder_resistance: Annotated[str, Field(description="One-conductor resistance per length, e.g. '0.2 ohm/km'; feeder mode required.")] = "",
    feeder_reactance: Annotated[str, Field(description="One-conductor inductive reactance per length, e.g. '0.1 ohm/km'; feeder mode required.")] = "",
    length: Annotated[str, Field(description="One-way feeder length, e.g. '100 m'; feeder mode only, '' omitted otherwise.")] = "",
    impedance: Annotated[str, Field(description="Per-unit mode 'real,imag' impedance, e.g. '1 ohm,2 ohm' or '.1 dimensionless,.2 dimensionless'; '' omits.")] = "",
    impedance_basis: Annotated[str, Field(description="Per-unit input basis: ohm or per_unit; new base outputs always include dimensionless impedance.")] = "ohm",
    base_voltage: Annotated[str, Field(description="Old line-line base voltage, e.g. '11 kV'; per_unit mode required.")] = "",
    base_power: Annotated[str, Field(description="Old three-phase apparent base, e.g. '10 MVA'; per_unit mode, VA family only.")] = "",
    new_base_voltage: Annotated[str, Field(description="New line-line voltage base, e.g. '33 kV'; per_unit mode, '' keeps old voltage base.")] = "",
    new_base_power: Annotated[str, Field(description="New three-phase apparent base, e.g. '20 MVA'; per_unit mode, '' keeps old apparent base.")] = "",
    phasors: Annotated[str, Field(description="Components mode: exactly 3 'real,imag' rows, e.g. '1 V,0 V;0 V,0 V;0 V,0 V'; <=1000 chars per row.")] = "",
    phasor_kind: Annotated[str, Field(description="Components quantity: voltage or current; all three phasors must use compatible V or A units.")] = "voltage",
    direction: Annotated[str, Field(description="Components direction: phase_to_sequence (abc to 012) or sequence_to_phase (012 to abc).")] = "phase_to_sequence",
    fault_type: Annotated[str, Field(description="Fault mode: three_phase, line_ground (a), line_line (b-c), or double_line_ground (b-c-ground).")] = "three_phase",
    positive_impedance: Annotated[str, Field(description="Fault positive-sequence Thevenin 'R,X', e.g. '0.1 ohm,1 ohm'; required, passive.")] = "",
    negative_impedance: Annotated[str, Field(description="Fault negative-sequence 'R,X', e.g. '0.1 ohm,1 ohm'; required for unbalanced faults, '' otherwise.")] = "",
    zero_impedance: Annotated[str, Field(description="Fault zero-sequence 'R,X', e.g. '0.2 ohm,3 ohm'; required for ground faults, '' otherwise.")] = "",
    fault_impedance: Annotated[str, Field(description="Fault path passive 'R,X', e.g. '0 ohm,0 ohm'; line_line is b-c path, ground types share ground path.")] = "0 ohm,0 ohm",
    peak_method: Annotated[str, Field(description="Peak factor: half_cycle approx. sqrt(2)(1+exp(-pi R/X)) of fault loop, or iec_kappa sqrt(2)*kappa from bolted R1/X1.")] = "half_cycle",
):
    inputs = locals()

    def compute(handle):
        selected = choice(mode, "mode", ("balanced", "feeder", "per_unit", "components", "fault"))
        r = Result({
            "balanced_power": "Fitzgerald-Kingsley-Umans RMS complex power S=3 Vphase Iphase*=sqrt(3) Vline Iline (cos(phi)+j sin(phi))",
            "feeder": "Vs_phase=Vr_phase+Iline(R+jX); approximate line drop=sqrt(3) I(R cos(phi)+X sin(phi))",
            "per_unit": "Zbase=Vline²/S3phase; Zpu_new=Zpu_old*(Snew/Sold)*(Vold/Vnew)²",
            "Fortescue": "[V0,V1,V2]=1/3 [[1,1,1],[1,a,a²],[1,a²,a]] [Va,Vb,Vc], a=exp(j 2pi/3)",
            "faults": "Sequence-network connections, Kirtley/Fortescue; prefault positive sequence only, no load or mutual coupling",
        }, "RMS sinusoidal fundamental, positive abc sequence. Balanced model uses line-neutral phase reference even for delta loads. Passive feeder and fault impedances. Lumped feeder shunt capacitance omitted.",
        "Steady-state balanced load or specified sequence Thevenin network; no saturation, harmonics, transient decay, cable sizing, protection coordination or code compliance. Peak factors are estimates, not an IEC 60909 study.")
        groups = {
            "balanced": ("line_voltage", "real_power", "reactive_power", "line_current", "current_angle"),
            "feeder": ("feeder_resistance", "feeder_reactance", "length"),
            "per_unit": ("impedance", "base_voltage", "base_power", "new_base_voltage", "new_base_power"),
            "components": ("phasors",),
            "fault": ("positive_impedance", "negative_impedance", "zero_impedance"),
        }
        active = set(groups[selected])
        if selected == "feeder":
            active.update(groups["balanced"])
        if selected == "fault":
            active.add("line_voltage")
        for group in groups.values():
            for name in group:
                if inputs[name] and name not in active:
                    invalid(name, f"quantity is not used in {selected} mode")
        if selected in ("balanced", "feeder"):
            vl = q(line_voltage, "line_voltage", "V", hi=1e7)
            src = choice(source, "source", ("power_pf", "power_pq", "phasors"))
            con = choice(connection, "connection", ("wye", "delta"))
            pf = number(power_factor, "power_factor", .001, 1)
            sign = 1 if choice(power_factor_direction, "power_factor_direction", ("lagging", "leading")) == "lagging" else -1
            if src == "phasors":
                if real_power or reactive_power:
                    invalid("real_power", "power inputs conflict with phasors source")
                il = q(line_current, "line_current", "A", hi=1e9)
                angle = q(current_angle or "0 deg", "current_angle", "rad", signed=True, zero=True, hi=2*math.pi)
                current = cmath.rect(il, angle)
                power = math.sqrt(3)*vl*current.conjugate()
                if power.real < -abs(power)*1e-12:
                    invalid("current_angle", "balanced load mode requires nonnegative real consumption")
                p, reactive = max(0., power.real), power.imag
                pf = p/abs(power)
            else:
                if line_current or current_angle:
                    invalid("line_current", "current phasors conflict with power source")
                p = q(real_power, "real_power", "W", power="real", hi=1e12)
                if src == "power_pf":
                    if reactive_power:
                        invalid("reactive_power", "reactive_power requires power_pq source")
                    reactive = sign*p*math.sqrt((1-pf)*(1+pf))/pf
                else:
                    reactive = q(reactive_power, "reactive_power", "var", power="reactive", signed=True, zero=True, hi=1e12)
                power = complex(p, reactive)
                current = power.conjugate()/(math.sqrt(3)*vl)
                il = abs(current)
                pf = p/abs(power)
            vphase = vl/(math.sqrt(3) if con == "wye" else 1)
            iphase = il/(math.sqrt(3) if con == "delta" else 1)
            for name, value, unit in (("real_power", p, "W"), ("reactive_power", reactive, "var"), ("apparent_power", abs(power), "VA"),
                                      ("power_factor", pf, "dimensionless"), ("line_current", il, "A"), ("phase_voltage", vphase, "V"), ("phase_current", iphase, "A")):
                r.add(name, value, unit)
            r.data["line_current_phasor"] = _phasor(current, "A")
            target = number(target_power_factor, "target_power_factor", 0, 1)
            if target:
                if target < .001 or target < pf or reactive < 0:
                    invalid("target_power_factor", "capacitor correction requires a lagging load and target >= existing power factor, >=0.001")
                f = q(frequency, "frequency", "Hz", hi=1e6)
                bank = choice(capacitor_connection, "capacitor_connection", ("delta", "wye"))
                newq = p*math.sqrt((1-target)*(1+target))/target
                qc = max(0., reactive-newq)
                newi = math.hypot(p, newq)/(math.sqrt(3)*vl)
                cap_v = vl if bank == "delta" else vl/math.sqrt(3)
                r.add("correction_reactive_power", qc, "var")
                r.add("capacitance_per_branch", qc/(3*2*math.pi*f*cap_v*cap_v), "F")
                r.add("corrected_line_current", newi, "A")
                r.add("current_reduction", il-newi, "A")
                r.add("resistive_loss_reduction_fraction", (il-newi)*(il+newi)/(il*il))
            if selected == "feeder":
                distance = q(length, "length", "m", hi=1e7)
                resistance = q(feeder_resistance, "feeder_resistance", "ohm/m", zero=True, hi=1e6)*distance
                reactance = q(feeder_reactance, "feeder_reactance", "ohm/m", zero=True, hi=1e6)*distance
                z = complex(resistance, reactance)
                vr = vl/math.sqrt(3)
                drop = current*z
                sending = vr+drop
                exact_drop = math.sqrt(3)*(2*vr*drop.real+abs(drop)**2)/(abs(sending)+vr)
                loss = 3*il*il*resistance
                r.add("voltage_drop_exact", exact_drop, "V")
                r.add("voltage_drop_approximate", math.sqrt(3)*drop.real, "V")
                r.add("sending_line_voltage", math.sqrt(3)*abs(sending), "V")
                r.add("feeder_loss", loss, "W")
                r.add("sending_real_power", p+loss, "W")
                r.add("sending_reactive_power", reactive+3*il*il*reactance, "var")
                r.data["sending_phase_phasor"] = _phasor(sending, "V")
                if target:
                    r.add("corrected_feeder_loss", 3*newi*newi*resistance, "W")
                if abs(drop) > .1*vr:
                    r.warnings.append("Feeder phasor drop exceeds 10% of receiving phase voltage; the approximate magnitude-drop formula may be inaccurate.")
            return r.done("real_power")
        if selected == "per_unit":
            vold = q(base_voltage, "base_voltage", "V", hi=1e7)
            sold = q(base_power, "base_power", "VA", power="apparent", hi=1e12)
            vnew = q(new_base_voltage, "new_base_voltage", "V", optional=True, hi=1e7) or vold
            snew = q(new_base_power, "new_base_power", "VA", power="apparent", optional=True, hi=1e12) or sold
            basis = choice(impedance_basis, "impedance_basis", ("ohm", "per_unit"))
            z = _pair(impedance, "impedance", "ohm" if basis == "ohm" else "dimensionless")
            zb_old, zb_new = vold*vold/sold, vnew*vnew/snew
            physical = z if basis == "ohm" else z*zb_old
            pu_old, pu_new = physical/zb_old, physical/zb_new
            r.add("old_base_impedance", zb_old, "ohm")
            r.add("new_base_impedance", zb_new, "ohm")
            r.add("old_base_current", sold/(math.sqrt(3)*vold), "A")
            r.add("new_base_current", snew/(math.sqrt(3)*vnew), "A")
            r.add("impedance_pu_magnitude", abs(pu_new))
            r.data.update(impedance_ohm=_phasor(physical, "ohm"), old_per_unit=_phasor(pu_old, "dimensionless"), new_per_unit=_phasor(pu_new, "dimensionless"))
            return r.done("impedance_pu_magnitude")
        if selected == "components":
            kind = choice(phasor_kind, "phasor_kind", ("voltage", "current"))
            way = choice(direction, "direction", ("phase_to_sequence", "sequence_to_phase"))
            unit = "V" if kind == "voltage" else "A"
            rows = records(phasors, "phasors", 3, required=True)
            if len(rows) != 3:
                invalid("phasors", "exactly three phasors are required")
            zs = []
            for i, row in enumerate(rows):
                fields(row, (2,), f"phasors/{i}")
                zs.append(complex(*(q(raw, f"phasors/{i}/{j}", unit, signed=True, zero=True, hi=1e12) for j, raw in enumerate(row))))
            phase, seq = (zs, _to_sequence(zs)) if way == "phase_to_sequence" else (_to_phase(zs), zs)
            r.data["phase"] = {key: _phasor(z, unit) for key, z in zip("abc", phase)}
            r.data["sequence"] = {str(i): _phasor(z, unit) for i, z in enumerate(seq)}
            r.data["a"] = _phasor(_A, "dimensionless")
            r.data["phase_to_sequence_matrix"] = [[_phasor(z/3, "dimensionless") for z in row] for row in ((1, 1, 1), (1, _A, _A**2), (1, _A**2, _A))]
            r.add("positive_sequence_magnitude", abs(seq[1]), unit)
            if abs(seq[1]) > 1e-12*max(abs(z) for z in zs):
                r.add("negative_sequence_unbalance", abs(seq[2])/abs(seq[1]))
                r.add("zero_sequence_unbalance", abs(seq[0])/abs(seq[1]))
            else:
                r.data["unbalance_factors"] = None
                r.warnings.append("Positive-sequence magnitude is zero or unresolved relative to inputs; normalized unbalance factors are omitted.")
            return r.done("positive_sequence_magnitude")
        ft = choice(fault_type, "fault_type", ("three_phase", "line_ground", "line_line", "double_line_ground"))
        method = choice(peak_method, "peak_method", ("half_cycle", "iec_kappa"))
        vl = q(line_voltage, "line_voltage", "V", hi=1e7)
        e = vl/math.sqrt(3)
        z1 = _pair(positive_impedance, "positive_impedance", "ohm", passive=True)
        zf = _pair(fault_impedance, "fault_impedance", "ohm", passive=True)
        z2 = _pair(negative_impedance, "negative_impedance", "ohm", passive=True) if ft != "three_phase" else 0j
        z0 = _pair(zero_impedance, "zero_impedance", "ohm", passive=True) if ft in ("line_ground", "double_line_ground") else 0j
        if ft == "three_phase" and (negative_impedance or zero_impedance):
            invalid("negative_impedance", "three-phase fault uses positive-sequence impedance only")
        if ft == "line_line" and zero_impedance:
            invalid("zero_impedance", "line-line fault does not use zero sequence")
        if abs(z1) < 1e-12:
            invalid("positive_impedance", "positive-sequence impedance must exceed 1e-12 ohm", "input.out_of_range")
        if ft == "three_phase":
            ze = z1+zf
            seq = (0j, e/ze, 0j)
        elif ft == "line_ground":
            ze = z1+z2+z0+3*zf
            seq = (e/ze, e/ze, e/ze)
        elif ft == "line_line":
            ze = z1+z2+zf
            seq = (0j, e/ze, -e/ze)
        else:
            branch = z2+z0+3*zf
            if abs(branch) < 1e-12:
                invalid("zero_impedance", "parallel sequence branch is singular", "electric.no_solution")
            ze = z1+z2*(z0+3*zf)/branch
            i1 = e/ze
            seq = (-i1*z2/branch, i1, -i1*(z0+3*zf)/branch)
        phase = _to_phase(seq)
        peak_current = max(abs(z) for z in phase)
        if method == "iec_kappa":
            # IEC 60909-0 takes kappa from the bolted three-phase (positive-
            # sequence) R/X and reuses it for line-line and earth faults; the
            # zero-sequence path and fault resistance do not enter kappa.
            rx1 = z1.real/z1.imag if z1.imag > 0 else None
            kappa = 1.02+.98*math.exp(-3*rx1) if rx1 is not None else 1.02
            factor = math.sqrt(2)*kappa
            r.add("kappa", kappa)
            if rx1 is not None:
                r.add("kappa_r_over_x", rx1)
            r.data["peak_factor_basis"] = "IEC 60909-0 kappa=1.02+0.98 exp(-3 R1/X1) from the bolted positive-sequence impedance, for every fault type; Zf and Z0 excluded"
        else:
            # Approximate: first-peak DC offset decaying with the whole fault-loop X/R.
            rx = ze.real/ze.imag if ze.imag > 0 else None
            factor = math.sqrt(2)*(1+math.exp(-math.pi*rx)) if rx is not None else math.sqrt(2)
            r.data["peak_factor_basis"] = "approximate half-cycle factor sqrt(2)(1+exp(-pi R/X)) from the effective fault-loop impedance including Z2, Z0 and fault resistance"
        r.add("fault_current", peak_current, "A")
        r.add("ground_current", abs(3*seq[0]), "A")
        r.add("asymmetrical_peak_factor", factor)
        r.add("asymmetrical_peak_current", factor*peak_current, "A")
        r.add("short_circuit_apparent_power", vl*vl/abs(z1), "VA")
        r.add("fault_apparent_power", math.sqrt(3)*vl*peak_current, "VA")
        r.data["x_over_r_basis"] = "effective fault-loop impedance (Z1+Z2+Z0+3Zf for line-ground), not the IEC kappa basis"
        if ze.real > 0:
            r.add("x_over_r", ze.imag/ze.real)
        else:
            r.data["x_over_r"] = None
            r.warnings.append("Zero fault-loop resistance gives unbounded X/R; it is omitted and a zero-resistance peak factor uses its finite limit.")
        r.data["peak_method"] = method
        r.data["fault_phase_currents"] = {key: _phasor(z, "A") for key, z in zip("abc", phase)}
        r.data["fault_sequence_currents"] = {str(i): _phasor(z, "A") for i, z in enumerate(seq)}
        vs = (-z0*seq[0], e-z1*seq[1], -z2*seq[2])
        r.data["fault_phase_voltages"] = {key: _phasor(z, "V") for key, z in zip("abc", _to_phase(vs))}
        r.warnings.append("Fault result assumes unloaded prefault voltage and supplied sequence networks. Peak-factor selection alone does not implement IEC 60909.")
        return r.done("fault_current")

    return run("power_flow", inputs, compute)
