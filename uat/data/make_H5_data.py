"""Generate uat/data/H5_fan_motor_accel_25k6.csv: 5 s of accelerometer data at 25.6 kHz from a fan motor (1790 rpm) whose drive-end
6206 bearing has an outer-race defect.

Run: python uat/data/make_H5_data.py   (deterministic, seed 11)
Bearing 6206: 9 balls, ball diameter 9.525 mm, pitch diameter 46.0 mm, contact angle 0 -> BPFO = n/2 (1 - d/D) fr = 3.568 fr.
Content (g): shaft 1x 29.833 Hz 0.30, 2x 0.12, 3x 0.05, mains pickup 60 Hz 0.06 and 120 Hz 0.04, fan blade pass 7x = 208.8 Hz 0.05,
outer-race impacts at BPFO = 106.4 Hz (1 % random slip) each exciting a 3.8 kHz resonance (decay time constant 0.35 ms), impact
amplitude 1.0 g with +-25 % random scatter and a mild once-per-revolution modulation (stationary load zone, outer race), a second
resonance at 8.1 kHz excited at 0.4 of the amplitude, white noise sigma 0.12 g, DC bias 0.05 g (IEPE).
Two columns: time in s, acceleration in g, no header.
"""
import numpy as np
from pathlib import Path

FS, DUR = 25_600, 5.0
RPM = 1790.0
FR = RPM / 60.0
N_BALLS, BD, PD = 9, 9.525, 46.0
BPFO = N_BALLS / 2 * (1 - BD / PD) * FR
rng = np.random.default_rng(11)
n = int(FS * DUR)
t = np.arange(n) / FS


def tone(f, a):
    return a * np.sin(2 * np.pi * f * t + rng.uniform(0, 2 * np.pi))


x = tone(FR, 0.30) + tone(2 * FR, 0.12) + tone(3 * FR, 0.05) + tone(60.0, 0.06) + tone(120.0, 0.04) + tone(7 * FR, 0.05)
# impacts at BPFO with random slip
times, tk = [], rng.uniform(0, 1 / BPFO)
while tk < DUR:
    times.append(tk)
    tk += (1 / BPFO) * (1 + rng.normal(0, 0.005))
tau1, tau2, f1, f2 = 0.35e-3, 0.30e-3, 3800.0, 8100.0
k = np.arange(int(0.006 * FS)) / FS
ring = np.exp(-k / tau1) * np.sin(2 * np.pi * f1 * k) + 0.4 * np.exp(-k / tau2) * np.sin(2 * np.pi * f2 * k)
imp = np.zeros(n)
for ti in times:
    i = int(round(ti * FS))
    amp = 1.0 * (1 + rng.uniform(-0.25, 0.25)) * (1 + 0.15 * np.cos(2 * np.pi * FR * ti))
    m = min(len(ring), n - i)
    if m > 0:
        imp[i:i + m] += amp * ring[:m]
x = x + imp + 0.05 + rng.normal(0, 0.12, n)
out = Path(__file__).resolve().parent / "H5_fan_motor_accel_25k6.csv"
np.savetxt(out, np.column_stack([t, x]), fmt=["%.10f", "%.5f"], delimiter=",")
print(out, out.stat().st_size, "BPFO", BPFO, "impacts", len(times))
