"""Independent truth for M2: rainflow + Miner on a 20 000-sample crane hook-block strain-gauge record.

Own ASTM E1049-85 (three-point, 4.4.2 'rainflow counting' variant with the start-point rule; residue = half cycles) written
with numpy only, and Miner's rule with the IIW FAT 63 S-N curve (Hobbacher, IIW Recommendations for Fatigue Design of Welded
Joints and Components, 2016: FAT class = stress RANGE at 2e6 cycles, slope m = 3). Stress = E * strain, E = 210 GPa
(1 microstrain = 0.21 MPa). No eng-toolkit code.
"""
import json
from pathlib import Path
import numpy as np

DATA = Path(__file__).resolve().parents[1] / "data"
E_MPA = 210000.0
FAT, N_REF, M = 63.0, 2.0e6, 3.0       # MPa range at 2e6 cycles, slope 3 (design office rule: straight line, no cut-off)
FS = 10.0
HOURS_ALREADY = 11000.0                  # hook-active hours the crane has already run on a similar duty (person's fact)


def load(name):
    rows = Path(DATA / name).read_text().strip().splitlines()
    if rows[0][0].isalpha():
        rows = rows[1:]
    return np.array([float(r.split(",")[1]) for r in rows])


def reversals(x):
    """turning points of x (flat plateaus collapsed); first and last sample are kept"""
    y = [x[0]]
    for v in x[1:]:
        if v != y[-1]:
            y.append(v)
    y = np.array(y)
    keep = [y[0]]
    for k in range(1, len(y) - 1):
        if (y[k] - y[k - 1]) * (y[k + 1] - y[k]) < 0:
            keep.append(y[k])
    keep.append(y[-1])
    return np.array(keep)


def e1049(points, gate=0.0):
    """ASTM E1049 three-point rainflow; returns list of (range, mean, count) with count 1.0 or 0.5"""
    out, stack = [], []
    for p in points:
        stack.append(p)
        while len(stack) >= 3:
            x = abs(stack[-1] - stack[-2])
            y = abs(stack[-2] - stack[-3])
            if x < y:
                break
            if len(stack) == 3:                      # Y contains the start point: count a half cycle, drop start
                out.append((y, (stack[-2] + stack[-3]) / 2, 0.5))
                stack.pop(0)
            else:                                    # full cycle, discard both points of Y
                out.append((y, (stack[-2] + stack[-3]) / 2, 1.0))
                del stack[-3:-1]
    for a, b in zip(stack[:-1], stack[1:]):          # residue counted as half cycles
        out.append((abs(b - a), (a + b) / 2, 0.5))
    return out


def damage(cycles):
    d = 0.0
    for rng_mpa, _mean, cnt in cycles:
        if rng_mpa <= 0:
            continue
        d += cnt * (rng_mpa / FAT) ** M / N_REF
    return d


def analyse(name, gate_mpa=0.0):
    ue = load(name)
    s = ue * E_MPA * 1e-6
    p = reversals(s)
    if gate_mpa > 0:                                  # drop reversals smaller than the gate (hysteresis filter)
        q = [p[0]]
        for v in p[1:]:
            if abs(v - q[-1]) >= gate_mpa:
                q.append(v)
        p = reversals(np.array(q))
    cyc = e1049(p)
    rng = np.array([c[0] for c in cyc])
    cnt = np.array([c[2] for c in cyc])
    d = damage(cyc)
    record_s = len(ue) / FS
    life_h = record_s / 3600.0 / d
    return {
        "n_samples": int(len(ue)), "record_s": record_s,
        "max_stress_MPa": round(float(s.max()), 1), "min_stress_MPa": round(float(s.min()), 1),
        "max_range_MPa": round(float(rng.max()), 1),
        "equivalent_full_cycles": round(float(cnt.sum()), 1),
        "cycles_range_over_20MPa": round(float(cnt[rng > 20].sum()), 1),
        "cycles_range_over_60MPa": round(float(cnt[rng > 60].sum()), 1),
        "damage_per_record": float(f"{d:.4g}"),
        "hours_to_failure_h": round(life_h, 0),
        "remaining_hours_after_11000h": round(life_h - HOURS_ALREADY, 0),
        "n_ranges_total_with_half_counts": round(float(cnt.sum()), 1),
    }, cyc


clean, cyc = analyse("crane_hook_strain_despiked.csv")
clean_gate, _ = analyse("crane_hook_strain_despiked.csv", gate_mpa=2.0)
raw, _ = analyse("crane_hook_strain_raw.csv")

# Haibach / IIW variable-amplitude alternative: slope 2m-1 = 5 below the knee at 1e7 cycles (amplitude knee)
knee_range = FAT * (N_REF / 1e7) ** (1 / M)
d_h = 0.0
for rng_mpa, _m, cnt in cyc:
    if rng_mpa <= 0:
        continue
    n_ = N_REF * (FAT / rng_mpa) ** M if rng_mpa >= knee_range else 1e7 * (knee_range / rng_mpa) ** (2 * M - 1)
    d_h += cnt / n_
haibach_life = 2000.0 / 3600.0 / d_h

# histogram of ranges (for the plot): 10 MPa bins of equivalent cycle counts, and damage share by bin
rng = np.array([c[0] for c in cyc]); cnt = np.array([c[2] for c in cyc])
edges = np.arange(0, 140, 10)
hist = [float(cnt[(rng >= a) & (rng < b)].sum()) for a, b in zip(edges[:-1], edges[1:])]

# generating parameters
params = json.loads((DATA / "crane_hook_strain_truth_params.json").read_text())

print(json.dumps({
    "answers": {
        "S_N_curve": "IIW FAT 63: stress range 63 MPa at 2e6 cycles, m=3  ->  Basquin amplitude form Sa = A N^b with A = 31.5*(2e6)^(1/3) = %.1f MPa, b = -1/3" % (31.5 * 2e6 ** (1 / 3)),
        "despiked_file": clean,
        "despiked_file_with_2MPa_hysteresis_gate": {k: clean_gate[k] for k in ("damage_per_record", "hours_to_failure_h", "equivalent_full_cycles")},
        "raw_file_with_three_EMI_spikes_(WRONG, spikes are not loads)": raw,
        "haibach_below_1e7_alternative_life_h": round(haibach_life, 0),
        "histogram_range_bins_10MPa_equivalent_counts": dict(zip([f"{int(a)}-{int(b)}" for a, b in zip(edges[:-1], edges[1:])], hist)),
        "generating_parameters": {"lifts": params["n_lifts"], "snatch_loads": params["n_snatch"], "spike_sample_indices": params["spike_samples"],
                                   "note": "the number of large (range > 60 MPa) full cycles is about the number of lifts with load above ~half SWL, plus the two snatch loads"},
        "expected_conclusion": "Raw file contains three single-sample spikes (3600, -1800, 4100 microstrain = 756, -378, 861 MPa, far above the yield of any steel and the 109 MPa strain at the 6.3 t SWL): they must be recognised as measurement artefacts. On the despiked file the Miner life is the 'hours_to_failure_h' above (hook-active hours), remaining = that minus 11000 h. With the spikes the damage is wildly overstated.",
    },
    "how": "numpy-only ASTM E1049 three-point rainflow (own code, residue as half cycles) on stress = 210 GPa * microstrain, Miner D = sum n_i / N_i with N_i = 2e6 (63/range_i)^3 (IIW FAT 63, straight line, no endurance cut-off, no mean-stress correction because IIW welded details are mean-stress independent); life = record time / D. Hours are hook-active hours; the person's elapsed 11000 h is subtracted for the remaining life.",
    "tolerance": "damage and life within 5 % of the despiked-file values (hysteresis gate or counting convention changes it by < 1 %); the raw count of cycles (4987 equivalent) is dominated by sensor-noise cycles below 1 MPa and drops to about 250 with a 2 MPa gate, so compare DAMAGE and the number of cycles with range above 20 MPa (34) rather than the total; must identify the spikes; the Haibach alternative is a different, longer life and only counts as right if clearly labelled as an alternative to the straight line the design office asked for."
}, indent=1))
