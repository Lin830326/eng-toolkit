"""``mech_beam_bending``: closed-form single-span beam cases (Euler-Bernoulli, linear elastic).

Cases and formulas (every strength-of-materials textbook; ``a`` = load position from the left support / fixed end, ``b = L - a``)
* simply supported, point load: ``R1 = P b / L``, ``R2 = P a / L``, ``Mmax = P a b / L`` (under the load),
  ``ymax = P s (L^2 - s^2)^(3/2) / (9 sqrt(3) E I L)`` with ``s = min(a, b)`` (midspan: ``P L^3 / 48 E I``)
* simply supported, uniform load ``w`` over the span: ``R = w L / 2``, ``Mmax = w L^2 / 8``, ``ymax = 5 w L^4 / (384 E I)``
* cantilever, point load at ``a`` from the wall: ``R = P``, ``M = P a``, tip ``y = P a^2 (3 L - a) / (6 E I)``
* cantilever, uniform load: ``R = w L``, ``M = w L^2 / 2``, tip ``y = w L^4 / (8 E I)``
* bending stress ``sigma = M c / I``
Section: ``I`` and ``c`` for a rectangle (``b h^3 / 12``, ``h/2``), solid circle (``pi d^4 / 64``, ``d/2``) and tube
(``pi (D^4 - d^4) / 64``, ``D/2``), or given directly.
"""

from __future__ import annotations

import math
from typing import Annotated

from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.mech._common import (
    QuantityIn,
    UnitSystemIn,
    check_range,
    choice,
    is_blank,
    optional_quantity,
    out,
    positive_quantity,
)
from eng_toolkit.domains.mech._common import unit_system as units_for
from eng_toolkit.domains.mech._server import ENGINE, server
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import UnitsError, parse_quantity, ureg

_SUPPORTS = ("simply_supported", "cantilever")
_LOADS = ("point", "udl")
_SECTIONS = ("rectangle", "circle", "tube", "custom")


def _section(kind: str, width: str, height: str, diameter: str, inner_diameter: str, second_moment: str, extreme_fiber: str):
    """(I in mm^4, c in mm, depth in mm) of the cross-section."""

    def need(raw: str, name: str, dimension: str = "length", unit: str = "mm", lo: float = 0.1, hi: float = 1e4):
        if is_blank(raw):
            raise UnitsError("input.missing", f"'{name}' is required for section '{kind}'", {"param": name, "section": kind})
        return float(positive_quantity(raw, name=name, dimension=dimension, unit=unit, lo=lo, hi=hi).to(unit).magnitude)

    if kind == "rectangle":
        b, h = need(width, "width"), need(height, "height")
        return b * h**3 / 12.0, h / 2.0, h
    if kind == "circle":
        d = need(diameter, "diameter")
        return math.pi * d**4 / 64.0, d / 2.0, d
    if kind == "tube":
        d_out, d_in = need(diameter, "diameter"), need(inner_diameter, "inner_diameter")
        if d_in >= d_out:
            raise UnitsError(
                "input.invalid",
                f"'inner_diameter' ({d_in:g} mm) must be smaller than 'diameter' ({d_out:g} mm)",
                {"param": "inner_diameter"},
            )
        return math.pi * (d_out**4 - d_in**4) / 64.0, d_out / 2.0, d_out
    i = need(second_moment, "second_moment", "[length] ** 4", "mm**4", 1e-3, 1e18)
    c = need(extreme_fiber, "extreme_fiber")
    return i, c, 2.0 * c


@server.tool(
    name="beam_bending",
    description=(
        "Closed-form single-span beam: simply supported or cantilever, with one point load or a uniform load over the "
        "whole span. Returns support reactions, maximum shear, maximum bending moment, maximum deflection and maximum "
        "bending stress (Euler-Bernoulli, linear elastic, self-weight ignored). Section: rectangle, circle, tube, or "
        "custom second moment of area and extreme-fibre distance."
    ),
)
def beam_bending(
    length: Annotated[QuantityIn, Field(description="Span (simply supported) or length from wall to free end (cantilever), e.g. '2 m'")],
    load: Annotated[
        QuantityIn,
        Field(description="Point load as a force, e.g. '5 kN', or for load_type 'udl' a force per length, e.g. '10 kN/m'"),
    ],
    support: Annotated[str, Field(description="'simply_supported' or 'cantilever'")] = "simply_supported",
    load_type: Annotated[str, Field(description="'point' (one concentrated load) or 'udl' (uniform over the whole length)")] = "point",
    position: Annotated[
        str,
        Field(
            description="Point load only: distance from the left support (or from the wall for a cantilever), e.g. '0.5 m'. "
            "Empty = midspan (simply supported) or the free end (cantilever)."
        ),
    ] = "",
    elastic_modulus: Annotated[QuantityIn, Field(description="Young's modulus E, e.g. '210 GPa' (steel), '70 GPa' (aluminium)")] = "210 GPa",
    section: Annotated[
        str,
        Field(
            description="'rectangle' (width, height), 'circle' (diameter), 'tube' (diameter, inner_diameter) or 'custom' "
            "(second_moment, extreme_fiber). Bending is about the axis parallel to the width; the load acts along the height."
        ),
    ] = "rectangle",
    width: Annotated[str, Field(description="Rectangle width b (parallel to the neutral axis), e.g. '50 mm'")] = "",
    height: Annotated[str, Field(description="Rectangle height h (in the load direction), e.g. '100 mm'")] = "",
    diameter: Annotated[str, Field(description="Circle or tube OUTER diameter, e.g. '40 mm'")] = "",
    inner_diameter: Annotated[str, Field(description="Tube inner diameter, e.g. '32 mm'")] = "",
    second_moment: Annotated[str, Field(description="Custom section: second moment of area I, e.g. '4.17e6 mm^4'")] = "",
    extreme_fiber: Annotated[
        str, Field(description="Custom section: distance c from the neutral axis to the extreme fibre, e.g. '50 mm'")
    ] = "",
    allowable_stress: Annotated[
        str, Field(description="Optional allowable bending stress, e.g. '160 MPa'. Gives the utilization.")
    ] = "",
    unit_system: UnitSystemIn = "metric",
) -> ToolResult:
    inputs = {
        "length": length,
        "load": load,
        "support": support,
        "load_type": load_type,
        "position": position,
        "elastic_modulus": elastic_modulus,
        "section": section,
        "width": width,
        "height": height,
        "diameter": diameter,
        "inner_diameter": inner_diameter,
        "second_moment": second_moment,
        "extreme_fiber": extreme_fiber,
        "allowable_stress": allowable_stress,
        "unit_system": unit_system,
    }

    def compute(_: JobHandle) -> Computed:
        units = units_for(unit_system)
        sup = choice(support, name="support", allowed=_SUPPORTS)
        kind_load = choice(load_type, name="load_type", allowed=_LOADS)
        sec = choice(section, name="section", allowed=_SECTIONS)
        ell = positive_quantity(length, name="length", dimension="length", unit="m", lo=1e-3, hi=1e3)
        # A force per length has the dimension of mass / time^2.
        load_dim, load_unit, load_lo, load_hi = (
            ("force", "N", 1e-3, 1e9) if kind_load == "point" else ("[mass] / [time] ** 2", "N/m", 1e-3, 1e9)
        )
        w = positive_quantity(load, name="load", dimension=load_dim, unit=load_unit, lo=load_lo, hi=load_hi)
        e = positive_quantity(elastic_modulus, name="elastic_modulus", dimension="pressure", unit="GPa", lo=0.001, hi=1000.0)
        i_mm4, c_mm, depth_mm = _section(sec, width, height, diameter, inner_diameter, second_moment, extreme_fiber)

        l_m = float(ell.to("m").magnitude)
        e_pa = float(e.to("Pa").magnitude)
        i_m4 = i_mm4 * 1e-12
        ei = e_pa * i_m4
        load_si = float(w.to(load_unit).magnitude)

        if kind_load == "point":
            if is_blank(position):
                a = l_m / 2.0 if sup == "simply_supported" else l_m
            else:
                pos = parse_quantity(position, name="position", dimension="length")
                if float(pos.magnitude) < 0:
                    raise UnitsError("input.invalid", f"'position' must be >= 0, got {pos:~P}", {"param": "position"})
                check_range(float(pos.to("m").magnitude), name="position", unit="m", lo=0.0, hi=l_m, received=f"{pos:~P}")
                a = float(pos.to("m").magnitude)
            b = l_m - a
            p = load_si
            if sup == "simply_supported":
                r1, r2 = p * b / l_m, p * a / l_m
                m_max = p * a * b / l_m
                s = min(a, b)
                y_max = p * s * (l_m**2 - s**2) ** 1.5 / (9.0 * math.sqrt(3.0) * ei * l_m)
                v_max = max(r1, r2)
                m_at = a
                reactions = {"reaction_left": r1, "reaction_right": r2}
            else:
                m_max, v_max = p * a, p
                y_max = p * a**2 * (3.0 * l_m - a) / (6.0 * ei)
                m_at = 0.0
                reactions = {"wall_reaction_force": p}
            total = p
        else:
            wl = load_si
            total = wl * l_m
            if sup == "simply_supported":
                r1 = r2 = total / 2.0
                m_max, v_max = wl * l_m**2 / 8.0, total / 2.0
                y_max = 5.0 * wl * l_m**4 / (384.0 * ei)
                m_at = l_m / 2.0
                reactions = {"reaction_left": r1, "reaction_right": r2}
            else:
                m_max, v_max = wl * l_m**2 / 2.0, total
                y_max = wl * l_m**4 / (8.0 * ei)
                m_at = 0.0
                reactions = {"wall_reaction_force": total}
        sigma_mpa = m_max * 1000.0 * c_mm / i_mm4  # N*mm * mm / mm^4 = MPa

        values: dict[str, QuantityOut] = {
            "max_bending_stress": out(ureg.Quantity(sigma_mpa, "MPa"), units["stress"]),
            "max_moment": out(ureg.Quantity(m_max, "N*m"), units["moment"]),
            "max_shear_force": out(ureg.Quantity(v_max, "N"), units["force"]),
            "max_deflection": out(ureg.Quantity(y_max * 1000.0, "mm"), units["length"]),
        }
        for name, newtons in reactions.items():
            values[name] = out(ureg.Quantity(newtons, "N"), units["force"])
        if sup == "cantilever":
            values["wall_moment"] = values["max_moment"]
        if y_max > 0:
            values["span_over_deflection"] = QuantityOut(quantity=l_m / y_max, unit="dimensionless")
        values["section_second_moment"] = QuantityOut(quantity=i_mm4, unit="millimeter ** 4")
        values["section_modulus"] = QuantityOut(quantity=i_mm4 / c_mm, unit="millimeter ** 3")

        warnings: list[str] = []
        if kind_load == "udl" and not is_blank(position):
            warnings.append("'position' is ignored for a uniform load (it always covers the whole length)")
        if y_max / l_m > 0.05:
            warnings.append(
                f"maximum deflection is {y_max / l_m:.1%} of the span: small-deflection theory is no longer accurate "
                "(large-displacement effects change moments and stiffness)"
            )
        slender = l_m * 1000.0 / depth_mm
        if slender < 10.0:
            warnings.append(
                f"span / depth = {slender:.1f} is below about 10: a deep beam, where shear deformation (not in this "
                "Euler-Bernoulli result) adds noticeably to the deflection"
            )
        allow = optional_quantity(allowable_stress, name="allowable_stress", dimension="pressure", unit="MPa", lo=1.0, hi=5000.0)
        if allow is not None:
            util = sigma_mpa / float(allow.to("MPa").magnitude)
            values["utilization"] = QuantityOut(quantity=util, unit="dimensionless")
            if util > 1.0:
                warnings.append(f"maximum bending stress is {util:.2f} x the allowable")
        return Computed(
            values=values,
            primary="max_bending_stress",
            warnings=warnings,
            data={
                "case": f"{sup}/{kind_load}",
                "formula": "sigma = M c / I with the closed-form M, V, y of the case",
                "max_moment_position_m": m_at,
                "total_load_n": total,
                "section": sec,
                "section_depth_mm": depth_mm,
            },
        )

    return run_tool(
        tool_name="mech_beam_bending",
        engine=ENGINE,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("pint",),
        standing_warnings=(
            "Prismatic beam, linear elastic, small deflection, self-weight ignored. Stress concentrations, lateral-"
            "torsional buckling, local buckling of thin walls and shear stress are not checked.",
        ),
    )


__all__ = ["beam_bending"]
