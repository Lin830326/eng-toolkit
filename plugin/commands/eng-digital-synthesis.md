---
description: Lint Verilog, get gate counts and check whether two Verilog designs are equivalent with Yosys
argument-hint: <Verilog text or files, top module, the claim to check>
---

First load the `eng-digital-synthesis` skill (and `eng-toolkit`) with the Skill tool, before calling any `mcp__eng-toolkit__` tool.

Use the `eng-digital-synthesis` skill (and `eng-toolkit` for the unit and reporting rules) to analyse this Verilog with the eng-toolkit MCP tools:

$ARGUMENTS

State the target and metric of every count (unit-gate, not um2), say whether an equivalence verdict is a proof or only bounded, and for a wide counter use the reduced-width or split-logic routes.
