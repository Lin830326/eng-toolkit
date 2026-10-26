"""Truth for F3: velocity loop of a Maxon RE 40 (148877) DC servo motor with a load inertia, PI/PID for 50 ms rise, 1 kHz digital.

Independent of eng-toolkit: scipy.signal for the plant, closed loop, margins from a dense frequency grid, and a sampled-data simulation
(ZOH plant + Tustin controller + 1 sample computation delay). Grade a given set of gains with:
    GAINS="kp,ki,kd" python uat/truth/F3_servo_velocity_pid.py       (kp in V/(rad/s), ki in V/(rad/s)/s, kd in V/(rad/s^2))
Prints JSON {answers, how, tolerance}.
"""
import json
import os
import numpy as np
from scipy import signal

# Maxon RE 40, 148877, 24 V: datasheet values
R, L = 0.299, 0.082e-3
KT = 30.2e-3                        # N m / A
KE = 1.0 / (317 * 2 * np.pi / 60)   # speed constant 317 rpm/V  ->  V s/rad  (0.03013)
J_ROTOR = 142e-7                    # 142 g cm^2
J_LOAD = 1.2e-4                     # load reflected to the motor shaft (the user's number)
J = J_ROTOR + J_LOAD
# viscous friction from the no-load point: 137 mA at 7580 rpm (24 V): T_fric = Kt*I0 over omega0
W0 = 7580 * 2 * np.pi / 60
B = KT * 0.137 / W0
TS = 1e-3
STEP = 500 * 2 * np.pi / 60         # 500 rpm reference step (the usual speed change)
V_MAX, I_MAX = 24.0, 10.0           # supply and drive current limit

# plant omega/V
num_p = [KT]
den_p = [L * J, R * J + L * B, R * B + KT * KE]
plant = signal.TransferFunction(num_p, den_p)
dc_gain = KT / (R * B + KT * KE)
tau_m = R * J / (R * B + KT * KE)


def margins(num_l, den_l):
    w = np.logspace(-1, 4, 20000)
    _, h = signal.freqs(num_l, den_l, w)
    mag = np.abs(h)
    ph = np.unwrap(np.angle(h)) * 180 / np.pi
    idx = np.where(np.diff(np.sign(mag - 1)) != 0)[0]
    wc = w[idx[0]] if len(idx) else None
    pm = 180 + ph[idx[0]] if len(idx) else None
    idp = np.where(np.diff(np.sign(ph + 180)) != 0)[0]
    gm = -20 * np.log10(mag[idp[0]]) if len(idp) else None
    return wc, pm, gm


def cl_metrics(t, y, final):
    y = np.asarray(y) / final
    t10 = t[np.argmax(y >= 0.1)]
    t90 = t[np.argmax(y >= 0.9)]
    ov = max(0.0, (y.max() - 1) * 100)
    out = np.where(np.abs(y - 1) > 0.02)[0]
    ts = t[min(out[-1] + 1, len(t) - 1)] if len(out) else 0.0
    return float(t90 - t10), float(ov), float(ts)


def continuous(kp, ki, kd):
    # C = (kd s^2 + kp s + ki)/s
    num_c = np.array([kd, kp, ki])
    den_c = np.array([1.0, 0.0])
    num_l = np.polymul(num_c, num_p)
    den_l = np.polymul(den_c, den_p)
    den_t = np.polyadd(den_l, num_l)
    t = np.linspace(0, 0.5, 20001)
    _, y = signal.step(signal.TransferFunction(num_l, den_t), T=t)
    tr, ov, ts = cl_metrics(t, y, 1.0)
    wc, pm, gm = margins(num_l, den_l)
    poles = np.roots(den_t)
    return dict(rise_10_90_ms=tr * 1e3, overshoot_pct=ov, settling_2pct_ms=ts * 1e3, crossover_rad_s=wc, phase_margin_deg=pm,
                gain_margin_dB=gm, stable=bool(np.all(poles.real < 0)))


def sampled(kp, ki, kd, delay=1, limit=True, step=None):
    """discrete loop: ZOH plant (state space), PI(D) with Tustin integrator, `delay` samples of computation delay,
    voltage and current saturation (anti-windup by clamping)"""
    A, Bm, C, D = signal.tf2ss(num_p, den_p)
    Ad, Bd, Cd, Dd, _ = signal.cont2discrete((A, Bm, C, D), TS, method="zoh")
    n = int(0.6 / TS)
    x = np.zeros((A.shape[0], 1))
    integ, prev_e = 0.0, 0.0
    u_queue = [0.0] * delay
    ys, us = [], []
    ref = STEP if step is None else step
    for k in range(n):
        y = float((Cd @ x)[0, 0])
        e = ref - y
        integ += 0.5 * ki * TS * (e + prev_e)
        d = kd * (e - prev_e) / TS if kd else 0.0
        u = kp * e + integ + d
        u_sat = u
        if limit:
            # current limit through the voltage: V = Ke*omega + R*I  -> |V - Ke*omega| <= R*I_MAX
            lo, hi = KE * y - R * I_MAX, KE * y + R * I_MAX
            u_sat = min(max(u, max(lo, -V_MAX)), min(hi, V_MAX))
            integ += (u_sat - u)   # clamp the integrator
        prev_e = e
        u_queue.append(u_sat)
        u_apply = u_queue.pop(0)
        x = Ad @ x + Bd * u_apply
        ys.append(y)
        us.append(u_apply)
    t = np.arange(n) * TS
    tr, ov, ts = cl_metrics(t, ys, ref)
    return dict(rise_10_90_ms=tr * 1e3, overshoot_pct=ov, settling_2pct_ms=ts * 1e3, peak_voltage_V=float(np.max(np.abs(us))),
                peak_current_A=float(np.max(np.abs(np.array(us) - KE * np.array(ys)) / R)),
                final_speed_rpm=float(ys[-1] * 60 / 2 / np.pi))


def rnd(d):
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()}


# reference design: PI with the zero on the mechanical pole, closed-loop time constant 50 ms / 2.2
tau_cl = 0.050 / 2.2
kp_ref = tau_m / (tau_cl * dc_gain)
ki_ref = kp_ref / tau_m
ref_cont = continuous(kp_ref, ki_ref, 0.0)
ref_samp = sampled(kp_ref, ki_ref, 0.0)
ref_samp_nolimit = sampled(kp_ref, ki_ref, 0.0, limit=False)
t_ol = np.linspace(0, 0.3, 6001)
_, y_ol = signal.step(plant, T=t_ol)
tr_ol, ov_ol, ts_ol = cl_metrics(t_ol, y_ol, dc_gain)

ans = {
    "plant_dc_gain_rad_s_per_V": round(dc_gain, 2),
    "plant_dominant_time_constant_ms": round(tau_m * 1e3, 2),
    "plant_electrical_time_constant_ms": round(L / R * 1e3, 3),
    "viscous_friction_from_no_load_Nm_s_rad": B,
    "Ke_V_s_per_rad": round(KE, 5),
    "open_loop_rise_time_ms": round(tr_ol * 1e3, 1),
    "reference_PI_kp_V_per_rad_s": round(kp_ref, 4),
    "reference_PI_ki_V_per_rad_s_per_s": round(ki_ref, 3),
    "reference_PI_Ti_ms": round(tau_m * 1e3, 1),
    "reference_continuous_loop": rnd(ref_cont),
    "reference_sampled_1kHz_with_1_sample_delay_and_current_limit": rnd(ref_samp),
    "reference_sampled_1kHz_no_limit": rnd(ref_samp_nolimit),
    "reference_sampled_1kHz_with_limit_1000rpm_step": rnd(sampled(kp_ref, ki_ref, 0.0, step=1000 * 2 * np.pi / 60)),
    "current_limited_min_rise_10_90_ms_for_500rpm": round(0.8 * STEP / (KT * I_MAX / J) * 1e3, 1),
    "current_limited_min_rise_10_90_ms_for_1000rpm": round(0.8 * 2 * STEP / (KT * I_MAX / J) * 1e3, 1),
    "spec": "10-90 % rise time 50 ms (accept 40-60 ms), overshoot < 10 %, stable, phase margin >= 55 deg",
    "kd_needed": False,
}
if os.environ.get("GAINS"):
    kp, ki, kd = [float(x) for x in os.environ["GAINS"].split(",")][:3]
    ans["graded_gains"] = {"kp": kp, "ki": ki, "kd": kd, "continuous": rnd(continuous(kp, ki, kd)), "sampled_1kHz": rnd(sampled(kp, ki, kd))}
print(json.dumps({"answers": ans,
 "how": ("Maxon RE 40 148877 datasheet: R 0.299 ohm, L 0.082 mH, Kt 30.2 mNm/A, speed constant 317 rpm/V (so Ke = 0.03013 V s/rad), "
         "J_rotor 142 g cm^2 plus 1.2e-4 kg m^2 load, viscous b from the no-load point (137 mA, 7580 rpm). Plant omega/V = Kt/(L J s^2 + (R J + L b)s + R b + Kt Ke). "
         "Closed loop and margins by scipy.signal; sampled-data check with ZOH plant, Tustin integrator, one sample delay, 24 V / 10 A limits and a 500 rpm step (and 1000 rpm for the saturation case). "
         "Reference design = PI with Ti = mechanical time constant (pole cancellation), closed-loop time constant 50 ms / 2.2. Grade other gains by env GAINS=kp,ki,kd."),
  "tolerance": ("Any gain set is right if the continuous loop gives 10-90 % rise 40-60 ms, overshoot < 10 %, stable, phase margin >= 55 deg, and the 1 kHz sampled loop still meets "
                "the same. Reference gains within a factor of 1.5 are fine. Plant numbers: dc gain and tau within 3 %. A D term is not needed; the electrical pole (0.27 ms) is above the 1 kHz Nyquist, "
                "so derivative action would only add noise. If Ke is entered as 3.155 V/krpm taken as V s/rad the plant is wrong by 1000x (unit trap)."),
 }, indent=1))
