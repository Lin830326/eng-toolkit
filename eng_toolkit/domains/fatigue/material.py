"""Material-table reductions and explicitly limited empirical correlations."""

import math
from typing import Annotated

import numpy as np
from pydantic import Field

from ._common import Result, choice, fields, invalid, number, positive_exp, q, records, regression, run
from ._materials import TYPICAL
from ._server import server


def stress_strain_points(text):
    points = []
    for i, row in enumerate(records(text, "points")):
        fields(row, (2,), f"points/{i}")
        points.append((q(row[0], f"points/{i}/stress", "stress", zero=True),
                       q(row[1], f"points/{i}/strain", "strain", zero=True)))
    return np.asarray(points, dtype=float)


@server.tool(description="Engineering/true stress-strain before necking; Hollomon/Ramberg-Osgood fits, table-based 0.2% yield and energy areas. Steel HB-strength correlation, Larson-Miller absolute-temperature parameter, and a small sourced typical-property table. No hardness-scale conversions or certified material allowables.")
def material(
    mode: Annotated[str, Field(description="convert, fit, yield, hardness, larson_miller or typical; selects a material-data reduction or correlation.")] = "convert",
    direction: Annotated[str, Field(description="Conversion direction: engineering_to_true or true_to_engineering; convert mode only.")] = "engineering_to_true",
    stress: Annotated[str, Field(description="Input tensile stress, e.g. '300 MPa'; convert mode, in the selected engineering/true convention; '' omits.")] = "",
    strain: Annotated[str, Field(description="Input tensile strain, e.g. '0.1 dimensionless'; convert mode, selected engineering/true convention; '' omits.")] = "",
    uniform_strain: Annotated[str, Field(description="Maximum uniform engineering strain before necking, e.g. '0.15 dimensionless'; required for convert; '' omits.")] = "",
    points: Annotated[str, Field(description="Records 'stress,strain', e.g. '200 MPa,0.001 dimensionless'; fit/yield, <=2000 and 60 kB; '' omits.")] = "",
    fit_model: Annotated[str, Field(description="ramberg_osgood uses true total strain minus sigma/E; hollomon uses true plastic strain directly; fit mode.")] = "ramberg_osgood",
    elastic_modulus: Annotated[str, Field(description="Young's modulus E, e.g. '200 GPa'; Ramberg-Osgood fit requires it, yield mode estimates it; '' omits.")] = "",
    elastic_fit_max: Annotated[str, Field(description="Yield-mode upper engineering strain for the initial elastic slope fit, e.g. '0.001 dimensionless'; >=3 rows needed.")] = "0.001 dimensionless",
    table_reaches_fracture: Annotated[bool, Field(description="Yield table includes fracture endpoint; true labels the full area toughness, false reports work to last point.")] = False,
    hardness_hb: Annotated[float, Field(description="Steel Brinell hardness HB, dimensionless 100..400; hardness mode, 0 means infer HB from ultimate_strength.")] = 0,
    ultimate_strength: Annotated[str, Field(description="Steel ultimate strength for inverse HB correlation, e.g. '690 MPa'; hardness mode, '' uses hardness_hb.")] = "",
    temperature: Annotated[str, Field(description="Absolute exposure temperature, e.g. '500 degC'; Larson-Miller only; offset aliases supported, '' omits.")] = "",
    exposure_time: Annotated[str, Field(description="Rupture exposure time, e.g. '1000 hour'; Larson-Miller input, conflicts with supplied parameter; '' omits.")] = "",
    larson_miller_c: Annotated[float, Field(description="Larson-Miller empirical constant C, dimensionless 1..50, default 20; calibrate for material and stress.")] = 20,
    larson_parameter: Annotated[str, Field(description="Larson-Miller P=T*(C+log10(hours)) in K or degR only (no degC/degF offsets), e.g. '20000 K'; inverse time query; '' computes P.")] = "",
    material_name: Annotated[str, Field(description="Typical-table alloy name: all, 1020, 1045, 4140, 304, 6061-T6, 7075-T6, Ti-6Al-4V or grey_iron.")] = "all",
):
    inputs = dict(locals())

    def compute(handle):
        kind = choice(mode, "mode", ("convert", "fit", "yield", "hardness", "larson_miller", "typical"))
        r = Result({"conversion": "sigma_true=sigma_eng*(1+eps_eng); eps_true=log1p(eps_eng)",
                    "Hollomon_RO": "sigma=K*eps_pl^n; RO eps_pl=eps_total-sigma/E; centered log10 least squares",
                    "offset_yield": "first intersection of linearly interpolated stress table with E*(eps-.002)+intercept",
                    "energy": "area by composite trapezoid; elastic resilience=Sy^2/(2E)",
                    "HB": "steel Su[MPa] ~=3.45*HB", "Larson_Miller": "P=T[K]*(C+log10(t[hour]))"},
                   "Uniaxial monotonic tensile data. Before-necking conversion assumes uniform deformation and constant volume. Table energies are engineering work per initial volume.",
                   "Conversion limited by caller's uniform engineering strain. Fits require >=1 decade plastic strain. HB=100..400 for steels only. Larson-Miller is a calibrated creep-rupture correlation, not creep-fatigue.")
        groups = {"convert": (stress, strain, uniform_strain), "table": (points, elastic_modulus),
                  "hardness": (hardness_hb, ultimate_strength), "larson_miller": (temperature, exposure_time, larson_parameter)}
        allowed = "table" if kind in ("fit", "yield") else kind
        for group, entries in groups.items():
            if group != allowed and any(entries):
                invalid("mode", f"{group} inputs conflict with {kind} mode")
        if kind == "convert":
            way = choice(direction, "direction", ("engineering_to_true", "true_to_engineering"))
            sigma = q(stress, "stress", "stress")
            eps = q(strain, "strain", "strain", zero=True)
            uniform = q(uniform_strain, "uniform_strain", "strain")
            number(uniform, "uniform_strain", 1e-6, 1)
            eng = eps if way == "engineering_to_true" else math.expm1(eps)
            if eng > uniform*(1+1e-12):
                invalid("strain", "strain exceeds caller's uniform strain; post-necking conversion is invalid")
            if way == "engineering_to_true":
                r.add("stress", sigma*(1+eps), "MPa")
                r.add("strain", math.log1p(eps))
            else:
                r.add("stress", sigma*math.exp(-eps), "MPa")
                r.add("strain", eng)
            return r.done("stress")
        if kind == "fit":
            model = choice(fit_model, "fit_model", ("ramberg_osgood", "hollomon"))
            table = stress_strain_points(points)
            stresses, strains = table.T
            e = q(elastic_modulus, "elastic_modulus", "stress", optional=model == "hollomon")
            if model == "hollomon" and e is not None:
                invalid("elastic_modulus", "Hollomon points already contain plastic strain; omit E")
            plastic = strains-stresses/e if e is not None else strains
            if np.any(stresses <= 0) or np.any(plastic <= 0):
                invalid("points", "fits need positive stress and positive plastic strain")
            if e is not None and np.any(plastic < 1e-10*np.maximum(strains, stresses/e)):
                invalid("points", "elastic subtraction leaves unresolved plastic strain", "input.out_of_range")
            if max(plastic)/min(plastic) < 10*(1-1e-12):
                invalid("points", "plastic strain must span at least one decade")
            stats = regression(np.log10(plastic), np.log10(stresses), "points")
            n = number(stats["slope"], "points", .01, 1)
            strength = positive_exp(stats["intercept"]*math.log(10), "points")
            r.add("strength_coefficient", strength, "MPa")
            r.add("hardening_exponent", n)
            r.data.update(fit=stats, plastic_strain_range=[float(min(plastic)), float(max(plastic))])
            r.warnings.append("Use true uniform-deformation data for constitutive fitting. Residual scatter is descriptive, not a material allowable.")
            return r.done("strength_coefficient")
        if kind == "yield":
            if elastic_modulus:
                invalid("elastic_modulus", "yield mode estimates E from the initial table slope")
            table = stress_strain_points(points)
            stresses, strains = table.T
            if len(table) < 4 or strains[0] != 0 or stresses[0] != 0 or np.any(np.diff(strains) <= 0):
                invalid("points", "yield table needs >=4 rows, origin (0,0), and strictly increasing strain")
            limit = q(elastic_fit_max, "elastic_fit_max", "strain")
            number(limit, "elastic_fit_max", 1e-6, .002)
            mask = strains <= limit
            if np.count_nonzero(mask) < 3:
                invalid("elastic_fit_max", "elastic fit range must include at least three points")
            stats = regression(strains[mask], stresses[mask], "points")
            e = number(stats["slope"], "points", 1, 1e7)
            intercept = stats["intercept"]
            if stats["r_squared"] is None or stats["r_squared"] < .999 or abs(intercept) > .02*max(stresses[mask]):
                invalid("elastic_fit_max", "initial data are not sufficiently linear: require R^2>=.999 and near-zero intercept")
            residual = stresses - (e*(strains-.002)+intercept)
            crossings = np.flatnonzero((residual[:-1] >= 0) & (residual[1:] <= 0) & (strains[1:] > limit))
            if not len(crossings):
                invalid("points", "table does not bracket the 0.2% offset yield intersection")
            i = int(crossings[0])
            fraction = residual[i]/(residual[i]-residual[i+1])
            sy = stresses[i]+fraction*(stresses[i+1]-stresses[i])
            ey = strains[i]+fraction*(strains[i+1]-strains[i])
            area = float(np.sum((stresses[:-1]+stresses[1:])*np.diff(strains)/2))
            r.add("elastic_modulus", e, "MPa")
            r.add("offset_yield", sy, "MPa")
            r.add("offset_yield_strain", ey)
            r.add("resilience", sy*sy/(2*e), "MJ/m^3")
            r.add("toughness" if table_reaches_fracture else "work_to_last_point", area, "MJ/m^3")
            r.data.update(elastic_fit={"slope_MPa": e, "intercept_MPa": intercept, "r_squared": stats["r_squared"],
                                      "points": int(np.count_nonzero(mask)), "strain_range": [0, float(max(strains[mask]))]},
                          yield_segment=[i, i+1], table_reaches_fracture=table_reaches_fracture)
            return r.done("offset_yield")
        if kind == "hardness":
            if bool(hardness_hb) == bool(ultimate_strength):
                invalid("hardness_hb", "supply HB or ultimate strength, not both or neither")
            su = q(ultimate_strength, "ultimate_strength", "stress", optional=True)
            hb = number(hardness_hb if su is None else su/3.45, "hardness_hb", 100, 400)
            r.add("brinell_hardness", hb)
            r.add("ultimate_strength", 3.45*hb, "MPa")
            r.data["illustrative_strength_band_MPa"] = [3.45*hb*.9, 3.45*hb*1.1]
            r.warnings.append("HB-to-Su is an approximate steel correlation. The +/-10% display band is a screening convention, not a guaranteed accuracy or confidence interval. No HRC/HV conversion is offered.")
            return r.done("ultimate_strength")
        if kind == "larson_miller":
            temp = q(temperature, "temperature", "temperature")
            constant = number(larson_miller_c, "larson_miller_c", 1, 50)
            if bool(exposure_time) == bool(larson_parameter):
                invalid("exposure_time", "supply exposure time or Larson-Miller parameter, not both or neither")
            if exposure_time:
                hours = q(exposure_time, "exposure_time", "time")
                parameter = temp*(constant+math.log10(hours))
            else:
                parameter = q(larson_parameter, "larson_parameter", "temperature_scale")
                hours = positive_exp((parameter/temp-constant)*math.log(10), "larson_parameter")
                number(hours, "larson_parameter", 1e-12, 1e12)
            r.add("temperature_kelvin", temp, "K")
            r.add("larson_miller_parameter", parameter, "K")
            r.add("exposure_time", hours, "hour")
            r.warnings.append("C=20 is a common starting value, not universal. Compare parameters only at the same stress for a calibrated material; no rupture strength or safe life is inferred.")
            return r.done("larson_miller_parameter")
        choice(material_name, "material_name", ("all", *(row["material"] for row in TYPICAL)))
        rows = TYPICAL if material_name == "all" else [row for row in TYPICAL if row["material"] == material_name]
        r.data.update(properties=rows, label="typical, not guaranteed, not for design",
                      range_definition="spans across listed source conditions, not statistical bounds; equal endpoints are nominal; null means unavailable or not applicable")
        r.add("material_count", len(rows))
        r.warnings.append("Typical, not guaranteed, not for design. Product form, heat treatment, direction, thickness and test method matter. These rows never supply properties automatically to another calculation.")
        return r.done("material_count")
    return run("material", inputs, compute)
