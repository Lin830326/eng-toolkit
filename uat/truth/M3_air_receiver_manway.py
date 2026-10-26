"""Independent truth for M3: 1.2 m ID, 10 bar air receiver (P265GH) with 2:1 ellipsoidal ends, DN200 handhole cover with 8x M12 8.8.

Closed forms only (EN 13445-3 s7 / ASME VIII-1 UG-27, UG-32 for the thickness, VDI 2230-style simple joint for the bolts,
Shigley 10th ed. Table 8-17 for the bolt endurance strength). No eng-toolkit code.
"""
import json
import math

# --- inputs ---
Di = 1200.0              # mm inner diameter
p_work_bar, p_design_bar = 10.0, 11.0   # bar(g); design pressure = relief valve set pressure 11 bar(g)
Re, Rm = 265.0, 410.0    # MPa, P265GH (EN 10028-2) minimum, t <= 16 mm
z = 0.85                 # joint coefficient, spot radiography (EN 13445-3 table 5.6-1)
c_corr = 1.0             # mm corrosion allowance (person)
c_tol = 0.3              # mm plate under-tolerance (EN 10029 class B, typical)
p = p_design_bar * 0.1   # MPa

f = min(Re / 1.5, Rm / 2.4)          # EN 13445-2 6.6.1 normal operating load case, ferritic steel
e_shell = p * Di / (2 * f * z - p)   # EN 13445-3 7.4.2
t_shell_required = e_shell + c_corr + c_tol
plate_shell = math.ceil(t_shell_required * 2) / 2   # next 0.5 mm; commercial plate 6 mm
plate_shell_commercial = 6.0

# thin-wall pr/t with the joint efficiency folded into the allowable (what a membrane tool gives)
sigma_allow = f * z
t_thin_inner = p * (Di / 2) / sigma_allow
# ASME VIII-1 UG-27(c)(1) with S = 20 ksi = 138 MPa (SA-516-70 at 100 C) and E = 0.85
S_asme, E_asme = 138.0, 0.85
t_asme = p * (Di / 2) / (S_asme * E_asme - 0.6 * p)

# 2:1 semi-ellipsoidal end: EN 13445-3 7.5.2 (K=1 for Do/2ho... ratio 2:1) ~ same as ASME UG-32(d)
t_head = p * Di / (2 * f * z - 0.5 * p)          # per EN 13445-3 7.5.2 for K = 1, 2:1
t_head_asme = p * Di / (2 * S_asme * E_asme - 0.2 * p)
# a hemispherical end (sphere formula) - what a naive sphere model gives; NOT valid for a 2:1 end
t_hemisphere = p * (Di / 2) / (2 * f * z - 0.5 * p) * 1.0

# hydrotest (EN 13445-5 10.2.3.3.1): Pt = max(1.25 Ps f_a/f_t, 1.43 Ps) = 1.43 Ps for same material
Pt = 1.43 * p
e_a = plate_shell_commercial - c_tol    # new condition: no corrosion allowance
sigma_test = Pt * (Di + e_a) / (2 * e_a)
sigma_test_allow = Re / 1.05

# longitudinal stress, closed ends (reported for completeness)
sigma_hoop_work = (p_work_bar * 0.1) * (Di + (plate_shell_commercial - c_corr - c_tol)) / (2 * (plate_shell_commercial - c_corr - c_tol))

# thickness vs design pressure 6..16 bar (EN 13445 formula) for the requested chart
curve = {str(pb): round(pb * 0.1 * Di / (2 * f * z - pb * 0.1) + c_corr + c_tol, 2) for pb in range(6, 17, 2)}

# --- handhole cover: 8 x M12 8.8, gasket mean diameter 230 mm ---
d_g = 230.0
n_b = 8
As, Sp, Sut, Sy = 84.3, 580.0, 830.0, 660.0   # ISO 898-1 class 8.8 M12 (Sp 580 for d<=16 mm), As = 84.3 mm2 (ISO 724)
F_p = As * Sp                                  # proof load N
F_i = 0.75 * F_p                               # 75 % preload (reused joint), N
K = 0.2                                        # nut factor
T_tight = K * F_i * 12.0e-3                    # N m at M12
A_cover = math.pi * d_g**2 / 4
F_ext_total = p * A_cover                      # N at design pressure (11 bar)
F_ext_bolt = F_ext_total / n_b
C = 0.25                                       # joint stiffness fraction kb/(kb+km) (supplier value, fact)
F_b_max = F_i + C * F_ext_bolt
util_proof = F_b_max / F_p
sep_load_per_bolt = F_i / (1 - C)               # external load at which the joint opens
sep_ratio = sep_load_per_bolt / F_ext_bolt
# fatigue: compressor cycles 6 <-> 10 bar(g) (Pmin 6, Pmax 10): Shigley 8-... with Se = 129 MPa (class 8.8, rolled threads, Table 8-17)
Pmin, Pmax = 6.0 * 0.1, 10.0 * 0.1
dF = C * (Pmax - Pmin) * A_cover / n_b            # force range on one bolt
sa = dF / 2 / As
sm = (F_i + C * (Pmax + Pmin) / 2 * A_cover / n_b) / As
si = F_i / As
Se = 129.0
n_goodman = Se * (Sut - si) / (Sut * sa + Se * (sm - si))
n_yield_static = Sp * As / F_b_max

print(json.dumps({
    "answers": {
        "allowable_design_stress_MPa": round(f, 1),
        "allowable_times_joint_coeff_MPa": round(sigma_allow, 1),
        "shell_required_thickness_EN13445_mm": round(e_shell, 2),
        "shell_required_thickness_ASME_UG27_SA516_70_mm": round(t_asme, 2),
        "shell_thin_wall_pr_over_t_with_efficiency_in_allowable_mm": round(t_thin_inner, 2),
        "shell_plate_to_order_with_1mm_corrosion_and_0.3mm_tolerance_mm": round(t_shell_required, 2),
        "commercial_plate_mm": plate_shell_commercial,
        "ellipsoidal_2to1_end_required_mm": round(t_head, 2),
        "ellipsoidal_2to1_end_ASME_UG32_mm": round(t_head_asme, 2),
        "WRONG_hemisphere_formula_applied_to_a_2to1_end_mm": round(t_hemisphere, 2),
        "hydrotest_pressure_bar": round(Pt * 10, 2),
        "hydrotest_membrane_stress_MPa": round(sigma_test, 1),
        "hydrotest_allowable_MPa": round(sigma_test_allow, 1),
        "hoop_stress_at_10bar_in_corroded_wall_MPa": round(sigma_hoop_work, 1),
        "required_thickness_vs_design_pressure_mm_(incl_c)": curve,
        "out_of_scope_must_be_flagged": ["nozzle / opening reinforcement (EN 13445-3 s9, ASME UG-37)", "shell-to-end and nozzle weld details", "external loads, supports and saddles", "cyclic (fatigue) assessment of the vessel itself", "any code compliance statement: PED/ASME stamping needs a notified body / code calculation"],
        "handhole_cover": {
            "pressure_force_on_cover_kN": round(F_ext_total / 1e3, 2),
            "force_per_bolt_kN": round(F_ext_bolt / 1e3, 3),
            "M12_8.8_stress_area_mm2": As, "proof_load_kN": round(F_p / 1e3, 2),
            "preload_75pct_kN": round(F_i / 1e3, 2),
            "tightening_torque_Nm_K0.2": round(T_tight, 1),
            "bolt_max_load_kN_C0.25": round(F_b_max / 1e3, 2),
            "bolt_utilisation_vs_proof": round(util_proof, 3),
            "joint_separation_margin_x": round(sep_ratio, 2),
            "fatigue_force_range_per_bolt_N": round(dF, 0),
            "fatigue_stress_amplitude_MPa": round(sa, 2),
            "fatigue_goodman_safety_factor_Se129": round(n_goodman, 1),
            "note": "bolt fatigue / joint stiffness is outside the toolkit's bolt check (tension only); the answer is either a hand calculation clearly labelled as such or an honest 'the tool does not do fatigue'. C = 0.25 is the supplier fact; with a soft gasket C is larger."
        },
    },
    "how": "EN 13445-3 7.4.2 shell e = p Di /(2 f z - p), f = min(Re/1.5, Rm/2.4) (EN 13445-2), z = 0.85; ASME VIII-1 UG-27 with SA-516-70 S = 138 MPa as cross-check; 2:1 ellipsoidal end e = p Di /(2 f z - 0.5 p) (EN 13445-3 7.5) / UG-32(d); hydrotest 1.43 Ps; bolt: ISO 898-1 class 8.8 M12 (As 84.3 mm2, Sp 580 MPa), T = K F d, simple joint-stiffness fraction C, Goodman with Se = 129 MPa (Shigley Table 8-17, class 8.8 rolled threads, Sut = 830 MPa).",
    "tolerance": "shell and end thickness within 3 % of the EN 13445 value for the person's allowable (a thin-wall p r/t with the joint coefficient folded into the allowable differs by < 1 %; the ASME number 5.7 mm uses a different, lower allowable (138 MPa) and is a different code, not a discrepancy); the 6 mm plate choice; the sphere/hemisphere shortcut for the 2:1 end must be rejected (it gives half the needed thickness); torque within 5 % for K = 0.2; bolt fatigue factor within 15 % if C = 0.25 and Se = 129 MPa are used; nozzle reinforcement must be flagged as not checked."
}, indent=1))
