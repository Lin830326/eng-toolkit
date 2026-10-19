"""Domain registry.

A *domain* is one FastMCP sub-server mounted on the gateway under a namespace
(``calc`` -> tools appear as ``calc_<name>``).  Each domain package exposes a
``DOMAIN`` :class:`DomainInfo`.

Domains are discovered, not listed: any sub-package of ``eng_toolkit.domains`` that exposes a ``DOMAIN`` is mounted,
so adding a domain edits no shared file.  The engines a domain brings are declared the same way, in the domain's own
``engines.py`` (see :func:`eng_toolkit.licenses.register_domain_engines`).  ``docs/adding-a-domain.md`` is the checklist.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from fastmcp import FastMCP

# The order tools were first listed in.  Kept so that tool listings do not move when a domain is added; any other
# domain follows, alphabetically.
HISTORICAL_ORDER: tuple[str, ...] = ("calc", "metrology", "geometry", "drawing")


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


def package_names() -> list[str]:
    """Names of the sub-packages of ``eng_toolkit.domains`` (every domain is one)."""
    import eng_toolkit.domains as pkg

    return sorted(m.name for m in pkgutil.iter_modules(pkg.__path__) if m.ispkg)


def all_domains() -> list[DomainInfo]:
    # Imported lazily so that importing ``eng_toolkit.domains`` stays cheap and acyclic.
    found: dict[str, DomainInfo] = {}
    for name in package_names():
        info = getattr(importlib.import_module(f"eng_toolkit.domains.{name}"), "DOMAIN", None)
        if isinstance(info, DomainInfo):
            if info.name in found:
                raise ValueError(f"two domains are named {info.name!r}")
            found[info.name] = info
    first = [found[n] for n in HISTORICAL_ORDER if n in found]
    return first + [found[n] for n in sorted(found) if n not in HISTORICAL_ORDER]


__all__ = ["DomainInfo", "HISTORICAL_ORDER", "all_domains", "package_names"]
