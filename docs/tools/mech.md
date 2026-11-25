# Mechanical design checks (`mech`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## `mech_bolt_check`
螺栓受拉檢核：應力面積 `A_s = π/4·(d − 0.9382P)²`、強度等級（`8.8` 等）的降伏強度、保證載重、扭矩↔預緊力 `T = K·F·d`、使用率。`torque` 與 `preload` 擇一給。輸入：`diameter`、`torque`／`preload`、`property_class`（預設 `8.8`）、`pitch`（空 = ISO 粗牙）、`nut_factor`（預設 0.2）、選填 `external_load`、`load_share`、`proof_strength`、`unit_system`。

```json
{"diameter": "10 mm", "torque": "40 N*m"}
```
→ `preload = 20 kN`、`proof_load ≈ 33.4 kN`、`utilization ≈ 0.60`。保證應力未給時以 0.9×降伏強度估計（警告中註明）；不含剪力、疲勞、扭轉應力。粗牙節距表見 [docs/calc-mech/metric-coarse-pitch-data.md](../calc-mech/metric-coarse-pitch-data.md)。

## `mech_bearing_life`
滾動軸承基本額定壽命 `L10 = (C/P)^p`（ISO 281 基本公式，球 p = 3、滾子 p = 10/3），換算小時；給 `required_life` 時反算所需 C。輸入：`dynamic_load_rating`、`equivalent_load`、`speed`、`bearing_type`（`ball`／`roller`）、選填 `required_life`。

```json
{"dynamic_load_rating": "30.7 kN", "equivalent_load": "5 kN", "speed": "1500 rpm"}
```
→ `l10_hours ≈ 2572 h`。不含可靠度修正 a1、潤滑修正 a_ISO、當量載重 X·Fr + Y·Fa（需廠商資料）。

## `mech_spring_compression`
圓線壓縮彈簧：彈簧常數 `k = G·d⁴/(8·D³·Na)`、旋繞比、Wahl 係數、剪應力、密合高度（依端部型式）；給 `load` 或 `deflection` 算另一個與應力，給 `free_length` 算到密合的行程並檢查挫屈。輸入：`wire_diameter`、`mean_diameter`、`active_coils`、`shear_modulus`（預設 79.3 GPa）、`end_type`、選填 `free_length`／`load`／`deflection`／`allowable_shear`、`unit_system`。

```json
{"wire_diameter": "2 mm", "mean_diameter": "16 mm", "active_coils": 8, "load": "100 N"}
```
→ `spring_rate = 4.84 N/mm`、`wahl_factor = 1.184`、`shear_stress ≈ 603 MPa`、`solid_height = 20 mm`。僅靜態；不含疲勞與共振。

## `mech_beam_bending`
單跨梁封閉解：簡支／懸臂 × 集中載重／均佈載重，回傳支承反力、最大剪力、最大彎矩、最大撓度、最大彎曲應力。斷面：`rectangle`、`circle`、`tube` 或 `custom`（`second_moment` + `extreme_fiber`）。輸入：`length`、`load`（集中為力，均佈為每長度力）、`support`、`load_type`、`position`、`elastic_modulus`（預設 210 GPa）、斷面尺寸、選填 `allowable_stress`、`unit_system`。

```json
{"length": "4 m", "load": "10 kN/m", "load_type": "udl", "width": "50 mm", "height": "100 mm"}
```
→ `max_bending_stress = 240 MPa`、`max_moment = 20 kN·m`、`max_deflection ≈ 38.1 mm`。線彈性、小變形、不計自重。
