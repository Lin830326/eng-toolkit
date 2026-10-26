"""Generate uat/data/F4_motor_accel_10kHz.csv: 12 s of accelerometer data at 10 kHz from a 1480 rpm motor with a 40-tooth gear.

Run: python uat/data/make_F4_data.py   (deterministic, seed 4)
Content (g peak): 1x 24.667 Hz 0.060, 2x 49.333 Hz 0.025, mains pickup 50 Hz 0.045, 120 Hz 0.090 (structural),
gear mesh 40x = 986.67 Hz 0.150 with +-1x sidebands 0.030, sensor resonance 6300 Hz 0.050 that ALIASES to 3700 Hz at fs = 10 kHz
(no analogue anti-alias filter), white noise sigma 0.02 g, DC bias 0.012 g.  Two columns: time in s, acceleration in g, no header.
"""
import numpy as np
from pathlib import Path

FS, DUR = 10_000, 12.0
RPM = 1480.0
F1 = RPM / 60.0
rng = np.random.default_rng(4)
t = np.arange(int(FS * DUR)) / FS


def tone(f, a):
    return a * np.sin(2 * np.pi * f * t + rng.uniform(0, 2 * np.pi))


x = (tone(F1, 0.060) + tone(2 * F1, 0.025) + tone(50.0, 0.045) + tone(120.0, 0.090)
     + tone(40 * F1, 0.150) + tone(40 * F1 - F1, 0.030) + tone(40 * F1 + F1, 0.030)
     + tone(6300.0, 0.050) + 0.012 + rng.normal(0, 0.02, t.size))
out = Path(__file__).resolve().parent / "F4_motor_accel_10kHz.csv"
np.savetxt(out, np.column_stack([t, x]), fmt=["%.4f", "%.5f"], delimiter=",")
print(out, out.stat().st_size)
