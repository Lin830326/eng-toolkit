---
description: Pump and system curve, branch flow split, pipe losses and other fluid flow checks with the eng-toolkit tools
argument-hint: <describe the fluid, pump curve points, pipes, fittings, valves and what to find>
---

First load the `eng-flow-network` skill (and `eng-toolkit`) with the Skill tool, before calling any `mcp__eng-toolkit__` tool.

Use the `eng-flow-network` skill (and `eng-toolkit` for the unit and reporting rules) to analyse this fluid-flow problem with the eng-toolkit MCP tools:

$ARGUMENTS

If key inputs are missing (the real pump curve points, fluid and temperature, DN and schedule, lengths, fittings, valve Kv, exchanger datasheet point, branch layout, required flow), ask for them first; otherwise run the analysis, convert Kv and datasheet pressure drops to K and show it, hand-check one pressure drop, and give the report in the user's language.
