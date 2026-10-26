"""Truth for E4: 24 V (up to 28 V) to 5 V, 3 A synchronous buck at 500 kHz: inductor, ripple, output capacitor, efficiency.

Closed-form design equations (Erickson/Maksimovic, Mohan: D, L = (Vin-Vo) D / (f dI), dV = dI/(8 f C) + dI ESR, loss budget with
conduction, DCR, V-I overlap, gate charge and dead-time terms) plus a standalone ngspice switching simulation (ideal switches with
the Rds(on) values, body diode in the dead time, DCR, ESR). No eng-toolkit code.

Parts the person has (facts): high-side Rds(on) 12 mohm, low-side 6 mohm, tr = tf = 10 ns (V-I overlap), Qg 8 nC each at 5 V gate drive,
dead time 20 ns per edge, body diode 0.7 V, inductor 10 uH with DCR 20 mohm and Isat 6 A, output capacitor ESR 5 mohm, ripple target 30 %
of Io for the inductor and 20 mV peak-to-peak at the output.
"""
import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path

NGSPICE = os.environ.get("NGSPICE", r"C:/Users/USER/miniconda3/envs/eng-eda/Library/bin/ngspice_con.exe")
VO, IO, F = 5.0, 3.0, 500e3
RHS, RLS, DCR, ESR, L, C = 12e-3, 6e-3, 20e-3, 5e-3, 10e-6, 47e-6
TR = TF = 10e-9
QG, VG, TDEAD, VBODY = 8e-9, 5.0, 20e-9, 0.7


def duty(vin):
    return (VO + IO * (RLS + DCR)) / (vin - IO * (RHS - RLS))


NETLIST = """sync buck
.model SWH sw(vt=0.5 vh=0 ron=12m roff=10meg)
.model SWL sw(vt=0.5 vh=0 ron=6m roff=10meg)
.model DBODY d(is=1e-9 n=1.2 rs=10m)
Vin vin 0 DC {vin}
Vg1 g1 0 PULSE(0 1 0 1n 1n {ton} 2u)
Vg2 g2 0 PULSE(0 1 {ton2} 1n 1n {toff2} 2u)
S1 vin sw g1 0 SWH
S2 sw 0 g2 0 SWL
D2 0 sw DBODY
L1 sw lx 10u ic={io}
Rdcr lx out 20m
C1 out 0 47u ic={vo}
Resr out co 5m
Rload co 0 {rload}
.control
tran 10n 3m 0 10n uic
meas tran imax max i(l1) from=2.9m to=3m
meas tran imin min i(l1) from=2.9m to=3m
meas tran iavg avg i(l1) from=2.9m to=3m
meas tran vmax max v(co) from=2.9m to=3m
meas tran vmin min v(co) from=2.9m to=3m
meas tran vavg avg v(co) from=2.9m to=3m
let pinv = -i(vin)*{vin}
let poutv = v(co)*v(co)/{rload}
meas tran pin avg pinv from=2.9m to=3m
meas tran pout avg poutv from=2.9m to=3m
.endc
.end
"""


def simulate(vin):
    d = duty(vin)
    ton = d * 2e-6
    net = NETLIST.format(vin=vin, ton=f"{ton:.6e}", ton2=f"{ton + TDEAD:.6e}", toff2=f"{2e-6 - ton - 2 * TDEAD:.6e}", io=IO, vo=VO, rload=VO / IO)
    with tempfile.TemporaryDirectory() as t:
        Path(t, "b.cir").write_text(net)
        log = subprocess.run([NGSPICE, "-b", "b.cir"], cwd=t, capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL).stdout
    g = lambda k: float(re.search(rf"^{k}\s*=\s*([-+0-9.eE]+)", log, re.M).group(1))
    return {"duty": round(d, 4), "inductor_ripple_pp_A": round(g("imax") - g("imin"), 3), "inductor_peak_A": round(g("imax"), 3),
            "inductor_avg_A": round(g("iavg"), 3), "vout_avg_V": round(g("vavg"), 3), "vout_ripple_pp_mV": round((g("vmax") - g("vmin")) * 1e3, 1),
            "efficiency_conduction_and_deadtime_only_percent": round(100 * g("pout") / g("pin"), 2), "pin_W": round(g("pin"), 3), "pout_W": round(g("pout"), 3)}


def closed_form(vin):
    d = duty(vin)
    ripple = (vin - VO) * d / (F * L)  # volt-second balance, ideal slope
    ipk = IO + ripple / 2
    irms2 = IO ** 2 + ripple ** 2 / 12
    return d, ripple, ipk, irms2


def main():
    d24, r24, pk24, irms2_24 = closed_form(24.0)
    d28, r28, pk28, _ = closed_form(28.0)
    l_for_30pct = (24 - VO) * (VO / 24) / (F * 0.3 * IO)
    l_for_30pct_28 = (28 - VO) * (VO / 28) / (F * 0.3 * IO)
    # output capacitor for 20 mV pp including ESR
    c_min = r28 / (8 * F * (0.020 - r28 * ESR))
    # loss budget at 24 V, full load
    hs = irms2_24 * RHS * d24
    ls = irms2_24 * RLS * (1 - d24)
    dcr = irms2_24 * DCR
    overlap = 0.5 * 24 * IO * (TR + TF) * F
    gate = 2 * QG * VG * F
    dead = VBODY * IO * 2 * TDEAD * F
    esr = (r24 / math.sqrt(12)) ** 2 * ESR
    total = hs + ls + dcr + overlap + gate + dead + esr
    pout = VO * IO
    answers = {
        "duty_at_24V": round(d24, 4),
        "ideal_duty_Vo_over_Vin": round(VO / 24, 4),
        "inductance_for_30pct_ripple_at_24V_uH": round(l_for_30pct * 1e6, 2),
        "inductance_for_30pct_ripple_at_28V_uH": round(l_for_30pct_28 * 1e6, 2),
        "standard_value_to_pick_uH": 10,
        "ripple_current_10uH_24V_pp_A": round(r24, 3), "ripple_current_10uH_28V_pp_A": round(r28, 3),
        "ripple_percent_of_Io_at_28V": round(100 * r28 / IO, 1),
        "inductor_peak_current_24V_A": round(pk24, 3), "inductor_peak_current_28V_A": round(pk28, 3),
        "inductor_rating_needed": "Isat well above 3.5 A (peak 3.4 to 3.5 A plus margin); the 6 A part is fine",
        "output_capacitor_min_for_20mVpp_28V_uF": round(c_min * 1e6, 1),
        "loss_budget_W_at_24V_3A": {"high_side_conduction": round(hs, 4), "low_side_conduction": round(ls, 4), "inductor_DCR": round(dcr, 4),
                                    "vi_overlap": round(overlap, 4), "gate_drive": round(gate, 4), "dead_time_body_diode": round(dead, 4),
                                    "capacitor_ESR": round(esr, 5), "total": round(total, 4)},
        "efficiency_percent_at_24V": round(100 * pout / (pout + total), 2),
        "efficiency_if_switching_losses_forgotten_percent": round(100 * pout / (pout + hs + ls + dcr + esr), 2),
        "simulation_24V": simulate(24.0), "simulation_28V": simulate(28.0),
        "converter_tool_caveat": "The electric_converter tool models a NON-synchronous buck (switch + diode with a constant drop): a synchronous stage is represented by giving the low-side conduction as an equivalent drop (diode_drop about Io*Rds_ls = 18 mV), which reproduces the low-side conduction loss but not the dead-time loss. A correct reply says this.",
    }
    print(json.dumps({"answers": answers,
                      "how": "D = (Vo + Io (Rls + DCR)) / (Vin - Io (Rhs - Rls)); dI = (Vin - Vo) D / (f L); C from dV = dI/(8 f C) + dI ESR; losses: I_rms^2 R terms with the triangular-ripple rms correction, 1/2 Vin Io (tr + tf) f overlap, 2 Qg Vg f gate drive, Vf Io 2 t_dead f dead time; ngspice 41 transient of the switched circuit (3 ms from the ideal start-up state, last 100 us measured, 10 ns step).",
                      "tolerance": "Inductor ripple with 10 uH at 24 V: 0.79 to 0.81 A pp (26 to 27 % of Io; 0.805 with the loss-corrected duty, 0.79 with the ideal D = Vo/Vin); at 28 V 0.82 to 0.84 A. Simulation and formula agree within 3 %. L for 30 % ripple 8.8 uH (round to the standard 10 uH). Peak current 3.4 A (3.42 A at 28 V); the saturation current of the part must exceed that. Output capacitor 13 to 15 uF minimum for 20 mV pp with 5 mohm ESR; the 47 uF part gives about 4 to 8 mV pp. Efficiency 95.6 % +/- 1 point at 24 V and 3 A once overlap (0.36 W), gate drive (0.04 W) and dead time (0.04 W) are included. A figure of 97.5 % or more means the switching losses were left out (a conduction + DCR + dead-time simulation gives 97.9 %; formulas without any switching term 98.4 %): the answer must say a lossless-switch simulation cannot give efficiency. Output voltage in the open-loop simulation at the fixed duty is about 4.99 V, not exactly 5 V."}, indent=1))


if __name__ == "__main__":
    main()
