"""``thermal_conduction``: steady 1-D conduction through layers in series with films and contact resistances.

Formulas (textbook thermal-resistance network; Incropera / Cengel / Holman)
* plane wall (area A):   R_layer = t / (k A);    R_film = 1 / (h A);          R_contact = R''c / A
* cylinder (length L):   R_layer = ln(r_out / r_in) / (2 pi k L);   R_film = 1 / (h 2 pi r L);   R_contact = R''c / (2 pi r L)
* sphere:                R_layer = (1/r_in - 1/r_out) / (4 pi k);   R_film = 1 / (h 4 pi r^2);   R_contact = R''c / (4 pi r^2)
* series: R_total = sum of R, q = (T_inner - T_outer) / R_total, UA = 1 / R_total, interface temperatures by cumulative q R
* critical radius of insulation on a cylinder r_crit = k / h_outer, on a sphere r_crit = 2 k / h_outer
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.thermal._common import (
    DIM_H,
    DIM_K,
    DIM_RA,
    QuantityIn,
    TemperatureIn,
    UnitSystemIn,
    check_data,
    choice,
    is_blank,
    optional_quantity,
    out,
    positive_quantity,
    split_list,
    temperature,
)
from eng_toolkit.domains.thermal._common import unit_system as units_for
from eng_toolkit.domains.thermal._server import ENGINE, server
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import UnitsError

GEOMETRIES = ("plane", "cylinder", "sphere")
MAX_LAYERS = 30


def _layer_pairs(raw: Any) -> list[tuple[Any, Any]]:
    """The (thickness, conductivity) of each layer from ``'200 mm @ 0.72 W/(m*K); 50 mm @ 0.04 W/(m*K)'`` or a JSON array."""
    items = split_list(raw, name="layers")
    if not items:
        raise UnitsError("input.invalid", "'layers' is empty; give at least one layer", {"param": "layers"})
    if len(items) > MAX_LAYERS:
        raise UnitsError("input.out_of_range", f"at most {MAX_LAYERS} layers are supported, got {len(items)}", {"param": "layers", "max": MAX_LAYERS})
    pairs: list[tuple[Any, Any]] = []
    for index, item in enumerate(items, start=1):
        if isinstance(item, str):
            if item.count("@") != 1:
                raise UnitsError(
                    "input.invalid",
                    f"layer {index} ({item!r}) must be '<thickness> @ <conductivity>', e.g. '200 mm @ 0.72 W/(m*K)'",
                    {"param": "layers", "layer": index, "received": item},
                )
            thickness, conductivity = (part.strip() for part in item.split("@"))
        elif isinstance(item, Mapping):
            thickness = item.get("thickness")
            conductivity = item.get("conductivity", item.get("k"))
            if thickness is None or conductivity is None:
                raise UnitsError(
                    "input.invalid",
                    f"layer {index} needs 'thickness' and 'conductivity' (or 'k')",
                    {"param": "layers", "layer": index},
                )
        else:
            raise UnitsError("input.invalid", f"layer {index} must be text or an object, got {type(item).__name__}", {"param": "layers", "layer": index})
        pairs.append((thickness, conductivity))
    return pairs


@server.tool(
    name="conduction",
    description=(
        "Steady 1-D conduction through layers in series, with an optional convective film on each side and optional contact "
        "resistance between layers, for a plane wall, a cylinder (pipe wall plus insulation) or a sphere. Returns total "
        "resistance, heat rate, UA, U, and the temperature at the inner surface, every interface and the outer surface "
        "(inner side = first layer). 'layers' = '<thickness> @ <conductivity>' items separated by ';' or a JSON array of "
        "{thickness, conductivity}. Cylinder and sphere: the layers are shells outward from 'inner_radius'. Gives the "
        "critical insulation radius k/h (cylinder) or 2k/h (sphere) and warns if the outer radius is below it."
    ),
)
def conduction(
    geometry: Annotated[str, Field(description="'plane', 'cylinder' or 'sphere'")],
    layers: Annotated[
        str,
        Field(description="Layers from the inner side outward, e.g. '200 mm @ 0.72 W/(m*K); 50 mm @ 0.04 W/(m*K)' (or JSON [{\"thickness\":\"200 mm\",\"conductivity\":\"0.72 W/(m*K)\"}]). Max 30."),
    ],
    temperature_inner: Annotated[TemperatureIn, Field(description="Fluid (or surface, if no film) temperature on the inner side, e.g. '90 degC'")],
    temperature_outer: Annotated[TemperatureIn, Field(description="Fluid (or surface) temperature on the outer side, e.g. '20 degC'")],
    h_inner: Annotated[str, Field(description="Optional film coefficient on the inner side, e.g. '1000 W/(m^2*K)'. Blank = no film (surface at fluid temperature).")] = "",
    h_outer: Annotated[str, Field(description="Optional film coefficient on the outer side, e.g. '10 W/(m^2*K)'. Blank = no film.")] = "",
    contact_resistances: Annotated[
        str,
        Field(description="Optional areal contact resistance at each interface between layers (one fewer than layers), e.g. '0.0005 m^2*K/W; 0'. Blank = perfect contact."),
    ] = "",
    area: Annotated[str, Field(description="Plane wall only: area, default '1 m^2'")] = "",
    inner_radius: Annotated[str, Field(description="Cylinder / sphere only (required): inner radius of the first layer, e.g. '25 mm'")] = "",
    length: Annotated[str, Field(description="Cylinder only: length, default '1 m'")] = "",
    unit_system: UnitSystemIn = "metric",
) -> ToolResult:
    inputs = {
        "geometry": geometry,
        "layers": layers,
        "temperature_inner": temperature_inner,
        "temperature_outer": temperature_outer,
        "h_inner": h_inner,
        "h_outer": h_outer,
        "contact_resistances": contact_resistances,
        "area": area,
        "inner_radius": inner_radius,
        "length": length,
        "unit_system": unit_system,
    }

    def compute(_: JobHandle) -> Computed:
        units = units_for(unit_system)
        kind = choice(geometry, name="geometry", allowed=GEOMETRIES)
        notes: list[str] = []
        warnings: list[str] = []
        pairs = _layer_pairs(layers)
        n = len(pairs)
        thick = [
            positive_quantity(t, name=f"layers[{i}].thickness", dimension="length", unit="m", lo=1e-6, hi=1e3)
            for i, (t, _) in enumerate(pairs, start=1)
        ]
        cond = [
            positive_quantity(k, name=f"layers[{i}].conductivity", dimension=DIM_K, unit="W/m/K", lo=1e-3, hi=1e4, notes=notes)
            for i, (_, k) in enumerate(pairs, start=1)
        ]
        t_in = temperature(temperature_inner, name="temperature_inner")
        t_out = temperature(temperature_outer, name="temperature_outer")
        hi_ = optional_quantity(h_inner, name="h_inner", dimension=DIM_H, unit="W/m**2/K", lo=1e-2, hi=1e7, notes=notes)
        ho_ = optional_quantity(h_outer, name="h_outer", dimension=DIM_H, unit="W/m**2/K", lo=1e-2, hi=1e7, notes=notes)
        rc = [0.0] * (n - 1)
        if not is_blank(contact_resistances):
            items = split_list(contact_resistances, name="contact_resistances")
            if len(items) != n - 1:
                raise UnitsError(
                    "input.invalid",
                    f"'contact_resistances' needs {n - 1} value(s) (one per interface between {n} layers), got {len(items)}",
                    {"param": "contact_resistances", "expected": n - 1, "received": len(items)},
                )
            rc = [
                positive_quantity(v, name=f"contact_resistances[{i}]", dimension=DIM_RA, unit="m**2*K/W", lo=0.0, hi=10.0, notes=notes, allow_zero=True)
                for i, v in enumerate(items, start=1)
            ]
        for ignored, label in ((area, "area"), (inner_radius, "inner_radius"), (length, "length")):
            relevant = {"area": kind == "plane", "inner_radius": kind != "plane", "length": kind == "cylinder"}[label]
            if not relevant and not is_blank(ignored):
                warnings.append(f"'{label}' is not used for a {kind} and was ignored")

        # Radii (cylinder, sphere) and the geometry factors
        radii: list[float] = []
        if kind == "plane":
            a = 1.0 if is_blank(area) else positive_quantity(area, name="area", dimension="area", unit="m**2", lo=1e-10, hi=1e9)
            surface = lambda _r: a  # noqa: E731 - the same area at every interface
        else:
            if is_blank(inner_radius):
                raise UnitsError("input.missing", f"'inner_radius' is required for a {kind}", {"param": "inner_radius"})
            r = positive_quantity(inner_radius, name="inner_radius", dimension="length", unit="m", lo=1e-6, hi=1e3)
            radii = [r]
            for t in thick:
                radii.append(radii[-1] + t)
            if kind == "cylinder":
                ln = 1.0 if is_blank(length) else positive_quantity(length, name="length", dimension="length", unit="m", lo=1e-6, hi=1e6)
                surface = lambda rr: 2.0 * math.pi * rr * ln  # noqa: E731
            else:
                ln = None
                surface = lambda rr: 4.0 * math.pi * rr * rr  # noqa: E731

        def layer_resistance(i: int) -> float:
            if kind == "plane":
                return thick[i] / (cond[i] * a)
            if kind == "cylinder":
                return math.log1p(thick[i] / radii[i]) / (2.0 * math.pi * cond[i] * ln)
            return thick[i] / (radii[i] * radii[i + 1] * 4.0 * math.pi * cond[i])

        # The network, inner side first: (name, kind, resistance in K/W)
        elements: list[tuple[str, str, float]] = []
        if hi_ is not None:
            elements.append(("film_inner", "film", 1.0 / (hi_ * surface(radii[0] if radii else 0.0))))
        for i in range(n):
            elements.append((f"layer_{i + 1}", "layer", layer_resistance(i)))
            if i < n - 1 and rc[i] > 0.0:
                elements.append((f"contact_{i + 1}", "contact", rc[i] / surface(radii[i + 1] if radii else 0.0)))
        if ho_ is not None:
            elements.append(("film_outer", "film", 1.0 / (ho_ * surface(radii[-1] if radii else 0.0))))

        total = math.fsum(r for _, _, r in elements)
        q = (t_in - t_out) / total
        temps = [t_in]
        for _, _, r in elements:
            temps.append(temps[-1] - q * r)
        # Energy balance: the heat rate recomputed from the temperature drop across each element must equal q.
        spread = max(abs((temps[j] - temps[j + 1]) / r - q) for j, (_, _, r) in enumerate(elements)) / abs(q) if q != 0.0 else 0.0

        values: dict[str, QuantityOut] = {}
        node = {name: temps[j + 1] for j, (name, _, _) in enumerate(elements)}  # temperature after each element
        inner_surface = node["film_inner"] if hi_ is not None else t_in
        outer_surface = node[f"layer_{n}"]
        values["heat_rate"] = out(q, "power", units, label="heat_rate")
        values["total_resistance"] = out(total, "resistance", units, label="total_resistance")
        values["ua"] = out(1.0 / total, "ua", units, label="ua")
        values["temperature_inner_surface"] = out(inner_surface, "temperature", units, label="temperature_inner_surface")
        for i in range(1, n):
            after = node[f"layer_{i}"]
            if rc[i - 1] > 0.0:
                values[f"temperature_interface_{i}_inner_side"] = out(after, "temperature", units, label="interface")
                values[f"temperature_interface_{i}_outer_side"] = out(node[f"contact_{i}"], "temperature", units, label="interface")
            else:
                values[f"temperature_interface_{i}"] = out(after, "temperature", units, label="interface")
        values["temperature_outer_surface"] = out(outer_surface, "temperature", units, label="temperature_outer_surface")
        if kind == "plane":
            values["u"] = out(1.0 / (total * a), "h", units, label="u")
            values["heat_flux"] = out(q / a, "flux", units, label="heat_flux")
        else:
            values["u_outer"] = out(1.0 / (total * surface(radii[-1])), "h", units, label="u_outer")
            values["u_inner"] = out(1.0 / (total * surface(radii[0])), "h", units, label="u_inner")
            values["outer_radius"] = out(radii[-1], "length", units, label="outer_radius")
            if kind == "cylinder":
                values["heat_rate_per_length"] = out(q / ln, "power_per_length", units, label="heat_rate_per_length")
        data: dict[str, Any] = {
            "geometry": kind,
            "formulas": {
                "plane": "R = t/(kA); film 1/(hA); contact R''c/A",
                "cylinder": "R = ln(r_out/r_in)/(2 pi k L); film 1/(h 2 pi r L); contact R''c/(2 pi r L)",
                "sphere": "R = (1/r_in - 1/r_out)/(4 pi k); film 1/(h 4 pi r^2); contact R''c/(4 pi r^2)",
            }[kind],
            "heat_flows_from": "inner side to outer side when positive",
            "resistances": [
                {"name": name, "kind": role, "resistance_K_per_W": r, "fraction_of_total": r / total} for name, role, r in elements
            ],
            "energy_balance": {
                "heat_rate_W": q,
                "max_relative_difference_between_elements": spread,
                "note": "heat rate recomputed from the temperature drop across each element; all must equal the total",
            },
            "assumptions": [
                "steady state, one-dimensional, constant conductivity in each layer, no internal heat generation",
                "uniform temperature at each fluid and uniform film coefficient; radiation only if included in h",
            ],
        }
        if kind != "plane":
            data["radii_m"] = radii
            k_last = cond[-1]
            if ho_ is not None:
                r_crit = (k_last if kind == "cylinder" else 2.0 * k_last) / ho_
                values["critical_radius"] = out(r_crit, "length", units, label="critical_radius")
                data["critical_radius_formula"] = "r_crit = k_outer_layer/h_outer (cylinder), 2 k/h_outer (sphere); meaningful when the outer layer is the insulation"
                if radii[-1] < r_crit:
                    warnings.append(
                        f"outer radius {radii[-1]:.4g} m is below the critical radius {r_crit:.4g} m of the outer layer: adding more of "
                        "it up to that radius INCREASES the heat loss"
                    )
            else:
                data["critical_radius_note"] = "give h_outer to get the critical radius k/h (cylinder) or 2k/h (sphere)"
        if notes:
            data["unit_interpretation"] = notes
            warnings.extend(notes)
        return Computed(values=values, primary="heat_rate", warnings=warnings, data=check_data(data))

    return run_tool(
        tool_name="thermal_conduction",
        engine=ENGINE,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("pint",),
        standing_warnings=(
            "Steady one-dimensional conduction with constant conductivities, ideal layers and uniform films; temperature-dependent "
            "conductivity, edge effects, thermal bridges and radiation (unless included in h) are not modelled.",
        ),
    )


__all__ = ["conduction"]
