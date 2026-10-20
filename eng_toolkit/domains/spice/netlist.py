"""The allow-list parser for netlists.

An ngspice netlist is a program (``.control`` runs shell commands, ``.include`` reads any file, ``.osdi`` and the A devices load code,
``.write`` writes anywhere).  A caller's text is therefore only ever **data** here: it is parsed line by line against an allow-list, every
accepted line is rewritten in a normal form, and the text handed to ngspice is built from that normal form plus a script this module's
caller writes from validated parameters.  What was checked is what runs, because the checked text is what is emitted:

1. Intake: UTF-8 only, no NUL or other control characters, at most :data:`MAX_BYTES` bytes and :data:`MAX_LINES` lines.
2. Lines: the first line is the title and is **never** forwarded (the deck gets a fixed title); a title that starts with ``.`` is refused,
   because some tools read it as a directive.  Comment lines (``*``) and inline comments (``;``, ``//``, ``$`` after a blank) are dropped.
   A ``+`` line is joined to the logical line above it *before* anything is checked, so a word split across continuation lines is seen whole.
   Tabs become blanks, the text is lower-cased (SPICE is case-insensitive), blanks around ``=`` are removed.
3. Every logical line must use only :data:`ALLOWED_CHARS` (no backslash, quote marks, ``$``, ``;``, backtick, non-ASCII), start with an allowed
   directive (exact match) or an allowed element letter, and pass the checks of its kind.  Nothing else is accepted: any other directive, any
   other element letter (A, N, O, P, U, Y, Z), ``file`` and ``external`` anywhere, random functions, text after ``.end``.
4. Numbers: every numeric literal, with its SPICE suffix, must be finite and between 1e-30 and 1e30 in magnitude (or zero).
5. Work caps: expanded element count (subcircuits are counted as many times as they are instanced; recursion is refused).

Refusals are :class:`~eng_toolkit.errors.ToolFailure` of family ``input.netlist_refused`` (policy), ``input.too_large`` (size and count caps),
``input.out_of_range`` (a magnitude) or ``input.invalid`` (encoding); the message names the line and the directive and never echoes more than 80
characters of the line.  Structural faults a simulator would trip over (no ground, a missing ``.model``, a duplicate name) are not refusals: they
are collected in :attr:`Netlist.problems`; ``spice_check_netlist`` reports them and ``spice_simulate`` stops before it starts ngspice.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from eng_toolkit.errors import ToolFailure

MAX_BYTES = 200_000
MAX_LINES = 5_000
MAX_LOGICAL_LINE = 2_000
MAX_ELEMENTS = 1_000  # after expanding subcircuit instances
MAX_NESTING = 3
MAX_LITERAL = 1e30
MIN_LITERAL = 1e-30
EXCERPT = 80

REFUSED = "input.netlist_refused"

ELEMENT_LETTERS = frozenset("rclvidqmjswtkxefgb")
# Letters ngspice knows that are refused, with the reason shown to the caller.
REFUSED_LETTERS = {
    "a": "an A device is an XSPICE code model, which loads compiled code",
    "n": "an N device is an OSDI device, which loads compiled code",
    "o": "an O device (lossy line) is not supported",
    "p": "a P device (coupled line) is not supported",
    "u": "a U device (distributed RC line) is not supported",
    "y": "a Y device (TXL line) is not supported",
    "z": "a Z device (HFET/MESFET) is not supported",
}
DIRECTIVES = frozenset({".model", ".param", ".subckt", ".ends", ".global", ".ic", ".nodeset", ".temp", ".options", ".option", ".end"})
# What a directive that is refused is for, so the message is useful.
KNOWN_DANGEROUS = {
    ".control": "a .control section runs commands, including shell commands",
    ".endc": "the end of a .control section",
    ".include": "it reads any file",
    ".inc": "it reads any file",
    ".lib": "it reads any file",
    ".osdi": "it loads compiled code",
    ".pre_osdi": "it loads compiled code",
    ".write": "it writes a file",
    ".save": "it names output files and vectors",
    ".csparam": "it writes into the control section's variables",
    ".print": "output cards are not used, outputs come from the tool parameters",
    ".plot": "output cards are not used, outputs come from the tool parameters",
    ".probe": "output cards are not used, outputs come from the tool parameters",
    ".meas": "measurements are not run in ngspice here, use spice_measure",
    ".measure": "measurements are not run in ngspice here, use spice_measure",
    ".tran": "the analysis is chosen by the tool parameters, not by the netlist",
    ".ac": "the analysis is chosen by the tool parameters, not by the netlist",
    ".dc": "the analysis is chosen by the tool parameters, not by the netlist",
    ".op": "the analysis is chosen by the tool parameters, not by the netlist",
    ".noise": "the analysis is chosen by the tool parameters, not by the netlist",
    ".func": "functions are not supported",
    ".if": "conditional netlists are not supported",
    ".title": "put the title on the first line",
    ".temp.": "",
}

ALLOWED_CHARS = re.compile(r"^[a-z0-9 _.,+\-*/^()=<>!&|{}'\[\]:#@~?]*$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INLINE_COMMENT = re.compile(r";|//|(?:^|(?<=\s))\$")
_NAME = re.compile(r"^[a-z][a-z0-9_.\-]{0,39}$")
_NODE = re.compile(r"^[a-z0-9_][a-z0-9_.\-#]{0,59}$")
_NODE_REF = re.compile(r"\bv\(\s*([a-z0-9_][a-z0-9_.\-#]*)\s*(?:,\s*([a-z0-9_][a-z0-9_.\-#]*)\s*)?\)")
_LITERAL = re.compile(r"(?<![a-z0-9_.#])(\d+\.?\d*|\.\d+)(e[+-]?\d+)?([a-z]*)")
_SCALE = {"t": 1e12, "g": 1e9, "k": 1e3, "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15, "a": 1e-18}
_WORDS = re.compile(r"[a-z0-9_]+")
# Words that may not appear anywhere in an accepted line.
FORBIDDEN_WORDS = {
    "file": "it names a file",
    "filename": "it names a file",
    "external": "an external source takes its value from outside the simulator",
    "gauss": "random functions make a run unrepeatable",
    "agauss": "random functions make a run unrepeatable",
    "unif": "random functions make a run unrepeatable",
    "aunif": "random functions make a run unrepeatable",
    "rand": "random functions make a run unrepeatable",
    "rnd": "random functions make a run unrepeatable",
    "sdrnd": "random functions make a run unrepeatable",
    "trnoise": "noise sources make a run unrepeatable",
    "trrandom": "random sources make a run unrepeatable",
}
# ngspice command words.  They only run inside a .control section, which is refused, but an element named like one (an S element called "shell")
# is refused as well so that nothing in an accepted netlist reads as a command, in any case or abbreviation of at least three letters.
COMMAND_WORDS = (
    "shell", "system", "source", "exec", "echo", "set", "unset", "write", "load", "codemodel", "osdi", "edit", "wrdata", "wrs2p", "hardcopy", "spice",
)
MODEL_TYPES = frozenset({"r", "c", "l", "sw", "csw", "d", "npn", "pnp", "njf", "pjf", "nmos", "pmos", "nmf", "pmf", "vdmos"})
OPTION_LIMITS: dict[str, tuple[float, float]] = {
    "reltol": (1e-9, 0.5),
    "abstol": (1e-30, 1.0),
    "vntol": (1e-30, 1.0),
    "chgtol": (1e-30, 1.0),
    "trtol": (0.1, 100.0),
    "gmin": (1e-30, 1e-3),
    "itl1": (1, 10_000),
    "itl2": (1, 10_000),
    "itl4": (1, 10_000),
    "maxord": (2, 6),
    "temp": (-273.15, 1000.0),
    "tnom": (-273.15, 1000.0),
    "pivtol": (1e-30, 1.0),
    "pivrel": (1e-30, 1.0),
    "scale": (1e-30, 1e30),
    "defl": (1e-9, 1.0),
    "defw": (1e-9, 1.0),
    "defad": (0.0, 1.0),
    "defas": (0.0, 1.0),
    "rshunt": (1.0, 1e30),
    "cshunt": (1e-30, 1e-3),
    "gminsteps": (0, 1000),
    "srcsteps": (0, 1000),
}
OPTION_WORDS = {"method": {"trap", "trapezoidal", "gear"}}


def excerpt(text: str, limit: int = EXCERPT) -> str:
    """At most ``limit`` printable ASCII characters of ``text`` (anything else escaped), ``...`` when cut."""
    safe = text.encode("ascii", "backslashreplace").decode("ascii")
    safe = re.sub(r"[\x00-\x1f\x7f]", " ", safe)
    return safe if len(safe) <= limit else safe[: limit - 3] + "..."


def refuse(line: int, directive: str, reason: str, *, text: str = "", family: str = REFUSED, code: str = "refused", **extra) -> ToolFailure:
    d = excerpt(directive, 40)
    return ToolFailure(
        family,
        f"line {line}: {reason} ({d!r}; line starts {excerpt(text, EXCERPT)!r})" if text else f"line {line}: {reason} ({d!r})",
        {"line": line, "directive": d, "reason_code": code, **extra},
    )


# --------------------------------------------------------------------------- the result of a parse


@dataclass
class Element:
    name: str
    letter: str
    line: int
    nodes: list[str] = field(default_factory=list)
    conducts: bool = True  # gives a DC path between its nodes
    model: str = ""
    refs: list[str] = field(default_factory=list)  # names of sources/inductors/subcircuits this element refers to
    positional: list[str] = field(default_factory=list)  # Q only: nodes then model, resolved when the models are known


@dataclass
class Scope:
    name: str  # "" for the top level, the subcircuit name inside .subckt
    line: int = 0
    ports: list[str] = field(default_factory=list)
    elements: list[Element] = field(default_factory=list)
    models: dict[str, str] = field(default_factory=dict)
    node_refs: list[tuple[int, str]] = field(default_factory=list)
    ended: bool = True


@dataclass
class Netlist:
    title: str
    body: list[str]  # the normalised lines to send, without title, control script or .end
    scopes: dict[str, Scope]  # "" is the top level
    top: Scope
    nodes: list[str]  # non-ground nodes of the top level in order of first use
    has_ground: bool
    expanded_elements: int
    problems: list[dict]
    warnings: list[str]
    options: dict[str, str]
    params: list[str]

    @property
    def sources(self) -> dict[str, str]:
        """Top-level V and I sources and inductors by name -> letter (what a DC sweep or ``i()`` output can name)."""
        return {e.name: e.letter for e in self.top.elements if e.letter in "vil"}


# --------------------------------------------------------------------------- helpers


def split_tokens(text: str) -> list[str]:
    """Split on blanks that are outside (), {} and '...'; raises ValueError for unbalanced groups."""
    out: list[str] = []
    cur: list[str] = []
    depth = 0
    quote = False
    for ch in text:
        if ch == "'":
            quote = not quote
        elif not quote:
            if ch in "({[":
                depth += 1
            elif ch in ")}]":
                depth -= 1
                if depth < 0:
                    raise ValueError("unbalanced")
            elif ch == " " and depth == 0:
                if cur:
                    out.append("".join(cur))
                    cur = []
                continue
        cur.append(ch)
    if depth or quote:
        raise ValueError("unbalanced")
    if cur:
        out.append("".join(cur))
    return out


def spice_number(token: str) -> float | None:
    """The value of a SPICE number token (``1k``, ``2.2u``, ``1e-3``, ``5v``), or None if it is not one."""
    m = re.fullmatch(r"([+-]?(?:\d+\.?\d*|\.\d+))(e[+-]?\d+)?([a-z]*)", token)
    if not m:
        return None
    try:
        value = float(m.group(1) + (m.group(2) or ""))
    except (ValueError, OverflowError):
        return None
    suffix = m.group(3)
    if suffix.startswith("meg"):
        value *= 1e6
    elif suffix.startswith("mil"):
        value *= 25.4e-6
    elif suffix and suffix[0] in _SCALE:
        value *= _SCALE[suffix[0]]
    return value


def _check_literals(text: str, lineno: int, directive: str) -> None:
    for m in _LITERAL.finditer(text):
        try:
            value = float(m.group(1) + (m.group(2) or ""))
        except (ValueError, OverflowError):  # pragma: no cover - the pattern only matches digits
            value = math.inf
        suffix = m.group(3)
        if suffix.startswith("meg"):
            value *= 1e6
        elif suffix.startswith("mil"):
            value *= 25.4e-6
        elif suffix and suffix[0] in _SCALE:
            value *= _SCALE[suffix[0]]
        if not math.isfinite(value) or abs(value) > MAX_LITERAL or (value != 0.0 and abs(value) < MIN_LITERAL):
            raise refuse(
                lineno,
                directive,
                f"the number {excerpt(m.group(0), 24)!r} is outside the accepted magnitude range {MIN_LITERAL:g} .. {MAX_LITERAL:g}",
                family="input.out_of_range",
                code="magnitude",
                min=MIN_LITERAL,
                max=MAX_LITERAL,
            )


def _line_number(text: str, index: int) -> int:
    return len(re.findall(r"\r\n|\n|\r", text[:index])) + 1


# --------------------------------------------------------------------------- the parser


class _Parser:
    def __init__(self) -> None:
        self.top = Scope("")
        self.scopes: dict[str, Scope] = {"": self.top}
        self.stack: list[Scope] = [self.top]
        self.body: list[str] = []
        self.problems: list[dict] = []
        self.warnings: list[str] = []
        self.options: dict[str, str] = {}
        self.params: list[str] = []
        self.ended = False
        self.count = 0  # elements seen, unexpanded: a quick cap before the expansion walk

    @property
    def scope(self) -> Scope:
        return self.stack[-1]

    def problem(self, line: int, code: str, message: str) -> None:
        self.problems.append({"line": line, "code": code, "message": message})

    # -- directives -------------------------------------------------------------------------
    def directive(self, line: str, lineno: int) -> None:
        tokens = line.split(" ")
        head = tokens[0]
        if head not in DIRECTIVES:
            why = KNOWN_DANGEROUS.get(head)
            raise refuse(
                lineno,
                head,
                f"the directive {excerpt(head, 40)!r} is not allowed" + (f" ({why})" if why else "; allowed: " + ", ".join(sorted(DIRECTIVES))),
                text=line,
                code="directive",
            )
        if self.ended:
            raise refuse(lineno, head, "text after .end is not accepted", text=line, code="after_end")
        rest = line[len(head) :].strip()
        if head == ".end":
            if rest:
                raise refuse(lineno, head, ".end takes no arguments here", text=line, code="syntax")
            if len(self.stack) > 1:
                self.problem(lineno, "unclosed_subckt", f".end inside the subcircuit {self.scope.name!r}")
            self.ended = True
            return
        if head == ".model":
            self._model(rest, lineno, head, line)
        elif head == ".param":
            self._param(rest, lineno, head, line)
        elif head == ".subckt":
            self._subckt(rest, lineno, head, line)
        elif head == ".ends":
            self._ends(rest, lineno, head, line)
        elif head == ".global":
            for n in rest.split():
                if not _NODE.match(n):
                    raise refuse(lineno, head, f"{excerpt(n, 30)!r} is not a node name", text=line, code="syntax")
            self.body.append(line)
        elif head in (".ic", ".nodeset"):
            self._ic(rest, lineno, head, line)
        elif head == ".temp":
            values = rest.split()
            if not values or any(spice_number(v) is None for v in values) or len(values) > 8:
                raise refuse(lineno, head, ".temp takes up to 8 numbers", text=line, code="syntax")
            for v in values:
                if not -273.15 <= (spice_number(v) or 0.0) <= 1000.0:
                    raise refuse(lineno, head, "a temperature outside -273.15 .. 1000 degC", text=line, family="input.out_of_range", code="magnitude")
            self.body.append(line)
        else:  # .options / .option
            self._options(rest, lineno, head, line)

    def _model(self, rest: str, lineno: int, head: str, line: str) -> None:
        flat = re.sub(r"[()]", " ", rest)
        tokens = flat.split()
        if len(tokens) < 2 or not _NAME.match(tokens[0]):
            raise refuse(lineno, head, ".model needs a name and a type", text=line, code="syntax")
        kind = tokens[1]
        if kind not in MODEL_TYPES:
            raise refuse(lineno, head, f"the model type {excerpt(kind, 30)!r} is not allowed; allowed: {sorted(MODEL_TYPES)}", text=line, code="model_type")
        for item in tokens[2:]:
            if not re.fullmatch(r"[a-z][a-z0-9_]*=[a-z0-9_.+\-*/^{}'<>!&|?:\[\]@#~]+", item):
                raise refuse(lineno, head, f"a model parameter must be name=value, got {excerpt(item, 30)!r}", text=line, code="syntax")
        self.scope.models[tokens[0]] = kind
        self.body.append(f".model {tokens[0]} {kind}" + "".join(f" {t}" for t in tokens[2:]))

    def _param(self, rest: str, lineno: int, head: str, line: str) -> None:
        pos = 0
        items = []
        pattern = re.compile(r"\s*([a-z_][a-z0-9_]*)=(\{[^{}]*\}|'[^']*'|[^\s{}']+)")
        while pos < len(rest):
            m = pattern.match(rest, pos)
            if not m:
                raise refuse(lineno, head, ".param takes name=value pairs, a value being a number, {expression} or 'expression'", text=line, code="syntax")
            items.append(f"{m.group(1)}={m.group(2)}")
            pos = m.end()
        if not items:
            raise refuse(lineno, head, ".param takes name=value pairs", text=line, code="syntax")
        self.params.extend(i.split("=", 1)[0] for i in items)
        self.body.append(".param " + " ".join(items))

    def _subckt(self, rest: str, lineno: int, head: str, line: str) -> None:
        if len(self.stack) > MAX_NESTING:
            raise refuse(lineno, head, f"subcircuits nested deeper than {MAX_NESTING}", text=line, family="input.too_large", code="nesting")
        try:
            tokens = split_tokens(rest)
        except ValueError:
            raise refuse(lineno, head, "unbalanced brackets", text=line, code="syntax") from None
        if not tokens or not _NAME.match(tokens[0]):
            raise refuse(lineno, head, ".subckt needs a name", text=line, code="syntax")
        name = tokens[0]
        ports: list[str] = []
        tail = []
        for i, t in enumerate(tokens[1:], 1):
            if t == "params:" or "=" in t:
                tail = tokens[i:]
                break
            ports.append(t)
        for p in ports:
            if not _NODE.match(p):
                raise refuse(lineno, head, f"{excerpt(p, 30)!r} is not a node name", text=line, code="syntax")
        for t in tail:
            if t != "params:" and not re.fullmatch(r"[a-z_][a-z0-9_]*=.+", t):
                raise refuse(lineno, head, f"{excerpt(t, 30)!r} is not name=value", text=line, code="syntax")
        if name in self.scopes:
            self.problem(lineno, "duplicate_subckt", f"the subcircuit {name!r} is defined twice")
        scope = Scope(name, lineno, ports, ended=False)
        self.scopes[name] = scope
        self.stack.append(scope)
        self.body.append(".subckt " + " ".join([name, *ports, *tail]))

    def _ends(self, rest: str, lineno: int, head: str, line: str) -> None:
        if len(self.stack) == 1:
            self.problem(lineno, "stray_ends", ".ends without a matching .subckt")
            return
        words = rest.split()
        if len(words) > 1 or (words and words[0] != self.scope.name):
            self.problem(lineno, "ends_name", f".ends names {excerpt(rest, 30)!r}, the open subcircuit is {self.scope.name!r}")
        self.scope.ended = True
        self.stack.pop()
        self.body.append(".ends")

    def _ic(self, rest: str, lineno: int, head: str, line: str) -> None:
        items = rest.split()
        if not items:
            raise refuse(lineno, head, f"{head} takes v(node)=value pairs", text=line, code="syntax")
        for item in items:
            m = re.fullmatch(r"v\(([a-z0-9_][a-z0-9_.\-#]*)\)=([+\-0-9.a-z]+)", item)
            if not m or spice_number(m.group(2)) is None:
                raise refuse(lineno, head, f"{head} takes v(node)=number pairs, got {excerpt(item, 30)!r}", text=line, code="syntax")
            self.scope.node_refs.append((lineno, m.group(1)))
        self.body.append(f"{head} " + " ".join(items))

    def _options(self, rest: str, lineno: int, head: str, line: str) -> None:
        items = rest.split()
        if not items:
            raise refuse(lineno, head, f"{head} needs key=value pairs", text=line, code="syntax")
        clean = []
        for item in items:
            key, sep, value = item.partition("=")
            if not sep:
                raise refuse(lineno, head, f"the option {excerpt(key, 30)!r} is not allowed (only key=value options from the allow-list)", text=line, code="option")
            if key in OPTION_WORDS:
                if value not in OPTION_WORDS[key]:
                    raise refuse(lineno, head, f"method must be one of {sorted(OPTION_WORDS[key])}", text=line, code="option")
            elif key in OPTION_LIMITS:
                number = spice_number(value)
                lo, hi = OPTION_LIMITS[key]
                if number is None:
                    raise refuse(lineno, head, f"the option {key} takes a number", text=line, code="option")
                if not lo <= number <= hi:
                    raise refuse(lineno, head, f"the option {key}={excerpt(value, 20)} is outside {lo:g} .. {hi:g}", text=line, family="input.out_of_range", code="magnitude")
            else:
                raise refuse(lineno, head, f"the option {excerpt(key, 30)!r} is not allowed; allowed: {sorted([*OPTION_LIMITS, *OPTION_WORDS])}", text=line, code="option")
            clean.append(f"{key}={value}")
            self.options[key] = value
        self.body.append(".options " + " ".join(clean))

    # -- elements ---------------------------------------------------------------------------
    def element(self, line: str, lineno: int) -> None:
        if self.ended:
            raise refuse(lineno, line.split(" ")[0], "text after .end is not accepted", text=line, code="after_end")
        try:
            tokens = split_tokens(line)
        except ValueError:
            raise refuse(lineno, line.split(" ")[0], "unbalanced brackets or quotes", text=line, code="syntax") from None
        name = tokens[0]
        letter = name[0]
        if not _NAME.match(name):
            raise refuse(lineno, name, f"the element name {excerpt(name, 30)!r} must be a letter followed by letters, digits, _ . -", text=line, code="name")
        for word in COMMAND_WORDS:
            if name == word or (len(name) >= 3 and word.startswith(name)):
                raise refuse(lineno, name, f"the element name {excerpt(name, 30)!r} is an ngspice command word or an abbreviation of one; rename it (R1, Rload, ...)", text=line, code="command_name")
        self.count += 1
        if self.count > MAX_ELEMENTS * 4:
            raise refuse(lineno, name, f"more than {MAX_ELEMENTS * 4} element lines", text="", family="input.too_large", code="elements", limit=MAX_ELEMENTS * 4)
        el = Element(name, letter, lineno)
        pos = [t for t in tokens[1:] if "=" not in t]
        # positional fields every letter needs (nodes, then model or controlling source); fewer is a problem, not a refusal
        need = {"r": 2, "c": 2, "l": 2, "v": 2, "i": 2, "b": 2, "e": 2, "g": 2, "d": 3, "m": 5, "j": 4, "s": 5, "w": 4, "f": 3, "h": 3, "k": 3, "t": 4}

        def nodes(n: int) -> list[str]:
            return pos[:n]

        if letter in "rclvib":
            el.nodes = nodes(2)
            el.conducts = letter in "rlv" or (letter == "b" and bool(re.match(r"b\S*\s+\S+\s+\S+\s+v=", line)))
            if letter in "rcl" and len(tokens) < 4:
                self.problem(lineno, "missing_value", f"{name} has no value")
            if letter in "rcl" and len(tokens) >= 4:
                value = tokens[3]
                if "=" not in value and spice_number(value) is None and value[0] not in "{'" and value not in self.scope.models and value not in self.top.models:
                    self.warnings.append(f"line {lineno}: the value {excerpt(value, 30)!r} of {name} is not a number or an expression in braces")
        elif letter == "d":
            el.nodes, el.model = nodes(2), (pos[2] if len(pos) > 2 else "")
        elif letter == "q":
            el.positional = list(pos)
        elif letter == "m":
            el.nodes, el.model = nodes(4), (pos[4] if len(pos) > 4 else "")
        elif letter == "j":
            el.nodes, el.model = nodes(3), (pos[3] if len(pos) > 3 else "")
        elif letter == "s":
            el.nodes, el.model = nodes(4), (pos[4] if len(pos) > 4 else "")
        elif letter == "w":
            el.nodes, el.model = nodes(2), (pos[3] if len(pos) > 3 else "")
            el.refs = [pos[2]] if len(pos) > 2 else []
        elif letter in "eg":
            controlled = len(pos) > 2 and not pos[2].startswith(("poly", "table", "value", "vol", "cur"))
            el.nodes = nodes(4 if controlled else 2)
            el.conducts = letter == "e"
        elif letter in "fh":
            el.nodes = nodes(2)
            if len(pos) > 2 and not pos[2].startswith("poly"):
                el.refs = [pos[2]]
            el.conducts = letter == "h"
        elif letter == "k":
            el.refs = pos[:2]
            el.conducts = False
        elif letter == "t":
            el.nodes = nodes(4)
        elif letter == "x":
            ports = []
            for t in tokens[1:]:
                if "=" in t or t == "params:":
                    break
                ports.append(t)
            if not ports:
                raise refuse(lineno, name, "a subcircuit instance needs a subcircuit name", text=line, code="syntax")
            el.model = ports[-1]
            el.nodes = ports[:-1]
            el.refs = [ports[-1]]
        if letter in need and len(pos) < need[letter]:
            self.problem(lineno, "missing_fields", f"{name} has fewer fields than a {letter.upper()} element needs")
        for n in el.nodes:
            if not _NODE.match(n):
                self.problem(lineno, "bad_node", f"{excerpt(n, 30)!r} is not a valid node name (in {name})")
        for m in _NODE_REF.finditer(line):
            for g in m.groups():
                if g:
                    self.scope.node_refs.append((lineno, g))
        self.scope.elements.append(el)
        self.body.append(line)


# --------------------------------------------------------------------------- driver


def parse(text: str) -> Netlist:
    if not isinstance(text, str):
        raise ToolFailure("input.invalid", "the netlist must be text", {})
    try:
        raw = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ToolFailure("input.invalid", "the netlist is not valid UTF-8 text (it holds a lone surrogate)", {"reason_code": "encoding", "at": exc.start}) from None
    if len(raw) > MAX_BYTES:
        raise ToolFailure("input.too_large", f"the netlist is {len(raw)} bytes, over the {MAX_BYTES} byte limit", {"bytes": len(raw), "limit_bytes": MAX_BYTES})
    bad = _CONTROL.search(text)
    if bad:
        code = ord(bad.group(0))
        raise ToolFailure(
            "input.invalid",
            f"line {_line_number(text, bad.start())}: the netlist holds the control character U+{code:04X}" + (" (NUL)" if code == 0 else ""),
            {"line": _line_number(text, bad.start()), "reason_code": "control_character", "codepoint": code},
        )
    physical = re.split(r"\r\n|\n|\r", text.lstrip("﻿"))
    if len(physical) > MAX_LINES:
        raise ToolFailure("input.too_large", f"the netlist has {len(physical)} lines, over the {MAX_LINES} line limit", {"lines": len(physical), "limit_lines": MAX_LINES})

    title = physical[0]
    stripped_title = title.strip()
    p = _Parser()
    warnings = p.warnings
    if stripped_title.startswith("."):
        raise refuse(1, stripped_title.split()[0] if stripped_title.split() else ".", "the first line is the title and may not start with '.' (put a title line such as '* my circuit' first)", text=stripped_title, code="title_directive")
    first = stripped_title.lower().split()
    if len(first) >= 3 and first[0][0] in ELEMENT_LETTERS and any(c.isdigit() for c in first[0]) and _NAME.match(first[0]):
        warnings.append(f"the first line {excerpt(stripped_title, 40)!r} looks like an element, but the first line is the title and is not simulated; add a title line above the circuit")

    logical: list[tuple[int, str]] = []
    open_line = False
    for index, original in enumerate(physical[1:], start=2):
        cut = _INLINE_COMMENT.search(original)
        line = (original[: cut.start()] if cut else original).replace("\t", " ").strip()
        if not line:
            continue
        if line.startswith("*"):
            continue
        if line.startswith("+"):
            if not open_line:
                raise refuse(index, "+", "a continuation line with nothing to continue", text=line, code="continuation")
            number, previous = logical[-1]
            joined = previous + " " + line[1:].strip()
            if len(joined) > MAX_LOGICAL_LINE:
                raise refuse(index, "+", f"a logical line longer than {MAX_LOGICAL_LINE} characters", text="", family="input.too_large", code="line_length", limit=MAX_LOGICAL_LINE)
            logical[-1] = (number, joined)
            continue
        if len(line) > MAX_LOGICAL_LINE:
            raise refuse(index, line.split()[0], f"a line longer than {MAX_LOGICAL_LINE} characters", text="", family="input.too_large", code="line_length", limit=MAX_LOGICAL_LINE)
        logical.append((index, line))
        open_line = True

    for lineno, raw_line in logical:
        if not raw_line.isascii():  # before lower(): U+212A lower-cases to an ASCII 'k'
            odd = next(ch for ch in raw_line if not ch.isascii())
            raise refuse(lineno, raw_line.split()[0] if raw_line.split() else "", f"the character U+{ord(odd):04X} is not allowed in a circuit line", text=raw_line, code="character")
        line = re.sub(r"\s+", " ", raw_line.lower()).strip()
        line = re.sub(r"\s*=\s*", "=", line)
        head = line.split(" ")[0]
        if head.startswith("."):
            if head not in DIRECTIVES:
                why = KNOWN_DANGEROUS.get(head)
                raise refuse(
                    lineno,
                    head,
                    f"the directive {excerpt(head, 40)!r} is not allowed" + (f" ({why})" if why else "; allowed: " + ", ".join(sorted(DIRECTIVES))),
                    text=line,
                    code="directive",
                )
        elif head[0] in REFUSED_LETTERS:
            raise refuse(lineno, head, f"the element {excerpt(head, 30)!r} is not allowed: {REFUSED_LETTERS[head[0]]}", text=line, code="element_letter")
        elif head[0] not in ELEMENT_LETTERS:
            raise refuse(lineno, head, "the line is not a comment, a directive or an element", text=line, code="unrecognised")
        if not ALLOWED_CHARS.match(line):
            bad_char = next(ch for ch in line if not ALLOWED_CHARS.match(ch))
            raise refuse(lineno, head, f"the character U+{ord(bad_char):04X} is not allowed in a circuit line", text=line, code="character")
        for word in _WORDS.findall(line):
            if word in FORBIDDEN_WORDS:
                raise refuse(lineno, head, f"the word {word!r} is not allowed ({FORBIDDEN_WORDS[word]})", text=line, code="word")
        hidden = re.search(r"(?<![a-z0-9_])\.[a-z]\w*", line[len(head) :])
        if hidden:
            raise refuse(lineno, hidden.group(0), f"a directive-like word {hidden.group(0)!r} inside a line (a directive hidden behind a continuation line?)", text=line, code="hidden_directive")
        _check_literals(line[len(head) :], lineno, head)
        if head.startswith("."):
            p.directive(line, lineno)
        else:
            p.element(line, lineno)

    if len(p.stack) > 1:
        p.problem(p.scope.line, "unclosed_subckt", f"the subcircuit {p.scope.name!r} has no .ends")
    return _finish(p, title=stripped_title)


def _finish(p: _Parser, *, title: str) -> Netlist:
    top = p.top
    all_models = dict(top.models)

    def model_known(scope: Scope, name: str) -> bool:
        return name in scope.models or name in all_models

    # Resolve Q: the first positional token from the 4th on that names a model ends the node list.
    for scope in p.scopes.values():
        for el in scope.elements:
            if el.letter == "q":
                for i in range(3, len(el.positional)):
                    if model_known(scope, el.positional[i]):
                        el.nodes, el.model = el.positional[:i], el.positional[i]
                        break
                else:
                    el.nodes = el.positional[:3]
                    el.model = el.positional[3] if len(el.positional) > 3 else ""
                if len(el.nodes) not in (3, 4):
                    p.problem(el.line, "missing_fields", f"{el.name} needs 3 or 4 nodes and a model")

    for scope in p.scopes.values():
        seen: dict[str, int] = {}
        names = {e.name: e for e in scope.elements}
        for el in scope.elements:
            if el.name in seen:
                p.problem(el.line, "duplicate_element", f"the element name {el.name!r} is used twice (first on line {seen[el.name]})")
            seen.setdefault(el.name, el.line)
            if el.letter in "dqmjsw":
                if not el.model:
                    p.problem(el.line, "missing_model", f"{el.name} needs a model name")
                elif not model_known(scope, el.model):
                    p.problem(el.line, "missing_model", f"{el.name} uses the model {el.model!r}, which no .model defines")
            if el.letter == "x":
                sub = p.scopes.get(el.model)
                if sub is None or el.model == "":
                    p.problem(el.line, "missing_subckt", f"{el.name} instances the subcircuit {el.model!r}, which no .subckt defines")
                elif len(sub.ports) != len(el.nodes):
                    p.problem(el.line, "port_mismatch", f"{el.name} connects {len(el.nodes)} nodes but {el.model!r} has {len(sub.ports)} ports")
            if el.letter in "fhw":
                for ref in el.refs:
                    if ref not in names or names[ref].letter != "v":
                        p.problem(el.line, "missing_source", f"{el.name} refers to the voltage source {ref!r}, which is not defined in the same scope")
            if el.letter == "k":
                for ref in el.refs:
                    if ref not in names or names[ref].letter != "l":
                        p.problem(el.line, "missing_inductor", f"{el.name} couples the inductor {ref!r}, which is not defined in the same scope")

    expanded = _expanded_count(p)
    if expanded > MAX_ELEMENTS:
        raise ToolFailure(
            "input.too_large",
            f"the netlist expands to {expanded} elements (subcircuit instances counted), over the {MAX_ELEMENTS} element limit",
            {"elements": expanded, "limit_elements": MAX_ELEMENTS, "reason_code": "elements"},
        )

    ground = {"0", "gnd"}
    order: list[str] = []
    incidence: dict[str, int] = {}
    has_ground = False
    for el in top.elements:
        for n in el.nodes:
            if n in ground:
                has_ground = True
                continue
            incidence[n] = incidence.get(n, 0) + 1
            if n not in order:
                order.append(n)
    if not top.elements:
        p.problem(1, "empty", "the netlist has no elements")
    elif not has_ground:
        p.problem(1, "no_ground", "no element is connected to the ground node 0; every circuit needs a reference node")
    for lineno, ref in top.node_refs:
        if ref not in ground and ref not in incidence:
            p.problem(lineno, "unknown_node", f"v({excerpt(ref, 30)}) refers to a node that no element uses")
    for n in order:
        if incidence[n] == 1:
            p.warnings.append(f"node {n!r} has only one connection (dangling)")
    # DC path to ground: union over elements that conduct at DC; unknown subcircuits count as conducting.
    parent: dict[str, str] = {}

    def find(a: str) -> str:
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for el in top.elements:
        group = [("0" if n in ground else n) for n in el.nodes]
        if el.letter in "eh" or el.letter in "sw":
            pairs = group[:2]
        elif el.letter == "m":
            pairs = [group[0], group[2], group[3]] if len(group) == 4 else group
        elif el.letter in "dqjtx" or el.conducts:
            pairs = group
        else:
            pairs = []
        for a in pairs[1:]:
            parent[find(a)] = find(pairs[0])
        for a in group:
            find(a)
    if has_ground:
        root = find("0")
        lost = [n for n in order if find(n) != root]
        if lost:
            p.warnings.append(
                f"no DC path to ground from node(s) {', '.join(repr(n) for n in lost[:6])}"
                + (" ..." if len(lost) > 6 else "")
                + " (only capacitors, current sources or gates reach them); ngspice may report a singular matrix, add a large resistor to ground"
            )
    return Netlist(
        title=title,
        body=p.body,
        scopes=p.scopes,
        top=top,
        nodes=order,
        has_ground=has_ground,
        expanded_elements=expanded,
        problems=sorted(p.problems, key=lambda d: d["line"]),
        warnings=p.warnings,
        options=p.options,
        params=p.params,
    )


def _expanded_count(p: _Parser) -> int:
    """Elements of the top level with every subcircuit instance replaced by its content (recursion raises a refusal)."""
    memo: dict[str, int] = {}

    def count(scope: Scope, path: tuple[str, ...]) -> int:
        if scope.name in memo:
            return memo[scope.name]
        total = 0
        for el in scope.elements:
            total += 1
            if el.letter == "x":
                sub = p.scopes.get(el.model)
                if sub is None:
                    continue
                if sub.name in path or sub is scope:
                    raise refuse(el.line, el.name, f"the subcircuit {sub.name!r} contains itself (recursion)", code="recursion")
                total += count(sub, (*path, scope.name))
                if total > MAX_ELEMENTS * 100:
                    break
        memo[scope.name] = total
        return total

    return count(p.top, ())


def element_counts(nl: Netlist) -> dict[str, int]:
    out: dict[str, int] = {}
    for el in nl.top.elements:
        out[el.letter.upper()] = out.get(el.letter.upper(), 0) + 1
    return dict(sorted(out.items()))


__all__ = [
    "ALLOWED_CHARS",
    "DIRECTIVES",
    "Element",
    "MAX_BYTES",
    "MAX_ELEMENTS",
    "Netlist",
    "REFUSED",
    "Scope",
    "element_counts",
    "excerpt",
    "parse",
    "spice_number",
    "split_tokens",
]
