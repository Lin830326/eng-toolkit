"""``thermal_exchanger``: heat exchanger rating and sizing by effectiveness-NTU, cross-checked with the LMTD method.

Formulas (Kays & London; Incropera, Cengel; effectiveness e, NTU = UA/Cmin, Cr = Cmin/Cmax)
* parallel flow:   e = (1 - exp(-NTU (1+Cr))) / (1+Cr)
* counterflow:     e = (1 - exp(-NTU (1-Cr))) / (1 - Cr exp(-NTU (1-Cr)));  Cr = 1: e = NTU/(1+NTU)
* crossflow, both fluids unmixed: the exact series  e = 1/(Cr NTU) sum_{n>=1} P(n, NTU) P(n, Cr NTU),  P(n,x) = 1 - exp(-x) sum_{k<n} x^k/k!
* crossflow, Cmax mixed / Cmin unmixed:  e = (1 - exp(-Cr (1 - exp(-NTU)))) / Cr
* crossflow, Cmin mixed / Cmax unmixed:  e = 1 - exp(-(1 - exp(-Cr NTU)) / Cr)
* one shell pass, 2, 4, ... tube passes:  e = 2 / (1 + Cr + sqrt(1+Cr^2) coth(NTU sqrt(1+Cr^2) / 2))
* Cr = 0 (all arrangements): e = 1 - exp(-NTU)
* LMTD = (dT1 - dT2) / ln(dT1/dT2);  Q = U A F LMTD, F = 1 for parallel flow and counterflow, F from Bowman's closed form for the
  one-shell-pass exchanger (computed from the outlet temperatures, so it is an independent route), and F = Q / (UA LMTD_counterflow)
  for crossflow (no closed form of F exists for the series; reported, not a check).
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
    DIM_POWER,
    DIM_UA,
    QuantityIn,
    TemperatureIn,
    UnitSystemIn,
    check_data,
    choice,
    is_blank,
    out,
    plain,
    positive_quantity,
    temperature,
)
from eng_toolkit.domains.thermal._common import unit_system as units_for
from eng_toolkit.domains.thermal._server import ENGINE, server
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import UnitsError

ARRANGEMENTS = ("parallel", "counterflow", "crossflow_unmixed", "crossflow_hot_mixed", "crossflow_cold_mixed", "shell_tube")
NTU_MAX = 100.0


class Infeasible(Exception):
    """The requested effectiveness cannot be reached by the arrangement at any NTU."""

    def __init__(self, message: str, eps_max: float):
        super().__init__(message)
        self.eps_max = eps_max


# --------------------------------------------------------------------------------------------- effectiveness relations


def _poisson_tail(n: int, x: float, pmf_n: float) -> float:
    """P(X >= n) for X ~ Poisson(x), given pmf_n = P(X = n), by direct summation of the (fast) decaying tail (n > x)."""
    term, total, k = pmf_n, pmf_n, n
    while term > 1e-19 * total:
        k += 1
        term *= x / k
        total += term
    return total


def _eps_cross_unmixed(ntu: float, cr: float) -> float:
    """Exact effectiveness of single-pass crossflow with both fluids unmixed (Mason / Kays & London series)."""
    if ntu <= 0.0:
        return 0.0
    if cr <= 0.0:
        return -math.expm1(-ntu)
    if ntu > NTU_MAX:
        raise UnitsError(
            "input.out_of_range",
            f"NTU = {ntu:.4g} is above {NTU_MAX:g}, the largest value of the crossflow (both unmixed) series implemented",
            {"param": "ntu", "max": NTU_MAX},
        )
    a, b = ntu, cr * ntu
    pmf_a, pmf_b = math.exp(-a), math.exp(-b)  # P(X = k) for k = 0
    cdf_a, cdf_b = pmf_a, pmf_b  # P(X <= k)
    total, n = 0.0, 1
    while n < 3000:
        # P(n, x) = P(X >= n): 1 - cdf(n-1) while the cdf is far from 1, the direct tail beyond the mean (no cancellation)
        tail_a = _poisson_tail(n, a, pmf_a * a / n) if n > a else 1.0 - cdf_a
        tail_b = _poisson_tail(n, b, pmf_b * b / n) if n > b else 1.0 - cdf_b
        pmf_a *= a / n
        pmf_b *= b / n
        cdf_a += pmf_a
        cdf_b += pmf_b
        term = tail_a * tail_b
        total += term
        if n > max(a, b) + 2 and term < 1e-18 * total:
            break
        n += 1
    return total / (cr * ntu)


def effectiveness(kind: str, ntu: float, cr: float) -> float:
    """Effectiveness for ``kind`` in parallel, counter, cross_unmixed, cross_cmax_mixed, cross_cmin_mixed, shell_tube."""
    if ntu <= 0.0:
        return 0.0
    if cr == 0.0:
        return -math.expm1(-ntu)
    if kind == "parallel":
        return -math.expm1(-ntu * (1.0 + cr)) / (1.0 + cr)
    if kind == "counter":
        x = ntu * (1.0 - cr)
        if x == 0.0:
            return ntu / (1.0 + ntu)
        return -math.expm1(-x) / ((1.0 - cr) - cr * math.expm1(-x))
    if kind == "cross_unmixed":
        return _eps_cross_unmixed(ntu, cr)
    if kind == "cross_cmax_mixed":
        return -math.expm1(-cr * -math.expm1(-ntu)) / cr
    if kind == "cross_cmin_mixed":
        return -math.expm1(-(-math.expm1(-cr * ntu)) / cr)
    if kind == "shell_tube":
        w = math.sqrt(1.0 + cr * cr)
        z = ntu * w / 2.0
        coth = 1.0 / math.tanh(z) if z < 350.0 else 1.0
        return 2.0 / (1.0 + cr + w * coth)
    raise ValueError(kind)


def eps_limit(kind: str, cr: float) -> float:
    """The effectiveness approached as NTU -> infinity."""
    if cr == 0.0:
        return 1.0
    if kind == "parallel":
        return 1.0 / (1.0 + cr)
    if kind in ("counter", "cross_unmixed"):
        return 1.0
    if kind == "cross_cmax_mixed":
        return -math.expm1(-cr) / cr
    if kind == "cross_cmin_mixed":
        return -math.expm1(-1.0 / cr)
    return 2.0 / (1.0 + cr + math.sqrt(1.0 + cr * cr))  # shell_tube


def ntu_for(kind: str, eps: float, cr: float) -> float:
    """The NTU that gives effectiveness ``eps``; raises :class:`Infeasible` when ``eps`` is not below the arrangement's limit."""
    limit = eps_limit(kind, cr)
    if eps <= 0.0:
        return 0.0
    if eps >= limit:
        raise Infeasible(f"effectiveness {eps:.6g} is not below the maximum {limit:.6g} this arrangement can reach at Cr = {cr:.4g}", limit)
    if cr == 0.0:
        return -math.log1p(-eps)
    if kind == "parallel":
        return -math.log1p(-eps * (1.0 + cr)) / (1.0 + cr)
    if kind == "counter":
        if cr == 1.0:
            return eps / (1.0 - eps)
        return math.log1p(eps * (1.0 - cr) / (1.0 - eps)) / (1.0 - cr)
    if kind == "cross_cmax_mixed":
        return -math.log1p(math.log1p(-eps * cr) / cr)
    if kind == "cross_cmin_mixed":
        return -math.log1p(cr * math.log1p(-eps)) / cr
    if kind == "shell_tube":
        w = math.sqrt(1.0 + cr * cr)
        big_e = (2.0 / eps - (1.0 + cr)) / w
        return -math.log((big_e - 1.0) / (big_e + 1.0)) / w
    # cross_unmixed: bisection on the monotonic series
    if effectiveness(kind, NTU_MAX, cr) <= eps:
        raise UnitsError(
            "input.out_of_range",
            f"effectiveness {eps:.6g} needs NTU above {NTU_MAX:g} for crossflow with both fluids unmixed",
            {"param": "duty", "max_ntu": NTU_MAX},
        )
    lo, hi = 0.0, NTU_MAX
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if effectiveness(kind, mid, cr) < eps:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-15 * hi:
            break
    return 0.5 * (lo + hi)


# --------------------------------------------------------------------------------------------- LMTD route


def lmtd(d1: float, d2: float) -> float:
    """Log-mean of two positive temperature differences (their mean when they are equal)."""
    if d1 <= 0.0 or d2 <= 0.0:
        raise ValueError("temperature differences must be positive")
    x = (d1 - d2) / d2
    if abs(x) < 1e-7:
        return d2 * (1.0 + x / 2.0 - x * x / 12.0)
    return d2 * x / math.log1p(x)


def bowman_f(p: float, r: float) -> float:
    """Correction factor F of a one-shell-pass exchanger with 2, 4, ... tube passes (Bowman, Mueller, Nagle 1940)."""
    if p <= 0.0:
        return 1.0
    w = math.sqrt(r * r + 1.0)
    denominator = math.log((2.0 - p * (r + 1.0 - w)) / (2.0 - p * (r + 1.0 + w)))
    if abs(r - 1.0) < 1e-9:
        return (math.sqrt(2.0) * p / (1.0 - p)) / denominator
    return (w / (r - 1.0)) * math.log1p(p * (r - 1.0) / (1.0 - r * p)) / denominator


def _kind(arrangement: str, c_hot: float, c_cold: float) -> str:
    """The effectiveness-relation key; the crossflow 'mixed' arrangements depend on whether the mixed stream is Cmax or Cmin."""
    if arrangement == "parallel":
        return "parallel"
    if arrangement == "counterflow":
        return "counter"
    if arrangement == "crossflow_unmixed":
        return "cross_unmixed"
    if arrangement == "shell_tube":
        return "shell_tube"
    mixed_is_hot = arrangement == "crossflow_hot_mixed"
    mixed_c, other_c = (c_hot, c_cold) if mixed_is_hot else (c_cold, c_hot)
    return "cross_cmax_mixed" if mixed_c >= other_c else "cross_cmin_mixed"


@server.tool(
    name="exchanger",
    description=(
        "Heat exchanger by effectiveness-NTU, cross-checked with the LMTD method. Arrangements: parallel, counterflow, "
        "crossflow_unmixed (both fluids unmixed), crossflow_hot_mixed / crossflow_cold_mixed (that fluid mixed, the other "
        "unmixed), shell_tube (one shell pass, 2+ tube passes). RATING: give 'ua' (or 'overall_u' and 'area') and get duty and "
        "both outlet temperatures. SIZING: give 'duty' (and optionally 'overall_u') and get UA (and area). Capacity rates "
        "are mass_flow x cp for each fluid. Returns effectiveness, NTU, Cr, LMTD, correction factor F, and refuses an "
        "impossible duty (outlet beyond the other fluid's inlet, or beyond what the arrangement can reach); warns at a temperature cross."
    ),
)
def exchanger(
    arrangement: Annotated[str, Field(description="'parallel', 'counterflow', 'crossflow_unmixed', 'crossflow_hot_mixed', 'crossflow_cold_mixed' or 'shell_tube'")],
    hot_inlet_temperature: Annotated[TemperatureIn, Field(description="Hot fluid inlet temperature, e.g. '150 degC'")],
    cold_inlet_temperature: Annotated[TemperatureIn, Field(description="Cold fluid inlet temperature, e.g. '20 degC' (must be below the hot inlet)")],
    hot_mass_flow: Annotated[QuantityIn, Field(description="Hot fluid mass flow, e.g. '2 kg/s'")],
    hot_cp: Annotated[QuantityIn, Field(description="Hot fluid specific heat, e.g. '2100 J/(kg*K)'")],
    cold_mass_flow: Annotated[QuantityIn, Field(description="Cold fluid mass flow, e.g. '1.5 kg/s'")],
    cold_cp: Annotated[QuantityIn, Field(description="Cold fluid specific heat, e.g. '4180 J/(kg*K)'")],
    ua: Annotated[str, Field(description="RATING: overall conductance U*A, e.g. '5000 W/K'")] = "",
    overall_u: Annotated[str, Field(description="Overall coefficient U, e.g. '500 W/(m^2*K)'. With 'area' it gives UA (rating); with 'duty' it gives the area (sizing).")] = "",
    area: Annotated[str, Field(description="RATING with overall_u: heat transfer area, e.g. '10 m^2'")] = "",
    duty: Annotated[str, Field(description="SIZING: required heat duty, e.g. '200 kW'")] = "",
    unit_system: UnitSystemIn = "metric",
) -> ToolResult:
    inputs = {
        "arrangement": arrangement,
        "hot_inlet_temperature": hot_inlet_temperature,
        "cold_inlet_temperature": cold_inlet_temperature,
        "hot_mass_flow": hot_mass_flow,
        "hot_cp": hot_cp,
        "cold_mass_flow": cold_mass_flow,
        "cold_cp": cold_cp,
        "ua": ua,
        "overall_u": overall_u,
        "area": area,
        "duty": duty,
        "unit_system": unit_system,
    }

    def compute(_: JobHandle) -> Computed:
        units = units_for(unit_system)
        arr = choice(arrangement, name="arrangement", allowed=ARRANGEMENTS)
        notes: list[str] = []
        warnings: list[str] = []
        t_hi = temperature(hot_inlet_temperature, name="hot_inlet_temperature")
        t_ci = temperature(cold_inlet_temperature, name="cold_inlet_temperature")
        if not t_hi > t_ci:
            raise UnitsError(
                "input.invalid",
                f"the hot inlet ({t_hi - 273.15:.6g} degC) must be above the cold inlet ({t_ci - 273.15:.6g} degC); swap the fluids or check the temperatures",
                {"param": "hot_inlet_temperature|cold_inlet_temperature"},
            )
        m_h = positive_quantity(hot_mass_flow, name="hot_mass_flow", dimension="mass_flow", unit="kg/s", lo=1e-6, hi=1e6)
        m_c = positive_quantity(cold_mass_flow, name="cold_mass_flow", dimension="mass_flow", unit="kg/s", lo=1e-6, hi=1e6)
        cp_h = positive_quantity(hot_cp, name="hot_cp", dimension=DIM_CP, unit="J/kg/K", lo=10.0, hi=1e5, notes=notes)
        cp_c = positive_quantity(cold_cp, name="cold_cp", dimension=DIM_CP, unit="J/kg/K", lo=10.0, hi=1e5, notes=notes)
        c_hot, c_cold = m_h * cp_h, m_c * cp_c
        c_min, c_max = min(c_hot, c_cold), max(c_hot, c_cold)
        cr = c_min / c_max
        kind = _kind(arr, c_hot, c_cold)
        dt_max = t_hi - t_ci

        given = {name: not is_blank(raw) for name, raw in (("ua", ua), ("overall_u", overall_u), ("area", area), ("duty", duty))}
        u_val = positive_quantity(overall_u, name="overall_u", dimension=DIM_H, unit="W/m**2/K", lo=1e-2, hi=1e6, notes=notes) if given["overall_u"] else None
        a_val = positive_quantity(area, name="area", dimension="area", unit="m**2", lo=1e-8, hi=1e9) if given["area"] else None
        rating = given["ua"] or (given["overall_u"] and given["area"])
        sizing = given["duty"]
        if rating == sizing or (given["ua"] and (given["overall_u"] or given["area"])) or (sizing and (given["ua"] or given["area"])):
            raise UnitsError(
                "input.invalid",
                "give either RATING data ('ua', or 'overall_u' with 'area') or SIZING data ('duty', optionally with 'overall_u'), not both and not neither",
                {"param": "ua|overall_u|area|duty", "given": [k for k, v in given.items() if v]},
            )

        if rating:
            ua_w = (
                positive_quantity(ua, name="ua", dimension=DIM_UA, unit="W/K", lo=1e-6, hi=1e12, notes=notes)
                if given["ua"]
                else u_val * a_val
            )
            ntu = ua_w / c_min
            eps = effectiveness(kind, ntu, cr)
            q = eps * c_min * dt_max
            mode = "rating"
        else:
            q = positive_quantity(duty, name="duty", dimension=DIM_POWER, unit="W", lo=1e-3, hi=1e13)
            q_max = c_min * dt_max
            if q >= q_max:
                raise UnitsError(
                    "input.invalid",
                    f"the duty {q:.6g} W is not below the thermodynamic maximum Cmin*(Th,in - Tc,in) = {q_max:.6g} W: an outlet would "
                    "have to pass the other fluid's inlet temperature",
                    {"param": "duty", "max_duty_W": q_max},
                )
            eps = q / q_max
            try:
                ntu = ntu_for(kind, eps, cr)
            except Infeasible as exc:
                raise UnitsError(
                    "input.invalid",
                    f"duty {q:.6g} W is infeasible for '{arr}': {exc}. The most this arrangement can transfer is "
                    f"{exc.eps_max * q_max:.6g} W for these flows; use counterflow, more flow or a higher temperature difference",
                    {"param": "duty", "max_duty_W": exc.eps_max * q_max, "max_effectiveness": exc.eps_max},
                ) from exc
            ua_w = ntu * c_min
            mode = "sizing"
        t_ho = t_hi - q / c_hot
        t_co = t_ci + q / c_cold

        # LMTD route -------------------------------------------------------------------------------------------------
        cross = t_co > t_ho
        lmtd_used: float | None = None
        f_val: float | None = None
        f_source = ""
        lmtd_cf = lmtd_par = None
        route_difference: float | None = None
        ua_lmtd: float | None = None
        d_cf = (t_hi - t_co, t_ho - t_ci)  # end differences of the counterflow arrangement
        d_par = (t_hi - t_ci, t_ho - t_co)  # and of the parallel arrangement
        floor = 1e-6 * dt_max
        try:
            if min(d_cf) > floor:
                lmtd_cf = lmtd(*d_cf)
            if min(d_par) > floor:
                lmtd_par = lmtd(*d_par)
            if kind == "parallel":
                lmtd_used, f_val, f_source = lmtd_par, 1.0, "exact (1)"
            elif kind == "counter":
                lmtd_used, f_val, f_source = lmtd_cf, 1.0, "exact (1)"
            elif kind == "shell_tube" and lmtd_cf is not None:
                # cold fluid on the tube side: P = (Tc,out - Tc,in)/(Th,in - Tc,in), R = (Th,in - Th,out)/(Tc,out - Tc,in); F is symmetric
                lmtd_used = lmtd_cf
                f_val = bowman_f((t_co - t_ci) / dt_max, (t_hi - t_ho) / (t_co - t_ci))
                f_source = "Bowman closed form from the outlet temperatures (independent of the NTU relation)"
            elif lmtd_cf is not None:
                lmtd_used = lmtd_cf
                f_val = q / (ua_w * lmtd_cf)
                f_source = "derived from the NTU result (no closed form of F exists for the crossflow series); not an independent check"
            if lmtd_used is not None and f_val:
                ua_lmtd = q / (f_val * lmtd_used)
                route_difference = abs(ua_lmtd - ua_w) / ua_w
        except (ValueError, ZeroDivisionError, OverflowError):
            lmtd_used = f_val = ua_lmtd = route_difference = None
        if lmtd_used is None or f_val is None:
            lmtd_used = f_val = ua_lmtd = route_difference = None
            f_source = "not evaluated"
            warnings.append(
                "the LMTD cross-check was skipped: an end temperature difference is below 1e-6 of the inlet span (NTU very large or "
                "the duty at the arrangement's limit), where the log-mean is ill-conditioned"
            )

        # Warnings ---------------------------------------------------------------------------------------------------
        if cross:
            if kind == "counter":
                warnings.append("temperature cross: the cold outlet is hotter than the hot outlet; this is possible only in true counterflow (it is, here)")
            else:
                warnings.append(
                    f"temperature cross: the cold outlet ({t_co - 273.15:.5g} degC) is hotter than the hot outlet ({t_ho - 273.15:.5g} degC); "
                    f"in a {arr} exchanger this means a low correction factor (F = {'n/a' if f_val is None else format(f_val, '.3g')}); consider counterflow or shells in series"
                )
        if f_val is not None and kind == "shell_tube" and f_val < 0.75:
            warnings.append(f"correction factor F = {f_val:.3f} is below 0.75: the LMTD method is unreliable there and the exchanger is a poor use of area; add shells in series")
        if ntu > 20.0:
            warnings.append(f"NTU = {ntu:.4g} is very large: the effectiveness is nearly flat and the result is extremely sensitive to the inputs")
        if eps > 0.95 and kind != "counter":
            warnings.append(f"effectiveness {eps:.4f} is close to the arrangement's limit {eps_limit(kind, cr):.4f}")

        values: dict[str, QuantityOut] = {
            "duty": out(q, "power", units, label="duty"),
            "ua": out(ua_w, "ua", units, label="ua"),
            "hot_outlet_temperature": out(t_ho, "temperature", units, label="hot_outlet_temperature"),
            "cold_outlet_temperature": out(t_co, "temperature", units, label="cold_outlet_temperature"),
            "effectiveness": plain(eps, label="effectiveness"),
            "ntu": plain(ntu, label="ntu"),
            "capacity_rate_ratio": plain(cr, label="capacity_rate_ratio"),
            "capacity_rate_hot": out(c_hot, "ua", units, label="capacity_rate_hot"),
            "capacity_rate_cold": out(c_cold, "ua", units, label="capacity_rate_cold"),
        }
        if lmtd_used is not None:
            values["lmtd"] = out(lmtd_used, "dtemp", units, label="lmtd")
        if f_val is not None:
            values["lmtd_correction_factor"] = plain(f_val, label="lmtd_correction_factor")
        if u_val is not None and mode == "sizing":
            values["area"] = out(ua_w / u_val, "area", units, label="area")
        data: dict[str, Any] = {
            "mode": mode,
            "arrangement": arr,
            "effectiveness_relation": kind,
            "effectiveness_limit_at_infinite_ntu": eps_limit(kind, cr),
            "cmin_stream": "hot" if c_hot <= c_cold else "cold",
            "energy_balance": {
                "hot_side_duty_W": c_hot * (t_hi - t_ho),
                "cold_side_duty_W": c_cold * (t_co - t_ci),
                "relative_difference": abs(c_hot * (t_hi - t_ho) - c_cold * (t_co - t_ci)) / max(q, 1e-300),
            },
            "lmtd_route": {
                "correction_factor_source": f_source,
                "ua_from_lmtd_W_per_K": ua_lmtd,
                "relative_difference_to_ntu_route": route_difference,
                "lmtd_counterflow_K": lmtd_cf,
                "lmtd_parallel_K": lmtd_par,
            },
            "temperature_cross": cross,
            "assumptions": [
                "steady state, constant specific heats and overall coefficient, no heat loss to the surroundings",
                "no phase change (capacity rates are finite); no axial conduction; uniform flow distribution",
                "shell_tube means one shell pass with any even number of tube passes",
            ],
        }
        return Computed(values=values, primary="duty", warnings=warnings + notes, data=check_data(data))

    return run_tool(
        tool_name="thermal_exchanger",
        engine=ENGINE,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("pint",),
        standing_warnings=(
            "Heat exchanger effectiveness-NTU / LMTD with constant properties and a constant overall coefficient; the "
            "overall coefficient itself, pressure drop, fouling, phase change and flow maldistribution are not calculated.",
        ),
    )


__all__ = ["bowman_f", "effectiveness", "eps_limit", "exchanger", "lmtd", "ntu_for"]
