"""``dsp_filter_apply``: run a record through a filter given as second-order sections, (b, a), or a design specification.

IIR sections and direct-form (b, a) run as transposed direct form II (the recursion of ``scipy.signal.lfilter``) from a zero state;
a FIR (a = [1]) is a convolution, by FFT for long ones.  An unstable IIR filter is refused unless ``allow_unstable``.  Zero-phase
filtering is forward then backward on an odd-reflection extension with a steady-state start (see :mod:`eng_toolkit.domains.dsp._apply`).
"""

from __future__ import annotations

import json
import math
from typing import Annotated, Any

import numpy as np
from pydantic import Field

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.dsp import _apply as ap
from eng_toolkit.domains.dsp import _iir as iir
from eng_toolkit.domains.dsp._common import (
    MAX_IIR_ORDER,
    MAX_TAPS,
    Computed,
    JobHandle,
    QuantityOut,
    array_from_json,
    clean,
    csv_text,
    is_blank,
    plain,
    qty,
    read_samples,
    record_text,
    resolve_rate,
    run,
)
from eng_toolkit.domains.dsp._server import server
from eng_toolkit.domains.dsp.filter_design import filter_design
from eng_toolkit.errors import ToolFailure
from eng_toolkit.units import UnitsError

COEF_MAX = 1e12
MAX_SECTIONS = 40  # a band design of prototype order 40 has 40 sections


def _coefficients(text: Any, param: str, max_len: int) -> np.ndarray:
    arr = array_from_json(text, param=param, min_len=1)
    if len(arr) > max_len:
        raise UnitsError("input.out_of_range", f"'{param}' has {len(arr)} coefficients, above the limit {max_len}", {"param": param, "limit": max_len})
    if float(np.max(np.abs(arr))) > COEF_MAX:
        raise UnitsError("input.out_of_range", f"'{param}' has a coefficient of magnitude above {COEF_MAX:g}", {"param": param, "limit": COEF_MAX})
    return arr


def _bad_constant(token: str):
    raise UnitsError("input.invalid", f"'sos' contains {token}; NaN and infinity are not allowed", {"param": "sos"})


def _sections(text: Any) -> np.ndarray:
    if not isinstance(text, str) or len(text) > 200_000:
        raise UnitsError("input.out_of_range", "'sos' must be JSON text of at most 200000 characters", {"param": "sos"})
    try:
        obj = json.loads(text, parse_constant=_bad_constant)
    except ToolFailure:
        raise
    except (ValueError, RecursionError) as exc:
        raise UnitsError("input.invalid", f"'sos' is not valid JSON: {str(exc)[:80]}", {"param": "sos"}) from None
    if not isinstance(obj, list) or not obj or not all(isinstance(r, list) and len(r) == 6 for r in obj):
        raise UnitsError("input.invalid", "'sos' must be a JSON list of rows [b0, b1, b2, a0, a1, a2]", {"param": "sos"})
    if len(obj) > MAX_SECTIONS:
        raise UnitsError("input.out_of_range", f"'sos' has {len(obj)} sections, above the limit {MAX_SECTIONS}", {"param": "sos", "limit": MAX_SECTIONS})
    if not {type(v) for row in obj for v in row} <= {int, float}:
        raise UnitsError("input.invalid", "'sos' rows must hold plain numbers (no text, booleans or nulls)", {"param": "sos"})
    try:
        arr = np.array(obj)
    except (ValueError, TypeError, OverflowError):
        raise UnitsError("input.invalid", "'sos' rows must hold plain numbers", {"param": "sos"}) from None
    if arr.dtype.kind not in "iuf":
        raise UnitsError("input.invalid", "'sos' rows must hold plain numbers (no text, booleans or nulls)", {"param": "sos"})
    arr = arr.astype(float)
    if not np.all(np.isfinite(arr)):
        raise UnitsError("input.invalid", "'sos' holds a value that is not finite", {"param": "sos"})
    if float(np.max(np.abs(arr))) > COEF_MAX:
        raise UnitsError("input.out_of_range", f"'sos' has a coefficient of magnitude above {COEF_MAX:g}", {"param": "sos", "limit": COEF_MAX})
    if np.any(arr[:, 3] == 0.0):
        raise UnitsError("input.invalid", "'sos' has a section whose a0 is 0", {"param": "sos"})
    with np.errstate(all="ignore"):
        normalised = arr / arr[:, 3:4]
    if not np.all(np.isfinite(normalised)):
        raise UnitsError("input.out_of_range", "'sos' has a section whose a0 is so small that dividing by it overflows (the sections are normalised to a0 = 1)", {"param": "sos"})
    return normalised


@server.tool(
    name="filter_apply",
    description=(
        "Apply a filter to samples. Give the filter as sos (JSON rows [b0,b1,b2,a0,a1,a2]), or b (and a; empty a = FIR), or a design "
        "(family, response, order+cutoff or passband_edge/stopband_edge, as in dsp_filter_design). IIR runs as transposed direct form II; "
        "an unstable IIR filter is refused unless allow_unstable. zero_phase=true filters forward and backward (odd-reflection edge "
        "extension, steady-state start; the result has no phase delay and the squared magnitude response). FIR delay (N-1)/2 samples "
        "is reported, and removed with compensate_delay. Samples: JSON list or CSV file. Returns input/output statistics, the stability "
        "check, the delay, and the output as a CSV artifact."
    ),
)
def filter_apply(
    samples: Annotated[str, Field(description="Samples as JSON text, e.g. '[0, 1, 0, -1]' (at most 4194304). Or use samples_file")] = "",
    samples_file: Annotated[str, Field(description="Path of a CSV/TXT file: one column of values, or time(s),value; numbers only")] = "",
    sample_rate: Annotated[str, Field(description="Sample rate with unit, e.g. '8 kHz'; needed for a design, otherwise only for time stamps")] = "",
    sos: Annotated[str, Field(description="Second-order sections as JSON, e.g. '[[0.2,0.4,0.2,1,-0.3,0.1]]' (rows b0,b1,b2,a0,a1,a2; at most 40 rows)")] = "",
    b: Annotated[str, Field(description="Numerator coefficients as JSON list (at most 4095 for FIR, 41 with a)")] = "",
    a: Annotated[str, Field(description="Denominator coefficients as JSON list, a[0] not 0; empty = FIR")] = "",
    family: Annotated[str, Field(description="Design instead of sos/b: butterworth, chebyshev1, chebyshev2, fir_rectangular, fir_hann, fir_hamming, fir_blackman, fir_kaiser")] = "",
    response: Annotated[str, Field(description="Design: lowpass, highpass, bandpass or bandstop")] = "",
    order: Annotated[int, Field(description="Design: IIR prototype order 1..40, or FIR taps 3..4095; 0 = from the specification")] = 0,
    cutoff: Annotated[str, Field(description="Design with order: cutoff frequency with unit ('low, high' for bands)")] = "",
    passband_edge: Annotated[str, Field(description="Design specification: passband edge(s) with unit")] = "",
    stopband_edge: Annotated[str, Field(description="Design specification: stopband edge(s) with unit")] = "",
    passband_ripple_db: Annotated[float, Field(description="Design: passband ripple Rp in dB")] = 0.0,
    stopband_attenuation_db: Annotated[float, Field(description="Design: stopband attenuation Rs in dB")] = 0.0,
    kaiser_beta: Annotated[float, Field(description="Design fir_kaiser: beta; 0 = from the attenuation")] = 0.0,
    zero_phase: Annotated[bool, Field(description="Forward-backward filtering (default false)")] = False,
    compensate_delay: Annotated[bool, Field(description="FIR, causal: advance the output by (N-1)/2 samples when that is a whole number (default false)")] = False,
    allow_unstable: Annotated[bool, Field(description="Run an unstable or marginal IIR filter anyway (default false: refused); the output is refused if it overflows")] = False,
) -> ToolResult:
    inputs = {
        "samples": record_text(samples), "samples_file": samples_file, "sample_rate": sample_rate, "sos": record_text(sos, 2000), "b": record_text(b, 2000),
        "a": record_text(a, 2000), "family": family, "response": response, "order": order, "cutoff": cutoff, "passband_edge": passband_edge,
        "stopband_edge": stopband_edge, "passband_ripple_db": passband_ripple_db, "stopband_attenuation_db": stopband_attenuation_db,
        "kaiser_beta": kaiser_beta, "zero_phase": zero_phase, "compensate_delay": compensate_delay, "allow_unstable": allow_unstable,
    }

    def compute(handle: JobHandle) -> Computed:
        for name, flag in (("zero_phase", zero_phase), ("compensate_delay", compensate_delay), ("allow_unstable", allow_unstable)):
            if not isinstance(flag, bool):
                raise UnitsError("input.invalid", f"'{name}' must be true or false", {"param": name})
        have = [name for name, text in (("sos", sos), ("b", b), ("family", family)) if not is_blank(text)]
        if not have:
            raise UnitsError("input.missing", "give the filter as 'sos', as 'b' (and 'a'), or as a design ('family' and 'response')", {"param": "sos"})
        if len(have) > 1:
            raise UnitsError("input.invalid", f"give one filter source, not several ({have})", {"param": have[1]})
        if not is_blank(a) and is_blank(b):
            raise UnitsError("input.invalid", "'a' needs 'b'", {"param": "b"})
        if have[0] == "family" and is_blank(response):
            raise UnitsError("input.missing", "a design needs 'response' (lowpass, highpass, bandpass or bandstop)", {"param": "response"})
        data_in = read_samples(handle, samples, samples_file, min_len=2)
        x = data_in.x
        n = len(x)
        design_job = None
        warnings: list[str] = []
        sos_arr = b_arr = a_arr = None
        fs = resolve_rate(data_in, sample_rate, required=(have[0] == "family"))
        if have[0] == "sos":
            sos_arr = _sections(sos)
            source = "sections"
        elif have[0] == "b":
            b_arr = _coefficients(b, "b", MAX_TAPS)
            a_arr = _coefficients(a, "a", MAX_IIR_ORDER + 1) if not is_blank(a) else np.array([1.0])
            if a_arr[0] == 0.0:
                raise UnitsError("input.invalid", "'a[0]' must not be 0", {"param": "a"})
            if len(a_arr) > 1 and len(b_arr) > MAX_IIR_ORDER + 1:
                raise UnitsError("input.out_of_range", f"an IIR filter (with 'a') may have at most {MAX_IIR_ORDER + 1} numerator coefficients, got {len(b_arr)}", {"param": "b", "limit": MAX_IIR_ORDER + 1})
            with np.errstate(all="ignore"):
                b_arr, a_arr = ap.normalise(b_arr, a_arr)
            if not (np.all(np.isfinite(b_arr)) and np.all(np.isfinite(a_arr))):
                raise UnitsError("input.out_of_range", "'a[0]' is so small that dividing by it overflows (the filter is normalised to a[0] = 1)", {"param": "a"})
            if len(a_arr) > 1 and max(len(a_arr), len(b_arr)) - 1 > 8:
                warnings.append(f"direct-form IIR of order {max(len(a_arr), len(b_arr)) - 1}: coefficient rounding can move the poles; prefer second-order sections")
            source = "direct form (b, a)"
        else:
            res = filter_design(
                family=family, response=response, sample_rate=f"{fs!r} Hz", order=order, cutoff=cutoff, passband_edge=passband_edge, stopband_edge=stopband_edge,
                passband_ripple_db=passband_ripple_db, stopband_attenuation_db=stopband_attenuation_db, kaiser_beta=kaiser_beta, points=16,
            )
            if not res.ok:
                raise ToolFailure(res.error.family, f"design: {res.error.message}", res.error.details)
            warnings += [f"design: {w}" for w in res.warnings[1:]]
            design_job = res.data.get("job_id")
            if "sos" in res.data:
                sos_arr = np.array(res.data["sos"], dtype=float)
                source = f"designed {family} {response} (second-order sections)"
            else:
                b_arr, a_arr = np.array(res.data["coefficients"], dtype=float), np.array([1.0])
                source = f"designed {family} {response} (FIR)"
        is_fir = sos_arr is None and len(a_arr) == 1
        stab = ap.stability(sos_arr, b_arr, a_arr) if not is_fir else {"max_pole_radius": 0.0, "verdict": "stable", "stable": True}
        if not stab["stable"]:
            if not allow_unstable:
                raise UnitsError(
                    "input.invalid",
                    f"the IIR filter is {stab['verdict']}: largest pole radius {stab['max_pole_radius']:.9g} (stable needs < 1). Refused; set allow_unstable=true to run it anyway",
                    {"param": "allow_unstable", "max_pole_radius": stab["max_pole_radius"], "verdict": stab["verdict"]},
                )
            if zero_phase:
                raise UnitsError("input.invalid", "zero-phase filtering of an unstable filter is not defined (the backward pass is not the time reverse of a stable system)", {"param": "zero_phase"})
            warnings.append(f"running an {stab['verdict']} filter (largest pole radius {stab['max_pole_radius']:.9g}) because allow_unstable is set; the output can grow without bound")
        passes = 2 if zero_phase else 1
        if sos_arr is not None:
            work = (n + 6 * len(sos_arr)) * len(sos_arr) * passes
        elif is_fir:
            work = 0
        else:
            work = n * max(1, (max(len(a_arr), len(b_arr)) - 1) // 2 + 1) * passes
        if work > ap.WORK_LIMIT:
            raise UnitsError("input.out_of_range", f"this call needs about {work:.2e} recursion steps, above the limit {ap.WORK_LIMIT:.1e} (a pure-Python recursion); shorten the record or lower the order", {"param": "samples", "work": work, "limit": ap.WORK_LIMIT})
        delay_info: dict[str, Any] = {}
        pad = None
        steady = None
        try:
            if zero_phase:
                try:
                    y, pad, steady = ap.filtfilt(x, sos_arr, b_arr, a_arr)
                except ValueError as exc:
                    raise UnitsError("input.invalid", str(exc), {"param": "samples"}) from None
                edge = (
                    f"odd reflection about the end points, {pad} samples at each end, steady-state initial conditions"
                    + ("" if steady else " (not available: a pole at z = 1, zero initial state used)")
                )
                delay_info = {"group_delay_samples": 0.0, "note": "zero phase: no delay; the magnitude response is squared"}
            elif is_fir:
                taps = len(b_arr)
                half = (taps - 1) / 2.0
                sym = bool(np.array_equal(b_arr, b_arr[::-1]) or np.array_equal(b_arr, -b_arr[::-1]))
                delay_info = {"group_delay_samples": half if sym else None, "group_delay_s": half / fs if (sym and fs) else None, "linear_phase": sym, "taps": taps}
                if compensate_delay:
                    if not sym:
                        warnings.append("compensate_delay: the taps are not symmetric, so there is no single delay to remove; output not shifted")
                        y = ap.convolve_causal(x, b_arr)
                    elif half != int(half):
                        warnings.append(f"compensate_delay: the delay {half} samples is not a whole number (even number of taps); output not shifted")
                        y = ap.convolve_causal(x, b_arr)
                    else:
                        full = ap.convolve_causal(x, b_arr, n + taps - 1)
                        y = full[int(half):int(half) + n]
                        delay_info["compensated_samples"] = int(half)
                else:
                    y = ap.convolve_causal(x, b_arr)
                    if sym:
                        warnings.append(f"the output is delayed by {half:g} samples (linear-phase FIR); use compensate_delay for whole-sample delays or zero_phase")
                edge = f"zero initial state: the first {taps - 1} output samples are the start-up transient" if not delay_info.get("compensated_samples") else (
                    f"zero state outside the record: the first {taps - 1 - delay_info['compensated_samples']} and the last {delay_info['compensated_samples']} samples are affected by the missing data"
                )
            else:
                y = ap.filter_signal(x, sos_arr, b_arr, a_arr)
                edge = "zero initial state: the start-up transient is in the output"
                delay_rows = []
                for frac in (0.01, 0.1, 0.25, 0.5):
                    w = math.pi * frac
                    if sos_arr is not None:
                        tau, _ = iir.group_delay_sos(sos_arr, w)
                    else:
                        tb, pb = iir._poly_delay(b_arr, np.array([w]))
                        ta, _ = iir._poly_delay(a_arr, np.array([w]))
                        tau = float(tb[0] - ta[0]) if pb[0] > 1e-9 else float("nan")
                    if math.isfinite(tau):
                        delay_rows.append({"frequency_fraction_of_nyquist": frac, "group_delay_samples": tau})
                delay_info = {"group_delay_at": delay_rows, "note": "an IIR filter has frequency-dependent delay; use zero_phase=true for none"}
                if compensate_delay:
                    warnings.append("compensate_delay applies to FIR filters only; ignored")
        except OverflowError as exc:
            raise UnitsError("input.out_of_range", f"{exc}: the output left the representable range, so no result is returned", {"param": "allow_unstable"}) from None
        if not np.all(np.isfinite(y)) or float(np.max(np.abs(y))) > 1e150:
            raise UnitsError("input.out_of_range", "the output left the representable range (the filter's gain or instability is too large)", {"param": "allow_unstable"})
        if sos_arr is not None:
            dc_gain = iir.dc_gain(sos_arr)
        else:
            den = float(np.sum(a_arr))
            dc_gain = float(np.sum(b_arr)) / den if den != 0.0 and math.isfinite(float(np.sum(b_arr)) / den) else None  # H(z = 1) = sum(b)/sum(a)
        in_stats, out_stats = ap.stats(x), ap.stats(y)
        rms_gain = out_stats["rms"] / in_stats["rms"] if in_stats["rms"] > 0 else None
        values: dict[str, QuantityOut] = {
            "output_rms": plain(out_stats["rms"], label="output_rms"),
            "output_peak": plain(out_stats["peak"], label="output_peak"),
            "input_rms": plain(in_stats["rms"], label="input_rms"),
            "output_mean": plain(out_stats["mean"], label="output_mean"),
            "max_pole_radius": plain(stab["max_pole_radius"], label="max_pole_radius"),
        }
        if rms_gain is not None:
            values["rms_gain"] = plain(rms_gain, label="rms_gain")
        if "group_delay_samples" in delay_info and delay_info["group_delay_samples"] is not None:
            values["group_delay_samples"] = plain(float(delay_info["group_delay_samples"]), label="group_delay_samples")
        cols = [np.arange(n, dtype=float)]
        header = ["index"]
        if fs is not None:
            cols.append(np.arange(n) / fs + (data_in.t0 or 0.0))
            header.append("time_s")
        cols += [x, y]
        header += ["input", "output"]
        table = handle.write_text("filtered.csv", csv_text(header, cols), fmt="csv")
        data: dict[str, Any] = {
            "samples": n, "sample_rate_hz": fs, "source": data_in.source, "filter": source, "zero_phase": zero_phase, "edge_handling": edge,
            "stability": {**stab, "method": "roots of the denominator(s)"},
            "input": in_stats, "output": out_stats, "dc_gain": dc_gain, "dc_gain_definition": "H(z = 1): product over sections of sum(b)/sum(a); null when a pole is at z = 1", "delay": delay_info,
            "structure": "transposed direct form II, pure Python recursion" if not is_fir else "convolution (FFT for long filters)",
            "table": {"file": "filtered.csv", "rows": n, "columns": header},
            "design_job_id": design_job,
        }
        if sos_arr is not None:
            data["sections"] = len(sos_arr)
        else:
            data["numerator_length"], data["denominator_length"] = len(b_arr), len(a_arr)
        if steady is not None:
            data["steady_state_initial_conditions"] = steady
        return Computed(values=values, primary="output_rms", warnings=warnings, data=clean(data), artifacts=[table])

    return run("dsp_filter_apply", inputs, compute)


__all__ = ["filter_apply"]
