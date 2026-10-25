"""Reading the data to plot: a CSV file with a header row (through ``JobHandle.input_file``) or inline JSON series.

CSV rules: UTF-8 (a BOM is skipped), the first non-blank line is the header (names, at most 256 columns of at most 200 characters),
comma-separated (or tab-separated, or whitespace-separated when the header has neither), standard CSV quoting, blank lines skipped
(at most 10 000), every row exactly as many cells as the header (a ragged row is ``input.invalid`` at ``/data_file/<line>``), at
most 200 000 data rows and 200 MB.  The file is streamed: decoded incrementally, split into records only at ``\\n``, ``\\r\\n`` and
``\\r`` (a U+2028 or form feed inside a cell stays in the cell and is then cleaned), the caps are enforced while reading, and only the
cells of the selected columns are kept.  Only the selected columns must be numeric: a text column (the ``side`` column of a frame
station table) is fine as long as it is not plotted.  A numeric cell is a plain ASCII decimal (``-1.5e3``, spaces and tabs around it
allowed); ``nan``, ``inf``, ``1_000``, ``0x10``, fullwidth digits, an empty cell and anything above 1e100 in magnitude are refused
at ``/data_file/<line>/<column>``.
"""

from __future__ import annotations

from eng_toolkit import jsonsafe
import csv
import io
import itertools
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import numpy as np

from eng_toolkit.domains.plot._common import (
    MAX_BLANK_LINES,
    MAX_CELL_CHARS,
    MAX_COLUMNS,
    MAX_CSV_BYTES,
    MAX_HEADER_CHARS,
    MAX_LABEL,
    MAX_PATH,
    MAX_POINTS,
    MAX_SERIES,
    MAX_SERIES_TEXT,
    NUMBER,
    VALUE_ABS_MAX,
    JobHandle,
    clean_text,
    fail,
    pointer_token,
    short,
)
from eng_toolkit.errors import ToolFailure


@dataclass
class Series:
    name: str  # display name (cleaned; escaped when written)
    x: np.ndarray
    y: np.ndarray
    x_param: str  # JSON pointer of the x data, for refusals (/data_file or /series/0/x)
    y_param: str
    x_name: str  # the x column name (cleaned), or "index" when an inline series has no x
    axis: str = "y"  # "y" (left) or "y2" (right)
    err: np.ndarray | None = None
    labels: list[str] | None = None  # category labels (bar charts), cleaned
    column: str = ""  # the CSV column or inline series name as given (for matching names only; never written out raw)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return display(self.column)


def is_blank(raw: Any) -> bool:
    return raw is None or (isinstance(raw, str) and not raw.strip())


def display(name: str) -> str:
    return clean_text(name)[0]


def cell_pointer(line: int, column: str) -> str:
    return f"data_file/{line}/{pointer_token(column)}"


# ----------------------------------------------------------------------------------------------- CSV

_WS = re.compile(r"[ \t]+")
_LINE_END = "\r\n"


class CsvTable:
    """A CSV file streamed record by record; the header is read first, the selected columns in one later pass."""

    def __init__(self, handle: JobHandle, raw: Any, param: str = "data_file"):
        if not isinstance(raw, str):
            fail(param, f"'{param}' must be a path as text")
        if len(raw) > MAX_PATH:
            fail(param, f"'{param}' is {len(raw)} characters long; a path of at most {MAX_PATH} is expected", "input.out_of_range")
        self.param = param
        self._path = handle.input_file(raw.strip(), param=param, extensions=(".csv", ".txt"), max_bytes=MAX_CSV_BYTES)
        self._mode = ","
        records = self._records()
        try:
            first = next(records, None)
        finally:
            records.close()
        if first is None:
            fail(param, f"{param}: the file is empty (a header row and data rows are needed)")
        _, header = first
        if len(header) > MAX_COLUMNS:
            fail(param, f"{param}: {len(header)} columns; at most {MAX_COLUMNS}", "input.out_of_range", limit=MAX_COLUMNS)
        for index, name in enumerate(header):
            if len(name) > MAX_HEADER_CHARS:
                fail(param, f"{param}: header cell {index + 1} is {len(name)} characters long; at most {MAX_HEADER_CHARS}", "input.out_of_range", column=index + 1)
        self.header = [h.strip() for h in header]

    def _records(self):
        """(line number, cells) of every non-blank record, header first.  Line numbers are physical lines (\\n, \\r\\n or \\r)."""
        try:
            with self._path.open("rb") as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
                number, blank = 0, 0

                def lines():
                    nonlocal number
                    for line in text:
                        number += 1
                        if "\x00" in line:
                            fail(self.param, f"{self.param} line {number}: the file is not text (it contains a NUL byte)", line=number)
                        yield line

                def blank_line():
                    nonlocal blank
                    blank += 1
                    if blank > MAX_BLANK_LINES:
                        fail(self.param, f"{self.param}: more than {MAX_BLANK_LINES} blank lines", "input.out_of_range", limit=MAX_BLANK_LINES)

                source = lines()
                head = None
                for line in source:
                    if line.strip(" \t" + _LINE_END):
                        head = line
                        break
                    blank_line()
                if head is None:
                    return
                if "," in head:
                    self._mode = ","
                elif "\t" in head:
                    self._mode = "\t"
                else:  # several space-separated names: whitespace columns; one name: a one-column CSV (quotes honoured)
                    self._mode = " " if len(_WS.split(head.strip(" \t" + _LINE_END))) > 1 and '"' not in head else ","
                if self._mode == " ":
                    for line in itertools.chain([head], source):
                        body = line.strip(" \t" + _LINE_END)
                        if not body:
                            blank_line()
                            continue
                        yield number, _WS.split(body)
                    return
                offset = number - 1
                reader = csv.reader(itertools.chain([head], source), delimiter=self._mode, strict=True)
                try:
                    for cells in reader:
                        if not cells or (len(cells) == 1 and not cells[0].strip(" \t")):
                            blank_line()
                            continue
                        yield reader.line_num + offset, cells
                except csv.Error as exc:
                    fail(self.param, f"{self.param} line {reader.line_num + offset}: not valid CSV ({str(exc)[:60]})", line=reader.line_num + offset)
        except UnicodeDecodeError:
            fail(self.param, f"{self.param}: the file is not UTF-8 text")

    def index(self, name: str, param: str) -> int:
        hits = [i for i, h in enumerate(self.header) if h == name]
        if not hits:
            listed = ", ".join(repr(display(h[:30])) for h in self.header[:12]) + (" ..." if len(self.header) > 12 else "")
            fail(param, f"'{param}': the file has no column {short(name)}; its columns are {listed}", column=name[:MAX_LABEL])
        if len(hits) > 1:
            fail(param, f"'{param}': the file has {len(hits)} columns named {short(name)}; rename them")
        return hits[0]

    def extract(self, indices: list[int]) -> tuple[dict[int, list[str]], list[int]]:
        """The cells of the given columns and the line number of every data row; checks every row's width and the row cap."""
        width = len(self.header)
        cols: dict[int, list[str]] = {i: [] for i in indices}
        lines: list[int] = []
        records = self._records()
        try:
            next(records, None)  # the header
            for number, cells in records:
                if len(cells) != width:
                    fail(f"{self.param}/{number}", f"{self.param} line {number}: {len(cells)} cells, the header has {width} (ragged row)", line=number)
                if len(lines) >= MAX_POINTS:
                    fail(self.param, f"{self.param}: more than {MAX_POINTS} data rows; at most {MAX_POINTS} points per series", "input.out_of_range", limit=MAX_POINTS)
                lines.append(number)
                for i in indices:
                    cols[i].append(cells[i])
        finally:
            records.close()
        if not lines:
            fail(self.param, f"{self.param}: the file has a header but no data rows")
        return cols, lines


def _plain(cell: str) -> str:
    return cell.strip(" \t")


def numeric_cells(cells: list[str], lines: list[int], column: str, *, exact: bool = False) -> np.ndarray:
    """Strict decimal numbers from the cells of one column (a refusal points at /data_file/<line>/<column>)."""
    out = np.empty(len(cells), dtype=object if exact else float)
    match = NUMBER.fullmatch
    for i, cell in enumerate(cells):
        t = _plain(cell)
        if len(t) > MAX_CELL_CHARS or match(t) is None:
            fail(cell_pointer(lines[i], column), f"data_file line {lines[i]}, column {short(column, 30)}: {short(t, 30)} is not a plain decimal number (no text, empty cells, nan, inf, hex or underscores)",
                 line=lines[i], column=display(column[:MAX_LABEL]), reason="not a number")
        value = float(t)
        if not abs(value) <= VALUE_ABS_MAX:  # also catches inf from 1e999
            fail(cell_pointer(lines[i], column), f"data_file line {lines[i]}, column {short(column, 30)}: {short(t, 30)} is beyond the supported magnitude {VALUE_ABS_MAX:g}",
                 "input.out_of_range", line=lines[i], column=display(column[:MAX_LABEL]), limit=VALUE_ABS_MAX, reason="out of range")
        out[i] = Decimal(t) if exact else value
    return out


def is_text_column(cells: list[str]) -> bool:
    """True when no cell of the column is a number at all (a text or empty column); one numeric cell makes it a numeric column."""
    match = NUMBER.fullmatch
    return not any(len(t) <= MAX_CELL_CHARS and match(t) for t in map(_plain, cells))


def csv_series(
    handle: JobHandle, data_file: Any, x_column: str, wanted: list[tuple[str, str]] | None, notes: list[str], *,
    extra: list[tuple[str, str]] = (), text_x: bool = False, err: list[tuple[str, str]] | None = None,
) -> tuple[list[Series], CsvTable]:
    """Series from CSV columns.  ``wanted`` = [(column name, the parameter that named it)]; None = every other column, where a column
    without a single numeric cell is skipped as a text column (said so) and a column with some bad cells is refused at the bad cell;
    ``extra`` columns are added (and must be numeric) in both cases.  ``text_x`` keeps the x column as category labels."""
    table = CsvTable(handle, data_file)
    x_name = x_column or table.header[0]
    xi = table.index(x_name, "x_column")
    taken = {n for n, _ in extra}
    if wanted is None:
        names = [h for i, h in enumerate(table.header) if i != xi and h not in taken]
        if not names and not extra:
            fail("y_columns", "the file has no column to plot besides the x column")
        if len([nm for nm in names if nm]) != len(names):
            fail("y_columns", "the file has a column without a header name; name the columns to plot in 'y_columns'")
        if len(names) + len(extra) > MAX_SERIES:
            fail("y_columns", f"the file has {len(names) + len(extra)} columns besides x; at most {MAX_SERIES} series: name the ones to plot in 'y_columns'", "input.out_of_range", limit=MAX_SERIES)
        picks = [(nm, table.index(nm, "y_columns"), "y_columns", False) for nm in names]
    else:
        picks = [(nm, table.index(nm, p), p, True) for nm, p in wanted]
    picks += [(nm, table.index(nm, p), p, True) for nm, p in extra]
    if len(picks) > MAX_SERIES:
        fail("y_columns", f"{len(picks)} columns selected; at most {MAX_SERIES} series", "input.out_of_range", limit=MAX_SERIES)
    err_picks = [(n, table.index(n, p), p) for n, p in (err or [])]
    cols, lines = table.extract(sorted({xi, *[i for _, i, _, _ in picks], *[i for _, i, _ in err_picks]}))
    if text_x:
        labels = [display(c.strip(" \t")) for c in cols[xi]]
        x = np.arange(len(lines), dtype=float)
    else:
        labels = None
        x = numeric_cells(cols[xi], lines, x_name)
    out = []
    skipped = []
    for k, (name, i, param, strict) in enumerate(picks):
        if not strict and is_text_column(cols[i]):
            skipped.append(name)
            continue
        y = numeric_cells(cols[i], lines, name)
        series = Series(display(name), x, y, "data_file", "data_file", display(x_name), labels=labels, column=name, meta={"lines": lines})
        if err_picks:
            series.err = numeric_cells(cols[err_picks[k][1]], lines, err_picks[k][0])
            series.meta["err_column"] = err_picks[k][0]
        out.append(series)
    if skipped:
        notes.append(f"column(s) not plotted because they hold no numeric cell at all (text columns): {', '.join(repr(display(s[:30])) for s in skipped[:10])}")
    if not out:
        fail("y_columns", "no column of the file besides x holds numbers; name the columns to plot in 'y_columns'")
    return out, table


# ----------------------------------------------------------------------------------------------- inline JSON

def _no_constants(token: str):
    fail("series", f"'series' contains {token}; NaN and infinity are not allowed")


def number_list(obj: Any, pointer: str, *, allow_empty: bool = False, exact: bool = False) -> np.ndarray:
    if not isinstance(obj, list):
        fail(pointer, f"{pointer} must be a list of numbers")
    if len(obj) > MAX_POINTS:
        fail(pointer, f"{pointer} has {len(obj)} values; at most {MAX_POINTS} points per series", "input.out_of_range", limit=MAX_POINTS)
    if not obj and not allow_empty:
        fail(pointer, f"{pointer} is empty")
    if not set(map(type, obj)) <= {int, float, Decimal}:
        bad = next(i for i, v in enumerate(obj) if type(v) not in (int, float, Decimal))
        fail(f"{pointer}/{bad}", f"{pointer}/{bad} is not a plain number (text, true/false, null and lists are not numbers)")
    try:
        arr = np.array(obj, dtype=float)
    except OverflowError:
        fail(pointer, f"{pointer} holds an integer beyond the range of a double-precision number", "input.out_of_range")
    with np.errstate(invalid="ignore"):
        bad = ~(np.abs(arr) <= VALUE_ABS_MAX)
    if bad.any():
        i = int(np.argmax(bad))
        fail(f"{pointer}/{i}", f"{pointer}/{i} is beyond the supported magnitude {VALUE_ABS_MAX:g}", "input.out_of_range", limit=VALUE_ABS_MAX)
    return np.array(obj, dtype=object) if exact else arr


def inline_series(raw: Any, notes: list[str], *, text_x: bool = False, allow_err: bool = False, max_series: int = MAX_SERIES, exact_y: bool = False) -> list[Series]:
    """Series from JSON text ``[{"name": "a", "x": [...], "y": [...]}]``; x may be omitted (0, 1, 2, ...)."""
    if not isinstance(raw, str):
        fail("series", "'series' must be JSON text")
    if len(raw) > MAX_SERIES_TEXT:
        fail("series", f"'series' is {len(raw)} characters long; at most {MAX_SERIES_TEXT} (give a CSV file instead)", "input.out_of_range", limit=MAX_SERIES_TEXT)
    duplicates: dict[int, tuple[dict, str]] = {}

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict:
        item: dict = {}
        for key, value in pairs:
            if key in item:
                duplicates.setdefault(id(item), (item, key))
            else:
                item[key] = value
        return item

    try:
        obj = jsonsafe.loads(raw, parse_constant=_no_constants, object_pairs_hook=pairs_hook, parse_float=Decimal if exact_y else float)
    except ToolFailure:
        raise
    except (ValueError, RecursionError) as exc:
        fail("series", f"'series' is not valid JSON: {str(exc)[:80]}; expected a list such as [{{\"name\": \"a\", \"x\": [0, 1], \"y\": [2, 3]}}]")
    if isinstance(obj, dict):
        obj = [obj]
    if not isinstance(obj, list) or not obj:
        fail("series", "'series' must be a non-empty JSON list of objects with 'name', 'x' and 'y'")
    if len(obj) > max_series:
        fail("series", f"'series' has {len(obj)} series; at most {max_series}", "input.out_of_range", limit=max_series)
    keys = {"name", "x", "y"} | ({"err"} if allow_err else set())
    out: list[Series] = []
    seen: set[str] = set()
    for i, item in enumerate(obj):
        p = f"series/{i}"
        if not isinstance(item, dict):
            fail(p, f"/{p} must be an object with 'name', 'x' and 'y'")
        if id(item) in duplicates:
            _, dup = duplicates[id(item)]
            fail(f"{p}/{pointer_token(dup)}", f"/{p} repeats the key {short(dup)}; every key may appear once")
        extra = sorted(k for k in item if k not in keys)
        if extra:
            fail(f"{p}/{pointer_token(extra[0][:40])}", f"/{p} has an unknown key {short(extra[0])}; allowed keys are {sorted(keys)}")
        name = item.get("name", f"series {i + 1}")
        if not isinstance(name, str):
            fail(f"{p}/name", f"/{p}/name must be text")
        if len(name) > MAX_LABEL:
            fail(f"{p}/name", f"/{p}/name is {len(name)} characters long; at most {MAX_LABEL}", "input.out_of_range", limit=MAX_LABEL)
        name = name.strip() or f"series {i + 1}"
        if name in seen:
            fail(f"{p}/name", f"two series are named {short(name)}; names must be unique")
        seen.add(name)
        if "y" not in item:
            fail(f"{p}/y", f"/{p} has no 'y' list")
        y = number_list(item["y"], f"{p}/y", exact=exact_y)
        labels = None
        if "x" in item:
            if text_x:
                xs = item["x"]
                if not isinstance(xs, list):
                    fail(f"{p}/x", f"/{p}/x must be a list of category labels")
                if len(xs) != len(y):
                    fail(f"{p}/x", f"/{p}/x has {len(xs)} entries and y has {len(y)}; they must match")
                labels = []
                for j, lab in enumerate(xs):
                    if isinstance(lab, bool) or not isinstance(lab, (str, int, float)):
                        fail(f"{p}/x/{j}", f"/{p}/x/{j} must be a text label or a number")
                    if not isinstance(lab, str) and not -VALUE_ABS_MAX <= lab <= VALUE_ABS_MAX:
                        fail(f"{p}/x/{j}", f"/{p}/x/{j} is beyond the supported magnitude {VALUE_ABS_MAX:g}", "input.out_of_range", limit=VALUE_ABS_MAX)
                    text = lab if isinstance(lab, str) else repr(lab)
                    if len(text) > MAX_LABEL:
                        fail(f"{p}/x/{j}", f"/{p}/x/{j} is {len(text)} characters long; at most {MAX_LABEL}", "input.out_of_range", limit=MAX_LABEL)
                    labels.append(display(text.strip()))
                x = np.arange(len(y), dtype=float)
                x_name = "category"
            else:
                x = number_list(item["x"], f"{p}/x")
                if len(x) != len(y):
                    fail(f"{p}/x", f"/{p}/x has {len(x)} values and y has {len(y)}; they must match")
                x_name = "x"
        else:
            x = np.arange(len(y), dtype=float)
            x_name = "index"
        series = Series(display(name), x, y, f"{p}/x", f"{p}/y", x_name, labels=labels, column=name)
        if allow_err and "err" in item:
            e = number_list(item["err"], f"{p}/err")
            if len(e) != len(y):
                fail(f"{p}/err", f"/{p}/err has {len(e)} values and y has {len(y)}; they must match")
            series.err = e
        out.append(series)
    return out


def check_one_source(data_file: Any, series: Any) -> str:
    has_file, has_inline = not is_blank(data_file), not is_blank(series)
    if has_file and has_inline:
        fail("series", "give either 'data_file' (a CSV path) or 'series' (inline JSON), not both")
    if not has_file and not has_inline:
        fail("data_file", "'data_file' (a CSV path with a header row) or 'series' (inline JSON) is required", "input.missing")
    return "csv" if has_file else "inline"


__all__ = [
    "CsvTable", "Series", "cell_pointer", "check_one_source", "csv_series", "display", "inline_series", "is_blank", "is_text_column", "number_list",
    "numeric_cells",
]
