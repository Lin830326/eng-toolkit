---
description: Build a part or make a coloured 3D GLB (face kind, DFM, highlight, FEM stress or displacement) with the eng-toolkit tools
argument-hint: <describe the part or give a CAD/FEM file, and what the picture should show>
---

Use the `eng-3d-view` skill (and `eng-toolkit` for the unit and reporting rules) to produce this 3D view with the eng-toolkit tools `geometry_make_part`, `geometry_view`, `geometry_convert` and the FEM chain `fem_static` / `fem_view`:

$ARGUMENTS

If the colouring, the material, the supports or the loads are missing, ask for them first; otherwise build or load the model, make the GLB, read the legend and warnings, check one number by hand (and a second mesh for FEM), give the file path and say what the picture does and does not prove, in the user's language.
