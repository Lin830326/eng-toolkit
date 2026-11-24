"""Truth for H5: is there a bearing defect in a 25.6 kHz accelerometer record of a 1790 rpm fan motor (6206, 9 balls)?

scipy/numpy only, no eng-toolkit code: Welch/periodogram of the raw record, band-pass 2-6 kHz around the structural resonance, Hilbert
envelope, FFT of the envelope with its peaks compared with the bearing defect frequencies computed from the geometry.
"""
import json
import math
from pathlib import Path

import numpy as np
from scipy import signal

DATA = Path(__file__).resolve().parents[1] / "data" / "H5_fan_motor_accel_25k6.csv"
RPM = 1790.0
FR = RPM / 60.0
N, BD, PD = 9, 9.525, 46.0
RATIO = BD / PD


def peaks_in(freq, amp, lo, hi, count=1):
    sel = (freq >= lo) & (freq <= hi)
    f, a = freq[sel], amp[sel]
    idx, _ = signal.find_peaks(a)
    order = idx[np.argsort(a[idx])[::-1]][:count]
    return [(round(float(f[i]), 2), float(a[i])) for i in order]


def main():
    t, x = np.loadtxt(DATA, delimiter=",", unpack=True)
    fs = round((len(t) - 1) / float(t[-1] - t[0]))
    x = x - x.mean()
    n = len(x)
    # raw amplitude spectrum, Hann window, peak amplitude scaled
    w = np.hanning(n)
    spec = np.abs(np.fft.rfft(x * w)) * 2 / w.sum()
    freq = np.fft.rfftfreq(n, 1 / fs)
    raw_peaks = []
    idx, _ = signal.find_peaks(spec, height=0.02)
    for i in idx[np.argsort(spec[idx])[::-1]][:12]:
        raw_peaks.append((round(float(freq[i]), 1), round(float(spec[i]), 3)))
    # band-pass around the resonance and envelope
    sos = signal.butter(4, [2000, 6000], btype="bandpass", fs=fs, output="sos")
    xb = signal.sosfiltfilt(sos, x)
    env = np.abs(signal.hilbert(xb))
    env -= env.mean()
    ew = np.hanning(n)
    espec = np.abs(np.fft.rfft(env * ew)) * 2 / ew.sum()
    # defect frequencies from the geometry (contact angle 0)
    bpfo = N / 2 * (1 - RATIO) * FR
    bpfi = N / 2 * (1 + RATIO) * FR
    bsf = PD / (2 * BD) * (1 - RATIO ** 2) * FR
    ftf = 0.5 * (1 - RATIO) * FR
    cand = {"BPFO": bpfo, "2xBPFO": 2 * bpfo, "3xBPFO": 3 * bpfo, "BPFI": bpfi, "2xBSF": 2 * bsf, "FTF": ftf, "1x": FR}
    floor = float(np.median(espec[(freq > 20) & (freq < 600)]))
    found = {}
    for name, f0 in cand.items():
        p = peaks_in(freq, espec, f0 * 0.97, f0 * 1.03)
        found[name] = {"expected_Hz": round(f0, 2), "envelope_peak_Hz": p[0][0] if p else None,
                       "envelope_peak_over_median_floor": round(p[0][1] / floor, 1) if p else None}
    top_env = peaks_in(freq, espec, 15, 1000, 6)
    # resonance location: band energy of the raw spectrum
    res = peaks_in(freq, spec, 2000, 12000, 3)
    kurt_raw = float(np.mean(x ** 4) / np.mean(x ** 2) ** 2)
    kurt_band = float(np.mean(xb ** 4) / np.mean(xb ** 2) ** 2)
    bpfo_raw = [peaks_in(freq, spec, bpfo * 0.97, bpfo * 1.03)[0]]
    answers = {
        "sampling_rate_Hz": fs, "record_s": round(n / fs, 2), "shaft_speed_Hz": round(FR, 3),
        "defect_frequencies_Hz": {"BPFO": round(bpfo, 2), "BPFI": round(bpfi, 2), "BSF": round(bsf, 2), "FTF": round(ftf, 2)},
        "common_mistake": {"BPFO_taken_as_balls_x_shaft_speed_Hz": round(N * FR, 1), "note": "the outer-race frequency is 3.57 x shaft speed, not 9 x"},
        "raw_spectrum_top_peaks_Hz_g": raw_peaks,
        "raw_spectrum_at_BPFO": {"freq_Hz": bpfo_raw[0][0], "amplitude_g": round(bpfo_raw[0][1], 4), "note": "no clear peak at BPFO in the raw spectrum: it is buried by the 1x, 2x, mains and noise; impacts show up as a hump around the resonance"},
        "resonance_peaks_Hz_g": [(f, round(a, 3)) for f, a in res],
        "envelope_band_Hz": [2000, 6000],
        "envelope_spectrum_top_peaks_Hz": [(f, round(a, 4)) for f, a in top_env],
        "envelope_peaks_at_defect_frequencies": found,
        "kurtosis_raw": round(kurt_raw, 2), "kurtosis_bandpassed": round(kurt_band, 2),
        "verdict": "Yes: an outer-race (BPFO) defect. The envelope spectrum of the 2-6 kHz band has its largest peak at 106.5 Hz (BPFO) with harmonics at 2x and 3x; no peak at BPFI, BSF or FTF. The raw spectrum alone does not show it (large 1x, 2x, 60 Hz and 7x blade-pass lines).",
    }
    print(json.dumps({"answers": answers,
                      "how": "numpy/scipy on the CSV: Hann FFT of the raw record; 4th-order Butterworth band-pass 2-6 kHz (zero phase) -> Hilbert envelope -> mean removed -> Hann FFT; peaks compared with bearing frequencies from n, d, D at contact angle 0",
                      "tolerance": "BPFO 106.5 Hz within 1 Hz; defect frequencies within 1 %. The envelope peak at BPFO must be called out (3x and more above the neighbouring floor). Any claim of a bearing fault from the raw FFT alone, or BPFO taken as 268 Hz (9 x fr), is wrong. Other faults (BPFI, BSF, FTF) must not be reported."},
                     indent=1))


if __name__ == "__main__":
    main()
