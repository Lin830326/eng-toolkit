---
description: Optimization, linear programs, root finding, curve fitting, uncertainty propagation and design of experiments with the eng-toolkit tools
argument-hint: <describe the objective, variables, constraints or data and what to compute>
---

First load the `eng-optimize` skill (and `eng-toolkit`) with the Skill tool, before calling any `mcp__eng-toolkit__` tool.

Use the `eng-optimize` skill (and `eng-toolkit` for the unit and reporting rules) to solve this problem with the eng-toolkit `optimize_*` tools:

$ARGUMENTS

If key inputs are missing (objective direction, bounds, constraints, data units, known measurement uncertainties), ask for them first; otherwise convert everything to one consistent unit system yourself (the formulas carry no units), run the tool, verify the result by hand or with a second start, give fit initial values of the right order of magnitude (they set the parameter scales), explain any refusal, and give the report in the user's language.
