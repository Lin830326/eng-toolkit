---
description: Motor and drive sizing, induction/PMSM/DC motor points, converter and power-flow checks with the eng-toolkit tools
argument-hint: <describe the motion profile, motor, converter or network and what to find>
---

Use the `eng-motor-drive-sizing` skill (and `eng-toolkit` for the unit and reporting rules) to analyse this electrical drive, motor, converter or power problem with the eng-toolkit MCP tools:

$ARGUMENTS

If key inputs are missing (the whole duty cycle including dwell (`cycle_time`), pulley or load inertia, gear ratio and efficiency, the candidate motor's datasheet, motor parameters, whether a power is kW or kVA, ripple targets, limits), ask for them first and never invent datasheet values; otherwise run the analysis, hand-check one number, explain any refusal as a result, and give the report in the user's language.
