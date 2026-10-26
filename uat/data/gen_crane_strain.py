"""Generate the synthetic-but-realistic strain-gauge record for scenario M2 (crane hook-block lifting-lug weld).

Known generating parameters (also written to crane_hook_strain_truth_params.json):
  fs = 10 Hz, N = 20000 samples (2000 s), seed 20261026
  hook-block empty weight strain 60 ue; payload strain per tonne 82.5 ue/t (SWL 6.3 t -> 520 ue = 109 MPa with E = 210 GPa)
  lifts: payload mass lognormal(median 2.0 t, sigma 0.55), clipped to 0.3..6.3 t; two snatch-loads (dynamic factor 1.7)
  each lift: 3 s pick-up ramp (+ damped 1.3 Hz overshoot, ~10 % of the load), travel with 0.25 Hz sway (3 % of load),
  2 s set-down ramp; empty gaps 8..30 s with +-3 ue swing of the empty block
  gauge noise 1.5 ue rms, 1 ue quantisation, slow thermal drift 6 ue over the record
  three single-sample EMI spikes (welder next door) are in the raw file only
"""
import json
import numpy as np
from pathlib import Path

rng = np.random.default_rng(20261026)
fs, N = 10.0, 20000
t = np.arange(N) / fs
eps = np.full(N, 60.0)                      # empty hook block
k_per_t = 520.0 / 6.3                      # ue per tonne
i = 40                                      # start sample
lifts = []
while True:
    gap = int(rng.uniform(8, 30) * fs)
    i += gap
    m = float(np.clip(rng.lognormal(np.log(2.0), 0.55), 0.3, 6.3))
    up, down = int(3 * fs), int(2 * fs)
    hold = int(rng.uniform(15, 40) * fs)
    if i + up + hold + down + 10 >= N:
        break
    load = m * k_per_t
    snatch = len(lifts) in (9, 23)
    n = up + hold + down
    prof = np.zeros(n)
    prof[:up] = load * np.linspace(0, 1, up)
    prof[up:up + hold] = load
    prof[up + hold:] = load * np.linspace(1, 0, down)
    tt = np.arange(n) / fs
    dyn = 0.10 * load * np.exp(-(tt - 3) / 1.6) * np.cos(2 * np.pi * 1.3 * (tt - 3)) * (tt >= 3)
    if snatch:
        dyn = dyn * 0 + 0.7 * load * np.exp(-(tt - 3) / 1.2) * np.cos(2 * np.pi * 1.3 * (tt - 3)) * (tt >= 3)
    sway = 0.03 * load * np.sin(2 * np.pi * 0.25 * tt + rng.uniform(0, 6.28)) * (np.arange(n) > up)
    eps[i:i + n] += prof + dyn + sway
    lifts.append({"start_s": i / fs, "mass_t": round(m, 3), "snatch": snatch})
    i += n
swing = 3.0 * np.sin(2 * np.pi * 0.4 * t) * np.exp(-((t % 40) / 20))
mask_empty = eps < 62
eps = eps + np.where(mask_empty, swing, 0)
eps += 6.0 * t / t[-1] + rng.normal(0, 1.5, N)
eps = np.round(eps)
clean = eps.copy()
raw = eps.copy()
spikes = [4317, 9902, 15561]
for s, v in zip(spikes, (3600, -1800, 4100)):
    raw[s] = v

out = Path(__file__).parent
for name, arr, header in (("crane_hook_strain_raw.csv", raw, True), ("crane_hook_strain_despiked.csv", clean, False)):
    with open(out / name, "w", newline="") as f:
        if header:
            f.write("time_s,strain_ue\n")
        for tv, ev in zip(t, arr):
            f.write(f"{tv:.1f},{int(ev)}\n")
(out / "crane_hook_strain_truth_params.json").write_text(json.dumps({
    "fs_Hz": fs, "samples": N, "n_lifts": len(lifts), "n_snatch": sum(l["snatch"] for l in lifts),
    "spike_samples": spikes, "lifts": lifts}, indent=1))
print(len(lifts), "lifts;", raw.max(), clean.max(), clean.min())
