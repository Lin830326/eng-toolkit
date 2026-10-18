"""``metrology`` sub-server: ISO 286 fit limits and 1-D tolerance stack-up.

The tools here only parse strings into numbers (through the unit gatekeeper) and shape the
result into the contract.  The tables and rules live in :mod:`.iso286`, the statistics in
:mod:`.stackup`.
"""

from __future__ import annotations

import json
import math
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.metrology import iso286, stackup
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import UnitsError, parse_quantity, to_quantity_out, ureg

ENGINE_ISO286 = "eng_toolkit.metrology.iso286"
ENGINE_STACKUP = "eng_toolkit.metrology.stackup"
TOOL_NAMES: tuple[str, ...] = ("fit_limits", "stackup_1d")

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

_LINK_KEYS = frozenset({"name", "nominal", "tol", "plus", "minus", "direction"})
_METHODS = ("worst_case", "rss")


def _invalid(message: str, **details: Any) -> UnitsError:
    return UnitsError("input.invalid", message, details)


def _length_in(raw: Any, *, name: str, unit: str) -> float:
    return float(parse_quantity(raw, name=name, dimension="length").to(unit).magnitude)


def _parse_link(item: Any, index: int, unit: str) -> stackup.Link:
    if not isinstance(item, dict):
        raise _invalid(f"link {index + 1} must be a JSON object, got {type(item).__name__}", link_index=index)
    name = str(item.get("name") or f"link{index + 1}")
    try:
        extra = sorted(set(item) - _LINK_KEYS)
        if extra:
            raise _invalid(f"unknown link field(s) {extra}; allowed: {sorted(_LINK_KEYS)}", unknown=extra)
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
        direction = item.get("direction")
        if direction not in ("+", "-"):
            raise _invalid("'direction' must be '+' (adds to the result) or '-' (subtracts)", received=direction)
    except UnitsError as exc:
        raise UnitsError(
            exc.family,
            f"link {index + 1} ({name}): {exc.message}",
            {**exc.details, "link_index": index, "link_name": name},
        ) from exc
    return stackup.Link(name=name, nominal=nominal, plus=plus, minus=minus, sign=1 if direction == "+" else -1)


def _parse_chain(chain: str, unit: str) -> list[stackup.Link]:
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
        "mean and limits of the closing dimension and each link's share. Optional seeded Monte Carlo "
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
                "Every length needs a unit, also '0 mm'. 'tol' is symmetric +/-; 'plus'/'minus' are magnitudes."
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
        links = _parse_chain(chain, unit)

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
                }
                for link, share in zip(links, stack.shares)
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


__all__ = ["TOOL_NAMES", "fit_limits", "server", "stackup_1d"]
