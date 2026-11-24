---
name: eng-signal-analysis
description: Use FIRST for "spectrum of my accelerometer or sensor record", which peak is 1x or gear mesh, is it aliased, design an anti-alias or low-pass filter, decimate a record (dsp_* tools). File format, alias check, filter traps.
---

# Signal analysis workflow (eng-toolkit)

Load `eng-toolkit` too. The six `dsp_*` tools are numpy FFT and closed-form filter design, unreviewed and deterministic. They do not judge leakage, non-stationary signals or sensor physics.

## 1. Pin down the problem (ask if it changes the answer)

Sample rate and record length; units of the data (g, V, mm/s); what the signal is (machine, rpm or line frequency, tooth counts, pole count); **which frequencies matter and which must be kept or rejected**; whether the record will be decimated and to what rate; **the analog front end (filter order and corner in front of the converter, sensor resonance)**: ask it in the first reply; the file layout (one column, or time,value; a header line is allowed).

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

- **Frequencies carry units and are Hz.** An `rpm` value is not a frequency: shaft `f = rpm / 60` (1750 rpm = 29.17 Hz, not 1750 Hz). Check 50/60 Hz mains separately: it is a different line from 2x.
- `samples_file` = numbers, optionally under one header line of column names (skipped and reported as `data.header_skipped`): one column (then give `sample_rate "8 kHz"`) or two columns time,value (rate read from the time column). A wider table needs `column` (header name or 1-based number; `data.column_used` says which was read): the `filtered.csv` of `dsp_filter_apply` (`index,time_s,input,output`) is read at `output` by default, `decimated.csv` (`index,time_s,value`) needs `column="value"`. So a filtered or decimated record goes straight back into `dsp_spectrum`. Other text is refused with the line number. Inline `samples` is a JSON list.
- Give `dsp_spectrum` `unit="g"` (or `m/s^2`, `V`): amplitude, rms and the table carry it, PSD is unit^2/Hz; without `unit` they print `dimensionless`.
- `dsp_spectrum`: `window "hann"` (flattop for accurate amplitude, rectangular only for whole periods), `peaks 10`, `remove_mean true`, `table_rows` up to 20000. Peaks are within `peak_threshold_db` (60) of the largest: raise it for weak lines. Resolution `bin = fs/N`: Hann needs the tones about 4 bins apart: two lines 1.67 Hz apart need bins of 0.4 Hz or finer (a record of 2.5 s or more). Welch (`segment_length`) trades resolution for variance
- Filter by specification: `family chebyshev1 | butterworth | chebyshev2 | fir_kaiser ...`, `response lowpass`, `sample_rate`, `passband_edge`, `stopband_edge`, `passband_ripple_db`, `stopband_attenuation_db`. Orders and tap counts come back; a Kaiser FIR can land short of the attenuation asked (see 4): read the measured `stopband_attenuation_db`.

## 4. Recipe (synthetic example: 8 kHz, 10 s, 4-pole motor 1750 rpm on 60 Hz, 17-tooth pinion)

Before designing a filter ask: highest frequency to keep, target rate, alias rejection.

1. `dsp_spectrum samples_file <path> unit g window hann peaks 12 remove_mean true`: bin 0.1 Hz; peaks 495.83 Hz 0.122 g, 29.165 Hz 0.051, 60.00 0.040, 2900.0 0.040, 58.335 0.031, 466.67 0.031, 525.00 0.030. A Hann line off a bin centre reads up to 1.4 dB low: use `flattop` for amplitudes.
2. Name every peak with a rule: 1x = rpm/60 = 29.17 Hz, 2x = 58.33 Hz (resolved from the 60.00 Hz line 1.67 Hz away), **gear mesh = teeth x 1x = 17 x 29.167 = 495.8 Hz** (hand check), sidebands at mesh +- 1x = 466.7 and 525.0 Hz. Synchronous speed `120 f / poles` = 1800 rpm, slip 2.8 %. A line no rule explains stays "unexplained"; do not invent a source.
3. **Alias check of every peak that matches no 1x, 2x, mesh or mains line** (here 2900 Hz): it may be real or a fold of 5100 Hz (`dsp_sampling alias frequency "5.1 kHz" sample_rate "8 kHz"` gives 2900 Hz, inverted). One record cannot tell: say "not decidable here; record again at another sample rate (a real line stays, an alias moves) or add a steeper analog filter". A digital filter cannot remove an alias afterwards.
4. **Decimate by 4 (8 to 2 kHz, keep 0..550 Hz, alias 60 dB down, ripple 0.5 dB)**: stopband edge = new rate - kept band = 2.0 - 0.55 = 1.45 kHz (1.45 to 2.55 and 3.45 to 4 kHz fold into 0..550 Hz). Direct route: `dsp_sampling operation decimate factor 4 samples_file <path> sample_rate "8 kHz" column 2 attenuation_db 60 passband_fraction 0.55` gives 35 taps, alias band attenuation 60.2 dB, ripple 0.014 dB, delay removed, `decimated.csv`. **Finish by running it on the person's file and comparing spectra** (`dsp_spectrum` on `decimated.csv`, `column value`, `sample_rate "2 kHz"`: the same lines, 2900 Hz gone). Give the coefficients when the person implements the filter elsewhere. A Kaiser FIR from a specification can land short (60 asked, 59.5 dB measured, `meets_specification` false): ask 62 (35 taps, 62.0 dB) and read the measured `stopband_attenuation_db`. IIR alternative: `dsp_filter_design chebyshev1 lowpass sample_rate "8 kHz" passband_edge "550 Hz" stopband_edge "1.45 kHz" passband_ripple_db 0.5 stopband_attenuation_db 60` gives order 5, 60.1 dB at 1.45 kHz, but group delay 2.1 ms at 500 Hz rising to 3.2 ms at 550 Hz, and (b, a) must be run as the `sos` rows. FIR (linear phase, constant delay) is the default for waveform work.
5. Plot as a PNG (`format "png"`, give `data.png_path`; skill `eng-plot-results`): `plot_xy` on `spectrum.csv` (`x_column "frequency_hz"`, `y_columns "amplitude"`, `kind line`, `x_range "0,700"`, labels with units, `vlines` at 1x, 2x, mesh) - a 0 Hz row blocks `x_scale log`. Filter response: `filter_response.csv` (`magnitude_db`) with `y_range "-90,5"`.

## 5. Delay and envelope

`dsp_correlate samples "[...]" samples_b "[...]" sample_rate "2 kHz"` (files also work): positive `delay` means the second record lags the first. `envelope true` for modulation; exact only for whole periods.

## 6. Outside the toolkit

Order tracking with varying speed, wavelet or time-frequency maps, bearing-defect formulas, ISO 10816 limits, accelerometer resonance and mounting, analog filter design, Kalman filtering, integration to velocity. Say so.

## 7. Report

As in `eng-toolkit`: peak table with frequency (Hz), amplitude (unit of data) and the rule that names it; filter spec versus measured attenuation and delay; assumptions (record length, window); checks (mesh = teeth x 1x, alias check, resolution); limits (one record cannot separate an alias from a real tone; unreviewed).
