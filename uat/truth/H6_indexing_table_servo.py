"""Truth for H6: servo sizing of a 6-station rotary indexing table, 50 index moves per minute, with a process load held in the dwell.

Plain rigid-body mechanics in numpy, no eng-toolkit code:
  * inertia of the steel disc (1/2 m r^2) plus six fixtures as point masses (m r^2), reflected to the motor by the gear ratio squared
  * trapezoidal speed profile: accel / constant speed / decel, then a dwell, repeated every cycle (the cycle, not the move, is what heats the motor)
  * motor torque = J_motor_side * alpha_motor + load torque reflected through the gearbox with a constant efficiency (power-balance: the
    load torque costs T / (ratio * eta) when the motor drives, T * eta / ratio when the load drives the motor); in the dwell the process
    torque is held with T / ratio (no motion, no power flow: no efficiency applied, +/-8 % is the honest uncertainty)
  * exact RMS over the whole cycle by integrating the piecewise-linear torque
"""
import json
import math

import numpy as np

RHO = 7850.0
D_DISC, T_DISC = 0.500, 0.030
N_FIX, M_FIX, R_FIX = 6, 3.0, 0.220
J_LOAD = 0.5 * RHO * math.pi / 4 * D_DISC ** 2 * T_DISC * (D_DISC / 2) ** 2 + N_FIX * M_FIX * R_FIX ** 2
RATIO, ETA = 20.0, 0.92
J_GEAR_IN = 0.05e-4        # kg m2, planetary gearbox input side
FRICTION = 1.5             # N m Coulomb at the table, always opposing motion
HOLD = 200.0 * R_FIX       # N m process torque held at the table during the whole dwell (200 N pushing at 0.22 m)
ANGLE = 2 * math.pi / N_FIX
T_ACC, T_CONST, T_DEC = 0.12, 0.12, 0.12
MOVE = T_ACC + T_CONST + T_DEC
CYCLE = 60.0 / 50.0        # 50 index moves per minute
DWELL = CYCLE - MOVE
W_PEAK = ANGLE / (T_ACC / 2 + T_CONST + T_DEC / 2)         # load rad/s
ALPHA = W_PEAK / T_ACC

MOTORS = {   # name: (rotor inertia kg m2, rated N m, peak N m, rated rpm, max rpm)
    "750 W (ECMA-C20807 class)": (1.33e-4, 2.39, 7.16, 3000, 5000),
    "1 kW": (2.65e-4, 3.18, 9.54, 3000, 5000),
}


def torque_segments(j_rotor):
    """[(duration, T_start, T_end)] of the motor torque over one cycle."""
    jm = j_rotor + J_GEAR_IN
    seg = []
    w = [(0.0, W_PEAK, T_ACC, +1), (W_PEAK, W_PEAK, T_CONST, 0), (W_PEAK, 0.0, T_DEC, -1)]
    for w0, w1, dur, sgn in w:
        # motor torque at the start and end of the segment (the speed changes linearly, friction and the load are constant)
        out = []
        for wl in (w0, w1):
            alpha_m = RATIO * (w1 - w0) / dur
            inertial_load = J_LOAD * (w1 - w0) / dur       # load-side torque to accelerate the table
            fric = FRICTION if wl > 0 or (w1 > 0) else 0.0
            tl = inertial_load + fric                      # load-side torque the gearbox output must supply (positive = motor drives)
            t_gear = tl / RATIO / ETA if tl > 0 else tl * ETA / RATIO
            out.append(jm * alpha_m + t_gear)
        seg.append((dur, out[0], out[1]))
    seg.append((DWELL, HOLD / RATIO, HOLD / RATIO))
    return seg


def rms(seg):
    tot, e = 0.0, 0.0
    for dur, a, b in seg:
        e += dur * (a * a + a * b + b * b) / 3.0       # exact integral of a linearly varying torque squared
        tot += dur
    return math.sqrt(e / tot)


def main():
    rows = {}
    for name, (jr, t_rated, t_peak, n_rated, n_max) in MOTORS.items():
        seg = torque_segments(jr)
        peak = max(max(abs(a), abs(b)) for _, a, b in seg)
        r_cycle = rms(seg)
        r_move_only = rms(seg[:3])
        r_no_hold = rms(seg[:3] + [(DWELL, 0.0, 0.0)])
        jm = jr + J_GEAR_IN
        rows[name] = {"peak_torque_Nm": round(peak, 2), "rms_cycle_Nm": round(r_cycle, 2), "rms_move_only_Nm": round(r_move_only, 2),
                      "rms_cycle_if_dwell_load_forgotten_Nm": round(r_no_hold, 2), "rated_Nm": t_rated, "peak_limit_Nm": t_peak,
                      "peak_ok": bool(peak <= t_peak), "rms_ok": bool(r_cycle <= t_rated),
                      "inertia_ratio_load_over_rotor": round(J_LOAD / RATIO ** 2 / jm, 1),
                      "max_speed_rpm_at_motor": round(W_PEAK * RATIO * 60 / (2 * math.pi)), "speed_ok": bool(W_PEAK * RATIO * 60 / (2 * math.pi) <= n_max),
                      "dwell_holding_torque_Nm": round(HOLD / RATIO, 2)}
    answers = {
        "load_inertia_kg_m2": round(J_LOAD, 3), "disc_mass_kg": round(RHO * math.pi / 4 * D_DISC ** 2 * T_DISC, 1),
        "index_angle_deg": 60, "move_time_s": MOVE, "cycle_time_s": CYCLE, "dwell_s": round(DWELL, 2),
        "peak_load_speed_rad_s": round(W_PEAK, 3), "peak_load_speed_rpm": round(W_PEAK * 60 / (2 * math.pi), 1),
        "load_acceleration_rad_s2": round(ALPHA, 2), "table_torque_to_accelerate_Nm": round(J_LOAD * ALPHA, 1),
        "table_speed_in_rev_per_min": round(50 / N_FIX, 2),
        "motors": rows,
        "verdict": "Peak torque is within the peak rating for both motors, but the 750 W motor fails on RMS torque over the 1.2 s cycle (dwell holding included) and has an inertia ratio far above 10; the 1 kW motor passes RMS and peak. The dwell holding torque matters: leaving it out makes the 750 W look fine. Taking '50 per minute' as table revolutions per minute is wrong: it is 50 index moves per minute, 8.33 table rpm.",
        "fix_suggestions": "lengthen the index time (acceleration torque falls with the square of the time), or use a mechanical lock/brake in the dwell, or the 1 kW motor",
    }
    print(json.dumps({"answers": answers,
                      "how": "numpy: disc + fixture inertia, trapezoidal index profile, power-balance motor torque with constant gear efficiency, exact RMS of the piecewise-linear torque over the whole 1.2 s cycle",
                      "tolerance": "Inertia within 2 %, peak torque within 5 %, RMS within 8 % (the efficiency convention in the dwell). The 750 W RMS verdict (fail, about 12 % over rated) and the 1 kW pass must match; an answer that uses the move time instead of the cycle, or ignores the dwell load, or reads '50 per minute' as rpm, is wrong."},
                     indent=1))


if __name__ == "__main__":
    main()
