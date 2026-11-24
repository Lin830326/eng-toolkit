# Numerical optimization and experiments (`optimize`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## Numerical optimization and experiments (`optimize`)

`optimize_minimize`, `optimize_linprog`, `optimize_root`, `optimize_fit`,
`optimize_sensitivity` and `optimize_doe` provide bounded optimization, LP/MIP,
root finding, nonlinear fitting, uncertainty propagation and experimental designs.
Formulas use a small interpreted arithmetic language, with no Python execution or
unit tracking inside expressions. Numerical certificates, resource caps and CSV
artifacts support review; all results remain unreviewed. Uses NumPy and Pint only.
See [algorithms, input formats and limits](../optimize/design.md).
