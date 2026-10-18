# ISO 286 資料表：來源、授權與涵蓋範圍

`metrology_fit_limits` 的資料在 `eng_toolkit/domains/metrology/data/iso286.json`。

## 來源與授權

* 內容是國際標準 **ISO 286-1 / ISO 286-2**（極限與配合系統，2010 版）中公布的**數值事實**：IT 公差等級表（Table 1）與軸基本偏差表。這些是公開引用的工程常數，本 repo 不含標準文本、圖表或排版，也不含任何其他軟體套件或公司資料。
* 表由本專案人工謄錄，授權隨整個 repo 為 Apache-2.0。**標準本身才是權威**：用於正式圖面或安全相關設計前，請對照合法取得的標準複核。所有輸出皆為 `review_status: "unreviewed"`。
* JSON 只存 IT 值與軸的基本偏差；孔的偏差由標準的一般規則（A–H：EI = −es）與特殊規則（K–N 至 IT8、P–ZC 至 IT7：ES = −ei + Δ）在 `iso286.py` 推導。
* 測試用的第三方比對資料（**不是執行期依賴**）：
  * [`isofits`](https://pypi.org/project/isofits/)（MIT）：在 `dev` 依賴群組，測試逐格掃描它的 37 個孔類別與 37 個軸類別（3–400 mm 全部尺寸範圍）。它自己有 8 個尺寸範圍的格子（E7、K6、M6、f6）與標準規則不符（見 `tests/test_metrology_fits.py` 的 `KNOWN_ISOFITS_ERRORS`，附手算），測試把這份清單釘死。
  * [`@casys/mcp-tolerance`](https://jsr.io/@casys/mcp-tolerance)（MIT）：只把其公開測試資料中與手算一致的 6 組配合值複製成純測試資料。
  * ISO 286-1 的 IT 公式與基本偏差公式：測試用公式重算整張表，誤差超過 max(3 µm, 4 %) 就失敗，用來抓謄錯數字。

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

1. **逐格驗證只到 isofits 的範圍**：孔 E F G H J JS K M N P R、軸 a d e f g h j js k m n p r，尺寸 3 < D ≤ 400 mm。其餘（b c cd ef fg s t u v x y z za zb zc、3 mm 以下的段、400–500 mm）只對公式驗證（容差約 ±4 %），結果會附 `warnings` 提醒「僅公式驗證」。
2. **M、N 孔在 IT8 以上**（M9–M18、N9–N18）：採 M：ES = −ei、N：ES = 0（D > 3 mm）。未找到獨立來源逐格驗證，結果會附警告。
3. **孔 J 僅 J6–J8、軸 j 僅 j5–j7**（標準的定義範圍）；其他等級回 `input.undefined_class`。
4. **孔 K–ZC 低於 IT3 不支援**（特殊規則的 Δ 只定義於 IT3 起）。
5. **JS／js 不做奇數取整**：標準允許 IT7–IT11 為奇數 µm 時取 (IT−1)/2，本工具取精確的 ±IT/2（與 isofits 一致）。
6. 3 mm 以下與 500 mm 以上不支援（資料表已含 ≤3 mm 段，只因為工具下限設為 3 mm，3 mm 本身屬該段）。
7. 不涵蓋 ISO 286-2 的「優選公差帶」旗標、形位公差、表面粗糙度、溫度影響。
