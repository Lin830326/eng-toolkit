"""Piecewise motion sizing with exact torque/power integrals and explicit gear losses."""

from __future__ import annotations

import math
import re
from typing import Annotated

from pydantic import Field

from eng_toolkit.domains.electric._common import Result, choice, fields, invalid, number, q, records, run
from eng_toolkit.domains.electric._server import server


def _integral(coefficients, start=0.0, end=1.0):
    return sum(value*(end**(index+1)-start**(index+1))/(index+1) for index, value in enumerate(coefficients))


def _statistics(segments, rotor):
    duration = sum(row[0] for row in segments)
    torque_square = power_square = regenerated = energy = speed_square = 0.0
    peak_torque = peak_power = peak_speed = 0.0
    rows = []
    elapsed = 0.0
    for dt, w0, w1, t0, t1 in segments:
        acceleration = (w1-w0)/dt
        t0, t1 = t0+rotor*acceleration, t1+rotor*acceleration
        dw, dtorque = w1-w0, t1-t0
        power = [t0*w0, t0*dw+dtorque*w0, dtorque*dw]
        power_squared = [0.0]*5
        for i, a in enumerate(power):
            for j, b in enumerate(power):
                power_squared[i+j] += a*b
        p_values = [power[0], sum(power)]
        if power[2] != 0:
            vertex = -power[1]/(2*power[2])
            if 0 < vertex < 1:
                p_values.append(sum(c*vertex**i for i, c in enumerate(power)))
        breaks = [0.0, 1.0]
        for initial, change in ((t0, dtorque), (w0, dw)):
            if change and 0 < -initial/change < 1:
                breaks.append(-initial/change)
        breaks = sorted(set(breaks))
        regen = 0.0
        for start, end in zip(breaks, breaks[1:]):
            mid = (start+end)/2
            if sum(c*mid**i for i, c in enumerate(power)) < 0:
                regen -= dt*_integral(power, start, end)
        peak_torque = max(peak_torque, abs(t0), abs(t1))
        peak_power = max(peak_power, *(abs(value) for value in p_values))
        peak_speed = max(peak_speed, abs(w0), abs(w1))
        torque_square += dt*(t0*t0+t0*t1+t1*t1)/3
        power_square += dt*_integral(power_squared)
        speed_square += dt*(w0*w0+w0*w1+w1*w1)/3
        energy += dt*_integral(power)
        regenerated += regen
        rows.append([elapsed, dt, w0, w1, acceleration, t0, t1, _integral(power), regen])
        elapsed += dt
    return {"duration": duration, "peak_torque": peak_torque,
            "rms_torque": math.sqrt(max(0.0, torque_square/duration)),
            "peak_power": peak_power, "rms_power": math.sqrt(max(0.0, power_square/duration)),
            "peak_speed": peak_speed, "rms_speed": math.sqrt(max(0.0, speed_square/duration)),
            "regenerative_energy": regenerated, "net_shaft_energy": energy, "rows": rows}


@server.tool(name="drive_sizing", description="Chapman/Fitzgerald rigid-body power-balance sizing for rotary, direct, belt, rack and screw drives. Piecewise linear load speed and constant external effort; exact RMS torque/power, gear losses, regeneration and candidate margin CSVs. Constant efficiency and friction; no elastic motion, jerk, inverter losses, cooling dynamics or rating certification. All results are unreviewed.")
def drive_sizing(
    model: Annotated[str, Field(description="Transmission model: rotary, direct, belt, rack, or screw; direct is unity-ratio rotary drive.")] = "rotary",
    profile: Annotated[str, Field(description="Up to 100 duration,start/end load speed,load effort rows; e.g. 1 s,0 rad/s,2 rad/s,1 N*m; linear uses m/s and N.")] = "",
    distance: Annotated[str, Field(description="Signed generated move, e.g. 2 rad for rotary/direct or 1 m for linear models; exclusive with profile.")] = "",
    move_time: Annotated[str, Field(description="All models: rest-to-rest move duration, e.g. 2 s; required with distance.")] = "",
    acceleration_time: Annotated[str, Field(description="All models: symmetric acceleration/deceleration time, e.g. 0.5 s; blank gives a triangle with half the move time.")] = "",
    load_torque: Annotated[str, Field(description="Rotary/direct signed external torque for generated motion, e.g. 2 N*m; not allowed with profile.")] = "",
    load_force: Annotated[str, Field(description="Linear models: signed external force for generated motion, e.g. 50 N; not allowed with profile.")] = "",
    load_inertia: Annotated[str, Field(description="Positive load-side inertia, e.g. 0.2 kg*m^2; required only for rotary/direct.")] = "",
    load_mass: Annotated[str, Field(description="Positive translated mass, e.g. 10 kg; required only for belt/rack/screw.")] = "",
    rotor_inertia: Annotated[str, Field(description="All models: motor rotor inertia, e.g. 0.01 kg*m^2; blank means zero pending candidate selection.")] = "",
    gear_ratio: Annotated[float, Field(description="Motor revolutions per transmission output revolution; positive 0.000001 through 1000000.")] = 1.0,
    efficiency: Annotated[float, Field(description="Transmission mechanical efficiency, greater than zero and at most 1; applied in both power directions.")] = 1.0,
    radius: Annotated[str, Field(description="Effective belt pulley or rack pinion radius, e.g. 0.05 m; required only for belt/rack.")] = "",
    lead: Annotated[str, Field(description="Axial travel per screw revolution, e.g. 5 mm; required only for screw.")] = "",
    coulomb_friction: Annotated[str, Field(description="Resisting Coulomb effort, e.g. 1 N*m for rotary/direct or 10 N for linear travel; defaults to zero.")] = "",
    viscous_friction: Annotated[str, Field(description="Load viscous coefficient, e.g. 1 N*m*s/rad for rotary/direct or 5 N*s/m for linear motion.")] = "",
    gravity_angle: Annotated[str, Field(description="Linear travel angle above horizontal, e.g. 90 deg; adds m*g*sin(angle); default horizontal.")] = "",
    available_torque: Annotated[str, Field(description="All models: available positive shaft torque, e.g. 10 N*m, for conservative acceleration-time estimates.")] = "",
    duty: Annotated[str, Field(description="All models: separate duration,shaft_torque duty, e.g. 1 s,2 N*m;3 s,0 N*m; at most 100 semicolon-separated rows.")] = "",
    rated_torque: Annotated[str, Field(description="All models: continuous motor torque, e.g. 5 N*m, for profile and separate duty RMS thermal screening.")] = "",
    candidates: Annotated[str, Field(description="All models: name,J,rated/max torque,rated/max speed; e.g. M1,0.01 kg*m^2,2 N*m,5 N*m,100 rad/s,200 rad/s; max 100 rows.")] = "",
):
    inputs = locals()
    def compute(handle):
        transmission = choice(model, "model", ("rotary", "direct", "belt", "rack", "screw"))
        linear = transmission in ("belt", "rack", "screw")
        ratio = number(gear_ratio, "gear_ratio", 1e-6, 1e6)
        eta = number(efficiency, "efficiency", 1e-6, 1)
        rotor = q(rotor_inertia, "rotor_inertia", "kg*m^2", zero=True, optional=True, hi=1e12) or 0.0
        if transmission == "direct" and ratio != 1:
            invalid("gear_ratio", "direct drive requires unity gear ratio")
        if transmission in ("belt", "rack"):
            arm = q(radius, "radius", "m", hi=1e6)
            if lead:
                invalid("lead", "lead applies only to screw drives")
        elif transmission == "screw":
            arm = q(lead, "lead", "m", hi=1e6)/(2*math.pi)
            if radius:
                invalid("radius", "radius applies only to belt and rack drives")
        else:
            arm = 1.0
            if radius or lead:
                invalid("radius" if radius else "lead", "rotary models do not accept linear transmission dimensions")
        conversion = ratio/arm
        if linear:
            inertia = q(load_mass, "load_mass", "kg", hi=1e12)
            if load_inertia or load_torque:
                invalid("load_inertia" if load_inertia else "load_torque", "linear models require load mass and force")
            speed_unit, distance_unit, effort_unit, viscous_unit = "m/s", "m", "N", "N*s/m"
        else:
            inertia = q(load_inertia, "load_inertia", "kg*m^2", hi=1e12)
            if load_mass or load_force or gravity_angle:
                invalid("load_mass" if load_mass else "load_force" if load_force else "gravity_angle", "rotary models require inertia and torque; specify gravity within external torque")
            speed_unit, distance_unit, effort_unit, viscous_unit = "rad/s", "rad", "N*m", "N*m*s/rad"
        friction = q(coulomb_friction, "coulomb_friction", effort_unit, zero=True, optional=True, hi=1e12) or 0.0
        viscous = q(viscous_friction, "viscous_friction", viscous_unit, zero=True, optional=True, hi=1e12) or 0.0
        angle = q(gravity_angle, "gravity_angle", "rad", zero=True, signed=True, optional=True, hi=2*math.pi) or 0.0
        gravity = inertia*9.80665*math.sin(angle) if linear else 0.0
        profile_rows = records(profile, "profile")
        motion = []
        if profile_rows:
            for name, value in (("distance", distance), ("move_time", move_time), ("acceleration_time", acceleration_time),
                                ("load_torque", load_torque), ("load_force", load_force)):
                if value:
                    invalid(name, "explicit profile and generated-motion inputs are mutually exclusive")
            for index, row in enumerate(profile_rows):
                prefix = f"profile/{index}"
                fields(row, (4,), prefix)
                duration = q(row[0], prefix+"/0", "s", lo=1e-9, hi=1e9)
                start = q(row[1], prefix+"/1", speed_unit, zero=True, signed=True, hi=1e9)
                end = q(row[2], prefix+"/2", speed_unit, zero=True, signed=True, hi=1e9)
                load = q(row[3], prefix+"/3", effort_unit, zero=True, signed=True, hi=1e12)
                if motion and not math.isclose(start, motion[-1][2], rel_tol=1e-9, abs_tol=1e-12):
                    invalid(prefix+"/1", "adjacent profile segments must have continuous speed")
                motion.append((duration, start, end, load))
        else:
            travel = q(distance, "distance", distance_unit, signed=True, hi=1e9)
            duration = q(move_time, "move_time", "s", lo=1e-9, hi=1e9)
            ramp = q(acceleration_time, "acceleration_time", "s", optional=True, lo=1e-9, hi=1e9)
            ramp = duration/2 if ramp is None else ramp
            if ramp > duration/2:
                invalid("acceleration_time", "acceleration time cannot exceed half the move duration")
            load_text = load_force if linear else load_torque
            load = q(load_text, "load_force" if linear else "load_torque", effort_unit, zero=True, signed=True, optional=True, hi=1e12) or 0.0
            top = travel/(duration-ramp)
            if abs(top) > 1e9:
                invalid("move_time", "generated speed exceeds the supported range", "input.out_of_range")
            motion.append((ramp, 0.0, top, load))
            if duration > 2*ramp:
                motion.append((duration-2*ramp, top, top, load))
            motion.append((ramp, top, 0.0, load))
        # Split at velocity and load-side power sign changes so gear efficiency
        # and Coulomb friction are constant within each exact polynomial segment.
        segments = []
        steady_load = 0.0
        for dt, start, end, external in motion:
            acceleration = (end-start)/dt
            cuts = [0.0, 1.0]
            if end != start and 0 < -start/(end-start) < 1:
                cuts.append(-start/(end-start))
            cuts.sort()
            for first, last in zip(cuts, cuts[1:]):
                velocity_mid = start+(end-start)*(first+last)/2
                sign = 1 if velocity_mid > 0 else -1 if velocity_mid < 0 else 0
                constant = inertia*acceleration+external+gravity+friction*sign
                subcuts = [first, last]
                slope = viscous*(end-start)
                if slope and first < -(constant+viscous*start)/slope < last:
                    subcuts.append(-(constant+viscous*start)/slope)
                subcuts.sort()
                for a, b in zip(subcuts, subcuts[1:]):
                    va, vb = start+(end-start)*a, start+(end-start)*b
                    effort_a, effort_b = constant+viscous*va, constant+viscous*vb
                    power_sign = ((va+vb)/2)*((effort_a+effort_b)/2)
                    factor = eta if power_sign < 0 else 1/eta
                    segments.append((dt*(b-a), conversion*va, conversion*vb,
                                     factor*effort_a/conversion, factor*effort_b/conversion))
                    steady_load = max(steady_load, abs(external+gravity+friction*sign+viscous*va)/conversion/eta,
                                      abs(external+gravity+friction*sign+viscous*vb)/conversion/eta)
        without = _statistics(segments, 0.0)
        with_rotor = _statistics(segments, rotor)
        reflected = inertia/conversion**2
        result = Result(
            ["omega_motor=k*v_load; Jreflected=Jload/k^2 (rotary) or m/k^2 (linear)",
             "Tmotor=(Jload*alpha+Text+Tc*sign(v)+b*v+gravity)*gear_factor/k+Jrotor*alpha_motor",
             "Trms=sqrt(integral(T^2 dt)/total_time); P=T*omega; Eregen=-integral(min(P,0) dt)"],
            ["Each row time is a segment duration, with linear load speed and constant external signed load effort.",
             "Positive external effort resists positive travel; Coulomb friction changes sign at reversal. Static friction is omitted.",
             "Gear efficiency is 1/eta for motoring and eta for regeneration; motor rotor inertia acts at the shaft.",
             "Drive and inverter losses, elastic modes, jerk, peak-duration ratings and brake-resistor limits are not modeled."],
            ["Rigid-body drive sizing using Chapman/Fitzgerald power balance; all torque and power statistics use exact piecewise integrals.",
             "Candidate passes mean within the stated profile and scalar limits; continuous RMS torque is only a thermal screening estimate."],
        )
        result.warnings.append("RMS torque screening assumes the continuous rating's cooling conditions. Intermittent operation, low-speed self-cooling and ambient temperature may require derating; no cooling or S1/intermittent-duty thermal model is included.")
        for suffix, stats in (("without_rotor", without), ("with_rotor", with_rotor)):
            for key in ("peak_torque", "rms_torque", "peak_power", "rms_power", "regenerative_energy", "net_shaft_energy"):
                unit = "N*m" if "torque" in key else "W" if "power" in key else "J"
                result.add(key+"_"+suffix, stats[key], unit)
        result.add("profile_duration", with_rotor["duration"], "s")
        result.add("peak_motor_speed", with_rotor["peak_speed"], "rad/s")
        result.add("rms_motor_speed", with_rotor["rms_speed"], "rad/s")
        result.add("reflected_load_inertia", reflected, "kg*m^2")
        result.add("total_motor_side_inertia", reflected+rotor, "kg*m^2")
        if rotor > 0:
            result.add("load_to_rotor_inertia_ratio", reflected/rotor)
            # At unity gear the effective rotary load includes radius/lead conversion.
            result.add("ideal_inertia_matching_ratio", math.sqrt(inertia*arm**2/rotor))
        result.data["inertia_matching_note"] = "sqrt(Jload_equivalent_at_output/Jmotor) maximizes ideal no-load acceleration for fixed motor torque; it ignores efficiency, speed limits, friction and process load."
        travel = sum(dt*(start+end)/2 for dt, start, end, _ in motion)
        result.add("net_travel", travel, distance_unit)
        result.csv(handle, "drive_profile.csv", ["time_s", "duration_s", "start_motor_speed_rad_s", "end_motor_speed_rad_s", "motor_acceleration_rad_s2", "start_motor_torque_N_m", "end_motor_torque_N_m", "mean_shaft_power_W", "regenerative_energy_J"], with_rotor["rows"])
        if available_torque:
            available = q(available_torque, "available_torque", "N*m", hi=1e12)
            reserve = available-steady_load
            result.add("conservative_acceleration_torque_reserve", reserve, "N*m")
            if reserve <= 0:
                result.data["acceleration_check"] = "fail: available torque does not exceed the conservative resisting-load bound"
            else:
                acceleration = reserve/(rotor+reflected/eta)
                result.add("conservative_available_acceleration", acceleration, "rad/s^2")
                result.add("conservative_zero_to_peak_time", with_rotor["peak_speed"]/acceleration, "s")
                result.data["acceleration_check"] = "estimated minimum time under the stated constant torque and worst resisting-load bound"
        duty_rows = records(duty, "duty")
        duty_rms = None
        if duty_rows:
            sum_time = sum_square = 0.0
            for index, row in enumerate(duty_rows):
                fields(row, (2,), f"duty/{index}")
                duration = q(row[0], f"duty/{index}/0", "s", lo=1e-9, hi=1e9)
                load = q(row[1], f"duty/{index}/1", "N*m", signed=True, zero=True, hi=1e12)
                sum_time += duration
                sum_square += duration*load*load
            duty_rms = math.sqrt(sum_square/sum_time)
            result.add("duty_rms_torque", duty_rms, "N*m")
            result.add("duty_duration", sum_time, "s")
        rated = q(rated_torque, "rated_torque", "N*m", optional=True, hi=1e12)
        if rated is not None:
            result.add("profile_thermal_torque_margin", rated-with_rotor["rms_torque"], "N*m")
            result.data["profile_thermal_check"] = "pass within stated RMS torque limit" if with_rotor["rms_torque"] <= rated else "fail: profile RMS torque exceeds stated continuous torque"
            if duty_rms is not None:
                result.add("duty_thermal_torque_margin", rated-duty_rms, "N*m")
                result.data["duty_thermal_check"] = "pass within stated RMS torque limit" if duty_rms <= rated else "fail: duty RMS torque exceeds stated continuous torque"
        candidate_table = []
        candidate_segments = []
        candidate_rows = records(candidates, "candidates")
        seen = set()
        for index, row in enumerate(candidate_rows):
            prefix = f"candidates/{index}"
            fields(row, (6,), prefix)
            name = row[0]
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}", name) or name in seen:
                invalid(prefix+"/0", "candidate names must be unique, 1-64 plain letters, digits, spaces, dot, underscore or hyphen")
            seen.add(name)
            candidate_inertia = q(row[1], prefix+"/1", "kg*m^2", zero=True, hi=1e12)
            continuous = q(row[2], prefix+"/2", "N*m", hi=1e12)
            peak = q(row[3], prefix+"/3", "N*m", hi=1e12)
            rated_speed = q(row[4], prefix+"/4", "rad/s", hi=1e9)
            max_speed = q(row[5], prefix+"/5", "rad/s", hi=1e9)
            if peak < continuous or max_speed < rated_speed:
                invalid(prefix, "maximum torque/speed must be at least the corresponding rated value")
            stats = _statistics(segments, candidate_inertia)
            margins = [continuous-stats["rms_torque"], peak-stats["peak_torque"], rated_speed-stats["peak_speed"], max_speed-stats["peak_speed"]]
            limits = (continuous, peak, rated_speed, max_speed)
            passed = all(margin >= -1e-10*limit for margin, limit in zip(margins, limits))
            candidate_table.append({"name": name, "rotor_inertia_kg_m2": candidate_inertia,
                                    "rms_torque_N_m": stats["rms_torque"], "peak_torque_N_m": stats["peak_torque"],
                                    "rated_torque_margin_N_m": margins[0], "maximum_torque_margin_N_m": margins[1],
                                    "rated_speed_margin_rad_s": margins[2], "maximum_speed_margin_rad_s": margins[3],
                                    "status": "pass within stated profile and scalar limits" if passed else "fail: one or more stated profile limits exceeded"})
            for segment in stats["rows"]:
                _, duration, start_speed, end_speed, _, start_torque, end_torque, _, _ = segment
                segment_rms = math.sqrt((start_torque**2+start_torque*end_torque+end_torque**2)/3)
                segment_peak = max(abs(start_torque), abs(end_torque))
                segment_speed = max(abs(start_speed), abs(end_speed))
                candidate_segments.append([name, segment[0], duration, start_torque, end_torque, segment_rms,
                                           continuous-stats["rms_torque"], peak-segment_peak,
                                           rated_speed-segment_speed, max_speed-segment_speed,
                                           candidate_table[-1]["status"]])
        result.data["candidates"] = candidate_table
        result.data["candidate_speed_note"] = "Conservative constant-torque screening requires peak speed at or below both rated and maximum speeds; field-weakening power curves are not inferred."
        if candidate_table:
            headers = list(candidate_table[0])
            result.csv(handle, "drive_candidates.csv", headers, [[row[name] for name in headers] for row in candidate_table])
            result.csv(handle, "drive_candidate_segments.csv",
                       ["name", "time_s", "duration_s", "start_torque_N_m", "end_torque_N_m", "segment_rms_torque_N_m",
                        "profile_rms_torque_margin_N_m", "segment_peak_torque_margin_N_m", "segment_rated_speed_margin_rad_s",
                        "segment_max_speed_margin_rad_s", "profile_status"], candidate_segments)
        return result.done("rms_torque_with_rotor")

    return run("drive_sizing", inputs, compute)
