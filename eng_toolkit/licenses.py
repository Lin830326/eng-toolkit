"""License policy and the registry of engines/dependencies the toolkit knows about.

Policy tiers (from the design doc):

* ``permissive``    Apache-2.0 / MIT / BSD / ISC / PSF ...  -> may be a core (import-time) dependency.
* ``lgpl-dynamic``  LGPL -> dynamic linking only, never modified, never vendored.
* ``gpl-isolated``  GPL -> never imported; runs as a separate process, hand-off via CLI + files.
* ``forbidden``     AGPL and non-commercial licenses -> never ships in the product.

``KNOWN`` lists every engine the toolkit may call with its declared license so
``toolkit_doctor`` can cross-check the metadata of what is actually installed.
"""

from __future__ import annotations

import importlib.metadata as md
import re
from dataclasses import dataclass, field

TIER_PERMISSIVE = "permissive"
TIER_LGPL = "lgpl-dynamic"
TIER_GPL = "gpl-isolated"
TIER_FORBIDDEN = "forbidden"
TIER_UNKNOWN = "unknown"

POLICY: dict[str, str] = {
    TIER_PERMISSIVE: "core dependency allowed (Apache/MIT/BSD-class)",
    TIER_LGPL: "dynamic linking only; never modified or vendored",
    TIER_GPL: "separate process only; hand-off via CLI and files; never imported",
    TIER_FORBIDDEN: "must not ship (AGPL, non-commercial, source-available)",
    TIER_UNKNOWN: "license could not be determined; review before use",
}


@dataclass(frozen=True)
class Engine:
    dist: str  # distribution name on PyPI
    license: str  # SPDX-ish identifier we declare
    tier: str
    role: str  # what it does for us
    domains: tuple[str, ...] = field(default_factory=tuple)
    optional: bool = False  # installed through an extra; absence is informational, not a failure
    bundled_license: str = ""  # license of code shipped inside the wheel when it differs from the metadata


def tier_explained_by_bundle(engine: Engine, detected: str) -> bool:
    """True when a permissive wrapper is declared ``lgpl-dynamic`` because its wheel bundles LGPL code.

    This is the only mismatch a bundled license may explain.  A declared ``gpl-isolated`` or
    ``forbidden`` engine is never excused, whatever it bundles or its metadata says.
    """
    return bool(engine.bundled_license) and engine.tier == TIER_LGPL and detected == TIER_PERMISSIVE


def violates_policy(engine: Engine) -> bool:
    """True for an engine declared ``gpl-isolated`` or ``forbidden``: it must not be installed in the toolkit environment."""
    return engine.tier in (TIER_GPL, TIER_FORBIDDEN)


KNOWN: dict[str, Engine] = {
    e.dist: e
    for e in (
        Engine("fastmcp", "Apache-2.0", TIER_PERMISSIVE, "MCP server/client framework", ("gateway",)),
        Engine("mcp", "MIT", TIER_PERMISSIVE, "MCP protocol SDK (fastmcp dependency)", ("gateway",)),
        Engine("pydantic", "MIT", TIER_PERMISSIVE, "return-contract models", ("gateway",)),
        Engine(
            "pint", "BSD-3-Clause", TIER_PERMISSIVE, "units and dimensional analysis", ("gateway", "calc", "metrology")
        ),
        Engine("fluids", "MIT", TIER_PERMISSIVE, "fluid mechanics (friction factor, Reynolds)", ("calc",)),
        Engine("numpy", "BSD-3-Clause", TIER_PERMISSIVE, "seeded Monte Carlo sampling for tolerance stack-up", ("metrology",)),
        Engine(
            "open3d",
            "MIT",
            TIER_PERMISSIVE,
            "point-cloud registration (ICP), distance queries and clustering for scan checks",
            ("metrology",),
            optional=True,
        ),
        Engine(
            "cadquery-ocp-novtk",
            "Apache-2.0",
            TIER_LGPL,
            "OpenCASCADE bindings: reads STEP and tessellates it for scan checks",
            ("metrology",),
            optional=True,
            bundled_license="LGPL-2.1 with OCCT exception (OpenCASCADE shared libraries in the wheel)",
        ),
    )
}

# Patterns over license text / classifiers -> tier.  Order matters (AGPL before GPL).
_TIER_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"agpl|affero", re.I), TIER_FORBIDDEN),
    (re.compile(r"non[- ]?commercial|cc[- ]by[- ]nc|sspl|\bbsl\b|business source", re.I), TIER_FORBIDDEN),
    (re.compile(r"lgpl|lesser general public", re.I), TIER_LGPL),
    (re.compile(r"\bgpl\b|gnu general public|GPL-?[23]", re.I), TIER_GPL),
    (
        re.compile(
            r"apache|\bmit\b|bsd|\bisc\b|psf|python software foundation|mozilla|\bmpl\b|unlicense|zlib|0bsd|cc0",
            re.I,
        ),
        TIER_PERMISSIVE,
    ),
)


def classify_license(text: str | None) -> str:
    """Map a license expression or classifier text to a policy tier."""
    if not text:
        return TIER_UNKNOWN
    for pattern, tier in _TIER_PATTERNS:
        if pattern.search(text):
            return tier
    return TIER_UNKNOWN


def installed_license(dist: str) -> tuple[str | None, str | None]:
    """Return ``(version, license_text)`` for an installed distribution, or ``(None, None)``."""
    try:
        meta = md.metadata(dist)
    except md.PackageNotFoundError:
        return None, None
    version = meta.get("Version")
    expression = meta.get("License-Expression") or meta.get("License")
    classifiers = [c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
    # Prefer a short expression; a long "License" field is usually the full license body.
    if expression and len(expression) < 80:
        text = expression
    elif classifiers:
        text = "; ".join(classifiers)
    else:
        text = expression
    return version, text


def scan_environment() -> list[dict[str, str]]:
    """Every installed distribution with its detected license tier (for doctor)."""
    rows: list[dict[str, str]] = []
    for dist in md.distributions():
        name = dist.metadata.get("Name") or "?"
        version, text = installed_license(name)
        rows.append(
            {
                "dist": name,
                "version": version or "?",
                "license": (text or "")[:120],
                "tier": classify_license(text),
            }
        )
    rows.sort(key=lambda r: r["dist"].lower())
    return rows


__all__ = [
    "KNOWN",
    "POLICY",
    "TIER_FORBIDDEN",
    "TIER_GPL",
    "TIER_LGPL",
    "TIER_PERMISSIVE",
    "TIER_UNKNOWN",
    "Engine",
    "classify_license",
    "installed_license",
    "scan_environment",
    "tier_explained_by_bundle",
    "violates_policy",
]
