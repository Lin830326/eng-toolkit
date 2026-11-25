# Structural analysis (`structures`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## Structural analysis (`structures`)

`structures_section`, `structures_truss`, `structures_frame`, `structures_modal`,
`structures_column` and `structures_stress` provide section properties, 2D/3D trusses,
2D frame statics and modes, Euler/Johnson/secant column estimates, and Mohr/Hooke
stress-strain calculations. Inputs carry units; frame stations and mode shapes are
CSV artifacts. Uses NumPy and Pint only. Linear textbook estimates are unreviewed,
with no design-code or P-delta checks. See [formats, formulas and limits](../structures/design.md).
