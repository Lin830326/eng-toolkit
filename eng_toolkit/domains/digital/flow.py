"""Yosys scripts, the limited run, and reading what Yosys said.

The Yosys script is written here, from fixed templates and values that were validated first (``src<N>.v`` file names the toolkit
chose, module names that matched a plain-identifier pattern, a language and a target from short lists, integers).  The caller's
Verilog is only ever the content of those files; no caller text is a command, a command-line argument or a file name.  The script
is stored with the result (``yosys-script.ys``) so a reviewer can read exactly what ran.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eng_toolkit import external
from eng_toolkit.domains.digital.guard import SNIPPET_CHARS, Source
from eng_toolkit.errors import ToolFailure
from eng_toolkit.external_catalog import YOSYS
from eng_toolkit.runner import JobHandle
from eng_toolkit.workspace import resolve_root

DEFAULT_TIMEOUT_S = 30
MAX_TIMEOUT_S = 120
MEMORY_MB = 1024
MESSAGE_CHARS = 300
LOG_TAIL_CHARS = 20_000
MAX_JSON_BYTES = 20_000_000
LANGUAGES = ("verilog", "systemverilog")
STACK_OVERFLOW_CODES = (0xC00000FD, -1073741571, -11)  # Windows STATUS_STACK_OVERFLOW (unsigned and signed), SIGSEGV
FLAT_MARKER = "eng-toolkit-marker-flat-check"

_SOURCE_NAME = re.compile(r"src\d+\.v")
_SOURCE_REF = re.compile(r"(src\d+\.v):(\d+)")
_WARNING = re.compile(r"^(?:(?P<file>src\d+\.v):(?P<line>\d+): )?Warning: (?P<msg>.*)$")
_ERROR = re.compile(r"^(?:(?P<file>src\d+\.v):(?P<line>\d+)(?:\.\d+-\d+\.\d+)?: )?ERROR:\s*(?P<msg>.*)$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def timeout_seconds(raw: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= MAX_TIMEOUT_S:
        raise ToolFailure(
            "input.out_of_range",
            f"timeout_seconds must be a whole number from 1 to {MAX_TIMEOUT_S}, got {raw!r}",
            {"param": "timeout_seconds", "min": 1, "max": MAX_TIMEOUT_S},
        )
    return raw


def language_flag(language: str) -> str:
    name = language.strip().lower()
    if name not in LANGUAGES:
        raise ToolFailure("input.invalid", f"language must be one of {list(LANGUAGES)}, got {language[:30]!r}", {"param": "language", "allowed": list(LANGUAGES)})
    return "-sv " if name == "systemverilog" else ""


def read_lines(sources: list[Source], language: str) -> list[str]:
    flag = language_flag(language)
    return [f"read_verilog {flag}{source.name}" for source in sources]


# --------------------------------------------------------------------------- the run


@dataclass
class Outcome:
    record: external.Record
    log: str
    workdir: Path
    labels: dict[str, str]
    script: str
    seconds: float
    version: str | None = None
    files: dict[str, Path] = field(default_factory=dict)

    def clean(self, text: str, limit: int = MESSAGE_CHARS) -> str:
        """Single-line printable ``text`` of at most ``limit`` characters, the job directory and the home directory hidden."""
        return external.clean_text(text, limit, _hidden(self.workdir))

    def relabel(self, text: str) -> str:
        """``text`` with the file names the toolkit gave the sources (``src0.v``) replaced by the names the caller knows."""
        return _SOURCE_NAME.sub(lambda m: self.labels.get(m.group(), m.group()), text)

    def file_text(self, name: str, limit: int = MAX_JSON_BYTES) -> str | None:
        path = self.workdir / name
        try:
            if not path.is_file() or path.stat().st_size > limit:
                return None
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def json_file(self, name: str) -> Any:
        text = self.file_text(name)
        if text is None:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None


def _hidden(workdir: Path) -> tuple[tuple[str, str], ...]:
    pairs = [(str(workdir), "<job>"), (str(workdir.resolve()), "<job>")]
    for home in {os.path.expanduser("~"), os.environ.get("USERPROFILE", "")}:
        if home and home != "~":
            pairs.append((home, "<home>"))
    return tuple(pairs)


def run_yosys(
    handle: JobHandle,
    sources: list[Source],
    script_lines: list[str],
    *,
    timeout_s: int,
    all_labels: dict[str, str] | None = None,
) -> Outcome:
    """Write the sources and the script into a fresh directory of the job, run Yosys on it, and return what happened.

    ``external.run`` supplies the limits: no shell, the scrubbed environment, the time limit (``timeout_s``), the memory cap
    (:data:`MEMORY_MB`), no stdin and the caps on what it may write and print.  A non-zero exit is not raised here (the caller
    reads the log, since Yosys reports its errors there); a timeout, a limit hit or a missing program is.
    """
    found, why = external.locate(YOSYS)
    if found is None:
        raise external.unavailable(YOSYS, why)
    base = resolve_root() / handle.job_id
    workdir = base / "yosys"
    index = 1
    while workdir.exists():
        index += 1
        workdir = base / f"yosys-{index}"
    workdir.mkdir(parents=True)
    for source in sources:
        (workdir / source.name).write_bytes(source.text.encode("utf-8"))
    script = "\n".join(script_lines) + "\n"
    (workdir / "script.ys").write_bytes(script.encode("utf-8"))
    log_path = workdir / "yosys.log"
    record = external.run(
        YOSYS,
        ["-q", "-l", log_path, "-s", workdir / "script.ys"],
        workdir=workdir,
        located=found,
        timeout_s=float(timeout_s),
        memory_mb=MEMORY_MB,
        check=False,
    )
    try:
        log = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log = ""
    labels = {source.name: source.label for source in sources}
    if all_labels:
        labels.update(all_labels)
    return Outcome(
        record=record,
        log=log,
        workdir=workdir,
        labels=labels,
        script=script,
        seconds=record.seconds,
        version=external.version(YOSYS, found),
    )


def raise_if_failed(out: Outcome, *, what: str) -> None:
    """Turn a non-zero exit into a :class:`ToolFailure` that names the file and line when Yosys gave them."""
    if out.record.returncode == 0:
        return
    errors = [m for m in messages(out)[1]]
    if errors:
        first = errors[0]
        where = f"{first['file']} line {first['line']}: " if first["file"] and first["line"] else (f"{first['file']}: " if first["file"] else "")
        family = "digital.source_error" if first["rule"] == "syntax-error" else "digital.synthesis_error"
        if "No matching port" in first["message"] or "port width" in first["message"].lower():
            family = "digital.interface_mismatch"
        raise ToolFailure(
            family,
            f"{what} failed: {where}{first['message']}",
            {"file": first["file"], "line": first["line"], "rule": first["rule"], "yosys_message": first["message"]},
        )
    said = out.relabel(out.clean(out.record.stderr_tail) or out.clean(out.record.stdout_tail))
    if "bad_alloc" in said or "out of memory" in said.lower():
        raise ToolFailure(
            "engine.resource_limit",
            f"yosys ran out of memory during {what} (the limit is {MEMORY_MB} MB); the design is too large for the limits of this tool",
            {"returncode": out.record.returncode, "memory_mb": MEMORY_MB},
        )
    code = out.record.returncode
    if code in STACK_OVERFLOW_CODES:
        raise ToolFailure(
            "engine.failure",
            f"yosys crashed with a stack overflow during {what}: the source nests expressions or blocks too deeply for its parser",
            {"returncode": code},
        )
    raise ToolFailure(
        "engine.failure",
        f"yosys exited with code {code} during {what}"
        + (f" and said: {said}" if said else f" and printed nothing (a crash, or it ran out of memory: the limit is {MEMORY_MB} MB)"),
        {"returncode": code, "memory_mb": MEMORY_MB},
    )


# --------------------------------------------------------------------------- reading the log


def _where(out: Outcome, text: str) -> tuple[str | None, int | None]:
    ref = _SOURCE_REF.search(text)
    if ref is None:
        return None, None
    return out.labels.get(ref.group(1), ref.group(1)), int(ref.group(2))


def _lines_of(out: Outcome, text: str) -> list[int]:
    """Source line numbers named in ``text`` (``src0.v:12.3-12.9`` and the like), in order, without repeats."""
    seen: list[int] = []
    for _file, line in _SOURCE_REF.findall(text):
        n = int(line)
        if n not in seen:
            seen.append(n)
    return seen


def classify(message: str, *, error: bool) -> tuple[str, str]:
    """``(rule, severity)`` for a Yosys message."""
    if error:
        if "syntax error" in message:
            return "syntax-error", "error"
        if "does not have a port named" in message or "Port " in message and "not found" in message:
            return "hierarchy-port-mismatch", "error"
        if "is not part of the design" in message:
            return "hierarchy-missing-module", "error"
        if "Re-definition of module" in message:
            return "duplicate-module", "error"
        return "yosys-error", "error"
    if message.startswith("Latch inferred"):
        return "latch-inferred", "warning"
    if message.startswith("found logic loop") or message.startswith("Detected loop"):
        return "combinational-loop", "error"
    if message.startswith("multiple conflicting drivers"):
        return "multiple-drivers", "error"
    if "is used but has no driver" in message:
        return "undriven-signal", "warning"
    if "is implicitly declared" in message:
        return "implicit-declaration", "warning"
    if message.startswith("Resizing cell port"):
        return "width-mismatch", "warning"
    return "yosys-warning", "warning"


def messages(out: Outcome, text: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(warnings, errors)`` found in the Yosys log, each ``{rule, severity, message, file, line, detail}``, repeats removed.

    A message keeps the indented lines that follow it (loops list their cells there) in ``detail``; ``file`` and ``line`` come
    from the message itself, or else from the first ``src<N>.v:<line>`` it mentions.
    """
    lines = (out.log if text is None else text).split("\n")
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int | None]] = set()
    index = 0
    while index < len(lines):
        raw = lines[index]
        index += 1
        is_error = False
        match = _WARNING.match(raw)
        if match is None:
            match = _ERROR.match(raw)
            is_error = True
        if match is None:
            continue
        detail: list[str] = []
        while index < len(lines) and lines[index].startswith("    "):
            detail.append(lines[index].strip())
            index += 1
        message = out.relabel(out.clean(match.group("msg"), MESSAGE_CHARS))
        file = out.labels.get(match.group("file")) if match.group("file") else None
        line = int(match.group("line")) if match.group("line") else None
        if file is None:
            file, line = _where(out, " ".join([raw, *detail]))
        key = (message, file or "", line)
        if key in seen:
            continue
        seen.add(key)
        rule, severity = classify(message, error=is_error)
        item = {
            "rule": rule,
            "severity": severity,
            "message": message,
            "file": file,
            "line": line,
            "detail": [out.relabel(out.clean(d, MESSAGE_CHARS)) for d in detail[:10]],
            "_lines": _lines_of(out, " ".join([raw, *detail])),
        }
        (errors if is_error else warnings).append(item)
    return warnings, errors


def log_tail(out: Outcome, limit: int = LOG_TAIL_CHARS) -> str:
    text = _CONTROL.sub(" ", out.log)
    for path, label in sorted(_hidden(out.workdir), key=lambda item: -len(item[0])):
        if path:
            for form in {path, path.replace("\\", "/")}:
                text = re.sub(re.escape(form), lambda _m, label=label: label, text, flags=re.IGNORECASE)
    text = "\n".join(line if len(line) <= 400 else line[:397] + "..." for line in text.split("\n"))
    return text if len(text) <= limit else "...\n" + text[-limit:]


def snippet(text: str) -> str:
    return text if len(text) <= SNIPPET_CHARS else text[: SNIPPET_CHARS - 3] + "..."
