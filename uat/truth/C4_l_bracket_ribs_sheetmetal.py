"""Truth for C4: 3 mm steel L-bracket (80 mm base, 60 mm upright, 40 mm wide), 4 x M5 clearance holes, 2 triangular ribs.

Independent of eng-toolkit: closed-form areas (rectangles, quarter-annulus bend, corner fill) cross-checked with a numpy polygon
integration of the discretised profile; sheet-metal rule comparison with the shop's minimum inside bend radius.
Chosen dimensions (all in mm): base length 80 (outer), upright height 60 (outer), width 40, t 3, inside bend radius ri (2 as the person
proposes, 3 as the shop requires), holes d5.5 (ISO 273 medium clearance for M5) 2 in the base at x=60, y=10/30 and 2 in the upright
at z=45, y=10/30; two triangular ribs 3 mm thick with 25 mm legs measured from the inside faces, sitting at y=12..15 and y=25..28.
"""
import json
import math

import numpy as np

T, LA, HB, W, D_HOLE, N_HOLES = 3.0, 80.0, 60.0, 40.0, 5.5, 4
RIB_T, RIB_LEG, N_RIBS = 3.0, 25.0, 2
RHO = 7850.0  # kg/m3 carbon steel


def section_area_closed(ri):
    ro = ri + T
    return LA * T + HB * T - T * T + (ri * ri - ro * ro) * (1 - math.pi / 4)


def section_area_numeric(ri, n=20000):
    ro = ri + T
    pts = [(ro, 0), (LA, 0), (LA, T), (ri + T, T)]
    # inner arc from (ri+T, T) to (T, ri+T), centre (ri+T, ri+T), turning clockwise (radius ri)
    for a in np.linspace(-math.pi / 2, -math.pi, n)[1:]:
        pts.append((ri + T + ri * math.cos(a), ri + T + ri * math.sin(a)))
    pts += [(T, HB), (0, HB), (0, ro)]
    # outer arc from (0, ro) to (ro, 0), centre (ro, ro), counter-clockwise
    for a in np.linspace(math.pi, 1.5 * math.pi, n)[1:]:
        pts.append((ro + ro * math.cos(a), ro + ro * math.sin(a)))
    p = np.array(pts)
    x, y = p[:, 0], p[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def design(ri):
    a_closed = section_area_closed(ri)
    a_num = section_area_numeric(ri)
    holes = N_HOLES * math.pi / 4 * D_HOLE ** 2 * T
    fill = ri * ri * (1 - math.pi / 4)  # corner fill under the ribs' sharp inside corner, already material
    ribs = N_RIBS * RIB_T * (RIB_LEG ** 2 / 2 - fill)
    volume = W * a_closed - holes + ribs
    return {
        "inside_radius_mm": ri,
        "r_over_t": round(ri / T, 3),
        "section_area_mm2_closed_vs_numeric": [round(a_closed, 3), round(a_num, 3)],
        "volume_mm3": round(volume, 1),
        "mass_g": round(volume * 1e-9 * RHO * 1000, 2),
        "rib_mass_g": round(ribs * 1e-9 * RHO * 1000, 2),
        "meets_shop_min_radius_1t": ri >= 1.0 * T - 1e-9,
    }


def flat_length(ri, k):
    return (LA - (ri + T)) + (HB - (ri + T)) + math.pi / 2 * (ri + k * T)


def main():
    two, three = design(2.0), design(3.0)
    hole_edge = min(10 - D_HOLE / 2, LA - 60 - D_HOLE / 2, HB - 45 - D_HOLE / 2)
    print(json.dumps({
        "answers": {
            "as_proposed_ri_2mm": two,
            "shop_rule_ri_3mm": three,
            "bend_verdict": "inside radius 2 mm = 0.67 t is below the shop/toolkit rule of 1.0 t for 3 mm low-carbon steel (min 3 mm): NOT OK; 3 mm (1.0 t) passes exactly at the limit. There is exactly ONE bend (90 degrees, full 40 mm width).",
            "hole_checks": {"hole_diameter_mm": D_HOLE, "iso273_M5_fine_medium_coarse_mm": [5.3, 5.5, 5.8],
                            "smallest_hole_wall_to_edge_mm": round(hole_edge, 2), "ratio_to_t": round(hole_edge / T, 2),
                            "rule_1.5t_mm": 1.5 * T, "holes_ok": hole_edge >= 1.5 * T,
                            "hole_to_bend_distance_mm": round(60 - D_HOLE / 2 - (2 + T), 1), "rule_2t_plus_r_mm": round(2 * T + 2, 1)},
            "flat_blank_length_mm_ri2_K0.33_to_0.5": [round(flat_length(2.0, 0.33), 1), round(flat_length(2.0, 0.5), 1)],
            "ribs_note": "the ribs are welded/pressed-in gussets, not bends. A sheet-metal DFM tool that sees the solid flags the gusset-to-plate inside corners as radius-0 'bends' (8 edges) and splits the real bend into 3 faces: those findings are not bend problems",
            "mass_of_plate_only_g_ri2": round((W * section_area_closed(2.0) - N_HOLES * math.pi / 4 * D_HOLE ** 2 * T) * 1e-9 * RHO * 1000, 2),
        },
        "how": "Closed form: L section area = La*t + Hb*t - t^2 + (ri^2 - ro^2)(1 - pi/4), ro = ri + t; x width; minus 4 x pi/4 d^2 t; plus 2 ribs x t_rib x (leg^2/2 - ri^2 (1 - pi/4)) (the triangle overlaps the bend fill); mass = V x 7850 kg/m3. Section area re-integrated numerically from the arc-discretised outline (agrees to 1e-6). Flat blank = (La - (ri+t)) + (Hb - (ri+t)) + pi/2 (ri + K t). Sheet-metal rule: shop minimum inside radius 1.0 t for S235/DC01 3 mm.",
        "tolerance": "volume and mass: the person's own dimensions decide them; if the assistant chooses its own hole diameter (5.3-5.8 mm) or rib geometry the mass differs by up to about 1.5 g, so compare against the dimensions the assistant states (formula above) within 0.5 %. Bend verdict: ri 2 mm fails, 3 mm passes (rule 1.0 t). Flat length +-0.7 mm.",
    }, indent=1))


if __name__ == "__main__":
    main()
