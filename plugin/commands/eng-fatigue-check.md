---
description: Fatigue check (infinite life, S-N, rainflow and Miner damage, strain-life, crack growth) with the eng-toolkit tools
argument-hint: <describe the part or load history, the material and what to check>
---

Use the `eng-fatigue-check` skill (and `eng-toolkit` for the unit and reporting rules) to analyse this fatigue or fracture problem with the eng-toolkit MCP tools:

$ARGUMENTS

If key inputs are missing (ultimate and yield strength, load type, surface finish, diameter, reliability, mean stress, notch Kt and radius, Paris coefficients), ask for them first; otherwise run the analysis, hand-check one number, treat any tool refusal as a result, and give the report in the user's language.
