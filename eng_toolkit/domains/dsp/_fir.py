"""FIR design by the window method, with the measured ripple, attenuation and linear-phase check.

The ideal impulse response of a low pass with cut-off ``wc`` (rad/sample, the -6 dB point of the result) delayed by ``alpha = (N-1)/2`` is
``(wc/pi) sinc(wc (n - alpha)/pi)``; high pass, band pass and band stop are sums and differences of low passes and the unit impulse.
The symmetric window is multiplied in and the taps are scaled so the gain is 1 at DC (low pass, band stop), at Nyquist (high pass) or at
the band centre (band pass).  Taps are built from the first half and mirrored, so the symmetry is exact.

Transition widths and orders (Oppenheim and Schafer, chapter 7): Kaiser ``beta`` from the attenuation A and order
``M = (A - 7.95)/(2.285 d_omega)``; for the fixed windows the table of approximate transition widths
``d_omega = c pi / M`` with c = 1.8 (rectangular), 6.2 (Hann), 6.6 (Hamming), 11 (Blackman) and peak approximation errors
-21, -44, -53, -74 dB.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from eng_toolkit.domains.dsp import _windows as win

# (transition width constant c in d_omega = c pi / M, attenuation the window can reach in dB)
WINDOW_TABLE = {"rectangular": (1.8, 21.0), "hann": (6.2, 44.0), "hamming": (6.6, 53.0), "blackman": (11.0, 74.0)}


def _lowpass(m: np.ndarray, wc: float) -> np.ndarray:
    return wc / math.pi * np.sinc(wc * m / math.pi)


def design(response: str, wc: list[float], taps: int, window: str, beta: float = 0.0) -> np.ndarray:
    """Taps of the windowed-sinc FIR; ``wc`` are the -6 dB points in rad/sample (one for lowpass/highpass, two for the band types)."""
    m = np.arange(taps, dtype=float) - (taps - 1) / 2.0
    w = win.window_values(window, taps, beta=beta)
    if response == "lowpass":
        ideal = _lowpass(m, wc[0])
    elif response == "highpass":
        ideal = (m == 0).astype(float) - _lowpass(m, wc[0])
    elif response == "bandpass":
        ideal = _lowpass(m, wc[1]) - _lowpass(m, wc[0])
    else:
        ideal = (m == 0).astype(float) + _lowpass(m, wc[0]) - _lowpass(m, wc[1])
    h = ideal * w
    w0 = {"lowpass": 0.0, "bandstop": 0.0, "highpass": math.pi, "bandpass": 0.5 * (wc[0] + wc[1]) if len(wc) == 2 else 0.0}[response]
    scale = float(np.sum(h * np.cos(w0 * m)))
    return h / scale


def amplitude_response(h: np.ndarray, n_fft: int) -> tuple[np.ndarray, np.ndarray]:
    """(angular frequencies 0..pi, |H|) on a zero-padded FFT grid of ``n_fft`` points."""
    spec = np.fft.rfft(h, n_fft)
    return np.arange(len(spec)) * 2.0 * math.pi / n_fft, np.abs(spec)


def linear_phase(h: np.ndarray) -> dict:
    """Symmetry type and the numerical check that H(w) exp(j w alpha) is real."""
    n = len(h)
    sym = bool(np.array_equal(h, h[::-1]))
    anti = bool(np.array_equal(h, -h[::-1]))
    kind = None
    if sym:
        kind = 1 if n % 2 == 1 else 2
    elif anti:
        kind = 3 if n % 2 == 1 else 4
    out: dict = {"symmetric": sym, "antisymmetric": anti, "linear_phase": sym or anti, "type": kind, "group_delay_samples": (n - 1) / 2.0}
    if sym or anti:
        w = np.linspace(0.0, math.pi, 2049)
        idx = np.arange(n) - (n - 1) / 2.0
        a = np.exp(1j * np.outer(w, idx)) @ h if n * len(w) < 2.5e7 else None
        if a is not None:
            part = np.imag(a) if sym else np.real(a)
            out["phase_residual"] = float(np.max(np.abs(part)) / max(float(np.max(np.abs(a))), 1e-300))
    out["zero_at_nyquist"] = bool(kind == 2 or kind == 3) if kind else None
    return out


@dataclass
class Plan:
    taps: int
    beta: float
    cutoffs_hz: list[float]
    transition_hz: float
    notes: list[str]


def plan_from_spec(
    family_window: str, response: str, pass_hz: list[float], stop_hz: list[float], fs: float, attenuation_db: float, beta_given: float
) -> Plan:
    """Taps, beta and -6 dB cut-offs from the pass/stop edges and the attenuation (see the module docstring for the formulas)."""
    notes: list[str] = []
    if response == "lowpass":
        ok = stop_hz[0] > pass_hz[0]
        trans = [stop_hz[0] - pass_hz[0]]
        cut = [0.5 * (pass_hz[0] + stop_hz[0])]
    elif response == "highpass":
        ok = stop_hz[0] < pass_hz[0]
        trans = [pass_hz[0] - stop_hz[0]]
        cut = [0.5 * (pass_hz[0] + stop_hz[0])]
    elif response == "bandpass":
        ok = stop_hz[0] < pass_hz[0] and stop_hz[1] > pass_hz[1]
        trans = [pass_hz[0] - stop_hz[0], stop_hz[1] - pass_hz[1]]
        cut = [0.5 * (pass_hz[0] + stop_hz[0]), 0.5 * (pass_hz[1] + stop_hz[1])]
    else:
        ok = stop_hz[0] > pass_hz[0] and stop_hz[1] < pass_hz[1]
        trans = [stop_hz[0] - pass_hz[0], pass_hz[1] - stop_hz[1]]
        cut = [0.5 * (pass_hz[0] + stop_hz[0]), 0.5 * (stop_hz[1] + pass_hz[1])]
    if not ok:
        raise ValueError(
            {"lowpass": "for a lowpass the stopband edge must be above the passband edge", "highpass": "for a highpass the stopband edge must be below the passband edge",
             "bandpass": "for a bandpass the stopband edges must lie outside the passband edges (stop_low < pass_low < pass_high < stop_high)",
             "bandstop": "for a bandstop the stopband edges must lie inside the passband edges (pass_low < stop_low < stop_high < pass_high)"}[response]
        )
    narrow = min(trans)
    if len(trans) == 2 and abs(trans[0] - trans[1]) > 1e-9 * max(trans):
        notes.append(f"the two transition bands differ ({trans[0]:g} Hz and {trans[1]:g} Hz); a windowed design has one transition width, so the narrower ({narrow:g} Hz) sets the order")
    d_omega = 2.0 * math.pi * narrow / fs
    beta = 0.0
    if family_window == "kaiser":
        beta = beta_given if beta_given > 0.0 else win.kaiser_beta(attenuation_db)
        a_eff = attenuation_db if attenuation_db > 0.0 else win.kaiser_attenuation(beta)
        taps = win.kaiser_order(a_eff, d_omega) + 1
    else:
        c, reach = WINDOW_TABLE[family_window]
        taps = math.ceil(c * math.pi / d_omega - 1e-9) + 1
        if attenuation_db > reach + 1e-9:
            notes.append(f"the {family_window} window reaches about {reach:g} dB of stopband attenuation, less than the {attenuation_db:g} dB asked for; use a Blackman or Kaiser window")
    taps = max(taps, 3)
    if response in ("highpass", "bandstop") and taps % 2 == 0:
        taps += 1
        notes.append("the number of taps was raised by one to make it odd: a highpass or bandstop FIR needs odd length (an even-length symmetric filter has a zero at Nyquist)")
    return Plan(taps, beta, cut, narrow, notes)


def estimated_transition_hz(window: str, taps: int, beta: float, fs: float) -> float:
    """Transition width (Hz) of a window design with ``taps`` taps from the same tables (used to place the measurement bands when only the cut-off is given)."""
    m = max(taps - 1, 1)
    if window == "kaiser":
        d_omega = (win.kaiser_attenuation(beta) - 7.95) / (2.285 * m)
    else:
        d_omega = WINDOW_TABLE[window][0] * math.pi / m
    return d_omega * fs / (2.0 * math.pi)


def measure(h: np.ndarray, response: str, fs: float, pass_bands: list[tuple[float, float]], stop_bands: list[tuple[float, float]]) -> dict:
    """Passband ripple and stopband attenuation from the dense amplitude response; bands are (low, high) in Hz."""
    n_fft = int(max(32768, 32 * len(h)))
    w, mag = amplitude_response(h, n_fft)
    f = w * fs / (2.0 * math.pi)

    def select(bands):
        mask = np.zeros(len(f), dtype=bool)
        for lo, hi in bands:
            mask |= (f >= lo) & (f <= hi)
        return mask

    pm, sm = select(pass_bands), select(stop_bands)
    out: dict = {"grid_points": len(f)}
    if pm.any():
        top, bottom = float(mag[pm].max()), float(mag[pm].min())
        out["passband_max_gain_db"] = 20.0 * math.log10(max(top, 1e-15))
        out["passband_min_gain_db"] = 20.0 * math.log10(max(bottom, 1e-15))
        out["passband_ripple_peak_to_peak_db"] = 20.0 * math.log10(max(top, 1e-15) / max(bottom, 1e-15))
        out["passband_max_deviation_db"] = float(np.max(np.abs(20.0 * np.log10(np.maximum(mag[pm], 1e-15)))))
    if sm.any():
        out["stopband_peak_gain_db"] = 20.0 * math.log10(max(float(mag[sm].max()), 1e-15))
        out["stopband_attenuation_db"] = -out["stopband_peak_gain_db"]
    return out


__all__ = ["Plan", "WINDOW_TABLE", "amplitude_response", "design", "estimated_transition_hz", "linear_phase", "measure", "plan_from_spec"]
