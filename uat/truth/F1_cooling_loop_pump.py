"""Truth for F1: closed water cooling loop, pump curve vs system curve, two parallel branches.

Independent of eng-toolkit: own Colebrook (scipy brentq), own Darcy-Weisbach, PCHIP pump curve (monotone, as a datasheet
read-off), operating point by brentq. Prints JSON {answers, how, tolerance}.
"""
import json
import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.optimize import brentq

RHO, MU, CP = 994.0, 0.72e-3, 4178.0          # water at ~35 C
EPS = 0.045e-3                                # commercial steel
G = 9.81

# pump read off the catalogue curve: Q [m3/h], H [m], eta [-]
PUMP = [(0, 12.4, 0.0), (1, 12.1, 0.22), (2, 11.5, 0.36), (3, 10.5, 0.44), (4, 9.0, 0.46),
        (5, 7.0, 0.43), (6, 4.4, 0.34)]
q_pts = np.array([p[0] for p in PUMP]) / 3600.0
H_pump = PchipInterpolator(q_pts, [p[1] for p in PUMP])
eta_pump = PchipInterpolator(q_pts, [p[2] for p in PUMP])

# pipes: ID from ASME B36.10 schedule 40
D40, D32, D25 = 0.0409, 0.03504, 0.02664
K_ELBOW = 0.75
K_TEE = 1.0          # each branch has a tee at the inlet and at the outlet, K referred to the branch velocity
K_PHE_DP, Q_PHE = 70e3, 3.0 / 3600           # plate heat exchanger: 45 kPa at 3.0 m3/h, quadratic


def colebrook(re, d):
    if re < 2300:
        return 64.0 / max(re, 1.0)
    rr = EPS / d
    f = brentq(lambda f: 1 / np.sqrt(f) + 2 * np.log10(rr / 3.7 + 2.51 / (re * np.sqrt(f))), 0.005, 0.2)
    return f


def dp_pipe(q, d, length, k_total):
    """pressure drop [Pa] of a pipe of ID d, length, plus K minor losses, at flow q [m3/s]"""
    if q <= 0:
        return 0.0
    v = q / (np.pi * d * d / 4)
    re = RHO * v * d / MU
    f = colebrook(re, d)
    return (f * length / d + k_total) * 0.5 * RHO * v * v


KV_A = 4.0           # balancing valve on branch A, set position: Kv 4.0 m3/h per sqrt(bar)


def dp_valve_kv(q, kv):
    return 1e5 * (q * 3600 / kv) ** 2 * (RHO / 1000.0)


def dp_a(q):
    return dp_pipe(q, D32, 15.0, 3 * K_ELBOW + 2 * K_TEE) + dp_valve_kv(q, KV_A)


def dp_b(q):
    return dp_pipe(q, D25, 28.0, 5 * K_ELBOW + 2 * K_TEE)




def split(dp):
    """flows in A and B for a common pressure drop dp"""
    qa = brentq(lambda q: dp_a(q) - dp, 1e-9, 0.02)
    qb = brentq(lambda q: dp_b(q) - dp, 1e-9, 0.02)
    return qa, qb


def dp_parallel_total(q):
    dp = brentq(lambda p: sum(split(p)) - q, 1e-3, 5e6)
    return dp


def dp_trunk(q):
    return dp_pipe(q, D40, 60.0, 6 * K_ELBOW) + K_PHE_DP * (q / Q_PHE) ** 2


def dp_system(q):
    return dp_trunk(q) + dp_parallel_total(q)


def resid(q):
    return RHO * G * float(H_pump(q)) - dp_system(q)


q_op = brentq(resid, 1e-5, q_pts[-1] * 0.999)
H_op = float(H_pump(q_op))
eta_op = float(eta_pump(q_op))
dp_par = dp_parallel_total(q_op)
qa, qb = split(dp_par)
hydraulic_kw = RHO * G * q_op * H_op / 1000
shaft_kw = hydraulic_kw / eta_op

# required flows for 15 kW at 5 K (A 9 kW, B 6 kW)
need_total = 15000 / (CP * 5) / RHO
need_a, need_b = 9000 / (CP * 5) / RHO, 6000 / (CP * 5) / RHO
dT_total = 15000 / (RHO * q_op * CP)
dT_a, dT_b = 9000 / (RHO * qa * CP), 6000 / (RHO * qb * CP)

# curves for the plot check
qs = np.linspace(0, 6, 13) / 3600
curve = [{"Q_m3h": round(q * 3600, 2), "pump_H_m": round(float(H_pump(q)), 2), "system_H_m": round(dp_system(q) / RHO / G, 2)} for q in qs[1:]]

ans = {
    "operating_flow_m3_per_h": round(q_op * 3600, 3),
    "operating_head_m": round(H_op, 2),
    "pump_efficiency_at_op": round(eta_op, 3),
    "pump_shaft_power_kW": round(shaft_kw, 3),
    "required_total_flow_m3_per_h_for_15kW_5K": round(need_total * 3600, 3),
    "flow_is_enough_total": bool(q_op > need_total),
    "branch_A_flow_m3_per_h": round(qa * 3600, 3),
    "branch_B_flow_m3_per_h": round(qb * 3600, 3),
    "branch_A_required_m3_per_h_9kW": round(need_a * 3600, 3),
    "branch_B_required_m3_per_h_6kW": round(need_b * 3600, 3),
    "branch_A_enough": bool(qa >= need_a),
    "branch_B_enough": bool(qb >= need_b),
    "temperature_rise_A_K": round(dT_a, 2),
    "temperature_rise_B_K": round(dT_b, 2),
    "temperature_rise_total_K": round(dT_total, 2),
    "head_loss_trunk_m": round(dp_trunk(q_op) / RHO / G, 2),
    "head_loss_parallel_m": round(dp_par / RHO / G, 2),
    "PHE_dp_kPa": round(K_PHE_DP * (q_op / Q_PHE) ** 2 / 1000, 1),
    "curve_table_for_plot": curve,
}
print(json.dumps({
    "answers": ans,
    "how": ("Water 994 kg/m3, 0.72 mPa s, cp 4178 (35 C). Colebrook-White (own brentq) with eps 0.045 mm, Darcy-Weisbach. Trunk: 60 m DN40 sch40 (ID 40.9 mm), "
            "6 elbows K 0.75, plate HX dp 45 kPa at 3.0 m3/h scaled with Q^2. Branch A: 15 m DN32 (ID 35.04), 3 elbows, 2 tees K 1.0, balancing valve Kv 4.0 m3/h (dp[bar] = (Q[m3/h]/Kv)^2 * rho/1000). "
            "Branch B: 28 m DN25 (ID 26.64), 5 elbows, 2 tees. Closed loop: static head 0. Pump: PCHIP through the datasheet points; operating point = "
            "root of pump head minus (trunk + parallel-branch) head. Required flow Q = P/(rho cp dT)."),
    "tolerance": ("Operating flow and branch flows +-8 % (elbow/tee K and PHE exponent are modelling choices: K 0.5-0.9 per elbow moves the flow by ~2 %); head +-0.7 m; "
                  "the verdicts (total flow enough, which branch is short) must match. If the assistant uses ID 40 mm instead of 40.9 mm the flow is still within tolerance."),
}, indent=1))
