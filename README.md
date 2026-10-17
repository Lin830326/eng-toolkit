# eng-toolkit

**AI 可調用的開源工程工具箱** — 一組 MCP (Model Context Protocol) server，把寬鬆授權的開源工程引擎包成 AI 代理可直接呼叫的工具，回傳**帶單位、可重現、附來源**的結構化結果。

> ⚠️ 所有輸出皆標示 `review_status: "unreviewed"`。本工具箱的結果是**未經工程核可**的估算，必須由合格工程師複核後才能用於設計或採購。

目前為 **Phase 0 骨架**：一個閘道 + 一個 `calc` 領域（油壓計算）。CAD／2D 圖檔／量測／EDA／模擬等領域尚未開始。

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
│   └── (未來) cad_*, dxf_*, meas_*, eda_* ...    │
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
uv run eng-toolkit --doctor      # 健康檢查；exit 0 = 全部通過
uv run pytest                    # 測試
```

---

## 接上 Claude Code

**方式 A：使用者層級註冊（所有專案都能用）**

```bash
claude mcp add --scope user eng-toolkit -- uv run --directory "C:\path\to\eng-toolkit" eng-toolkit
```

**方式 B：專案層級 `.mcp.json`**（本 repo 已附；在 repo 內開 Claude Code 即可）

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

接上後在 Claude Code 中：`/mcp` 應看到 `eng-toolkit` 連線，工具 `toolkit_doctor`、`toolkit_list_tools`、`calc_cylinder_force`、`calc_pipe_pressure_drop`。

---

## 工具

### `toolkit_doctor`
檢查 Python 版本、每個引擎的安裝版本與授權（宣告值 vs. 套件 metadata）、工作區可寫性、pint 自檢，並掃描整個環境標出 GPL／AGPL／未知授權的套件。任何 GPL／AGPL 出現 → `ok: false`。

### `toolkit_list_tools`
列出已安裝領域、背後引擎（版本、授權分級）與全部工具名稱。

### `calc_cylinder_force`
單桿油壓缸理想推力／拉力。輸入：`bore`、`rod`（可省略）、`pressure`、`output_unit`（預設 `kN`）。

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

---

## 輸入規則：每個物理量都要帶單位

接受 `"100 mm"`、`"210 bar"`、`"40 cP"`，或 `{"value": 100, "unit": "mm"}`。

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

目前核心依賴全為 permissive：fastmcp (Apache-2.0)、mcp (MIT)、pydantic (MIT)、pint (BSD-3-Clause)、fluids (MIT)。`tests/test_licenses.py` 會在 CI 中斷言環境裡沒有任何 GPL／AGPL 套件。

## Clean-room 聲明

本專案為獨立的開源工程工具箱，不含、也不得引入任何雇主或客戶的程式碼、資料、模型、圖檔或品牌。貢獻時請遵守此界線。

---

## 開發

```bash
uv sync --all-groups
uv run pytest -v
uv run eng-toolkit --doctor
```

新增領域：在 `eng_toolkit/domains/<name>/` 建一個 FastMCP 子 server 與 `DOMAIN = DomainInfo(...)`，在 `eng_toolkit/domains/__init__.py::all_domains` 註冊，把用到的引擎加進 `licenses.KNOWN`。每個工具用 `runner.run_tool` 包裝，輸入過 `units.parse_quantity`，就自動符合契約、落檔與來源紀錄。

## 路線圖（摘要）

Phase 0（本 repo）閘道 + 契約 + 單位守門 + `calc` 油壓 → Phase 1 CAD/B-Rep 與 2D 圖檔 → 公差量測 → PCB/IC EDA → 模擬。各領域引擎與授權評估見設計文件。
