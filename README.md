# eng-toolkit

**AI 可調用的開源工程工具箱** — 一組 MCP (Model Context Protocol) server，把寬鬆授權的開源工程引擎包成 AI 代理可直接呼叫的工具，回傳**帶單位、可重現、附來源**的結構化結果。

> ⚠️ 所有輸出皆標示 `review_status: "unreviewed"`。本工具箱的結果是**未經工程核可**的估算，必須由合格工程師複核後才能用於設計或採購。

目前有四個領域：`calc`（油壓計算）、`metrology`（ISO 286 配合查詢與反查、一維公差堆疊、掃描點雲對 STEP 模型的偏差檢查）與 `geometry`（STEP／IGES／BREP 模型的精確量測：體積、面積、質量、面與孔清單、距離角度厚度查詢；以及由 JSON 規格建出零件並輸出 STEP／GLB／STL／BREP，不執行任何程式碼），以及 `drawing`（由 3D 模型產生含隱藏線、ISO 圖框與標題欄、外形尺寸、孔標註與座標尺寸的三視圖，輸出可重現的 DXF、向量 PDF、SVG 與 PNG；讀 DXF 與 PDF 圖檔）。2D 圖檔讀取／EDA／模擬等領域尚未開始。

---

## 架構

```
Claude Code / 任何 MCP 客戶端
        │  stdio (JSON-RPC)
        ▼
┌────────────────────────────────────────────────┐
│  gateway  (FastMCP, Apache-2.0)                │
│   toolkit_list_tools   toolkit_doctor          │
│   ├── mount(namespace="calc") ──► calc 子 server │
│   │        calc_cylinder_force                 │
│   │        calc_pipe_pressure_drop  (fluids)   │
│   ├── mount(namespace="metrology") ► metrology 子 server│
│   │        metrology_fit_limits  (ISO 286 自建表)  │
│   │        metrology_fit_search  (間隙窗口反查配合)  │
│   │        metrology_stackup_1d  (NumPy)           │
│   │        metrology_check_scan  (Open3D + OCP, 選用)│
│   ├── mount(namespace="geometry") ► geometry 子 server  │
│   │        geometry_measure  (OCP, 選用 cad extra)  │
│   │        geometry_make_part (JSON 規格 → STEP, OCP)│
│   ├── mount(namespace="drawing") ► drawing 子 server   │
│   │        drawing_make_views (OCP + ezdxf, 選用 drawing extra; PNG 另需 png extra)│
│   │        drawing_read  (ezdxf + pdfminer.six, 選用 drawing extra)│
│   └── (未來) eda_* ...                            │
├────────────────────────────────────────────────┤
│  contract.py   統一回傳契約 (pydantic)          │
│  units.py      單位守門 (pint)：拒收裸數字       │
│  workspace.py  ws/<job-id>/ 落檔 + sha256       │
│  licenses.py   授權分級與環境掃描               │
└────────────────────────────────────────────────┘
```

**工具命名**：MCP 工具名稱不能含 `.`，設計文件中的 `calc.cylinder_force` 在 MCP 上即為 `calc_cylinder_force`（`<領域>_<工具>`）。

---

## 安裝（Windows 11，乾淨環境一鍵）

需求：只要能連網。腳本會自動安裝 [uv](https://docs.astral.sh/uv/) 與 Python 3.12，不需要預先裝 Python。

```powershell
git clone https://github.com/Lin830326/eng-toolkit.git
cd eng-toolkit
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

腳本會：安裝 uv → `uv sync --all-groups --extra scan --extra drawing --extra png` 依 `uv.lock` 建立 `.venv`（含掃描檢查用的 Open3D 與 OpenCASCADE，以及出圖用的 ezdxf 與 PNG 用的 pypdfium2） → 執行 `toolkit_doctor` → 印出註冊到 Claude Code 的指令。

手動安裝（已有 uv）：

```bash
uv sync --all-groups --extra scan --extra drawing --extra png
uv run eng-toolkit doctor        # 健康檢查；exit 0 = 全部通過
uv run pytest                    # 測試
```

`metrology_check_scan` 用的 Open3D 與 `cadquery-ocp-novtk` 合計約 340 MB、匯入也慢，所以放在選用的 `scan` extra，不在核心依賴也不在 `dev` 群組：預設的 `uv sync` 與 `uv run eng-toolkit` 不會安裝它們，要用這個工具（或跑它的測試）請加 `--extra scan`，開發環境用上面的 `uv sync --all-groups --extra scan`。沒裝時工具仍會列出，呼叫會回 `engine.unavailable` 並附安裝指令，`toolkit_doctor` 也只標示 `not-installed (optional)`，不算失敗；`metrology_check_scan` 的測試會因缺少引擎而跳過並提示 `uv sync --extra scan`。

OpenCASCADE 另有獨立的 `cad` extra（`uv sync --extra cad`，只裝 `cadquery-ocp-novtk`）：它是所有 3D 幾何程式碼（`eng_toolkit/brep/`）唯一的引擎，`geometry_measure` 只需要它；`scan` extra 等於 `cad` 加 Open3D。沒裝時 `geometry_measure` 同樣會列出，呼叫回 `engine.unavailable` 並附 `uv sync --extra cad`，它的測試會因缺少引擎而跳過。出圖另有 `drawing` extra（`uv sync --extra drawing`，等於 `cad` 加 `ezdxf`，MIT 授權；連帶的 fonttools、pyparsing 也是 MIT）：`drawing_make_views` 需要它，沒裝時工具照常列出、呼叫回 `engine.unavailable` 並附 `uv sync --extra drawing`。**不要安裝 `ezdxf[draw]`**：它會帶進 PyMuPDF（AGPL），`toolkit_doctor` 會把已安裝的 `pymupdf` 當成授權政策失敗；本工具箱的 PDF 由自己的後端（約 150 行）寫出。`drawing_make_views` 的 PNG 另需 `png` extra（`uv sync --extra png`，等於 `drawing` 加 `pypdfium2`，BSD-3-Clause 或 Apache-2.0；wheel 內含 PDFium 與它的函式庫，全是寬鬆授權，沒有 GPL 或 AGPL，清單在 `licenses.KNOWN`）：只用來把**本工具剛寫出的 PDF** 轉成圖片，不解析任何呼叫者給的檔案；沒裝時要求 `png` 會回 `engine.unavailable` 並附 `uv sync --extra png`，其餘格式不受影響。`drawing_read` 讀 PDF 用 pdfminer.six（MIT，在 `drawing` extra）；測試用的獨立 PDF 讀取器 `pdfplumber`（MIT，連帶 `pypdfium2`）只在 `dev` 群組。各引擎屬於哪個 extra 記在 `licenses.KNOWN`（`Engine.extra`），`engine.unavailable` 的安裝指令由它產生，`tests/test_extras_registry.py` 會檢查它與 `pyproject.toml` 一致。

---

## 啟動伺服器

```bash
uv run eng-toolkit serve --transport stdio                      # 預設；由 MCP 客戶端以子行程啟動
uv run eng-toolkit serve --transport http --host 127.0.0.1 --port 8765   # streamable HTTP：http://127.0.0.1:8765/mcp
uv run eng-toolkit doctor                                       # 健康檢查，exit 0 = 通過
```

`eng-toolkit` 不帶參數等同 `serve --transport stdio`。

**任何 MCP 客戶端都能用**：工具箱內部**不呼叫任何 LLM、不需要任何 API 金鑰**（`toolkit_doctor` 的 `llm_free` 欄位會回報環境裡有沒有 LLM SDK 或金鑰）。Claude Code、OpenAI Codex、Cursor、LM Studio、Ollama 相容前端等皆可接上。

## 接上 MCP 客戶端

所有客戶端的設定格式基本相同：stdio 給 `command` + `args`；HTTP 給 `url`。

**Claude Code（使用者層級，所有專案可用）**

```bash
claude mcp add --scope user eng-toolkit -- uv run --directory "C:\path\to\eng-toolkit" eng-toolkit
```

要用 `metrology_check_scan`，註冊指令要帶 scan extra，否則該工具只會回 `engine.unavailable`：

```bash
claude mcp add --scope user eng-toolkit -- uv run --extra scan --directory "C:\path\to\eng-toolkit" eng-toolkit
```

（其他客戶端的 `args` 同理，在 `run` 後加 `"--extra", "scan"`。）或用本 repo 附的專案層級 `.mcp.json`（在 repo 內開 Claude Code 即可；它不帶 scan extra，要用掃描檢查請改成上面的指令）。接上後 `/mcp` 應看到 `eng-toolkit` 與各領域的工具（`toolkit_list_tools` 列出全部）（要用 `geometry_measure`、`geometry_make_part` 則註冊指令至少帶 `--extra cad`，要用 `drawing_make_views`、`drawing_read` 帶 `--extra drawing`）。

**Cursor**（`~/.cursor/mcp.json` 或專案 `.cursor/mcp.json`）

```json
{
  "mcpServers": {
    "eng-toolkit": {
      "command": "uv",
      "args": ["run", "--directory", "C:\\path\\to\\eng-toolkit", "eng-toolkit"]
    }
  }
}
```

**LM Studio**（Program → Install → Edit `mcp.json`）

```json
{
  "mcpServers": {
    "eng-toolkit": {
      "command": "uv",
      "args": ["run", "--directory", "C:\\path\\to\\eng-toolkit", "eng-toolkit"]
    }
  }
}
```

**HTTP 模式**（先執行 `uv run eng-toolkit serve --transport http`；適合 Ollama 相容前端或遠端客戶端）

```json
{
  "mcpServers": {
    "eng-toolkit": { "url": "http://127.0.0.1:8765/mcp" }
  }
}
```

> **HTTP 與讀檔路徑。** 讀檔的工具（`drawing_read`、`geometry_measure`、`drawing_make_views`、`metrology_check_scan`）收到什麼路徑就讀什麼路徑，只要執行伺服器的使用者讀得到。stdio 模式下伺服器就是提問的那個使用者，這沒問題；用 `--transport http` 時請設定環境變數 `ENG_TOOLKIT_READ_DIRS`（目錄以 `os.pathsep` 分隔，Windows 用 `;`，其他用 `:`），例如 `ENG_TOOLKIT_READ_DIRS="D:\drawings;D:\models"`：之後只讀這些目錄底下的檔案（解析 `..`、符號連結與大小寫後比對）加上伺服器自己的工作區（可以讀回別的工具寫出的檔案），其他路徑回 `input.path_not_allowed`，錯誤訊息不含路徑。沒設定就不限制。`toolkit_doctor` 的 `data.file_reads` 會顯示目前狀態。檔案只開一次：白名單比對的是「已開啟檔案」由作業系統回報的真實路徑（Windows `GetFinalPathNameByHandleW`、Linux `/proc/self/fd`），之後的位元組複製到本次呼叫的 `ws/<job-id>/input/`，工具讀的是這份複本（`provenance.input_files` 的 `sha256` 是複本的雜湊，`path` 是原檔的真實路徑），所以檢查之後路徑被換成指向外面的目錄連結（junction、符號連結）也讀不到外面的檔案；以並行翻轉 junction 的測試驗證，舊做法 4269 次讀取洩漏 411 次，現在 0 次。代價是每個輸入檔在工作區多一份複本。允許目錄本身被換掉不在防護內。**未設定**這個變數才是不限制；**設了但是空的**（空字串、只有空白或分隔符號）是「只允許工作區」：沒有任何其他目錄可讀，因為把清單清空的人要的是更少的檔案可讀，不是更多。`toolkit_doctor` 的 `data.file_reads` 會寫 `restricted: true`、`directories: 0` 與說明；要解除限制請取消設定變數。

**OpenAI Codex CLI**（`~/.codex/config.toml`）

```toml
[mcp_servers.eng-toolkit]
command = "uv"
args = ["run", "--directory", "C:\\path\\to\\eng-toolkit", "eng-toolkit"]
```

> 為了讓地端中小模型也能穩定呼叫，工具維持少量粗粒度（目前 10 個），所有輸入參數都是**基本 JSON 型別**（字串／布林／整數），每個參數附描述；數量一律是「數字 + 單位」的單一字串，如 `"100 mm"`。

---

## 工具

### `toolkit_doctor`
檢查 Python 版本、每個引擎的安裝版本與授權（宣告值 vs. 套件 metadata）、工作區可寫性、pint 自檢，並掃描整個環境標出 GPL／AGPL／未知授權的套件。任何 GPL／AGPL 出現 → `ok: false`。

### `toolkit_list_tools`
列出已安裝領域、背後引擎（版本、授權分級）與全部工具名稱。

### `calc_cylinder_force`
單桿油壓缸理想推力／拉力。輸入：`bore`、`rod`（空字串 = 無桿）、`pressure`、`output_unit`（預設 `kN`）。

```json
{"bore": "100 mm", "rod": "50 mm", "pressure": "210 bar"}
```
→ `push_force = 164.93 kN`、`pull_force = 123.70 kN`、`bore_area`、`annulus_area`、`area_ratio`。
不含密封摩擦、背壓、桿側壓力（警告中註明）。

### `calc_pipe_pressure_drop`
直管 Darcy–Weisbach 摩擦壓降。層流 `64/Re`；紊流 Colebrook（Clamond 精確解），由 MIT 授權的 [`fluids`](https://github.com/CalebBell/fluids) 計算。輸入：`diameter`、`length`、`flow_rate`、`density`、`viscosity`（**動力**黏度，cSt 會被拒收）、`roughness`（預設 0.0015 mm）、`output_unit`（預設 `bar`）。

```json
{"diameter": "10 mm", "length": "1 m", "flow_rate": "4.71 L/min",
 "density": "870 kg/m^3", "viscosity": "40 cP"}
```
→ `pressure_drop ≈ 0.128 bar ± 5%`、`velocity`、`reynolds`、`friction_factor_darcy`、`head_loss`。
不含管件、彎頭、進出口與高程損失。

### `calc_orifice_flow`
孔口／節流閥流量 `Q = Cd·A·√(2Δp/ρ)`。`pressure_drop` 與 `flow_rate` 擇一給，另一個由公式算出。輸入：`diameter`、`density`、`discharge_coeff`（預設 0.62）、選填 `viscosity`（只用來算孔口雷諾數並在 Re < 10⁴ 時警告）、`flow_unit`、`pressure_unit`。

```json
{"diameter": "5 mm", "density": "870 kg/m^3", "pressure_drop": "100 bar"}
```
→ `flow_rate ≈ 110.75 L/min`、`orifice_velocity`、`orifice_area`。不含氣體可壓縮性、穴蝕、進流速度修正（Cd 由使用者給）。

### `calc_pump_power`
泵的液壓功率 `Q·Δp`、軸功率 `Q·Δp/η`（η 為**總效率**，0–1 的小數）、損失；給 `speed` 時另算軸扭矩。輸入：`flow_rate`、`pressure_rise`、`efficiency`、選填 `speed`、`power_unit`（預設 `kW`）。

```json
{"flow_rate": "60 L/min", "pressure_rise": "210 bar", "efficiency": 0.85, "speed": "1500 rpm"}
```
→ `shaft_power = 24.71 kW`、`hydraulic_power = 21.0 kW`、`shaft_torque ≈ 157.3 N·m`。

### `calc_accumulator_precharge`
氣囊／活塞式蓄壓器容量：氣體定律 `p·Vⁿ = 常數`（n = 1 等溫、1.4 絕熱），由要放出的油量與最低／最高工作壓力求氣體容積 V0 與預充壓力。輸入：`usable_volume`、`min_pressure`、`max_pressure`、`precharge_pressure`（空 = 0.9 × 最低壓力）、`exponent`、`pressures_are_gauge`（預設 true）、`volume_unit`。

```json
{"usable_volume": "5 L", "min_pressure": "100 bar", "max_pressure": "200 bar", "pressures_are_gauge": false, "precharge_pressure": "90 bar", "exponent": 1.0}
```
→ `gas_volume = 11.11 L`（V1 = 10 L、V2 = 5 L）。不含溫度變化與標準尺寸選型。

### `calc_cylinder_speed`
單桿油壓缸由流量求伸出／縮回速度 `v = Q/A`、行程時間，以及另一側回油流量（選回油管閥用）。輸入：`bore`、`flow_rate`、`rod`（空 = 無桿）、`stroke`、`speed_unit`（預設 `mm/s`）。

```json
{"bore": "100 mm", "rod": "50 mm", "flow_rate": "60 L/min", "stroke": "500 mm"}
```
→ `extend_speed = 127.3 mm/s`、`retract_speed = 169.8 mm/s`、`extend_time = 3.93 s`、回油 45／80 L/min。不含洩漏、油液壓縮性與加減速。

### `mech_bolt_check`
螺栓受拉檢核：應力面積 `A_s = π/4·(d − 0.9382P)²`、強度等級（`8.8` 等）的降伏強度、保證載重、扭矩↔預緊力 `T = K·F·d`、使用率。`torque` 與 `preload` 擇一給。輸入：`diameter`、`torque`／`preload`、`property_class`（預設 `8.8`）、`pitch`（空 = ISO 粗牙）、`nut_factor`（預設 0.2）、選填 `external_load`、`load_share`、`proof_strength`、`unit_system`。

```json
{"diameter": "10 mm", "torque": "40 N*m"}
```
→ `preload = 20 kN`、`proof_load ≈ 33.4 kN`、`utilization ≈ 0.60`。保證應力未給時以 0.9×降伏強度估計（警告中註明）；不含剪力、疲勞、扭轉應力。粗牙節距表見 [docs/calc-mech/metric-coarse-pitch-data.md](docs/calc-mech/metric-coarse-pitch-data.md)。

### `mech_bearing_life`
滾動軸承基本額定壽命 `L10 = (C/P)^p`（ISO 281 基本公式，球 p = 3、滾子 p = 10/3），換算小時；給 `required_life` 時反算所需 C。輸入：`dynamic_load_rating`、`equivalent_load`、`speed`、`bearing_type`（`ball`／`roller`）、選填 `required_life`。

```json
{"dynamic_load_rating": "30.7 kN", "equivalent_load": "5 kN", "speed": "1500 rpm"}
```
→ `l10_hours ≈ 2572 h`。不含可靠度修正 a1、潤滑修正 a_ISO、當量載重 X·Fr + Y·Fa（需廠商資料）。

### `mech_spring_compression`
圓線壓縮彈簧：彈簧常數 `k = G·d⁴/(8·D³·Na)`、旋繞比、Wahl 係數、剪應力、密合高度（依端部型式）；給 `load` 或 `deflection` 算另一個與應力，給 `free_length` 算到密合的行程並檢查挫屈。輸入：`wire_diameter`、`mean_diameter`、`active_coils`、`shear_modulus`（預設 79.3 GPa）、`end_type`、選填 `free_length`／`load`／`deflection`／`allowable_shear`、`unit_system`。

```json
{"wire_diameter": "2 mm", "mean_diameter": "16 mm", "active_coils": 8, "load": "100 N"}
```
→ `spring_rate = 4.84 N/mm`、`wahl_factor = 1.184`、`shear_stress ≈ 603 MPa`、`solid_height = 20 mm`。僅靜態；不含疲勞與共振。

### `mech_beam_bending`
單跨梁封閉解：簡支／懸臂 × 集中載重／均佈載重，回傳支承反力、最大剪力、最大彎矩、最大撓度、最大彎曲應力。斷面：`rectangle`、`circle`、`tube` 或 `custom`（`second_moment` + `extreme_fiber`）。輸入：`length`、`load`（集中為力，均佈為每長度力）、`support`、`load_type`、`position`、`elastic_modulus`（預設 210 GPa）、斷面尺寸、選填 `allowable_stress`、`unit_system`。

```json
{"length": "4 m", "load": "10 kN/m", "load_type": "udl", "width": "50 mm", "height": "100 mm"}
```
→ `max_bending_stress = 240 MPa`、`max_moment = 20 kN·m`、`max_deflection ≈ 38.1 mm`。線彈性、小變形、不計自重。

### `metrology_fit_limits`
ISO 286 孔軸配合查詢。輸入：`nominal`（公稱尺寸，3–500 mm，如 `"25 mm"`）、`fit`（孔大寫／軸小寫，如 `"H7/g6"`）。IT01–IT18、孔 A–ZC／軸 a–zc，資料表自建於 `eng_toolkit/domains/metrology/data/iso286.json`（來源、授權、涵蓋範圍與已知缺口見 [docs/iso286-data.md](docs/iso286-data.md)）。

```json
{"nominal": "25 mm", "fit": "H7/g6"}
```
→ 孔 `25.000–25.021 mm`、軸 `24.980–24.993 mm`、`min_clearance = 0.007 mm`、`max_clearance = 0.041 mm`、`data.fit_type = "clearance"`；另有上下偏差（µm）、公差、極限尺寸。間隙為「孔尺寸 − 軸尺寸」，負值即干涉（`data.max_interference_mm` 等已換算）。尺寸超出 3–500 mm、未知或格式錯誤的配合代號（`input.unknown_fit_code`）、標準未定義的組合（`input.undefined_class`）都會回明確錯誤。資料表已與兩份獨立公開表逐格比對（見 [docs/iso286-data.md](docs/iso286-data.md)），但標準本身才是權威。

### `metrology_fit_search`
反查 ISO 286 配合：已知公稱尺寸與可接受的間隙範圍，找出符合的標準配合。輸入：`nominal`（3–500 mm）、`min_clearance`／`max_clearance`（間隙窗口，帶單位的有號長度，如 `"-0.03 mm"`；間隙為「孔 − 軸」，負值即干涉；min 大於 max 回 `input.invalid`）、`basis`（`hole` 孔基制，預設；或 `shaft` 軸基制）、`fit_set`（`preferred` 預設，10 個優選配合；或 `common`，孔 H6–H11 搭軸 c d e f g h js k m n p r s t u，軸公差等級與孔相同或細一級，軸基制則對稱）、`max_results`（1–50，預設 10）。配合的最小間隙 ≥ `min_clearance` 且最大間隙 ≤ `max_clearance` 才算符合；標準在該尺寸沒有定義的組合會略過並計入 `data.skipped_undefined`。

```json
{"nominal": "25 mm", "min_clearance": "0 mm", "max_clearance": "0.05 mm"}
```
→ `candidate_count = 2`，`data.candidates` 依序為 `H7/g6`（0.007–0.041 mm）、`H7/h6`（0–0.034 mm）。排序：孔公差加軸公差較大者優先（製造成本的代理指標），其次是配合中心最接近窗口中心者，再依產生順序。這只是成本代理，**不是**配合是否適用的判斷。沒有符合者時仍回 `ok=true`、`candidates` 為空，並在 `data.nearest` 列出最接近的 3 個配合與 `violation_um`（超出窗口的微米數）及警告，方便放寬窗口。

### `metrology_stackup_1d`
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

### `metrology_check_scan`
掃描點雲對 STEP 模型的偏差檢查：回答「零件離模型多遠、在哪裡」。需要選用的 `scan` extra（`uv sync --extra scan`，見上方安裝）。輸入：`scan_path`（`.ply` `.pcd` `.xyz` `.xyzn` `.xyzrgb` `.pts`）、`scan_unit`（點雲座標沒有單位，必須給，且只能是單位本身，如 `"mm"`、`"m"`、`"inch"`、`"um"`；`"2 mm"`、`"0 mm"` 這類帶數字的寫法會回 `input.invalid`，免得悄悄縮放掃描）、`model_path`（`.step`／`.stp`，以 mm 讀入）、`tolerance`（正的長度，如 `"0.1 mm"`）、`registration`（`none` 預設，點雲已在模型座標系；`icp`，先以點對面 ICP 從單位矩陣精修，點雲必須已大致對齊）、`icp_max_distance`（預設 `"5 mm"`）、`mesh_deflection`（STEP 離散化的弦高誤差上限，預設 `"0.01 mm"`）、`max_points`（1000 到 5000000，預設 200000；超過就用固定種子降取樣，結果可重現）、`output_unit`（預設 `mm`）。

```json
{"scan_path": "C:/scans/bracket.ply", "scan_unit": "mm", "model_path": "C:/cad/bracket.step",
 "tolerance": "0.1 mm", "registration": "icp"}
```
→ 主值 `rms_deviation`，另有 `mean_deviation`、`std_deviation`（母體標準差）、`max_deviation`（絕對值最大）、`max_positive_deviation`、`max_negative_deviation`（有號最小值）、`p95_abs_deviation`、`out_of_tolerance_fraction`（|偏差| > `tolerance` 的比例）、`points_evaluated`。`data` 含 `histogram`（絕對偏差 20 格）、`worst_points`（前 10 大偏差的座標）、`regions`（超差點以 DBSCAN 分群；先把座標相同（四捨五入到 1e-6 mm）的點併成一個位置，`eps` 為整份使用點雲（去重後）最近鄰距離中位數的 5 倍，所以重複記錄的點不影響分區；每區有相異位置數、重心、外框、最大偏差，依位置數排序，最多 20 區）、`registration`（4×4 變換、`fitness`、`inlier_rmse`、估計變換用的點數 `source_points`、`unconstrained` 未被約束的方向、`note`）、`mesh`（三角形數、`deflection_mm`、是否封閉、`solids` 實體數、`step_roots` STEP 根實體數與實際轉出的數量）與 `points`（原始與實際使用點數）；每個超差點寫入 `deviations.csv` 工作區產物（欄位 `x_mm,y_mm,z_mm,deviation_mm,region`，最多 100000 列，超過會警告）。

偏差是掃描點到模型表面的**有號距離，在材料外為正**。模型不是剛好一個封閉實體時內外無定義或有歧義：開放殼面，或 STEP 內有多個實體（重疊的實體會讓射線奇偶判斷出錯），都改用到最近表面的無號距離，`data.signed = false` 並警告，有號的最大／最小值不回傳。STEP 有根實體沒轉成幾何時（`step_roots.transferred` 小於 `in_file`）也會警告模型不完整。量測對象是離散化後的網格，誤差上限即 `mesh_deflection`，小於它的偏差沒有意義。到網格的距離以 float64 計算：Open3D 在平移到網格中心後的 float32 座標只負責找出最近三角形，再對它與共用頂點的所有鄰接三角形重算精確距離（測試中誤差低於 1e-9 mm；退化網格仍可能選錯三角形）。ICP 只在初始對齊夠近時收斂；`fitness` 是配準後與模型表面距離不超過 `icp_max_distance` 的點的比例（與 `inlier_rmse` 同一批點），低於 0.5 會警告；估計變換最多用 200000 個點（固定種子子集），再套用到全部點；相同輸入重跑，ICP 結果可能在 1e-5 mm 量級有差異（多執行緒浮點加總）。**ICP 後的偏差描述的是形狀，不是位置**：最佳擬合會吸收真實缺陷（例如頂面 28% 範圍凸起 0.2 mm，不配準時 22.2% 超差，配準後只剩 2.7%），所以一律警告；已配準的點若只在單一平面或圓柱上，平移或旋轉有未約束的方向（單一平面無法固定面內平移與繞法向的旋轉），會在警告與 `registration.unconstrained` 具名列出。全域配準（未對齊的掃描）不在範圍內。ASCII／二進位 PLY 會對照標頭驗證資料是否完整（被截斷的檔案回 `input.invalid`）；座標超過 1e7 mm 或非有限值一律視為壞檔。掃描品質與適用標準由使用者負責。

### `metrology_form_error`
由量測點算形狀誤差：`feature` 為 `flatness`（平面度，x,y,z 點）、`straightness`（真直度，平面內輪廓的 x,y 點）、`circularity`（真圓度，截面的 x,y 點；給 x,y,z 時先投影到最佳擬合平面）或 `cylindricity`（圓柱度，x,y,z 點）。點以 `points` 內嵌（JSON `[[x,y,z],...]` 或每行／以 `;` 分隔一點的文字）或 `points_path` 指到 `.csv` `.txt` `.xyz` `.pts` `.asc` `.dat`（開檔一次，標頭行會略過，標頭有 x、y、z 時依名稱取欄）；座標沒有單位，`unit` 必須給且只能是單位本身（`"mm"`、`"um"`、`"inch"`）。選配 `tolerance` 回傳合格與否，`output_unit` 指定結果單位。

```json
{"feature": "circularity", "points": "10.1,0; 4.95,-8.57; -5.05,8.75; -9.9,0; -4.95,-8.57; 5.05,8.75", "unit": "mm"}
```
→ 主值 `form_error` 是**最小區域**的寬度（包住所有點的最薄一對平行線／平行平面／同心圓／同軸圓柱），另附最小平方參考的對照值 `least_squares_form_error` 與區域幾何（`data.zone`）。`data.method` 說明用了哪個估計法、`exact` 是否證明為最小：直真度是精確解；平面度與圓度在 24／32 點以內窮舉所有可能的 4 點組合（全域最小），更多點時以多起點加候選枚舉收斂並檢查最佳性條件（`first_order_optimal`），但不證明全域最小；圓柱度是軸向搜尋，結果一定是真能包住所有點的區域，故為最小區域的上界，不宣稱最小。因此合格判定恆有效，在非精確方法下不合格不一定是真不合格（`tolerance_check.conclusive`）。點數太少（平面度需 4 個相異點、真直度 3、圓度 4、圓柱度 6）回 `input.too_few_points`，共線或共面的退化點集回 `geometry.degenerate`，NaN／無限大、欄數不符回 `input.invalid`（指出第幾列）。數值的解析度（座標最後一位）與取樣不足的低估都會警告。演算法、精度與限制見 [docs/metrology-gdt/design.md](docs/metrology-gdt/design.md)。

### `metrology_position_tolerance`
位置度：由名目與量測座標算每個特徵的位置度並含 MMC／LMC 補償公差。輸入：`nominal`、`measured`（每個特徵一列，x,y 或 x,y,z，格式同上）、`tolerance`（位置度公差帶直徑，如 `"0.1 mm"`）、`unit`（座標與 `feature_size` 的單位）、`bonus_modifier`（`none` 預設、`M`、`L`）、`feature_kind`（`hole` 孔：MMC 是最小孔；`shaft` 軸：MMC 是最大軸）、`feature_size`（每個特徵的實際尺寸）、`mmc_size`／`lmc_size`。

```json
{"nominal": "10,20", "measured": "10.045,20.06", "tolerance": "0.1 mm", "bonus_modifier": "M",
 "feature_kind": "hole", "feature_size": "10.06", "mmc_size": "10 mm"}
```
→ 偏差 (0.045, 0.06)，所需公差帶 `position_error = 2·√(dx²+dy²) = 0.15 mm`（x,y,z 時為球形 SØ），補償 `bonus = 實際尺寸 − MMC = 0.06 mm`，容許 `0.16 mm`，合格；`data.features` 逐特徵列出。實際尺寸超出材料極限時補償為 0 並警告。基準位移（datum shift）、基準修飾、複合公差帶與突出公差帶不在範圍內，量測座標須已在名目的基準座標系中。

### `geometry_measure`
3D CAD 模型的精確量測：回答「這個零件多大、多重、有哪些面和孔、這兩個特徵相距多遠」。需要選用的 `cad` extra（`uv sync --extra cad`，見上方安裝）。數字是檔案 B-rep 的精確性質（OpenCASCADE），不是離散化網格的近似。輸入：`model_path`（`.step` `.stp` `.iges` `.igs` `.brep`；`.stl` 回 `input.unsupported_format`，網格檔留待之後的版本）、`queries`（可省略；以 `;` 分隔、最多 20 個）、`density`（可省略，帶單位，如 `"2700 kg/m^3"`，會多回傳 `mass`；裸數字回 `units.bare_number`，非密度的單位回 `units.dimension_mismatch`）、`inventory_limit`（0 到 500，預設 50：`data` 內列出的面與孔的數量；`inventory.json` 產物永遠列出全部）、`output_unit`（預設 `mm`）。

查詢語法：`distance A B`（兩個實體實際表面之間的最短距離：面是被邊界修剪後的那一片，不是它延伸出去的無限大平面，所以點在孔正上方 30 mm、孔半徑 5 mm 時，到頂面是 30.414 mm 而不是 30）、`angle A B`（角度，單位 degree）、`diameter A`、`radius A`、`area A`、`thickness P(x, y, z) <軸>`（從該點沿軸射出一條精確射線，取前兩個交點的距離，即壁厚）。`A`／`B` 是 `F<n>`（面）、`H<n>`（孔）或 `P(10 mm, 0 mm, 5 mm)`（點，座標必須帶單位，裸數字回 `units.bare_number`）；軸是 `+x -x +y -y +z -z` 其中之一。孔的距離與面積以孔壁的圓柱面計（直徑 10 mm、軸線離平行面 25 mm 的孔，到該面是 20 mm，不是 25）；`diameter`／`radius` 只適用圓柱面與孔，其他回 `geometry.unsupported_query`。`angle` 兩個平面時取外法向量夾角（0 到 180 度）；只要其中一個是圓柱面或孔，就取軸線的夾角（軸線沒有方向，0 到 90 度）。引用不存在的 `F999` 回 `geometry.unknown_entity`，`details` 帶 `ref`、`faces`、`holes`（模型實際有幾個面和孔）與 `query_index`；語法錯誤回 `input.invalid`，`details.query_index`（從 0 起算）與 `details.query` 指出是哪一個。壁厚的射線穿不到兩個交點（例如正好穿過孔）時不是錯誤：該 `q` 不出現在 `values`，`data.queries[i].found` 為 `false`，並加一則警告。

```json
{"model_path": "C:/cad/bracket.step", "density": "2700 kg/m^3",
 "queries": "distance F1 H1; angle F1 F2; diameter H1; thickness P(10 mm, 10 mm, 30 mm) -z"}
```
→ 主值 `volume`（單位為 `output_unit` 的立方，如 `millimeter ** 3`），另有 `surface_area`、`size_x`／`size_y`／`size_z`（外框尺寸）、`mass`（有給 `density` 時）與依查詢順序的 `q1`、`q2`、…（長度用 `output_unit`，角度用 `degree`，面積用 `output_unit` 的平方）。`data` 含 `solids`、`faces_by_kind`、`holes`（孔：`id` 為 `H1`…、`face_ids`、`diameter_mm`、`axis` 鑽入方向（指向材料內）、`entry_mm`、`depth_mm` 圓柱段深度（不含鑽頭錐底）、`thru` 是否貫穿、`counterbore`／`countersink` 恆為 `null`）、`faces`（`id`、`kind`、`area_mm2`、`sense` 平面／凸／凹／混合、法向量或軸線、外框；`faces`／`holes` 受 `inventory_limit` 限制）、`centroid_mm`、`bbox_min_mm`／`bbox_max_mm`（精確外框）、`step_roots`（檔內根實體數與實際轉出的數量；IGES 與 BREP 也用這個名稱）、`queries`（每個查詢的解析結果與它解析到的實體）、`face_ids_valid_for`（輸入檔的 sha256）、`conventions` 與 `coverage`。

孔的辨識規則：孔是**凹**的圓柱面，且（可能由兩個半面拼成）涵蓋整圈；圓角是局部圓柱而且是凸的，不算孔。貫穿與否（`thru`）用精確射線判斷：從圓柱段兩端各往外一點點、略偏離軸線的幾條沿軸向射線，若都沒碰到任何東西，那一端就通到零件外面；兩端都通是貫穿孔，只有一端通是有底的盲孔（偏離軸線，錐形鑽頭底才不會擋住射線）。貫穿孔沒有自己的入口，所以從其主軸的正向端鑽入（`axis` 的主要分量為負）。包住凸台的環形槽外壁不算孔。**不偵測**沉孔、錐孔與螺紋：沉孔會被列成貫穿孔旁邊另一個盲孔，`data.coverage` 會明講這些限制。

面編號（`F<n>`）是 OpenCASCADE 走訪面的順序，孔編號依軸向、直徑、入口排序：兩者只對**同一個檔案的位元組與同一版 OpenCASCADE** 有效，`face_ids_valid_for` 給出該檔的 sha256，重新匯出的檔案要重新取得清單。先不帶 `queries` 呼叫一次讀 `data.faces`／`data.holes`，再下查詢。單位：STEP 與 IGES 由 OpenCASCADE 依檔內單位轉成 mm，BREP 沒有單位，數字一律當 mm。IGES 只有在輸出端寫成實體（B-rep solid）時才有體積；只有曲面的 IGES 沒有封閉實體，`volume` 不出現，`surface_area` 成為主值並警告。曲面 IGES 還可能把圓柱存成旋轉曲面，列為 `kind: other` 而不是圓柱，所以**偵測不到孔**（Open CASCADE 自己的 IGES 曲面輸出就是如此，其他 CAD 的輸出各不相同）；需要體積或孔資訊時請用 STEP，或把 IGES 輸出成實體。檔案沒有封閉實體（殼面、散面）時同樣如此，且 `density` 會被忽略並警告；多個實體時體積與面積是各實體的總和，重疊的實體會重複計算。這些結果沒有經過工程核可，量測的前提（單位、檔案是否忠於設計）由使用者負責。

### `geometry_make_part`
由宣告式 JSON 規格建出零件：輸出 STEP（可加 GLB／STL／BREP）、精確的體積、表面積、外框尺寸與選配的質量。**伺服器不執行任何程式碼**（沒有 `make_from_code`）：想用 build123d 的代理在自己的 shell 裡跑，再把 STEP 交給 `geometry_measure` 等工具。需要選用的 `cad` extra（`uv sync --extra cad`）。輸入：`spec`（JSON 字串，上限 64 kB）、`name`（產品名，也是輸出檔名主幹，`[A-Za-z0-9_-]{1,64}`，預設 `part`）、`formats`（逗號分隔的 `glb`、`stl`、`brep`；STEP 一律輸出，因為它是讀回驗證的依據）、`mesh_deflection`（GLB／STL 離散化的弦高誤差，0.001 到 100 mm，預設 `"0.01 mm"`）、`density`（可省略，帶單位，會多回傳 `mass`）、`output_unit`（預設 `mm`）。

**規格語法。** 規格是 `{"params": {...}, "features": [...]}`。每個數字都是帶單位的字串（`"25 mm"`、`"90 deg"`、`"1.5 inch"`；裸數字回 `units.bare_number`，長度欄位給質量回 `units.dimension_mismatch`）。`params` 是 `名稱: 運算式`；任何欄位都可以用 `$名稱` 引用**較前面**的 param（引用後面的或自己，即前向引用與循環，回 `input.invalid`），運算式交給 `units.parse_quantity`（pint 的解析器，不是 `eval`），例如 `"($L - 2 mm) / 2"`。注意 pint 把「數字 空格 單位」當隱含乘法：`$L / 2 mm` 是（$L ÷ 2）× mm，是面積，不是長度；除以長度要加括號 `($L) / (2 mm)`（結果無因次），一般只除以純數字。features 依序作用在**同一個實體**上，第一個必須是 `mode` 為 `"add"` 的實體（body）；每個 feature 有 `op`、選填的 `name`（預設 `<op><序號>`）與該 op 的欄位。

運算式護欄（`units.parse_quantity` 本身不受影響，其他工具照舊）：實測未加護欄時 pint 對 `9**9**9 mm` 會算一個三億多位數的整數、10 秒內不返回（`9**9**9**9 mm` 同樣；`10**1000000 mm` 約 0.14 秒後才溢位出錯）。因此 Resolver 在交給 pint 之前拒絕：超過 200 字元、括號巢狀超過 8 層、任何數字絕對值超過 1e9、`**` 或 `^` 後面不是 6 以下的一般數字或接著連乘（`2 ** 2 ** 2`），一律回 `input.invalid`（`details.pointer` 指出欄位）。`(100 mm) / 4 + 2 mm`、`2 * (10 mm + 5 mm)` 照常運作。

| op | 角色 | 欄位（`*` 必填） |
|---|---|---|
| `box` | body | `size`* `[x, y, z]`、`at`（最小角，預設原點）、`mode` |
| `cylinder` | body | `diameter`*、`height`*、`at`（底面圓心，預設原點）、`axis`（預設 `"+z"`）、`mode` |
| `sphere` | body | `diameter`*、`at`（球心，預設原點）、`mode` |
| `cone` | body | `diameter`*（底面）、`top_diameter`*（頂面，可為 `"0 mm"` 成為尖錐；與 `diameter` 相等就是圓柱）、`height`*、`at`（底面圓心，預設原點）、`axis`（預設 `"+z"`）、`mode` |
| `extrude` | body | `plane`（`"xy"` 預設、`"xz"`、`"yz"`）、`offset`（沿平面法向移動，預設 `0 mm`）、`profile`*、`distance`*（沿法向，負值反向）、`mode` |
| `revolve` | body | `plane`、`offset`、`profile`*、`axis`（平面的 `"u"` 或 `"v"` 軸，過平面原點，預設 `"v"`）、`angle`（預設 `360 deg`）、`mode` |
| `hole` | 修改 | `at`*（入口點清單 `[[x, y, z], ...]`）、`axis`（鑽入方向，預設 `"-z"`）、`diameter`*、`depth`（長度或 `"thru"`，預設 `"thru"`；從入口算起）、`counterbore` `{diameter, depth}`、`countersink` `{diameter, angle}`（含角）、`thread`、`fit`（後兩者只是標註文字，不產生螺紋或公差幾何） |
| `fillet` | 修改 | `edges`*（邊選擇器）、`radius`* |
| `chamfer` | 修改 | `edges`*、`distance`*（對稱倒角） |

`mode` 是 `"add"`（預設，聯集）、`"cut"` 或 `"intersect"`；`axis` 是 `"+x" "-x" "+y" "-y" "+z" "-z"` 之一。平面 `plane` 的草圖座標 (u, v) 為 (x, y)、(x, z)、(y, z)，法向 +z、+y、+x。輪廓 `profile` 是恰有一個鍵的物件：`{"rect": {"size": [u, v], "at": [u, v], "centered": false}}`（`at` 是最小角，`centered` 為 true 時是中心）、`{"circle": {"diameter", "at"}}`、`{"polygon": {"points": [[u, v], ...]}}`（至少 3 點，不可自交）、`{"slot": {"length", "width", "at", "angle"}}`（`length` 含兩端圓頭，須大於 `width`）。`revolve` 的輪廓必須在旋轉軸的同一側。孔從 `at` 的入口點沿 `axis` 鑽 `depth`（`"thru"` 為穿過整個零件；盲孔深過零件就是穿透）；整個 feature 沒切掉任何材料時回警告（多半是入口點或方向弄反了）。

**限制與邊界行為**（都有測試與獨立驗證）：
* 一份規格所有 `hole` 的孔位合計最多 **1000** 個；超過立刻回 `input.invalid`，`details.limit` 是 1000。1000 個孔在開發機上約 25 秒，其中約 9 秒是把 STEP 讀回驗證，250 個約 3 秒，時間隨孔數成長得比線性快。
* 最終結果必須是**剛好一個有效實體**，否則回 `geometry.invalid_result`（訊息會說「join loose bodies or remove them」）。執行途中可以暫時有多個分開的實體：例如先對兩個分開的方塊倒圓角或鑽孔，再用第三個方塊把它們接起來是合法的，邊選擇器（如 `>x`、`convex`）會作用在目前所有實體上。
* 圓角或倒角的大小容不下時（例如 10 mm 方塊全部邊倒 5 mm 或更大，或比板厚還大的圓角）回 `geometry.build_failed`，`details` 指出是第幾個 feature；剛好小於一半（如 4.9 mm）則照常做出有效實體。半徑或距離為 0 或負數回 `input.invalid`。
* 切到什麼都不剩、或與不相交的實體取交集，回 `geometry.build_failed` 並指出該 feature。

**邊選擇器**是以 `&` 相交的詞：`all`；`|x` `|y` `|z`（平行該軸的直線邊）；`>x` `<x` `>y` `<y` `>z` `<z`（位於零件外框該方向極值面上的邊）；`convex`（外角）與 `concave`（內角），由相鄰兩面的二面角判斷，平滑相接（例如圓柱的接縫線，兩側是同一個面）兩者都不是，所以 `|z & convex` 只會選到外緣的四條豎邊，不會選到孔的接縫線；`line`（直線邊）與 `circle`（圓邊）。沒有任何邊符合時回 `geometry.empty_selection`，`details.matches_per_term` 列出每個詞各單獨符合幾條，方便修正選擇器。

```json
{"params":{"L":"100 mm","W":"50 mm","T":"20 mm","d":"10 mm"},"features":[{"op":"box","name":"body","size":["$L","$W","$T"]},{"op":"hole","name":"bore","at":[["$L / 4","$W / 2","$T"]],"axis":"-z","diameter":"$d","depth":"thru","fit":"H7"},{"op":"fillet","name":"corners","edges":"|z & convex","radius":"2 mm"}]}
```
→ 主值 `volume = 98360.531085 mm³`（手算 100·50·20 − π·5²·20 − 4·(2² − π·2²/4)·20），`surface_area = 16395.699 mm²`，`size_x/y/z = 100/50/20 mm`，給 `density` 時另有 `mass`。用 `geometry_measure` 讀產出的 STEP：1 個貫穿孔、直徑 10 mm、入口 (25, 25, 20)。

`data`：`features`（每個 feature 的 `index`（從 1 起）、`name`、`op`、`volume_after_mm3`、`edges_used`）、`holes`（宣告的孔，已換成 mm：入口、鑽入方向、直徑、深度（`null` 為貫穿）、沉孔、錐孔、`thread`、`fit`；`data` 內最多列 50 個，`holes_total` 是總數，完整清單在 `features.json`）、`params`（解析後的 param，長度為 mm、角度為 rad）、`bbox`、`solids`、`faces`、`readback`（把寫出的 STEP 讀回，比對體積：`volume_written_mm3`、`volume_read_mm3`、`relative_difference`；超過 1e-6 回 `geometry.readback_mismatch`）、`spec_sha256` 與 `step_sha256`。產物：`<name>.step`、`<name>.features.json`、`spec.json`，以及要求的 `<name>.glb`（公尺、+Y 向上）、`<name>.stl`（二進位，毫米）、`<name>.brep`。`spec.json` 是**正規化後的規格**（預設值補齊、運算式與單位都解成 mm 與 rad、鍵排序、無多餘空白），它本身就是合法規格，其 sha256 就是 `spec_sha256`：空白或鍵順序不同的同一份規格得到相同的雜湊。`features.json` 是宣告的設計意圖（孔位置、直徑、深度、沉孔、螺紋與配合標註），內含 STEP 的 sha256，之後的工程圖工具只有在 STEP 雜湊相符時才會印出 `Ø10 H7 THRU` 這類標註。

**確定性。** 同一份規格得到位元組完全相同的 STEP（XCAF 寫出器、固定的 `FILE_NAME` 標頭與時間戳、產品名取自 `name`；同一行程內與跨行程都測試過，也不受 `PYTHONHASHSEED` 影響）。布林運算以單執行緒進行，合併共面的面。

**錯誤。** 規格錯誤是 `input.invalid`，`details.pointer` 是 JSON pointer（如 `/features/2/diameter`），`details.allowed` 是該處允許的欄位或值；單位錯誤沿用 `units.bare_number`、`units.dimension_mismatch`、`units.parse_error`，`details` 同樣帶 `pointer`。核心建模失敗時 `error.details` 帶 `feature_index`（從 1 起）、`feature_name`、`op` 與 `pointer`：`geometry.build_failed`（核心失敗，如不可能的圓角半徑；`details.occt` 是核心訊息）、`geometry.empty_selection`；最後結果必須恰好是一個通過 BRepCheck 的實體，否則回 `geometry.invalid_result`（例如兩個互不相連的實體）。失敗的結果不帶警告，也不寫出零件檔。

限制：規格 64 kB、200 個 feature、全規格孔位置共 1000、運算式 200 字元。v1 沒有螺紋幾何、放樣、掃掠、陣列、鏡射、薄殼、傾斜拉伸與匯入既有 STEP；結果同樣是未經工程核可的模型。

### `geometry_check_dfm`
對實體 3D 模型做可製造性（DFM）檢查：`process` 目前只支援 `cnc_milling` 與 `sheet_metal`，其他製程（`turning`、`injection_molding`、`additive`…）回 `geometry.unsupported_process`，不猜。需要選用的 `cad` extra。輸入：`model_path`、`process`、`limits`（可省略的 JSON 物件，覆蓋預設限值，長度要帶單位，例如 `{"min_wall": "1 mm", "tool_radius": "2 mm"}`）、`tool_axis`（銑削主軸方向，預設 `+z`）、`max_findings`、`output_unit`。每個發現（finding）都有規則代號、嚴重度（`error`／`warn`／`info`）、實測值與限值、面／邊編號、模型座標中的一個點，以及 `check`：`exact`（精確的 B-rep 量，如半徑、平面間距、精確距離）或 `heuristic`（取樣或把形狀解讀成特徵，兩個方向都可能錯）。結果另有依嚴重度的計數 `data.summary` 與 `data.not_performed`（沒做的檢查）。**預設限值是本工具箱自己的經驗值，不是任何標準的數值，也不宣稱符合任何標準**，請以 `limits` 帶入貴廠的值；沒有閉合實體（只有殼或曲面）的檔案會被拒絕。規則：`cnc.internal_corner_radius`、`cnc.min_wall`、`cnc.hole_depth_ratio`、`cnc.min_hole_diameter`、`cnc.tool_access`（heuristic）、`sheet.thickness`、`sheet.thickness_uniform`（heuristic）、`sheet.min_bend_radius`、`sheet.hole_to_edge`。規則、做法、實測時間與未做項目見 [`docs/geometry-dfm/design.md`](docs/geometry-dfm/design.md)。

```json
{"model_path": "C:/cad/bracket.step", "process": "cnc_milling", "limits": "{\"tool_radius\": \"3 mm\", \"min_wall\": \"1 mm\"}"}
```
→ 主值 `errors`（error 的個數），另有 `warnings`、`info`，以及有量到時的 `smallest_wall`（`sheet_metal` 為 `thickness`）。四個尖銳內角都回 `measured = 0 mm`、`limit = 3 mm`，位置在各內角邊的中點；R0.5 的內圓角回 `measured = 0.5 mm`；Ø2、深 30 的孔回 `measured = 15`（深徑比）。

### `geometry_convert`
轉換 3D 模型檔並列出轉換損失。輸入 `.step` `.stp` `.iges` `.igs` `.brep`（以公釐讀取）或 `.stl`（二進位或 ASCII，數字視為公釐）；`output_format` 為 `step`、`brep`（精確，只能由 STEP/IGES/BREP 轉出）、`stl`（二進位、公釐）或 `glb`（公尺、+Y 向上，轉換已套用：模型 (x, y, z) mm 變成檔內 (x, z, −y)/1000）。STL 只能轉成 `stl` 或 `glb`：網格不是實體，不會被轉成 STEP/BREP（回 `geometry.needs_brep`）。B-rep 轉網格時以 `mesh_deflection`（預設 `0.01 mm`，0.001 到 100 mm）細分曲面；輸出檔寫在工作區的工作目錄並附 sha256，輸入檔不會被改動。STEP/IGES/BREP 需要選用的 `cad` extra；STL 轉 STL/GLB 不需要任何 engine。輸入（全為字串）：`input_path`、`output_format`、`mesh_deflection`、`name`（輸出檔名與 STEP 產品名，空白 = 輸入檔名）。

```json
{"input_path": "C:/cad/part.step", "output_format": "stl", "mesh_deflection": "0.01 mm"}
```
→ 主值 `volume_out`，另有 `volume_in`、`volume_relative_change`、`surface_area_in/out`，網格輸出另有 `triangle_count`；網格的數字是從寫出的檔案讀回來的。`data.losses` 逐項說明損失：精確曲面變成三角形（弦高誤差以 `mesh_deflection` 為上限）、單位換算（例如 STEP 宣告 `inch` 已換成 mm）、GLB 的軸與單位、32 位元浮點精度、名稱／顏色／組合結構不會帶過去。Ø20 × 30 的圓柱在 0.01 mm 下 STL 體積 9418.578 mm³，比真值 9424.778 少 6.2 mm³，不超過 2π·10·0.01·30 = 18.85 mm³。沒有封閉實體的檔案（殼、開放網格）沒有體積，主值改為 `surface_area_out`；座標超過 1 km 或非有限值回 `input.out_of_range`。設計與未驗證項目見 [docs/geometry-convert/design.md](docs/geometry-convert/design.md)。

### `drawing_make_views`
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

**確定性。** 同一模型與參數得到位元組完全相同的 DXF、PDF 與 SVG，跨行程（任何 `PYTHONHASHSEED`）也一樣；PNG 同一台機器相同、跨機器不保證（見上）：測試以 1 到 12 的種子與未設定各跑一次整個工具（四種格式），比對 sha256。ezdxf 寫 CLASSES 區段的順序會隨 `PYTHONHASHSEED` 改變，工具在存檔前把它排序（細節與量測在 [docs/geometry-drawing/drawing-determinism.md](docs/geometry-drawing/drawing-determinism.md)）。PDF 由本工具箱自己的後端寫出（沒有日期、產生者字串，不壓縮，物件順序固定）；**PDF 沒有文字圖層**：文字是字形輪廓，無法搜尋或複製，DXF 才有文字。中文標題會使用機器上第一個有該字形的 CJK 字型（`conventions.FALLBACK_FONTS`）；沒有任何字型有的字元，會在警告中逐字列出，PDF 上顯示字型的缺字框。字型不同的機器，同一張圖的 PDF 位元組會不同。

**範圍與限制（`data.coverage`）。** 標外形尺寸、孔標註與孔位座標尺寸；沒有中心線、公差、表面粗糙度，沉頭孔與埋頭孔不辨識、斜面上的特徵不標。外形尺寸來自外框，孔的直徑、數量、位置來自幾何，配合與螺紋只來自雜湊相符的 `features.json`。標題欄一律印 `UNREVIEWED - generated by eng-toolkit, not engineering approved`。這不是完整的製造用圖，必須對照模型複核。

### `drawing_read`
讀別人做的圖：`.dxf`（R12 到 R2018、文字或二進位、舊式編碼與 CJK）與 `.pdf`，回傳結構化內容。需要選用的 `drawing` extra（`uv sync --extra drawing`：ezdxf 與 pdfminer.six，都是 MIT）。輸入（字串、整數）：`path`、`detail`（`summary` 預設，或 `full`：每個項目多帶欄位，不是多帶項目）、`max_items`（每個清單回傳幾筆，1 到 500，預設 25）。`.dwg` 不讀（回 `input.unsupported_format`，請先用自己安裝的 `dwg2dxf` 轉成 DXF）。

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

**確定性。** 同一檔案得到同一結果（`inputs_hash`、`input_files` 的 sha256 記在 `provenance`）。**範圍與限制：** `data.extents.model` 與 `paper` 各有兩個外框：`geometry`（只含幾何實體：LINE、ARC、CIRCLE、多段線、SPLINE、區塊裡的幾何等；不含文字、尺寸、引線、標註，區塊內的也不含）與 `with_annotations`（再加上文字插入點與尺寸的定義點，也就是尺寸線與文字擺的位置）。例：100 × 60 的零件上方 25 mm 處有一個尺寸與一行文字，`geometry` 是 100 × 60，`with_annotations` 是 100 × 85；摘要與 `values.extent_width`／`extent_height` 用 `geometry`，摘要括號裡另附含標註的大小。只有文字與尺寸、沒有幾何時 `geometry` 是 `null`。舊版的單一 `min`／`max`／`size`／`size_mm` 已移除，因為它們把標註算進零件大小。外框是點的外框（弧與圓每 15 度取樣再加軸向極值，不含填充圖案、圖片、視埠）；圖層長度只計 LINE、ARC、CIRCLE、ELLIPSE、LWPOLYLINE、2D POLYLINE；尺寸文字的公制以外格式（分數、建築）不自行計算，只回塊內文字；不讀 DWG、不做 OCR、不解析 GD&T；標題欄與視圖數是推測，已標註。小模型可用性檢查（Claude Haiku）的結果在 [docs/geometry-drawing/drawing-read.md](docs/geometry-drawing/drawing-read.md)。

### `drawing_convert`
把 `.dxf`（R12 到 R2018）轉成 `pdf`、`svg`、`png`，或改寫成另一個 DXF 版本（`R12` `R2000` `R2004` `R2007` `R2010` `R2013` `R2018`）。只轉模型空間，**真實比例**：單位取自 `$INSUNITS`（沒寫單位就當 mm 並警告），1:1 時 100 mm 的線在 PDF 裡是 283.465 pt。需要選用的 `drawing` extra（`uv sync --extra drawing`）；PNG 另需 `png` extra。輸入（全為字串）：`input_path`、`output_format`、`sheet`（`auto`＝圖面範圍外各加 10 mm，或 `A4` 到 `A0`）、`scale`（`auto` 或 ISO 5455 比例，`1:1` 到 `1:10000`、`2:1` 到 `50:1`；圖紙放不下會拒絕而不是裁切）、`dxf_version`（只對 DXF 輸出有效）。

```
drawing_convert(input_path=".../flange.dxf", output_format="pdf", sheet="A3", scale="1:1")
```
→ 輸出 `converted.<格式>` 在工作目錄（原檔不動，附 sha256）；`data.losses.items` 列出目標格式放不下或沒畫出的東西（顏色一律黑、字型被取代、PDF/SVG/PNG 的文字沒有文字層、MTEXT 格式、ACIS 實體與代理物件、被關閉或凍結的圖層、圖紙空間、外部參考；DXF 輸出則是舊版本沒有的實體類型與屬性，例如 R12 沒有 LWPOLYLINE、線寬、真彩色、`$INSUNITS`，是寫出檔案再讀回來數出來的），`data.entities` 依類型數出畫了與跳過的實體數。`.dwg` 回 `engine.unavailable`（需要 `dwg2dxf`，列為 S9，尚未提供）。檔案不受信任：在子行程裡轉換（預設 60 秒、2048 MB；`ENG_TOOLKIT_CONVERT_TIMEOUT_S`、`ENG_TOOLKIT_CONVERT_MEMORY_MB`、`ENG_TOOLKIT_CONVERT_MAX_MB` 可調），超過 20 萬個實體、100 萬單位工作量（區塊展開後）、區塊自我參照或巢狀超過 16 層、座標超過 1e12 mm、頁面超過 5080 mm 的檔案會被拒絕或截掉並說明原因，NaN 與無限大座標的實體會被移除並列出。詳見 `docs/drawing-convert/design.md`。

---

## 輸入規則：每個物理量都要帶單位

接受 `"100 mm"`、`"210 bar"`、`"40 cP"`（MCP schema 為單一字串）；也接受 JSON 字串 `'{"value": 100, "unit": "mm"}'`。

| 輸入 | 結果 | `error.family` |
|---|---|---|
| `100`、`"100"` | 拒收 | `units.bare_number` |
| `"100 mm"` 填在壓力欄 | 拒收 | `units.dimension_mismatch` |
| `"abc"`、`"mm"` | 拒收 | `units.parse_error` |
| `"0 mm"`、桿徑 ≥ 缸徑 | 拒收 | `input.invalid` |

錯誤仍以完整契約回傳（`ok: false` + `error`），不是 MCP 協定錯誤，代理可以讀 `error.details.param` 自行修正後重試。

---

## 回傳契約

每個工具、每個領域都回傳同一個形狀（`eng_toolkit/contract.py`）：

```json
{
  "ok": true,
  "value":  {"quantity": 164.93, "unit": "kilonewton", "uncertainty": null},
  "values": {"push_force": {...}, "pull_force": {...}},
  "data":   {"formula": "...", "job_id": "...", "job_dir": "..."},
  "artifacts": [{"path": "ws/<job-id>/inputs.json", "format": "json", "sha256": "..."}],
  "provenance": {"engine": "eng_toolkit.calc.hydraulics", "version": "0.0.1",
                 "inputs_hash": "sha256:...", "input_files": [], "engine_versions": {}},
  "warnings": ["Ideal theoretical force: ..."],
  "error": null,
  "review_status": "unreviewed"
}
```

* `value` 是主要結果；`values` 含所有命名結果，多輸出工具因此不必跳出契約。
* `uncertainty` 與 `quantity` 同單位；`null` 表示未估算（封閉公式）。壓降工具給的是名目區間（層流 ±5%、紊流 ±10%、過渡 ±20%），不是校準值。
* `provenance.inputs_hash` 是輸入的 canonical JSON 之 SHA-256：相同輸入 → 相同雜湊，可重現。
* `provenance.input_files` 列出這次呼叫讀過的檔案（`path` 絕對路徑、`bytes`、`sha256`，依讀取順序）；`provenance.engine_versions` 是用到的引擎在環境中的版本（如 `cadquery-ocp-novtk`）。不讀檔的工具兩者皆為空。失敗的呼叫也帶有失敗前已讀過的檔案。`metrology_check_scan` 原本放在 `data.input_files` 的資料搬到這裡。
* `review_status` 是 pydantic `Literal["unreviewed"]`，無法被設成別的值。

## 工作區

每次呼叫在 `ws/<job-id>/` 落檔（`ENG_TOOLKIT_WS` 環境變數可改根目錄）：
`inputs.json`（原始輸入）、`result.json`（回傳內容）、`manifest.json`（每個檔案的 sha256）。`ws/` 已列入 `.gitignore`。

---

## 授權政策

本專案：**Apache-2.0**。依賴分級（`eng_toolkit/licenses.py`，`toolkit_doctor` 會實際核對）：

| 分級 | 授權 | 規則 |
|---|---|---|
| `permissive` | Apache / MIT / BSD / ISC / PSF | 可作核心（import）依賴 |
| `lgpl-dynamic` | LGPL | 只能動態連結，不修改、不內嵌 |
| `gpl-isolated` | GPL | 絕不 import；獨立行程，以 CLI 與檔案交接 |
| `forbidden` | AGPL、非商業、source-available | 不進產品 |

目前核心依賴全為 permissive：fastmcp (Apache-2.0)、mcp (MIT)、pydantic (MIT)、pint (BSD-3-Clause)、fluids (MIT)、numpy (BSD-3-Clause)。`tests/test_licenses.py` 會在 CI 中斷言環境裡沒有任何 GPL／AGPL 套件。

選用的 `png` extra 在 `drawing` 之上只多 pypdfium2（BSD-3-Clause 或 Apache-2.0；wheel 帶的 PDFium、abseil、llvm-libc、AGG、FreeType、ICU、lcms2、libjpeg-turbo、OpenJPEG、libpng、zlib 等授權文字逐一查過，全是寬鬆授權；它是原生程式碼，所以只渲染自己剛寫的 PDF，不碰不受信任的輸入）。選用的 `cad` extra（`geometry_measure`、`metrology_check_scan`、`drawing_make_views` 共用）是 cadquery-ocp-novtk，`scan` extra 再加 open3d (MIT, permissive)，`drawing` extra 再加 ezdxf (MIT, permissive；連帶 fonttools、pyparsing 為 MIT，typing_extensions 為 PSF) 與 pdfminer.six (MIT, permissive；連帶 charset-normalizer 為 MIT，cryptography 為 Apache-2.0 或 BSD-3-Clause；純 Python，沒選用 pdfplumber 與 pypdfium2，前者只多一層包裝並帶進 Pillow 與原生 PDFium，後者是原生程式碼，解析不受信任的檔案時當掉會拖垮伺服器)。`licenses.KNOWN` 另有一列護欄 `pymupdf`（宣告 `forbidden`，AGPL）：它不是依賴，只是讓誤裝 `ezdxf[draw]` 時 doctor 判定失敗。cadquery-ocp-novtk 的 wrapper 是 Apache-2.0，但 wheel 內含 OpenCASCADE（LGPL-2.1 加 OCCT exception），所以在 `licenses.KNOWN` 誠實宣告為 `lgpl-dynamic`（動態連結、不修改、不內嵌進本 repo），並以 `bundled_license` 記下來源；`toolkit_doctor` 不會因 metadata 顯示 Apache 而誤報授權不符；這個「bundled 說明」只適用於 permissive wrapper 宣告為 `lgpl-dynamic`，任何已安裝、宣告為 `gpl-isolated` 或 `forbidden` 的引擎，doctor 一律判定失敗（`policy-violation`）。OCP wheel 另外還內含其他雙重授權的函式庫（回報有 FreeImage、LibRaw、FreeType、zstd，各有非 GPL 的授權選項）；再散布前應逐一確認。

## Clean-room 聲明

本專案為獨立的開源工程工具箱，不含、也不得引入任何雇主或客戶的程式碼、資料、模型、圖檔或品牌。貢獻時請遵守此界線。

---

## 開發

```bash
uv sync --all-groups --extra scan --extra drawing
uv run pytest -v
uv run eng-toolkit doctor
```

新增領域：在 `eng_toolkit/domains/<name>/` 建一個 FastMCP 子 server 與 `DOMAIN = DomainInfo(...)`；領域與引擎都是**被發現的**，不用改任何共用清單：套件下有 `DOMAIN` 就會掛上閘道，領域自己的 `engines.py` 寫 `ENGINES = (Engine(...), ...)` 就會進授權登記表。清單見 [docs/adding-a-domain.md](docs/adding-a-domain.md)。每個工具用 `runner.run_tool` 包裝，輸入過 `units.parse_quantity`，就自動符合契約、落檔與來源紀錄。

## 設計紀錄

* [docs/metrology-design.md](docs/metrology-design.md)：`metrology` 領域的設計決策（資料表自建、NumPy 而非 monaco、工具切分粒度）。
* [docs/iso286-data.md](docs/iso286-data.md)：ISO 286 資料表來源、授權、涵蓋範圍與已知缺口。
* [docs/geometry-drawing/design.md](docs/geometry-drawing/design.md)：`geometry` 與 `drawing` 領域的合成設計（目前實作了 `geometry_measure`、`geometry_make_part`、`drawing_make_views` 的第一部分與 `drawing_read`）：兩份候選設計與跨評審結論、切片交付計畫、待專案負責人決定的問題。
* [docs/geometry-drawing/drawing-determinism.md](docs/geometry-drawing/drawing-determinism.md)：`drawing_make_views` 的 DXF 與 PDF 為何、如何做到跨行程位元組相同：實測哪裡不同、採用的修法與成本。
* [docs/geometry-drawing/drawing-read.md](docs/geometry-drawing/drawing-read.md)：`drawing_read` 的設計決策（輸出與大小控制、DXF 與 PDF 引擎的選擇與授權、子行程隔離、HTTP 下的讀檔白名單、單位與 DIMLFAC 規則、標題欄推斷）、驗證方法與小模型（Haiku）可用性檢查結果。
* [docs/geometry-drawing/drawing-callouts.md](docs/geometry-drawing/drawing-callouts.md)：S6（`drawing_make_views` 第二部分）的設計決策：孔分組與標註的規則、版面空間、座標尺寸、`features.json` 雜湊綁定與孔對應、SVG 自寫後端、PNG 的方法與授權查核與確定性範圍、與 `drawing_read` 的往返檢查、Haiku 可用性檢查（兩輪結果與據此改的摘要）、已知缺口。
* [docs/geometry-drawing/drawing-fixes.md](docs/geometry-drawing/drawing-fixes.md)：獨立驗證後修的七個小問題（只存曲面的 IGES 接縫、全形空白、`%%` 跳脫、PDF 字級與預設線寬、外框分開報告、`ENG_TOOLKIT_READ_DIRS` 空值）：實測、決定與被否決的做法。
* [docs/geometry-drawing/make-part-eval.md](docs/geometry-drawing/make-part-eval.md)：`geometry_make_part` 的小模型評測（基準為 Claude Haiku，由專案負責人決定）：18 題的解析解、與 build123d 的對照結果、據此新增 `sphere` 與 `cone`、結果的限制，以及如何重跑。

## 路線圖（摘要）

Phase 0 閘道 + 契約 + 單位守門 + `calc` 油壓 → Phase 1 `metrology` 公差（本 repo 已含 ISO 286 與一維堆疊）、CAD/B-Rep 與 2D 圖檔 → PCB/IC EDA → 模擬。各領域引擎與授權評估見設計文件。
