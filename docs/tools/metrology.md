# Dimensional metrology (tolerances) (`metrology`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## `metrology_fit_limits`
ISO 286 孔軸配合查詢。輸入：`nominal`（公稱尺寸，3–500 mm，如 `"25 mm"`）、`fit`（孔大寫／軸小寫，如 `"H7/g6"`）。IT01–IT18、孔 A–ZC／軸 a–zc，資料表自建於 `eng_toolkit/domains/metrology/data/iso286.json`（來源、授權、涵蓋範圍與已知缺口見 [docs/iso286-data.md](../iso286-data.md)）。

```json
{"nominal": "25 mm", "fit": "H7/g6"}
```
→ 孔 `25.000–25.021 mm`、軸 `24.980–24.993 mm`、`min_clearance = 0.007 mm`、`max_clearance = 0.041 mm`、`data.fit_type = "clearance"`；另有上下偏差（µm）、公差、極限尺寸。間隙為「孔尺寸 − 軸尺寸」，負值即干涉（`data.max_interference_mm` 等已換算）。尺寸超出 3–500 mm、未知或格式錯誤的配合代號（`input.unknown_fit_code`）、標準未定義的組合（`input.undefined_class`）都會回明確錯誤。資料表已與兩份獨立公開表逐格比對（見 [docs/iso286-data.md](../iso286-data.md)），但標準本身才是權威。

## `metrology_fit_search`
反查 ISO 286 配合：已知公稱尺寸與可接受的間隙範圍，找出符合的標準配合。輸入：`nominal`（3–500 mm）、`min_clearance`／`max_clearance`（間隙窗口，帶單位的有號長度，如 `"-0.03 mm"`；間隙為「孔 − 軸」，負值即干涉；min 大於 max 回 `input.invalid`）、`basis`（`hole` 孔基制，預設；或 `shaft` 軸基制）、`fit_set`（`preferred` 預設，10 個優選配合；或 `common`，孔 H6–H11 搭軸 c d e f g h js k m n p r s t u，軸公差等級與孔相同或細一級，軸基制則對稱）、`max_results`（1–50，預設 10）。配合的最小間隙 ≥ `min_clearance` 且最大間隙 ≤ `max_clearance` 才算符合；標準在該尺寸沒有定義的組合會略過並計入 `data.skipped_undefined`。

```json
{"nominal": "25 mm", "min_clearance": "0 mm", "max_clearance": "0.05 mm"}
```
→ `candidate_count = 2`，`data.candidates` 依序為 `H7/g6`（0.007–0.041 mm）、`H7/h6`（0–0.034 mm）。排序：孔公差加軸公差較大者優先（製造成本的代理指標），其次是配合中心最接近窗口中心者，再依產生順序。這只是成本代理，**不是**配合是否適用的判斷。沒有符合者時仍回 `ok=true`、`candidates` 為空，並在 `data.nearest` 列出最接近的 3 個配合與 `violation_um`（超出窗口的微米數）及警告，方便放寬窗口。

## `metrology_stackup_1d`
一維公差堆疊。`chain` 為 JSON 陣列字串，每個環含 `nominal`、對稱公差 `tol` 或 `plus`/`minus`（皆為量值，`0 mm` 也要帶單位）、`direction`（`"+"` 加入／`"-"` 減去）。`method`：`worst_case`（預設）或 `rss`（各環公差視為獨立常態的 ±3σ）。選配 `monte_carlo=true`（固定 `seed`，預設 12345；`samples` 1000–2000000；`distribution` 為 `normal` 或 `uniform`），配合 `lower_limit`／`upper_limit` 回傳超出規格的機率（附二項標準誤）。

```json
{"chain": "[{\"name\":\"A\",\"nominal\":\"50 mm\",\"tol\":\"0.10 mm\",\"direction\":\"+\"},{\"name\":\"B\",\"nominal\":\"20 mm\",\"plus\":\"0 mm\",\"minus\":\"0.12 mm\",\"direction\":\"-\"},{\"name\":\"C\",\"nominal\":\"29.5 mm\",\"tol\":\"0.05 mm\",\"direction\":\"-\"}]",
 "method": "rss"}
```
→ `result_nominal = 0.50 mm`、`result_mean = 0.56 mm`、`result_min/max = 0.433/0.687 mm`，`data.contributions` 給每環佔比。只算一維線性鏈，不含形位公差、熱變形與裝配效應。

環也可以是 ISO 286 配合：寫 `"fit"`（如 `"H7/g6"`）、`"nominal"` 與 `"direction"`，不可再帶 `tol`／`plus`／`minus`。此時 `nominal` 是孔軸直徑（3–500 mm，任何長度單位），環本身是間隙（孔 − 軸）：名目 0、上限為最大間隙、下限為最小間隙，干涉配合的上限為負值，因此不必手動換算符號。

```json
{"chain": "[{\"name\":\"housing\",\"nominal\":\"50 mm\",\"tol\":\"0.10 mm\",\"direction\":\"+\"},{\"name\":\"seat play\",\"fit\":\"H7/g6\",\"nominal\":\"25 mm\",\"direction\":\"-\"}]"}
```
→ `result_nominal = 50 mm`、`result_min/max = 49.859/50.093 mm`（最壞情況）。`data.contributions[i].kind` 為 `"dimension"` 或 `"fit"`（配合環另有 `fit`）。配合代號、尺寸範圍與單位的錯誤沿用 `metrology_fit_limits` 的 `error.family`，訊息前加 `link N (name):`，`details` 帶 `link_index`／`link_name`。配合環的間隙帶視為一個獨立變數（RSS、Monte Carlo 皆然）。

## `metrology_check_scan`
掃描點雲對 STEP 模型的偏差檢查：回答「零件離模型多遠、在哪裡」。需要選用的 `scan` extra（`uv sync --extra scan`，見上方安裝）。輸入：`scan_path`（`.ply` `.pcd` `.xyz` `.xyzn` `.xyzrgb` `.pts`）、`scan_unit`（點雲座標沒有單位，必須給，且只能是單位本身，如 `"mm"`、`"m"`、`"inch"`、`"um"`；`"2 mm"`、`"0 mm"` 這類帶數字的寫法會回 `input.invalid`，免得悄悄縮放掃描）、`model_path`（`.step`／`.stp`，以 mm 讀入）、`tolerance`（正的長度，如 `"0.1 mm"`）、`registration`（`none` 預設，點雲已在模型座標系；`icp`，先以點對面 ICP 從單位矩陣精修，點雲必須已大致對齊）、`icp_max_distance`（預設 `"5 mm"`）、`mesh_deflection`（STEP 離散化的弦高誤差上限，預設 `"0.01 mm"`）、`max_points`（1000 到 5000000，預設 200000；超過就用固定種子降取樣，結果可重現）、`output_unit`（預設 `mm`）。

```json
{"scan_path": "C:/scans/bracket.ply", "scan_unit": "mm", "model_path": "C:/cad/bracket.step",
 "tolerance": "0.1 mm", "registration": "icp"}
```
→ 主值 `rms_deviation`，另有 `mean_deviation`、`std_deviation`（母體標準差）、`max_deviation`（絕對值最大）、`max_positive_deviation`、`max_negative_deviation`（有號最小值）、`p95_abs_deviation`、`out_of_tolerance_fraction`（|偏差| > `tolerance` 的比例）、`points_evaluated`。`data` 含 `histogram`（絕對偏差 20 格）、`worst_points`（前 10 大偏差的座標）、`regions`（超差點以 DBSCAN 分群；先把座標相同（四捨五入到 1e-6 mm）的點併成一個位置，`eps` 為整份使用點雲（去重後）最近鄰距離中位數的 5 倍，所以重複記錄的點不影響分區；每區有相異位置數、重心、外框、最大偏差，依位置數排序，最多 20 區）、`registration`（4×4 變換、`fitness`、`inlier_rmse`、估計變換用的點數 `source_points`、`unconstrained` 未被約束的方向、`note`）、`mesh`（三角形數、`deflection_mm`、是否封閉、`solids` 實體數、`step_roots` STEP 根實體數與實際轉出的數量）與 `points`（原始與實際使用點數）；每個超差點寫入 `deviations.csv` 工作區產物（欄位 `x_mm,y_mm,z_mm,deviation_mm,region`，最多 100000 列，超過會警告）。

偏差是掃描點到模型表面的**有號距離，在材料外為正**。模型不是剛好一個封閉實體時內外無定義或有歧義：開放殼面，或 STEP 內有多個實體（重疊的實體會讓射線奇偶判斷出錯），都改用到最近表面的無號距離，`data.signed = false` 並警告，有號的最大／最小值不回傳。STEP 有根實體沒轉成幾何時（`step_roots.transferred` 小於 `in_file`）也會警告模型不完整。量測對象是離散化後的網格，誤差上限即 `mesh_deflection`，小於它的偏差沒有意義。到網格的距離以 float64 計算：Open3D 在平移到網格中心後的 float32 座標只負責找出最近三角形，再對它與共用頂點的所有鄰接三角形重算精確距離（測試中誤差低於 1e-9 mm；退化網格仍可能選錯三角形）。ICP 只在初始對齊夠近時收斂；`fitness` 是配準後與模型表面距離不超過 `icp_max_distance` 的點的比例（與 `inlier_rmse` 同一批點），低於 0.5 會警告；估計變換最多用 200000 個點（固定種子子集），再套用到全部點；相同輸入重跑，ICP 結果可能在 1e-5 mm 量級有差異（多執行緒浮點加總）。**ICP 後的偏差描述的是形狀，不是位置**：最佳擬合會吸收真實缺陷（例如頂面 28% 範圍凸起 0.2 mm，不配準時 22.2% 超差，配準後只剩 2.7%），所以一律警告；已配準的點若只在單一平面或圓柱上，平移或旋轉有未約束的方向（單一平面無法固定面內平移與繞法向的旋轉），會在警告與 `registration.unconstrained` 具名列出。全域配準（未對齊的掃描）不在範圍內。ASCII／二進位 PLY 會對照標頭驗證資料是否完整（被截斷的檔案回 `input.invalid`）；座標超過 1e7 mm 或非有限值一律視為壞檔。掃描品質與適用標準由使用者負責。

## `metrology_form_error`
由量測點算形狀誤差：`feature` 為 `flatness`（平面度，x,y,z 點）、`straightness`（真直度，平面內輪廓的 x,y 點）、`circularity`（真圓度，截面的 x,y 點；給 x,y,z 時先投影到最佳擬合平面）或 `cylindricity`（圓柱度，x,y,z 點）。點以 `points` 內嵌（JSON `[[x,y,z],...]` 或每行／以 `;` 分隔一點的文字）或 `points_path` 指到 `.csv` `.txt` `.xyz` `.pts` `.asc` `.dat`（開檔一次，標頭行會略過，標頭有 x、y、z 時依名稱取欄）；座標沒有單位，`unit` 必須給且只能是單位本身（`"mm"`、`"um"`、`"inch"`）。選配 `tolerance` 回傳合格與否，`output_unit` 指定結果單位。

```json
{"feature": "circularity", "points": "10.1,0; 4.95,-8.57; -5.05,8.75; -9.9,0; -4.95,-8.57; 5.05,8.75", "unit": "mm"}
```
→ 主值 `form_error` 是**最小區域**的寬度（包住所有點的最薄一對平行線／平行平面／同心圓／同軸圓柱），另附最小平方參考的對照值 `least_squares_form_error` 與區域幾何（`data.zone`）。`data.method` 說明用了哪個估計法、`exact` 是否證明為最小：直真度是精確解；平面度與圓度在 24／32 點以內窮舉所有可能的 4 點組合（全域最小），更多點時以多起點加候選枚舉收斂並檢查最佳性條件（`first_order_optimal`），但不證明全域最小；圓柱度是軸向搜尋，結果一定是真能包住所有點的區域，故為最小區域的上界，不宣稱最小。因此合格判定恆有效，在非精確方法下不合格不一定是真不合格（`tolerance_check.conclusive`）。點數太少（平面度需 4 個相異點、真直度 3、圓度 4、圓柱度 6）回 `input.too_few_points`，共線或共面的退化點集回 `geometry.degenerate`，NaN／無限大、欄數不符回 `input.invalid`（指出第幾列）。數值的解析度（座標最後一位）與取樣不足的低估都會警告。演算法、精度與限制見 [docs/metrology-gdt/design.md](../metrology-gdt/design.md)。

## `metrology_position_tolerance`
位置度：由名目與量測座標算每個特徵的位置度並含 MMC／LMC 補償公差。輸入：`nominal`、`measured`（每個特徵一列，x,y 或 x,y,z，格式同上）、`tolerance`（位置度公差帶直徑，如 `"0.1 mm"`）、`unit`（座標與 `feature_size` 的單位）、`bonus_modifier`（`none` 預設、`M`、`L`）、`feature_kind`（`hole` 孔：MMC 是最小孔；`shaft` 軸：MMC 是最大軸）、`feature_size`（每個特徵的實際尺寸）、`mmc_size`／`lmc_size`。

```json
{"nominal": "10,20", "measured": "10.045,20.06", "tolerance": "0.1 mm", "bonus_modifier": "M",
 "feature_kind": "hole", "feature_size": "10.06", "mmc_size": "10 mm"}
```
→ 偏差 (0.045, 0.06)，所需公差帶 `position_error = 2·√(dx²+dy²) = 0.15 mm`（x,y,z 時為球形 SØ），補償 `bonus = 實際尺寸 − MMC = 0.06 mm`，容許 `0.16 mm`，合格；`data.features` 逐特徵列出。實際尺寸超出材料極限時補償為 0 並警告。基準位移（datum shift）、基準修飾、複合公差帶與突出公差帶不在範圍內，量測座標須已在名目的基準座標系中。
