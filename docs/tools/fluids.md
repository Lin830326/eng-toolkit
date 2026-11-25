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
