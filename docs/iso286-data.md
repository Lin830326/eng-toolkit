# ISO 286 資料表：來源、授權與涵蓋範圍

`metrology_fit_limits` 的資料在 `eng_toolkit/domains/metrology/data/iso286.json`。

## 來源與授權

* 內容是國際標準 **ISO 286-1 / ISO 286-2**（極限與配合系統，2010 版）中公布的**數值事實**：IT 公差等級表（Table 1）與軸基本偏差表。這些是公開引用的工程常數，本 repo 不含標準文本、圖表或排版，也不含任何其他軟體套件或公司資料。
* 表由本專案人工謄錄，授權隨整個 repo 為 Apache-2.0。**標準本身才是權威**：用於正式圖面或安全相關設計前，請對照合法取得的標準複核。所有輸出皆為 `review_status: "unreviewed"`。
* JSON 只存 IT 值與軸的基本偏差；孔的偏差由標準的一般規則（A–H：EI = −es）與特殊規則（K–N 至 IT8、P–ZC 至 IT7：ES = −ei + Δ）在 `iso286.py` 推導。
* 比對資料（**不是執行期依賴**，也沒有複製進 repo）：
  * [`isofits`](https://pypi.org/project/isofits/)（MIT）：在 `dev` 依賴群組，測試逐格掃描它的 37 個孔類別與 37 個軸類別（3–400 mm 全部尺寸範圍）。它自己有 8 個尺寸範圍的格子（E7、K6、M6、f6）與標準規則不符，見 `tests/test_metrology_fits.py` 的 `KNOWN_ISOFITS_ERRORS`（附手算），測試把這份清單釘死。
  * [RoyMech](https://www.roymech.co.uk/Useful_Tables/ISO_Tolerances/ISO_SHAFT_LIM_1.html) 公開的 ISO 286 基本偏差表（0–500 mm，軸與孔）：一次性人工比對共約 1400 格（軸 a–zc 全部位置、孔 A–ZC 與 M/N 的 IT8 以上規則）。差異只有下列幾格，全部是來源表的筆誤：
    * 軸 j5、j6：其基本偏差表在 18–65 mm 與它自己的極限偏差表不一致；本表與其極限偏差表、isofits 一致。
    * 孔 N7 ≤ 3 mm：來源 −5，同表 N8 與 N>8 皆為 −4；本表取 −4（= −n，ISO 在 3 mm 以下 Δ = 0）。
    * 孔 ZC 65–80 mm（來源 −490，同站軸表 zc = 480）與孔 X 180–200 mm（來源 −340，同站軸表 x = 350）：本表跟隨軸表。
    * 軸 zb 280–315 mm：來源兩張表皆為 1300，本表原先誤記 1350，已改為 1300（也最接近 ISO 公式 IT9 + 4D = 1318）。
  * [`@casys/mcp-tolerance`](https://jsr.io/@casys/mcp-tolerance)（MIT）：只把其公開測試資料中與手算一致的 6 組配合值複製成純測試資料。
  * ISO 286-1 的 IT 公式與基本偏差公式：測試用公式重算整張表，誤差超過 max(3 µm, 4 %) 就失敗，用來抓謄錯數字。
  * 注意：isofits 與 RoyMech 的極限偏差表來自同一個上游（連筆誤都相同），所以它們互相印證的程度有限；基本偏差表是 RoyMech 另外整理的，是較獨立的一份。

## 涵蓋範圍

| 項目 | 內容 |
|---|---|
| 公稱尺寸 | 3 至 500 mm（含兩端）；範圍外回 `input.out_of_range` |
| 尺寸段 | 上包含、下不包含：18 mm 屬 10–18，18.001 mm 屬 18–30；IT 值用 13 個主段，基本偏差用 25 個細段 |
| IT 等級 | IT01、IT0、IT1 至 IT18（全部） |
| 孔位置 | A B C CD D E EF F FG G H J JS K M N P R S T U V X Y Z ZA ZB ZC |
| 軸位置 | a b c cd d e ef f fg g h j js k m n p r s t u v x y z za zb zc |
| 配合型式 | 間隙／過渡／干涉，依 ISO 286-1 定義（EI ≥ es 為間隙，ES ≤ ei 為干涉） |

## 已知缺口與信心分級

1. **沒有對照 ISO 標準原文**：所有比對都是對照公開網頁與套件，不是對照付費取得的標準。正式圖面、安全相關設計前，請對照標準複核。
2. **M、N 孔在 IT8 以上**（M9–M18：ES = −ei；N9–N18、D > 3 mm：ES = 0）已與 RoyMech 的 ">M8"、">N8" 欄一致。
3. **孔 J 僅 J6–J8、軸 j 僅 j5–j7**（標準的定義範圍）；其他等級回 `input.undefined_class`。
4. **孔 K–ZC 低於 IT3 不支援**（特殊規則的 Δ 只定義於 IT3 起）。
5. **JS／js 不做奇數取整**：標準允許 IT7–IT11 為奇數 µm 時取 (IT−1)/2，本工具取精確的 ±IT/2（與 isofits 一致）。
6. 3 mm 以下與 500 mm 以上不支援（資料表已含 ≤3 mm 段，只因為工具下限設為 3 mm，3 mm 本身屬該段）。
7. 不涵蓋 ISO 286-2 的「優選公差帶」旗標、形位公差、表面粗糙度、溫度影響。
8. `metrology_fit_search` 的優選配合清單（孔基制 H11/c11、H9/d9、H8/f7、H7/g6、H7/h6、H7/k6、H7/n6、H7/p6、H7/s6、H7/u6，及軸基制對應）來自 ISO 286 / ANSI B4.2 的公開優選配合；清單只是代號，數值仍全部由本表推導。
