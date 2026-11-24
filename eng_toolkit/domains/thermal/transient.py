"""``thermal_transient``: lumped capacitance, one-term series for a plane wall / long cylinder / sphere, semi-infinite solid.

Formulas (textbook closed forms; Incropera, Cengel, Carslaw & Jaeger)
* lumped capacitance: tau = rho V cp / (h As),  (T - Tinf)/(Ti - Tinf) = exp(-t/tau),  valid for Bi = h (V/As) / k < 0.1
* one-term series (first eigenvalue z1), Bi = h L / k with L = half thickness or radius, Fo = alpha t / L^2, alpha = k/(rho cp):
  theta/theta_i = C1 exp(-z1^2 Fo) * shape(z1 x*), valid for Fo > 0.2
    plane wall   z tan z = Bi;       C1 = 4 sin z / (2 z + sin 2z);                shape = cos(z x*);       Q/Q0 = 1 - theta0* sin z / z
    long cylinder z J1(z)/J0(z) = Bi; C1 = 2 J1(z) / (z (J0^2 + J1^2));           shape = J0(z r*);        Q/Q0 = 1 - theta0* 2 J1(z) / z
    sphere       1 - z cot z = Bi;   C1 = 4 (sin z - z cos z) / (2 z - sin 2z);   shape = sin(z r*)/(z r*); Q/Q0 = 1 - theta0* 3 (sin z - z cos z) / z^3
* lumped capacitance with a heat input Q (W) and the table mode: C dT/dt = Q - h As (T - Tinf), R = 1/(h As), C = rho V cp:
    T(t) = Tinf + Q R + (Ti - Tinf - Q R) exp(-t/tau)   (Ti = Tinf: T = Tinf + Q R (1 - exp(-t/tau))); steady state Tinf + Q R
* semi-infinite solid, eta = x / (2 sqrt(alpha t)):
    imposed surface temperature Ts:  (T - Ts)/(Ti - Ts) = erf(eta)
    convective surface:  (T - Ti)/(Tinf - Ti) = erfc(eta) - exp(h x/k + h^2 alpha t/k^2) erfc(eta + h sqrt(alpha t)/k)
    contact of two semi-infinite solids: Ts = (e_A T_A + e_B T_B)/(e_A + e_B), effusivity e = sqrt(k rho cp)
"""

from __future__ import annotations

import math
from typing import Annotated, Any

from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.thermal._common import (
    DIM_CP,
    DIM_H,
    DIM_K,
    DIM_POWER,
    QuantityIn,
    TemperatureIn,
    UnitSystemIn,
    check_data,
    choice,
    is_blank,
    out,
    plain,
    plain_number,
    positive_quantity,
    split_list,
    temperature,
    whole_number,
)
from eng_toolkit.domains.thermal._common import unit_system as units_for
from eng_toolkit.domains.thermal._server import ENGINE, server
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import UnitsError

MAX_TABLE_POINTS = 2000
DEFAULT_TABLE_POINTS = 101
MODELS = ("lumped", "plane_wall", "cylinder", "sphere", "semi_infinite")
SURFACE_CONDITIONS = ("fixed_temperature", "convection", "contact")
J01 = 2.404825557695773  # first zero of J0


# ----------------------------------------------------------------------------------------------- special functions


def bessel_j0(x: float) -> float:
    term = total = 1.0
    q = x * x / 4.0
    for k in range(1, 80):
        term *= -q / (k * k)
        total += term
        if abs(term) < 1e-18:
            break
    return total


def bessel_j1(x: float) -> float:
    term = total = x / 2.0
    q = x * x / 4.0
    for k in range(1, 80):
        term *= -q / (k * (k + 1))
        total += term
        if abs(term) < 1e-18 * max(abs(total), 1e-300):
            break
    return total


def _sin_minus_z_cos(z: float) -> float:
    """sin z - z cos z, without cancellation for small z."""
    if z < 0.1:
        z2 = z * z
        return z * z2 * (1.0 / 3.0 - z2 * (1.0 / 30.0 - z2 * (1.0 / 840.0 - z2 / 45360.0)))
    return math.sin(z) - z * math.cos(z)


def _x_minus_sin(x: float) -> float:
    """x - sin x, without cancellation for small x."""
    if x < 0.2:
        x2 = x * x
        return x * x2 * (1.0 / 6.0 - x2 * (1.0 / 120.0 - x2 * (1.0 / 5040.0 - x2 / 362880.0)))
    return x - math.sin(x)


def erfcx(z: float) -> float:
    """exp(z^2) erfc(z) for z >= 0, without overflow."""
    if z < 20.0:
        return math.exp(z * z) * math.erfc(z)
    z2 = z * z
    return (1.0 - 1.0 / (2.0 * z2) + 3.0 / (4.0 * z2 * z2) - 15.0 / (8.0 * z2**3) + 105.0 / (16.0 * z2**4)) / (z * math.sqrt(math.pi))


def first_eigenvalue(shape: str, bi: float) -> float:
    """The first positive root z1 of the eigencondition of the plane wall, long cylinder or sphere."""
    if shape == "plane_wall":
        hi, f = math.pi / 2.0, lambda z: z * math.sin(z) - bi * math.cos(z)
    elif shape == "cylinder":
        hi, f = J01, lambda z: z * bessel_j1(z) - bi * bessel_j0(z)
    else:
        hi, f = math.pi, lambda z: _sin_minus_z_cos(z) - bi * math.sin(z)
    lo = 0.0
    for _ in range(300):
        mid = 0.5 * (lo + hi)
        if f(mid) < 0.0:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-16 * hi:
            break
    return 0.5 * (lo + hi)


def one_term(shape: str, bi: float) -> tuple[float, float]:
    """(z1, C1) of the one-term solution."""
    z = first_eigenvalue(shape, bi)
    if shape == "plane_wall":
        return z, 4.0 * math.sin(z) / (2.0 * z + math.sin(2.0 * z))
    if shape == "cylinder":
        j0, j1 = bessel_j0(z), bessel_j1(z)
        return z, 2.0 * j1 / (z * (j0 * j0 + j1 * j1))
    return z, 4.0 * _sin_minus_z_cos(z) / _x_minus_sin(2.0 * z)


def shape_factor(shape: str, z: float, pos: float) -> float:
    if shape == "plane_wall":
        return math.cos(z * pos)
    if shape == "cylinder":
        return bessel_j0(z * pos)
    return 1.0 if pos == 0.0 else math.sin(z * pos) / (z * pos)


def energy_fraction(shape: str, z: float, theta0: float) -> float:
    """Q/Q0 for the one-term solution with centre value ``theta0`` = C1 exp(-z^2 Fo)."""
    if shape == "plane_wall":
        return 1.0 - theta0 * math.sin(z) / z
    if shape == "cylinder":
        return 1.0 - theta0 * 2.0 * bessel_j1(z) / z
    return 1.0 - theta0 * 3.0 * _sin_minus_z_cos(z) / z**3


def semi_infinite_convection(eta: float, beta: float) -> float:
    """(T - Ti)/(Tinf - Ti) for a convective surface; beta = h sqrt(alpha t)/k."""
    return math.erfc(eta) - math.exp(-eta * eta) * erfcx(eta + beta)


def _in_system(kelvin: float, units: dict[str, str]) -> float:
    """``kelvin`` expressed in the temperature unit of the output system (degC or degF), for the CSV."""
    return float(out(kelvin, "temperature", units, label="temperature").quantity)


def _csv_text(header: list[str], rows: list[list[float]]) -> str:
    return ",".join(header) + chr(10) + "".join(",".join(repr(float(x)) for x in row) + chr(10) for row in rows)


def _table_times(times_raw: str, t_end_raw: str, points_raw: Any) -> list[float]:
    """The ascending time list (s) of the table: from 'times', or evenly spaced 0 .. t_end with 'points' rows."""
    if not is_blank(times_raw):
        items = split_list(times_raw, name="times")
        if not 1 <= len(items) <= MAX_TABLE_POINTS:
            raise UnitsError("input.out_of_range", f"'times' has {len(items)} entries; allowed 1 .. {MAX_TABLE_POINTS}", {"param": "times", "count": len(items), "max": MAX_TABLE_POINTS})
        seq = [positive_quantity(item, name="times", dimension="time", unit="s", lo=0.0, hi=1e11, allow_zero=True) for item in items]
        if any(b <= a for a, b in zip(seq, seq[1:])):
            raise UnitsError("input.invalid", "'times' must be strictly ascending", {"param": "times"})
        return seq
    t_end_s = positive_quantity(t_end_raw, name="t_end", dimension="time", unit="s", lo=1e-6, hi=1e11)
    n = whole_number(points_raw, name="points", lo=2, hi=MAX_TABLE_POINTS)
    return [t_end_s * i / (n - 1) for i in range(n)]


@server.tool(
    name="transient",
    description=(
        "Transient conduction after a sudden change of the surroundings. model='lumped': lumped capacitance with the Biot "
        "check (warns above 0.1), time constant tau, T(t), the time to reach a target temperature, and (lumped only) a "
        "warm-up with a constant 'heat_input' (e.g. '8 W'): give 'times' (list) or 't_end' with 'points' and ONE call returns "
        "the whole T(t) table (CSV temperature_vs_time.csv, time in s) plus tau and the final temperature. "
        "model='plane_wall' | 'cylinder' | 'sphere': one-term series (Biot, Fourier number, warns below 0.2). "
        "model='semi_infinite': T(depth, time) by erf/erfc or contact temperature. Temperatures are absolute. "
        "Give 'time' or 'target_temperature' (or the table inputs for lumped)."
    ),
)
def transient(
    model: Annotated[str, Field(description="'lumped', 'plane_wall', 'cylinder', 'sphere' or 'semi_infinite'")],
    initial_temperature: Annotated[TemperatureIn, Field(description="Uniform initial temperature of the body, e.g. '200 degC'")],
    ambient_temperature: Annotated[
        TemperatureIn,
        Field(description="Fluid temperature after the step, e.g. '20 degC' (semi_infinite with fixed_temperature: the imposed surface temperature; with contact: not used)"),
    ],
    conductivity: Annotated[QuantityIn, Field(description="Body conductivity, e.g. '15 W/(m*K)'")],
    density: Annotated[QuantityIn, Field(description="Body density, e.g. '7800 kg/m^3'")],
    specific_heat: Annotated[QuantityIn, Field(description="Body specific heat, e.g. '500 J/(kg*K)'")],
    h: Annotated[str, Field(description="Convection coefficient, e.g. '100 W/(m^2*K)' (all models except semi_infinite with fixed_temperature / contact)")] = "",
    time: Annotated[str, Field(description="Elapsed time, e.g. '120 s'. Give this or 'target_temperature'.")] = "",
    target_temperature: Annotated[str, Field(description="Lumped / series only: temperature to reach (between initial and ambient); returns the time instead")] = "",
    volume: Annotated[str, Field(description="lumped only: body volume, e.g. '1e-4 m^3'")] = "",
    surface_area: Annotated[str, Field(description="lumped only: surface area exposed to the fluid, e.g. '0.03 m^2'")] = "",
    size: Annotated[str, Field(description="plane_wall: HALF the thickness; cylinder / sphere: the radius, e.g. '10 mm'")] = "",
    position: Annotated[float, Field(description="plane_wall / cylinder / sphere: location as a fraction of 'size', 0 = centre (default), 1 = surface")] = 0.0,
    depth: Annotated[str, Field(description="semi_infinite: depth below the surface, e.g. '5 mm' (blank = at the surface)")] = "",
    surface_condition: Annotated[str, Field(description="semi_infinite: 'fixed_temperature' (default), 'convection' (needs h) or 'contact' (needs the other_* inputs)")] = "fixed_temperature",
    other_temperature: Annotated[str, Field(description="contact: initial temperature of the other semi-infinite body")] = "",
    other_conductivity: Annotated[str, Field(description="contact: conductivity of the other body")] = "",
    other_density: Annotated[str, Field(description="contact: density of the other body")] = "",
    other_specific_heat: Annotated[str, Field(description="contact: specific heat of the other body")] = "",
    body_thickness: Annotated[str, Field(description="semi_infinite, optional: real thickness of the body, to check the semi-infinite assumption")] = "",
    heat_input: Annotated[str, Field(description="lumped only: constant heat power into the body, e.g. '8 W' (blank = 0, a pure cool-down). Steady state is Tinf + Q/(h As).")] = "",
    times: Annotated[str, Field(description="lumped table: list of times, e.g. '0 s; 60 s; 600 s' or a JSON array of such strings. Ascending, <= 2000 entries.")] = "",
    t_end: Annotated[str, Field(description="lumped table: end time, e.g. '3600 s'; rows are spaced evenly from 0 s to t_end ('points' of them)")] = "",
    points: Annotated[int, Field(description="lumped table with 't_end': number of rows including 0 s and t_end, 2..2000")] = DEFAULT_TABLE_POINTS,
    unit_system: UnitSystemIn = "metric",
) -> ToolResult:
    inputs = {
        "heat_input": heat_input, "times": times, "t_end": t_end, "points": points,
        "model": model, "initial_temperature": initial_temperature, "ambient_temperature": ambient_temperature,
        "conductivity": conductivity, "density": density, "specific_heat": specific_heat, "h": h, "time": time,
        "target_temperature": target_temperature, "volume": volume, "surface_area": surface_area, "size": size,
        "position": position, "depth": depth, "surface_condition": surface_condition, "other_temperature": other_temperature,
        "other_conductivity": other_conductivity, "other_density": other_density, "other_specific_heat": other_specific_heat,
        "body_thickness": body_thickness, "unit_system": unit_system,
    }

    def compute(handle: JobHandle) -> Computed:
        units = units_for(unit_system)
        kind = choice(model, name="model", allowed=MODELS)
        notes: list[str] = []
        warnings: list[str] = []
        t_i = temperature(initial_temperature, name="initial_temperature")
        t_inf = temperature(ambient_temperature, name="ambient_temperature")
        k = positive_quantity(conductivity, name="conductivity", dimension=DIM_K, unit="W/m/K", lo=1e-3, hi=1e4, notes=notes)
        rho = positive_quantity(density, name="density", dimension="density", unit="kg/m**3", lo=1e-3, hi=3e4)
        cp = positive_quantity(specific_heat, name="specific_heat", dimension=DIM_CP, unit="J/kg/K", lo=10.0, hi=1e5, notes=notes)
        alpha = k / (rho * cp)
        have_time, have_target = not is_blank(time), not is_blank(target_temperature)
        have_table = not is_blank(times) or not is_blank(t_end)
        if not is_blank(times) and not is_blank(t_end):
            raise UnitsError("input.invalid", "give 'times' or 't_end', not both", {"param": "times|t_end"})
        if have_table and kind != "lumped":
            raise UnitsError("input.invalid", "the T(t) table ('times' / 't_end') is available for model='lumped' only; give 'time' for this model", {"param": "times" if not is_blank(times) else "t_end"})
        if not is_blank(heat_input) and kind != "lumped":
            raise UnitsError("input.invalid", "'heat_input' is available for model='lumped' only", {"param": "heat_input"})
        if int(have_time) + int(have_target) + int(have_table) != 1:
            raise UnitsError(
                "input.invalid",
                "give exactly one of 'time', 'target_temperature' or the table inputs ('times' or 't_end')",
                {"param": "time|target_temperature"},
            )
        if have_target and kind == "semi_infinite":
            raise UnitsError("input.invalid", "'target_temperature' is not supported for semi_infinite; give 'time'", {"param": "target_temperature"})
        t_s = positive_quantity(time, name="time", dimension="time", unit="s", lo=0.0, hi=1e11, allow_zero=True) if have_time else None
        t_target = temperature(target_temperature, name="target_temperature") if have_target else None
        table_times = _table_times(times, t_end, points) if have_table else None
        q_w = 0.0
        if not is_blank(heat_input):
            q_w = positive_quantity(heat_input, name="heat_input", dimension=DIM_POWER, unit="W", lo=0.0, hi=1e9, allow_zero=True)
        h_ = vol = area = None
        t_eq = t_inf  # temperature the body approaches: the fluid temperature plus Q R when heat is put in
        if kind == "lumped":
            for name, raw in (("h", h), ("volume", volume), ("surface_area", surface_area)):
                if is_blank(raw):
                    raise UnitsError("input.missing", f"'{name}' is required for the lumped model", {"param": name})
            h_ = positive_quantity(h, name="h", dimension=DIM_H, unit="W/m**2/K", lo=1e-2, hi=1e7, notes=notes)
            vol = positive_quantity(volume, name="volume", dimension="volume", unit="m**3", lo=1e-12, hi=1e6)
            area = positive_quantity(surface_area, name="surface_area", dimension="area", unit="m**2", lo=1e-8, hi=1e6)
            t_eq = t_inf + q_w / (h_ * area)
        theta_i = t_i - t_eq
        ratio_target = None
        if t_target is not None:
            ratio_target = (t_target - t_eq) / theta_i if theta_i != 0.0 else None
            if ratio_target is None or not 0.0 < ratio_target < 1.0:
                between = "initial temperature and the steady-state temperature (ambient + heat_input/(h As))" if q_w > 0.0 else "ambient and the initial temperature"
                raise UnitsError(
                    "input.invalid",
                    f"'target_temperature' must lie strictly between the {between} (and those two must differ)",
                    {"param": "target_temperature"},
                )
        values: dict[str, QuantityOut] = {}
        data: dict[str, Any] = {"model": kind, "thermal_diffusivity_m2_per_s": alpha}

        if kind == "lumped":
            if area < (36.0 * math.pi) ** (1.0 / 3.0) * vol ** (2.0 / 3.0) * (1.0 - 1e-9):
                raise UnitsError(
                    "input.invalid",
                    f"surface_area {area:.4g} m^2 is smaller than the surface of a sphere of the same volume ({(36.0 * math.pi) ** (1.0 / 3.0) * vol ** (2.0 / 3.0):.4g} m^2): no body has that",
                    {"param": "surface_area|volume"},
                )
            lc = vol / area
            bi = h_ * lc / k
            tau = rho * vol * cp / (h_ * area)
            r_th = 1.0 / (h_ * area)
            heat_cap = rho * vol * cp
            if t_target is not None:
                t_s = -tau * math.log(ratio_target)
            biot_warning = (
                f"Biot number {bi:.3g} is not below 0.1: the lumped-capacitance model is NOT valid (internal temperature gradients matter); "
                "use plane_wall, cylinder or sphere with the one-term series"
            )
            if table_times is not None:
                t_end_s = table_times[-1]
                temps = [t_eq + theta_i * math.exp(-tt / tau) for tt in table_times]
                unit_label = "degF" if units["temperature"] == "degF" else "degC"
                csv_rows = [[tt, _in_system(temp_k, units)] for tt, temp_k in zip(table_times, temps)]
                artifact = handle.write_text("temperature_vs_time.csv", _csv_text(["time_s", f"temperature_{unit_label}"], csv_rows), fmt="csv")
                values.update(
                    {
                        "final_temperature": out(t_eq, "temperature", units, label="final_temperature"),
                        "temperature_at_t_end": out(temps[-1], "temperature", units, label="temperature_at_t_end"),
                        "time_constant": out(tau, "time", units, label="time_constant"),
                        "t_end": out(t_end_s, "time", units, label="t_end"),
                        "thermal_resistance": out(r_th, "resistance", units, label="thermal_resistance"),
                        "heat_capacity": QuantityOut(quantity=heat_cap, unit="joule / kelvin"),
                        "biot": plain(bi, label="biot"),
                    }
                )
                if q_w > 0.0:
                    values["heat_input"] = out(q_w, "power", units, label="heat_input")
                data.update(
                    formula="T(t) = Tinf + Q R + (Ti - Tinf - Q R) exp(-t/tau); R = 1/(h As), tau = rho V cp R; Q = heat_input (0 for a pure cool-down)",
                    validity="Biot number h (V/As)/k < 0.1; Q constant from t = 0",
                    assumptions=[
                        "uniform body temperature (no internal gradient); constant h, k, rho, cp",
                        "the heat input is switched on at t = 0 and stays constant; heat leaves only through h As (radiation only if included in h)",
                        "CSV columns: time_s (seconds) and the temperature in the unit of unit_system",
                    ],
                    table={"rows": len(csv_rows), "t_end_s": t_end_s, "t_end_over_tau": t_end_s / tau},
                )
                if bi >= 0.1:
                    warnings.append(biot_warning)
                if t_end_s < 3.0 * tau:
                    warnings.append(f"the table ends at {t_end_s / tau:.2g} time constants: the final temperature is not reached within it")
                return Computed(values=values, primary="final_temperature", warnings=warnings + notes, data=check_data(data), artifacts=[artifact])
            frac = math.exp(-t_s / tau)
            temp = t_eq + theta_i * frac
            values.update(
                {
                    "temperature": out(temp, "temperature", units, label="temperature"),
                    "time": out(t_s, "time", units, label="time"),
                    "time_constant": out(tau, "time", units, label="time_constant"),
                    "biot": plain(bi, label="biot"),
                    "characteristic_length": out(lc, "length", units, label="characteristic_length"),
                    "remaining_fraction": plain(frac, label="remaining_fraction"),
                }
            )
            if q_w > 0.0:
                values.update(
                    {
                        "final_temperature": out(t_eq, "temperature", units, label="final_temperature"),
                        "heat_input": out(q_w, "power", units, label="heat_input"),
                        "energy_stored": out(heat_cap * (temp - t_i), "energy", units, label="energy_stored"),
                        "heat_rate_to_ambient": out(h_ * area * (temp - t_inf), "power", units, label="heat_rate_to_ambient"),
                    }
                )
            else:
                values.update(
                    {
                        "energy_transferred": out(heat_cap * (t_i - temp), "energy", units, label="energy_transferred"),
                        "heat_rate_now": out(h_ * area * (t_inf - temp), "power", units, label="heat_rate_now"),
                    }
                )
            primary = "temperature" if have_time else "time"
            data.update(
                formula="T = Tinf + (Ti - Tinf) exp(-t/tau), tau = rho V cp/(h As)"
                if q_w == 0.0
                else "T = Tinf + Q R + (Ti - Tinf - Q R) exp(-t/tau), R = 1/(h As), tau = rho V cp R",
                validity="Biot number h (V/As)/k < 0.1",
                assumptions=["uniform body temperature (no internal gradient)", "constant h, k, rho, cp; no internal heat generation; radiation only if in h"],
            )
            if bi >= 0.1:
                warnings.append(biot_warning)
        elif kind in ("plane_wall", "cylinder", "sphere"):
            for name, raw in (("h", h), ("size", size)):
                if is_blank(raw):
                    raise UnitsError("input.missing", f"'{name}' is required for the {kind} model", {"param": name})
            h_ = positive_quantity(h, name="h", dimension=DIM_H, unit="W/m**2/K", lo=1e-2, hi=1e7, notes=notes)
            ls = positive_quantity(size, name="size", dimension="length", unit="m", lo=1e-6, hi=100.0)
            pos = plain_number(position, name="position", lo=0.0, hi=1.0, hint="fraction of the half thickness / radius: 0 centre, 1 surface")
            bi = h_ * ls / k
            z1, c1 = one_term(kind, bi)
            shape_v = shape_factor(kind, z1, pos)
            if t_target is not None:
                fo = math.log(c1 * shape_v / ratio_target) / (z1 * z1) if c1 * shape_v > ratio_target else -1.0
                if fo < 0.0:
                    raise UnitsError(
                        "input.invalid",
                        "the target temperature is reached before the one-term series is meaningful (it would need Fo < 0); "
                        "the body is still close to its initial temperature there",
                        {"param": "target_temperature"},
                    )
                t_s = fo * ls * ls / alpha
            fo = alpha * t_s / (ls * ls)
            theta0 = c1 * math.exp(-z1 * z1 * fo)
            ratio = theta0 * shape_v
            temp = t_inf + theta_i * ratio
            frac_q = energy_fraction(kind, z1, theta0)
            q0 = {
                "plane_wall": (2.0 * ls * rho * cp * theta_i, "energy_per_area", "J per m^2 of wall face, full thickness 2L"),
                "cylinder": (math.pi * ls * ls * rho * cp * theta_i, "energy_per_length", "J per m of cylinder length"),
                "sphere": (4.0 / 3.0 * math.pi * ls**3 * rho * cp * theta_i, "energy", "J for the whole sphere"),
            }[kind]
            values.update(
                {
                    "temperature": out(temp, "temperature", units, label="temperature"),
                    "centre_temperature": out(t_inf + theta_i * theta0, "temperature", units, label="centre_temperature"),
                    "time": out(t_s, "time", units, label="time"),
                    "biot": plain(bi, label="biot"),
                    "fourier": plain(fo, label="fourier"),
                    "first_eigenvalue": plain(z1, label="first_eigenvalue"),
                    "c1": plain(c1, label="c1"),
                    "theta_ratio": plain(ratio, label="theta_ratio"),
                    "energy_fraction": plain(frac_q, label="energy_fraction"),
                    "energy_transferred": out(q0[0] * frac_q, q0[1], units, label="energy_transferred"),
                }
            )
            primary = "temperature" if have_time else "time"
            data.update(
                formula="theta/theta_i = C1 exp(-z1^2 Fo) shape(z1 x*); Fo = alpha t/L^2; Bi = h L/k",
                energy_basis=q0[2],
                validity="Fo > 0.2 (one term); constant properties; Bi = h L/k with L = half thickness or radius",
                assumptions=["uniform initial temperature, sudden convective boundary, 1-D (plane wall: infinite slab; cylinder: infinitely long)", "constant properties, no heat generation"],
            )
            if fo < 0.2:
                warnings.append(f"Fourier number {fo:.3g} is below 0.2: the one-term series is not accurate (error can exceed a few percent); more terms are needed")
            if bi < 0.1:
                warnings.append(f"Biot number {bi:.3g} is below 0.1: the lumped model would also be valid here (agrees to O(Bi))")
        else:
            cond = choice(surface_condition, name="surface_condition", allowed=SURFACE_CONDITIONS)
            x = 0.0 if is_blank(depth) else positive_quantity(depth, name="depth", dimension="length", unit="m", lo=0.0, hi=1e3, allow_zero=True)
            sqrt_at = math.sqrt(alpha * t_s)
            eta = x / (2.0 * sqrt_at) if sqrt_at > 0.0 else math.inf
            surf_flux = None
            if cond == "convection":
                if is_blank(h):
                    raise UnitsError("input.missing", "surface_condition='convection' needs 'h'", {"param": "h"})
                h_ = positive_quantity(h, name="h", dimension=DIM_H, unit="W/m**2/K", lo=1e-2, hi=1e7, notes=notes)
                if t_s == 0.0:
                    ratio = 0.0  # no time has passed: the body is still at its initial temperature
                else:
                    beta = h_ * sqrt_at / k
                    ratio = semi_infinite_convection(eta, beta)
                    surf_t = t_i + (t_inf - t_i) * semi_infinite_convection(0.0, beta)
                    values["surface_temperature"] = out(surf_t, "temperature", units, label="surface_temperature")
                    surf_flux = h_ * (t_inf - surf_t)
                    data["biot_like_beta"] = beta
                temp = t_i + (t_inf - t_i) * ratio
                data["formula"] = "(T - Ti)/(Tinf - Ti) = erfc(eta) - exp(h x/k + h^2 alpha t/k^2) erfc(eta + h sqrt(alpha t)/k)"
            else:
                if cond == "contact":
                    names = (("other_temperature", other_temperature), ("other_conductivity", other_conductivity), ("other_density", other_density), ("other_specific_heat", other_specific_heat))
                    for name, raw in names:
                        if is_blank(raw):
                            raise UnitsError("input.missing", f"surface_condition='contact' needs '{name}'", {"param": name})
                    t_b = temperature(other_temperature, name="other_temperature")
                    k_b = positive_quantity(other_conductivity, name="other_conductivity", dimension=DIM_K, unit="W/m/K", lo=1e-3, hi=1e4, notes=notes)
                    rho_b = positive_quantity(other_density, name="other_density", dimension="density", unit="kg/m**3", lo=1e-3, hi=3e4)
                    cp_b = positive_quantity(other_specific_heat, name="other_specific_heat", dimension=DIM_CP, unit="J/kg/K", lo=10.0, hi=1e5, notes=notes)
                    e_a, e_b = math.sqrt(k * rho * cp), math.sqrt(k_b * rho_b * cp_b)
                    t_surface = (e_a * t_i + e_b * t_b) / (e_a + e_b)
                    data["effusivity_this_W_s05_per_m2_K"] = e_a
                    data["effusivity_other_W_s05_per_m2_K"] = e_b
                    data["formula"] = "Ts = (e_A T_A + e_B T_B)/(e_A + e_B), e = sqrt(k rho cp); then (T - Ts)/(Ti - Ts) = erf(x/(2 sqrt(alpha t)))"
                else:
                    t_surface = t_inf
                    data["formula"] = "(T - Ts)/(Ti - Ts) = erf(x/(2 sqrt(alpha t)))"
                values["surface_temperature"] = out(t_surface, "temperature", units, label="surface_temperature")
                ratio_s = math.erf(eta) if t_s > 0.0 or x > 0.0 else 0.0
                if t_s == 0.0:
                    ratio_s = 1.0 if x > 0.0 else 0.0
                temp = t_surface + (t_i - t_surface) * ratio_s
                if t_s > 0.0:
                    surf_flux = k * (t_surface - t_i) / math.sqrt(math.pi * alpha * t_s)
            values["temperature"] = out(temp, "temperature", units, label="temperature")
            values["thermal_penetration_depth"] = out(4.0 * sqrt_at, "length", units, label="thermal_penetration_depth")
            values["thermal_diffusivity"] = QuantityOut(quantity=alpha, unit="meter ** 2 / second")
            if surf_flux is not None:
                values["surface_heat_flux_into_body"] = out(surf_flux, "flux", units, label="surface_heat_flux_into_body")
            primary = "temperature"
            data.update(
                condition=cond,
                eta=eta if math.isfinite(eta) else None,
                validity="body thickness well above the penetration depth 4 sqrt(alpha t) (about 0.5 % of the step remains there)",
                assumptions=["semi-infinite solid, uniform initial temperature, 1-D, constant properties"],
            )
            if not is_blank(body_thickness):
                thick = positive_quantity(body_thickness, name="body_thickness", dimension="length", unit="m", lo=1e-6, hi=1e3)
                if 4.0 * sqrt_at > thick:
                    warnings.append(f"the penetration depth 4*sqrt(alpha*t) = {4.0 * sqrt_at:.4g} m exceeds the body thickness {thick:.4g} m: the semi-infinite model is not valid (use the wall model)")
            if cond != "convection" and not is_blank(h):
                warnings.append("'h' is not used with this surface_condition and was ignored")
        return Computed(values=values, primary=primary, warnings=warnings + notes, data=check_data(data))

    return run_tool(
        tool_name="thermal_transient",
        engine=ENGINE,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("pint",),
        standing_warnings=(
            "Transient conduction with constant properties and a sudden step in the surroundings; multi-dimensional bodies, "
            "temperature-dependent properties, internal heat generation and radiation (unless in h) are not modelled.",
        ),
    )


__all__ = ["bessel_j0", "bessel_j1", "energy_fraction", "erfcx", "first_eigenvalue", "one_term", "shape_factor", "transient"]
