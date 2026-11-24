# eng-toolkit

[English](README.md) | [繁體中文](README.zh-TW.md)

**AI 可調用的開源工程工具箱** — 一組 MCP (Model Context Protocol) server，把寬鬆授權的開源工程引擎包成 AI 代理可直接呼叫的工具，回傳**帶單位、可重現、附來源**的結構化結果。

> ⚠️ 所有輸出皆標示 `review_status: "unreviewed"`。本工具箱的結果是**未經工程核可**的估算，必須由合格工程師複核後才能用於設計或採購。

各領域與工具列在下方的[領域與工具](#領域與工具)；每個領域是一個子 server，最多 6 個工具。

---

## 架構

```
MCP client (Claude Code, Codex, Cursor, LM Studio, ...)
        |  stdio or streamable HTTP (JSON-RPC)
        v
+--------------------------------------------------------------+
|  gateway (FastMCP)       toolkit_list_tools   toolkit_doctor |
|    mounts every package under eng_toolkit/domains/ that      |
|    exposes DOMAIN, as <domain>_<tool>                        |
+--------------------------------------------------------------+
|  domains: calc  metrology  geometry  drawing  control  ...   |
|    in-process engines: permissive licenses only              |
|    GPL programs (Gmsh, CalculiX, KiCad): separate processes  |
+--------------------------------------------------------------+
|  contract.py   one return shape for every tool (pydantic)    |
|  units.py      unit gate (pint): bare numbers are refused    |
|  workspace.py  ws/<job-id>/ files with sha256                |
|  licenses.py   license tiers and environment scan            |
+--------------------------------------------------------------+
```

領域是**被發現的**：`eng_toolkit/domains/` 下有 `DOMAIN` 的套件就會掛上閘道，不用改任何共用清單。

**工具命名**：MCP 工具名稱不能含 `.`，設計文件中的 `calc.cylinder_force` 在 MCP 上即為 `calc_cylinder_force`（`<領域>_<工具>`）。

Gateway 的參數型別錯誤、缺少必填參數與未知參數也回傳 `ok=false` 的結構化結果，包含錯誤 family 與 `error.details.param` JSON pointer。複合單位內的偏移溫度單位（如 `MPa*degF/K`）會被拒絕；絕對溫度仍可用 `degC`／`degF`，溫差使用 K 或明確的 delta 單位。JSON 工作檔將非有限輸入記錄為字串，不寫入裸 `NaN`／`Infinity`。詳見[共用 gateway 設計](docs/gateway/design.md)。

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

> 為了讓地端中小模型也能穩定呼叫，工具維持少量粗粒度（每個領域最多 6 個），所有輸入參數都是**基本 JSON 型別**（字串／布林／整數），每個參數附描述；數量一律是「數字 + 單位」的單一字串，如 `"100 mm"`。

---

## 領域與工具

每個領域的完整說明在 `docs/tools/<領域>.md`。閘道另有 `toolkit_list_tools` 與 `toolkit_doctor`（[說明](docs/tools/toolkit.md)）。

| 領域 | 涵蓋內容 | 工具 | 需要 |
|---|---|---|---|
| [`calc`](docs/tools/calc.md) | 油壓計算：油缸出力與速度、管路壓降、孔口流量、泵功率、蓄壓器預充 | `calc_cylinder_force` `calc_pipe_pressure_drop` `calc_orifice_flow` `calc_pump_power` `calc_accumulator_precharge` `calc_cylinder_speed` | — |
| [`metrology`](docs/tools/metrology.md) | ISO 286 配合查詢與反查、一維公差堆疊、形狀誤差、位置度、掃描點雲對 STEP 模型的偏差 | `metrology_fit_limits` `metrology_stackup_1d` `metrology_fit_search` `metrology_check_scan` `metrology_form_error` `metrology_position_tolerance` | `metrology_check_scan` 需要 `scan` extra |
| [`geometry`](docs/tools/geometry.md) | 量測 STEP／IGES／BREP 模型、由 JSON 規格建零件、DFM 檢查、格式轉換、3D 檢視 | `geometry_measure` `geometry_make_part` `geometry_check_dfm` `geometry_convert` `geometry_view` | `cad` extra |
| [`drawing`](docs/tools/drawing.md) | 含隱藏線、圖框與尺寸的三視圖（DXF、PDF、SVG、PNG）；讀圖檔與轉檔 | `drawing_make_views` `drawing_read` `drawing_convert` | `drawing` extra；PNG 需要 `png` extra；DWG 需要 LibreDWG 的 `dwg2dxf` |
| [`control`](docs/tools/control.md) | 極點零點與穩定性、時域與頻域響應、回授、PID 調整、離散化 | `control_analyze` `control_response` `control_frequency` `control_feedback` `control_pid_tune` `control_discretize` | — |
| [`digital`](docs/tools/digital.md) | Verilog 合成、檢查與等價性驗證 | `digital_synthesize` `digital_check` `digital_equivalent` | Yosys |
| [`dsp`](docs/tools/dsp.md) | IIR 與 FIR 濾波器設計、頻譜、濾波、取樣、相關、窗函數 | `dsp_filter_design` `dsp_spectrum` `dsp_filter_apply` `dsp_sampling` `dsp_correlate` `dsp_window` | — |
| [`electric`](docs/tools/electric.md) | 變壓器、感應馬達與永磁／直流馬達、三相功率、驅動選型、DC-DC 轉換器 | `electric_transformer` `electric_induction_motor` `electric_pm_dc_motor` `electric_power_flow` `electric_drive_sizing` `electric_converter` | — |
| [`fatigue`](docs/tools/fatigue.md) | 應力壽命、雨流計數、應變壽命、裂縫成長、材料資料 | `fatigue_sn` `fatigue_rainflow` `fatigue_strain_life` `fatigue_fracture` `fatigue_material` | — |
| [`fem`](docs/tools/fem.md) | 實體的四面體網格與線性靜力分析、著色的結果檢視 | `fem_mesh` `fem_static` `fem_view` | `cad` extra、Gmsh、CalculiX |
| [`fluids`](docs/tools/fluids.md) | 管網、泵系統、明渠、可壓縮流、水錘、外部阻力 | `fluids_pipe_network` `fluids_pump_system` `fluids_open_channel` `fluids_compressible` `fluids_surge` `fluids_external_flow` | — |
| [`machine`](docs/tools/machine.md) | 軸、正齒輪、銲道群、壓力容器、皮帶、鍵與栓槽 | `machine_shaft` `machine_gear` `machine_weld` `machine_pressure_vessel` `machine_belt` `machine_key` | — |
| [`mech`](docs/tools/mech.md) | 螺栓檢核、軸承壽命、壓縮彈簧、樑彎曲 | `mech_bolt_check` `mech_bearing_life` `mech_spring_compression` `mech_beam_bending` | — |
| [`optimize`](docs/tools/optimize.md) | 有限制的最小化、LP／MIP、求根、曲線擬合、敏感度、實驗設計 | `optimize_minimize` `optimize_linprog` `optimize_root` `optimize_fit` `optimize_sensitivity` `optimize_doe` | — |
| [`pcb`](docs/tools/pcb.md) | 電路板資料、設計規則檢查、電氣規則檢查、gerber／鑽孔／座標檔輸出 | `pcb_info` `pcb_drc` `pcb_erc` `pcb_export` | DRC、ERC 與輸出需要 KiCad（`kicad-cli`） |
| [`plot`](docs/tools/plot.md) | 由 CSV 產出物或行內數列畫出可重現的 SVG 或 PNG 圖表 | `plot_xy` `plot_panels` `plot_bar` | PNG 需要 `png` extra |
| [`spice`](docs/tools/spice.md) | 工作點、DC、AC 與暫態電路模擬、netlist 檢查、量測 | `spice_simulate` `spice_check_netlist` `spice_measure` | ngspice |
| [`structures`](docs/tools/structures.md) | 斷面性質、桁架、平面構架、振動模態、柱挫屈、應力狀態 | `structures_section` `structures_truss` `structures_frame` `structures_modal` `structures_column` `structures_stress` | — |
| [`structures3d`](docs/tools/structures3d.md) | 空間構架、模態分析、線性挫屈、薄板、Winkler 基礎 | `structures3d_static` `structures3d_modal` `structures3d_buckling` `structures3d_plate` `structures3d_plate_stability` `structures3d_foundation` | — |
| [`thermal`](docs/tools/thermal.md) | 熱傳導、熱交換器、鰭片、暫態熱傳、輻射、對流 | `thermal_conduction` `thermal_exchanger` `thermal_fin` `thermal_transient` `thermal_radiation` `thermal_convection` | — |

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

本專案：**Apache-2.0**；`plugin/` 目錄（Claude Code plugin 的 skill、指令與 hook）另採 **MIT**，見 [plugin/LICENSE](plugin/LICENSE)。依賴分級（`eng_toolkit/licenses.py`，`toolkit_doctor` 會實際核對）：

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

## 貢獻

歡迎回報錯誤、補驗證案例、新增工具或領域。issue 與 pull request 可以用英文或繁體中文。

* 規則、環境設定與送 pull request 的步驟：[CONTRIBUTING.md](CONTRIBUTING.md)。
* 新增領域的檔案清單與預算：[docs/adding-a-domain.md](docs/adding-a-domain.md)。
* 安全漏洞不可開公開 issue，回報方式見 [SECURITY.md](SECURITY.md)。

## 設計紀錄

* [docs/metrology-design.md](docs/metrology-design.md)：`metrology` 領域的設計決策（資料表自建、NumPy 而非 monaco、工具切分粒度）。
* [docs/iso286-data.md](docs/iso286-data.md)：ISO 286 資料表來源、授權、涵蓋範圍與已知缺口。
* [docs/geometry-drawing/design.md](docs/geometry-drawing/design.md)：`geometry` 與 `drawing` 領域的合成設計（目前實作了 `geometry_measure`、`geometry_make_part`、`drawing_make_views` 的第一部分與 `drawing_read`）：兩份候選設計與跨評審結論、切片交付計畫、待專案負責人決定的問題。
* [docs/geometry-drawing/drawing-determinism.md](docs/geometry-drawing/drawing-determinism.md)：`drawing_make_views` 的 DXF 與 PDF 為何、如何做到跨行程位元組相同：實測哪裡不同、採用的修法與成本。
* [docs/geometry-drawing/drawing-read.md](docs/geometry-drawing/drawing-read.md)：`drawing_read` 的設計決策（輸出與大小控制、DXF 與 PDF 引擎的選擇與授權、子行程隔離、HTTP 下的讀檔白名單、單位與 DIMLFAC 規則、標題欄推斷）、驗證方法與小模型（Haiku）可用性檢查結果。
* [docs/geometry-drawing/drawing-callouts.md](docs/geometry-drawing/drawing-callouts.md)：S6（`drawing_make_views` 第二部分）的設計決策：孔分組與標註的規則、版面空間、座標尺寸、`features.json` 雜湊綁定與孔對應、SVG 自寫後端、PNG 的方法與授權查核與確定性範圍、與 `drawing_read` 的往返檢查、Haiku 可用性檢查（兩輪結果與據此改的摘要）、已知缺口。
* [docs/geometry-drawing/drawing-fixes.md](docs/geometry-drawing/drawing-fixes.md)：獨立驗證後修的七個小問題（只存曲面的 IGES 接縫、全形空白、`%%` 跳脫、PDF 字級與預設線寬、外框分開報告、`ENG_TOOLKIT_READ_DIRS` 空值）：實測、決定與被否決的做法。
* [docs/geometry-drawing/make-part-eval.md](docs/geometry-drawing/make-part-eval.md)：`geometry_make_part` 的小模型評測（基準為 Claude Haiku，由專案負責人決定）：18 題的解析解、與 build123d 的對照結果、據此新增 `sphere` 與 `cone`、結果的限制，以及如何重跑。

## 路線圖

已完成的領域列在[領域與工具](#領域與工具)。每個領域還沒驗證或還沒做的項目，記在該領域的設計紀錄（`docs/<領域>/`）。想新增工具或領域，請先開 issue 討論範圍與引擎授權。
