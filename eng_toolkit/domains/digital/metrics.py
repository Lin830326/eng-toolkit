"""What a synthesis run is measured by: cell counts, the unit-gate area estimate, and the longest path in gate levels.

``UNIT_GATE_AREA`` is a convention of this toolkit, not a technology number.  It is loosely the "unit-gate" idea (a simple
two-input gate is one unit, an exclusive-or costs about two) extended to the cells Yosys uses; it is meant for comparing two
versions of one design, never for predicting silicon area.  The table is part of the result (``data.area_model``) so nobody has to
guess it.  A cell type that is not in the table is counted in the cell total, listed under ``unrated`` and left out of the area.
"""

from __future__ import annotations

import re
from typing import Any

UNIT_GATE_AREA: dict[str, float] = {
    "$_BUF_": 0.0,
    "$_NOT_": 1.0,
    "$_AND_": 1.0,
    "$_NAND_": 1.0,
    "$_OR_": 1.0,
    "$_NOR_": 1.0,
    "$_ANDNOT_": 1.0,
    "$_ORNOT_": 1.0,
    "$_XOR_": 2.0,
    "$_XNOR_": 2.0,
    "$_AOI3_": 2.0,
    "$_OAI3_": 2.0,
    "$_AOI4_": 3.0,
    "$_OAI4_": 3.0,
    "$_MUX_": 3.0,
    "$_NMUX_": 3.0,
}
FLIP_FLOP_AREA = 5.0  # any $_DFF*, $_SDFF*, $_ALDFF* cell (with or without enable and reset)
LATCH_AREA = 3.0  # any $_DLATCH* cell
# Cells that carry information for simulation or for tools, not logic: where a flattened module came from, a $display, an assertion.
NOT_HARDWARE = frozenset({"$scopeinfo", "$print", "$check", "$assert", "$assume", "$cover", "$live", "$fair", "$allseq", "$allconst", "$anyseq", "$anyconst"})
_LTP = re.compile(r"Longest topological path in (\S+) \(length=(\d+)\)")
_LTP_STEP = re.compile(r"^\s+(?:\d+|ff): (.+?)(?: \(via .*\))?\s*$")


def area_model() -> dict[str, Any]:
    return {
        "unit": "unit-gate (a convention of this toolkit, not a technology number)",
        "cells": dict(UNIT_GATE_AREA),
        "flip_flop": FLIP_FLOP_AREA,
        "latch": LATCH_AREA,
        "not_counted": sorted(NOT_HARDWARE),
        "unrated": "any other cell type: counted as a cell, listed in data.unrated_cells, left out of the area",
    }


def cell_area(cell_type: str) -> float | None:
    if cell_type in UNIT_GATE_AREA:
        return UNIT_GATE_AREA[cell_type]
    if cell_type.startswith(("$_DFF", "$_SDFF", "$_ALDFF")):
        return FLIP_FLOP_AREA
    if cell_type.startswith("$_DLATCH"):
        return LATCH_AREA
    return None


def area_estimate(cells_by_type: dict[str, int]) -> tuple[float, dict[str, int]]:
    """``(area in unit gates, {cell type: count} of the types the table does not rate)``."""
    total = 0.0
    unrated: dict[str, int] = {}
    for cell_type, count in sorted(cells_by_type.items()):
        unit = cell_area(cell_type)
        if unit is None:
            unrated[cell_type] = count
        else:
            total += unit * count
    return total, unrated


def module_stats(stat: Any, top: str) -> dict[str, Any] | None:
    """The block of ``stat -json`` that describes module ``top`` (Yosys keys it ``\\top``), or the only module when there is one."""
    if not isinstance(stat, dict):
        return None
    modules = stat.get("modules")
    if isinstance(modules, dict):
        for key in (f"\\{top}", top):
            if isinstance(modules.get(key), dict):
                return modules[key]
        if len(modules) == 1:
            only = next(iter(modules.values()))
            return only if isinstance(only, dict) else None
    return None


def cells_by_type(stats: dict[str, Any]) -> dict[str, int]:
    raw = stats.get("num_cells_by_type")
    if not isinstance(raw, dict):
        return {}
    return {str(k): int(v) for k, v in raw.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and k not in NOT_HARDWARE}


def longest_path(log: str, top: str) -> tuple[int | None, list[str]]:
    """``(gate levels, the signals along the path)`` from the ``ltp`` report of module ``top``; ``(None, [])`` when there is none."""
    lines = log.split("\n")
    for index, line in enumerate(lines):
        match = _LTP.search(line)
        if match and match.group(1) in (top, f"\\{top}"):
            steps: list[str] = []
            for follow in lines[index + 1 :]:
                step = _LTP_STEP.match(follow)
                if step is None:
                    break
                steps.append(re.sub(r"\s+", " ", step.group(1)).replace("\\", "")[:80])
            return int(match.group(2)), steps[:40]
    return None, []


def has_state(stat: Any) -> list[str]:
    """Names of the state-holding cell types (flip-flops, latches, memories) in any module of a ``stat -json`` report."""
    found: set[str] = set()
    modules = stat.get("modules") if isinstance(stat, dict) else None
    for block in (modules or {}).values():
        if isinstance(block, dict):
            for cell_type in cells_by_type(block):
                lower = cell_type.lower()
                if any(word in lower for word in ("dff", "latch", "mem", "$sr", "$ff", "fsm")):
                    found.add(cell_type)
            if isinstance(block.get("num_memories"), int) and block["num_memories"] > 0:
                found.add("memory")
    return sorted(found)
