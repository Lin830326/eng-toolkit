"""Input checks, sample readers and output helpers shared by the ``dsp`` tools.

Error families are the existing ones: ``input.invalid`` for a value that is not allowed at all (a frequency at or above the Nyquist
frequency, a non-number in a sample list, an unknown choice), ``input.out_of_range`` for a value that parses but is beyond what the
tools support (more than 2**22 samples, IIR order above 40, a sample rate of 1e15 Hz), ``input.missing`` for a required parameter that
is blank, ``input.too_large`` for a file over the byte cap, and ``units.*`` for a frequency without a unit or with a unit of another
dimension.  Nothing the caller sends is executed: frequencies go through a regular expression and a table of units, samples through
``json.loads`` (which builds data only) or a strict line parser.
"""

from __future__ import annotations

from eng_toolkit import jsonsafe
import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

import numpy as np

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.dsp._server import ENGINE
from eng_toolkit.errors import ToolFailure
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import UnitsError

MAX_SAMPLES = 1 << 22
MAX_TEXT_CHARS = 100_000_000
MAX_FILE_BYTES = 120_000_000
SAMPLE_ABS_MAX = 1e100
RATE_RANGE = (1e-3, 1e12)
MAX_IIR_ORDER = 40
MAX_TAPS = 4095
MAX_TABLE_ROWS = 20000
LOG_FLOOR = 1e-15  # |H| below this is shown as -300 dB

STANDING = (
    "Digital signal processing with numpy only (FFT, closed-form filter design). Designs and spectra are deterministic and "
    "tested against textbook values and scipy.signal; leakage, windowing, finite word length and non-stationary signals are the "
    "caller's to judge. Starting points for design, not a substitute for measuring the real system.",
)


# ----------------------------------------------------------------------------------------------- scalar parameters


def is_blank(raw: Any) -> bool:
    return raw is None or (isinstance(raw, str) and raw.strip() == "")


def choice(value: Any, *, name: str, allowed: tuple[str, ...]) -> str:
    key = str(value).strip().lower().replace(" ", "_").replace("-", "_") if isinstance(value, str) else ""
    if key not in allowed:
        raise UnitsError("input.invalid", f"'{name}' must be one of {list(allowed)}, got {str(value)[:40]!r}", {"param": name, "allowed": list(allowed)})
    return key


ABSURD = 1e6  # a number outside its range by this much (or beyond float range) is "out of range" rather than "invalid"


def _as_float(value: Any, name: str) -> float:
    try:
        return float(value)
    except OverflowError:
        raise UnitsError("input.out_of_range", f"'{name}' is far outside the range of a double-precision number", {"param": name}) from None


def plain_number(value: Any, *, name: str, lo: float, hi: float, lo_open: bool = False, range_error: bool = False) -> float:
    """A JSON number in ``[lo, hi]``.  NaN, infinity and non-numbers are ``input.invalid``; outside the range is ``input.invalid`` for an
    ordinary wrong value and ``input.out_of_range`` for an absurd magnitude (above 1e6, or ``range_error``)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnitsError("input.invalid", f"'{name}' must be a plain number, got {str(value)[:40]!r}", {"param": name})
    number = _as_float(value, name)
    if not math.isfinite(number):
        raise UnitsError("input.invalid", f"'{name}' is not finite", {"param": name})
    ok = (number > lo if lo_open else number >= lo) and number <= hi
    if not ok:
        family = "input.out_of_range" if (range_error or abs(number) > ABSURD) else "input.invalid"
        raise UnitsError(family, f"'{name}' must be in {'(' if lo_open else '['}{lo:g}, {hi:g}], got {number:g}", {"param": name, "received": number, "min": lo, "max": hi})
    return number


def whole_number(value: Any, *, name: str, lo: int, hi: int, range_error: bool = True) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnitsError("input.invalid", f"'{name}' must be a whole number, got {str(value)[:40]!r}", {"param": name})
    number = _as_float(value, name)
    if not math.isfinite(number):
        raise UnitsError("input.invalid", f"'{name}' must be a whole number, got {str(value)[:40]!r}", {"param": name})
    if number != int(number):
        raise UnitsError("input.invalid", f"'{name}' must be a whole number, got {number:g}", {"param": name})
    n = int(value) if isinstance(value, int) else int(number)
    if not lo <= n <= hi:
        family = "input.out_of_range" if (range_error or abs(n) > ABSURD) else "input.invalid"
        raise UnitsError(family, f"'{name}' must be between {lo} and {hi}, got {str(n)[:30]}", {"param": name, "min": lo, "max": hi})
    return n


# ----------------------------------------------------------------------------------------------- frequencies with units

_FREQ_RE = re.compile(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(1/s|[^\d\s+\-.].*)?")
_TWO_PI = 2.0 * math.pi
# the whole table of accepted units; case matters only where it must (mHz is milli, MHz is mega)
_FREQ_UNITS = {
    "Hz": 1.0, "hz": 1.0, "HZ": 1.0, "hertz": 1.0, "Hertz": 1.0, "1/s": 1.0,
    "mHz": 1e-3, "kHz": 1e3, "khz": 1e3, "KHz": 1e3, "MHz": 1e6, "GHz": 1e9, "ghz": 1e9,
    "rad/s": 1.0 / _TWO_PI, "krad/s": 1e3 / _TWO_PI, "Mrad/s": 1e6 / _TWO_PI,
}
# units of other dimensions people type by mistake: said so instead of "unknown unit"
_OTHER_UNITS = {
    "m", "mm", "cm", "km", "um", "nm", "s", "ms", "us", "ns", "min", "h", "V", "mV", "A", "mA", "W", "kW", "kg", "g", "N", "Pa", "kPa", "bar",
    "psi", "K", "degC", "degF", "rad", "deg", "dB", "dBFS", "samples", "sample", "bit", "bits", "percent", "%",
}


def parse_frequency(raw: Any, *, name: str) -> float:
    """A frequency or rate with a unit as Hz.  Accepts a number, a space and one of Hz, kHz, MHz, GHz, mHz, rad/s, krad/s, Mrad/s."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise UnitsError("input.missing", f"'{name}' is required", {"param": name})
    if not isinstance(raw, str):
        raise UnitsError("units.parse_error", f"'{name}' must be a text such as '1 kHz', got {type(raw).__name__}", {"param": name})
    if len(raw) > 200:
        raise UnitsError("input.out_of_range", f"'{name}' is {len(raw)} characters long; expected a short quantity such as '1 kHz'", {"param": name})
    match = _FREQ_RE.fullmatch(raw.strip())
    if match is None:
        raise UnitsError("units.parse_error", f"'{name}' = {raw[:40]!r} is not a number followed by a unit (e.g. '48 kHz', '100 Hz', '628 rad/s')", {"param": name})
    number_text, unit = match.group(1), (match.group(2) or "").strip()
    if not unit:
        raise UnitsError(
            "units.bare_number", f"'{name}' was given as a bare number ({number_text}); a frequency needs a unit, e.g. '{number_text} Hz' or '{number_text} kHz'",
            {"param": name, "received": number_text},
        )
    number = float(number_text)
    if not math.isfinite(number):
        raise UnitsError("input.out_of_range", f"'{name}' is far outside the range of a double-precision number", {"param": name})
    if unit not in _FREQ_UNITS:
        if unit in _OTHER_UNITS or unit.lower() in {u.lower() for u in _OTHER_UNITS}:
            hint = " (a period is not a rate: give 1/T in Hz)" if unit in ("s", "ms", "us", "ns") else ""
            raise UnitsError("units.dimension_mismatch", f"'{name}' = {raw.strip()!r} has the unit {unit!r}, which is not a frequency; use Hz, kHz, MHz or rad/s{hint}", {"param": name, "received": raw.strip()[:40]})
        if unit == "mhz":
            raise UnitsError("units.parse_error", f"'{name}': 'mhz' is ambiguous (milli or mega); write 'mHz' or 'MHz'", {"param": name})
        raise UnitsError("units.parse_error", f"'{name}' = {raw.strip()[:40]!r}: unknown frequency unit {unit[:20]!r}; use Hz, kHz, MHz, GHz, mHz, rad/s, krad/s or Mrad/s", {"param": name})
    hertz = number * _FREQ_UNITS[unit]
    if not math.isfinite(hertz):
        raise UnitsError("input.out_of_range", f"'{name}' is far outside the range of a double-precision number", {"param": name})
    return hertz


def sample_rate_hz(raw: Any, *, name: str = "sample_rate") -> float:
    """A sample rate in Hz: positive, within 1e-3 .. 1e12 Hz."""
    rate = parse_frequency(raw, name=name)
    if rate <= 0.0:
        raise UnitsError("input.invalid", f"'{name}' must be positive, got {str(raw).strip()[:40]!r}", {"param": name})
    if not RATE_RANGE[0] <= rate <= RATE_RANGE[1]:
        raise UnitsError("input.out_of_range", f"'{name}' = {rate:g} Hz is outside the supported range {RATE_RANGE[0]:g} .. {RATE_RANGE[1]:g} Hz", {"param": name, "min": RATE_RANGE[0], "max": RATE_RANGE[1], "unit": "Hz"})
    return rate


def frequency_list(raw: Any, *, name: str, count: int, fs: float, allow_zero: bool = False) -> list[float]:
    """``count`` comma-separated frequencies in Hz, each strictly inside (0, fs/2), strictly increasing."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise UnitsError("input.missing", f"'{name}' is required", {"param": name})
    if not isinstance(raw, str):
        raise UnitsError("units.parse_error", f"'{name}' must be a text such as '1 kHz', got {type(raw).__name__}", {"param": name})
    parts = raw.split(",")
    if len(parts) != count:
        what = "one frequency" if count == 1 else f"{count} frequencies separated by a comma (low, high)"
        raise UnitsError("input.invalid", f"'{name}' needs {what} for this response, got {len(parts)}", {"param": name, "expected": count})
    values = [parse_frequency(p, name=name) for p in parts]
    nyquist = fs / 2.0
    for f in values:
        if f <= 0.0 and not allow_zero:
            raise UnitsError("input.invalid", f"'{name}' = {f:g} Hz must be above 0 Hz (an edge at 0 Hz has no meaning)", {"param": name, "received_hz": f})
        if f >= nyquist:
            raise UnitsError("input.invalid", f"'{name}' = {f:g} Hz is at or above the Nyquist frequency {nyquist:g} Hz (half the sample rate {fs:g} Hz)", {"param": name, "received_hz": f, "nyquist_hz": nyquist})
    for lo, hi in zip(values, values[1:]):
        if not lo < hi:
            raise UnitsError("input.invalid", f"'{name}' must list the lower frequency first and be strictly increasing, got {lo:g} Hz then {hi:g} Hz", {"param": name})
    return values


# ----------------------------------------------------------------------------------------------- samples


@dataclass
class Samples:
    x: np.ndarray
    fs: float | None  # from the time column of a two-column file, else None
    source: str
    t0: float | None = None


def record_text(text: Any, limit: int = 300) -> Any:
    """What goes into inputs.json for a long text: its length and sha256 (the inputs hash still identifies the data)."""
    if isinstance(text, str) and len(text) > limit:
        return f"<{len(text)} characters, sha256:{hashlib.sha256(text.encode('utf-8', 'replace')).hexdigest()}>"
    return text


def _bad_constant(token: str):
    raise UnitsError("input.invalid", f"the sample list contains {token}; NaN and infinity are not allowed", {"param": "samples"})


def array_from_json(text: Any, *, param: str, min_len: int = 1, samples: bool = False) -> np.ndarray:
    """A 1-D float array from JSON text such as '[0.1, 2, -3e-2]'; every kind of bad input is a clean ``input.*`` error."""
    if not isinstance(text, str):
        raise UnitsError("input.invalid", f"'{param}' must be JSON text such as '[0.1, 0.2]', got {type(text).__name__}", {"param": param})
    if len(text) > MAX_TEXT_CHARS:
        raise UnitsError("input.out_of_range", f"'{param}' is {len(text)} characters long, over the limit of {MAX_TEXT_CHARS}; at most {MAX_SAMPLES} samples are supported (give a file instead)", {"param": param, "limit_samples": MAX_SAMPLES})
    try:
        obj = jsonsafe.loads(text, parse_constant=_bad_constant)
    except ToolFailure as exc:
        raise UnitsError(exc.family, exc.message.replace("'samples'", f"'{param}'"), {"param": param}) from None
    except (ValueError, RecursionError) as exc:
        raise UnitsError("input.invalid", f"'{param}' is not valid JSON: {str(exc)[:80]}; expected a list of numbers such as '[0.1, 0.2]'", {"param": param}) from None
    if not isinstance(obj, list):
        raise UnitsError("input.invalid", f"'{param}' must be a JSON list of numbers, got {type(obj).__name__}", {"param": param})
    if len(obj) > MAX_SAMPLES:
        raise UnitsError("input.out_of_range", f"'{param}' has {len(obj)} values; at most {MAX_SAMPLES} (2**22) are supported", {"param": param, "received": len(obj), "limit_samples": MAX_SAMPLES})
    if len(obj) < min_len:
        raise UnitsError("input.invalid", f"'{param}' has {len(obj)} value(s); at least {min_len} are needed", {"param": param, "received": len(obj), "minimum": min_len})
    if not set(map(type, obj)) <= {int, float}:  # a bool, a string, null or a nested list is not a sample (numpy would turn True into 1)
        raise UnitsError("input.invalid", f"'{param}' must be a flat list of plain numbers (no text, booleans, nulls or nested lists)", {"param": param})
    try:
        arr = np.array(obj)
    except (ValueError, TypeError, OverflowError) as exc:
        raise UnitsError("input.invalid", f"'{param}' must be a flat list of numbers: {str(exc)[:80]}", {"param": param}) from None
    if arr.ndim != 1 or arr.dtype.kind not in "iuf":
        raise UnitsError("input.invalid", f"'{param}' must be a flat list of plain numbers (no text, booleans, nulls or nested lists)", {"param": param})
    return finite_array(arr.astype(float), param=param, samples=samples)


SAMPLE_ABS_MIN = 1e-100  # a record whose largest sample is below this (but not zero) has squares that underflow


def finite_array(arr: np.ndarray, *, param: str, samples: bool = False) -> np.ndarray:
    bad = ~np.isfinite(arr)
    if bad.any():
        raise UnitsError("input.invalid", f"'{param}' contains a value that is not finite (first at index {int(np.argmax(bad))})", {"param": param, "index": int(np.argmax(bad))})
    peak = float(np.max(np.abs(arr))) if arr.size else 0.0
    if peak > SAMPLE_ABS_MAX:
        raise UnitsError("input.out_of_range", f"'{param}' contains a value of magnitude above {SAMPLE_ABS_MAX:g}", {"param": param, "limit": SAMPLE_ABS_MAX})
    if samples and 0.0 < peak < SAMPLE_ABS_MIN:
        raise UnitsError("input.out_of_range", f"'{param}' has a largest magnitude of {peak:g}, below {SAMPLE_ABS_MIN:g}: the squares used for power and energy would underflow; rescale the data (the tools are linear)", {"param": param, "limit": SAMPLE_ABS_MIN})
    return arr


_NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


def parse_csv_text(text: str, *, param: str) -> list[np.ndarray]:
    """Columns of numbers from CSV text: one or two columns, comma or whitespace separated, blank lines ignored.

    Any other content (a header, a word, a third column, nan, inf, a hexadecimal or underscore number) is ``input.invalid`` with the line number.
    """
    if "\x00" in text:
        raise UnitsError("input.invalid", f"{param}: the file is not text (it contains a NUL byte)", {"param": param})
    if text.count("\n") > MAX_SAMPLES + 1000:
        raise UnitsError("input.out_of_range", f"{param}: more than {MAX_SAMPLES} (2**22) lines", {"param": param, "limit_samples": MAX_SAMPLES})
    columns: list[list[float]] | None = None
    ncols = 0
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        parts = [p.strip() for p in stripped.split(",")] if "," in stripped else stripped.split()
        if columns is None:
            ncols = len(parts)
            if ncols not in (1, 2):
                raise UnitsError("input.invalid", f"{param} line {number}: {ncols} columns; use one column of values or two columns time,value", {"param": param, "line": number})
            columns = [[] for _ in range(ncols)]
        elif len(parts) != ncols:
            raise UnitsError("input.invalid", f"{param} line {number}: {len(parts)} column(s), the first line had {ncols}", {"param": param, "line": number})
        for index, token in enumerate(parts):
            if _NUMBER.fullmatch(token) is None:
                raise UnitsError("input.invalid", f"{param} line {number}: {token[:30]!r} is not a number (the file must hold numbers only, no header, no text, no nan/inf)", {"param": param, "line": number})
            value = float(token)
            if not math.isfinite(value):
                raise UnitsError("input.invalid", f"{param} line {number}: {token[:30]!r} is not finite", {"param": param, "line": number})
            columns[index].append(value)
        if len(columns[0]) > MAX_SAMPLES:
            raise UnitsError("input.out_of_range", f"{param}: more than {MAX_SAMPLES} (2**22) samples", {"param": param, "limit_samples": MAX_SAMPLES})
    if columns is None or not columns[0]:
        raise UnitsError("input.invalid", f"{param}: the file holds no numbers", {"param": param})
    return [np.array(c, dtype=float) for c in columns]


def read_samples(handle: JobHandle, inline: Any, file: Any, *, param: str = "samples", file_param: str = "samples_file", min_len: int = 1) -> Samples:
    """Samples from the inline JSON list or from a CSV file read through ``JobHandle.input_file`` (the caller's path is never opened directly)."""
    has_inline, has_file = not is_blank(inline), not is_blank(file)
    if has_inline and has_file:
        raise UnitsError("input.invalid", f"give either '{param}' or '{file_param}', not both", {"param": param})
    if not has_inline and not has_file:
        raise UnitsError("input.missing", f"'{param}' (a JSON list of numbers) or '{file_param}' (a CSV file) is required", {"param": param})
    if has_inline:
        return Samples(array_from_json(inline, param=param, min_len=min_len, samples=True), None, "inline")
    if not isinstance(file, str):
        raise UnitsError("input.invalid", f"'{file_param}' must be a path as text", {"param": file_param})
    path = handle.input_file(file, param=file_param, extensions=(".csv", ".txt"), max_bytes=MAX_FILE_BYTES)
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UnitsError("input.invalid", f"{file_param}: the file is not UTF-8 text", {"param": file_param}) from None
    cols = parse_csv_text(text, param=file_param)
    if len(cols[0]) < min_len:
        raise UnitsError("input.invalid", f"{file_param} has {len(cols[0])} value(s); at least {min_len} are needed", {"param": file_param})
    if len(cols) == 1:
        return Samples(finite_array(cols[0], param=file_param, samples=True), None, "file")
    t, x = cols
    finite_array(t, param=file_param)
    if len(t) < 2:
        raise UnitsError("input.invalid", f"{file_param}: a time column needs at least two rows", {"param": file_param})
    dt = np.diff(t)
    if not np.all(dt > 0):
        raise UnitsError("input.invalid", f"{file_param}: the time column is not strictly increasing (first problem at row {int(np.argmax(dt <= 0)) + 2})", {"param": file_param})
    mean_dt = float((t[-1] - t[0]) / (len(t) - 1))
    if float(np.max(np.abs(dt - mean_dt))) > 1e-3 * mean_dt:
        raise UnitsError("input.invalid", f"{file_param}: the time column is not uniformly sampled (steps differ by more than 0.1 %); resample to a uniform grid first", {"param": file_param})
    fs = 1.0 / mean_dt
    if not RATE_RANGE[0] <= fs <= RATE_RANGE[1]:
        raise UnitsError("input.out_of_range", f"{file_param}: the time column implies a sample rate of {fs:g} Hz, outside {RATE_RANGE[0]:g} .. {RATE_RANGE[1]:g} Hz", {"param": file_param})
    return Samples(finite_array(x, param=file_param, samples=True), fs, "file (time,value; time read as seconds)", float(t[0]))


def resolve_rate(samples: Samples, raw_rate: Any, *, required: bool = True, name: str = "sample_rate") -> float | None:
    """The sample rate in Hz from the parameter and/or the file's time column (they must agree to 0.1 %)."""
    given = None if is_blank(raw_rate) else sample_rate_hz(raw_rate, name=name)
    if samples.fs is not None and given is not None and abs(given - samples.fs) > 1e-3 * samples.fs:
        raise UnitsError("input.invalid", f"'{name}' = {given:g} Hz disagrees with the {samples.fs:g} Hz implied by the file's time column; give one of them", {"param": name})
    rate = given if given is not None else samples.fs
    if rate is None and required:
        raise UnitsError("input.missing", f"'{name}' is required (a one-column file or an inline list carries no time base)", {"param": name})
    return rate


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
        return {"real": _finite(obj.real, path) + 0.0, "imag": _finite(obj.imag, path) + 0.0}
    if isinstance(obj, dict):
        return {str(k): clean(v, f"{path}.{k}") for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, (list, tuple)):
        return [clean(v, f"{path}[{i}]") for i, v in enumerate(obj)]
    return str(obj)


def csv_text(header: list[str], columns: list[Any]) -> str:
    """CSV text from equally long columns, every number as its shortest round-trip repr (no digits lost), formatted in chunks so a 4-million-row table stays cheap."""
    template = ",".join(["{!r}"] * len(columns))
    lists = [np.asarray(c, dtype=float) for c in columns]
    if lists and not all(np.all(np.isfinite(c)) for c in lists):
        raise UnitsError("input.out_of_range", "a table column holds a value that is not finite (overflow); the inputs are outside what the tool supports", {})
    n = len(lists[0]) if lists else 0
    out = [",".join(header)]
    step = 200_000
    for start in range(0, n, step):
        chunk = [c[start:start + step].tolist() for c in lists]
        out.append("\n".join(map(template.format, *chunk)))
    return "\n".join(out) + "\n"


def pointer_details(details: dict[str, Any] | None) -> dict[str, Any]:
    """``details`` with ``param`` as a JSON pointer (``/order``); a failure that names no parameter gets ``/inputs``."""
    out = dict(details or {})
    name = out.get("param")
    out["param"] = "/" + name.lstrip("/") if isinstance(name, str) and name.strip("/") else "/inputs"
    return out


def run(tool_name: str, inputs: dict[str, Any], compute) -> ToolResult:
    """Run a tool body under the runner.  Every refusal carries ``details.param`` as a JSON pointer, and a numerical overflow or singularity
    inside the computation is an ``input.out_of_range`` refusal (the inputs are beyond what double precision supports), never ``engine.failure``."""

    def guarded(handle: JobHandle) -> Computed:
        try:
            return compute(handle)
        except ToolFailure as exc:
            raise ToolFailure(exc.family, exc.message, pointer_details(exc.details)) from None
        except (ArithmeticError, np.linalg.LinAlgError) as exc:
            raise ToolFailure(
                "input.out_of_range", f"the computation overflowed or became singular ({type(exc).__name__}) for these inputs: they are outside what double precision supports", {"param": "/inputs"}
            ) from None

    return run_tool(tool_name=tool_name, engine=ENGINE, version=__version__, inputs=inputs, compute=guarded, engines=("numpy",), standing_warnings=STANDING)


def db(value: float) -> float:
    """20 log10 of a magnitude, floored so an exact zero is -300 dB, never -infinity."""
    return 20.0 * math.log10(max(abs(value), LOG_FLOOR))


__all__ = [
    "Computed", "JobHandle", "MAX_IIR_ORDER", "MAX_SAMPLES", "MAX_TABLE_ROWS", "MAX_TAPS", "QuantityOut", "Samples", "STANDING",
    "array_from_json", "choice", "clean", "csv_text", "db", "finite_array", "frequency_list", "is_blank", "parse_csv_text", "parse_frequency",
    "plain", "plain_number", "qty", "read_samples", "record_text", "resolve_rate", "run", "sample_rate_hz", "whole_number",
]
