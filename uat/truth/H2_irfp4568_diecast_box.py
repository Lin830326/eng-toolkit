"""Truth for H2: IRFP4568 (TO-247) bolted inside a die-cast aluminium box that is its only heatsink, natural convection + radiation.

Independent of eng-toolkit. Modelling choices (stated so the tolerance is fair):
  * box isothermal at the wall temperature Ts (3 mm aluminium, heat-spreading resistance not modelled: it adds about 3 to 6 K to the case);
  * free-standing on a shelf: bottom face does not exchange heat, top + 4 sides do;
  * sides: Churchill-Chu vertical plate, L = box height; top: Lloyd-Moran / McAdams horizontal plate facing up, L = A/perimeter;
  * radiation to surroundings at the air temperature, view factor 1, grey surface;
  * air properties from the ideal gas and Sutherland's law at the film temperature.
Resistance chain Tj = Ts + P (R_jc + R_cs), R_jc from the datasheet, R_cs from the insulating pad (thickness / (k A)).
"""
import json

from scipy.optimize import brentq

SIG, G = 5.670374e-8, 9.80665
LX, LY, H = 0.220, 0.160, 0.060            # box 220 x 160 x 60 mm
A_TOP = LX * LY
A_SIDES = 2 * (LX + LY) * H
P = 48.0                                   # W, continuous dissipation of the MOSFET
T_AIR = 40.0                               # degC, room air around the box
TJ_LIMIT = 125.0                           # the company's own derating rule
RJC = 0.29                                 # K/W, IRFP4568 datasheet junction-to-case
PAD_T, PAD_K, PAD_A = 0.3e-3, 2.5, 15.9e-3 * 20.0e-3    # 0.3 mm silicone pad, k 2.5 W/mK, TO-247 tab footprint ~15.9 x 20 mm
RCS = PAD_T / (PAD_K * PAD_A)


def air(tf):
    mu = 1.716e-5 * (tf / 273.15) ** 1.5 * (273.15 + 110.4) / (tf + 110.4)
    rho = 101325.0 / (287.05 * tf)
    k = 0.02414 * (tf / 273.15) ** 0.8646
    cp = 1006.0
    return rho, mu, k, mu * cp / k, cp


def ra_pr(ts, tinf, length):
    tf = 0.5 * (ts + tinf)
    rho, mu, k, pr, cp = air(tf)
    ra = G * (1.0 / tf) * abs(ts - tinf) * length ** 3 * rho ** 2 * cp / (mu * k)
    return ra, pr, k


def h_side(ts, tinf):
    ra, pr, k = ra_pr(ts, tinf, H)
    nu = (0.825 + 0.387 * ra ** (1 / 6) / (1 + (0.492 / pr) ** (9 / 16)) ** (8 / 27)) ** 2
    return nu * k / H


def h_top(ts, tinf):
    lc = A_TOP / (2 * (LX + LY))
    ra, pr, k = ra_pr(ts, tinf, lc)
    nu = 0.54 * ra ** 0.25 if ra < 1e7 else 0.15 * ra ** (1 / 3)
    return nu * k / lc


def surface_temperature(emissivity, power=P, tair=T_AIR):
    tinf = tair + 273.15

    def balance(ts_c):
        ts = ts_c + 273.15
        hr = emissivity * SIG * (ts ** 2 + tinf ** 2) * (ts + tinf)
        q = A_SIDES * (h_side(ts, tinf) + hr) * (ts - tinf) + A_TOP * (h_top(ts, tinf) + hr) * (ts - tinf)
        return q - power

    return brentq(balance, tair + 0.01, tair + 400.0)


def case(label, emissivity, power=P):
    ts = surface_temperature(emissivity, power)
    tj = ts + power * (RJC + RCS)
    tinf = T_AIR + 273.15
    ts_k = ts + 273.15
    return {"surface": label, "emissivity": emissivity, "box_wall_C": round(ts, 1), "box_rise_K": round(ts - T_AIR, 1),
            "tj_C": round(tj, 1), "margin_to_125C_K": round(TJ_LIMIT - tj, 1),
            "h_conv_side_W_m2K": round(h_side(ts_k, tinf), 2), "h_conv_top_W_m2K": round(h_top(ts_k, tinf), 2),
            "h_rad_W_m2K": round(emissivity * SIG * (ts_k ** 2 + tinf ** 2) * (ts_k + tinf), 2)}


def max_power(emissivity):
    return brentq(lambda p: surface_temperature(emissivity, p) + p * (RJC + RCS) - TJ_LIMIT, 1.0, 200.0)


def main():
    bare = case("bare die-cast aluminium", 0.10)
    black = case("painted black / anodised black", 0.90)
    white = case("painted white", 0.85)
    answers = {
        "box_area_exchanging_m2": round(A_TOP + A_SIDES, 4), "R_jc_K_per_W": RJC, "R_cs_pad_K_per_W": round(RCS, 3),
        "bare_aluminium": bare, "painted_black": black, "painted_white": white,
        "max_dissipation_W_for_Tj_125C": {"bare": round(max_power(0.10), 1), "painted_black": round(max_power(0.90), 1)},
        "verdict": (f"With bare aluminium (emissivity about 0.1) the {P:.0f} W MOSFET reaches Tj about {bare['tj_C']:.0f} C and the box wall {bare['box_wall_C']:.0f} C: it FAILS the 125 C rule. "
                    f"The cheap fix is paint (matt black or white, emissivity 0.85-0.9): box rise {bare['box_rise_K']:.0f} K falls to {black['box_rise_K']:.0f} K and Tj to about {black['tj_C']:.0f} C, a pass with a thin margin. "
                    "Radiation decides this: a bare metal surface radiates almost nothing."),
        "not_modelled": "heat spreading in the 3 mm wall (adds 3 to 6 K), the other 'losses' inside the box, solar load, altitude, a wall or shelf close to the box",
    }
    print(json.dumps({"answers": answers,
                      "how": "energy balance P = sum A (h_conv + h_rad)(Ts - Tair) solved with a bracketing root finder; Churchill-Chu for the sides, McAdams for the top, grey radiation; Tj from the resistance chain",
                      "tolerance": "Box rise within 15 %, Tj within 8 K (the toolkit has no horizontal-plate correlation, so approximating the top as a vertical plate is acceptable). The verdict (bare fails 125 C, painted passes) and the emissivity insight must match. The person's own h = 10 W/m2K rule gives box wall about 99 C and Tj about 131 C (also a fail, by luck): an answer that stops there and does not separate convection from radiation cannot show that painting helps, and is incomplete."},
                     indent=1))


if __name__ == "__main__":
    main()
