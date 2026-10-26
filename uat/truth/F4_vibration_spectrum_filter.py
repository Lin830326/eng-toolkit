"""Truth for F4: 10 kHz accelerometer record of a 1480 rpm motor, dominant peaks, are 1x / 2x there, anti-alias low-pass for decimation by 4.

Independent of eng-toolkit: scipy.signal (periodogram / welch / find_peaks / ellipord / buttord / cheb1ord / kaiserord / freqz) on
uat/data/F4_motor_accel_10kHz.csv (made by uat/data/make_F4_data.py; known content listed there). Prints JSON {answers, how, tolerance}.
"""
import json
from pathlib import Path
import numpy as np
from scipy import signal

DATA = Path(__file__).resolve().parents[1] / "data" / "F4_motor_accel_10kHz.csv"
FS = 10_000.0
RPM = 1480.0
F1 = RPM / 60.0

d = np.loadtxt(DATA, delimiter=",")
t, x = d[:, 0], d[:, 1]
assert abs(1 / (t[1] - t[0]) - FS) < 1e-6
x0 = x - x.mean()


def amp_spectrum(sig, nfft=None, window="hann"):
    n = len(sig)
    w = signal.get_window(window, n)
    f = np.fft.rfftfreq(n, 1 / FS)
    a = np.abs(np.fft.rfft(sig * w)) * 2 / w.sum()
    return f, a


f, a = amp_spectrum(x0)
noise_floor = float(np.median(a))
pk, _ = signal.find_peaks(a, height=10 * noise_floor, distance=int(0.3 / (f[1] - f[0])))


def refine(i):
    y0, y1, y2 = np.log(a[i - 1]), np.log(a[i]), np.log(a[i + 1])
    den = y0 - 2 * y1 + y2
    delta = 0.5 * (y0 - y2) / den if den else 0.0
    return float(f[i] + delta * (f[1] - f[0])), float(np.exp(y1 - 0.25 * (y0 - y2) * delta))


peaks = sorted((refine(i) for i in pk), key=lambda p: -p[1])
peak_list = [{"f_Hz": round(fr, 2), "amp_g": round(am, 4)} for fr, am in peaks[:12]]


def amp_at(fc, half=0.5):
    m = (f > fc - half) & (f < fc + half)
    i = np.argmax(np.where(m, a, 0))
    return refine(i)


one_x, two_x, mains = amp_at(F1), amp_at(2 * F1, 0.3), amp_at(50.0, 0.3)
# resolution needed to separate 2x from the mains line
sep = 50.0 - 2 * F1
# 1 s Welch (Hann): bin 1 Hz -> 2x and 50 Hz merge
fw, pw = signal.welch(x0, FS, window="hann", nperseg=10000, noverlap=5000, scaling="spectrum")
aw = np.sqrt(2 * pw)  # RMS-ish amplitude per Hann ENBW bin: only used to show merging
merged_1s = bool(abs(fw[np.argmax(np.where((fw > 45) & (fw < 55), pw, 0))] - 50) <= 1.0)

# design spec for decimation by 4 (10 kHz -> 2.5 kHz): keep to 1.1 kHz, alias band starts at 1.4 kHz, 60 dB
FP, FST, RP, RS = 1100.0, 1400.0, 0.5, 60.0
n_butter, _ = signal.buttord(FP, FST, RP, RS, fs=FS)
n_cheb1, _ = signal.cheb1ord(FP, FST, RP, RS, fs=FS)
n_cheb2, _ = signal.cheb2ord(FP, FST, RP, RS, fs=FS)
n_ellip, _ = signal.ellipord(FP, FST, RP, RS, fs=FS)
numtaps, beta = signal.kaiserord(RS, (FST - FP) / (FS / 2))
b, aa = signal.ellip(n_ellip, RP, RS, FP, fs=FS)
w_, h_ = signal.freqz(b, aa, worN=[1000, 1100, 1250, 1400, 3700], fs=FS)
att = {f"{int(fq)} Hz": round(float(20 * np.log10(abs(hh))), 1) for fq, hh in zip(w_, h_)}
# a plain 4th order Butterworth at 1.25 kHz for comparison (what a quick guess gives)
bq, aq = signal.butter(4, 1250, fs=FS)
_, hq = signal.freqz(bq, aq, worN=[1000, 1400, 3700], fs=FS)
quick = {f"{int(fq)} Hz": round(float(20 * np.log10(abs(hh))), 1) for fq, hh in zip([1000, 1400, 3700], hq)}
alias = FS - 6300.0

ans = {
    "record": f"{len(x)} samples, {t[-1] + 1 / FS:.1f} s at {FS:.0f} Hz, bin width {f[1]:.3f} Hz",
    "shaft_1x_Hz": round(F1, 3),
    "shaft_2x_Hz": round(2 * F1, 3),
    "mains_line_Hz": 50.0,
    "separation_2x_vs_mains_Hz": round(sep, 3),
    "dominant_peaks_hann_whole_record": peak_list,
    "gear_mesh_peak_Hz": round(amp_at(40 * F1, 1.0)[0], 2),
    "gear_mesh_Hz_expected_40_teeth": round(40 * F1, 2),
    "amp_1x_g": round(one_x[1], 4),
    "amp_2x_g": round(two_x[1], 4),
    "amp_50Hz_g": round(mains[1], 4),
    "1x_present": bool(one_x[1] > 10 * noise_floor),
    "2x_present_and_separate_from_50Hz": bool(two_x[1] > 10 * noise_floor and abs(two_x[0] - 49.333) < 0.1),
    "needs_resolution_better_than_Hz": round(sep / 2, 3),
    "1s_welch_segments_merge_2x_and_mains": merged_1s,
    "alias_peak_Hz_from_6300Hz_resonance": round(alias, 1),
    "alias_peak_amp_g": round(amp_at(alias, 5.0)[1], 4),
    "alias_note": "the 3.7 kHz line is a 6.3 kHz sensor resonance folded by sampling; a digital filter cannot tell it from a real 3.7 kHz tone",
    "noise_floor_median_amp_g": round(noise_floor, 5),
    "decimation_by_4_spec": f"pass to {FP:.0f} Hz (<= {RP} dB ripple), stop from {FST:.0f} Hz (>= {RS:.0f} dB) at fs {FS:.0f} Hz",
    "min_order_butterworth": int(n_butter),
    "min_order_chebyshev1": int(n_cheb1),
    "min_order_chebyshev2": int(n_cheb2),
    "min_order_elliptic": int(n_ellip),
    "min_fir_taps_kaiser": int(numtaps),
    "elliptic_gain_dB_at": att,
    "quick_butter4_1250Hz_gain_dB_at": quick,
}
print(json.dumps({"answers": ans,
  "how": ("scipy: whole-record Hann amplitude spectrum with log-parabolic peak interpolation (resolution 1/12 s = 0.083 Hz), find_peaks 20 dB over the median floor; "
          "filter orders from buttord / cheb1ord / cheb2ord / ellipord and kaiserord for the stated spec; gains from freqz. The record is synthetic with known content (see uat/data/make_F4_data.py): "
          "1x 0.060 g, 2x 0.025 g, mains 50 Hz 0.045 g, 120 Hz 0.090 g, gear mesh 986.7 Hz 0.150 g with 1x sidebands, 6.3 kHz resonance aliased to 3.7 kHz."),
  "tolerance": ("Peak frequencies +-0.2 Hz (below 200 Hz) / +-1 Hz above, amplitudes +-10 % (window scalloping). The key facts: 1x (24.67 Hz) and 2x (49.33 Hz) are both present; 2x is only "
                "separable from the 50 Hz mains line with a record of several seconds (>= ~3 s with Hann); the user's 1 kHz tone is really the 40-tooth gear mesh at 986.7 Hz; a peak at 3.7 kHz is an alias. "
                "Any filter that meets the stated spec is right: Butterworth needs order 30 (so elliptic, Chebyshev or FIR is the sensible choice), a quick 4th-order Butterworth does NOT give 60 dB at 1.4 kHz."),
 }, indent=1))
