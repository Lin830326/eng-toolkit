# Digital signal processing (`dsp`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## `dsp_filter_design`、`dsp_spectrum`、`dsp_filter_apply`、`dsp_sampling`、`dsp_correlate`、`dsp_window`
數位訊號處理（取樣資料，只用 numpy，不用 scipy）。樣本以 JSON 清單或 CSV 檔（一欄數值，或「時間,數值」兩欄，只准數字，錯誤指出行號）給入；頻率與取樣率帶單位（`"48 kHz"`、`"628 rad/s"`）。`filter_design`：Butterworth、Chebyshev I／II（類比原型＋雙線性轉換與預扭曲，閉式最小階數）與視窗法 FIR（Kaiser 的 β 與階數由衰減與過渡帶寬算出），輸出二階節、(b, a)、極零點、穩定性、邊界頻率增益、群延遲與幅相表；`spectrum`：振幅譜（同調增益已校正）、PSD、Welch、峰值拋物線內插、SNR／SINAD／THD／SFDR／ENOB；`filter_apply`：DF2T 濾波、零相位、不穩定濾波器預設拒絕；`sampling`：混疊、最低取樣率、量化 6.02N+1.76、整數倍抽取與內插；`correlate`：統計量、自相關週期、互相關延遲（次樣本）、Hilbert 包絡；`window`：視窗性質與閉式值對照

```json
{"family": "butterworth", "response": "lowpass", "sample_rate": "8 kHz", "passband_edge": "1 kHz", "stopband_edge": "2 kHz", "passband_ripple_db": 1, "stopband_attenuation_db": 40}
```
→ `dsp_filter_design`：`order = 6`（閉式 5.99 進位），通帶邊界 −1.000 dB、阻帶邊界 −40.07 dB。精確與近似之處、驗證方式與未涵蓋項目（無適應濾波、小波、多相、橢圓濾波器、Parks-McClellan、即時處理）見 [docs/dsp/design.md](../dsp/design.md)。
