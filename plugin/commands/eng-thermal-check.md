---
description: Heat-transfer check (heatsink and junction temperature, insulation, fins, radiation, exchanger, warm-up) with the eng-toolkit tools
argument-hint: <describe the part, heat load, ambient, datasheet thermal data and the limit to meet>
---

First load the `eng-thermal-check` skill (and `eng-toolkit`) with the Skill tool, before calling any `mcp__eng-toolkit__` tool.

Use the `eng-thermal-check` skill (and `eng-toolkit` for the unit and reporting rules) to analyse this thermal problem with the eng-toolkit MCP tools:

$ARGUMENTS

If key inputs are missing (datasheet Rth and the temperature rise it is quoted at, junction or surface limit and who sets it, ambient at the part, orientation, heatsink geometry and mass, fan data), ask for them first; otherwise run the analysis, hand-check one number, use the `natural_channel` model for fins (not the isolated-plate h), say what is outside the toolkit (fan curves and airflow, a two-lump junction model), and give the report in the user's language.
