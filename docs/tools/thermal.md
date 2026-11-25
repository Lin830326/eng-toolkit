# Heat transfer calculations (`thermal`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## `thermal_conduction`、`thermal_exchanger`、`thermal_fin`、`thermal_transient`、`thermal_radiation`、`thermal_convection`
傳熱教科書封閉解（純 Python，不呼叫外部程式；流體物性由使用者輸入，沒有內建物性表）。`conduction`：平壁／圓管／球殼多層串聯加兩側對流膜與接觸熱阻，回傳總熱阻、熱流、UA、U、各界面溫度與臨界絕熱半徑；`exchanger`：順流、逆流、交叉流（雙未混合或一側混合）、單殼程管殼式的 ε-NTU 與 LMTD，可核算（給 UA 求出口溫度）或設計（給負荷求 UA／面積），溫度交叉與不可能的負荷會警告或拒絕；`fin`：矩形與針狀肋片四種端部條件、效率、有效度、肋片陣列總表面效率；`transient`：集總熱容（含 Biot 檢查）、平板／長圓柱／圓球單項級數（含 Fourier 檢查）、半無限體（erf／erfc、接觸溫度）；`radiation`：兩平行板、同心圓柱／球、小物體在大空腔的灰體輻射交換與 N 層遮熱板；`convection`：內流（層流常數、Dittus-Boelter、Gnielinski）、外流（平板、Churchill-Bernstein、圓球）、自然對流（Churchill-Chu）的 Nu 與 h，超出相關式適用範圍會警告。所有溫度輸入為絕對溫度（`degC`、`K`、`degF`），差值單位會被拒絕。

```json
{"geometry": "plane", "layers": "200 mm @ 0.72 W/(m*K); 100 mm @ 0.04 W/(m*K); 20 mm @ 0.5 W/(m*K)", "temperature_inner": "20 degC", "temperature_outer": "-5 degC", "h_inner": "8 W/(m^2*K)", "h_outer": "25 W/(m^2*K)", "area": "10 m^2"}
```
→ `thermal_conduction`：`heat_rate ≈ 83.8 W`、`total_resistance ≈ 0.298 K/W`。各公式、適用範圍與未涵蓋項目見 [docs/thermal/design.md](../thermal/design.md)。
