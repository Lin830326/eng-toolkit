"""Truth for S2: 8 m span steel roof truss (angles), 3 m truss spacing, gravity case (dead + snow) and wind-suction case (dead + suction).

Independent matrix (direct stiffness) solve with numpy; Euler pin-ended buckling about the weakest (v-v) axis with catalogue
radius of gyration of equal angles (EN 10056-1 / ArcelorMittal tables). No eng-toolkit code. Run: python uat/truth/S2_roof_truss_wind_uplift.py
"""
import json

import numpy as np

E = 210e9
SPACING = 3.0     # m between trusses
DEAD, SNOW, WIND = 0.25e3, 0.80e3, 0.90e3   # N/m2: dead & snow per m2 of plan, wind suction per m2 of roof surface (normal to the roof)
FOS = 2.0         # the person's rule: allowable compression = Euler load / 2

# equal angles: A [cm2], i_v (minor axis) [cm]  -- EN 10056-1 sections
ANGLES = {"L80x80x8": (12.3, 1.55), "L60x60x6": (6.91, 1.17), "L50x50x5": (4.80, 0.97), "L60x60x5": (5.82, 1.17), "L70x70x7": (9.40, 1.37)}
NODES = {"B0": (0, 0), "B1": (2, 0), "B2": (4, 0), "B3": (6, 0), "B4": (8, 0), "T1": (2, .8), "T2": (4, 1.6), "T3": (6, .8)}
MEMBERS = [("B0", "B1", "bottom"), ("B1", "B2", "bottom"), ("B2", "B3", "bottom"), ("B3", "B4", "bottom"),
           ("B0", "T1", "top"), ("T1", "T2", "top"), ("T2", "T3", "top"), ("T3", "B4", "top"),
           ("T1", "B1", "web"), ("T2", "B2", "web"), ("T3", "B3", "web"), ("B1", "T2", "web"), ("B3", "T2", "web")]
CHOICE = {"top": "L80x80x8", "bottom": "L60x60x6", "web": "L50x50x5"}
THETA = np.arctan(0.4)          # roof slope 21.8 deg
SEG = 2 / np.cos(THETA)         # length of one top-chord panel along the slope
NAMES = list(NODES)
IDX = {n: i for i, n in enumerate(NAMES)}


def solve(force, choice):
    ndof = 2 * len(NAMES)
    K = np.zeros((ndof, ndof))
    geo = []
    for a, b, kind in MEMBERS:
        (xa, ya), (xb, yb) = NODES[a], NODES[b]
        L = np.hypot(xb - xa, yb - ya)
        c, s = (xb - xa) / L, (yb - ya) / L
        A, iv = ANGLES[choice[kind]]
        A *= 1e-4
        iv *= 1e-2
        v = np.array([-c, -s, c, s])
        d = [2 * IDX[a], 2 * IDX[a] + 1, 2 * IDX[b], 2 * IDX[b] + 1]
        K[np.ix_(d, d)] += E * A / L * np.outer(v, v)
        geo.append((a + "-" + b, kind, L, A, iv, v, d))
    fixed = [0, 1, 2 * IDX["B4"] + 1]
    free = [i for i in range(ndof) if i not in fixed]
    u = np.zeros(ndof)
    u[free] = np.linalg.solve(K[np.ix_(free, free)], force[free])
    react = K @ u - force
    out = {}
    for name, kind, L, A, iv, v, d in geo:
        N = E * A / L * (v @ u[d])
        Pcr = np.pi ** 2 * E * A * iv ** 2 / L ** 2     # I_min = A i_v^2
        out[name] = {"section": choice[kind], "length_m": round(L, 3), "N_kN": round(N / 1e3, 3), "Pcr_kN": round(Pcr / 1e3, 2),
                     "Nover_Pcr": round(-N / Pcr, 3) if N < 0 else None,
                     "util_with_FoS": round(-N / (Pcr / FOS), 3) if N < 0 else None,
                     "stress_MPa": round(N / A / 1e6, 1)}
    return out, react[fixed]


def loads(dead, snow, wind):
    F = np.zeros(2 * len(NAMES))
    plan_share = {"B0": 1, "T1": 2, "T2": 2, "T3": 2, "B4": 1}      # in metres of plan width
    slope_share = {"B0": SEG / 2, "T1": SEG, "T2": SEG, "T3": SEG, "B4": SEG / 2}
    for n in plan_share:
        F[2 * IDX[n] + 1] -= (dead + snow) * plan_share[n] * SPACING
        if wind:
            side = {"B0": -1, "T1": -1, "T2": 0, "T3": 1, "B4": 1}[n]   # outward normal of left slope is (-sin, +cos)
            F[2 * IDX[n]] += wind * np.sin(THETA) * side * slope_share[n] * SPACING
            F[2 * IDX[n] + 1] += wind * np.cos(THETA) * slope_share[n] * SPACING
    return F


def main():
    res = {}
    for case, F in {"gravity_dead_plus_snow": loads(DEAD, SNOW, 0), "uplift_dead_plus_wind_suction": loads(DEAD, 0, WIND)}.items():
        out, rx = solve(F, CHOICE)
        comp = {k: v for k, v in out.items() if v["Nover_Pcr"] is not None}
        crit = max(comp, key=lambda k: comp[k]["Nover_Pcr"])
        res[case] = {"members": out, "critical_compression_member": crit, "critical": comp[crit], "reactions_kN": [round(float(x) / 1e3, 3) for x in rx],
                     "max_tension_kN": max(v["N_kN"] for v in out.values())}
    # upgrade the web angles
    up = dict(CHOICE, web="L60x60x6")
    out_up, _ = solve(loads(DEAD, 0, WIND), up)
    answers = {
        "loads": "dead 0.25 + snow 0.80 kN/m2 on plan; wind suction 0.90 kN/m2 normal to the roof; 3 m truss spacing; purlin joints at the top-chord nodes",
        "gravity_case": res["gravity_dead_plus_snow"],
        "uplift_case": res["uplift_dead_plus_wind_suction"],
        "governing": "wind UPLIFT (suction) governs, not snow: it reverses the web diagonals B1-T2 and B3-T2 into compression (about 7.6 kN) and they are the slender L50x50x5 "
                     "members 2.56 m long: Euler load only about 14.3 kN, so N/Pcr about 0.53 and utilisation with a factor of 2 about 1.07 (FAILS the person's rule). "
                     "In the snow case the top chord is the most compressed member (25.4 kN) but is only about 0.39 utilised.",
        "fix": {"web_changed_to": "L60x60x6", "diagonal_N_kN": out_up["B1-T2"]["N_kN"], "diagonal_util_with_FoS": out_up["B1-T2"]["util_with_FoS"]},
        "trap": "using Ix (11.0 cm4 for L50x50x5) instead of the minor-axis Iv (about 4.5 cm4) makes the diagonal look safe (util about 0.4). Angles buckle about the weak v-v axis.",
        "not_checked": "single-angle connection eccentricity, gusset design, purlin spacing on the chords (bending of the top chord between nodes), load factors, sway of the support, tension capacity (bars are at most about 35 MPa)",
    }
    print(json.dumps({"answers": answers,
                      "how": "numpy direct-stiffness solve of the 8-node, 13-bar pin-jointed truss (pin at B0, roller at B4); Euler load pi^2 E A i_v^2 / L^2 with L = joint-to-joint length (K = 1)",
                      "tolerance": "member forces within 1 % (they depend only on geometry and load, not on E or A for this determinate truss); Euler loads within 5 % (catalogue i_v of the angle); the governing case (wind uplift), the critical member (diagonal B1-T2 / B3-T2) and the failing verdict (util about 1.07, i.e. N/Pcr about 0.53 against the allowable 0.5) must match. The assistant may phrase utilisation as N/Pcr = 0.53 or N/(Pcr/2) = 1.07: both are the same statement."},
                     indent=1, default=float))


if __name__ == "__main__":
    main()
