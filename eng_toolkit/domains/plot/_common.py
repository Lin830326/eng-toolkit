"""Caps, parameter checks, text cleaning and the run wrapper shared by the ``plot`` tools.

Error families: ``input.invalid`` for a value that is not allowed (an unknown choice, a non-numeric cell, a ragged row, a
non-positive value on a log axis, min >= max), ``input.out_of_range`` for a value beyond a stated cap or an absurd magnitude
(more than 20 series, 1e308, a width of 100000 px, a 6000-character label), ``input.missing`` for absent data, and the file
families of :meth:`JobHandle.input_file`.  Every refusal carries ``details.param`` as a JSON pointer (``/width``,
``/series/2/y``, ``/data_file/14/V_N``).  Nothing the caller sends is executed: numbers go through one anchored, non-backtracking
regular expression and ``float``, JSON through ``json.loads`` (which builds data only).  Every caller string that reaches the
SVG, the result or ``plot_data.json`` goes through :func:`clean_text` (and :func:`scrub` as the last guard).
"""

from __future__ import annotations

import contextvars
import hashlib
import math
import re
from typing import Any

import numpy as np

from eng_toolkit import __version__
from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.plot._server import ENGINE
from eng_toolkit.errors import ToolFailure
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import UnitsError

# ----------------------------------------------------------------------------------------------- caps (all enforced)
MAX_SERIES = 20
MAX_POINTS = 200_000  # per series
MAX_LABEL = 120  # characters in a title, axis label, series name or reference-line label
MAX_NAME_PARAM = 4000  # characters in a column list
MAX_RECORDS_TEXT = 4000  # characters in hlines / vlines / band / panels / bin_edges
MAX_PATH = 1000
MAX_REF_LINES = 10
MAX_COLUMNS = 256
MAX_HEADER_CHARS = 200
MAX_CELL_CHARS = 64  # a numeric cell longer than this is not a plain number
MAX_CSV_BYTES = 200_000_000  # 20 columns x 200000 rows of full-precision numbers are about 130 MB
MAX_BLANK_LINES = 10_000  # blank lines in a CSV file (a flood of them is refused while reading)
MAX_SERIES_TEXT = 200_000_000
VALUE_ABS_MAX = 1e100
VALUE_ABS_MIN = 1e-100  # an axis whose largest magnitude is non-zero but below this is refused (rescale)
SIZE_RANGE = (200, 2400)
INLINE_SVG_MAX = 150_000
MAX_BAR_SERIES = 6
MAX_CATEGORIES = 200
BINS_RANGE = (2, 200)
PANELS_RANGE = (2, 6)
DECIMATE_PER_PX = 4  # more points than this per horizontal pixel are decimated

STANDING = (
    "Chart rendering only: the SVG draws the numbers it was given and checks nothing about them; read values from the "
    "tables, not from the picture. Unreviewed.",
)


# ----------------------------------------------------------------------------------------------- text

# Whitespace controls and line/paragraph separators become a space; other C0/C1 controls, lone surrogates, U+FFFE/U+FFFF (illegal
# in XML 1.0, and a lone surrogate cannot even be written as UTF-8) and bidi overrides become U+FFFD.  One regular expression, so a
# 2 MB attribute value costs one C-level scan.
_SPACES = "\t\n\r\x0b\x0c  "
_BAD = re.compile("[\t\n\r\x0b\x0c  \x00-\x08\x0e-\x1f\x7f-\x9f\ud800-\udfff￾￿؜‎‏‪-‮⁦-⁩]")
_REPLACED: contextvars.ContextVar[set[str] | None] = contextvars.ContextVar("plot_replaced_text", default=None)


def _sub(match: re.Match) -> str:
    return " " if match.group(0) in _SPACES else "�"


def scrub_text(text: str) -> str:
    """``text`` cleaned without counting (messages, internal strings)."""
    return _BAD.sub(_sub, text) if _BAD.search(text) else text


def clean_text(text: str) -> tuple[str, bool]:
    """Caller text cleaned for the SVG and the result: (text, whether anything was replaced).

    Every distinct string that needed a replacement is counted for the current call; the tool reports the count in one warning."""
    if not _BAD.search(text):
        return text, False
    cleaned = _BAD.sub(_sub, text)
    seen = _REPLACED.get()
    if seen is not None:
        seen.add(cleaned)
    return cleaned, True


def scrub(obj: Any) -> Any:
    """Every string in a JSON-like structure cleaned (and counted): the last guard before data, plot_data.json and warnings are written."""
    if isinstance(obj, str):
        return clean_text(obj)[0]
    if isinstance(obj, dict):
        return {(scrub(k) if isinstance(k, str) else k): scrub(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub(v) for v in obj]
    return obj


def replaced_text_warning() -> list[str]:
    seen = _REPLACED.get()
    if seen:
        return [f"{len(seen)} text value(s) (labels, names, headers or categories) contained control, surrogate, line-separator or direction-override "
                "characters; they were replaced (U+FFFD, or a space for whitespace controls)"]
    return []


# ----------------------------------------------------------------------------------------------- refusals


def pointer_token(text: str) -> str:
    """One JSON-pointer reference token (RFC 6901: '~' -> '~0', '/' -> '~1'), cleaned and at most 120 characters."""
    return scrub_text(text)[:MAX_LABEL].replace("~", "~0").replace("/", "~1")


def _scrub_quiet(obj: Any) -> Any:
    if isinstance(obj, str):
        return scrub_text(obj)
    if isinstance(obj, dict):
        return {k: _scrub_quiet(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub_quiet(v) for v in obj]
    return obj


def fail(param: str, message: str, family: str = "input.invalid", **details: Any) -> None:
    """Refuse.  Message and details are scrubbed, so no lone surrogate or control character reaches the result files."""
    raise UnitsError(family, scrub_text(message), _scrub_quiet({"param": "/" + scrub_text(param).lstrip("/"), **details}))


def short(value: Any, n: int = 40) -> str:
    """A repr of at most ``n`` characters of a caller's value, for messages (never the whole 6000-character string)."""
    text = value if isinstance(value, str) else str(value)
    return repr(scrub_text(text[:n])) + ("..." if len(text) > n else "")


def text_param(raw: Any, name: str, *, cap: int = MAX_LABEL, notes: list[str] | None = None) -> str:
    """A plain-text label: a string of at most ``cap`` characters (longer is ``input.out_of_range``), cleaned."""
    if raw is None:
        return ""
    if not isinstance(raw, str):
        fail(name, f"'{name}' must be text")
    if len(raw) > cap:
        fail(name, f"'{name}' is {len(raw)} characters long; at most {cap} are allowed", "input.out_of_range", limit=cap)
    return clean_text(raw.strip())[0]


def choice(raw: Any, name: str, allowed: tuple[str, ...]) -> str:
    key = raw.strip().lower().replace("-", "_").replace(" ", "_") if isinstance(raw, str) and len(raw) <= 40 else None
    if key not in allowed:
        fail(name, f"'{name}' must be one of {list(allowed)}, got {short(raw)}", allowed=list(allowed))
    return key


def flag(raw: Any, name: str) -> bool:
    if not isinstance(raw, bool):
        fail(name, f"'{name}' must be true or false")
    return raw


def whole(raw: Any, name: str, lo: int, hi: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        fail(name, f"'{name}' must be a whole number, got {short(raw)}")
    try:
        number = float(raw)
    except OverflowError:
        fail(name, f"'{name}' is far outside the range of a double-precision number", "input.out_of_range")
    if not math.isfinite(number) or number != int(number):
        fail(name, f"'{name}' must be a whole number, got {short(raw)}")
    n = int(number)
    if not lo <= n <= hi:
        fail(name, f"'{name}' must be between {lo} and {hi}, got {short(n, 30)}", "input.out_of_range", min=lo, max=hi)
    return n


# ----------------------------------------------------------------------------------------------- numbers in text

# ASCII digits only (no fullwidth digits, no underscores, no hex, no nan/inf); the alternatives do not overlap, so a failed
# match costs time linear in the token, and tokens are capped at MAX_CELL_CHARS before matching anyway.
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def number_token(token: str, param: str, what: str) -> float:
    """One strict decimal number from text; NaN, inf and non-numbers are ``input.invalid``, |x| > 1e100 is ``input.out_of_range``."""
    t = token.strip()
    if len(t) > MAX_CELL_CHARS or NUMBER.fullmatch(t) is None:
        lead = NUMBER.match(t) if len(t) <= MAX_CELL_CHARS else None
        unit_hint = (
            f" It looks like a number followed by the unit text {short(t[lead.end():].strip(), 12)}: this value is a plain number in the unit of the axis "
            "(for example '0,3600' for seconds); write the unit in the axis label instead"
            if lead is not None and t[lead.end():].strip().isalpha() else ""
        )
        fail(param, f"{what}: {short(t, 30)} is not a plain decimal number (digits, optional sign, point and exponent; no nan, inf, hex or underscores).{unit_hint}")
    value = float(t)
    if not math.isfinite(value) or abs(value) > VALUE_ABS_MAX:
        fail(param, f"{what}: {short(t, 30)} is beyond the supported magnitude {VALUE_ABS_MAX:g}", "input.out_of_range", limit=VALUE_ABS_MAX)
    return value


def range_param(raw: Any, name: str) -> tuple[float, float] | None:
    """``"min,max"`` as two numbers with min < max, or None for blank (automatic)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if not isinstance(raw, str):
        fail(name, f"'{name}' must be text 'min,max'")
    if len(raw) > 200:
        fail(name, f"'{name}' is {len(raw)} characters long; expected 'min,max'", "input.out_of_range")
    parts = raw.split(",")
    if len(parts) != 2:
        fail(name, f"'{name}' must be two numbers 'min,max', got {short(raw)}")
    lo, hi = (number_token(p, name, f"'{name}'") for p in parts)
    if not lo < hi:
        fail(name, f"'{name}': min must be below max, got {lo:g},{hi:g}")
    return lo, hi


def split_records(raw: Any, name: str, cap: int) -> list[str]:
    """Records separated by ';' (or new lines), blank records skipped; at most ``cap`` records and MAX_RECORDS_TEXT characters."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return []
    if not isinstance(raw, str):
        fail(name, f"'{name}' must be text")
    if len(raw) > MAX_RECORDS_TEXT:
        fail(name, f"'{name}' is {len(raw)} characters long; at most {MAX_RECORDS_TEXT}", "input.out_of_range", limit=MAX_RECORDS_TEXT)
    records = [r for r in raw.replace("\r\n", "\n").replace("\n", ";").split(";") if r.strip()]
    if len(records) > cap:
        fail(name, f"'{name}' has {len(records)} records; at most {cap}", "input.out_of_range", limit=cap)
    return records


def ref_lines(raw: Any, name: str, notes: list[str]) -> list[tuple[float, str]]:
    """``value,label`` records (label optional, may contain commas)."""
    out = []
    for i, rec in enumerate(split_records(raw, name, MAX_REF_LINES)):
        value_text, _, label = rec.partition(",")
        value = number_token(value_text, f"{name}/{i}", f"'{name}' record {i + 1}")
        out.append((value, text_param(label, f"{name}/{i}", notes=notes)))
    return out


def band_param(raw: Any, name: str, notes: list[str]) -> tuple[float, float, str] | None:
    records = split_records(raw, name, 1)
    if not records:
        return None
    parts = records[0].split(",", 2)
    if len(parts) < 2:
        fail(name, f"'{name}' must be 'lo,hi,label', got {short(records[0])}")
    lo = number_token(parts[0], name, f"'{name}' lo")
    hi = number_token(parts[1], name, f"'{name}' hi")
    if not lo < hi:
        fail(name, f"'{name}': lo must be below hi, got {lo:g},{hi:g}")
    return lo, hi, text_param(parts[2] if len(parts) == 3 else "", name, notes=notes)


def point_param(raw: Any, name: str, notes: list[str]) -> tuple[float, float, str] | None:
    """One marked point ``x,y,label`` (label optional, may contain commas); None when blank."""
    records = split_records(raw, name, 1)
    if not records:
        return None
    parts = records[0].split(",", 2)
    if len(parts) < 2:
        fail(name, f"'{name}' must be 'x,y,label' (the label is optional), got {short(records[0])}")
    x = number_token(parts[0], name, f"'{name}' x")
    y = number_token(parts[1], name, f"'{name}' y")
    return x, y, text_param(parts[2] if len(parts) == 3 else "", name, notes=notes)


def column_param(raw: Any, name: str) -> str:
    """One column name (blank = default)."""
    if raw is None:
        return ""
    if not isinstance(raw, str):
        fail(name, f"'{name}' must be text")
    if len(raw) > MAX_HEADER_CHARS:
        fail(name, f"'{name}' is {len(raw)} characters long; a column name has at most {MAX_HEADER_CHARS}", "input.out_of_range", limit=MAX_HEADER_CHARS)
    return raw.strip()


def name_list(raw: Any, name: str) -> list[str]:
    """Comma-separated column or series names (blank entries ignored)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return []
    if not isinstance(raw, str):
        fail(name, f"'{name}' must be text")
    if len(raw) > MAX_NAME_PARAM:
        fail(name, f"'{name}' is {len(raw)} characters long; at most {MAX_NAME_PARAM}", "input.out_of_range", limit=MAX_NAME_PARAM)
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if len(names) > MAX_SERIES:
        fail(name, f"'{name}' names {len(names)} columns; at most {MAX_SERIES} series can be plotted", "input.out_of_range", limit=MAX_SERIES)
    if len(set(names)) != len(names):
        fail(name, f"'{name}' names a column twice")
    return names


# ----------------------------------------------------------------------------------------------- outputs


def record_text(text: Any, limit: int = 300) -> Any:
    """What goes into inputs.json for a long text: its length and sha256 (the inputs hash still identifies the data)."""
    if isinstance(text, str) and len(text) > limit:
        return f"<{len(text)} characters, sha256:{hashlib.sha256(text.encode('utf-8', 'surrogatepass')).hexdigest()}>"
    return text


def clean(obj: Any, path: str = "data") -> Any:
    """Plain JSON data: numpy scalars and arrays converted, strings cleaned, and a NaN or infinity refused (never reaches a result)."""
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, np.ndarray):
        obj = obj.tolist()
    if isinstance(obj, str):
        return clean_text(obj)[0]
    if isinstance(obj, bool) or obj is None or isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            fail("inputs", f"the result '{scrub_text(path)}' is not finite for these inputs", "input.out_of_range")
        return obj + 0.0
    if isinstance(obj, dict):
        return {clean_text(str(k))[0]: clean(v, f"{path}.{k}") for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v, f"{path}[{i}]") for i, v in enumerate(obj)]
    return clean_text(str(obj))[0]


def pointer_details(details: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(details or {})
    name = out.get("param")
    out["param"] = "/" + name.lstrip("/") if isinstance(name, str) and name.strip("/") else "/inputs"
    return out


def run(tool_name: str, inputs: dict[str, Any], compute, *, engines: tuple[str, ...] = ()) -> ToolResult:
    """Run a tool body under the runner: refusals get JSON-pointer params, numerical trouble is ``input.out_of_range``."""

    def guarded(handle: JobHandle) -> Computed:
        token = _REPLACED.set(set())
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                return compute(handle)
        except ToolFailure as exc:
            raise ToolFailure(exc.family, scrub_text(exc.message), _scrub_quiet(pointer_details(exc.details))) from None
        except ArithmeticError as exc:
            raise ToolFailure(
                "input.out_of_range", f"the numbers overflowed while the chart was laid out ({type(exc).__name__}); the inputs are outside what the tool supports", {"param": "/inputs"}
            ) from None
        finally:
            _REPLACED.reset(token)

    return run_tool(tool_name=tool_name, engine=ENGINE, version=__version__, inputs=inputs, compute=guarded, engines=("numpy", *engines), standing_warnings=STANDING)


__all__ = [
    "BINS_RANGE", "Computed", "DECIMATE_PER_PX", "INLINE_SVG_MAX", "JobHandle", "MAX_BAR_SERIES", "MAX_BLANK_LINES", "MAX_CATEGORIES", "MAX_CELL_CHARS",
    "MAX_COLUMNS", "MAX_CSV_BYTES", "MAX_HEADER_CHARS", "MAX_LABEL", "MAX_PATH", "MAX_POINTS", "MAX_REF_LINES", "MAX_SERIES",
    "MAX_SERIES_TEXT", "NUMBER", "PANELS_RANGE", "SIZE_RANGE", "STANDING", "VALUE_ABS_MAX", "VALUE_ABS_MIN", "band_param", "choice",
    "clean", "clean_text", "column_param", "fail", "flag", "name_list", "number_token", "point_param", "pointer_token", "range_param", "record_text", "ref_lines",
    "replaced_text_warning", "run", "scrub", "scrub_text", "short", "split_records", "text_param", "whole",
]
