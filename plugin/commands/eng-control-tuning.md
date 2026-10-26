---
description: PI/PID tuning, step response, margins and discretisation of a control loop with the eng-toolkit tools
argument-hint: <describe the plant or datasheet numbers, the spec and the loop rate>
---

Use the `eng-control-tuning` skill (and `eng-toolkit` for the unit and reporting rules) to analyse this control loop problem with the eng-toolkit MCP tools:

$ARGUMENTS

If key inputs are missing (plant data and units (V/krpm versus V*s/rad), load inertia and friction, what is the controller output and feedback, rise-time definition, supply voltage and current limit, usual step size, loop rate and delay), ask for them first; otherwise run the analysis, check the actuator limit by hand, report the phase margin from control_frequency, and give the report in the user's language.
