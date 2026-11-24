"""Truth for H4: countershaft with a V-belt pulley overhanging bearing B, a spur pinion between the bearings, two 6207 ball bearings.

Shigley, Mechanical Engineering Design (10th ed.) closed forms, numpy only, no eng-toolkit code:
  * belt: net pull on the shaft is F1 + F2 (both strands pull toward the motor), the torque comes from F1 - F2
  * spur gear: Wt = 2T/d, Wr = Wt tan(20 deg)
  * 3-D statics with cross products (belt horizontal, gear partner below the pinion: Wt horizontal, Wr vertical)
  * DE-Goodman, Eq. (7-7)/(7-8); Marin factors ka, kb, ke (99 %); Kt end-mill keyseat 2.14 / 3.0, well-rounded fillet r/d = 0.1: 1.7 / 1.5;
    notch sensitivity from Neuber constants Eq. (6-35a, b)
  * bearing: ISO 281 basic rating life, radial load only (no axial force on a spur gear)
  * key: Shigley section 7-8, shear Ssy = 0.577 Sy, crushing on half the key height
"""
import json
import math

import numpy as np

P_SHAFT = 5.5e3        # W at the pulley
N = 720.0              # rpm
D_PULLEY = 0.200       # m pitch diameter
RATIO = 4.0            # F1 / F2
Z, M_MOD, ALPHA = 22, 3.0, math.radians(20)
D_GEAR = Z * M_MOD * 1e-3          # 66 mm
XA, XG, XB, XP = 0.0, 0.070, 0.180, 0.265    # bearing A, pinion centre, bearing B, pulley centre (m)
HUB_HALF = 0.030                   # m from the pulley centre plane to the inner hub face (section next to bearing B on the stub side)
KA = 1.5               # service factor: belt-driven machine tool with light shocks
N_DESIGN = 2.0
SUT, SY = 630.0, 530.0 # MPa, AISI 1045 cold drawn (Shigley Table A-20 gives Sut 630, Sy 530)
KE = 0.814             # 99 % reliability
SEATS = {"bearing_seat_B": 35.0, "pinion_seat": 40.0, "pulley_stub": 30.0}   # mm, the person's drawing
C_6207, C0_6207 = 25.5e3, 15.3e3      # SKF catalogue 6207: 35 x 72 x 17
C_6307 = 33.2e3                       # SKF catalogue 6307: 35 x 80 x 21
L_REQ = 10000.0        # h required life of the machine

T = P_SHAFT / (N * 2 * math.pi / 60)             # N m
F_net = T / (D_PULLEY / 2)                       # F1 - F2
F2 = F_net / (RATIO - 1)
F1 = RATIO * F2
F_belt = F1 + F2                                 # net pull on the shaft
WT = 2 * T / D_GEAR
WR = WT * math.tan(ALPHA)


def statics(same_direction=True):
    """Reactions and resultant moments. x along the shaft; belt pull along -y; pinion tangential force along -y (adds) or +y; radial +z."""
    f_gear = np.array([0.0, -WT if same_direction else WT, WR])
    f_belt = np.array([0.0, -F_belt, 0.0])
    p_gear = np.array([XG, 0.0, -D_GEAR / 2])
    p_belt = np.array([XP, 0.0, 0.0])
    # moments about A: p x F + [XB,0,0] x [0,By,Bz] = 0
    mom = np.cross(p_gear, f_gear) + np.cross(p_belt, f_belt)
    by, bz = -mom[2] / XB, mom[1] / XB
    ay = -(f_gear[1] + f_belt[1] + by)
    az = -(f_gear[2] + f_belt[2] + bz)
    a_vec = np.array([0.0, ay, az])

    def moment_at(x, side="L"):
        sec = np.array([x, 0.0, 0.0])
        tot = np.cross(np.array([0.0, 0.0, 0.0]) - sec, a_vec)
        if x > XG or (x == XG and side == "R"):
            tot = tot + np.cross(p_gear - sec, f_gear)
        if x > XB:
            tot = tot + np.cross(np.array([XB, 0.0, 0.0]) - sec, np.array([0.0, by, bz]))
        return math.hypot(tot[1], tot[2])

    return {"RA": math.hypot(ay, az), "RB": math.hypot(by, bz), "moment_at": moment_at, "ay": ay, "az": az, "by": by, "bz": bz}


def marin(d_mm):
    ka = 4.51 * SUT ** -0.265
    kb = 1.24 * d_mm ** -0.107 if d_mm <= 51 else 1.51 * d_mm ** -0.157
    return ka * kb * KE * 0.5 * SUT, ka, kb


def sqrt_a(tors):
    s = SUT / 6.895
    return 0.190 - 2.51e-3 * s + 1.35e-5 * s ** 2 - 2.67e-8 * s ** 3 if tors else 0.246 - 3.08e-3 * s + 1.51e-5 * s ** 2 - 2.67e-8 * s ** 3


def kf_of(kt, r_mm, tors):
    q = 1.0 / (1.0 + sqrt_a(tors) / math.sqrt(r_mm / 25.4))
    return 1 + q * (kt - 1), q


def n_f(d_mm, m_nm, t_nm, kt_b, kt_s, r_over_d):
    se, _, _ = marin(d_mm)
    kf, _ = kf_of(kt_b, r_over_d * d_mm, False)
    kfs, _ = kf_of(kt_s, r_over_d * d_mm, True)
    d = d_mm * 1e-3
    inv = 16 / (math.pi * d ** 3) * (2 * kf * m_nm / (se * 1e6) + math.sqrt(3) * kfs * t_nm / (SUT * 1e6))
    return 1 / inv


def min_d(m_nm, t_nm, kt_b, kt_s, r_over_d):
    lo, hi = 8.0, 200.0
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if n_f(mid, m_nm, t_nm, kt_b, kt_s, r_over_d) < N_DESIGN else (lo, mid)
    return hi


def din6885(d):
    for hi, w, h in [(12, 4, 4), (17, 5, 5), (22, 6, 6), (30, 8, 7), (38, 10, 8), (44, 12, 8), (50, 14, 9), (58, 16, 10)]:
        if d <= hi:
            return w, h


def life(c, p):
    return (c / p) ** 3 * 1e6 / (60 * N)


def main():
    out = {}
    for label, same in (("belt_and_gear_forces_add", True), ("belt_and_gear_forces_oppose", False)):
        s = statics(same)
        sections = {
            "pinion_keyseat_at_gear_plane": (s["moment_at"](XG, "L"), s["moment_at"](XG, "R"), T, 2.14, 3.0, 0.02, SEATS["pinion_seat"]),
            "bearing_B_seat_fillet": (s["moment_at"](XB), s["moment_at"](XB), T, 1.7, 1.5, 0.1, SEATS["bearing_seat_B"]),
            "pulley_keyseat_at_inner_hub_face": (s["moment_at"](XP - HUB_HALF), s["moment_at"](XP - HUB_HALF), T, 2.14, 3.0, 0.02, SEATS["pulley_stub"]),
        }
        rows = {}
        for name, (ml, mr, tq, ktb, kts, rd, d_mm) in sections.items():
            m = max(ml, mr)
            rows[name] = {"M_Nm": round(m, 1), "T_Nm": round(tq, 1), "d_drawn_mm": d_mm, "n_f_at_drawn_d": round(n_f(d_mm, m, tq, ktb, kts, rd), 2),
                          "min_d_for_n2_mm": round(min_d(m, tq, ktb, kts, rd), 1)}
        pa, pb = KA * s["RA"], KA * s["RB"]
        out[label] = {"RA_N": round(s["RA"], 0), "RB_N": round(s["RB"], 0), "sections": rows,
                      "L10h_A_6207": round(life(C_6207, pa)), "L10h_B_6207": round(life(C_6207, pb)), "L10h_B_6307": round(life(C_6307, pb)),
                      "L10h_B_6207_no_service_factor": round(life(C_6207, s["RB"]))}
    w, h = din6885(SEATS["pulley_stub"])
    fk = T / (SEATS["pulley_stub"] * 1e-3 / 2)
    ssy = 0.577 * SY
    l_shear = fk / (w * 1e-3 * ssy * 1e6 / N_DESIGN) * 1e3
    l_crush = fk / ((h / 2) * 1e-3 * SY * 1e6 / N_DESIGN) * 1e3
    naive = {"if_belt_net_pull_taken_as_F1_minus_F2": round(F_net, 0), "correct_net_pull_F1_plus_F2": round(F_belt, 0),
             "note": "using F1 - F2 (the tangential force) instead of F1 + F2 as the shaft load underestimates the overhang load by 40 %"}
    answers = {
        "torque_Nm": round(T, 1), "belt_F1_N": round(F1, 0), "belt_F2_N": round(F2, 0), "belt_net_pull_on_shaft_N": round(F_belt, 0),
        "pinion_pitch_diameter_mm": D_GEAR * 1e3, "pinion_Wt_N": round(WT, 0), "pinion_Wr_N": round(WR, 0),
        "worst_case_belt_and_gear_add": out["belt_and_gear_forces_add"], "other_case_forces_oppose": out["belt_and_gear_forces_oppose"],
        "pulley_key_DIN6885": f"{w} x {h}", "pulley_key_min_length_mm": {"shear_n2": round(l_shear, 1), "crushing_n2": round(l_crush, 1)},
        "common_mistake": naive,
        "required_life_h": L_REQ,
        "verdict": (f"The shaft is fatigue-safe at all three drawn diameters (every n_f above 2, the smallest {min(r['n_f_at_drawn_d'] for r in out['belt_and_gear_forces_add']['sections'].values())}). "
                    f"The bearing is not: 6207 at B gives L10h {out['belt_and_gear_forces_add']['L10h_B_6207']} h with the 1.5 service factor, below the required {L_REQ:.0f} h "
                    f"(without the factor {out['belt_and_gear_forces_add']['L10h_B_6207_no_service_factor']} h); 6307 gives {out['belt_and_gear_forces_add']['L10h_B_6307']} h, which passes. "
                    "The overhang load (F1 + F2, not F1 - F2) decides the bearing."),
    }
    print(json.dumps({"answers": answers,
                      "how": "Shigley closed forms in numpy: 3-D statics by cross products, DE-Goodman with Marin factors (ka from Sut, kb from d, 99 % reliability) and Neuber-based notch sensitivity, ISO 281 L10 with C from the SKF catalogue",
                      "tolerance": "Forces and reactions within 2 %, moments within 3 %, n_f within 10 % (Kf choices differ slightly between textbooks), L10h within 10 %. Taking the belt pull as F1 - F2 is wrong. Verdict on the 6207 life must match; the assistant must say which direction assumption it used."},
                     indent=1))


if __name__ == "__main__":
    main()
