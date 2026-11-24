# Drawing (2D views of 3D CAD models) (`drawing`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## `drawing_make_views`
由 3D 模型（`.step` `.stp` `.iges` `.igs` `.brep`）產生工程圖：正投影視圖、以 OpenCASCADE 精確移除隱藏線（隱藏線以虛線畫出）、ISO 5457 橫向圖紙（A4 到 A0）含圖框與 ISO 7200 標題欄、每個模型軸各標一次的外形尺寸（ISO 129「同一尺寸只標一次」）、孔標註與孔位的座標尺寸（由幾何求出，見下）、你給的備註。輸出 `drawing.dxf`（R2018、單位 mm，是來源檔）與 `drawing.pdf`（向量、真實比例、一頁），`formats` 可再加 `drawing.svg`（向量，本工具箱自己的寫出器）與 `drawing.png`（把 PDF 轉成 8 位元灰階圖片）。需要選用的 `drawing` extra（`uv sync --extra drawing`）；PNG 另需 `png` extra（`uv sync --extra png`）。輸入（全為字串、布林）：`model_path`、`views`（逗號分隔，自 `front back top bottom left right iso`，最多 7 個、不可重複，預設 `front,top,right`）、`projection`（`third_angle` 預設，或 `first_angle`）、`sheet`（`auto` 或 `A4` `A3` `A2` `A1` `A0`）、`scale`（`auto` 或 `1:1` `1:2` `1:5` `1:10` `1:20` `1:50` `1:100` `2:1` `5:1` `10:1`）、`hidden_lines`（預設 true）、`dimensions`（`auto`：外形尺寸加孔標註與座標尺寸；`overall`：只有外形尺寸；`none`）、`title`（80 字）、`part_number`（40）、`material`（40）、`revision`（8，預設 `A`）、`general_tolerance`（80，只印文字）、`notes`（以 `;` 分隔，最多 6 則、每則 100 字，印在標題欄上方）、`formats`（`dxf`、`pdf`、`svg`、`png` 的逗號清單；DXF 一律輸出；只要 `png` 時不寫 `drawing.pdf` 檔，PNG 在記憶體裡由 PDF 轉成）、`features_path`（`geometry_make_part` 寫出的 `features.json`，見下）。文字可用中文等非 ASCII 字元，不可含控制字元、Tab 與換行。全形空白（U+3000）、不換行空白（U+00A0）與其他空白分隔字元（Unicode 類別 Zs）會被接受並一律寫成一般空白（U+0020），結果有警告說明：許多 CAD 字型沒有這些字元的字形，PDF 後端也無法得知缺字的字寬，一般空白在每個檢視器都一樣。文字中的 `%%c`、`%%d`、`%%p`（以及 `%%u`、`%%nnn`）在 DXF 單行文字裡是控制碼，檢視器會畫成直徑、度數、正負號；你輸入的字面文字會被跳脫（連續兩個以上的 `%`，每個寫成 `%%%`；單獨的 `%` 不用），所以顯示的就是你輸入的字。`drawing_read` 讀回時會把真正的控制碼轉成符號（`%%c10` 讀出 `Ø10`），而跳脫過的字面文字讀出原樣（`%%c10`），所以兩者讀回來分得出來；測試以 `drawing_read` 讀回 `drawing_make_views` 的 DXF 驗證，並確認 PDF 畫的是字面字元而不是符號。

**版面。** 第三角法（預設）：上視圖在前視圖上方、右視圖在其右、左視圖在其左、下視圖在下方、後視圖在右視圖右側；第一角法左右上下對調。等角視圖放在上排最右欄上方的空位。視圖間距 15 mm，再加該視圖尺寸列所在那一側每列 8 mm（水平尺寸在視圖下方、垂直尺寸在左側）；有孔標註的視圖，右側另留一塊放標註文字的空間（6 mm 加最長文字寬再加 2 mm），有座標尺寸的視圖多占一列在下方、一列在左側，離視圖最近，外形尺寸往外移一列。`sheet="auto"` 與 `scale="auto"`：先取最大的標準比例（依序 1:1、1:2、1:5、1:10、1:20、1:50、1:100），再取放得下的最小圖紙；視圖加間距與尺寸列在 1:1 時不到 A4 圖框面積四分之一的零件會放大到 10:1、5:1、2:1 中放得下的最大者。只固定圖紙或只固定比例時，只搜尋另一個。放不下回 `drawing.layout_failed`，`details.fits_at_scale` 與 `details.fits_on_sheet` 指出放得下的比例或圖紙。

**尺寸。** 真正的 `DIMENSION` 實體，`DIMLFAC = 1/比例`，所以任何比例下顯示的都是真實尺寸（1:2 的圖上 50 mm 長的線顯示 100）；樣式 `ENG_ISO`（字高 3.5、箭頭 3）與公制線型 `HIDDEN_MM`、`CENTER_MM` 是本工具箱自己的，因為 ezdxf 預設樣式在 mm 圖上只是一個點。外形尺寸的數值是投影可見幾何外框的大小。

**孔標註與座標尺寸（`dimensions="auto"`）。** 孔由 `geometry_measure` 同一套孔搜尋（`brep.inventory`）從幾何找出，不信任任何檔案裡的數字。孔依直徑、方向、貫穿或深度（盲孔的方向也算）分組，每組一則標註：`Ø10 THRU`、`4× Ø6.6 THRU`、`Ø5 DEEP 8`（Arial 沒有深度與沉頭符號，所以用文字 `DEEP`）。標註畫在第一個「沿孔軸方向看」的視圖（貫穿孔取要求順序中第一個，盲孔優先取看得到孔入口的視圖），用一條 `LEADER` 從孔邊指到右側的文字，一組只指一個孔（最靠近文字的那個，引線不穿過同組的其他孔）。孔位用真正的座標 `DIMENSION`（ordinate）：以該視圖幾何的左下角為基準，標 0 與每個不同的孔座標一次，`DIMLFAC = 1/比例`，任何比例下讀到的都是真實座標。限制：每個視圖最多 6 組標註，每個軸最多 16 個座標，超過的列在警告裡、不畫；沒有視圖沿孔軸方向看時，警告會說要加哪個視圖（鉛直孔要 `top`）；文字互相重疊只警告不移動；超過 3000 個面的模型不找孔；只存曲面的模型（圓柱是旋轉曲面）找不到孔，警告會說。沉頭孔、埋頭孔與斜面上的特徵不標。

**`features_path` 與雜湊綁定。** `geometry_make_part` 寫出的 `<名稱>.features.json` 記錄螺紋與配合（`H7`、`M6x1-6H`），因為這兩樣在模型裡只是文字、幾何只有名義直徑。檔案裡的 `step_sha256` 必須等於本次畫的模型檔的 sha256，配合與螺紋才會印出（`Ø10 H7 THRU`、`Ø5 M6x1-6H THRU`）；不符就警告並**不印任何配合**（標註仍來自幾何），`data.features.status` 為 `bound`、`hash_mismatch` 或 `not given`。宣告的孔要與模型的孔直徑、方向、貫穿或深度、中心線都對上才算（多出來的宣告孔有警告）。尺寸一律來自幾何，檔案只提供這兩個字。檔案內容是資料：格式錯誤回 `input.invalid`（`details.pointer` 是 JSON 指標），要印的字串必須是可印字元、至多 24 字、不含換行；`dimensions` 不是 `auto` 時不讀這個檔（有警告）。

**SVG 與 PNG。** SVG 由本工具箱自己的後端從同一份記錄寫出（單位 mm、`viewBox` 同圖紙、`<path>` 元素，沒有腳本、樣式或外部參照；文字同 PDF 是字形輪廓），測試用標準庫的 XML 解析器讀它、拿各線寬與虛實的長度對 pdfminer.six 從 PDF 讀到的長度（相對誤差 0.2% 內），並用 Chromium 實際畫過一次。PNG 是 PDF 由 PDFium（pypdfium2）轉成 150 dpi 的 8 位元灰階圖（長邊最多 4000 像素，A0 約 85 dpi），由本模組用標準庫編成沒有附加區塊（時間、文字、gamma）的 PNG。**確定性範圍：** 同一台機器、同一個 PDFium 建置，任何 `PYTHONHASHSEED` 下位元組相同（測試 12 個種子加未設定）；跨機器不保證（PDFium 的反鋸齒與 zlib 版本不同，像素或位元組可能不同），警告會這樣說。

```
drawing_make_views(model_path=".../bracket.step", title="BRACKET")
```
→ 對 `geometry_make_part` 的範例零件（100 × 50 × 20 mm，Ø10 貫穿孔在 (25, 25)，四條外緣豎邊 R2）：`scale = 1`（A4，297 × 210 mm）、三個尺寸 `100`、`20`、`50`，上視圖有標註 `Ø10 THRU`（給 `features_path` 且雜湊相符時是 `Ø10 H7 THRU`）與座標尺寸 x 0、25 和 y 0、25；上視圖可見線總長 284 + 14π = 327.9823 mm（4 條線、4 段弧、1 個圓）；前視圖與右視圖的隱藏線各 40.0000 mm（孔的兩條輪廓線）。

`values`：`scale`（無因次，1.0 = 1:1，0.5 = 1:2）、`view_count`、`dimension_count`（`DIMENSION` 實體數：外形尺寸加座標尺寸）、`callout_count`、`sheet_width`、`sheet_height`（mm）。`data`：`sheet`（`name`、`size_mm`、`scale_text`、`projection`）、`views`（每個視圖的 `name`、`origin_mm`（該視圖模型原點在圖紙上的位置，圖上座標 = origin + 比例 × 模型座標）、`extent_mm`、`visible_primitives`、`hidden_primitives`、`method`、`verified`、`dropped`）、`dimensions`（外形尺寸：`kind`、`view`、`orientation`、`value_mm` 真實尺寸、`text`、`source` 如 `bbox:x`）、`callouts`（每則：`text`、`view`、`count`、`diameter_mm`、`thru`、`depth_mm`、`axis`、`hole_ids`、`entry_points_mm`、`fit`、`thread`、`from_features_file`）、`ordinates`（`view`、`axis`、`value_mm`、`text`）、`features`（`given`、`status`、檔內與模型的 sha256、宣告與對上的孔數）、`png`（有要 PNG 時：像素寬高、dpi）、`layout_warnings`、`coverage`、`determinism`。視圖的 `method` 為 `polygonal`（面數超過上限，改用多邊形演算法）或 `exact_unverified`（精確結果無法與多邊形結果交叉驗證）時，各有專屬警告。

**只有曲面的模型。** 檔案裡沒有封閉實體時（例如只存曲面的 IGES，圓柱會被存成旋轉曲面），回傳警告「no closed solid」；投影前先把各面縫合（sewing，容差 0.001 mm）並標記接縫與相切邊，所以圓柱孔中心不會多出一條接縫虛線，隱藏線與同一零件的 STEP 版相同（測試以同一零件的 STEP 與只存曲面的 IGES 比對）。曲面沒有封閉時隱藏線仍可能不可靠，要對照模型。

**確定性。** 同一模型與參數得到位元組完全相同的 DXF、PDF 與 SVG，跨行程（任何 `PYTHONHASHSEED`）也一樣；PNG 同一台機器相同、跨機器不保證（見上）：測試以 1 到 12 的種子與未設定各跑一次整個工具（四種格式），比對 sha256。ezdxf 寫 CLASSES 區段的順序會隨 `PYTHONHASHSEED` 改變，工具在存檔前把它排序（細節與量測在 [docs/geometry-drawing/drawing-determinism.md](../geometry-drawing/drawing-determinism.md)）。PDF 由本工具箱自己的後端寫出（沒有日期、產生者字串，不壓縮，物件順序固定）；**PDF 沒有文字圖層**：文字是字形輪廓，無法搜尋或複製，DXF 才有文字。中文標題會使用機器上第一個有該字形的 CJK 字型（`conventions.FALLBACK_FONTS`）；沒有任何字型有的字元，會在警告中逐字列出，PDF 上顯示字型的缺字框。字型不同的機器，同一張圖的 PDF 位元組會不同。

**範圍與限制（`data.coverage`）。** 標外形尺寸、孔標註與孔位座標尺寸；沒有中心線、公差、表面粗糙度，沉頭孔與埋頭孔不辨識、斜面上的特徵不標。外形尺寸來自外框，孔的直徑、數量、位置來自幾何，配合與螺紋只來自雜湊相符的 `features.json`。標題欄一律印 `UNREVIEWED - generated by eng-toolkit, not engineering approved`。這不是完整的製造用圖，必須對照模型複核。

## `drawing_read`
讀別人做的圖：`.dxf`（R12 到 R2018、文字或二進位、舊式編碼與 CJK）與 `.pdf`，回傳結構化內容。需要選用的 `drawing` extra（`uv sync --extra drawing`：ezdxf 與 pdfminer.six，都是 MIT）。輸入（字串、整數）：`path`、`detail`（`summary` 預設，或 `full`：每個項目多帶欄位，不是多帶項目）、`max_items`（每個清單回傳幾筆，1 到 500，預設 25）。`.dwg`（含副檔名被改成 `.dxf` 的）只在使用者自己裝了 LibreDWG 的 `dwg2dxf` 時才讀，見下面〈DWG 輸入〉；沒裝回 `engine.unavailable`。

```
drawing_read(path=".../flange.dxf")
```
→ `data.summary` 是一段話（版本、單位、外框、標題欄、尺寸、視圖數），後面是同一份內容的結構化欄位，每個清單截到 `max_items` 並附 `total`；完整內容（清單最多各 20 000 筆）在 `data.full_json` 指的 `drawing_read.json`，不會塞進對話。預設回傳約 5 到 6 kB（slice 4 的圖）。`value` 是實體數（PDF 為頁數）；`values` 有 `layer_count`、`dimension_count`、`text_count`，單位已知時有 `extent_width`、`extent_height`（mm，**只算幾何**，不含尺寸與文字；沒有幾何時沒有這兩個值）；PDF 有 `page_width`、`page_height`、`vector_length`（mm）。

**單位絕不假設。** `data.units` 由 `$INSUNITS` 給出名稱與換算成 mm 的倍數：英吋圖寫 `inch`、`25.4`，所有長度同時給圖面單位與 `_mm`。檔案沒寫單位（`$INSUNITS` 缺、不是整數或為 0；R12 與更舊的檔案沒有這個變數；單位表含 1 到 24 號，包括美制測量單位）；單位只讀檔案自己的檔頭文字，不用函式庫補的預設值時 `name` 是 `unitless`、`mm_per_unit` 與所有 `*_mm` 都是 `null`，摘要寫 `UNKNOWN`，並有警告；`$MEASUREMENT`（公制或英制）只當提示回報，不用來換算。

**座標尺寸與孔標註。** 每個座標尺寸（ordinate）回報 `ordinate_axis`（`x` 或 `y`，由 group 70 的 64 位元），摘要把它們依軸合併成一句（`Ordinates (8): x 0, 10, 50, 90; y 0, 10, 25, 40 (coordinates from the datum corner of the view; 0 is the datum).`，不再是一串沒有軸的 `ordinate 10 mm`）。讀起來像孔標註的文字（直徑符號加數字、`THRU`、`DEEP`、螺紋 `M6x1`）單獨列出：`Hole callouts (2, found by their wording in the texts): 4× Ø6.6 THRU (4 holes); Ø5 DEEP 8 (1 hole).`，沒有數量前綴的直徑標註算一個孔；`data.hole_callouts` 列出文字與位置，並標明是依用字推測。圖上同時有標註與座標尺寸時，摘要說這些座標「probably」是那些孔的位置。

**尺寸的值。** 不信 group 42，由定義點算出量測值（線性、對齊、直徑、半徑、兩線與三點角度、座標），再套 `DIMLFAC`：實體自己的 `DSTYLE` 擴充資料（`drawing_make_views` 就是這樣寫 `1/比例`）優先，其次是尺寸樣式表，再來是檔頭 `$DIMLFAC`（樣式不存在或沒設時），都沒有就是 1；負值只在圖紙空間生效。`DIMLFAC` 只縮放長度，不縮放角度。每個尺寸回報 `measured`（圖上量到的）、`dimlfac`、`dimlfac_source`、`value`（顯示的值）、`value_mm`、`text`（觀看者看到的：先看尺寸匿名塊裡的文字，沒有就用文字覆寫把 `<>` 換成值，再沒有就用 `DIMDEC`、`DIMZIN`、`DIMRND`、`DIMPOST` 格式化；直徑加 `Ø`、半徑加 `R`）、`text_override`；塊內文字與算出的值不一致時有警告。範例：100 mm 的量測、`DIMLFAC` 2，讀出 200；1:2 的 `drawing_make_views` 圖上量到 50 的尺寸讀出 100。

**標題欄。** `title_block.fields` 每個欄位帶 `value`、`source`、`inferred`、`confidence`。屬性式（`INSERT` 加 `ATTRIB`）以固定詞彙（中英文）比對標籤名：`inferred: false`、`confidence: 1.0`。標籤與值分開的文字（`drawing_make_views` 的寫法）靠位置配對（值在標籤正下方或右方最近的非標籤文字，每個值只給最近的標籤）：`inferred: true`、`confidence` 小於 1（同圖層 0.85，不同圖層再扣）。不在詞彙裡的標籤不指派；空的格子不會拿到下一格的值。`UNREVIEWED - generated by eng-toolkit ...` 那行回報為 `status`，並標 `recognised_as`。**視圖數**（`views`）是依位置把幾何分群的推測（排除 FRAME、TITLE、DIM、TEXT 等圖面元素層），同樣標 `inferred`。

**文字編碼。** 2007 以後（AC1021 起）是 UTF-8；更舊的檔案依 `$DWGCODEPAGE` 解碼（ANSI_950 大五碼、ANSI_936、ANSI_932、ANSI_949 都有測試），`data.dxf.encoding` 回報實際編碼；`\U+XXXX` 跳脫、`%%c` `%%d` `%%p`、MTEXT 格式碼都轉成字元。

**PDF。** `pages`：每頁大小（mm，已處理 `/Rotate`、`/UserUnit`）、文字行與位置（mm，原點在頁面左下）、字型大小（`size_pt` 是 Tf 字級乘上文字矩陣與目前變換矩陣的縮放，轉向的文字也一樣，不是單一字元的寬度）；`vector`：路徑數（描邊、填色、矩形）、依線寬與虛實分組的長度（沒設線寬的描邊用 PDF 規格的初始線寬 1 pt，明確的 `0 w` 仍是 0）、直線與曲線長度、外框；曲線以 32 弦展開，只有描邊的路徑計入長度。**沒有文字圖層的 PDF**（`drawing_make_views` 寫的 PDF，文字是字形輪廓）回 `text.has_text_layer: false` 與警告，只給向量統計，不假裝讀到文字；有圖片時說明沒有 OCR。PDF 內的 JavaScript、URI、Launch、OpenAction、嵌入檔等列在 `pdf.active_content` 與警告，**不執行、不開啟、不跟隨**（掃描解析後的物件，所以壓縮在物件串流裡的也找得到，另外掃原始位元組並解開 `#xx` 名稱跳脫）；連結網址不會複製進警告。

**不受信任的檔案。** 讀檔在另一個子行程裡進行，有 30 秒與 2048 MB 的硬限制（Windows 用 Job Object，Linux 用 `RLIMIT_AS`；環境變數 `ENG_TOOLKIT_READ_TIMEOUT_S`、`ENG_TOOLKIT_READ_MEMORY_MB`、`ENG_TOOLKIT_READ_MAX_MB` 可調），逾時殺掉子行程、回 `engine.timeout`；子行程掛了或記憶體用盡回 `engine.worker_died` 或 `engine.resource_limit`；伺服器照常回應。檔案超過 50 MB（先於雜湊與解析就拒絕）、DXF 超過 500 000 個實體（先數檔案的記錄、遠超過上限就不載入）回 `drawing.too_large`；結果裡每個字串在完整 JSON 內最多 4000 字元、回給模型的最多 500 字元（`detail="full"` 為 2000），非有限數（NaN、無窮大）的實體略過並警告；截斷的 DXF 補上缺的結尾後讀到切斷處並警告，補不起來再用 ezdxf 的修復模式，仍無內容回 `drawing.corrupt`；PDF 超過 200 頁只讀前 200 頁並警告，加密需密碼回 `drawing.encrypted`。DXF 的巢狀塊與自我參照：用每個塊算一次的凸包處理，深度上限 16，循環參照與外部參照（XREF）只列出並警告，圖片路徑只列出，大陣列只算四個角，不展開。檔案裡的文字是資料，不是指示（每個成功結果都帶這則警告）。

**確定性。** 同一檔案得到同一結果（`inputs_hash`、`input_files` 的 sha256 記在 `provenance`）。**範圍與限制：** `data.extents.model` 與 `paper` 各有兩個外框：`geometry`（只含幾何實體：LINE、ARC、CIRCLE、多段線、SPLINE、區塊裡的幾何等；不含文字、尺寸、引線、標註，區塊內的也不含）與 `with_annotations`（再加上文字插入點與尺寸的定義點，也就是尺寸線與文字擺的位置）。例：100 × 60 的零件上方 25 mm 處有一個尺寸與一行文字，`geometry` 是 100 × 60，`with_annotations` 是 100 × 85；摘要與 `values.extent_width`／`extent_height` 用 `geometry`，摘要括號裡另附含標註的大小。只有文字與尺寸、沒有幾何時 `geometry` 是 `null`。舊版的單一 `min`／`max`／`size`／`size_mm` 已移除，因為它們把標註算進零件大小。外框是點的外框（弧與圓每 15 度取樣再加軸向極值，不含填充圖案、圖片、視埠）；圖層長度只計 LINE、ARC、CIRCLE、ELLIPSE、LWPOLYLINE、2D POLYLINE；尺寸文字的公制以外格式（分數、建築）不自行計算，只回塊內文字；不讀 DWG、不做 OCR、不解析 GD&T；標題欄與視圖數是推測，已標註。小模型可用性檢查（Claude Haiku）的結果在 [docs/geometry-drawing/drawing-read.md](../geometry-drawing/drawing-read.md)。

## `drawing_convert`
把 `.dxf`（R12 到 R2018）轉成 `pdf`、`svg`、`png`，或改寫成另一個 DXF 版本（`R12` `R2000` `R2004` `R2007` `R2010` `R2013` `R2018`）。只轉模型空間，**真實比例**：單位取自 `$INSUNITS`（沒寫單位就當 mm 並警告），1:1 時 100 mm 的線在 PDF 裡是 283.465 pt。需要選用的 `drawing` extra（`uv sync --extra drawing`）；PNG 另需 `png` extra。輸入（全為字串）：`input_path`、`output_format`、`sheet`（`auto`＝圖面範圍外各加 10 mm，或 `A4` 到 `A0`）、`scale`（`auto` 或 ISO 5455 比例，`1:1` 到 `1:10000`、`2:1` 到 `50:1`；圖紙放不下會拒絕而不是裁切）、`dxf_version`（只對 DXF 輸出有效）。

```
drawing_convert(input_path=".../flange.dxf", output_format="pdf", sheet="A3", scale="1:1")
```
→ 輸出 `converted.<格式>` 在工作目錄（原檔不動，附 sha256）；`data.losses.items` 列出目標格式放不下或沒畫出的東西（顏色一律黑、字型被取代、PDF/SVG/PNG 的文字沒有文字層、MTEXT 格式、ACIS 實體與代理物件、被關閉或凍結的圖層、圖紙空間、外部參考；DXF 輸出則是舊版本沒有的實體類型與屬性，例如 R12 沒有 LWPOLYLINE、線寬、真彩色、`$INSUNITS`，是寫出檔案再讀回來數出來的），`data.entities` 依類型數出畫了與跳過的實體數。`.dwg` 同樣先經 `dwg2dxf` 轉成 DXF 再轉（見下面〈DWG 輸入〉）。檔案不受信任：在子行程裡轉換（預設 60 秒、2048 MB；`ENG_TOOLKIT_CONVERT_TIMEOUT_S`、`ENG_TOOLKIT_CONVERT_MEMORY_MB`、`ENG_TOOLKIT_CONVERT_MAX_MB` 可調），超過 20 萬個實體、100 萬單位工作量（區塊展開後）、區塊自我參照或巢狀超過 16 層、座標超過 1e12 mm、頁面超過 5080 mm 的檔案會被拒絕或截掉並說明原因，NaN 與無限大座標的實體會被移除並列出。詳見 `docs/drawing-convert/design.md`。

### DWG 輸入（選用，需自行安裝 LibreDWG）
`drawing_read` 與 `drawing_convert` 收 DWG（R12、R2000、R2004、R2007、R2010、R2013、R2018）的方式，是把檔案交給**使用者自己安裝**的 [LibreDWG](https://www.gnu.org/software/libredwg/) `dwg2dxf`（GPL-3.0）當**獨立行程**執行，再讀它寫出的 DXF；本專案不內嵌、不 import、不連結它。用環境變數 `ENG_TOOLKIT_DWG2DXF`（完整路徑）或 `PATH` 上的 `dwg2dxf` 指定；找不到時回 `engine.unavailable` 並說明怎麼取得，伺服器不受影響，`toolkit_doctor` 的 `external_tools` 會列出是否找到、路徑與版本。執行時有 60 秒（`ENG_TOOLKIT_DWG2DXF_TIMEOUT_S`）、2048 MB（`ENG_TOOLKIT_DWG2DXF_MEMORY_MB`）與輸出大小上限，環境變數被清空、工作目錄是該次的 job 目錄；轉出的 DXF 先驗證再走原本的 DXF 流程。結果的 `data.dwg` 寫明 DWG 版本與 `dwg2dxf` 版本，並警告 LibreDWG 可能漏掉或改變內容。目前只用假的 `dwg2dxf` 測過，沒有對真的 LibreDWG 跑過。詳見 `docs/drawing-dwg/design.md`。
