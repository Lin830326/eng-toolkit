"""Truth for C3: ISO fits for a 6205 bearing seat (rotating shaft, stationary aluminium housing) and the axial gap stack-up.

Independent of eng-toolkit: published numbers hard-coded with their source, closed-form interval arithmetic, RSS, numpy Monte Carlo.
Sources:
  ISO 286-1/-2:2010 limit deviations (micrometres), nominal 18-30 mm: IT5 9, IT6 13, IT7 21; H7 +21/0; h6 0/-13; k5 +11/+2; k6 +15/+2; m5 +17/+8.
  nominal 50-80 mm: IT6 19, IT7 30; H7 +30/0; J7 +18/-12; K7 +9/-21; M7 0/-30; h6 0/-19.
  ISO 492 (normal tolerance class) 6205: bore d=25: dmp 0/-10 um; outside D=52: Dmp 0/-13 um; width B=15: Bs 0/-120 um.
  SKF 6205 basic dynamic load rating C = 14.8 kN.  Bearing fit practice: SKF General Catalogue (rotating inner ring load, normal load
  P/C 0.05..0.1, shaft 18-100 mm: k5 (or k6); stationary outer ring: housing H7 (steel/cast iron); light-alloy housings call for
  tighter fits than the table value).
"""
import json
import math

import numpy as np

SHAFT = {"k5": (11, 2), "k6": (15, 2), "h6": (0, -13), "m5": (17, 8), "j5": (4, -5)}  # (upper, lower) um at 25 mm
HOUSING = {"H7": (30, 0), "K7": (9, -21), "J7": (18, -12), "M7": (0, -30)}  # (upper, lower) um at 52 mm
BORE = (0, -10)   # 6205 inner ring bore, ISO 492 normal
OD = (0, -13)     # 6205 outer ring OD, ISO 492 normal


def seat_clearance(bore_or_hole, shaft_or_od):
    """clearance = hole size - shaft size, in um (negative = interference); returns (min, max)."""
    hu, hl = bore_or_hole
    su, sl = shaft_or_od
    return hl - su, hu - sl


def main():
    inner = {s: seat_clearance(BORE, SHAFT[s]) for s in SHAFT}
    textbook_h7 = {s: seat_clearance((21, 0), SHAFT[s]) for s in ("k5", "k6", "h6")}  # a plain H7 hole, NOT the bearing bore
    outer = {h: seat_clearance(HOUSING[h], OD) for h in HOUSING}
    d_alpha = 23e-6 - 12e-6      # 6061 housing vs steel bearing ring, 1/K
    dT = 50.0                    # operating 70 C, assembled 20 C
    loosen_um = d_alpha * 52.0 * dT * 1000.0
    outer_hot = {h: (outer[h][0] + loosen_um, outer[h][1] + loosen_um) for h in ("H7", "K7")}

    # axial gap stack: gap = shaft_len - spacer - bearing - circlip  (mm)
    links = [  # name, nominal, plus, minus, sign
        ("shaft shoulder to far groove wall", 21.5, 0.10, 0.10, +1),
        ("spacer", 5.0, 0.0, 0.10, -1),
        ("bearing 6205 width", 15.0, 0.0, 0.12, -1),
        ("circlip DIN 471 d25 thickness", 1.2, 0.0, 0.06, -1),
    ]
    nominal = sum(s * n for _, n, _, _, s in links)
    wc_max = sum((n + p) if s > 0 else -(n - m) for _, n, p, m, s in links)
    wc_min = sum((n - m) if s > 0 else -(n + p) for _, n, p, m, s in links)
    mid = [(n + (p - m) / 2, (p + m) / 2, s) for _, n, p, m, s in links]
    mean = sum(s * c for c, _, s in mid)
    rss = math.sqrt(sum(h * h for _, h, _ in mid))
    sigma = rss / 3.0
    rng = np.random.default_rng(1)
    samples = sum(s * rng.normal(c, h / 3.0, 2_000_000) for c, h, s in mid)
    p_hi = float((samples > 0.5).mean())
    p_lo = float((samples < 0.1).mean())
    z_hi = (0.5 - mean) / sigma

    # a fix that meets 0.1..0.5 in the worst case: tighter shaft +-0.05, spacer +0/-0.05, shaft nominal 21.4
    fix_max = (21.4 + 0.05) - (5.0 - 0.05) - (15.0 - 0.12) - (1.2 - 0.06)
    fix_min = (21.4 - 0.05) - 5.0 - 15.0 - 1.2

    print(json.dumps({
        "answers": {
            "load_class": {"P_kN": 1.2, "C_kN": 14.8, "P_over_C": round(1.2 / 14.8, 3), "class": "normal (0.05 < P/C <= 0.10)"},
            "shaft_fit_vs_bearing_bore_um_clearance_min_max": {k: list(v) for k, v in inner.items()},
            "shaft_recommendation": "k5 (or k6): inner ring rotates with the shaft, so it needs an interference fit; k5 gives 2 to 21 um interference with the 6205 bore (0/-10 um)",
            "h6_shaft_problem": "h6 gives -10 to +13 um: up to 13 um clearance, the inner ring can creep on the shaft (fretting, wear): not recommended for a rotating inner ring",
            "plain_H7_textbook_numbers_um": {k: list(v) for k, v in textbook_h7.items()},
            "textbook_vs_bearing_note": "ISO 286 'H7/k5' (-11 to +19 um, transition) is NOT the bearing: the real bearing bore is 0/-10 um, so the seat is -21 to -2 um (all interference). Same for h6.",
            "housing_fit_vs_bearing_od_um_clearance_min_max": {k: list(v) for k, v in outer.items()},
            "housing_recommendation": "H7 (0 to +43 um) is the normal seat for a stationary outer ring in a steel or cast-iron housing; K7 (-21 to +22 um) is used for heavier loads. In a 6061 housing that runs 50 K above assembly temperature the seat opens by about 29 um, so a tighter fit than H7 (K7, or M7 -30 to +13 um) is the usual choice. So the person's K7 housing is acceptable, the h6 shaft is the wrong half of his drawing.",
            "thermal_loosening_um_dT50K": round(loosen_um, 1),
            "housing_fit_hot_um_70C": {k: [round(a, 1), round(b, 1)] for k, (a, b) in outer_hot.items()},
            "stack_gap_mm": {
                "nominal_from_nominals": round(nominal, 3),
                "mean_midpoint": round(mean, 3),
                "worst_case_min_max": [round(wc_min, 3), round(wc_max, 3)],
                "rss_3sigma_min_max": [round(mean - rss, 4), round(mean + rss, 4)],
                "rss_half_width": round(rss, 4),
                "sigma": round(sigma, 4),
                "spec_0.1_to_0.5": {"worst_case_ok": bool(wc_min >= 0.1 and wc_max <= 0.5), "rss_ok": bool(mean - rss >= 0.1 and mean + rss <= 0.5),
                                    "probability_above_0.5": round(p_hi, 4), "probability_below_0.1": round(p_lo, 5),
                                    "analytic_z_for_0.5": round(z_hi, 3)},
                "note_nominal_vs_mean": "asymmetric tolerances move the mean (0.44) away from the nominal gap (0.30): the average gap is 0.44 mm, not 0.30",
                "largest_contributor": "shaft shoulder-to-groove dimension (+-0.10): 59 % of the RSS variance (42 % of the worst-case span)",
            },
            "fix_that_meets_spec_worst_case": {"shaft": "21.4 +-0.05", "spacer": "5.0 +0/-0.05", "gap_min_max": [round(fix_min, 3), round(fix_max, 3)]},
        },
        "how": "Published ISO 286-2 limit deviations and ISO 492 ring tolerances (sources in the module docstring); clearance = hole - shaft limits; stack-up by interval arithmetic, RSS = sqrt(sum of squared half-ranges) about the mid-point, Monte Carlo (2e6 normal samples, half-range = 3 sigma) with numpy. Thermal loosening = (23e-6 - 12e-6) * 52 mm * 50 K.",
        "tolerance": "ISO deviations and interval numbers exact (+-1 um on the fits, +-0.005 mm on the stack); probability above 0.5 mm 8.4 % +-0.5 %; thermal loosening 28.6 um +-3 um (depends on the alpha values stated). Recommended fits (k5, H7) are catalogue practice, k6 and J7/K7 in light alloy are also acceptable if justified. The statement that the toolkit's fit tool gives H7/k5 for a plain hole, not the bearing bore, is itself part of the correct answer.",
    }, indent=1))


if __name__ == "__main__":
    main()
