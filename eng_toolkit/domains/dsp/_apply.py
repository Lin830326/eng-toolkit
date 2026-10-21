"""Filtering of a sampled record: direct form II transposed second-order sections, direct-form (b, a), FIR by convolution, zero-phase.

The recursion of one section (and the general case) is the transposed direct form II used by ``scipy.signal.lfilter``:

    y[n] = b0 x[n] + z0;  z0 = z1 + b1 x[n] - a1 y[n];  z1 = b2 x[n] - a2 y[n]

Zero-phase filtering runs the filter forward, then backward over the reversed output, on a record extended at both ends by an odd
reflection about the end points (``2 x[0] - x[padlen..1]`` before, ``2 x[-1] - x[-2..-padlen-1]`` after) with the initial state of the steady
response to a constant equal to the first sample, so the edges start without a transient.  The extension is cut off again.
"""

from __future__ import annotations

import math

import numpy as np

WORK_LIMIT = 40_000_000  # recursion steps (samples x sections, or samples x order / 2): about 20 s in pure Python


def normalise(b: np.ndarray, a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return b / a[0], a / a[0]


def lfilter_zi(b: np.ndarray, a: np.ndarray) -> np.ndarray:
    """Initial state for a unit-step input to give a constant output (direct form II transposed); ``a[0] = 1``."""
    n = max(len(a), len(b))
    if n == 1:
        return np.zeros(0)
    a = np.concatenate([a, np.zeros(n - len(a))])
    b = np.concatenate([b, np.zeros(n - len(b))])
    if float(np.sum(a)) == 0.0:
        raise ValueError("a pole at z = 1: no steady state")
    if np.all(a[1:] == 0.0):  # FIR: z_k = sum of the taps after k
        return np.cumsum(b[::-1])[::-1][1:].copy()
    companion = np.zeros((n - 1, n - 1))
    companion[0, :] = -a[1:]
    companion[np.arange(1, n - 1), np.arange(0, n - 2)] = 1.0
    return np.linalg.solve(np.eye(n - 1) - companion.T, b[1:] - a[1:] * b[0])


def sos_zi(sos: np.ndarray) -> np.ndarray | None:
    zi = np.zeros((len(sos), 2))
    scale = 1.0
    for i, s in enumerate(sos):
        if float(np.sum(s[3:])) == 0.0:
            return None
        zi[i] = scale * lfilter_zi(s[:3], s[3:])
        scale *= float(np.sum(s[:3]) / np.sum(s[3:]))
    return zi


def _section(row: np.ndarray, x: np.ndarray, z0: float, z1: float) -> tuple[np.ndarray, float, float]:
    b0, b1, b2, _, a1, a2 = (float(v) for v in row)
    out = []
    push = out.append
    for xn in x.tolist():
        yn = b0 * xn + z0
        z0 = b1 * xn - a1 * yn + z1
        z1 = b2 * xn - a2 * yn
        push(yn)
    return np.array(out), z0, z1


def sosfilt(sos: np.ndarray, x: np.ndarray, zi: np.ndarray | None = None) -> np.ndarray:
    y = x
    for i, row in enumerate(sos):
        z0, z1 = (0.0, 0.0) if zi is None else (float(zi[i, 0]), float(zi[i, 1]))
        y, _, _ = _section(row, y, z0, z1)
        if not np.all(np.isfinite(y)):
            raise OverflowError(f"the output overflowed in section {i + 1} (the filter is unstable or the gain is huge)")
    return y


def lfilter_direct(b: np.ndarray, a: np.ndarray, x: np.ndarray, zi: np.ndarray | None = None) -> np.ndarray:
    """Direct form II transposed of any order (``a[0] = 1``); FIR (a = [1]) is a convolution."""
    if len(a) == 1:
        h = b
        if zi is None:
            return convolve_causal(x, h)
        # steady-state start for a constant: the same as a record that started with zi/..: handled by the caller through padding
        raise ValueError("FIR with initial state is done by padding")
    m = max(len(a), len(b)) - 1
    bb = np.concatenate([b, np.zeros(m + 1 - len(b))]).tolist()
    aa = np.concatenate([a, np.zeros(m + 1 - len(a))]).tolist()
    z = [0.0] * m if zi is None else [float(v) for v in zi]
    out = []
    b0 = bb[0]
    for xn in x.tolist():
        yn = b0 * xn + z[0]
        for i in range(m - 1):
            z[i] = z[i + 1] + bb[i + 1] * xn - aa[i + 1] * yn
        z[m - 1] = bb[m] * xn - aa[m] * yn
        out.append(yn)
    y = np.array(out)
    if not np.all(np.isfinite(y)):
        raise OverflowError("the output overflowed (the filter is unstable or the gain is huge)")
    return y


def convolve_causal(x: np.ndarray, h: np.ndarray, length: int | None = None) -> np.ndarray:
    """First ``length`` (default len(x)) samples of the full convolution; FFT when the direct product would be large."""
    n_out = len(x) if length is None else length
    full = len(x) + len(h) - 1
    if len(x) * len(h) <= 2_000_000:
        y = np.convolve(x, h)
    else:
        size = 1 << (full - 1).bit_length()
        y = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(h, size), size)[:full]
    return y[:n_out]


def padlen_for(sos: np.ndarray | None, b: np.ndarray | None, a: np.ndarray | None) -> int:
    if sos is not None:
        zeros_b2 = int(np.sum(sos[:, 2] == 0.0))
        zeros_a2 = int(np.sum(sos[:, 5] == 0.0))
        return 3 * (2 * len(sos) + 1 - min(zeros_b2, zeros_a2))
    return 3 * max(len(a), len(b))


def odd_extension(x: np.ndarray, pad: int) -> np.ndarray:
    left = 2.0 * x[0] - x[pad:0:-1]
    right = 2.0 * x[-1] - x[-2:-pad - 2:-1]
    return np.concatenate([left, x, right])


def filter_signal(x: np.ndarray, sos: np.ndarray | None, b: np.ndarray | None, a: np.ndarray | None) -> np.ndarray:
    """Causal filtering from a zero state."""
    if sos is not None:
        return sosfilt(sos, x)
    return lfilter_direct(b, a, x)


def filtfilt(x: np.ndarray, sos: np.ndarray | None, b: np.ndarray | None, a: np.ndarray | None) -> tuple[np.ndarray, int, bool]:
    """Zero-phase filtering with odd extension and steady-state start; returns (output, padlen, steady_state_used)."""
    pad = padlen_for(sos, b, a)
    if len(x) <= pad:
        raise ValueError(f"zero-phase filtering needs more than {pad} samples for this filter (the odd extension is {pad} samples long); got {len(x)}")
    ext = odd_extension(x, pad)
    steady = True

    def one_pass(sig: np.ndarray) -> np.ndarray:
        nonlocal steady
        first = float(sig[0])
        if sos is not None:
            zi = sos_zi(sos)
            if zi is None:
                steady = False
                return sosfilt(sos, sig)
            return sosfilt(sos, sig, zi * first)
        if len(a) == 1:  # FIR: a constant history equal to the first sample
            hist = np.full(len(b) - 1, first)
            return convolve_causal(np.concatenate([hist, sig]), b, len(sig) + len(b) - 1)[len(b) - 1:][:len(sig)]
        try:
            zi = lfilter_zi(b, a)
        except ValueError:
            steady = False
            return lfilter_direct(b, a, sig)
        return lfilter_direct(b, a, sig, zi * first)

    forward = one_pass(ext)
    backward = one_pass(forward[::-1])[::-1]
    return backward[pad:len(backward) - pad], pad, steady


def stability(sos: np.ndarray | None, b: np.ndarray | None, a: np.ndarray | None) -> dict:
    """Largest pole radius from the roots of the denominators (sections: each quadratic separately) and the verdict."""
    radius = 0.0
    if sos is not None:
        for row in sos:
            a1, a2 = float(row[4]), float(row[5])
            r = np.roots([1.0, a1, a2]) if a2 != 0.0 else (np.array([-a1]) if a1 != 0.0 else np.zeros(0))
            if r.size:
                radius = max(radius, float(np.max(np.abs(r))))
    elif len(a) > 1:
        r = np.roots(a)
        if r.size:
            radius = float(np.max(np.abs(r)))
    verdict = "stable" if radius < 1.0 - 1e-12 else ("marginal" if radius <= 1.0 + 1e-12 else "unstable")
    return {"max_pole_radius": radius, "verdict": verdict, "stable": verdict == "stable"}


def response_at(sos: np.ndarray | None, b: np.ndarray | None, a: np.ndarray | None, w: np.ndarray) -> np.ndarray:
    from eng_toolkit.domains.dsp import _iir as iir

    return iir.sos_response(sos, w) if sos is not None else iir.ba_response(b, a, w)


def stats(x: np.ndarray) -> dict:
    return {
        "mean": float(np.mean(x)), "rms": float(math.sqrt(float(np.mean(x * x)))), "min": float(np.min(x)), "max": float(np.max(x)),
        "peak": float(np.max(np.abs(x))), "peak_to_peak": float(np.max(x) - np.min(x)), "first": float(x[0]), "last": float(x[-1]),
    }


__all__ = [
    "WORK_LIMIT", "convolve_causal", "filter_signal", "filtfilt", "lfilter_direct", "lfilter_zi", "normalise", "odd_extension", "padlen_for",
    "response_at", "sos_zi", "sosfilt", "stability", "stats",
]
