"""GD&T tools of the ``metrology`` sub-server: form errors from measured points and true position with bonus tolerance.

Registered on the domain's server from :mod:`eng_toolkit.domains.metrology`.  Like the other tools here they only parse strings
into numbers (through the unit gatekeeper) and shape the result into the contract; the geometry lives in :mod:`.form` and the
position arithmetic in :mod:`.position`, the text of point lists in :mod:`.pointio`.
"""

from __future__ import annotations

import math
from typing import Annotated, Any

import numpy as np
from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.metrology import form, pointio, position
from eng_toolkit.domains.metrology.tools import QuantityIn, _choice, _invalid, _positive_mm, _unit_to_mm, server
from eng_toolkit.errors import ToolFailure
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import parse_quantity, ureg

ENGINE_FORM = "eng_toolkit.metrology.form_error"
ENGINE_POSITION = "eng_toolkit.metrology.position_tolerance"
TOOL_NAMES: tuple[str, ...] = ("form_error", "position_tolerance")

_AXES = ("x", "y", "z")
_COLUMNS = {"flatness": 3, "straightness": 2, "circularity": (2, 3), "cylindricity": 3}


def _output_unit(output_unit: str) -> tuple[str, float]:
    unit = str(parse_quantity(f"1 {output_unit}", name="output_unit", dimension="length").units)
    return unit, float(ureg.Quantity(1.0, "millimeter").to(unit).magnitude)


def _signif(value: float) -> float:
    if not math.isfinite(value):  # unreachable after the magnitude guards; a non-finite number would break the JSON result
        raise ToolFailure("engine.failure", f"a result value is not finite ({value})", {"value": repr(value)})
    return float(f"{value:.9g}")


def _scaled(raw: np.ndarray, factor: float, name: str) -> np.ndarray:
    """``raw * factor`` (millimetres) after refusing magnitudes that cannot be meant."""
    with np.errstate(over="ignore", invalid="ignore"):
        out = raw * factor
    form.check_magnitude(out, name)
    return out


def _length_mm(raw: str, name: str) -> float:
    value = float(parse_quantity(raw, name=name, dimension="length").to("millimeter").magnitude)
    form.check_magnitude(value, name)
    return value


# --------------------------------------------------------------------------- form error


def _read_points(job: JobHandle, points: str, points_path: str, feature: str) -> np.ndarray:
    given_inline, given_file = bool(points.strip()), bool(points_path.strip())
    if given_inline == given_file:
        raise _invalid("give the points either inline ('points') or as a file ('points_path'), exactly one of them")
    if given_file:
        path = job.input_file(
            points_path,
            param="points_path",
            extensions=pointio.POINT_FILE_EXTENSIONS,
            max_bytes=pointio.MAX_FILE_BYTES,
            too_large_family="input.too_large",
        )
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise _invalid("points_path: the file is not UTF-8 text; give a CSV or whitespace-separated point list") from exc
        param = "points_path"
    else:
        text, param = points, "points"
    return pointio.parse_numbers(text, param=param, columns=_COLUMNS[feature], names=_AXES)


@server.tool(
    name="form_error",
    description=(
        "Form error of a feature from measured points: flatness (x,y,z points), straightness (x,y points of a profile in a "
        "plane), circularity/roundness (x,y points of a section; x,y,z points are projected onto their best-fit plane) or "
        "cylindricity (x,y,z points). The value is the width of the minimum zone (ISO 1101): the thinnest pair of parallel "
        "planes / lines, concentric circles or coaxial cylinders that holds every point. Straightness is exact; flatness and "
        "circularity are exact minimum zones for small point sets and polished, optimality-checked minimum zones above that; "
        "cylindricity is an axis search that gives an upper bound, never claimed minimal. data.method says which estimator "
        "ran and whether the value is the proven minimum. The least-squares zone is returned for comparison. Points come "
        "inline ('points') or from a CSV/text file ('points_path'); coordinates carry no unit, so 'unit' names it. An optional "
        "'tolerance' gives a pass/fail."
    ),
)
def form_error(
    feature: Annotated[
        str, Field(description="'flatness', 'straightness', 'circularity' or 'cylindricity'")
    ],
    points: Annotated[
        str,
        Field(
            description=(
                "Measured points inline, one point per row: JSON [[x,y,z],...] or text with a row per line or ';' and values "
                "separated by commas or spaces, e.g. '0,0,0.01; 10,0,-0.01; 0,10,0.02; 10,10,0'. x,y for straightness and "
                "circularity, x,y,z for flatness and cylindricity. Leave empty when using points_path"
            )
        ),
    ] = "",
    points_path: Annotated[
        str,
        Field(
            description=(
                "Path to a point file (.csv .txt .xyz .pts .asc .dat), same layout as 'points'; one header line is skipped and, "
                "if it names x, y, z, those columns are used. Leave empty when using 'points'"
            )
        ),
    ] = "",
    unit: Annotated[
        str,
        Field(description="Length unit of the coordinates, which carry none, as a unit only with no number: 'mm', 'um', 'inch', 'm'"),
    ] = "mm",
    tolerance: Annotated[
        str, Field(description="Optional form tolerance with unit, e.g. '0.02 mm'; adds a pass/fail to the result. Empty = none")
    ] = "",
    output_unit: Annotated[str, Field(description="Length unit of the results, e.g. 'mm', 'um', 'inch'")] = "mm",
) -> ToolResult:
    inputs = {
        "feature": feature,
        "points": points,
        "points_path": points_path,
        "unit": unit,
        "tolerance": tolerance,
        "output_unit": output_unit,
    }

    def compute(job: JobHandle) -> Computed:
        _choice(feature, form.FEATURES, "feature")
        to_mm = _unit_to_mm(unit, "unit")
        tol_mm = _positive_mm(tolerance, "tolerance") if tolerance.strip() else None
        if tol_mm is not None:
            form.check_magnitude(tol_mm, "tolerance")
        out_unit, per_mm = _output_unit(output_unit)
        raw = _read_points(job, points, points_path, feature)
        coords = _scaled(raw, to_mm, "points")
        resolution = float(np.spacing(np.abs(coords).max()))
        distinct = form.unique_rows(coords)
        form.check_count(feature, len(distinct), len(coords))

        warnings: list[str] = []
        extra: dict[str, Any] = {}
        if len(distinct) < len(coords):
            warnings.append(
                f"{len(coords) - len(distinct)} duplicate point(s) were dropped; {len(distinct)} distinct points are used"
            )
        if feature == "flatness":
            res = form.flatness(distinct)
        elif feature == "straightness":
            res = form.straightness(distinct)
        elif feature == "cylindricity":
            res = form.cylindricity(distinct)
        else:
            plane = distinct
            if distinct.shape[1] == 3:
                plane, off_plane = form.project_to_plane(distinct)
                plane = form.unique_rows(plane)
                form.check_count("circularity", len(plane), len(distinct))
                extent = float(np.ptp(plane, axis=0).max())
                extra["out_of_plane_spread_mm"] = off_plane
                extra["projection"] = "x,y,z points projected onto their least-squares plane"
                if off_plane > 0.01 * extent:
                    warnings.append(
                        f"the points are not in one plane: {off_plane:.6g} mm of spread off the best-fit plane (1% of the "
                        "section size is exceeded). Circularity is that of the projected points; use cylindricity for a 3-D "
                        "feature"
                    )
            res = form.circularity(plane)

        if len(distinct) < 4 * form.MIN_POINTS[feature]:
            warnings.append(
                f"only {len(distinct)} points: a form error measured from sparse points under-estimates the form error of the "
                "surface between them"
            )
        if res.value <= 10 * resolution:
            warnings.append(
                f"the value ({res.value:.3g} mm) is within 10 times the resolution of the coordinates ({resolution:.3g} mm, one "
                "unit in the last place of the largest coordinate): treat it as zero"
            )
        warnings.extend(res.notes)
        if not res.exact:
            warnings.append(
                "the value is a zone that contains every point, hence an upper bound of the minimum-zone form error: a pass is "
                "conclusive, a fail is not proven to be a true fail"
            )

        def length(value_mm: float) -> QuantityOut:
            return QuantityOut(quantity=_signif(value_mm * per_mm), unit=out_unit)

        values = {"form_error": length(res.value)}
        if res.least_squares_value is not None:
            values["least_squares_form_error"] = length(res.least_squares_value)
        values["points_used"] = QuantityOut(quantity=float(len(distinct)), unit="dimensionless")

        data: dict[str, Any] = {
            "feature": feature,
            "method": {
                "estimator": res.estimator,
                "search": res.search,
                "exact": res.exact,
                "first_order_optimal": res.first_order_optimal,
                "is_upper_bound_of_minimum_zone": True,
                "note": (
                    "every value is the width of a zone that really contains all points; 'exact' means it is also proven "
                    "to be the thinnest such zone"
                ),
            },
            "least_squares_reference": (
                None if res.least_squares_value is None else "zone about the least-squares reference, for comparison"
            ),
            "zone": res.zone,
            "zone_unit": "mm (coordinates converted to millimetres; normals and directions are unit vectors)",
            "points": {
                "given": int(len(coords)),
                "distinct": int(len(distinct)),
                "unit": unit.strip(),
                "coordinate_resolution_mm": resolution,
            },
            **extra,
        }
        if tol_mm is not None:
            passed = res.value <= tol_mm * (1.0 + position.RELATIVE_SLACK) + position.ULPS * resolution
            data["tolerance_check"] = {
                "tolerance_mm": tol_mm,
                "form_error_mm": res.value,
                "within_tolerance": passed,
                "margin_mm": tol_mm - res.value,
                "conclusive": bool(passed or res.exact),
                "basis": (
                    "minimum zone (proven)" if res.exact else "upper bound of the minimum zone: a pass is conclusive, a fail may not be"
                ),
            }
            if not passed and not res.exact:
                warnings.append(
                    "the part fails the tolerance with this zone, but the zone is not proven minimal, so the fail is not proven"
                )
        return Computed(values=values, primary="form_error", warnings=warnings, data=data)

    return run_tool(
        tool_name="metrology_form_error",
        engine=ENGINE_FORM,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("numpy",),
        standing_warnings=(
            "Form error of the measured points only, from their coordinates as given: no filtering, probe-radius "
            "compensation, outlier removal or measurement uncertainty. The applicable standard (ISO 1101, ISO 12181, ISO 12780, "
            "ISO 12781) and the quality of the measurement are the user's responsibility.",
        ),
    )


# --------------------------------------------------------------------------- true position


def _modifier(raw: str) -> str:
    text = raw.strip()
    if text.lower() == "none" or text == "":
        return "none"
    if text.upper() in ("M", "L"):
        return text.upper()
    raise _invalid(f"bonus_modifier must be one of ['none', 'M', 'L'], got {raw!r}", param="bonus_modifier")


def _size_mm(raw: str, name: str) -> float:
    return _length_mm(raw, name)


@server.tool(
    name="position_tolerance",
    description=(
        "True position (ISO 1101) of features from their nominal and measured coordinates. For each feature the deviation is "
        "measured minus nominal and the position error is the diameter of the zone it needs, 2*sqrt(dx^2+dy^2) for x,y "
        "(spherical SØ 2*sqrt(dx^2+dy^2+dz^2) for x,y,z). It passes when that does not exceed the tolerance plus the bonus. "
        "With bonus_modifier 'M' (maximum material condition) or 'L' (least material condition) the bonus is how far the "
        "actual feature size has left its MMC / LMC size (for a hole MMC is the smallest hole, for a shaft the largest). "
        "Datum shift is not applied; coordinates must already be in the datum reference frame of the nominal."
    ),
)
def position_tolerance(
    nominal: Annotated[
        str,
        Field(
            description=(
                "Nominal feature coordinates, one feature per row: JSON [[x,y],...] or text '10,20; 30,40' (rows by line or ';'). "
                "x,y or x,y,z, in 'unit'"
            )
        ),
    ],
    measured: Annotated[
        str, Field(description="Measured feature coordinates, same layout, row count and column count as 'nominal'")
    ],
    tolerance: Annotated[
        QuantityIn,
        Field(description="Stated position tolerance, the diameter of the zone, with unit, e.g. '0.1 mm' ('0 mm' is allowed)"),
    ],
    unit: Annotated[
        str,
        Field(
            description=(
                "Length unit of the numbers in nominal, measured and feature_size, as a unit only with no number: 'mm', 'um', "
                "'inch'"
            )
        ),
    ] = "mm",
    bonus_modifier: Annotated[
        str, Field(description="'none' (default, no bonus), 'M' (MMC bonus) or 'L' (LMC bonus)")
    ] = "none",
    feature_kind: Annotated[
        str,
        Field(
            description=(
                "'hole' (internal feature: slot, hole; MMC is its smallest size) or 'shaft' (external feature: pin, boss; MMC is "
                "its largest size). Only used with a bonus modifier"
            )
        ),
    ] = "hole",
    feature_size: Annotated[
        str,
        Field(
            description=(
                "Actual size of each feature (measured diameter or width) in 'unit', one number per feature in the order of "
                "nominal: JSON [10.02, 10.05] or '10.02; 10.05'. Required with a bonus modifier"
            )
        ),
    ] = "",
    mmc_size: Annotated[
        str, Field(description="Maximum material size with unit, e.g. '10 mm'. Required with bonus_modifier 'M'")
    ] = "",
    lmc_size: Annotated[
        str, Field(description="Least material size with unit, e.g. '10.1 mm'. Required with bonus_modifier 'L'")
    ] = "",
    output_unit: Annotated[str, Field(description="Length unit of the results, e.g. 'mm', 'um', 'inch'")] = "mm",
) -> ToolResult:
    inputs = {
        "nominal": nominal,
        "measured": measured,
        "tolerance": tolerance,
        "unit": unit,
        "bonus_modifier": bonus_modifier,
        "feature_kind": feature_kind,
        "feature_size": feature_size,
        "mmc_size": mmc_size,
        "lmc_size": lmc_size,
        "output_unit": output_unit,
    }

    def compute(_: JobHandle) -> Computed:
        to_mm = _unit_to_mm(unit, "unit")
        modifier = _modifier(bonus_modifier)
        _choice(feature_kind, position.FEATURE_KINDS, "feature_kind")
        out_unit, per_mm = _output_unit(output_unit)
        tol_mm = _size_mm(tolerance, "tolerance")
        if tol_mm < 0:
            raise _invalid(f"tolerance must be >= 0, got {tolerance!r}", param="tolerance")

        nom = _scaled(pointio.parse_numbers(nominal, param="nominal", columns=(2, 3), names=_AXES), to_mm, "nominal")
        mea = _scaled(pointio.parse_numbers(measured, param="measured", columns=(2, 3), names=_AXES), to_mm, "measured")
        if len(nom) > 10_000:
            raise ToolFailure("input.too_large", f"{len(nom)} features; at most 10000 are evaluated", {"features": len(nom)})
        if nom.shape != mea.shape:
            raise _invalid(
                f"nominal has {len(nom)} rows of {nom.shape[1]} values but measured has {len(mea)} rows of {mea.shape[1]}; "
                "give one measured row per nominal row with the same columns",
                nominal_shape=list(nom.shape),
                measured_shape=list(mea.shape),
            )

        warnings: list[str] = []
        sizes = mmc = lmc = None
        if modifier != "none":
            if not feature_size.strip():
                raise ToolFailure(
                    "input.missing",
                    f"bonus_modifier {modifier!r} needs feature_size, the actual size of each feature",
                    {"param": "feature_size"},
                )
            sizes = _scaled(pointio.parse_numbers(feature_size, param="feature_size", columns=1), to_mm, "feature_size")
            if len(sizes) != len(nom):
                raise _invalid(
                    f"feature_size has {len(sizes)} values but there are {len(nom)} features; give one size per feature",
                    param="feature_size",
                )
            need = "mmc_size" if modifier == "M" else "lmc_size"
            if not (mmc_size if modifier == "M" else lmc_size).strip():
                raise ToolFailure("input.missing", f"bonus_modifier {modifier!r} needs {need}", {"param": need})
        else:
            ignored = [n for n, v in (("feature_size", feature_size), ("mmc_size", mmc_size), ("lmc_size", lmc_size)) if v.strip()]
            if ignored:
                warnings.append(f"{', '.join(ignored)} given but bonus_modifier is 'none': no bonus is applied and they are ignored")
        mmc = _size_mm(mmc_size, "mmc_size") if mmc_size.strip() else None
        lmc = _size_mm(lmc_size, "lmc_size") if lmc_size.strip() else None
        if mmc is not None and lmc is not None and modifier != "none":
            ordered = mmc <= lmc if feature_kind == "hole" else mmc >= lmc
            if not ordered:
                raise _invalid(
                    f"for a {feature_kind} the MMC size must be "
                    f"{'not above' if feature_kind == 'hole' else 'not below'} the LMC size, got mmc_size={mmc_size!r}, "
                    f"lmc_size={lmc_size!r}",
                    param="mmc_size",
                )
        res = position.evaluate(
            nom, mea, tol_mm, modifier=modifier, kind=feature_kind, sizes=sizes, mmc=mmc, lmc=lmc
        )
        if res.beyond_limit.any():
            which = (np.flatnonzero(res.beyond_limit) + 1).tolist()
            limit = "MMC" if modifier == "M" else "LMC"
            warnings.append(
                f"feature(s) {which[:10]} are beyond their {limit} size: they fail their size tolerance and get no bonus "
                "(bonus 0)"
            )
        if res.outside_size_limits.any():
            which = (np.flatnonzero(res.outside_size_limits) + 1).tolist()
            warnings.append(f"feature(s) {which[:10]} are outside the size limits between mmc_size and lmc_size")
        dims = nom.shape[1]
        zone_name = "cylindrical zone (diameter)" if dims == 2 else "spherical zone (SØ, diameter)"

        def length(value_mm: float) -> QuantityOut:
            return QuantityOut(quantity=_signif(value_mm * per_mm), unit=out_unit)

        def row(i: int) -> dict[str, Any]:
            entry: dict[str, Any] = {
                "feature": i + 1,
                "nominal": [_signif(v * per_mm) for v in nom[i]],
                "measured": [_signif(v * per_mm) for v in mea[i]],
                "deviation": [_signif(v * per_mm) for v in res.deviation[i]],
                "radial_deviation": _signif(res.distance[i] * per_mm),
                "position_error": _signif(res.error[i] * per_mm),
                "tolerance": _signif(tol_mm * per_mm),
                "bonus": _signif(res.bonus[i] * per_mm),
                "allowed_zone": _signif(res.allowed[i] * per_mm),
                "margin": _signif(res.margin[i] * per_mm),
                "within_tolerance": bool(res.within[i]),
            }
            if dims == 2:
                entry["deviation_angle_deg"] = None if res.distance[i] == 0 else _signif(
                    math.degrees(math.atan2(res.deviation[i][1], res.deviation[i][0]))
                )
            if sizes is not None:
                entry["feature_size"] = _signif(sizes[i] * per_mm)
            return entry

        worst = int(np.argmax(res.error - res.allowed))
        failed = int((~res.within).sum())
        values = {
            "position_error": length(float(res.error.max())),
            "worst_margin": length(float(res.margin.min())),
            "features_out_of_position": QuantityOut(quantity=float(failed), unit="dimensionless"),
            "max_bonus": length(float(res.bonus.max())),
        }
        data: dict[str, Any] = {
            "unit": out_unit,
            "zone": zone_name,
            "bonus_modifier": modifier,
            "feature_kind": feature_kind if modifier != "none" else None,
            "all_within_tolerance": failed == 0,
            "features_count": len(nom),
            "worst_feature": worst + 1,
            "features": [row(i) for i in range(len(nom))],
            "formula": "position_error = 2*|measured - nominal|; allowed = tolerance + bonus; bonus (hole, M) = size - MMC, "
            "(shaft, M) = MMC - size, (hole, L) = LMC - size, (shaft, L) = size - LMC, never below 0",
            "sizes": (
                None
                if modifier == "none"
                else {
                    "mmc_size": None if mmc is None else _signif(mmc * per_mm),
                    "lmc_size": None if lmc is None else _signif(lmc * per_mm),
                }
            ),
        }
        return Computed(values=values, primary="position_error", warnings=warnings, data=data)

    return run_tool(
        tool_name="metrology_position_tolerance",
        engine=ENGINE_POSITION,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("numpy",),
        standing_warnings=(
            "True position of each feature on its own against the stated zone, in the coordinates as given: the measured "
            "coordinates must already be in the datum reference frame of the nominal (no datum shift or datum modifier, composite "
            "or projected tolerance zone, nor form or orientation of the feature is evaluated). The applicable standard "
            "(ISO 1101, ISO 2692) and the measurement are the user's responsibility.",
        ),
    )


__all__ = ["ENGINE_FORM", "ENGINE_POSITION", "TOOL_NAMES", "form_error", "position_tolerance"]
