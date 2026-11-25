# Digital logic (Verilog with Yosys) (`digital`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## `digital_synthesize`、`digital_check`、`digital_equivalent`
Verilog 的綜合、檢查與等價比對，用 [Yosys](https://yosyshq.net/yosys/)（ISC 授權；由使用者自行安裝，工具只把它當獨立程式啟動，`toolkit_doctor` 的 `external_tools` 會列出；環境變數 `ENG_TOOLKIT_YOSYS` 可指定路徑）。三個工具都收 `source`（Verilog 文字）或 `files`（`.v`/`.sv`，最多 20 個，合計 1 MB）、`language`、`timeout_seconds`（預設 30、最多 120）。**Verilog 只是資料**：`` `include ``、會讀寫檔案或執行程式的系統任務（`$readmemh`、`$fopen`、`$system` 等）、用巨集拼出來的同類寫法、非識別字的模組名一律拒絕（`input.forbidden_construct`／`input.invalid`，訊息寫明語法與行號）；Yosys 腳本由工具自己寫，呼叫端的文字不會變成指令。

```
digital_synthesize(top="fa", source="module fa(input a, input b, input cin, output sum, output cout); ...", target="structural")
digital_check(source="module t(input a, input en, output reg q); always @* if (en) q = a; endmodule")
digital_equivalent(top_a="t", source_a=A, source_b=B, cycles=0)
```
* `digital_synthesize`：回傳各種閘的數量（`data.cells_by_type`）、線數、最長組合路徑的閘級數（`levels`，Yosys `ltp`）、單位閘面積估計（本工具的約定：NOT/AND/OR/NAND/NOR 為 1、XOR 為 2、MUX 為 3、正反器為 5，不是製程面積；表在 `data.area_model`）；附 Verilog 與 JSON 網表、實際執行的 Yosys 腳本與 log 尾端。`target`：`structural`（不經優化器，數量可手算：一位全加器 = 2 XOR + 3 AND + 2 OR = 7）、`optimized`（預設，Yosys 內建 ABC，數量依 ABC 與 Yosys 版本而定；0.70 為 6）、`abc_simple`／`abc_gates`／`abc_aig`／`abc_cmos2`／`abc_nand`（對應到指定的通用閘庫）。
* `digital_check`：語法錯誤（檔案與行號）、缺少的模組與錯的埠名、未驅動與多重驅動的訊號、不完整的 `if` 推出的栓鎖、組合迴路（含跨模組）、隱式宣告、埠寬度不符；每項有嚴重度、規則 id、行號與建議。檢查有跑成就是 `ok=true`，錯誤在 `data.findings`。
* `digital_equivalent`：兩個設計（或同一份原始碼的兩個 top）用 miter 與 SAT 比對。`cycles=0` 是組合電路的完整證明（有暫存器就拒絕）；`cycles=N` 是**有界**檢查（N 個時間步、暫存器從 0 起算）：在界內找不到差異**不是**等價的證明，結果寫 `no_mismatch_within_bound` 並附警告；找到差異就附反例（輸入值、兩邊輸出、週期）。

設計、安全模型、哪些數字是精確的、哪些依 ABC 與 Yosys 版本、沒做什麼，見 [docs/digital/design.md](../digital/design.md)。
