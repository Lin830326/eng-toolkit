"""IIR design: analogue prototype, frequency transformation, bilinear transform with prewarping, second-order sections.

All of it is closed form (no iteration), written from the textbook definitions:

* Butterworth ``|H(jw)|^2 = 1/(1 + w^(2n))``: poles ``exp(j pi (2k + n - 1)/(2n))``, left half plane, on the unit circle.
* Chebyshev I ``|H|^2 = 1/(1 + eps^2 T_n(w)^2)``, ``eps^2 = 10^(Rp/10) - 1``: poles ``-sinh(mu) sin(t_k) + j cosh(mu) cos(t_k)`` with
  ``t_k = (2k - 1) pi/(2n)`` and ``mu = asinh(1/eps)/n``; for even n the DC gain is ``1/sqrt(1 + eps^2)``.
* Chebyshev II ``|H|^2 = eps^2 T_n(1/w)^2 / (1 + eps^2 T_n(1/w)^2)``, ``eps^2 = 1/(10^(Rs/10) - 1)``: poles are the reciprocals of the
  Chebyshev I poles built with ``mu = asinh(1/eps)/n``, zeros ``j/cos(t_k)``; the stopband edge is at w = 1 where the attenuation is Rs.
* Frequency transformations ``s -> s/wo`` (low pass), ``wo/s`` (high pass), ``(s^2 + wo^2)/(bw s)`` (band pass), ``bw s/(s^2 + wo^2)``
  (band stop) applied to the roots, then ``z = (1 + s)/(1 - s)`` (bilinear, with analogue frequency ``tan(pi f/fs)`` = the prewarping).

Roots are kept as conjugate pairs plus real roots so the polynomials and sections are exactly real.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

IIR_FAMILIES = ("butterworth", "chebyshev1", "chebyshev2")
RESPONSES = ("lowpass", "highpass", "bandpass", "bandstop")
ROOT_TOL = 1e-9


# ----------------------------------------------------------------------------------------------- root bookkeeping


def split_roots(r: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(upper-half-plane representatives of the conjugate pairs, real roots) of a conjugate-symmetric root set."""
    r = np.asarray(r, dtype=complex)
    if r.size == 0:
        return np.zeros(0, dtype=complex), np.zeros(0)
    near_real = np.abs(r.imag) <= ROOT_TOL * np.maximum(1.0, np.abs(r))
    upper = r[(r.imag > 0) & ~near_real]
    lower = int(np.sum((r.imag < 0) & ~near_real))
    if len(upper) != lower:  # pragma: no cover - internal consistency
        raise RuntimeError("root set is not conjugate symmetric")
    return upper, r[near_real].real


def full_roots(upper: np.ndarray, reals: np.ndarray) -> np.ndarray:
    return np.concatenate([upper, np.conj(upper), np.asarray(reals, dtype=complex)])


# ----------------------------------------------------------------------------------------------- analogue prototypes (cutoff 1 rad/s)


def _angles(n: int) -> tuple[np.ndarray, bool]:
    """t_k = (2k - 1) pi/(2n) for the pairs with t < pi/2 (k = 1..n//2) and whether n is odd (one real root)."""
    k = np.arange(1, n // 2 + 1)
    return (2 * k - 1) * math.pi / (2 * n), n % 2 == 1


def butterworth_prototype(n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    t, odd = _angles(n)
    upper = -np.sin(t) + 1j * np.cos(t)
    reals = np.array([-1.0]) if odd else np.zeros(0)
    return np.zeros(0, dtype=complex), upper, reals, 1.0


def chebyshev1_prototype(n: int, ripple_db: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    eps = math.sqrt(10.0 ** (ripple_db / 10.0) - 1.0)
    mu = math.asinh(1.0 / eps) / n
    t, odd = _angles(n)
    upper = -math.sinh(mu) * np.sin(t) + 1j * math.cosh(mu) * np.cos(t)
    reals = np.array([-math.sinh(mu)]) if odd else np.zeros(0)
    poles = full_roots(upper, reals)
    gain = float(np.real(np.prod(-poles)))
    if n % 2 == 0:
        gain /= math.sqrt(1.0 + eps * eps)
    return np.zeros(0, dtype=complex), upper, reals, gain


def chebyshev2_prototype(n: int, atten_db: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    mu = math.asinh(math.sqrt(10.0 ** (atten_db / 10.0) - 1.0)) / n
    t, odd = _angles(n)
    upper = 1.0 / (-math.sinh(mu) * np.sin(t) + 1j * math.cosh(mu) * np.cos(t))
    reals = np.array([-1.0 / math.sinh(mu)]) if odd else np.zeros(0)
    zeros_upper = 1j / np.cos(t)
    poles = full_roots(upper, reals)
    zeros = full_roots(zeros_upper, np.zeros(0))
    gain = float(np.real(np.prod(-poles) / np.prod(-zeros))) if zeros.size else float(np.real(np.prod(-poles)))
    return zeros_upper, upper, reals, gain


def prototype(family: str, n: int, ripple_db: float, atten_db: float):
    """(zero pairs, pole pairs, real poles, gain): the low-pass prototype with its unit point at w = 1 rad/s."""
    if family == "butterworth":
        return butterworth_prototype(n)
    if family == "chebyshev1":
        return chebyshev1_prototype(n, ripple_db)
    return chebyshev2_prototype(n, atten_db)


# ----------------------------------------------------------------------------------------------- frequency transformations (full root arrays)


def _quad_roots(c: np.ndarray, wo: float) -> np.ndarray:
    """Both roots of s^2 - 2 c s + wo^2 = 0 for every c: c +- sqrt(c^2 - wo^2)."""
    root = np.sqrt((c * c - wo * wo).astype(complex))
    return np.concatenate([c + root, c - root])


def transform(kind: str, zeros: np.ndarray, poles: np.ndarray, gain: float, wo: float, bw: float = 0.0) -> tuple[np.ndarray, np.ndarray, float]:
    """The low-pass prototype's roots after the transformation to ``kind``; ``wo`` is the cutoff (lp, hp) or centre (bp, bs), ``bw`` the band width."""
    degree = len(poles) - len(zeros)
    if kind == "lowpass":
        return zeros * wo, poles * wo, gain * wo ** degree
    if kind == "highpass":
        z = np.concatenate([wo / zeros, np.zeros(degree, dtype=complex)]) if zeros.size else np.zeros(degree, dtype=complex)
        k = gain * float(np.real(np.prod(-zeros) / np.prod(-poles))) if zeros.size else gain * float(np.real(1.0 / np.prod(-poles)))
        return z, wo / poles, k
    if kind == "bandpass":
        zl, pl = zeros * bw / 2.0, poles * bw / 2.0
        z = _quad_roots(zl, wo) if zeros.size else np.zeros(0, dtype=complex)
        z = np.concatenate([z, np.zeros(degree, dtype=complex)])
        return z, _quad_roots(pl, wo), gain * bw ** degree
    # bandstop
    zh, ph = (bw / 2.0) / zeros if zeros.size else zeros, (bw / 2.0) / poles
    z = _quad_roots(zh, wo) if zeros.size else np.zeros(0, dtype=complex)
    z = np.concatenate([z, 1j * wo * np.ones(degree), -1j * wo * np.ones(degree)])
    k = gain * float(np.real(np.prod(-zeros) / np.prod(-poles))) if zeros.size else gain * float(np.real(1.0 / np.prod(-poles)))
    return z, _quad_roots(ph, wo), k


def bilinear(z_a: np.ndarray, p_a: np.ndarray, gain: float) -> tuple[tuple[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray], float]:
    """z = (1 + s)/(1 - s) on conjugate-symmetric analogue roots: ((zero pairs, real zeros), (pole pairs, real poles), gain).

    The analogue frequency is ``tan(pi f/fs)``, so this is the bilinear transform with the usual prewarping.  Each analogue zero
    that sits at infinity (the pole excess) becomes a zero at z = -1.
    """
    degree = len(p_a) - len(z_a)
    gain_z = gain * float(np.real(np.prod(1.0 - z_a) / np.prod(1.0 - p_a))) if z_a.size else gain * float(np.real(1.0 / np.prod(1.0 - p_a)))
    zu, zr = split_roots(z_a)
    pu, pr = split_roots(p_a)
    zu_z, zr_z = (1.0 + zu) / (1.0 - zu), (1.0 + zr) / (1.0 - zr)
    pu_z, pr_z = (1.0 + pu) / (1.0 - pu), (1.0 + pr) / (1.0 - pr)
    zr_z = np.concatenate([zr_z, -np.ones(degree)])
    return (zu_z, zr_z), (pu_z, pr_z), gain_z


# ----------------------------------------------------------------------------------------------- second-order sections


def _groups(upper: np.ndarray, reals: np.ndarray, *, by_radius: bool) -> list[np.ndarray]:
    gs: list[np.ndarray] = [np.array([u, np.conj(u)]) for u in upper]
    order = sorted(reals, key=(lambda r: -abs(r)) if by_radius else (lambda r: r))
    for i in range(0, len(order), 2):
        gs.append(np.array(order[i:i + 2], dtype=complex))
    return gs


def _section(g: np.ndarray | None) -> list[float]:
    if g is None or len(g) == 0:
        return [1.0, 0.0, 0.0]
    if len(g) == 1:
        return [1.0, -float(np.real(g[0])), 0.0]
    return [1.0, -float(np.real(g[0] + g[1])), float(np.real(g[0] * g[1]))]


def to_sos(zeros: tuple[np.ndarray, np.ndarray], poles: tuple[np.ndarray, np.ndarray], gain: float) -> np.ndarray:
    """Second-order sections ``[b0 b1 b2 1 a1 a2]``: poles in conjugate pairs (two real poles together), each pair given its nearest zeros.

    Sections are ordered with the poles nearest the unit circle last; the overall gain is in the first section.
    """
    pg = _groups(*poles, by_radius=True)
    zg = _groups(*zeros, by_radius=False)
    pg.sort(key=lambda g: -float(np.max(np.abs(g))))
    pairs: list[tuple[np.ndarray | None, np.ndarray | None]] = []
    remaining = list(range(len(zg)))
    for g in pg:
        if remaining:
            j = min(remaining, key=lambda i: float(np.min(np.abs(zg[i][:, None] - g[None, :]))))
            remaining.remove(j)
            pairs.append((g, zg[j]))
        else:
            pairs.append((g, None))
    for j in remaining:  # more zero groups than pole groups: sections with no poles
        pairs.append((None, zg[j]))
    pairs.sort(key=lambda pr: 0.0 if pr[0] is None else float(np.max(np.abs(pr[0]))))
    sos = np.array([_section(z) + _section(p) for p, z in [(p, z) for p, z in pairs]], dtype=float)
    sos[0, :3] *= gain
    return sos


# ----------------------------------------------------------------------------------------------- responses


def sos_response(sos: np.ndarray, w: np.ndarray) -> np.ndarray:
    """H(e^{jw}) of a cascade of sections at angular frequencies ``w`` (rad/sample)."""
    e1 = np.exp(-1j * np.asarray(w, dtype=float))
    e2 = e1 * e1
    h = np.ones(len(e1), dtype=complex)
    for s in sos:
        h = h * (s[0] + s[1] * e1 + s[2] * e2) / (s[3] + s[4] * e1 + s[5] * e2)
    return h


def ba_response(b: np.ndarray, a: np.ndarray, w: np.ndarray) -> np.ndarray:
    e1 = np.exp(-1j * np.asarray(w, dtype=float))
    return np.polyval(np.asarray(b)[::-1], e1) / np.polyval(np.asarray(a)[::-1], e1)


def _poly_delay(c: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Re(S/P) for P = sum c_k e^{-jkw}, S = sum k c_k e^{-jkw}: the group delay (samples) of the polynomial seen as a numerator."""
    x = np.exp(-1j * w)
    c = np.asarray(c, dtype=float)
    p = np.polyval(c[::-1], x)
    s = x * np.polyval(np.polyder(c[::-1]), x) if len(c) > 1 else np.zeros_like(x)
    return np.real(s / p), np.abs(p)


def group_delay_sos(sos: np.ndarray, w: float) -> tuple[float, bool]:
    """Group delay in samples at ``w`` rad/sample, analytic: sum over sections of (numerator delay - denominator delay).

    Exactly on a zero of the unit circle the delay is 0/0.  "On a zero" is judged against the size of the section's own coefficients
    (|B(e^jw)| below 1e-9 of sum |b_k|), so a section that merely has a small gain is not mistaken for one; the value is then taken
    1e-7 rad inside and ``nudged`` is True.
    """
    nudged = False
    wv = np.array([float(w)])
    x = np.exp(-1j * float(w))
    for s in sos:
        scale = float(np.sum(np.abs(s[:3])))
        if scale > 0.0 and abs(np.polyval(s[:3][::-1], x)) < 1e-9 * scale:
            nudged = True
    if nudged:
        wv = np.array([w - 1e-7 if w > 1e-6 else w + 1e-7])
    total = 0.0
    for s in sos:
        tb, _ = _poly_delay(s[:3], wv)
        ta, _ = _poly_delay(s[3:], wv)
        total += float(tb[0] - ta[0])
    return total, nudged


def group_delay_roots(zeros: np.ndarray, poles: np.ndarray, w: float) -> tuple[float, bool]:
    """Group delay (samples) of a filter given by its z-plane roots: sum over zeros of -Re(z x/(1 - z x)) plus over poles of Re(p x/(1 - p x)), x = e^{-jw}.

    Uses the roots themselves (accurate to rounding of each root), not the polynomial coefficients, so a pole 1e-7 from the unit circle does not lose digits to
    cancellation.  On a zero of the unit circle (|1 - z x| < 1e-9) the value is taken 1e-7 rad inside and ``nudged`` is True.
    """
    nudged = False
    w_eval = float(w)
    if zeros.size and float(np.min(np.abs(1.0 - zeros * np.exp(-1j * w_eval)))) < 1e-9:
        nudged = True
        w_eval = w_eval - 1e-7 if w_eval > 1e-6 else w_eval + 1e-7
    x = np.exp(-1j * w_eval)
    tau = float(np.sum(np.real(poles * x / (1.0 - poles * x)))) - float(np.sum(np.real(zeros * x / (1.0 - zeros * x))))
    return tau, nudged


def dc_gain(sos: np.ndarray) -> float | None:
    """H(1) = product over the sections of sum(b)/sum(a); None when a pole sits at z = 1 (infinite DC gain)."""
    gain = 1.0
    for s in sos:
        den = float(np.sum(s[3:]))
        if den == 0.0:
            return None
        gain *= float(np.sum(s[:3])) / den
    return gain if math.isfinite(gain) else None


def unwrapped_phase(h: np.ndarray) -> np.ndarray:
    """Phase in radians, unwrapped along the grid; end points where |H| is ~0 (a zero on the unit circle) copy their neighbour."""
    peak = float(np.max(np.abs(h))) or 1.0
    tiny = np.abs(h) < 1e-9 * peak
    ph = np.unwrap(np.angle(h))
    if len(h) > 2:
        if tiny[0]:
            ph[0] = ph[1]
        if tiny[-1]:
            ph[-1] = ph[-2]
    return ph


# ----------------------------------------------------------------------------------------------- order equations and edges


def order_equation(family: str, ratio: float, rp_db: float, rs_db: float) -> tuple[float, int]:
    """Minimum order from the closed forms (A = 10^(Rs/10) - 1, E = 10^(Rp/10) - 1, ratio = stop edge / pass edge of the low-pass equivalent):

    Butterworth ``n >= ln(A/E) / (2 ln ratio)``;  Chebyshev I and II ``n >= acosh(sqrt(A/E)) / acosh(ratio)``.  Returns (exact, rounded up).
    """
    big_a = 10.0 ** (rs_db / 10.0) - 1.0
    small_e = 10.0 ** (rp_db / 10.0) - 1.0
    if family == "butterworth":
        exact = 0.5 * math.log(big_a / small_e) / math.log(ratio)
    else:
        exact = math.acosh(math.sqrt(big_a / small_e)) / math.acosh(ratio)
    return exact, max(1, math.ceil(exact - 1e-9))


@dataclass
class Edges:
    """The analogue (prewarped, tan(pi f/fs)) description of where the prototype's unit point lands."""

    response: str
    wc: float = 0.0   # lowpass / highpass
    wo: float = 0.0   # bandpass / bandstop centre (sqrt of the product of the two edges)
    bw: float = 0.0   # bandpass / bandstop width


def edges_from_hz(response: str, edges_hz: list[float], fs: float) -> Edges:
    w = [math.tan(math.pi * f / fs) for f in edges_hz]
    if response in ("lowpass", "highpass"):
        return Edges(response, wc=w[0])
    return Edges(response, wo=math.sqrt(w[0] * w[1]), bw=w[1] - w[0])


def edges_to_hz(edges: Edges, fs: float) -> list[float]:
    if edges.response in ("lowpass", "highpass"):
        return [fs / math.pi * math.atan(edges.wc)]
    half = edges.bw / 2.0
    lo, hi = -half + math.sqrt(half * half + edges.wo ** 2), half + math.sqrt(half * half + edges.wo ** 2)
    return [fs / math.pi * math.atan(lo), fs / math.pi * math.atan(hi)]


def lowpass_equivalent(response: str, pass_hz: list[float], stop_hz: list[float], fs: float) -> tuple[float, Edges, str | None]:
    """(ratio, passband edges in the analogue domain, problem): the stop edge of the low-pass equivalent over the pass edge, which is 1.

    ``problem`` is a text when the edges are in the wrong order for the response (a stopband edge inside the passband, ...).
    """
    wp = [math.tan(math.pi * f / fs) for f in pass_hz]
    ws = [math.tan(math.pi * f / fs) for f in stop_hz]
    if response == "lowpass":
        if not stop_hz[0] > pass_hz[0]:
            return 0.0, edges_from_hz(response, pass_hz, fs), "for a lowpass the stopband edge must be above the passband edge"
        return ws[0] / wp[0], Edges(response, wc=wp[0]), None
    if response == "highpass":
        if not stop_hz[0] < pass_hz[0]:
            return 0.0, edges_from_hz(response, pass_hz, fs), "for a highpass the stopband edge must be below the passband edge"
        return wp[0] / ws[0], Edges(response, wc=wp[0]), None
    wo2, bw = wp[0] * wp[1], wp[1] - wp[0]
    edges = Edges(response, wo=math.sqrt(wo2), bw=bw)
    if response == "bandpass":
        if not (stop_hz[0] < pass_hz[0] and stop_hz[1] > pass_hz[1]):
            return 0.0, edges, "for a bandpass the stopband edges must lie outside the passband edges (stop_low < pass_low < pass_high < stop_high)"
        return min(abs(w * w - wo2) / (bw * w) for w in ws), edges, None
    if not (stop_hz[0] > pass_hz[0] and stop_hz[1] < pass_hz[1]):
        return 0.0, edges, "for a bandstop the stopband edges must lie inside the passband edges (pass_low < stop_low < stop_high < pass_high)"
    # The centre wo of the transformation is free for a band stop: with x(w) = w / |w^2 - wo^2| (bandwidth factored out) the low-pass equivalent has
    # ratio = (smaller x at the stop edges) / (larger x at the pass edges).  Maximise it over wo between the stop edges (golden section on log wo); the
    # geometric centre of the pass edges is the starting guess, not the answer.  The binding pass edge is then exactly at x = 1.
    def x_of(w: float, centre: float) -> float:
        return w / abs(w * w - centre * centre)

    def ratio_at(log_centre: float) -> float:
        c = math.exp(log_centre)
        return min(x_of(w, c) for w in ws) / max(x_of(w, c) for w in wp)

    g = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = math.log(ws[0]), math.log(ws[1])
    c, d = b - g * (b - a), a + g * (b - a)
    fc, fd = ratio_at(c), ratio_at(d)
    for _ in range(100):
        if fc > fd:
            b, d, fd = d, c, fc
            c = b - g * (b - a)
            fc = ratio_at(c)
        else:
            a, c, fc = c, d, fd
            d = a + g * (b - a)
            fd = ratio_at(d)
    centre = math.exp(0.5 * (a + b))
    best = ratio_at(math.log(centre))
    if best < min(x_of(w, math.sqrt(wo2)) for w in ws) / max(x_of(w, math.sqrt(wo2)) for w in wp):  # never worse than the geometric centre
        centre = math.sqrt(wo2)
        best = ratio_at(math.log(centre))
    width = 1.0 / max(x_of(w, centre) for w in wp)
    return best, Edges(response, wo=centre, bw=width), None


def unit_point_scale(family: str, ratio: float, n: int, rp_db: float, rs_db: float = 0.0) -> float:
    """Where the prototype's unit point (-3 dB, -Rp or -Rs) falls in the low-pass equivalent whose passband edge is 1 and stop edge is ``ratio``.

    In all three families the passband edge is met exactly (loss Rp there) and the stopband with margin."""
    if family == "butterworth":
        eps = math.sqrt(10.0 ** (rp_db / 10.0) - 1.0)
        return eps ** (-1.0 / n)
    if family == "chebyshev1":
        return 1.0
    # Chebyshev II: loss Rp at the pass edge needs T_n(x_u) = sqrt(A/E) -> x_u = cosh(acosh(sqrt(A/E))/n): the passband edge is met exactly
    big_a, small_e = 10.0 ** (rs_db / 10.0) - 1.0, 10.0 ** (rp_db / 10.0) - 1.0
    return math.cosh(math.acosh(math.sqrt(big_a / small_e)) / n)


def scaled_edges(edges: Edges, scale: float) -> Edges:
    """Edges of the design whose prototype unit point is at ``scale`` times the passband edge (low-pass equivalent)."""
    if edges.response == "lowpass":
        return Edges("lowpass", wc=edges.wc * scale)
    if edges.response == "highpass":
        return Edges("highpass", wc=edges.wc / scale)
    if edges.response == "bandpass":
        return Edges("bandpass", wo=edges.wo, bw=edges.bw * scale)
    return Edges("bandstop", wo=edges.wo, bw=edges.bw / scale)


# ----------------------------------------------------------------------------------------------- the design


@dataclass
class IIRDesign:
    family: str
    response: str
    order: int
    ripple_db: float
    atten_db: float
    edges: Edges
    sos: np.ndarray
    b: np.ndarray
    a: np.ndarray
    zeros: np.ndarray
    poles: np.ndarray
    gain: float
    analog_poles: np.ndarray
    analog_zeros: np.ndarray
    analog_gain: float
    dc_gain: float = 1.0
    notes: list[str] = field(default_factory=list)

    @property
    def digital_order(self) -> int:
        return len(self.poles)


def build(family: str, response: str, n: int, edges: Edges, ripple_db: float = 0.0, atten_db: float = 0.0) -> IIRDesign:
    """Design the digital filter of prototype order ``n`` whose prototype unit point sits at the (prewarped) ``edges``."""
    zp, pp, pr, gain = prototype(family, n, ripple_db, atten_db)
    zeros_p, poles_p = full_roots(zp, np.zeros(0)), full_roots(pp, pr)
    zeros_a, poles_a, gain_a = transform(response, zeros_p, poles_p, gain, edges.wc if response in ("lowpass", "highpass") else edges.wo, edges.bw)
    zeros_z, poles_z, gain_z = bilinear(zeros_a, poles_a, gain_a)
    sos = to_sos(zeros_z, poles_z, gain_z)
    zeros_full, poles_full = full_roots(*zeros_z), full_roots(*poles_z)
    b = gain_z * np.real(np.poly(zeros_full))
    a = np.real(np.poly(poles_full))
    dc = gain_a * float(np.real(np.prod(-zeros_a) / np.prod(-poles_a))) if zeros_a.size else gain_a * float(np.real(1.0 / np.prod(-poles_a)))
    for name, arr in (("sections", sos), ("b", b), ("a", a), ("poles", poles_full), ("zeros", zeros_full)):
        if not np.all(np.isfinite(arr)):
            raise OverflowError(f"the {name} of this design are not finite")
    return IIRDesign(
        family, response, n, ripple_db, atten_db, edges, sos, np.atleast_1d(b), np.atleast_1d(a), zeros_full, poles_full, gain_z,
        poles_a, zeros_a, gain_a, dc_gain=dc,
    )


__all__ = [
    "Edges", "IIRDesign", "IIR_FAMILIES", "RESPONSES", "ba_response", "bilinear", "build", "dc_gain", "group_delay_roots", "edges_from_hz", "edges_to_hz", "group_delay_sos",
    "lowpass_equivalent", "order_equation", "prototype", "scaled_edges", "sos_response", "to_sos", "transform", "unit_point_scale", "unwrapped_phase",
]
