"""``pcb`` domain: KiCad boards and schematics. Our own board reader, and rule checks and exports through the user's kicad-cli."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.pcb.tools import TOOL_NAMES, server

DOMAIN = DomainInfo(
    name="pcb",
    title="PCB (KiCad boards and schematics)",
    description=(
        "Facts from a .kicad_pcb read by the toolkit's own parser (outline, layers, nets, footprints, tracks, vias, zones; no KiCad needed), "
        "and design rule check, electrical rule check and gerber/drill/position/PDF/SVG export through kicad-cli, which the user installs "
        "(GPL, started as a separate program only)."
    ),
    engines=(),
    server=server,
    tools=TOOL_NAMES,
)

__all__ = ["DOMAIN", "server"]
