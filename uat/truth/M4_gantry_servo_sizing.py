"""Independent truth for M4: X-axis belt gantry, 80 kg, 1 m in 0.6 s, Delta ECMA-C20807 (750 W) + 5:1 planetary.

Pure numpy/closed form. No eng-toolkit imports. Rigid body, trapezoidal speed profile 1/3 accel, 1/3 cruise, 1/3 decel,
Coulomb friction constant, constant mechanical efficiency (applied as 1/eta motoring, eta braking), rotor inertia added at the
motor shaft. Cycle: 0.6 s forward, 0.4 s dwell, 0.6 s back, 0.4 s dwell (period 2.0 s).
"""
import json
import numpy as np

# --- inputs (person's data and datasheet values) ---
m_carriage = 80.0            # kg, carriage + payload (person)
m_belt = 1.2                 # kg, belt run that moves with the carriage (belt datasheet, fact)
J_pulleys = 0.0011 + 0.0006  # kg m^2, drive + idler pulley (CAD, fact), at the pulley side
r = 0.0637 / 2               # m, HTD 5M 40-tooth pulley pitch diameter 63.66 mm
i = 5.0                      # planetary ratio (motor revs per pulley rev)
eta = 0.97 * 0.96            # gearbox 0.97, belt+bearings 0.96 -> 0.931
J_motor = 1.13e-4            # kg m^2 Delta ECMA-C20807RS rotor (no brake) from a reseller datasheet listing
J_gb = 0.20e-4               # kg m^2 gearbox input inertia (brochure, fact)
F_c = 15.0                   # N, Coulomb friction of guides + seals (measured, fact)
T_rated, T_peak = 2.39, 7.16 # N m, motor datasheet
n_rated, n_max = 3000.0, 5000.0   # rpm
d, t_move, t_a = 1.0, 0.6, 0.2
t_dwell = 0.4
g = 9.81

m_eq = m_carriage + m_belt + J_pulleys / r**2            # kg translating at the carriage
v = d / (t_move - t_a)                                   # cruise speed (trapezoid: d = v (t - ta))
a = v / t_a


def phase_torque(acc, vel, sign_motion=1.0):
    """motor shaft torque for load acceleration `acc`, speed `vel` (carriage), moving in the +direction (sign_motion=+1)"""
    F = m_eq * acc + F_c * np.sign(vel if vel != 0 else sign_motion)   # force the belt must deliver at the carriage
    T_pulley = F * r
    J_rot = J_motor + J_gb
    alpha_m = acc / r * i
    P_sign = F * vel
    if P_sign >= 0:   # motoring (or standstill): losses add to the motor torque
        T_m = T_pulley / (i * eta)
    else:             # braking: losses reduce the torque the motor must absorb
        T_m = T_pulley * eta / i
    return T_m + J_rot * alpha_m


# time-stepped forward move
dt = 1e-4
t = np.arange(0, t_move, dt) + dt / 2
acc = np.where(t < t_a, a, np.where(t < t_move - t_a, 0.0, -a))
vel = np.where(t < t_a, a * t, np.where(t < t_move - t_a, v, v - a * (t - (t_move - t_a))))
T = np.array([phase_torque(ac, ve) for ac, ve in zip(acc, vel)])
w_m = vel / r * i                                         # rad/s motor
n_rpm = w_m * 60 / (2 * np.pi)

T_peak_acc = T.max()
T_peak_dec = T.min()
# one full cycle (forward + dwell + back + dwell): back move mirrors forward -> same |T|, signs flipped
Tsq_int = 2 * np.sum(T**2) * dt
T_rms_cycle = np.sqrt(Tsq_int / (2 * (t_move + t_dwell)))
T_rms_move = np.sqrt(np.sum(T**2) * dt / t_move)
peak_speed_rpm = n_rpm.max()
ratio_inertia = (m_eq * r**2 / i**2) / (J_motor + J_gb)
# smallest motor continuous torque with 20 % margin; and best ratio under 5000 rpm
i_max_speed = n_max * 2 * np.pi / 60 / (v / r)
mech_power_peak = (T * w_m).max()

# what-if: triangular profile (no cruise) = the assumption an assistant may make if it does not ask
v_tri = 2 * d / t_move
a_tri = v_tri / (t_move / 2)

h = t_move / 2
tt = np.arange(0, t_move, dt) + dt / 2
acc_t = np.where(tt < h, a_tri, -a_tri)
vel_t = np.where(tt < h, a_tri * tt, v_tri - a_tri * (tt - h))
T_t = np.array([phase_torque(ac, ve) for ac, ve in zip(acc_t, vel_t)])
tri_peak = T_t.max()
tri_rms = np.sqrt(2 * np.sum(T_t**2) * dt / (2 * (t_move + t_dwell)))

# torque-speed points (speed rpm, torque Nm) at the corners of the profile, forward move
corners = [(0, 0), (peak_speed_rpm, phase_torque(a, v))]
pts = {
    "accel_end": [float(round(peak_speed_rpm, 1)), float(round(phase_torque(a, a * t_a), 3))],
    "cruise": [float(round(peak_speed_rpm, 1)), float(round(phase_torque(0, v), 3))],
    "decel_start": [float(round(peak_speed_rpm, 1)), float(round(phase_torque(-a, v), 3))],
}

print(json.dumps({
    "answers": {
        "cruise_speed_m_per_s": round(v, 3),
        "acceleration_m_per_s2": round(a, 3),
        "acceleration_g": round(a / g, 3),
        "peak_motor_speed_rpm": round(peak_speed_rpm, 0),
        "motor_max_speed_rpm": n_max,
        "speed_ok": bool(peak_speed_rpm <= n_max),
        "peak_motor_torque_Nm": round(T_peak_acc, 3),
        "motor_peak_torque_limit_Nm": T_peak,
        "peak_torque_ok": bool(T_peak_acc <= T_peak),
        "decel_torque_Nm": round(T_peak_dec, 3),
        "rms_torque_full_cycle_Nm": round(T_rms_cycle, 3),
        "rms_torque_during_move_only_Nm": round(T_rms_move, 3),
        "motor_rated_torque_Nm": T_rated,
        "rms_ok": bool(T_rms_cycle <= T_rated),
        "rms_over_rated_ratio": round(T_rms_cycle / T_rated, 2),
        "reflected_load_to_rotor_inertia_ratio": round(ratio_inertia, 1),
        "verdict": "NOT adequate: speed OK (3750 of 5000 rpm), peak torque over the 7.16 N.m limit (about 7.4), RMS torque about 1.8x the 2.39 N.m continuous rating (thermal overload) and load/rotor inertia ratio about 25. A motor of at least ~4.4 N.m continuous and ~7.5 N.m peak (1.5 kW class) or a lower acceleration is needed.",
        "minimum_continuous_torque_needed_Nm": round(T_rms_cycle, 2),
        "highest_gear_ratio_allowed_by_5000rpm": round(i_max_speed, 2),
        "triangular_profile_alt": {"v_m_s": round(v_tri, 2), "a_m_s2": round(a_tri, 2), "peak_speed_rpm": round(v_tri / r * i * 60 / (2*np.pi), 0), "peak_torque_Nm": round(tri_peak, 2), "rms_cycle_Nm": round(tri_rms, 2), "note": "if the assistant assumes a triangle (no cruise): speed just under 5000 rpm, peak torque lower than the trapezoid but RMS still far above 2.39 N.m, so the verdict is the same"},
        "torque_speed_points_rpm_Nm": pts,
        "equivalent_moving_mass_kg": round(m_eq, 2),
    },
    "how": "Closed-form rigid-body trapezoid (1/3,1/3,1/3 of 0.6 s) evaluated on a 0.1 ms grid with numpy: F = m_eq a + Fc sign(v), T_motor = F r /(i eta) when motoring or F r eta / i when braking, plus (J_rotor+J_gearbox_input) * alpha_motor; RMS over the 2.0 s cycle (0.4 s dwell after each move). Motor datasheet (Delta ECMA-C20807RS: 2.39 N.m rated, 7.16 N.m peak, 3000/5000 rpm, 1.13e-4 kg.m2 from reseller listings) are INPUTS the person quotes.",
    "tolerance": "peak and RMS torque within 5 %; cruise speed and acceleration within 1 %; the verdict (RMS torque exceeds the rated torque, inertia ratio ~20) must be stated. Efficiency convention (1/eta motoring, eta braking) and friction sign matter at the 2-5 % level. If the assistant uses a different cycle (e.g. no dwell) the RMS rises to about the move-only value and the verdict is unchanged."
}, indent=1))
