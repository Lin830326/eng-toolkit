"""Truth for S1: simply supported IPE 240 (S275) secondary beam, 6 m span, 3 kN/m2 on 2.5 m tributary width plus a 1200 kg unit 2 m from the left support.

Closed-form Euler-Bernoulli beam theory (Macaulay / superposition), catalogue section properties from the Eurocode
section tables (ArcelorMittal / EN 10365 values). No eng-toolkit code is used. Run: python uat/truth/S1_mezzanine_beam_ipe240.py
"""
import json

import numpy as np
from scipy.optimize import minimize_scalar

E = 210e9          # Pa, structural steel
G_ACC = 9.80665
L = 6.0            # m
W = 3.0e3 * 2.5    # N/m  (3 kN/m2 x 2.5 m)
MASS = 1200.0      # kg
A_POS = 2.0        # m from the left support
P = MASS * G_ACC   # N
FY = 275e6         # Pa, S275 (t <= 16 mm)
LIMIT = L / 360.0

# catalogue values: I_y [cm4], W_el,y [cm3], mass [kg/m]  (EN 10365 / ArcelorMittal tables)
SECTIONS = {
    "IPE 200": (1943, 194.3, 22.4), "IPE 220": (2772, 252.0, 26.2), "IPE 240": (3892, 324.3, 30.7),
    "IPE 270": (5790, 428.9, 36.1), "IPE 300": (8356, 557.1, 42.2),
    "HEA 160": (1673, 220.1, 30.4), "HEA 180": (2510, 293.6, 35.5), "HEA 200": (3692, 388.6, 42.3),
    "HEA 220": (5410, 515.2, 50.5),
}


def analyse(I_cm4, W_cm3, kg_per_m, self_weight):
    I = I_cm4 * 1e-8
    Wel = W_cm3 * 1e-6
    w = W + (kg_per_m * G_ACC if self_weight else 0.0)
    b = L - A_POS
    ra = w * L / 2 + P * b / L
    rb = w * L / 2 + P * A_POS / L

    def moment(x):
        return ra * x - w * x * x / 2 - P * np.maximum(x - A_POS, 0.0)

    def defl(x):  # downward positive
        yu = w * x * (L ** 3 - 2 * L * x ** 2 + x ** 3) / (24 * E * I)
        yp = np.where(x <= A_POS, P * b * x * (L ** 2 - b ** 2 - x ** 2) / (6 * E * I * L),
                      P * A_POS * (L - x) * (2 * L * x - x ** 2 - A_POS ** 2) / (6 * E * I * L))
        return yu + yp

    xm = A_POS + (ra - P - w * A_POS) / w if ra - P - w * A_POS > 0 else A_POS  # shear zero right of load
    if ra - w * A_POS - P <= 0:
        xm = ra / w if ra / w < A_POS else A_POS
    mmax = float(moment(np.array(xm)))
    res = minimize_scalar(lambda x: -float(defl(np.array(x))), bounds=(0, L), method="bounded", options={"xatol": 1e-9})
    return {"w_kN_per_m": w / 1e3, "R_left_kN": ra / 1e3, "R_right_kN": rb / 1e3, "M_max_kNm": mmax / 1e3, "x_M_max_m": float(xm),
            "sigma_max_MPa": mmax / Wel / 1e6, "utilisation_vs_fy": mmax / Wel / FY,
            "delta_max_mm": float(defl(np.array(res.x))) * 1e3, "x_delta_max_m": float(res.x),
            "delta_midspan_mm": float(defl(np.array(L / 2))) * 1e3, "span_over_delta": L / float(defl(np.array(res.x))),
            "passes_L360": float(defl(np.array(res.x))) <= LIMIT}


def main():
    ipe240 = analyse(*SECTIONS["IPE 240"], self_weight=False)
    ipe240_sw = analyse(*SECTIONS["IPE 240"], self_weight=True)
    table = {name: analyse(*v, self_weight=True) for name, v in SECTIONS.items()}
    ok_ipe = [n for n in SECTIONS if n.startswith("IPE") and table[n]["passes_L360"]]
    answers = {
        "load_line_kN_per_m": 7.5,
        "unit_weight_kN": P / 1e3,
        "limit_L360_mm": LIMIT * 1e3,
        "IPE240_without_selfweight": {k: round(v, 3) if isinstance(v, float) else v for k, v in ipe240.items()},
        "IPE240_with_selfweight": {k: round(v, 3) if isinstance(v, float) else v for k, v in ipe240_sw.items()},
        "verdict_IPE240": "bending stress is fine (about 144 MPa without or 148 MPa with self weight, utilisation about 0.53 of S275 yield) but the L/360 deflection limit FAILS "
                          "(about 21 mm against 16.7 mm): the section is governed by deflection, not strength",
        "smallest_IPE_passing_L360_with_selfweight": ok_ipe[0] if ok_ipe else None,
        "all_sections_with_selfweight": {n: {"sigma_MPa": round(v["sigma_max_MPa"], 1), "delta_mm": round(v["delta_max_mm"], 2), "passes_L360": v["passes_L360"]} for n, v in table.items()},
        "note_on_allowable": "S275 means fy = 275 MPa, it is the yield strength, not an allowable stress. Working-stress practice would use about 0.6 fy = 165 MPa; "
                             "Eurocode would use fy/gamma_M0 = 275 MPa against FACTORED loads. IPE 240 passes either on stress.",
        "not_checked": "lateral-torsional buckling (assumed the deck restrains the top flange), shear, web crippling under the point load, connections, vibration",
    }
    print(json.dumps({"answers": answers,
                      "how": "Euler-Bernoulli superposition (UDL + point load) with catalogue Iy and Wel,y, E = 210 GPa; maximum deflection found by a bounded scalar minimiser on the exact deflection curve",
                      "tolerance": "moments and reactions within 1 %, stress within 2 %, deflection within 3 % (catalogue Iy; a sharp-cornered I section built from the four dimensions (structures_section) has no root radius, gives Iy = 3671 cm4 (5.7 % low) and Wel = 306 cm3, so its deflection is about 6 % higher (22.3 mm) and its stress about 8 % higher (156 MPa): accept up to 8 % if the assistant says it used an idealised section, but the catalogue values are the better answer). The verdict (stress OK, L/360 fails, IPE 270 is the next IPE size that passes) must match."},
                     indent=1, default=float))


if __name__ == "__main__":
    main()
