# Geometry (3D CAD models) (`geometry`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## `geometry_measure`
3D CAD 模型的精確量測：回答「這個零件多大、多重、有哪些面和孔、這兩個特徵相距多遠」。需要選用的 `cad` extra（`uv sync --extra cad`，見上方安裝）。數字是檔案 B-rep 的精確性質（OpenCASCADE），不是離散化網格的近似。輸入：`model_path`（`.step` `.stp` `.iges` `.igs` `.brep`；`.stl` 回 `input.unsupported_format`，網格檔留待之後的版本）、`queries`（可省略；以 `;` 分隔、最多 20 個）、`density`（可省略，帶單位，如 `"2700 kg/m^3"`，會多回傳 `mass`；裸數字回 `units.bare_number`，非密度的單位回 `units.dimension_mismatch`）、`inventory_limit`（0 到 500，預設 50：`data` 內列出的面與孔的數量；`inventory.json` 產物永遠列出全部）、`output_unit`（預設 `mm`）。

查詢語法：`distance A B`（兩個實體實際表面之間的最短距離：面是被邊界修剪後的那一片，不是它延伸出去的無限大平面，所以點在孔正上方 30 mm、孔半徑 5 mm 時，到頂面是 30.414 mm 而不是 30）、`angle A B`（角度，單位 degree）、`diameter A`、`radius A`、`area A`、`thickness P(x, y, z) <軸>`（從該點沿軸射出一條精確射線，取前兩個交點的距離，即壁厚）。`A`／`B` 是 `F<n>`（面）、`H<n>`（孔）或 `P(10 mm, 0 mm, 5 mm)`（點，座標必須帶單位，裸數字回 `units.bare_number`）；軸是 `+x -x +y -y +z -z` 其中之一。孔的距離與面積以孔壁的圓柱面計（直徑 10 mm、軸線離平行面 25 mm 的孔，到該面是 20 mm，不是 25）；`diameter`／`radius` 只適用圓柱面與孔，其他回 `geometry.unsupported_query`。`angle` 兩個平面時取外法向量夾角（0 到 180 度）；只要其中一個是圓柱面或孔，就取軸線的夾角（軸線沒有方向，0 到 90 度）。引用不存在的 `F999` 回 `geometry.unknown_entity`，`details` 帶 `ref`、`faces`、`holes`（模型實際有幾個面和孔）與 `query_index`；語法錯誤回 `input.invalid`，`details.query_index`（從 0 起算）與 `details.query` 指出是哪一個。壁厚的射線穿不到兩個交點（例如正好穿過孔）時不是錯誤：該 `q` 不出現在 `values`，`data.queries[i].found` 為 `false`，並加一則警告。

```json
{"model_path": "C:/cad/bracket.step", "density": "2700 kg/m^3",
 "queries": "distance F1 H1; angle F1 F2; diameter H1; thickness P(10 mm, 10 mm, 30 mm) -z"}
```
→ 主值 `volume`（單位為 `output_unit` 的立方，如 `millimeter ** 3`），另有 `surface_area`、`size_x`／`size_y`／`size_z`（外框尺寸）、`mass`（有給 `density` 時）與依查詢順序的 `q1`、`q2`、…（長度用 `output_unit`，角度用 `degree`，面積用 `output_unit` 的平方）。`data` 含 `solids`、`faces_by_kind`、`holes`（孔：`id` 為 `H1`…、`face_ids`、`diameter_mm`、`axis` 鑽入方向（指向材料內）、`entry_mm`、`depth_mm` 圓柱段深度（不含鑽頭錐底）、`thru` 是否貫穿、`counterbore`／`countersink` 恆為 `null`）、`faces`（`id`、`kind`、`area_mm2`、`sense` 平面／凸／凹／混合、法向量或軸線、外框；`faces`／`holes` 受 `inventory_limit` 限制）、`centroid_mm`、`bbox_min_mm`／`bbox_max_mm`（精確外框）、`step_roots`（檔內根實體數與實際轉出的數量；IGES 與 BREP 也用這個名稱）、`queries`（每個查詢的解析結果與它解析到的實體）、`face_ids_valid_for`（輸入檔的 sha256）、`conventions` 與 `coverage`。

孔的辨識規則：孔是**凹**的圓柱面，且（可能由兩個半面拼成）涵蓋整圈；圓角是局部圓柱而且是凸的，不算孔。貫穿與否（`thru`）用精確射線判斷：從圓柱段兩端各往外一點點、略偏離軸線的幾條沿軸向射線，若都沒碰到任何東西，那一端就通到零件外面；兩端都通是貫穿孔，只有一端通是有底的盲孔（偏離軸線，錐形鑽頭底才不會擋住射線）。貫穿孔沒有自己的入口，所以從其主軸的正向端鑽入（`axis` 的主要分量為負）。包住凸台的環形槽外壁不算孔。**不偵測**沉孔、錐孔與螺紋：沉孔會被列成貫穿孔旁邊另一個盲孔，`data.coverage` 會明講這些限制。

面編號（`F<n>`）是 OpenCASCADE 走訪面的順序，孔編號依軸向、直徑、入口排序：兩者只對**同一個檔案的位元組與同一版 OpenCASCADE** 有效，`face_ids_valid_for` 給出該檔的 sha256，重新匯出的檔案要重新取得清單。先不帶 `queries` 呼叫一次讀 `data.faces`／`data.holes`，再下查詢。單位：STEP 與 IGES 由 OpenCASCADE 依檔內單位轉成 mm，BREP 沒有單位，數字一律當 mm。IGES 只有在輸出端寫成實體（B-rep solid）時才有體積；只有曲面的 IGES 沒有封閉實體，`volume` 不出現，`surface_area` 成為主值並警告。曲面 IGES 還可能把圓柱存成旋轉曲面，列為 `kind: other` 而不是圓柱，所以**偵測不到孔**（Open CASCADE 自己的 IGES 曲面輸出就是如此，其他 CAD 的輸出各不相同）；需要體積或孔資訊時請用 STEP，或把 IGES 輸出成實體。檔案沒有封閉實體（殼面、散面）時同樣如此，且 `density` 會被忽略並警告；多個實體時體積與面積是各實體的總和，重疊的實體會重複計算。這些結果沒有經過工程核可，量測的前提（單位、檔案是否忠於設計）由使用者負責。

## `geometry_make_part`
由宣告式 JSON 規格建出零件：輸出 STEP（可加 GLB／STL／BREP）、精確的體積、表面積、外框尺寸與選配的質量。**伺服器不執行任何程式碼**（沒有 `make_from_code`）：想用 build123d 的代理在自己的 shell 裡跑，再把 STEP 交給 `geometry_measure` 等工具。需要選用的 `cad` extra（`uv sync --extra cad`）。輸入：`spec`（JSON 字串，上限 64 kB）、`name`（產品名，也是輸出檔名主幹，`[A-Za-z0-9_-]{1,64}`，預設 `part`）、`formats`（逗號分隔的 `glb`、`stl`、`brep`；STEP 一律輸出，因為它是讀回驗證的依據）、`mesh_deflection`（GLB／STL 離散化的弦高誤差，0.001 到 100 mm，預設 `"0.01 mm"`）、`density`（可省略，帶單位，會多回傳 `mass`）、`output_unit`（預設 `mm`）。

**規格語法。** 規格是 `{"params": {...}, "features": [...]}`。每個數字都是帶單位的字串（`"25 mm"`、`"90 deg"`、`"1.5 inch"`；裸數字回 `units.bare_number`，長度欄位給質量回 `units.dimension_mismatch`）。`params` 是 `名稱: 運算式`；任何欄位都可以用 `$名稱` 引用**較前面**的 param（引用後面的或自己，即前向引用與循環，回 `input.invalid`），運算式交給 `units.parse_quantity`（pint 的解析器，不是 `eval`），例如 `"($L - 2 mm) / 2"`。注意 pint 把「數字 空格 單位」當隱含乘法：`$L / 2 mm` 是（$L ÷ 2）× mm，是面積，不是長度；除以長度要加括號 `($L) / (2 mm)`（結果無因次），一般只除以純數字。features 依序作用在**同一個實體**上，第一個必須是 `mode` 為 `"add"` 的實體（body）；每個 feature 有 `op`、選填的 `name`（預設 `<op><序號>`）與該 op 的欄位。

運算式護欄（`units.parse_quantity` 本身不受影響，其他工具照舊）：實測未加護欄時 pint 對 `9**9**9 mm` 會算一個三億多位數的整數、10 秒內不返回（`9**9**9**9 mm` 同樣；`10**1000000 mm` 約 0.14 秒後才溢位出錯）。因此 Resolver 在交給 pint 之前拒絕：超過 200 字元、括號巢狀超過 8 層、任何數字絕對值超過 1e9、`**` 或 `^` 後面不是 6 以下的一般數字或接著連乘（`2 ** 2 ** 2`），一律回 `input.invalid`（`details.pointer` 指出欄位）。`(100 mm) / 4 + 2 mm`、`2 * (10 mm + 5 mm)` 照常運作。

| op | 角色 | 欄位（`*` 必填） |
|---|---|---|
| `box` | body | `size`* `[x, y, z]`、`at`（最小角，預設原點）、`mode` |
| `cylinder` | body | `diameter`*、`height`*、`at`（底面圓心，預設原點）、`axis`（預設 `"+z"`）、`mode` |
| `sphere` | body | `diameter`*、`at`（球心，預設原點）、`mode` |
| `cone` | body | `diameter`*（底面）、`top_diameter`*（頂面，可為 `"0 mm"` 成為尖錐；與 `diameter` 相等就是圓柱）、`height`*、`at`（底面圓心，預設原點）、`axis`（預設 `"+z"`）、`mode` |
| `extrude` | body | `plane`（`"xy"` 預設、`"xz"`、`"yz"`）、`offset`（沿平面法向移動，預設 `0 mm`）、`profile`*、`distance`*（沿法向，負值反向）、`taper`（0 到 45 deg，側壁沿拉伸方向內縮；不提供外擴，要外擴請從較寬的一端以負 `distance` 反向拉伸）、`mode` |
| `import` | body | `file`*（`.step` `.stp` `.iges` `.igs` `.brep`，經 `JobHandle.input_file`，單位規則同 `geometry_measure`）、`at`（位移）、`mode`；檔案必須剛好一個實體 |
| `revolve` | body | `plane`、`offset`、`profile`*、`axis`（平面的 `"u"` 或 `"v"` 軸，過平面原點，預設 `"v"`）、`angle`（預設 `360 deg`）、`mode` |
| `hole` | 修改 | `at`*（入口點清單 `[[x, y, z], ...]`）、`axis`（鑽入方向，預設 `"-z"`）、`diameter`*、`depth`（長度或 `"thru"`，預設 `"thru"`；從入口算起）、`counterbore` `{diameter, depth}`、`countersink` `{diameter, angle}`（含角）、`thread`、`fit`（後兩者只是標註文字，不產生螺紋或公差幾何） |
| `pattern` | 修改 | `of`*（較前面的 body 或 hole 的名稱）、`count`*（共幾個，含原件，2 到 256）、`step`（線性，每個再移 `[x, y, z]`）或 `angle`（環形，360 deg 為均分一圈，較小則首尾涵蓋該角）二擇一、`center`、`axis`（環形的軸，預設過原點、`"+z"`）；孔的陣列會宣告 `count × 原孔數` 個孔 |
| `mirror` | 修改 | `plane`*、`offset`、`of`（省略 = 整個目前零件，否則只鏡射該 feature）|
| `shell` | 修改 | `faces`*（面選擇器）、`thickness`*（壁厚向內長，移除選到的面）|
| `fillet` | 修改 | `edges`*（邊選擇器）、`radius`* |
| `chamfer` | 修改 | `edges`*、`distance`*（對稱倒角） |

`mode` 是 `"add"`（預設，聯集）、`"cut"` 或 `"intersect"`；`axis` 是 `"+x" "-x" "+y" "-y" "+z" "-z"` 之一。平面 `plane` 的草圖座標 (u, v) 為 (x, y)、(x, z)、(y, z)，法向 +z、+y、+x。輪廓 `profile` 是恰有一個鍵的物件：`{"rect": {"size": [u, v], "at": [u, v], "centered": false}}`（`at` 是最小角，`centered` 為 true 時是中心）、`{"circle": {"diameter", "at"}}`、`{"polygon": {"points": [[u, v], ...]}}`（至少 3 點，不可自交）、`{"slot": {"length", "width", "at", "angle"}}`（`length` 含兩端圓頭，須大於 `width`）、`{"path": {"start": [u, v], "segments": [{"to"}, {"to", "radius", "turn"}, {"to", "via"}]}}`（直線與圓弧的封閉輪廓，最後一段的 `to` 須等於 `start`，檢查封閉、自交與弧線一致；`radius` 取較短的弧，`turn` 為 `"left"`（逆時針，預設）或 `"right"`；`via` 是弧通過的點）。`revolve` 的輪廓必須在旋轉軸的同一側。孔從 `at` 的入口點沿 `axis` 鑽 `depth`（`"thru"` 為穿過整個零件；盲孔深過零件就是穿透）；整個 feature 沒切掉任何材料時回警告（多半是入口點或方向弄反了）。

**限制與邊界行為**（都有測試與獨立驗證）：
* 一份規格所有 `hole` 的孔位合計最多 **1000** 個；超過立刻回 `input.invalid`，`details.limit` 是 1000。1000 個孔在開發機上約 25 秒，其中約 9 秒是把 STEP 讀回驗證，250 個約 3 秒，時間隨孔數成長得比線性快。
* 最終結果必須是**剛好一個有效實體**，否則回 `geometry.invalid_result`（訊息會說「join loose bodies or remove them」）。執行途中可以暫時有多個分開的實體：例如先對兩個分開的方塊倒圓角或鑽孔，再用第三個方塊把它們接起來是合法的，邊選擇器（如 `>x`、`convex`）會作用在目前所有實體上。
* 圓角或倒角的大小容不下時（例如 10 mm 方塊全部邊倒 5 mm 或更大，或比板厚還大的圓角）回 `geometry.build_failed`，`details` 指出是第幾個 feature；剛好小於一半（如 4.9 mm）則照常做出有效實體。半徑或距離為 0 或負數回 `input.invalid`。
* 切到什麼都不剩、或與不相交的實體取交集，回 `geometry.build_failed` 並指出該 feature。

**邊選擇器**是以 `&` 相交的詞：`all`；`|x` `|y` `|z`（平行該軸的直線邊）；`>x` `<x` `>y` `<y` `>z` `<z`（位於零件外框該方向極值面上的邊）；`convex`（外角）與 `concave`（內角），由相鄰兩面的二面角判斷，平滑相接（例如圓柱的接縫線，兩側是同一個面）兩者都不是，所以 `|z & convex` 只會選到外緣的四條豎邊，不會選到孔的接縫線；`line`（直線邊）與 `circle`（圓邊）。`of:名稱` 選出較前面的 body、hole、pattern 或 mirror feature 所建立的邊（落在它的工具形狀表面、且先前不存在的邊）；面選擇器（`shell` 用）有 `all` `>x` `<x` `>y` `<y` `>z` `<z` `plane` `cylinder` 與 `of:名稱`，`>z` 是頂面。沒有任何邊符合時回 `geometry.empty_selection`，`details.matches_per_term` 列出每個詞各單獨符合幾條，方便修正選擇器。
邊與面選擇器都可用小寫 ` or ` 聯集最多 8 組非空條件，`&` 優先且不支援括號；例如 `>z or <z` 可讓 `shell` 同時移除頂面與底面，重複選到的面或邊只計一次。

```json
{"params":{"L":"100 mm","W":"50 mm","T":"20 mm","d":"10 mm"},"features":[{"op":"box","name":"body","size":["$L","$W","$T"]},{"op":"hole","name":"bore","at":[["$L / 4","$W / 2","$T"]],"axis":"-z","diameter":"$d","depth":"thru","fit":"H7"},{"op":"fillet","name":"corners","edges":"|z & convex","radius":"2 mm"}]}
```
→ 主值 `volume = 98360.531085 mm³`（手算 100·50·20 − π·5²·20 − 4·(2² − π·2²/4)·20），`surface_area = 16395.699 mm²`，`size_x/y/z = 100/50/20 mm`，給 `density` 時另有 `mass`。用 `geometry_measure` 讀產出的 STEP：1 個貫穿孔、直徑 10 mm、入口 (25, 25, 20)。

`data`：`features`（每個 feature 的 `index`（從 1 起）、`name`、`op`、`volume_after_mm3`、`edges_used`）、`holes`（宣告的孔，已換成 mm：入口、鑽入方向、直徑、深度（`null` 為貫穿）、沉孔、錐孔、`thread`、`fit`；`data` 內最多列 50 個，`holes_total` 是總數，完整清單在 `features.json`）、`params`（解析後的 param，長度為 mm、角度為 rad）、`bbox`、`solids`、`faces`、`readback`（把寫出的 STEP 讀回，比對體積：`volume_written_mm3`、`volume_read_mm3`、`relative_difference`；超過 1e-6 回 `geometry.readback_mismatch`）、`spec_sha256` 與 `step_sha256`。產物：`<name>.step`、`<name>.features.json`、`spec.json`，以及要求的 `<name>.glb`（公尺、+Y 向上）、`<name>.stl`（二進位，毫米）、`<name>.brep`。`spec.json` 是**正規化後的規格**（預設值補齊、運算式與單位都解成 mm 與 rad、鍵排序、無多餘空白），它本身就是合法規格，其 sha256 就是 `spec_sha256`：空白或鍵順序不同的同一份規格得到相同的雜湊。`features.json` 是宣告的設計意圖（孔位置、直徑、深度、沉孔、螺紋與配合標註），內含 STEP 的 sha256，之後的工程圖工具只有在 STEP 雜湊相符時才會印出 `Ø10 H7 THRU` 這類標註。

**確定性。** 同一份規格得到位元組完全相同的 STEP（XCAF 寫出器、固定的 `FILE_NAME` 標頭與時間戳、產品名取自 `name`；同一行程內與跨行程都測試過，也不受 `PYTHONHASHSEED` 影響）。布林運算以單執行緒進行，合併共面的面。

**錯誤。** 規格錯誤是 `input.invalid`，`details.pointer` 是 JSON pointer（如 `/features/2/diameter`），`details.allowed` 是該處允許的欄位或值；單位錯誤沿用 `units.bare_number`、`units.dimension_mismatch`、`units.parse_error`，`details` 同樣帶 `pointer`。核心建模失敗時 `error.details` 帶 `feature_index`（從 1 起）、`feature_name`、`op` 與 `pointer`：`geometry.build_failed`（核心失敗，如不可能的圓角半徑；`details.occt` 是核心訊息）、`geometry.empty_selection`；最後結果必須恰好是一個通過 BRepCheck 的實體，否則回 `geometry.invalid_result`（例如兩個互不相連的實體）。失敗的結果不帶警告，也不寫出零件檔。

限制：規格 64 kB、200 個 feature、全規格孔位置共 1000、運算式 200 字元。沒有螺紋幾何、放樣與掃掠（陣列、鏡射、薄殼、傾斜拉伸、含圓弧的路徑輪廓與匯入既有 STEP 為第二輪，設計記錄見 `docs/geometry-drawing/make-part-round2.md`）；結果同樣是未經工程核可的模型。

## `geometry_check_dfm`
對實體 3D 模型做可製造性（DFM）檢查：`process` 目前只支援 `cnc_milling` 與 `sheet_metal`，其他製程（`turning`、`injection_molding`、`additive`…）回 `geometry.unsupported_process`，不猜。需要選用的 `cad` extra。輸入：`model_path`、`process`、`limits`（可省略的 JSON 物件，覆蓋預設限值，長度要帶單位，例如 `{"min_wall": "1 mm", "tool_radius": "2 mm"}`）、`tool_axis`（銑削主軸方向；留空時用 `+z`，並回警告「tool_axis defaulted to +z」與 `data.tool_axis_defaulted: true`，明確給 `+z` 則沒有警告）、`max_findings`、`output_unit`。每個發現（finding）都有規則代號、嚴重度（`error`／`warn`／`info`）、實測值與限值、面／邊編號、模型座標中的一個點，以及 `check`：`exact`（精確的 B-rep 量，如半徑、平面間距、精確距離）或 `heuristic`（取樣或把形狀解讀成特徵，兩個方向都可能錯）。結果另有依嚴重度的計數 `data.summary` 與 `data.not_performed`（沒做的檢查）。**預設限值是本工具箱自己的經驗值，不是任何標準的數值，也不宣稱符合任何標準**，請以 `limits` 帶入貴廠的值；沒有閉合實體（只有殼或曲面）的檔案會被拒絕。規則：`cnc.internal_corner_radius`、`cnc.min_wall`、`cnc.hole_depth_ratio`、`cnc.min_hole_diameter`、`cnc.tool_access`（heuristic）、`sheet.thickness`、`sheet.thickness_uniform`（heuristic）、`sheet.min_bend_radius`、`sheet.hole_to_edge`、`sheet.bend_tolerance`（heuristic，warn：內外圓柱面的軸線偏差超過精確容差、但在 0.05 mm 以內的折彎仍然配對並檢查，發現裡寫出實測的偏差）、`sheet.bend_unrecognised`（heuristic，warn：看起來像折彎、卻配不到另一個圓柱面的面數；這些面沒有被檢查，`data.summary.not_evaluated` 也會列出）。規則、做法、實測時間與未做項目見 [`docs/geometry-dfm/design.md`](../geometry-dfm/design.md)。

```json
{"model_path": "C:/cad/bracket.step", "process": "cnc_milling", "limits": "{\"tool_radius\": \"3 mm\", \"min_wall\": \"1 mm\"}"}
```
→ 主值 `errors`（error 的個數），另有 `warnings`、`info`，以及有量到時的 `smallest_wall`（`sheet_metal` 為 `thickness`）。四個尖銳內角都回 `measured = 0 mm`、`limit = 3 mm`，位置在各內角邊的中點；R0.5 的內圓角回 `measured = 0.5 mm`；Ø2、深 30 的孔回 `measured = 15`（深徑比）。

## `geometry_convert`
轉換 3D 模型檔並列出轉換損失。輸入 `.step` `.stp` `.iges` `.igs` `.brep`（以公釐讀取）或 `.stl`（二進位或 ASCII，數字視為公釐）；`output_format` 為 `step`、`brep`（精確，只能由 STEP/IGES/BREP 轉出）、`stl`（二進位、公釐）或 `glb`（公尺、+Y 向上，轉換已套用：模型 (x, y, z) mm 變成檔內 (x, z, −y)/1000）。STL 只能轉成 `stl` 或 `glb`：網格不是實體，不會被轉成 STEP/BREP（回 `geometry.needs_brep`）。B-rep 轉網格時以 `mesh_deflection`（預設 `0.01 mm`，0.001 到 100 mm）細分曲面；輸出檔寫在工作區的工作目錄並附 sha256，輸入檔不會被改動。STEP/IGES/BREP 需要選用的 `cad` extra；STL 轉 STL/GLB 不需要任何 engine。輸入（全為字串）：`input_path`、`output_format`、`mesh_deflection`、`name`（輸出檔名與 STEP 產品名，空白 = 輸入檔名）。

```json
{"input_path": "C:/cad/part.step", "output_format": "stl", "mesh_deflection": "0.01 mm"}
```
→ 主值 `volume_out`，另有 `volume_in`、`volume_relative_change`、`surface_area_in/out`，網格輸出另有 `triangle_count`；網格的數字是從寫出的檔案讀回來的。`data.losses` 逐項說明損失：精確曲面變成三角形（弦高誤差以 `mesh_deflection` 為上限）、單位換算（例如 STEP 宣告 `inch` 已換成 mm）、GLB 的軸與單位、32 位元浮點精度、名稱／顏色／組合結構不會帶過去。Ø20 × 30 的圓柱在 0.01 mm 下 STL 體積 9418.578 mm³，比真值 9424.778 少 6.2 mm³，不超過 2π·10·0.01·30 = 18.85 mm³。沒有封閉實體的檔案（殼、開放網格）沒有體積，主值改為 `surface_area_out`；座標超過 1 km 或非有限值回 `input.out_of_range`。設計與未驗證項目見 [docs/geometry-convert/design.md](../geometry-convert/design.md)。

## `geometry_view`
* 把實體 CAD 檔（`.step` `.stp` `.iges` `.igs` `.brep`）寫成**上色的 GLB**（公尺、+Y 向上，轉換同 `geometry_convert`）供網頁／AR／CAD 檢視器使用：`color_by` 為 `none`、`face_kind`（依面種類）、`dfm`（依 `geometry_check_dfm` 每個面最嚴重的發現：error 紅、warn 橘、info 藍）或 `highlight`（指定 `F3,F7`，或分組 `bends: F5,F12; holes: F3-F4`，每組一色）；每個顏色一個 primitive 與材質，`extras` 列出面編號（與 `geometry_measure` 相同）與三角形範圍，可加 B-rep 邊線；另寫 `view.json`（面 → 顏色 → 發現）；預設還寫一張 PNG 預覽圖（`preview` 為 `png`、`iso` 或 `none`），可直接貼進信件；PNG 不超過 600 kB 時，也會以圖片區塊直接附在回覆裡（`inline_image`，預設 `true`）。`color_by="dfm"` 時可帶 `process`、`tool_axis`、`limits`（意義同 `geometry_check_dfm`），PNG 會在每個 error 與 warn 發現的位置畫圓圈（最多 30 個，部分附發現代號如 `D26`），`data.preview.markers` 列出每個圓圈的發現、所在視圖與像素位置；`data.flagged_area_share` 與每個發現的 `footprint` 說明一個面被上色的原因與涉及的面積上限。`focus` 在 PNG 加一格特寫，附比例尺與面編號：可給面編號（`F3,F7`、`F14-F19`）、`highlight` 的群組名稱，或 `color_by="dfm"` 時的發現代號（`D2`）。主值 `triangle_count`。設計見 [docs/geometry-view/design.md](../geometry-view/design.md)。
