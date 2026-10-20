"""A small, safe reader for KiCad's S-expression files (``.kicad_pcb``, ``.kicad_sch``, ``.kicad_pro`` is JSON and is not read here).

Written for files that are not trusted:

* **iterative**: the parser keeps its own stack, so a file of a million ``(`` cannot overflow Python's recursion limit; and
  **depth-limited** (:data:`MAX_DEPTH`) on top of that, because no real board nests deeper than a dozen levels;
* **streaming**: :func:`top_level` yields the direct children of the root list one at a time and does not keep them, so
  the memory used is that of the largest single element (a footprint or a zone), not of the whole file;
* **bounded**: a token limit for the file, a limit on the length of one string, and a clear error with line and column for
  every way a file can be wrong (unbalanced parentheses, an unterminated string, a NUL byte, text that is not UTF-8).

Nothing is evaluated: the file is text, the result is nested Python lists of ``str`` (a quoted string and a bare atom both
become ``str``).
"""

from __future__ import annotations

import re
from typing import Iterator

MAX_DEPTH = 64  # a board nests about 8 levels; the deepest real thing (a footprint's custom pad primitive) is about 12
MAX_TOKENS = 15_000_000
MAX_STRING_CHARS = 16_000_000  # one quoted string (an embedded font or model is a long one)
MAX_FILE_BYTES = 50_000_000


class SexpError(ValueError):
    """The text is not a usable S-expression.  ``kind`` is ``syntax``, ``depth``, ``too_large`` or ``encoding``."""

    def __init__(self, kind: str, message: str, line: int = 0, column: int = 0, **details):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.line = line
        self.column = column
        self.details = details


# One alternative per token class.  The string patterns are "unrolled" (no nested quantifier), so there is no
# catastrophic backtracking and no recursion inside ``re`` however long the string is.
_TOKEN = re.compile(
    r"""
      (?P<space>[ \t\r\n]+)
    | (?P<open>\()
    | (?P<close>\))
    | "(?P<dq>[^"\\]*(?:\\.[^"\\]*)*)"
    | \|(?P<bar>[^|]*)\|
    | (?P<atom>[^\s()"|]+)
    """,
    re.VERBOSE | re.DOTALL,
)
_ESCAPE = re.compile(r"\\(.)", re.DOTALL)
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r"}


def _unescape(raw: str) -> str:
    if "\\" not in raw:
        return raw
    return _ESCAPE.sub(lambda m: _ESCAPES.get(m.group(1), m.group(1)), raw)


def decode(data: bytes) -> str:
    """The text of a KiCad file: UTF-8 (a BOM is dropped), no NUL.  ``SexpError('encoding')`` otherwise."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    nul = data.find(b"\x00")
    if nul >= 0:
        raise SexpError("encoding", "the file holds a NUL byte, so it is not a KiCad text file", byte_offset=nul)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SexpError("encoding", f"the file is not UTF-8 text (invalid byte at offset {exc.start})", byte_offset=exc.start) from exc


def _where(text: str, pos: int) -> tuple[int, int]:
    line = text.count("\n", 0, pos) + 1
    return line, pos - (text.rfind("\n", 0, pos) + 1) + 1


def top_level(text: str) -> Iterator[list]:
    """Yield the children of the single root list, each fully parsed as nested lists, and the root's own head first.

    The first item yielded is ``[head]`` for the root (``['kicad_pcb']``); after it come the root's children in file order.
    Raises :class:`SexpError` for anything that is not exactly one balanced list (trailing text after it, a second root, an
    unterminated string, unbalanced parentheses, too deep, too many tokens).
    """
    stack: list[list] = []
    tokens = 0
    pos = 0
    end = len(text)
    root_head_sent = False
    started = False
    finished = False
    match = _TOKEN.match
    while pos < end:
        m = match(text, pos)
        if m is None:
            line, col = _where(text, pos)
            if text[pos] == '"':
                raise SexpError("syntax", f"unterminated string starting at line {line}, column {col}", line, col)
            if text[pos] == "|":
                raise SexpError("syntax", f"unterminated |...| string starting at line {line}, column {col}", line, col)
            raise SexpError("syntax", f"unexpected character {text[pos]!r} at line {line}, column {col}", line, col)
        kind = m.lastgroup
        pos = m.end()
        if kind == "space":
            continue
        tokens += 1
        if tokens > MAX_TOKENS:
            raise SexpError("too_large", f"more than {MAX_TOKENS} tokens", limit=MAX_TOKENS)
        if finished:
            line, col = _where(text, m.start())
            raise SexpError("syntax", f"text after the closing parenthesis of the file's root list (line {line}, column {col})", line, col)
        if kind == "open":
            if len(stack) >= MAX_DEPTH:
                line, col = _where(text, m.start())
                raise SexpError("depth", f"parentheses nested deeper than {MAX_DEPTH} levels at line {line}, column {col}", line, col, limit=MAX_DEPTH)
            stack.append([])
            started = True
        elif kind == "close":
            if not stack:
                line, col = _where(text, m.start())
                raise SexpError("syntax", f"')' without a matching '(' at line {line}, column {col}", line, col)
            done = stack.pop()
            if not stack:
                finished = True
                if not root_head_sent:  # a root without children: "(kicad_pcb)"
                    yield [done[0]] if done else []
                    root_head_sent = True
            elif len(stack) == 1:
                if not root_head_sent:
                    yield stack[0][:1]
                    root_head_sent = True
                yield done
            else:
                stack[-1].append(done)
        else:
            if not started:
                line, col = _where(text, m.start())
                raise SexpError("syntax", f"the file does not start with '(' (line {line}, column {col})", line, col)
            value: str
            if kind == "atom":
                value = m.group("atom")
            else:
                raw = m.group("dq") if kind == "dq" else m.group("bar")
                if len(raw) > MAX_STRING_CHARS:
                    line, col = _where(text, m.start())
                    raise SexpError("too_large", f"a string of {len(raw)} characters at line {line} (limit {MAX_STRING_CHARS})", line, col, limit=MAX_STRING_CHARS)
                value = _unescape(raw) if kind == "dq" else raw
            if len(stack) == 1 and not root_head_sent:
                stack[0].append(value)
                continue
            stack[-1].append(value)
    if stack:
        line, col = _where(text, end)
        raise SexpError("syntax", f"{len(stack)} unclosed '(' at the end of the file (line {line}); the file is cut off or unbalanced", line, col, unclosed=len(stack))
    if not started:
        raise SexpError("syntax", "the file holds no S-expression")


def head(node: list) -> str:
    """The first atom of a list (its name), or ``''`` when the list is empty or starts with a list."""
    return node[0] if node and isinstance(node[0], str) else ""


def children(node: list, name: str) -> Iterator[list]:
    """The direct child lists of ``node`` whose head is ``name``."""
    for item in node:
        if isinstance(item, list) and item and item[0] == name:
            yield item


def child(node: list, name: str) -> list | None:
    for item in children(node, name):
        return item
    return None


__all__ = ["MAX_DEPTH", "MAX_FILE_BYTES", "MAX_STRING_CHARS", "MAX_TOKENS", "SexpError", "child", "children", "decode", "head", "top_level"]
