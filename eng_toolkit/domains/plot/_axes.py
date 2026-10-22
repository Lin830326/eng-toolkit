"""Axes: 'nice' ticks (1, 2, 5 times a power of ten) for linear axes, decades for log axes, labels, thinning, pixel mapping.

Linear, automatic: step = the smallest of 1, 2, 5, 10 x 10^e that is at least span/target; the axis runs from the multiple of the
step at or below the data minimum to the multiple at or above the maximum (padded to nice ticks, so the ticks cover the data).
Given a range: the axis is exactly that range and the ticks are the multiples of the step inside it.  Ticks are computed as
``k*m*10^e`` from integers, so 0.1 + 0.2 never appears as 0.30000000000000004.  Equal (or relatively closer than 1e-9) minimum
and maximum are padded by 10 % of the value (to -1..1 for zero) and said so.

Log: the axis runs between whole decades around the data (one decade more on each side when the data sit on a single power of
ten); decades are equally spaced because the mapping is linear in log10.  Every ``stride``-th decade is labelled, stride from
1, 2, 5, 10, ... so that at most ``target`` labels fit; 2x and 5x are labelled when the axis spans at most two decades; minor
ticks at 2..9 x 10^n when every decade is labelled and there are at most 15.

Pixel mapping: ``px = origin_px + (v - min) * scale`` (linear) or ``px = origin_px + (log10 v - log10 min) * scale`` (log); for a
y axis ``origin_px`` is the bottom of the plot area and ``scale`` is negative (SVG y grows downwards).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from eng_toolkit.domains.plot._common import VALUE_ABS_MIN, fail
from eng_toolkit.domains.plot._svg import text_width

REL_EQUAL = 1e-9  # a span below this fraction of the largest magnitude is treated as a constant
MAX_TICKS = 60


@dataclass
class Axis:
    scale: str  # "linear" or "log"
    lo: float
    hi: float
    ticks: list[float]
    labels: list[str]
    minor: list[float] = field(default_factory=list)
    auto: bool = True
    notes: list[str] = field(default_factory=list)
    p0: float = 0.0
    p1: float = 1.0
    shown: list[bool] = field(default_factory=list)

    def tv(self, v: float) -> float:
        return math.log10(v) if self.scale == "log" else v

    @property
    def k(self) -> float:
        return (self.p1 - self.p0) / (self.tv(self.hi) - self.tv(self.lo))

    def px(self, v: float) -> float:
        return self.p0 + (self.tv(v) - self.tv(self.lo)) * self.k

    def pxa(self, values: np.ndarray) -> np.ndarray:
        t = np.log10(values) if self.scale == "log" else values
        return self.p0 + (t - self.tv(self.lo)) * self.k

    def contains(self, v: float) -> bool:
        return self.lo <= v <= self.hi

    def set_pixels(self, p0: float, p1: float) -> None:
        self.p0, self.p1 = p0, p1

    def mapping(self, name: str) -> dict:
        fn = f"log10({name})" if self.scale == "log" else name
        lo = f"log10({name}min)" if self.scale == "log" else f"{name}min"
        return {
            "transform": "log10" if self.scale == "log" else "linear", "min": self.lo, "max": self.hi, "origin_px": self.p0,
            "scale_px_per_unit": self.k, "formula": f"{name}_px = origin_px + ({fn} - {lo}) * scale_px_per_unit",
            "unit_of_scale": "px per decade" if self.scale == "log" else "px per data unit",
        }

    def describe(self) -> dict:
        return {
            "scale": self.scale, "min": self.lo, "max": self.hi, "automatic": self.auto, "ticks": self.ticks, "tick_labels": self.labels,
            "labelled": [lab for lab, s in zip(self.labels, self.shown or [True] * len(self.labels)) if s], "minor_ticks": self.minor, "notes": self.notes,
        }


def nice_step(span: float, target: int) -> tuple[int, int]:
    """(m, e) with m in 1, 2, 5: the smallest m x 10^e at least span/target."""
    raw = span / max(target, 1)
    e = math.floor(math.log10(raw))
    f = raw / 10.0 ** e
    for m in (1, 2, 5):
        if f <= m * (1 + 1e-9):
            return m, e
    return 1, e + 1


def tick_value(k: int, m: int, e: int) -> float:
    return float(f"{k * m}e{e}")


def _trim(s: str) -> str:
    return "0" if s.lstrip("-") in ("0", "0.0", "") or float(s) == 0 else s


def linear_labels(ticks: list[float], e: int) -> list[str]:
    big = max((abs(t) for t in ticks), default=0.0)
    decimals = max(0, -e)
    if big < 1e6 and decimals <= 5:
        return [_trim(f"{t:.{decimals}f}") for t in ticks]
    top = math.floor(math.log10(big)) if big > 0 else 0
    digits = min(12, max(0, top - e))
    out = []
    for t in ticks:
        if t == 0:
            out.append("0")
            continue
        mant, exp = f"{t:.{digits}e}".split("e")
        if "." in mant:
            mant = mant.rstrip("0").rstrip(".")
        out.append(f"{mant}e{int(exp)}")
    return out


def format_value(v: float, sig: int = 4) -> str:
    """A number for a label: exact integers below 1e9 as integers, 1e-4 <= |v| < 1e9 as a plain decimal with ``sig`` significant
    digits (trailing zeros trimmed), anything else as ``1.234e-5`` (no '+', no leading zeros in the exponent)."""
    if v == 0:
        return "0"
    a = abs(v)
    if a < 1e9 and v == int(v):
        return str(int(v))
    if 1e-4 <= a < 1e9:
        decimals = max(0, sig - 1 - math.floor(math.log10(a)))
        text = f"{v:.{decimals}f}"
        return text.rstrip("0").rstrip(".") if "." in text else text
    mant, exp = f"{v:.{sig - 1}e}".split("e")
    if "." in mant:
        mant = mant.rstrip("0").rstrip(".")
    return f"{mant}e{int(exp)}"


def verified_labels(ticks: list[float], labels: list[str]) -> list[str]:
    """``labels`` if they are all different and each reads back as its tick (to 1e-6 of the tick spacing); otherwise labels with as
    many significant digits as the spacing needs.  So no label repeats and none names a different value than its tick."""
    if not ticks:
        return labels
    spacing = min((b - a for a, b in zip(ticks, ticks[1:])), default=abs(ticks[0]) or 1.0) or 1.0

    def good(labs: list[str]) -> bool:
        if len(set(labs)) != len(labs):
            return False
        try:
            return all(abs(float(lab) - t) <= 1e-6 * spacing for lab, t in zip(labs, ticks))
        except ValueError:
            return False

    if good(labels):
        return labels
    for sig in range(2, 18):
        labs = [format_value(t, sig) for t in ticks]
        if good(labs):
            return labs
    return [repr(t) for t in ticks]


def _equalish(lo: float, hi: float) -> bool:
    return hi - lo <= REL_EQUAL * max(abs(lo), abs(hi))


def linear_axis(lo: float, hi: float, target: int, fixed: tuple[float, float] | None, *, param: str, what: str, margin: float = 0.0) -> Axis:
    notes: list[str] = []
    if fixed is None:
        big = max(abs(lo), abs(hi))
        if 0.0 < big < VALUE_ABS_MIN:
            fail(param, f"{what}: every value is below {VALUE_ABS_MIN:g} in magnitude (largest {big:g}); rescale the data", "input.out_of_range", limit=VALUE_ABS_MIN)
        if _equalish(lo, hi):
            centre = (lo + hi) / 2.0
            pad = abs(centre) * 0.1 if centre != 0.0 else 1.0
            notes.append(f"{what}: all values are equal ({centre:.6g}); the axis was padded to {centre - pad:.6g} .. {centre + pad:.6g}")
            lo, hi = centre - pad, centre + pad
        elif margin:
            span = hi - lo
            lo, hi = lo - margin * span, hi + margin * span
    else:
        lo, hi = fixed
        if _equalish(lo, hi) or 0.0 < max(abs(lo), abs(hi)) < VALUE_ABS_MIN:
            fail(param, f"{what}: the range {lo:g},{hi:g} is too narrow to draw (relative width below {REL_EQUAL:g}) or too small (below {VALUE_ABS_MIN:g})", "input.out_of_range")
    m, e = nice_step(hi - lo, target)
    step = m * 10.0 ** e
    if fixed is None:
        kmin, kmax = math.floor(lo / step + 1e-9), math.ceil(hi / step - 1e-9)
    else:
        kmin, kmax = math.ceil(lo / step - 1e-9), math.floor(hi / step + 1e-9)
    ticks = [tick_value(k, m, e) for k in range(kmin, kmax + 1)] if 0 <= kmax - kmin <= MAX_TICKS else []
    if any(b <= a for a, b in zip(ticks, ticks[1:])) or len(ticks) < 2:
        # bound check: ticks must be distinct and increasing; otherwise fall back to the two ends
        ticks = []
    if fixed is None and ticks:
        # the end ticks, widened by the (at most 1e-9 step) rounding noise a value may carry beyond them, so the axis always
        # covers the data: 1e-15 above 0 dB does not add a whole tick
        lo, hi = min(ticks[0], lo), max(ticks[-1], hi)
    if not ticks:
        ticks = [lo, hi]
        labels = [f"{lo:.6g}", f"{hi:.6g}"]
    else:
        labels = linear_labels(ticks, e)
    return Axis("linear", lo, hi, ticks, verified_labels(ticks, labels), auto=fixed is None, notes=notes)


def _log_label(v: float) -> str:
    n = math.floor(math.log10(v) + 1e-9)
    m = round(v / 10.0 ** n)
    if -4 <= n <= 5:
        return f"{v:.{max(0, -n)}f}"
    return f"{m}e{n}" if m != 1 else f"1e{n}"


def _nice_below(v: float) -> float:
    n = math.floor(math.log10(v))
    return next((c for c in (float(f"{m}e{n}") for m in (5, 2, 1)) if c <= v), float(f"5e{n - 1}"))


def _nice_above(v: float) -> float:
    n = math.floor(math.log10(v))
    return next(c for c in (float(f"{m}e{n}") for m in (1, 2, 5, 10)) if c >= v)


def log_axis(lo: float, hi: float, target: int, fixed: tuple[float, float] | None, *, param: str, what: str, margin: float = 0.0) -> Axis:
    notes: list[str] = []
    if fixed is not None:
        lo, hi = fixed
        if lo <= 0.0:
            fail(param, f"{what}: a log axis needs a positive range, got {lo:g},{hi:g}")
        la, lb = math.log10(lo), math.log10(hi)
        if lb - la < 1e-6:
            fail(param, f"{what}: the range {lo:g},{hi:g} is too narrow for a log axis", "input.out_of_range")
    elif margin and hi > lo:
        # markers: the data plus a margin, out to the next 1, 2 or 5 x 10^n (whole decades would waste a decade per side)
        grow = 10.0 ** (margin * (math.log10(hi) - math.log10(lo)))
        lo, hi = _nice_below(lo / grow), _nice_above(hi * grow)
        la, lb = math.log10(lo), math.log10(hi)
    else:
        la, lb = math.floor(math.log10(lo) + 1e-12), math.ceil(math.log10(hi) - 1e-12)
        if lb <= la:
            notes.append(f"{what}: all values lie on {lo:.6g}; the log axis was padded by one decade on each side")
            la, lb = la - 1, lb + 1
        lo, hi = min(10.0 ** la, lo), max(10.0 ** lb, hi)  # whole decades, widened by rounding noise only
    first, last = math.floor(la + 1e-9), math.ceil(lb - 1e-9)
    decades = max(1, last - first)
    stride = next(s for s in (1, 2, 5, 10, 20, 50, 100, 200, 500) if decades / s <= max(target, 1))
    inside = lambda v: lo * (1 - 1e-12) <= v <= hi * (1 + 1e-12)  # noqa: E731
    labelled = [float(f"1e{n}") for n in range(first, last + 1) if n % stride == 0 and inside(float(f"1e{n}"))]
    if decades <= 2:
        labelled += [float(f"{mm}e{n}") for n in range(first, last + 1) for mm in (2, 5) if inside(float(f"{mm}e{n}"))]
    minor = []
    if stride == 1 and decades <= 15:
        minor = [float(f"{mm}e{n}") for n in range(first, last + 1) for mm in range(2, 10) if inside(float(f"{mm}e{n}"))]
    if len(labelled) < 2:
        labelled = sorted(set(labelled) | {float(f"{mm}e{n}") for n in range(first, last + 1) for mm in range(1, 10) if inside(float(f"{mm}e{n}"))})
    if len(labelled) < 2:  # a range inside one decade with fewer than two 1..9 multiples: linear nice ticks, labelled by their spacing
        lin = linear_axis(lo, hi, target, (lo, hi), param=param, what=what)
        pairs = [(t, lab) for t, lab in zip(lin.ticks, lin.labels) if t > 0]
        ticks, labels = [t for t, _ in pairs], [lab for _, lab in pairs]
    else:
        ticks = sorted(set(labelled))
        labels = [_log_label(t) for t in ticks]
    minor = [v for v in minor if v not in set(ticks)]
    return Axis("log", lo, hi, ticks, verified_labels(ticks, labels), minor=minor, auto=fixed is None, notes=notes)


def make_axis(scale: str, lo: float, hi: float, target: int, fixed: tuple[float, float] | None, *, param: str, what: str, margin: float = 0.0) -> Axis:
    """An axis; ``margin`` (a fraction of the data span, automatic ranges only) keeps markers off the frame."""
    if scale == "log":
        return log_axis(lo, hi, target, fixed, param=param, what=what, margin=margin)
    return linear_axis(lo, hi, target, fixed, param=param, what=what, margin=margin)


def thin(positions: list[float], extents: list[float], gap: float) -> list[bool]:
    """Which labels to show so that no two overlap: every k-th label, the smallest k that works (k = 1 keeps all).

    ``extents`` are the label widths (x axis) or heights (y axis) in px, centred on ``positions``."""
    n = len(positions)
    for k in range(1, max(n, 1) + 1):
        idx = list(range(0, n, k))
        if all(abs(positions[b] - positions[a]) >= (extents[a] + extents[b]) / 2 + gap for a, b in zip(idx, idx[1:])):
            return [i % k == 0 for i in range(n)]
    return [i == 0 for i in range(n)]


def label_widths(labels: list[str], size: float) -> list[float]:
    return [text_width(lab, size) for lab in labels]


__all__ = ["format_value", "verified_labels", "Axis", "label_widths", "linear_axis", "log_axis", "make_axis", "nice_step", "thin", "tick_value"]
