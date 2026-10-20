"""Build the ngspice deck from validated parameters, run it through :mod:`eng_toolkit.external`, read the table it wrote.

The deck is **ours**: a fixed title, the caller's circuit in the normal form :mod:`~eng_toolkit.domains.spice.netlist` produced, and a control
script made here from numbers and from output names that were checked against the parsed circuit.  Nothing from the caller's text is part of a
command line; the files are named by this module (``deck.cir``, ``table.csv``, ``ngspice.log``) in a fresh ``sim/`` directory below the job.

ngspice exits with code 0 even when the circuit or a vector is wrong, so success means: the table exists, it parses, every number is finite
and the log holds no ``Error`` line.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from eng_toolkit import external
from eng_toolkit.errors import ToolFailure
from eng_toolkit.external_catalog import NGSPICE

TIMEOUT_S = 60.0
MEMORY_MB = 2048
MAX_OUTPUT_BYTES = 40_000_000
MAX_TABLE_ROWS = 200_000
MAX_OUTPUTS = 16

_FATAL = re.compile(r"^\s*(error|fatal)\b|timestep too small|simulation(\(s\))? (interrupted|aborted)", re.IGNORECASE)
_WARN_ANY = re.compile(r"warning:.*?(?=warning:|note:|error:|$)", re.IGNORECASE)
LOG_LINES = 2000


@dataclass
class Table:
    x_name: str
    x: list[float]
    columns: dict[str, list[float]]  # op/dc/tran: output -> values; ac: "<output>.re" / "<output>.im"


def format_number(value: float) -> str:
    """A float as ngspice reads it (never an SI suffix, never inf or nan)."""
    if not math.isfinite(value):  # pragma: no cover - callers bound their numbers
        raise ToolFailure("input.invalid", "a non-finite number reached the deck", {})
    return repr(float(f"{value:.12g}"))  # 9.999999999999999e-06 from a unit conversion becomes 1e-05


def build_deck(body: list[str], analysis_lines: list[str], outputs: list[str], table_name: str = "table.csv") -> str:
    """The text of ``deck.cir``: the circuit, then the fixed control script, then ``.end``."""
    control = [
        ".control",
        "set wr_singlescale",
        "set wr_vecnames",
        "set numdgt=15",
        *analysis_lines,
        f"wrdata {table_name} " + " ".join(outputs),
        "quit",
        ".endc",
    ]
    return "\n".join(["eng-toolkit deck", *body, *control, ".end", ""])


def run_deck(deck: str, workdir: Path, *, write_file) -> tuple[external.Record, list[str]]:
    """Write ``deck`` under ``workdir`` (``write_file(name, text)`` writes below it), run ngspice, return the record and the cleaned log lines."""
    write_file("deck.cir", deck)
    log = workdir / "ngspice.log"
    record = external.run(
        NGSPICE,
        ["-b", "-o", log, workdir / "deck.cir"],
        workdir=workdir,
        timeout_s=TIMEOUT_S,
        memory_mb=MEMORY_MB,
        max_output_bytes=MAX_OUTPUT_BYTES,
        check=False,
    )
    hide = ((str(workdir), "<job>"), (str(workdir.resolve()), "<job>"))
    lines: list[str] = []
    if log.exists():
        raw = log.read_bytes()[-400_000:].decode("utf-8", errors="replace").splitlines()
        lines = [external.clean_text(line, 240, hide) for line in raw[-LOG_LINES:]]
    return record, [line for line in lines if line]


def log_findings(lines: list[str], streams: tuple[str, ...] = ()) -> tuple[list[str], list[str]]:
    """``(fatal messages, warnings)`` from the log lines and the program's own output; a fatal message joins the line with the two after it (ngspice puts the cause there).

    ngspice writes ``Warning:`` in the middle of a line too (``Trying gmin = ... Warning: singular matrix``), so warnings are cut out wherever they start.
    """
    fatal: list[str] = []
    warn: list[str] = []
    for text in (*lines, *streams):
        for m in _WARN_ANY.finditer(text):
            piece = external.clean_text(m.group(0), 200)
            if len(warn) < 5 and piece not in warn:
                warn.append(piece)
    for i, line in enumerate(lines):
        if _FATAL.search(line):
            fatal.append(external.clean_text(" ".join(lines[i : i + 3]), 240))
    return fatal[:5], warn


def read_table(path: Path, *, complex_columns: bool, outputs: list[str]) -> Table:
    if not path.is_file():
        raise ToolFailure("engine.failure", "ngspice wrote no result table", {"reason_code": "no_table"})
    if path.stat().st_size > MAX_OUTPUT_BYTES:
        raise ToolFailure("engine.resource_limit", "the result table is larger than the limit", {"limit_bytes": MAX_OUTPUT_BYTES})
    lines = path.read_text(encoding="ascii", errors="replace").splitlines()
    if len(lines) < 2:
        raise ToolFailure("engine.failure", "ngspice wrote an empty result table", {"reason_code": "empty_table"})
    rows = lines[1:]
    if len(rows) > MAX_TABLE_ROWS:
        raise ToolFailure("engine.resource_limit", f"the simulation produced {len(rows)} rows, over the {MAX_TABLE_ROWS} row limit", {"rows": len(rows)})
    width = 1 + (2 if complex_columns else 1) * len(outputs)
    data: list[list[float]] = []
    for i, row in enumerate(rows, start=2):
        parts = row.split()
        if len(parts) != width:
            raise ToolFailure("engine.failure", f"result table line {i} has {len(parts)} columns, expected {width}", {"reason_code": "table_shape"})
        try:
            values = [float(p) for p in parts]
        except ValueError:
            raise ToolFailure("engine.failure", f"result table line {i} holds a value that is not a number", {"reason_code": "table_value"}) from None
        if not all(math.isfinite(v) for v in values):
            raise ToolFailure(
                "engine.failure",
                f"ngspice produced a non-finite number (nan or inf) at row {i - 1} of the result; the circuit has no usable solution there",
                {"reason_code": "non_finite", "row": i - 1},
            )
        data.append(values)
    columns: dict[str, list[float]] = {}
    for k, name in enumerate(outputs):
        if complex_columns:
            columns[f"{name}.re"] = [r[1 + 2 * k] for r in data]
            columns[f"{name}.im"] = [r[2 + 2 * k] for r in data]
        else:
            columns[name] = [r[1 + k] for r in data]
    return Table(lines[0].split()[0] if lines[0].split() else "x", [r[0] for r in data], columns)


def simulate(
    deck: str,
    workdir: Path,
    *,
    write_file,
    complex_columns: bool,
    outputs: list[str],
) -> tuple[Table, external.Record, list[str]]:
    """Run the deck; raise ``engine.failure`` with ngspice's own words when it did not produce a clean table."""
    record, log = run_deck(deck, workdir, write_file=write_file)
    fatal, warnings = log_findings(log, (record.stderr_tail, record.stdout_tail))
    if record.returncode != 0:
        raise ToolFailure(
            "engine.failure",
            f"ngspice exited with code {record.returncode}" + (f" and said: {fatal[0]}" if fatal else ""),
            {"returncode": record.returncode, "ngspice": fatal},
        )
    if fatal:
        raise ToolFailure("engine.failure", f"ngspice reported an error: {fatal[0]}", {"ngspice": fatal, "reason_code": "ngspice_error"})
    table = read_table(workdir / "table.csv", complex_columns=complex_columns, outputs=outputs)
    return table, record, warnings


__all__ = [
    "MAX_OUTPUTS",
    "MAX_TABLE_ROWS",
    "Table",
    "build_deck",
    "format_number",
    "log_findings",
    "read_table",
    "run_deck",
    "simulate",
]
