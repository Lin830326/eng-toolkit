"""Truth for F5: calibrate a TDK/EPCOS B57861S0103F040 10 k NTC (R/T no. 8016) from its datasheet table, read a bench measurement, then size a heatsink.

Independent of eng-toolkit: scipy.optimize.least_squares / curve_fit for Steinhart-Hart and beta fits with covariance-based t95 intervals,
Monte-Carlo for the datasheet tolerances, and a brute-force grid for the fin optimisation. Prints JSON {answers, how, tolerance}.

Source of the table: TDK Electronics "NTC thermistors, miniature sensors with bendable wires S861" data sheet, R/T characteristics no. 8016
(B25/100 = 3988 K, R25 = 10 kOhm; part B57861S0103F040: R25 +-1 %, B +-1 %). RT/R25 values copied from the table; resistance = ratio x 10 kOhm.
"""
import json
import numpy as np
from scipy import stats
from scipy.optimize import curve_fit

TABLE = [(-40.0, 336500.0), (-35.0, 242600.0), (-30.0, 177000.0), (-25.0, 130400.0), (-20.0, 97070.0), (-15.0, 72930.0), (-10.0, 55330.0),
         (-5.0, 42320.0), (0.0, 32650.0), (5.0, 25390.0), (10.0, 19900.0), (15.0, 15710.0), (20.0, 12490.0), (25.0, 10000.0), (30.0, 8057.0),
         (35.0, 6531.0), (40.0, 5327.0), (45.0, 4369.0), (50.0, 3603.0), (55.0, 2986.0), (60.0, 2488.0), (65.0, 2083.0), (70.0, 1752.0),
         (75.0, 1481.0), (80.0, 1258.0), (85.0, 1072.0), (90.0, 917.7), (95.0, 788.5), (100.0, 680.0), (105.0, 588.6), (110.0, 511.2),
         (115.0, 445.4), (120.0, 389.3), (125.0, 341.7)]
T_C = np.array([r[0] for r in TABLE])
R = np.array([r[1] for r in TABLE])
RANGE = (T_C >= 0) & (T_C <= 100)          # the working range of the user: 0 .. 100 C
tc, r = T_C[RANGE], R[RANGE]
tk = tc + 273.15
lnr = np.log(r)


def sh(x, a, b, c):
    return a + b * x + c * x ** 3


# Steinhart-Hart (3 term, no squared term): fit 1/T on ln R, absolute temperature errors converted to K via sigma = T^2 * dInv
inv_sigma = np.full(tk.shape, 1.0)
popt, pcov = curve_fit(sh, lnr, 1 / tk, p0=[1e-3, 2e-4, 1e-7], sigma=(0.01 / tk ** 2) * inv_sigma, absolute_sigma=False)
res_inv = 1 / tk - sh(lnr, *popt)
dof = len(tk) - 3
ci = stats.t.ppf(0.975, dof) * np.sqrt(np.diag(pcov))
t_sh = 1 / sh(lnr, *popt) - 273.15
err_sh = t_sh - tc

# beta model through R25 and B25/100 = 3988 K from the datasheet
B100 = 3988.0
t_beta = 1 / (1 / 298.15 + np.log(r / 10000.0) / B100) - 273.15
err_beta = t_beta - tc
# beta fit with the best B over the range, free R25
def beta_model(x, a, b):          # 1/T = 1/T0 + (ln R - ln R0)/B -> 1/T = a + b lnR
    return a + b * x
pb, cb = curve_fit(beta_model, lnr, 1 / tk, p0=[1e-3, 2.5e-4])
t_betafit = 1 / beta_model(lnr, *pb) - 273.15
err_betafit = t_betafit - tc


def temp_from_r(rr, p=popt):
    return 1 / sh(np.log(rr), *p) - 273.15


R_MEAS, P_TEST, T_AMB = 2150.0, 60.0, 25.0
t_meas = float(temp_from_r(R_MEAS))
# propagate the fit covariance (delta method) to the temperature at R_MEAS
x0 = np.log(R_MEAS)
tk_m = t_meas + 273.15
var_fit = (tk_m ** 2) ** 2 * (np.array([1.0, x0, x0 ** 3]) @ pcov @ np.array([1.0, x0, x0 ** 3]))
u_fit = float(np.sqrt(var_fit))
tcrit = stats.t.ppf(0.975, dof)

# part tolerances: R25 +-1 %, B +-1 % (treated as uniform limits) -> Monte Carlo of the temperature inferred from the reading
rng = np.random.default_rng(1)
n = 200000
d_r = rng.uniform(-0.01, 0.01, n)
d_b = rng.uniform(-0.01, 0.01, n)
# the thermistor's true curve: R = R25 (1+d_r) * [table ratio]^(1+d_b) around T25 ; invert for the given reading
ln_ratio_nom = np.log(R_MEAS / 10000.0)
# nominal ratio -> real: ln(R/(R25(1+dr))) = (1+db) * ln_ratio_table(T)  -> ln_ratio_table = (ln(R/R25') )/(1+db)
ln_ratio_table = (np.log(R_MEAS / (10000.0 * (1 + d_r)))) / (1 + d_b)
tt = np.array([temp_from_r(10000.0 * np.exp(v)) for v in ln_ratio_table[:20000]])
tol_95 = float(np.percentile(np.abs(tt - t_meas), 95))
tol_max = float(np.max(np.abs(tt - t_meas)))

rth_meas = (t_meas - T_AMB) / P_TEST
rth_lo, rth_hi = (t_meas - tol_max - T_AMB) / P_TEST, (t_meas + tol_max - T_AMB) / P_TEST

# ----- heatsink optimisation (forced convection, fan datasheet h = 25 W/m2K) -----
W, L, TB, H = 0.100, 0.080, 0.006, 0.030
K_AL, RHO_AL, HC = 200.0, 2700.0, 25.0
RTH_MAX, GAP_MIN, T_MIN = 0.55, 0.003, 0.0015   # extrusion limit: fin height / thickness <= 20 -> t >= 1.5 mm


def rth(nf, t):
    lc = H + t / 2
    m = np.sqrt(2 * HC / (K_AL * t))
    eta = np.tanh(m * lc) / (m * lc)
    a_f = 2 * lc * L
    a_b = (W - nf * t) * L
    return 1.0 / (HC * (a_b + nf * eta * a_f))


def mass(nf, t):
    return RHO_AL * L * (W * TB + nf * t * H)


best = None
rows = []
for nf in range(3, 40):
    for t_mm in np.arange(1.5, 5.01, 0.1):
        t = t_mm / 1000
        gap = (W - nf * t) / (nf - 1)
        if gap < GAP_MIN or nf * t >= W:
            continue
        rt = rth(nf, t)
        if rt <= RTH_MAX:
            m_ = mass(nf, t)
            rows.append((m_, nf, t_mm, rt, gap))
            if best is None or m_ < best[0]:
                best = (m_, nf, t_mm, rt, gap)
rows.sort()
top3 = [{"fins": int(x[1]), "thickness_mm": round(float(x[2]), 1), "mass_g": round(x[0] * 1000, 1), "Rth_K_per_W": round(x[3], 3), "gap_mm": round(x[4] * 1000, 2)} for x in rows[:3]]
# best mass for each fin count (for the plot)
by_n = {}
for m_, nf, t_mm, rt, gap in rows:
    if nf not in by_n or m_ < by_n[nf][0]:
        by_n[nf] = (m_, t_mm, rt)
curve = [{"fins": nf, "min_mass_g": round(v[0] * 1000, 1), "thickness_mm": round(float(v[1]), 1)} for nf, v in sorted(by_n.items())]

ans = {
    "steinhart_hart_form": "1/T = A + B ln(R) + C ln(R)^3 fitted on 0..100 C (21 datasheet points)",
    "SH_A": popt[0], "SH_B": popt[1], "SH_C": popt[2],
    "SH_95pct_halfwidth_A_B_C": [float(v) for v in ci],
    "SH_residual_rms_K": float(np.sqrt(np.mean(err_sh ** 2))),
    "SH_max_abs_error_K_0_100C": float(np.max(np.abs(err_sh))),
    "beta_datasheet_B25_100_3988K_max_abs_error_K_0_100C": float(np.max(np.abs(err_beta))),
    "beta_best_fit_B_K": float(1 / pb[1]),
    "beta_best_fit_max_abs_error_K_0_100C": float(np.max(np.abs(err_betafit))),
    "reading_ohm": R_MEAS,
    "temperature_from_SH_C": round(t_meas, 2),
    "fit_only_95pct_halfwidth_K": float(tcrit * u_fit),
    "datasheet_tolerance_R25_1pct_B_1pct_95pct_halfwidth_K": round(tol_95, 2),
    "datasheet_tolerance_worst_case_halfwidth_K": round(tol_max, 2),
    "heatsink_Rth_measured_K_per_W": round(rth_meas, 3),
    "heatsink_Rth_range_with_tolerance_K_per_W": [round(rth_lo, 3), round(rth_hi, 3)],
    "new_heatsink_requirement": f"Rth <= {RTH_MAX} K/W, fan h = {HC} W/m2K, base {W*1000:.0f} x {L*1000:.0f} x {TB*1000:.0f} mm, fins {H*1000:.0f} mm tall, gap >= {GAP_MIN*1000:.0f} mm, fin thickness >= {T_MIN*1000:.1f} mm (extrusion aspect ratio 20:1), 6063 k=200",
    "optimum_fins": int(best[1]), "optimum_thickness_mm": round(float(best[2]), 1), "optimum_mass_g": round(best[0] * 1000, 1),
    "optimum_Rth_K_per_W": round(best[3], 3), "optimum_gap_mm": round(best[4] * 1000, 2),
    "continuous_relaxation_note": "treating the fin count as continuous gives n = 14.2, t = 1.5 mm, 267.7 g; rounding down to 14 violates Rth unless t is raised to 2.2 mm (329 g); 15 x 1.5 mm is the true optimum",
    "three_best_designs": top3,
    "min_mass_per_fin_count": curve,
}
print(json.dumps({"answers": ans,
  "how": ("Datasheet R/T no. 8016 table (TDK B57861S0103F040), 0..100 C (21 points), curve_fit of 1/T = A + B lnR + C lnR^3 with covariance scaled by residual variance and t95 (18 dof); "
          "beta model from B25/100 = 3988 K and a free two-parameter beta fit; the reading's temperature from the SH fit, delta-method fit uncertainty, Monte-Carlo of the datasheet tolerances "
          "(R25 +-1 %, B +-1 %); heat-sink Rth = 1/(h (A_base + n eta A_fin)) with corrected fin length H + t/2, brute-force grid over fin count 3..39 and thickness 1.5..5.0 mm (0.1 step) "
          "for minimum mass with Rth <= 0.55 K/W and gap >= 3 mm."),
  "tolerance": ("SH coefficients: any equivalent fit (weighting-dependent: A 1.124-1.128e-3, B 2.34-2.35e-4, C 8.5-8.8e-8), judged by the temperature error: max error over 0..100 C about 0.01 K for SH, ~1 K for the "
                "datasheet beta. Temperature at the reading (64.1 C) +-0.05 K from the fit; the fit-only CI is tiny (<0.01 K) and the honest uncertainty is the part tolerance (about 0.6 K at 95 %, 0.7 K worst case): an answer that quotes "
                "only the fit CI as 'the uncertainty' is incomplete. Rth +-0.01 K/W. Optimum: same fin count and thickness within one grid step, mass within 3 %. "
                "A continuous optimiser needs rounding of the fin count to an integer."),
 }, indent=1, default=float))
