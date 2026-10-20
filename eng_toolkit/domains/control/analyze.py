"""``control_analyze``: poles, zeros, gain and stability of one transfer function.

Definitions (continuous G(s) = N(s)/D(s); discrete G(z) with sample time T)
* poles / zeros: roots of D / N (numpy companion-matrix eigenvalues; approximate, residual checked).
* natural frequency wn = |p|, damping ratio zeta = -Re(p)/|p|, time constant tau = -1/Re(p) (Re p < 0).  Discrete: the same through
  the equivalent s = ln(z)/T, tau = -T / ln|z|.
* DC gain G(0) (continuous) or G(1) (discrete) from the lowest-order coefficients, so poles at the origin / at z = 1 give
  'infinite' and are counted as the system type.
* stability: Routh-Hurwitz (continuous) or Jury (discrete) in exact rational arithmetic, cross-checked against the roots.
* minimum phase: no zero in the closed right half plane (outside or on the unit circle), exact count.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.control import _poly as poly
from eng_toolkit.domains.control._common import (
    Computed,
    DenominatorIn,
    DomainIn,
    JobHandle,
    NumeratorIn,
    SampleTimeIn,
    build_tf,
    clean,
    complex_out,
    dominant,
    numerical_checks,
    plain,
    pole_rows,
    qty,
    run,
    stability_report,
    tf_out,
)
from eng_toolkit.domains.control._lti import TF
from eng_toolkit.domains.control._server import server


def dc_gain(tf: TF) -> tuple[float | None, int]:
    """(gain at s = 0 or z = 1, net number of integrators).  ``None`` for an infinite gain; ``0.0`` if a zero sits there."""
    if tf.dt is None:
        kn, cn = poly.lowest_order(tf.num)
        kd, cd = poly.lowest_order(tf.den)
        net = kd - kn
        return (cn / cd if net == 0 else (0.0 if net < 0 else None)), net
    kn, kd = poly.multiplicity_at(tf.num, 1.0), poly.multiplicity_at(tf.den, 1.0)
    num, den = tf.num.astype(float), tf.den.astype(float)

    def deflate(p: np.ndarray, times: int) -> np.ndarray:
        for _ in range(times):
            out = [p[0]]
            for c in p[1:-1]:
                out.append(out[-1] + c)
            p = np.array(out)
        return p

    cn, cd = float(np.sum(deflate(num, kn))), float(np.sum(deflate(den, kd)))
    net = kd - kn
    return (cn / cd if net == 0 else (0.0 if net < 0 else None)), net


@server.tool(
    name="analyze",
    description=(
        "Analyse one SISO transfer function G = numerator/denominator (coefficient lists in descending powers, e.g. '[1, 3, 2]', "
        "or text 's^2 + 3 s + 2'). Continuous by default; domain='discrete' + sample_time for G(z). Returns order, poles, zeros, "
        "DC gain, system type, relative degree, per pole natural frequency / damping ratio / time constant, dominant pole, "
        "minimum-phase check, and stability from an EXACT Routh-Hurwitz array (continuous) or Jury table (discrete) cross-checked "
        "against the numerical roots. Warns on near pole-zero cancellation, clustered roots, huge coefficient range."
    ),
)
def analyze(
    numerator: NumeratorIn,
    denominator: DenominatorIn,
    domain: DomainIn = "continuous",
    sample_time: SampleTimeIn = "",
) -> ToolResult:
    inputs = {"numerator": numerator, "denominator": denominator, "domain": domain, "sample_time": sample_time}

    def compute(_: JobHandle) -> Computed:
        tf = build_tf(numerator, denominator, domain=domain, sample_time=sample_time)
        poles, zeros = poly.roots(tf.den), poly.roots(tf.num)
        stab = stability_report(tf.den, tf.dt, poles)
        checks = numerical_checks(tf.num, tf.den, zeros, poles, tf.dt)
        warnings = list(stab["_warnings"]) + checks["warnings"]
        gain, net = dc_gain(tf)
        zero_counts = poly.half_plane_counts(tf.num) if tf.dt is None else poly.unit_circle_counts(tf.num)
        z_out = zero_counts["rhp"] if tf.dt is None else zero_counts["outside"]
        z_on = zero_counts["imag"] if tf.dt is None else zero_counts["on_circle"]
        minimum_phase = z_out == 0 and z_on == 0
        if not tf.proper:
            warnings.append(f"improper transfer function (relative degree {tf.relative_degree}): the gain grows with frequency and it has no state-space realisation; other control_* tools will refuse it")
        rows = pole_rows(poles, tf.dt)
        lead = dominant(poles, tf.dt)
        dom_rows = pole_rows(lead, tf.dt) if lead is not None else []

        values: dict[str, QuantityOut] = {
            "order": plain(float(tf.order), label="order"),
            "relative_degree": plain(float(tf.relative_degree), label="relative_degree"),
            "unstable_poles": plain(float(stab["_counts"][0]), label="unstable_poles"),
        }
        if gain is not None:
            values["dc_gain"] = plain(gain, label="dc_gain")
        if dom_rows:
            d0 = dom_rows[0]
            if d0["natural_frequency"] is not None:
                values["dominant_natural_frequency"] = qty(d0["natural_frequency"], "radian / second", label="dominant_natural_frequency")
            if d0["damping_ratio"] is not None:
                values["dominant_damping_ratio"] = plain(d0["damping_ratio"], label="dominant_damping_ratio")
            if d0["time_constant"] is not None:
                values["dominant_time_constant"] = qty(d0["time_constant"], "second", label="dominant_time_constant")
        data: dict[str, Any] = {
            "transfer_function": tf_out(tf),
            "poles": rows,
            "zeros": [complex_out(z) for z in zeros],
            "dc_gain": gain if gain is not None else "infinite",
            "system_type": net if net > 0 else 0,
            "system_type_note": (
                "number of poles at s = 0 (z = 1) minus zeros there" if net >= 0 else "a zero at s = 0 (z = 1) outweighs the poles there: the DC gain is 0 (differentiating behaviour)"
            ),
            "relative_degree": tf.relative_degree,
            "dominant_pole": {"definition": "largest real part (continuous) or largest magnitude (discrete)", "roots": dom_rows},
            "stability": stab,
            "minimum_phase": {
                "minimum_phase": bool(minimum_phase),
                "zeros_outside_stable_region": z_out,
                "zeros_on_boundary": z_on,
                "definition": "no zero in the closed right half plane (continuous) / on or outside the unit circle (discrete); exact count",
            },
            "numerical_checks": checks,
            "method": {
                "roots": "numpy.roots (companion-matrix eigenvalues), approximate",
                "stability": stab["method"]["name"],
                "damping": "wn = |p|, zeta = -Re(p)/|p|, tau = -1/Re(p); discrete through s = ln(z)/T",
            },
        }
        return Computed(values=values, primary="order", warnings=warnings, data=clean(data))

    return run("control_analyze", inputs, compute)


__all__ = ["analyze", "dc_gain"]
