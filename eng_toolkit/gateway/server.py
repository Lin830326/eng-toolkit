"""The eng-toolkit MCP gateway.

* Mounts each domain sub-server with FastMCP ``mount(server, namespace=...)``, so
  the ``calc`` domain's ``cylinder_force`` is exposed as ``calc_cylinder_force``.
  (MCP tool names may not contain ``.``; the design doc's ``calc.cylinder_force``
  maps to ``calc_cylinder_force``.)
* Adds two gateway-level tools:
  ``toolkit_list_tools`` -- installed domains, engines and versions;
  ``toolkit_doctor``     -- dependency / license-tier / version / workspace health check.

Run with ``eng-toolkit`` (console script) or ``python -m eng_toolkit`` -> stdio transport.
"""

from __future__ import annotations

import platform
import sys
from typing import Any

from fastmcp import FastMCP

from eng_toolkit import __version__
from eng_toolkit.contract import Provenance, ToolResult, inputs_hash
from eng_toolkit.domains import all_domains
from eng_toolkit.licenses import (
    KNOWN,
    POLICY,
    TIER_FORBIDDEN,
    TIER_GPL,
    TIER_PERMISSIVE,
    TIER_UNKNOWN,
    classify_license,
    installed_license,
    scan_environment,
)
from eng_toolkit.workspace import Workspace

ENGINE = "eng_toolkit.gateway"
MIN_PYTHON = (3, 12)

gateway = FastMCP(
    "eng-toolkit",
    version=__version__,
    instructions=(
        "AI-callable open-source engineering toolkit. Tools are namespaced <domain>_<tool>. "
        "Every physical input must carry a unit ('100 mm', '210 bar'); bare numbers are rejected. "
        "Every result follows one contract {ok, value, values, artifacts, provenance, warnings, "
        "error, review_status='unreviewed'}. Results are NOT engineering-approved."
    ),
)

DOMAINS = all_domains()
for _domain in DOMAINS:
    gateway.mount(_domain.server, namespace=_domain.name)


def _engine_rows(dists: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = []
    for dist in dists:
        version, text = installed_license(dist)
        known = KNOWN.get(dist)
        rows.append(
            {
                "dist": dist,
                "version": version,
                "installed": version is not None,
                "declared_license": known.license if known else None,
                "detected_license": text,
                "tier": known.tier if known else classify_license(text),
            }
        )
    return rows


def _gateway_result(*, ok: bool, data: dict[str, Any], warnings: list[str], inputs: dict[str, Any]) -> ToolResult:
    return ToolResult(
        ok=ok,
        data=data,
        provenance=Provenance(engine=ENGINE, version=__version__, inputs_hash=inputs_hash(inputs)),
        warnings=warnings,
    )


@gateway.tool(
    name="toolkit_list_tools",
    description="List installed domains, the engines (with versions and license tiers) behind them, and every tool name.",
)
async def toolkit_list_tools() -> ToolResult:
    tools = await gateway.list_tools()
    tool_names = sorted(t.name for t in tools)
    domains = [
        {
            "name": d.name,
            "title": d.title,
            "description": d.description,
            "engines": _engine_rows(d.engines),
            "tools": [f"{d.name}_{t}" for t in d.tools],
        }
        for d in DOMAINS
    ]
    data = {
        "toolkit_version": __version__,
        "domains": domains,
        "gateway_tools": [n for n in tool_names if n.startswith("toolkit_")],
        "tools": tool_names,
    }
    return _gateway_result(ok=True, data=data, warnings=[], inputs={})


@gateway.tool(
    name="toolkit_doctor",
    description=(
        "Health check: Python version, installed engine versions vs. declared licenses and policy "
        "tiers, workspace writability, pint sanity, and (optionally) a license scan of the whole "
        "environment flagging GPL/AGPL/unknown distributions."
    ),
)
def toolkit_doctor(scan_env: bool = True) -> ToolResult:
    warnings: list[str] = []
    hard_failures: list[str] = []

    # Python
    py_ok = sys.version_info[:2] >= MIN_PYTHON
    if not py_ok:
        hard_failures.append(f"Python {platform.python_version()} < {'.'.join(map(str, MIN_PYTHON))}")

    # Declared engines
    deps = []
    for engine in KNOWN.values():
        version, text = installed_license(engine.dist)
        detected = classify_license(text)
        status = "ok"
        if version is None:
            status = "missing"
            hard_failures.append(f"dependency '{engine.dist}' is not installed")
        elif detected != engine.tier:
            status = "license-mismatch"
            warnings.append(
                f"'{engine.dist}': declared {engine.license} ({engine.tier}) but metadata says "
                f"{text!r} ({detected}); review before use"
            )
        deps.append(
            {
                "dist": engine.dist,
                "role": engine.role,
                "domains": list(engine.domains),
                "version": version,
                "declared_license": engine.license,
                "declared_tier": engine.tier,
                "detected_license": text,
                "detected_tier": detected,
                "status": status,
            }
        )

    # Workspace
    ws = Workspace()
    ws_ok = ws.is_writable()
    if not ws_ok:
        hard_failures.append(f"workspace {ws.root} is not writable")

    # Units sanity
    try:
        from eng_toolkit.units import parse_quantity

        pa = float(parse_quantity("1 bar", name="probe", dimension="pressure").to("Pa").magnitude)
        units_ok = abs(pa - 1e5) < 1e-6
    except Exception as exc:  # noqa: BLE001
        units_ok = False
        hard_failures.append(f"pint sanity check failed: {exc}")

    # Environment license scan
    scan: dict[str, Any] | None = None
    if scan_env:
        rows = scan_environment()
        by_tier: dict[str, int] = {}
        for r in rows:
            by_tier[r["tier"]] = by_tier.get(r["tier"], 0) + 1
        flagged = [r for r in rows if r["tier"] in (TIER_GPL, TIER_FORBIDDEN)]
        unknown = [r for r in rows if r["tier"] == TIER_UNKNOWN]
        for r in flagged:
            msg = f"'{r['dist']}' {r['version']} is {r['tier']} ({r['license']})"
            if r["tier"] == TIER_FORBIDDEN:
                hard_failures.append(msg)
            else:
                hard_failures.append(msg + " -- GPL code must not be installed in the toolkit environment")
        if unknown:
            warnings.append(
                "license could not be determined for: " + ", ".join(r["dist"] for r in unknown)
            )
        scan = {
            "distributions": len(rows),
            "by_tier": by_tier,
            "flagged": flagged,
            "unknown": unknown,
            "permissive_count": by_tier.get(TIER_PERMISSIVE, 0),
        }

    data = {
        "toolkit_version": __version__,
        "python": {"version": platform.python_version(), "executable": sys.executable, "ok": py_ok},
        "platform": platform.platform(),
        "dependencies": deps,
        "workspace": {"root": str(ws.root), "writable": ws_ok},
        "units": {"pint_ok": units_ok},
        "license_policy": POLICY,
        "license_scan": scan,
        "domains": [d.name for d in DOMAINS],
        "failures": hard_failures,
    }
    return _gateway_result(
        ok=not hard_failures,
        data=data,
        warnings=warnings + hard_failures,
        inputs={"scan_env": scan_env},
    )


def main(argv: list[str] | None = None) -> None:
    """Console entry point.

    ``eng-toolkit``            serve the gateway over stdio (what an MCP client launches)
    ``eng-toolkit --doctor``   print the toolkit_doctor report as JSON and exit 0/1
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="eng-toolkit", description="eng-toolkit MCP gateway")
    parser.add_argument("--doctor", action="store_true", help="run toolkit_doctor and exit")
    parser.add_argument("--no-scan", action="store_true", help="with --doctor: skip the environment license scan")
    args = parser.parse_args(argv)

    if args.doctor:
        # FastMCP >= 4 returns the plain function from @tool; older versions return a FunctionTool with .fn
        doctor_fn = getattr(toolkit_doctor, "fn", toolkit_doctor)
        report = doctor_fn(scan_env=not args.no_scan)
        print(json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False))
        raise SystemExit(0 if report.ok else 1)

    gateway.run(transport="stdio", show_banner=False)


if __name__ == "__main__":  # pragma: no cover
    main()
