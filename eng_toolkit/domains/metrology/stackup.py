"""One-dimensional tolerance stack-up: worst case, RSS and seeded Monte Carlo.

Pure maths on floats in one common length unit; no MCP, no unit handling.

A chain closes on ``G = sum(sign_i * L_i)`` where link ``L_i`` runs from ``nominal - minus``
to ``nominal + plus``.  Bilateral and unilateral tolerances are both handled by the
mid-point / half-range form::

    mid_i  = nominal_i + (plus_i - minus_i) / 2
    half_i = (plus_i + minus_i) / 2

* worst case : every link at the limit that pushes the result furthest.
* RSS        : mean = sum(sign_i * mid_i), width t = sqrt(sum(half_i^2)); each half-range is a
               +/-3 sigma bound of an independent normal variable centred on mid_i.
* Monte Carlo: the same distributions sampled with ``numpy.random.default_rng(seed)``
               (normal with sigma = half/3, or uniform over mid +/- half).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

SIGMA_LEVEL = 3.0
"""A link tolerance is taken as +/- SIGMA_LEVEL standard deviations."""
MAX_LINKS = 200
MIN_SAMPLES = 1_000
MAX_SAMPLES = 2_000_000
DISTRIBUTIONS = ("normal", "uniform")


@dataclass(frozen=True)
class Link:
    name: str
    nominal: float
    plus: float
    minus: float
    sign: int  # +1 adds to the result, -1 subtracts

    def __post_init__(self) -> None:
        # plus or minus alone may be negative (a fit link whose band excludes its nominal), but the
        # band nominal - minus .. nominal + plus must still have a lower limit not above the upper one.
        if self.plus + self.minus < 0:
            raise ValueError(
                f"link {self.name!r} has an empty tolerance band: plus + minus = {self.plus + self.minus} < 0"
            )

    @property
    def mid(self) -> float:
        return self.nominal + (self.plus - self.minus) / 2

    @property
    def half(self) -> float:
        return (self.plus + self.minus) / 2


@dataclass(frozen=True)
class Stack:
    method: str
    nominal: float
    mean: float
    minimum: float
    maximum: float
    sigma: float | None  # one-sigma of the result; RSS only
    shares: tuple[float, ...]  # per link, same order; sums to 1 (all 0 for a zero-tolerance chain)


@dataclass(frozen=True)
class MonteCarlo:
    samples: int
    seed: int
    distribution: str
    mean: float
    std: float
    minimum: float
    maximum: float
    p_below: float | None
    p_above: float | None

    @property
    def p_out(self) -> float | None:
        if self.p_below is None and self.p_above is None:
            return None
        return (self.p_below or 0.0) + (self.p_above or 0.0)


def _nominal(links: list[Link]) -> float:
    return math.fsum(link.sign * link.nominal for link in links)


def _mean(links: list[Link]) -> float:
    return math.fsum(link.sign * link.mid for link in links)


def _shares(weights: list[float]) -> tuple[float, ...]:
    total = math.fsum(weights)
    if total == 0:
        return tuple(0.0 for _ in weights)
    return tuple(w / total for w in weights)


def worst_case(links: list[Link]) -> Stack:
    parts_hi, parts_lo = [], []
    for link in links:
        if link.sign > 0:
            parts_hi.append(link.nominal + link.plus)
            parts_lo.append(link.nominal - link.minus)
        else:
            parts_hi.append(-(link.nominal - link.minus))
            parts_lo.append(-(link.nominal + link.plus))
    return Stack(
        method="worst_case",
        nominal=_nominal(links),
        mean=_mean(links),
        minimum=math.fsum(parts_lo),
        maximum=math.fsum(parts_hi),
        sigma=None,
        shares=_shares([link.half for link in links]),
    )


def rss(links: list[Link]) -> Stack:
    mean = _mean(links)
    t = math.sqrt(math.fsum(link.half**2 for link in links))
    return Stack(
        method="rss",
        nominal=_nominal(links),
        mean=mean,
        minimum=mean - t,
        maximum=mean + t,
        sigma=t / SIGMA_LEVEL,
        shares=_shares([link.half**2 for link in links]),
    )


def monte_carlo(
    links: list[Link],
    *,
    samples: int,
    seed: int,
    distribution: str,
    lower: float | None,
    upper: float | None,
) -> MonteCarlo:
    rng = np.random.default_rng(seed)
    total = np.zeros(samples)
    for link in links:  # one link at a time keeps memory at O(samples) for long chains
        if distribution == "normal":
            draw = rng.normal(link.mid, link.half / SIGMA_LEVEL, samples)
        else:
            draw = rng.uniform(link.mid - link.half, link.mid + link.half, samples)
        total += link.sign * draw
    return MonteCarlo(
        samples=samples,
        seed=seed,
        distribution=distribution,
        mean=float(total.mean()),
        std=float(total.std(ddof=1)),
        minimum=float(total.min()),
        maximum=float(total.max()),
        p_below=None if lower is None else float(np.count_nonzero(total < lower) / samples),
        p_above=None if upper is None else float(np.count_nonzero(total > upper) / samples),
    )


__all__ = [
    "DISTRIBUTIONS",
    "MAX_LINKS",
    "MAX_SAMPLES",
    "MIN_SAMPLES",
    "SIGMA_LEVEL",
    "Link",
    "MonteCarlo",
    "Stack",
    "monte_carlo",
    "rss",
    "worst_case",
]
