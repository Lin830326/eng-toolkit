# Material fatigue and fracture (`fatigue`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## Material fatigue and fracture (`fatigue`)

`fatigue_sn`, `fatigue_rainflow`, `fatigue_strain_life`, `fatigue_fracture`, and
`fatigue_material` provide Basquin/Marin stress life, ASTM rainflow and Miner
damage, local strain life, LEFM crack growth, and material-data reductions.
Inputs carry units; curve, cycle and crack-growth tables are CSV artifacts.
Uses NumPy and Pint only. Empirical estimates are unreviewed and do not certify
components. See [methods, sources, formats and limits](../fatigue/design.md).
