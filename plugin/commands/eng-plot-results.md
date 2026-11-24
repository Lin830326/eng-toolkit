---
description: Plot results of eng-toolkit tools (or your own numbers) as a PNG chart (Bode, S-N, spectrum, histogram, bars)
argument-hint: <what to plot, source tool or data, axes, scales, ranges>
---

First load the `eng-plot-results` skill (and `eng-toolkit`) with the Skill tool, before calling any `mcp__eng-toolkit__` tool.

Use the `eng-plot-results` skill (and `eng-toolkit` for the unit and reporting rules) to draw this chart with the eng-toolkit plot tools:

$ARGUMENTS

If the data source, the quantities on the axes (with units) or the scale is missing, ask for it first; otherwise run the producing tool if needed, draw the chart, check the points drawn and the axis ranges against the tool's numbers, give the PNG file path (`data.png_path`: the tools write the PNG by default and show it in the reply) and say in words what the chart shows, then report in the user's language.
