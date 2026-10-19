"""Read lists of numbers for the GD&T tools: an inline string or the text of a CSV-like point file.

Accepted forms, the same inline and in a file:

* JSON: ``[[x, y, z], ...]`` (a list of rows) or ``[v1, v2, ...]`` (a plain list, for one number per row);
* text: one row per line (or separated by ``;``), values separated by commas, spaces or tabs; ``#`` starts a comment line; one
  leading header line of names is skipped, and when it names the wanted columns (``x``, ``y``, ``z``) those columns are used.

Every value must be a finite number.  Errors name the row and column so the caller can fix the list.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

import numpy as np

from eng_toolkit.errors import ToolFailure

MAX_ROWS = 600_000
POINT_FILE_EXTENSIONS = (".csv", ".txt", ".xyz", ".pts", ".asc", ".dat")
MAX_FILE_BYTES = 64 * 1024 * 1024
_SPLIT = re.compile(r"[,\s]+")
_AXIS_NAMES = ("x", "y", "z")


def _bad(message: str, **details: Any) -> ToolFailure:
    return ToolFailure("input.invalid", message, details)


def _number(token: Any, row: int, col: int, param: str) -> float:
    if isinstance(token, bool) or token is None or isinstance(token, (list, dict)):
        raise _bad(f"{param}: row {row} value {col} is {token!r}, not a number", param=param, row=row, column=col)
    try:
        value = float(token)
    except (TypeError, ValueError) as exc:
        raise _bad(f"{param}: row {row} value {col} {token!r} is not a number", param=param, row=row, column=col) from exc
    if isinstance(token, str) and not re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?|[+-]?(nan|inf|infinity)", token.strip(), re.I):
        raise _bad(f"{param}: row {row} value {col} {token!r} is not a plain number", param=param, row=row, column=col)
    if not math.isfinite(value):
        raise _bad(
            f"{param}: row {row} value {col} is {token!r}; every value must be a finite number (no NaN or infinity)",
            param=param,
            row=row,
            column=col,
        )
    return value


def _from_json(text: str, param: str) -> list[list[Any]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _bad(f"{param}: looks like JSON but is not valid ({exc.msg} at position {exc.pos})", param=param) from exc
    if not isinstance(data, list):
        raise _bad(f"{param}: JSON must be a list of rows, e.g. [[1,2],[3,4]] or a list of numbers", param=param)
    rows = []
    for i, item in enumerate(data, start=1):
        rows.append(item if isinstance(item, list) else [item])
    return rows


def _from_text(text: str, wanted: Any, param: str) -> tuple[list[list[Any]], list[str] | None]:
    rows: list[list[Any]] = []
    header: list[str] | None = None
    lines = [seg for line in text.splitlines() for seg in line.split(";")]
    for raw in lines:
        line = raw.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        tokens = [t for t in _SPLIT.split(line) if t]
        if not rows and header is None and not _looks_numeric(tokens[0]):
            header = [t.strip().lower() for t in tokens]
            continue
        rows.append(tokens)
    return rows, header


def _looks_numeric(token: str) -> bool:
    try:
        float(token)
    except ValueError:
        return False
    return True


def parse_numbers(
    text: str,
    *,
    param: str,
    columns: int | tuple[int, ...] | None = None,
    names: tuple[str, ...] = (),
) -> np.ndarray:
    """Parse ``text`` into an ``(n, k)`` float array (``(n,)`` when ``columns`` is 1).

    ``columns`` is the width required: an int, a tuple of accepted widths, or None for the width of the first row.  ``names``
    are the column names (in order) a header row may use to pick columns out of a wider file; with several accepted widths the
    widest one the header names wins."""
    stripped = text.strip()
    if not stripped:
        raise ToolFailure("input.missing", f"{param} is empty; give the values", {"param": param})
    header: list[str] | None = None
    if stripped[0] == "[":
        rows = _from_json(stripped, param)
    else:
        rows, header = _from_text(stripped, columns, param)
    if len(rows) > MAX_ROWS:
        raise ToolFailure("input.too_large", f"{param}: {len(rows)} rows; at most {MAX_ROWS} are read", {"param": param, "rows": len(rows)})
    if not rows:
        raise _bad(f"{param}: no data rows found", param=param)
    allowed = (columns,) if isinstance(columns, int) else tuple(columns or ())
    width = len(rows[0])
    for i, row in enumerate(rows, start=1):
        if len(row) != width:
            raise _bad(
                f"{param}: row {i} has {len(row)} values but row 1 has {width}; every row needs the same number of values",
                param=param,
                row=i,
            )
    pick: list[int] | None = None
    if header is not None and names:
        for k in sorted(allowed or (len(names),), reverse=True):
            if k <= len(names) and all(n in header for n in names[:k]):
                pick = [header.index(n) for n in names[:k]]
                break
        if pick is None and allowed and width not in allowed:
            raise _bad(
                f"{param}: the header {header} does not name the columns {list(names)}; give exactly {list(allowed)} columns or name them",
                param=param,
            )
    if pick is None:
        if allowed and width not in allowed:
            wanted = " or ".join(str(a) for a in allowed)
            raise _bad(
                f"{param}: rows have {width} values; {wanted} are needed ({', '.join(names) if names else 'one per row'})",
                param=param,
                values_per_row=width,
                expected=list(allowed),
            )
        pick = list(range(width))
    out = np.empty((len(rows), len(pick)))
    for i, row in enumerate(rows):
        for j, col in enumerate(pick):
            out[i, j] = _number(row[col], i + 1, col + 1, param)
    return out[:, 0] if allowed == (1,) else out


__all__ = ["MAX_FILE_BYTES", "MAX_ROWS", "POINT_FILE_EXTENSIONS", "parse_numbers"]
