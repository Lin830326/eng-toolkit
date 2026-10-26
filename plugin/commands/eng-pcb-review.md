---
description: Review a KiCad board and schematic for fabrication with DRC, ERC, board facts and gerber and drill export
argument-hint: <path to .kicad_pcb and .kicad_sch, the fab's capability sheet>
---

Use the `eng-pcb-review` skill (and `eng-toolkit` for the unit and reporting rules) to review this KiCad design with the eng-toolkit MCP tools:

$ARGUMENTS

Say which file is the board, the schematic and the project; ask for the fab's capability sheet if there is none; separate real findings from library-visibility noise, treat a count of 499 as at least 499, and end with a checklist verdict, never a flat yes.
