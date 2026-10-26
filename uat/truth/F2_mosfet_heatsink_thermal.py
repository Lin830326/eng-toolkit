"""Truth for F2: TO-220 MOSFET (IRF540N) on an extruded aluminium heatsink, natural convection in a 45 C enclosure.

Independent of eng-toolkit: Bar-Cohen/Rohsenow isothermal parallel-plate natural convection (Elenbaas), 1-D fin efficiency,
grey-body radiation from the envelope, thermal resistance chain, lumped-capacitance warm-up (analytic with constant R and
an ODE with temperature-dependent h). Prints JSON {answers, how, tolerance}.
"""
import json
import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import brentq

SIG = 5.670374e-8
G = 9.81
# heatsink (extruded 6063-T5): flow direction = vertical fins, length L along the fins
W, L, T_BASE = 0.060, 0.100, 0.005
N_FIN, T_FIN, H_FIN = 9, 0.002, 0.024
K_AL, RHO_AL, CP_AL = 201.0, 2700.0, 900.0
EMISS = 0.85            # black anodised
P = 8.0
T_AMB = 45.0 + 273.15
RJC, RCS = 1.15, 1.40   # IRF540N datasheet: junction-to-case 1.15 K/W; case-to-sink through an electrically insulating silicone pad (TO-220, typ 1.4 K/W)
TJ_MAX_DATASHEET, TJ_LIMIT = 175.0, 90.0   # user's own reliability rule: Tj <= 90 C
RSA_CATALOGUE = 3.0     # what the heatsink catalogue states (at dT = 75 K)

GAP = (W - N_FIN * T_FIN) / (N_FIN - 1)
MASS = RHO_AL * L * (W * T_BASE + N_FIN * T_FIN * H_FIN)


def air(tf):
    """dry air near 1 atm, film temperature tf [K] (Sutherland / Ideal gas), good to ~1 %"""
    mu = 1.716e-5 * (tf / 273.15) ** 1.5 * (273.15 + 110.4) / (tf + 110.4)
    rho = 101325 / (287.05 * tf)
    k = 0.02414 * (tf / 273.15) ** 0.8646 * 1.0
    cp = 1006.0
    pr = mu * cp / k
    return rho, mu, k, pr, cp


def h_nat(tb):
    """Elenbaas / Bar-Cohen-Rohsenow isothermal channel: Nu_s = [576/(Ra* )^2 + 2.873/(Ra*)^0.5]^-0.5, Ra* = Ra_s s/L"""
    tf = 0.5 * (tb + T_AMB) * 1.0
    rho, mu, k, pr, cp = air(tf)
    nu, alpha, beta = mu / rho, k / (rho * cp), 1.0 / tf
    ra = G * beta * (tb - T_AMB) * GAP ** 3 / (nu * alpha)
    rastar = ra * GAP / L
    nu_s = (576.0 / rastar ** 2 + 2.873 / rastar ** 0.5) ** -0.5
    return nu_s * k / GAP


def r_sa(tb, area_extra=True):
    """sink-to-ambient resistance at base temperature tb [K]"""
    h = h_nat(tb)
    m = np.sqrt(2 * h / (K_AL * T_FIN))
    lc = H_FIN + T_FIN / 2
    eta = np.tanh(m * lc) / (m * lc)
    a_fin = 2 * L * lc * N_FIN
    a_base = (W - N_FIN * T_FIN) * L
    g_conv = h * (a_base + eta * a_fin)
    hr = EMISS * SIG * (tb ** 2 + T_AMB ** 2) * (tb + T_AMB)
    a_env = L * (W + 2 * H_FIN)          # radiates to surroundings: outer fin faces + tips envelope
    g_rad = hr * a_env * 0.8             # 0.8: fin-to-fin shading / view-factor allowance
    return 1.0 / (g_conv + g_rad), h, eta, g_conv, g_rad


def steady(power):
    tb = brentq(lambda t: (t - T_AMB) * (1.0 / r_sa(t)[0]) - power, T_AMB + 0.1, T_AMB + 300)
    return tb


tb = steady(P)
rsa, h, eta, gc, gr = r_sa(tb)
tj = T_AMB + P * (RJC + RCS) + (tb - T_AMB)
tc = tb + P * RCS
# natural-convection heatsink thermal resistance the catalogue would quote at dT = 75 K (what the person read)
tb75 = T_AMB + 75
rsa_75 = r_sa(tb75)[0]

# what would be needed for Tj <= limit
rsa_needed = (TJ_LIMIT + 273.15 - T_AMB) / P - RJC - RCS

# forced air along the fins at mean channel velocity v (ducted): laminar developing channel flow, Nu >= 7.54
def rsa_forced(v):
    rho, mu, k, pr, cp = air(T_AMB + 20)
    dh = 2 * GAP * H_FIN / (GAP + H_FIN)
    re = rho * v * dh / mu
    nu = max(7.54, 1.86 * (re * pr * dh / L) ** (1 / 3))
    hh = nu * k / dh
    m = np.sqrt(2 * hh / (K_AL * T_FIN)); lc = H_FIN + T_FIN / 2
    et = np.tanh(m * lc) / (m * lc)
    return 1.0 / (hh * ((W - N_FIN * T_FIN) * L + et * 2 * L * lc * N_FIN)), hh

rsa_f1 = rsa_forced(1.0)[0]
tj_forced_1ms = (T_AMB - 273.15) + P * (RJC + RCS + rsa_f1)
tj_catalogue_naive = (T_AMB - 273.15) + P * (RJC + RCS + RSA_CATALOGUE)

# warm-up of the heatsink as a lumped mass (200 g aluminium): C = m cp
C = MASS * CP_AL
tau_const = C * rsa            # constant-R lumped solution at the steady-state resistance
t_const = {s: T_AMB - 273.15 + (tb - T_AMB) * (1 - np.exp(-s / tau_const)) for s in (300, 600, 1200, 1800, 3600)}


def rhs(t, y):
    tbv = y[0]
    if tbv - T_AMB < 1e-3:
        tbv = T_AMB + 1e-3
    return [(P - (tbv - T_AMB) / r_sa(tbv)[0]) / C]


sol = solve_ivp(rhs, [0, 4 * 3600], [T_AMB + 1e-3], dense_output=True, rtol=1e-8, atol=1e-8)
t_grid = np.array([0, 120, 300, 600, 900, 1200, 1800, 2400, 3600])
base_curve = {int(t): round(float(sol.sol(t)[0]) - 273.15, 1) for t in t_grid}
rise_ss = tb - T_AMB
def t_to(frac):
    return float(brentq(lambda t: sol.sol(t)[0] - T_AMB - frac * rise_ss, 1, 4 * 3600 - 1))

ans = {
    "heatsink_mass_g": round(MASS * 1000, 0),
    "fin_gap_mm": round(GAP * 1000, 2),
    "base_temperature_C_steady_8W": round(tb - 273.15, 1),
    "sink_to_ambient_Rth_K_per_W_at_8W": round(rsa, 2),
    "sink_Rth_if_read_at_dT75K_catalogue_style": round(rsa_75, 2),
    "h_natural_W_m2K": round(h, 2),
    "fin_efficiency": round(eta, 3),
    "convection_share_of_heat": round(gc / (gc + gr), 2),
    "case_temperature_C": round(tc - 273.15, 1),
    "junction_temperature_C": round(tj - 273.15, 1),
    "junction_limit_user_C": TJ_LIMIT,
    "junction_below_datasheet_max_175C": bool((tj - 273.15) < TJ_MAX_DATASHEET),
    "fan_needed_for_90C_rule": bool((tj - 273.15) > TJ_LIMIT),
    "required_Rth_sink_ambient_K_per_W_for_90C": round(rsa_needed, 2),
    "junction_temperature_C_if_catalogue_Rth_3.0_used_naively": round(tj_catalogue_naive, 1),
    "forced_Rth_sink_ambient_K_per_W_ducted_1ms": round(rsa_f1, 2),
    "junction_temperature_C_with_1ms_airflow": round(tj_forced_1ms, 1),
    "warmup_time_constant_s_constant_R": round(tau_const, 0),
    "warmup_base_temperature_C_constantR": {str(k): round(v, 1) for k, v in t_const.items()},
    "warmup_base_temperature_C_nonlinear_h": base_curve,
    "time_to_63pct_of_rise_s_nonlinear": round(t_to(1 - np.exp(-1)), 0),
    "time_to_95pct_of_rise_s_nonlinear": round(t_to(0.95), 0),
    "heat_capacity_J_per_K": round(C, 0),
}
print(json.dumps({"answers": ans,
 "how": ("Heatsink: extruded 6063 (k 201), 60 mm wide x 100 mm long (vertical fins), 5 mm base, 9 fins 2 mm x 24 mm, black anodised e 0.85, mass from geometry. "
         "Natural convection by Bar-Cohen/Rohsenow channel correlation (Elenbaas) with air properties from Sutherland/ideal gas at the film temperature, fin efficiency tanh(mLc)/mLc, "
         "radiation from the envelope with a 0.8 view-factor allowance, solved at 8 W. IRF540N: Rjc 1.15 K/W, Rcs 1.4 K/W silicone insulating pad (typical TO-220 pad). "
         "Tj = Tamb + P (Rjc + Rcs + Rsa). Forced air: ducted laminar channel Nu >= 7.54 / Sieder-Tate developing (an unducted fan gives less; the point is only that a fan brings Rsa well below the 3.1 K/W that is needed). Warm-up: lumped C = m cp, tau = C Rsa (constant R) and an ODE with h(T) for the nonlinear case."),
  "tolerance": ("Rsa natural +-25 % (correlation spread, radiation allowance, spreading resistance ignored); Tj +-10 K; the verdict (Tj above the user's 90 C rule -> fan needed, "
                "but well below the 175 C silicon max) must match; tau +-30 %, 95 % time +-35 %. An answer built on the single-vertical-plate correlation (h ~ 5.7 W/m2K, Rsa ~ 2.9 K/W, Tj ~ 89 C, 'no fan needed') ignores boundary-layer merging in the 5 mm fin gaps and is wrong; the answer must flag the fin-gap effect or at least call the case borderline and recommend a fan. Key insight the answer should contain: the catalogue Rth (quoted at dT 75 K) is much lower than the real Rth at the actual dT of ~50 K."),
 }, indent=1))
