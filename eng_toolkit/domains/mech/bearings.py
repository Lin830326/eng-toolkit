"""``mech_bearing_life``: basic rating life L10 of a rolling bearing.

Formulas (ISO 281 basic rating life, the textbook form)
* ``L10 = (C / P)^p`` in millions of revolutions, ``p = 3`` for ball bearings, ``p = 10/3`` for roller bearings
* ``L10h = 10^6 * L10 / (60 n)`` hours at ``n`` rev/min
* inverse: the dynamic rating needed for a required life, ``C = P * (L10h * 60 n / 10^6)^(1/p)``

Not done: the modified rating life ``L_nm = a1 * a_ISO * L10`` (reliability and lubrication/contamination factors) and the
equivalent load ``P = X Fr + Y Fa`` (bearing-specific factors); both need manufacturer data, so ``equivalent_load`` is an input.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.mech._common import (
    QuantityIn,
    choice,
    is_blank,
    positive_quantity,
    revolutions_per_second,
)
from eng_toolkit.domains.mech._server import ENGINE, server
from eng_toolkit.runner import Computed, JobHandle, run_tool

_EXPONENT = {"ball": 3.0, "roller": 10.0 / 3.0}


@server.tool(
    name="bearing_life",
    description=(
        "Basic rating life L10 (ISO 281 basic formula, 90 % reliability, ordinary lubrication) of a rolling bearing "
        "from its basic dynamic load rating C, the equivalent dynamic load P and the speed: L10 = (C/P)^p million "
        "revolutions, p = 3 for ball and 10/3 for roller bearings; also in hours. With 'required_life' it returns the "
        "dynamic rating C needed. The equivalent load P must already include radial and axial parts."
    ),
)
def bearing_life(
    dynamic_load_rating: Annotated[
        QuantityIn, Field(description="Basic dynamic load rating C from the bearing catalogue, e.g. '30.7 kN'")
    ],
    equivalent_load: Annotated[
        QuantityIn, Field(description="Equivalent dynamic bearing load P (radial + axial combined), e.g. '5 kN'")
    ],
    speed: Annotated[
        QuantityIn, Field(description="Shaft speed, e.g. '1500 rpm' (a bare '25 Hz' counts as 25 revolutions per second)")
    ],
    bearing_type: Annotated[
        str, Field(description="'ball' (exponent 3) or 'roller' (cylindrical, spherical, tapered; exponent 10/3)")
    ] = "ball",
    required_life: Annotated[
        str, Field(description="Optional required L10 life in time, e.g. '20000 h'. Given: the needed rating C is returned.")
    ] = "",
) -> ToolResult:
    inputs = {
        "dynamic_load_rating": dynamic_load_rating,
        "equivalent_load": equivalent_load,
        "speed": speed,
        "bearing_type": bearing_type,
        "required_life": required_life,
    }

    def compute(_: JobHandle) -> Computed:
        kind = choice(bearing_type, name="bearing_type", allowed=tuple(_EXPONENT))
        p_exp = _EXPONENT[kind]
        c = positive_quantity(dynamic_load_rating, name="dynamic_load_rating", dimension="force", unit="N", lo=1.0, hi=1e8)
        p = positive_quantity(equivalent_load, name="equivalent_load", dimension="force", unit="N", lo=1.0, hi=1e8)
        n_rps = revolutions_per_second(speed, name="speed", lo_rpm=0.1, hi_rpm=1.0e6)
        n_rpm = n_rps * 60.0
        c_n, p_n = float(c.to("N").magnitude), float(p.to("N").magnitude)

        l10 = (c_n / p_n) ** p_exp  # million revolutions
        l10h = l10 * 1.0e6 / (60.0 * n_rpm)
        load_ratio = p_n / c_n
        values = {
            "l10_hours": QuantityOut(quantity=l10h, unit="hour"),
            "l10_million_revolutions": QuantityOut(quantity=l10, unit="dimensionless"),
            "load_ratio_p_over_c": QuantityOut(quantity=load_ratio, unit="dimensionless"),
        }
        warnings: list[str] = []
        data = {
            "formula": "L10 = (C/P)^p [million rev] ; L10h = 1e6 L10 / (60 n)",
            "exponent": p_exp,
            "speed_rpm": n_rpm,
        }
        if not is_blank(required_life):
            life = positive_quantity(required_life, name="required_life", dimension="time", unit="hour", lo=1.0, hi=1.0e7)
            hours = float(life.to("hour").magnitude)
            c_req = p_n * (hours * 60.0 * n_rpm / 1.0e6) ** (1.0 / p_exp)
            values["required_dynamic_rating"] = QuantityOut(quantity=c_req / 1000.0, unit="kilonewton")
            values["rating_margin"] = QuantityOut(quantity=c_n / c_req, unit="dimensionless")
            if c_n < c_req:
                warnings.append(
                    f"the bearing's C = {c_n / 1000:g} kN is below the {c_req / 1000:.3g} kN needed for {hours:g} h: "
                    "it does not reach the required L10 life"
                )
        if load_ratio > 0.5:
            warnings.append(
                f"P/C = {load_ratio:.2f} is above 0.5: heavy load, where the C/P power law is extrapolated far from "
                "the usual range and plastic deformation matters; also check the static rating C0"
            )
        elif load_ratio < 0.05:
            warnings.append(
                f"P/C = {load_ratio:.3f} is below 0.05: a very light load, where the basic life is very long and "
                "other failure modes (lubricant life, skidding, contamination) govern"
            )
        if n_rpm < 10.0:
            warnings.append("speed below 10 rpm: the rating-life method assumes continuous rotation; check the static load rating C0")
        if l10h > 1.0e6:
            warnings.append(f"L10h = {l10h:.3g} h is beyond any realistic service life; the basic life is not the limit here")
        return Computed(values=values, primary="l10_hours", warnings=warnings, data=data)

    return run_tool(
        tool_name="mech_bearing_life",
        engine=ENGINE,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("pint",),
        standing_warnings=(
            "Basic rating life only (90 % survival, normal lubrication, no misalignment or contamination). "
            "L10 is not a guaranteed life: 10 % of identical bearings fail earlier.",
        ),
    )


__all__ = ["bearing_life"]
