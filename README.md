# eng-toolkit

**AI 可調用的開源工程工具箱** — 一組 MCP (Model Context Protocol) server，把寬鬆授權的開源工程引擎包成 AI 代理可直接呼叫的工具，回傳**帶單位、可重現、附來源**的結構化結果。

> ⚠️ 所有輸出皆標示 `review_status: "unreviewed"`。本工具箱的結果是**未經工程核可**的估算，必須由合格工程師複核後才能用於設計或採購。

目前有兩個領域：`calc`（油壓計算）與 `metrology`（ISO 286 配合查詢與反查、一維公差堆疊）。CAD／2D 圖檔／EDA／模擬等領域尚未開始。

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
│   └── (未來) cad_*, dxf_*, eda_* ...               │
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

腳本會：安裝 uv → `uv sync` 依 `uv.lock` 建立 `.venv` → 執行 `toolkit_doctor` → 印出註冊到 Claude Code 的指令。

手動安裝（已有 uv）：

```bash
uv sync --all-groups
uv run eng-toolkit doctor        # 健康檢查；exit 0 = 全部通過
uv run pytest                    # 測試
```

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

或用本 repo 附的專案層級 `.mcp.json`（在 repo 內開 Claude Code 即可）。接上後 `/mcp` 應看到 `eng-toolkit` 與七個工具。

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

**OpenAI Codex CLI**（`~/.codex/config.toml`）

```toml
[mcp_servers.eng-toolkit]
command = "uv"
args = ["run", "--directory", "C:\\path\\to\\eng-toolkit", "eng-toolkit"]
```

> 為了讓地端中小模型也能穩定呼叫，工具維持少量粗粒度（目前 6 個），所有輸入參數都是**基本 JSON 型別**（字串／布林），每個參數附描述；數量一律是「數字 + 單位」的單一字串，如 `"100 mm"`。

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
                 "inputs_hash": "sha256:..."},
  "warnings": ["Ideal theoretical force: ..."],
  "error": null,
  "review_status": "unreviewed"
}
```

* `value` 是主要結果；`values` 含所有命名結果，多輸出工具因此不必跳出契約。
* `uncertainty` 與 `quantity` 同單位；`null` 表示未估算（封閉公式）。壓降工具給的是名目區間（層流 ±5%、紊流 ±10%、過渡 ±20%），不是校準值。
* `provenance.inputs_hash` 是輸入的 canonical JSON 之 SHA-256：相同輸入 → 相同雜湊，可重現。
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

## Clean-room 聲明

本專案為獨立的開源工程工具箱，不含、也不得引入任何雇主或客戶的程式碼、資料、模型、圖檔或品牌。貢獻時請遵守此界線。

---

## 開發

```bash
uv sync --all-groups
uv run pytest -v
uv run eng-toolkit doctor
```

新增領域：在 `eng_toolkit/domains/<name>/` 建一個 FastMCP 子 server 與 `DOMAIN = DomainInfo(...)`，在 `eng_toolkit/domains/__init__.py::all_domains` 註冊，把用到的引擎加進 `licenses.KNOWN`。每個工具用 `runner.run_tool` 包裝，輸入過 `units.parse_quantity`，就自動符合契約、落檔與來源紀錄。

## 設計紀錄

* [docs/metrology-design.md](docs/metrology-design.md)：`metrology` 領域的設計決策（資料表自建、NumPy 而非 monaco、工具切分粒度）。
* [docs/iso286-data.md](docs/iso286-data.md)：ISO 286 資料表來源、授權、涵蓋範圍與已知缺口。

## 路線圖（摘要）

Phase 0 閘道 + 契約 + 單位守門 + `calc` 油壓 → Phase 1 `metrology` 公差（本 repo 已含 ISO 286 與一維堆疊）、CAD/B-Rep 與 2D 圖檔 → PCB/IC EDA → 模擬。各領域引擎與授權評估見設計文件。
