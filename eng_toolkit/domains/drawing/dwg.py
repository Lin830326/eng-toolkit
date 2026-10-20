"""DWG input: LibreDWG's ``dwg2dxf``, a program the user installed, run as a separate process.

DWG is a closed format and has no permissive reader.  The toolkit never bundles, imports or links LibreDWG (GPL-3.0); it
starts ``dwg2dxf`` the way a person would from a shell, with a fixed list of arguments, and reads the DXF file it wrote.
That DXF is then checked and handed to the existing DXF paths of ``drawing_read`` and ``drawing_convert`` unchanged, so
every limit and protection that applies to a DXF applies to it.

Where the program comes from is decided by the operator only: ``ENG_TOOLKIT_DWG2DXF`` (a full path) or ``PATH``.  Nothing in
a drawing can name, replace or add to it.  The only values on its command line are the two files this module made in the
job directory under fixed names (``input.dwg``, ``output.dxf``), so a file name with spaces, a leading dash or characters
the program's C library cannot open never reaches it.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eng_toolkit import external as X
from eng_toolkit.errors import ToolFailure
from eng_toolkit.external_catalog import DWG2DXF
from eng_toolkit.runner import JobHandle

TOOL = DWG2DXF  # the descriptor (names, variable, places to look, version probe); the tests replace it to hide a program installed here
ENV_EXE = DWG2DXF.env_var
ENV_TIMEOUT = "ENG_TOOLKIT_DWG2DXF_TIMEOUT_S"
ENV_MEMORY = "ENG_TOOLKIT_DWG2DXF_MEMORY_MB"
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_MEMORY_MB = 2048
PROJECT = DWG2DXF.project
STDERR_CHARS = 300  # how much of the program's last words an error shows
WORKDIR = "dwg2dxf"  # inside the job directory
INPUT_NAME, OUTPUT_NAME = "input.dwg", "output.dxf"

# The first six bytes of a DWG name its release.  Only the releases LibreDWG reads and this module was written for are
# accepted; R13 and R14 (AC1012, AC1014) and older are refused, as is anything unknown.
SUPPORTED: dict[bytes, str] = {
    b"AC1009": "R12",
    b"AC1015": "R2000",
    b"AC1018": "R2004",
    b"AC1021": "R2007",
    b"AC1024": "R2010",
    b"AC1027": "R2013",
    b"AC1032": "R2018",
}
OLDER: dict[bytes, str] = {b"AC1006": "R10", b"AC1012": "R13", b"AC1014": "R14"}
HEADER = re.compile(rb"AC10\d\d")
# a DXF starts with group code 0 and SECTION (after any 999 comment records), or with the binary DXF sentinel
_DXF_START = re.compile(rb"\A(?:\xef\xbb\xbf)?(?:\s*999\s*\r?\n[^\r\n]*\r?\n)*\s*0\s*\r?\n\s*SECTION\s*(?:\r?\n|\Z)")
BINARY_DXF = b"AutoCAD Binary DXF"


@dataclass(frozen=True)
class Converted:
    path: Path  # the DXF the program wrote, in the job directory
    info: dict[str, Any]  # what drawing_read and drawing_convert report under data.dwg
    warnings: list[str]


def header_of(path: Path, size: int = 6) -> bytes:
    with open(path, "rb") as handle:
        return handle.read(size)


def has_dwg_header(path: Path) -> bool:
    return bool(HEADER.fullmatch(header_of(path)))


def needs_conversion(path: Path) -> bool:
    """True for a ``.dwg`` and for any file, whatever its extension says, that starts with a DWG header.  A real DXF never does."""
    return path.suffix.lower() == ".dwg" or has_dwg_header(path)


def release_of(start: bytes, shown: str) -> tuple[str, str]:
    """``(code, release)`` from the first bytes of a file (at least six; 64 are enough to recognise a DXF), or the refusal."""
    head = start[:6]
    if not HEADER.fullmatch(head):
        hint = " It looks like a DXF; give it the extension .dxf." if looks_like_dxf(start) else ""
        raise ToolFailure(
            "input.invalid",
            f"{shown}: the file does not start with a DWG header (AC10xx), so it is not a DWG.{hint}",
            {"path": shown, "format": "dwg", "first_bytes": head.decode("latin-1").encode("ascii", "replace").decode("ascii")},
        )
    code = head.decode("ascii")
    if head in SUPPORTED:
        return code, SUPPORTED[head]
    older = OLDER.get(head)
    name = f"{older} ({code})" if older else f"an unknown release ({code})"
    raise ToolFailure(
        "input.unsupported_format",
        f"{shown}: this DWG is {name}; supported are {', '.join(f'{r} ({c.decode()})' for c, r in SUPPORTED.items())}. "
        "Open the file in a CAD program and save it as DWG 2000 or later, or as DXF",
        {"path": shown, "format": "dwg", "dwg_version": code, "supported": [c.decode() for c in SUPPORTED]},
    )


def unavailable(why: str, shown: str = "") -> ToolFailure:
    """``engine.unavailable`` for a DWG: the layer's message (what was looked at, the project page, the variable) plus the way round it."""
    base = X.unavailable(TOOL, why, format="dwg", **({"path": shown} if shown else {}))
    return ToolFailure(base.family, f"A DWG file needs LibreDWG's dwg2dxf. {base.message} Or convert the drawing to DXF in a CAD program and give the DXF", base.details)


def require_tool(shown: str = "") -> X.Located:
    """The located program, or ``engine.unavailable``.  Checked before a DWG is opened, so a missing tool costs nothing."""
    found, why = X.locate(TOOL)
    if found is None:
        raise unavailable(why, shown)
    return found


def _number(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        return default
    return value if value > 0 and value != float("inf") else default


def limits() -> tuple[float, int]:
    """``(timeout_s, memory_mb)`` for one run; the operator can change them with ``ENG_TOOLKIT_DWG2DXF_TIMEOUT_S`` and ``..._MEMORY_MB``."""
    return _number(ENV_TIMEOUT, DEFAULT_TIMEOUT_S), int(_number(ENV_MEMORY, DEFAULT_MEMORY_MB))


def looks_like_dxf(head: bytes) -> bool:
    return head.startswith(BINARY_DXF) or bool(_DXF_START.match(head))


def convert(handle: JobHandle, source: Path, *, shown: str, max_output_bytes: int) -> Converted:
    """Run dwg2dxf on ``source`` (the job's copy of a DWG) and return the DXF it wrote.

    Raises ``input.invalid`` (not a DWG header), ``input.unsupported_format`` (a release not supported), ``engine.unavailable``
    (program not found), ``engine.timeout``, ``engine.failure`` (non-zero exit, or no output), ``drawing.too_large`` (the DXF
    outgrew ``max_output_bytes``; the program is stopped) and ``drawing.corrupt`` (the output is empty or not a DXF).
    """
    code, release = release_of(header_of(source, 64), shown)
    found = require_tool(shown)
    timeout_s, memory_mb = limits()

    work = handle.job_dir / WORKDIR
    work.mkdir(exist_ok=True)
    local, output = work / INPUT_NAME, work / OUTPUT_NAME
    shutil.copyfile(source, local)
    version = X.version(TOOL, found)

    try:
        done = X.run(
            TOOL, ["-o", output, local], workdir=handle.job_dir, located=found, timeout_s=timeout_s, memory_mb=memory_mb, max_output_bytes=max_output_bytes, check=False
        )
    except ToolFailure as exc:
        if exc.family == "engine.timeout":
            raise ToolFailure(
                "engine.timeout",
                f"dwg2dxf took longer than {timeout_s:g} s to convert {shown} and was stopped (the limit can be raised with {ENV_TIMEOUT})",
                {"tool": TOOL.name, "timeout_s": timeout_s, "env_var": ENV_TIMEOUT},
            ) from exc
        if exc.family == "engine.resource_limit" and exc.details.get("stream") == "output":
            raise ToolFailure(
                "drawing.too_large",
                f"dwg2dxf was writing a DXF of more than {max_output_bytes} bytes for {shown} and was stopped; the toolkit reads DXF files up to that size",
                {"tool": TOOL.name, "limit_bytes": max_output_bytes, "bytes": exc.details.get("bytes")},
            ) from exc
        raise
    tail = X.clean_text(done.stderr_tail, STDERR_CHARS)
    if done.returncode != 0:
        raise ToolFailure(
            "engine.failure",
            f"dwg2dxf could not convert {shown}: it exited with code {done.returncode}"
            + (f" and said: {tail}" if tail else " and printed nothing")
            + ". The file may be damaged, or use features LibreDWG does not read",
            {"tool": TOOL.name, "returncode": done.returncode, "stderr_tail": tail, "dwg_version": code},
        )
    try:
        status = os.lstat(output)
    except OSError:
        raise ToolFailure(
            "engine.failure",
            f"dwg2dxf finished without an error but wrote no output file for {shown}" + (f" (it said: {tail})" if tail else ""),
            {"tool": TOOL.name, "returncode": 0, "stderr_tail": tail},
        ) from None
    if not stat.S_ISREG(status.st_mode):
        raise ToolFailure("drawing.corrupt", f"dwg2dxf's output for {shown} is not a regular file", {"tool": TOOL.name})
    if status.st_size > max_output_bytes:
        raise ToolFailure(
            "drawing.too_large",
            f"dwg2dxf wrote {status.st_size} bytes for {shown}, over the {max_output_bytes} byte limit for a DXF",
            {"tool": TOOL.name, "limit_bytes": max_output_bytes, "bytes": status.st_size},
        )
    if status.st_size == 0:
        raise ToolFailure("drawing.corrupt", f"dwg2dxf wrote an empty file for {shown}" + (f" (it said: {tail})" if tail else ""), {"tool": TOOL.name, "stderr_tail": tail})
    with open(output, "rb") as fh:
        head = fh.read(4096)
    if not looks_like_dxf(head):
        raise ToolFailure(
            "drawing.corrupt",
            f"dwg2dxf's output for {shown} is not a DXF file (it does not start with a DXF section)" + (f"; the program said: {tail}" if tail else ""),
            {"tool": TOOL.name, "bytes": status.st_size, "stderr_tail": tail},
        )
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    handle.engine_versions[TOOL.name] = version or "unknown"
    cap_note = (
        []
        if done.memory_cap_applied
        else ["The memory limit could not be applied to dwg2dxf on this platform; only the time and output size limits protect the server."]
    )
    info = {
        "source_format": "dwg",
        "dwg_version": code,
        "dwg_release": release,
        "converted_by": "LibreDWG dwg2dxf, a separate program installed by the user (GPL-3.0)",
        "engine_version": version or "unknown",
        "found_via": f"{found.source}: {found.via}",
        "tool_path": found.path,
        "arguments": ["-o", f"<job>/{WORKDIR}/{OUTPUT_NAME}", f"<job>/{WORKDIR}/{INPUT_NAME}"],
        "seconds": round(done.seconds, 3),
        "limits": {"timeout_s": timeout_s, "memory_mb": memory_mb, "max_output_bytes": max_output_bytes, "memory_cap_applied": done.memory_cap_applied},
        "converted_dxf": {"path": str(output), "bytes": status.st_size, "sha256": digest},
    }
    warnings = [
        f"This drawing is a DWG ({release}, {code}) that went through LibreDWG's dwg2dxf (version {version or 'unknown'}) before it was read: "
        "LibreDWG rebuilds the DXF from a reverse-engineered reading of the closed DWG format, so entities, styles, dimensions or text can be "
        "missing or different. Compare the result with the original drawing before relying on it.",
        *cap_note,
    ]
    return Converted(output, info, warnings)


__all__ = [
    "Converted",
    "convert",
    "has_dwg_header",
    "needs_conversion",
    "release_of",
    "require_tool",
    "unavailable",
]
