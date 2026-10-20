"""``control_frequency``: Bode data, margins, bandwidth, resonance and the closed-loop stability of a loop transfer function.

Definitions (L the transfer function given; for a loop it is the OPEN-loop L = C G H, closed by unity negative feedback)
* L(j w) by Horner evaluation of the polynomials at s = j w (continuous) or z = exp(j w T) (discrete, w up to pi/T).
* magnitude dB = 20 log10 |L|, phase = unwrapped angle in degrees (numerator and denominator unwrapped separately, then anchored
  at the low-frequency asymptote: -90 degrees per integrator).
* gain crossover: |L| = 1, phase margin PM = 180 + angle L (wrapped to (-180, 180]); phase crossover: Im L = 0 with Re L < 0, gain
  margin GM = 1 / |L| there (20 log10 in dB, GM < 1 means the loop is stable only because of the gain, see below).  Each crossing is
  refined by bisection on the direct evaluation; the crossing search uses a 6000-point log grid two decades wider than the
  table, so two crossings closer than one grid cell (0.1 % in frequency) can be missed.
* bandwidth: first frequency where |L| falls to |L(0)|/sqrt(2) (needs a finite non-zero DC gain); resonant peak: the maximum of
  |L| above |L(0)| (golden-section refined).
* closed loop with unity negative feedback: characteristic polynomial den + num; its roots are counted EXACTLY (Routh or Jury /
  unit-circle count) and compared with the verdict a margin-reading would give.  Margins are a reliable test only for a loop that
  is open-loop stable with a single gain and a single phase crossover; for a conditionally stable loop, or one with open-loop
  unstable poles, they can say "unstable" for a stable loop or the reverse.  The pole count is the authority.
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
    DomainIn,
    JobHandle,
    NumeratorIn,
    SampleTimeIn,
    build_tf,
    capped,
    clean,
    csv_text,
    numerical_checks,
    plain,
    plain_number,
    qty,
    run,
    stability_report,
    tf_out,
    whole_number,
)
from eng_toolkit.domains.control._server import server
from eng_toolkit.domains.control.analyze import dc_gain
from eng_toolkit.units import UnitsError

SCAN_POINTS = 6000
RAD_S = "radian / second"


def equivalent_s_magnitudes(roots: np.ndarray, dt: float | None) -> np.ndarray:
    """|s| of each non-zero root: the root itself (continuous) or ``|ln(z)/T|`` (discrete, z != 0)."""
    if dt is None:
        mags = np.abs(roots)
    else:
        r = roots[np.abs(roots) > 0]
        mags = np.abs(np.log(r.astype(complex))) / dt
    return mags[mags > 0]


def auto_range(tf: lti.TF, zeros: np.ndarray, poles: np.ndarray) -> tuple[float, float]:
    """Table range: from 1/30 of the smallest to 30 times the largest non-zero root magnitude (0.01..100 rad/s without roots)."""
    mags = np.concatenate([equivalent_s_magnitudes(poles, tf.dt), equivalent_s_magnitudes(zeros, tf.dt)])
    lo, hi = (0.01, 100.0) if mags.size == 0 else (float(mags.min()) / 30.0, float(mags.max()) * 30.0)
    if hi / lo < 100.0:
        lo, hi = lo / 10.0, hi * 10.0
    lo, hi = max(lo, FREQ_RANGE[0]), min(hi, FREQ_RANGE[1])
    if tf.dt is not None:
        nyquist = math.pi / tf.dt
        hi = nyquist
        lo = min(lo, nyquist / 1000.0)
    return lo, hi


def _find_edge_crossing(evaluate, grid: np.ndarray, level: float) -> float | None:
    """First frequency where |evaluate| goes from above to below ``level``, refined by bisection; None if it never does."""
    mags = np.abs(evaluate(grid))
    idx = np.nonzero((mags[:-1] > level) & (mags[1:] <= level))[0]
    if idx.size == 0:
        return None
    i = int(idx[0])
    return lti.bisect_log(lambda x: float(abs(evaluate(np.array([x]))[0])) - level, float(grid[i]), float(grid[i + 1]))


@server.tool(
    name="frequency",
    description=(
        "Frequency response of a SISO transfer function (coefficient lists in descending powers or polynomial text; continuous, or "
        "domain='discrete' + sample_time). Returns Bode data (magnitude dB, unwrapped phase deg) on a log grid as a CSV artifact, "
        "DC gain, gain and phase margins with crossover frequencies, delay margin, -3 dB bandwidth and resonant peak. With "
        "closed_loop_check (default) it treats the input as an OPEN loop L with unity negative feedback and counts the "
        "closed-loop poles exactly; it states when the margins disagree with the pole count (conditionally stable or open-loop "
        "unstable loops), in which case trust the pole count."
    ),
)
def frequency(
    numerator: NumeratorIn,
    denominator: DenominatorIn,
    domain: DomainIn = "continuous",
    sample_time: SampleTimeIn = "",
    frequency_min: Annotated[float, Field(description="Lowest table frequency in rad/s; 0 = automatic from the poles and zeros")] = 0.0,
    frequency_max: Annotated[float, Field(description="Highest table frequency in rad/s; 0 = automatic (discrete: up to the Nyquist frequency pi/T)")] = 0.0,
    points: Annotated[int, Field(description="Number of table points, 10 to 20000 (default 300)")] = 300,
    closed_loop_check: Annotated[bool, Field(description="Treat the input as an open loop L and check unity-negative-feedback stability by counting characteristic-polynomial roots (default true)")] = True,
) -> ToolResult:
    inputs = {
        "numerator": numerator, "denominator": denominator, "domain": domain, "sample_time": sample_time,
        "frequency_min": frequency_min, "frequency_max": frequency_max, "points": points, "closed_loop_check": closed_loop_check,
    }

    def compute(handle: JobHandle) -> Computed:
        tf = build_tf(numerator, denominator, domain=domain, sample_time=sample_time, require_proper=True)
        n_points = whole_number(points, name="points", lo=10, hi=20000)
        f_lo = plain_number(frequency_min, name="frequency_min", lo=0.0, hi=FREQ_RANGE[1], range_error=True)
        f_hi = plain_number(frequency_max, name="frequency_max", lo=0.0, hi=FREQ_RANGE[1], range_error=True)
        for name, v in (("frequency_min", f_lo), ("frequency_max", f_hi)):
            if 0.0 < v < FREQ_RANGE[0]:
                raise UnitsError("input.out_of_range", f"'{name}' = {v:g} rad/s is below the supported {FREQ_RANGE[0]:g} rad/s", {"param": name})
        warnings: list[str] = []
        poles, zeros = poly.roots(tf.den), poly.roots(tf.num)
        a_lo, a_hi = auto_range(tf, zeros, poles)
        lo, hi = (f_lo or a_lo), (f_hi or a_hi)
        if tf.dt is not None:
            nyquist = math.pi / tf.dt
            if hi > nyquist * (1 + 1e-12):
                raise UnitsError("input.out_of_range", f"'frequency_max' = {hi:g} rad/s is above the Nyquist frequency pi/T = {nyquist:g} rad/s of this sample time (the response repeats there)", {"param": "frequency_max", "max": nyquist})
            if f_lo == 0.0:
                lo = min(lo, hi / 1000.0)
        if not lo < hi:
            raise UnitsError("input.invalid", f"frequency_min ({lo:g}) must be below frequency_max ({hi:g})", {"param": "frequency_min"})

        def evaluate(w: np.ndarray) -> np.ndarray:
            return lti.tf_eval(tf, w)

        table_w = lti.log_grid(lo, hi, n_points)
        n_val, d_val = lti.tf_values(tf, table_w)
        mag_db = lti.magnitude_db(n_val, d_val)
        phase = lti.phase_degrees(tf, table_w)
        if float(np.max(np.abs(np.diff(phase)))) > 90.0 if len(phase) > 1 else False:
            warnings.append("the unwrapped phase changes by more than 90 degrees between two table points (a lightly damped resonance or a very coarse grid): add points or narrow the range")
        if float(mag_db.min()) <= -299.0:
            warnings.append("magnitude below -300 dB (an exact zero on the grid) was floored at -300 dB")

        gain0, integrators = dc_gain(tf)
        # crossing search on a wider, denser grid
        s_lo, s_hi = max(lo / 100.0, FREQ_RANGE[0]), (min(hi * 100.0, FREQ_RANGE[1]) if tf.dt is None else hi)
        scan = lti.log_grid(s_lo, s_hi, SCAN_POINTS)
        found = lti.margins(evaluate, scan)
        gcs, pcs = found["gain_crossovers"], found["phase_crossovers"]
        primary_pm = min(gcs, key=lambda c: abs(c["phase_margin_deg"])) if gcs else None
        primary_gm = min(pcs, key=lambda c: abs(c["gain_margin_db"])) if pcs else None
        delay_margin = None
        if tf.dt is None:
            cands = [math.radians(c["phase_margin_deg"]) / c["frequency"] for c in gcs if c["phase_margin_deg"] > 0]
            delay_margin = min(cands) if cands else None

        bandwidth = None
        resonance = None
        if gain0 not in (None, 0.0):
            level = abs(gain0) / math.sqrt(2.0)
            bandwidth = _find_edge_crossing(evaluate, scan, level)
            peak = lti.peak_magnitude(evaluate, scan)
            if not peak["at_edge"] and peak["magnitude"] > abs(gain0) * (1.0 + 1e-9):
                resonance = peak
        peak_db = float(mag_db.max())

        values: dict[str, QuantityOut] = {"peak_magnitude_db": qty(peak_db, "decibel", label="peak_magnitude_db")}
        if gain0 is not None:
            values["dc_gain"] = plain(gain0, label="dc_gain")
            if gain0 != 0.0:
                values["dc_gain_db"] = qty(20.0 * math.log10(abs(gain0)), "decibel", label="dc_gain_db")
        if primary_gm:
            values["gain_margin"] = plain(primary_gm["gain_margin"], label="gain_margin")
            values["gain_margin_db"] = qty(primary_gm["gain_margin_db"], "decibel", label="gain_margin_db")
            values["phase_crossover_frequency"] = qty(primary_gm["frequency"], RAD_S, label="phase_crossover_frequency")
        if primary_pm:
            values["phase_margin"] = qty(primary_pm["phase_margin_deg"], "degree", label="phase_margin")
            values["gain_crossover_frequency"] = qty(primary_pm["frequency"], RAD_S, label="gain_crossover_frequency")
        if delay_margin is not None:
            values["delay_margin"] = qty(delay_margin, "second", label="delay_margin")
        if bandwidth is not None:
            values["bandwidth"] = qty(bandwidth, RAD_S, label="bandwidth")
        if resonance is not None:
            values["resonant_peak_db"] = qty(20.0 * math.log10(resonance["magnitude"] / abs(gain0)), "decibel", label="resonant_peak_db")
            values["resonant_frequency"] = qty(resonance["frequency"], RAD_S, label="resonant_frequency")

        data: dict[str, Any] = {
            "transfer_function": tf_out(tf),
            "table": {"frequencies_rad_s": [float(table_w[0]), float(table_w[-1])], "points": n_points, "columns": ["frequency_rad_s", "magnitude_db", "phase_deg"], "grid": "logarithmic"},
            "dc_gain": gain0 if gain0 is not None else "infinite",
            "integrators": max(integrators, 0),
            "gain_crossovers": capped([{"frequency_rad_s": c["frequency"], "phase_margin_deg": c["phase_margin_deg"]} for c in gcs]),
            "phase_crossovers": capped([{"frequency_rad_s": c["frequency"], "gain_margin": c["gain_margin"], "gain_margin_db": c["gain_margin_db"]} for c in pcs]),
            "margin_selection": "the crossing with the smallest |margin| is the primary value; every crossing is listed",
            "crossover_search": {"range_rad_s": [float(scan[0]), float(scan[-1])], "grid_points": SCAN_POINTS},
            "bandwidth_definition": "first frequency where |L| falls to |L(0)|/sqrt(2) (finite non-zero DC gain required)",
            "bandwidth": bandwidth if bandwidth is not None else None,
            "resonance": None if resonance is None else {"frequency_rad_s": resonance["frequency"], "magnitude": resonance["magnitude"], "relative_to_dc_db": 20.0 * math.log10(resonance["magnitude"] / abs(gain0))},
        }
        if not gcs:
            data["gain_crossover_note"] = "|L| never equals 1 in the search range (phase margin undefined)"
        if not pcs:
            data["phase_crossover_note"] = "the phase never crosses -180 degrees with Re L < 0 in the search range (gain margin infinite)"
        if gain0 in (None, 0.0):
            data["bandwidth_note"] = "bandwidth and resonance are reported only for a finite, non-zero DC gain (integrators or a zero at DC: use the closed loop from control_feedback)"
        if resonance is None and gain0 not in (None, 0.0):
            data["resonance_note"] = "no peak above the DC gain (no resonance)"
        if tf.dt is not None:
            data["nyquist_rad_s"] = math.pi / tf.dt
            data["margin_units"] = "frequencies in rad/s of the sampled model (z = exp(j w T))"

        if closed_loop_check:
            char = poly.add(tf.den, tf.num)
            char_roots = poly.roots(char)
            cl = stability_report(char, tf.dt, char_roots)
            warnings += [f"closed loop: {w}" for w in cl["_warnings"]]
            ol = stability_report(tf.den, tf.dt, poly.roots(tf.den))
            open_unstable = ol["_counts"][0]
            naive_ok = all(c["gain_margin"] > 1.0 for c in pcs) and all(c["phase_margin_deg"] > 0.0 for c in gcs)
            exact_ok = cl["verdict"] == "stable"
            misleading = naive_ok != exact_ok
            reasons = []
            if len(gcs) > 1 or len(pcs) > 1:
                reasons.append(f"{len(gcs)} gain crossover(s) and {len(pcs)} phase crossover(s): with more than one, a stable loop can have a margin below 1 (conditionally stable)")
            if open_unstable:
                reasons.append(f"the open loop has {open_unstable} unstable pole(s): margins alone do not decide stability (Nyquist: encirclements must equal the number of unstable open-loop poles)")
            if not gcs and not pcs:
                reasons.append("no crossover in the search range: margins say nothing")
            cl_data = {
                "characteristic_polynomial": clean({"coefficients_descending": [float(c) for c in char], "text": poly.to_text(char, "s" if tf.dt is None else "z")}),
                "closed_loop_poles": clean([{"real": float(r.real), "imag": float(r.imag)} for r in char_roots]),
                "stability": cl,
                "open_loop_unstable_poles": open_unstable,
                "verdict": cl["verdict"],
                "verdict_by_margins": "stable" if naive_ok else "unstable",
                "margins_agree_with_pole_count": not misleading,
                "authority": "the pole count of the characteristic polynomial (exact)",
                "margin_reading_reliable_only_if": "the open loop is stable and there is one gain crossover and one phase crossover",
                "reasons_to_distrust_margins": reasons,
            }
            data["closed_loop"] = cl_data
            values["closed_loop_unstable_poles"] = plain(float(cl["_counts"][0]), label="closed_loop_unstable_poles")
            if misleading:
                warnings.append(
                    f"the margins say '{'stable' if naive_ok else 'unstable'}' but the closed loop is {cl['verdict']} by an exact count of the characteristic polynomial "
                    f"({cl['_counts'][0]} unstable pole(s)): margins are misleading here (conditionally stable loop or open-loop unstable poles). Trust the pole count."
                )
            elif reasons and exact_ok:
                warnings.append("the margins agree with the pole count here, but " + reasons[0])
        checks = numerical_checks(tf.num, tf.den, zeros, poles, tf.dt)
        warnings += checks["warnings"]
        data["numerical_checks"] = checks

        artifact = handle.write_text("bode.csv", csv_text(["frequency_rad_s", "magnitude_db", "phase_deg"], [table_w, mag_db, phase]), fmt="csv")
        return Computed(values=values, primary="peak_magnitude_db", warnings=warnings, data=clean(data), artifacts=[artifact])

    return run("control_frequency", inputs, compute)


__all__ = ["auto_range", "frequency"]
