"""Input checks and output helpers shared by the ``control`` tools.

Error families are the existing ones: ``input.invalid`` for a value that is not allowed at all (empty, not a number, a zero
leading coefficient of a denominator, an unknown choice, an improper transfer function where a proper one is needed),
``input.out_of_range`` for a value that parses but is outside what the tools support (order above 40, a coefficient above 1e12,
more than 100 000 samples), ``input.missing`` for a required parameter that is blank, and ``units.*`` from ``parse_quantity``.

Models are given as coefficient lists in descending powers (JSON text such as ``"[1, 3, 2]"``) or as small polynomial text
(``"s^2 + 3 s + 2"``, parsed by :mod:`eng_toolkit.domains.control._poly`, never evaluated).  A discrete model is declared with
``domain='discrete'`` and a ``sample_time``; a ``sample_time`` with a continuous model is refused.
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import Annotated, Any

import numpy as np
from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.control import _poly as poly
from eng_toolkit.domains.control._lti import TF
from eng_toolkit.domains.control._server import ENGINE
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import UnitsError, parse_quantity

NumeratorIn = Annotated[str, Field(description="Numerator polynomial: JSON list of coefficients in DESCENDING powers, e.g. '[1, 3]' for s+3, or text such as 's + 3'.")]
DenominatorIn = Annotated[str, Field(description="Denominator polynomial, same formats, e.g. '[1, 3, 2]' or 's^2 + 3 s + 2'. The leading coefficient must not be 0. Order at most 40, coefficients 1e-12..1e12 in magnitude.")]
DomainIn = Annotated[str, Field(description="'continuous' (variable s, default) or 'discrete' (variable z; needs 'sample_time')")]
SampleTimeIn = Annotated[str, Field(description="Sample time with unit, e.g. '0.1 s'. Only with domain='discrete'; refused for a continuous model.")]

DOMAINS = ("continuous", "discrete")
SAMPLE_TIME_RANGE = (1e-9, 1e9)
TIME_RANGE = (1e-9, 1e9)
FREQ_RANGE = (1e-6, 1e6)

STANDING = (
    "Classical linear time-invariant SISO analysis. Polynomials are exact as given; root-based results (poles, damping, "
    "margins) are numerical and flagged when ill-conditioned. Starting points for design, not a substitute for simulation "
    "of the real system.",
)


# ----------------------------------------------------------------------------------------------- scalar parameters


def is_blank(raw: Any) -> bool:
    return raw is None or (isinstance(raw, str) and raw.strip() == "")


def choice(value: Any, *, name: str, allowed: tuple[str, ...]) -> str:
    key = str(value).strip().lower().replace(" ", "_").replace("-", "_") if isinstance(value, str) else ""
    if key not in allowed:
        raise UnitsError("input.invalid", f"'{name}' must be one of {list(allowed)}, got {str(value)[:40]!r}", {"param": name, "allowed": list(allowed)})
    return key


def plain_number(value: Any, *, name: str, lo: float, hi: float, lo_open: bool = False, range_error: bool = False) -> float:
    """A JSON number in ``[lo, hi]``.  Outside is ``input.invalid`` (or ``input.out_of_range`` with ``range_error``); NaN and infinity never pass."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnitsError("input.invalid", f"'{name}' must be a plain number, got {str(value)[:40]!r}", {"param": name})
    number = float(value)
    if not math.isfinite(number):
        raise UnitsError("input.invalid", f"'{name}' is not finite", {"param": name})
    ok = (number > lo if lo_open else number >= lo) and number <= hi
    if not ok:
        family = "input.out_of_range" if range_error else "input.invalid"
        raise UnitsError(family, f"'{name}' must be in {'(' if lo_open else '['}{lo:g}, {hi:g}], got {number:g}", {"param": name, "received": number, "min": lo, "max": hi})
    return number


def whole_number(value: Any, *, name: str, lo: int, hi: int, range_error: bool = True) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise UnitsError("input.invalid", f"'{name}' must be a whole number, got {str(value)[:40]!r}", {"param": name})
    if float(value) != int(value):
        raise UnitsError("input.invalid", f"'{name}' must be a whole number, got {value:g}", {"param": name})
    n = int(value)
    if not lo <= n <= hi:
        raise UnitsError("input.out_of_range" if range_error else "input.invalid", f"'{name}' must be between {lo} and {hi}, got {n}", {"param": name, "min": lo, "max": hi, "received": n})
    return n


def time_value(raw: Any, *, name: str, lo: float, hi: float) -> float:
    """A positive time with a unit, in seconds.  <= 0 is ``input.invalid``; outside ``[lo, hi]`` is ``input.out_of_range``."""
    if isinstance(raw, str) and len(raw) > 200:
        raise UnitsError("input.out_of_range", f"'{name}' is {len(raw)} characters long; expected a short quantity such as '0.1 s'", {"param": name})
    qty = parse_quantity(raw, name=name, dimension="time")
    seconds = float(qty.to("second").magnitude)
    if not math.isfinite(seconds):
        raise UnitsError("input.invalid", f"'{name}' is not finite", {"param": name})
    if seconds <= 0:
        raise UnitsError("input.invalid", f"'{name}' must be positive, got {qty:~P}", {"param": name, "received": str(raw)[:40]})
    if not lo <= seconds <= hi:
        raise UnitsError("input.out_of_range", f"'{name}' = {qty:~P} is outside the supported range {lo:g} .. {hi:g} s", {"param": name, "min": lo, "max": hi, "unit": "s"})
    return seconds


# ----------------------------------------------------------------------------------------------- models


def domain_and_step(domain: Any, sample_time: Any) -> tuple[str, float | None]:
    kind = choice(domain, name="domain", allowed=DOMAINS)
    if kind == "continuous":
        if not is_blank(sample_time):
            raise UnitsError(
                "input.invalid",
                "'sample_time' was given with domain='continuous'; a continuous model has no sample time (use domain='discrete' for a z-domain model, or leave 'sample_time' empty)",
                {"param": "sample_time"},
            )
        return kind, None
    if is_blank(sample_time):
        raise UnitsError("input.missing", "domain='discrete' needs 'sample_time', e.g. '0.1 s'", {"param": "sample_time"})
    return kind, time_value(sample_time, name="sample_time", lo=SAMPLE_TIME_RANGE[0], hi=SAMPLE_TIME_RANGE[1])


def numerator_polynomial(raw: Any, *, param: str, var: str) -> np.ndarray:
    p = poly.parse_polynomial(raw, param=param, var=var)
    t = poly.trim(p)
    if t[0] == 0.0:
        raise UnitsError("input.invalid", f"'{param}' is the zero polynomial; a transfer function with a zero numerator has no dynamics to analyse", {"param": param})
    return t


def denominator_polynomial(raw: Any, *, param: str, var: str) -> np.ndarray:
    p = poly.parse_polynomial(raw, param=param, var=var)
    if p[0] == 0.0:
        raise UnitsError(
            "input.invalid",
            f"the leading coefficient of '{param}' is 0; drop the leading zeros (the order is the number of coefficients minus one, so '[0, 1, 2]' is not a valid denominator)",
            {"param": param},
        )
    return p


def build_tf(
    numerator: Any, denominator: Any, *, domain: Any = "continuous", sample_time: Any = "", require_proper: bool = False,
    num_name: str = "numerator", den_name: str = "denominator",
) -> TF:
    kind, step = domain_and_step(domain, sample_time)
    var = "s" if kind == "continuous" else "z"
    num = numerator_polynomial(numerator, param=num_name, var=var)
    den = denominator_polynomial(denominator, param=den_name, var=var)
    tf = TF(num, den, step)
    if require_proper and not tf.proper:
        raise UnitsError(
            "input.invalid",
            f"the transfer function is improper (numerator order {len(num) - 1} above denominator order {len(den) - 1}); this tool needs a proper one "
            "- add poles (for a PID write a derivative filter, e.g. 's/(0.1 s + 1)')",
            {"param": num_name, "numerator_order": len(num) - 1, "denominator_order": len(den) - 1},
        )
    return tf


def check_order(p: np.ndarray, *, name: str) -> np.ndarray:
    p = poly.trim(p)
    if len(p) - 1 > poly.MAX_ORDER:
        raise UnitsError("input.out_of_range", f"{name} (a product of the polynomials given) has order {len(p) - 1}, above the limit {poly.MAX_ORDER}", {"param": name, "limit_order": poly.MAX_ORDER})
    if not np.all(np.isfinite(p)) or float(np.max(np.abs(p))) > 1e280:
        raise UnitsError("input.out_of_range", f"{name} (a product of the polynomials given) has a coefficient far outside the representable range", {"param": name})
    return p


# ----------------------------------------------------------------------------------------------- roots, stability, numerical quality


def complex_out(z: complex) -> dict[str, float]:
    return {"real": float(z.real) + 0.0, "imag": float(z.imag) + 0.0}


def pole_rows(roots: np.ndarray, dt: float | None) -> list[dict[str, Any]]:
    """For each root: natural frequency, damping ratio, time constant and whether it lies in the stable region.

    Continuous: ``wn = |p|``, ``zeta = -Re p / |p|``, ``tau = -1/Re p`` (None if Re p >= 0).  Discrete: through the equivalent
    ``s = ln(z)/T`` (``wn`` in rad/s, ``tau = -T/ln|z|``); a root at ``z = 0`` is a pure delay (no ``wn``).
    """
    rows = []
    for r in roots:
        row: dict[str, Any] = {**complex_out(r)}
        if dt is None:
            mag = abs(r)
            row.update(
                natural_frequency=float(mag),
                damping_ratio=float(-r.real / mag) if mag > 0 else None,
                time_constant=float(-1.0 / r.real) if r.real < 0 else None,
                stable=bool(r.real < 0),
            )
        else:
            mag = abs(r)
            row["magnitude"] = float(mag)
            row["stable"] = bool(mag < 1.0)
            if mag == 0.0:
                row.update(natural_frequency=None, damping_ratio=None, time_constant=0.0, note="pole at z = 0: a pure delay of one sample")
            else:
                s = complex(np.log(r)) / dt
                row.update(
                    natural_frequency=float(abs(s)),
                    damping_ratio=float(-s.real / abs(s)) if abs(s) > 0 else None,
                    time_constant=float(-dt / math.log(mag)) if mag < 1.0 else None,
                    equivalent_s=complex_out(s),
                )
        rows.append(row)
    return rows


def dominant(roots: np.ndarray, dt: float | None) -> np.ndarray | None:
    """The root(s) closest to instability (largest real part, or largest magnitude for a discrete model)."""
    if roots.size == 0:
        return None
    key = roots.real if dt is None else np.abs(roots)
    best = float(key.max())
    tol = 1e-9 * max(1.0, abs(best))
    return roots[key >= best - tol]


def stability_report(den: np.ndarray, dt: float | None, roots: np.ndarray) -> dict[str, Any]:
    """Stability of the polynomial ``den`` by an exact test, cross-checked against the numerical roots.

    Continuous: the Routh array and the exact half-plane count; discrete: the Jury table and the exact unit-circle count.  The
    counts are in rational arithmetic on the coefficients as given; ``roots_agree`` compares them with ``numpy.roots``
    (tolerance 1e-7 relative).  Verdict: ``stable`` (all poles strictly inside), ``marginally stable`` (some on the boundary,
    each a simple root, none outside) or ``unstable`` (a root outside, or a repeated root on the boundary).
    """
    warnings: list[str] = []
    n = len(den) - 1
    if dt is None:
        counts = poly.half_plane_counts(den)
        arr = poly.routh_counts(den)
        outside, on, inside, distinct = counts["rhp"], counts["imag"], counts["lhp"], counts["imag_distinct"]
        array_counts = (arr["rhp"], arr["imag"], arr["lhp"]) if arr["exact"] else None
        method = {
            "name": "Routh-Hurwitz (exact, rational arithmetic)",
            "array": arr["array"],
            "first_column": arr["first_column"],
            "array_conclusive": bool(arr["exact"]),
            "array_counts": None if array_counts is None else {"rhp": array_counts[0], "imag": array_counts[1], "lhp": array_counts[2]},
            "array_agrees_with_cauchy_count": None if array_counts is None else array_counts == (outside, on, inside),
            "count_method": "Cauchy index of Q/P with a Sturm chain (no special cases); the array is shown and cross-checked",
        }
        if not arr["exact"]:
            method["array_note"] = "the Routh array hit a degenerate case (a zero first element that multiplying by (s+a) cannot cure); the counts come from the Cauchy-index method, which has no such case"
        tol = lambda r: 1e-7 * max(1.0, abs(r))  # noqa: E731
        num_out = int(sum(1 for r in roots if r.real > tol(r)))
        num_on = int(sum(1 for r in roots if abs(r.real) <= tol(r)))
        num_in = int(sum(1 for r in roots if r.real < -tol(r)))
    else:
        counts = poly.unit_circle_counts(den)
        jury = poly.jury_table(den)
        outside, on, inside, distinct = counts["outside"], counts["on_circle"], counts["inside"], counts["on_circle_distinct"]
        method = {
            "name": "Jury table (exact, rational arithmetic; order <= 12) + exact unit-circle root count (bilinear map and half-plane count)",
            "jury_stable": None if jury["stable"] is None else bool(jury["stable"]),
            "jury_conditions": jury["conditions"],
            "jury_rows": jury["rows"],
            **({"jury_note": jury["reason"]} if jury.get("omitted") else {}),
        }
        tol = lambda r: 1e-7  # noqa: E731
        num_out = int(sum(1 for r in roots if abs(r) > 1 + tol(r)))
        num_on = int(sum(1 for r in roots if abs(abs(r) - 1) <= tol(r)))
        num_in = int(sum(1 for r in roots if abs(r) < 1 - tol(r)))
    if not counts["exact"]:  # pragma: no cover - internal consistency check
        warnings.append("the exact half-plane count failed its consistency check; the numerical roots are used")
        outside, on, inside, distinct = num_out, num_on, num_in, num_on
    agree = (outside, on, inside) == (num_out, num_on, num_in)
    if not agree:
        warnings.append(
            f"the exact count (outside {outside}, on the boundary {on}, inside {inside}) and the numerical roots "
            f"({num_out}, {num_on}, {num_in}) disagree: the polynomial is ill-conditioned (repeated or clustered roots, or a huge coefficient range); trust the exact count"
        )
    if outside > 0:
        verdict = "unstable"
    elif on == 0:
        verdict = "stable"
    elif on == distinct:
        verdict = "marginally stable"
    else:
        verdict = "unstable"
    notes = []
    if verdict == "unstable" and outside == 0:
        notes.append("repeated roots on the stability boundary: the response grows without bound (polynomially)")
    if verdict == "marginally stable":
        notes.append("simple roots on the stability boundary: bounded for some inputs, unbounded for others (an integrator drifts under a constant input)")
    return {
        "verdict": verdict,
        "stable": verdict == "stable",
        "order": n,
        "outside" if dt is not None else "right_half_plane": outside,
        ("on_unit_circle" if dt is not None else "on_imaginary_axis"): on,
        ("inside" if dt is not None else "left_half_plane"): inside,
        "method": method,
        "numerical_roots_count": {"outside": num_out, "on_boundary": num_on, "inside": num_in},
        "roots_agree": bool(agree),
        "notes": notes,
        "_warnings": warnings,
        "_counts": (outside, on, inside),
    }


def numerical_checks(num: np.ndarray, den: np.ndarray, zeros: np.ndarray, poles: np.ndarray, dt: float | None) -> dict[str, Any]:
    """Conditioning diagnostics: coefficient range, root residuals, clustered roots, near pole-zero cancellations, high order."""
    warnings: list[str] = []
    out: dict[str, Any] = {}

    def spread(p: np.ndarray) -> float:
        nz = np.abs(p[p != 0])
        return float(nz.max() / nz.min()) if nz.size else 1.0

    out["coefficient_range_ratio"] = {"numerator": spread(num), "denominator": spread(den)}
    for label, r in (("numerator", out["coefficient_range_ratio"]["numerator"]), ("denominator", out["coefficient_range_ratio"]["denominator"])):
        if r > 1e8:
            warnings.append(f"the {label} coefficients span a ratio of {r:.1e}: expect lost digits in the roots; rescale the time or the gain")
    worst = 0.0
    for p, rts in ((den, poles), (num, zeros)):
        for r in rts:
            scale = float(np.sum(np.abs(p) * np.abs(r) ** np.arange(len(p) - 1, -1, -1)))
            if scale > 0:
                worst = max(worst, float(abs(poly.polyval(p, r))) / scale)
    out["max_root_residual"] = worst
    if worst > 1e-8:
        warnings.append(f"a computed root leaves a relative residual of {worst:.1e} in its polynomial: the roots are inaccurate (ill-conditioned polynomial)")
    close = 0
    for rts, label in ((poles, "poles"), (zeros, "zeros")):
        for i in range(len(rts)):
            for j in range(i + 1, len(rts)):
                if abs(rts[i] - rts[j]) < 1e-3 * max(1.0, abs(rts[i]), abs(rts[j])):
                    close += 1
    out["close_root_pairs"] = close
    if close:
        warnings.append(
            f"{close} pair(s) of poles or zeros lie within 1e-3 of each other (repeated or clustered roots): numerical roots of a root repeated m times are accurate only to about 1e-16^(1/m)"
        )
    cancel = []
    for p in poles:
        for z in zeros:
            if abs(p - z) <= 1e-3 * max(abs(p), abs(z), 1e-12):
                unstable = (p.real >= 0) if dt is None else (abs(p) >= 1)
                cancel.append({"pole": complex_out(p), "zero": complex_out(z), "distance": float(abs(p - z)), "cancelled_pole_is_unstable_or_marginal": bool(unstable)})
    out["near_pole_zero_cancellations"] = cancel
    if cancel:
        warnings.append(
            f"{len(cancel)} near pole-zero cancellation(s): the transfer function hides a mode that is still in the system (uncontrollable or unobservable); "
            "stability of the transfer function does not guarantee internal stability"
        )
        if any(c["cancelled_pole_is_unstable_or_marginal"] for c in cancel):
            warnings.append("a cancelled pole is unstable or marginal: this loop is NOT internally stable even if the cancelled transfer function looks stable")
    if len(den) - 1 > 15:
        warnings.append(f"order {len(den) - 1}: companion-matrix based results (roots, state-space simulation) lose accuracy above about order 15")
    out["warnings"] = warnings
    return out


# ----------------------------------------------------------------------------------------------- outputs


def _finite(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise UnitsError(
            "input.out_of_range",
            f"the result '{label}' is not a finite number for these inputs (overflow or division by zero); the inputs are outside what the tool supports",
            {"result": label},
        )
    return value


def qty(value: float, unit: str, *, label: str) -> QuantityOut:
    return QuantityOut(quantity=_finite(float(value), label), unit=unit)


def plain(value: float, *, label: str) -> QuantityOut:
    return qty(value, "dimensionless", label=label)


def clean(obj: Any, path: str = "data") -> Any:
    """Plain Python data: numpy scalars and arrays converted, complex numbers as ``{real, imag}``, and a NaN or infinity refused."""
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, np.ndarray):
        obj = obj.tolist()
    if isinstance(obj, bool) or obj is None or isinstance(obj, (str, int)):
        return obj
    if isinstance(obj, float):
        return _finite(obj, path) + 0.0
    if isinstance(obj, complex):
        return {"real": _finite(obj.real, path), "imag": _finite(obj.imag, path)}
    if isinstance(obj, Fraction):
        return clean(float(obj), path)
    if isinstance(obj, dict):
        return {str(k): clean(v, f"{path}.{k}") for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, (list, tuple)):
        return [clean(v, f"{path}[{i}]") for i, v in enumerate(obj)]
    return str(obj)


def capped(rows: list[Any], limit: int = 10) -> dict[str, Any]:
    """The first ``limit`` rows of a list and how many there are in all (a delay gives infinitely many phase crossings)."""
    return {"items": rows[:limit], "total": len(rows), "truncated": len(rows) > limit}


def poly_out(p: np.ndarray, var: str = "s") -> dict[str, Any]:
    p = poly.trim(p)
    return {"coefficients_descending": [float(c) for c in p], "text": poly.to_text(p, var), "order": len(p) - 1}


def tf_out(tf: TF) -> dict[str, Any]:
    var = "s" if tf.dt is None else "z"
    return {
        "domain": "continuous" if tf.dt is None else "discrete",
        "sample_time_s": tf.dt,
        "numerator": poly_out(tf.num, var),
        "denominator": poly_out(tf.den, var),
        "variable": var,
    }


def csv_text(header: list[str], columns: list[np.ndarray]) -> str:
    lines = [",".join(header)]
    rows = zip(*columns)
    lines.extend(",".join(format(float(v), ".12g") for v in row) for row in rows)
    return "\n".join(lines) + "\n"


def run(tool_name: str, inputs: dict[str, Any], compute) -> ToolResult:
    return run_tool(tool_name=tool_name, engine=ENGINE, version=__version__, inputs=inputs, compute=compute, engines=("numpy",), standing_warnings=STANDING)


__all__ = [
    "Computed", "DenominatorIn", "DomainIn", "FREQ_RANGE", "JobHandle", "NumeratorIn", "QuantityOut", "SampleTimeIn", "TIME_RANGE",
    "build_tf", "capped", "check_order", "choice", "clean", "complex_out", "csv_text", "denominator_polynomial", "domain_and_step", "dominant",
    "is_blank", "numerator_polynomial", "numerical_checks", "plain", "plain_number", "pole_rows", "poly_out", "qty", "run",
    "stability_report", "tf_out", "time_value", "whole_number",
]
