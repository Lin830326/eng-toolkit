"""Just enough of a ``.kicad_sch`` to check it before KiCad sees it and to calibrate the positions in KiCad's ERC report.

Reads the top-level list only: the root must be ``kicad_sch``; placed symbols (reference and position) and hierarchical sheet blocks
are collected; every number that is read goes through :func:`eng_toolkit.domains.pcb.boardread.num`, so a coordinate beyond the
limit is ``input.out_of_range`` before any program starts.  Nothing else is interpreted.
"""

from __future__ import annotations

from typing import Any

from eng_toolkit.domains.pcb import sexp
from eng_toolkit.domains.pcb.boardread import MAX_ELEMENTS, Skip, clean, num
from eng_toolkit.domains.pcb.sexp import child, children, head
from eng_toolkit.errors import ToolFailure

MAX_SYMBOLS_LISTED = 5000


def read_schematic(text: str) -> dict[str, Any]:
    symbols: dict[str, tuple[float, float]] = {}
    sheets: list[str] = []
    version = ""
    count = 0
    issues = 0
    try:
        items = sexp.top_level(text)
        root = next(items)
        if head(root) != "kicad_sch":
            raise ToolFailure(
                "input.invalid",
                f"not a KiCad schematic: the file's first list is {clean(head(root), 40)!r}, not 'kicad_sch'"
                + (" (this looks like a board: use pcb_drc or pcb_info)" if head(root) == "kicad_pcb" else ""),
                {"first_list": clean(head(root), 40)},
            )
        for node in items:
            count += 1
            if count > MAX_ELEMENTS:
                raise ToolFailure("input.too_large", f"the schematic holds more than {MAX_ELEMENTS} elements", {"limit": MAX_ELEMENTS})
            name = head(node)
            try:
                if name == "version" and len(node) > 1:
                    version = clean(node[1], 40)
                elif name == "symbol" and child(node, "lib_id") is not None:
                    at = child(node, "at")
                    reference = next((p[2] for p in children(node, "property") if len(p) >= 3 and p[1] == "Reference"), "")
                    if at is not None and len(at) >= 3:
                        position = (num(at[1], "symbol x"), num(at[2], "symbol y"))
                        if reference and len(symbols) < MAX_SYMBOLS_LISTED:
                            symbols[clean(reference, 60)] = position
                elif name == "sheet":
                    sheetfile = next((p[2] for p in children(node, "property") if len(p) >= 3 and p[1] in ("Sheetfile", "Sheet file")), "")
                    sheets.append(clean(sheetfile, 120))
                elif name in ("wire", "bus", "junction", "no_connect", "label", "global_label", "hierarchical_label", "text"):
                    pts = child(node, "pts")
                    for xy in children(pts, "xy") if pts else ():
                        num(xy[1], f"{name} x")
                        num(xy[2], f"{name} y")
                    at = child(node, "at")
                    if at is not None and len(at) >= 3:
                        num(at[1], f"{name} x")
                        num(at[2], f"{name} y")
            except (Skip, IndexError):
                issues += 1
    except sexp.SexpError as exc:
        family = "input.too_large" if exc.kind == "too_large" else "input.invalid"
        raise ToolFailure(family, f"not a readable KiCad schematic: {exc.message}", {"reason": exc.kind, "line": exc.line, "column": exc.column, **exc.details}) from exc
    return {"version": version, "symbols": symbols, "sheets": sheets, "elements": count, "issues": issues}


__all__ = ["read_schematic"]
