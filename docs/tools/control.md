# Classical control analysis (`control`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## `control_analyze`、`control_response`、`control_frequency`、`control_feedback`、`control_pid_tune`、`control_discretize`
經典控制（單輸入單輸出線性系統，只用 numpy，不用 scipy）。轉移函數以降冪係數表示，例如 `"[1, 3, 2]"` 或簡單文字 `"s^2 + 3 s + 2"`（自寫解析器，不執行任何輸入）。`analyze`：極點、零點、直流增益、阻尼比與時間常數，並以精確有理數算術的 Routh-Hurwitz（連續）或 Jury（離散）判穩，與數值根交叉核對；`response`：階躍／脈衝／斜坡響應（精確離散化，CSV 表）與上升時間、超越量、整定時間、穩態誤差，不穩定系統回報 `unbounded`；`frequency`：Bode 資料、增益／相位裕度、頻寬、共振峰，並以特徵多項式根數精確判斷閉迴路穩定（條件穩定或開迴路不穩定時裕度會誤導，工具會明說）；`feedback`：開迴路／閉迴路／靈敏度函數、系統型別、Kp／Kv／Ka 與各輸入穩態誤差、靈敏度峰值；`pid_tune`：Ziegler-Nichols、Cohen-Coon、IMC、SIMC 的 P／PI／PID 公式與 Padé 近似的閉迴路模擬（起點值，非最終整定）；`discretize`：ZOH、FOH、Tustin（可預扭曲）、前向／後向 Euler、極零點匹配，附公式與穩定性對照。階數上限 40，係數絕對值 1e-12～1e12，模擬點數上限 100000。

```json
{"numerator": "16", "denominator": "[1, 4, 16]"}
```
→ `control_response`：`overshoot ≈ 16.30 %`、`peak_time ≈ 0.907 s`、`settling_time ≈ 2.02 s`。精確與近似之處、驗證方式與未涵蓋項目（無根軌跡、MIMO、非線性、狀態回授、Kalman、LQR）見 [docs/control/design.md](../control/design.md)。
