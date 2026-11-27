# Material fatigue and fracture (`fatigue`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## Material fatigue and fracture (`fatigue`)

`fatigue_sn`, `fatigue_rainflow`, `fatigue_strain_life`, `fatigue_fracture`, and
`fatigue_material` provide Basquin/Marin stress life, ASTM rainflow and Miner
damage, local strain life, LEFM crack growth, and material-data reductions.
Inputs carry units; curve, cycle and crack-growth tables are CSV artifacts.
Uses NumPy and Pint only. Empirical estimates are unreviewed and do not certify
components. See [methods, sources, formats and limits](../fatigue/design.md).

`fatigue_rainflow` checks the record before counting. It always returns the extremes of the history as given
(`data.history_min`, `history_max`, `history_range`, and `history_extremes` with the sample index of each). `data.spikes`
lists isolated single-sample spikes: an interior sample more than 6 robust sigmas from both neighbours, on the same side
of both. At most 5 are listed, the total is in `count`, and a warning names them. With damage, `data.dominant_cycle`
names the cycle that carries the most damage, and a warning appears when one cycle carries more than 50 % of the total.

The safety factor of `fatigue_sn` assumes proportional loading: mean and alternating stress grow together along a load
line through the origin. It is not valid where the mean stays fixed, as in a preloaded bolt; `data.assumptions` and a
warning say so.
