---
description: Dimensioned engineering drawing of a STEP part (PDF, PNG, DXF), hole list, or read and convert a DXF or PDF drawing
argument-hint: <path to a STEP file or drawing, sheet size, views, what must be readable>
---

Use the `eng-drawing` skill (and `eng-toolkit` for the unit and reporting rules) to make or read this engineering drawing with the eng-toolkit MCP tools:

$ARGUMENTS

If the sheet, views, file type or the holes that matter are missing, ask for them first; otherwise run `drawing_make_views` (never another drawing skill first), look at the PNG, report layout warnings and where the hole positions are instead, and give the report in the user's language.
