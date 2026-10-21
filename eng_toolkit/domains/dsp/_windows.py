"""Window functions: values, the cosine-sum coefficients, the textbook reference values and the numerical properties.

Cosine-sum windows ``w[m] = a0 - a1 cos(x) + a2 cos(2x) - a3 cos(3x) + a4 cos(4x)`` with ``x = 2 pi m / D``:
rectangular (a0 = 1), Hann (0.5, 0.5), Hamming (0.54, 0.46), Blackman (0.42, 0.5, 0.08) and the flat-top window of the
SRS / scipy family (0.21557895, 0.41663158, 0.277263158, 0.083578947, 0.006947368).  Kaiser is ``I0(beta sqrt(1 - u^2)) / I0(beta)``
with ``u = 2 m / D - 1``.  ``D = N - 1`` for the symmetric form (filter design) and ``D = N`` for the periodic (DFT-even) form
used in spectrum analysis; the periodic form is the one for which a coherent sine reads its exact amplitude.
"""

from __future__ import annotations

import math

import numpy as np

COSINE_SUM: dict[str, tuple[float, ...]] = {
    "rectangular": (1.0,),
    "hann": (0.5, 0.5),
    "hamming": (0.54, 0.46),
    "blackman": (0.42, 0.5, 0.08),
    "flattop": (0.21557895, 0.41663158, 0.277263158, 0.083578947, 0.006947368),
}
SPECTRAL_WINDOWS = ("rectangular", "hann", "hamming", "blackman", "flattop")
FIR_WINDOWS = ("rectangular", "hann", "hamming", "blackman", "kaiser")
ALL_WINDOWS = ("rectangular", "hann", "hamming", "blackman", "flattop", "kaiser")

# Main-lobe half width in bins of the periodic window (the support of its DFT is +-(len(a)-1) bins; the main lobe of the
# rectangular window is +-1 bin): used to decide which bins belong to a tone.
LOBE_HALF_WIDTH = {"rectangular": 1, "hann": 2, "hamming": 2, "blackman": 3, "flattop": 5, "kaiser": 3}


def window_values(name: str, n: int, *, periodic: bool = False, beta: float = 0.0) -> np.ndarray:
    """``n`` window samples.  The symmetric form is mirrored from its first half, so ``w == w[::-1]`` holds exactly."""
    if n < 1:
        raise ValueError("window length must be at least 1")
    if n == 1:
        return np.ones(1)
    d = n if periodic else n - 1
    half = (n + 1) // 2 if not periodic else n
    m = np.arange(half, dtype=float)
    if name == "kaiser":
        u = 2.0 * m / d - 1.0
        w = np.i0(beta * np.sqrt(np.maximum(0.0, 1.0 - u * u))) / np.i0(beta)
    elif name in COSINE_SUM:
        a = COSINE_SUM[name]
        x = 2.0 * math.pi * m / d
        w = np.zeros(half)
        for k, ak in enumerate(a):
            w = w + ((-1.0) ** k) * ak * np.cos(k * x)
    else:
        raise ValueError(f"unknown window {name!r}")
    if periodic:
        return w
    out = np.empty(n)
    out[:half] = w
    out[n - half:] = w[::-1]  # mirror
    return out


def sums(w: np.ndarray) -> tuple[float, float]:
    """(sum of w, sum of w squared)."""
    return float(np.sum(w)), float(np.sum(w * w))


# How far a finite-length window may differ from the long-window (N -> infinity) value and still be reported as agreeing.
REFERENCE_TOLERANCE = {"coherent_gain": 1e-6, "enbw_bins": 2e-3, "scalloping_loss_db": 2e-3, "highest_sidelobe_db": 0.15, "bandwidth_3db_bins": 3e-3}


def limit_response(name: str, delta: np.ndarray) -> np.ndarray:
    """The transform of a cosine-sum window of N -> infinity points, divided by N, at offsets ``delta`` (bins), centred (real):

    ``a0 sinc(d) + sum_k (a_k / 2) (sinc(d - k) + sinc(d + k))`` with ``sinc(x) = sin(pi x)/(pi x)``.  Exact closed form, independent of the FFT code.
    """
    a = COSINE_SUM[name]
    delta = np.asarray(delta, dtype=float)
    out = a[0] * np.sinc(delta)
    for k in range(1, len(a)):
        out = out + 0.5 * a[k] * (np.sinc(delta - k) + np.sinc(delta + k))
    return out


def limit_properties(name: str) -> dict[str, float]:
    """Coherent gain, ENBW, scalloping loss, 3 dB bandwidth and highest side lobe of a cosine-sum window of unlimited length (the textbook values)."""
    a = COSINE_SUM[name]
    cg = a[0]
    enbw = (a[0] ** 2 + sum(ak * ak for ak in a[1:]) / 2.0) / a[0] ** 2
    w0 = float(limit_response(name, np.array([0.0]))[0])
    scallop = -20.0 * math.log10(abs(float(limit_response(name, np.array([0.5]))[0])) / w0)
    lo, hi = 0.0, 0.5
    while abs(float(limit_response(name, np.array([hi]))[0])) / w0 > 1.0 / math.sqrt(2.0):
        lo, hi = hi, hi + 0.5
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if abs(float(limit_response(name, np.array([mid]))[0])) / w0 > 1.0 / math.sqrt(2.0):
            lo = mid
        else:
            hi = mid
    props = {"coherent_gain": cg, "enbw_bins": enbw, "scalloping_loss_db": scallop, "bandwidth_3db_bins": 2.0 * 0.5 * (lo + hi)}
    grid = np.arange(0.0, 40.0, 0.01)
    mag = np.abs(limit_response(name, grid)) / w0
    d = np.diff(mag)
    minima = np.nonzero((d[:-1] < 0) & (d[1:] >= 0))[0] + 1
    minima = minima[mag[minima] < 0.2]
    if minima.size:
        start = int(minima[0])
        peaks = np.nonzero((d[start:-1] > 0) & (d[start + 1:] <= 0))[0] + start + 1
        best = 0.0
        for p in peaks[:300]:
            _, val = _golden_max(lambda x: abs(float(limit_response(name, np.array([x]))[0])) / w0, float(grid[p - 1]), float(grid[p + 1]), 50)
            best = max(best, val)
        if best > 0.0:
            props["highest_sidelobe_db"] = 20.0 * math.log10(best)
    return props


def dtft(w: np.ndarray, offsets_bins: np.ndarray) -> np.ndarray:
    """The DTFT of ``w`` at frequencies ``offsets_bins`` (in units of fs / N, N = len(w)): sum w[n] exp(-j 2 pi f n / N), centred on the
    window midpoint so the value of a symmetric window is real (the magnitude does not depend on the centring)."""
    n = len(w)
    idx = np.arange(n, dtype=float) - (n - 1) / 2.0
    out = np.empty(len(offsets_bins), dtype=complex)
    step = max(1, int(2_000_000 // n))
    for start in range(0, len(offsets_bins), step):
        f = np.asarray(offsets_bins[start:start + step], dtype=float)
        out[start:start + step] = np.exp(-2j * math.pi * np.outer(f, idx) / n) @ w
    return out


def _magnitude(w: np.ndarray, f: float) -> float:
    return float(abs(dtft(w, np.array([f]))[0]))


def _golden_max(fn, lo: float, hi: float, iterations: int = 60) -> tuple[float, float]:
    g = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = lo, hi
    c, d = b - g * (b - a), a + g * (b - a)
    fc, fd = fn(c), fn(d)
    for _ in range(iterations):
        if fc > fd:
            b, d, fd = d, c, fc
            c = b - g * (b - a)
            fc = fn(c)
        else:
            a, c, fc = c, d, fd
            d = a + g * (b - a)
            fd = fn(d)
    x = (a + b) / 2.0
    return x, fn(x)


def properties(w: np.ndarray) -> dict[str, float]:
    """Numerical properties of a window: coherent gain, ENBW, scalloping loss, highest side-lobe level and 3 dB bandwidth.

    The spectrum is sampled by a zero-padded FFT (K points per bin); the side lobes and the -3 dB crossing are then refined by
    evaluating the DTFT at arbitrary frequencies (golden-section search and bisection), so the results do not depend on the grid.
    """
    n = len(w)
    s1, s2 = sums(w)
    peak = abs(s1)
    k = int(max(8, min(64, (1 << 21) // n)))
    spec = np.abs(np.fft.rfft(w, n * k)) / peak
    half_bins = n // 2
    grid = spec[: half_bins * k + 1]
    bins = np.arange(len(grid)) / k
    # 3 dB bandwidth: first crossing of 1/sqrt(2), refined by bisection on the DTFT
    below = np.nonzero(grid < 1.0 / math.sqrt(2.0))[0]
    if below.size == 0:  # pragma: no cover - a window narrower than the grid
        bw3 = float("nan")
    else:
        j = int(below[0])
        lo, hi = float(bins[j - 1]), float(bins[j])
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if _magnitude(w, mid) / peak > 1.0 / math.sqrt(2.0):
                lo = mid
            else:
                hi = mid
        bw3 = 2.0 * 0.5 * (lo + hi)
    # first minimum of the magnitude = end of the main lobe; side lobes are the local maxima beyond it
    d = np.diff(grid)
    minima = np.nonzero((d[:-1] < 0) & (d[1:] >= 0))[0] + 1
    minima = minima[grid[minima] < 0.2]  # a dip that is not yet near zero (the ripple inside a flat-top main lobe) is no null
    sidelobe_db = None
    first_null = None
    if minima.size:
        first_null = float(bins[int(minima[0])])
        rest = grid[int(minima[0]):]
        rest_bins = bins[int(minima[0]):]
        dr = np.diff(rest)
        peaks = np.nonzero((dr[:-1] > 0) & (dr[1:] <= 0))[0] + 1
        best = 0.0
        for p in peaks[:400]:
            if rest[p] < best * 0.9:  # cheap pruning: only refine candidates near the best so far
                continue
            x, val = _golden_max(lambda f: _magnitude(w, f) / peak, float(rest_bins[p - 1]), float(rest_bins[p + 1]), 40)
            best = max(best, val)
        if best > 0.0:
            sidelobe_db = 20.0 * math.log10(best)
    scallop = _magnitude(w, 0.5) / peak
    return {
        "coherent_gain": s1 / n,
        "enbw_bins": n * s2 / (s1 * s1),
        "scalloping_loss_db": -20.0 * math.log10(scallop),
        "bandwidth_3db_bins": bw3,
        **({"highest_sidelobe_db": sidelobe_db} if sidelobe_db is not None else {}),
        **({"main_lobe_first_null_bins": first_null} if first_null is not None else {}),
        "fft_points_per_bin": float(k),
    }


def kaiser_beta(attenuation_db: float) -> float:
    """Kaiser's beta for a stopband attenuation A in dB: 0.1102 (A - 8.7) for A > 50; 0.5842 (A - 21)^0.4 + 0.07886 (A - 21) for 21 <= A <= 50; 0 below 21."""
    a = attenuation_db
    if a > 50.0:
        return 0.1102 * (a - 8.7)
    if a >= 21.0:
        return 0.5842 * (a - 21.0) ** 0.4 + 0.07886 * (a - 21.0)
    return 0.0


def kaiser_attenuation(beta: float) -> float:
    """The attenuation in dB that Kaiser's formula maps to ``beta`` (the inverse of :func:`kaiser_beta`)."""
    if beta <= 0.0:
        return 21.0
    if beta > kaiser_beta(50.0):
        return beta / 0.1102 + 8.7
    lo, hi = 21.0, 50.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if kaiser_beta(mid) < beta:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def kaiser_order(attenuation_db: float, transition_rad: float) -> int:
    """Kaiser's filter order M = (A - 7.95) / (2.285 d_omega) rounded up (taps = M + 1); d_omega the transition width in rad/sample."""
    return max(1, math.ceil((attenuation_db - 7.95) / (2.285 * transition_rad) - 1e-9))


__all__ = [
    "ALL_WINDOWS", "COSINE_SUM", "FIR_WINDOWS", "LOBE_HALF_WIDTH", "REFERENCE_TOLERANCE", "SPECTRAL_WINDOWS", "dtft",
    "kaiser_attenuation", "kaiser_beta", "kaiser_order", "limit_properties", "limit_response", "properties", "sums", "window_values",
]
