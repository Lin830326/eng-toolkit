"""Truth for H3: (a) crane runway beam, simply supported, two wheel loads, HEB section; (b) pitched Warren canopy truss under dead load
plus a wind pressure normal to the sloping top chord.

Independent of eng-toolkit: Euler-Bernoulli beam statics scanned over wheel positions (Barre's rule is checked against the scan),
exact superposition deflections, catalogue section values (EN 10365 tables); the truss by a plain numpy pin-jointed direct stiffness
solver with the nodal loads computed here. Prints JSON {answers, how, tolerance}.
"""
import json
import math

import numpy as np

# ---------------------------------------------------------------- (a) runway beam
E = 210e9
G = 9.80665
L = 7.5                    # m span, simply supported
Q = 48e3                   # N static vertical load per wheel
A_WHEEL = 2.6              # m wheel base
PHI = 1.25                 # dynamic factor on the wheel loads for the STRESS check only
RAIL = 0.30e3              # N/m rail weight
FY = 355e6                 # S355
ALLOW = 0.6 * FY           # the company's working-stress allowable
LIMIT = L / 600.0          # deflection limit under the static wheel loads
# I_y [cm4], W_el,y [cm3], mass [kg/m]   (EN 10365 / ArcelorMittal)
HEB = {"HEB 200": (5696, 570, 61.3), "HEB 220": (8091, 736, 71.5), "HEB 240": (11260, 938, 83.2), "HEB 260": (14920, 1148, 93.0),
       "HEB 280": (19270, 1376, 103.0), "HEB 300": (25170, 1678, 117.0), "HEB 320": (30820, 1926, 127.0), "HEB 340": (36660, 2156, 134.0)}
XS = np.linspace(0, L, 3001)


def two_wheels(x_first, ei, w_udl, load_factor=1.0):
    """Bending moment max and max deflection for two wheels at x_first and x_first + A_WHEEL plus a UDL w_udl [N/m]."""
    loads = [(x_first, Q * load_factor), (x_first + A_WHEEL, Q * load_factor)]
    xs = XS
    ra = sum(p * (L - a) / L for a, p in loads) + w_udl * L / 2
    m = ra * xs - w_udl * xs ** 2 / 2 - sum(p * np.maximum(xs - a, 0.0) for a, p in loads)
    y = w_udl * xs * (L ** 3 - 2 * L * xs ** 2 + xs ** 3) / (24 * ei)
    for a, p in loads:
        b = L - a
        y = y + np.where(xs <= a, p * b * xs * (L ** 2 - b ** 2 - xs ** 2) / (6 * ei * L),
                         p * a * (L - xs) * (2 * L * xs - xs ** 2 - a ** 2) / (6 * ei * L))
    return float(m.max()), float(xs[m.argmax()]), float(y.max()), float(xs[y.argmax()]), ra


def check(name):
    i_cm4, w_cm3, kg = HEB[name]
    ei, wel = E * i_cm4 * 1e-8, w_cm3 * 1e-6
    w = RAIL + kg * G
    positions = np.linspace(0, L - A_WHEEL, 197)
    m_worst, x_worst = max((two_wheels(x, ei, w, PHI)[0], x) for x in positions)
    centred = (L - A_WHEEL) / 2
    m_sym = two_wheels(centred, ei, w, PHI)[0]
    d_worst, xd = max((two_wheels(x, ei, w, 1.0)[2], x) for x in positions)
    d_sym = two_wheels(centred, ei, w, 1.0)[2]
    return {"M_worst_kNm": round(m_worst / 1e3, 1), "first_wheel_x_m_at_worst_M": round(float(x_worst), 2), "M_with_pair_centred_kNm": round(m_sym / 1e3, 1),
            "sigma_worst_MPa": round(m_worst / wel / 1e6, 1), "stress_ok": bool(m_worst / wel <= ALLOW),
            "delta_worst_mm": round(d_worst * 1e3, 1), "delta_pair_centred_mm": round(d_sym * 1e3, 1), "span_over_delta": round(L / d_worst),
            "deflection_ok": bool(d_worst <= LIMIT)}


# ---------------------------------------------------------------- (b) canopy truss
SPAN, H0, H1 = 12.0, 1.2, 4.0        # bottom chord horizontal; the top chord line rises from H0 at x = 0 to H1 at x = SPAN
PITCH = 2.0                          # panel width (plan) m
TRUSS_SPACING = 4.0                  # m between trusses
WIND = 0.8e3                         # Pa normal to the roof, pressure on the roof
DEAD = 0.35e3                        # Pa gravity on the plan area
E_S = 210e9
A_CH, I_CH = 1170e-6, 108e-8         # chords: SHS 80x80x4 catalogue values (as given to the assistant)
A_DG, I_DG = 680e-6, 34.2e-8         # diagonals: SHS 60x60x3


def truss():
    ytop = lambda x: H0 + (H1 - H0) * x / SPAN
    nodes = {f"B{i}": (2.0 * i, 0.0) for i in range(7)}
    nodes.update({f"T{i}": (2.0 * i - 1.0, ytop(2.0 * i - 1.0)) for i in range(1, 7)})
    members = []
    for i in range(6):
        members.append((f"B{i}-B{i+1}", f"B{i}", f"B{i+1}", A_CH, I_CH))
    for i in range(1, 6):
        members.append((f"T{i}-T{i+1}", f"T{i}", f"T{i+1}", A_CH, I_CH))
    for i in range(1, 7):
        members.append((f"B{i-1}-T{i}", f"B{i-1}", f"T{i}", A_DG, I_DG))
        members.append((f"T{i}-B{i}", f"T{i}", f"B{i}", A_DG, I_DG))
    theta = math.atan2(H1 - H0, SPAN)
    nrm = (math.sin(theta), -math.cos(theta))      # unit normal pointing into the roof (down and toward +x)
    panel = PITCH / math.cos(theta)                # roof length per top node along the slope
    loads = {}
    for i in range(1, 7):
        fn = WIND * TRUSS_SPACING * panel
        fg = DEAD * TRUSS_SPACING * PITCH
        loads[f"T{i}"] = (fn * nrm[0], fn * nrm[1] - fg)
    return nodes, members, loads, theta


def solve_truss(loads_override=None):
    nodes, members, loads, theta = truss()
    ids = list(nodes)
    idx = {n: k for k, n in enumerate(ids)}
    K = np.zeros((2 * len(ids), 2 * len(ids)))
    geo = []
    for name, a, b, A, I in members:
        (xa, ya), (xb, yb) = nodes[a], nodes[b]
        length = math.hypot(xb - xa, yb - ya)
        c, s = (xb - xa) / length, (yb - ya) / length
        k = E_S * A / length * np.array([[c * c, c * s], [c * s, s * s]])
        dofs = [2 * idx[a], 2 * idx[a] + 1, 2 * idx[b], 2 * idx[b] + 1]
        for r in range(2):
            for q in range(2):
                K[dofs[r], dofs[q]] += k[r, q]
                K[dofs[r + 2], dofs[q + 2]] += k[r, q]
                K[dofs[r], dofs[q + 2]] -= k[r, q]
                K[dofs[r + 2], dofs[q]] -= k[r, q]
        geo.append((name, a, b, A, I, length, c, s))
    F = np.zeros(2 * len(ids))
    for n, (fx, fy) in (loads_override or loads).items():
        F[2 * idx[n]] += fx
        F[2 * idx[n] + 1] += fy
    fixed = [2 * idx["B0"], 2 * idx["B0"] + 1, 2 * idx["B6"] + 1]      # pin at B0, vertical roller at B6
    free = [d for d in range(len(F)) if d not in fixed]
    u = np.zeros(len(F))
    u[free] = np.linalg.solve(K[np.ix_(free, free)], F[free])
    forces = {}
    for name, a, b, A, I, length, c, s in geo:
        du = (u[2 * idx[b]] - u[2 * idx[a]], u[2 * idx[b] + 1] - u[2 * idx[a] + 1])
        forces[name] = E_S * A / length * (c * du[0] + s * du[1])
    reac = K @ u - F
    return nodes, geo, forces, u, idx, {"B0": (reac[2 * idx["B0"]], reac[2 * idx["B0"] + 1]), "B6": reac[2 * idx["B6"] + 1]}, loads


def member_table(geo, forces):
    rows = []
    for name, a, b, A, I, length, c, s in geo:
        f = forces[name]
        pcr = math.pi ** 2 * E_S * I / length ** 2
        rows.append({"member": name, "length_m": round(length, 3), "force_kN": round(f / 1e3, 2), "stress_MPa": round(f / A / 1e6, 1),
                     "euler_Pcr_kN": round(pcr / 1e3, 1), "euler_factor": round(pcr / -f, 2) if f < 0 else None})
    return rows


def main():
    beam = {name: check(name) for name in HEB}
    smallest = next(n for n in HEB if beam[n]["stress_ok"] and beam[n]["deflection_ok"])
    nodes, geo, forces, u, idx, reac, loads = solve_truss()
    members = member_table(geo, forces)
    comp = min((m for m in members if m["euler_factor"]), key=lambda m: m["euler_factor"])
    nid = list(nodes)
    maxd = max(range(len(nid)), key=lambda k: abs(u[2 * k + 1]))
    theta = truss()[3]
    fn = WIND * TRUSS_SPACING * PITCH / math.cos(theta)
    # the wrong way: wind taken as a vertical force of the same size
    wrong_loads = {n: (0.0, -fn * 1.0 - DEAD * TRUSS_SPACING * PITCH) for n in loads}
    _, geo_w, forces_w, _, _, reac_w, _ = solve_truss(wrong_loads)
    answers = {
        "beam_inputs": {"span_m": L, "wheel_load_kN": Q / 1e3, "wheelbase_m": A_WHEEL, "dynamic_factor_on_stress": PHI, "allow_MPa": ALLOW / 1e6,
                        "limit_L600_mm": round(LIMIT * 1e3, 1), "self_weight_note": "beam mass per metre from the catalogue plus 0.30 kN/m rail"},
        "HEB240_asked": beam["HEB 240"],
        "all_sections": {n: {k: v for k, v in r.items() if k in ("sigma_worst_MPa", "delta_worst_mm", "stress_ok", "deflection_ok")} for n, r in beam.items()},
        "smallest_HEB_that_passes_both": smallest,
        "barre_rule": f"worst bending moment under a wheel when the beam centre lies midway between that wheel and the resultant of the pair: first wheel at L/2 - a/4 = {L / 2 - A_WHEEL / 4:.2f} m from the left support; a few percent above the centred-pair moment",
        "verdict_beam": "see all_sections: HEB 240 passes the stress check but fails the L/600 deflection limit, so deflection governs; the smallest HEB that meets both is given in smallest_HEB_that_passes_both",
        "truss_geometry": f"7 bottom nodes at x = 0..12 m (2 m pitch), 6 top nodes at x = 1, 3 .. 11 m on a top chord rising from 1.2 m to 4.0 m (slope {math.degrees(theta):.2f} deg); 13 nodes, 23 bars; pin at B0, vertical roller at B6",
        "truss_nodal_load_per_top_node": {"wind_normal_kN": round(fn / 1e3, 3), "dead_kN_down": round(DEAD * TRUSS_SPACING * PITCH / 1e3, 3),
                                          "combined_Fx_kN": round(loads["T3"][0] / 1e3, 3), "combined_Fy_kN": round(loads["T3"][1] / 1e3, 3)},
        "truss_reactions_kN": {"B0_x": round(reac["B0"][0] / 1e3, 2), "B0_y": round(reac["B0"][1] / 1e3, 2), "B6_y": round(reac["B6"] / 1e3, 2)},
        "truss_members": members,
        "truss_max_tension_kN": max(m["force_kN"] for m in members), "truss_max_compression_kN": min(m["force_kN"] for m in members),
        "truss_lowest_euler_factor": comp,
        "verdict_truss": "strength is no problem (largest stress about 45 MPa), but with the required Euler safety factor of 2 the long end diagonals fail: " + ", ".join(f"{m['member']} ({m['euler_factor']})" for m in members if m["euler_factor"] and m["euler_factor"] < 2) + "; the chords are fine",
        "truss_max_vertical_deflection_mm": round(abs(u[2 * maxd + 1]) * 1e3, 2), "truss_max_deflection_node": nid[maxd],
        "if_wind_wrongly_taken_vertical": {"B0_x_reaction_kN": round(reac_w["B0"][0] / 1e3, 2), "max_compression_kN": round(min(forces_w.values()) / 1e3, 2),
                                           "B0-T1_kN": round(forces_w["B0-T1"] / 1e3, 2), "T6-B6_kN": round(forces_w["T6-B6"] / 1e3, 2), "T5-B5_kN": round(forces_w["T5-B5"] / 1e3, 2),
                                           "note": "the horizontal reaction vanishes and member forces change by 5 to 6 %: the load is NORMAL to the roof, so it has a horizontal part"},
    }
    print(json.dumps({"answers": answers,
                      "how": "numpy: beam statics and exact superposition deflection scanned over 197 wheel positions with the catalogue Iy and Wel (EN 10365); pin-jointed direct stiffness for the truss with the normal wind pressure resolved into x and y per node",
                      "tolerance": "Beam moments and stress within 2 %, deflections within 3 % (catalogue section values; a sharp-cornered I built from four dimensions reads about 6 % less stiff: accept up to 8 % if stated). The verdict (HEB 240: stress ok, deflection fails; the next HEB that works; the worst wheel position is not the centred one) must match smallest_HEB_that_passes_both. Truss member forces within 2 % when the wind is resolved normal to the roof; an assistant that applies the wind as purely vertical forces gets a horizontal reaction of 0 instead of about 9 kN and diagonal and chord forces 5 to 6 % off (see if_wind_wrongly_taken_vertical)."},
                     indent=1, default=str))


if __name__ == "__main__":
    main()
