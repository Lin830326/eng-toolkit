"""One-sided spectra by FFT (single record or Welch averaging), peak picking and single-tone converter metrics.

Scaling (``y = w x`` the windowed segment of length L, ``X = rfft(y)``, ``S1 = sum w``, ``S2 = sum w^2``, ``c_k = 2`` for the bins that
have a mirror image and ``1`` for DC and, when L is even, Nyquist):

* amplitude ``a_k = c_k |X_k| / S1``: a sine of amplitude A exactly on bin k reads A (this IS the coherent-gain correction: the
  window's coherent gain is ``S1/L``, and dividing by ``S1`` instead of ``L`` undoes it);
* power ``P_k = c_k |X_k|^2 / (L S2)``: ``sum_k P_k = sum y^2 / S2`` (Parseval), for a rectangular window the mean square of x;
* power spectral density ``c_k |X_k|^2 / (fs S2)`` in unit^2/Hz, so ``sum PSD_k (fs/L) = sum_k P_k``.

With Welch averaging ``|X_k|^2`` is the average over the segments (the amplitude is the square root of that average).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from eng_toolkit.domains.dsp import _windows as win

CHUNK_ELEMENTS = 1 << 21


@dataclass
class Spectrum:
    freqs: np.ndarray
    amp: np.ndarray
    psd: np.ndarray
    power: np.ndarray
    seg_len: int
    n_seg: int
    hop: int
    fs: float
    sum_w: float
    sum_w2: float
    window: str

    @property
    def df(self) -> float:
        return self.fs / self.seg_len

    @property
    def coherent_gain(self) -> float:
        return self.sum_w / self.seg_len

    @property
    def enbw_bins(self) -> float:
        return self.seg_len * self.sum_w2 / (self.sum_w * self.sum_w)


def spectrum(x: np.ndarray, fs: float, window: str, seg_len: int, noverlap: int, remove_mean: bool) -> Spectrum:
    n = len(x)
    w = win.window_values(window, seg_len, periodic=True)
    s1, s2 = win.sums(w)
    hop = seg_len - noverlap
    n_seg = (n - noverlap) // hop
    view = np.lib.stride_tricks.sliding_window_view(x, seg_len)[::hop][:n_seg]
    nb = seg_len // 2 + 1
    acc = np.zeros(nb)
    rows = max(1, CHUNK_ELEMENTS // seg_len)
    for start in range(0, n_seg, rows):
        seg = np.array(view[start:start + rows], dtype=float)
        if remove_mean:
            seg -= seg.mean(axis=1, keepdims=True)
        spec = np.fft.rfft(seg * w, axis=1)
        acc += np.sum(spec.real ** 2 + spec.imag ** 2, axis=0)
    mean_sq = acc / n_seg
    c = np.full(nb, 2.0)
    c[0] = 1.0
    if seg_len % 2 == 0:
        c[-1] = 1.0
    amp = c * np.sqrt(mean_sq) / s1
    power = c * mean_sq / (seg_len * s2)
    psd = c * mean_sq / (fs * s2)
    return Spectrum(np.arange(nb) * fs / seg_len, amp, psd, power, seg_len, n_seg, hop, fs, s1, s2, window)


def parabolic(values: np.ndarray, k: int) -> tuple[float, float]:
    """Offset (bins, within +-0.5) and value of the parabola through ln(values) at k-1, k, k+1 (needs 1 <= k <= len-2)."""
    if min(float(values[k - 1]), float(values[k + 1])) < 1e-9 * float(values[k]):
        return 0.0, float(values[k])  # a line sharper than rounding noise (an on-bin tone, rectangular window): the log-parabola would only amplify noise
    a, b, c = (math.log(max(float(values[i]), 1e-300)) for i in (k - 1, k, k + 1))
    denom = a - 2.0 * b + c
    if denom >= 0.0:
        return 0.0, float(values[k])
    delta = 0.5 * (a - c) / denom
    delta = max(-0.5, min(0.5, delta))
    return delta, math.exp(b - 0.25 * (a - c) * delta)


def find_peaks(sp: Spectrum, limit: int, threshold_db: float) -> list[dict]:
    amp = sp.amp
    if len(amp) < 3:
        return []
    floor = float(amp.max()) * 10.0 ** (-threshold_db / 20.0)
    mid = amp[1:-1]
    idx = np.nonzero((mid > amp[:-2]) & (mid >= amp[2:]) & (mid >= floor) & (mid > 0.0))[0] + 1
    out = []
    for k in idx:
        delta, value = parabolic(amp, int(k))
        out.append(
            {
                "frequency_hz": (int(k) + delta) * sp.df, "amplitude": value, "bin": int(k), "bin_frequency_hz": float(sp.freqs[k]),
                "bin_amplitude": float(amp[k]), "offset_bins": delta,
            }
        )
    if amp[0] > amp[1] and amp[0] >= floor and amp[0] > 0.0:  # a DC component
        out.append({"frequency_hz": 0.0, "amplitude": float(amp[0]), "bin": 0, "bin_frequency_hz": 0.0, "bin_amplitude": float(amp[0]), "offset_bins": 0.0})
    out.sort(key=lambda p: -p["amplitude"])
    return out[:limit]


def adc_metrics(sp: Spectrum, harmonics: int, hw: int, full_scale: float) -> tuple[dict, list[str]]:
    """SNR, SINAD, THD, SFDR and ENOB of a single-tone record from the power spectrum; definitions are returned with the numbers."""
    warnings: list[str] = []
    amp, power, nb, length = sp.amp, sp.power, len(sp.amp), sp.seg_len
    if nb < 4 * (hw + 1):
        raise ValueError("the record is too short for converter metrics with this window (fewer than about 8 bins per lobe)")
    low = hw + 1
    k0 = int(low + np.argmax(amp[low:]))
    if k0 == nb - 1 or k0 == 0:
        delta = 0.0
    else:
        delta, _ = parabolic(amp, k0)
    f0 = (k0 + delta) * sp.df
    lobe = lambda k: range(max(0, k - hw), min(nb - 1, k + hw) + 1)  # noqa: E731
    taken = np.zeros(nb, dtype=bool)
    dc = np.zeros(nb, dtype=bool)
    dc[: hw + 1] = True
    fund = np.zeros(nb, dtype=bool)
    fund[list(lobe(k0))] = True
    if (fund & dc).any():
        warnings.append("the fundamental's lobe touches the DC lobe: the tone is too close to 0 Hz for this window")
    taken |= dc | fund
    p_sig = float(power[fund].sum())
    harm_rows, harm_mask, skipped = [], np.zeros(nb, dtype=bool), []
    for h in range(2, harmonics + 1):
        pos = (h * f0 / sp.df) % length
        if pos > length / 2.0:
            pos = length - pos
        k_exp = int(round(pos))
        if k_exp < 0 or k_exp > nb - 1:
            skipped.append({"harmonic": h, "reason": "outside the spectrum"})
            continue
        lo, hi = max(0, k_exp - 1), min(nb - 1, k_exp + 1)
        k_h = int(lo + np.argmax(amp[lo:hi + 1]))
        mask = np.zeros(nb, dtype=bool)
        mask[list(lobe(k_h))] = True
        if (mask & taken).any():
            skipped.append({"harmonic": h, "reason": "its lobe overlaps the DC lobe, the fundamental or another harmonic (aliased onto it)"})
            continue
        taken |= mask
        harm_mask |= mask
        p_h = float(power[mask].sum())
        harm_rows.append({"harmonic": h, "frequency_hz": float(sp.freqs[k_h]), "bin": k_h, "amplitude": math.sqrt(2.0 * p_h), "relative_db": 10.0 * math.log10(max(p_h, 1e-300) / p_sig)})
    p_noise = float(power[~taken].sum())
    p_nd = float(power[~(dc | fund)].sum())
    p_harm = float(sum(r["amplitude"] ** 2 / 2.0 for r in harm_rows))
    a1 = math.sqrt(2.0 * p_sig)
    out: dict = {
        "fundamental": {"frequency_hz": f0, "bin": k0, "amplitude": a1, "power": p_sig, "lobe_bins": [int(np.argmax(fund)), int(nb - 1 - np.argmax(fund[::-1]))]},
        "signal_power": p_sig, "noise_power": p_noise, "noise_plus_distortion_power": p_nd, "harmonic_power": p_harm,
        "lobe_half_width_bins": hw, "harmonics": harm_rows, "harmonics_skipped": skipped,
        "excluded_bins": {"dc_lobe": int(dc.sum()), "fundamental_lobe": int(fund.sum()), "harmonic_lobes": int(harm_mask.sum()), "bins_in_noise": int((~taken).sum()), "total": nb},
    }
    if p_sig <= 0.0:
        raise ValueError("no signal power found")
    floor_ratio = 1e-30
    if p_noise > p_sig * floor_ratio:
        out["snr_db"] = 10.0 * math.log10(p_sig / p_noise)
    if p_nd > p_sig * floor_ratio:
        out["sinad_db"] = 10.0 * math.log10(p_sig / p_nd)
    if p_harm > p_sig * floor_ratio:
        out["thd_ratio"] = math.sqrt(p_harm / p_sig)
        out["thd_db"] = 20.0 * math.log10(out["thd_ratio"])
        out["thd_percent"] = 100.0 * out["thd_ratio"]
    spur_mask = ~(dc | fund)
    spur_bin = int(np.argmax(np.where(spur_mask, amp, -1.0)))
    spur = float(amp[spur_bin])
    if spur > a1 * 1e-15:
        out["sfdr_dbc"] = 20.0 * math.log10(a1 / spur)
        out["largest_spur"] = {"frequency_hz": float(sp.freqs[spur_bin]), "bin": spur_bin, "amplitude": spur}
    if "sinad_db" in out:
        level_db = 20.0 * math.log10(a1 / full_scale) if full_scale > 0.0 else 0.0
        out["signal_level_dbfs"] = level_db if full_scale > 0.0 else None
        out["enob_bits"] = (out["sinad_db"] - 1.76 - level_db) / 6.02
        if out["sinad_db"] > 200.0:
            warnings.append("SINAD above 200 dB: the record is numerically noise-free (synthetic); the figures are set by FFT rounding, not by a converter")
        if full_scale <= 0.0:
            warnings.append("no full_scale_amplitude given: ENOB assumes the tone is at full scale (a smaller tone makes ENOB read low by the back-off)")
        elif a1 > full_scale * (1.0 + 1e-9):
            warnings.append(f"the tone amplitude {a1:.6g} exceeds the full scale {full_scale:g}: check the full_scale_amplitude (a clipped converter record is not a clean tone)")
    k_est = f0 / sp.df
    off = abs(k_est - round(k_est))
    out["bin_offset_of_fundamental"] = off
    out["definitions"] = {
        "signal": "sum of the power spectrum over the fundamental's lobe (+-lobe_half_width_bins around the peak bin); sqrt(2 P) is the amplitude",
        "noise": "sum of the power over every bin outside the DC lobe, the fundamental's lobe and the lobes of harmonics 2..N (no extrapolation to the excluded bins)",
        "snr_db": "10 log10(signal / noise), harmonics excluded from the noise",
        "sinad_db": "10 log10(signal / (noise + all harmonics and spurs)): everything except the DC lobe and the fundamental's lobe",
        "thd": "sqrt(sum of harmonic powers 2..N) / fundamental amplitude, each harmonic a lobe at the folded frequency h f0 (aliased harmonics fold into 0..fs/2); thd_db = 20 log10(ratio)",
        "sfdr_dbc": "20 log10(fundamental amplitude / largest single-bin amplitude outside the DC and fundamental lobes), spurs included; amplitude of a bin read from the amplitude spectrum",
        "enob_bits": "(SINAD - 1.76 - level_dBFS) / 6.02 with level_dBFS = 20 log10(fundamental amplitude / full scale) (0 if no full scale is given)",
        "power_units": "mean-square of the samples (unit^2): sum over bins equals sum(w x)^2 / sum(w^2)",
    }
    return out, warnings


__all__ = ["Spectrum", "adc_metrics", "find_peaks", "parabolic", "spectrum"]
