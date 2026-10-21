"""``dsp_spectrum``: amplitude spectrum, power spectral density, peaks and single-tone converter metrics of a sampled record.

Definitions are in :mod:`eng_toolkit.domains.dsp._spectral` (scaling, the coherent-gain correction, Welch averaging) and are also returned in
``data``.  The window is the periodic (DFT-even) form, so an on-bin sine of amplitude A reads exactly A for every window; an off-bin tone
reads low by the scalloping loss of the window (3.92 dB rectangular, 1.42 dB Hann, 0.01 dB flat top at worst).
"""

from __future__ import annotations

import math
from typing import Annotated, Any

import numpy as np
from pydantic import Field

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.dsp import _spectral as sp
from eng_toolkit.domains.dsp import _windows as win
from eng_toolkit.domains.dsp._common import (
    MAX_TABLE_ROWS,
    Computed,
    JobHandle,
    QuantityOut,
    choice,
    clean,
    csv_text,
    plain,
    plain_number,
    qty,
    read_samples,
    record_text,
    resolve_rate,
    run,
    whole_number,
)
from eng_toolkit.domains.dsp._server import server
from eng_toolkit.units import UnitsError

HZ = "hertz"


def _table(s: sp.Spectrum, rows: int) -> tuple[list[np.ndarray], bool]:
    """Spectrum columns, max-hold decimated to at most ``rows`` rows (a peak is never lost, only its neighbours)."""
    nb = len(s.amp)
    if nb <= rows:
        return [s.freqs, s.amp, s.psd], False
    g = math.ceil(nb / rows)
    pad = (-nb) % g
    a = np.concatenate([s.amp, np.zeros(pad)]).reshape(-1, g)
    p = np.concatenate([s.psd, np.zeros(pad)]).reshape(-1, g)
    arg = np.argmax(a, axis=1) + np.arange(a.shape[0]) * g
    arg = np.minimum(arg, nb - 1)
    return [s.freqs[arg], a.max(axis=1), p.max(axis=1)], True


@server.tool(
    name="spectrum",
    description=(
        "One-sided spectrum of a sampled record: amplitude spectrum (an on-bin sine of amplitude A reads A, coherent-gain corrected) and power "
        "spectral density (unit^2/Hz), by FFT with a rectangular, hann, hamming, blackman or flattop window, optional Welch averaging "
        "(segment_length, overlap_percent), a peak list with parabolic interpolation of frequency and amplitude, and with adc_metrics the "
        "single-tone SNR, SINAD, THD (harmonics 2..N), SFDR and ENOB with their definitions. Samples: JSON list in 'samples' or a CSV file "
        "(value, or time,value) in 'samples_file'; sample_rate with unit unless the file has a time column. Returns bin width, ENBW, "
        "leakage/resolution warnings and the spectrum table as CSV (rows bounded, max-hold decimated)."
    ),
)
def spectrum(
    samples: Annotated[str, Field(description="Samples as JSON text, e.g. '[0.1, 0.5, -0.2]' (at most 4194304 values). Or use samples_file")] = "",
    samples_file: Annotated[str, Field(description="Path of a CSV/TXT file: one column of values, or two columns time(s),value; numbers only, no header")] = "",
    sample_rate: Annotated[str, Field(description="Sample rate with unit, e.g. '48 kHz'; optional when the file has a time column")] = "",
    window: Annotated[str, Field(description="rectangular, hann, hamming, blackman or flattop (default hann)")] = "hann",
    segment_length: Annotated[int, Field(description="Welch segment length in samples (8..N); 0 = one FFT of the whole record")] = 0,
    overlap_percent: Annotated[float, Field(description="Welch overlap in percent, 0 to below 100 (default 50); used with segment_length")] = 50.0,
    remove_mean: Annotated[bool, Field(description="Subtract each segment's mean before windowing (default false)")] = False,
    peaks: Annotated[int, Field(description="Maximum number of peaks to list, 0..100 (default 5)")] = 5,
    peak_threshold_db: Annotated[float, Field(description="Only peaks within this many dB below the highest one (default 60)")] = 60.0,
    adc_metrics: Annotated[bool, Field(description="Compute SNR, SINAD, THD, SFDR, ENOB assuming a single tone (default false)")] = False,
    harmonics: Annotated[int, Field(description="Highest harmonic number counted in THD (2..50, default 5)")] = 5,
    full_scale_amplitude: Annotated[float, Field(description="Converter full-scale peak amplitude in the unit of the samples, for ENOB at less than full scale; 0 = tone assumed at full scale")] = 0.0,
    table_rows: Annotated[int, Field(description="Maximum rows of the spectrum table, 16..20000 (default 2048)")] = 2048,
) -> ToolResult:
    inputs = {
        "samples": record_text(samples), "samples_file": samples_file, "sample_rate": sample_rate, "window": window, "segment_length": segment_length,
        "overlap_percent": overlap_percent, "remove_mean": remove_mean, "peaks": peaks, "peak_threshold_db": peak_threshold_db, "adc_metrics": adc_metrics,
        "harmonics": harmonics, "full_scale_amplitude": full_scale_amplitude, "table_rows": table_rows,
    }

    def compute(handle: JobHandle) -> Computed:
        wname = choice(window, name="window", allowed=win.SPECTRAL_WINDOWS)
        overlap = plain_number(overlap_percent, name="overlap_percent", lo=0.0, hi=100.0)
        if overlap >= 100.0:
            raise UnitsError("input.invalid", "'overlap_percent' must be below 100: a 100 % overlap advances by zero samples", {"param": "overlap_percent"})
        n_peaks = whole_number(peaks, name="peaks", lo=0, hi=100)
        thr = plain_number(peak_threshold_db, name="peak_threshold_db", lo=0.0, hi=400.0, lo_open=True)
        n_harm = whole_number(harmonics, name="harmonics", lo=2, hi=50)
        fs_amp = plain_number(full_scale_amplitude, name="full_scale_amplitude", lo=0.0, hi=1e100)
        rows = whole_number(table_rows, name="table_rows", lo=16, hi=MAX_TABLE_ROWS)
        seg_in = whole_number(segment_length, name="segment_length", lo=0, hi=1 << 22, range_error=False)
        data_in = read_samples(handle, samples, samples_file, min_len=8)
        x = data_in.x
        n = len(x)
        fs = resolve_rate(data_in, sample_rate)
        seg = n if seg_in == 0 else seg_in
        if seg > n:
            raise UnitsError("input.invalid", f"'segment_length' = {seg} is longer than the record ({n} samples)", {"param": "segment_length"})
        if seg < 8:
            raise UnitsError("input.invalid", f"'segment_length' = {seg} is too short; use at least 8 samples", {"param": "segment_length"})
        noverlap = 0 if seg == n else int(round(seg * overlap / 100.0))
        noverlap = min(noverlap, seg - 1)
        warnings: list[str] = []
        s = sp.spectrum(x, fs, wname, seg, noverlap, bool(remove_mean))
        used = (s.n_seg - 1) * s.hop + seg
        if used < n:
            warnings.append(f"the last {n - used} sample(s) do not fill a segment and were not used")
        if n < 64:
            warnings.append(f"only {n} samples: the spectrum is very coarse ({s.df:g} Hz per bin)")
        pk = sp.find_peaks(s, n_peaks, thr)
        enbw_hz = s.enbw_bins * s.df
        hw = win.LOBE_HALF_WIDTH[wname]
        # leakage and resolution
        top = int(np.argmax(s.amp))
        if 1 <= top <= len(s.amp) - 2:
            delta, _ = sp.parabolic(s.amp, top)
            frac = abs(delta)
            if frac > 0.05:
                scallop = win.limit_properties(wname)["scalloping_loss_db"]
                warnings.append(
                    f"the strongest tone is about {frac:.2f} bin off a bin centre (not coherent with the record): "
                    + ("the rectangular window leaks into every bin; use a Hann or flat-top window or a coherent record" if wname == "rectangular"
                       else f"its amplitude reads low by up to {scallop:.2f} dB (scalloping loss of the {wname} window); use a flattop window for amplitudes")
                )
        close = [(a, b) for a in pk for b in pk if a["bin"] < b["bin"] and (b["bin"] - a["bin"]) <= 2 * hw]
        if close:
            warnings.append(f"two listed peaks are within {2 * hw} bins ({2 * hw * s.df:g} Hz) of each other, closer than the {wname} main lobe: they cannot be resolved reliably")
        if s.n_seg > 1:
            warnings.append(f"Welch average of {s.n_seg} segments of {seg} samples: bin width {s.df:g} Hz (resolution of one segment), the PSD variance is reduced by about {s.n_seg:g} for independent segments")

        values: dict[str, QuantityOut] = {
            "bin_width": qty(s.df, HZ, label="bin_width"),
            "enbw": qty(enbw_hz, HZ, label="enbw"),
            "coherent_gain": plain(s.coherent_gain, label="coherent_gain"),
            "rms": plain(float(np.sqrt(np.mean(x * x))), label="rms"),
            "mean": plain(float(np.mean(x)), label="mean"),
            "peak_amplitude": plain(float(pk[0]["amplitude"]) if pk else float(s.amp.max()), label="peak_amplitude"),
            "peak_frequency": qty(float(pk[0]["frequency_hz"]) if pk else float(s.freqs[int(np.argmax(s.amp))]), HZ, label="peak_frequency"),
            "integrated_power": plain(float(s.power.sum()), label="integrated_power"),
        }
        data: dict[str, Any] = {
            "sample_rate_hz": fs, "samples": n, "source": data_in.source, "window": wname, "segment_length": seg, "segments": s.n_seg,
            "overlap_samples": noverlap, "bin_width_hz": s.df, "nyquist_hz": fs / 2.0, "bins": len(s.amp), "enbw_bins": s.enbw_bins,
            "coherent_gain": s.coherent_gain, "amplitude_correction_factor": 1.0 / s.coherent_gain,
            "scaling": {
                "amplitude": "a_k = c_k |X_k| / sum(w), c_k = 2 (1 for DC and Nyquist): an on-bin sine of amplitude A reads A; the factor sum(w) = N x coherent_gain is the coherent-gain correction",
                "psd": "c_k |X_k|^2 / (fs sum(w^2)) in unit^2/Hz (Welch: |X_k|^2 averaged over segments)",
                "integral": "sum(PSD) x bin_width = sum over bins of c_k |X_k|^2/(N sum(w^2)) = sum((w x)^2)/sum(w^2); for a rectangular window the mean square of x",
                "window_form": "periodic (DFT-even) so cosine-sum windows have exactly-finite spectra and on-bin tones are exact",
            },
            "integrated_power_definition": "sum of the power spectrum over all bins (unit^2): the mean square of the windowed samples normalised by sum(w^2)",
            "peaks": pk,
            "peak_interpolation": "parabola through ln(amplitude) at the peak bin and its two neighbours; frequency offset d = 0.5 (a-c)/(a-2b+c) bins; approximate (error depends on window and offset, see docs/dsp/design.md)",
            "resolution": f"main lobe of the {wname} window is +-{hw} bins; ENBW {enbw_hz:g} Hz",
        }
        if adc_metrics:
            try:
                metrics, w2 = sp.adc_metrics(s, n_harm, hw, fs_amp)
            except ValueError as exc:
                raise UnitsError("input.invalid", f"converter metrics: {exc}", {"param": "adc_metrics"}) from None
            warnings += w2
            if wname == "rectangular" and metrics["bin_offset_of_fundamental"] > 0.05:
                warnings.append("converter metrics with a rectangular window and a non-coherent tone: the leakage of the tone is counted as noise; use a Hann/Blackman window or coherent sampling")
            if "snr_db" in metrics:
                values["snr_db"] = qty(metrics["snr_db"], "decibel", label="snr_db")
            if "sinad_db" in metrics:
                values["sinad_db"] = qty(metrics["sinad_db"], "decibel", label="sinad_db")
            if "thd_db" in metrics:
                values["thd_db"] = qty(metrics["thd_db"], "decibel", label="thd_db")
                values["thd_ratio"] = plain(metrics["thd_ratio"], label="thd_ratio")
            if "sfdr_dbc" in metrics:
                values["sfdr_dbc"] = qty(metrics["sfdr_dbc"], "decibel", label="sfdr_dbc")
            if "enob_bits" in metrics:
                values["enob_bits"] = plain(metrics["enob_bits"], label="enob_bits")
            values["fundamental_frequency"] = qty(metrics["fundamental"]["frequency_hz"], HZ, label="fundamental_frequency")
            values["fundamental_amplitude"] = plain(metrics["fundamental"]["amplitude"], label="fundamental_amplitude")
            if "snr_db" not in metrics:
                metrics["note"] = "no noise power found above the numerical floor: SNR, SINAD and ENOB are not reported (infinite for this record)"
            data["adc_metrics"] = metrics
        cols, decimated = _table(s, rows)
        table = handle.write_text("spectrum.csv", csv_text(["frequency_hz", "amplitude", "psd_unit2_per_hz"], cols), fmt="csv")
        data["table"] = {
            "file": "spectrum.csv", "rows": len(cols[0]), "bins": len(s.amp), "columns": ["frequency_hz", "amplitude", "psd_unit2_per_hz"],
            "decimated": decimated, "decimation": "max-hold in groups of whole bins; each row is the strongest bin of its group, listed at that bin's frequency" if decimated else None,
        }
        if decimated:
            warnings.append(f"the table holds {len(cols[0])} of {len(s.amp)} bins (max-hold decimation); raise table_rows for all of them")
        return Computed(values=values, primary="peak_amplitude", warnings=warnings, data=clean(data), artifacts=[table])

    return run("dsp_spectrum", inputs, compute)


__all__ = ["spectrum"]
