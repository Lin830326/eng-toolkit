"""``dsp_filter_design``: IIR (Butterworth, Chebyshev I and II) and window-method FIR design with the checks that go with it.

Definitions
* IIR: analogue prototype -> frequency transformation -> bilinear transform ``z = (1 + s)/(1 - s)`` with the analogue frequency
  ``tan(pi f/fs)`` (prewarping, so every edge frequency maps exactly).  Butterworth ``cutoff`` is the -3.0103 dB point, Chebyshev I
  ``cutoff`` the passband edge where the gain is -Rp dB, Chebyshev II ``cutoff`` the stopband edge where the gain is -Rs dB.
* Minimum order from the specification (passband edge, ripple Rp, stopband edge, attenuation Rs): Butterworth
  ``n = ceil(ln((10^(Rs/10)-1)/(10^(Rp/10)-1)) / (2 ln r))``, Chebyshev ``n = ceil(acosh(sqrt((10^(Rs/10)-1)/(10^(Rp/10)-1))) / acosh(r))`` with
  ``r`` the stop edge over the pass edge of the low-pass equivalent of the prewarped edges.  The passband edge is met exactly (loss Rp) for all three families, the stopband with margin; for a band stop the centre of the
  transformation is optimised for the lowest order.
* FIR: windowed sinc with the -6 dB points at ``cutoff``; Kaiser beta and order from the attenuation and transition width.
"""

from __future__ import annotations

import math
from typing import Annotated, Any

import numpy as np
from pydantic import Field

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.dsp import _fir as fir
from eng_toolkit.domains.dsp import _iir as iir
from eng_toolkit.domains.dsp import _windows as win
from eng_toolkit.domains.dsp._common import (
    MAX_IIR_ORDER,
    MAX_TABLE_ROWS,
    MAX_TAPS,
    Computed,
    JobHandle,
    QuantityOut,
    choice,
    clean,
    csv_text,
    db,
    frequency_list,
    is_blank,
    plain,
    plain_number,
    qty,
    run,
    sample_rate_hz,
    whole_number,
)
from eng_toolkit.domains.dsp._server import server
from eng_toolkit.units import UnitsError

FAMILIES = ("butterworth", "chebyshev1", "chebyshev2", "fir_rectangular", "fir_hann", "fir_hamming", "fir_blackman", "fir_kaiser")
HZ = "hertz"


def _count(response: str) -> int:
    return 1 if response in ("lowpass", "highpass") else 2


def _complex_list(r: np.ndarray) -> list[complex]:
    return [complex(v) for v in sorted(r, key=lambda v: (round(v.real, 12), v.imag))]


def _near_ends_warnings(freqs: list[float], fs: float, label: str) -> list[str]:
    out = []
    for f in freqs:
        if f / fs < 1e-3:
            out.append(f"{label} {f:g} Hz is below 0.1 % of the sample rate: the poles crowd z = 1 and the design is ill-conditioned (the coefficients of the direct form lose precision; use the second-order sections, or lower the sample rate)")
        elif f / fs > 0.5 - 1e-3:
            out.append(f"{label} {f:g} Hz is within 0.1 % of the sample rate below the Nyquist frequency: the prewarped analogue frequency is huge and the design is ill-conditioned")
    return out


def _iir_checks(d: iir.IIRDesign, spec_pass: list[float], spec_stop: list[float], rp_db: float, rs_db: float, fs: float, cut_hz: list[float], tag: str) -> list[dict[str, Any]]:
    checks = []

    def gain_at(f: float) -> float:
        return db(complex(iir.sos_response(d.sos, np.array([2.0 * math.pi * f / fs]))[0]))

    for f in cut_hz:
        target = {"butterworth": 3.0103, "chebyshev1": rp_db, "chebyshev2": rs_db}[d.family]
        checks.append({"role": f"{tag}_edge", "frequency_hz": f, "gain_db": gain_at(f), "design_target_gain_db": -target})
    for f in spec_pass:
        g = gain_at(f)
        checks.append({"role": "passband_edge", "frequency_hz": f, "gain_db": g, "requirement": f"gain >= {-rp_db:g} dB", "met": bool(rp_db > 0 and g >= -rp_db - 1e-9)})
    for f in spec_stop:
        g = gain_at(f)
        checks.append({"role": "stopband_edge", "frequency_hz": f, "gain_db": g, "requirement": f"gain <= {-rs_db:g} dB", "met": bool(rs_db > 0 and g <= -rs_db + 1e-9)})
    return checks


def _bands(response: str, p: list[float], s: list[float], nyq: float) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    if response == "lowpass":
        pb, sb = [(0.0, p[0])], [(s[0], nyq)]
    elif response == "highpass":
        pb, sb = [(p[0], nyq)], [(0.0, s[0])]
    elif response == "bandpass":
        pb, sb = [(p[0], p[1])], [(0.0, s[0]), (s[1], nyq)]
    else:
        pb, sb = [(0.0, p[0]), (p[1], nyq)], [(s[0], s[1])]
    return [b for b in pb if b[1] > b[0]], [b for b in sb if b[1] > b[0]]


@server.tool(
    name="filter_design",
    description=(
        "Design a digital filter. IIR families butterworth, chebyshev1, chebyshev2 (analogue prototype + bilinear transform with "
        "prewarping) or FIR by the window method (fir_rectangular, fir_hann, fir_hamming, fir_blackman, fir_kaiser), response lowpass, "
        "highpass, bandpass or bandstop. Give either order+cutoff, or a specification (passband_edge, stopband_edge, "
        "passband_ripple_db, stopband_attenuation_db) and the minimum order is computed from the closed-form order equation. "
        "Frequencies carry units ('2 kHz', 'low, high' for band responses); sample_rate too. Returns second-order sections, (b, a), "
        "poles and zeros, a stability check, gain at the edges, group delay, and a magnitude/phase table (CSV). FIR returns taps, "
        "measured ripple and attenuation and the linear-phase check. order = prototype order (IIR, at most 40) or number of taps (FIR, at most 4095)."
    ),
)
def filter_design(
    family: Annotated[str, Field(description="butterworth, chebyshev1, chebyshev2, fir_rectangular, fir_hann, fir_hamming, fir_blackman or fir_kaiser")],
    response: Annotated[str, Field(description="lowpass, highpass, bandpass or bandstop")],
    sample_rate: Annotated[str, Field(description="Sample rate with unit, e.g. '48 kHz' or '8000 Hz'")],
    order: Annotated[int, Field(description="IIR: order of the analogue prototype, 1..40 (a band filter has twice as many poles). FIR: number of taps, 3..4095 (odd for highpass/bandstop). 0 = compute from the specification")] = 0,
    cutoff: Annotated[str, Field(description="With order: butterworth = -3 dB frequency, chebyshev1 = passband edge, chebyshev2 = stopband edge, FIR = -6 dB frequency. One value, or 'low, high' for band responses. Leave empty with a specification")] = "",
    passband_edge: Annotated[str, Field(description="Specification: passband edge(s) with unit, e.g. '1 kHz' or '900 Hz, 1.7 kHz'")] = "",
    stopband_edge: Annotated[str, Field(description="Specification: stopband edge(s) with unit; lowpass above the passband, highpass below, bandpass outside, bandstop inside")] = "",
    passband_ripple_db: Annotated[float, Field(description="Passband ripple Rp in dB (chebyshev1 needs it; 0.0001..20; also the specification's allowed passband loss). 0 = not given")] = 0.0,
    stopband_attenuation_db: Annotated[float, Field(description="Stopband attenuation Rs in dB (chebyshev2 and the specification need it; 1..200; fir_kaiser uses it for beta and order). 0 = not given")] = 0.0,
    kaiser_beta: Annotated[float, Field(description="fir_kaiser only: beta (0..50); 0 = derive from stopband_attenuation_db")] = 0.0,
    points: Annotated[int, Field(description="Rows of the magnitude/phase table, 16..20000 (default 512), linear from 0 to the Nyquist frequency")] = 512,
) -> ToolResult:
    inputs = {
        "family": family, "response": response, "sample_rate": sample_rate, "order": order, "cutoff": cutoff, "passband_edge": passband_edge,
        "stopband_edge": stopband_edge, "passband_ripple_db": passband_ripple_db, "stopband_attenuation_db": stopband_attenuation_db,
        "kaiser_beta": kaiser_beta, "points": points,
    }

    def compute(handle: JobHandle) -> Computed:
        fam = choice(family, name="family", allowed=FAMILIES)
        resp = choice(response, name="response", allowed=iir.RESPONSES)
        fs = sample_rate_hz(sample_rate)
        nyq = fs / 2.0
        rp = plain_number(passband_ripple_db, name="passband_ripple_db", lo=0.0, hi=20.0)
        rs = plain_number(stopband_attenuation_db, name="stopband_attenuation_db", lo=0.0, hi=200.0)
        beta_in = plain_number(kaiser_beta, name="kaiser_beta", lo=0.0, hi=50.0)
        n_rows = whole_number(points, name="points", lo=16, hi=MAX_TABLE_ROWS)
        n_order = whole_number(order, name="order", lo=0, hi=MAX_TAPS if fam.startswith("fir_") else MAX_IIR_ORDER)
        if rp != 0.0 and not 1e-4 <= rp:
            raise UnitsError("input.invalid", "'passband_ripple_db' must be at least 0.0001 dB (or 0 for not given)", {"param": "passband_ripple_db"})
        if rs != 0.0 and not 1.0 <= rs:
            raise UnitsError("input.invalid", "'stopband_attenuation_db' must be at least 1 dB (or 0 for not given)", {"param": "stopband_attenuation_db"})
        count = _count(resp)
        spec_mode = n_order == 0
        has_cut, has_pass, has_stop = (not is_blank(cutoff)), (not is_blank(passband_edge)), (not is_blank(stopband_edge))
        if spec_mode:
            if has_cut:
                raise UnitsError("input.invalid", "'cutoff' goes with 'order'; for a specification leave 'cutoff' empty and give 'passband_edge' and 'stopband_edge'", {"param": "cutoff"})
            if not (has_pass and has_stop):
                raise UnitsError("input.missing", "give 'order' with 'cutoff', or a specification: 'passband_edge' and 'stopband_edge' (and the ripple/attenuation in dB)", {"param": "passband_edge" if not has_pass else "stopband_edge"})
        elif not has_cut:
            raise UnitsError("input.missing", "'cutoff' is required with 'order'", {"param": "cutoff"})
        cut = frequency_list(cutoff, name="cutoff", count=count, fs=fs) if has_cut else []
        pass_hz = frequency_list(passband_edge, name="passband_edge", count=count, fs=fs) if has_pass else []
        stop_hz = frequency_list(stopband_edge, name="stopband_edge", count=count, fs=fs) if has_stop else []
        if has_pass and has_stop and any(a == b for a, b in zip(pass_hz, stop_hz)):
            raise UnitsError("input.invalid", "a passband edge and a stopband edge coincide: there is no transition band", {"param": "stopband_edge"})
        warnings: list[str] = []
        warnings += _near_ends_warnings(cut + pass_hz + stop_hz, fs, "an edge at")
        if count == 2:
            for lo, hi in ((cut or [None, None]), (pass_hz or [None, None])):
                if lo is not None and (hi - lo) / fs < 0.01:
                    warnings.append(f"the band from {lo:g} to {hi:g} Hz is narrower than 1 % of the sample rate: poles crowd together, the design is ill-conditioned (use the second-order sections, or lower the sample rate)")
        w_grid = np.linspace(0.0, math.pi, n_rows)
        f_grid = w_grid * fs / (2.0 * math.pi)
        data: dict[str, Any] = {
            "family": fam, "response": resp, "sample_rate_hz": fs, "nyquist_hz": nyq, "mode": "specification" if spec_mode else "order and cutoff",
        }
        values: dict[str, QuantityOut] = {}

        if not fam.startswith("fir_"):
            if fam == "chebyshev1" and rp == 0.0:
                raise UnitsError("input.missing", "chebyshev1 needs 'passband_ripple_db' (the ripple Rp in dB)", {"param": "passband_ripple_db"})
            if fam == "chebyshev2" and rs == 0.0:
                raise UnitsError("input.missing", "chebyshev2 needs 'stopband_attenuation_db' (the stopband attenuation Rs in dB)", {"param": "stopband_attenuation_db"})
            if spec_mode:
                if rp == 0.0 or rs == 0.0:
                    raise UnitsError("input.missing", "a specification needs both 'passband_ripple_db' and 'stopband_attenuation_db'", {"param": "passband_ripple_db" if rp == 0.0 else "stopband_attenuation_db"})
                if rs <= rp:
                    raise UnitsError("input.invalid", "the stopband attenuation must exceed the passband ripple", {"param": "stopband_attenuation_db"})
                ratio, pass_edges, problem = iir.lowpass_equivalent(resp, pass_hz, stop_hz, fs)
                if problem:
                    raise UnitsError("input.invalid", problem, {"param": "stopband_edge"})
                exact, n = iir.order_equation(fam, ratio, rp, rs)
                if n > MAX_IIR_ORDER:
                    raise UnitsError("input.out_of_range", f"the specification needs order {n} (exactly {exact:.2f}), above the limit {MAX_IIR_ORDER}: widen the transition band or relax the attenuation", {"param": "stopband_edge", "needed_order": n, "limit": MAX_IIR_ORDER})
                scale = iir.unit_point_scale(fam, ratio, n, rp, rs)
                edges = iir.scaled_edges(pass_edges, scale)
                data["order_equation"] = {
                    "A": 10.0 ** (rs / 10.0) - 1.0, "E": 10.0 ** (rp / 10.0) - 1.0, "stop_over_pass_ratio": ratio, "exact_order": exact, "order": n,
                    "formula": "n = ceil(ln(A/E) / (2 ln r))" if fam == "butterworth" else "n = ceil(acosh(sqrt(A/E)) / acosh(r))",
                    "ratio_definition": "stop edge / pass edge of the low-pass equivalent of the prewarped (tan(pi f/fs)) edges",
                }
                data["met_exactly"] = "passband"
            else:
                n = n_order
                if fam == "butterworth" and (rp or rs):
                    warnings.append("ripple and attenuation are ignored by a Butterworth design given an order and a cutoff")
                edges = iir.edges_from_hz(resp, cut, fs)
            d = iir.build(fam, resp, n, edges, rp if fam == "chebyshev1" else 0.0, rs if fam == "chebyshev2" else 0.0)
            edge_hz = iir.edges_to_hz(d.edges, fs)
            radii = np.abs(d.poles)
            max_r = float(radii.max())
            roots_ba = np.roots(d.a) if len(d.a) > 1 else np.zeros(0)
            max_r_ba = float(np.abs(roots_ba).max()) if roots_ba.size else 0.0
            h_sos, h_ba = iir.sos_response(d.sos, w_grid[1:-1]), iir.ba_response(d.b, d.a, w_grid[1:-1])
            ba_err = float(np.max(np.abs(h_sos - h_ba)))
            if d.digital_order > 8:
                warnings.append(f"direct-form (b, a) of order {d.digital_order}: coefficient rounding is amplified (the response from (b, a) differs from the second-order sections by {ba_err:.1e}); implement the filter with the sections")
            if ba_err > 1e-6 or max_r_ba >= 1.0 > max_r:
                warnings.append(f"the direct-form polynomials do not reproduce the design (response error {ba_err:.1e}, pole radius {max_r_ba:.6f} against {max_r:.6f}): use the second-order sections only")
            if max_r > 1.0 - 1e-6:
                warnings.append(f"a pole lies at radius {max_r:.9f}, very close to the unit circle: the filter is stable but its response is extremely sensitive to coefficient rounding")
            tag = {"butterworth": "minus_3db", "chebyshev1": "design_passband", "chebyshev2": "design_stopband"}[fam]
            checks = _iir_checks(d, pass_hz if spec_mode or has_pass else [], stop_hz if spec_mode or has_stop else [], rp, rs, fs, edge_hz, tag)
            gd_freqs = sorted({round(f, 9) for f in edge_hz + pass_hz + stop_hz + [nyq / 8.0, nyq / 4.0, nyq / 2.0]})[:12]
            gd_rows = []
            for f in gd_freqs:
                tau, nudged = iir.group_delay_sos(d.sos, 2.0 * math.pi * f / fs)
                gd_rows.append({"frequency_hz": f, "group_delay_samples": tau, "group_delay_s": tau / fs, **({"note": "on a zero of the unit circle: taken 1e-7 rad inside"} if nudged else {})})
            h_tab = iir.sos_response(d.sos, w_grid)
            mag_db = 20.0 * np.log10(np.maximum(np.abs(h_tab), 1e-15))
            phase_deg = np.degrees(iir.unwrapped_phase(h_tab))
            data.update(
                {
                    "order": n, "digital_order": d.digital_order, "edges_hz": edge_hz,
                    "edges_meaning": {"butterworth": "the -3.0103 dB frequencies", "chebyshev1": "the passband edges (gain -Rp dB)", "chebyshev2": "the stopband edges (gain -Rs dB)"}[fam],
                    "ripple_db": rp if fam == "chebyshev1" else None, "stopband_attenuation_db": rs if fam == "chebyshev2" else None,
                    "sos": d.sos, "sos_layout": "each row [b0, b1, b2, a0=1, a1, a2]; overall gain is in the first section; run the rows in order (direct form II transposed)",
                    "b": d.b, "a": d.a,
                    "transfer_function": "H(z) = (b0 + b1 z^-1 + ...) / (1 + a1 z^-1 + ...), the sections multiply to the same H(z)",
                    "poles": _complex_list(d.poles), "zeros": _complex_list(d.zeros),
                    "analog_poles_before_bilinear": _complex_list(d.analog_poles),
                    "analog_poles_note": "left half plane; for butterworth lowpass they lie on a circle of radius wc = tan(pi fc/fs)",
                    "stability": {
                        "stable": bool(max_r < 1.0), "max_pole_radius": max_r, "margin_to_unit_circle": 1.0 - max_r,
                        "method": "poles computed in closed form, so the check does not depend on a root finder",
                        "max_pole_radius_of_direct_form_polynomial": max_r_ba, "ba_vs_sos_max_response_error": ba_err,
                    },
                    "spec_checks": checks, "group_delay": gd_rows,
                    "dc_gain_db": db(complex(iir.sos_response(d.sos, np.array([1e-9]))[0])),
                }
            )
            values["order"] = plain(float(n), label="order")
            values["digital_order"] = plain(float(d.digital_order), label="digital_order")
            values["max_pole_radius"] = plain(max_r, label="max_pole_radius")
            if spec_mode:
                pass_g = [c["gain_db"] for c in checks if c["role"] == "passband_edge"]
                stop_g = [c["gain_db"] for c in checks if c["role"] == "stopband_edge"]
                values["worst_passband_edge_gain_db"] = qty(min(pass_g), "decibel", label="worst_passband_edge_gain_db")
                values["worst_stopband_edge_gain_db"] = qty(max(stop_g), "decibel", label="worst_stopband_edge_gain_db")
            else:
                values["gain_at_cutoff_db"] = qty(checks[0]["gain_db"], "decibel", label="gain_at_cutoff_db")
            primary = "order"
        else:
            window = fam[4:]
            if window == "kaiser" and beta_in == 0.0 and rs == 0.0:
                raise UnitsError("input.missing", "fir_kaiser needs 'kaiser_beta' or 'stopband_attenuation_db' (beta follows from the attenuation)", {"param": "kaiser_beta"})
            if spec_mode:
                try:
                    plan = fir.plan_from_spec(window, resp, pass_hz, stop_hz, fs, rs, beta_in)
                except ValueError as exc:
                    raise UnitsError("input.invalid", str(exc), {"param": "stopband_edge"}) from None
                if plan.taps > MAX_TAPS:
                    raise UnitsError("input.out_of_range", f"the specification needs {plan.taps} taps, above the limit {MAX_TAPS}: widen the transition band or relax the attenuation", {"param": "stopband_edge", "needed_taps": plan.taps, "limit": MAX_TAPS})
                taps, beta, cutoffs = plan.taps, plan.beta, plan.cutoffs_hz
                warnings += plan.notes
                trans = plan.transition_hz
                data["order_formula"] = (
                    "Kaiser: beta = 0.1102 (A - 8.7) for A > 50, 0.5842 (A - 21)^0.4 + 0.07886 (A - 21) for 21 <= A <= 50, 0 below; "
                    "M = ceil((A - 7.95)/(2.285 d_omega)), taps = M + 1" if window == "kaiser" else
                    f"transition width d_omega = {fir.WINDOW_TABLE[window][0]} pi/M (Oppenheim and Schafer, Table 7.1); taps = M + 1"
                )
                data["transition_width_hz"] = trans
                p_edges, s_edges = pass_hz, stop_hz
            else:
                taps = n_order
                if taps < 3:
                    raise UnitsError("input.out_of_range", "a FIR filter needs at least 3 taps", {"param": "order"})
                if resp in ("highpass", "bandstop") and taps % 2 == 0:
                    raise UnitsError("input.invalid", f"a {resp} windowed FIR needs an odd number of taps (an even-length symmetric filter has a zero at the Nyquist frequency); got {taps}", {"param": "order"})
                beta = (beta_in if beta_in > 0.0 else win.kaiser_beta(rs)) if window == "kaiser" else 0.0
                cutoffs = cut
                trans = fir.estimated_transition_hz(window, taps, beta, fs)
                h2 = trans / 2.0
                if has_pass and has_stop:
                    p_edges, s_edges = pass_hz, stop_hz
                else:
                    sign = {"lowpass": ([-1], [1]), "highpass": ([1], [-1]), "bandpass": ([1, -1], [-1, 1]), "bandstop": ([-1, 1], [1, -1])}[resp]
                    p_edges = [c + s * h2 for c, s in zip(cutoffs, sign[0])]
                    s_edges = [c + s * h2 for c, s in zip(cutoffs, sign[1])]
                    data["measurement_bands_note"] = (
                        f"no passband/stopband edges given: the measurement bands are the cut-off +- half the typical transition width of this window and length ({trans:g} Hz)"
                    )
            for c in cutoffs:
                if not 0.0 < c < nyq:
                    raise UnitsError("input.invalid", f"the -6 dB frequency {c:g} Hz computed for the design is outside (0, Nyquist)", {"param": "cutoff"})
            h = fir.design(resp, [2.0 * math.pi * c / fs for c in cutoffs], taps, window, beta)
            pb, sb = _bands(resp, [min(max(e, 0.0), nyq) for e in p_edges], [min(max(e, 0.0), nyq) for e in s_edges], nyq)
            meas = fir.measure(h, resp, fs, pb, sb)
            lin = fir.linear_phase(h)
            full = np.polyval(h[::-1], np.exp(-1j * w_grid))
            mag_db = 20.0 * np.log10(np.maximum(np.abs(full), 1e-15))
            phase_deg = np.degrees(np.unwrap(np.angle(full)))
            if np.abs(full[0]) < 1e-9:
                phase_deg[0] = phase_deg[1]
            if np.abs(full[-1]) < 1e-9:
                phase_deg[-1] = phase_deg[-2]
            f_fine = np.array([e for e in set(p_edges + s_edges + cutoffs)])
            edge_gain = [{"frequency_hz": float(f), "gain_db": db(complex(np.polyval(h[::-1], np.exp(-1j * 2.0 * math.pi * f / fs)))) } for f in sorted(f_fine)]
            req_atten = rs if rs > 0.0 else None
            if req_atten is not None and "stopband_attenuation_db" in meas and meas["stopband_attenuation_db"] < req_atten - 1e-9:
                warnings.append(f"the measured stopband attenuation {meas['stopband_attenuation_db']:.1f} dB is below the {req_atten:g} dB asked for")
            if window != "kaiser" and "stopband_attenuation_db" in meas and meas["stopband_attenuation_db"] < fir.WINDOW_TABLE[window][1] - 12.0:
                warnings.append("the measured stopband attenuation is far below what this window reaches: the cut-off or the number of taps leaves the transition band too wide for the measurement bands")
            if taps < 5 or (taps < 15 and window != "rectangular"):
                warnings.append("very few taps: the windowed-sinc approximation is crude (the table formulas assume dozens of taps)")
            data.update(
                {
                    "window": window, "num_taps": taps, "filter_order": taps - 1, "beta": beta if window == "kaiser" else None,
                    "cutoff_minus_6db_hz": cutoffs, "coefficients": h, "dc_gain": float(np.sum(h)), "nyquist_gain": float(abs(np.sum(h * (-1.0) ** np.arange(taps)))),
                    "scaling": {"lowpass": "gain 1 at DC", "bandstop": "gain 1 at DC", "highpass": "gain 1 at the Nyquist frequency", "bandpass": "gain 1 at the band centre"}[resp],
                    "measurement": {**meas, "passband_bands_hz": pb, "stopband_bands_hz": sb, "method": f"zero-padded FFT of {max(32768, 32 * taps)} points"},
                    "linear_phase": lin, "edge_gains": edge_gain,
                    "group_delay": {"samples": (taps - 1) / 2.0, "seconds": (taps - 1) / 2.0 / fs, "constant": True},
                    "stable": True, "stability_note": "an FIR filter has no poles other than z = 0",
                }
            )
            values["num_taps"] = plain(float(taps), label="num_taps")
            values["group_delay_samples"] = qty((taps - 1) / 2.0, "dimensionless", label="group_delay_samples")
            if "passband_ripple_peak_to_peak_db" in meas:
                values["passband_ripple_db"] = qty(meas["passband_ripple_peak_to_peak_db"], "decibel", label="passband_ripple_db")
            if "stopband_attenuation_db" in meas:
                values["stopband_attenuation_db"] = qty(meas["stopband_attenuation_db"], "decibel", label="stopband_attenuation_db")
            primary = "num_taps"
        table = handle.write_text("filter_response.csv", csv_text(["frequency_hz", "magnitude_db", "phase_deg"], [f_grid, mag_db, phase_deg]), fmt="csv")
        data["table"] = {"file": "filter_response.csv", "rows": n_rows, "columns": ["frequency_hz", "magnitude_db", "phase_deg"], "grid": "linear, 0 to Nyquist", "floor": "magnitude floored at -300 dB; phase unwrapped along the grid"}
        return Computed(values=values, primary=primary, warnings=warnings, data=clean(data), artifacts=[table])

    return run("dsp_filter_design", inputs, compute)


__all__ = ["filter_design"]
