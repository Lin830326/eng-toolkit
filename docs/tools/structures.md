# Structural analysis (`structures`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## Structural analysis (`structures`)

`structures_section`, `structures_truss`, `structures_frame`, `structures_modal`,
`structures_column` and `structures_stress` provide section properties, 2D/3D trusses,
2D frame statics and modes, Euler/Johnson/secant column estimates, and Mohr/Hooke
stress-strain calculations. Inputs carry units; frame stations and mode shapes are
CSV artifacts. Uses NumPy and Pint only. Linear textbook estimates are unreviewed,
with no design-code or P-delta checks. See [formats, formulas and limits](../structures/design.md).

A refusal that points into a record (nodes, members, supports, loads, member loads, releases and so on) names the record
kind and its number, shows the text received, lists the expected fields in order and gives an example.

`structures_truss` returns the sums of the joint loads given, `data.applied_total_fx` / `fy` (and `fz` in 3D), next to
`data.reaction_total_fx` / `fy` / `fz`, the ids of the loaded joints in `data.loaded_nodes`, and a `data.load_check`
sentence. Compare the applied totals with a hand total: a joint left out of `loads` shows there as a shortfall. The
reactions cannot show it, because they always balance what was applied.
