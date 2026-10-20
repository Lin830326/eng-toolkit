"""``control_discretize``: continuous transfer function G(s) to a discrete G(z) for a sample time T.

Methods (T the sample time; D, N the denominator and numerator of G)
* zoh   - zero-order hold, exact for a piecewise-constant input:  Gd(z) = (1 - z^-1) Z{ G(s)/s }.  Computed in state space:
          (Ad, Bd) = exp([[A, B], [0, 0]] T), Cd = C, Dd = D, then Gd = Cd (zI - Ad)^-1 Bd + Dd.  Poles z = exp(p T) exactly.
* foh   - first-order (triangle) hold, noncausal FOH as in MATLAB c2d: the input is linearly interpolated between samples;
          Phi = exp(A T), Gamma1 = int_0^T exp(A s) B ds, Gamma2 = (1/T) int_0^T exp(A (T - tau)) B tau dtau (from one block
          exponential of size n + 2), Ad = Phi, Bd = Gamma1 + (Phi - I) Gamma2, Cd = C, Dd = D + C Gamma2.
* tustin - bilinear s = (2/T)(z - 1)/(z + 1); with prewarp frequency w0 (rad/s, below pi/T): s = w0/tan(w0 T/2) (z - 1)/(z + 1),
          which matches the response exactly at w0.
* forward_euler  - s = (z - 1)/T;   can map a stable pole to an unstable one (a real pole -a needs T < 2/a).
* backward_euler - s = (z - 1)/(T z);   maps the left half plane into a disc: stable stays stable, may be over-damped.
* matched - every pole and finite zero p of G maps to exp(p T); the r-1 zeros at infinity (r the relative degree) go to z = -1; the
          gain is set so that Gd(1) = G(0) (finite non-zero DC gain), else |Gd| = |G| at a low reference frequency
          min(smallest root magnitude/10, 0.1 pi/T).  Approximate, not an equivalence.

The result is normalised to a monic denominator.  The substitution methods (tustin, euler) are polynomial algebra
(N(s(z)) (q1 z + q0)^n over D(s(z)) (q1 z + q0)^n); zoh and foh obtain the polynomials from characteristic polynomials of the discrete
state matrices, which cancels digits at high order.
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
    SAMPLE_TIME_RANGE,
    Computed,
    DenominatorIn,
    JobHandle,
    NumeratorIn,
    build_tf,
    choice,
    clean,
    complex_out,
    numerical_checks,
    plain,
    plain_number,
    qty,
    run,
    stability_report,
    time_value,
    tf_out,
)
from eng_toolkit.domains.control._server import server
from eng_toolkit.domains.control.analyze import dc_gain
from eng_toolkit.units import UnitsError

METHODS = ("zoh", "foh", "tustin", "forward_euler", "backward_euler", "matched")
FORMULAS = {
    "zoh": "Gd(z) = (1 - z^-1) Z{G(s)/s}; state space (Ad, Bd) = expm([[A, B], [0, 0]] T), Gd = C (zI - Ad)^-1 Bd + D; poles z = exp(p T)",
    "foh": "triangle hold: Phi = expm(A T), Ad = Phi, Bd = Gamma1 + (Phi - I) Gamma2, Cd = C, Dd = D + C Gamma2 (Gamma1, Gamma2 from the block exponential of [[A T, B T, 0], [0, 0, I], [0, 0, 0]])",
    "tustin": "s = (2/T)(z - 1)/(z + 1)",
    "tustin_prewarp": "s = w0/tan(w0 T/2) (z - 1)/(z + 1), exact match of the frequency response at w0",
    "forward_euler": "s = (z - 1)/T",
    "backward_euler": "s = (z - 1)/(T z)",
    "matched": "each pole/zero p -> exp(p T); zeros at infinity -> z = -1 (relative degree - 1 of them); gain matched at DC (or a low reference frequency)",
}


def _mobius(num_s: np.ndarray, den_s: np.ndarray, q: tuple[float, float], lin: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    """Substitute ``s = (lin[0] z + lin[1])/(q[0] z + q[1])`` and multiply through by ``(q[0] z + q[1])^n`` (n = denominator order)."""
    n = len(den_s) - 1
    top = np.array(lin, dtype=float)
    bot = np.array(q, dtype=float)
    pw_top, pw_bot = [np.array([1.0])], [np.array([1.0])]
    for _ in range(n):
        pw_top.append(np.convolve(pw_top[-1], top))
        pw_bot.append(np.convolve(pw_bot[-1], bot))

    def apply(c: np.ndarray) -> np.ndarray:
        m = len(c) - 1
        total = np.zeros(n + 1)
        for i, coef in enumerate(c):
            k = m - i
            term = np.convolve(pw_top[k], pw_bot[n - k]) * coef
            total[n + 1 - len(term) :] += term
        return total

    return apply(num_s), apply(den_s)


def _state_space_methods(method: str, tf: lti.TF, step: float) -> tuple[np.ndarray, np.ndarray]:
    a, b, c, d = lti.tf2ss(tf)
    n = a.shape[0]
    if n == 0:
        return np.array([d]), np.array([1.0])
    if method == "zoh":
        m = np.zeros((n + 1, n + 1))
        m[:n, :n], m[:n, n:] = a * step, b * step
        e = lti.expm(m)
        ad, bd, dd = e[:n, :n], e[:n, n:], d
    else:
        m = np.zeros((n + 2, n + 2))
        m[:n, :n], m[:n, n : n + 1], m[n, n + 1] = a * step, b * step, 1.0
        e = lti.expm(m)
        phi, g1, g2 = e[:n, :n], e[:n, n : n + 1], e[:n, n + 1 : n + 2]
        ad, bd, dd = phi, g1 + (phi - np.eye(n)) @ g2, d + float((c @ g2)[0, 0])
    return lti.ss_to_tf_polys(ad, bd, c, dd)


def _matched(tf: lti.TF, step: float, notes: list[str]) -> tuple[np.ndarray, np.ndarray]:
    poles_s, zeros_s = poly.roots(tf.den), poly.roots(tf.num)
    poles_z = np.exp(poles_s * step)
    zeros_z = np.exp(zeros_s * step)
    extra = max(tf.relative_degree - 1, 0)
    zeros_z = np.concatenate([zeros_z, -np.ones(extra)])
    den = np.real(np.poly(poles_z)) if poles_z.size else np.array([1.0])
    num0 = np.real(np.poly(zeros_z)) if zeros_z.size else np.array([1.0])
    gain0, _ = dc_gain(tf)
    nyq = math.pi / step
    if gain0 not in (None, 0.0):
        denom = float(np.sum(num0)) / float(np.sum(den))
        if abs(denom) < 1e-300:
            raise UnitsError("input.out_of_range", "the matched discrete model has a zero or pole at z = 1 that cannot be gain-matched", {"param": "method"})
        k = gain0 / denom
        notes.append("matched: the gain is set so that Gd(1) = G(0)")
    else:
        mags = np.abs(np.concatenate([poles_s, zeros_s]))
        mags = mags[mags > 0]
        w_ref = min(float(mags.min()) / 10.0 if mags.size else nyq, 0.1 * nyq)
        z_ref = np.exp(1j * w_ref * step)
        g_ref = lti.tf_eval(tf, np.array([w_ref]))[0]
        d_ref = np.polyval(num0, z_ref) / np.polyval(den, z_ref)
        ratio = g_ref / d_ref
        k = abs(ratio) * (1.0 if ratio.real >= 0 else -1.0)
        notes.append(f"matched: G has a pole or zero at s = 0 so the gain is matched in magnitude at w = {w_ref:.4g} rad/s instead of DC")
    return k * num0, den


def pole_mapping(method: str, step: float, c_tustin: float):
    if method in ("zoh", "foh", "matched"):
        return lambda s: np.exp(s * step)
    if method == "tustin":
        return lambda s: (c_tustin + s) / (c_tustin - s)
    if method == "forward_euler":
        return lambda s: 1.0 + s * step
    return lambda s: 1.0 / (1.0 - s * step)


def _trim_relative(p: np.ndarray, warnings: list[str], label: str) -> np.ndarray:
    scale = float(np.max(np.abs(p))) if p.size else 0.0
    out = p.copy()
    cut = 0
    while len(out) > 1 and abs(out[0]) <= 1e-10 * scale:
        out = out[1:]
        cut += 1
    if cut:
        warnings.append(f"{cut} leading {label} coefficient(s) below 1e-10 of the largest were dropped (they are zero analytically; rounding left residues)")
    return out


@server.tool(
    name="discretize",
    description=(
        "Convert a continuous SISO transfer function G(s) (numerator/denominator coefficient lists in descending powers or text) to discrete G(z) for a "
        "sample_time: method zoh (zero-order hold), foh (first-order hold), tustin (bilinear; optional prewarp_frequency in rad/s), forward_euler, "
        "backward_euler or matched (pole-zero). Returns the z-domain polynomials (monic denominator), the exact formula used, the difference "
        "equation, a pole map (each s-pole to its z-pole with |z| and stable/unstable), an exact Jury stability verdict, DC gain "
        "comparison and the frequency-response error. The transfer function must be proper."
    ),
)
def discretize(
    numerator: NumeratorIn,
    denominator: DenominatorIn,
    sample_time: Annotated[str, Field(description="Sample time with unit, e.g. '0.1 s' (1e-9 s .. 1e9 s)")],
    method: Annotated[str, Field(description="'zoh' (default), 'foh', 'tustin', 'forward_euler', 'backward_euler' or 'matched'")] = "zoh",
    prewarp_frequency: Annotated[float, Field(description="Tustin only: prewarp frequency in rad/s (0 = none); must be below pi/T")] = 0.0,
) -> ToolResult:
    inputs = {"numerator": numerator, "denominator": denominator, "sample_time": sample_time, "method": method, "prewarp_frequency": prewarp_frequency}

    def compute(_: JobHandle) -> Computed:
        key = choice(method, name="method", allowed=METHODS)
        step = time_value(sample_time, name="sample_time", lo=SAMPLE_TIME_RANGE[0], hi=SAMPLE_TIME_RANGE[1])
        w0 = plain_number(prewarp_frequency, name="prewarp_frequency", lo=-1e300, hi=1e300)
        if w0 < 0.0:
            raise UnitsError("input.invalid", f"'prewarp_frequency' must be >= 0 (0 = no prewarping), got {w0:g}", {"param": "prewarp_frequency"})
        w0 = plain_number(w0, name="prewarp_frequency", lo=0.0, hi=1e9, range_error=True)
        tf = build_tf(numerator, denominator, domain="continuous", sample_time="", require_proper=True)
        nyq = math.pi / step
        if w0 > 0:
            if key != "tustin":
                raise UnitsError("input.invalid", "'prewarp_frequency' is only used with method='tustin'", {"param": "prewarp_frequency"})
            if w0 >= nyq:
                raise UnitsError("input.out_of_range", f"'prewarp_frequency' = {w0:g} rad/s must be below the Nyquist frequency pi/T = {nyq:g} rad/s", {"param": "prewarp_frequency", "max": nyq})
        warnings: list[str] = []
        c_t = (w0 / math.tan(w0 * step / 2.0)) if w0 > 0 else 2.0 / step
        if key in ("zoh", "foh"):
            num_z, den_z = _state_space_methods(key, tf, step)
        elif key == "matched":
            num_z, den_z = _matched(tf, step, warnings)
        else:
            if key == "tustin":
                lin, bot = (c_t, -c_t), (1.0, 1.0)
            elif key == "forward_euler":
                lin, bot = (1.0 / step, -1.0 / step), (0.0, 1.0)
            else:
                lin, bot = (1.0 / step, -1.0 / step), (1.0, 0.0)
            num_z, den_z = _mobius(tf.num, tf.den, bot, lin)
        if not (np.all(np.isfinite(num_z)) and np.all(np.isfinite(den_z))):
            raise UnitsError("input.out_of_range", "the discrete polynomial coefficients overflowed: the sample time is far too small or large for this system", {"param": "sample_time"})
        den_z = poly.trim(den_z)
        if abs(den_z[0]) == 0.0:  # pragma: no cover - defensive
            raise UnitsError("input.out_of_range", "the discrete denominator vanished", {"param": "sample_time"})
        num_z = _trim_relative(poly.trim(num_z), warnings, "numerator")
        scale = den_z[0]
        num_z, den_z = num_z / scale, den_z / scale
        if len(num_z) > len(den_z):  # pragma: no cover - cannot happen for a proper G
            warnings.append("the discrete model came out improper")
        td = lti.TF(num_z, den_z, step)
        poles_d, zeros_d = poly.roots(den_z), poly.roots(num_z)
        stab = stability_report(den_z, step, poles_d)
        warnings += stab["_warnings"]
        poles_s = poly.roots(tf.den)
        mapper = pole_mapping(key, step, c_t)
        pole_map = []
        for p in poles_s:
            z = complex(mapper(p))
            pole_map.append(
                {
                    "continuous_pole": complex_out(p), "stable_continuous": bool(p.real < 0),
                    "discrete_pole": complex_out(z), "magnitude": float(abs(z)), "stable_discrete": bool(abs(z) < 1.0),
                    "mapping": "z = exp(s T)" if key in ("zoh", "foh", "matched") else {"tustin": "z = (c + s)/(c - s), c = 2/T or w0/tan(w0 T/2)", "forward_euler": "z = 1 + s T", "backward_euler": "z = 1/(1 - s T)"}[key],
                }
            )
        c_stable = bool(np.all(poles_s.real < 0)) if poles_s.size else True
        d_stable = stab["verdict"] == "stable"
        if c_stable and not d_stable:
            warnings.append(f"the continuous system is stable but the {key.replace('_', ' ')} discretisation with T = {step:g} s is NOT ({stab['verdict']}): the sample time is too long for this method (forward Euler needs T < 2/|Re p| for a real pole)")
        if (not c_stable) and d_stable:
            warnings.append("the continuous system is unstable but its discretisation is stable: the method maps an unstable pole inside the unit circle (backward Euler does this for poles beyond 1/T)")
        fast = float(np.abs(poles_s).max()) if poles_s.size else 0.0
        if fast * step > 1.0:
            warnings.append(f"the fastest pole has |p| T = {fast * step:.3g} > 1: the sample time is long compared with the fastest dynamics; the discrete model is a poor description of the continuous one")

        # DC gain and frequency-response comparison
        g0, _ = dc_gain(tf)
        gd0, _ = dc_gain(td)
        dc_diff = None
        if g0 is not None and gd0 is not None and g0 != 0.0:
            dc_diff = abs(gd0 - g0) / abs(g0)
        freqs = nyq * np.array([0.01, 0.05, 0.1, 0.25, 0.5])
        gc = lti.tf_eval(tf, freqs)
        gz = lti.tf_eval(td, freqs)
        with np.errstate(all="ignore"):
            rel = np.abs(gz / gc - 1.0)
            ph = np.degrees(np.angle(gz / gc))
        rel = np.where(np.isfinite(rel), rel, np.nan)
        comparison = [
            {"frequency_rad_s": float(f), "fraction_of_nyquist": float(f / nyq), "magnitude_ratio": float(abs(zv / cv)) if abs(cv) > 0 else None, "phase_difference_deg": float(p_)}
            for f, zv, cv, p_ in zip(freqs, gz, gc, ph)
            if np.isfinite(p_)
        ]
        if key in ("zoh", "foh"):
            ref_note = "a ZOH (FOH) equivalent contains the hold: expect a phase lag of about w T/2 (FOH: a lead of about w T/2) relative to the bare G(jw); the magnitude ratio is |sinc|-shaped"
        else:
            ref_note = "ratio of Gd(exp(jwT)) to G(jw); a perfect match would be 1 at 0 degrees"
        checks = numerical_checks(num_z, den_z, zeros_d, poles_d, step)
        warnings += checks["warnings"]

        values: dict[str, QuantityOut] = {
            "sample_time": qty(step, "second", label="sample_time"),
            "discrete_unstable_poles": plain(float(stab["_counts"][0]), label="discrete_unstable_poles"),
        }
        if g0 is not None:
            values["dc_gain_continuous"] = plain(g0, label="dc_gain_continuous")
        if gd0 is not None:
            values["dc_gain_discrete"] = plain(gd0, label="dc_gain_discrete")
        if dc_diff is not None:
            values["dc_gain_relative_difference"] = plain(dc_diff, label="dc_gain_relative_difference")
        b = [float(x) for x in np.concatenate([np.zeros(len(den_z) - len(num_z)), num_z])]
        a = [float(x) for x in den_z]
        terms_u = " + ".join(f"{b[j]:.8g} u[k{'-' + str(j) if j else ''}]" for j in range(len(b)) if b[j] != 0.0) or "0"
        terms_y = "".join(f" {'-' if a[j] > 0 else '+'} {abs(a[j]):.8g} y[k-{j}]" for j in range(1, len(a)) if a[j] != 0.0)
        data: dict[str, Any] = {
            "method": key,
            "sample_time_s": step,
            "prewarp_frequency_rad_s": w0 if w0 > 0 else None,
            "formula": FORMULAS["tustin_prewarp"] if (key == "tustin" and w0 > 0) else FORMULAS[key],
            "continuous": tf_out(tf),
            "discrete": tf_out(td),
            "difference_equation": {"b": b, "a": a, "text": f"y[k] = {terms_u}{terms_y}", "note": "a[0] = 1; b is aligned so that b[0] multiplies u[k]"},
            "discrete_poles": [complex_out(r) for r in poles_d],
            "discrete_zeros": [complex_out(r) for r in zeros_d],
            "pole_map": pole_map,
            "stability_map": {
                "continuous_stable": c_stable,
                "discrete_verdict": stab["verdict"],
                "stability_preserved": c_stable == d_stable,
                "exact_test": stab["method"]["name"],
                "stability": stab,
            },
            "dc_gain": {"continuous": g0 if g0 is not None else "infinite", "discrete": gd0 if gd0 is not None else "infinite", "relative_difference": dc_diff},
            "frequency_response_comparison": {"note": ref_note, "points": comparison},
            "nyquist_rad_s": nyq,
            "numerical_checks": checks,
        }
        return Computed(values=values, primary="sample_time", warnings=warnings, data=clean(data))

    return run("control_discretize", inputs, compute)


__all__ = ["discretize", "pole_mapping"]
