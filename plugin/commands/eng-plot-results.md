---
description: Plot results of eng-toolkit tools (or your own numbers) as an SVG chart
argument-hint: <what to plot, source tool or data, axes, scales, ranges>
---

Use the `eng-plot-results` skill (and `eng-toolkit` for the unit and reporting rules) to draw this chart with the eng-toolkit plot tools:

$ARGUMENTS

If the data source, the quantities on the axes (with units) or the scale is missing, ask for it first; otherwise run the producing tool if needed, draw the chart, check the points drawn and the axis ranges against the tool's numbers, give the SVG file path and report in the user's language.
