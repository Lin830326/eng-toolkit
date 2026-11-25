---
description: Review a KiCad board and schematic for fabrication with DRC, ERC, board facts and gerber and drill export
argument-hint: <path to .kicad_pcb and .kicad_sch, the fab's capability sheet>
---

First load the `eng-pcb-review` skill (and `eng-toolkit`) with the Skill tool, before calling any `mcp__eng-toolkit__` tool.

Use the `eng-pcb-review` skill (and `eng-toolkit` for the unit and reporting rules) to review this KiCad design with the eng-toolkit MCP tools:

$ARGUMENTS

Say which file is the board, the schematic and the project; ask for the fab's capability sheet if there is none; separate real findings from library-visibility noise (`library_warnings`), read a count flagged `at_kicad_limit` as "at least", and end with a checklist verdict, never a flat yes; the gerber export is one zip with drill and job file.
