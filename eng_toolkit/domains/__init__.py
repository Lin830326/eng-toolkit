"""Domain registry.

A *domain* is one FastMCP sub-server mounted on the gateway under a namespace
(``calc`` -> tools appear as ``calc_<name>``).  Each domain package exposes a
``DOMAIN`` :class:`DomainInfo`.  Add new domains to :func:`all_domains`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from fastmcp import FastMCP


@dataclass(frozen=True)
class DomainInfo:
    name: str
    """Namespace used on mount (``calc``)."""
    title: str
    description: str
    engines: tuple[str, ...]
    """Distribution names (keys of :data:`eng_toolkit.licenses.KNOWN`) this domain calls."""
    server: "FastMCP"
    tools: tuple[str, ...] = field(default_factory=tuple)
    """Tool names *inside* the sub-server (without namespace)."""


def all_domains() -> list[DomainInfo]:
    # Imported lazily so that importing ``eng_toolkit.domains`` stays cheap and acyclic.
    from eng_toolkit.domains.calc import DOMAIN as calc
    from eng_toolkit.domains.drawing import DOMAIN as drawing
    from eng_toolkit.domains.geometry import DOMAIN as geometry
    from eng_toolkit.domains.metrology import DOMAIN as metrology

    return [calc, metrology, geometry, drawing]


__all__ = ["DomainInfo", "all_domains"]
