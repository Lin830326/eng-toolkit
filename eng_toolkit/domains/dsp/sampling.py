"""``dsp_sampling``: aliasing, minimum sample rate, quantisation and integer-factor decimation and interpolation.

Definitions
* alias of a tone f sampled at fs: ``|f - fs round(f/fs)|``; the folding zones are the intervals ``[j fs/2, (j+1) fs/2]``; in an odd zone the
  baseband image runs backwards (the spectrum is inverted).
* minimum sample rate for a signal up to f_max with a guard band g: ``2 (f_max + g)``.  For a band [f_low, f_high] the bandpass-sampling
  ranges are ``2 f_high/n <= fs <= 2 f_low/(n-1)`` for n = 1 .. floor(f_high/(f_high - f_low)).
* quantisation of an N-bit converter with a full-scale sine: ``SNR = 6.02 N + 1.76 dB`` (rounded textbook constants; exact 6.0206 N + 1.7609);
  with a tone at L dBFS the SNR is lower by -L.  ENOB inverse: ``(SINAD - 1.76 - L)/6.02``.
* decimation by M: Kaiser-window low-pass FIR with its -6 dB point at the new Nyquist frequency, passband edge ``pf`` times the new Nyquist,
  stopband starting where the first alias band reaches the passband, ``fs_out - pf fs_out/2``; delay compensated, zero beyond the record.
  Interpolation by L: zero stuffing and the same kind of filter (gain L), cut-off at the old Nyquist frequency.
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import Annotated, Any

import numpy as np
from pydantic import Field

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.dsp import _apply as ap
from eng_toolkit.domains.dsp import _fir as fir
from eng_toolkit.domains.dsp import _windows as win
from eng_toolkit.domains.dsp._common import (
    MAX_SAMPLES,
    MAX_TAPS,
    Computed,
    JobHandle,
    QuantityOut,
    choice,
    clean,
    csv_text,
    is_blank,
    parse_frequency,
    plain,
    plain_number,
    qty,
    read_samples,
    record_text,
    resolve_rate,
    run,
    sample_rate_hz,
    whole_number,
)
from eng_toolkit.domains.dsp._server import server
from eng_toolkit.units import UnitsError

OPERATIONS = ("alias", "min_sample_rate", "quantization", "decimate", "interpolate")
HZ = "hertz"
SNR_SLOPE, SNR_OFFSET = 6.02, 1.76
EXACT_SLOPE, EXACT_OFFSET = 20.0 * math.log10(2.0), 10.0 * math.log10(1.5)


MAX_ALIAS_RATIO = 2.0 ** 40  # f/fs above this: one ulp of f is more than 1e-4 of fs, the alias would be noise


def alias_of(f: float, fs: float) -> tuple[float, int, bool]:
    """(alias frequency, folding zone, spectrum inverted) of a tone ``f`` Hz sampled at ``fs`` Hz, by exact rational arithmetic on the two floats.

    ``alias = |f - fs round(f/fs)|`` computed as the exact remainder of f modulo fs folded to [0, fs/2]; no cancellation between large products.
    """
    exact_f, exact_fs = Fraction(f), Fraction(fs)
    rem = exact_f % exact_fs
    alias = min(rem, exact_fs - rem)
    zone = int((exact_f / (exact_fs / 2)).__floor__())
    return float(alias), zone, bool(zone % 2 == 1)


def _need(raw: Any, name: str, op: str) -> None:
    if is_blank(raw):
        raise UnitsError("input.missing", f"operation '{op}' needs '{name}'", {"param": name})


def _bands_alias(fp: float, f_out: float, fs: float) -> list[tuple[float, float]]:
    bands, k = [], 1
    while k * f_out - fp < fs / 2.0:
        bands.append((k * f_out - fp, min(k * f_out + fp, fs / 2.0)))
        k += 1
    return bands


def _kaiser_lowpass(fs: float, cutoff_rad: float, atten: float, transition_rad: float) -> tuple[np.ndarray, float, int]:
    beta = win.kaiser_beta(atten)
    taps = win.kaiser_order(atten, transition_rad) + 1
    if taps % 2 == 0:
        taps += 1
    if taps > MAX_TAPS:
        raise UnitsError("input.out_of_range", f"the anti-alias filter needs {taps} taps, above the limit {MAX_TAPS}: lower the attenuation, widen the transition (lower passband_fraction) or use a smaller factor", {"param": "attenuation_db", "needed_taps": taps, "limit": MAX_TAPS})
    return fir.design("lowpass", [cutoff_rad], taps, "kaiser", beta), beta, taps


@server.tool(
    name="sampling",
    description=(
        "Sampling calculators. operation 'alias': alias frequency, folding zone and spectrum inversion of a tone (frequency, sample_rate). "
        "'min_sample_rate': 2 (max_frequency + guard_band), and with lowest_frequency the bandpass-sampling ranges. 'quantization': "
        "SNR = 6.02 N + 1.76 dB for bits N (input_level_dbfs for a smaller tone) and ENOB = (SINAD - 1.76)/6.02 for sinad_db. 'decimate' "
        "and 'interpolate': integer factor with a Kaiser-window anti-alias / anti-image FIR (attenuation_db, passband_fraction), samples "
        "as JSON or CSV file; returns the output record (CSV), the filter, and the measured attenuation of the alias or image bands. "
        "Frequencies and rates carry units ('1.3 kHz')."
    ),
)
def sampling(
    operation: Annotated[str, Field(description="alias, min_sample_rate, quantization, decimate or interpolate")],
    frequency: Annotated[str, Field(description="alias: tone frequency with unit, e.g. '1.3 kHz'")] = "",
    sample_rate: Annotated[str, Field(description="Sample rate with unit, e.g. '1 kHz' (alias, decimate, interpolate; optional with a time column)")] = "",
    max_frequency: Annotated[str, Field(description="min_sample_rate: highest frequency of interest, with unit")] = "",
    guard_band: Annotated[str, Field(description="min_sample_rate: guard band above max_frequency with unit (default '0 Hz')")] = "0 Hz",
    lowest_frequency: Annotated[str, Field(description="min_sample_rate: lower edge of a band signal (bandpass sampling); empty = baseband signal")] = "",
    bits: Annotated[int, Field(description="quantization: converter resolution N, 1..32 (0 = not given)")] = 0,
    input_level_dbfs: Annotated[float, Field(description="quantization: tone level in dBFS, 0 or negative (default 0 = full scale)")] = 0.0,
    sinad_db: Annotated[float, Field(description="quantization: measured SINAD in dB, to get the ENOB (0 = not given)")] = 0.0,
    factor: Annotated[int, Field(description="decimate/interpolate: integer factor, 2..1000")] = 0,
    samples: Annotated[str, Field(description="decimate/interpolate: samples as JSON text (at most 4194304)")] = "",
    samples_file: Annotated[str, Field(description="decimate/interpolate: CSV file, value or time,value")] = "",
    attenuation_db: Annotated[float, Field(description="decimate/interpolate: stopband attenuation of the FIR in dB, 21..140 (default 60)")] = 60.0,
    passband_fraction: Annotated[float, Field(description="decimate/interpolate: kept fraction of the Nyquist band, 0.1..0.95 (default 0.8)")] = 0.8,
) -> ToolResult:
    inputs = {
        "operation": operation, "frequency": frequency, "sample_rate": sample_rate, "max_frequency": max_frequency, "guard_band": guard_band,
        "lowest_frequency": lowest_frequency, "bits": bits, "input_level_dbfs": input_level_dbfs, "sinad_db": sinad_db, "factor": factor,
        "samples": record_text(samples), "samples_file": samples_file, "attenuation_db": attenuation_db, "passband_fraction": passband_fraction,
    }

    def compute(handle: JobHandle) -> Computed:
        op = choice(operation, name="operation", allowed=OPERATIONS)
        warnings: list[str] = []
        values: dict[str, QuantityOut] = {}
        data: dict[str, Any] = {"operation": op}
        artifacts = []
        if op == "alias":
            _need(frequency, "frequency", op)
            _need(sample_rate, "sample_rate", op)
            f = parse_frequency(frequency, name="frequency")
            fs = sample_rate_hz(sample_rate)
            if f < 0.0 or f > 1e15:
                raise UnitsError("input.invalid" if f < 0.0 else "input.out_of_range", f"'frequency' = {f:g} Hz must be between 0 and 1e15 Hz", {"param": "frequency"})
            if f / fs > MAX_ALIAS_RATIO:
                raise UnitsError(
                    "input.out_of_range",
                    f"the tone is {f / fs:.3g} sample rates up: a relative error of 1e-16 in the frequency is then more than 1e-4 of a sample rate, so the alias is not determined (limit {MAX_ALIAS_RATIO:.3g})",
                    {"param": "frequency", "limit_ratio": MAX_ALIAS_RATIO},
                )
            alias, zone, inverted = alias_of(f, fs)
            nyq = fs / 2.0
            aliased = f > nyq
            values.update({"alias_frequency": qty(alias, HZ, label="alias_frequency"), "nyquist_frequency": qty(nyq, HZ, label="nyquist_frequency"), "folding_zone": plain(float(zone), label="folding_zone")})
            images = sorted({round(abs(k * fs + s * alias), 9) for k in range(0, 4) for s in (-1, 1) if abs(k * fs + s * alias) >= 0.0})[:8]
            data.update(
                {
                    "tone_hz": f, "sample_rate_hz": fs, "aliased": aliased, "spectrum_inverted": inverted,
                    "formula": "alias = |f - fs round(f/fs)|; zone = floor(f/(fs/2)); odd zones are mirrored",
                    "folding_map": [
                        {"zone": j, "from_hz": j * nyq, "to_hz": (j + 1) * nyq, "maps_to": "f - j fs/2 (increasing)" if j % 2 == 0 else "(j+1) fs/2 - f (decreasing: inverted)", "baseband_start_hz": 0.0 if j % 2 == 0 else nyq}
                        for j in range(0, 4)
                    ],
                    "same_samples_as_tones_hz": images,
                    "same_samples_note": "tones at k fs +- alias produce identical samples (indistinguishable after sampling)",
                }
            )
            if aliased:
                warnings.append(f"{f:g} Hz is above the Nyquist frequency {nyq:g} Hz: it appears at {alias:g} Hz{' with the spectrum inverted' if inverted else ''}")
            if abs(f - nyq) < 1e-9 * fs:
                warnings.append("the tone is exactly at the Nyquist frequency: its amplitude and phase after sampling depend on its phase")
            return Computed(values=values, primary="alias_frequency", warnings=warnings, data=clean(data), artifacts=artifacts)
        if op == "min_sample_rate":
            _need(max_frequency, "max_frequency", op)
            fmax = parse_frequency(max_frequency, name="max_frequency")
            guard = parse_frequency(guard_band, name="guard_band") if not is_blank(guard_band) else 0.0
            if fmax <= 0.0 or guard < 0.0:
                raise UnitsError("input.invalid", "'max_frequency' must be positive and 'guard_band' not negative", {"param": "max_frequency"})
            if fmax > 1e11 or guard > 1e11:
                raise UnitsError("input.out_of_range", "frequencies above 1e11 Hz are not supported", {"param": "max_frequency"})
            fs_min = 2.0 * (fmax + guard)
            values["min_sample_rate"] = qty(fs_min, HZ, label="min_sample_rate")
            data.update({"max_frequency_hz": fmax, "guard_band_hz": guard, "formula": "fs >= 2 (f_max + guard_band); strictly above 2 f_max for any bandwidth to be recoverable", "nyquist_at_min_hz": fs_min / 2.0})
            if not is_blank(lowest_frequency):
                flow = parse_frequency(lowest_frequency, name="lowest_frequency")
                if not 0.0 < flow < fmax:
                    raise UnitsError("input.invalid", "'lowest_frequency' must be positive and below 'max_frequency'", {"param": "lowest_frequency"})
                hi, lo = fmax + guard, max(flow - guard, 1e-12 * fmax)
                n_max = int(math.floor(hi / (hi - lo)))
                ranges = [{"n": 1, "fs_min_hz": 2.0 * hi, "fs_max_hz": None}] + [
                    {"n": n, "fs_min_hz": 2.0 * hi / n, "fs_max_hz": 2.0 * lo / (n - 1)} for n in range(2, min(n_max, 12) + 1) if 2.0 * hi / n <= 2.0 * lo / (n - 1)
                ]
                lowest = 2.0 * hi / n_max
                if n_max >= 2 and 2.0 * hi / n_max <= 2.0 * lo / (n_max - 1):
                    values["min_bandpass_sample_rate"] = qty(lowest, HZ, label="min_bandpass_sample_rate")
                data["bandpass_sampling"] = {"ranges": ranges, "ranges_listed": len(ranges), "n_max": n_max, "formula": "2 f_high/n <= fs <= 2 f_low/(n-1), n = 1 .. floor(f_high/(f_high - f_low)); the band is widened by the guard band on both sides"}
                if n_max > 12:
                    warnings.append(f"n up to {n_max} is mathematically allowed but only the 12 highest-rate ranges are listed; very low rates leave almost no tolerance on the sample clock and the anti-alias filter")
                warnings.append("bandpass sampling folds the band into baseband: the anti-alias filter must be a band-pass filter that removes everything outside the band")
            return Computed(values=values, primary="min_sample_rate", warnings=warnings, data=clean(data), artifacts=artifacts)
        if op == "quantization":
            n_bits = whole_number(bits, name="bits", lo=0, hi=32)
            level = plain_number(input_level_dbfs, name="input_level_dbfs", lo=-400.0, hi=0.0)
            sinad = plain_number(sinad_db, name="sinad_db", lo=0.0, hi=400.0)
            if n_bits == 0 and sinad == 0.0:
                raise UnitsError("input.missing", "operation 'quantization' needs 'bits' (converter resolution) and/or 'sinad_db' (to get the ENOB)", {"param": "bits"})
            if n_bits:
                snr = SNR_SLOPE * n_bits + SNR_OFFSET + level
                exact = EXACT_SLOPE * n_bits + EXACT_OFFSET + level
                values["quantization_snr_db"] = qty(snr, "decibel", label="quantization_snr_db")
                values["quantization_snr_exact_db"] = qty(exact, "decibel", label="quantization_snr_exact_db")
                values["quantization_noise_rms_lsb"] = plain(1.0 / math.sqrt(12.0), label="quantization_noise_rms_lsb")
                data.update(
                    {
                        "bits": n_bits, "input_level_dbfs": level, "levels": 2 ** n_bits, "lsb_fraction_of_full_scale_range": 2.0 ** -n_bits,
                        "formula": "SNR = 6.02 N + 1.76 dB for a full-scale sine and uniform quantisation error (rounded textbook constants); the exact values are 20 log10(2) N + 10 log10(1.5) = 6.0206 N + 1.7609",
                        "difference_to_exact_db": snr - exact, "assumptions": "full-scale sine, error uniformly distributed over one LSB (rms LSB/sqrt(12)), no dither, no clipping, noise spread over 0..fs/2",
                    }
                )
                if level < -3.0:
                    warnings.append(f"the tone is {-level:g} dB below full scale: the SNR is lowered by the same amount")
            if sinad:
                level_for_enob = level
                enob = (sinad - SNR_OFFSET - level_for_enob) / SNR_SLOPE
                values["enob_bits"] = plain(enob, label="enob_bits")
                data["enob"] = {"sinad_db": sinad, "enob_bits": enob, "formula": "ENOB = (SINAD - 1.76 - level_dBFS)/6.02, the inverse of the SNR formula", "level_dbfs_used": level_for_enob}
            primary = "quantization_snr_db" if n_bits else "enob_bits"
            return Computed(values=values, primary=primary, warnings=warnings, data=clean(data), artifacts=artifacts)
        # decimate / interpolate
        m = whole_number(factor, name="factor", lo=0, hi=1000)
        if m < 2:
            raise UnitsError("input.missing" if m == 0 else "input.invalid", f"operation '{op}' needs an integer 'factor' of at least 2", {"param": "factor"})
        atten = plain_number(attenuation_db, name="attenuation_db", lo=21.0, hi=140.0)
        pf = plain_number(passband_fraction, name="passband_fraction", lo=0.1, hi=0.95)
        data_in = read_samples(handle, samples, samples_file, min_len=2)
        x = data_in.x
        n = len(x)
        fs = resolve_rate(data_in, sample_rate)
        if op == "decimate":
            f_out = fs / m
            fp = pf * f_out / 2.0
            transition = 2.0 * math.pi * (1.0 - pf) / m
            h, beta, taps = _kaiser_lowpass(fs, math.pi / m, atten, transition)
            delay = (taps - 1) // 2
            full = ap.convolve_causal(x, h, n + taps - 1)
            idx = np.arange(0, n, m)
            y = full[delay + idx]
            band = _bands_alias(fp, f_out, fs)
            meas = fir.measure(h, "lowpass", fs, [(0.0, fp)], band)
            n_out = len(y)
            values.update(
                {
                    "output_samples": plain(float(n_out), label="output_samples"), "output_rate": qty(f_out, HZ, label="output_rate"), "filter_taps": plain(float(taps), label="filter_taps"),
                    "alias_band_attenuation_db": qty(meas["stopband_attenuation_db"], "decibel", label="alias_band_attenuation_db"),
                    "passband_ripple_db": qty(meas["passband_ripple_peak_to_peak_db"], "decibel", label="passband_ripple_db"),
                }
            )
            cols = [np.arange(n_out, dtype=float), idx / fs + (data_in.t0 or 0.0), y]
            table = handle.write_text("decimated.csv", csv_text(["index", "time_s", "value"], cols), fmt="csv")
            data.update(
                {
                    "factor": m, "input_rate_hz": fs, "output_rate_hz": f_out, "input_samples": n, "output_samples": n_out, "kept_band_hz": [0.0, fp], "new_nyquist_hz": f_out / 2.0,
                    "filter": {"type": "Kaiser-window low-pass FIR", "taps": taps, "beta": beta, "attenuation_design_db": atten, "cutoff_minus_6db_hz": f_out / 2.0, "delay_samples_input_rate": delay, "dc_gain": float(np.sum(h)), "transition_hz": (1.0 - pf) * f_out},
                    "alias_bands_hz": [list(bnd) for bnd in band], "measurement": meas,
                    "alias_definition": "frequencies k fs_out +- (kept band) for k = 1 .. up to fs/2 fold into the kept band 0 .. passband_fraction x new Nyquist",
                    "edge_handling": f"delay of {delay} input samples removed; the record is taken as zero outside, so the first and last about {delay // m + 1} output samples are affected",
                    "table": {"file": "decimated.csv", "rows": n_out, "columns": ["index", "time_s", "value"]},
                }
            )
            if f_out < 2.0 * 1e-3:
                warnings.append("the output rate is very low")
            if n_out < 8:
                warnings.append(f"only {n_out} output samples")
            if atten > 100:
                warnings.append("an attenuation above 100 dB is limited by the double-precision word length and the noise of the input, not only by the filter")
            return Computed(values=values, primary="output_samples", warnings=warnings, data=clean(data), artifacts=[table])
        # interpolate
        if n * m > MAX_SAMPLES:
            raise UnitsError("input.out_of_range", f"{n} samples x factor {m} = {n * m} output samples, above the limit {MAX_SAMPLES}", {"param": "factor", "limit_samples": MAX_SAMPLES})
        fs_new = fs * m
        fp = pf * fs / 2.0
        transition = 2.0 * math.pi * (1.0 - pf) / m
        h, beta, taps = _kaiser_lowpass(fs_new, math.pi / m, atten, transition)
        h = h * m
        delay = (taps - 1) // 2
        up = np.zeros(n * m)
        up[::m] = x
        full = ap.convolve_causal(up, h, n * m + taps - 1)
        y = full[delay:delay + n * m]
        bands = []
        k = 1
        while k * fs - fp < fs_new / 2.0:
            bands.append((k * fs - fp, min(k * fs + fp, fs_new / 2.0)))
            k += 1
        meas = fir.measure(h / m, "lowpass", fs_new, [(0.0, fp)], bands)
        n_out = len(y)
        values.update(
            {
                "output_samples": plain(float(n_out), label="output_samples"), "output_rate": qty(fs_new, HZ, label="output_rate"), "filter_taps": plain(float(taps), label="filter_taps"),
                "image_band_attenuation_db": qty(meas["stopband_attenuation_db"], "decibel", label="image_band_attenuation_db"),
                "passband_ripple_db": qty(meas["passband_ripple_peak_to_peak_db"], "decibel", label="passband_ripple_db"),
            }
        )
        cols = [np.arange(n_out, dtype=float), np.arange(n_out) / fs_new + (data_in.t0 or 0.0), y]
        table = handle.write_text("interpolated.csv", csv_text(["index", "time_s", "value"], cols), fmt="csv")
        data.update(
            {
                "factor": m, "input_rate_hz": fs, "output_rate_hz": fs_new, "input_samples": n, "output_samples": n_out, "kept_band_hz": [0.0, fp],
                "filter": {"type": "Kaiser-window low-pass FIR on the zero-stuffed record, gain = factor", "taps": taps, "beta": beta, "attenuation_design_db": atten, "cutoff_minus_6db_hz": fs / 2.0, "delay_samples_output_rate": delay, "dc_gain": float(np.sum(h)), "transition_hz": (1.0 - pf) * fs},
                "image_bands_hz": [list(bnd) for bnd in bands], "measurement": meas,
                "image_definition": "images of the kept band at k fs +- (kept band), k >= 1, up to the new Nyquist frequency",
                "edge_handling": f"delay of {delay} output samples removed; the record is taken as zero outside, so about {delay // m + 1} input samples at each end are affected by the missing data",
                "table": {"file": "interpolated.csv", "rows": n_out, "columns": ["index", "time_s", "value"]},
            }
        )
        return Computed(values=values, primary="output_samples", warnings=warnings, data=clean(data), artifacts=[table])

    return run("dsp_sampling", inputs, compute)


__all__ = ["alias_of", "sampling"]
