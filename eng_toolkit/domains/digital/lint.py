"""``digital_check``: the lint script and the findings read from Yosys's messages.

Yosys is not a lint tool.  What is reported is what the Yosys front end, ``hierarchy -check``, ``proc`` and ``check`` say about
the design; each message becomes a finding with a rule id, a severity, the file and line when they can be found, and a hint.  The
line comes from the message itself, or from the ``src`` attribute Yosys keeps on every wire and cell (read back from a JSON dump of
the design just before ``check``).
"""

from __future__ import annotations

import re
from typing import Any

from eng_toolkit.domains.digital import flow
from eng_toolkit.domains.digital.guard import Source

MAX_FINDINGS = 200

HINTS: dict[str, str] = {
    "syntax-error": "Yosys's Verilog parser stopped here; fix the syntax. Yosys reports one syntax error at a time.",
    "hierarchy-port-mismatch": "an instance connects a port the module does not have (or leaves out a required one); compare the instance with the module header.",
    "hierarchy-missing-module": "an instantiated module is not defined in the given sources; pass the file that defines it.",
    "duplicate-module": "two modules have the same name; rename one.",
    "latch-inferred": "a signal is not assigned on every path of a combinational always block, so it holds its old value: a latch. Assign it in every branch or give it a default value first.",
    "combinational-loop": "an output feeds back into its own input with no register in the path; break the loop with a flip-flop or remove the feedback.",
    "multiple-drivers": "one wire is driven from more than one place; keep a single driver, or use a tri-state with enable if that is what you mean.",
    "undriven-signal": "a wire is read but nothing drives it (a typo in a name, or a missing assign); it is an unknown value in simulation.",
    "implicit-declaration": "an undeclared name was used and Verilog silently made it a 1-bit wire; this is how a misspelt signal hides. Declare it, or add `default_nettype none.",
    "width-mismatch": "a connection is wider or narrower than the port it feeds; the extra bits are dropped or padded with zero.",
}


def check_script(sources: list[Source], top: str | None, language: str) -> list[str]:
    hier = f"hierarchy -check -top {top}" if top else "hierarchy -check"
    return [
        *flow.read_lines(sources, language),
        hier,
        "proc -noopt",  # proc's final opt_expr can erase inverter loops before check sees them.
        "write_json design.json",
        "check",
        "log " + flow.FLAT_MARKER,
        "flatten",
        "check",
    ]


_WIRE = re.compile(r"(?:for |Wire )(?P<mod>[A-Za-z_][\w$]*)\.(?P<name>\\?\S+?)(?::$| is used)")
_LATCH = re.compile(r"signal `\\?(?P<mod>[A-Za-z_][\w$]*)\.(?P<name>[^']+)'")
_RESIZE = re.compile(r"Resizing cell port (?P<mod>[A-Za-z_][\w$]*)\.(?P<rest>\S+) from")
_LOOP_MODULE = re.compile(r"in module (?P<mod>[A-Za-z_][\w$]*)")


def _first_line(src: Any, out: flow.Outcome) -> tuple[str | None, int | None]:
    if not isinstance(src, str):
        return None, None
    ref = re.search(r"(src\d+\.v):(\d+)", src)
    return (out.labels.get(ref.group(1), ref.group(1)), int(ref.group(2))) if ref else (None, None)


def _clean_name(name: str) -> str:
    return name.replace("\\", "")


def findings(out: flow.Outcome) -> tuple[list[dict[str, Any]], bool]:
    """``(findings, truncated)``.  Errors first, then warnings, each group by line."""
    design = out.json_file("design.json") or {}
    modules = design.get("modules", {}) if isinstance(design, dict) else {}

    def net_src(module: str, name: str) -> Any:
        block = modules.get(module) or {}
        return ((block.get("netnames") or {}).get(_clean_name(name)) or {}).get("attributes", {}).get("src")

    def cell_src(module: str, name: str) -> Any:
        block = modules.get(module) or {}
        return ((block.get("cells") or {}).get(_clean_name(name)) or {}).get("attributes", {}).get("src")

    text = out.log
    flat_at = text.find(flow.FLAT_MARKER)
    first_pass, second_pass = (text, "") if flat_at < 0 else (text[:flat_at], text[flat_at:])
    warnings, errors = flow.messages(out, first_pass)
    loop_lines = [set(item["_lines"]) for item in warnings if item["rule"] == "combinational-loop"]
    if second_pass:
        later, _ = flow.messages(out, second_pass)
        for item in later:
            # Only loops that go through a module boundary are new; everything else was said in the first pass under other names.
            if item["rule"] == "combinational-loop" and not any(set(item["_lines"]) <= known for known in loop_lines):
                item["message"] += " (after flattening the hierarchy)"
                warnings.append(item)
                loop_lines.append(set(item["_lines"]))
    found: list[dict[str, Any]] = []
    for item in [*errors, *warnings]:
        module = signal = None
        message = item["message"]
        file, line = item["file"], item["line"]
        raw = " ".join([message, *item["detail"]])
        if item["rule"] == "latch-inferred":
            m = _LATCH.search(message)
            if m:
                module, signal = m.group("mod"), _clean_name(m.group("name"))
        elif item["rule"] in ("undriven-signal", "multiple-drivers"):
            m = _WIRE.search(message)
            if m:
                module, signal = m.group("mod"), _clean_name(m.group("name"))
                if signal.startswith("$"):
                    signal = None  # a name Yosys made up for an internal wire
                elif line is None:
                    file, line = _first_line(net_src(module, signal), out)
        elif item["rule"] == "combinational-loop":
            m = _LOOP_MODULE.search(message)
            module = m.group("mod") if m else None
            wire = re.search(r"wire \\?(\S+) source", raw)
            signal = _clean_name(wire.group(1)) if wire else None
        elif item["rule"] == "width-mismatch":
            m = _RESIZE.search(message)
            if m:
                module = m.group("mod")
                parts = m.group("rest").rsplit(".", 1)
                if len(parts) == 2 and line is None:
                    file, line = _first_line(cell_src(module, parts[0]), out)
        if line is None and item["_lines"]:
            line = item["_lines"][0]
            file = file or next(iter(out.labels.values()), None)
        entry = {
            "rule": item["rule"],
            "severity": item["severity"],
            "message": message,
            "file": file,
            "line": line,
            "module": module,
            "signal": signal,
            "lines": item["_lines"] if item["_lines"] else ([line] if line else []),
            "detail": item["detail"],
            "hint": HINTS.get(item["rule"]),
        }
        found.append(entry)
    found.sort(key=lambda f: (f["severity"] != "error", f["line"] if f["line"] is not None else 10**9))
    return found[:MAX_FINDINGS], len(found) > MAX_FINDINGS
