"""Independent truth for M1: output shaft of a single-stage helical reducer, 30 kW / 1470 rpm motor, i = 71/20.

Shigley, Mechanical Engineering Design (10th ed.) closed forms, numpy only, no eng-toolkit code:
  * forces on a helical gear: Wt = 2T/d, Wr = Wt tan(an)/cos(b), Wa = Wt tan(b)
  * DE-Goodman diameter, Eq. (7-8); Marin factors Eq. (6-19), (6-20), Table 6-5 (reliability), ke = 0.814 for 99 %
  * Kt for end-mill keyseat 2.14 / 3.0 and well-rounded shoulder fillet r/d = 0.1: 1.7 / 1.5 (Table 7-1); notch sensitivity from
    Neuber constants Eq. (6-35a, b) -> Kf = 1 + q (Kt - 1)
  * bearing: ISO 281 basic rating life, L10 = (C/P)^3 10^6 rev, P = X Fr + Y Fa with e/Y from f0 Fa/C0 (SKF catalogue)
  * key: Shigley section 7-8: shear Ssy = 0.577 Sy, crushing on half the height
"""
import json
import math

import numpy as np

# ---------------- inputs ----------------
P_motor = 30e3            # W
n_motor = 1470.0          # rpm
z1, z2 = 20, 71
mn_mm, beta_deg, alpha_n_deg = 4.0, 15.0, 20.0
eta = 0.985               # one helical stage with bearings
KA = 1.25                 # service factor on all loads (medium shock conveyor), the person's fact
L = 300.0e-3              # m bearing centre to bearing centre
a = 120.0e-3              # m from bearing A (locating, motor side) to gear centre plane
b_sh = 40.0e-3            # m gear centre to the shoulder fillet on the output (torque carrying) side
n_design = 2.0            # design factor (fatigue)
Sut, Sy = 630.0, 370.0    # MPa, C45+QT (EN 10083-2) minimum for 40 < d <= 100 mm
reliability_ke = 0.814    # 99 %

beta = math.radians(beta_deg)
an = math.radians(alpha_n_deg)
i = z2 / z1
d2 = z2 * mn_mm / math.cos(beta) * 1e-3        # m
n_out = n_motor / i
w_out = n_out * 2 * math.pi / 60
T_nom = P_motor * eta / w_out                  # N m
T = KA * T_nom
Wt = 2 * T / d2
Wr = Wt * math.tan(an) / math.cos(beta)
Wa = Wt * math.tan(beta)
r2 = d2 / 2

# statics: x along the shaft, A at 0, B at L; mesh point on top (z = +r2); tangential force along y;
# radial force on the wheel is -z (toward the axis); axial force points to A (-x), the locating-bearing side
F = np.array([-Wa, Wt, -Wr])
pos = np.array([a, 0.0, r2])
# reactions: A (Ax, Ay, Az) at x = 0, B (0, By, Bz) at x = L. Moments about A: pos x F + [L,0,0] x [0,By,Bz] = 0
Mg = np.cross(pos, F)
By = -Mg[2] / L           # z comp of [L,0,0]x[0,By,Bz] = L*By
Bz = Mg[1] / L            # y comp = -L*Bz
Ax = -F[0]
Ay = -(F[1] + By)
Az = -(F[2] + Bz)
RA = math.hypot(Ay, Az)
RB = math.hypot(By, Bz)
A_vec = np.array([Ax, Ay, Az])


def moment_at(x, side):
    """resultant bending moment on the shaft at x, from the A side; side 'L' just left of the gear plane, 'R' just right"""
    sec = np.array([x, 0.0, 0.0])
    tot = np.cross(np.array([0.0, 0.0, 0.0]) - sec, A_vec)
    if x > a or (x == a and side == "R"):
        tot = tot + np.cross(pos - sec, F)
    return math.hypot(tot[1], tot[2])


M_left = moment_at(a, "L")
M_right = moment_at(a, "R")
M_gear = max(M_left, M_right)
M_shoulder = moment_at(a + b_sh, "R")


def statics_alt():
    """same geometry, axial force pointing toward bearing B instead (opposite helix hand / rotation): other couple sign"""
    F2 = np.array([Wa, Wt, -Wr])
    Mg2 = np.cross(pos, F2)
    By2 = -Mg2[2] / L
    Bz2 = Mg2[1] / L
    A2 = np.array([-F2[0], -(F2[1] + By2), -(F2[2] + Bz2)])
    out = {}
    for side in ("L", "R"):
        sec = np.array([a, 0.0, 0.0])
        tot = np.cross(-sec, A2)
        if side == "R":
            tot = tot + np.cross(pos - sec, F2)
        out[side] = math.hypot(tot[1], tot[2])
    return out, math.hypot(A2[1], A2[2]), math.hypot(By2, Bz2)


alt_M, alt_RA, alt_RB = statics_alt()


# ---------------- fatigue sizing, DE-Goodman ----------------
def marin(d_mm):
    ka = 4.51 * Sut ** -0.265
    kb = 1.24 * d_mm ** -0.107 if d_mm <= 51 else 1.51 * d_mm ** -0.157
    return ka * kb * 1.0 * 1.0 * reliability_ke * 0.5 * Sut, ka, kb


def sqrt_a(tors):
    s = Sut / 6.895  # kpsi
    if tors:
        return 0.190 - 2.51e-3 * s + 1.35e-5 * s**2 - 2.67e-8 * s**3
    return 0.246 - 3.08e-3 * s + 1.51e-5 * s**2 - 2.67e-8 * s**3


def kf_of(kt, r_mm, tors):
    q = 1.0 / (1.0 + sqrt_a(tors) / math.sqrt(r_mm / 25.4))
    return 1 + q * (kt - 1), q


def n_factor(d_mm, M, Tm, kt_b, kt_s, r_mm):
    Se, _, _ = marin(d_mm)
    kf, _ = kf_of(kt_b, r_mm, False)
    kfs, _ = kf_of(kt_s, r_mm, True)
    d = d_mm * 1e-3
    inv = 16 / (math.pi * d**3) * (math.sqrt(4 * (kf * M) ** 2) / (Se * 1e6) + math.sqrt(3 * (kfs * Tm) ** 2) / (Sut * 1e6))
    return 1 / inv


def solve_d(M, Tm, kt_b, kt_s, r_over_d):
    lo, hi = 20.0, 200.0
    for _ in range(80):
        mid = (lo + hi) / 2
        n = n_factor(mid, M, Tm, kt_b, kt_s, r_over_d * mid)
        lo, hi = (mid, hi) if n < n_design else (lo, mid)
    return hi


d_key = solve_d(M_gear, T, 2.14, 3.0, 0.02)       # end-mill keyseat at the gear centre plane
d_fil = solve_d(M_shoulder, T, 1.7, 1.5, 0.1)     # well-rounded shoulder fillet
d_req = max(d_key, d_fil)
d_std = math.ceil(d_req / 5.0) * 5.0


def detail(d_mm, M, kt_b, kt_s, r_over_d):
    Se, ka, kb = marin(d_mm)
    kf, qb = kf_of(kt_b, r_over_d * d_mm, False)
    kfs, qs = kf_of(kt_s, r_over_d * d_mm, True)
    return {"Se_MPa": round(Se, 1), "ka": round(ka, 4), "kb": round(kb, 4), "q_bend": round(qb, 3), "q_tors": round(qs, 3),
            "Kf": round(kf, 3), "Kfs": round(kfs, 3), "n_f": round(n_factor(d_mm, M, T, kt_b, kt_s, r_over_d * d_mm), 2)}


# kf = Kt variant (what a quick assistant may do)
def solve_d_kt(M, Tm, kt_b, kt_s):
    lo, hi = 20.0, 200.0
    for _ in range(80):
        mid = (lo + hi) / 2
        Se, _, _ = marin(mid)
        d = mid * 1e-3
        n = 1 / (16 / (math.pi * d**3) * (2 * kt_b * M / (Se * 1e6) + math.sqrt(3) * kt_s * Tm / (Sut * 1e6)))
        lo, hi = (mid, hi) if n < n_design else (lo, mid)
    return hi


d_key_kt = solve_d_kt(M_gear, T, 2.14, 3.0)

# ---------------- bearing SKF 6210 ----------------
C, C0, f0 = 37.1e3, 23.2e3, 14.0   # N (SKF catalogue 6210, 50 x 90 x 20: C = 37.1 kN, C0 = 23.2 kN; f0 about 14)
tbl = [(0.172, 0.19, 2.30), (0.345, 0.22, 1.99), (0.689, 0.26, 1.71), (1.03, 0.28, 1.55), (1.38, 0.30, 1.45),
       (2.07, 0.34, 1.31), (3.45, 0.38, 1.15), (5.17, 0.42, 1.04), (6.89, 0.44, 1.00)]


def eY(x):
    xs = [t[0] for t in tbl]
    return float(np.interp(x, xs, [t[1] for t in tbl])), float(np.interp(x, xs, [t[2] for t in tbl]))


Fa = Wa
x = f0 * Fa / C0
e, Y = eY(x)
P_A = RA if Fa / RA <= e else 0.56 * RA + Y * Fa
P_B = RB
L10h_A = (C / P_A) ** 3 * 1e6 / (60 * n_out)
L10h_B = (C / P_B) ** 3 * 1e6 / (60 * n_out)
P_A_nom = P_A / KA
L10h_A_nom = (C / P_A_nom) ** 3 * 1e6 / (60 * n_out)


# ---------------- key ----------------
def din6885(d):
    for hi, w, h in [(12, 4, 4), (17, 5, 5), (22, 6, 6), (30, 8, 7), (38, 10, 8), (44, 12, 8), (50, 14, 9), (58, 16, 10),
                     (65, 18, 11), (75, 20, 12), (85, 22, 14), (95, 25, 14)]:
        if d <= hi:
            return w, h


w_key, h_key = din6885(d_std)
Sy_key = 530.0       # AISI 1045 cold drawn (Shigley Table A-20)
Ssy = 0.577 * Sy_key
n_key = 2.0
Fk = T / (d_std * 1e-3 / 2)
l_shear = Fk / (w_key * 1e-3 * Ssy * 1e6 / n_key) * 1e3
l_crush = Fk / ((h_key / 2) * 1e-3 * Sy_key * 1e6 / n_key) * 1e3

print(json.dumps({
    "answers": {
        "gear_ratio": round(i, 3), "output_speed_rpm": round(n_out, 1),
        "wheel_pitch_diameter_mm": round(d2 * 1e3, 1),
        "output_torque_nominal_Nm": round(T_nom, 1), "design_torque_with_KA_1.25_Nm": round(T, 1),
        "tangential_force_N": round(Wt, 0), "radial_force_N": round(Wr, 0), "axial_force_N": round(Wa, 0),
        "bearing_reactions_N": {"A_radial": round(RA, 0), "B_radial": round(RB, 0), "A_axial": round(Ax, 0)},
        "bending_moment_at_gear_plane_Nm": {"left": round(M_left, 1), "right": round(M_right, 1)},
        "bending_moment_at_shoulder_Nm": round(M_shoulder, 1),
        "if_axial_force_points_toward_B_instead": {"gear_plane_moment_left_right_Nm": [round(alt_M["L"], 1), round(alt_M["R"], 1)], "reaction_A_radial_N": round(alt_RA, 0), "reaction_B_radial_N": round(alt_RB, 0), "L10h_locating_B": round((C / (0.56 * alt_RB + eY(f0 * Fa / C0)[1] * Fa)) ** 3 * 1e6 / (60 * n_out), 0), "note": "the direction of the axial force (helix hand x rotation) was not given in the first message; the assistant should ask or take the worst case (moment about 5 % higher, diameter about 2 % higher; the locating bearing is then B)"},
        "min_diameter_keyseat_section_mm_DE_Goodman_n2_99pct": round(d_key, 1),
        "min_diameter_keyseat_section_mm_if_Kf_equals_Kt": round(d_key_kt, 1),
        "min_diameter_shoulder_fillet_section_mm_DE_Goodman_n2_99pct": round(d_fil, 1),
        "required_shaft_diameter_mm": round(d_req, 1), "round_up_to_5mm_standard_mm": d_std,
        "check_at_standard_diameter": {"keyseat": detail(d_std, M_gear, 2.14, 3.0, 0.02),
                                       "shoulder_fillet": detail(d_std, M_shoulder, 1.7, 1.5, 0.1)},
        "bearing_SKF_6210": {"C_kN": 37.1, "C0_kN": 23.2, "P_locating_A_N": round(P_A, 0), "P_B_N": round(P_B, 0),
                             "e": round(e, 3), "Y": round(Y, 3), "L10h_A_with_KA": round(L10h_A, 0),
                             "L10h_B_with_KA": round(L10h_B, 0), "L10h_A_nominal_loads": round(L10h_A_nom, 0)},
        "key": {"DIN6885_size_mm": f"{w_key} x {h_key}", "min_length_shear_mm": round(l_shear, 1),
                "min_length_crushing_mm": round(l_crush, 1), "governing": "crushing",
                "practical_note": "the calculated minimum is far below what is fitted: the length is set by hub width and the DIN 6885 standard length series (e.g. 56-70 mm for an 80 mm hub), not by strength"},
        "persons_guess_d50": {"n_f_keyseat_at_50mm": detail(50.0, M_gear, 2.14, 3.0, 0.02)["n_f"], "comment": "the guess of 50 mm for the gear seat gives a fatigue factor well below the required 2 (about 1.4-1.5), so 50 mm is too small for the 99 % reliability, n = 2 target"},
        "bending_moment_stations_Nm": {"x0_bearing_A": 0.0, "x120_gear_left": round(M_left, 1), "x120_gear_right": round(M_right, 1), "x180_shoulder": round(M_shoulder, 1), "x300_bearing_B": round(moment_at(L - 1e-9, "R"), 1)},
        "coupling_end_key": {"stub_diameter_mm": 45, "DIN6885_mm": "14 x 9", "min_length_crushing_mm_n2": round((2 * T / 0.045) / ((9 / 2) * 1e-3 * Sy_key * 1e6 / 2) * 1e3, 1), "min_length_shear_mm_n2": round(2 * T / 0.045 / (14e-3 * Ssy * 1e6 / 2) * 1e3, 1), "stub_torsional_shear_MPa": round(16 * T / (math.pi * 0.045**3) / 1e6, 1)},
        "static_check": {"von_mises_nominal_at_gear_seat_d60_MPa": round(math.sqrt((32 * M_gear / (math.pi * 0.060**3))**2 + 3 * (16 * T / (math.pi * 0.060**3))**2) / 1e6, 1), "Sy_MPa": Sy},
        "sensitivity_without_service_factor": "KA = 1: torque is 681 N m and the diameters are about 8-10 % lower",
    },
    "how": "Shigley 10th ed.: DE-Goodman Eq. 7-8 solved iteratively (kb depends on d) with Se = ka kb ke 0.5 Sut, ka = 4.51 Sut^-0.265, kb = 1.51 d^-0.157 (d > 51 mm), kc = kd = 1, ke = 0.814 (99 %), Sut = 900 MPa; Kt 2.14/3.0 (end-mill keyseat, r/d = .02) and 1.7/1.5 (shoulder, r/d = .1) from Table 7-1; Kf from Neuber constants (6-35); rotating shaft with fixed gear loads -> fully reversed bending, steady torque; forces by 3D statics (mesh on top, axial force toward the locating bearing A). Bearing ISO 281 with SKF 6210 catalogue ratings; key Shigley 7-8 with 1045 CD Sy = 530 MPa, n = 2.",
    "tolerance": "gear forces within 1 %; shaft diameter within about 5 % (Kf/q interpretation and kb are the main modelling choices; the assistant may use Kf = Kt, which gives a larger diameter, acceptable if stated); bearing life within 15 % (X, Y interpolation, f0); key length: crushing governs, minimum within 10 %, and the answer must not recommend the minimum as the fitted length without comment."
}, indent=1))
