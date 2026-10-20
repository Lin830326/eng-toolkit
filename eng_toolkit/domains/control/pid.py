"""``control_pid_tune``: classical PID tuning rules for a first-order-plus-dead-time (FOPDT) process, with a simulated check.

Process model  G(s) = K exp(-L s) / (T s + 1)   (K gain, T time constant, L dead time), controller in the standard (ISA) form

    C(s) = Kc (1 + 1/(Ti s) + Td s)           parallel form:  Kp = Kc, Ki = Kc/Ti, Kd = Kc Td

Rules (r = L/T, lambda = the closed-loop time constant for IMC and SIMC)
* ziegler_nichols (open-loop reaction curve, Ziegler and Nichols 1942):
    P   Kc = T/(K L)
    PI  Kc = 0.9 T/(K L),  Ti = L/0.3
    PID Kc = 1.2 T/(K L),  Ti = 2 L,  Td = 0.5 L
* cohen_coon (Cohen and Coon 1953):
    P   Kc = (1/K)(T/L)(1 + r/3)
    PI  Kc = (1/K)(T/L)(0.9 + r/12),   Ti = L (30 + 3 r)/(9 + 20 r)
    PID Kc = (1/K)(T/L)(4/3 + r/4),    Ti = L (32 + 6 r)/(13 + 8 r),  Td = 4 L/(11 + 2 r)
* imc (internal model control / lambda tuning, Rivera, Morari and Skogestad 1986; first-order Pade for the delay in the PID case):
    PI  Kc = T/(K (lambda + L)),  Ti = T
    PID Kc = (T + L/2)/(K (lambda + L/2)),  Ti = T + L/2,  Td = T L/(2 T + L)
* simc (Skogestad 2003, tau_c = lambda):
    PI  Kc = T/(K (lambda + L)),  Ti = min(T, 4 (lambda + L))
    PID the same with Td = 0 (a FOPDT process has no second lag to cancel, so SIMC's PID equals its PI)

The closed loop (unity feedback) is then simulated with the delay replaced by an (n, n) Pade approximation of the stated order
and a derivative filter Td s / (1 + Td s / N) (N = 10 by default); overshoot, rise time, settling time (2 %) and the closed-loop
pole count come from that model.  The gain and phase margins use the EXACT delay exp(-j w L), not the Pade approximation.
The numbers are textbook starting points for a model-based first guess, not a finished tuning.
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
    Computed,
    JobHandle,
    capped,
    choice,
    clean,
    is_blank,
    numerical_checks,
    plain,
    plain_number,
    qty,
    run,
    stability_report,
    time_value,
    whole_number,
)
from eng_toolkit.domains.control._server import server
from eng_toolkit.units import UnitsError

RULES = ("ziegler_nichols", "cohen_coon", "imc", "simc")
CONTROLLERS = ("p", "pi", "pid")
FORMS = ("standard", "parallel")
ALLOWED = {
    "ziegler_nichols": ("p", "pi", "pid"),
    "cohen_coon": ("p", "pi", "pid"),
    "imc": ("pi", "pid"),
    "simc": ("pi", "pid"),
}
RATIO_RANGE = (1e-2, 1e2)

INTENT = {
    "ziegler_nichols": "Ziegler-Nichols open-loop (reaction-curve) rules: aim at about quarter-amplitude decay of a load disturbance on a lag-dominant process (roughly 0.1 < L/T < 1). Aggressive: expect large setpoint overshoot; usually detuned afterwards.",
    "cohen_coon": "Cohen-Coon rules: like Ziegler-Nichols (quarter-amplitude decay) but corrected for larger dead time relative to the lag (L/T from about 0.1 to 1 and above). Aggressive, little robustness margin.",
    "imc": "IMC / lambda tuning: lambda is the wanted closed-loop time constant; larger lambda is slower and more robust. Rule of thumb lambda >= 0.8 L and >= 0.1 T. Smooth setpoint response, any L/T.",
    "simc": "SIMC (Skogestad): IMC-based with a cap on the integral time, tau_c = lambda; good setpoint/load-disturbance compromise for any L/T. A FOPDT model gives no derivative action.",
}


def rule_parameters(rule: str, kind: str, k: float, t: float, l: float, lam: float) -> tuple[float, float | None, float | None, dict[str, str]]:
    """(Kc, Ti, Td, formulas) from the published formula; ``Ti``/``Td`` are ``None`` when the controller has no I/D term."""
    r = l / t
    if rule == "ziegler_nichols":
        if kind == "p":
            return t / (k * l), None, None, {"Kc": "T/(K L)"}
        if kind == "pi":
            return 0.9 * t / (k * l), l / 0.3, None, {"Kc": "0.9 T/(K L)", "Ti": "L/0.3"}
        return 1.2 * t / (k * l), 2.0 * l, 0.5 * l, {"Kc": "1.2 T/(K L)", "Ti": "2 L", "Td": "0.5 L"}
    if rule == "cohen_coon":
        if kind == "p":
            return (1.0 / k) * (t / l) * (1.0 + r / 3.0), None, None, {"Kc": "(1/K)(T/L)(1 + r/3), r = L/T"}
        if kind == "pi":
            return (1.0 / k) * (t / l) * (0.9 + r / 12.0), l * (30.0 + 3.0 * r) / (9.0 + 20.0 * r), None, {
                "Kc": "(1/K)(T/L)(0.9 + r/12)", "Ti": "L (30 + 3 r)/(9 + 20 r)", "r": "L/T"}
        return (1.0 / k) * (t / l) * (4.0 / 3.0 + r / 4.0), l * (32.0 + 6.0 * r) / (13.0 + 8.0 * r), 4.0 * l / (11.0 + 2.0 * r), {
            "Kc": "(1/K)(T/L)(4/3 + r/4)", "Ti": "L (32 + 6 r)/(13 + 8 r)", "Td": "4 L/(11 + 2 r)", "r": "L/T"}
    if rule == "imc":
        if kind == "pi":
            return t / (k * (lam + l)), t, None, {"Kc": "T/(K (lambda + L))", "Ti": "T"}
        return (t + l / 2.0) / (k * (lam + l / 2.0)), t + l / 2.0, t * l / (2.0 * t + l), {
            "Kc": "(T + L/2)/(K (lambda + L/2))", "Ti": "T + L/2", "Td": "T L/(2 T + L)"}
    kc = t / (k * (lam + l))
    ti = min(t, 4.0 * (lam + l))
    return kc, ti, (0.0 if kind == "pid" else None), {"Kc": "T/(K (lambda + L))", "Ti": "min(T, 4 (lambda + L))", **({"Td": "0 (FOPDT: no second lag)"} if kind == "pid" else {})}


def controller_polynomials(kc: float, ti: float | None, td: float | None, n_filter: float) -> tuple[np.ndarray, np.ndarray]:
    """Numerator and denominator of ``Kc (1 + 1/(Ti s) + Td s/(1 + Td s/N))`` (time in whatever unit Ti and Td use)."""
    if ti is None:
        return np.array([kc]), np.array([1.0])
    if not td:
        return np.array([kc * ti, kc]), np.array([ti, 0.0])
    a = td / n_filter
    num = kc * np.array([ti * a + ti * td, ti + a, 1.0])
    den = np.array([ti * a, ti, 0.0])
    return num, den


def _simulate_step(num: np.ndarray, den: np.ndarray, y_ss: float, tol: float = 0.02) -> tuple[dict[str, Any], lti.Simulation, list[str], bool]:
    tf = lti.TF(poly.trim(num), poly.trim(den), None)
    poles = poly.roots(tf.den)
    n_samples, dt, notes = lti.time_grid(poles, discrete=False, step=None, t_final=None, samples=None)
    a, b, c, d = lti.tf2ss(tf)
    sim = lti.simulate(a, b, c, d, kind="step", samples=n_samples, step=dt, discrete=False)
    stab = stability_report(tf.den, None, poles)
    metrics = lti.step_metrics(sim.t, sim.y, y_ss, tol) if stab["stable"] and y_ss != 0 else {}
    return metrics, sim, notes + stab["_warnings"], bool(stab["stable"])


@server.tool(
    name="pid_tune",
    description=(
        "PID tuning from a first-order-plus-dead-time model G = K exp(-L s)/(T s + 1): rule = ziegler_nichols (open-loop reaction curve), "
        "cohen_coon, imc (lambda tuning) or simc; controller = P, PI or PID; form = standard (Kc, Ti, Td) or parallel (Kp, Ki, Kd). "
        "Returns the gains with the formulas named, what the rule is meant for, and a closed-loop simulation (delay as a Pade "
        "approximation of the stated order, derivative filter N): overshoot, rise and settling time, stability by exact pole count, and gain/phase margins "
        "with the true delay. Starting points only."
    ),
)
def pid_tune(
    gain: Annotated[float, Field(description="Process gain K (> 0): change of output per unit change of controller output at steady state")],
    time_constant: Annotated[str, Field(description="Process time constant T with unit, e.g. '10 s'")],
    delay: Annotated[str, Field(description="Dead time L with unit, e.g. '2 s' (must be > 0; L/T between 0.01 and 100)")],
    rule: Annotated[str, Field(description="'ziegler_nichols', 'cohen_coon', 'imc' or 'simc'")] = "imc",
    controller: Annotated[str, Field(description="'P', 'PI' or 'PID' (imc and simc: PI or PID)")] = "PI",
    form: Annotated[str, Field(description="'standard' Kc(1 + 1/(Ti s) + Td s) (default) or 'parallel' Kp + Ki/s + Kd s")] = "standard",
    lambda_time: Annotated[str, Field(description="IMC/SIMC closed-loop time constant with unit, e.g. '3 s'. Empty = max(L, 0.1 T)")] = "",
    pade_order: Annotated[int, Field(description="Order n (1..10) of the (n, n) Pade approximation of the delay in the simulated loop (default 3)")] = 3,
    derivative_filter: Annotated[float, Field(description="Derivative filter ratio N in Td s/(1 + Td s/N) used in the simulation (2..100, default 10)")] = 10.0,
) -> ToolResult:
    inputs = {
        "gain": gain, "time_constant": time_constant, "delay": delay, "rule": rule, "controller": controller, "form": form,
        "lambda_time": lambda_time, "pade_order": pade_order, "derivative_filter": derivative_filter,
    }

    def compute(_: JobHandle) -> Computed:
        k = plain_number(gain, name="gain", lo=-1e300, hi=1e300)
        if k <= 0.0:
            raise UnitsError("input.invalid", f"'gain' must be > 0, got {k:g} (for a reverse-acting process tune with |K| and invert the controller sign)", {"param": "gain"})
        k = plain_number(k, name="gain", lo=1e-6, hi=1e6, range_error=True)
        rule_key = choice(rule, name="rule", allowed=RULES)
        kind = choice(controller, name="controller", allowed=CONTROLLERS)
        form_key = choice(form, name="form", allowed=FORMS)
        if kind not in ALLOWED[rule_key]:
            raise UnitsError("input.invalid", f"the rule '{rule_key}' defines no {kind.upper()} controller; use one of {[c.upper() for c in ALLOWED[rule_key]]}", {"param": "controller", "allowed": [c.upper() for c in ALLOWED[rule_key]]})
        t = time_value(time_constant, name="time_constant", lo=1e-6, hi=1e7)
        l = time_value(delay, name="delay", lo=1e-6, hi=1e7)
        ratio = l / t
        if not RATIO_RANGE[0] <= ratio <= RATIO_RANGE[1]:
            raise UnitsError("input.out_of_range", f"L/T = {ratio:.3g} is outside the supported range {RATIO_RANGE[0]:g} .. {RATIO_RANGE[1]:g} (the FOPDT rules and the Pade check are not meaningful there)", {"param": "delay", "min": RATIO_RANGE[0], "max": RATIO_RANGE[1]})
        order = whole_number(pade_order, name="pade_order", lo=1, hi=10)
        n_filter = plain_number(derivative_filter, name="derivative_filter", lo=2.0, hi=100.0)
        auto_lambda = is_blank(lambda_time)
        lam = max(l, 0.1 * t) if auto_lambda else time_value(lambda_time, name="lambda_time", lo=1e-6, hi=1e7)
        warnings: list[str] = []

        kc, ti, td, formulas = rule_parameters(rule_key, kind, k, t, l, lam)
        if rule_key in ("ziegler_nichols", "cohen_coon"):
            if ratio < 0.1:
                warnings.append(f"L/T = {ratio:.3g} is below 0.1: the delay is small and the {rule_key.replace('_', '-')} gains (proportional to T/(K L)) become very large and aggressive; consider imc or simc")
            if ratio > 1.0 and rule_key == "ziegler_nichols":
                warnings.append(f"L/T = {ratio:.3g} is above 1 (dead-time dominant): Ziegler-Nichols open-loop rules are poor there; prefer cohen_coon, imc or simc")
            if ratio > 1.0 and rule_key == "cohen_coon":
                warnings.append(f"L/T = {ratio:.3g} is above 1 (dead-time dominant): any PID is a poor choice; consider a Smith predictor (not provided)")
        if rule_key in ("imc", "simc"):
            if lam < 0.8 * l:
                warnings.append(f"lambda = {lam:.4g} s is below 0.8 L = {0.8 * l:.4g} s: the loop will have little robustness margin (rule of thumb lambda >= 0.8 L)")
            if lam < 0.1 * t:
                warnings.append(f"lambda = {lam:.4g} s is below 0.1 T = {0.1 * t:.4g} s: very aggressive tuning")
            if rule_key == "simc" and kind == "pid":
                warnings.append("SIMC has no derivative term for a first-order-plus-delay process: Td = 0 and the PID equals the PI (Skogestad's PID rules need a second lag)")
        if kind != "p" and ti is not None and ti <= 0:  # pragma: no cover - defensive
            raise UnitsError("input.invalid", "the rule gave a non-positive integral time", {"param": "rule"})

        # --- closed loop in normalised time (s' = s T): everything of order one
        pn, pd = lti.pade_delay(ratio, order)
        plant_n = k * pn
        plant_d = np.convolve([1.0, 1.0], pd)
        ti_n = None if ti is None else ti / t
        td_n = None if td is None else td / t
        cn, cd = controller_polynomials(kc, ti_n, td_n, n_filter)
        open_n, open_d = np.convolve(cn, plant_n), np.convolve(cd, plant_d)
        char = poly.add(open_d, open_n)
        y_ss = 1.0 if kind != "p" else k * kc / (1.0 + k * kc)
        metrics, sim, notes, stable = _simulate_step(open_n, char, y_ss)
        warnings += notes
        stab = stability_report(char, None, poly.roots(char))
        warnings += stab["_warnings"]
        checks = numerical_checks(poly.trim(open_n), char, poly.roots(open_n), poly.roots(char), None)
        warnings += checks["warnings"]
        if sim.truncated:
            warnings.append("the simulated closed loop diverged: this tuning is unstable for the Pade model")

        # --- load disturbance at the plant input: Gp/(1+L)
        dist_n = np.convolve(plant_n, cd)
        _, dist_sim, _, _ = _simulate_step(dist_n, char, 0.0)
        dist_peak = float(np.max(np.abs(dist_sim.y))) if len(dist_sim.y) else 0.0
        iae = float(np.sum(0.5 * (np.abs(dist_sim.y[1:]) + np.abs(dist_sim.y[:-1])) * np.diff(dist_sim.t)) * t) if stable and len(dist_sim.t) > 1 else None

        # --- margins with the exact delay
        def loop(w: np.ndarray) -> np.ndarray:
            s = 1j * w * t  # normalised frequency
            c_val = np.polyval(cn, s) / np.where(np.abs(np.polyval(cd, s)) < 1e-300, 1e-300, np.polyval(cd, s))
            return c_val * k * np.exp(-ratio * s) / (s + 1.0)

        grid = lti.log_grid(1e-3 / t, 1e3 / t, 6000)
        m = lti.margins(loop, grid)
        gcs, pcs = m["gain_crossovers"], m["phase_crossovers"]
        pm = min(gcs, key=lambda c: abs(c["phase_margin_deg"])) if gcs else None
        gm = min(pcs, key=lambda c: abs(c["gain_margin_db"])) if pcs else None
        delay_margin = (math.radians(pm["phase_margin_deg"]) / pm["frequency"]) if pm and pm["phase_margin_deg"] > 0 else None

        # --- outputs
        values: dict[str, QuantityOut] = {"controller_gain": plain(kc, label="controller_gain")}
        std = {"Kc": kc, "Ti": ti, "Td": td}
        par = {"Kp": kc, "Ki": None if ti is None else kc / ti, "Kd": None if not td else kc * td}
        if form_key == "standard":
            if ti is not None:
                values["integral_time"] = qty(ti, "second", label="integral_time")
            if td:
                values["derivative_time"] = qty(td, "second", label="derivative_time")
        else:
            if par["Ki"] is not None:
                values["integral_gain"] = qty(par["Ki"], "1 / second", label="integral_gain")
            if par["Kd"] is not None:
                values["derivative_gain"] = qty(par["Kd"], "second", label="derivative_gain")
        values["closed_loop_unstable_poles"] = plain(float(stab["_counts"][0]), label="closed_loop_unstable_poles")
        if stable:
            if isinstance(metrics.get("overshoot_percent"), (int, float)):
                values["overshoot"] = qty(metrics["overshoot_percent"] if metrics["overshoot_percent"] is not None else 0.0, "percent", label="overshoot")
            if isinstance(metrics.get("rise_time"), (int, float)):
                values["rise_time"] = qty(metrics["rise_time"] * t, "second", label="rise_time")
            if isinstance(metrics.get("settling_time"), (int, float)):
                values["settling_time"] = qty(metrics["settling_time"] * t, "second", label="settling_time")
            values["load_disturbance_peak"] = plain(dist_peak, label="load_disturbance_peak")
        if gm:
            values["gain_margin"] = plain(gm["gain_margin"], label="gain_margin")
        if pm:
            values["phase_margin"] = qty(pm["phase_margin_deg"], "degree", label="phase_margin")
        if delay_margin is not None:
            values["delay_margin"] = qty(delay_margin, "second", label="delay_margin")

        sim_metrics: dict[str, Any] = {}
        if stable:
            for key in ("overshoot_percent", "rise_time", "settling_time", "peak_value", "peak_time"):
                v = metrics.get(key)
                sim_metrics[key] = None if v is None else (v * t if key.endswith("_time") else v)
            if metrics.get("settling_time") is None:
                sim_metrics["settling_note"] = metrics.get("settling_reason", "not reached in the window")
            sim_metrics["steady_state_value"] = y_ss
        else:
            sim_metrics = {"overshoot_percent": "unbounded", "settling_time": "unbounded", "note": "the closed loop is " + stab["verdict"] + " (exact pole count)"}
        data: dict[str, Any] = {
            "process": {"K": k, "T_s": t, "L_s": l, "L_over_T": ratio, "model": "K exp(-L s)/(T s + 1)"},
            "rule": rule_key,
            "controller": kind.upper(),
            "formulas": formulas,
            "intended_for": INTENT[rule_key],
            "lambda_s": lam if rule_key in ("imc", "simc") else None,
            "lambda_default": bool(auto_lambda and rule_key in ("imc", "simc")),
            "form": form_key,
            "standard": {"Kc": kc, "Ti_s": ti, "Td_s": td, "law": "C(s) = Kc (1 + 1/(Ti s) + Td s)"},
            "parallel": {"Kp": par["Kp"], "Ki_per_s": par["Ki"], "Kd_s": par["Kd"], "law": "C(s) = Kp + Ki/s + Kd s"},
            "simulation": {
                "model": "unity feedback, delay replaced by the (n, n) Pade approximation, derivative filter Td s/(1 + Td s/N)",
                "pade_order": order,
                "derivative_filter_N": n_filter,
                "closed_loop_stability": stab["verdict"],
                "closed_loop_stability_method": stab["method"]["name"],
                "setpoint_step": sim_metrics,
                "setpoint_step_definitions": "rise 10-90 %, settling inside +/- 2 % of the final value; final value 1 with integral action, K Kc/(1 + K Kc) for P",
                "load_disturbance_step_at_plant_input": {"peak_deviation": dist_peak, "iae": iae, "note": "peak of |y| for a unit step disturbance at the plant input; iae = integral of |y| dt (s), only if stable"},
                "margins_with_exact_delay": {
                    "gain_crossovers": capped([{"frequency_rad_s": c["frequency"], "phase_margin_deg": c["phase_margin_deg"]} for c in gcs]),
                    "phase_crossovers": capped([{"frequency_rad_s": c["frequency"], "gain_margin": c["gain_margin"]} for c in pcs]),
                },
            },
            "caveats": [
                "these are textbook starting points from a model; check against the real plant or a detailed simulation",
                "the FOPDT model must have been identified around the operating point; actuator limits, noise and integrator windup are not modelled",
                "the Pade approximation replaces the delay in the time simulation only (margins use the exact delay)",
            ],
            "numerical_checks": checks,
        }
        return Computed(values=values, primary="controller_gain", warnings=warnings, data=clean(data))

    return run("control_pid_tune", inputs, compute)


__all__ = ["controller_polynomials", "pid_tune", "rule_parameters"]
