# Circuit simulation (ngspice) (`spice`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## `spice_simulate`、`spice_check_netlist`、`spice_measure`
用 ngspice 做電路模擬（ngspice 是你自己安裝的程式，只當獨立行程以批次模式啟動，不隨附；用 `toolkit_doctor` 看有沒有找到，或設定 `ENG_TOOLKIT_NGSPICE`）。`spice_simulate`：`analysis` 為 `op`、`dc`、`ac`、`tran`，網表用文字（`netlist`）或檔案（`netlist_file`）給，`outputs` 如 `v(out),i(v1)`；`op` 回每個輸出的值，掃描類回每個輸出的最小、最大、最後值，完整表格寫成 `result.csv`（`ac` 表另有 `<訊號>.phase_unwrapped_deg`，沿頻率展開的相位）。`spice_check_netlist`：不啟動 ngspice，只解析並列出元件、節點、缺接地、重複名稱、缺 `.model`／`.subckt`、浮接節點。`spice_measure`：從 `result.csv` 算 −3 dB 頻率、10–90 % 上升／下降時間、過衝、整定時間、穩態值，公式隨結果回傳。

```
spice_simulate(analysis="tran", netlist="rc\nV1 in 0 PULSE(0 1 0 1n 1n 1 2)\nR1 in out 1k\nC1 out 0 1u", outputs="v(out)", step="10 us", stop="5 ms")
```
→ `v(out).final` = 0.99326 V（1 − e^−5）；`i(v1)` 的符號是 SPICE 慣例：電流流進電源的 + 端為正，供電的電源顯示負值。**網表當作資料，不是程式**：逐行對照白名單，`.control`、`.include`、`.lib`、`.write`、`.osdi`、A／N 元件（程式碼模型）、`file`、`external`、隨機函數、`.end` 之後的文字、超過 1e±30 的數值、超過 200 KB／1000 個元件（子電路展開後計）的網表一律拒絕，錯誤訊息指出行號與指令；分析由參數決定，不由網表決定；暫態超過 10 萬點拒絕。**確定性：** 同一網表同一結果。**數值誤差：** 工作點與 AC 是線性代數（約 1e-12 相對誤差）；暫態與二極體取決於 ngspice 的積分誤差與常數。詳見 `docs/spice/design.md`。
