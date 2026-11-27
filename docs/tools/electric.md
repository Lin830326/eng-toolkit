# Electrical machines, power and drives (`electric`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## Electrical machines, power and drives (`electric`)

`electric_transformer`, `electric_induction_motor`, `electric_pm_dc_motor`,
`electric_power_flow`, `electric_drive_sizing` and `electric_converter` calculate
steady-state machines, three-phase phasors/faults, motion-cycle drive requirements
and buck/boost/buck-boost converters. Unit-checked results include model limits,
loss budgets and CSV curves. Uses NumPy and Pint; all estimates are unreviewed.
See [formulas, sources, input formats and verification](../electric/design.md).

`electric_drive_sizing` with the `distance` shortcut models one stroke per `cycle_time` unless `strokes_per_cycle`
(1 to 4, default 1) says otherwise. `2` is an axis that goes out and back: the return stroke mirrors the speed and the
load that resists travel, while gravity and `dwell_load` keep their sign. One stroke understates the RMS torque of an
out-and-back axis by a factor of √2. Every shortcut result states the assumption: `data.cycle` holds
`strokes_per_cycle`, `period_s`, `dwell_per_stroke_s` and `active_fraction`, and a warning names the stroke count. An
explicit `profile` makes no stroke assumption.
