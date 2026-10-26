"""``spice_measure``: numbers derived from a simulation table (the ``result.csv`` of ``spice_simulate``, or any CSV with a header row).

The formulas (``y`` is the chosen column, ``x`` the first column; every crossing is found by linear interpolation between neighbouring samples):

* ``steady_state``  time-weighted (trapezoidal) mean of ``y`` over the last ``final_fraction`` of the x span.  ``y_final`` below means this value.
* ``rise_time``     for a rising step (``y_final > y[0]``): ``t90 - t10``, the times at which ``y`` first reaches ``y[0] + 0.1 D`` and ``y[0] + 0.9 D``,
                    ``D = y_final - y[0]``.  Needs a ``time`` column.
* ``fall_time``     the same for a falling step (``D < 0``): the first times at which ``y`` reaches ``y[0] + 0.9 D`` and ``y[0] + 0.1 D`` (that is, 90 % to 10 % of the way down).
* ``overshoot``     ``100 (max y - y_final) / D`` for ``D > 0``; ``100 (y_final - min y) / |D|`` for ``D < 0``; never negative, in percent.
* ``settling_time`` the time after which ``y`` stays inside ``y_final +- tolerance |D|`` for the rest of the record, counted from the first sample
                    (``tolerance`` default 0.02); an error if the last sample is still outside.
* ``bandwidth_3db`` for a low-pass response (``.mag`` or ``.db`` column of an ac table): the first frequency at which the gain is 10 log10(2) = 3.0103 dB
                    below the gain of the first (lowest-frequency) sample; interpolated linearly in dB against log10(frequency).  Needs a ``frequency`` column.
* ``bandpass_3db``  for a band-pass response: the peak (maximum gain) and the two frequencies at which the gain is 3.0103 dB below the peak, one on each side
                    (interpolated linearly in dB against log10(frequency)); the value is the bandwidth ``f_upper - f_lower``, and the lower and upper corners,
                    the peak frequency and gain and the geometric centre ``sqrt(f_lower f_upper)`` are values too.  A missing corner (the gain never falls
                    3 dB on that side within the table) is an error that names the other corner.
* ``value_at``      ``y`` at the x given in ``at``, interpolated linearly.

Units follow the column: ``v(...)`` volt, ``i(...)`` ampere, but ``.db`` decibel and ``.phase_deg`` degree.  On an ac table a bare name such as ``v(out)``
means ``v(out).mag`` (the magnitude per unit source) for every measurement; ask for ``v(out).db`` or ``v(out).phase_deg`` explicitly.

These are properties of the sampled table, not of the circuit: a coarse time grid or a few ac points per decade limits them (the interpolation
error is the second difference of the curve over the step, about step^2 / 8 times its curvature).
"""

from __future__ import annotations

import csv
import math
from typing import Annotated, Any

from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut
from eng_toolkit.domains.spice._server import ENGINE, server
from eng_toolkit.errors import ToolFailure
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import UnitsError, parse_quantity

MEASUREMENTS = ("steady_state", "rise_time", "fall_time", "overshoot", "settling_time", "bandwidth_3db", "bandpass_3db", "value_at")
MAX_CSV_BYTES = 20_000_000
MAX_CSV_ROWS = 500_000
MAX_CSV_COLUMNS = 200
DB_3 = 10.0 * math.log10(2.0)


def read_table(path, *, max_rows: int = MAX_CSV_ROWS) -> tuple[list[str], list[list[float]]]:
    """``(header, columns)`` of a CSV file with a header row and only finite numbers below it."""
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            header = [h.strip() for h in next(reader)]
        except StopIteration:
            raise ToolFailure("input.invalid", "the CSV file is empty", {"param": "csv_file"}) from None
        if len(header) < 2 or len(header) > MAX_CSV_COLUMNS or any(not h for h in header):
            raise ToolFailure("input.invalid", f"the CSV header needs 2 to {MAX_CSV_COLUMNS} non-empty column names", {"param": "csv_file", "columns": len(header)})
        columns: list[list[float]] = [[] for _ in header]
        for number, row in enumerate(reader, start=2):
            if number - 1 > max_rows:
                raise ToolFailure("input.too_large", f"the CSV file has more than {max_rows} rows", {"param": "csv_file", "limit_rows": max_rows})
            if len(row) != len(header):
                raise ToolFailure("input.invalid", f"CSV line {number} has {len(row)} fields, the header has {len(header)}", {"param": "csv_file", "line": number})
            for k, field in enumerate(row):
                try:
                    value = float(field)
                except ValueError:
                    raise ToolFailure("input.invalid", f"CSV line {number}: {field[:20]!r} is not a number", {"param": "csv_file", "line": number}) from None
                if not math.isfinite(value):
                    raise ToolFailure("input.invalid", f"CSV line {number}: a value is not finite", {"param": "csv_file", "line": number})
                columns[k].append(value)
    if len(columns[0]) < 3:
        raise ToolFailure("input.invalid", "the CSV file needs at least 3 data rows", {"param": "csv_file", "rows": len(columns[0])})
    return header, columns


def _resolve(header: list[str], column: str, suffixes: tuple[str, ...] = ()) -> int:
    names = [h.lower() for h in header[1:]]
    wanted = column.strip().lower()
    if not wanted:
        if len(names) == 1:
            return 1
        raise ToolFailure("input.invalid", f"'column' is required; the file has: {', '.join(header[1:6])}{' ...' if len(header) > 6 else ''}", {"param": "column", "columns": header[1:20]})
    for candidate in (wanted, *[wanted + s for s in suffixes]):
        if candidate in names:
            return names.index(candidate) + 1
    raise ToolFailure("input.invalid", f"'column' {wanted[:40]!r} is not in the file; columns: {', '.join(header[1:8])}{' ...' if len(header) > 8 else ''}", {"param": "column", "columns": header[1:20]})


def column_unit(name: str) -> str:
    """The unit of a table column from its name: ``.db`` decibel, ``.phase_deg`` degree, else volt for ``v(...)``, ampere for ``i(...)``."""
    low = name.lower()
    if low.endswith(".db"):
        return "decibel"
    if low.endswith(".phase_deg"):
        return "degree"
    return "volt" if low.startswith("v(") else "ampere" if low.startswith("i(") else "dimensionless"


def _gain_db(header: list[str], cols: list[list[float]], k: int, kind: str) -> list[float]:
    name = header[k].lower()
    if name.endswith(".db"):
        return cols[k]
    if name.endswith(".mag"):
        return [20.0 * math.log10(v) if v > 1e-20 else -400.0 for v in cols[k]]
    raise ToolFailure("input.invalid", f"{kind} needs a '.mag' or '.db' column", {"param": "column", "column": header[k][:40]})


def _corner_below(logf: list[float], gain_db: list[float], target: float, peak: int) -> float | None:
    """log10(f) of the first crossing of ``target`` going from the peak towards lower frequency, or ``None``."""
    for k in range(peak, 0, -1):
        a, b = gain_db[k], gain_db[k - 1]  # a is nearer the peak
        if a > target >= b:
            return logf[k] + (logf[k - 1] - logf[k]) * (target - a) / (b - a)
    return None


def interpolate(xs: list[float], ys: list[float], x: float) -> float:
    if x < xs[0] or x > xs[-1]:
        raise ToolFailure("measure.not_found", f"x = {x:g} is outside the data ({xs[0]:g} .. {xs[-1]:g})", {"min": xs[0], "max": xs[-1], "param": "/at"})
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    if xs[hi] == xs[lo]:
        return ys[lo]
    return ys[lo] + (ys[hi] - ys[lo]) * (x - xs[lo]) / (xs[hi] - xs[lo])


def crossing(xs: list[float], ys: list[float], level: float, *, rising: bool, start: int = 0) -> float | None:
    """The first x at or after sample ``start`` where ``y`` goes through ``level`` in the given direction (linear interpolation)."""
    for k in range(start, len(xs) - 1):
        a, b = ys[k], ys[k + 1]
        if rising and a < level <= b or (not rising) and a > level >= b:
            if b == a:
                return xs[k + 1]
            return xs[k] + (xs[k + 1] - xs[k]) * (level - a) / (b - a)
    return None


def steady_state(xs: list[float], ys: list[float], fraction: float) -> float:
    span = xs[-1] - xs[0]
    edge = xs[-1] - fraction * span
    area = 0.0
    width = 0.0
    for k in range(len(xs) - 1):
        a, b = max(xs[k], edge), xs[k + 1]
        if b <= a:
            continue
        ya = ys[k] + (ys[k + 1] - ys[k]) * (a - xs[k]) / (xs[k + 1] - xs[k])
        area += 0.5 * (ya + ys[k + 1]) * (b - a)
        width += b - a
    return area / width


def _step(xs: list[float], ys: list[float], fraction: float) -> tuple[float, float, float]:
    y0 = ys[0]
    yf = steady_state(xs, ys, fraction)
    d = yf - y0
    scale = max(abs(y0), abs(yf), 1e-300)
    if abs(d) <= 1e-9 * scale:
        raise ToolFailure("measure.not_found", "the signal does not change between its first sample and its steady state; there is no step to measure", {"first": y0, "final": yf})
    return y0, yf, d


def measure(kind: str, header: list[str], cols: list[list[float]], column: str, *, fraction: float, tolerance: float, at: float | None) -> tuple[float, str, dict[str, Any]]:
    """``(value, unit, details)``; raises ``measure.not_found`` when the table does not contain what the measurement needs.

    A refusal that names no argument of its own points at ``csv_file``: the table is what has to change.
    """
    try:
        return _measure(kind, header, cols, column, fraction=fraction, tolerance=tolerance, at=at)
    except ToolFailure as exc:
        exc.details.setdefault("param", "/csv_file")
        raise


def _measure(kind: str, header: list[str], cols: list[list[float]], column: str, *, fraction: float, tolerance: float, at: float | None) -> tuple[float, str, dict[str, Any]]:
    xs = cols[0]
    xname = header[0].lower()
    if any(b <= a for a, b in zip(xs, xs[1:])):
        raise ToolFailure("input.invalid", "the first column must be strictly increasing", {"param": "csv_file"})
    if kind in ("bandwidth_3db", "bandpass_3db"):
        k = _resolve(header, column, (".db", ".mag"))
        if xname != "frequency":
            raise ToolFailure("input.invalid", f"{kind} needs a table whose first column is 'frequency' (an ac result)", {"first_column": header[0][:30]})
        gain_db = _gain_db(header, cols, k, kind)
        logf = [math.log10(f) if f > 0 else -300.0 for f in xs]
        if kind == "bandpass_3db":
            return _bandpass(header[k], xs, logf, gain_db)
        target = gain_db[0] - DB_3
        f = crossing(logf, gain_db, target, rising=False)
        if f is None:
            hint = "; if this is a band-pass response (the gain rises first) use bandpass_3db" if max(gain_db) > gain_db[0] + 0.5 else ""
            raise ToolFailure(
                "measure.not_found",
                "the gain never falls 3.0103 dB below its first sample within the table; sweep to a higher stop frequency (or this is not a low-pass response)" + hint,
                {"first_gain_db": gain_db[0], "last_gain_db": gain_db[-1], "peak_gain_db": max(gain_db)},
            )
        return 10.0**f, "hertz", {"reference_gain_db": gain_db[0], "target_gain_db": target, "formula": "first f with gain = gain(first sample) - 3.0103 dB, linear in dB vs log10(f)"}
    k = _resolve(header, column, (".mag",) if xname == "frequency" else ())
    ys = cols[k]
    unit = column_unit(header[k])
    if kind == "value_at":
        assert at is not None
        return interpolate(xs, ys, at), unit, {"x": at, "formula": "linear interpolation between neighbouring samples"}
    if kind == "steady_state":
        return steady_state(xs, ys, fraction), unit, {"final_fraction": fraction, "formula": "time-weighted mean over the last final_fraction of the x span"}
    y0, yf, d = _step(xs, ys, fraction)
    base = {"first_sample": y0, "steady_state": yf, "step": d, "final_fraction": fraction, "last_sample_vs_steady_state_percent_of_step": 100.0 * abs(ys[-1] - yf) / abs(d)}
    if kind in ("rise_time", "fall_time", "settling_time") and xname != "time":
        raise ToolFailure("input.invalid", f"{kind} needs a table whose first column is 'time' (a transient result)", {"first_column": header[0][:30]})
    if kind == "rise_time" or kind == "fall_time":
        rising = kind == "rise_time"
        if (d > 0) != rising:
            raise ToolFailure("measure.not_found", f"{kind} needs a {'rising' if rising else 'falling'} step but the signal goes {'down' if rising else 'up'}", base)
        t10 = crossing(xs, ys, y0 + 0.1 * d, rising=rising)
        if t10 is None:
            raise ToolFailure("measure.not_found", "the 10 % level is never crossed", base)
        index = max(0, max(i for i, x in enumerate(xs) if x <= t10))
        t90 = crossing(xs, ys, y0 + 0.9 * d, rising=rising, start=index)
        if t90 is None:
            raise ToolFailure("measure.not_found", "the 90 % level is never reached within the table", base)
        return t90 - t10, "second", {**base, "t_10_percent_s": t10, "t_90_percent_s": t90, "formula": "time between the 10 % and 90 % points of the step"}
    if kind == "overshoot":
        peak = max(ys) if d > 0 else min(ys)
        over = max(0.0, (peak - yf) / d * 100.0)
        return over, "percent", {**base, "peak": peak, "formula": "100 (peak - steady_state) / step"}
    if kind == "settling_time":
        band = tolerance * abs(d)
        last_out = None
        for i in range(len(xs) - 1, -1, -1):
            if abs(ys[i] - yf) > band:
                last_out = i
                break
        if last_out is None:
            return 0.0, "second", {**base, "tolerance": tolerance, "band": band, "formula": "never outside the band"}
        if last_out == len(xs) - 1:
            raise ToolFailure("measure.not_found", "the signal is still outside the tolerance band at the last sample; it has not settled within the table", {**base, "tolerance": tolerance})
        edge = yf + math.copysign(band, ys[last_out] - yf)
        a, b = ys[last_out], ys[last_out + 1]
        t = xs[last_out] + (xs[last_out + 1] - xs[last_out]) * (edge - a) / (b - a)
        return t - xs[0], "second", {**base, "tolerance": tolerance, "band": band, "formula": "time of the last entry into yfinal +- tolerance |step|, from the first sample"}
    raise ToolFailure("input.invalid", f"unknown measurement {kind!r}", {"param": "measurement", "allowed": list(MEASUREMENTS)})


def _bandpass(column_name: str, xs: list[float], logf: list[float], gain_db: list[float]) -> tuple[float, str, dict[str, Any]]:
    peak = max(range(len(gain_db)), key=gain_db.__getitem__)
    peak_db = gain_db[peak]
    target = peak_db - DB_3
    lower = _corner_below(logf, gain_db, target, peak)
    upper = crossing(logf, gain_db, target, rising=False, start=peak)
    info: dict[str, Any] = {
        "column_used": column_name, "peak_frequency_hz": xs[peak], "peak_gain_db": peak_db, "target_gain_db": target,
        "lower_corner_hz": None if lower is None else 10.0**lower, "upper_corner_hz": None if upper is None else 10.0**upper,
        "formula": "peak = maximum gain; each corner is the crossing of peak - 3.0103 dB on its side of the peak, linear in dB vs log10(f)",
    }
    if lower is None or upper is None:
        side = "lower" if lower is None else "upper"
        raise ToolFailure(
            "measure.not_found",
            f"the gain never falls 3.0103 dB below its peak ({peak_db:.2f} dB at {xs[peak]:g} Hz) on the {side} side within the table; "
            f"sweep further {'down' if lower is None else 'up'} (the {'upper' if lower is None else 'lower'} corner is in the details)",
            info,
        )
    fl, fu = 10.0**lower, 10.0**upper
    info["extra_values"] = {
        "lower_corner_3db": (fl, "hertz"), "upper_corner_3db": (fu, "hertz"), "peak_frequency": (xs[peak], "hertz"),
        "peak_gain_db": (peak_db, "decibel"), "center_frequency_geometric": (math.sqrt(fl * fu), "hertz"),
    }
    return fu - fl, "hertz", info


def _at(raw: str, xname: str) -> float:
    if not raw or not raw.strip():
        raise UnitsError("input.missing", "'at' is required for value_at (a position on the first column, with a unit)", {"param": "at"})
    dimension, unit = {"time": ("[time]", "second"), "frequency": ("1 / [time]", "hertz")}.get(xname.lower(), (None, None))
    if dimension is None:
        qty = parse_quantity(raw, name="at")
        for dim, u in (("[electric_potential]", "volt"), ("[current]", "ampere")):
            if qty.check(dim):
                return float(qty.to(u).magnitude)
        raise UnitsError("units.dimension_mismatch", "'at' must be a voltage or a current for a sweep table", {"param": "at"})
    return float(parse_quantity(raw, name="at", dimension=dimension).to(unit).magnitude)


@server.tool(
    name="measure",
    description=(
        "Derive a number from a simulation table (the result.csv of spice_simulate, or any CSV whose first column is the x axis): "
        "steady_state value, rise_time or fall_time (10 % to 90 %), overshoot (percent), settling_time (within a tolerance band), "
        "bandwidth_3db (low-pass -3.0103 dB frequency from an ac table), bandpass_3db (peak, lower and upper -3.0103 dB corners and bandwidth) or "
        "value_at (interpolated). Units follow the column (volt, ampere, decibel for .db, degree for .phase_deg). The formulas are in the result. "
        "Needs no ngspice."
    ),
)
def spice_measure(
    csv_file: Annotated[str, Field(description="Path to the table, e.g. the result.csv artifact of spice_simulate (header row, first column = time, frequency or sweep)")],
    measurement: Annotated[str, Field(description="One of: steady_state, rise_time, fall_time, overshoot, settling_time, bandwidth_3db, bandpass_3db, value_at")],
    column: Annotated[str, Field(description="Column name, e.g. 'v(out)' (ac tables: 'v(out)' means v(out).mag; use 'v(out).db' or '.phase_deg' to say otherwise). May be empty when the table has one data column.")] = "",
    final_fraction: Annotated[float, Field(description="Share of the x span at the end that defines the steady-state value, 0 < f <= 0.5 (default 0.1)")] = 0.1,
    tolerance: Annotated[float, Field(description="settling_time only: half-width of the band as a share of the step, 0 < tol < 1 (default 0.02 = 2 %)")] = 0.02,
    at: Annotated[str, Field(description="value_at only: the x position with unit, e.g. '1 ms' for a time table, '1 kHz' for frequency, '2 V' for a DC sweep")] = "",
):
    inputs = {"csv_file": csv_file, "measurement": measurement, "column": column, "final_fraction": final_fraction, "tolerance": tolerance, "at": at}

    def compute(handle: JobHandle) -> Computed:
        kind = (measurement or "").strip().lower()
        if kind not in MEASUREMENTS:
            raise ToolFailure("input.invalid", f"'measurement' must be one of {list(MEASUREMENTS)}", {"param": "measurement", "allowed": list(MEASUREMENTS)})
        for name, v, hi, closed in (("final_fraction", final_fraction, 0.5, True), ("tolerance", tolerance, 1.0, False)):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 or v > hi or (v == hi and not closed):
                raise ToolFailure("input.invalid", f"'{name}' must be a number above 0 and {'at most' if closed else 'below'} {hi:g}", {"param": name, "received": repr(v)})
        path = handle.input_file(csv_file, param="csv_file", extensions=(".csv",), max_bytes=MAX_CSV_BYTES)
        header, cols = read_table(path)
        at_value = _at(at, header[0]) if kind == "value_at" else None
        value, unit, details = measure(kind, header, cols, column, fraction=float(final_fraction), tolerance=float(tolerance), at=at_value)
        if not math.isfinite(value):
            raise ToolFailure("engine.failure", "the measurement is not a finite number", {"param": "/column"})
        q = QuantityOut(quantity=value, unit=unit)
        extra = details.pop("extra_values", {})
        warnings = ["A property of the sampled table: its accuracy is limited by the sample spacing and by the simulation's own error."]
        moving = details.get("last_sample_vs_steady_state_percent_of_step", 0.0)
        if moving > 0.1:
            warnings.append(
                f"the signal is still moving at the end of the table (the last sample is {moving:.1f} % of the step away from the steady-state mean), "
                "so this result is relative to a value that is not final; run the simulation longer"
            )
        return Computed(
            values={kind: q, **{name: QuantityOut(quantity=float(v), unit=u) for name, (v, u) in extra.items()}},
            primary=kind,
            data={"measurement": kind, "x_column": header[0], "column": column, "samples": len(cols[0]), **details},
            warnings=warnings,
        )

    return run_tool(tool_name="spice_measure", engine=ENGINE, version=__version__, inputs=inputs, compute=compute)


__all__ = ["MEASUREMENTS", "measure", "read_table", "spice_measure"]
