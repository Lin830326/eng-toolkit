---
name: eng-signal-analysis
description: Use to analyse a sampled signal with the eng-toolkit dsp_* tools - vibration spectrum and peak naming, running-speed and gear-mesh lines, aliasing, anti-alias filter design, decimation, delay by correlation. Covers file format and traps. Load eng-toolkit first.
---

# Signal analysis workflow (eng-toolkit)

Load `eng-toolkit` first. The six `dsp_*` tools are numpy FFT and closed-form filter design, unreviewed and deterministic. They do not judge leakage, non-stationary signals or sensor physics.

## 1. Pin down the problem (ask if it changes the answer)

Sample rate and record length; units of the data (g, V, mm/s); what the signal is (machine, rpm or line frequency, tooth counts, pole count); **which frequencies matter and which must be kept or rejected**; whether the record will be decimated and to what rate; analog filter in front of the converter (order, corner); the file layout (**header-less**, one column or time,value).

## 2. Choose the tool

| Question | Tool |
|---|---|
| Spectrum, peak list, SNR/THD/ENOB | `dsp_spectrum` |
| Is a tone aliased; minimum sample rate; ADC bits | `dsp_sampling` (`alias`, `min_sample_rate`, `quantization`) |
| Decimate or interpolate a record with the right anti-alias FIR | `dsp_sampling` (`decimate`, `interpolate`) |
| Design a filter by order or by specification | `dsp_filter_design` |
| Run a filter on samples (causal or zero-phase) | `dsp_filter_apply` |
| Delay between two records, RMS, crest, envelope, period | `dsp_correlate` |
| Window properties (ENBW, side lobes, scalloping) | `dsp_window` |

## 3. Build the inputs

- **Frequencies carry units and are Hz.** An `rpm` value is not a frequency: shaft `f = rpm / 60` (1480 rpm = 24.67 Hz; 1x = 24.67, 2x = 49.33 Hz, not 1480 Hz). Check 50/60 Hz mains separately: it is a different line from 2x.
- `samples_file` = **numbers only, no header**, one column of values (then give `sample_rate "10 kHz"`) or two columns time,value (rate read from the time column). A header line or a third column is refused with the line number: strip the header (shell, or ask the user to re-export). The CSV tables the tools write (`filtered.csv`, `decimated.csv`: header, 3-4 columns) can NOT be fed back into `dsp_spectrum`; check a filtered result with the filter's own measurements instead. Inline `samples` is a JSON list.
- Amplitude comes back in the unit of the samples (g stays g) although the tool prints `dimensionless`; PSD is unit^2/Hz.
- `dsp_spectrum`: `window "hann"` (flattop for accurate amplitude, rectangular only for whole periods), `peaks 10`, `remove_mean true`, `table_rows` up to 20000. Peaks are within `peak_threshold_db` (60) of the largest: raise it for weak lines. Resolution `bin = fs/N`: Hann needs the tones about 4 bins apart, so two lines 0.67 Hz apart need a record of at least 6 s (the 12 s record has 0.083 Hz bins). Welch (`segment_length`) trades resolution for variance.
- Filter by specification: `family chebyshev1 | butterworth | chebyshev2 | fir_kaiser ...`, `response lowpass`, `sample_rate`, `passband_edge`, `stopband_edge`, `passband_ripple_db`, `stopband_attenuation_db`. Orders and tap counts come back; a Kaiser FIR from the specification can land 0.1 dB short (59.9 dB for 60 asked): ask for 61 (125 taps, 61.1 dB, `meets_specification` true) and read the measured `stopband_attenuation_db`.

## 4. Recipe: vibration record, 10 kHz, 1480 rpm motor, 40-tooth pinion (verified on the 12 s file)

1. `dsp_spectrum samples_file <path> window hann peaks 10 remove_mean true` : bin 0.0833 Hz; peaks 986.67 Hz 0.150 g, 120.00 Hz 0.090, 24.667 Hz 0.060, 3700 Hz 0.050, 50.00 Hz 0.045, 1011.3 and 962.0 Hz 0.030, 49.33 Hz 0.025.
2. Name every peak with a rule: 1x = 24.67 Hz (peak 24.667), 2x = 49.33 Hz (peak 49.333, resolved from the 50.00 Hz line 0.67 Hz away), **gear mesh = teeth x 1x = 40 x 24.667 = 986.7 Hz** (hand check), sidebands at mesh +- 1x = 962.0 and 1011.3 Hz. Tell the user the "1 kHz" tone is the mesh; 120 Hz stays unexplained unless the user knows a source (do not invent one). Synchronous speed 25 Hz at 50 Hz and 4 poles, slip 1.3 %.
3. **Alias check of every high peak**: the 3700 Hz line could be real or a fold of 6300 Hz (`dsp_sampling alias frequency "6.3 kHz" sample_rate "10 kHz"` gives 3700 Hz, inverted). One record cannot tell: say "not decidable here; record again at another sample rate (a real line stays, an alias moves) or put a steeper analog filter in front". A first-order 5 kHz RC filter passes 6.3 kHz almost untouched; a digital filter cannot remove an alias after the fact.
4. **Decimate by 4 (10 to 2.5 kHz, keep 0..1.1 kHz, alias 60 dB down, ripple 0.5 dB)**: stopband edge = new rate - kept band = 2.5 - 1.1 = 1.4 kHz (anything from 1.4 to 3.6 and 3.9 to 5 kHz folds into 0..1.1 kHz). Direct route: `dsp_sampling operation decimate factor 4 samples_file <path> attenuation_db 60 passband_fraction 0.88` returns 123 taps, alias band attenuation 60.3 dB, ripple 0.016 dB, delay removed, `decimated.csv`. IIR alternative: `dsp_filter_design chebyshev1 lowpass sample_rate "10 kHz" passband_edge "1.1 kHz" stopband_edge "1.4 kHz" passband_ripple_db 0.5 stopband_attenuation_db 60` gives order 12, 64.6 dB at 1.4 kHz, but group delay 9.7 ms rising steeply at the passband edge and a direct-form (b, a) that must be run as the `sos` rows. FIR (linear phase, constant delay 61 samples) is the default for waveform work; IIR with `zero_phase true` removes phase but squares the magnitude.
5. Plot: `plot_xy` on `spectrum.csv` (`x_column "frequency_hz"`, `y_columns "amplitude"`, `kind line`, `x_range "0,1200"`, labels with units, `vlines`) - a 0 Hz row blocks `x_scale log`; stems of thousands of rows give a huge result. Filter response: `filter_response.csv` (`magnitude_db`) with `y_range "-90,5"`.

## 5. Delay and envelope

`dsp_correlate samples "[...]" samples_b "[...]" sample_rate "1 kHz"`: positive `delay` means the second record lags the first (checked: 3 samples = 3 ms, refined 2.975). `envelope true` for modulation; exact only for whole periods.

## 6. Outside the toolkit

Order tracking with varying speed, wavelet or time-frequency maps, bearing-defect formulas, ISO 10816 severity limits, accelerometer resonance and mounting, analog filter design, adaptive or Kalman filtering, integration to velocity. Say so.

## 7. Report

As in `eng-toolkit`: peak table with frequency (Hz), amplitude (unit of data) and the rule that names it; filter spec versus measured attenuation and delay; assumptions (record length, window); checks (mesh = teeth x 1x, alias check, resolution); limits (one record cannot separate an alias from a real tone; unreviewed).
