# Engineering calculations (hydraulics) (`calc`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## `calc_cylinder_force`
單桿油壓缸理想推力／拉力。輸入：`bore`、`rod`（空字串 = 無桿）、`pressure`、`output_unit`（預設 `kN`）。

```json
{"bore": "100 mm", "rod": "50 mm", "pressure": "210 bar"}
```
→ `push_force = 164.93 kN`、`pull_force = 123.70 kN`、`bore_area`、`annulus_area`、`area_ratio`。
不含密封摩擦、背壓、桿側壓力（警告中註明）。

## `calc_pipe_pressure_drop`
直管 Darcy–Weisbach 摩擦壓降。層流 `64/Re`；紊流 Colebrook（Clamond 精確解），由 MIT 授權的 [`fluids`](https://github.com/CalebBell/fluids) 計算。輸入：`diameter`、`length`、`flow_rate`、`density`、`viscosity`（**動力**黏度，cSt 會被拒收）、`roughness`（預設 0.0015 mm）、`output_unit`（預設 `bar`）。

```json
{"diameter": "10 mm", "length": "1 m", "flow_rate": "4.71 L/min",
 "density": "870 kg/m^3", "viscosity": "40 cP"}
```
→ `pressure_drop ≈ 0.128 bar ± 5%`、`velocity`、`reynolds`、`friction_factor_darcy`、`head_loss`。
不含管件、彎頭、進出口與高程損失。

## `calc_orifice_flow`
孔口／節流閥流量 `Q = Cd·A·√(2Δp/ρ)`。`pressure_drop` 與 `flow_rate` 擇一給，另一個由公式算出。輸入：`diameter`、`density`、`discharge_coeff`（預設 0.62）、選填 `viscosity`（只用來算孔口雷諾數並在 Re < 10⁴ 時警告）、`flow_unit`、`pressure_unit`。

```json
{"diameter": "5 mm", "density": "870 kg/m^3", "pressure_drop": "100 bar"}
```
→ `flow_rate ≈ 110.75 L/min`、`orifice_velocity`、`orifice_area`。不含氣體可壓縮性、穴蝕、進流速度修正（Cd 由使用者給）。

## `calc_pump_power`
泵的液壓功率 `Q·Δp`、軸功率 `Q·Δp/η`（η 為**總效率**，0–1 的小數）、損失；給 `speed` 時另算軸扭矩。輸入：`flow_rate`、`pressure_rise`、`efficiency`、選填 `speed`、`power_unit`（預設 `kW`）。

```json
{"flow_rate": "60 L/min", "pressure_rise": "210 bar", "efficiency": 0.85, "speed": "1500 rpm"}
```
→ `shaft_power = 24.71 kW`、`hydraulic_power = 21.0 kW`、`shaft_torque ≈ 157.3 N·m`。

## `calc_accumulator_precharge`
氣囊／活塞式蓄壓器容量：氣體定律 `p·Vⁿ = 常數`（n = 1 等溫、1.4 絕熱），由要放出的油量與最低／最高工作壓力求氣體容積 V0 與預充壓力。輸入：`usable_volume`、`min_pressure`、`max_pressure`、`precharge_pressure`（空 = 0.9 × 最低壓力）、`exponent`、`pressures_are_gauge`（預設 true）、`volume_unit`。

```json
{"usable_volume": "5 L", "min_pressure": "100 bar", "max_pressure": "200 bar", "pressures_are_gauge": false, "precharge_pressure": "90 bar", "exponent": 1.0}
```
→ `gas_volume = 11.11 L`（V1 = 10 L、V2 = 5 L）。不含溫度變化與標準尺寸選型。

## `calc_cylinder_speed`
單桿油壓缸由流量求伸出／縮回速度 `v = Q/A`、行程時間，以及另一側回油流量（選回油管閥用）。輸入：`bore`、`flow_rate`、`rod`（空 = 無桿）、`stroke`、`speed_unit`（預設 `mm/s`）。

```json
{"bore": "100 mm", "rod": "50 mm", "flow_rate": "60 L/min", "stroke": "500 mm"}
```
→ `extend_speed = 127.3 mm/s`、`retract_speed = 169.8 mm/s`、`extend_time = 3.93 s`、回油 45／80 L/min。不含洩漏、油液壓縮性與加減速。
