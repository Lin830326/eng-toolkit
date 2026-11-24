# Finite elements (meshing and linear statics) (`fem`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## `fem_mesh`、`fem_static`
實體 CAD 模型的有限元素：`fem_mesh` 用 Gmsh 產生二階四面體網格（10 節點），回傳節點／元素數、元素品質（平均比值：1 為正四面體、0 為扁平）、網格體積對精確 B-rep 體積的相對差，以及碎片元素與過粗網格的警告，網格存成 `.msh`；`fem_static` 再用 CalculiX 做**線性靜力**分析（小位移、線彈性等向材料），回傳最大位移及位置、最大 von Mises 應力及位置、支承反力對外力的平衡檢查（偏差超過 1e-6 就整個呼叫失敗），並存 `.inp`、`.frd`、`.dat` 與每個節點位移與應力的 `results.csv`。Gmsh 與 CalculiX 是**使用者自己安裝**的獨立程式（GPL，只當外部行程啟動，從不打包或匯入；用 `ENG_TOOLKIT_GMSH`／`ENG_TOOLKIT_CCX` 指定路徑，或放在 `PATH`，`toolkit_doctor` 會列出）；讀幾何需要 `cad` extra。輸入檔與單位處理同 `geometry_measure`（STEP／IGES 依檔內單位轉成 mm）。

```
fem_static(model_path=".../bar.step", element_size="2.5 mm", youngs_modulus="210 GPa", poisson_ratio=0.3,
           supports="x = 0 mm : ux; point(0 mm, 5 mm, 0 mm) : uy; point(0 mm, 5 mm, 10 mm) : uy; point(0 mm, 0 mm, 5 mm) : uz",
           loads="force (10 kN, 0 N, 0 N) on x = 100 mm")
```
面的選法是平面：`x = 0 mm`（落在該平面的所有邊界面）或 `normal +z at z = 20 mm`（還要外法向量朝 +z）；支承 `<面> [: ux uy uz | fixed]` 或 `point(x, y, z) : <自由度>`（最近的網格節點，須在一個元素大小內），載重 `force (Fx, Fy, Fz) on <面>`（總力依面積均勻分布）或 `pressure p on <面>`（正值壓向材料）；以 `;` 分隔。一個選不到任何面的選擇器、同時是支承又受力的面、留有剛體運動的支承，都回清楚的錯誤（`fem.empty_selection`、`fem.overlapping_selection`、`fem.not_constrained`）。**呼叫者從不提供 Gmsh 腳本或 CalculiX 輸入檔**，選擇器語言只允許字母、數字與少數符號，解析成數字後由工具自己寫輸入；元素數在網格前以 `6√2·體積/尺寸³` 估算，超過 6 萬個、或尺寸小於外框對角線的 1/1000 回 `input.too_large`。整面固定（`fixed`）會擋住該面的泊松收縮，應力與撓度與梁理論略有差異（警告會說明）。不含塑性、接觸、大位移、動態、溫度。數字的推導、容許誤差與限制見 [docs/fem/design.md](../fem/design.md)。

- `fem_view`：把一次 `fem_static` 的結果（`result_dir` 為該工作目錄，會核對 `manifest.json` 的 sha256；或 `mesh_file` + `results_file`）畫成色彩對應的 GLB（網格表面，每個二次三角面分成 4 個三角形；von Mises、位移、各應力分量或主應力；可放大變形並畫出未變形輪廓），另寫 `legend.svg`、`view.json` 與預設的 PNG 預覽圖（`fem_view.png`，含色階、單位與範圍；`preview="none"` 關閉）；顏色是 CalculiX 的節點值（外插並平均），只顯示表面，不跑任何程式。說明見 [docs/fem/design.md](../fem/design.md#fem_view-a-static-result-as-a-colour-mapped-glb)。

---
