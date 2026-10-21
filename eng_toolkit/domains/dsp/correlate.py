"""``dsp_correlate``: time-domain statistics, autocorrelation and dominant period, cross-correlation and delay, Hilbert envelope.

Definitions (x the record, N samples, mu its mean):
* variance (population) ``mean((x - mu)^2)``; RMS ``sqrt(mean(x^2))``; peak ``max |x|``; crest factor ``peak/RMS``.
* autocorrelation ``c[k] = sum_{n=0}^{N-1-k} (x[n]-mu)(x[n+k]-mu)``, listed normalised by ``c[0]`` (biased estimate, ``r[0] = 1``); the dominant
  period is read from the unbiased form ``c[k]/(N-k)`` (no taper towards long lags): the first local maximum after the first zero crossing that is at
  least 0.9 of the highest one (so a multiple of the period is not mistaken for it).
* cross-correlation ``c[k] = sum_n x[n] y[n+k]`` (means removed unless told otherwise), normalised by ``sqrt(sum x^2 sum y^2)``; a positive lag k
  means y is x delayed by k samples (``y[n] = x[n-k]``).  Sub-sample lag: parabola through the three values around the maximum.
* analytic signal by the FFT: keep DC (and Nyquist), double the positive frequencies, zero the negative ones; envelope = modulus.  Exact for a
  record that holds whole periods (the FFT treats the record as periodic).
"""

from __future__ import annotations

import math
from typing import Annotated, Any

import numpy as np
from pydantic import Field

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.dsp._common import (
    Computed,
    JobHandle,
    QuantityOut,
    clean,
    csv_text,
    is_blank,
    plain,
    qty,
    read_samples,
    record_text,
    resolve_rate,
    run,
    whole_number,
)
from eng_toolkit.domains.dsp._server import server
from eng_toolkit.units import UnitsError

MAX_LAG = 100_000
HZ = "hertz"


def _fft_size(n: int) -> int:
    return 1 << (n - 1).bit_length()


def autocovariance(x: np.ndarray, max_lag: int) -> np.ndarray:
    """c[0..max_lag] of the mean-removed record by FFT (zero padded to at least 2N, so the linear and not the circular sum)."""
    n = len(x)
    size = _fft_size(2 * n)
    spec = np.fft.rfft(x, size)
    return np.fft.irfft(spec.real ** 2 + spec.imag ** 2, size)[: max_lag + 1]


def cross_covariance(x: np.ndarray, y: np.ndarray, max_lag: int) -> tuple[np.ndarray, np.ndarray]:
    """(lags, c[k] = sum_n x[n] y[n+k]) for k in -max_lag .. max_lag."""
    nx, ny = len(x), len(y)
    size = _fft_size(nx + ny - 1)
    corr = np.fft.irfft(np.conj(np.fft.rfft(x, size)) * np.fft.rfft(y, size), size)
    lags = np.arange(-max_lag, max_lag + 1)
    valid = (lags >= -(nx - 1)) & (lags <= ny - 1)  # outside the overlap the sum is empty; the circular index would alias a real lag
    return lags, np.where(valid, corr[lags % size], 0.0)


def analytic_envelope(x: np.ndarray) -> np.ndarray:
    n = len(x)
    spec = np.fft.fft(x)
    h = np.zeros(n)
    h[0] = 1.0
    if n % 2 == 0:
        h[n // 2] = 1.0
        h[1:n // 2] = 2.0
    else:
        h[1:(n + 1) // 2] = 2.0
    return np.abs(np.fft.ifft(spec * h))


def parabola_peak(values: np.ndarray, k: int) -> tuple[float, float]:
    """Offset in samples (within +-0.5) and value of the parabola through values[k-1], values[k], values[k+1]."""
    a, b, c = float(values[k - 1]), float(values[k]), float(values[k + 1])
    denom = a - 2.0 * b + c
    if denom >= 0.0:
        return 0.0, b
    delta = max(-0.5, min(0.5, 0.5 * (a - c) / denom))
    return delta, b - 0.25 * (a - c) * delta


def dominant_period(x_centered: np.ndarray, max_lag: int) -> dict[str, Any]:
    n = len(x_centered)
    limit = min(max_lag, n // 2)
    c = autocovariance(x_centered, limit)
    if c[0] <= 0.0 or limit < 3:
        return {"found": False, "reason": "no variance or too few lags"}
    ru = c / (n - np.arange(limit + 1)) / (c[0] / n)
    neg = np.nonzero(ru < 0.0)[0]
    if neg.size == 0:
        return {"found": False, "reason": f"the autocorrelation does not cross zero within {limit} lags: the period (if any) is longer than {limit} samples or there is none", "lags_searched": limit}
    start = int(neg[0])
    top = float(ru[start:].max())
    inner = np.arange(max(start, 1), limit)
    maxima = inner[(ru[inner] >= ru[inner - 1]) & (ru[inner] > ru[inner + 1])]
    prominent = maxima[ru[maxima] >= 0.9 * top] if maxima.size else maxima
    k = int(prominent[0]) if prominent.size else start + int(np.argmax(ru[start:]))  # the first peak that is nearly as high as the highest: the fundamental period, not a multiple
    if k >= limit or ru[k] < 0.2:
        return {"found": False, "reason": "no clear peak after the first zero crossing (periodicity weaker than 0.2, or the peak lies at the end of the searched lags)", "strongest": float(ru[k]), "at_lag": k, "lags_searched": limit}
    delta, value = parabola_peak(ru, k) if 1 <= k < limit else (0.0, float(ru[k]))
    return {"found": True, "lag_samples": k, "lag_refined_samples": k + delta, "strength": float(ru[k]), "strength_refined": value, "lags_searched": limit}


@server.tool(
    name="correlate",
    description=(
        "Time-domain analysis of one record and optionally a second. One record: mean, RMS, peak, peak-to-peak, crest factor, variance, "
        "autocorrelation up to max_lag (CSV) and the dominant period from it. With a second record (samples_b / samples_b_file): "
        "cross-correlation, the lag of the maximum in samples and seconds with parabolic sub-sample refinement, the normalised correlation "
        "coefficient and the delay estimate (positive = second record lags the first). envelope=true adds the FFT Hilbert envelope (exact "
        "for records holding whole periods). Samples: JSON lists or CSV files; sample_rate with unit for seconds."
    ),
)
def correlate(
    samples: Annotated[str, Field(description="First record as JSON text, e.g. '[0, 1, 0, -1]' (at most 4194304). Or samples_file")] = "",
    samples_file: Annotated[str, Field(description="First record as CSV/TXT file: values, or time(s),value")] = "",
    samples_b: Annotated[str, Field(description="Optional second record as JSON text, for cross-correlation")] = "",
    samples_b_file: Annotated[str, Field(description="Optional second record as CSV/TXT file")] = "",
    sample_rate: Annotated[str, Field(description="Sample rate with unit, e.g. '1 kHz'; gives lags and periods in seconds (optional with a time column)")] = "",
    max_lag: Annotated[int, Field(description="Largest lag in samples, 1..100000; 0 = automatic (half the record, at most 100000; cross-correlation: the full overlap range)")] = 0,
    remove_mean: Annotated[bool, Field(description="Remove each record's mean before correlating (default true)")] = True,
    envelope: Annotated[bool, Field(description="Also compute the Hilbert-transform envelope of the first record (CSV) (default false)")] = False,
) -> ToolResult:
    inputs = {
        "samples": record_text(samples), "samples_file": samples_file, "samples_b": record_text(samples_b), "samples_b_file": samples_b_file,
        "sample_rate": sample_rate, "max_lag": max_lag, "remove_mean": remove_mean, "envelope": envelope,
    }

    def compute(handle: JobHandle) -> Computed:
        for name, flag in (("remove_mean", remove_mean), ("envelope", envelope)):
            if not isinstance(flag, bool):
                raise UnitsError("input.invalid", f"'{name}' must be true or false", {"param": name})
        lag_in = whole_number(max_lag, name="max_lag", lo=0, hi=MAX_LAG)
        a = read_samples(handle, samples, samples_file, min_len=3)
        two = not (is_blank(samples_b) and is_blank(samples_b_file))
        b = read_samples(handle, samples_b, samples_b_file, param="samples_b", file_param="samples_b_file", min_len=3) if two else None
        fs = resolve_rate(a, sample_rate, required=False)
        if b is not None and b.fs is not None and fs is not None and abs(b.fs - fs) > 1e-3 * fs:
            raise UnitsError("input.invalid", f"the two files imply different sample rates ({fs:g} Hz and {b.fs:g} Hz)", {"param": "samples_b_file"})
        x = a.x
        n = len(x)
        warnings: list[str] = []
        mean = float(np.mean(x))
        centered = x - mean
        variance = float(np.mean(centered * centered))
        rms = float(math.sqrt(float(np.mean(x * x))))
        peak = float(np.max(np.abs(x)))
        values: dict[str, QuantityOut] = {
            "mean": plain(mean, label="mean"), "rms": plain(rms, label="rms"), "peak": plain(peak, label="peak"), "peak_to_peak": plain(float(np.max(x) - np.min(x)), label="peak_to_peak"),
            "variance": plain(variance, label="variance"), "standard_deviation": plain(math.sqrt(variance), label="standard_deviation"),
        }
        data: dict[str, Any] = {
            "samples": n, "sample_rate_hz": fs,
            "statistics": {
                "mean": mean, "rms": rms, "peak": peak, "peak_to_peak": float(np.max(x) - np.min(x)), "variance_population": variance,
                "variance_sample": float(np.var(x, ddof=1)) if n > 1 else None, "crest_factor": peak / rms if rms > 0.0 else "undefined (RMS is 0)",
                "definitions": "variance = mean((x - mean)^2); rms = sqrt(mean(x^2)); peak = max|x|; crest factor = peak/rms",
            },
        }
        if rms > 0.0:
            values["crest_factor"] = plain(peak / rms, label="crest_factor")
        artifacts = []
        primary = "rms"
        if not two:
            lag = (min(n // 2, MAX_LAG) if lag_in == 0 else lag_in)
            if lag >= n:
                raise UnitsError("input.invalid", f"'max_lag' = {lag} must be below the record length {n}", {"param": "max_lag"})
            c = autocovariance(centered if remove_mean else x, lag)
            if c[0] <= 0.0:
                data["autocorrelation"] = "not applicable (the record has no variance after removing the mean)" if remove_mean else "not applicable (all zeros)"
            else:
                r = c / c[0]
                lags = np.arange(lag + 1, dtype=float)
                cols = [lags] + ([lags / fs] if fs else []) + [r]
                artifacts.append(handle.write_text("autocorrelation.csv", csv_text(["lag_samples"] + (["lag_s"] if fs else []) + ["r"], cols), fmt="csv"))
                per = dominant_period(centered if remove_mean else x, lag)
                data["autocorrelation"] = {"max_lag": lag, "definition": "r[k] = sum (x[n]-mean)(x[n+k]-mean) / sum (x[n]-mean)^2 (biased estimate)", "table": {"file": "autocorrelation.csv", "rows": lag + 1}}
                if per["found"]:
                    period = per["lag_refined_samples"]
                    values["period_samples"] = plain(period, label="period_samples")
                    entry = {**per, "method": "first local maximum of the unbiased autocorrelation c[k]/(N-k) after its first zero crossing that reaches 0.9 of the highest; parabolic refinement on the three lags around it"}
                    if fs:
                        values["period"] = qty(period / fs, "second", label="period")
                        values["period_frequency"] = qty(fs / period, HZ, label="period_frequency")
                        entry.update({"period_s": period / fs, "frequency_hz": fs / period})
                    data["dominant_period"] = entry
                    primary = "period_samples"
                    if n < 3 * per["lag_samples"]:
                        warnings.append(f"fewer than 3 periods in the record ({n / per['lag_samples']:.1f}): the period estimate is rough")
                else:
                    data["dominant_period"] = per
                if lag_in and lag < 3:
                    warnings.append("max_lag below 3: no period can be searched")
        else:
            y = b.x
            ny = len(y)
            xs, ys = (x - mean, y - float(np.mean(y))) if remove_mean else (x, y)
            ex, ey = float(np.sum(xs * xs)), float(np.sum(ys * ys))
            if ex <= 0.0 or ey <= 0.0:
                raise UnitsError("input.invalid", "a record with no variance (constant, or all zeros after removing the mean) has no cross-correlation", {"param": "samples_b" if ex > 0.0 else "samples"})
            full = min(max(n, ny) - 1, MAX_LAG)
            lag = full if lag_in == 0 else min(lag_in, max(n, ny) - 1)
            if lag_in and lag_in > max(n, ny) - 1:
                warnings.append(f"max_lag {lag_in} exceeds the longest record; using {lag}")
            lags, cov = cross_covariance(xs, ys, lag)
            rho = cov / math.sqrt(ex * ey)
            k = int(np.argmax(rho))
            peak_lag = int(lags[k])
            if 0 < k < len(lags) - 1:
                delta, rho_ref = parabola_peak(rho, k)
            else:
                delta, rho_ref = 0.0, float(rho[k])
                warnings.append("the correlation maximum lies at the end of the searched lag range: the true delay may be beyond max_lag")
            lag_ref = peak_lag + delta
            values["lag_samples"] = plain(float(peak_lag), label="lag_samples")
            values["lag_refined_samples"] = plain(lag_ref, label="lag_refined_samples")
            values["correlation_coefficient"] = plain(float(rho[k]), label="correlation_coefficient")
            if fs:
                values["delay"] = qty(lag_ref / fs, "second", label="delay")
            neg = int(np.argmin(rho))
            data["cross_correlation"] = {
                "records": [n, ny], "max_lag": lag, "lag_samples": peak_lag, "lag_refined_samples": lag_ref, "delay_s": lag_ref / fs if fs else None,
                "correlation_coefficient": float(rho[k]), "correlation_coefficient_refined": rho_ref, "most_negative": {"lag_samples": int(lags[neg]), "value": float(rho[neg])},
                "sign": "positive lag: the second record is the first delayed by that many samples (y[n] = x[n - lag])",
                "normalisation": "sum x[n] y[n+k] / sqrt(sum x^2 sum y^2) over the full records (means removed unless remove_mean=false); a value of 1 needs the whole of both records inside the overlap",
                "refinement": "parabola through the three values around the maximum; accurate to about 0.01 sample for smooth, oversampled signals, worse for signals near the Nyquist frequency",
                "table": {"file": "cross_correlation.csv", "rows": len(lags)},
            }
            cols = [lags.astype(float)] + ([lags / fs] if fs else []) + [rho]
            artifacts.append(handle.write_text("cross_correlation.csv", csv_text(["lag_samples"] + (["lag_s"] if fs else []) + ["rho"], cols), fmt="csv"))
            if float(rho[k]) < 0.3:
                warnings.append(f"the correlation maximum {float(rho[k]):.2f} is low: the records are weakly related and the delay is not reliable")
            primary = "lag_refined_samples"
        if envelope:
            env = analytic_envelope(x)
            span = float(np.max(x) - np.min(x))
            if span > 0.0 and abs(float(x[-1] - x[0])) > 0.1 * span:
                warnings.append("the record's ends differ by more than 10 % of its range, so it is not periodic: the FFT Hilbert envelope has errors near both ends")
            values["envelope_max"] = plain(float(env.max()), label="envelope_max")
            values["envelope_mean"] = plain(float(env.mean()), label="envelope_mean")
            cols = [np.arange(n, dtype=float)] + ([np.arange(n) / fs] if fs else []) + [x, env]
            artifacts.append(handle.write_text("envelope.csv", csv_text(["index"] + (["time_s"] if fs else []) + ["x", "envelope"], cols), fmt="csv"))
            data["envelope"] = {
                "method": "analytic signal by FFT (keep DC and Nyquist, double positive frequencies, drop negative); envelope = |x + j H{x}|",
                "exactness": "exact for a record holding whole periods of every component (periodic extension); other records show wrap-around error at both ends",
                "max": float(env.max()), "mean": float(env.mean()), "table": {"file": "envelope.csv", "rows": n},
            }
        return Computed(values=values, primary=primary, warnings=warnings, data=clean(data), artifacts=artifacts)

    return run("dsp_correlate", inputs, compute)


__all__ = ["analytic_envelope", "autocovariance", "correlate", "cross_covariance", "dominant_period", "parabola_peak"]
