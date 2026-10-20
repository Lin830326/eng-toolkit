"""``control_feedback``: a unity-or-sensor feedback loop from plant G, controller C and sensor H (continuous time).

With G = Gn/Gd, C = Cn/Cd, H = Hn/Hd (the signal chain is r -> (+/-) -> C -> G -> y, with H in the feedback path):
* open loop (loop transfer function)   L = C G H = Ln/Ld,  Ln = Cn Gn Hn,  Ld = Cd Gd Hd
* characteristic polynomial            P = Ld + Ln
* closed loop                          T = C G / (1 + C G H) = Cn Gn Hd / P
* sensitivity                          S = 1 / (1 + L)  = Ld / P
* control sensitivity                  U = C S = Cn Gd Hd / P       (the input r -> controller output)
* system type = number of poles of L at the origin (minus zeros there); static error constants
  Kp = lim L(s), Kv = lim s L(s), Ka = lim s^2 L(s) (s -> 0).
* actuating error e = r - H y:  e_ss = lim s R(s) / (1 + L(s));  with H = 1 this is the usual 1/(1+Kp), 1/Kv, 1/Ka.
* tracking error  r - y:  e_ss = lim s R(s) (1 - T(s)), exact for any H, for R = 1/s (step), 1/s^2 (ramp), 1/s^3 (parabola);
  the limit is read from the lowest-order coefficients (exact, no numerical limit).  Only meaningful if the closed loop is stable.
* peak of the sensitivity Ms = max |S(j w)|: guaranteed margins GM >= Ms/(Ms - 1) and PM >= 2 asin(1/(2 Ms)).
"""

from __future__ import annotations

import math
from typing import Annotated, Any

import numpy as np
from pydantic import Field

from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.control import _lti as lti
from eng_toolkit.domains.control import _poly as poly
from eng_toolkit.domains.control._common import (
    FREQ_RANGE,
    Computed,
    DenominatorIn,
    JobHandle,
    NumeratorIn,
    build_tf,
    check_order,
    clean,
    complex_out,
    dominant,
    numerical_checks,
    plain,
    pole_rows,
    poly_out,
    qty,
    run,
    stability_report,
)
from eng_toolkit.domains.control._server import server
from eng_toolkit.units import UnitsError

SCAN_POINTS = 6000


def limit_at_zero(num: np.ndarray, den: np.ndarray, shift: int = 0) -> float | None:
    """``lim_{s -> 0} s^shift num(s)/den(s)`` read from the lowest-order non-zero coefficients; ``None`` means infinite.

    ``shift`` may be negative (a ramp error divides by s).  Exact: the polynomials' zero coefficients are exact zeros.
    """
    vn, cn = poly.lowest_order(num)
    if cn == 0.0:
        return 0.0
    vd, cd = poly.lowest_order(den)
    if vn + shift > vd:
        return 0.0
    if vn + shift == vd:
        return cn / cd
    return None


def _fmt(x: float | None) -> float | str:
    return "infinite" if x is None else x


@server.tool(
    name="feedback",
    description=(
        "Feedback loop of plant G, controller C and sensor H (continuous; each as numerator/denominator coefficient lists in descending "
        "powers or polynomial text; controller and sensor default to 1). Returns open-loop L = C G H, closed-loop T = C G/(1 + C G H) "
        "and sensitivity S = 1/(1 + L) as polynomials, closed-loop poles (natural frequency, damping) and exact stability, system type, "
        "position/velocity/acceleration error constants Kp Kv Ka, steady-state errors for step, ramp and parabolic inputs "
        "(actuating and tracking error), and the peak of |S| with the margins it guarantees. The loop L must be proper."
    ),
)
def feedback(
    plant_numerator: Annotated[str, Field(description="Plant numerator, e.g. '[1]' or '1'")],
    plant_denominator: Annotated[str, Field(description="Plant denominator, e.g. '[1, 3, 2, 0]' or 's (s+1)(s+2)'")],
    controller_numerator: Annotated[str, Field(description="Controller numerator (default '1', i.e. a unit gain)")] = "1",
    controller_denominator: Annotated[str, Field(description="Controller denominator (default '1'); a PID is e.g. numerator '[Kd, Kp, Ki]' over '[1, 0]'")] = "1",
    sensor_numerator: Annotated[str, Field(description="Sensor / feedback-path numerator (default '1', unity feedback)")] = "1",
    sensor_denominator: Annotated[str, Field(description="Sensor / feedback-path denominator (default '1')")] = "1",
) -> ToolResult:
    inputs = {
        "plant_numerator": plant_numerator, "plant_denominator": plant_denominator, "controller_numerator": controller_numerator,
        "controller_denominator": controller_denominator, "sensor_numerator": sensor_numerator, "sensor_denominator": sensor_denominator,
    }

    def compute(_: JobHandle) -> Computed:
        g = build_tf(plant_numerator, plant_denominator, num_name="plant_numerator", den_name="plant_denominator")
        c = build_tf(controller_numerator, controller_denominator, num_name="controller_numerator", den_name="controller_denominator")
        h = build_tf(sensor_numerator, sensor_denominator, num_name="sensor_numerator", den_name="sensor_denominator")
        ln = check_order(poly.mul(poly.mul(c.num, g.num), h.num), name="open_loop_numerator")
        ld = check_order(poly.mul(poly.mul(c.den, g.den), h.den), name="open_loop_denominator")
        if len(ln) > len(ld):
            raise UnitsError(
                "input.invalid",
                f"the open loop C G H is improper (numerator order {len(ln) - 1} above denominator order {len(ld) - 1}); add a pole to the controller (a derivative filter) or use a strictly proper plant",
                {"param": "controller_numerator", "numerator_order": len(ln) - 1, "denominator_order": len(ld) - 1},
            )
        char = check_order(poly.add(ld, ln), name="characteristic_polynomial")
        if char[0] == 0.0:
            raise UnitsError("input.invalid", "1 + C G H is identically zero: the closed loop does not exist", {"param": "controller_numerator"})
        if len(char) != len(ld):
            raise UnitsError(
                "input.invalid",
                "the leading coefficients of L cancel in 1 + L (an algebraic loop: L(infinity) = -1): the closed loop is ill-posed and has an infinite pole",
                {"param": "controller_numerator"},
            )
        t_num = check_order(poly.mul(poly.mul(c.num, g.num), h.den), name="closed_loop_numerator")
        s_num = ld
        u_num = check_order(poly.mul(poly.mul(c.num, g.den), h.den), name="control_sensitivity_numerator")
        err_num = poly.add(char, -t_num)  # P - Cn Gn Hd : numerator of 1 - T

        warnings: list[str] = []
        cl_poles = poly.roots(char)
        stab = stability_report(char, None, cl_poles)
        warnings += stab["_warnings"]
        ol_poles = poly.roots(ld)
        ol_stab = stability_report(ld, None, ol_poles)
        stable = stab["verdict"] == "stable"
        if not stable:
            warnings.append(f"the closed loop is {stab['verdict']}: steady-state errors are not meaningful and are reported as not applicable")

        kn, cn_ = poly.lowest_order(ln)
        kd, cd_ = poly.lowest_order(ld)
        net = kd - kn
        system_type = max(net, 0)
        kp, kv, ka = limit_at_zero(ln, ld, 0), limit_at_zero(ln, ld, 1), limit_at_zero(ln, ld, 2)
        act = [limit_at_zero(ld, char, shift) for shift in (0, -1, -2)]
        trk = [limit_at_zero(err_num, char, shift) for shift in (0, -1, -2)]
        t_dc = limit_at_zero(t_num, char, 0)
        if net < 0:
            warnings.append("L has a zero at the origin that outweighs its poles there (differentiating loop): the error constants shown are what the limits give")

        # sensitivity peak on a log grid around the closed- and open-loop dynamics
        mags = np.concatenate([np.abs(r)[np.abs(r) > 0] for r in (cl_poles, ol_poles, poly.roots(ln))] or [np.array([1.0])])
        lo, hi = (float(mags.min()) / 100.0, float(mags.max()) * 100.0) if mags.size else (0.01, 100.0)
        if hi / lo < 1e3:
            lo, hi = lo / 10.0, hi * 10.0
        lo, hi = max(lo, FREQ_RANGE[0]), min(hi, FREQ_RANGE[1])
        grid = lti.log_grid(lo, hi, SCAN_POINTS)
        s_tf, t_tf = lti.TF(s_num, char, None), lti.TF(t_num, char, None)
        ms = lti.peak_magnitude(lambda w: lti.tf_eval(s_tf, w), grid)
        mt = lti.peak_magnitude(lambda w: lti.tf_eval(t_tf, w), grid)
        s_peak = max(ms["magnitude"], 1.0)  # |S(j infinity)| = 1 for a proper loop with L(infinity) = 0
        if ms["at_edge"] and ms["magnitude"] > 1.0 + 1e-9:
            warnings.append("the sensitivity peak lies at the edge of the searched frequency range: the true peak may be larger")
        if ms["magnitude"] >= 1e6:
            warnings.append("|S| is huge somewhere (the closed loop has a pole on or very near the imaginary axis)")
        guaranteed_gm = s_peak / (s_peak - 1.0) if s_peak > 1.0 + 1e-12 and ms["magnitude"] < 1e6 else None
        guaranteed_pm = math.degrees(2.0 * math.asin(min(1.0, 1.0 / (2.0 * s_peak)))) if s_peak > 0.5 else 180.0
        checks = numerical_checks(t_num, char, poly.roots(t_num), cl_poles, None)
        warnings += checks["warnings"]

        values: dict[str, QuantityOut] = {
            "sensitivity_peak": plain(s_peak, label="sensitivity_peak"),
            "sensitivity_peak_frequency": qty(ms["frequency"], "radian / second", label="sensitivity_peak_frequency"),
            "complementary_sensitivity_peak": plain(max(mt["magnitude"], 0.0), label="complementary_sensitivity_peak"),
            "closed_loop_unstable_poles": plain(float(stab["_counts"][0]), label="closed_loop_unstable_poles"),
            "system_type": plain(float(system_type), label="system_type"),
        }
        if guaranteed_gm is not None:
            values["guaranteed_gain_margin"] = plain(guaranteed_gm, label="guaranteed_gain_margin")
        values["guaranteed_phase_margin"] = qty(guaranteed_pm, "degree", label="guaranteed_phase_margin")
        for key, val, unit in (("position_error_constant", kp, "dimensionless"), ("velocity_error_constant", kv, "1 / second"), ("acceleration_error_constant", ka, "1 / second ** 2")):
            if val is not None:
                values[key] = qty(val, unit, label=key)
        if t_dc is not None:
            values["closed_loop_dc_gain"] = plain(t_dc, label="closed_loop_dc_gain")
        names = ("step", "ramp", "parabolic")
        if stable:
            for name, a_, t_ in zip(names, act, trk):
                if a_ is not None:
                    values[f"{name}_actuating_error"] = plain(a_, label=f"{name}_actuating_error")
                if t_ is not None:
                    values[f"{name}_tracking_error"] = plain(t_, label=f"{name}_tracking_error")

        def na(v: float | None) -> Any:
            return _fmt(v) if stable else "not applicable (closed loop not stable)"

        data: dict[str, Any] = {
            "open_loop": {"definition": "L = C G H", "numerator": poly_out(ln), "denominator": poly_out(ld)},
            "closed_loop": {"definition": "T = C G / (1 + C G H)", "numerator": poly_out(t_num), "denominator": poly_out(char)},
            "sensitivity": {"definition": "S = 1 / (1 + L)", "numerator": poly_out(s_num), "denominator": poly_out(char)},
            "control_sensitivity": {"definition": "U = C S (reference to controller output)", "numerator": poly_out(u_num), "denominator": poly_out(char)},
            "common_factor_note": "no common factors are cancelled: a factor shared by the numerator and denominator of T is a mode that stays in the closed loop (its pole is in 'poles')",
            "poles": pole_rows(cl_poles, None),
            "dominant_pole": pole_rows(dominant(cl_poles, None), None) if cl_poles.size else [],
            "open_loop_poles": [complex_out(r) for r in ol_poles],
            "open_loop_unstable_poles": ol_stab["_counts"][0],
            "stability": stab,
            "system_type": system_type,
            "system_type_definition": "poles of L at the origin minus zeros of L at the origin",
            "error_constants": {"Kp": _fmt(kp), "Kv": _fmt(kv), "Ka": _fmt(ka), "definition": "Kp = lim L, Kv = lim s L, Ka = lim s^2 L as s -> 0"},
            "steady_state_errors": {
                "applicable": stable,
                "step": {"actuating_error": na(act[0]), "tracking_error": na(trk[0])},
                "ramp": {"actuating_error": na(act[1]), "tracking_error": na(trk[1]), "unit": "per unit slope"},
                "parabolic": {"actuating_error": na(act[2]), "tracking_error": na(trk[2]), "unit": "per unit acceleration"},
                "definitions": {
                    "actuating_error": "e = r - H y, e_ss = lim s R/(1+L); with H = 1: 1/(1+Kp), 1/Kv, 1/Ka",
                    "tracking_error": "r - y, e_ss = lim s R (1 - T), exact for any sensor H",
                },
            },
            "closed_loop_dc_gain": _fmt(t_dc),
            "sensitivity_peak": {
                "value": s_peak, "frequency_rad_s": ms["frequency"], "searched_range_rad_s": [float(grid[0]), float(grid[-1])],
                "guaranteed_gain_margin": guaranteed_gm, "guaranteed_phase_margin_deg": guaranteed_pm,
                "meaning": "a loop with Ms = max|S| has at least these gain and phase margins (lower bounds, not the margins themselves)",
                "complementary_sensitivity_peak": mt["magnitude"], "complementary_sensitivity_peak_frequency_rad_s": mt["frequency"],
            },
            "numerical_checks": checks,
        }
        return Computed(values=values, primary="sensitivity_peak", warnings=warnings, data=clean(data))

    return run("control_feedback", inputs, compute)


__all__ = ["feedback", "limit_at_zero"]
