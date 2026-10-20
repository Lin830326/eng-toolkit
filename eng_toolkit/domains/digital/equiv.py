"""``digital_equivalent``: the miter script and the counterexample read back from Yosys's SAT table.

Both designs are elaborated in their own Yosys design, flattened, their asynchronous resets turned into synchronous logic
(``async2sync``; the SAT model has no asynchronous flip-flops) and memories into registers, then stashed under the fixed names
``gold`` and ``gate`` (``rename -top``), so two designs may use the same module names.  ``miter -equiv`` joins them (same ports
required), and ``sat -prove trigger 0`` asks whether any input can make an output differ:

* ``cycles = 0``: a combinational proof over all input values.  Only legal when the designs hold no flip-flop, latch or memory.
* ``cycles = N``: ``sat -seq N -set-init-zero``: all inputs free in each of N time steps (steps 0 to N-1, step k is the state after k
  clock edges), every flip-flop without an initial value starting at 0.  That is a bounded check.  No mismatch within N steps proves
  nothing about step N and later.
"""

from __future__ import annotations

import re
from typing import Any

from eng_toolkit.domains.digital import flow
from eng_toolkit.domains.digital.guard import Source

MAX_CYCLES = 100
MAX_COUNTEREXAMPLE_STEPS = 101
SAT_PROVED = "SAT proof finished - no model found: SUCCESS!"
SAT_FAILED = "SAT proof finished - model found: FAIL!"
_ROW = re.compile(r"^\s*(?:(?P<time>init|\d+)\s+)?(?P<name>\S+)\s+(?P<dec>-?\d+)\s+(?P<hex>[0-9a-fA-F]+)\s+(?P<bin>[01xzXZ?]+)\s*$")


def equiv_script(
    sources_a: list[Source], top_a: str, sources_b: list[Source], top_b: str, language: str, cycles: int
) -> list[str]:
    def prepare(sources: list[Source], top: str, new_name: str, stash: str) -> list[str]:
        return [
            *flow.read_lines(sources, language),
            f"hierarchy -check -top {top}",
            "proc",
            "flatten",
            "memory",
            "opt_clean",
            "async2sync",
            f"rename -top {new_name}",
            f"design -stash {stash}",
            "design -reset",
        ]

    sat = "sat -verify -prove trigger 0 -show-inputs -show-outputs"
    if cycles > 0:
        sat += f" -seq {cycles} -set-init-zero"
    return [
        *prepare(sources_a, top_a, "gold", "gold_d"),
        *prepare(sources_b, top_b, "gate", "gate_d"),
        "design -copy-from gold_d -as gold gold",
        "design -copy-from gate_d -as gate gate",
        "tee -o state.json stat -json",
        "miter -equiv -flatten -make_outputs gold gate miter",
        sat + " miter",
    ]


def verdict(log: str) -> str | None:
    """``"proved"`` (no input makes the outputs differ), ``"counterexample"``, or ``None`` when the log says neither."""
    if SAT_FAILED in log:
        return "counterexample"
    if SAT_PROVED in log:
        return "proved"
    return None


def _signal(name: str) -> tuple[str, str] | None:
    """``(kind, port)`` of a name in the SAT table: ``\\in_a`` is input ``a``, ``\\gold_y`` output ``y`` of the gold design."""
    bare = name.lstrip("\\")
    for prefix, kind in (("in_", "input"), ("gold_", "gold"), ("gate_", "gate")):
        if bare.startswith(prefix):
            return kind, bare[len(prefix) :]
    if bare == "trigger":
        return "trigger", "trigger"
    return None


def counterexample(log: str, *, sequential: bool) -> dict[str, Any] | None:
    """The input values that make the outputs differ, and both designs' outputs for them, from the table after ``FAIL!``.

    Combinational: ``{"inputs": {...}, "gold_outputs": {...}, "gate_outputs": {...}}`` (integers; ``widths`` gives bits).
    Sequential: the same per step in ``steps`` (``cycle`` counts from 0 = the initial state), and ``first_mismatch_cycle``.
    """
    at = log.find(SAT_FAILED)
    if at < 0:
        return None
    steps: dict[int, dict[str, Any]] = {}
    widths: dict[str, int] = {}
    for line in log[at:].split("\n"):
        row = _ROW.match(line)
        if row is None:
            continue
        kind_port = _signal(row.group("name"))
        if kind_port is None:
            continue
        time = row.group("time")
        if time == "init":
            continue
        step = int(time) - 1 if time else 0
        kind, port = kind_port
        bits = row.group("bin")
        widths[port] = len(bits)
        try:
            value: int | None = int(bits, 2)
        except ValueError:  # an undefined (x) bit
            value = None
        entry = steps.setdefault(step, {"cycle": step, "inputs": {}, "gold_outputs": {}, "gate_outputs": {}, "mismatch": False})
        if kind == "input":
            entry["inputs"][port] = value
        elif kind == "gold":
            entry["gold_outputs"][port] = value
        elif kind == "gate":
            entry["gate_outputs"][port] = value
        else:
            entry["mismatch"] = value == 1
    if not steps:
        return None
    ordered = [steps[k] for k in sorted(steps)][:MAX_COUNTEREXAMPLE_STEPS]
    first = next((s for s in ordered if s["mismatch"]), ordered[-1])
    result: dict[str, Any] = {
        "cycle": first["cycle"],
        "inputs": first["inputs"],
        "gold_outputs": first["gold_outputs"],
        "gate_outputs": first["gate_outputs"],
        "differing_outputs": sorted(p for p, v in first["gold_outputs"].items() if first["gate_outputs"].get(p) != v),
        "widths": {k: widths[k] for k in sorted(widths) if k != "trigger"},
    }
    if sequential:
        result["steps"] = [{k: v for k, v in s.items() if k != "mismatch"} for s in ordered]
        result["first_mismatch_cycle"] = first["cycle"]
    return result
