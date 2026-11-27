# Fluid systems (`fluids`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## Fluid systems (`fluids`)

`fluids_pipe_network`, `fluids_pump_system`, `fluids_open_channel`,
`fluids_compressible`, `fluids_surge` and `fluids_external_flow` cover looped
networks, pump operating points, channel profiles, ideal-gas relations, single-pipe
water hammer and external drag. Inputs carry units and accept caller fluid
properties. Channel profiles and surge histories include CSV artifacts. Uses
NumPy and Pint only; all estimates are unreviewed. See the
[formulas, record formats, sources and validity limits](../fluids/design.md).

`fluids_pump_system` and `fluids_pipe_network` take `curve_points` (0 = off; 2 to 500 for `pump_system`, 2 to 200 for
`pipe_network`). They then write `system_curve.csv` over the pump's flow range, with the columns `flow_m3_s`,
`system_head_m`, `pump_head_m` (plus `efficiency` for `pump_system`) and `is_operating_point`; the operating point is
inserted as one more row with the flag 1, so a plot can mark it. `data.operating_point` and `data.curve_table` describe
it. `pipe_network` needs exactly one pump for the table; with none or several it warns and writes no table.
