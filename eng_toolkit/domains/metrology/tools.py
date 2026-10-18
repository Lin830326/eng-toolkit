"""``metrology`` sub-server: ISO 286 fit limits, 1-D tolerance stack-up and scan-to-model checks.

The tools here only parse strings into numbers (through the unit gatekeeper) and shape the
result into the contract.  The tables and rules live in :mod:`.iso286`, the statistics in
:mod:`.stackup`, the point-cloud geometry in :mod:`.scan`.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
from typing import Annotated, Any

import numpy as np
from fastmcp import FastMCP
from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.metrology import iso286, scan, stackup
from eng_toolkit.domains.metrology.fit_search import (
    BASES,
    FIT_SET_NAMES,
    MAX_RESULTS,
    Candidate,
    search_fits,
)
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import UnitsError, parse_quantity, require_positive, to_quantity_out, ureg

ENGINE_ISO286 = "eng_toolkit.metrology.iso286"
ENGINE_STACKUP = "eng_toolkit.metrology.stackup"
ENGINE_FIT_SEARCH = "eng_toolkit.metrology.fit_search"
ENGINE_CHECK_SCAN = "eng_toolkit.metrology.check_scan"
TOOL_NAMES: tuple[str, ...] = ("fit_limits", "stackup_1d", "fit_search", "check_scan")

QuantityIn = Annotated[
    str,
    Field(
        description=(
            "Number followed by a unit, as one string, e.g. '25 mm', '0.05 mm', '1 inch'. "
            "A bare number without a unit is rejected."
        )
    ),
]

server = FastMCP(
    "metrology",
    instructions=(
        "Dimensional tolerance tools. Every length must carry a unit. "
        "Results are unreviewed engineering estimates and must be checked by a qualified engineer."
    ),
)


def _um(value: float) -> QuantityOut:
    return to_quantity_out(ureg.Quantity(value, "micrometer"))


def _mm(value: float) -> QuantityOut:
    return QuantityOut(quantity=round(value, 6), unit="millimeter")


# --------------------------------------------------------------------------- fit limits


@server.tool(
    name="fit_limits",
    description=(
        "ISO 286 limits and fits for a hole/shaft pair such as 'H7/g6' at a nominal size from "
        "3 to 500 mm. Returns the upper/lower deviations, limit sizes, tolerances, min and max "
        "clearance (negative = interference) and the fit type (clearance, transition, interference). "
        "IT01 to IT18; positions A..ZC / a..zc as tabulated in ISO 286-1."
    ),
)
def fit_limits(
    nominal: Annotated[QuantityIn, Field(description="Nominal size of hole and shaft with unit, e.g. '25 mm'. Range 3 to 500 mm.")],
    fit: Annotated[
        str,
        Field(description="Hole class in upper case, '/', shaft class in lower case, e.g. 'H7/g6', 'H7/p6', 'G7/h6'"),
    ],
) -> ToolResult:
    inputs = {"nominal": nominal, "fit": fit}

    def compute(_: JobHandle) -> Computed:
        size = parse_quantity(nominal, name="nominal", dimension="length")
        size_mm = round(float(size.to("millimeter").magnitude), 9)
        res = iso286.fit_limits(size_mm, fit)
        h, s = res.hole, res.shaft
        values = {
            "min_clearance": _mm(res.min_clearance_um / 1000),
            "max_clearance": _mm(res.max_clearance_um / 1000),
            "hole_upper_deviation": _um(h.upper_um),
            "hole_lower_deviation": _um(h.lower_um),
            "shaft_upper_deviation": _um(s.upper_um),
            "shaft_lower_deviation": _um(s.lower_um),
            "hole_tolerance": _um(h.tolerance_um),
            "shaft_tolerance": _um(s.tolerance_um),
            "hole_max_size": _mm(size_mm + h.upper_um / 1000),
            "hole_min_size": _mm(size_mm + h.lower_um / 1000),
            "shaft_max_size": _mm(size_mm + s.upper_um / 1000),
            "shaft_min_size": _mm(size_mm + s.lower_um / 1000),
        }
        over, upto = res.size_range_mm
        dev_over, dev_upto = res.deviation_range_mm
        return Computed(
            values=values,
            primary="min_clearance",
            data={
                "fit_type": res.fit_type,
                "hole": h.designation,
                "shaft": s.designation,
                "nominal_mm": size_mm,
                "size_range_mm": [over, upto],
                "deviation_range_mm": [dev_over, dev_upto],
                "max_interference_mm": round(max(0.0, -res.min_clearance_um) / 1000, 6),
                "min_interference_mm": round(max(0.0, -res.max_clearance_um) / 1000, 6),
                "sign_convention": "clearance = hole size - shaft size; negative = interference",
                "standard": iso286.tables()["meta"]["standard"],
            },
        )

    return run_tool(
        tool_name="metrology_fit_limits",
        engine=ENGINE_ISO286,
        version=__version__,
        inputs=inputs,
        compute=compute,
        standing_warnings=(
            "Standard limits only: no form, roughness, temperature or measurement effects, and no "
            "judgement that the fit suits the application. The standard itself is authoritative.",
        ),
    )


# --------------------------------------------------------------------------- 1-D stack-up

_LINK_KEYS = frozenset({"name", "nominal", "tol", "plus", "minus", "direction", "fit"})
_METHODS = ("worst_case", "rss")


def _invalid(message: str, **details: Any) -> UnitsError:
    return UnitsError("input.invalid", message, details)


def _length_in(raw: Any, *, name: str, unit: str) -> float:
    return float(parse_quantity(raw, name=name, dimension="length").to(unit).magnitude)


def _manual_band(item: dict[str, Any], unit: str) -> tuple[float, float, float]:
    nominal = _length_in(item.get("nominal"), name="nominal", unit=unit)
    if "tol" in item and ("plus" in item or "minus" in item):
        raise _invalid("give either 'tol' (symmetric +/-) or 'plus' and 'minus', not both")
    if "tol" in item:
        plus = minus = _length_in(item["tol"], name="tol", unit=unit)
    elif "plus" in item and "minus" in item:
        plus = _length_in(item["plus"], name="plus", unit=unit)
        minus = _length_in(item["minus"], name="minus", unit=unit)
    elif "plus" in item or "minus" in item:
        raise _invalid("'plus' and 'minus' must be given together (use '0 mm' for a one-sided tolerance)")
    else:
        raise _invalid("a tolerance is required: 'tol' (symmetric +/-) or 'plus' and 'minus'")
    if plus < 0 or minus < 0:
        raise _invalid(
            "tolerances are magnitudes and must be >= 0: 'plus' is how far the link may grow, "
            "'minus' how far it may shrink (20 -0.12 is plus '0 mm', minus '0.12 mm')"
        )
    return nominal, plus, minus


def _fit_band(item: dict[str, Any], unit: str) -> tuple[float, float, float]:
    """A fit link is the clearance of the pair: nominal 0, from -min_clearance to +max_clearance."""
    given = sorted({"tol", "plus", "minus"} & set(item))
    if given:
        raise _invalid(f"a fit link takes its band from the fit; remove {given}")
    code = item["fit"]
    if not isinstance(code, str):
        raise _invalid(f"'fit' must be a string such as 'H7/g6', got {type(code).__name__}", received=code)
    if "nominal" not in item:
        raise _invalid("a fit link needs 'nominal', the diameter of the hole/shaft pair (3 to 500 mm)")
    size_mm = round(_length_in(item["nominal"], name="nominal", unit="millimeter"), 9)
    res = iso286.fit_limits(size_mm, code)
    to_unit = ureg.Quantity(1.0, "millimeter").to(unit).magnitude / 1000
    return 0.0, res.max_clearance_um * to_unit, 0.0 - res.min_clearance_um * to_unit


def _parse_link(item: Any, index: int, unit: str) -> tuple[stackup.Link, str | None]:
    if not isinstance(item, dict):
        raise _invalid(f"link {index + 1} must be a JSON object, got {type(item).__name__}", link_index=index)
    name = str(item.get("name") or f"link{index + 1}")
    try:
        extra = sorted(set(item) - _LINK_KEYS)
        if extra:
            raise _invalid(f"unknown link field(s) {extra}; allowed: {sorted(_LINK_KEYS)}", unknown=extra)
        fit = item.get("fit")
        nominal, plus, minus = _fit_band(item, unit) if "fit" in item else _manual_band(item, unit)
        direction = item.get("direction")
        if direction not in ("+", "-"):
            raise _invalid("'direction' must be '+' (adds to the result) or '-' (subtracts)", received=direction)
    except UnitsError as exc:
        raise UnitsError(
            exc.family,
            f"link {index + 1} ({name}): {exc.message}",
            {**exc.details, "link_index": index, "link_name": name},
        ) from exc
    link = stackup.Link(name=name, nominal=nominal, plus=plus, minus=minus, sign=1 if direction == "+" else -1)
    return link, fit


def _parse_chain(chain: str, unit: str) -> list[tuple[stackup.Link, str | None]]:
    try:
        items = json.loads(chain)
    except (TypeError, json.JSONDecodeError) as exc:
        raise _invalid(
            "'chain' must be a JSON array of link objects, e.g. "
            '[{"name":"A","nominal":"50 mm","tol":"0.1 mm","direction":"+"}]',
            received=str(chain)[:200],
        ) from exc
    if not isinstance(items, list) or not items:
        raise _invalid("'chain' must be a non-empty JSON array of link objects", received=str(chain)[:200])
    if len(items) > stackup.MAX_LINKS:
        raise _invalid(f"'chain' has {len(items)} links; the maximum is {stackup.MAX_LINKS}")
    return [_parse_link(item, i, unit) for i, item in enumerate(items)]


@server.tool(
    name="stackup_1d",
    description=(
        "One-dimensional tolerance stack-up of a dimension chain. 'chain' is a JSON array of links "
        '{"name","nominal","tol" or ("plus","minus"),"direction":"+"|"-"}; direction "+" adds to the '
        'closing dimension, "-" subtracts. method "worst_case" (default) or "rss". Returns the nominal, '
        "mean and limits of the closing dimension and each link's share. A link may instead be an ISO 286 "
        'fit {"name","fit":"H7/g6","nominal":<diameter>,"direction"}: its nominal is the diameter (3 to 500 mm) '
        "and the link itself is the clearance. Optional seeded Monte Carlo "
        "returns the probability of falling outside lower_limit/upper_limit."
    ),
)
def stackup_1d(
    chain: Annotated[
        str,
        Field(
            description=(
                'JSON array of links, e.g. [{"name":"housing","nominal":"50 mm","tol":"0.1 mm","direction":"+"},'
                '{"name":"bearing","nominal":"20 mm","plus":"0 mm","minus":"0.12 mm","direction":"-"}]. '
                "Every length needs a unit, also '0 mm'. 'tol' is symmetric +/-; 'plus'/'minus' are magnitudes. "
                'A fit link {"fit":"H7/g6","nominal":"25 mm","direction":"-"} replaces the tolerance: '
                "'nominal' is then the diameter and the link itself is the clearance (hole - shaft)."
            )
        ),
    ],
    method: Annotated[str, Field(description="'worst_case' (default) or 'rss'")] = "worst_case",
    output_unit: Annotated[str, Field(description="Length unit of the results, e.g. 'mm', 'um', 'inch'")] = "mm",
    lower_limit: Annotated[
        str, Field(description="Lower spec limit of the closing dimension with unit, e.g. '0.2 mm'. Empty = none")
    ] = "",
    upper_limit: Annotated[
        str, Field(description="Upper spec limit of the closing dimension with unit, e.g. '0.8 mm'. Empty = none")
    ] = "",
    monte_carlo: Annotated[bool, Field(description="Also run a seeded Monte Carlo simulation")] = False,
    samples: Annotated[int, Field(description="Monte Carlo sample count, 1000 to 2000000")] = 100_000,
    seed: Annotated[int, Field(description="Monte Carlo random seed; the same seed gives the same result")] = 12345,
    distribution: Annotated[
        str, Field(description="Monte Carlo link distribution: 'normal' (tolerance = +/-3 sigma, default) or 'uniform'")
    ] = "normal",
) -> ToolResult:
    inputs = {
        "chain": chain,
        "method": method,
        "output_unit": output_unit,
        "lower_limit": lower_limit,
        "upper_limit": upper_limit,
        "monte_carlo": monte_carlo,
        "samples": samples,
        "seed": seed,
        "distribution": distribution,
    }

    def compute(_: JobHandle) -> Computed:
        if method not in _METHODS:
            raise _invalid(f"method must be one of {list(_METHODS)}, got {method!r}", param="method")
        if monte_carlo:
            if distribution not in stackup.DISTRIBUTIONS:
                raise _invalid(
                    f"distribution must be one of {list(stackup.DISTRIBUTIONS)}, got {distribution!r}",
                    param="distribution",
                )
            if not (stackup.MIN_SAMPLES <= samples <= stackup.MAX_SAMPLES):
                raise _invalid(
                    f"samples must be between {stackup.MIN_SAMPLES} and {stackup.MAX_SAMPLES}, got {samples}",
                    param="samples",
                )
        unit = str(parse_quantity(f"1 {output_unit}", name="output_unit", dimension="length").units)
        lower = _length_in(lower_limit, name="lower_limit", unit=unit) if str(lower_limit).strip() else None
        upper = _length_in(upper_limit, name="upper_limit", unit=unit) if str(upper_limit).strip() else None
        if lower is not None and upper is not None and lower > upper:
            raise _invalid(f"lower_limit ({lower_limit}) is above upper_limit ({upper_limit})", param="lower_limit")
        parsed = _parse_chain(chain, unit)
        links = [link for link, _ in parsed]

        stack = stackup.rss(links) if method == "rss" else stackup.worst_case(links)

        def q(value: float, sigma: float | None = None) -> QuantityOut:
            return QuantityOut(quantity=value, unit=unit, uncertainty=sigma)

        values = {
            "result_nominal": q(stack.nominal),
            "result_mean": q(stack.mean, stack.sigma),
            "result_min": q(stack.minimum),
            "result_max": q(stack.maximum),
        }
        warnings: list[str] = []
        basis = "worst-case limits"
        if method == "rss":
            basis = "RSS +/-3 sigma limits"
            warnings.append(
                "RSS: each link tolerance is taken as a +/-3 sigma bound of an independent normal variable "
                "centred on the link mid-point; result limits are mean +/- sqrt(sum of squared half-ranges) and "
                "'uncertainty' on result_mean is the one-sigma of the result. It is not a process-capability study"
            )
        data: dict[str, Any] = {
            "method": method,
            "unit": unit,
            "link_count": len(links),
            "contributions": [
                {
                    "name": link.name,
                    "direction": "+" if link.sign > 0 else "-",
                    "nominal": link.nominal,
                    "plus": link.plus,
                    "minus": link.minus,
                    "share": share,
                    "kind": "dimension" if fit is None else "fit",
                    **({} if fit is None else {"fit": fit}),
                }
                for (link, fit), share in zip(parsed, stack.shares)
            ],
            "share_basis": (
                "fraction of the sum of squared half-ranges (RSS variance)"
                if method == "rss"
                else "fraction of the sum of half-ranges (worst-case span)"
            ),
            "formula": (
                "G = sum(sign_i * L_i); worst case: limit sums; "
                "RSS: mean +/- sqrt(sum(((plus_i + minus_i) / 2)^2))"
            ),
        }
        if lower is not None or upper is not None:
            data["spec_check"] = {
                "basis": basis,
                "lower_limit": lower,
                "upper_limit": upper,
                "within_spec": (lower is None or stack.minimum >= lower) and (upper is None or stack.maximum <= upper),
                "lower_margin": None if lower is None else stack.minimum - lower,
                "upper_margin": None if upper is None else upper - stack.maximum,
            }

        if monte_carlo:
            mc = stackup.monte_carlo(
                links, samples=samples, seed=seed, distribution=distribution, lower=lower, upper=upper
            )
            values["mc_mean"] = q(mc.mean)
            values["mc_std"] = q(mc.std)
            values["mc_min"] = q(mc.minimum)
            values["mc_max"] = q(mc.maximum)
            if mc.p_out is not None:
                p = mc.p_out
                values["out_of_spec_probability"] = QuantityOut(
                    quantity=p, unit="dimensionless", uncertainty=math.sqrt(p * (1 - p) / samples)
                )
                if mc.p_below is not None:
                    values["probability_below_lower"] = QuantityOut(quantity=mc.p_below, unit="dimensionless")
                if mc.p_above is not None:
                    values["probability_above_upper"] = QuantityOut(quantity=mc.p_above, unit="dimensionless")
            else:
                warnings.append(
                    "monte_carlo: give lower_limit and/or upper_limit to get out_of_spec_probability; "
                    "only the sample moments are reported"
                )
            data["monte_carlo"] = {
                "samples": samples,
                "seed": seed,
                "distribution": distribution,
                "rng": "numpy PCG64 (default_rng)",
            }
            warnings.append(
                "Monte Carlo: links are independent; 'normal' uses sigma = half-range/3 about the mid-point "
                "(tails beyond the tolerance are not truncated), 'uniform' spans mid-point +/- half-range. "
                "'uncertainty' on out_of_spec_probability is the binomial standard error of the estimate"
            )
        return Computed(values=values, primary="result_nominal", warnings=warnings, data=data)

    return run_tool(
        tool_name="metrology_stackup_1d",
        engine=ENGINE_STACKUP,
        version=__version__,
        inputs=inputs,
        compute=compute,
        standing_warnings=(
            "Linear one-dimensional chain only: no geometric tolerances, form, thermal or assembly effects. "
            "Valid only if the chain contains every contributing dimension.",
        ),
    )


# --------------------------------------------------------------------------- fit search


def _candidate_data(c: Candidate) -> dict[str, Any]:
    limits = c.limits
    return {
        "fit": c.fit,
        "hole": limits.hole.designation,
        "shaft": limits.shaft.designation,
        "preferred": c.preferred,
        "fit_type": limits.fit_type,
        "min_clearance_mm": round(limits.min_clearance_um / 1000, 6),
        "max_clearance_mm": round(limits.max_clearance_um / 1000, 6),
        "hole_tolerance_um": limits.hole.tolerance_um,
        "shaft_tolerance_um": limits.shaft.tolerance_um,
    }


def _choice(value: str, allowed: tuple[str, ...], param: str) -> None:
    if value not in allowed:
        raise _invalid(f"{param} must be one of {list(allowed)}, got {value!r}", param=param)


@server.tool(
    name="fit_search",
    description=(
        "Reverse lookup of ISO 286 fits: which standard fits keep the clearance between min_clearance "
        "and max_clearance at a nominal size from 3 to 500 mm. Clearance = hole size - shaft size, so "
        "give interference as a negative length, e.g. min '-0.05 mm', max '-0.001 mm'. A fit qualifies "
        "when its minimum clearance is at least min_clearance and its maximum clearance is at most "
        "max_clearance. Searches the 10 preferred fits (default) or a wider 'common' set, on hole basis "
        "(H holes) or shaft basis (h shafts). Candidates come best first, ranked by larger tolerances "
        "(a cost proxy), then by closeness to the window centre. If nothing fits, the closest misses "
        "are returned with how far outside the window they lie."
    ),
)
def fit_search(
    nominal: Annotated[QuantityIn, Field(description="Nominal size of hole and shaft with unit, e.g. '25 mm'. Range 3 to 500 mm.")],
    min_clearance: Annotated[
        str,
        Field(
            description=(
                "Smallest clearance the assembly tolerates, with unit, e.g. '0 mm', '0.007 mm'. "
                "Negative means interference: '-0.03 mm' allows up to 0.03 mm of interference."
            )
        ),
    ],
    max_clearance: Annotated[
        str,
        Field(
            description=(
                "Largest clearance the assembly tolerates, with unit, e.g. '0.05 mm'. "
                "Must not be below min_clearance; negative values are interference."
            )
        ),
    ],
    basis: Annotated[
        str, Field(description="'hole' (default): H holes with shafts c..u, e.g. H7/g6. 'shaft': h shafts with holes C..U, e.g. G7/h6")
    ] = "hole",
    fit_set: Annotated[
        str,
        Field(
            description=(
                "'preferred' (default): the 10 preferred fits of the basis. "
                "'common': every hole grade 6 to 11 paired with shaft letters c..u at the same or one finer grade"
            )
        ),
    ] = "preferred",
    max_results: Annotated[int, Field(description="Maximum number of candidates returned, 1 to 50")] = 10,
) -> ToolResult:
    inputs = {
        "nominal": nominal,
        "min_clearance": min_clearance,
        "max_clearance": max_clearance,
        "basis": basis,
        "fit_set": fit_set,
        "max_results": max_results,
    }

    def compute(_: JobHandle) -> Computed:
        size_mm = round(_length_in(nominal, name="nominal", unit="millimeter"), 9)
        # Rounded to the 1e-6 um grid so a window edge typed as '0.007 mm' equals the table's 7 um exactly.
        window_min = round(_length_in(min_clearance, name="min_clearance", unit="micrometer"), 6)
        window_max = round(_length_in(max_clearance, name="max_clearance", unit="micrometer"), 6)
        if window_min > window_max:
            raise _invalid(
                f"min_clearance ({min_clearance}) is above max_clearance ({max_clearance})", param="min_clearance"
            )
        _choice(basis, BASES, "basis")
        _choice(fit_set, FIT_SET_NAMES, "fit_set")
        if not (1 <= max_results <= MAX_RESULTS):
            raise _invalid(f"max_results must be between 1 and {MAX_RESULTS}, got {max_results}", param="max_results")

        found = search_fits(size_mm, window_min, window_max, basis=basis, fit_set=fit_set)
        shown = found.candidates[:max_results]

        values = {"candidate_count": QuantityOut(quantity=len(shown), unit="dimensionless")}
        warnings: list[str] = []
        data: dict[str, Any] = {
            "basis": basis,
            "fit_set": fit_set,
            "window_mm": [round(window_min / 1000, 6), round(window_max / 1000, 6)],
            "size_range_mm": list(found.size_range_mm),
            "searched": found.searched,
            "skipped_undefined": found.skipped_undefined,
            "candidates": [_candidate_data(c) for c in shown],
        }
        if shown:
            best = shown[0].limits
            values["best_min_clearance"] = _mm(best.min_clearance_um / 1000)
            values["best_max_clearance"] = _mm(best.max_clearance_um / 1000)
            if len(found.candidates) > len(shown):
                warnings.append(
                    f"{len(found.candidates)} fits qualify; only the best {len(shown)} are listed. "
                    "Raise max_results (up to 50) to see more"
                )
        else:
            data["nearest"] = [{**_candidate_data(c), "violation_um": c.violation_um} for c in found.nearest]
            warnings.append(
                f"No {fit_set} {basis}-basis fit keeps the clearance inside the window. data.nearest lists the "
                "closest misses with violation_um, the total micrometres by which each lies outside the window; "
                "widen the window, or try fit_set='common' or the other basis"
            )
        return Computed(values=values, primary="candidate_count", warnings=warnings, data=data)

    return run_tool(
        tool_name="metrology_fit_search",
        engine=ENGINE_FIT_SEARCH,
        version=__version__,
        inputs=inputs,
        compute=compute,
        standing_warnings=(
            "Candidates are ranked by tolerance size, a manufacturing-cost proxy, not by suitability: "
            "the ranking is not a judgement that a fit suits the application. Check function, temperature, "
            "surface finish and assembly before choosing. The standard itself is authoritative.",
        ),
    )


# --------------------------------------------------------------------------- scan check

SIGN_CONVENTION_SIGNED = (
    "deviation = signed distance from the scan point to the model surface; positive = outside the "
    "material, negative = inside"
)
SIGN_CONVENTION_UNSIGNED = (
    "deviation = unsigned distance to the model surface (the model is not exactly one closed solid, so "
    "outside and inside are undefined or ambiguous); every value is >= 0"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _positive_mm(raw: str, name: str) -> float:
    qty = require_positive(parse_quantity(raw, name=name, dimension="length"), name=name)
    return float(qty.to("millimeter").magnitude)


def _unit_to_mm(raw: str, name: str) -> float:
    """Millimetres per one ``raw``, where ``raw`` is a bare unit: '2 mm' or 'mm*2' would silently rescale the scan."""
    text = raw.strip()
    not_a_unit = UnitsError(
        "input.invalid",
        f"{name} must be a unit only, such as 'mm', 'm', 'inch' or 'um', not a quantity; got {raw!r}",
        {"param": name, "received": raw},
    )
    if not text or text[0] in "+-.0123456789":
        raise not_a_unit
    try:
        unit = ureg.parse_units(text)
    except ValueError as exc:  # pint: "Unit expression cannot have a scaling factor"
        raise not_a_unit from exc
    except Exception as exc:  # noqa: BLE001 - pint raises several error types for an unknown unit
        raise UnitsError(
            "units.parse_error", f"{name}: could not parse {raw!r} as a unit ({exc})", {"param": name, "received": raw}
        ) from exc
    if not unit.is_compatible_with("millimeter"):
        raise UnitsError(
            "units.dimension_mismatch",
            f"{name} must be a length unit; {raw!r} has dimensionality {unit.dimensionality}",
            {"param": name, "received": raw, "received_dimensionality": str(unit.dimensionality)},
        )
    return float(ureg.Quantity(1.0, unit).to("millimeter").magnitude)


def _deviations_csv(points: np.ndarray, deviation: np.ndarray, labels: np.ndarray) -> tuple[str, int]:
    """CSV text of the out-of-tolerance points, largest |deviation| first, capped at MAX_CSV_ROWS."""
    order = np.argsort(-np.abs(deviation), kind="stable")[: scan.MAX_CSV_ROWS]
    table = np.column_stack([points[order], deviation[order], labels[order]])
    buffer = io.StringIO()
    np.savetxt(
        buffer,
        table,
        fmt=["%.6f", "%.6f", "%.6f", "%.6f", "%d"],
        delimiter=",",
        header="x_mm,y_mm,z_mm,deviation_mm,region",
        comments="",
    )
    return buffer.getvalue(), len(order)


@server.tool(
    name="check_scan",
    description=(
        "Compare a 3D scan (point cloud) with its STEP model and report how far the part is from the "
        "model and where. Deviation is the signed distance from each scan point to the model surface, "
        "positive outside the material (unsigned if the model is not exactly one closed solid). Returns RMS, mean, "
        "standard deviation, maximum, 95th percentile, the fraction of points beyond 'tolerance', a "
        "histogram, the worst points, clustered out-of-tolerance regions (centroid, bounding box, worst "
        "deviation) and a deviations.csv artifact. registration 'icp' first refines the alignment with "
        "point-to-plane ICP (the scan must already be roughly aligned); 'none' trusts the scan frame. "
        f"Needs the optional scan engines ({scan.INSTALL_COMMAND})."
    ),
)
def check_scan(
    scan_path: Annotated[
        str, Field(description="Path to the point cloud: .ply .pcd .xyz .xyzn .xyzrgb or .pts")
    ],
    scan_unit: Annotated[
        str,
        Field(
            description=(
                "Length unit of the point-cloud coordinates, which carry none, as a unit only with no number: "
                "'mm', 'm', 'inch', 'um'. '2 mm' is rejected"
            )
        ),
    ],
    model_path: Annotated[str, Field(description="Path to the STEP model (.step or .stp); read in millimetres")],
    tolerance: Annotated[
        QuantityIn,
        Field(description="Largest acceptable distance between a scan point and the model, with unit, e.g. '0.1 mm'"),
    ],
    registration: Annotated[
        str, Field(description="'none' (default): the scan is already in the model frame. 'icp': refine with ICP first")
    ] = "none",
    icp_max_distance: Annotated[
        str, Field(description="ICP only: largest scan-to-model correspondence distance with unit, e.g. '5 mm'")
    ] = "5 mm",
    mesh_deflection: Annotated[
        str,
        Field(
            description=(
                "Largest chord error allowed when the STEP model is tessellated, with unit, e.g. '0.01 mm'. "
                "Deviations smaller than this are not meaningful"
            )
        ),
    ] = "0.01 mm",
    max_points: Annotated[
        int, Field(description="Scans with more points are downsampled to this many (fixed seed), 1000 to 5000000")
    ] = 200_000,
    output_unit: Annotated[str, Field(description="Length unit of the results, e.g. 'mm', 'um', 'inch'")] = "mm",
) -> ToolResult:
    inputs = {
        "scan_path": scan_path,
        "scan_unit": scan_unit,
        "model_path": model_path,
        "tolerance": tolerance,
        "registration": registration,
        "icp_max_distance": icp_max_distance,
        "mesh_deflection": mesh_deflection,
        "max_points": max_points,
        "output_unit": output_unit,
    }

    def compute(job: JobHandle) -> Computed:
        to_mm = _unit_to_mm(scan_unit, "scan_unit")
        tolerance_mm = _positive_mm(tolerance, "tolerance")
        icp_mm = _positive_mm(icp_max_distance, "icp_max_distance")
        deflection_mm = _positive_mm(mesh_deflection, "mesh_deflection")
        _choice(registration, scan.REGISTRATIONS, "registration")
        if not (scan.MIN_POINTS_LIMIT <= max_points <= scan.MAX_POINTS_LIMIT):
            raise _invalid(
                f"max_points must be between {scan.MIN_POINTS_LIMIT} and {scan.MAX_POINTS_LIMIT}, got {max_points}",
                param="max_points",
            )
        unit = str(parse_quantity(f"1 {output_unit}", name="output_unit", dimension="length").units)
        per_mm = float(ureg.Quantity(1.0, "millimeter").to(unit).magnitude)

        scan_file = scan.check_input_file(scan_path, param="scan_path", extensions=scan.SCAN_EXTENSIONS)
        model_file = scan.check_input_file(model_path, param="model_path", extensions=scan.MODEL_EXTENSIONS)
        run = scan.check(
            scan_file,
            model_file,
            to_mm=to_mm,
            registration=registration,
            icp_max_distance_mm=icp_mm,
            deflection_mm=deflection_mm,
            max_points=max_points,
        )

        def length(value_mm: float) -> QuantityOut:
            return QuantityOut(quantity=float(f"{value_mm * per_mm:.9g}"), unit=unit)

        reg = run.registration
        stats = scan.deviation_stats(run.deviation)
        outside = np.abs(run.deviation) > tolerance_mm
        used = len(run.points)
        values = {
            "rms_deviation": length(stats["rms"]),
            "mean_deviation": length(stats["mean"]),
            "std_deviation": length(stats["std"]),
            "max_deviation": length(stats["max_abs"]),
            "p95_abs_deviation": length(stats["p95_abs"]),
            "out_of_tolerance_fraction": QuantityOut(quantity=float(outside.sum()) / used, unit="dimensionless"),
            "points_evaluated": QuantityOut(quantity=float(used), unit="dimensionless"),
        }
        if run.signed:
            values["max_positive_deviation"] = length(stats["max"])
            values["max_negative_deviation"] = length(stats["min"])

        spacing = scan.scan_spacing(run.points) if outside.any() else 0.0
        regions = scan.cluster_regions(run.points[outside], run.deviation[outside], spacing)
        csv_text, csv_rows = _deviations_csv(run.points[outside], run.deviation[outside], regions.labels)
        artifact = job.write_text("deviations.csv", csv_text, fmt="csv")

        warnings: list[str] = []
        if not run.mesh.watertight:
            warnings.append(
                "The model mesh is not a closed solid (an open shell, or faces that do not join), so inside and "
                "outside are undefined: deviations are unsigned distances and the signed maximum/minimum are omitted"
            )
        elif not run.signed:
            warnings.append(
                f"The STEP file holds {run.mesh.solids} solids: more than one solid: overlapping bodies make "
                "inside and outside ambiguous, so deviations are unsigned distances to the nearest surface of any "
                "body and the signed maximum/minimum are omitted"
            )
        if run.mesh.roots_transferred < run.mesh.roots_in_file:
            warnings.append(
                f"Only {run.mesh.roots_transferred} of {run.mesh.roots_in_file} root entities of the STEP file "
                "could be transferred: the model is incomplete, and the deviation of scan points near the missing "
                "parts is meaningless"
            )
        if used < run.original_count:
            warnings.append(
                f"The scan has {run.original_count} points; {used} were kept (max_points, fixed seed) and "
                "the statistics describe that subset"
            )
        if run.registration is not None:
            warnings.append(
                "ICP only converges from a close initial alignment. Check registration.fitness (share of points "
                "whose distance to the model surface after registration is within icp_max_distance) and "
                "inlier_rmse (RMS of those distances, mm)"
            )
            warnings.append(
                "Deviations after ICP describe form, not location: a best fit can absorb a real defect (a raised "
                "plateau on part of a face shifts the fit and shrinks its own deviation). Judge location and "
                "datum-referenced features with registration 'none' on a scan already in the model frame"
            )
            if run.registration.fitness < scan.LOW_FITNESS:
                warnings.append(
                    f"ICP fitness is {run.registration.fitness:.3f} (below {scan.LOW_FITNESS}): few scan points "
                    "are within icp_max_distance of the model surface after registration, so the alignment is "
                    "not trustworthy"
                )
            if run.registration.unconstrained:
                warnings.append(
                    "The registered points leave the fit unconstrained in: "
                    + "; ".join(run.registration.unconstrained)
                    + ". ICP can place the scan anywhere along these, so the deviations do not locate the part "
                    "in those directions. Scan more surfaces with different normals"
                )
        outside_total = int(outside.sum())
        capped = outside_total > csv_rows
        if capped:
            warnings.append(
                f"deviations.csv holds the {csv_rows} largest of {outside_total} out-of-tolerance points"
            )

        data: dict[str, Any] = {
            "signed": run.signed,
            "sign_convention": SIGN_CONVENTION_SIGNED if run.signed else SIGN_CONVENTION_UNSIGNED,
            "tolerance_mm": round(tolerance_mm, 9),
            "mesh": {
                "triangles": int(len(run.mesh.triangles)),
                "deflection_mm": deflection_mm,
                "watertight": run.mesh.watertight,
                "solids": run.mesh.solids,
                "step_roots": {"in_file": run.mesh.roots_in_file, "transferred": run.mesh.roots_transferred},
            },
            "points": {"original": run.original_count, "used": used},
            "registration": {
                "mode": registration,
                "transform": None if reg is None else reg.transform.tolist(),
                "fitness": None if reg is None else reg.fitness,
                "inlier_rmse": None if reg is None else round(reg.inlier_rmse, 9),
                "source_points": None if reg is None else reg.source_points,
                "unconstrained": None if reg is None else list(reg.unconstrained),
                "note": None if reg is None else scan.ICP_NOTE,
                "unit": "mm",
            },
            "histogram": scan.histogram(run.deviation),
            "worst_points": scan.worst_points(run.points, run.deviation),
            "regions": regions.listed,
            "regions_total": regions.total,
            "points_outside_regions": int((regions.labels < 0).sum()),
            "deviations_csv": {
                "rows": csv_rows,
                "out_of_tolerance_total": outside_total,
                "capped": capped,
                "cap": scan.MAX_CSV_ROWS,
                "columns": ["x_mm", "y_mm", "z_mm", "deviation_mm", "region"],
                "region_column": "region id as in data.regions; -1 = in no region",
            },
            "frame": "points and regions are in the model frame (after registration, if any)",
            "input_files": {
                "scan": {
                    "path": str(scan_file.resolve()),
                    "bytes": scan_file.stat().st_size,
                    "sha256": _sha256(scan_file),
                },
                "model": {
                    "path": str(model_file.resolve()),
                    "bytes": model_file.stat().st_size,
                    "sha256": _sha256(model_file),
                },
            },
        }
        return Computed(values=values, primary="rms_deviation", warnings=warnings, data=data, artifacts=[artifact])

    return run_tool(
        tool_name="metrology_check_scan",
        engine=ENGINE_CHECK_SCAN,
        version=__version__,
        inputs=inputs,
        compute=compute,
        standing_warnings=(
            "Deviations are measured against a tessellated mesh whose error is bounded by mesh_deflection; "
            "deviations below it are not meaningful. Distances to that mesh are float64, taken against the "
            "triangle a single-precision search found closest and that triangle's neighbours: below 1e-9 mm "
            "in the tests, but a degenerate mesh can defeat the search. "
            "The applicable standard and the quality of the scan (noise, coverage, calibration) are the "
            "user's responsibility.",
        ),
    )


__all__ = ["TOOL_NAMES", "check_scan", "fit_limits", "fit_search", "server", "stackup_1d"]
