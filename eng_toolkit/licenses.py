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
    extra: str = ""  # the pyproject extra that installs it; "" = core dependency


@dataclass(frozen=True)
class ExternalTool:
    """A program the user installs themselves and the toolkit starts as a separate process (never imported, linked or shipped).

    One descriptor per program; ``eng_toolkit.external`` finds it (``env_var``, then ``PATH``, then ``locations``), runs it with
    limits and probes its version.  A domain registers its descriptors in its own ``engines.py`` as
    ``EXTERNAL_TOOLS = (ExternalTool(...),)``.  ``tier`` is ``gpl-isolated`` for a GPL program (running it as a separate process,
    handing it files and reading files back, is the only way the toolkit may use it) and ``permissive`` otherwise.
    The doctor reports whether it is found and never fails when an optional one is not.

    ``locations`` are directories to look in after ``PATH``: ``~`` and environment variables are expanded, ``*`` matches one path
    component (the highest version wins), and a pattern with an unset variable is skipped.  ``path_additions`` are directories put
    on the child's ``PATH``, written relative to the folder holding the program with ``{bin}``: ``{bin}/../lib``.  Everything else
    the parent's environment holds is dropped, except the names in ``keep_env``; ``env`` gives fixed variables for the child.
    ``version_regex`` is searched in what ``version_args`` prints (the exit code is not looked at: CalculiX exits 201 for ``-v``).
    """

    name: str
    license: str
    tier: str
    role: str
    env_var: str  # ENG_TOOLKIT_<NAME>: the full path of the program; wins over everything else
    executables: tuple[str, ...]  # exact file names, in the order to try them in each directory
    project: str  # where the user gets it (a page, never a download command)
    domains: tuple[str, ...] = field(default_factory=tuple)
    optional: bool = True
    locations: tuple[str, ...] = field(default_factory=tuple)
    path_additions: tuple[str, ...] = field(default_factory=tuple)
    keep_env: tuple[str, ...] = field(default_factory=tuple)
    env: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    version_args: tuple[str, ...] = ("--version",)
    version_regex: str = r"(\d+(?:\.\d+)+)"


EXTERNAL: dict[str, ExternalTool] = {}


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
            "pint",
            "BSD-3-Clause",
            TIER_PERMISSIVE,
            "units and dimensional analysis",
            ("gateway", "calc", "metrology", "geometry", "drawing"),
        ),
        Engine("fluids", "MIT", TIER_PERMISSIVE, "fluid mechanics (friction factor, Reynolds)", ("calc",)),
        Engine(
            "numpy",
            "BSD-3-Clause",
            TIER_PERMISSIVE,
            "seeded Monte Carlo sampling for tolerance stack-up; array maths for meshes and geometry",
            ("metrology", "geometry", "drawing"),
        ),
        Engine(
            "open3d",
            "MIT",
            TIER_PERMISSIVE,
            "point-cloud registration (ICP), distance queries and clustering for scan checks",
            ("metrology",),
            optional=True,
            extra="scan",
        ),
        Engine(
            "cadquery-ocp-novtk",
            "Apache-2.0",
            TIER_LGPL,
            "OpenCASCADE bindings: reads STEP, IGES and BREP, tessellates it for scan checks, measures it exactly, builds parts from a spec, projects them into drawing views",
            ("metrology", "geometry", "drawing"),
            optional=True,
            bundled_license="LGPL-2.1 with OCCT exception (OpenCASCADE shared libraries in the wheel)",
            extra="cad",
        ),
        Engine(
            "ezdxf",
            "MIT",
            TIER_PERMISSIVE,
            "writes the DXF and PDF of a drawing (the one DXF module of the drawing domain)",
            ("drawing",),
            optional=True,
            extra="drawing",
        ),
        Engine(
            "pdfminer.six",
            "MIT",
            TIER_PERMISSIVE,
            "reads the text and vector content of a PDF (the one PDF module of the drawing domain; pure Python, brings charset-normalizer MIT and cryptography Apache-2.0 or BSD-3-Clause)",
            ("drawing",),
            optional=True,
            extra="drawing",
        ),
        Engine(
            "pypdfium2",
            "BSD-3-Clause OR Apache-2.0",
            TIER_PERMISSIVE,
            "rasterises the PDF this toolkit has just written into the PNG of a drawing or of a chart (the one PDFium module, drawing/raster.py; never given a file from a caller)",
            ("drawing", "plot"),
            optional=True,
            bundled_license="PDFium BSD-3-Clause and its bundled libraries, all permissive: abseil, llvm-libc (Apache-2.0), AGG 2.3, FreeType (FreeType License), ICU (Unicode-3.0), lcms2, fast_float, simdutf (MIT), libjpeg-turbo (IJG, BSD), OpenJPEG (BSD-2-Clause), libpng, zlib; no GPL or AGPL",
            extra="png",
        ),
        # A guard, not a dependency: ezdxf[draw] pulls PyMuPDF for a PDF backend, and the doctor must flag it.
        Engine(
            "pymupdf",
            "AGPL-3.0",
            TIER_FORBIDDEN,
            "ezdxf's AGPL PDF backend: must never be installed",
            ("drawing",),
            optional=True,
        ),
    )
}


def install_hint(dist: str) -> str:
    """The command that installs a registered engine: ``uv sync --extra cad`` for an optional one, ``uv sync`` for a core one."""
    engine = KNOWN[dist]
    return f"uv sync --extra {engine.extra}" if engine.extra else "uv sync"

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


def register_domain_engines() -> list[str]:
    """Add the engines each domain declares to :data:`KNOWN`; return the distribution names added.

    A domain declares its engines in its own ``engines.py`` as ``ENGINES = (Engine(...), ...)`` and the programs it starts as ``EXTERNAL_TOOLS = (ExternalTool(...), ...)`` (they join :data:`EXTERNAL`; the return value lists Python distributions only), so adding a domain
    edits no shared table.  The file is loaded by path, without importing the domain package (which would pull in the
    domain's server and its engines, and could form an import cycle with this module).  Declaring an engine that
    ``KNOWN`` already holds is fine when the declaration is identical; a different one is an error, because two
    domains disagreeing about one distribution's license tier is exactly what this table exists to prevent.
    """
    import importlib.util
    from pathlib import Path

    import eng_toolkit.domains as pkg  # light: its module level only defines the registry

    added: list[str] = []
    for base in pkg.__path__:
        for engines_file in sorted(Path(base).glob("*/engines.py")):
            name = engines_file.parent.name
            spec = importlib.util.spec_from_file_location(f"_eng_toolkit_{name}_engines", engines_file)
            if spec is None or spec.loader is None:  # pragma: no cover - a readable file always has a loader
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for engine in getattr(module, "ENGINES", ()):
                have = KNOWN.get(engine.dist)
                if have is None:
                    KNOWN[engine.dist] = engine
                    added.append(engine.dist)
                elif have != engine:
                    raise ValueError(f"domain {name!r} declares {engine.dist!r} differently from the registered engine: {have} vs {engine}")
            for tool in getattr(module, "EXTERNAL_TOOLS", ()):
                have_tool = EXTERNAL.get(tool.name)
                if have_tool is None:
                    EXTERNAL[tool.name] = tool
                elif have_tool != tool:
                    raise ValueError(f"domain {name!r} declares the program {tool.name!r} differently from the registered one: {have_tool} vs {tool}")
    return added


register_domain_engines()

__all__ = [
    "register_domain_engines",
    "KNOWN",
    "POLICY",
    "TIER_FORBIDDEN",
    "TIER_GPL",
    "TIER_LGPL",
    "TIER_PERMISSIVE",
    "TIER_UNKNOWN",
    "EXTERNAL",
    "Engine",
    "ExternalTool",
    "classify_license",
    "install_hint",
    "installed_license",
    "scan_environment",
    "tier_explained_by_bundle",
    "violates_policy",
]
