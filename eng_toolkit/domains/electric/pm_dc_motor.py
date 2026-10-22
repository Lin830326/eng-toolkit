"""Steady PM motor models in coherent SI, with explicit winding conventions."""

from __future__ import annotations

import math
import re
from typing import Annotated

import numpy as np
from numpy.polynomial import polynomial as poly
from pydantic import Field

from eng_toolkit.domains.electric._common import _LITERAL, Result, choice, count, fields, invalid, q, records, run
from eng_toolkit.domains.electric._server import server
from eng_toolkit.units import ureg

_ANGULAR_NAMES = ("radian", "degree", "revolution", "rpm", "turn")


def _speed_constant(raw, param, unit, *, optional=False):
    """Refuse cyclic-rate factors (Hz, cps) in a per-angular-speed constant.

    Pint treats Hz as 1/s, so '0.05 V/Hz' would silently become 0.05 V*s/rad,
    2*pi away from its per-revolution meaning. Angular spellings stay valid.
    """
    value = q(raw, param, unit, optional=optional, hi=1e9)
    if value is None:
        return None
    units = re.sub(r"\s*([*/^])\s*", r"\1", _LITERAL.fullmatch(raw.strip()).group(2).replace("**", "^"))
    for factor in re.split(r"[*/\s]+", units):
        name = factor.split("^")[0]
        if not name or name == "1":
            continue
        parsed = ureg.parse_units(name)
        if ureg.Quantity(1, parsed).check("1/[time]") and not any(k in str(parsed) for k in _ANGULAR_NAMES):
            invalid(param, f"cyclic-rate unit {name!r} is ambiguous (per radian or per revolution); "
                    "use V*s/rad, V*s/revolution, V/rpm or V/krpm", "units.dimension_mismatch")
    return value


def _field_weakening(requested, omega, resistance, ld, lq, flux, pairs, vmax, imax):
    """Minimum-current point with exactly the requested torque on the voltage boundary.

    Along the torque hyperbola iq=T/(1.5 p s), s=psi+(Ld-Lq) id>0, the current
    squared id²+iq² is strictly convex in id. When MTPA violates the voltage
    limit, the constrained minimum is therefore a hyperbola/voltage-ellipse
    intersection. Multiplying the voltage margin by s² gives a polynomial of
    degree <=4 in id; every real root is a candidate and the least current wins.
    """
    electrical = pairs*omega
    tau = requested/(1.5*pairs)
    delta = ld-lq
    s = np.array([flux, delta])
    vd = poly.polysub(poly.polymul([0.0, resistance], s), [electrical*lq*tau])
    vq = poly.polyadd([resistance*tau], electrical*poly.polymul([flux, ld], s))
    margin = poly.polysub(poly.polysub(vmax*vmax*poly.polymul(s, s), poly.polymul(vd, vd)), poly.polymul(vq, vq))
    margin = np.trim_zeros(np.asarray(margin, dtype=float), "b")
    if len(margin) < 2:
        return None
    # Scale id by the current limit so the companion matrix is well balanced.
    scaled = margin*imax**np.arange(len(margin))
    roots = poly.polyroots(scaled/np.max(np.abs(scaled)))*imax
    slope = poly.polyder(margin)
    candidates = []
    for root in roots:
        direct = float(root.real)
        # Newton polish on the unscaled polynomial; stops when the step underflows.
        for _ in range(100):
            derivative = poly.polyval(direct, slope)
            if derivative == 0 or not math.isfinite(derivative):
                break
            step = poly.polyval(direct, margin)/derivative
            if not math.isfinite(step) or direct-step == direct:
                break
            direct -= step
        positive = flux+delta*direct
        if not math.isfinite(direct) or positive <= 0:
            continue
        quadrature = tau/positive
        pv = _dq(direct, quadrature, omega, resistance, ld, lq, flux, pairs)
        # Each candidate carries exactly the requested torque; keep only those
        # inside the voltage limit at the final operating-point tolerance.
        if math.hypot(pv[0], pv[1]) <= vmax*(1+1e-9):
            candidates.append((math.hypot(direct, quadrature), direct, quadrature))
    return min(candidates) if candidates else None


def _mtpa(amplitude, flux, delta):
    if delta == 0:
        return 0.0, amplitude
    # Rationalized quadratic root avoids cancellation as saliency tends to zero.
    direct = 2 * delta * amplitude**2 / (flux + math.sqrt(flux**2 + 8 * delta**2 * amplitude**2))
    return direct, math.sqrt(max(0.0, amplitude**2 - direct**2))


def _dq(direct, quadrature, omega, resistance, ld, lq, flux, pairs):
    electrical = pairs * omega
    vd = resistance * direct - electrical * lq * quadrature
    vq = resistance * quadrature + electrical * (ld * direct + flux)
    torque = 1.5 * pairs * (flux + (ld - lq) * direct) * quadrature
    return vd, vq, torque


def _envelope(omega, resistance, ld, lq, flux, pairs, imax, vmax):
    """Maximize positive torque over the intersection of voltage ellipse and current disk."""
    electrical = pairs * omega
    # q=0 voltage boundary bounds every positive-torque solution because its
    # linear q coefficient is 2 R we (psi + (Ld-Lq) id), which is nonnegative.
    a = resistance**2 + (electrical * ld)**2
    center = -electrical**2*ld*flux/a
    halfwidth_squared = vmax*vmax/a-(resistance*electrical*flux/a)**2
    if halfwidth_squared < 0:
        return None
    halfwidth = math.sqrt(halfwidth_squared)
    low, high = max(-imax, center-halfwidth), min(imax, center+halfwidth)
    delta = ld-lq
    if delta > 0:
        low = max(low, -flux/delta)
    elif delta < 0:
        high = min(high, -flux/delta)
    if low > high:
        return None

    def point(direct):
        current_q = math.sqrt(max(0.0, imax*imax-direct*direct))
        aa = resistance**2 + (electrical*lq)**2
        bb = 2*resistance*electrical*(flux+delta*direct)
        cc = resistance**2*direct**2 + electrical**2*(ld*direct+flux)**2-vmax*vmax
        # Stable nonnegative root; tiny positive cc is boundary roundoff.
        disc = max(0.0, bb*bb-4*aa*cc)
        divisor = math.sqrt(disc)+bb
        voltage_q = max(0.0, -2*cc/divisor) if divisor > 0 else 0.0
        quadrature = min(current_q, voltage_q)
        torque = 1.5*pairs*(flux+delta*direct)*quadrature
        return torque, direct, quadrature

    if ld == lq:
        # Surface PM has a circular voltage locus even with R>0. The maximum
        # q point and its intersections with the current circle suffice exactly.
        norm = resistance**2+(electrical*ld)**2
        center_d = -electrical**2*ld*flux/norm
        center_q = -resistance*electrical*flux/norm
        voltage_radius = vmax/math.sqrt(norm)
        candidates = [point(low), point(high)]
        for direct in (0.0, center_d):
            if low <= direct <= high:
                candidates.append(point(direct))
        distance = math.hypot(center_d, center_q)
        if distance > 0 and abs(imax-voltage_radius) <= distance <= imax+voltage_radius:
            along = (imax*imax-voltage_radius*voltage_radius+distance*distance)/(2*distance)
            across = math.sqrt(max(0.0, imax*imax-along*along))
            for sign in (-1, 1):
                direct = along*center_d/distance+sign*across*(-center_q)/distance
                quadrature = along*center_q/distance+sign*across*center_d/distance
                if low <= direct <= high and quadrature >= 0:
                    candidates.append(point(direct))
        return max(candidates)
    # A fixed scan finds local maxima; each bracket has an explicit convergence
    # check and a hard 64-step cap. At most eight distinct maxima are refined.
    grid = np.linspace(low, high, 129)
    sampled = [point(float(x)) for x in grid]
    candidates = [sampled[0], sampled[-1], max(sampled)]
    mtpa_d, _ = _mtpa(imax, flux, delta)
    if low <= mtpa_d <= high:
        candidates.append(point(mtpa_d))
    brackets = 0
    for index in range(1, len(sampled)-1):
        if (sampled[index][0] >= sampled[index-1][0] and sampled[index][0] >= sampled[index+1][0]
                and (sampled[index][0] > sampled[index-1][0] or sampled[index][0] > sampled[index+1][0])):
            brackets += 1
            if brackets > 8:
                invalid("speed", "too many envelope extrema for the bounded solver", "electric.nonconvergence")
            left, right = float(grid[index-1]), float(grid[index+1])
            ratio = (math.sqrt(5)-1)/2
            x1, x2 = right-ratio*(right-left), left+ratio*(right-left)
            y1, y2 = point(x1), point(x2)
            for _ in range(64):
                if right-left <= 1e-11*max(imax, 1e-15):
                    break
                if y1[0] > y2[0]:
                    right, x2, y2 = x2, x1, y1
                    x1 = right-ratio*(right-left)
                    y1 = point(x1)
                else:
                    left, x1, y1 = x1, x2, y2
                    x2 = left+ratio*(right-left)
                    y2 = point(x2)
            else:
                invalid("speed", "envelope bracket did not converge within 64 refinements", "electric.nonconvergence")
            candidates.extend((y1, y2))
    return max(candidates)


def _thermal(result, copper, thermal_resistance, allowed_temperature_rise):
    if bool(thermal_resistance) != bool(allowed_temperature_rise):
        invalid("thermal_resistance", "thermal resistance and allowed temperature rise must be supplied together")
    if thermal_resistance:
        theta = q(thermal_resistance, "thermal_resistance", "K/W", hi=1e9)
        rise = q(allowed_temperature_rise, "allowed_temperature_rise", "K", hi=1e6)
        actual = copper*theta
        result.add("temperature_rise", actual, "K")
        result.add("temperature_rise_margin", rise-actual, "K")
        result.data["thermal_check"] = "pass within stated steady copper-loss limit" if actual <= rise else "fail: copper-loss temperature rise exceeds stated limit"


@server.tool(name="pm_dc_motor", description="Chapman/Fitzgerald PM DC, Boldea-Nasar trapezoidal BLDC and Krause peak-amplitude PMSM dq estimates. Constant R, flux and inductance; steady nonnegative motoring. Returns operating points, thermal screening, SVPWM current/voltage envelopes and CSV curves. Ignores saturation, switching, iron loss and demagnetization; unreviewed, not a rating or protection certification.")
def pm_dc_motor(
    mode: Annotated[str, Field(description="Motor model: pm_dc, pmsm, or bldc_trapezoidal.")] = "pm_dc",
    voltage: Annotated[str, Field(description="DC terminal supply or PMSM inverter DC bus voltage, e.g. 48 V.")] = "",
    resistance: Annotated[str, Field(description="DC terminal or BLDC line-line resistance; PMSM phase resistance, e.g. 0.2 ohm.")] = "",
    torque_constant: Annotated[str, Field(description="DC or trapezoidal BLDC torque per terminal current, e.g. 0.1 N*m/A.")] = "",
    back_emf_constant: Annotated[str, Field(description="Optional DC/BLDC line-line EMF constant, e.g. 0.1 V*s/rad or 10 V/krpm; must match torque constant within 1e-3; no Hz.")] = "",
    torque: Annotated[str, Field(description="DC/PMSM electromagnetic torque, e.g. 1 N*m; nonnegative and exclusive with explicit dq currents.")] = "",
    speed: Annotated[str, Field(description="Mechanical speed, e.g. 100 rad/s or 1500 rpm; DC accepts speed or torque, not both.")] = "",
    mechanical_loss: Annotated[str, Field(description="All modes: fixed mechanical loss, e.g. 5 W; default zero; PMSM applies it only at the requested point.")] = "",
    current_limit: Annotated[str, Field(description="Optional DC terminal current limit; required PMSM peak phase current limit, e.g. 10 A.")] = "",
    voltage_limit: Annotated[str, Field(description="All modes: optional supply or bus ceiling, e.g. 60 V; supplied voltage must stay within it.")] = "",
    thermal_resistance: Annotated[str, Field(description="All modes: optional winding-to-ambient thermal resistance, e.g. 2 K/W; requires allowed temperature rise.")] = "",
    allowed_temperature_rise: Annotated[str, Field(description="All modes: allowed winding rise, e.g. 40 K, paired with thermal resistance; absolute degC is invalid.")] = "",
    pole_pairs: Annotated[int, Field(description="PMSM electrical pole-pair count, integer 1 through 100.")] = 1,
    d_inductance: Annotated[str, Field(description="PMSM d-axis phase inductance in H, e.g. 0.5 mH.")] = "",
    q_inductance: Annotated[str, Field(description="PMSM q-axis phase inductance in H, e.g. 0.8 mH.")] = "",
    flux_linkage: Annotated[str, Field(description="PMSM permanent-magnet peak phase flux linkage, e.g. 0.05 Wb.")] = "",
    d_current: Annotated[str, Field(description="PMSM signed peak d-axis current, e.g. -5 A; must accompany q-axis current.")] = "",
    q_current: Annotated[str, Field(description="PMSM nonnegative peak q-axis current, e.g. 10 A; must accompany d-axis current.")] = "",
    speed_points: Annotated[str, Field(description="Optional PMSM envelope speeds, e.g. 0 rad/s;100 rad/s; at most 100 semicolon-separated entries.")] = "",
    curve_points: Annotated[int, Field(description="Number of automatically spaced motor curve rows, integer 2 through 100.")] = 41,
):
    inputs = locals()
    def compute(handle):
        model = choice(mode, "mode", ("pm_dc", "pmsm", "bldc_trapezoidal"))
        n = count(curve_points, "curve_points", 2, 100)
        supply = q(voltage, "voltage", "V", hi=1e9)
        winding_r = q(resistance, "resistance", "ohm", lo=1e-12, hi=1e9)
        loss = q(mechanical_loss, "mechanical_loss", "W", zero=True, optional=True, hi=1e12, power="real") or 0.0
        maximum_voltage = q(voltage_limit, "voltage_limit", "V", optional=True, hi=1e9)
        if maximum_voltage is not None and supply > maximum_voltage:
            invalid("voltage", "supply exceeds the stated voltage limit", "electric.limit_exceeded")
        maximum_current = q(current_limit, "current_limit", "A", optional=True, hi=1e9)
        result = Result(
            ["V=E+IR; E=ke*omega; Te=kt*I", "vd=R*id-we*Lq*iq; vq=R*iq+we*(Ld*id+psi)",
             "Te=1.5*p*(psi*iq+(Ld-Lq)*id*iq); Vphase_peak=Vdc/sqrt(3)"],
            ["Steady motoring; constant winding resistance and magnetic parameters; no inverter switching or iron loss.",
             "DC torque input is electromagnetic torque. Shaft power subtracts the supplied fixed mechanical loss.",
             "PMSM uses amplitude-invariant Clarke/Park phase-peak currents, voltages and flux; mechanical speed is rad/s."],
            ["Textbook estimates following Chapman, Fitzgerald and Krause; winding conventions must match supplied constants.",
             "Current and voltage bounds are operating limits, not a protection, demagnetization or thermal certification."],
        )
        if model != "pmsm":
            for name, value in (("d_inductance", d_inductance), ("q_inductance", q_inductance),
                                ("flux_linkage", flux_linkage), ("d_current", d_current),
                                ("q_current", q_current), ("speed_points", speed_points)):
                if value:
                    invalid(name, "this parameter applies only to pmsm mode")
            if pole_pairs != 1:
                invalid("pole_pairs", "pole pairs apply only to pmsm mode")
            kt = _speed_constant(torque_constant, "torque_constant", "N*m/A")
            ke = _speed_constant(back_emf_constant, "back_emf_constant", "V*s/rad", optional=True)
            # Rounded datasheet constants (e.g. 5.236 V/krpm for 0.05 N*m/A)
            # are accepted within 1e-3 relative; torque_constant is then used.
            if ke is not None and abs(ke-kt) > 1e-3*kt:
                invalid("back_emf_constant", "coherent terminal SI constants require ke=kt within 1e-3 relative; convert winding conventions first")
            if ke is not None and abs(ke-kt) > 1e-9*kt:
                result.warnings.append(f"back_emf_constant differs from torque_constant by {abs(ke-kt)/kt:.3g} relative "
                                       "(within the 1e-3 tolerance); torque_constant is used for both.")
            ke = kt
            stall_current, no_load = supply/winding_r, supply/ke
            stall_torque = kt*stall_current
            maximum_power = supply*supply/(4*winding_r)
            if loss >= maximum_power:
                invalid("mechanical_loss", "fixed mechanical loss must be below maximum converted mechanical power")
            no_load_current = 2*loss/(supply+math.sqrt(supply*supply-4*winding_r*loss))
            actual_no_load = (supply-winding_r*no_load_current)/ke
            if torque and speed:
                invalid("torque", "provide DC electromagnetic torque or speed, not both")
            if torque:
                te = q(torque, "torque", "N*m", zero=True, hi=1e12)
                current = te/kt
                omega = (supply-winding_r*current)/ke
            elif speed:
                omega = q(speed, "speed", "rad/s", zero=True, hi=1e9)
                current = (supply-ke*omega)/winding_r
                te = kt*current
            else:
                current = min(stall_current/2, maximum_current) if maximum_current else stall_current/2
                te, omega = kt*current, (supply-winding_r*current)/ke
            if current < -1e-12*stall_current or omega < -1e-12*no_load:
                invalid("torque" if torque else "speed", "requested point is outside the nonnegative motoring torque-speed line")
            current, omega = max(0.0, current), max(0.0, omega)
            if maximum_current is not None and current > maximum_current*(1+1e-10):
                invalid("current_limit", "operating current exceeds the stated current limit", "electric.limit_exceeded")
            converted, pin, copper = te*omega, supply*current, current*current*winding_r
            if converted < loss-1e-10*max(loss, 1e-15):
                invalid("mechanical_loss", "fixed loss exceeds converted mechanical power at the operating point")
            shaft = max(0.0, converted-loss)
            for key, val, unit in (("current", current, "A"), ("speed", omega, "rad/s"),
                                   ("electromagnetic_torque", te, "N*m"), ("back_emf", ke*omega, "V"),
                                   ("input_power", pin, "W"), ("copper_loss", copper, "W"),
                                   ("shaft_power", shaft, "W"), ("stall_current", stall_current, "A"),
                                   ("stall_torque", stall_torque, "N*m"), ("no_load_speed", actual_no_load, "rad/s"),
                                   ("ideal_no_load_speed", no_load, "rad/s"), ("no_load_current", no_load_current, "A"),
                                   ("maximum_converted_power", maximum_power, "W"),
                                   ("maximum_shaft_power", maximum_power-loss, "W"),
                                   ("maximum_power_torque", stall_torque/2, "N*m"),
                                   ("maximum_power_speed", no_load/2, "rad/s")):
                result.add(key, val, unit)
            result.data["efficiency"] = shaft/pin if pin > 0 else None
            result.data["shaft_torque"] = shaft/omega if omega > 0 else None
            if pin > 0:
                result.add("efficiency", shaft/pin)
            if omega > 0:
                result.add("shaft_torque", shaft/omega, "N*m")
            optimum_i = math.sqrt(loss/winding_r) if loss else 0.0
            optimum_omega = (supply-winding_r*optimum_i)/ke
            result.add("maximum_efficiency_current", optimum_i, "A")
            result.add("maximum_efficiency_speed", optimum_omega, "rad/s")
            result.add("maximum_efficiency_electromagnetic_torque", kt*optimum_i, "N*m")
            result.add("maximum_efficiency_shaft_torque", kt*optimum_i-loss/optimum_omega, "N*m")
            result.add("maximum_efficiency", 1-2*math.sqrt(loss*winding_r)/supply if loss else 1.0)
            result.data["maximum_efficiency_note"] = "unconstrained voltage-line optimum; zero-loss value is a zero-output limiting efficiency"
            feasible_power_i = min(stall_current/2, maximum_current or stall_current)
            result.add("maximum_current_limited_shaft_power", (supply-winding_r*feasible_power_i)*feasible_power_i-loss, "W")
            feasible_efficiency_i = min(optimum_i, maximum_current or stall_current)
            feasible_efficiency = (1-winding_r*feasible_efficiency_i/supply-loss/(supply*feasible_efficiency_i)
                                   if feasible_efficiency_i > 0 else 1.0)
            result.add("maximum_current_limited_efficiency", feasible_efficiency)
            result.data["stall_note"] = "theoretical supply-voltage stall values; a current controller may impose a lower stall torque"
            result.add("current_limited_stall_torque", kt*min(stall_current, maximum_current or stall_current), "N*m")
            if model == "bldc_trapezoidal":
                result.add("phase_flat_back_emf_constant", kt/2, "V*s/rad")
                result.add("line_line_flat_back_emf_constant", kt, "V*s/rad")
                result.add("phase_rms_current", math.sqrt(2/3)*current, "A")
                result.add("speed_constant", 60/(2*math.pi*kt), "rpm/V")
                result.data["assumptions"].append("Ideal 120-degree BLDC conduction: two phases carry terminal current; PLL=2*Ephase_flat*I, kt=2*ke_phase_flat=ke_line_line_flat; R is the two-phase series resistance. Boldea-Nasar convention.")
            rows = []
            for fraction in np.linspace(0, 1, n):
                amps = float(fraction)*stall_current
                w = (supply-winding_r*amps)/ke
                electromagnetic = kt*amps
                pshaft = electromagnetic*w-loss
                feasible = pshaft >= 0 and (maximum_current is None or amps <= maximum_current*(1+1e-10))
                rows.append([w, electromagnetic, amps, supply*amps, amps*amps*winding_r,
                             pshaft, pshaft/(supply*amps) if amps > 0 and feasible else None, feasible])
            result.csv(handle, "pm_dc_curve.csv", ["speed_rad_s", "electromagnetic_torque_N_m", "current_A", "input_power_W", "copper_loss_W", "shaft_power_W", "efficiency", "within_stated_limits"], rows)
            _thermal(result, copper, thermal_resistance, allowed_temperature_rise)
            return result.done("shaft_power")

        if torque_constant or back_emf_constant:
            invalid("torque_constant", "pmsm uses flux linkage and pole pairs instead of DC constants")
        pairs = count(pole_pairs, "pole_pairs", 1, 100)
        ld = q(d_inductance, "d_inductance", "H", lo=1e-12, hi=1e3)
        lq = q(q_inductance, "q_inductance", "H", lo=1e-12, hi=1e3)
        flux = q(flux_linkage, "flux_linkage", "Wb", lo=1e-12, hi=1e6)
        omega = q(speed, "speed", "rad/s", zero=True, hi=1e7)
        if maximum_current is None:
            invalid("current_limit", "pmsm requires a peak phase current limit", "input.missing")
        if bool(d_current) != bool(q_current):
            invalid("d_current", "explicit d and q currents must be supplied together")
        if d_current and torque:
            invalid("torque", "torque and explicit dq currents are mutually exclusive")
        vmax = supply/math.sqrt(3)
        limit = _envelope(omega, winding_r, ld, lq, flux, pairs, maximum_current, vmax)
        if limit is None:
            invalid("speed", "no feasible current vector within voltage and current limits", "electric.no_solution")
        if d_current:
            direct = q(d_current, "d_current", "A", zero=True, signed=True, hi=1e9)
            quadrature = q(q_current, "q_current", "A", zero=True, hi=1e9)
        elif torque:
            requested = q(torque, "torque", "N*m", zero=True, hi=1e12)
            if requested > limit[0]*(1+1e-8):
                invalid("torque", "requested torque exceeds the voltage/current envelope", "electric.limit_exceeded")
            if requested == 0:
                electrical = pairs*omega
                norm = winding_r**2+(electrical*ld)**2
                center = -electrical**2*ld*flux/norm
                halfwidth = math.sqrt(max(0.0, vmax*vmax/norm-(winding_r*electrical*flux/norm)**2))
                direct, quadrature = min(0.0, center+halfwidth), 0.0
            else:
                low, high = 0.0, maximum_current
                for _ in range(64):
                    amplitude = (low+high)/2
                    trial_d, trial_q = _mtpa(amplitude, flux, ld-lq)
                    trial_t = _dq(trial_d, trial_q, omega, winding_r, ld, lq, flux, pairs)[2]
                    if trial_t < requested:
                        low = amplitude
                    else:
                        high = amplitude
                if high-low > 1e-11*maximum_current:
                    invalid("torque", "MTPA current bracket failed to converge", "electric.nonconvergence")
                direct, quadrature = _mtpa(high, flux, ld-lq)
                trial = _dq(direct, quadrature, omega, winding_r, ld, lq, flux, pairs)
                if math.hypot(trial[0], trial[1]) > vmax*(1+1e-9):
                    # MTPA violates the voltage limit: solve the torque hyperbola
                    # against the voltage ellipse directly (exact requested torque).
                    best = _field_weakening(requested, omega, winding_r, ld, lq, flux, pairs, vmax, maximum_current)
                    if best is not None and best[0] <= maximum_current*(1+1e-8):
                        # One-sided optimality check: the envelope only evaluates
                        # feasible points, so reaching the torque with 1e-6 less
                        # current would prove the root search missed a better point.
                        lower = _envelope(omega, winding_r, ld, lq, flux, pairs, best[0]*(1-1e-6), vmax)
                        if lower is not None and lower[0] >= requested:
                            invalid("torque", "field-weakening root search missed a lower-current point", "electric.nonconvergence")
                        direct, quadrature = best[1], best[2]
                    elif requested >= limit[0]*(1-1e-6):
                        # Near the maximum-torque tangency the two intersections
                        # merge; the envelope point is within 1e-6 of the request.
                        direct, quadrature = limit[1], limit[2]
                        result.warnings.append("Requested torque is within 1e-6 relative of the voltage/current envelope; the envelope point is returned.")
                    else:
                        invalid("torque", "field-weakening solver found no voltage-boundary point for a torque below the envelope", "electric.nonconvergence")
        else:
            direct, quadrature = limit[1], limit[2]
        vd, vq, te = _dq(direct, quadrature, omega, winding_r, ld, lq, flux, pairs)
        current, required_voltage = math.hypot(direct, quadrature), math.hypot(vd, vq)
        if current > maximum_current*(1+1e-8):
            invalid("d_current", "dq vector exceeds the peak phase current limit", "electric.limit_exceeded")
        if required_voltage > vmax*(1+1e-8):
            invalid("speed", "dq operating point exceeds SVPWM phase voltage limit", "electric.limit_exceeded")
        if te < 0:
            invalid("d_current", "only nonnegative electromagnetic motoring torque is supported")
        copper = 1.5*winding_r*current*current
        converted = te*omega
        # The dq dot product cancels equal reactive cross terms. Sum its real
        # power identity instead, retaining tiny torque/copper contributions.
        pin = math.fsum((converted, copper))
        if converted < loss:
            invalid("mechanical_loss", "fixed loss exceeds converted mechanical power at the operating point")
        for key, val, unit in (("d_current", direct, "A"), ("q_current", quadrature, "A"),
                               ("phase_peak_current", current, "A"), ("phase_rms_current", current/math.sqrt(2), "A"),
                               ("d_voltage", vd, "V"), ("q_voltage", vq, "V"),
                               ("phase_peak_voltage", required_voltage, "V"), ("phase_voltage_limit", vmax, "V"),
                               ("electromagnetic_torque", te, "N*m"), ("speed", omega, "rad/s"),
                               ("input_power", pin, "W"), ("copper_loss", copper, "W"),
                               ("shaft_power", converted-loss, "W"), ("envelope_torque", limit[0], "N*m")):
            result.add(key, val, unit)
        if pin > 0:
            result.add("efficiency", (converted-loss)/pin)
        result.data["efficiency"] = (converted-loss)/pin if pin > 0 else None
        result.data["shaft_torque"] = (converted-loss)/omega if omega > 0 else None
        if omega > 0:
            result.add("shaft_torque", (converted-loss)/omega, "N*m")
        mtpa_d, mtpa_q = _mtpa(maximum_current, flux, ld-lq)
        result.add("mtpa_d_current_at_limit", mtpa_d, "A")
        result.add("mtpa_q_current_at_limit", mtpa_q, "A")
        result.add("mtpa_current_angle", math.atan2(mtpa_q, mtpa_d), "rad")
        result.data["mtpa_angle_convention"] = "Angle from the positive d axis toward the positive q axis at the stated current limit."
        # Voltage magnitude at fixed MTPA current is quadratic in electrical speed.
        aa = (lq*mtpa_q)**2 + (ld*mtpa_d+flux)**2
        bb = 2*winding_r*mtpa_q*(flux+(ld-lq)*mtpa_d)
        cc = (winding_r*maximum_current)**2-vmax*vmax
        base = (-2*cc/(bb+math.sqrt(bb*bb-4*aa*cc)))/pairs if cc < 0 else 0.0
        result.add("base_speed_at_current_limit", base, "rad/s")
        result.data["base_speed_note"] = "First voltage boundary at full-current MTPA; zero means full current cannot be sustained at standstill."
        result.data["envelope_method"] = "Surface PM: analytic circle intersections. Salient PM: 129 bounded samples, at most eight local maxima, 64 refinements each; current bracket tolerance 1e-11 of current limit. Requested field-weakening torque: real roots of the quartic torque-hyperbola/voltage-ellipse intersection, Newton-polished, least current kept."
        grid_rows = records(speed_points, "speed_points")
        if grid_rows:
            speeds = []
            for index, row in enumerate(grid_rows):
                fields(row, (1,), f"speed_points/{index}")
                speeds.append(q(row[0], f"speed_points/{index}", "rad/s", zero=True, hi=1e7))
        else:
            end = min(1e7, max(omega, 2*base, vmax/(pairs*flux)))
            speeds = np.linspace(0.0, end, n).tolist()
        envelope = []
        for value in speeds:
            point = _envelope(value, winding_r, ld, lq, flux, pairs, maximum_current, vmax)
            if point is None:
                envelope.append([value, None, None, None, None, None, False])
            else:
                td, di, qi = point
                pv = _dq(di, qi, value, winding_r, ld, lq, flux, pairs)
                envelope.append([value, td, di, qi, math.hypot(pv[0], pv[1]), math.hypot(di, qi), True])
        result.csv(handle, "pmsm_envelope.csv", ["speed_rad_s", "torque_N_m", "d_current_A", "q_current_A", "phase_peak_voltage_V", "phase_peak_current_A", "within_stated_limits"], envelope)
        _thermal(result, copper, thermal_resistance, allowed_temperature_rise)
        return result.done("electromagnetic_torque")

    return run("pm_dc_motor", inputs, compute)
