# PCB (KiCad boards and schematics) (`pcb`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## `pcb_info`、`pcb_drc`、`pcb_erc`、`pcb_export`
KiCad 電路板（`.kicad_pcb`）與原理圖（`.kicad_sch`）。`pcb_info` 用本工具箱自己的解析器讀板子，**不需要 KiCad**：`values` 有 `track_length_total`（mm）、`track_count`、`via_count`、`footprint_count`、`net_count`、`copper_layers`、`zone_count`，以及 Edge.Cuts 外框的 `board_width`／`board_height`（mm，取圖形中心線）；`data` 有各層走線數與長度、線寬、通孔與鑽孔、網路（焊盤／走線／通孔／長度）、元件（編號、值、x／y 座標，**y 向下**、角度、面別）、銅箔區（zone）與 `parse_issues`（損壞而被略過的元件）。讀得懂 KiCad 5 到 10 的格式；檔案超過 50 MB、巢狀超過 64 層、元素超過 100 萬個、座標超過 ±10 m（或 `1e308`、`1e999`）、不是 UTF-8、含 NUL、不是 KiCad 板子，一律在啟動任何程式之前回 `input.invalid`、`input.too_large` 或 `input.out_of_range`。`check_with_kicad=true` 會再用 `kicad-cli` 自己的統計與座標檔交叉核對數字。`pcb_drc`、`pcb_erc` 與 `pcb_export` 需要使用者自己安裝的 `kicad-cli`（GPL，只當作獨立程式啟動，不內含、不匯入；`ENG_TOOLKIT_KICAD_CLI` 指定路徑）：

```
pcb_info(board_path=".../board.kicad_pcb")
pcb_drc(board_path=".../board.kicad_pcb", min_clearance="0.2 mm", min_track_width="0.15 mm")
pcb_erc(schematic_path=".../main.kicad_sch")
pcb_export(board_path=".../board.kicad_pcb", export="gerbers", layers="F.Cu,B.Cu,Edge.Cuts")
```
`pcb_drc`／`pcb_erc` 回 `error_count`、`warning_count`（DRC 另有 `unconnected_count`）；`data.violations` 依 KiCad 的類型（`clearance`、`track_width`、`invalid_outline`…）與嚴重度計數並列出每項涉及的物件與位置（mm），`data.rule_set` 說明用了哪組規則（KiCad 預設值，或板子旁 `.kicad_pro`／`.kicad_dru` 的白名單內容；`use_project_files=false` 則忽略）；原始 JSON 報告是 artifact。**重要限制：** 使用者的函式庫表看不到（設定目錄被導到工作目錄），所以會出現 `lib_footprint_issues`／`lib_symbol_issues` 警告；沒有 Edge.Cuts 外框的板子必有一個 `invalid_outline` 錯誤；違規沒有自己的標記位置，給的是涉及物件的位置；KiCad 10.0.6 的 ERC 報告座標縮小了 100 倍，工具會對照檔內符號位置校正並寫在 `data.position_calibration`；只檢查給定的那一個原理圖檔（子圖紙不複製）。`pcb_export` 的 `export` 為 `gerbers`（另有 `gerbers.zip`）、`drill`（Excellon，mm）、`pos`（CSV，mm，y 向上）、`pdf`、`svg`；圖層名稱必須是該板子圖層表裡的名稱，`data.files` 列出每個檔案的大小與 sha256。不做板子編輯、自動佈線與 3D。判定規則、可信範圍與沒做的事見 [docs/pcb/design.md](../pcb/design.md)。
