"""Truth for E2: single-stage 2N2222 common-emitter amplifier, frequency response and a 10 mV transient.

Standalone ngspice (batch mode, .control scripts), plus the textbook hand estimate. No eng-toolkit code.
Circuit: Vcc 12 V; Vsrc (Rs 600 ohm) -> Cin 1 uF -> base; R1 47k (Vcc-base), R2 10k (base-gnd); Rc 2.2k; Re 470 with Ce 100 uF;
Cout 10 uF -> RL 10k.  Two transistor models: KiCad's demo file 2n2222.model (".model Q2N2222 npn (bf=200)", no capacitances)
and the usual PSpice Q2N2222 parameter set (with Cje/Cjc/Tf).
"""
import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path

NGSPICE = os.environ.get("NGSPICE", r"C:/Users/USER/miniconda3/envs/eng-eda/Library/bin/ngspice_con.exe")
KICAD_MODEL = Path(r"C:/Users/USER/AppData/Local/Programs/KiCad/10.0/share/kicad/demos/simulation/amplifier-ac/2n2222.model")
PSPICE = ("NPN(Is=14.34f Xti=3 Eg=1.11 Vaf=74.03 Bf=255.9 Ne=1.307 Ise=14.34f Ikf=.2847 Xtb=1.5 Br=6.092 Nc=2 Isc=0 Ikr=0 Rc=1 "
          "Cjc=7.306p Mjc=.3416 Vjc=.75 Fc=.5 Cje=22.01p Mje=.377 Vje=.75 Tr=46.91n Tf=411.1p Itf=.6 Vtf=1.7 Xtf=3 Rb=10)")

CIRCUIT = """common emitter
{model}
Vcc vcc 0 DC 12
Vin src 0 DC 0 AC 1 SIN(0 10m 1k)
Rs src a 600
Cin a b 1u
R1 vcc b 47k
R2 b 0 10k
Q1 c b e Q2N2222
Rc vcc c 2.2k
Re e 0 470
Ce e 0 100u
Cout c out 10u
RL out 0 10k
.control
op
print v(b) v(e) v(c) i(vcc)
ac dec 50 1 1000Meg
wrdata ac.txt vdb(out)
tran 10u 120m
meas tran vmax max v(out) from=100m to=120m
meas tran vmin min v(out) from=100m to=120m
.endc
.end
"""


def run(model):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "ce.cir").write_text(CIRCUIT.format(model=model), encoding="utf-8")
        log = subprocess.run([NGSPICE, "-b", "ce.cir"], cwd=tmp, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL).stdout
        rows = [list(map(float, line.split())) for line in (tmp / "ac.txt").read_text().splitlines() if line.strip()]
    f = [r[0] for r in rows]
    db = [r[1] for r in rows]
    num = lambda name: float(re.search(rf"{name}\s*=\s*([-+0-9.eE]+)", log).group(1))
    peak = max(db)
    band = [x for x, d in zip(f, db) if d >= peak - 3.0103]
    at = lambda x: min(zip(f, db), key=lambda p: abs(math.log10(p[0] / x)))[1]

    def interp_low():
        for (f1, d1), (f2, d2) in zip(zip(f, db), zip(f[1:], db[1:])):
            if d1 < peak - 3.0103 <= d2:
                return f1 * (f2 / f1) ** ((peak - 3.0103 - d1) / (d2 - d1))

    def interp_high():
        for (f1, d1), (f2, d2) in reversed(list(zip(zip(f, db), zip(f[1:], db[1:])))):
            if d1 >= peak - 3.0103 > d2:
                return f1 * (f2 / f1) ** ((d1 - (peak - 3.0103)) / (d1 - d2))

    high = interp_high()
    vb, ve, vc, icc = num(r"v\(b\)"), num(r"v\(e\)"), num(r"v\(c\)"), num(r"i\(vcc\)")
    return {"v_base_V": round(vb, 3), "v_emitter_V": round(ve, 3), "v_collector_V": round(vc, 3),
            "collector_current_mA": round((-icc - (12 - vb) / 47e3) * 1e3, 3),  # supply current minus the R1 bias current
            "gain_at_1kHz_dB": round(at(1e3), 2), "midband_peak_gain_dB": round(peak, 2), "gain_at_1kHz_V_per_V": round(10 ** (at(1e3) / 20), 1),
            "lower_3dB_corner_Hz": round(interp_low(), 0),
            "upper_3dB_corner_Hz": round(high, -3) if high else None,
            "upper_corner_note": "none up to 1 GHz: flat" if high is None else "",
            "output_amplitude_V_at_10mV_in_after_settling": round((num("vmax") - num("vmin")) / 2, 3)}


def main():
    kicad_model = KICAD_MODEL.read_text().strip()
    demo = run(kicad_model)
    full = run(f".model Q2N2222 {PSPICE}")
    # hand estimate with the textbook rules: Vbe = 0.7 V, beta = 200, VT = 25.85 mV
    vth, rth = 12 * 10 / 57, 47e3 * 10e3 / 57e3
    ie = (vth - 0.7) / (470 + rth / 201)
    gm = ie / 0.02585
    rpi = 200 / gm
    rin = 1 / (1 / 47e3 + 1 / 10e3 + 1 / rpi)
    rl_eff = 1 / (1 / 2.2e3 + 1 / 10e3)
    av = gm * rl_eff * rin / (rin + 600)
    f_in = 1 / (2 * math.pi * (600 + rin) * 1e-6)
    f_out = 1 / (2 * math.pi * (2.2e3 + 10e3) * 10e-6)
    f_e = (gm + 1 / 470) / (2 * math.pi * 100e-6)
    hand = {"Ic_mA": round(ie * 1e3 * 201 / 200, 2), "gm_mS": round(gm * 1e3, 1), "Rin_ohm": round(rin), "midband_gain_V_per_V": round(av, 1),
            "midband_gain_dB": round(20 * math.log10(av), 1), "pole_Cin_Hz": round(f_in), "pole_Cout_Hz": round(f_out, 1), "pole_Ce_Hz": round(f_e),
            "lower_corner_estimate_Hz": round(f_in + f_out + f_e)}
    print(json.dumps({"answers": {
        "with_KiCad_demo_model_bf200_only": demo,
        "with_full_PSpice_Q2N2222_model": full,
        "hand_estimate": hand,
        "judgement": "KiCad's 2n2222.model is only '.model Q2N2222 npn (bf=200)': no junction capacitances or transit time, so ngspice shows a flat response up to 1 GHz and any 'upper bandwidth' from it is meaningless; the lower corner (set by Cin, Ce, Cout) is real. With a complete model the upper -3 dB corner is a few hundred kHz. The circuit is a band-pass, not a low-pass: 'bandwidth' means a lower and an upper corner."},
        "how": "ngspice 41 in batch mode: .op, .ac dec 50 1 1G (corners interpolated at peak - 3.0103 dB, log-frequency), .tran 10 us to 120 ms with the output peak-to-peak read over 100-120 ms; hand estimate = textbook bias (Vbe 0.7 V, beta 200) and open-circuit time-constant poles summed.",
        "tolerance": "Gain at 1 kHz within 1 dB of the demo-model value (42.1 dB; 127 V/V); lower corner 190 Hz +/- 15 %; the hand estimate is only good to about +/- 20 % on gain and a factor 1.5 on the corner. An answer that quotes a precise 'upper bandwidth' for the bf=200 model without a warning is wrong. Collector DC current 2.5 mA +/- 0.4 (demo model)."},
        indent=1))


if __name__ == "__main__":
    main()
