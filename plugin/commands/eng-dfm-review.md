---
description: DFM review of a STEP/IGES/BREP part for CNC milling or sheet metal
argument-hint: <path to the CAD file> [cnc_milling|sheet_metal] [shop limits]
---

First load the `eng-dfm-review` skill (and `eng-toolkit`) with the Skill tool, before calling any `mcp__eng-toolkit__` tool.

Use the `eng-dfm-review` skill (and `eng-toolkit` for the unit and reporting rules) to review this part for manufacturability with the eng-toolkit tools `geometry_measure` and `geometry_check_dfm`:

$ARGUMENTS

Confirm the process, the milling spindle axis and the shop limits (`tool_radius` is the cutter's radius, half its diameter; or state the defaults you assume), measure first, run the checks, verify the important findings, and write the review in the user's language. List the checks that were not performed.
