---
description: Build a part or make a picture of it (PNG to paste into a mail, plus a coloured GLB) by face kind, DFM findings, named face groups, or an FEM stress or displacement map, with the eng-toolkit tools
argument-hint: <describe the part or give a CAD/FEM file, and what the picture should show>
---

First load the `eng-3d-view` skill (and `eng-toolkit`) with the Skill tool, before calling any `mcp__eng-toolkit__` tool.

Use the `eng-3d-view` skill (and `eng-toolkit` for the unit and reporting rules) to produce this 3D view with the eng-toolkit tools `geometry_make_part`, `geometry_view`, `geometry_convert` and the FEM chain `fem_static` / `fem_view`:

$ARGUMENTS

If the colouring, the material, the supports or the loads are missing, ask for them first; otherwise build or load the model, make the picture, read the legend and warnings, check one number by hand (and a second mesh for FEM). Then hand it over as the skill's section 7 says, in the user's language: the PNG path first (the tools write it next to the GLB and show it in the reply, never draw one yourself), the picture described in words (shape and size, what each colour means, the colour bar's range and unit, where the extremes are), the GLB mentioned once for people with a 3D viewer, and a plain sentence that a face colour means "flagged by a rule of thumb" or "of this kind", not a verdict.
