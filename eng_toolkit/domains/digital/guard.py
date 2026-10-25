"""Gatekeeper for Verilog text: the caller's source is data, and nothing in it may reach a file, a program or a Yosys command.

Yosys reads Verilog with a preprocessor that opens any file named by an include directive, and with system tasks that read or
write files (``$readmemh``, ``$fopen``, ``$fwrite``, ``$dumpfile``) or run programs (``$system``).  The toolkit never lets a document
decide that, so every source passes through :func:`sanitize` before a single byte is handed to Yosys, and the bytes Yosys reads are
the bytes that were checked (the sanitised text is written to the job directory by the toolkit, never the caller's file).

What :func:`sanitize` does, in order:

1. size (:data:`MAX_SOURCE_BYTES`), UTF-8, NUL and other control characters, line endings normalised to ``\\n``, no line longer than
   :data:`MAX_LINE_CHARS` (Yosys's scanner fails on one very long token; ``input.too_large``);
2. line continuation (a backslash at the end of a line) is refused everywhere: it is the way a line comment or a macro body could
   be made to mean something different to the checker and to Yosys;
3. one pass splits the text into code, comments, strings and escaped identifiers.  Comments and the insides of strings are blanked
   (line numbers are kept), an unterminated comment or string is refused, an escaped identifier that holds a quote, a backtick or a
   comment marker is refused;
4. in the code that is left: a backtick must start a directive or a macro use.  ``include`` in any case is refused; only a short list
   of harmless directives is allowed; a macro may be used only if the same file defines it, a definition may not take arguments, and
   a body may not hold a backtick, ``$``, a quote, a backslash or a comment marker (so no expansion can ever produce a directive, a
   system task, a string or a comment); ``$`` must start a system task from the short list in :data:`ALLOWED_SYSTEM_TASKS`;
   non-ASCII characters outside comments and strings are refused;
5. the number of ``module`` keywords is capped (:data:`MAX_MODULES`) and the module names are collected.

Nothing here is a parser: it is a deliberately narrow filter.  A valid design that uses a feature outside the list is refused with
the name of the feature and the line; it is never silently altered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from eng_toolkit.errors import ToolFailure

MAX_SOURCE_BYTES = 1_000_000  # all sources of one design together
MAX_LINE_CHARS = 8000  # one line of Verilog; Yosys's scanner fails on a single token near 64 000 characters, real RTL lines are far shorter
MAX_MODULES = 200
MAX_NAME_LENGTH = 64
SNIPPET_CHARS = 80  # the most a message or detail ever echoes of the caller's text

ALLOWED_DIRECTIVES = frozenset(
    {"timescale", "default_nettype", "define", "undef", "ifdef", "ifndef", "else", "elsif", "endif", "resetall", "celldefine", "endcelldefine"}
)
# Pure functions and display tasks that never touch a file or a program.  $display and $write print into Yosys's log only.
ALLOWED_SYSTEM_TASKS = frozenset(
    {"signed", "unsigned", "clog2", "bits", "size", "high", "low", "left", "right", "dimensions", "display", "write", "finish", "stop"}
)

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PLAIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,%d}" % (MAX_NAME_LENGTH - 1))
_SYS_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_MACRO_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_WORD = re.compile(r"[A-Za-z0-9_$]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")
_MODULE = re.compile(r"(?<![A-Za-z0-9_$])(?:macro)?module(?![A-Za-z0-9_$])\s*([A-Za-z_][A-Za-z0-9_$]*)?")
_LONG_LINE = re.compile(r"[^\n]{%d,}" % (MAX_LINE_CHARS + 1))
_TOKEN = re.compile(
    r"""
      (?P<line>//[^\n]*)
    | (?P<block>/\*.*?\*/)
    | (?P<blockopen>/\*)
    | (?P<string>"(?:[^"\\\n]|\\[^\n])*")
    | (?P<badstring>")
    | (?P<escid>\\\S+)
    | (?P<stray>\\)
    | (?P<tick>`)
    """,
    re.VERBOSE | re.DOTALL,
)


@dataclass(frozen=True)
class Source:
    """One checked source: what messages call it, the file name Yosys reads it under, the text, and its module names."""

    label: str
    name: str
    text: str
    modules: tuple[str, ...]
    param: str = ""  # JSON pointer to the tool argument the text came from ("/source", "/files", ...); empty when not known


def unicode_text(raw: str, *, param: str) -> None:
    """Reject lone surrogates without including them in an error message."""
    try:
        raw.encode("utf-8")
    except UnicodeEncodeError:
        raise ToolFailure("input.invalid", f"{param}: the text is not valid Unicode text", {"param": param}) from None


def plain_identifier(raw: str, *, param: str) -> str:
    """``raw`` stripped, if it is a plain Verilog identifier of at most 64 characters (letters, digits, underscore; not starting with a digit)."""
    name = raw.strip()
    if not _PLAIN_NAME.fullmatch(name):
        raise ToolFailure(
            "input.invalid",
            f"{param} must be a plain identifier (letters, digits and '_', not starting with a digit, at most {MAX_NAME_LENGTH} characters)",
            {"param": param, "received": _clip(name, 40)},
        )
    return name


def _clip(text: str, limit: int = SNIPPET_CHARS) -> str:
    text = _CONTROL.sub(" ", text.replace("\n", " ")).strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _snippet(text: str, offset: int) -> str:
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    return _clip(text[start : end if end >= 0 else len(text)])


def _refuse(label: str, text: str, offset: int, construct: str, why: str, *, family: str = "input.forbidden_construct") -> ToolFailure:
    line = _line_of(text, offset)
    return ToolFailure(
        family,
        f"{label} line {line}: {construct} is not allowed: {why}",
        {"file": label, "line": line, "construct": _clip(construct, 40), "snippet": _snippet(text, offset)},
    )


def decode(raw: bytes | str, *, label: str, limit: int = MAX_SOURCE_BYTES) -> str:
    """Text of a source: size, encoding, control characters and line endings checked; a BOM dropped; every line ending ``\\n``."""
    if isinstance(raw, str):
        try:
            data = raw.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ToolFailure("input.invalid", f"{label}: the text holds a character that is not valid Unicode (a lone surrogate)", {"file": label}) from exc
    else:
        data = raw
    if len(data) > limit:
        raise ToolFailure("input.too_large", f"{label}: {len(data)} bytes is over the {limit} byte limit for Verilog source", {"file": label, "bytes": len(data), "limit_bytes": limit})
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        line = data.count(b"\n", 0, exc.start) + 1
        raise ToolFailure("input.invalid", f"{label} line {line}: the file is not valid UTF-8 (byte offset {exc.start})", {"file": label, "line": line, "offset": exc.start}) from exc
    if text.startswith("﻿"):
        text = text[1:]
    nul = text.find("\x00")
    if nul >= 0:
        raise ToolFailure("input.invalid", f"{label} line {_line_of(text, nul)}: the source holds a NUL byte", {"file": label, "line": _line_of(text, nul)})
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    bad = _CONTROL.search(text)
    if bad:
        raise ToolFailure(
            "input.invalid",
            f"{label} line {_line_of(text, bad.start())}: the source holds the control character U+{ord(bad.group()):04X}",
            {"file": label, "line": _line_of(text, bad.start())},
        )
    long_line = _LONG_LINE.search(text)
    if long_line:
        line = _line_of(text, long_line.start())
        raise ToolFailure(
            "input.too_large",
            f"{label} line {line}: the line is {len(long_line.group())} characters long; the limit is {MAX_LINE_CHARS} (put the statement on several lines)",
            {"file": label, "line": line, "line_chars": len(long_line.group()), "limit_chars": MAX_LINE_CHARS},
        )
    return text


def sanitize(raw: bytes | str, *, label: str, name: str, limit: int = MAX_SOURCE_BYTES, param: str | None = None) -> Source:
    """Check one source and return it, or raise :class:`ToolFailure` (family ``input.*``) naming the construct and line.

    ``param`` is the name of the tool argument the text came from; every refusal then carries ``details.param`` as the pointer to it.
    """
    try:
        checked = _sanitize(raw, label=label, name=name, limit=limit)
    except ToolFailure as exc:
        if param:
            exc.details.setdefault("param", "/" + param.lstrip("/"))
        raise
    return replace(checked, param="/" + param.lstrip("/")) if param else checked


def _sanitize(raw: bytes | str, *, label: str, name: str, limit: int) -> Source:
    text = decode(raw, label=label, limit=limit)
    continuation = re.search(r"\\\n", text)
    if continuation:
        raise _refuse(label, text, continuation.start(), "a backslash at the end of a line", "line continuation is not supported")
    code = _scan(text, label)
    _check_code(code, text, label)
    names: list[str] = []
    count = 0
    for match in _MODULE.finditer(code):
        count += 1
        if match.group(1):
            names.append(match.group(1))
    if count > MAX_MODULES:
        raise ToolFailure(
            "input.out_of_range",
            f"{label}: {count} modules is over the limit of {MAX_MODULES} per design",
            {"file": label, "modules": count, "max_modules": MAX_MODULES},
        )
    return Source(label=label, name=name, text=text, modules=tuple(names))


def _scan(text: str, label: str) -> str:
    """The text with comments, string contents, escaped identifiers and ``define`` lines blanked (same length, same line breaks).

    Raises on unterminated comments and strings, on unsafe escaped identifiers and ``define`` lines, and on directives and macro
    uses that are not allowed.
    """
    out: list[str] = []
    macros: set[str] = set()
    pos = 0
    length = len(text)

    def blank(segment: str) -> str:
        return re.sub(r"[^\n]", " ", segment)

    while pos < length:
        match = _TOKEN.search(text, pos)
        if match is None:
            out.append(text[pos:])
            break
        out.append(text[pos : match.start()])
        kind = match.lastgroup
        segment = match.group()
        end = match.end()
        if kind in ("line", "block"):
            out.append(blank(segment))
        elif kind == "blockopen":
            raise _refuse(label, text, match.start(), "an unterminated /* comment", "the comment never ends", family="input.invalid")
        elif kind == "string":
            out.append('"' + blank(segment[1:-1]) + '"')
        elif kind == "badstring":
            raise _refuse(label, text, match.start(), "an unterminated string", "a string must end on its line", family="input.invalid")
        elif kind == "stray":
            raise _refuse(label, text, match.start(), "a stray backslash", "a backslash must start an escaped identifier", family="input.invalid")
        elif kind == "escid":
            for bad in ("`", '"', "//", "/*"):
                if bad in segment:
                    raise _refuse(label, text, match.start(), "an escaped identifier", f"it contains {bad!r}")
            out.append("e" * len(segment))
        else:  # tick
            end = _directive(text, match.start(), label, macros, out)
        pos = end
    return "".join(out)


def _directive(text: str, tick: int, label: str, macros: set[str], out: list[str]) -> int:
    """Handle the backtick at ``tick``; append what stays in the code to ``out``; return the offset to continue from."""
    named = _MACRO_NAME.match(text, tick + 1)
    if named is None:
        raise _refuse(label, text, tick, "a backtick", "it must be followed directly by a directive or macro name")
    word = named.group()
    lower = word.lower()
    if lower == "include":
        raise _refuse(label, text, tick, "`include", "it would read a file the caller names")
    if word == "define":
        return _define(text, tick, named.end(), label, macros, out)
    if word in ALLOWED_DIRECTIVES:
        out.append("`" + " " * (named.end() - tick - 1))
        return named.end()
    if lower in ALLOWED_DIRECTIVES:
        raise _refuse(label, text, tick, f"`{_clip(word, 30)}", "directives are case-sensitive; write it in lower case")
    if word in macros:
        out.append("`" + " " * (named.end() - tick - 1))  # the backtick stays so that "$display`X" is seen for what it is
        return named.end()
    if word in {"__FILE__", "__LINE__", "line", "pragma", "begin_keywords", "end_keywords", "undefineall", "include_next"} or lower in {"line", "pragma"}:
        raise _refuse(label, text, tick, f"`{_clip(word, 30)}", "this directive is not supported")
    raise _refuse(label, text, tick, f"`{_clip(word, 30)}", "it is not an allowed directive and not a macro defined in this file")


def _define(text: str, tick: int, after: int, label: str, macros: set[str], out: list[str]) -> int:
    eol = text.find("\n", after)
    eol = len(text) if eol < 0 else eol
    rest = text[after:eol]
    comment = rest.find("//")
    body_text = rest if comment < 0 else rest[:comment]
    head = re.match(r"[ \t]+([A-Za-z_][A-Za-z0-9_$]*)", body_text)
    if head is None:
        raise _refuse(label, text, tick, "`define", "it must be followed by a macro name", family="input.invalid")
    name = head.group(1)
    body = body_text[head.end() :]
    if body.startswith("("):
        raise _refuse(label, text, tick, "a macro with arguments", "only plain text macros are supported")
    for bad, why in (("`", "a backtick"), ("$", "a '$'"), ('"', "a quote"), ("\\", "a backslash"), ("/*", "a comment marker"), ("*/", "a comment marker")):
        if bad in body:
            raise _refuse(label, text, tick, "a macro body", f"it contains {why}")
    stripped = body.strip()
    if stripped[:1] in ("/", "*") or stripped[-1:] in ("/", "*"):
        raise _refuse(label, text, tick, "a macro body", "it starts or ends with '/' or '*', which could join a comment marker at the place it is used")
    macros.add(name)
    out.append(" " * (eol - tick))
    return eol


def _check_code(code: str, text: str, label: str) -> None:
    bad = re.search(r"[^\x00-\x7f]", code)
    if bad:
        raise _refuse(label, text, bad.start(), "a non-ASCII character", "outside comments and strings Verilog is ASCII", family="input.invalid")
    for match in re.finditer(r"\$", code):
        index = match.start()
        if index > 0 and _WORD.match(code[index - 1]):
            continue  # inside an identifier such as a$b
        named = _SYS_NAME.match(code, index + 1)
        if named is None:
            raise _refuse(label, text, index, "a '$'", "it must start a system task name")
        word = named.group()
        following = code[named.end() : named.end() + 1]
        if word not in ALLOWED_SYSTEM_TASKS or following == "`":
            raise _refuse(
                label,
                text,
                index,
                f"${_clip(word, 30)}",
                "system tasks that read or write files or run programs are not allowed; allowed: " + ", ".join(f"${t}" for t in sorted(ALLOWED_SYSTEM_TASKS)),
            )


def find_module(sources: list[Source], name: str, *, param: str) -> None:
    """Raise ``input.invalid`` unless some source defines ``name``."""
    found = sorted({m for s in sources for m in s.modules})
    if name not in found:
        raise ToolFailure(
            "input.invalid",
            f"{param}: no module named {name!r} in the source (modules found: {', '.join(found[:20]) or 'none'}{', ...' if len(found) > 20 else ''})",
            {"param": param, "modules": found[:50]},
        )


def describe_limits() -> dict[str, Any]:
    return {
        "max_source_bytes": MAX_SOURCE_BYTES,
        "max_line_chars": MAX_LINE_CHARS,
        "max_modules": MAX_MODULES,
        "allowed_directives": sorted(ALLOWED_DIRECTIVES),
        "allowed_system_tasks": sorted(ALLOWED_SYSTEM_TASKS),
    }


__all__ = [
    "ALLOWED_DIRECTIVES",
    "ALLOWED_SYSTEM_TASKS",
    "MAX_LINE_CHARS",
    "MAX_MODULES",
    "MAX_SOURCE_BYTES",
    "Source",
    "decode",
    "describe_limits",
    "find_module",
    "plain_identifier",
    "sanitize",
]
